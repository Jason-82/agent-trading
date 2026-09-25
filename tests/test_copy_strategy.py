"""Consensus rules, crowd-burst exclusion, late-entry guard and the (inert by default) live copy strategy."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from fakes import FakePriceSource, FakeTokenData
from tiller.clock import SimClock
from tiller.config import CopyCfg
from tiller.copy.models import LeaderTrade, ShadowReport
from tiller.copy.shadow import ShadowTracker
from tiller.copy.strategy import (
    ConsensusCopyStrategy,
    ConsensusSignal,
    CopyCandidate,
    TokenSnapshotRing,
    consensus,
    crowd_burst,
    late_entry_ok,
)
from tiller.ledger import Ledger
from tiller.models import Position, TokenInfoBoard
from tiller.strategies.base import MarketContext

D = Decimal
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
WINDOW = timedelta(minutes=30)
MINT = "ConsensusMintAaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
WIF = "xT92gRRMJCZY2uXBAdqkX829ZACN8BnGxmdjhFV4JN1M"
POPCAT = "UPGBtuvUtZ1ZRh8xYtgFWQjK8bbmAx22zywC4ZW9EFMQ"
BONK = "zBaNkPfSSMpLsBPfcYJV6tyowXHBgBKXJ6y7oRuUjWX"
POOL = {"fam:a", "fam:b", "fam:c", "fam:d"}
CLUSTERS = {"fam:a": 0, "fam:b": 1, "fam:c": 2, "fam:d": 0}  # d is a sybil of a


def buy(
    key: str,
    minutes_ago: int,
    price: str = "1.00",
    mint: str = MINT,
    side: str = "buy",
    verified: bool = True,
) -> LeaderTrade:
    ts = NOW - timedelta(minutes=minutes_ago)
    return LeaderTrade(
        key=key,
        wallet=key,
        signature=f"{key}:{mint}:{minutes_ago}:{side}",
        ts=ts,
        detected_at=ts,
        side=side,  # type: ignore[arg-type]
        mint=mint,
        usd_value=D(price) * 10,
        amount=D(10),
        price_usd=D(price),
        source="familiars",
        chain_verified=verified,
    )


# ----------------------------------------------------------------------------- consensus


def test_consensus_needs_three_distinct_clusters() -> None:
    trades = [buy("fam:a", 20, "1.00"), buy("fam:b", 10, "1.10"), buy("fam:c", 5, "1.20")]
    sig = consensus(trades, POOL, CLUSTERS, WINDOW, 3, NOW)
    assert sig == [
        ConsensusSignal(
            mint=MINT, earliest_leader_price=D("1.00"), cluster_count=3, first_ts=NOW - timedelta(minutes=20)
        )
    ]
    assert consensus(trades[:2], POOL, CLUSTERS, WINDOW, 3, NOW) == []
    # a sybil of 'a' does not add a cluster
    assert consensus([trades[0], trades[1], buy("fam:d", 3)], POOL, CLUSTERS, WINDOW, 3, NOW) == []
    assert (
        consensus([trades[0], trades[1], buy("fam:d", 3)], POOL, CLUSTERS, WINDOW, 2, NOW)[0].cluster_count
        == 2
    )


def test_consensus_window_pool_verification_and_side() -> None:
    fresh = [buy("fam:a", 29), buy("fam:b", 15), buy("fam:c", 1)]
    assert len(consensus(fresh, POOL, CLUSTERS, WINDOW, 3, NOW)) == 1
    stale = [buy("fam:a", 31), buy("fam:b", 15), buy("fam:c", 1)]
    assert consensus(stale, POOL, CLUSTERS, WINDOW, 3, NOW) == []
    outsider = [buy("fam:zzz", 5), buy("fam:b", 15), buy("fam:c", 1)]
    assert consensus(outsider, POOL | {"fam:zzz"}, CLUSTERS, WINDOW, 3, NOW) == []  # no cluster id -> ignored
    unverified = [buy("fam:a", 5, verified=False), buy("fam:b", 15), buy("fam:c", 1)]
    assert consensus(unverified, POOL, CLUSTERS, WINDOW, 3, NOW) == []
    sells = [buy("fam:a", 5, side="sell"), buy("fam:b", 15), buy("fam:c", 1)]
    assert consensus(sells, POOL, CLUSTERS, WINDOW, 3, NOW) == []
    future = [buy("fam:a", -1), buy("fam:b", 15), buy("fam:c", 1)]
    assert consensus(future, POOL, CLUSTERS, WINDOW, 3, NOW) == []


def test_consensus_earliest_price_and_per_mint_grouping() -> None:
    a = buy("fam:a", 25, "2.00").model_copy(update={"price_usd": None})
    trades = [
        a,
        buy("fam:b", 20, "1.50"),
        buy("fam:c", 2, "1.90"),
        buy("fam:a", 4, mint="Other"),
        buy("fam:b", 3, mint="Other"),
    ]
    sig = consensus(trades, POOL, CLUSTERS, WINDOW, 3, NOW)
    assert len(sig) == 1 and sig[0].mint == MINT
    assert (
        sig[0].earliest_leader_price == D("1.50") and sig[0].first_ts == a.ts
    )  # first priced fill after an unpriced one
    unpriced = [t.model_copy(update={"price_usd": None}) for t in trades[:3]]
    assert consensus(unpriced, POOL, CLUSTERS, WINDOW, 3, NOW) == []


# ----------------------------------------------------------------------------- crowd burst


def test_crowd_burst_from_tokens_fixtures(load_fixture: Callable[[str], Any]) -> None:
    prev = [TokenInfoBoard.model_validate(t) for t in load_fixture("familiars/tokens")]
    cur = [TokenInfoBoard.model_validate(t) for t in load_fixture("familiars/tokens_burst")]
    assert crowd_burst(prev, cur, WIF)  # 12 -> 40
    assert crowd_burst(prev, cur, POPCAT)  # new row with 22 agents
    assert not crowd_burst(prev, cur, BONK)  # unchanged
    assert not crowd_burst(prev, cur, "unknown")


def test_crowd_burst_threshold_is_strictly_more_than_max_delta() -> None:
    def rows(n: int) -> list[TokenInfoBoard]:
        return [TokenInfoBoard(mint=MINT, agents=n)]

    assert not crowd_burst(rows(10), rows(25), MINT, max_delta=15)
    assert crowd_burst(rows(10), rows(26), MINT, max_delta=15)
    assert not crowd_burst(rows(30), rows(10), MINT)


def test_token_snapshot_ring_fails_closed_until_an_hour_of_history() -> None:
    ring = TokenSnapshotRing()
    assert ring.burst(MINT, NOW)  # nothing recorded yet -> exclude
    ring.add(NOW - timedelta(minutes=59), [TokenInfoBoard(mint=MINT, agents=5)])
    ring.add(NOW, [TokenInfoBoard(mint=MINT, agents=30)])
    assert ring.burst(MINT, NOW)  # no snapshot >= 1 h old yet
    ring2 = TokenSnapshotRing()
    ring2.add(NOW - timedelta(hours=1), [TokenInfoBoard(mint=MINT, agents=5)])
    ring2.add(NOW - timedelta(minutes=30), [TokenInfoBoard(mint=MINT, agents=12)])
    ring2.add(NOW, [TokenInfoBoard(mint=MINT, agents=20)])
    assert not ring2.burst(MINT, NOW)  # 5 -> 20 = +15, not a burst
    ring2.add(NOW, [TokenInfoBoard(mint=MINT, agents=21)])
    assert ring2.burst(MINT, NOW)
    ring2.add(NOW + timedelta(hours=3), [TokenInfoBoard(mint=MINT, agents=21)])
    assert ring2.at_least(timedelta(hours=1), NOW + timedelta(hours=3)) is None  # old rows pruned after 2 h


# ----------------------------------------------------------------------------- late entry


def test_late_entry_guard() -> None:
    assert late_entry_ok(D("1.15"), D("1.00"))
    assert not late_entry_ok(D("1.1501"), D("1.00"))
    assert late_entry_ok(D("0.50"), D("1.00"))
    assert late_entry_ok(D("1.20"), D("1.00"), max_above=D("0.20"))
    assert (
        not late_entry_ok(D("1.00"), D(0)) and not late_entry_ok(None, D(1)) and not late_entry_ok(D(1), None)
    )


# ----------------------------------------------------------------------------- live strategy


class StubTracker:
    """Reports whatever gate state the test wants; never touches a ledger."""

    def __init__(self, met: bool) -> None:
        self.met = met

    def report(self, days: int = 60) -> ShadowReport:
        return ShadowReport(
            days=90,
            n_trades=150,
            expectancy_pct=D("0.02"),
            profit_factor=1.5,
            median_lag_s=60.0,
            median_lag_cost_pct=D("0.003"),
            p_positive_block20=0.9,
            leaders_positive_share=0.6,
            promotion_gate_met=self.met,
            reasons=[] if self.met else ["x"],
        )


def signal(mint: str = MINT, price: str = "1.00") -> ConsensusSignal:
    return ConsensusSignal(
        mint=mint, earliest_leader_price=D(price), cluster_count=3, first_ts=NOW - timedelta(minutes=10)
    )


def candidate(
    mint: str = MINT, current: str = "1.05", gate_ok: bool = True, burst: bool = False, first: str = "1.00"
) -> CopyCandidate:
    return CopyCandidate(
        signal=signal(mint, first), current_price=D(current), gate_ok=gate_ok, crowd_burst=burst
    )


def ctx(positions: list[Position] | None = None, equity: str = "1000", now: datetime = NOW) -> MarketContext:
    return MarketContext(now=now, candles={}, equity_usd=D(equity), positions=positions or [], regime_on=True)


def strategy(
    live: bool = True, met: bool = True, regime: bool = True, gate: bool = True, **over: Any
) -> ConsensusCopyStrategy:
    cfg = CopyCfg(live=live, shadow_enabled=True, **over)
    return ConsensusCopyStrategy(cfg, StubTracker(met), lambda _m: gate, lambda: regime)  # type: ignore[arg-type]


def test_targets_empty_unless_live_gate_and_regime() -> None:
    for kw, reason in (
        (dict(live=False), "copy.live"),
        (dict(met=False), "promotion gate"),
        (dict(regime=False), "regime"),
    ):
        s = strategy(**kw)  # type: ignore[arg-type]
        s.propose([candidate()])
        assert s.targets(ctx())[0] == []
        ok, why = s.enabled()
        assert not ok and reason in why


def test_default_config_is_inert() -> None:
    s = ConsensusCopyStrategy(CopyCfg(), StubTracker(True), lambda _m: True, lambda: True)  # type: ignore[arg-type]
    s.propose([candidate()])
    assert s.targets(ctx())[0] == [] and s.name == "copy_consensus" and s.cadence == "monitor"


def test_live_entry_carries_exit_rule_and_sizing() -> None:
    s = strategy()
    s.propose([candidate(current="1.05")])
    targets, _ = s.targets(ctx())
    assert len(targets) == 1
    t = targets[0]
    assert t.mint == MINT and t.strategy == "copy_consensus" and t.weight == D("0.02")
    assert t.exit is not None
    assert t.exit.stop_price == D("1.05") * D("0.80") and t.exit.trail_pct == D("0.25")
    assert t.exit.trail_from_gain_pct == D("0.30") and t.exit.time_stop_at == NOW + timedelta(hours=48)
    assert "consensus of 3 clusters" in t.reason
    # the candidate is consumed: the next tick emits nothing for it
    assert s.targets(ctx())[0] == []


def test_live_entry_respects_gate_burst_and_late_entry() -> None:
    s = strategy()
    s.propose(
        [candidate(gate_ok=False), candidate(mint="B", burst=True), candidate(mint="C", current="1.16")]
    )
    assert s.targets(ctx())[0] == []
    s = strategy(gate=False)
    s.propose([candidate()])
    assert s.targets(ctx())[0] == []
    s = strategy()
    s.propose([candidate(mint="C", current="1.15")])
    assert [t.mint for t in s.targets(ctx())[0]] == ["C"]


def test_caps_concurrent_per_day_and_sleeve() -> None:
    held = [
        Position(mint="H1", amount_base=1, cost_usd=D(20), opened_at=NOW, strategy="copy_consensus"),
        Position(mint="H2", amount_base=1, cost_usd=D(20), opened_at=NOW, strategy="copy_consensus"),
    ]
    s = strategy()
    s.propose([candidate()])
    assert s.targets(ctx(held))[0] == []  # 2 concurrent
    other = [
        Position(mint="SOL", amount_base=1, cost_usd=D(400), opened_at=NOW, strategy="sol_trend_ensemble")
    ]
    assert len(s.targets(ctx(other))[0]) == 1  # other sleeves do not count
    # 2 entries per UTC day, one per tick
    s = strategy()
    s.propose([candidate("A"), candidate("B"), candidate("C")])
    assert [t.mint for t in s.targets(ctx())[0]] == ["A"]
    assert [t.mint for t in s.targets(ctx())[0]] == ["B"]
    assert s.targets(ctx())[0] == [] and s.entries_today(NOW) == 2
    assert [t.mint for t in s.targets(ctx(now=NOW + timedelta(days=1)))[0]] == ["C"]
    # sleeve cap 5%: one copy position at 4% leaves 1%
    s = strategy()
    s.propose([candidate()])
    four_pct = [Position(mint="H1", amount_base=1, cost_usd=D(40), opened_at=NOW, strategy="copy_consensus")]
    assert s.targets(ctx(four_pct))[0][0].weight == D("0.01")
    s = strategy()
    s.propose([candidate()])
    full = [Position(mint="H1", amount_base=1, cost_usd=D(50), opened_at=NOW, strategy="copy_consensus")]
    assert s.targets(ctx(full))[0] == []
    # already-held mint is skipped
    s = strategy()
    s.propose([candidate()])
    assert (
        s.targets(
            ctx(
                [Position(mint=MINT, amount_base=1, cost_usd=D(10), opened_at=NOW, strategy="copy_consensus")]
            )
        )[0]
        == []
    )


async def test_strategy_with_real_tracker_stays_inert_on_empty_book(tmp_path: Path) -> None:
    clock = SimClock(NOW)
    ledger = Ledger(tmp_path / "l.sqlite", clock=clock)
    tracker = ShadowTracker(None, FakePriceSource({}), FakeTokenData(), ledger, CopyCfg(live=True), clock)
    s = ConsensusCopyStrategy(CopyCfg(live=True), tracker, lambda _m: True, lambda: True)
    s.propose([candidate()])
    assert s.targets(ctx())[0] == []
    ok, why = s.enabled()
    assert not ok and "promotion gate not met" in why and "trades 0 < 100" in why


def test_copy_live_requires_shadow_enabled() -> None:
    """Config-level L0 invariant is WP-A's; the strategy refuses independently as defence in depth."""
    s = ConsensusCopyStrategy(
        CopyCfg(live=True, shadow_enabled=False), StubTracker(True), lambda _m: True, lambda: True
    )  # type: ignore[arg-type]
    s.propose([candidate()])
    assert s.enabled() == (False, "copy.shadow_enabled is false") and s.targets(ctx())[0] == []
