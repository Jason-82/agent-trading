"""Ledger: order lifecycle, crash recovery, deposits, day stats, blocklist, copy tables, tax export, raw snapshots."""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from tiller.clock import SimClock
from tiller.copy.models import LeaderTrade
from tiller.ledger import Ledger
from tiller.models import SOL_MINT, USDC_MINT, ExitRule, Fill, Order, SwapRequest

T0 = datetime(2026, 9, 24, 0, 5, tzinfo=UTC)
DAY = T0.replace(hour=0, minute=0)
MEME = "MemeMint1111111111111111111111111111111111"


def _req(strategy: str = "sol_trend_ensemble") -> SwapRequest:
    return SwapRequest(
        input_mint=USDC_MINT, output_mint=SOL_MINT, amount_base=10_000_000, strategy=strategy, reason="test"
    )


def _fill(
    sig: str | None,
    ts: datetime,
    buy: bool = True,
    usd: str = "10",
    strategy: str = "sol_trend_ensemble",
    mode: str = "normal",
) -> Fill:
    if buy:
        return Fill(
            signature=sig,
            in_mint=USDC_MINT,
            out_mint=SOL_MINT,
            in_base=10_000_000,
            out_base=50_000_000,
            usd_in=Decimal(usd),
            usd_out=Decimal(usd) - Decimal("0.03"),
            fee_usd=Decimal("0.03"),
            ts=ts,
            paper=False,
            strategy=strategy,
            quote_out_base=50_100_000,
            mode=mode,
        )
    return Fill(
        signature=sig,
        in_mint=SOL_MINT,
        out_mint=USDC_MINT,
        in_base=50_000_000,
        out_base=10_000_000,
        usd_in=Decimal(usd),
        usd_out=Decimal(usd) - Decimal("0.03"),
        fee_usd=Decimal("0.03"),
        ts=ts,
        paper=False,
        strategy=strategy,
        quote_out_base=10_000_000,
        mode=mode,
    )


def _order() -> Order:
    return Order.model_validate(
        {
            "transaction": "AA==",
            "requestId": "r1",
            "inAmount": 10_000_000,
            "outAmount": 50_000_000,
            "slippageBps": 50,
            "priceImpactPct": "0.001",
            "feeBps": 2,
            "router": "metis",
        }
    )


def test_order_submitted_then_finalized_round_trip(tmp_ledger: Ledger) -> None:
    oid = tmp_ledger.record_order(_req(), _order(), "sig-a")
    assert tmp_ledger.pending_signatures() == [(oid, "sig-a")]
    assert tmp_ledger.orders()[0]["state"] == "submitted"
    tmp_ledger.finalize_order(oid, _fill("sig-a", T0), None)
    assert tmp_ledger.pending_signatures() == []
    assert tmp_ledger.orders()[0]["state"] == "filled"
    pos = tmp_ledger.positions()
    assert len(pos) == 1 and pos[0].mint == SOL_MINT and pos[0].amount_base == 50_000_000
    assert pos[0].cost_usd == Decimal(10)
    # finalize is idempotent
    tmp_ledger.finalize_order(oid, _fill("sig-a", T0), None)
    assert len(tmp_ledger.fills()) == 1


def test_failed_order_records_error(tmp_ledger: Ledger) -> None:
    oid = tmp_ledger.record_order(_req(), None, "sig-b")
    tmp_ledger.finalize_order(oid, None, "execute: code 3")
    row = tmp_ledger.orders()[0]
    assert row["state"] == "failed" and row["error"] == "execute: code 3"
    assert tmp_ledger.positions() == []


def test_pending_signatures_survive_simulated_crash(tmp_path: Path, sim_clock: SimClock) -> None:
    path = tmp_path / "crash.sqlite"
    first = Ledger(path, clock=sim_clock)
    oid = first.record_order(_req(), _order(), "sig-crash")
    first.close()  # process dies before /execute returned
    second = Ledger(path, clock=sim_clock)
    assert second.pending_signatures() == [(oid, "sig-crash")]
    second.close()


def test_sell_reduces_position_and_cost_pro_rata(tmp_ledger: Ledger) -> None:
    tmp_ledger.record_fill(_fill("s1", T0, buy=True, usd="10"))
    tmp_ledger.record_fill(_fill("s2", T0 + timedelta(minutes=1), buy=True, usd="10"))
    assert tmp_ledger.positions()[0].amount_base == 100_000_000
    tmp_ledger.record_fill(_fill("s3", T0 + timedelta(minutes=2), buy=False, usd="10"))
    pos = tmp_ledger.positions()
    assert pos[0].amount_base == 50_000_000 and pos[0].cost_usd == Decimal(10)
    tmp_ledger.record_fill(_fill("s4", T0 + timedelta(minutes=3), buy=False, usd="10"))
    assert tmp_ledger.positions() == []


def test_fill_dedupe_by_signature(tmp_ledger: Ledger) -> None:
    tmp_ledger.record_fill(_fill("dup", T0))
    tmp_ledger.record_fill(_fill("dup", T0))
    assert len(tmp_ledger.fills()) == 1
    assert tmp_ledger.positions()[0].amount_base == 50_000_000


def test_net_deposits_excludes_fills(tmp_ledger: Ledger) -> None:
    tmp_ledger.record_transfer(T0, USDC_MINT, 500_000_000, Decimal(500), "in", signature="dep1")
    tmp_ledger.record_transfer(T0, USDC_MINT, 500_000_000, Decimal(500), "in", signature="dep1")  # idempotent
    tmp_ledger.record_transfer(T0 + timedelta(days=1), USDC_MINT, 100_000_000, Decimal(100), "out")
    tmp_ledger.record_fill(_fill("f1", T0, usd="250"))
    assert tmp_ledger.net_deposits_usd() == Decimal(400)


def test_day_stats_counts_buys_swaps_and_start_equity(tmp_ledger: Ledger) -> None:
    tmp_ledger.add_equity_snapshot(DAY - timedelta(hours=1), Decimal(990), Decimal(900), "ledger")
    tmp_ledger.add_equity_snapshot(DAY + timedelta(minutes=1), Decimal(1000), Decimal(900), "ledger")
    tmp_ledger.add_equity_snapshot(DAY + timedelta(hours=5), Decimal(1010), Decimal(900), "ledger")
    tmp_ledger.record_fill(_fill("d1", DAY + timedelta(hours=1), buy=True, usd="10"))
    tmp_ledger.record_fill(_fill("d2", DAY + timedelta(hours=2), buy=False, usd="5"))
    tmp_ledger.record_fill(_fill("d3", DAY + timedelta(hours=3), buy=False, usd="5", mode="emergency"))
    tmp_ledger.record_fill(_fill("d4", DAY + timedelta(days=1, hours=1), buy=True, usd="10"))
    stats = tmp_ledger.day_stats(DAY)
    assert stats.start_equity == Decimal(1000)
    assert stats.buys_usd == Decimal(10) and stats.entries == 1
    assert stats.swaps == 2  # emergency exit exempt
    assert tmp_ledger.swaps_today(DAY) == 2
    assert tmp_ledger.swaps_today(DAY, exclude_emergency=False) == 3
    assert tmp_ledger.swaps_today(DAY + timedelta(days=1)) == 1
    # explicit day-start record wins over snapshots
    tmp_ledger.set_day_start_equity(DAY, Decimal(1234))
    tmp_ledger.set_day_start_equity(DAY, Decimal(1))  # first call sticks
    assert tmp_ledger.day_stats(DAY).start_equity == Decimal(1234)
    # a day with no snapshot uses the last one before it
    assert tmp_ledger.day_stats(DAY + timedelta(days=3)).start_equity == Decimal(1010)


def test_equity_curve_window_and_pnl(tmp_ledger: Ledger, sim_clock: SimClock) -> None:
    for d in range(10):
        tmp_ledger.add_equity_snapshot(T0 - timedelta(days=d), Decimal(1000 + d), Decimal(500), "ledger")
    pts = tmp_ledger.equity_curve(3, now=sim_clock.now())
    assert len(pts) == 4 and pts[-1].pnl_usd == Decimal(500)
    assert pts[0].ts <= pts[-1].ts


def test_blocklist_expiry(tmp_ledger: Ledger) -> None:
    tmp_ledger.blocklist_add(MEME, T0 + timedelta(hours=24), "3 failed simulations")
    assert tmp_ledger.blocklist_active(T0 + timedelta(hours=23)) == {MEME}
    assert tmp_ledger.blocklist_active(T0 + timedelta(hours=25)) == set()
    tmp_ledger.blocklist_add(MEME, T0 + timedelta(hours=48))
    assert tmp_ledger.blocklist_active(T0 + timedelta(hours=25)) == {MEME}


def _leader(sig: str, verified: bool = False) -> LeaderTrade:
    return LeaderTrade(
        key="fam:alpha",
        wallet="W" * 32,
        signature=sig,
        ts=T0,
        detected_at=T0 + timedelta(seconds=30),
        side="buy",
        mint=MEME,
        usd_value=Decimal(100),
        amount=Decimal(1000),
        price_usd=Decimal("0.1"),
        source="familiars",
        chain_verified=verified,
    )


def test_leader_trade_dedupe_by_signature(tmp_ledger: Ledger) -> None:
    assert tmp_ledger.upsert_leader_trades([_leader("a"), _leader("b")]) == 2
    assert tmp_ledger.upsert_leader_trades([_leader("a", verified=True), _leader("c")]) == 1
    trades = tmp_ledger.leader_trades(T0 - timedelta(days=1))
    assert [t.signature for t in trades] == ["a", "b", "c"]
    assert trades[0].chain_verified is True  # verification flag upgraded in place
    assert tmp_ledger.leader_trades(T0 - timedelta(days=1), keys=["wallet:x"]) == []
    assert len(tmp_ledger.leader_trades(T0 - timedelta(days=1), keys=["fam:alpha"])) == 3


def test_posts_queue_and_not_before(tmp_ledger: Ledger) -> None:
    tmp_ledger.post_upsert(
        "sig-p", "trade", "bought SOL", "queued", 0, mint=SOL_MINT, not_before=T0 + timedelta(seconds=90)
    )
    assert tmp_ledger.posts_pending(T0) == []
    pend = tmp_ledger.posts_pending(T0 + timedelta(seconds=90))
    assert len(pend) == 1 and pend[0].kind == "trade" and pend[0].attempts == 0
    tmp_ledger.post_upsert("sig-p", "trade", "bought SOL", "posted", 1, mint=SOL_MINT)
    assert tmp_ledger.posts_pending(T0 + timedelta(hours=1)) == []
    assert tmp_ledger.post("sig-p") is not None and tmp_ledger.post("sig-p").state == "posted"  # type: ignore[union-attr]


def test_tax_csv_columns_and_row_count(tmp_ledger: Ledger, tmp_path: Path) -> None:
    tmp_ledger.record_fill(_fill("t1", T0, buy=True, usd="10"))
    tmp_ledger.record_fill(_fill("t2", T0 + timedelta(hours=1), buy=False, usd="11"))
    tmp_ledger.record_fill(
        _fill(None, T0 + timedelta(hours=2), buy=True, usd="9")
    )  # paper-like, no signature
    out = tmp_path / "tax.csv"
    assert tmp_ledger.export_tax_csv(out) == 3
    with out.open() as f:
        rows = list(csv.reader(f))
    assert rows[0] == list(Ledger.TAX_COLUMNS)
    assert len(rows) == 4
    assert rows[1][4] == USDC_MINT and rows[1][6] == "10" and rows[1][7] == SOL_MINT and rows[1][9] == "9.97"


def test_raw_snapshot_stored_verbatim(tmp_ledger: Ledger) -> None:
    payload = {"agents": [{"handle": "x", "weird": [1, 2, {"nested": None}]}], "unicode": "ünïcode"}
    tmp_ledger.add_raw_snapshot(T0, "familiars", "/api/agents?range=7D", payload)
    tmp_ledger.add_raw_snapshot(T0, "jupiter", "/swap/v2/order", '{"raw": "text as received"}')
    snaps = tmp_ledger.raw_snapshots("familiars")
    assert len(snaps) == 1 and json.loads(snaps[0]["json"]) == payload
    assert tmp_ledger.raw_snapshots("jupiter")[0]["json"] == '{"raw": "text as received"}'
    assert len(tmp_ledger.raw_snapshots()) == 2


def test_events_and_position_exit_rule(tmp_ledger: Ledger) -> None:
    tmp_ledger.add_event("warn", "brake", {"reason": "dd7", "usd": Decimal("1.5"), "at": T0})
    ev = tmp_ledger.events(kind="brake")
    assert ev[0]["payload"]["reason"] == "dd7" and ev[0]["payload"]["usd"] == "1.5"
    tmp_ledger.record_fill(_fill("e1", T0))
    rule = ExitRule(stop_price=Decimal("120"), trail_pct=Decimal("0.25"))
    tmp_ledger.set_position_exit(SOL_MINT, "sol_trend_ensemble", rule)
    assert tmp_ledger.positions()[0].exit == rule
    tmp_ledger.replace_positions([])
    assert tmp_ledger.positions() == []
