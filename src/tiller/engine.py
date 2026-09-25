"""The tick and the scheduler.

Tick order (spec ``engine.py``): lock heartbeat -> load state -> reconcile (boot, every 10th
tick) -> owner limits -> candles -> account snapshot -> brakes -> must_flatten (emergency
exits) -> position exit rules -> daily strategies (00:05-06:00 UTC window, once per closed
bar) -> allocator plan -> risk check / size -> venue swap -> ledger/state -> post queue flush
-> board snapshots (15 min) -> copy feeds + shadow marks -> alerts -> save state.

:func:`plan_tick` is the pure core: given a snapshot, the strategy targets and the risk
state it returns the ordered plan (exits first, then at most one sized entry) with every
refusal and its reason. :class:`Agent` wraps it with I/O.

Units: USD ``Decimal``; base units for swap amounts; seconds for scheduler intervals.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time, timedelta
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from tiller.clock import Clock, utc_day_start
from tiller.config import Config
from tiller.data.candles import CandleSourceError, InsufficientCandles
from tiller.data.prices import ReferenceDisagreement, ReferenceUnavailable, reference_price
from tiller.data.tokens import GateResult, evaluate_token_gate, gate_cfg_from_thresholds
from tiller.execution.venue import ExecutionFailed, ExecutionUncertain, GuardRejected
from tiller.ledger import Ledger
from tiller.models import (
    SOL_DECIMALS,
    SOL_MINT,
    USDC_DECIMALS,
    USDC_MINT,
    Candle,
    DomainModel,
    Fill,
    OwnerLimits,
    SwapRequest,
    TradeContext,
)
from tiller.portfolio.allocator import (
    OrderIntent,
    current_value_usd,
    plan,
    ratchet_trailing_stop,
    sleeve_budgets,
    tradeable_base,
)
from tiller.risk.account import (
    AccountSnapshot,
    BalanceSource,
    build_snapshot,
    record_snapshot,
    snapshot_summary,
)
from tiller.risk.engine import (
    Limits,
    SizedOrder,
    advance_canary,
    brake_state,
    check_entry,
    effective_limits,
    load_local_overrides,
    must_flatten,
    parse_directive,
    read_kill_file,
    record_failure,
    record_success,
    size_order,
)
from tiller.risk.reconcile import ReconcileReport, reconcile
from tiller.state import AgentState, BrakeState, SleeveState, load_state, save_state
from tiller.strategies.base import MarketContext, TargetExposure, closed_daily_bars
from tiller.strategies.indicators import realized_vol
from tiller.strategies.sol_trend import candles_to_arrays

DAILY_WINDOW_START = time(0, 5)
DAILY_WINDOW_END = time(6, 0)
DAILY_NOTE_AT = time(0, 10)
DAILY_RETRY = timedelta(minutes=15)
MONITOR_INTERVAL_S = 60.0
BOARD_SNAPSHOT_EVERY = timedelta(minutes=15)
RESCORE_EVERY = timedelta(hours=6)
FLATTEN_RETRY = timedelta(minutes=5)
CANDLE_REFRESH = timedelta(hours=6)
CANDLE_WINDOW = 500
"""Closed bars handed to the strategies (>= min_bars 300 + the longest lookback)."""
SIM_FAILURES_TO_BLOCKLIST = 3
CONSECUTIVE_FAILURES_TO_PAUSE = 3
REF_DISAGREEMENT = Decimal("0.05")
REF_DISAGREEMENT_EMERGENCY = Decimal("0.10")
FLATTEN_STRATEGY = "flatten"
STALE = timedelta(days=3650)

SleepFn = Callable[[float], Awaitable[None]]


class Deps(BaseModel):
    """Everything the agent talks to; wired by ``tiller.cli`` (or by fakes in tests)."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    venue: Any
    rpc: Any | None = None
    candles: Any
    prices: Any
    cex: Any | None = None
    tokens: Any | None = None
    familiars: Any | None = None
    ledger: Ledger
    strategies: list[Any] = Field(default_factory=list)
    poster: Any | None = None
    shadow: Any | None = None
    feeds: tuple[Any | None, Any | None] = (None, None)
    alerts: Any
    signer_pubkey: str
    local_overrides: Path | None = None
    error_rate_sources: list[Any] = Field(default_factory=list)
    lock: Any | None = None


class TickReport(DomainModel):
    ts: datetime
    equity_usd: Decimal
    brakes: BrakeState
    intents: list[OrderIntent] = Field(default_factory=list)
    fills: list[Fill] = Field(default_factory=list)
    refused: list[tuple[OrderIntent, str]] = Field(default_factory=list)
    flattened: bool = False
    daily_evaluated: bool = False
    limits: Limits | None = None
    notes: list[str] = Field(default_factory=list)
    halted: bool = False


class TickPlan(DomainModel):
    """Output of the pure planner."""

    intents: list[OrderIntent]
    approved: list[tuple[OrderIntent, SizedOrder]]
    refused: list[tuple[OrderIntent, str]]


# --------------------------------------------------------------------------- pure planner


def sleeve_budget_remaining(strategy: str, acct: AccountSnapshot, cfg: Config) -> Decimal | None:
    """USD still available to ``strategy`` inside its sleeve (None for the netted core)."""
    if strategy == "core":
        return None
    budget = sleeve_budgets(cfg).get(strategy)
    if budget is None:
        return Decimal(0)
    used = sum(
        (current_value_usd(p.mint, acct, cfg) for p in acct.positions if p.strategy == strategy), Decimal(0)
    )
    return max(Decimal(0), budget * acct.equity_usd - used)


def plan_tick(
    acct: AccountSnapshot,
    targets: list[TargetExposure],
    state: AgentState,
    cfg: Config,
    *,
    limits: Limits | None,
    brakes: BrakeState,
    blocklist: set[str],
    sigma90: float | None,
    now: datetime,
    gates: dict[str, GateResult] | None = None,
) -> TickPlan:
    """Pure: ordered plan with sized entries and refusals. Exits are never blocked."""
    intents = plan(targets, acct, sigma90, cfg, now=now)
    approved: list[tuple[OrderIntent, SizedOrder]] = []
    refused: list[tuple[OrderIntent, str]] = []
    entries = 0
    allowlist = {SOL_MINT, USDC_MINT, cfg.strategies.hold_mint}
    for intent in intents:
        if intent.side == "sell":
            approved.append((intent, SizedOrder(usd=intent.usd, binding_cap="exit")))
            continue
        if limits is None:
            refused.append((intent, "owner limits unreadable (fail closed)"))
            continue
        decision = check_entry(
            intent, acct, brakes, limits, cfg, acct.day, blocklist, entries_this_tick=entries
        )
        if not decision.allowed:
            refused.append((intent, "; ".join(decision.reasons)))
            continue
        liquidity: Decimal | None = None
        if intent.mint not in allowlist:
            gate = (gates or {}).get(intent.mint)
            if gate is None or not gate.ok:
                refused.append(
                    (intent, "token gate: " + ("; ".join(gate.reasons) if gate else "not evaluated"))
                )
                continue
            liquidity = gate.liquidity_usd
        stop_pct: Decimal | None = None
        if intent.exit is not None and intent.exit.stop_price is not None:
            mark = acct.marks.get(intent.mint)
            if mark is not None and mark > 0 and intent.exit.stop_price < mark:
                stop_pct = Decimal(1) - intent.exit.stop_price / mark
        sized = size_order(
            intent,
            acct,
            limits,
            cfg,
            liquidity,
            acct.day,
            stop_pct,
            sleeve_budget_usd=sleeve_budget_remaining(intent.strategy, acct, cfg),
        )
        if sized is None:
            refused.append((intent, "below min order after caps"))
            continue
        approved.append((intent, sized))
        entries += 1
    return TickPlan(intents=intents, approved=approved, refused=refused)


def in_daily_window(now: datetime) -> bool:
    t = now.timetz().replace(tzinfo=None)
    return DAILY_WINDOW_START <= t < DAILY_WINDOW_END


def sigma90_from_bars(bars: list[Candle], lookback: int = 90) -> float | None:
    """Annualised realised vol of the last ``lookback`` daily log returns (None if unavailable)."""
    if len(bars) < lookback + 1:
        return None
    _, _, _, c = candles_to_arrays(bars[-(lookback + 1) :])
    rv = float(realized_vol(c, lookback)[-1])
    return None if math.isnan(rv) else rv


def is_flat(acct: AccountSnapshot, cfg: Config) -> bool:
    """No tradeable non-USDC holding worth at least ``min_order_usd``."""
    threshold = Decimal(cfg.risk.min_order_usd)
    for mint in acct.holdings:
        if mint == USDC_MINT:
            continue
        if current_value_usd(mint, acct, cfg) >= threshold:
            return False
    return True


class DayOpenClock:
    """Clock floored to 00:00 UTC of the current day.

    :class:`FixtureVenue` fills at the first bar with ``ts >= now``; ticking inside the
    00:05-06:00 window would otherwise skip to the NEXT day's open. Floored, a tick on day D
    fills at the open of bar D, which is the study convention (signal on close D-1, fill at
    open D).
    """

    def __init__(self, inner: Clock) -> None:
        self.inner = inner

    def now(self) -> datetime:
        return utc_day_start(self.inner.now())


class CandlePrices:
    """PriceSource over daily bars: the open of the bar containing ``now`` (the latest bar
    with ``ts <= now``), so marks line up with :class:`FixtureVenue` fills on a
    :class:`DayOpenClock`. Before the first bar the first open is used."""

    def __init__(self, bars: dict[str, list[Candle]], clock: Clock) -> None:
        self.bars = {k: sorted(v, key=lambda b: b.ts) for k, v in bars.items()}
        self.clock = clock

    def price(self, mint: str) -> Decimal | None:
        bars = self.bars.get(mint)
        if not bars:
            return None
        now = self.clock.now()
        for b in reversed(bars):
            if b.ts <= now:
                return b.open
        return bars[0].open

    async def usd_prices(self, mints: list[str]) -> dict[str, Decimal]:
        out: dict[str, Decimal] = {}
        for m in mints:
            if m == USDC_MINT:
                out[m] = Decimal(1)
                continue
            px = self.price(m)
            if px is not None:
                out[m] = px
        return out


# --------------------------------------------------------------------------- agent


class Agent:
    """One trading process: ``tick`` once, ``run_forever`` on the 60 s monitor loop."""

    def __init__(
        self,
        cfg: Config,
        deps: Deps,
        clock: Clock,
        state_path: Path,
        kill_file: Path,
        *,
        paper_capital_usd: Decimal = Decimal(10_000),
        paper_sol: Decimal = Decimal("0.1"),
        reconcile_every: int = 10,
        sleep: SleepFn = asyncio.sleep,
    ) -> None:
        self.cfg = cfg
        self.deps = deps
        self.clock = clock
        self.state_path = Path(state_path)
        self.kill_file = Path(kill_file)
        self.paper_capital_usd = Decimal(paper_capital_usd)
        self.paper_sol = Decimal(paper_sol)
        self.reconcile_every = reconcile_every
        self._sleep = sleep
        self.state: AgentState = load_state(self.state_path)
        self.tick_no = 0
        self.last_report: TickReport | None = None
        self.last_reconcile: ReconcileReport | None = None
        self._bars: dict[str, list[Candle]] = {}
        self._bars_fetched_at: datetime | None = None
        self._daily_attempt_at: datetime | None = None
        self._last_flatten_attempt: datetime | None = None
        self._last_board_snapshot: datetime | None = None
        self._last_rescore: datetime | None = None
        self._note_day: datetime | None = None
        self._symbols: dict[str, str] = {SOL_MINT: "SOL", USDC_MINT: "USDC"}
        self._decimals: dict[str, int] = {}
        self._stop = False

    # ------------------------------------------------------------------ helpers

    @property
    def live(self) -> bool:
        return self.cfg.mode == "live"

    @property
    def ledger(self) -> Ledger:
        return self.deps.ledger

    def _event(self, level: str, kind: str, payload: dict[str, Any] | None = None) -> None:
        self.ledger.add_event(level, kind, payload or {})
        self.deps.alerts.log(
            kind, level="warn" if level in ("warn", "warning", "critical") else "info", **(payload or {})
        )

    async def _alert(self, level: str, text: str) -> None:
        try:
            await self.deps.alerts.send(level, text)
        except Exception:
            pass

    def _save(self) -> None:
        save_state(self.state_path, self.state)

    async def _decimals_of(self, mint: str) -> int:
        if mint == SOL_MINT:
            return SOL_DECIMALS
        if mint == USDC_MINT:
            return USDC_DECIMALS
        if mint not in self._decimals:
            if self.deps.tokens is None:
                raise ValueError(f"decimals unknown for {mint} (no token data source)")
            self._decimals[mint] = int(await self.deps.tokens.decimals(mint))
        return self._decimals[mint]

    # ------------------------------------------------------------------ paper book

    async def _ensure_paper_capital(self, now: datetime) -> None:
        if self.live or self.paper_capital_usd <= 0:
            return
        rows = self.ledger._db.execute("SELECT COUNT(*) AS n FROM transfers").fetchone()
        if int(rows["n"]) > 0:
            return
        self.ledger.record_transfer(
            now, USDC_MINT, int(self.paper_capital_usd * 10**USDC_DECIMALS), self.paper_capital_usd, "in"
        )
        if self.paper_sol > 0:
            try:
                px = (await self.deps.prices.usd_prices([SOL_MINT])).get(SOL_MINT, Decimal(0))
            except Exception:
                px = Decimal(0)
            self.ledger.record_transfer(
                now, SOL_MINT, int(self.paper_sol * 10**SOL_DECIMALS), self.paper_sol * Decimal(px), "in"
            )
        self._event(
            "info", "paper.capital_recorded", {"usd": str(self.paper_capital_usd), "sol": str(self.paper_sol)}
        )

    # ------------------------------------------------------------------ steps

    async def _reconcile_step(self, now: datetime) -> None:
        if not self.live or self.deps.rpc is None:
            self.state.last_reconcile_ok = True
            self.state.reconcile_mismatch_ticks = 0
            return
        detail = None
        fam = self.deps.familiars
        if fam is not None and self.cfg.familiars.handle:
            try:
                detail = await fam.agent(self.cfg.familiars.handle)
            except Exception:
                detail = None
        try:
            report = await reconcile(
                self.deps.rpc,
                self.ledger,
                self.deps.prices,
                self.deps.signer_pubkey,
                detail,
                self.clock,
                reserve_lamports=self.cfg.wallet.sol_reserve_lamports,
                token_decimals=dict(self._decimals),
            )
        except Exception as e:
            self._event("warn", "reconcile.error", {"error": f"{type(e).__name__}: {e}"})
            self.state.last_reconcile_ok = False
            self.state.reconcile_mismatch_ticks += 1
            return
        self.last_reconcile = report
        self.state.last_reconcile_ok = report.ok
        if report.ok:
            self.state.reconcile_mismatch_ticks = 0
        else:
            self.state.reconcile_mismatch_ticks += 1
            if self.state.reconcile_mismatch_ticks >= 2:
                await self._alert(
                    "warn",
                    f"reconcile mismatch {report.usd_mismatch_pct:.2%} of equity for "
                    f"{self.state.reconcile_mismatch_ticks} ticks; entries blocked until `tiller reconcile --accept`",
                )

    async def _owner_limits(self, now: datetime) -> tuple[OwnerLimits | None, OwnerLimits | None]:
        owner: OwnerLimits | None = None
        if self.cfg.familiars.enabled and self.deps.familiars is not None:
            try:
                owner = await self.deps.familiars.me()
            except Exception as e:
                self._event("warn", "owner_limits.error", {"error": type(e).__name__})
                owner = OwnerLimits(readable=False, read_at=now)
        elif self.cfg.familiars.enabled and self.live:
            owner = OwnerLimits(readable=False, read_at=now)  # live without a client: fail closed
        local = load_local_overrides(self.deps.local_overrides, now)
        return owner, local

    def _symbols_needed(self) -> list[str]:
        symbols = ["SOL"]
        s = self.cfg.strategies
        if (s.sol_trend_ensemble.enabled and s.sol_trend_ensemble.btc_confirm) or (
            s.sol_regime_switch.enabled and s.sol_regime_switch.btc_confirm
        ):
            symbols.append("BTC")
        return symbols

    def _expected_last_bar(self, now: datetime) -> datetime:
        return utc_day_start(now) - timedelta(days=1)

    async def _candles(self, now: datetime) -> tuple[dict[str, list[Candle]], timedelta]:
        """Refresh closed bars when needed; returns (bars by symbol, age of the SOL data)."""
        need = self._bars_fetched_at is None or now - self._bars_fetched_at > CANDLE_REFRESH
        sol = self._bars.get("SOL") or []
        if not need and sol and sol[-1].ts < self._expected_last_bar(now):
            need = self._daily_attempt_at is None or now - self._daily_attempt_at >= DAILY_RETRY
        if need:
            fresh: dict[str, list[Candle]] = {}
            for sym in self._symbols_needed():
                try:
                    bars = await self.deps.candles.closed_bars(sym, 300)
                except (CandleSourceError, InsufficientCandles, Exception) as e:
                    self._event("warn", "candles.error", {"symbol": sym, "error": f"{type(e).__name__}: {e}"})
                    bars = self._bars.get(sym, [])
                fresh[sym] = closed_daily_bars(bars, now)[-CANDLE_WINDOW:]
            self._bars = fresh
            self._bars_fetched_at = now
            if not sol or (fresh.get("SOL") and fresh["SOL"][-1].ts < self._expected_last_bar(now)):
                self._daily_attempt_at = now
        sol = self._bars.get("SOL") or []
        if not sol:
            return self._bars, STALE
        age = now - (sol[-1].ts + timedelta(days=1))
        return self._bars, age

    def _strategy_targets(
        self, bars: dict[str, list[Candle]], acct: AccountSnapshot, now: datetime, data_stale: bool
    ) -> tuple[list[TargetExposure], bool]:
        targets: list[TargetExposure] = []
        evaluated = False
        sol = bars.get("SOL") or []
        last_ts = sol[-1].ts if sol else None
        hold = self.cfg.strategies.hold_mint
        regime = self.state.sleeves.get("sol_regime_switch")
        regime_on = regime.regime_on if regime is not None else None
        for strat in self.deps.strategies:
            prev = self.state.sleeves.get(strat.name, SleeveState())
            cadence = getattr(strat, "cadence", "daily")
            if cadence == "daily":
                due = (
                    in_daily_window(now)
                    and not data_stale
                    and last_ts is not None
                    and prev.last_bar_ts != last_ts
                )
            else:
                due = True
            if due:
                ctx = MarketContext(
                    now=now,
                    candles=bars,
                    equity_usd=acct.equity_usd,
                    positions=acct.positions,
                    sleeve_state=dict(self.state.sleeves),
                    regime_on=regime_on,
                )
                try:
                    tl, new_state = strat.targets(ctx)
                except Exception as e:
                    self._event(
                        "warn",
                        "strategy.error",
                        {"strategy": strat.name, "error": f"{type(e).__name__}: {e}"},
                    )
                    continue
                self.state.sleeves[strat.name] = new_state
                targets.extend(tl)
                if cadence == "daily" and new_state.last_bar_ts == last_ts:
                    evaluated = True
                    self._event(
                        "info",
                        "strategy.evaluated",
                        {
                            "strategy": strat.name,
                            "bar": last_ts.isoformat() if last_ts else None,
                            "weight": new_state.last_weight,
                            "reasons": [t.reason for t in tl],
                        },
                    )
            elif cadence == "daily" and prev.last_bar_ts is not None:
                targets.append(
                    TargetExposure(
                        mint=hold,
                        weight=Decimal(str(prev.last_weight)),
                        strategy=strat.name,
                        reason=f"carried from bar {prev.last_bar_ts.date().isoformat()}",
                    )
                )
        return targets, evaluated

    async def _gates(self, targets: list[TargetExposure], now: datetime) -> dict[str, GateResult]:
        allow = {SOL_MINT, USDC_MINT, self.cfg.strategies.hold_mint}
        mints = sorted({t.mint for t in targets if t.mint not in allow and t.weight > 0})
        out: dict[str, GateResult] = {}
        if not mints:
            return out
        tokens = self.deps.tokens
        blocklist = self.ledger.blocklist_active(now)
        own = set(self.cfg.familiars.own_token_mints)
        gate_cfg = gate_cfg_from_thresholds("established", self.cfg.guard.token_gate_established)
        for mint in mints:
            if tokens is None:
                out[mint] = GateResult(ok=False, reasons=["no token data source"])
                continue
            try:
                info = await tokens.token_info(mint)
                shield = await tokens.shield([mint])
                acc = await tokens.mint_account(mint)
            except Exception as e:
                out[mint] = GateResult(ok=False, reasons=[f"token data error: {type(e).__name__}"])
                continue
            warnings = None if shield is None else shield.get(mint, [])
            out[mint] = evaluate_token_gate(
                mint, info, warnings, acc, gate_cfg, self.deps.signer_pubkey, own, blocklist, now, allow
            )
            if info is not None:
                self._symbols[mint] = info.symbol
        return out

    async def _reference_price(self, mint: str, *, is_exit: bool, emergency: bool) -> Decimal:
        warnings: list[str] = []
        jup: Decimal | None = None
        try:
            jup = (await self.deps.prices.usd_prices([mint])).get(mint)
        except Exception as e:
            warnings.append(f"jupiter price error: {type(e).__name__}")
        cex: Decimal | None = None
        if mint == SOL_MINT and self.deps.cex is not None:
            try:
                cex = Decimal(await self.deps.cex.sol_usd_mid())
            except Exception as e:
                warnings.append(f"cex mid error: {type(e).__name__}")
        tol = REF_DISAGREEMENT_EMERGENCY if emergency else REF_DISAGREEMENT
        single_ok = is_exit or emergency or mint != SOL_MINT or self.cfg.mode == "offline"
        ref = reference_price(jup, cex, tol, allow_single_source=single_ok, warnings=warnings)
        if warnings:
            self._event("warn", "reference.warning", {"mint": mint, "warnings": warnings})
        return ref

    def _sim_failure(self, mint: str, now: datetime) -> None:
        since = now - timedelta(hours=self.cfg.risk.sim_fail_blocklist_h)
        n = 0
        for e in self.ledger.events(limit=200, kind="sim_rejected"):
            ts = datetime.fromisoformat(str(e["ts"]))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            if ts >= since and e["payload"].get("mint") == mint:
                n += 1
        if n >= SIM_FAILURES_TO_BLOCKLIST:
            until = now + timedelta(hours=self.cfg.risk.sim_fail_blocklist_h)
            self.ledger.blocklist_add(mint, until, f"{n} failed simulations")
            self._event("warn", "blocklist.added", {"mint": mint, "until": until.isoformat()})

    def _failure(self, now: datetime) -> None:
        before = self.state.brakes.paused_until
        self.state.brakes = record_failure(
            self.state.brakes, now, self.cfg.risk, threshold=CONSECUTIVE_FAILURES_TO_PAUSE
        )
        if self.state.brakes.paused_until != before and self.state.brakes.paused_until is not None:
            self._event("warn", "loop_guard.paused", {"until": self.state.brakes.paused_until.isoformat()})

    def _trade_context(
        self, intent: OrderIntent, fill: Fill, ref: Decimal, brakes: BrakeState
    ) -> TradeContext:
        regime = self.state.sleeves.get("sol_regime_switch")
        return TradeContext(
            strategy=intent.strategy,
            rule=intent.reason[:120],
            side=intent.side,
            symbol=self._symbols.get(intent.mint, intent.mint[:6]),
            mint=intent.mint,
            notional_usd=fill.usd_in if intent.side == "buy" else fill.usd_out,
            price_usd=ref,
            stop_price=intent.exit.stop_price if intent.exit is not None else None,
            regime_on=regime.regime_on if regime is not None else None,
            brake_state="entries blocked" if brakes.entries_blocked else "normal",
            paper=fill.paper,
            signature=fill.signature,
        )

    async def _execute(
        self,
        intent: OrderIntent,
        sized: SizedOrder,
        acct: AccountSnapshot,
        gates: dict[str, GateResult],
        brakes: BrakeState,
        now: datetime,
        *,
        emergency: bool = False,
    ) -> tuple[Fill | None, str | None]:
        mint = intent.mint
        mode = "emergency" if emergency else "normal"
        try:
            dec = await self._decimals_of(mint)
        except Exception as e:
            return None, f"decimals: {type(e).__name__}"
        if intent.side == "buy":
            amount = int((sized.usd * Decimal(10**USDC_DECIMALS)).to_integral_value(ROUND_DOWN))
            req = SwapRequest(
                input_mint=USDC_MINT,
                output_mint=mint,
                amount_base=amount,
                strategy=intent.strategy,
                reason=intent.reason[:200],
                mode=mode,
            )
        else:
            available = tradeable_base(mint, acct, self.cfg)
            if intent.is_exit or intent.amount_base is not None:
                amount = min(available, intent.amount_base or available)
            else:
                mark = acct.marks.get(mint)
                if mark is None or mark <= 0:
                    return None, "no mark for sell sizing"
                amount = min(
                    available, int((sized.usd / mark * Decimal(10**dec)).to_integral_value(ROUND_DOWN))
                )
            if amount <= 0:
                return None, "nothing to sell"
            req = SwapRequest(
                input_mint=mint,
                output_mint=USDC_MINT,
                amount_base=amount,
                strategy=intent.strategy,
                reason=intent.reason[:200],
                mode=mode,
            )
        try:
            ref = await self._reference_price(mint, is_exit=intent.side == "sell", emergency=emergency)
        except (ReferenceUnavailable, ReferenceDisagreement) as e:
            self._event("warn", "reference.unavailable", {"mint": mint, "error": str(e)})
            return None, f"reference price: {e}"
        gate = gates.get(mint)
        try:
            fill = await self.deps.venue.swap(req, ref, gate)
        except GuardRejected as e:
            if e.stage == "simulation":
                self._sim_failure(mint, now)
            self._failure(now)
            return None, f"guard {e.stage}: {e.reason}"
        except ExecutionUncertain as e:
            self._failure(now)
            await self._alert("critical", f"execution uncertain for {mint}: {e}")
            return None, f"uncertain: {e}"
        except ExecutionFailed as e:
            self._failure(now)
            return None, f"failed: {e}"
        except Exception as e:
            self._failure(now)
            self._event("warn", "execution.error", {"mint": mint, "error": f"{type(e).__name__}: {e}"})
            return None, f"error: {type(e).__name__}: {e}"
        self.state.brakes = record_success(self.state.brakes)
        if intent.side == "buy" and intent.exit is not None:
            try:
                self.ledger.set_position_exit(mint, intent.strategy, intent.exit)
            except Exception:
                pass
        ctx = self._trade_context(intent, fill, ref, brakes)
        if self.deps.poster is not None and not fill.paper and fill.signature:
            try:
                self.deps.poster.enqueue_trade(fill, ctx)
            except Exception as e:
                self._event("warn", "post.enqueue_error", {"error": type(e).__name__})
        if not fill.paper and fill.signature:
            if intent.side == "buy":
                self.state.canary.verified_buy = True
            else:
                self.state.canary.verified_sell = True
        self._event(
            "info",
            "fill",
            {
                "mint": mint,
                "side": intent.side,
                "usd": str(sized.usd),
                "binding_cap": sized.binding_cap,
                "signature": fill.signature,
                "paper": fill.paper,
                "mode": mode,
            },
        )
        return fill, None

    def _update_trailing_stops(self, acct: AccountSnapshot) -> None:
        for pos in acct.positions:
            new_rule = ratchet_trailing_stop(pos, acct.marks.get(pos.mint), acct.decimals)
            if new_rule is not None:
                self.ledger.set_position_exit(pos.mint, pos.strategy, new_rule)

    def _post_verified(self) -> bool:
        if self.state.canary.verified_post:
            return True
        try:
            rows = self.ledger.posts_since(datetime(2000, 1, 1, tzinfo=UTC), kind="trade")
        except Exception:
            return False
        return any(r.state == "posted" for r in rows)

    async def _snapshot(self, *, record: bool = True) -> AccountSnapshot:
        """Build the snapshot, resolving decimals for any newly seen token first."""
        source: BalanceSource = "chain" if self.live else "paper"
        acct = await build_snapshot(
            self.deps.rpc,
            self.deps.prices,
            self.ledger,
            self.deps.signer_pubkey,
            self.clock,
            None,
            source=source,
            decimals=dict(self._decimals),
            record=False,
        )
        missing = [m for m in acct.holdings if m not in (SOL_MINT, USDC_MINT) and m not in self._decimals]
        if missing:
            for m in missing:
                try:
                    await self._decimals_of(m)
                except Exception as e:
                    self._event("warn", "decimals.unknown", {"mint": m, "error": type(e).__name__})
            acct = await build_snapshot(
                self.deps.rpc,
                self.deps.prices,
                self.ledger,
                self.deps.signer_pubkey,
                self.clock,
                None,
                source=source,
                decimals=dict(self._decimals),
                record=False,
            )
        if record:
            record_snapshot(self.ledger, acct)
            acct = acct.model_copy(update={"day": self.ledger.day_stats(utc_day_start(acct.ts))})
        return acct

    async def _board_and_copy_hooks(self, now: datetime) -> None:
        fam = self.deps.familiars
        if fam is not None and (
            self._last_board_snapshot is None or now - self._last_board_snapshot >= BOARD_SNAPSHOT_EVERY
        ):
            self._last_board_snapshot = now
            calls: list[Callable[[], Awaitable[Any]]] = [lambda: fam.agents("7D"), lambda: fam.tokens()]
            for call in calls:
                try:
                    await call()  # the client persists the raw response itself
                except Exception as e:
                    self._event("warn", "board.snapshot_error", {"error": type(e).__name__})
        for feed in self.deps.feeds:
            poll = getattr(feed, "poll", None)
            if poll is None:
                continue
            try:
                await poll(now)
            except Exception as e:
                self._event("warn", "copy.feed_error", {"error": type(e).__name__})
        shadow = self.deps.shadow
        if shadow is not None:
            mark = getattr(shadow, "mark", None)
            if mark is not None:
                try:
                    await mark(now)
                except Exception as e:
                    self._event("warn", "copy.shadow_error", {"error": type(e).__name__})
            rescore = getattr(shadow, "rescore", None)
            if rescore is not None and (
                self._last_rescore is None or now - self._last_rescore >= RESCORE_EVERY
            ):
                self._last_rescore = now
                try:
                    await rescore(now)
                except Exception as e:
                    self._event("warn", "copy.rescore_error", {"error": type(e).__name__})

    async def _posting(self, acct: AccountSnapshot, brakes: BrakeState, now: datetime) -> None:
        poster = self.deps.poster
        if poster is None:
            return
        try:
            await poster.flush()
        except Exception as e:
            self._event("warn", "post.flush_error", {"error": type(e).__name__})
        day = utc_day_start(now)
        if now.timetz().replace(tzinfo=None) >= DAILY_NOTE_AT and self._note_day != day and self.live:
            self._note_day = day
            try:
                await poster.daily_note(acct, brakes)
            except Exception as e:
                self._event("warn", "post.note_error", {"error": type(e).__name__})

    # ------------------------------------------------------------------ flatten

    async def flatten(self, reason: str) -> list[Fill]:
        """Emergency exit of every non-USDC holding, smallest first; sets ``halted``."""
        now = self.clock.now()
        self._last_flatten_attempt = now
        self._event("critical", "flatten.start", {"reason": reason})
        await self._alert("critical", f"FLATTEN: {reason}")
        acct = await self._snapshot(record=False)
        items: list[tuple[Decimal, str, int]] = []
        for mint in acct.holdings:
            if mint == USDC_MINT:
                continue
            base = tradeable_base(mint, acct, self.cfg)
            usd = current_value_usd(mint, acct, self.cfg)
            if base <= 0 or usd < Decimal(self.cfg.risk.min_order_usd):
                continue
            items.append((usd, mint, base))
        items.sort()
        fills: list[Fill] = []
        brakes = self.state.brakes
        for usd, mint, base in items:
            intent = OrderIntent(
                mint=mint,
                side="sell",
                usd=usd,
                strategy=FLATTEN_STRATEGY,
                reason=reason[:200],
                is_exit=True,
                amount_base=base,
            )
            fill, err = await self._execute(
                intent, SizedOrder(usd=usd, binding_cap="flatten"), acct, {}, brakes, now, emergency=True
            )
            if fill is not None:
                fills.append(fill)
            else:
                await self._alert("critical", f"flatten of {mint} failed: {err}")
        self.state.halted = True
        self.state.halt_reason = reason
        self._save()
        remaining = len(items) - len(fills)
        self._event("critical", "flatten.done", {"fills": len(fills), "remaining": remaining})
        return fills

    # ------------------------------------------------------------------ tick

    async def tick(self) -> TickReport:
        now = self.clock.now()
        notes: list[str] = []
        if self.deps.lock is not None:
            self.deps.lock.heartbeat()
        self.state = load_state(self.state_path)
        self.tick_no += 1
        await self._ensure_paper_capital(now)
        if self.tick_no == 1 or self.tick_no % self.reconcile_every == 0:
            await self._reconcile_step(now)

        owner, local = await self._owner_limits(now)
        self.state.last_owner_limits = owner
        limits = effective_limits(
            self.cfg.risk, owner, local, self.state.canary, now, canary_enabled=self.live
        )
        directive = limits.directive if limits is not None else None
        if limits is None and local is not None and local.readable:
            directive = parse_directive(local.instructions)  # a liquidate/pause order still counts

        bars, data_age = await self._candles(now)
        data_stale = data_age > timedelta(hours=self.cfg.risk.max_data_age_h)
        try:
            acct = await self._snapshot()
        except Exception as e:
            self._event("warn", "snapshot.error", {"error": f"{type(e).__name__}: {e}"})
            brakes = self.state.brakes.model_copy(
                update={"entries_blocked": True, "reasons": ["snapshot_failed"]}
            )
            self._save()
            report = TickReport(
                ts=now,
                equity_usd=Decimal(0),
                brakes=brakes,
                notes=[f"snapshot failed: {e}"],
                halted=self.state.halted,
            )
            self.last_report = report
            return report
        for w in acct.warnings:
            notes.append(w)
        error_rate = max((float(s.error_rate()) for s in self.deps.error_rate_sources), default=0.0)
        kill_present, kill_liquidate = read_kill_file(self.kill_file)
        prev_blocked = self.state.brakes.entries_blocked
        brakes = brake_state(
            acct, self.cfg.risk, data_age, limits, error_rate, self.state, now, kill_file=kill_present
        )
        brakes = brakes.model_copy(update={"consecutive_failures": self.state.brakes.consecutive_failures})
        tripped = brakes.entries_blocked and not prev_blocked
        self.state.brakes = brakes
        if tripped:
            await self._alert("warn", "entries blocked: " + "; ".join(brakes.reasons))

        fills: list[Fill] = []
        refused: list[tuple[OrderIntent, str]] = []
        intents: list[OrderIntent] = []
        flattened = False
        evaluated = False
        reason = must_flatten(acct, self.cfg, directive, kill_liquidate, self.state)
        if reason is not None and reason.startswith("dd30") and is_flat(acct, self.cfg):
            # nothing left to protect: the dd30 ENTRY brake keeps us flat; an explicit liquidate order
            # (directive / KILL file) still halts even when flat.
            reason = None
        if reason is not None and not self.state.halted:
            fills = await self.flatten(reason)
            flattened = True
        elif self.state.halted:
            if not is_flat(acct, self.cfg) and (
                self._last_flatten_attempt is None or now - self._last_flatten_attempt >= FLATTEN_RETRY
            ):
                fills = await self.flatten(self.state.halt_reason or "halted: retry until flat")
                flattened = True
            notes.append("halted; run `tiller resume` to trade again")
        else:
            self._update_trailing_stops(acct)
            acct = acct.model_copy(update={"positions": self.ledger.positions()})
            targets, evaluated = self._strategy_targets(bars, acct, now, data_stale)
            gates = await self._gates(targets, now)
            sigma = sigma90_from_bars(bars.get("SOL") or [])
            tp = plan_tick(
                acct,
                targets,
                self.state,
                self.cfg,
                limits=limits,
                brakes=self.state.brakes,
                blocklist=self.ledger.blocklist_active(now),
                sigma90=sigma,
                now=now,
                gates=gates,
            )
            intents = tp.intents
            refused = list(tp.refused)
            for intent, sized in tp.approved:
                fill, err = await self._execute(intent, sized, acct, gates, self.state.brakes, now)
                if fill is not None:
                    fills.append(fill)
                else:
                    refused.append((intent, err or "unknown"))
            for intent, why in refused:
                self._event(
                    "info",
                    "intent.refused",
                    {"mint": intent.mint, "side": intent.side, "usd": str(intent.usd), "reason": why},
                )

        await self._posting(acct, self.state.brakes, now)  # flush first so a posted trade counts now
        verified = (self.state.canary.verified_buy, self.state.canary.verified_sell, self._post_verified())
        self.state.canary = advance_canary(self.state.canary, verified, tripped, now, self.cfg.risk.canary)
        await self._board_and_copy_hooks(now)
        self._save()
        report = TickReport(
            ts=now,
            equity_usd=acct.equity_usd,
            brakes=self.state.brakes,
            intents=intents,
            fills=fills,
            refused=refused,
            flattened=flattened,
            daily_evaluated=evaluated,
            limits=limits,
            notes=notes,
            halted=self.state.halted,
        )
        self.last_report = report
        self.deps.alerts.log(
            "tick",
            **snapshot_summary(acct),
            fills=len(fills),
            refused=len(refused),
            blocked=self.state.brakes.entries_blocked,
        )
        return report

    def stop(self) -> None:
        self._stop = True

    async def run_forever(
        self, *, interval_s: float = MONITOR_INTERVAL_S, max_ticks: int | None = None
    ) -> None:
        """60 s monitor loop; the daily job, board snapshots and re-scores are scheduled inside ``tick``."""
        n = 0
        while not self._stop:
            try:
                await self.tick()
            except Exception as e:
                self._event("warn", "tick.error", {"error": f"{type(e).__name__}: {e}"})
                await self._alert("warn", f"tick error: {type(e).__name__}: {e}")
            n += 1
            if max_ticks is not None and n >= max_ticks:
                return
            await self._sleep(interval_s)
