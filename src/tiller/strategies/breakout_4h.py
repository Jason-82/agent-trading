"""STRETCH, disabled by default: pure 4h breakout signal and simulator reproducing Study 2.

Nothing here is wired into the live agent (no GeckoTerminal source, no venue). It exists
so the rule is certified against the offline study before anyone considers a satellite.

Rule (Study 2, ``scratchpad/analysis/study2_4h_breakout.py``), all on UTC-aligned 4h bars:

* signal at the 4h CLOSE of bar t, entry at the OPEN of bar t+1;
* entry: ``close > max(high[t-20..t-1])`` and ``volume >= 1.5 * median(volume[t-20..t-1])``
  and ``EMA20 > EMA50`` and ``close > EMA50``;
* initial stop ``2.5 x ATR(14, Wilder)`` below entry, distance clamped to [4%, 20%] = 1R;
* break-even at a close >= entry + 1R; trail ``max(stop, close - 4 x ATR)`` from +2R;
* exit at the next open when ``close < EMA50``, or after 18 bars without a close >= +0.3R;
* stops are checked intrabar on the next bar (fill at the open if it gaps through);
* sizing ``risk1pct``: notional = min(1, 1% / stop distance) of equity; ``fixed100``: 100%;
* cost per side on notional (bps).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict

from tiller.models import Candle
from tiller.strategies.indicators import atr_wilder, ema, rolling_max, rolling_median

FOUR_HOURS = timedelta(hours=4)
PERIODS_PER_YEAR_4H = 6 * 365


class BreakoutParams(BaseModel):
    """Study-2 parameters. Fractions unless noted; ``stop_clamp`` = (min, max) stop distance."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    lookback: int = 20
    vol_mult: float = 1.5
    atr_n: int = 14
    atr_stop: float = 2.5
    stop_clamp: tuple[float, float] = (0.04, 0.20)
    trail_atr: float = 4.0
    trail_from_r: float = 2.0
    breakeven_from_r: float = 1.0
    time_stop_bars: int = 18
    time_stop_min_r: float = 0.3
    ema_fast: int = 20
    ema_slow: int = 50
    risk_per_trade: float = 0.01
    cap: float = 0.10
    max_positions: int = 2


class BreakoutResult(BaseModel):
    """Simulator summary; ``pf`` = gross profit / gross loss, fractions elsewhere."""

    model_config = ConfigDict(extra="forbid")

    trades: int
    pf: float
    cagr: float
    max_dd: float
    sharpe: float = 0.0
    win_rate: float = 0.0
    time_in_market: float = 0.0
    last_12m_trades: int = 0
    last_12m_pf: float = 0.0
    exit_reasons: dict[str, int] = {}


def resample_4h(bars_1h: list[Candle]) -> list[Candle]:
    """Aggregate hourly bars into UTC-aligned 4h bars (00,04,08,12,16,20); empty buckets are dropped."""
    out: list[Candle] = []
    cur_key: datetime | None = None
    o = h = lo = c = v = Decimal(0)
    for b in sorted(bars_1h, key=lambda x: x.ts):
        key = b.ts - timedelta(
            hours=b.ts.hour % 4, minutes=b.ts.minute, seconds=b.ts.second, microseconds=b.ts.microsecond
        )
        if key != cur_key:
            if cur_key is not None:
                out.append(Candle(ts=cur_key, open=o, high=h, low=lo, close=c, volume=v))
            cur_key, o, h, lo, c, v = key, b.open, b.high, b.low, b.close, b.volume
        else:
            h = max(h, b.high)
            lo = min(lo, b.low)
            c = b.close
            v += b.volume
    if cur_key is not None:
        out.append(Candle(ts=cur_key, open=o, high=h, low=lo, close=c, volume=v))
    return out


@dataclass(frozen=True)
class BreakoutSeries:
    """Indicator series aligned with the 4h bars."""

    ema_fast: np.ndarray
    ema_slow: np.ndarray
    atr: np.ndarray
    prior_high: np.ndarray
    prior_median_vol: np.ndarray
    entry_signal: np.ndarray  # bool per bar: breakout conditions met at this close


def breakout_series(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray, p: BreakoutParams
) -> BreakoutSeries:
    """Pure signal: every entry condition evaluated on closed bars (no position state)."""
    c = np.asarray(close, dtype=np.float64)
    n = c.shape[0]
    ef = ema(c, p.ema_fast)
    es = ema(c, p.ema_slow)
    atr = atr_wilder(high, low, c, p.atr_n)
    prior_high = np.full(n, np.nan)
    prior_med = np.full(n, np.nan)
    if n > p.lookback:
        prior_high[p.lookback :] = rolling_max(np.asarray(high, dtype=np.float64), p.lookback)[
            p.lookback - 1 : -1
        ]
        prior_med[p.lookback :] = rolling_median(np.asarray(volume, dtype=np.float64), p.lookback)[
            p.lookback - 1 : -1
        ]
    vol = np.asarray(volume, dtype=np.float64)
    with np.errstate(invalid="ignore"):
        sig = (
            ~np.isnan(prior_high)
            & ~np.isnan(prior_med)
            & ~np.isnan(atr)
            & (c > prior_high)
            & (vol >= p.vol_mult * prior_med)
            & (ef > es)
            & (c > es)
        )
    return BreakoutSeries(
        ema_fast=ef, ema_slow=es, atr=atr, prior_high=prior_high, prior_median_vol=prior_med, entry_signal=sig
    )


@dataclass
class _Trade:
    entry_i: int
    exit_i: int
    entry: float
    exit: float
    pnl: float
    r_multiple: float
    reason: str
    notional_frac: float


@dataclass
class _Pos:
    i: int
    entry: float
    stop: float
    r: float
    cost_basis: float
    notional_frac: float
    hit_min_r: bool = False
    trades: list[_Trade] = field(default_factory=list)


def breakout_backtest(
    bars4h: list[Candle],
    p: BreakoutParams,
    cost_bps: int,
    sizing: Literal["risk1pct", "fixed100"] = "risk1pct",
    start: datetime = datetime(2022, 1, 1, tzinfo=UTC),
    last_12m_start: datetime = datetime(2025, 9, 24, tzinfo=UTC),
    gate: np.ndarray | None = None,
) -> BreakoutResult:
    """Event-driven single-position simulator identical to Study 2.

    Indicators warm up on every bar in ``bars4h``; trading starts at the first bar >= ``start``.
    ``gate`` (bool per bar) optionally requires an external regime for entries.
    """
    o = np.array([float(b.open) for b in bars4h])
    h = np.array([float(b.high) for b in bars4h])
    lo = np.array([float(b.low) for b in bars4h])
    c = np.array([float(b.close) for b in bars4h])
    v = np.array([float(b.volume) for b in bars4h])
    s = breakout_series(h, lo, c, v, p)
    n = c.shape[0]
    cost = cost_bps / 10_000.0
    ok_gate = np.ones(n, dtype=bool) if gate is None else np.asarray(gate, dtype=bool)
    ts = [b.ts for b in bars4h]
    start_i = next((i for i, t in enumerate(ts) if t >= start), n)
    equity = np.full(n, np.nan)
    cash, units = 1.0, 0.0
    pos: _Pos | None = None
    pending_exit: str | None = None
    pending_entry: float | None = None
    trades: list[_Trade] = []

    def close_trade(i: int, price: float, reason: str) -> None:
        nonlocal cash, units, pos
        assert pos is not None
        proceeds = units * price * (1.0 - cost)
        cash += proceeds
        trades.append(
            _Trade(
                entry_i=pos.i,
                exit_i=i,
                entry=pos.entry,
                exit=price,
                pnl=proceeds - pos.cost_basis,
                r_multiple=(price - pos.entry) / pos.r,
                reason=reason,
                notional_frac=pos.notional_frac,
            )
        )
        units, pos = 0.0, None

    for i in range(start_i, n):
        just_exited = False
        if pos is not None and pending_exit is not None:
            close_trade(i, o[i], pending_exit)
            pending_exit = None
            just_exited = True
        if pos is None and pending_entry is not None and not just_exited:
            sd = pending_entry
            frac = min(1.0, p.risk_per_trade / sd) if sizing == "risk1pct" else 1.0
            notional = frac * cash
            entry = o[i]
            units = notional / (entry * (1.0 + cost))
            cash -= notional
            pos = _Pos(
                i=i,
                entry=entry,
                stop=entry * (1.0 - sd),
                r=entry * sd,
                cost_basis=notional,
                notional_frac=frac,
            )
        pending_entry = None
        if pos is not None and i > pos.i:
            if o[i] <= pos.stop:
                close_trade(i, o[i], "stop_gap")
            elif lo[i] <= pos.stop:
                close_trade(i, pos.stop, "stop")
        if pos is not None:
            e, r = pos.entry, pos.r
            if c[i] >= e + p.time_stop_min_r * r:
                pos.hit_min_r = True
            if c[i] >= e + p.breakeven_from_r * r:
                pos.stop = max(pos.stop, e)
            if c[i] >= e + p.trail_from_r * r:
                pos.stop = max(pos.stop, c[i] - p.trail_atr * s.atr[i])
            if c[i] < s.ema_slow[i]:
                pending_exit = "ema_slow"
            elif i - pos.i >= p.time_stop_bars and not pos.hit_min_r:
                pending_exit = "time"
        elif s.entry_signal[i] and ok_gate[i] and i + 1 < n:
            sd = p.atr_stop * s.atr[i] / c[i]
            pending_entry = float(min(p.stop_clamp[1], max(p.stop_clamp[0], sd)))
        equity[i] = cash + units * c[i]

    eq = equity[start_i:]
    r_series = eq[1:] / eq[:-1] - 1.0
    return _summarise(r_series, trades, ts, start_i, last_12m_start)


def _pf(pnls: list[float]) -> float:
    gp = sum(x for x in pnls if x > 0)
    gl = -sum(x for x in pnls if x < 0)
    return gp / gl if gl > 0 else (float("inf") if gp > 0 else 0.0)


def _summarise(
    r: np.ndarray, trades: list[_Trade], ts: list[datetime], start_i: int, l12: datetime
) -> BreakoutResult:
    if r.shape[0] == 0:
        return BreakoutResult(trades=0, pf=0.0, cagr=0.0, max_dd=0.0)
    eq = np.cumprod(1.0 + r)
    n_years = r.shape[0] / PERIODS_PER_YEAR_4H
    cagr = float(eq[-1] ** (1.0 / n_years) - 1.0)
    sd = float(np.std(r, ddof=1)) if r.shape[0] > 1 else 0.0
    sharpe = float(np.mean(r) / sd * np.sqrt(PERIODS_PER_YEAR_4H)) if sd > 0 else 0.0
    dd = float((eq / np.maximum.accumulate(eq) - 1.0).min())
    in_mkt = np.zeros(len(ts) - start_i, dtype=bool)
    for t in trades:
        in_mkt[t.entry_i - start_i : t.exit_i - start_i] = True
    pnls = [t.pnl for t in trades]
    l12_trades = [t for t in trades if ts[t.exit_i] >= l12]
    reasons: dict[str, int] = {}
    for t in trades:
        reasons[t.reason] = reasons.get(t.reason, 0) + 1
    return BreakoutResult(
        trades=len(trades),
        pf=_pf(pnls),
        cagr=cagr,
        max_dd=dd,
        sharpe=sharpe,
        win_rate=float(np.mean([x > 0 for x in pnls])) if pnls else 0.0,
        time_in_market=float(in_mkt.mean()),
        last_12m_trades=len(l12_trades),
        last_12m_pf=_pf([t.pnl for t in l12_trades]),
        exit_reasons=reasons,
    )
