"""AccountSnapshot: equity arithmetic, deposits never raise pnl, peaks on equity minus deposits, paper book."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest

from fakes import FakePriceSource, FakeRpc
from tiller.clock import SimClock
from tiller.execution.rpc import parse_token_account_value
from tiller.ledger import Ledger
from tiller.models import SOL_MINT, USDC_MINT, Fill
from tiller.risk.account import build_snapshot, deposit_adjusted_peaks, paper_balances
from wpd_helpers import LAMPORTS, TOKEN_X, WALLET

PRICES = {SOL_MINT: Decimal(150), TOKEN_X: Decimal("0.05")}


def owned(load_fixture: Any) -> list[Any]:
    rows = load_fixture("rpc/token_accounts_spl")["result"]["value"]
    return [parse_token_account_value(r["pubkey"], r["account"]) for r in rows]


async def test_equity_arithmetic_from_chain(
    load_fixture: Any, tmp_ledger: Ledger, sim_clock: SimClock
) -> None:
    rpc = FakeRpc(balances={WALLET: 2 * LAMPORTS}, token_accounts={WALLET: owned(load_fixture)})
    acct = await build_snapshot(
        rpc, FakePriceSource(PRICES), tmp_ledger, WALLET, sim_clock, decimals={TOKEN_X: 6}
    )
    assert acct.usdc_usd == Decimal(500)
    assert acct.sol_lamports == 2 * LAMPORTS and acct.sol_usd == Decimal(300)
    assert acct.equity_usd == Decimal(500) + Decimal(300) + Decimal("12.345678") * Decimal("0.05")
    assert acct.holdings[TOKEN_X] == 12_345_678 and acct.unpriced == []
    assert acct.source == "chain" and acct.day.start_equity == acct.equity_usd
    # the snapshot was recorded
    assert len(tmp_ledger.equity_curve(1)) == 1


async def test_unknown_mark_is_unpriced_not_fatal(
    load_fixture: Any, tmp_ledger: Ledger, sim_clock: SimClock
) -> None:
    rpc = FakeRpc(balances={WALLET: LAMPORTS}, token_accounts={WALLET: owned(load_fixture)})
    acct = await build_snapshot(rpc, FakePriceSource({SOL_MINT: Decimal(100)}), tmp_ledger, WALLET, sim_clock)
    assert acct.unpriced == [TOKEN_X] and acct.equity_usd == Decimal(600)


async def test_deposit_does_not_raise_pnl_or_create_drawdown(tmp_ledger: Ledger, sim_clock: SimClock) -> None:
    rpc = FakeRpc(balances={WALLET: LAMPORTS})
    prices = FakePriceSource({SOL_MINT: Decimal(100)})
    a0 = await build_snapshot(rpc, prices, tmp_ledger, WALLET, sim_clock)
    assert a0.equity_usd == Decimal(100) and a0.adjusted_equity == Decimal(100)
    sim_clock.advance(timedelta(hours=1))
    tmp_ledger.record_transfer(sim_clock.now(), SOL_MINT, 5 * LAMPORTS, Decimal(500), "in")
    rpc.balances[WALLET] = 6 * LAMPORTS
    a1 = await build_snapshot(rpc, prices, tmp_ledger, WALLET, sim_clock)
    assert a1.equity_usd == Decimal(600)
    assert a1.adjusted_equity == Decimal(100)  # pnl unchanged by the deposit
    assert a1.pnl_7d_pct == Decimal(0)
    assert a1.drawdown_from(a1.peak_7d) == 0 and a1.peak_7d == Decimal(600)


async def test_withdrawal_is_not_a_drawdown_and_loss_is(tmp_ledger: Ledger, sim_clock: SimClock) -> None:
    rpc = FakeRpc(balances={WALLET: 20 * LAMPORTS})
    prices = FakePriceSource({SOL_MINT: Decimal(100)})
    await build_snapshot(rpc, prices, tmp_ledger, WALLET, sim_clock)  # 2000
    sim_clock.advance(timedelta(days=1))
    tmp_ledger.record_transfer(sim_clock.now(), SOL_MINT, 10 * LAMPORTS, Decimal(1000), "out")
    rpc.balances[WALLET] = 10 * LAMPORTS
    a = await build_snapshot(rpc, prices, tmp_ledger, WALLET, sim_clock)
    assert a.equity_usd == Decimal(1000) and a.drawdown_from(a.peak_30d) == 0
    sim_clock.advance(timedelta(days=1))
    prices.table[SOL_MINT] = Decimal(90)
    b = await build_snapshot(rpc, prices, tmp_ledger, WALLET, sim_clock)
    assert b.equity_usd == Decimal(900)
    assert b.drawdown_from(b.peak_30d) == pytest.approx(Decimal("0.10"))
    assert b.drawdown_from(b.peak_7d) == pytest.approx(Decimal("0.10"))


async def test_peaks_are_rolling_not_all_time(tmp_ledger: Ledger, sim_clock: SimClock) -> None:
    rpc = FakeRpc(balances={WALLET: 20 * LAMPORTS})
    prices = FakePriceSource({SOL_MINT: Decimal(100)})
    await build_snapshot(rpc, prices, tmp_ledger, WALLET, sim_clock)  # 2000, 40 days ago
    sim_clock.advance(timedelta(days=40))
    prices.table[SOL_MINT] = Decimal(60)
    await build_snapshot(rpc, prices, tmp_ledger, WALLET, sim_clock)  # 1200, 8 days before now
    sim_clock.advance(timedelta(days=8))
    prices.table[SOL_MINT] = Decimal(55)
    a = await build_snapshot(rpc, prices, tmp_ledger, WALLET, sim_clock)
    assert a.peak_30d == Decimal(1200)  # the 2000 point is outside the 30-day window
    assert a.peak_7d == Decimal(1100)  # only the current point is inside 7 days
    assert a.drawdown_from(a.peak_30d) == pytest.approx(Decimal(1) - Decimal(1100) / Decimal(1200))


def test_deposit_adjusted_peaks_pure() -> None:
    from datetime import UTC, datetime

    from tiller.models import EquityPoint

    now = datetime(2026, 9, 24, tzinfo=UTC)
    pts = [
        EquityPoint(
            timestamp=now - timedelta(days=6),
            equityUsd=Decimal(1000),
            netDepositsUsd=Decimal(0),
            pnlUsd=Decimal(1000),
        ),
        EquityPoint(
            timestamp=now - timedelta(days=3),
            equityUsd=Decimal(2100),
            netDepositsUsd=Decimal(1000),
            pnlUsd=Decimal(1100),
        ),
    ]
    p7, p30, pnl7 = deposit_adjusted_peaks(pts, Decimal(2050), Decimal(1000), now)
    assert p7 == p30 == Decimal(2100)
    assert pnl7 == Decimal(2050) / Decimal(2000) - 1


async def test_paper_book_from_transfers_and_paper_fills(tmp_ledger: Ledger, sim_clock: SimClock) -> None:
    now = sim_clock.now()
    tmp_ledger.record_transfer(now, USDC_MINT, 10_000_000_000, Decimal(10_000), "in")
    tmp_ledger.record_transfer(now, SOL_MINT, 100_000_000, Decimal(15), "in")
    tmp_ledger.record_fill(
        Fill(
            signature=None,
            in_mint=USDC_MINT,
            out_mint=SOL_MINT,
            in_base=1_500_000_000,
            out_base=10 * LAMPORTS,
            usd_in=Decimal(1500),
            usd_out=Decimal(1495),
            fee_usd=Decimal(5),
            ts=now,
            paper=True,
            strategy="core",
            quote_out_base=10 * LAMPORTS,
        )
    )
    usdc, holdings = paper_balances(tmp_ledger)
    assert usdc == 8_500_000_000 and holdings[SOL_MINT] == 10 * LAMPORTS + 100_000_000
    acct = await build_snapshot(
        None, FakePriceSource({SOL_MINT: Decimal(150)}), tmp_ledger, WALLET, sim_clock, source="paper"
    )
    assert acct.source == "paper" and acct.usdc_usd == Decimal(8500)
    assert acct.equity_usd == Decimal(8500) + Decimal("10.1") * 150
    assert acct.net_deposits_usd == Decimal(10_015)
    assert [p.mint for p in acct.positions] == [SOL_MINT]
