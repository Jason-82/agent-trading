"""Adversarial review (lens: trading correctness and restart safety).

Each test encodes the CORRECT behaviour for a defect found in the review; they fail on the
current code and document the fix the engine/ledger/adapter must implement:

1. a price-source outage (unpriced holdings) must never trigger the dd30 hard flatten;
2. a live order left ``submitted`` (ExecutionUncertain) must block a fresh entry for the same
   mint on the next tick instead of re-quoting and risking a double fill;
3. the ledger must reduce a mint's position rows when the sell is booked under a different
   strategy label (flatten sells under 'flatten', reconciled rows under 'reconciled'), so an
   emergency flatten does not leave phantom positions behind;
4. a withdrawal (``tiller sweep``) must not trip the daily-loss brake;
5. the copy adapter must evaluate consensus over the 30-minute window of LOGGED trades, not
   only over the trades that happened to verify in the same poll;
6. ``maxPositionUsd`` is a cap on the resulting position, not on each order;
7. a leader buy detected long after its fill must not open a shadow trade at today's price.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from fakes import FakePriceSource, FakeTokenData
from test_engine_e2e import ConstStrategy, LiveHarness, PaperHarness, open_at, start_of
from tiller.clock import SimClock
from tiller.copy.models import LeaderTrade
from tiller.copy.shadow import ShadowTracker
from tiller.execution.venue import ExecutionUncertain
from tiller.ledger import Ledger
from tiller.models import SOL_MINT, USDC_MINT, Candle, Fill, SwapRequest
from tiller.portfolio.allocator import OrderIntent
from tiller.risk.engine import Limits, size_order
from wpd_helpers import LAMPORTS, TOKEN_X, make_cfg, make_snapshot

DAY = timedelta(days=1)


# --------------------------------------------------------------------------- 1. price outage


async def test_price_outage_must_not_trigger_hard_flatten(
    tmp_path: Path, load_fixture: Any, sol_daily: list[Candle]
) -> None:
    """With SOL unpriced the snapshot equity collapses to the USDC leg; that is a data failure,
    not a drawdown. The tick must block entries (fail closed) and keep every position.

    A full outage is only saved by accident today (an unpriced holding is valued at 0, so
    ``is_flat`` cancels the flatten); a PARTIAL outage (SOL mark missing, a token still priced)
    sells the priced holdings in emergency mode and halts the agent.
    """
    start = start_of(sol_daily, "2025-08-05")
    h = PaperHarness(
        tmp_path, load_fixture, sol_daily, start, strategies=[ConstStrategy("sol_trend_ensemble", "0.30")]
    )
    h.seed_paper_position(TOKEN_X, Decimal(100), 2_000_000_000, "copy_consensus")
    rep = await h.tick()
    assert len(rep.fills) == 1 and rep.fills[0].out_mint == SOL_MINT
    before = {(p.mint, p.strategy): p.amount_base for p in h.ledger.positions()}
    assert len(before) == 2

    table = h.prices.table

    async def partial_outage(mints: list[str]) -> dict[str, Decimal]:
        return {m: table[m] for m in mints if m in table and m != SOL_MINT}

    h.clock.set(start + timedelta(minutes=1))
    h.prices.usd_prices = partial_outage  # type: ignore[method-assign]
    rep2 = await h.agent.tick()
    assert not rep2.flattened, "hard flatten fired on an equity figure that is missing the SOL mark"
    assert not rep2.halted
    assert rep2.fills == []
    assert rep2.brakes.entries_blocked
    assert {(p.mint, p.strategy): p.amount_base for p in h.ledger.positions()} == before


# --------------------------------------------------------------------------- 2. pending order


class UncertainOnceVenue:
    """Live-shaped venue: the first swap is recorded ``submitted`` and its outcome is unknown."""

    def __init__(self, ledger: Ledger, clock: SimClock) -> None:
        self.ledger = ledger
        self.clock = clock
        self.requests: list[SwapRequest] = []

    async def swap(self, req: SwapRequest, ref_price_usd: Decimal, gate: Any) -> Fill:
        self.requests.append(req)
        sig = f"pending{len(self.requests):03d}" + "x" * 80
        self.ledger.record_order(req, None, sig)
        raise ExecutionUncertain("no confirmation within 90s")


async def test_pending_submitted_order_blocks_reentry_for_same_mint(
    tmp_path: Path, load_fixture: Any, sol_daily: list[Candle]
) -> None:
    """An order that was sent but not confirmed may still land. Until reconciliation resolves
    the signature, a second buy of the same mint must be refused (never re-quote a pending order)."""
    start = start_of(sol_daily, "2025-08-05")
    h = LiveHarness(tmp_path, load_fixture, sol_daily, start, reconcile_every=10)
    venue = UncertainOnceVenue(h.ledger, h.clock)
    h.venue = venue  # type: ignore[assignment]
    h.agent = h.make_agent()
    rep = await h.tick(start)
    assert rep.fills == [] and len(venue.requests) == 1
    assert h.ledger.pending_signatures(), "the submitted order must stay pending"
    rep2 = await h.tick(start + timedelta(minutes=1))
    assert len(venue.requests) == 1, "a fresh order was quoted while the previous one is still pending"
    assert rep2.refused and any("pending" in why.lower() for _intent, why in rep2.refused), rep2.refused


# --------------------------------------------------------------------------- 3. phantom positions


def _paper_fill(
    in_mint: str, out_mint: str, in_base: int, out_base: int, usd: Decimal, strategy: str, ts: datetime
) -> Fill:
    return Fill(
        signature=None,
        in_mint=in_mint,
        out_mint=out_mint,
        in_base=in_base,
        out_base=out_base,
        usd_in=usd,
        usd_out=usd,
        fee_usd=Decimal(0),
        ts=ts,
        paper=True,
        strategy=strategy,
        quote_out_base=out_base,
        mode="emergency" if strategy == "flatten" else "normal",
    )


def test_ledger_sell_under_another_label_reduces_the_mint_position(tmp_path: Path) -> None:
    """A full sell of a mint booked under 'core' but executed as 'flatten' must leave no position."""
    ledger = Ledger(tmp_path / "l.sqlite")
    t0 = datetime(2025, 8, 5, 0, 5, tzinfo=UTC)
    ledger.record_fill(
        _paper_fill(USDC_MINT, SOL_MINT, 1_000_000_000, 5 * LAMPORTS, Decimal(1000), "core", t0)
    )
    assert [p.strategy for p in ledger.positions()] == ["core"]
    ledger.record_fill(
        _paper_fill(
            SOL_MINT, USDC_MINT, 5 * LAMPORTS, 990_000_000, Decimal(990), "flatten", t0 + timedelta(minutes=1)
        )
    )
    assert ledger.positions() == [], "phantom position survives a flatten sell"


async def test_flatten_leaves_no_ledger_positions(
    tmp_path: Path, load_fixture: Any, sol_daily: list[Candle]
) -> None:
    """After the kill drill the paper book is flat, so the positions table must be empty too;
    otherwise ``max_positions`` and reconciliation see holdings that no longer exist."""
    start = start_of(sol_daily, "2025-08-05")
    h = PaperHarness(
        tmp_path, load_fixture, sol_daily, start, strategies=[ConstStrategy("sol_trend_ensemble", "0.30")]
    )
    px = open_at(sol_daily, start)
    h.seed_paper_position(SOL_MINT, Decimal(20) * px, 20 * LAMPORTS, "core")
    h.seed_paper_position(TOKEN_X, Decimal(100), 2_000_000_000, "copy_consensus")
    equity_now = Decimal(10_000) + Decimal("0.1") * px
    deposits = Decimal(10_000) + Decimal("0.1") * px
    h.ledger.add_equity_snapshot(start - 10 * DAY, equity_now * Decimal("1.36"), deposits, "tick")
    rep = await h.tick()
    assert rep.flattened and len(rep.fills) == 2
    assert h.ledger.positions() == [], [p.model_dump() for p in h.ledger.positions()]


# --------------------------------------------------------------------------- 4. withdrawal vs daily loss


async def test_withdrawal_does_not_trip_daily_loss_brake(
    tmp_path: Path, load_fixture: Any, sol_daily: list[Candle]
) -> None:
    """Equity minus net deposits is the P&L series; a sweep of 8% of the wallet is not a loss."""
    start = start_of(sol_daily, "2025-08-05")
    h = PaperHarness(
        tmp_path, load_fixture, sol_daily, start, strategies=[ConstStrategy("sol_trend_ensemble", "0")]
    )
    rep = await h.tick()
    assert not rep.brakes.entries_blocked
    h.ledger.record_transfer(start + timedelta(minutes=30), USDC_MINT, 800_000_000, Decimal(800), "out")
    rep2 = await h.tick(start + timedelta(hours=1))
    assert not any(r.startswith("daily_loss") for r in rep2.brakes.reasons), rep2.brakes.reasons


# --------------------------------------------------------------------------- 5. copy adapter consensus


class _OneTradePerPollFeed:
    def __init__(self, trades: list[LeaderTrade]) -> None:
        self.trades = list(trades)
        self.polls = 0

    async def poll(self, keys: Any) -> list[LeaderTrade]:
        self.polls += 1
        return [self.trades.pop(0)] if self.trades else []


class _RecordingShadow:
    def __init__(self) -> None:
        self.calls: list[tuple[list[LeaderTrade], set[str], list[str]]] = []

    async def on_leader_trades(self, trades: Any, pool: set[str], consensus_mints: Any) -> list[Any]:
        self.calls.append((list(trades), set(pool), list(consensus_mints)))
        return []


async def test_copy_adapter_consensus_spans_polls_within_window() -> None:
    """Three unrelated leaders buying the same mint 60 s apart is a consensus signal (>= 3
    clusters within 30 min). The adapter must look at the logged window, not one poll's batch."""
    from tiller.cli import _CopyFeedAdapter

    cfg = make_cfg()
    t0 = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
    trades = [
        LeaderTrade(
            key=f"fam:leader{i}",
            wallet=f"wallet{i}",
            signature=f"sig{i}" + "x" * 84,
            ts=t0 + i * timedelta(seconds=60),
            detected_at=t0 + i * timedelta(seconds=60),
            side="buy",
            mint=TOKEN_X,
            usd_value=Decimal(100),
            amount=Decimal(1000),
            price_usd=Decimal("0.1"),
            source="familiars",
            chain_verified=True,
        )
        for i in range(3)
    ]
    feed = _OneTradePerPollFeed(trades)
    shadow = _RecordingShadow()

    async def keys() -> list[str]:
        return ["leader0", "leader1", "leader2"]

    adapter = _CopyFeedAdapter(feed, keys, shadow, cfg)
    for i in range(3):
        await adapter.poll(t0 + i * timedelta(seconds=60) + timedelta(seconds=5))
    assert feed.polls == 3
    assert any(TOKEN_X in mints for _t, _p, mints in shadow.calls), [c[2] for c in shadow.calls]


# --------------------------------------------------------------------------- 6. maxPositionUsd


def test_max_position_usd_caps_the_resulting_position_not_each_order() -> None:
    """Owner cap 100 USD with 3907 USD of SOL already held: no further SOL entry may be sized."""
    acct = make_snapshot()  # 26.05 SOL at 150 = ~3907 USD held
    limits = Limits(max_position_usd=Decimal(100), daily_limit_usd=Decimal(1000))
    intent = OrderIntent(mint=SOL_MINT, side="buy", usd=Decimal(50), strategy="core", reason="t")
    sized = size_order(intent, acct, limits, make_cfg(), None, acct.day, None)
    held = Decimal(acct.sol_lamports) / LAMPORTS * acct.marks[SOL_MINT]
    assert sized is None or held + sized.usd <= Decimal(100), sized


# --------------------------------------------------------------------------- 7. stale leader buys


async def test_shadow_tracker_ignores_stale_leader_buys(tmp_path: Path) -> None:
    """A buy verified ten days after the leader's fill is history for scoring, not a signal:
    opening a shadow trade at today's quote with a ten-day 'lag' corrupts the per-leader stats."""
    now = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
    clock = SimClock(now)
    ledger = Ledger(tmp_path / "l.sqlite", clock=clock)
    prices = FakePriceSource({TOKEN_X: Decimal("0.2")})
    tracker = ShadowTracker(
        None, prices, FakeTokenData(decimals={TOKEN_X: 6}), ledger, make_cfg().copy, clock
    )
    stale = LeaderTrade(
        key="fam:old",
        wallet="wallet_old",
        signature="stale" + "x" * 83,
        ts=now - 10 * DAY,
        detected_at=now,
        side="buy",
        mint=TOKEN_X,
        usd_value=Decimal(100),
        amount=Decimal(1000),
        price_usd=Decimal("0.1"),
        source="familiars",
        chain_verified=True,
    )
    opened = await tracker.on_leader_trades([stale], {"fam:old"}, [])
    assert opened == [], [t.model_dump() for t in opened]
    assert ledger.shadow_trades(since=now - 30 * DAY) == []
