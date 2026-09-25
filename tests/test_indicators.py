"""Hand-computed indicator values, hysteresis transitions and no-lookahead."""

from __future__ import annotations

import numpy as np
import pytest

from tiller.strategies import indicators as ind

X = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 4.0])


def _nan_equal(a: np.ndarray, b: list[float]) -> None:
    b_arr = np.array(b)
    assert a.shape == b_arr.shape
    for x, y in zip(a, b_arr, strict=True):
        if np.isnan(y):
            assert np.isnan(x)
        else:
            assert x == pytest.approx(y, rel=1e-12)


def test_sma_n3() -> None:
    _nan_equal(ind.sma(X, 3), [np.nan, np.nan, 2.0, 3.0, 4.0, 13 / 3])


def test_ema_n3_adjust_false() -> None:
    # alpha = 0.5: 1, 1.5, 2.25, 3.125, 4.0625, 4.03125
    _nan_equal(ind.ema(X, 3), [1.0, 1.5, 2.25, 3.125, 4.0625, 4.03125])


def test_atr_wilder_n3() -> None:
    high = np.array([2.0, 3.0, 5.0, 4.0])
    low = np.array([1.0, 2.0, 3.0, 2.0])
    close = np.array([1.5, 2.5, 4.0, 3.0])
    # TR: 1, max(1,|3-1.5|=1.5,|2-1.5|=.5)=1.5, max(2,|5-2.5|=2.5,|3-2.5|=.5)=2.5, max(2,|4-4|=0,|2-4|=2)=2
    # ATR alpha=1/3: 1, 1+(1.5-1)/3=1.1666.., 1.16667+(2.5-1.16667)/3=1.61111.., 1.61111+(2-1.61111)/3=1.74074..
    _nan_equal(ind.true_range(high, low, close), [1.0, 1.5, 2.5, 2.0])
    _nan_equal(ind.atr_wilder(high, low, close, 3), [1.0, 7 / 6, 29 / 18, 47 / 27])


def test_donchian_prior_high_and_mid_n3() -> None:
    high = np.array([5.0, 7.0, 6.0, 8.0, 4.0, 9.0])
    low = np.array([1.0, 2.0, 3.0, 2.0, 1.0, 5.0])
    _nan_equal(ind.donchian_prior_high(high, 3), [np.nan, np.nan, np.nan, 7.0, 8.0, 8.0])
    _nan_equal(ind.donchian_mid(high, low, 3), [np.nan, np.nan, 4.0, 5.0, 4.5, 5.0])


def test_realized_vol_matches_manual_std() -> None:
    close = np.array([100.0, 101.0, 99.0, 102.0, 103.0, 101.0])
    lr = np.diff(np.log(close))
    out = ind.realized_vol(close, lookback=3, periods_per_year=365)
    assert np.isnan(out[:3]).all()
    assert out[3] == pytest.approx(np.std(lr[0:3], ddof=1) * np.sqrt(365))
    assert out[5] == pytest.approx(np.std(lr[2:5], ddof=1) * np.sqrt(365))


def test_rolling_median_and_min() -> None:
    _nan_equal(ind.rolling_median(np.array([3.0, 1.0, 2.0, 10.0]), 3), [np.nan, np.nan, 2.0, 2.0])
    _nan_equal(ind.rolling_min(np.array([3.0, 1.0, 2.0, 10.0]), 2), [np.nan, 1.0, 1.0, 2.0])


def test_hysteresis_on_off_hold() -> None:
    ref = np.full(7, 100.0)
    close = np.array([101.0, 102.5, 101.0, 98.5, 97.0, 99.0, 103.0])
    # 101 < 102 hold OFF; 102.5 > 102 ON; 101 hold; 98.5 hold (not < 98); 97 OFF; 99 hold; 103 ON
    _nan_equal(ind.hysteresis_state(close, ref, 0.02), [0, 1, 1, 1, 0, 0, 1])


def test_hysteresis_nan_ref_is_off_and_entry_gate_blocks_only_entries() -> None:
    ref = np.array([np.nan, 100.0, 100.0, 100.0, 100.0])
    close = np.array([200.0, 105.0, 105.0, 90.0, 105.0])
    _nan_equal(ind.hysteresis_state(close, ref, 0.02), [0, 1, 1, 0, 1])
    gate = np.array([1, 0, 0, 0, 1])
    _nan_equal(ind.hysteresis_state(close, ref, 0.02, entry_ok=gate), [0, 0, 0, 0, 1])
    gate2 = np.array([1, 1, 0, 0, 0])
    # entered at bar 1 while gate open; gate closing later never forces an exit
    _nan_equal(ind.hysteresis_state(close, ref, 0.02, entry_ok=gate2), [0, 1, 1, 0, 0])


def test_window_validation() -> None:
    with pytest.raises(ValueError):
        ind.sma(X, 0)
    with pytest.raises(ValueError):
        ind.hysteresis_state(X, X[:-1])


@pytest.mark.parametrize("seed", range(20))
def test_no_lookahead_appending_future_bars_never_changes_past(seed: int) -> None:
    rng = np.random.default_rng(seed)
    n = 120
    close = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.03, n)))
    high = close * (1 + np.abs(rng.normal(0, 0.01, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.01, n)))
    cut = int(rng.integers(40, n - 5))
    funcs = {
        "sma": lambda h, lo, c: ind.sma(c, 10),
        "ema": lambda h, lo, c: ind.ema(c, 10),
        "atr": lambda h, lo, c: ind.atr_wilder(h, lo, c, 14),
        "prior_high": lambda h, lo, c: ind.donchian_prior_high(h, 20),
        "mid": lambda h, lo, c: ind.donchian_mid(h, lo, 20),
        "rv": lambda h, lo, c: ind.realized_vol(c, 30),
        "hyst": lambda h, lo, c: ind.hysteresis_state(c, ind.sma(c, 20), 0.02),
    }
    for name, fn in funcs.items():
        full = fn(high, low, close)[:cut]
        partial = fn(high[:cut], low[:cut], close[:cut])
        np.testing.assert_array_equal(np.isnan(full), np.isnan(partial), err_msg=name)
        np.testing.assert_allclose(np.nan_to_num(full), np.nan_to_num(partial), rtol=1e-12, err_msg=name)
