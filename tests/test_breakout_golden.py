"""Stretch: the pure 4h breakout simulator reproduces Study 2 on SOL (skipped without the hourly fixture)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from itertools import pairwise
from pathlib import Path

import numpy as np
import pytest

from tiller.backtest.runner import load_csv_candles
from tiller.models import Candle
from tiller.strategies.breakout_4h import BreakoutParams, breakout_backtest, breakout_series, resample_4h

HOURLY = Path(__file__).parent / "fixtures" / "ohlcv" / "SOLUSDT_1h.csv"
WARM = datetime(2021, 6, 1, tzinfo=UTC)
END = datetime(2026, 9, 23, 23, 59, 59, tzinfo=UTC)

pytestmark = pytest.mark.skipif(not HOURLY.exists(), reason="SOLUSDT_1h.csv fixture not bundled")


@pytest.fixture(scope="module")
def sol_4h() -> list[Candle]:
    bars = [b for b in load_csv_candles(HOURLY, end=END) if b.ts >= WARM]
    return resample_4h(bars)


def test_resample_is_utc_aligned_and_aggregates(sol_4h: list[Candle]) -> None:
    assert all(b.ts.hour % 4 == 0 and b.ts.minute == 0 for b in sol_4h)
    assert all(a.ts < b.ts for a, b in pairwise(sol_4h))
    hourly = [b for b in load_csv_candles(HOURLY, end=END) if b.ts >= WARM]
    first = [b for b in hourly if b.ts < sol_4h[1].ts]
    assert sol_4h[0].open == first[0].open and sol_4h[0].close == first[-1].close
    assert sol_4h[0].high == max(b.high for b in first) and sol_4h[0].low == min(b.low for b in first)
    assert sol_4h[0].volume == sum((b.volume for b in first), Decimal(0))


def test_study2_sol_own_gate_risk1pct_30bps(sol_4h: list[Candle]) -> None:
    res = breakout_backtest(sol_4h, BreakoutParams(), 30, sizing="risk1pct")
    assert abs(res.trades - 90) <= 2
    assert res.pf == pytest.approx(1.52, abs=0.1)
    assert res.cagr == pytest.approx(0.053, abs=0.005)
    assert res.sharpe == pytest.approx(0.59, abs=0.02)
    assert res.max_dd == pytest.approx(-0.119, abs=0.005)
    assert res.last_12m_pf == pytest.approx(0.75, abs=0.05) and res.last_12m_trades == 18
    assert res.exit_reasons == {"ema_slow": 43, "stop": 42, "time": 5}


def test_study2_sol_fixed100_30bps(sol_4h: list[Candle]) -> None:
    res = breakout_backtest(sol_4h, BreakoutParams(), 30, sizing="fixed100")
    assert res.trades == 90 and res.pf == pytest.approx(1.20, abs=0.05)
    assert res.max_dd == pytest.approx(-0.592, abs=0.01)


def test_breakout_signal_is_pure_and_causal(sol_4h: list[Candle]) -> None:
    h = np.array([float(b.high) for b in sol_4h])
    lo = np.array([float(b.low) for b in sol_4h])
    c = np.array([float(b.close) for b in sol_4h])
    v = np.array([float(b.volume) for b in sol_4h])
    full = breakout_series(h, lo, c, v, BreakoutParams())
    cut = 5000
    part = breakout_series(h[:cut], lo[:cut], c[:cut], v[:cut], BreakoutParams())
    np.testing.assert_array_equal(full.entry_signal[:cut], part.entry_signal)
    assert full.entry_signal.sum() > 90  # more raw signals than trades (position is single)
    # gate closed everywhere -> no trades
    res = breakout_backtest(sol_4h, BreakoutParams(), 30, gate=np.zeros(len(sol_4h), dtype=bool))
    assert res.trades == 0 and res.cagr == 0.0
