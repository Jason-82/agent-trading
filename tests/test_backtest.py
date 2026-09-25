"""Backtester conventions, buy-and-hold golden numbers, band variant, combined rule, grid and report."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from tiller.backtest.__main__ import main as backtest_main
from tiller.backtest.runner import (
    RULE_BUY_AND_HOLD,
    RULE_COMBINED,
    RULE_DONCHIAN_VT,
    RULE_SMA200,
    backtest_exposure,
    btc_confirm_decision,
    grid_key,
    load_csv_candles,
    render_report,
    rule_exposure,
    run_grid,
    simulate_returns,
)
from tiller.models import Candle
from tiller.strategies.sol_trend import candles_to_arrays

FIXTURES = Path(__file__).parent / "fixtures"


def test_csv_loader_row_count_and_order(sol_daily: list[Candle]) -> None:
    with (FIXTURES / "ohlcv" / "SOLUSDT_1d.csv").open() as f:
        n_rows = sum(1 for _ in f) - 1
    assert len(sol_daily) == n_rows == 2235
    assert sol_daily[0].ts.isoformat() == "2020-08-11T00:00:00+00:00"
    assert sol_daily[-1].ts.date() == date(2026, 9, 23)
    assert all(a.ts < b.ts for a, b in pairwise(sol_daily))


def test_buy_and_hold_golden(sol_daily: list[Candle], study1_expected: dict[str, Any]) -> None:
    o, _, _, c = candles_to_arrays(sol_daily)
    e, rt = rule_exposure(RULE_BUY_AND_HOLD, sol_daily, None)
    res = backtest_exposure(e, o, c, [b.ts.date() for b in sol_daily], 30, round_trips=rt)
    exp = study1_expected["SOLUSDT"]["buy_and_hold"]["30bps"]
    assert res.full.cagr == pytest.approx(0.262, abs=0.01)
    assert res.full.max_dd == pytest.approx(-0.963, abs=0.01)
    assert res.full.cagr == pytest.approx(exp["cagr"], abs=1e-6)
    assert res.full.round_trips == 1
    assert len(res.equity) == len(res.dates) == res.full.n_periods


def test_fill_at_next_open_and_cost_on_turnover() -> None:
    """Hand-checkable four-bar example (warmup 0): exposure decided at close t, filled at open t+1."""
    opens = np.array([10.0, 11.0, 12.0, 12.0])
    closes = np.array([10.5, 11.5, 12.5, 11.0])
    dates = [date(2026, 1, d) for d in range(1, 5)]
    e = np.array([1.0, 1.0, 0.0, 0.0])
    res = backtest_exposure(e, opens, closes, dates, cost_bps=100, warmup=0)
    # px = [11, 12, 12, 11.0(last close)]; period 0: 1*(12/11-1) - 1%*1; period 1: 1*(12/12-1) - 0; period 2: 0 - 1%*1
    expected = [(12 / 11 - 1) - 0.01, 0.0, -0.01]
    eq = np.cumprod(1 + np.array(expected))
    np.testing.assert_allclose(res.equity, eq)
    assert res.dates == ["2026-01-02", "2026-01-03", "2026-01-04"]
    assert res.full.round_trips == 1
    assert res.full.turnover_per_year == pytest.approx(2.0 / (3 / 365))
    assert res.full.time_in_market == pytest.approx(2 / 3)


def test_warmup_zeroes_exposure_and_charges_entry_inside_window() -> None:
    opens = np.full(8, 10.0)
    closes = np.full(8, 10.0)
    dates = [date(2026, 1, d) for d in range(1, 9)]
    res = backtest_exposure(np.ones(8), opens, closes, dates, cost_bps=100, warmup=3)
    # evaluation periods: t=3..6 (4 periods); entry at t=3 costs 1%, flat prices otherwise
    assert res.full.n_periods == 4
    assert res.equity[0] == pytest.approx(0.99) and res.equity[-1] == pytest.approx(0.99)
    with pytest.raises(ValueError):
        backtest_exposure(np.ones(4), opens[:4], closes[:4], dates[:4], 10, warmup=3)


def test_band_suppresses_small_trades_and_lowers_turnover(sol_daily: list[Candle]) -> None:
    o, _, _, c = candles_to_arrays(sol_daily)
    dates = [b.ts.date() for b in sol_daily]
    e, rt = rule_exposure(RULE_DONCHIAN_VT, sol_daily, None)
    no_band = backtest_exposure(e, o, c, dates, 30, round_trips=rt)
    band = backtest_exposure(e, o, c, dates, 30, band=0.02, round_trips=rt)
    assert band.full.turnover_per_year < no_band.full.turnover_per_year
    assert abs(band.full.cagr - no_band.full.cagr) < 0.03  # same rule, slightly different path
    # simulate_returns: a target change below the band is ignored, above it is traded
    px = np.array([100.0, 100.0, 100.0, 100.0])
    _, held, to = simulate_returns(np.array([0.10, 0.11, 0.15, 0.15]), px, 0.0, band=0.02)
    np.testing.assert_allclose(held, [0.10, 0.10, 0.15])
    np.testing.assert_allclose(to, [0.10, 0.0, 0.05, 0.0])
    # drift: after a +10% move the held weight rises; a 0.02 band ignores the 0.9pp drift on a 10% weight
    px2 = np.array([100.0, 110.0, 110.0])
    _, held2, to2 = simulate_returns(np.array([0.10, 0.10, 0.10]), px2, 0.0, band=0.02)
    assert held2[1] == pytest.approx(0.11 / 1.01) and to2[1] == 0.0


def test_combined_default_is_capped_and_between_components(sol_daily: list[Candle]) -> None:
    e_c, rt = rule_exposure(RULE_COMBINED, sol_daily, None)
    _, _, _, c = candles_to_arrays(sol_daily)
    from tiller.strategies.indicators import realized_vol
    from tiller.strategies.sol_trend import RegimeParams, SolTrendParams, ensemble_exposure, regime_exposure

    _, h, lo, _ = candles_to_arrays(sol_daily)
    a_live = ensemble_exposure(h, lo, c, SolTrendParams())
    b_live = regime_exposure(c, RegimeParams())
    assert (e_c <= 0.5 + 1e-12).all()
    assert (e_c <= a_live + b_live + 1e-12).all()
    assert (e_c >= np.minimum(a_live + b_live, 0.0)).all()
    rv = realized_vol(c, 90)
    cap = np.minimum(0.5, 0.3 / np.maximum(rv, 0.05))
    binding = (a_live + b_live > cap + 1e-12) & ~np.isnan(rv)
    assert binding.any()  # the beta cap binds on some days
    np.testing.assert_allclose(e_c[binding], cap[binding])
    assert rt > 0
    o, _, _, _ = candles_to_arrays(sol_daily)
    res = backtest_exposure(e_c, o, c, [b.ts.date() for b in sol_daily], 30, round_trips=rt)
    assert -0.30 < res.full.max_dd < 0.0
    assert res.full.avg_exposure < 0.5


def test_run_grid_and_report(sol_daily: list[Candle], btc_daily: list[Candle]) -> None:
    results = run_grid(sol_daily, btc_daily, cost_bps_list=(30,), bands=(None, 0.02))
    assert grid_key(RULE_COMBINED, 30, True, 0.02) in results
    assert grid_key(RULE_BUY_AND_HOLD, 30, True) not in results
    assert len(results) == (1 + 4 * 2) * 2
    report = render_report(results)
    for rule in (RULE_BUY_AND_HOLD, RULE_SMA200, RULE_DONCHIAN_VT, RULE_COMBINED):
        assert rule in report
    assert "## Calendar-year returns" in report and "2022" in report
    assert "24m Sharpe" in report and "btc_confirm default =" in report and "Rebalance band" in report
    ok, notes = btc_confirm_decision(results, 30)
    assert isinstance(ok, bool) and len(notes) == 2
    assert results[grid_key(RULE_SMA200, 30, False, None)].full.round_trips == 16


def test_module_entry_writes_report(tmp_path: Path) -> None:
    out = tmp_path / "BACKTEST.md"
    rc = backtest_main(
        ["--csv-dir", str(FIXTURES / "ohlcv"), "--cost-bps", "30", "--out", str(out), "--no-band"]
    )
    assert rc == 0 and out.exists()
    text = out.read_text()
    assert "# Tiller backtest report" in text and "combined_default" in text


def test_load_csv_end_filter(tmp_path: Path) -> None:
    p = tmp_path / "x.csv"
    p.write_text(
        "timestamp,open,high,low,close,volume\n1700000000,1,2,0.5,1.5,10\n1700086400,1.5,2,1,1.2,5\n"
    )
    bars = load_csv_candles(p)
    assert len(bars) == 2 and bars[1].close == Decimal("1.2")
    from datetime import UTC, datetime

    assert len(load_csv_candles(p, end=datetime.fromtimestamp(1700000000, tz=UTC))) == 1
