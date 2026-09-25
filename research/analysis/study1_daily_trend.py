"""Study 1 - daily trend following on SOL / BTC / ETH (long-only, no leverage).

Execution: signal on the daily CLOSE of bar t, filled at the OPEN of bar t+1.
Costs: charged per side on |change in exposure|; reported at 5 / 10 / 30 bps.
Primary cost: SOL 30 bps (Solana DEX spot), BTC/ETH 5 bps (perp / CEX spot).
Warm-up: the first 250 daily bars per asset are excluded from evaluation so that
every rule (max lookback 250) and buy-and-hold share the same window.
Parameters are fixed a priori (no optimisation); the walk-forward check simply
reports the window up to 2024-09-23 ("in-sample") and the last 24 months
(2024-09-24 -> 2026-09-23) separately.
"""
import numpy as np
import pandas as pd
from common import (load_ohlcv, exec_prices, strategy_returns, perf_metrics,
                    calendar_year_returns, count_round_trips, dump, md_table,
                    fmt_pct, fmt_num)

ASSETS = ["SOLUSDT", "BTCUSDT", "ETHUSDT"]
COSTS = {"5bps": 0.0005, "10bps": 0.0010, "30bps": 0.0030}
PRIMARY = {"SOLUSDT": "30bps", "BTCUSDT": "5bps", "ETHUSDT": "5bps"}
WARMUP = 250
PPY = 365
OOS_START = pd.Timestamp("2024-09-24", tz="UTC")
DONCHIAN_N = [10, 20, 30, 60, 90, 150, 250]
HYST = 0.02
VOL_TARGET = 0.25
VOL_LOOKBACK = 90


def sma_regime(df, n, hyst=HYST):
    sma = df["close"].rolling(n).mean()
    close = df["close"].values
    up = (sma * (1 + hyst)).values
    dn = (sma * (1 - hyst)).values
    state = np.zeros(len(df))
    s = 0
    for i in range(len(df)):
        if np.isnan(up[i]):
            state[i] = 0
            continue
        if s == 0 and close[i] > up[i]:
            s = 1
        elif s == 1 and close[i] < dn[i]:
            s = 0
        state[i] = s
    return pd.Series(state, index=df.index)


def donchian_sleeve(df, n):
    """Long when close > prior n-day high (highs of bars t-n..t-1).
    Trailing stop = max(Donchian midpoint of the last n bars incl. t, prior stop);
    exit when close < stop (checked from the bar after entry)."""
    prior_high = df["high"].shift(1).rolling(n).max().values
    mid = ((df["high"].rolling(n).max() + df["low"].rolling(n).min()) / 2.0).values
    close = df["close"].values
    state = np.zeros(len(df))
    s, stop = 0, np.nan
    for i in range(len(df)):
        if np.isnan(prior_high[i]) or np.isnan(mid[i]):
            continue
        if s == 0:
            if close[i] > prior_high[i]:
                s, stop = 1, mid[i]
        else:
            stop = max(stop, mid[i])
            if close[i] < stop:
                s, stop = 0, np.nan
        state[i] = s
    return pd.Series(state, index=df.index)


def donchian_ensemble(df):
    sleeves = {n: donchian_sleeve(df, n) for n in DONCHIAN_N}
    ens = sum(sleeves.values()) / len(sleeves)
    rt = sum(count_round_trips(s) for s in sleeves.values())
    return ens, rt, sleeves


def vol_scale(df):
    lr = np.log(df["close"]).diff()
    rv = lr.rolling(VOL_LOOKBACK).std(ddof=1) * np.sqrt(PPY)
    return (VOL_TARGET / rv).clip(upper=1.0)


def evaluate(exposure, px, cost, label):
    r = strategy_returns(exposure, px, cost)
    r = r.iloc[WARMUP:]
    ex = exposure.shift(1)  # exposure decided at close t is held over the period labelled t+1
    m = perf_metrics(r, PPY, exposure=ex)
    m["calendar_years"] = calendar_year_returns(r)
    m["turnover_per_year"] = float((exposure - exposure.shift(1).fillna(0)).abs().iloc[WARMUP:].sum()
                                   / (len(r) / PPY))
    r_is = r[r.index < OOS_START]
    r_oos = r[r.index >= OOS_START]
    m["in_sample"] = perf_metrics(r_is, PPY, exposure=ex)
    m["last_24m"] = perf_metrics(r_oos, PPY, exposure=ex)
    m["label"] = label
    return m, r


def run_asset(sym):
    df = load_ohlcv(sym, "1d")
    px = exec_prices(df)
    rules = {}
    exposures = {}
    e_bh = pd.Series(1.0, index=df.index)
    exposures["buy_and_hold"] = (e_bh, 1)
    for n in (200, 100):
        e = sma_regime(df, n)
        exposures[f"sma{n}_hyst2pct"] = (e, count_round_trips(e))
    ens, rt, sleeves = donchian_ensemble(df)
    exposures["donchian_ensemble"] = (ens, rt)
    vt = (ens * vol_scale(df)).fillna(0.0)
    exposures["donchian_ensemble_voltarget25"] = (vt, rt)
    for name, (e, rt) in exposures.items():
        e = e.copy()
        e.iloc[:WARMUP] = 0.0  # evaluation window starts at bar 250; entry cost charged there
        rules[name] = {}
        for cname, c in COSTS.items():
            m, r = evaluate(e, px, c, f"{sym} {name} {cname}")
            m["round_trips"] = int(rt)
            rules[name][cname] = m
    return {"asset": sym, "primary_cost": PRIMARY[sym], "eval_start": str(df.index[WARMUP].date()),
            "eval_end": str(df.index[-1].date()), "rules": rules}


def main():
    results = {"description": __doc__, "assets": {}}
    md = []
    md.append("Execution: signal at daily close t, fill at open t+1; costs per side on |change in exposure|. "
              "Evaluation starts after a 250-bar warm-up (SOL 2021-04-18, BTC/ETH 2018-04-24) and ends 2026-09-23. "
              "Walk-forward: 'IS' = eval start -> 2024-09-23, 'last 24m' = 2024-09-24 -> 2026-09-23 (parameters fixed a priori, nothing optimised).\n")
    for sym in ASSETS:
        res = run_asset(sym)
        results["assets"][sym] = res
        pc = res["primary_cost"]
        md.append(f"\n### {sym} (primary cost {pc}; window {res['eval_start']} -> {res['eval_end']})\n")
        rows = []
        for rule, byc in res["rules"].items():
            m = byc[pc]
            cy = m["calendar_years"]
            rows.append([rule, fmt_pct(m["cagr"]), fmt_pct(m["ann_vol"]), fmt_num(m["sharpe"]),
                         fmt_pct(m["max_drawdown"]), fmt_pct(m.get("time_in_market")),
                         fmt_num(m.get("avg_exposure"), 2), m["round_trips"], fmt_num(m["turnover_per_year"], 1),
                         fmt_pct(cy.get("2022")), fmt_pct(cy.get("2025")), fmt_pct(cy.get("2026")),
                         fmt_num(m["in_sample"].get("sharpe")), fmt_pct(m["in_sample"].get("cagr")),
                         fmt_num(m["last_24m"].get("sharpe")), fmt_pct(m["last_24m"].get("cagr")),
                         fmt_pct(m["last_24m"].get("max_drawdown"))])
        md.append(md_table(["rule", "CAGR", "vol", "Sharpe", "MaxDD", "time in mkt", "avg exp", "round trips",
                            "turnover/yr", "2022", "2025", "2026 YTD", "IS Sharpe", "IS CAGR",
                            "24m Sharpe", "24m CAGR", "24m MaxDD"], rows))
        # cost sensitivity
        rows = []
        for rule, byc in res["rules"].items():
            rows.append([rule] + [f"{fmt_pct(byc[c]['cagr'])} / {fmt_num(byc[c]['sharpe'])}" for c in COSTS])
        md.append("\nCost sensitivity (CAGR / Sharpe):\n")
        md.append(md_table(["rule"] + list(COSTS), rows))
        # all calendar years
        years = sorted({y for byc in res["rules"].values() for y in byc[pc]["calendar_years"]})
        rows = []
        for rule, byc in res["rules"].items():
            cy = byc[pc]["calendar_years"]
            rows.append([rule] + [fmt_pct(cy.get(y)) for y in years])
        md.append(f"\nCalendar-year returns at {pc}:\n")
        md.append(md_table(["rule"] + years, rows))
    dump("study1", results, "\n".join(md))
    print("\n".join(md))


if __name__ == "__main__":
    main()
