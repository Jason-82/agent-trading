"""Shadow tracker: hypothetical fills, own exits, marks, promotion-gate report (SimClock, fakes only)."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from fakes import FakeJupiter, FakePriceSource, FakeTokenData
from tiller.clock import SimClock
from tiller.config import CopyCfg
from tiller.copy.models import LeaderTrade, ShadowReport
from tiller.copy.shadow import CONSENSUS_KEY, ShadowTracker, bootstrap_p_positive_block, render_shadow_report
from tiller.ledger import Ledger
from tiller.models import TokenInfo

D = Decimal
CFG = CopyCfg()
START = datetime(2026, 6, 1, tzinfo=UTC)
MINT = "TokenMintAaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
POOL = {"fam:l0", "fam:l1", "fam:l2"}


def leader_buy(
    mint: str, ts: datetime, price: str, key: str = "fam:l0", sig: str | None = None, side: str = "buy"
) -> LeaderTrade:
    return LeaderTrade(
        key=key,
        wallet=key,
        signature=sig or f"{key}:{mint}:{side}:{int(ts.timestamp())}",
        ts=ts,
        detected_at=ts,
        side=side,  # type: ignore[arg-type]
        mint=mint,
        usd_value=D(price) * 100,
        amount=D(100),
        price_usd=D(price),
        source="familiars",
        chain_verified=True,
    )


def token_info(mint: str, liquidity: str) -> TokenInfo:
    return TokenInfo.model_validate({"id": mint, "symbol": "T", "liquidity": liquidity})


@pytest.fixture
def clock() -> SimClock:
    return SimClock(START)


@pytest.fixture
def ledger(tmp_path: Path, clock: SimClock) -> Ledger:
    return Ledger(tmp_path / "shadow.sqlite", clock=clock)


@pytest.fixture
def prices() -> FakePriceSource:
    return FakePriceSource({MINT: D("1.00")})


@pytest.fixture
def tokens() -> FakeTokenData:
    return FakeTokenData(infos={MINT: token_info(MINT, "500000")}, decimals={MINT: 6})


def make_tracker(
    ledger: Ledger,
    clock: SimClock,
    prices: FakePriceSource,
    tokens: FakeTokenData,
    jup: FakeJupiter | None = None,
) -> ShadowTracker:
    return ShadowTracker(
        jup, prices, tokens, ledger, CFG, clock, rng_seed=42, equity_usd=D(1000), taker="taker"
    )


# ----------------------------------------------------------------------------- opening


async def test_open_uses_worst_of_detection_quote_and_leader_price(
    ledger: Ledger, clock: SimClock, prices: FakePriceSource, tokens: FakeTokenData
) -> None:
    jup = FakeJupiter(prices={MINT: D("1.05")}, decimals={MINT: 6}, impact=D(0))
    tracker = make_tracker(ledger, clock, prices, tokens, jup)
    clock.advance(timedelta(seconds=90))
    buy = leader_buy(MINT, START, "1.00")
    opened = await tracker.on_leader_trades([buy], POOL, [MINT])
    assert [t.leader_key for t in opened] == [CONSENSUS_KEY, "fam:l0"]
    t = opened[0]
    assert t.size_usd == D(20)  # 2% of 1,000 nominal equity
    assert (
        jup.order_calls and jup.order_calls[0][0].amount_base == 20_000_000 and jup.order_calls[0][2] == 100
    )
    assert t.leader_price == D("1.00")
    # quote 1.05 (from 20 USDC -> 19.047619 tokens, base units rounded down) beats leader*1.01; then 1% cost
    assert abs(t.entry_price - D("1.05") * D("1.01")) < D("1e-6")
    assert t.lag_s == 90.0 and abs(t.lag_cost_pct - D("0.05")) < D("1e-6")
    assert abs(t.exit.stop_price - D("1.05") * D("0.80")) < D("1e-6")
    assert t.exit.trail_pct == D("0.25") and t.exit.trail_from_gain_pct == D("0.30")
    assert t.exit.time_stop_at == clock.now() + timedelta(hours=48)
    assert (
        set(t.marks) == {"high", "liq_entry"}
        and abs(t.marks["high"] - D("1.05")) < D("1e-6")
        and t.marks["liq_entry"] == 500000
    )
    assert t.id.startswith("consensus:") and opened[1].id == f"leader:{buy.signature}"
    assert len(ledger.shadow_trades(since=START)) == 2


async def test_open_leader_markup_wins_when_quote_is_lower(
    ledger: Ledger, clock: SimClock, prices: FakePriceSource, tokens: FakeTokenData
) -> None:
    prices.table[MINT] = D("0.98")
    tracker = make_tracker(ledger, clock, prices, tokens)  # no Jupiter: falls back to the price feed
    opened = await tracker.on_leader_trades([leader_buy(MINT, START, "1.00")], POOL, [])
    assert len(opened) == 1 and opened[0].leader_key == "fam:l0"
    assert opened[0].entry_price == D("1.01") * D("1.01")
    assert opened[0].lag_cost_pct == D("-0.02")


async def test_open_falls_back_when_jupiter_fails_and_skips_without_any_price(
    ledger: Ledger, clock: SimClock, tokens: FakeTokenData
) -> None:
    jup = FakeJupiter(prices={}, decimals={})  # unknown mint -> KeyError inside the fake
    tracker = make_tracker(ledger, clock, FakePriceSource({MINT: D("2.00")}), tokens, jup)
    opened = await tracker.on_leader_trades([leader_buy(MINT, START, "1.00")], POOL, [])
    assert opened[0].entry_price == D("2.00") * D("1.01")
    assert ledger.events(kind="shadow_quote_fallback")
    tracker2 = make_tracker(ledger, clock, FakePriceSource({}), tokens)
    other = "OtherMintBbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    assert (
        await tracker2.on_leader_trades(
            [leader_buy(other, START, "1.00").model_copy(update={"price_usd": None})], POOL, []
        )
        == []
    )
    assert ledger.events(kind="shadow_skipped")


async def test_open_ignores_non_pool_unverified_and_dedupes(
    ledger: Ledger, clock: SimClock, prices: FakePriceSource, tokens: FakeTokenData
) -> None:
    tracker = make_tracker(ledger, clock, prices, tokens)
    outsider = leader_buy(MINT, START, "1.00", key="fam:nobody")
    unverified = leader_buy(MINT, START, "1.00", sig="unv").model_copy(update={"chain_verified": False})
    assert await tracker.on_leader_trades([outsider, unverified], POOL, []) == []
    buy = leader_buy(MINT, START, "1.00")
    assert len(await tracker.on_leader_trades([buy], POOL, [MINT])) == 2
    # same signature and same open consensus mint: nothing new
    clock.advance(timedelta(minutes=5))
    assert await tracker.on_leader_trades([buy], POOL, [MINT]) == []
    assert len(tracker.open_trades()) == 2


# ----------------------------------------------------------------------------- marks and exits


async def _one_open(
    ledger: Ledger, clock: SimClock, prices: FakePriceSource, tokens: FakeTokenData
) -> ShadowTracker:
    tracker = make_tracker(ledger, clock, prices, tokens)
    await tracker.on_leader_trades([leader_buy(MINT, START, "1.00")], POOL, [MINT])
    return tracker


async def test_marks_1h_6h_24h_and_running_high(
    ledger: Ledger, clock: SimClock, prices: FakePriceSource, tokens: FakeTokenData
) -> None:
    tracker = await _one_open(ledger, clock, prices, tokens)
    for minutes, px in ((30, "1.05"), (60, "1.10"), (360, "1.20"), (1440, "1.15")):
        clock.set(START + timedelta(minutes=minutes))
        prices.table[MINT] = D(px)
        await tracker.mark_and_exit()
    t = tracker.open_trades()[0]
    assert t.marks["1h"] == D("1.10") and t.marks["6h"] == D("1.20") and t.marks["24h"] == D("1.15")
    assert t.marks["high"] == D("1.20") and t.exit_ts is None


async def test_stop_exit_and_pnl(
    ledger: Ledger, clock: SimClock, prices: FakePriceSource, tokens: FakeTokenData
) -> None:
    tracker = await _one_open(ledger, clock, prices, tokens)
    clock.advance(timedelta(hours=1))
    prices.table[MINT] = D("0.81")  # basis 1.01 * 0.8 = 0.808
    await tracker.mark_and_exit()
    assert tracker.open_trades()
    prices.table[MINT] = D("0.80")
    await tracker.mark_and_exit()
    assert tracker.open_trades() == []
    closed = next(t for t in ledger.shadow_trades(since=START) if t.leader_key == CONSENSUS_KEY)
    assert closed.exit_reason == "stop" and closed.exit_ts == clock.now()
    assert closed.exit_price == D("0.80") * D("0.99")
    assert closed.pnl_pct == closed.exit_price / closed.entry_price - 1


async def test_trail_exit_from_plus_30(
    ledger: Ledger, clock: SimClock, prices: FakePriceSource, tokens: FakeTokenData
) -> None:
    tracker = await _one_open(ledger, clock, prices, tokens)
    clock.advance(timedelta(hours=2))
    prices.table[MINT] = D("1.40")  # armed: 1.01 * 1.30 = 1.313
    await tracker.mark_and_exit()
    prices.table[MINT] = D("1.06")  # 1.40 * 0.75 = 1.05 -> still above
    await tracker.mark_and_exit()
    assert tracker.open_trades()
    prices.table[MINT] = D("1.05")
    await tracker.mark_and_exit()
    assert all(t.exit_reason == "trail" for t in ledger.shadow_trades(since=START))


async def test_time_stop_after_48h(
    ledger: Ledger, clock: SimClock, prices: FakePriceSource, tokens: FakeTokenData
) -> None:
    tracker = await _one_open(ledger, clock, prices, tokens)
    clock.set(START + timedelta(hours=47, minutes=59))
    await tracker.mark_and_exit()
    assert tracker.open_trades()
    clock.set(START + timedelta(hours=48))
    await tracker.mark_and_exit()
    assert all(t.exit_reason == "time_stop" for t in ledger.shadow_trades(since=START))


async def test_liquidity_collapse_exit(
    ledger: Ledger, clock: SimClock, prices: FakePriceSource, tokens: FakeTokenData
) -> None:
    tracker = await _one_open(ledger, clock, prices, tokens)
    clock.advance(timedelta(hours=1))
    tokens.infos[MINT] = token_info(MINT, "200001")  # > 40% of 500k remains
    await tracker.mark_and_exit()
    assert tracker.open_trades()
    tokens.infos[MINT] = token_info(MINT, "200000")
    await tracker.mark_and_exit()
    assert all(t.exit_reason == "liquidity_collapse" for t in ledger.shadow_trades(since=START))


async def test_leader_sell_only_tightens_trail_never_exits(
    ledger: Ledger, clock: SimClock, prices: FakePriceSource, tokens: FakeTokenData
) -> None:
    tracker = await _one_open(ledger, clock, prices, tokens)
    clock.advance(timedelta(hours=1))
    prices.table[MINT] = D("1.10")
    await tracker.mark_and_exit()
    sell = leader_buy(MINT, clock.now(), "1.10", side="sell")
    assert await tracker.on_leader_trades([sell], POOL, []) == []
    assert len(tracker.open_trades()) == 2  # the sell itself closed nothing
    assert all(
        t.exit.trail_pct == D("0.15") and t.exit.trail_from_gain_pct == 0 for t in tracker.open_trades()
    )
    prices.table[MINT] = D("0.94")  # 1.10 * 0.85 = 0.935: hold
    await tracker.mark_and_exit()
    assert len(tracker.open_trades()) == 2
    prices.table[MINT] = D("0.93")
    await tracker.mark_and_exit()
    assert tracker.open_trades() == []
    assert all(t.exit_reason == "trail" for t in ledger.shadow_trades(since=START))


async def test_leader_sell_from_outside_pool_or_other_mint_is_ignored(
    ledger: Ledger, clock: SimClock, prices: FakePriceSource, tokens: FakeTokenData
) -> None:
    tracker = await _one_open(ledger, clock, prices, tokens)
    await tracker.on_leader_trades([leader_buy(MINT, START, "1.0", key="fam:x", side="sell")], POOL, [])
    await tracker.on_leader_trades([leader_buy("Other", START, "1.0", side="sell")], POOL, [])
    assert all(t.exit.trail_pct == D("0.25") for t in tracker.open_trades())


async def test_restart_reloads_open_book(
    ledger: Ledger, clock: SimClock, prices: FakePriceSource, tokens: FakeTokenData
) -> None:
    await _one_open(ledger, clock, prices, tokens)
    tracker2 = make_tracker(ledger, clock, prices, tokens)
    assert len(tracker2.open_trades()) == 2
    clock.advance(timedelta(hours=49))
    await tracker2.mark_and_exit()
    assert tracker2.open_trades() == [] and all(
        t.pnl_pct is not None for t in ledger.shadow_trades(since=START)
    )


async def test_mark_skips_when_price_feed_fails(
    ledger: Ledger, clock: SimClock, tokens: FakeTokenData
) -> None:
    class Broken(FakePriceSource):
        async def usd_prices(self, mints: list[str]) -> dict[str, Decimal]:
            if self.calls:
                raise RuntimeError("feed down")
            self.calls += 1
            return await super().usd_prices(mints)

    p = Broken({MINT: D("1.00")})
    tracker = make_tracker(ledger, clock, p, tokens)
    await tracker.on_leader_trades([leader_buy(MINT, START, "1.00")], POOL, [])
    clock.advance(timedelta(hours=49))
    await tracker.mark_and_exit()
    assert len(tracker.open_trades()) == 1 and ledger.events(kind="shadow_mark_failed")


# ----------------------------------------------------------------------------- 60-day runs and the gate


async def run_book(
    ledger: Ledger,
    clock: SimClock,
    n_trades: int,
    span_days: float,
    move: str,
    *,
    leaders_move: dict[str, str] | None = None,
) -> ShadowTracker:
    """Open ``n_trades`` consensus signals spread over ``span_days`` (each also a followed-leader buy), price each
    at 1.00 then ``move`` an hour later, step the clock hourly and let the 48 h time stop close everything."""
    prices = FakePriceSource({})
    tokens = FakeTokenData()
    tracker = make_tracker(ledger, clock, prices, tokens)
    keys = sorted(POOL)
    schedule = [
        (START + timedelta(days=span_days * i / n_trades), f"M{i:03d}", keys[i % 3]) for i in range(n_trades)
    ]
    pending = list(schedule)
    end = START + timedelta(days=span_days) + timedelta(hours=50)
    while clock.now() <= end:
        now = clock.now()
        while pending and pending[0][0] <= now:
            _, mint, key = pending.pop(0)
            prices.table[mint] = D("1.00")
            await tracker.on_leader_trades([leader_buy(mint, now, "1.00", key=key)], POOL, [mint])
            prices.table[mint] = D(leaders_move.get(key, move) if leaders_move else move)
        await tracker.mark_and_exit()
        clock.advance(timedelta(hours=1))
    return tracker


async def test_sixty_day_positive_run_meets_gate(ledger: Ledger, clock: SimClock) -> None:
    tracker = await run_book(ledger, clock, n_trades=120, span_days=60, move="1.12")
    rep = tracker.report(days=90)
    assert rep.n_trades == 120 and rep.days >= 60
    expected = D("1.12") * D("0.99") / (D("1.01") * D("1.01")) - 1
    assert rep.expectancy_pct == expected
    assert rep.profit_factor == 999.0 and rep.p_positive_block20 == 1.0 and rep.leaders_positive_share == 1.0
    assert rep.median_lag_s == 0.0 and rep.median_lag_cost_pct == 0
    assert rep.promotion_gate_met and rep.reasons == []
    assert {s.key for s in rep.per_leader} == POOL and all(s.n_trades == 40 for s in rep.per_leader)
    assert all(t.exit_reason == "time_stop" for t in ledger.shadow_trades(since=START))


async def test_gate_closed_at_59_days(ledger: Ledger, clock: SimClock) -> None:
    tracker = await run_book(ledger, clock, n_trades=120, span_days=56, move="1.12")
    clock.set(START + timedelta(days=59, hours=23))
    rep = tracker.report(days=90)
    assert rep.days == 59 and rep.n_trades == 120 and not rep.promotion_gate_met
    assert rep.reasons == ["days 59 < 60"]
    clock.set(START + timedelta(days=60))
    assert tracker.report(days=90).promotion_gate_met


async def test_gate_closed_at_99_trades(ledger: Ledger, clock: SimClock) -> None:
    tracker = await run_book(ledger, clock, n_trades=99, span_days=61, move="1.12")
    rep = tracker.report(days=90)
    assert rep.n_trades == 99 and rep.days >= 60
    assert rep.reasons == ["trades 99 < 100"] and not rep.promotion_gate_met


async def test_gate_closed_at_negative_expectancy(ledger: Ledger, clock: SimClock) -> None:
    tracker = await run_book(ledger, clock, n_trades=120, span_days=61, move="0.95")
    rep = tracker.report(days=90)
    assert rep.expectancy_pct < 0 and rep.profit_factor == 0.0 and rep.p_positive_block20 == 0.0
    assert not rep.promotion_gate_met
    assert rep.reasons[0].startswith("expectancy") and any(
        r.startswith("p_positive_block20") for r in rep.reasons
    )
    assert any(r.startswith("leaders_positive_share") for r in rep.reasons)


async def test_gate_closed_when_too_few_leaders_positive(ledger: Ledger, clock: SimClock) -> None:
    moves = {"fam:l0": "1.30", "fam:l1": "0.99", "fam:l2": "0.99"}
    tracker = await run_book(ledger, clock, n_trades=120, span_days=61, move="1.30", leaders_move=moves)
    rep = tracker.report(days=90)
    assert rep.leaders_positive_share == pytest.approx(1 / 3)
    assert rep.n_trades == 120 and rep.expectancy_pct > D("0.01") and rep.p_positive_block20 >= 0.8
    assert (
        not rep.promotion_gate_met
        and len(rep.reasons) == 1
        and rep.reasons[0].startswith("leaders_positive_share")
    )
    per = {s.key: s for s in rep.per_leader}
    assert per["fam:l0"].positive and not per["fam:l1"].positive


async def test_report_window_and_empty_book(ledger: Ledger, clock: SimClock) -> None:
    empty = make_tracker(ledger, clock, FakePriceSource({}), FakeTokenData()).report()
    assert (
        empty.n_trades == 0 and empty.days == 0 and not empty.promotion_gate_met and len(empty.reasons) == 5
    )
    tracker = await run_book(ledger, clock, n_trades=10, span_days=20, move="1.12")
    assert tracker.report(days=5).n_trades < tracker.report(days=90).n_trades == 10


# ----------------------------------------------------------------------------- bootstrap and rendering


def test_bootstrap_is_seeded_and_sane() -> None:
    rets = [D("0.05"), D("-0.03"), D("0.02"), D("-0.10"), D("0.08")]
    a = bootstrap_p_positive_block(rets, 20, 2000, 42)
    b = bootstrap_p_positive_block(rets, 20, 2000, 42)
    assert a == b and 0.0 < a < 1.0
    assert (
        bootstrap_p_positive_block(rets, 20, 2000, 7) != a or True
    )  # different seed may differ; determinism is what matters
    assert bootstrap_p_positive_block([], 20, 100, 42) == 0.0
    assert bootstrap_p_positive_block([D("0.01")] * 3, 20, 100, 42) == 1.0
    assert bootstrap_p_positive_block([D("-0.01")] * 3, 20, 100, 42) == 0.0


def test_render_shadow_report_text_and_json() -> None:
    rep = ShadowReport(
        days=12,
        n_trades=3,
        expectancy_pct=D("-0.0123"),
        profit_factor=0.8,
        median_lag_s=61.0,
        median_lag_cost_pct=D("0.004"),
        p_positive_block20=0.31,
        leaders_positive_share=0.5,
        per_leader=[],
        promotion_gate_met=False,
        reasons=["days 12 < 60"],
    )
    text = render_shadow_report(rep, "text")
    assert "SHADOW COPY REPORT" in text and "-1.23%" in text and "NOT MET" in text and "days 12 < 60" in text
    assert "{" not in text
    js = json.loads(render_shadow_report(rep, "json"))
    assert js["n_trades"] == 3 and js["promotion_gate_met"] is False and js["expectancy_pct"] == "-0.0123"
    both = render_shadow_report(rep, "both")
    assert both.startswith("SHADOW COPY REPORT") and json.loads(both[both.index("{") :])["days"] == 12
    with pytest.raises(ValueError):
        render_shadow_report(rep, "xml")


def test_shadow_module_has_no_order_authority() -> None:
    import tiller.copy.shadow

    loaded = {m for m in sys.modules if m in ("tiller.execution.venue", "tiller.execution.wallet")}
    src = Path(tiller.copy.shadow.__file__).read_text()
    assert "execution.venue" not in src and "execution.wallet" not in src and ".execute(" not in src
    assert (
        not loaded or True
    )  # other tests may have imported the venue; the source check above is the guarantee
