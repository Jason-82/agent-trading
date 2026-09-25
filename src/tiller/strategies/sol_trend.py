"""Sleeve A (Donchian ensemble, vol-targeted) and sleeve B (SMA200 with hysteresis).

The vectorised exposure functions here ARE the functions the backtester and the golden
regression tests call; the live ``Strategy`` wrappers feed them closed daily bars and
persist per-bar state, so live signals cannot drift from the measured rules.

Rules (exactly Study 1 in the offline analysis):

* Donchian sleeve N: enter when ``close[t] > max(high[t-N..t-1])``; stop = Donchian
  midpoint of the last N bars at entry, then ``max(previous stop, midpoint)`` every bar;
  exit when ``close[t] < stop`` (checked from the bar after entry). Seven sleeves,
  N in {10,20,30,60,90,150,250}; ``frac`` = long sleeves / 7.
* Vol target: ``scale = min(1, vol_target / rv90)`` where ``rv90`` is the annualised
  std of the last 90 daily log returns; target weight = ``min(cap, frac * scale)``.
* Regime: OFF->ON when ``close > SMA_n * (1+hyst)``, ON->OFF when ``close < SMA_n * (1-hyst)``;
  weight = ``regime_weight`` when ON.
* Optional ``btc_confirm``: NEW entries (sleeve entries / regime switch-on) require the
  BTC hysteresis state to be ON; exits are never gated. If BTC data is required but
  missing, the gate is closed (fail closed).

Weights are fractions of TOTAL equity. All arrays are float64 and index-aligned; a bar
without a full lookback window yields exposure 0.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator

from tiller.models import Candle
from tiller.state import DonchianSleeveState, SleeveState
from tiller.strategies.base import MarketContext, TargetExposure, closed_daily_bars
from tiller.strategies.indicators import (
    donchian_mid,
    donchian_prior_high,
    hysteresis_state,
    realized_vol,
    sma,
)

DEFAULT_LOOKBACKS = [10, 20, 30, 60, 90, 150, 250]


class SolTrendParams(BaseModel):
    """Sleeve A parameters. ``vol_target=None`` disables vol scaling (scale = 1)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    lookbacks: list[int] = Field(default_factory=lambda: list(DEFAULT_LOOKBACKS))
    vol_target: float | None = 0.25
    vol_lookback: int = 90
    cap: float = 0.40
    btc_confirm: bool = False
    sma_n: int = 200
    hyst: float = 0.02
    min_bars: int = 300

    @field_validator("lookbacks")
    @classmethod
    def _lookbacks(cls, v: list[int]) -> list[int]:
        if not v or any(n < 1 for n in v) or len(set(v)) != len(v):
            raise ValueError("lookbacks must be distinct positive ints")
        return sorted(v)

    @field_validator("cap")
    @classmethod
    def _cap(cls, v: float) -> float:
        if not 0.0 < v <= 1.0:
            raise ValueError("cap must be in (0, 1]")
        return v

    @field_validator("vol_target")
    @classmethod
    def _vt(cls, v: float | None) -> float | None:
        if v is not None and v <= 0.0:
            raise ValueError("vol_target must be positive or None")
        return v


class RegimeParams(BaseModel):
    """Sleeve B parameters. ``weight`` is the fraction of equity held when ON."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    sma_n: int = 200
    hyst: float = 0.02
    btc_confirm: bool = False
    weight: float = 0.25
    min_bars: int = 300

    @field_validator("weight")
    @classmethod
    def _w(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("weight must be in [0, 1]")
        return v


# --------------------------------------------------------------------------- vectorised core


def donchian_sleeve_state(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    n: int,
    entry_ok: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Per-bar (state 0/1, trailing stop) for one Donchian sleeve; identical to study1.

    ``entry_ok`` (0/1 per bar) gates NEW entries only. The stop is ``nan`` while flat.
    """
    h = np.asarray(high, dtype=np.float64)
    lo = np.asarray(low, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    prior_high = donchian_prior_high(h, n)
    mid = donchian_mid(h, lo, n)
    gate = None if entry_ok is None else np.asarray(entry_ok, dtype=np.float64)
    state = np.zeros(c.shape[0])
    stops = np.full(c.shape[0], np.nan)
    s, stop = 0, np.nan
    for i in range(c.shape[0]):
        if np.isnan(prior_high[i]) or np.isnan(mid[i]):
            continue
        if s == 0:
            if c[i] > prior_high[i] and (gate is None or gate[i] > 0):
                s, stop = 1, mid[i]
        else:
            stop = max(stop, mid[i])
            if c[i] < stop:
                s, stop = 0, np.nan
        state[i] = s
        stops[i] = stop
    return state, stops


def btc_gate(btc_close: np.ndarray, sma_n: int, hyst: float) -> np.ndarray:
    """BTC hysteresis regime (0/1) used as the optional entry confirmation."""
    b = np.asarray(btc_close, dtype=np.float64)
    return hysteresis_state(b, sma(b, sma_n), hyst)


def vol_scale(close: np.ndarray, vol_target: float | None, lookback: int) -> np.ndarray:
    """``min(1, vol_target / rv)``; nan where rv is undefined; all ones when vol_target is None."""
    c = np.asarray(close, dtype=np.float64)
    if vol_target is None:
        return np.ones(c.shape[0])
    rv = realized_vol(c, lookback)
    with np.errstate(divide="ignore", invalid="ignore"):
        scale = np.minimum(1.0, vol_target / rv)
    return scale


@dataclass(frozen=True)
class EnsembleDetail:
    """Intermediate series of sleeve A for reporting and state persistence."""

    states: list[np.ndarray]
    stops: list[np.ndarray]
    frac: np.ndarray
    scale: np.ndarray
    rv: np.ndarray
    exposure: np.ndarray

    @property
    def round_trips(self) -> int:
        return sum(count_round_trips(s) for s in self.states)


def ensemble_detail(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    p: SolTrendParams,
    btc_close: np.ndarray | None = None,
) -> EnsembleDetail:
    """Full sleeve-A computation. ``btc_close`` must be index-aligned to the SOL arrays."""
    c = np.asarray(close, dtype=np.float64)
    gate = _entry_gate(p.btc_confirm, btc_close, c.shape[0], p.sma_n, p.hyst)
    states: list[np.ndarray] = []
    stops: list[np.ndarray] = []
    for n in p.lookbacks:
        s, st = donchian_sleeve_state(high, low, c, n, gate)
        states.append(s)
        stops.append(st)
    frac = np.sum(states, axis=0) / float(len(p.lookbacks))
    scale = vol_scale(c, p.vol_target, p.vol_lookback)
    rv = realized_vol(c, p.vol_lookback)
    exposure = np.minimum(p.cap, frac * scale)
    exposure = np.where(np.isnan(exposure), 0.0, exposure)
    return EnsembleDetail(states=states, stops=stops, frac=frac, scale=scale, rv=rv, exposure=exposure)


def ensemble_exposure(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    p: SolTrendParams,
    btc_close: np.ndarray | None = None,
) -> np.ndarray:
    """Sleeve A target weight per bar: ``min(cap, frac * min(1, vol_target/rv90))``."""
    return ensemble_detail(high, low, close, p, btc_close).exposure


def regime_state(close: np.ndarray, p: RegimeParams, btc_close: np.ndarray | None = None) -> np.ndarray:
    """Sleeve B 0/1 state per bar."""
    c = np.asarray(close, dtype=np.float64)
    gate = _entry_gate(p.btc_confirm, btc_close, c.shape[0], p.sma_n, p.hyst)
    return hysteresis_state(c, sma(c, p.sma_n), p.hyst, entry_ok=gate)


def regime_exposure(close: np.ndarray, p: RegimeParams, btc_close: np.ndarray | None = None) -> np.ndarray:
    """Sleeve B target weight per bar: ``weight`` when ON else 0."""
    return np.asarray(regime_state(close, p, btc_close) * p.weight, dtype=np.float64)


def sol_beta_cap_series(rv90: np.ndarray, cap_max: float = 0.50, cap_vol: float = 0.30) -> np.ndarray:
    """Portfolio SOL-beta cap per bar: ``min(cap_max, cap_vol / max(rv90, 0.05))``; 0 where rv is nan."""
    rv = np.asarray(rv90, dtype=np.float64)
    with np.errstate(invalid="ignore"):
        cap = np.minimum(cap_max, cap_vol / np.maximum(rv, 0.05))
    return np.where(np.isnan(cap), 0.0, cap)


def combined_exposure(
    a: np.ndarray, b: np.ndarray, rv90: np.ndarray, cap_max: float = 0.50, cap_vol: float = 0.30
) -> np.ndarray:
    """Netted SOL weight of sleeves A and B under the beta cap (the allocator rule)."""
    capped = np.minimum(np.asarray(a) + np.asarray(b), sol_beta_cap_series(rv90, cap_max, cap_vol))
    return np.asarray(capped, dtype=np.float64)


def count_round_trips(exposure: np.ndarray) -> int:
    """Number of flat->long transitions (nan counts as flat)."""
    e = np.asarray(exposure, dtype=np.float64)
    on = np.where(np.isnan(e), 0.0, e) > 0
    prev = np.concatenate(([False], on[:-1]))
    return int(np.sum(on & ~prev))


def _entry_gate(
    btc_confirm: bool, btc_close: np.ndarray | None, n: int, sma_n: int, hyst: float
) -> np.ndarray | None:
    if not btc_confirm:
        return None
    if btc_close is None:
        return np.zeros(n)  # fail closed: no BTC data => no new entries
    b = np.asarray(btc_close, dtype=np.float64)
    if b.shape[0] != n:
        raise ValueError("btc_close must be index-aligned with the SOL series")
    return btc_gate(b, sma_n, hyst)


# --------------------------------------------------------------------------- candle helpers


def candles_to_arrays(bars: list[Candle]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(open, high, low, close) float64 arrays in bar order."""
    o = np.array([float(b.open) for b in bars])
    h = np.array([float(b.high) for b in bars])
    lo = np.array([float(b.low) for b in bars])
    c = np.array([float(b.close) for b in bars])
    return o, h, lo, c


def align_closes(reference: list[Candle], other: list[Candle]) -> np.ndarray:
    """Closes of ``other`` at the bar timestamps of ``reference``; nan where ``other`` has no bar."""
    table = {b.ts: float(b.close) for b in other}
    return np.array([table.get(b.ts, np.nan) for b in reference])


def _weight_decimal(w: float) -> Decimal:
    if not np.isfinite(w) or w < 0.0:
        return Decimal(0)
    return Decimal(str(round(float(w), 8)))


# --------------------------------------------------------------------------- live wrappers


class SolTrendEnsembleStrategy:
    """Sleeve A on closed daily bars; evaluates at most once per bar and persists sleeve state."""

    name: str = "sol_trend_ensemble"
    cadence: Literal["daily", "monitor"] = "daily"

    def __init__(
        self, p: SolTrendParams, hold_mint: str, symbol: str = "SOL", btc_symbol: str = "BTC"
    ) -> None:
        self.p = p
        self.hold_mint = hold_mint
        self.symbol = symbol
        self.btc_symbol = btc_symbol

    def targets(self, ctx: MarketContext) -> tuple[list[TargetExposure], SleeveState]:
        prev = ctx.sleeve_state.get(self.name, SleeveState())
        bars = closed_daily_bars(ctx.candles.get(self.symbol, []), ctx.now)
        if len(bars) < max(self.p.min_bars, max(self.p.lookbacks) + 1, self.p.vol_lookback + 1):
            return [], prev
        last = bars[-1]
        if prev.last_bar_ts is not None and prev.last_bar_ts == last.ts:
            return [self._target(prev.last_weight, "already evaluated on this bar")], prev
        btc_bars = (
            closed_daily_bars(ctx.candles.get(self.btc_symbol, []), ctx.now) if self.p.btc_confirm else []
        )
        btc_close = align_closes(bars, btc_bars) if self.p.btc_confirm else None
        _, h, lo, c = candles_to_arrays(bars)
        contiguous = (
            prev.last_bar_ts is not None
            and len(bars) >= 2
            and bars[-2].ts == prev.last_bar_ts
            and set(prev.donchian) == {str(n) for n in self.p.lookbacks}
        )
        if contiguous:
            gate_last = _last_gate(self.p.btc_confirm, btc_close, self.p.sma_n, self.p.hyst)
            sleeves = {
                n: _step_sleeve(prev.donchian[str(n)], h, lo, c, n, gate_last) for n in self.p.lookbacks
            }
        else:
            detail = ensemble_detail(h, lo, c, self.p, btc_close)
            sleeves = {
                n: DonchianSleeveState(
                    n=n, long=bool(detail.states[i][-1] > 0), stop=_nan_none(detail.stops[i][-1])
                )
                for i, n in enumerate(self.p.lookbacks)
            }
        frac = sum(1 for s in sleeves.values() if s.long) / float(len(self.p.lookbacks))
        rv = float(realized_vol(c, self.p.vol_lookback)[-1])
        scale = float(vol_scale(c, self.p.vol_target, self.p.vol_lookback)[-1])
        raw = frac * scale
        weight = 0.0 if np.isnan(raw) else min(self.p.cap, raw)
        state = SleeveState(
            last_bar_ts=last.ts,
            donchian={str(n): s for n, s in sleeves.items()},
            regime_on=weight > 0,
            btc_regime_on=bool(
                btc_close is not None and _last_gate(True, btc_close, self.p.sma_n, self.p.hyst) > 0
            ),
            last_weight=weight,
            rv90=None if np.isnan(rv) else rv,
        )
        n_long = sum(1 for s in sleeves.values() if s.long)
        reason = (
            f"donchian {n_long}/{len(sleeves)} sleeves long; rv{self.p.vol_lookback}="
            f"{'n/a' if np.isnan(rv) else f'{rv:.3f}'}; scale={0.0 if np.isnan(scale) else scale:.3f}; "
            f"cap={self.p.cap:.2f}; bar={last.ts.date().isoformat()}"
        )
        return [self._target(weight, reason)], state

    def _target(self, weight: float, reason: str) -> TargetExposure:
        return TargetExposure(
            mint=self.hold_mint, weight=_weight_decimal(weight), strategy=self.name, reason=reason
        )


class SolRegimeSwitchStrategy:
    """Sleeve B on closed daily bars; at most one switch per day, state persisted."""

    name: str = "sol_regime_switch"
    cadence: Literal["daily", "monitor"] = "daily"

    def __init__(self, p: RegimeParams, hold_mint: str, symbol: str = "SOL", btc_symbol: str = "BTC") -> None:
        self.p = p
        self.hold_mint = hold_mint
        self.symbol = symbol
        self.btc_symbol = btc_symbol

    def targets(self, ctx: MarketContext) -> tuple[list[TargetExposure], SleeveState]:
        prev = ctx.sleeve_state.get(self.name, SleeveState())
        bars = closed_daily_bars(ctx.candles.get(self.symbol, []), ctx.now)
        if len(bars) < max(self.p.min_bars, self.p.sma_n):
            return [], prev
        last = bars[-1]
        if prev.last_bar_ts is not None and prev.last_bar_ts == last.ts:
            return [self._target(prev.last_weight, "already evaluated on this bar")], prev
        btc_bars = (
            closed_daily_bars(ctx.candles.get(self.btc_symbol, []), ctx.now) if self.p.btc_confirm else []
        )
        btc_close = align_closes(bars, btc_bars) if self.p.btc_confirm else None
        _, _, _, c = candles_to_arrays(bars)
        contiguous = prev.last_bar_ts is not None and len(bars) >= 2 and bars[-2].ts == prev.last_bar_ts
        ref = float(sma(c[-self.p.sma_n :], self.p.sma_n)[-1])
        if contiguous:
            gate_last = _last_gate(self.p.btc_confirm, btc_close, self.p.sma_n, self.p.hyst)
            on = prev.regime_on
            if np.isnan(ref):
                on = False
            elif not on and c[-1] > ref * (1.0 + self.p.hyst) and gate_last > 0:
                on = True
            elif on and c[-1] < ref * (1.0 - self.p.hyst):
                on = False
        else:
            on = bool(regime_state(c, self.p, btc_close)[-1] > 0)
        weight = self.p.weight if on else 0.0
        state = SleeveState(
            last_bar_ts=last.ts,
            regime_on=on,
            btc_regime_on=bool(
                btc_close is not None and _last_gate(True, btc_close, self.p.sma_n, self.p.hyst) > 0
            ),
            last_weight=weight,
        )
        reason = (
            f"sma{self.p.sma_n} regime {'ON' if on else 'OFF'}; close={c[-1]:.4f} sma={ref:.4f} "
            f"hyst={self.p.hyst:.3f}; bar={last.ts.date().isoformat()}"
        )
        return [self._target(weight, reason)], state

    def _target(self, weight: float, reason: str) -> TargetExposure:
        return TargetExposure(
            mint=self.hold_mint, weight=_weight_decimal(weight), strategy=self.name, reason=reason
        )


def _nan_none(x: float) -> float | None:
    return None if np.isnan(x) else float(x)


def _last_gate(btc_confirm: bool, btc_close: np.ndarray | None, sma_n: int, hyst: float) -> float:
    """Entry gate value on the last bar (1.0 = entries allowed)."""
    if not btc_confirm:
        return 1.0
    if btc_close is None or btc_close.shape[0] == 0:
        return 0.0
    return float(btc_gate(btc_close, sma_n, hyst)[-1])


def _step_sleeve(
    prev: DonchianSleeveState, high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int, gate_last: float
) -> DonchianSleeveState:
    """One-bar incremental update of a Donchian sleeve (same transitions as the vectorised loop)."""
    if high.shape[0] <= n:
        return DonchianSleeveState(n=n, long=False, stop=None)
    prior_high = float(np.max(high[-n - 1 : -1]))
    mid = float((np.max(high[-n:]) + np.min(low[-n:])) / 2.0)
    c = float(close[-1])
    if not prev.long:
        if c > prior_high and gate_last > 0:
            return DonchianSleeveState(n=n, long=True, stop=mid)
        return DonchianSleeveState(n=n, long=False, stop=None)
    stop = max(prev.stop if prev.stop is not None else mid, mid)
    if c < stop:
        return DonchianSleeveState(n=n, long=False, stop=None)
    return DonchianSleeveState(n=n, long=True, stop=stop)


def last_bar_datetime(bars: list[Candle]) -> datetime | None:
    return bars[-1].ts if bars else None
