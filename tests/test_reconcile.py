"""Reconciliation: pending signature finalized after a crash, mismatch > 1% flagged, accept rewrites,
equal amounts with different marks are not a mismatch, familiars cost-basis backfill."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

from fakes import FakePriceSource, FakeRpc
from tiller.clock import SimClock
from tiller.execution.rpc import parse_token_account_value
from tiller.ledger import Ledger
from tiller.models import SOL_MINT, USDC_MINT, AgentDetail, Fill, Order, Position, SwapRequest
from tiller.risk.reconcile import RECONCILED_STRATEGY, reconcile
from wpd_helpers import LAMPORTS, TOKEN_X, WALLET

PRICES = {SOL_MINT: Decimal(150), TOKEN_X: Decimal("0.05")}
RESERVE = 50_000_000


def owned(load_fixture: Any) -> list[Any]:
    rows = load_fixture("rpc/token_accounts_spl")["result"]["value"]
    return [parse_token_account_value(r["pubkey"], r["account"]) for r in rows]


def buy_req() -> SwapRequest:
    return SwapRequest(
        input_mint=USDC_MINT, output_mint=SOL_MINT, amount_base=100_000_000, strategy="core", reason="t"
    )


def chain_rpc(load_fixture: Any, **kw: Any) -> FakeRpc:
    return FakeRpc(balances={WALLET: 2 * LAMPORTS}, token_accounts={WALLET: owned(load_fixture)}, **kw)


async def test_pending_signature_finalized_after_crash(
    load_fixture: Any, tmp_ledger: Ledger, sim_clock: SimClock
) -> None:
    ids = load_fixture("identities")
    sig = ids["buy_signature"]
    order = Order.model_validate(load_fixture("jupiter/order_ok"))
    oid = tmp_ledger.record_order(buy_req(), order, sig)  # crash happened after this row was written
    assert tmp_ledger.pending_signatures() == [(oid, sig)]
    rpc = chain_rpc(
        load_fixture,
        statuses={sig: load_fixture("rpc/statuses_finalized")["result"]["value"][0]},
        transactions={sig: load_fixture("rpc/tx_swap_buy")["result"]},
    )
    # chain already reflects the fill: 2 SOL includes the bought 0.6658 SOL; ledger SOL will be 0.6658 after finalize
    rpc.balances[WALLET] = int(Decimal("0.6658") * LAMPORTS) + RESERVE
    rep = await reconcile(
        rpc, tmp_ledger, FakePriceSource(PRICES), WALLET, None, sim_clock, token_decimals={TOKEN_X: 6}
    )
    assert rep.finalized == 1 and rep.failed == 0 and rep.still_pending == 0
    assert tmp_ledger.pending_signatures() == []
    fills = tmp_ledger.fills()
    assert len(fills) == 1 and fills[0].signature == sig and fills[0].out_base == 665_800_000
    sol = [p for p in tmp_ledger.positions() if p.mint == SOL_MINT]
    assert sol and sol[0].amount_base == 665_800_000
    # TOKEN_X (12.345678) is on chain but not in the ledger -> mismatch of 0.617 USD, below 1% of equity
    assert [m.mint for m in rep.mismatches] == [TOKEN_X]
    assert rep.ok


async def test_failed_and_dropped_pending_orders(
    load_fixture: Any, tmp_ledger: Ledger, sim_clock: SimClock
) -> None:
    order = Order.model_validate(load_fixture("jupiter/order_ok"))
    a = tmp_ledger.record_order(buy_req(), order, "sigA")
    b = tmp_ledger.record_order(buy_req(), order, "sigB")
    c = tmp_ledger.record_order(buy_req(), order, "sigC")
    rpc = chain_rpc(
        load_fixture,
        statuses={"sigA": {"err": {"InstructionError": [3, "Custom"]}}, "sigB": None, "sigC": None},
    )
    rep = await reconcile(
        rpc,
        tmp_ledger,
        FakePriceSource(PRICES),
        WALLET,
        None,
        sim_clock,
        token_decimals={TOKEN_X: 6},
        pending_grace=timedelta(minutes=5),
    )
    assert rep.failed == 1 and rep.still_pending == 2
    assert {s for _, s in tmp_ledger.pending_signatures()} == {"sigB", "sigC"}
    assert tmp_ledger.orders()[a - 1]["state"] == "failed"
    sim_clock.advance(timedelta(minutes=6))
    rep2 = await reconcile(
        rpc, tmp_ledger, FakePriceSource(PRICES), WALLET, None, sim_clock, token_decimals={TOKEN_X: 6}
    )
    assert rep2.failed == 2 and rep2.still_pending == 0
    assert all(tmp_ledger.orders()[i - 1]["state"] == "failed" for i in (b, c))


async def test_mismatch_above_one_percent_flagged(
    load_fixture: Any, tmp_ledger: Ledger, sim_clock: SimClock
) -> None:
    rpc = chain_rpc(load_fixture)  # 1.95 SOL tradeable (292.5 USD) + 12.345678 TKX; ledger has nothing
    rep = await reconcile(
        rpc, tmp_ledger, FakePriceSource(PRICES), WALLET, None, sim_clock, token_decimals={TOKEN_X: 6}
    )
    assert not rep.ok
    assert {m.mint for m in rep.mismatches} == {SOL_MINT, TOKEN_X}
    sol = next(m for m in rep.mismatches if m.mint == SOL_MINT)
    assert (
        sol.chain_base == 2 * LAMPORTS - RESERVE and sol.ledger_base == 0 and sol.usd_diff == Decimal("292.5")
    )
    assert rep.usd_mismatch_pct > Decimal("0.01")
    assert tmp_ledger.positions() == []  # nothing rewritten without --accept
    assert rep.backfilled == 2 and {p.strategy for p in rep.proposed} == {RECONCILED_STRATEGY}


async def test_small_gas_drift_is_not_a_mismatch(
    load_fixture: Any, tmp_ledger: Ledger, sim_clock: SimClock
) -> None:
    rpc = chain_rpc(load_fixture)
    rpc.token_accounts[WALLET] = [a for a in owned(load_fixture) if a.mint != TOKEN_X]
    tmp_ledger.replace_positions(
        [
            Position(
                mint=SOL_MINT,
                amount_base=2 * LAMPORTS - RESERVE + 5_000,
                cost_usd=Decimal(280),
                opened_at=sim_clock.now(),
                strategy="core",
            )
        ]
    )
    rep = await reconcile(rpc, tmp_ledger, FakePriceSource(PRICES), WALLET, None, sim_clock)
    assert rep.ok and rep.mismatches == []


async def test_accept_rewrites_ledger_to_chain(
    load_fixture: Any, tmp_ledger: Ledger, sim_clock: SimClock
) -> None:
    rpc = chain_rpc(load_fixture)
    tmp_ledger.replace_positions(
        [
            Position(
                mint=SOL_MINT,
                amount_base=LAMPORTS,
                cost_usd=Decimal(100),
                opened_at=sim_clock.now(),
                strategy="core",
            )
        ]
    )
    rep = await reconcile(
        rpc,
        tmp_ledger,
        FakePriceSource(PRICES),
        WALLET,
        None,
        sim_clock,
        accept=True,
        token_decimals={TOKEN_X: 6},
    )
    assert not rep.ok  # the report still describes the pre-accept mismatch
    pos = {p.mint: p for p in tmp_ledger.positions()}
    assert pos[SOL_MINT].amount_base == 2 * LAMPORTS - RESERVE and pos[SOL_MINT].strategy == "core"
    assert pos[SOL_MINT].cost_usd == Decimal(100) * Decimal(2 * LAMPORTS - RESERVE) / Decimal(LAMPORTS)
    assert pos[TOKEN_X].amount_base == 12_345_678 and pos[TOKEN_X].strategy == RECONCILED_STRATEGY
    assert pos[TOKEN_X].cost_usd == Decimal("12.345678") * Decimal("0.05")
    rep2 = await reconcile(
        rpc, tmp_ledger, FakePriceSource(PRICES), WALLET, None, sim_clock, token_decimals={TOKEN_X: 6}
    )
    assert rep2.ok and rep2.mismatches == []


async def test_equal_amounts_with_different_marks_is_not_a_mismatch(
    load_fixture: Any, tmp_ledger: Ledger, sim_clock: SimClock
) -> None:
    rpc = chain_rpc(load_fixture)
    tmp_ledger.replace_positions(
        [
            Position(
                mint=SOL_MINT,
                amount_base=2 * LAMPORTS - RESERVE,
                cost_usd=Decimal(9999),
                opened_at=sim_clock.now(),
                strategy="core",
            ),
            Position(
                mint=TOKEN_X,
                amount_base=12_345_678,
                cost_usd=Decimal(1),
                opened_at=sim_clock.now(),
                strategy="copy",
            ),
        ]
    )
    rep = await reconcile(
        rpc,
        tmp_ledger,
        FakePriceSource({SOL_MINT: Decimal(9), TOKEN_X: Decimal(400)}),
        WALLET,
        None,
        sim_clock,
        token_decimals={TOKEN_X: 6},
    )
    assert rep.ok and rep.mismatches == [] and rep.usd_mismatch_pct == 0


async def test_familiars_backfill_of_cost_basis(
    load_fixture: Any, tmp_ledger: Ledger, sim_clock: SimClock
) -> None:
    detail = AgentDetail.model_validate(load_fixture("familiars/agent_detail"))
    rpc = FakeRpc(balances={WALLET: 3 * LAMPORTS + RESERVE})
    rep = await reconcile(rpc, tmp_ledger, FakePriceSource(PRICES), WALLET, detail, sim_clock)
    assert rep.backfilled == 1
    p = rep.proposed[0]
    assert p.mint == SOL_MINT and p.amount_base == 3 * LAMPORTS and p.strategy == RECONCILED_STRATEGY
    buys = [t for t in detail.trades if t.token == SOL_MINT and t.kind == "buy"]
    avg = sum(t.usd_value for t in buys) / sum(t.amount for t in buys)
    assert p.cost_usd == avg * 3
    assert p.opened_at == min(t.time for t in buys)
    # without familiars the basis falls back to the mark
    rep2 = await reconcile(rpc, tmp_ledger, FakePriceSource(PRICES), WALLET, None, sim_clock)
    assert rep2.proposed[0].cost_usd == Decimal(450)


async def test_unmarked_mismatch_fails_closed(
    load_fixture: Any, tmp_ledger: Ledger, sim_clock: SimClock
) -> None:
    rpc = chain_rpc(load_fixture)
    rpc.token_accounts[WALLET] = [a for a in owned(load_fixture) if a.mint == TOKEN_X]
    rpc.balances[WALLET] = RESERVE
    rep = await reconcile(rpc, tmp_ledger, FakePriceSource({SOL_MINT: Decimal(150)}), WALLET, None, sim_clock)
    assert not rep.ok and rep.mismatches[0].usd_diff is None and rep.notes


async def test_paper_fill_positions_do_not_confuse_live_reconcile(
    load_fixture: Any, tmp_ledger: Ledger, sim_clock: SimClock
) -> None:
    now = sim_clock.now()
    tmp_ledger.record_fill(
        Fill(
            signature=None,
            in_mint=USDC_MINT,
            out_mint=SOL_MINT,
            in_base=100_000_000,
            out_base=2 * LAMPORTS - RESERVE,
            usd_in=Decimal(100),
            usd_out=Decimal(100),
            fee_usd=Decimal(0),
            ts=now,
            paper=True,
            strategy="core",
            quote_out_base=1,
        )
    )
    rpc = chain_rpc(load_fixture)
    rpc.token_accounts[WALLET] = [a for a in owned(load_fixture) if a.mint != TOKEN_X]
    rep = await reconcile(rpc, tmp_ledger, FakePriceSource(PRICES), WALLET, None, sim_clock)
    assert rep.ok
