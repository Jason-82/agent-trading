"""Leader eligibility, replay scoring, clustering, sticky pool and demotion on seeded synthetic streams."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tiller.config import CopyCfg
from tiller.copy.leaders import (
    SeriesMarkStore,
    cluster,
    copy_exit_rule,
    eligible,
    exit_reason,
    exit_template,
    mint_overlap,
    platform_drawdown,
    replay,
    round_trips,
    score_leaders,
    select_pool,
    should_demote,
    tighten_for_leader_sell,
    transfer_near_pnl_jump,
    worst_of_entry,
)
from tiller.copy.models import LeaderScore, LeaderTrade
from tiller.models import AgentDetail, ExitRule, TokenInfo

T0 = datetime(2026, 7, 1, tzinfo=UTC)
NOW = T0 + timedelta(days=40)
CFG = CopyCfg()
MINTS = [f"Mint{i:02d}xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" for i in range(14)]
WALLET_A = "LeaderAaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
D = Decimal


def price_paths(rng: random.Random, drift: float = 0.0, hours: int = 40 * 24) -> SeriesMarkStore:
    """Hourly geometric random walks for every mint (deterministic under the seed)."""
    store = SeriesMarkStore()
    for i, mint in enumerate(MINTS):
        px = 1.0 + i * 0.5
        for h in range(hours + 1):
            store.add(mint, T0 + timedelta(hours=h), D(f"{px:.6f}"))
            px *= 1.0 + drift + rng.gauss(0, 0.02)
    return store


def make_leader(
    rng: random.Random,
    key: str,
    wallet: str,
    marks: SeriesMarkStore,
    n: int = 40,
    mints: list[str] | None = None,
    hold_h: tuple[int, int] = (6, 30),
    start_day: int = 0,
) -> list[LeaderTrade]:
    """``n`` buy/sell round trips priced off the mark store (buy at t, sell at t+hold)."""
    mints = mints or MINTS[:12]
    out: list[LeaderTrade] = []
    t = T0 + timedelta(days=start_day, hours=1)
    for i in range(n):
        mint = mints[i % len(mints)]
        hold = timedelta(hours=rng.randint(*hold_h))
        amt = D(rng.randint(50, 500))
        for side, ts in (("buy", t), ("sell", t + hold)):
            px = marks.price_at(mint, ts) or D(1)
            out.append(
                LeaderTrade(
                    key=key,
                    wallet=wallet,
                    signature=f"{key}-{i}-{side}",
                    ts=ts,
                    detected_at=ts + timedelta(seconds=30),
                    side=side,
                    mint=mint,
                    usd_value=amt * px,
                    amount=amt,
                    price_usd=px,
                    source="familiars",
                    chain_verified=True,
                )
            )
        t += timedelta(hours=rng.randint(12, 20))
    return out


def meta_for(
    mints: list[str],
    dev: str = "DevXxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    created: datetime = T0 - timedelta(days=30),
) -> dict[str, TokenInfo]:
    return {
        m: TokenInfo.model_validate({"id": m, "symbol": m[:4], "firstPoolCreatedAt": created, "dev": dev})
        for m in mints
    }


def detail_with_history(points: list[tuple[int, str, str]]) -> AgentDetail:
    """points = [(day, equity, net_deposits)] -> AgentDetail with history snapshots."""
    snaps = [
        {
            "timestamp": int((T0 + timedelta(days=d)).timestamp() * 1000),
            "equityUsd": e,
            "netDepositsUsd": nd,
            "pnlUsd": str(D(e) - D(nd)),
        }
        for d, e, nd in points
    ]
    return AgentDetail.model_validate(
        {
            "agent": {
                "handle": "x",
                "wallet": WALLET_A,
                "hosted": False,
                "equityUsd": "1000",
                "pnl": {"7D": "1"},
                "trades": 1,
            },
            "history": {"snapshots": snaps},
        }
    )


@pytest.fixture
def rng() -> random.Random:
    return random.Random(42)


@pytest.fixture
def marks(rng: random.Random) -> SeriesMarkStore:
    return price_paths(rng)


@pytest.fixture
def good(rng: random.Random, marks: SeriesMarkStore) -> list[LeaderTrade]:
    return make_leader(rng, "fam:good", WALLET_A, marks)


# ----------------------------------------------------------------------------- round trips


def test_round_trips_fifo_partial_fills() -> None:
    def t(side: str, amt: int, usd: int, h: int) -> LeaderTrade:
        return LeaderTrade(
            key="k",
            wallet="w",
            signature=f"{side}{h}",
            ts=T0 + timedelta(hours=h),
            detected_at=T0,
            side=side,  # type: ignore[arg-type]
            mint=MINTS[0],
            usd_value=D(usd),
            amount=D(amt),
            price_usd=D(usd) / D(amt),
            source="wallet",
            chain_verified=True,
        )

    closed, open_lots = round_trips([t("buy", 100, 100, 0), t("buy", 100, 120, 1), t("sell", 150, 300, 5)])
    assert [(rt.amount, rt.buy_usd, rt.sell_usd) for rt in closed] == [
        (D(100), D(100), D(200)),
        (D(50), D(60), D(100)),
    ]
    assert closed[0].hold == timedelta(hours=5)
    assert open_lots == {MINTS[0]: (D(50), D(60))}


# ----------------------------------------------------------------------------- eligibility


def test_good_leader_is_eligible(good: list[LeaderTrade]) -> None:
    ok, reasons = eligible(good, None, meta_for(MINTS), CFG, NOW, T0)
    assert ok, reasons


def test_insufficient_history(good: list[LeaderTrade]) -> None:
    ok, reasons = eligible(good, None, meta_for(MINTS), CFG, NOW, NOW - timedelta(days=13, hours=23))
    assert not ok and "insufficient_history" in reasons
    ok, reasons = eligible(good, None, meta_for(MINTS), CFG, NOW, NOW - timedelta(days=14))
    assert "insufficient_history" not in reasons


def test_too_few_round_trips(rng: random.Random, marks: SeriesMarkStore) -> None:
    trades = make_leader(rng, "fam:few", WALLET_A, marks, n=29)
    assert "too_few_round_trips" in eligible(trades, None, meta_for(MINTS), CFG, NOW, T0)[1]
    trades = make_leader(rng, "fam:enough", WALLET_A, marks, n=30)
    assert "too_few_round_trips" not in eligible(trades, None, meta_for(MINTS), CFG, NOW, T0)[1]


def test_unverified_trades_do_not_count(good: list[LeaderTrade]) -> None:
    unverified = [t.model_copy(update={"chain_verified": False}) for t in good]
    ok, reasons = eligible(unverified, None, meta_for(MINTS), CFG, NOW, T0)
    assert not ok and "too_few_round_trips" in reasons


def test_too_few_mints(rng: random.Random, marks: SeriesMarkStore) -> None:
    trades = make_leader(rng, "fam:narrow", WALLET_A, marks, mints=MINTS[:9])
    assert "too_few_mints" in eligible(trades, None, meta_for(MINTS), CFG, NOW, T0)[1]
    trades = make_leader(rng, "fam:wide", WALLET_A, marks, mints=MINTS[:10])
    assert "too_few_mints" not in eligible(trades, None, meta_for(MINTS), CFG, NOW, T0)[1]


def test_median_hold_too_short(rng: random.Random, marks: SeriesMarkStore) -> None:
    trades = make_leader(rng, "fam:scalper", WALLET_A, marks, hold_h=(1, 3))
    assert "median_hold_too_short" in eligible(trades, None, meta_for(MINTS), CFG, NOW, T0)[1]
    trades = make_leader(rng, "fam:swing", WALLET_A, marks, hold_h=(4, 4))
    assert "median_hold_too_short" not in eligible(trades, None, meta_for(MINTS), CFG, NOW, T0)[1]


def test_concentrated_pnl(good: list[LeaderTrade]) -> None:
    boosted = [
        t.model_copy(update={"usd_value": t.usd_value * 50})
        if (t.mint == MINTS[0] and t.side == "sell")
        else t
        for t in good
    ]
    assert "concentrated_pnl" in eligible(boosted, None, meta_for(MINTS), CFG, NOW, T0)[1]
    assert "concentrated_pnl" not in eligible(good, None, meta_for(MINTS), CFG, NOW, T0)[1]


def test_concentrated_pnl_marks_open_lots(good: list[LeaderTrade]) -> None:
    """An open position marked far above cost counts toward the single-mint share."""
    extra = good[0].model_copy(
        update={
            "signature": "open-lot",
            "mint": MINTS[13],
            "ts": NOW - timedelta(hours=1),
            "amount": D(100),
            "usd_value": D(100),
        }
    )
    with_open = [*good, extra]
    assert "concentrated_pnl" not in eligible(with_open, None, meta_for(MINTS), CFG, NOW, T0)[1]
    assert (
        "concentrated_pnl"
        in eligible(with_open, None, meta_for(MINTS), CFG, NOW, T0, marks={MINTS[13]: D(1000)})[1]
    )


def test_platform_drawdown_rule(good: list[LeaderTrade]) -> None:
    deep = detail_with_history(
        [(0, "1000", "1000"), (5, "1400", "1000"), (10, "980", "1000"), (20, "1500", "1000")]
    )
    assert platform_drawdown(deep) == pytest.approx(0.30)
    assert "platform_drawdown" in eligible(good, deep, meta_for(MINTS), CFG, NOW, T0)[1]
    shallow = detail_with_history([(0, "1000", "1000"), (5, "1400", "1000"), (10, "1000", "1000")])
    assert "platform_drawdown" not in eligible(good, shallow, meta_for(MINTS), CFG, NOW, T0)[1]


def test_platform_drawdown_ignores_deposits() -> None:
    """A withdrawal is not a drawdown; a deposit is not a gain."""
    d = detail_with_history([(0, "1000", "1000"), (1, "2000", "2000"), (2, "1000", "1000")])
    assert platform_drawdown(d) == 0.0


def test_young_mint_buy_and_unknown_age(good: list[LeaderTrade]) -> None:
    first_buy = next(t for t in good if t.side == "buy")
    meta = meta_for(MINTS)
    meta[first_buy.mint] = TokenInfo.model_validate(
        {
            "id": first_buy.mint,
            "symbol": "Y",
            "firstPoolCreatedAt": first_buy.ts - timedelta(hours=23),
            "dev": "d",
        }
    )
    assert "young_mint_buy" in eligible(good, None, meta, CFG, NOW, T0)[1]
    meta[first_buy.mint] = TokenInfo.model_validate(
        {
            "id": first_buy.mint,
            "symbol": "Y",
            "firstPoolCreatedAt": first_buy.ts - timedelta(hours=24),
            "dev": "d",
        }
    )
    assert "young_mint_buy" not in eligible(good, None, meta, CFG, NOW, T0)[1]
    del meta[first_buy.mint]
    assert "unknown_mint_age" in eligible(good, None, meta, CFG, NOW, T0)[1]
    assert "unknown_mint_age" not in eligible(good, None, meta, CFG, NOW, T0, allowlist=[first_buy.mint])[1]


def test_own_token(good: list[LeaderTrade]) -> None:
    meta = meta_for(MINTS)
    meta[MINTS[3]] = TokenInfo.model_validate(
        {"id": MINTS[3], "symbol": "OWN", "firstPoolCreatedAt": T0 - timedelta(days=9), "dev": WALLET_A}
    )
    assert "own_token" in eligible(good, None, meta, CFG, NOW, T0)[1]


def test_transfer_near_pnl_jump(good: list[LeaderTrade]) -> None:
    jump = detail_with_history(
        [(0, "1000", "1000"), (1, "1000", "1000"), (2, "1600", "1300"), (3, "1600", "1300")]
    )
    assert transfer_near_pnl_jump(jump)
    assert "transfer_near_pnl_jump" in eligible(good, jump, meta_for(MINTS), CFG, NOW, T0)[1]
    calm = detail_with_history([(0, "1000", "1000"), (1, "1300", "1300"), (10, "1600", "1300")])
    assert not transfer_near_pnl_jump(calm)


# ----------------------------------------------------------------------------- exit rules


def test_exit_reason_order_and_thresholds() -> None:
    rule = copy_exit_rule(D(100), T0, CFG)
    assert rule.stop_price == D(80) and rule.time_stop_at == T0 + timedelta(hours=48)
    assert exit_reason(D("80.01"), D(100), D(100), rule, T0) is None
    assert exit_reason(D(80), D(100), D(100), rule, T0) == "stop"
    assert exit_reason(D(100), D(129), D(100), rule, T0) is None  # trail not armed below +30%
    assert exit_reason(D("97.6"), D(130), D(100), rule, T0) is None  # 130 * 0.75 = 97.5
    assert exit_reason(D("97.5"), D(130), D(100), rule, T0) == "trail"
    assert exit_reason(D(100), D(100), D(100), rule, T0 + timedelta(hours=48)) == "time_stop"
    assert (
        exit_reason(D(100), D(100), D(100), rule, T0, liquidity_usd=D(41), entry_liquidity_usd=D(100)) is None
    )
    assert (
        exit_reason(D(100), D(100), D(100), rule, T0, liquidity_usd=D(40), entry_liquidity_usd=D(100))
        == "liquidity_collapse"
    )


def test_tighten_for_leader_sell_arms_15pct_trail() -> None:
    rule = copy_exit_rule(D(100), T0, CFG)
    tight = tighten_for_leader_sell(rule)
    assert tight.trail_pct == D("0.15") and tight.trail_from_gain_pct == 0 and tight.stop_price == D(80)
    assert exit_reason(D(90), D(100), D(100), rule, T0) is None
    assert exit_reason(D(90), D(100), D(100), tight, T0) is None  # 100 * 0.85 = 85
    assert exit_reason(D(85), D(100), D(100), tight, T0) == "trail"
    assert tighten_for_leader_sell(tight.model_copy(update={"trail_pct": D("0.10")})).trail_pct == D("0.10")


def test_worst_of_entry() -> None:
    assert worst_of_entry(D(100), D(100)) == D(101)
    assert worst_of_entry(D(105), D(100)) == D(105)
    assert worst_of_entry(None, D(100)) == D(101)
    assert worst_of_entry(D(100), None) == D(100)
    assert worst_of_entry(None, None) is None


# ----------------------------------------------------------------------------- replay


def _buy(mint: str, ts: datetime, price: str, key: str = "fam:l", sig: str = "b1") -> LeaderTrade:
    return LeaderTrade(
        key=key,
        wallet="w",
        signature=sig,
        ts=ts,
        detected_at=ts,
        side="buy",
        mint=mint,
        usd_value=D(price) * 10,
        amount=D(10),
        price_usd=D(price),
        source="familiars",
        chain_verified=True,
    )


def _series(mint: str, pts: list[tuple[int, str]]) -> SeriesMarkStore:
    return SeriesMarkStore({mint: [(T0 + timedelta(minutes=m), D(p)) for m, p in pts]})


def test_replay_hand_computed_trail_exit() -> None:
    m = MINTS[0]
    marks = _series(m, [(0, "1.00"), (1, "1.00"), (60, "1.50"), (120, "1.10"), (180, "1.20")])
    res = replay([_buy(m, T0, "1.00")], marks, exit_template(CFG), lag_s=60, slip=D("0.01"), fee=D("0.001"))
    assert res.n == 1
    tr = res.trades[0]
    assert tr.entry_ts == T0 + timedelta(seconds=60)
    assert tr.entry_price == D("1.01") * D("1.011")  # worst-of(quote 1.00, leader 1.01) plus 1% slip + 10 bps
    assert tr.reason == "trail" and tr.exit_ts == T0 + timedelta(minutes=120)
    assert tr.exit_price == D("1.10") * D("0.989")
    assert tr.ret == tr.exit_price / tr.entry_price - 1
    assert (
        res.expectancy_pct == tr.ret and res.pf == pytest.approx(999.0) and res.weekly_positive_share == 1.0
    )


def test_replay_stop_time_stop_and_open() -> None:
    m = MINTS[1]
    stop = _series(m, [(0, "2.00"), (30, "1.9"), (90, "1.60")])  # 2.02 * 0.8 = 1.616
    res = replay([_buy(m, T0, "2.00")], stop, exit_template(CFG))
    assert res.trades[0].reason == "stop" and res.trades[0].exit_ts == T0 + timedelta(minutes=90)
    flat = _series(m, [(0, "2.00"), (60, "2.00"), (48 * 60, "2.05"), (48 * 60 + 1, "2.05")])
    res = replay([_buy(m, T0, "2.00")], flat, exit_template(CFG))
    assert res.trades[0].reason == "time_stop" and res.trades[0].exit_ts == T0 + timedelta(
        hours=48, minutes=1
    )
    short = _series(m, [(0, "2.00"), (60, "2.10")])
    res = replay([_buy(m, T0, "2.00")], short, exit_template(CFG))
    assert res.trades[0].reason == "open" and res.trades[0].exit_price == D("2.10") * D("0.989")


def test_replay_leader_sell_only_tightens() -> None:
    m = MINTS[2]
    marks = _series(m, [(0, "1.00"), (60, "1.10"), (120, "0.93"), (180, "0.90")])
    buy = _buy(m, T0, "1.00")
    sell = buy.model_copy(update={"side": "sell", "signature": "s1", "ts": T0 + timedelta(minutes=61)})
    without = replay([buy], marks, exit_template(CFG))
    assert without.trades[0].reason == "open"  # 0.90 > stop 0.808, trail never armed
    with_sell = replay([buy, sell], marks, exit_template(CFG))
    assert with_sell.trades[0].reason == "trail" and with_sell.trades[0].exit_ts == T0 + timedelta(
        minutes=120
    )
    early_sell = sell.model_copy(update={"ts": T0 + timedelta(minutes=30)})
    hold = _series(m, [(0, "1.00"), (60, "1.10"), (120, "1.05")])
    assert replay([buy, early_sell], hold, exit_template(CFG)).trades[0].reason == "open"


def test_replay_skips_unpriceable_and_unverified() -> None:
    m = MINTS[3]
    empty = SeriesMarkStore()
    assert replay([_buy(m, T0, "1.0")], empty, exit_template(CFG)).n == 0
    marks = _series(m, [(0, "1.0"), (60, "1.0")])
    unverified = _buy(m, T0, "1.0").model_copy(update={"chain_verified": False})
    assert replay([unverified], marks, exit_template(CFG)).n == 0


def test_replay_deterministic(good: list[LeaderTrade], marks: SeriesMarkStore) -> None:
    a = replay(good, marks, exit_template(CFG), 60, CFG.replay_slip, CFG.replay_fee)
    b = replay(good, marks, exit_template(CFG), 60, CFG.replay_slip, CFG.replay_fee)
    assert a.n == 40 and a.pf == b.pf and a.sortino == b.sortino and a.returns == b.returns
    assert a.weekly_positive_share == b.weekly_positive_share


# ----------------------------------------------------------------------------- scoring


def _candidates(rng: random.Random, marks: SeriesMarkStore) -> dict[str, list[LeaderTrade]]:
    return {
        "fam:a": make_leader(rng, "fam:a", "WalletA", marks),
        "fam:b": make_leader(rng, "fam:b", "WalletB", marks, mints=MINTS[1:13]),
        "fam:c": make_leader(rng, "fam:c", "WalletC", marks, n=10),  # too few signals
    }


def test_score_leaders_ranks_and_qualifies(rng: random.Random) -> None:
    marks = price_paths(rng, drift=0.004)
    cands = _candidates(rng, marks)
    scores = score_leaders(cands, {}, marks, meta_for(MINTS), CFG, NOW, dict.fromkeys(cands, T0))
    by_key = {s.key: s for s in scores}
    assert not by_key["fam:c"].qualified and "too_few_replayed_signals" in by_key["fam:c"].reasons
    assert by_key["fam:c"].score == 0.0
    qualified = [s for s in scores if s.qualified]
    assert qualified, [s.reasons for s in scores]
    assert all(s.replay_pf is not None and s.replay_pf >= 1.3 and s.n_replayed >= 20 for s in qualified)
    assert all(0 < s.score <= 1 for s in qualified)


def test_pf_below_min_disqualifies(rng: random.Random) -> None:
    marks = price_paths(rng, drift=-0.01)
    cands = {"fam:a": make_leader(rng, "fam:a", "WalletA", marks)}
    scores = score_leaders(cands, {}, marks, meta_for(MINTS), CFG, NOW, {"fam:a": T0})
    assert not scores[0].qualified and "replay_pf_below_min" in scores[0].reasons


def test_ranking_ignores_board_pnl_and_winrate(rng: random.Random) -> None:
    marks = price_paths(rng, drift=0.004)
    cands = _candidates(rng, marks)
    details = {k: detail_with_history([(0, "1000", "1000"), (30, "1100", "1000")]) for k in cands}
    base = score_leaders(cands, details, marks, meta_for(MINTS), CFG, NOW, dict.fromkeys(cands, T0))
    for k, d in details.items():
        d.agent.pnl = {"7D": D(-999999) if k == "fam:a" else D(999999), "ALL": D(-1)}
        d.agent.win_rate = D("0.01") if k == "fam:a" else D("0.99")
        d.agent.drawdown = D("-0.9")
        d.agent.trades = 1
    mutated = score_leaders(cands, details, marks, meta_for(MINTS), CFG, NOW, dict.fromkeys(cands, T0))
    assert [s.model_dump() for s in base] == [s.model_dump() for s in mutated]


# ----------------------------------------------------------------------------- clustering


def test_mint_overlap() -> None:
    assert mint_overlap({"a", "b", "c", "d", "e"}, {"a", "b", "c", "d", "z"}) == 0.8
    assert mint_overlap({"a"}, set()) == 0.0
    assert mint_overlap({"a", "b"}, {"a", "b", "c", "d"}) == 1.0


def test_cluster_merges_sybils(rng: random.Random, marks: SeriesMarkStore) -> None:
    trades = {
        "fam:a": make_leader(rng, "fam:a", "A", marks, mints=MINTS[:10]),
        "fam:b": make_leader(rng, "fam:b", "B", marks, mints=[*MINTS[:9], MINTS[12]]),  # 90% overlap with a
        "fam:c": make_leader(
            rng, "fam:c", "C", marks, mints=MINTS[:8] + MINTS[10:12]
        ),  # 80% with a: not > 0.8
        "fam:d": make_leader(rng, "fam:d", "D", marks, mints=MINTS[11:14]),
    }
    scores = [
        LeaderScore(key=k, wallet=k[-1], qualified=True, score=0.5, cluster_id=i)
        for i, k in enumerate(trades)
    ]
    out = cluster(scores, trades, overlap=0.8)
    ids = {s.key: s.cluster_id for s in out}
    assert ids["fam:a"] == ids["fam:b"] == 0
    assert ids["fam:c"] == 2 and ids["fam:d"] == 3
    assert len({s.cluster_id for s in out}) == 3


# ----------------------------------------------------------------------------- sticky pool


def _scores(ranked: list[str], unqualified: list[str] = ()) -> list[LeaderScore]:  # type: ignore[assignment]
    n = len(ranked)
    out = [
        LeaderScore(key=k, wallet=k, qualified=True, score=(n - i) / n, cluster_id=i)
        for i, k in enumerate(ranked)
    ]
    out += [LeaderScore(key=k, wallet=k, qualified=False, score=0.0, cluster_id=99) for k in unqualified]
    return out


def test_select_pool_hysteresis() -> None:
    ranked = [f"L{i}" for i in range(1, 8)]
    assert select_pool(_scores(ranked), set(), n=2) == {"L1", "L2"}
    assert select_pool(_scores(ranked), {"L3"}, n=2) == {"L1", "L2", "L3"}  # rank 3 <= 1.5*2 stays
    assert select_pool(_scores(ranked), {"L4"}, n=2) == {"L1", "L2"}  # rank 4 leaves
    assert select_pool(_scores(ranked, ["Lx"]), {"Lx"}, n=2) == {"L1", "L2"}  # unqualified always leaves


def test_select_pool_top20_in_below30_out() -> None:
    ranked = [f"L{i:02d}" for i in range(1, 41)]
    pool = select_pool(_scores(ranked), {"L30", "L31"}, n=20)
    assert "L30" in pool and "L31" not in pool and len(pool) == 21


# ----------------------------------------------------------------------------- demotion


def test_should_demote_each_reason(good: list[LeaderTrade]) -> None:
    ls = LeaderScore(key="fam:good", wallet=WALLET_A, qualified=True, score=0.9, cluster_id=0)
    meta = meta_for(MINTS)
    recent = good[-6:]
    seen = NOW - timedelta(hours=1)
    assert should_demote(ls, D(1), recent, meta, seen, NOW) is None
    assert should_demote(ls, D(1), recent, meta, NOW - timedelta(hours=48, seconds=1), NOW) == "absent_48h"
    assert should_demote(ls, D(1), recent, meta, NOW - timedelta(hours=48), NOW) is None
    assert should_demote(ls, D("-0.01"), recent, meta, seen, NOW) == "negative_4w_copied_pnl"
    young = dict(meta)
    b = next(t for t in recent if t.side == "buy")
    young[b.mint] = TokenInfo.model_validate(
        {"id": b.mint, "symbol": "y", "firstPoolCreatedAt": b.ts - timedelta(hours=1), "dev": "d"}
    )
    assert should_demote(ls, D(1), recent, young, seen, NOW) == "young_mint_buy"
    assert should_demote(ls, D(1), recent, {}, seen, NOW) == "young_mint_buy"  # unknown age fails closed
    own = dict(meta)
    own[b.mint] = TokenInfo.model_validate(
        {"id": b.mint, "symbol": "o", "firstPoolCreatedAt": T0 - timedelta(days=9), "dev": WALLET_A}
    )
    assert should_demote(ls, D(1), recent, own, seen, NOW) == "own_token"
    assert should_demote(ls, D(1), recent, meta, seen, NOW, platform_dd=0.31) == "drawdown_over_max"
    assert should_demote(ls, D(1), recent, meta, seen, NOW, platform_dd=0.30) is None


def test_exit_rule_model_roundtrip() -> None:
    rule = ExitRule(stop_price=D(1), trail_pct=D("0.25"), trail_from_gain_pct=D("0.30"), time_stop_at=T0)
    assert ExitRule.model_validate_json(rule.model_dump_json()) == rule
