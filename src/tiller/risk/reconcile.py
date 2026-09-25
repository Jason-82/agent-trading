"""Boot / periodic reconciliation: chain vs ledger (amounts, not USD marks).

1. Every ledger order still ``submitted`` with a signature is looked up on chain: a
   successful transaction is finalized with the realised fill, a failed one is marked
   failed, one that is still unknown after ``pending_grace`` is marked failed (dropped).
2. Chain positions are rebuilt from ``getTokenAccountsByOwner`` + ``getBalance`` (SOL net of
   the gas reserve, wrapped SOL folded in).
3. Per mint, ``|chain - ledger| > mismatch_amount_pct`` of the larger amount is a mismatch;
   the USD size of every mismatch is summed at the price source's marks and expressed as a
   fraction of chain equity (``usd_mismatch_pct``). Equal amounts with different USD marks
   are never a mismatch.
4. Cost basis for chain holdings the ledger does not know is backfilled from the familiars
   trade feed (average buy price) when available, else from the current mark.
5. ``accept=True`` rewrites the ledger positions to the chain amounts.

Units: base units for amounts, USD ``Decimal`` for values, fractions for percentages.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from pydantic import Field

from tiller.clock import Clock, ensure_utc
from tiller.data.prices import PriceSource
from tiller.execution.rpc import Rpc
from tiller.execution.venue import realised_fill_from_tx
from tiller.ledger import Ledger
from tiller.models import (
    SOL_DECIMALS,
    SOL_MINT,
    USDC_DECIMALS,
    USDC_MINT,
    AgentDetail,
    DomainModel,
    Position,
    SwapRequest,
)
from tiller.risk.account import chain_balances, holding_units

RECONCILED_STRATEGY = "reconciled"
DEFAULT_PENDING_GRACE = timedelta(minutes=5)


class Mismatch(DomainModel):
    mint: str
    chain_base: int
    ledger_base: int
    usd_diff: Decimal | None = None
    """USD size of the difference; None when the mint has no mark or unknown decimals."""


class ReconcileReport(DomainModel):
    ok: bool
    mismatches: list[Mismatch] = Field(default_factory=list)
    usd_mismatch_pct: Decimal = Decimal(0)
    backfilled: int = 0
    finalized: int = 0
    failed: int = 0
    still_pending: int = 0
    proposed: list[Position] = Field(default_factory=list)
    equity_usd: Decimal = Decimal(0)
    notes: list[str] = Field(default_factory=list)


async def finalize_pending(
    rpc: Rpc,
    ledger: Ledger,
    prices: PriceSource,
    wallet: str,
    now: datetime,
    *,
    pending_grace: timedelta = DEFAULT_PENDING_GRACE,
) -> tuple[int, int, int]:
    """Step 1. Returns (finalized, failed, still_pending)."""
    rows = [r for r in ledger.orders(state="submitted") if r.get("signature")]
    if not rows:
        return 0, 0, 0
    sigs = [str(r["signature"]) for r in rows]
    statuses = await rpc.get_signature_statuses(sigs)
    finalized = failed = pending = 0
    for row, st in zip(rows, statuses, strict=True):
        order_id = int(row["id"])
        sig = str(row["signature"])
        req = SwapRequest.model_validate_json(row["req"])
        if st is None:
            submitted_at = ensure_utc(datetime.fromisoformat(str(row["ts"])))
            if now - submitted_at > pending_grace:
                ledger.finalize_order(order_id, None, "not found on chain after grace period (dropped)")
                ledger.add_event("warn", "reconcile.order_dropped", {"signature": sig})
                failed += 1
            else:
                pending += 1
            continue
        if st.get("err") is not None:
            ledger.finalize_order(order_id, None, f"on-chain error {st['err']!r}")
            failed += 1
            continue
        if st.get("confirmationStatus") not in ("confirmed", "finalized"):
            pending += 1
            continue
        tx = await rpc.get_transaction(sig)
        if tx is None:
            pending += 1
            continue
        leg = req.output_mint if req.input_mint == USDC_MINT else req.input_mint
        marks: dict[str, Decimal] = {USDC_MINT: Decimal(1)}
        try:
            fetched = await prices.usd_prices([leg, SOL_MINT])
        except Exception:
            fetched = {}
        marks[leg] = Decimal(fetched.get(leg, Decimal(0)))
        if SOL_MINT in fetched:
            marks[SOL_MINT] = Decimal(fetched[SOL_MINT])
        if marks[leg] <= 0:
            ledger.add_event("warn", "reconcile.unmarked_fill", {"signature": sig, "mint": leg})
        try:
            fill = realised_fill_from_tx(tx, wallet, req, marks)
        except ValueError as e:
            ledger.finalize_order(order_id, None, f"transaction did not show the swap: {e}")
            failed += 1
            continue
        ledger.finalize_order(order_id, fill.model_copy(update={"mode": req.mode}), None)
        finalized += 1
    return finalized, failed, pending


def _ledger_amounts(positions: list[Position]) -> dict[str, int]:
    out: dict[str, int] = {}
    for p in positions:
        out[p.mint] = out.get(p.mint, 0) + p.amount_base
    return out


def _backfill_cost(
    mint: str, chain_base: int, decimals: int, detail: AgentDetail | None, mark: Decimal | None, now: datetime
) -> Position:
    units = holding_units(chain_base, decimals)
    if detail is not None:
        buys = [t for t in detail.trades if t.token == mint and t.kind == "buy" and t.amount > 0]
        if buys:
            usd = sum((t.usd_value for t in buys), Decimal(0))
            amount = sum((t.amount for t in buys), Decimal(0))
            avg = usd / amount if amount > 0 else Decimal(0)
            return Position(
                mint=mint,
                amount_base=chain_base,
                cost_usd=avg * units,
                opened_at=min(t.time for t in buys),
                strategy=RECONCILED_STRATEGY,
            )
    return Position(
        mint=mint,
        amount_base=chain_base,
        cost_usd=units * mark if mark is not None else Decimal(0),
        opened_at=now,
        strategy=RECONCILED_STRATEGY,
    )


async def reconcile(
    rpc: Rpc,
    ledger: Ledger,
    prices: PriceSource,
    wallet: str,
    familiars_detail: AgentDetail | None,
    clock: Clock,
    accept: bool = False,
    *,
    reserve_lamports: int = 50_000_000,
    token_decimals: dict[str, int] | None = None,
    mismatch_amount_pct: Decimal = Decimal("0.005"),
    block_pct: Decimal = Decimal("0.01"),
    pending_grace: timedelta = DEFAULT_PENDING_GRACE,
) -> ReconcileReport:
    """Run the four steps; ``ok`` is True when the USD mismatch is at most ``block_pct`` of equity."""
    now = clock.now()
    finalized, failed, pending = await finalize_pending(
        rpc, ledger, prices, wallet, now, pending_grace=pending_grace
    )
    usdc_base, holdings = await chain_balances(rpc, wallet)
    chain: dict[str, int] = {m: b for m, b in holdings.items() if m != SOL_MINT}
    chain[SOL_MINT] = max(0, holdings.get(SOL_MINT, 0) - reserve_lamports)
    decimals: dict[str, int] = {SOL_MINT: SOL_DECIMALS, USDC_MINT: USDC_DECIMALS, **(token_decimals or {})}
    mints = sorted(set(chain) | {SOL_MINT})
    try:
        marks = {m: Decimal(v) for m, v in (await prices.usd_prices(mints)).items()}
    except Exception:
        marks = {}
    marks[USDC_MINT] = Decimal(1)
    equity = holding_units(usdc_base, USDC_DECIMALS)
    for m, base in holdings.items():
        if m in marks and m in decimals:
            equity += holding_units(base, decimals[m]) * marks[m]

    positions = ledger.positions()
    ledger_amounts = _ledger_amounts(positions)
    mismatches: list[Mismatch] = []
    usd_total = Decimal(0)
    unmarked = False
    for mint in sorted(set(chain) | set(ledger_amounts)):
        c = chain.get(mint, 0)
        led = ledger_amounts.get(mint, 0)
        diff = abs(c - led)
        if diff == 0:
            continue
        if Decimal(diff) <= mismatch_amount_pct * Decimal(max(c, led)):
            continue
        usd: Decimal | None = None
        if mint in marks and mint in decimals:
            usd = holding_units(diff, decimals[mint]) * marks[mint]
            usd_total += usd
        else:
            unmarked = True
        mismatches.append(Mismatch(mint=mint, chain_base=c, ledger_base=led, usd_diff=usd))
    pct = usd_total / equity if equity > 0 else (Decimal(1) if mismatches else Decimal(0))
    ok = pct <= block_pct and not unmarked
    notes: list[str] = []
    if unmarked:
        notes.append("a mismatched mint has no mark or unknown decimals (fail closed)")

    # proposed ledger positions = chain amounts, keeping strategy attribution where possible
    proposed: list[Position] = []
    backfilled = 0
    by_mint: dict[str, list[Position]] = {}
    for p in positions:
        by_mint.setdefault(p.mint, []).append(p)
    for mint, c in chain.items():
        if c <= 0:
            continue
        rows = by_mint.get(mint)
        if not rows:
            dec = decimals.get(mint)
            if dec is None:
                notes.append(f"{mint}: decimals unknown, not proposed")
                continue
            proposed.append(_backfill_cost(mint, c, dec, familiars_detail, marks.get(mint), now))
            backfilled += 1
            continue
        total = sum(p.amount_base for p in rows)
        if total == c:
            proposed.extend(rows)
            continue
        # scale every row of this mint pro rata to the chain amount (cost basis follows)
        for p in rows:
            share = Decimal(p.amount_base) / Decimal(total) if total > 0 else Decimal(1) / Decimal(len(rows))
            new_base = int((Decimal(c) * share).to_integral_value())
            cost = (
                p.cost_usd * Decimal(new_base) / Decimal(p.amount_base) if p.amount_base > 0 else p.cost_usd
            )
            proposed.append(p.model_copy(update={"amount_base": new_base, "cost_usd": cost}))
    if accept:
        ledger.replace_positions(proposed)
        ledger.add_event(
            "warn",
            "reconcile.accepted",
            {"positions": len(proposed), "mismatches": [m.model_dump() for m in mismatches]},
        )
    else:
        ledger.add_event(
            "info" if ok else "warn",
            "reconcile.report",
            {
                "ok": ok,
                "usd_mismatch_pct": str(pct),
                "mismatches": [m.model_dump() for m in mismatches],
                "finalized": finalized,
                "failed": failed,
                "pending": pending,
            },
        )
    return ReconcileReport(
        ok=ok,
        mismatches=mismatches,
        usd_mismatch_pct=pct,
        backfilled=backfilled,
        finalized=finalized,
        failed=failed,
        still_pending=pending,
        proposed=proposed,
        equity_usd=equity,
        notes=notes,
    )


def summarize(report: ReconcileReport) -> dict[str, Any]:
    return {
        "ok": report.ok,
        "usd_mismatch_pct": str(report.usd_mismatch_pct),
        "mismatches": [m.model_dump() for m in report.mismatches],
        "finalized": report.finalized,
        "failed": report.failed,
        "still_pending": report.still_pending,
        "backfilled": report.backfilled,
        "notes": report.notes,
    }
