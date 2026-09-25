"""Allocator: nets strategy targets per mint and turns them into an ordered order plan.

Rules (spec L2 / 'Netting + portfolio SOL-beta cap'):

1. each strategy's weights are clipped to its effective sleeve budget (disabled sleeves
   contribute 0 because :func:`tiller.config.effective_allocation` folds them into cash);
2. weights are summed per mint; the hold mint (SOL) total is capped by
   ``min(sol_beta_cap_max, sol_beta_cap_vol / sigma90_SOL)``;
3. total non-cash weight is limited to ``1 - effective cash floor`` (pro-rata scaling);
4. every mint other than the hold mint is capped at ``max_token_pct`` of equity;
5. ``delta = target_usd - current_usd``; a trade is emitted only when
   ``|delta| >= max(rebalance_band_pct * equity, min_order_usd)``, except that a ZERO target
   is an exit and closes the whole holding whenever it is worth at least ``min_order_usd``;
   buys are additionally clipped to the USDC headroom above the cash floor;
6. exits (positions whose :class:`ExitRule` fired, and sells to a zero target) come first,
   then other sells, then at most ``max_entries_per_tick`` buys (largest first).

Units: weights are fractions of TOTAL equity; ``usd`` is USD (``Decimal``); base units are
integers (lamports for SOL). All functions here are pure.
"""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from typing import Literal

from tiller.config import Config, RiskCfg, effective_allocation
from tiller.models import LAMPORTS_PER_SOL, SOL_MINT, USDC_MINT, DomainModel, ExitRule, Position
from tiller.risk.account import AccountSnapshot, holding_units, mint_decimals_of
from tiller.strategies.base import TargetExposure

CORE_STRATEGY = "core"
"""Strategy label booked for the netted hold-mint (A+B) position."""

SLEEVE_OF: dict[str, str] = {
    "sol_trend_ensemble": "sol_trend_ensemble",
    "sol_regime_switch": "sol_regime_switch",
    "copy_consensus": "copy",
    "breakout_4h": "breakout",
}
"""Strategy name -> allocation sleeve. Unknown strategies get no budget (fail closed)."""


class OrderIntent(DomainModel):
    """One planned swap. ``usd`` is the notional; ``amount_base`` is set for full exits."""

    mint: str
    side: Literal["buy", "sell"]
    usd: Decimal
    strategy: str
    reason: str
    exit: ExitRule | None = None
    is_exit: bool = False
    amount_base: int | None = None


def sol_beta_cap(sigma90_sol: float | None, cfg: RiskCfg) -> float:
    """Portfolio SOL-beta cap ``min(cap_max, cap_vol / max(sigma, 0.05))``; ``cap_max`` when sigma is unknown."""
    if sigma90_sol is None or sigma90_sol != sigma90_sol:  # None or nan
        return float(cfg.sol_beta_cap_max)
    return float(min(cfg.sol_beta_cap_max, cfg.sol_beta_cap_vol / max(float(sigma90_sol), 0.05)))


def rebalance_band_usd(equity_usd: Decimal, cfg: RiskCfg) -> Decimal:
    """``max(rebalance_band_pct * equity, min_order_usd)`` in USD."""
    return max(Decimal(str(cfg.rebalance_band_pct)) * equity_usd, Decimal(cfg.min_order_usd))


def sleeve_budgets(cfg: Config) -> dict[str, Decimal]:
    """Strategy name -> maximum weight (fraction of equity) from the effective allocation."""
    alloc = effective_allocation(cfg)
    return {name: Decimal(str(alloc.get(sleeve, 0.0) / 100.0)) for name, sleeve in SLEEVE_OF.items()}


def cash_floor_fraction(cfg: Config) -> Decimal:
    """Effective USDC floor as a fraction of equity (hard 30% plus disabled sleeves' budgets)."""
    return Decimal(str(effective_allocation(cfg)["cash"] / 100.0))


class NettedTarget(DomainModel):
    """Per-mint netted target after all caps."""

    mint: str
    weight: Decimal
    strategies: list[str]
    reasons: list[str]
    exit: ExitRule | None = None


def net_targets(
    targets: list[TargetExposure], sigma90_sol: float | None, cfg: Config
) -> dict[str, NettedTarget]:
    """Steps 1-4: clip per sleeve, sum per mint, beta cap, cash floor, per-token cap."""
    budgets = sleeve_budgets(cfg)
    hold = cfg.strategies.hold_mint
    per_strategy: dict[str, Decimal] = {}
    netted: dict[str, NettedTarget] = {}
    for t in targets:
        budget = budgets.get(t.strategy)
        if budget is None or budget <= 0:
            continue  # disabled or unknown sleeve: contributes nothing
        w = max(Decimal(0), Decimal(t.weight))
        used = per_strategy.get(t.strategy, Decimal(0))
        w = min(w, max(Decimal(0), budget - used))
        per_strategy[t.strategy] = used + w
        cur = netted.get(t.mint)
        if cur is None:
            netted[t.mint] = NettedTarget(
                mint=t.mint,
                weight=w,
                strategies=[t.strategy],
                reasons=[f"{t.strategy}: {t.reason}"],
                exit=t.exit,
            )
        else:
            cur.weight += w
            cur.strategies.append(t.strategy)
            cur.reasons.append(f"{t.strategy}: {t.reason}")
            if cur.exit is None or (t.exit is not None and w > 0):
                cur.exit = t.exit if t.exit is not None else cur.exit
    if hold in netted:
        cap = Decimal(str(sol_beta_cap(sigma90_sol, cfg.risk)))
        if netted[hold].weight > cap:
            netted[hold].reasons.append(f"beta cap {cap:.4f} binds")
            netted[hold].weight = cap
    max_non_cash = Decimal(1) - cash_floor_fraction(cfg)
    total = sum((n.weight for n in netted.values()), Decimal(0))
    if total > max_non_cash and total > 0:
        scale = max_non_cash / total
        for n in netted.values():
            n.weight = n.weight * scale
            n.reasons.append(f"cash floor scaling x{scale:.4f}")
    token_cap = Decimal(str(cfg.risk.max_token_pct))
    for n in netted.values():
        if n.mint != hold and n.weight > token_cap:
            n.weight = token_cap
            n.reasons.append(f"per-token cap {token_cap} binds")
    return netted


def current_value_usd(mint: str, acct: AccountSnapshot, cfg: Config) -> Decimal:
    """USD value of the tradeable holding of ``mint`` (SOL net of the gas reserve)."""
    if mint == USDC_MINT:
        return acct.usdc_usd
    base = acct.holdings.get(mint, 0)
    if mint == SOL_MINT:
        base = max(0, base - cfg.wallet.sol_reserve_lamports)
    mark = acct.marks.get(mint)
    if mark is None:
        return Decimal(0)
    return holding_units(base, mint_decimals_of(mint, acct)) * mark


def tradeable_base(mint: str, acct: AccountSnapshot, cfg: Config) -> int:
    """Base units of ``mint`` that may be sold (SOL keeps the reserve)."""
    base = acct.holdings.get(mint, 0)
    if mint == SOL_MINT:
        base = max(0, base - cfg.wallet.sol_reserve_lamports)
    return base


def exit_fired(pos: Position, mark: Decimal | None, now: datetime) -> str | None:
    """Reason when a position's exit rule fired (stop price, time stop); None otherwise."""
    rule = pos.exit
    if rule is None:
        return None
    if rule.time_stop_at is not None and now >= rule.time_stop_at:
        return f"time stop {rule.time_stop_at.isoformat(timespec='minutes')} reached"
    if rule.stop_price is not None and mark is not None and mark < rule.stop_price:
        return f"stop {rule.stop_price} hit at {mark}"
    return None


def ratchet_trailing_stop(
    pos: Position, mark: Decimal | None, decimals: dict[str, int] | None = None
) -> ExitRule | None:
    """Raise ``stop_price`` for a trailing rule once the gain threshold is reached (never lowers).

    ``trail_pct`` is the distance below the mark; ``trail_from_gain_pct`` the unrealised gain
    (fraction of cost) from which trailing starts. Returns the updated rule or None if unchanged.
    """
    rule = pos.exit
    if rule is None or rule.trail_pct is None or mark is None or pos.amount_base <= 0 or pos.cost_usd <= 0:
        return None
    dec = 9 if pos.mint == SOL_MINT else (decimals or {}).get(pos.mint)
    if dec is None:
        return None
    units = Decimal(pos.amount_base) / Decimal(10**dec)
    if units <= 0:
        return None
    entry = pos.cost_usd / units
    gain = mark / entry - Decimal(1)
    threshold = rule.trail_from_gain_pct if rule.trail_from_gain_pct is not None else Decimal(0)
    if gain < threshold:
        return None
    candidate = mark * (Decimal(1) - rule.trail_pct)
    if rule.stop_price is not None and candidate <= rule.stop_price:
        return None
    return rule.model_copy(update={"stop_price": candidate})


def exit_intents(acct: AccountSnapshot, cfg: Config, now: datetime) -> list[OrderIntent]:
    """Full-close sell intents for every position whose exit rule fired."""
    out: list[OrderIntent] = []
    seen: set[str] = set()
    for pos in acct.positions:
        if pos.mint in seen or pos.mint == USDC_MINT:
            continue
        reason = exit_fired(pos, acct.marks.get(pos.mint), now)
        if reason is None:
            continue
        base = tradeable_base(pos.mint, acct, cfg)
        usd = current_value_usd(pos.mint, acct, cfg)
        if base <= 0 or usd <= 0:
            continue
        seen.add(pos.mint)
        out.append(
            OrderIntent(
                mint=pos.mint,
                side="sell",
                usd=usd,
                strategy=pos.strategy,
                reason=f"exit rule: {reason}",
                exit=None,
                is_exit=True,
                amount_base=base,
            )
        )
    return out


def _strategy_label(strategies: list[str], hold_mint: str, mint: str) -> str:
    if mint == hold_mint:
        return CORE_STRATEGY
    uniq = sorted(set(strategies))
    return uniq[0] if len(uniq) == 1 else "+".join(uniq)


def plan(
    targets: list[TargetExposure],
    acct: AccountSnapshot,
    sigma90_sol: float | None,
    cfg: Config,
    *,
    now: datetime | None = None,
) -> list[OrderIntent]:
    """Ordered plan: exits first, then sells, then at most ``max_entries_per_tick`` buys."""
    at = now or acct.ts
    equity = acct.equity_usd
    band = rebalance_band_usd(equity, cfg.risk)
    netted = net_targets(targets, sigma90_sol, cfg)
    exits = exit_intents(acct, cfg, at)
    exited = {e.mint for e in exits}
    sells: list[OrderIntent] = []
    buys: list[OrderIntent] = []
    floor_usd = cash_floor_fraction(cfg) * equity
    headroom = acct.usdc_usd - floor_usd
    for mint, n in netted.items():
        if mint == USDC_MINT or mint in exited:
            continue
        target_usd = n.weight * equity
        current = current_value_usd(mint, acct, cfg)
        delta = target_usd - current
        label = _strategy_label(n.strategies, cfg.strategies.hold_mint, mint)
        reason = "; ".join(n.reasons)
        full = n.weight == 0 and current >= Decimal(cfg.risk.min_order_usd)
        if delta <= -band or full:
            # a zero target is an exit: exempt from the band (min order still applies) so a flat
            # signal never leaves a residual position behind and canary-sized positions can close
            sells.append(
                OrderIntent(
                    mint=mint,
                    side="sell",
                    usd=-delta,
                    strategy=label,
                    reason=reason,
                    exit=None,
                    is_exit=full,
                    amount_base=tradeable_base(mint, acct, cfg) if full else None,
                )
            )
        elif delta >= band:
            usd = min(delta, headroom)
            if usd < band:
                continue  # cash floor leaves no room for this entry
            usd = usd.quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
            buys.append(
                OrderIntent(
                    mint=mint, side="buy", usd=usd, strategy=label, reason=reason, exit=n.exit, is_exit=False
                )
            )
    sells.sort(key=lambda o: o.usd)
    buys.sort(key=lambda o: o.usd, reverse=True)
    return exits + sells + buys[: max(0, cfg.risk.max_entries_per_tick)]


def lamports_to_sol(lamports: int) -> Decimal:
    return Decimal(lamports) / Decimal(LAMPORTS_PER_SOL)
