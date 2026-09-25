"""Pure risk decisions: brakes, entry checks, sizing, loop guards, canary + ramp, directives,
hard-flatten decision and the tighten-only merge of owner limits.

Every function here is pure (no I/O, no clock reads); the engine passes ``now`` in. Layers
may only veto or shrink; exits are never blocked (only entries), except that the hard
flatten is itself an exit.

Units: USD ``Decimal``; percentages are fractions (0.05 = 5%); ``data_age`` is a
``timedelta``; ``error_rate`` is a fraction of the last calls that failed.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field

from tiller.config import CanaryCfg, Config, RiskCfg
from tiller.models import SOL_MINT, USDC_MINT, DayStats, DomainModel, OwnerLimits
from tiller.portfolio.allocator import OrderIntent, cash_floor_fraction, current_value_usd
from tiller.risk.account import AccountSnapshot
from tiller.state import AgentState, BrakeState, CanaryState

Directive = Literal["pause", "liquidate"]
DIRECTIVE_PHRASES: tuple[tuple[str, Directive], ...] = (
    ("liquidate", "liquidate"),
    ("sell all", "liquidate"),
    ("stop trading", "pause"),
    ("pause", "pause"),
)
_SENTENCE_SPLIT = re.compile(r"[.!?;\n]+")


class Decision(DomainModel):
    allowed: bool
    reasons: list[str] = Field(default_factory=list)


class SizedOrder(DomainModel):
    """Final entry size and the name of the cap that bound it."""

    usd: Decimal
    binding_cap: str


class Limits(DomainModel):
    """Effective entry caps after the tighten-only merge (``None`` cap = unlimited)."""

    max_position_usd: Decimal | None = None
    daily_limit_usd: Decimal | None = None
    multiplier: Decimal = Decimal(1)
    stage: Literal["canary", "ramp", "full", "paper"] = "full"
    directive: Directive | None = None
    sources: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- directives


def parse_directive(instructions: str | None) -> Directive | None:
    """Sentence-initial, case-insensitive prefix match on pause / stop trading / liquidate /
    sell all. Substrings ('please do not pause') never match. 'liquidate' wins over 'pause'."""
    if not instructions:
        return None
    found: set[Directive] = set()
    for sentence in _SENTENCE_SPLIT.split(instructions):
        head = sentence.strip().lower()
        if not head:
            continue
        for phrase, directive in DIRECTIVE_PHRASES:
            if head.startswith(phrase) and not re.match(rf"^{re.escape(phrase)}[a-z]", head):
                found.add(directive)
                break
    if "liquidate" in found:
        return "liquidate"
    if "pause" in found:
        return "pause"
    return None


def load_local_overrides(path: Path | None, now: datetime) -> OwnerLimits | None:
    """``config/owner_overrides.json`` -> OwnerLimits; missing file = no local source;
    unparseable file = ``readable=False`` (fail closed)."""
    if path is None or not Path(path).exists():
        return None
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("not an object")

        def dec(v: object) -> Decimal | None:
            return None if v in (None, "") else Decimal(str(v))

        return OwnerLimits(
            max_position_usd=dec(raw.get("maxPositionUsd")),
            daily_limit_usd=dec(raw.get("dailyLimitUsd")),
            instructions=str(raw.get("instructions") or "") or None,
            readable=True,
            read_at=now,
        )
    except Exception:
        return OwnerLimits(readable=False, read_at=now)


# --------------------------------------------------------------------------- limits / canary


def _min_opt(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def canary_verified(canary: CanaryState) -> bool:
    return canary.verified_buy and canary.verified_sell and canary.verified_post


def effective_limits(
    cfg: RiskCfg,
    owner: OwnerLimits | None,
    local: OwnerLimits | None,
    canary: CanaryState,
    now: datetime,
    *,
    canary_enabled: bool = True,
) -> Limits | None:
    """min() of every source (tighten-only); ``None`` = fail closed (a source exists but is
    unreadable). ``canary_enabled`` applies the canary caps / ramp multiplier (live mode)."""
    sources: list[str] = []
    max_pos: Decimal | None = None
    daily: Decimal | None = None
    directives: list[Directive | None] = []
    for name, src in (("owner", owner), ("local", local)):
        if src is None:
            continue
        if not src.readable:
            return None
        sources.append(name)
        max_pos = _min_opt(max_pos, src.max_position_usd)
        daily = _min_opt(daily, src.daily_limit_usd)
        directives.append(parse_directive(src.instructions))
    directive: Directive | None = None
    if "liquidate" in directives:
        directive = "liquidate"
    elif "pause" in directives:
        directive = "pause"
    if not canary_enabled:
        return Limits(
            max_position_usd=max_pos,
            daily_limit_usd=daily,
            stage="paper",
            directive=directive,
            sources=sources,
        )
    if not canary_verified(canary):
        return Limits(
            max_position_usd=_min_opt(max_pos, Decimal(cfg.canary.max_position_usd)),
            daily_limit_usd=_min_opt(daily, Decimal(cfg.canary.daily_limit_usd)),
            multiplier=Decimal(1),
            stage="canary",
            directive=directive,
            sources=[*sources, "canary"],
        )
    steps = list(cfg.canary.ramp_steps) or [1.0]
    step = min(max(canary.ramp_step, 0), len(steps) - 1)
    mult = Decimal(str(steps[step]))
    return Limits(
        max_position_usd=max_pos,
        daily_limit_usd=daily,
        multiplier=mult,
        stage="full" if mult >= 1 else "ramp",
        directive=directive,
        sources=[*sources, f"ramp[{step}]"],
    )


def advance_canary(
    state: CanaryState, verified: tuple[bool, bool, bool], brake_tripped: bool, now: datetime, cfg: CanaryCfg
) -> CanaryState:
    """Sticky verified flags; step 0 until all three are verified, then one ramp step every
    ``ramp_step_days``; any brake trip moves the ramp back one step (never below 0)."""
    buy, sell, post = verified
    out = state.model_copy(
        update={
            "verified_buy": state.verified_buy or buy,
            "verified_sell": state.verified_sell or sell,
            "verified_post": state.verified_post or post,
        }
    )
    if not canary_verified(out):
        return out.model_copy(update={"ramp_step": 0, "ramp_step_started": None})
    steps = max(1, len(cfg.ramp_steps))
    if out.ramp_step_started is None:
        return out.model_copy(update={"ramp_step": 0, "ramp_step_started": now})
    step = out.ramp_step
    started = out.ramp_step_started
    if brake_tripped:
        return out.model_copy(update={"ramp_step": max(0, step - 1), "ramp_step_started": now})
    if now - started >= timedelta(days=cfg.ramp_step_days) and step < steps - 1:
        return out.model_copy(update={"ramp_step": step + 1, "ramp_step_started": now})
    return out


# --------------------------------------------------------------------------- brakes


def brake_state(
    acct: AccountSnapshot,
    cfg: RiskCfg,
    data_age: timedelta,
    limits: Limits | None,
    error_rate: float,
    state: AgentState,
    now: datetime,
    *,
    kill_file: bool = False,
) -> BrakeState:
    """L3 brakes: every reason that blocks ENTRIES this tick (exits are never blocked)."""
    reasons: list[str] = []
    start = acct.day.start_equity
    if start > 0:
        loss = (start - acct.equity_usd) / start
        if loss >= Decimal(str(cfg.daily_loss_pct)):
            reasons.append(f"daily_loss {loss:.2%} >= {cfg.daily_loss_pct:.0%}")
    dd7 = acct.drawdown_from(acct.peak_7d)
    if dd7 >= Decimal(str(cfg.dd7_entry_block_pct)):
        reasons.append(f"dd7 {dd7:.2%} >= {cfg.dd7_entry_block_pct:.0%}")
    dd30 = acct.drawdown_from(acct.peak_30d)
    if dd30 >= Decimal(str(cfg.dd30_entry_block_pct)):
        reasons.append(f"dd30 {dd30:.2%} >= {cfg.dd30_entry_block_pct:.0%}")
    if data_age > timedelta(hours=cfg.max_data_age_h):
        reasons.append(f"data_stale {data_age.total_seconds() / 3600:.1f}h > {cfg.max_data_age_h}h")
    if limits is None:
        reasons.append("owner_limits_unreadable")
    elif limits.directive == "pause":
        reasons.append("directive_pause")
    if error_rate > cfg.error_rate_block:
        reasons.append(f"error_rate {error_rate:.0%} > {cfg.error_rate_block:.0%}")
    paused_until = state.brakes.paused_until
    if paused_until is not None and paused_until > now:
        reasons.append(f"paused_until {paused_until.isoformat(timespec='minutes')}")
    else:
        paused_until = None
    if state.halted:
        reasons.append("halted" + (f": {state.halt_reason}" if state.halt_reason else ""))
    if state.reconcile_mismatch_ticks >= 2:
        reasons.append(f"reconcile_mismatch x{state.reconcile_mismatch_ticks}")
    if kill_file:
        reasons.append("kill_file")
    return BrakeState(
        entries_blocked=bool(reasons),
        reasons=reasons,
        paused_until=paused_until,
        consecutive_failures=state.brakes.consecutive_failures,
    )


def record_failure(state: BrakeState, now: datetime, cfg: RiskCfg, threshold: int = 3) -> BrakeState:
    """One more consecutive execution failure; ``threshold`` of them pause entries for
    ``consecutive_failure_pause_min`` minutes and reset the counter."""
    n = state.consecutive_failures + 1
    if n >= threshold:
        return state.model_copy(
            update={
                "consecutive_failures": 0,
                "paused_until": now + timedelta(minutes=cfg.consecutive_failure_pause_min),
                "entries_blocked": True,
                "reasons": [*state.reasons, f"{threshold} consecutive execution failures"],
            }
        )
    return state.model_copy(update={"consecutive_failures": n})


def record_success(state: BrakeState) -> BrakeState:
    return state.model_copy(update={"consecutive_failures": 0})


# --------------------------------------------------------------------------- entries


def check_entry(
    intent: OrderIntent,
    acct: AccountSnapshot,
    brakes: BrakeState,
    limits: Limits,
    cfg: Config,
    ledger_day: DayStats,
    blocklist: set[str],
    *,
    entries_this_tick: int = 0,
) -> Decision:
    """Veto layer for one entry (buy). Every failing rule is reported."""
    reasons: list[str] = []
    if intent.side != "buy":
        return Decision(allowed=True, reasons=[])
    if brakes.entries_blocked:
        reasons.extend(brakes.reasons or ["entries blocked"])
    r = cfg.risk
    if ledger_day.swaps >= r.max_swaps_per_day:
        reasons.append(f"swaps_today {ledger_day.swaps} >= {r.max_swaps_per_day}")
    if entries_this_tick >= r.max_entries_per_tick:
        reasons.append(f"entries_this_tick {entries_this_tick} >= {r.max_entries_per_tick}")
    held = {p.mint for p in acct.positions if p.amount_base > 0}
    if intent.mint not in held and len(held) >= r.max_positions:
        reasons.append(f"positions {len(held)} >= {r.max_positions}")
    if intent.mint in blocklist:
        reasons.append("mint blocklisted")
    floor = cash_floor_fraction(cfg) * acct.equity_usd
    if acct.usdc_usd - intent.usd < floor:
        reasons.append(f"post-trade USDC {acct.usdc_usd - intent.usd:.2f} < cash floor {floor:.2f}")
    if acct.sol_lamports < cfg.wallet.sol_reserve_lamports:
        reasons.append("SOL below gas reserve")
    if intent.usd < Decimal(r.min_order_usd):
        reasons.append(f"order {intent.usd} < min {r.min_order_usd}")
    return Decision(allowed=not reasons, reasons=reasons)


def size_order(
    intent: OrderIntent,
    acct: AccountSnapshot,
    limits: Limits,
    cfg: Config,
    pool_liquidity_usd: Decimal | None,
    day: DayStats,
    stop_pct: Decimal | None,
    *,
    sleeve_budget_usd: Decimal | None = None,
) -> SizedOrder | None:
    """Entry size = min over every cap, times the ramp multiplier; ``None`` below the minimum.

    Caps: ``target`` (the intent), ``stop_risk`` (1% of equity at risk off a gap-adjusted
    stop ``stop_pct * 1.5``), ``max_token_pct`` (remaining, non-hold mints only),
    ``pool_liquidity`` (1%), ``max_position`` (owner/canary), ``daily_limit`` (remaining),
    ``sleeve_budget`` (remaining), ``ramp`` (multiplier < 1).
    """
    r = cfg.risk
    equity = acct.equity_usd
    caps: list[tuple[str, Decimal]] = [("target", Decimal(intent.usd))]
    if stop_pct is not None and stop_pct > 0:
        caps.append(("stop_risk", equity * Decimal("0.01") / (Decimal(stop_pct) * Decimal("1.5"))))
    if intent.mint not in (cfg.strategies.hold_mint, SOL_MINT, USDC_MINT):
        remaining = Decimal(str(r.max_token_pct)) * equity - current_value_usd(intent.mint, acct, cfg)
        caps.append(("max_token_pct", remaining))
    if pool_liquidity_usd is not None:
        caps.append(("pool_liquidity", Decimal(pool_liquidity_usd) * Decimal("0.01")))
    if limits.max_position_usd is not None:
        caps.append(("max_position", Decimal(limits.max_position_usd)))
    if limits.daily_limit_usd is not None:
        caps.append(("daily_limit", Decimal(limits.daily_limit_usd) - day.buys_usd))
    if sleeve_budget_usd is not None:
        caps.append(("sleeve_budget", Decimal(sleeve_budget_usd)))
    name, usd = min(caps, key=lambda c: c[1])
    if limits.multiplier < 1:
        usd = usd * limits.multiplier
        name = "ramp"
    if usd < Decimal(r.min_order_usd):
        return None
    return SizedOrder(usd=usd.quantize(Decimal("0.000001")), binding_cap=name)


# --------------------------------------------------------------------------- flatten


def must_flatten(
    acct: AccountSnapshot, cfg: Config, directive: Directive | None, kill_file: bool, state: AgentState
) -> str | None:
    """Reason for a hard flatten: drawdown from the ROLLING 30-day peak, a liquidate
    directive, or a KILL file that asks for liquidation. ``None`` = keep trading."""
    dd30 = acct.drawdown_from(acct.peak_30d)
    if dd30 >= Decimal(str(cfg.risk.dd30_flatten_pct)):
        return f"dd30 {dd30:.2%} >= {cfg.risk.dd30_flatten_pct:.0%} flatten threshold"
    if directive == "liquidate":
        return "owner directive: liquidate"
    if kill_file:
        return "KILL file requests liquidation"
    return None


def read_kill_file(path: Path) -> tuple[bool, bool]:
    """(present, liquidate): an empty KILL file blocks entries; one containing 'liquidate'
    (case-insensitive) triggers the emergency flatten."""
    p = Path(path)
    if not p.exists():
        return False, False
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return True, False
    return True, "liquidate" in text.lower()
