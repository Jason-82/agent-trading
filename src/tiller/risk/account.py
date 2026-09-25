"""AccountSnapshot: everything the allocator and the risk engine need about the account.

``equity = USDC + SOL * mark + sum(token amount * mark)``. Peaks are computed on the
DEPOSIT-ADJUSTED equity ``equity(t) - net_deposits(t) + net_deposits(now)`` (a deposit or
withdrawal moves every point of the series by the same amount, so drawdowns and the
daily-loss brake never react to transfers). The local ledger is the source of truth; an
optional familiars ``history`` is only cross-checked and reported in ``warnings``.

Balances come from the chain in live mode and from the ledger's paper book (transfers +
paper fills) in paper/offline mode, so paper runs never read real balances that the paper
venue cannot move.

Units: ``*_usd`` are USD ``Decimal``; ``sol_lamports`` and ``holdings`` are base units
(lamports for SOL, 1e-6 for USDC); ``pnl_7d_pct`` is a fraction; timestamps are UTC.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field

from tiller.clock import Clock, utc_day_start
from tiller.data.prices import PriceSource
from tiller.execution.rpc import Rpc
from tiller.ledger import Ledger
from tiller.models import (
    LAMPORTS_PER_SOL,
    SOL_DECIMALS,
    SOL_MINT,
    USDC_DECIMALS,
    USDC_MINT,
    DayStats,
    DomainModel,
    EquityPoint,
    Position,
)

BalanceSource = Literal["chain", "paper"]
FAMILIARS_CROSSCHECK_TOLERANCE = Decimal("0.05")


class AccountSnapshot(DomainModel):
    """Point-in-time view of the account (see module docstring for units)."""

    ts: datetime
    equity_usd: Decimal
    usdc_usd: Decimal
    sol_lamports: int
    sol_usd: Decimal
    positions: list[Position]
    marks: dict[str, Decimal]
    net_deposits_usd: Decimal
    day: DayStats
    peak_7d: Decimal
    peak_30d: Decimal
    pnl_7d_pct: Decimal
    holdings: dict[str, int] = Field(default_factory=dict)
    """Base units per non-USDC mint actually held (SOL under ``SOL_MINT`` in lamports)."""
    decimals: dict[str, int] = Field(default_factory=dict)
    unpriced: list[str] = Field(default_factory=list)
    source: BalanceSource = "chain"
    warnings: list[str] = Field(default_factory=list)

    @property
    def adjusted_equity(self) -> Decimal:
        """Equity minus net deposits (cumulative P&L)."""
        return self.equity_usd - self.net_deposits_usd

    def drawdown_from(self, peak: Decimal) -> Decimal:
        """Fraction below ``peak`` (0 when at or above it, or when the peak is not positive)."""
        if peak <= 0:
            return Decimal(0)
        return max(Decimal(0), Decimal(1) - self.equity_usd / peak)


def holding_units(base: int, decimals: int) -> Decimal:
    return Decimal(base) / Decimal(10**decimals)


def mint_decimals_of(mint: str, acct: AccountSnapshot) -> int:
    """Decimals for ``mint`` from the snapshot (SOL/USDC are known without a lookup)."""
    if mint == SOL_MINT:
        return SOL_DECIMALS
    if mint == USDC_MINT:
        return USDC_DECIMALS
    if mint in acct.decimals:
        return acct.decimals[mint]
    raise KeyError(f"decimals unknown for {mint}")


# --------------------------------------------------------------------------- paper book


def _transfers(ledger: Ledger) -> list[tuple[str, int, str]]:
    """(mint, amount_base, direction) rows. Adapter over the ledger's private connection: the
    WP-A ledger exposes ``net_deposits_usd`` only, and the paper book needs base amounts."""
    rows = ledger._db.execute("SELECT mint, amount_base, direction FROM transfers ORDER BY id").fetchall()
    return [(str(r["mint"]), int(r["amount_base"]), str(r["direction"])) for r in rows]


def paper_balances(ledger: Ledger) -> tuple[int, dict[str, int]]:
    """USDC base units and per-mint holdings implied by transfers plus PAPER fills."""
    balances: dict[str, int] = {}
    for mint, base, direction in _transfers(ledger):
        balances[mint] = balances.get(mint, 0) + (base if direction == "in" else -base)
    for f in ledger.fills():
        if not f.paper:
            continue
        balances[f.in_mint] = balances.get(f.in_mint, 0) - f.in_base
        balances[f.out_mint] = balances.get(f.out_mint, 0) + f.out_base
    usdc = max(0, balances.pop(USDC_MINT, 0))
    holdings = {m: b for m, b in balances.items() if b > 0}
    return usdc, holdings


async def chain_balances(rpc: Rpc, wallet: str) -> tuple[int, dict[str, int]]:
    """USDC base units and per-mint holdings from ``getBalance`` + ``getTokenAccountsByOwner``.

    Wrapped-SOL token accounts are folded into the SOL lamport balance.
    """
    lamports = await rpc.get_balance(wallet)
    accounts = await rpc.get_token_accounts_by_owner(wallet)
    holdings: dict[str, int] = {}
    usdc = 0
    for a in accounts:
        if a.owner != wallet or a.amount_base <= 0:
            continue
        if a.mint == USDC_MINT:
            usdc += a.amount_base
        elif a.mint == SOL_MINT:
            lamports += a.amount_base
        else:
            holdings[a.mint] = holdings.get(a.mint, 0) + a.amount_base
    holdings[SOL_MINT] = lamports
    return usdc, holdings


# --------------------------------------------------------------------------- peaks


def deposit_adjusted_peaks(
    points: list[EquityPoint], now_equity: Decimal, now_deposits: Decimal, now: datetime
) -> tuple[Decimal, Decimal, Decimal]:
    """(peak_7d, peak_30d, pnl_7d_pct) over deposit-adjusted equity, the current point included."""
    peak_7 = now_equity
    peak_30 = now_equity
    first_7: Decimal | None = None
    for p in sorted(points, key=lambda x: x.ts):
        adjusted = p.equity_usd - p.net_deposits_usd + now_deposits
        age = now - p.ts
        if age <= timedelta(days=30):
            peak_30 = max(peak_30, adjusted)
        if age <= timedelta(days=7):
            peak_7 = max(peak_7, adjusted)
            if first_7 is None:
                first_7 = adjusted
    pnl_7d = Decimal(0)
    if first_7 is not None and first_7 > 0:
        pnl_7d = now_equity / first_7 - Decimal(1)
    return peak_7, peak_30, pnl_7d


# --------------------------------------------------------------------------- builder


async def build_snapshot(
    rpc: Rpc | None,
    prices: PriceSource,
    ledger: Ledger,
    wallet: str,
    clock: Clock,
    familiars_history: list[EquityPoint] | None = None,
    *,
    source: BalanceSource = "chain",
    decimals: dict[str, int] | None = None,
    record: bool = True,
) -> AccountSnapshot:
    """Build the snapshot; with ``record`` the equity snapshot and day-start equity are stored.

    ``source='chain'`` needs ``rpc``; ``'paper'`` reads the paper book from the ledger.
    ``decimals`` supplies token decimals for mints other than SOL/USDC (holdings whose
    decimals or mark are unknown are valued at 0 and listed in ``unpriced``).
    """
    now = clock.now()
    if source == "chain":
        if rpc is None:
            raise ValueError("chain balances need an rpc")
        usdc_base, holdings = await chain_balances(rpc, wallet)
    else:
        usdc_base, holdings = paper_balances(ledger)
    dec: dict[str, int] = {SOL_MINT: SOL_DECIMALS, USDC_MINT: USDC_DECIMALS, **(decimals or {})}
    mints = [m for m in holdings if m != USDC_MINT]
    if SOL_MINT not in mints:
        mints.append(SOL_MINT)
    marks: dict[str, Decimal] = {USDC_MINT: Decimal(1)}
    try:
        fetched = await prices.usd_prices(mints)
    except Exception as e:  # price outage: fail closed via unpriced marks
        fetched = {}
        warnings = [f"price source error: {type(e).__name__}"]
    else:
        warnings = []
    for m, px in fetched.items():
        if px is not None and px > 0:
            marks[m] = Decimal(px)
    usdc_usd = holding_units(usdc_base, USDC_DECIMALS)
    sol_lamports = holdings.get(SOL_MINT, 0)
    sol_mark = marks.get(SOL_MINT)
    sol_usd = holding_units(sol_lamports, SOL_DECIMALS) * sol_mark if sol_mark is not None else Decimal(0)
    unpriced: list[str] = [] if sol_mark is not None else [SOL_MINT]
    equity = usdc_usd + sol_usd
    for m, base in holdings.items():
        if m == SOL_MINT:
            continue
        mark = marks.get(m)
        if mark is None or m not in dec:
            unpriced.append(m)
            continue
        equity += holding_units(base, dec[m]) * mark
    net_deposits = ledger.net_deposits_usd()
    day_start = utc_day_start(now)
    if record:
        ledger.set_day_start_equity(day_start, equity)
        ledger.add_equity_snapshot(now, equity, net_deposits, "tick")
    day = ledger.day_stats(day_start)
    if day.start_equity <= 0:
        day = day.model_copy(update={"start_equity": equity})
    points = ledger.equity_curve(30, now=now)
    peak_7, peak_30, pnl_7d = deposit_adjusted_peaks(points, equity, net_deposits, now)
    if familiars_history:
        last = max(familiars_history, key=lambda p: p.ts)
        ours = equity - net_deposits
        if equity > 0 and abs(last.pnl_usd - ours) / equity > FAMILIARS_CROSSCHECK_TOLERANCE:
            warnings.append(
                f"familiars pnl {last.pnl_usd} differs from ledger pnl {ours:.2f} by more than "
                f"{FAMILIARS_CROSSCHECK_TOLERANCE:.0%} of equity"
            )
    return AccountSnapshot(
        ts=now,
        equity_usd=equity,
        usdc_usd=usdc_usd,
        sol_lamports=sol_lamports,
        sol_usd=sol_usd,
        positions=ledger.positions(),
        marks=marks,
        net_deposits_usd=net_deposits,
        day=day,
        peak_7d=peak_7,
        peak_30d=peak_30,
        pnl_7d_pct=pnl_7d,
        holdings=holdings,
        decimals={m: d for m, d in dec.items() if m not in (SOL_MINT, USDC_MINT)},
        unpriced=unpriced,
        source=source,
        warnings=warnings,
    )


def record_snapshot(ledger: Ledger, acct: AccountSnapshot) -> None:
    """Store the equity snapshot and (first call of the day only) the day-start equity."""
    ledger.set_day_start_equity(utc_day_start(acct.ts), acct.equity_usd)
    ledger.add_equity_snapshot(acct.ts, acct.equity_usd, acct.net_deposits_usd, "tick")


def snapshot_summary(acct: AccountSnapshot) -> dict[str, Any]:
    """Loggable summary (no secrets)."""
    return {
        "ts": acct.ts.isoformat(),
        "equity_usd": str(acct.equity_usd.quantize(Decimal("0.01"))),
        "usdc_usd": str(acct.usdc_usd.quantize(Decimal("0.01"))),
        "sol": str(Decimal(acct.sol_lamports) / Decimal(LAMPORTS_PER_SOL)),
        "net_deposits_usd": str(acct.net_deposits_usd),
        "peak_7d": str(acct.peak_7d.quantize(Decimal("0.01"))),
        "peak_30d": str(acct.peak_30d.quantize(Decimal("0.01"))),
        "positions": len(acct.positions),
        "unpriced": acct.unpriced,
        "source": acct.source,
    }
