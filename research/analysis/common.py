"""Shared helpers for the offline studies. Deterministic, no network.

Conventions
-----------
* All timestamps UTC. OHLCV timestamp = bar OPEN time (unix s).
* Signals are computed on CLOSED bars only. Unless a study states otherwise a
  signal generated at the close of bar t is executed at the OPEN of bar t+1.
* Costs are charged per side on the absolute change in exposure (turnover).
"""
import json
import os
import numpy as np
import pandas as pd

ROOT = ".."
DATA = os.path.join(ROOT, "data", "normalized")
ANALYSIS = os.path.join(ROOT, "analysis")
OUT = os.path.join(ANALYSIS, "out")
os.makedirs(OUT, exist_ok=True)

END_DATE = pd.Timestamp("2026-09-23 23:59:59", tz="UTC")


def load_ohlcv(symbol, tf):
    df = pd.read_csv(os.path.join(DATA, f"{symbol}_{tf}.csv"))
    df["ts"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    df = df.set_index("ts").drop(columns=["timestamp"]).sort_index()
    df = df[df.index <= END_DATE]
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def exec_prices(df):
    """Execution price for a signal formed at close of bar t: open of bar t+1.
    The final bar has no successor, so it is marked at its own close."""
    p = df["open"].shift(-1)
    p.iloc[-1] = df["close"].iloc[-1]
    return p


def strategy_returns(exposure, px, cost_per_side):
    """exposure[t] decided at close t, established at price px[t] (= next open).
    Return over [px[t], px[t+1]] = exposure[t] * (px[t+1]/px[t] - 1)
    minus cost_per_side * |exposure[t] - exposure[t-1]| charged at px[t].
    Returned series is indexed by the bar whose open the period starts at."""
    e = exposure.fillna(0.0).astype(float)
    fwd = px.shift(-1) / px - 1.0
    turnover = (e - e.shift(1).fillna(0.0)).abs()
    r = e * fwd - cost_per_side * turnover
    r = r.iloc[:-1]  # last bar has no forward return
    # label each period by the bar whose open starts it (t+1), so calendar
    # buckets line up with the day the return accrues over
    r.index = px.index[1:]
    return r


def perf_metrics(r, periods_per_year, exposure=None, round_trips=None):
    r = r.dropna()
    if len(r) == 0:
        return {}
    eq = (1.0 + r).cumprod()
    n_years = len(r) / periods_per_year
    total = float(eq.iloc[-1])
    cagr = total ** (1.0 / n_years) - 1.0 if n_years > 0 else np.nan
    vol = float(r.std(ddof=1) * np.sqrt(periods_per_year))
    sharpe = float(r.mean() / r.std(ddof=1) * np.sqrt(periods_per_year)) if r.std(ddof=1) > 0 else np.nan
    dd = eq / eq.cummax() - 1.0
    mdd = float(dd.min())
    out = {
        "start": str(r.index[0].date()),
        "end": str(r.index[-1].date()),
        "n_periods": int(len(r)),
        "total_return": total - 1.0,
        "cagr": cagr,
        "ann_vol": vol,
        "sharpe": sharpe,
        "max_drawdown": mdd,
    }
    if exposure is not None:
        ex = exposure.reindex(r.index).fillna(0.0)
        out["time_in_market"] = float((ex > 0).mean())
        out["avg_exposure"] = float(ex.mean())
    if round_trips is not None:
        out["round_trips"] = int(round_trips)
    return out


def calendar_year_returns(r):
    r = r.dropna()
    yr = (1.0 + r).groupby(r.index.year).prod() - 1.0
    return {str(k): float(v) for k, v in yr.items()}


def count_round_trips(exposure):
    """Number of flat->long transitions (a round trip = one entry and its exit)."""
    e = (exposure.fillna(0.0) > 0).astype(int)
    entries = ((e == 1) & (e.shift(1).fillna(0) == 0)).sum()
    return int(entries)


def fmt_pct(x, d=1):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "n/a"
    return f"{100*x:.{d}f}%"


def fmt_num(x, d=2):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "n/a"
    return f"{x:.{d}f}"


def md_table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)


def dump(name, obj, md_text):
    with open(os.path.join(OUT, f"{name}.json"), "w") as f:
        json.dump(obj, f, indent=1, default=_json_default)
    with open(os.path.join(OUT, f"{name}.md"), "w") as f:
        f.write(md_text)


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        v = float(o)
        return None if np.isnan(v) else v
    if isinstance(o, float) and np.isnan(o):
        return None
    if isinstance(o, (pd.Timestamp,)):
        return str(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(str(type(o)))
