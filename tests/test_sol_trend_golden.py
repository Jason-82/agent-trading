"""Golden regression: the live exposure functions reproduce Study 1 (RESULTS.md) on the bundled SOL history,
and the stateful Strategy wrappers emit exactly the vectorised exposure when fed bar by bar."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import numpy as np
import pytest

from tiller.backtest.runner import (
    RULE_DONCHIAN,
    RULE_DONCHIAN_VT,
    RULE_SMA200,
    BacktestResult,
    backtest_exposure,
    rule_exposure,
)
from tiller.clock import SimClock
from tiller.models import SOL_MINT, Candle
from tiller.state import SleeveState
from tiller.strategies.base import MarketContext
from tiller.strategies.sol_trend import (
    RegimeParams,
    SolRegimeSwitchStrategy,
    SolTrendEnsembleStrategy,
    SolTrendParams,
    candles_to_arrays,
    ensemble_exposure,
    regime_exposure,
)

WARMUP = 250


def _run(rule: str, sol: list[Candle], cost_bps: int, **kw: Any) -> BacktestResult:
    o, _, _, c = candles_to_arrays(sol)
    e, rt = rule_exposure(rule, sol, None, **kw)
    return backtest_exposure(e, o, c, [b.ts.date() for b in sol], cost_bps, warmup=WARMUP, round_trips=rt)


def _check(res: BacktestResult, exp: dict[str, Any], tol: dict[str, float], vt: bool = False) -> None:
    f = res.full
    assert f.cagr == pytest.approx(exp["cagr"], abs=tol["voltarget25_cagr"] if vt else tol["cagr"])
    assert f.sharpe == pytest.approx(exp["sharpe"], abs=tol["sharpe"])
    assert f.max_dd == pytest.approx(
        exp["max_drawdown"], abs=tol["voltarget25_max_drawdown"] if vt else tol["max_drawdown"]
    )
    assert f.vol == pytest.approx(exp["ann_vol"], abs=tol["ann_vol"])
    assert f.round_trips == exp["round_trips"]
    assert f.time_in_market == pytest.approx(exp["time_in_market"], abs=1e-6)
    assert f.avg_exposure == pytest.approx(exp["avg_exposure"], abs=1e-6)
    assert f.turnover_per_year == pytest.approx(exp["turnover_per_year"], abs=1e-6)
    assert f.n_periods == exp["n_periods"] and f.start == exp["start"] and f.end == exp["end"]
    for year, v in exp["calendar_years"].items():
        assert res.calendar_years[int(year)] == pytest.approx(v, abs=tol["calendar_years"])
    assert res.in_sample.sharpe == pytest.approx(exp["in_sample"]["sharpe"], abs=tol["sharpe"])
    assert res.in_sample.cagr == pytest.approx(exp["in_sample"]["cagr"], abs=tol["cagr"])
    assert res.last_24m.sharpe == pytest.approx(exp["last_24m"]["sharpe"], abs=tol["sharpe"])
    assert res.last_24m.cagr == pytest.approx(exp["last_24m"]["cagr"], abs=tol["cagr"])
    assert res.last_24m.max_dd == pytest.approx(exp["last_24m"]["max_drawdown"], abs=tol["max_drawdown"])


def test_donchian_ensemble_golden(sol_daily: list[Candle], study1_expected: dict[str, Any]) -> None:
    res = _run(RULE_DONCHIAN, sol_daily, 30)
    _check(res, study1_expected["SOLUSDT"]["donchian_ensemble"]["30bps"], study1_expected["tolerances"])
    # headline numbers from RESULTS.md (SOL, 30 bps): CAGR 60.8%, Sharpe 1.20, MaxDD -45.2%, 161 round trips
    assert res.full.cagr == pytest.approx(0.608, abs=0.01)
    assert res.full.sharpe == pytest.approx(1.20, abs=0.02)
    assert res.full.max_dd == pytest.approx(-0.452, abs=0.01)
    assert res.full.round_trips == 161


def test_donchian_voltarget25_golden(sol_daily: list[Candle], study1_expected: dict[str, Any]) -> None:
    res = _run(RULE_DONCHIAN_VT, sol_daily, 30)
    _check(
        res,
        study1_expected["SOLUSDT"]["donchian_ensemble_voltarget25"]["30bps"],
        study1_expected["tolerances"],
        vt=True,
    )
    assert res.full.cagr == pytest.approx(0.148, abs=0.005)
    assert res.full.max_dd == pytest.approx(-0.142, abs=0.005)
    assert res.full.vol == pytest.approx(0.121, abs=0.005)
    assert res.full.round_trips == 161


def test_voltarget25_with_live_cap_040_is_identical(sol_daily: list[Candle]) -> None:
    """The live default cap (0.40 of equity) never binds on the SOL history, so the live parameters
    reproduce the measured rule exactly."""
    _, h, lo, c = candles_to_arrays(sol_daily)
    study = ensemble_exposure(h, lo, c, SolTrendParams(vol_target=0.25, cap=1.0))
    live = ensemble_exposure(h, lo, c, SolTrendParams())
    np.testing.assert_array_equal(study, live)
    assert study.max() < 0.40


def test_sma200_hyst2pct_golden(sol_daily: list[Candle], study1_expected: dict[str, Any]) -> None:
    res = _run(RULE_SMA200, sol_daily, 30)
    _check(res, study1_expected["SOLUSDT"]["sma200_hyst2pct"]["30bps"], study1_expected["tolerances"])
    assert res.full.cagr == pytest.approx(0.579, abs=0.01)
    assert res.full.max_dd == pytest.approx(-0.691, abs=0.01)
    assert res.full.round_trips == 16


@pytest.mark.parametrize("cost", [5, 10])
def test_cost_sensitivity_matches_study(
    sol_daily: list[Candle], study1_expected: dict[str, Any], cost: int
) -> None:
    tol = study1_expected["tolerances"]
    for rule in (RULE_SMA200, RULE_DONCHIAN, RULE_DONCHIAN_VT):
        res = _run(rule, sol_daily, cost)
        exp = study1_expected["SOLUSDT"][rule][f"{cost}bps"]
        assert res.full.cagr == pytest.approx(exp["cagr"], abs=tol["cagr"])
        assert res.full.sharpe == pytest.approx(exp["sharpe"], abs=tol["sharpe"])


def test_btc_sma200_round_trips(btc_daily: list[Candle], study1_expected: dict[str, Any]) -> None:
    _, rt = rule_exposure(RULE_SMA200, btc_daily, None)
    assert rt == 17 == study1_expected["BTCUSDT"]["sma200_hyst2pct"]["5bps"]["round_trips"]
    res = _run(RULE_SMA200, btc_daily, 5)
    assert res.full.cagr == pytest.approx(
        study1_expected["BTCUSDT"]["sma200_hyst2pct"]["5bps"]["cagr"], abs=0.01
    )


def test_btc_confirm_changes_exposure_but_never_forces_exits(
    sol_daily: list[Candle], btc_daily: list[Candle]
) -> None:
    from tiller.strategies.sol_trend import align_closes

    _, h, lo, c = candles_to_arrays(sol_daily)
    btc_c = align_closes(sol_daily, btc_daily)
    base = ensemble_exposure(h, lo, c, SolTrendParams(vol_target=None, cap=1.0))
    gated = ensemble_exposure(
        h, lo, c, SolTrendParams(vol_target=None, cap=1.0, btc_confirm=True), btc_close=btc_c
    )
    assert (gated <= base + 1e-12).all()
    assert gated.sum() < base.sum()
    with pytest.raises(ValueError):
        ensemble_exposure(h, lo, c, SolTrendParams(btc_confirm=True), btc_close=btc_c[:-1])
    # missing BTC data with btc_confirm => fail closed: no entries at all
    assert ensemble_exposure(h, lo, c, SolTrendParams(btc_confirm=True), btc_close=None).sum() == 0.0


# --------------------------------------------------------------------------- strategy wrappers


def _ctx(
    bars: list[Candle], now: datetime, state: dict[str, SleeveState], btc: list[Candle] | None = None
) -> MarketContext:
    candles = {"SOL": bars}
    if btc is not None:
        candles["BTC"] = btc
    return MarketContext(now=now, candles=candles, equity_usd=Decimal(1000), sleeve_state=state)


@pytest.mark.parametrize("btc_confirm", [False, True])
def test_ensemble_wrapper_matches_vectorised_bar_by_bar(
    sol_daily: list[Candle], btc_daily: list[Candle], btc_confirm: bool
) -> None:
    from tiller.strategies.sol_trend import align_closes

    p = SolTrendParams(btc_confirm=btc_confirm)
    strat = SolTrendEnsembleStrategy(p, SOL_MINT)
    start, stop = 300, 700
    bars = sol_daily[:stop]
    _, h, lo, c = candles_to_arrays(bars)
    btc_c = align_closes(bars, btc_daily) if btc_confirm else None
    expected = ensemble_exposure(h, lo, c, p, btc_c)
    state: dict[str, SleeveState] = {}
    clock = SimClock(bars[start].ts + timedelta(days=1, minutes=5))
    n_eval = 0
    for i in range(start, stop):
        clock.set(bars[i].ts + timedelta(days=1, minutes=5))
        # the store delivers every bar it knows; the strategy must ignore the unclosed one
        window = sol_daily[: i + 2]
        targets, new_state = strat.targets(
            _ctx(window, clock.now(), state, btc_daily if btc_confirm else None)
        )
        assert len(targets) == 1 and targets[0].mint == SOL_MINT
        assert float(targets[0].weight) == pytest.approx(expected[i], abs=1e-8), f"bar {i}"
        assert new_state.last_bar_ts == bars[i].ts
        n_eval += 1
        # persist as the engine would (JSON round trip) and simulate a second tick on the same bar
        state = {strat.name: SleeveState.model_validate_json(new_state.model_dump_json())}
        again, same_state = strat.targets(_ctx(window, clock.now() + timedelta(hours=3), state))
        assert same_state == state[strat.name]
        assert again[0].weight == targets[0].weight and "already evaluated" in again[0].reason
    assert n_eval == stop - start
    assert set(state[strat.name].donchian) == {str(n) for n in p.lookbacks}


def test_ensemble_wrapper_rewarms_after_a_missed_day(sol_daily: list[Candle]) -> None:
    p = SolTrendParams()
    strat = SolTrendEnsembleStrategy(p, SOL_MINT)
    _, h, lo, c = candles_to_arrays(sol_daily)
    expected = ensemble_exposure(h, lo, c, p)
    i = 400
    t1, s1 = strat.targets(_ctx(sol_daily[: i + 1], sol_daily[i].ts + timedelta(days=1, minutes=5), {}))
    assert float(t1[0].weight) == pytest.approx(expected[i], abs=1e-8)
    j = i + 3  # agent was down for two days: state is not contiguous -> vectorised re-warm
    t2, s2 = strat.targets(
        _ctx(sol_daily[: j + 1], sol_daily[j].ts + timedelta(days=1, minutes=5), {strat.name: s1})
    )
    assert float(t2[0].weight) == pytest.approx(expected[j], abs=1e-8)
    assert s2.last_bar_ts == sol_daily[j].ts


def test_wrappers_refuse_unclosed_bars_and_short_history(sol_daily: list[Candle]) -> None:
    strat = SolTrendEnsembleStrategy(SolTrendParams(), SOL_MINT)
    bars = sol_daily[:400]
    # 'now' is inside the last bar's day: that bar is not closed, so the previous one is evaluated
    now = bars[-1].ts + timedelta(hours=12)
    targets, state = strat.targets(_ctx(bars, now, {}))
    assert state.last_bar_ts == bars[-2].ts and targets
    targets, state = strat.targets(_ctx(sol_daily[:200], sol_daily[199].ts + timedelta(days=2), {}))
    assert targets == [] and state == SleeveState()
    reg = SolRegimeSwitchStrategy(RegimeParams(), SOL_MINT)
    targets, state = reg.targets(_ctx(sol_daily[:150], sol_daily[149].ts + timedelta(days=2), {}))
    assert targets == []


@pytest.mark.parametrize("btc_confirm", [False, True])
def test_regime_wrapper_matches_vectorised_bar_by_bar(
    sol_daily: list[Candle], btc_daily: list[Candle], btc_confirm: bool
) -> None:
    from tiller.strategies.sol_trend import align_closes

    p = RegimeParams(btc_confirm=btc_confirm)
    strat = SolRegimeSwitchStrategy(p, SOL_MINT)
    start, stop = 300, len(sol_daily)
    _, _, _, c = candles_to_arrays(sol_daily)
    btc_c = align_closes(sol_daily, btc_daily) if btc_confirm else None
    expected = regime_exposure(c, p, btc_c)
    state: dict[str, SleeveState] = {}
    switches = 0
    prev_w = 0.0
    for i in range(start, stop):
        now = sol_daily[i].ts + timedelta(days=1, minutes=5)
        targets, new_state = strat.targets(
            _ctx(sol_daily[: i + 1], now, state, btc_daily if btc_confirm else None)
        )
        w = float(targets[0].weight)
        assert w == pytest.approx(expected[i], abs=1e-12), f"bar {i}"
        assert w in (0.0, 0.25)
        switches += int(w != prev_w)
        prev_w = w
        state = {strat.name: SleeveState.model_validate_json(new_state.model_dump_json())}
        again, _ = strat.targets(_ctx(sol_daily[: i + 1], now + timedelta(hours=2), state))
        assert float(again[0].weight) == w
    assert switches >= 2


def test_regime_wrapper_persists_state_across_restart(sol_daily: list[Candle]) -> None:
    p = RegimeParams()
    strat = SolRegimeSwitchStrategy(p, SOL_MINT)
    _, _, _, c = candles_to_arrays(sol_daily)
    expected = regime_exposure(c, p)
    on_days = [i for i in range(300, len(sol_daily) - 1) if expected[i] > 0 and expected[i + 1] > 0]
    i = on_days[0]
    _, s = strat.targets(_ctx(sol_daily[: i + 1], sol_daily[i].ts + timedelta(days=1), {}))
    assert s.regime_on is True
    restored = {strat.name: SleeveState.model_validate_json(s.model_dump_json())}
    fresh = SolRegimeSwitchStrategy(p, SOL_MINT)  # new process
    t, s2 = fresh.targets(_ctx(sol_daily[: i + 2], sol_daily[i + 1].ts + timedelta(days=1), restored))
    assert s2.regime_on is True and float(t[0].weight) == 0.25
    assert datetime.now(tz=UTC) > s2.last_bar_ts  # sanity: state timestamps are tz-aware UTC
