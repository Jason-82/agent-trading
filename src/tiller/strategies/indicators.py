"""Pure numpy indicators with the offline-study conventions.

Every function is causal: output[t] depends only on inputs[0..t]. Positions that lack a
full window are ``nan``. Conventions match ``scratchpad/analysis`` (the offline studies):

* ``sma``/rolling max/min/std use a window of exactly ``n`` bars ending at ``t``;
* ``ema`` is ``span=n, adjust=False`` (alpha = 2/(n+1)), seeded with the first value;
* ``atr_wilder`` is the true range smoothed with ``alpha = 1/n`` (Wilder), first TR = high-low;
* ``donchian_prior_high`` uses bars ``t-n..t-1`` (excludes the current bar);
* ``donchian_mid`` uses bars ``t-n+1..t`` (includes the current bar);
* ``realized_vol`` is the ``ddof=1`` standard deviation of daily log returns times
  ``sqrt(periods_per_year)``, i.e. an annualised fraction (0.80 = 80% vol).

No pandas here: these run on the live hot path.
"""

from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view


def _as_f64(x: np.ndarray) -> np.ndarray:
    a = np.asarray(x, dtype=np.float64)
    if a.ndim != 1:
        raise ValueError("indicators expect 1-D arrays")
    return a


def _check_n(n: int) -> None:
    if n < 1:
        raise ValueError("window must be >= 1")


def _rolling(x: np.ndarray, n: int, fn: str) -> np.ndarray:
    """Apply ``fn`` over trailing windows of length ``n``; nan where the window is incomplete."""
    out = np.full(x.shape[0], np.nan)
    if x.shape[0] < n:
        return out
    w = sliding_window_view(x, n)
    if fn == "mean":
        out[n - 1 :] = w.mean(axis=1)
    elif fn == "max":
        out[n - 1 :] = w.max(axis=1)
    elif fn == "min":
        out[n - 1 :] = w.min(axis=1)
    elif fn == "std":
        out[n - 1 :] = w.std(axis=1, ddof=1) if n > 1 else np.nan
    elif fn == "median":
        out[n - 1 :] = np.median(w, axis=1)
    else:  # pragma: no cover
        raise ValueError(fn)
    return out


def sma(x: np.ndarray, n: int) -> np.ndarray:
    """Simple moving average over the last ``n`` bars (nan for the first ``n-1``)."""
    _check_n(n)
    return _rolling(_as_f64(x), n, "mean")


def rolling_max(x: np.ndarray, n: int) -> np.ndarray:
    """Max over bars ``t-n+1..t``."""
    _check_n(n)
    return _rolling(_as_f64(x), n, "max")


def rolling_min(x: np.ndarray, n: int) -> np.ndarray:
    """Min over bars ``t-n+1..t``."""
    _check_n(n)
    return _rolling(_as_f64(x), n, "min")


def rolling_median(x: np.ndarray, n: int) -> np.ndarray:
    """Median over bars ``t-n+1..t``."""
    _check_n(n)
    return _rolling(_as_f64(x), n, "median")


def ema(x: np.ndarray, n: int) -> np.ndarray:
    """Exponential moving average, span ``n``, ``adjust=False`` (pandas semantics)."""
    _check_n(n)
    a = _as_f64(x)
    out = np.empty_like(a)
    if a.shape[0] == 0:
        return out
    alpha = 2.0 / (n + 1.0)
    out[0] = a[0]
    for i in range(1, a.shape[0]):
        out[i] = alpha * a[i] + (1.0 - alpha) * out[i - 1]
    return out


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """True range; the first bar uses high-low (no previous close)."""
    h, lo, c = _as_f64(high), _as_f64(low), _as_f64(close)
    tr = h - lo
    if h.shape[0] > 1:
        pc = c[:-1]
        tr[1:] = np.maximum.reduce([h[1:] - lo[1:], np.abs(h[1:] - pc), np.abs(lo[1:] - pc)])
    return np.asarray(tr, dtype=np.float64)


def atr_wilder(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int) -> np.ndarray:
    """Wilder ATR: true range smoothed with alpha = 1/n, adjust=False."""
    _check_n(n)
    tr = true_range(high, low, close)
    out = np.empty_like(tr)
    if tr.shape[0] == 0:
        return out
    alpha = 1.0 / n
    out[0] = tr[0]
    for i in range(1, tr.shape[0]):
        out[i] = alpha * tr[i] + (1.0 - alpha) * out[i - 1]
    return out


def donchian_prior_high(high: np.ndarray, n: int) -> np.ndarray:
    """``max(high[t-n..t-1])``: the prior-``n`` high excluding bar ``t``; nan for ``t < n``."""
    _check_n(n)
    h = _as_f64(high)
    out = np.full(h.shape[0], np.nan)
    if h.shape[0] > n:
        out[n:] = rolling_max(h, n)[n - 1 : -1]
    return out


def donchian_mid(high: np.ndarray, low: np.ndarray, n: int) -> np.ndarray:
    """``(max(high[t-n+1..t]) + min(low[t-n+1..t])) / 2``; nan for ``t < n-1``."""
    _check_n(n)
    return np.asarray((rolling_max(high, n) + rolling_min(low, n)) / 2.0, dtype=np.float64)


def log_returns(close: np.ndarray) -> np.ndarray:
    """``log(close[t]/close[t-1])`` with nan at index 0 (same length as ``close``)."""
    c = _as_f64(close)
    out = np.full(c.shape[0], np.nan)
    if c.shape[0] > 1:
        out[1:] = np.diff(np.log(c))
    return out


def realized_vol(close: np.ndarray, lookback: int = 90, periods_per_year: int = 365) -> np.ndarray:
    """Annualised realised volatility (fraction): std(ddof=1) of the last ``lookback`` log returns.

    Valid from index ``lookback`` onwards (the first log return is undefined).
    """
    _check_n(lookback)
    lr = log_returns(close)
    out = np.full(lr.shape[0], np.nan)
    if lr.shape[0] > lookback:
        out[lookback:] = _rolling(lr[1:], lookback, "std")[lookback - 1 :]
    return np.asarray(out * np.sqrt(float(periods_per_year)), dtype=np.float64)


def hysteresis_state(
    close: np.ndarray,
    ref: np.ndarray,
    hyst: float = 0.02,
    entry_ok: np.ndarray | None = None,
    initial_state: int = 0,
) -> np.ndarray:
    """Two-threshold state machine: OFF->ON when ``close > ref*(1+hyst)``, ON->OFF when
    ``close < ref*(1-hyst)``, otherwise hold. A nan ``ref`` forces OFF. ``entry_ok`` (0/1 per
    bar) optionally gates NEW entries only (exits are never gated). Returns 0/1 floats.
    """
    c, r = _as_f64(close), _as_f64(ref)
    if c.shape != r.shape:
        raise ValueError("close and ref must have the same shape")
    gate = None if entry_ok is None else np.asarray(entry_ok, dtype=np.float64)
    if gate is not None and gate.shape != c.shape:
        raise ValueError("entry_ok must have the same shape as close")
    up = r * (1.0 + hyst)
    dn = r * (1.0 - hyst)
    out = np.zeros(c.shape[0])
    s = 1 if initial_state else 0
    for i in range(c.shape[0]):
        if np.isnan(r[i]):
            s = 0
            out[i] = 0.0
            continue
        if s == 0:
            if c[i] > up[i] and (gate is None or gate[i] > 0):
                s = 1
        elif c[i] < dn[i]:
            s = 0
        out[i] = float(s)
    return out
