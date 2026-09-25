"""Study 2 - 4h breakout on SOL and ETH (long-only, no leverage).

Data: Binance spot 1h bars resampled to UTC-aligned 4h bars (00,04,08,12,16,20).
Signal at the 4h CLOSE of bar t; entry at the OPEN of bar t+1.
Entry: close > prior 20-bar high (highs of bars t-20..t-1)
       and volume >= 1.5 x median volume of the prior 20 bars
       and EMA20 > EMA50 and close > EMA50 (own regime gate)
       [variant: additionally BTC 4h close > BTC 4h EMA50].
Initial stop: 2.5 x ATR(14, Wilder) below entry, distance clamped to [4%, 20%]; R = that distance.
Break-even: once a bar CLOSES >= entry + 1R, stop -> entry (from the next bar).
Trail: once a bar CLOSES >= entry + 2R, stop = max(stop, close - 4 x ATR) (from the next bar).
Exit on close < EMA50 (filled next open); time stop: 18 bars held without a close >= +0.3R (filled next open).
Stops are checked intrabar on the NEXT bar: fill at the open if the bar opens through the stop, else at the stop.
No re-entry on the same open as an exit.
Sizing: (i) 1% of equity at risk => notional = min(100%, 1% / stop_distance) of equity;
        (ii) fixed 100% of equity notional.
Costs per side on notional: 5 / 10 / 30 bps. Primary: SOL 30 bps (DEX spot), ETH 5 bps (perp).
Window: 2022-01-01 -> 2026-09-23 (indicators warmed up on 2021 data); last 12 months = 2025-09-24 -> 2026-09-23.
"""
import numpy as np
import pandas as pd
from common import (load_ohlcv, perf_metrics, calendar_year_returns, dump, md_table, fmt_pct, fmt_num)

COSTS = {"5bps": 0.0005, "10bps": 0.0010, "30bps": 0.0030}
PRIMARY = {"SOLUSDT": "30bps", "ETHUSDT": "5bps"}
PPY = 6 * 365
START = pd.Timestamp("2022-01-01", tz="UTC")
LAST12 = pd.Timestamp("2025-09-24", tz="UTC")
WARM = pd.Timestamp("2021-06-01", tz="UTC")


def to_4h(df1h):
    o = df1h.resample("4h", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    return o.dropna(subset=["open"])


def indicators(df):
    d = df.copy()
    d["ema20"] = d["close"].ewm(span=20, adjust=False).mean()
    d["ema50"] = d["close"].ewm(span=50, adjust=False).mean()
    pc = d["close"].shift(1)
    tr = pd.concat([d["high"] - d["low"], (d["high"] - pc).abs(), (d["low"] - pc).abs()], axis=1).max(axis=1)
    d["atr14"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    d["hh20"] = d["high"].shift(1).rolling(20).max()
    d["medvol20"] = d["volume"].shift(1).rolling(20).median()
    return d


def simulate(d, btc_gate, cost, sizing, start=START):
    """Event-driven bar loop. Returns equity series (per 4h bar) and trade list."""
    n = len(d)
    o, h, l, c = d["open"].values, d["high"].values, d["low"].values, d["close"].values
    ema20, ema50, atr, hh20, medvol, vol = (d["ema20"].values, d["ema50"].values, d["atr14"].values,
                                            d["hh20"].values, d["medvol20"].values, d["volume"].values)
    gate = d["btc_gate"].values if btc_gate else np.ones(n, dtype=bool)
    idx = d.index
    start_i = int(np.searchsorted(idx, start))
    equity = np.full(n, np.nan)
    cash = 1.0
    units = 0.0
    pos = None
    pending_exit = None  # reason string -> exit at this bar's open
    pending_entry = None  # (stop_dist_pct) -> enter at this bar's open
    trades = []

    def close_trade(i, price, reason):
        nonlocal cash, units, pos
        proceeds = units * price * (1 - cost)
        cash += proceeds
        pnl = proceeds - pos["cost_basis"]
        trades.append({"entry_time": str(idx[pos["i"]]), "exit_time": str(idx[i]), "entry": pos["entry"],
                       "exit": price, "notional_frac": pos["notional_frac"], "pnl": pnl,
                       "ret_on_notional": price * (1 - cost) / (pos["entry"] * (1 + cost)) - 1,
                       "R": (price - pos["entry"]) / pos["R"], "bars": i - pos["i"], "reason": reason})
        units, pos = 0.0, None

    for i in range(start_i, n):
        # 1) fills at this bar's open
        if pos is not None and pending_exit is not None:
            close_trade(i, o[i], pending_exit)
            pending_exit = None
            just_exited = True
        else:
            just_exited = False
        if pos is None and pending_entry is not None and not just_exited:
            sd = pending_entry
            eq = cash
            frac = min(1.0, 0.01 / sd) if sizing == "risk1pct" else 1.0
            notional = frac * eq
            entry = o[i]
            units = notional / (entry * (1 + cost))
            cash -= notional
            pos = {"i": i, "entry": entry, "stop": entry * (1 - sd), "R": entry * sd, "cost_basis": notional,
                   "notional_frac": frac, "be": False, "trail": False, "hit03": False}
        pending_entry = None
        # 2) intrabar stop check (stop level set at a previous close)
        if pos is not None and i > pos["i"]:
            if o[i] <= pos["stop"]:
                close_trade(i, o[i], "stop_gap")
            elif l[i] <= pos["stop"]:
                close_trade(i, pos["stop"], "stop")
        # 3) bar close: manage position, generate signals
        if pos is not None:
            e, R = pos["entry"], pos["R"]
            if c[i] >= e + 0.3 * R:
                pos["hit03"] = True
            if c[i] >= e + 1.0 * R:
                pos["stop"] = max(pos["stop"], e)
            if c[i] >= e + 2.0 * R:
                pos["stop"] = max(pos["stop"], c[i] - 4.0 * atr[i])
            bars_held = i - pos["i"]
            if c[i] < ema50[i]:
                pending_exit = "ema50"
            elif bars_held >= 18 and not pos["hit03"]:
                pending_exit = "time"
        else:
            ok = (not np.isnan(hh20[i]) and not np.isnan(medvol[i]) and not np.isnan(atr[i])
                  and c[i] > hh20[i] and vol[i] >= 1.5 * medvol[i] and ema20[i] > ema50[i]
                  and c[i] > ema50[i] and gate[i] and i + 1 < n)
            if ok:
                sd = 2.5 * atr[i] / c[i]
                pending_entry = float(min(0.20, max(0.04, sd)))
        equity[i] = cash + units * c[i]
    if pos is not None:  # mark open position at last close (no forced exit)
        pass
    eq = pd.Series(equity, index=idx).iloc[start_i:]
    return eq, trades


def trade_stats(trades):
    if not trades:
        return {"n_trades": 0}
    pnl = np.array([t["pnl"] for t in trades])
    R = np.array([t["R"] for t in trades])
    gp, gl = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    reasons = pd.Series([t["reason"] for t in trades]).value_counts().to_dict()
    return {"n_trades": int(len(trades)), "win_rate": float((pnl > 0).mean()),
            "profit_factor": float(gp / gl) if gl > 0 else np.inf, "avg_R": float(R.mean()),
            "median_R": float(np.median(R)), "avg_bars": float(np.mean([t["bars"] for t in trades])),
            "avg_ret_on_notional": float(np.mean([t["ret_on_notional"] for t in trades])),
            "exit_reasons": reasons}


def metrics_from_equity(eq, trades, exposure):
    r = eq.pct_change().dropna()
    m = perf_metrics(r, PPY, exposure=exposure)
    m["calendar_years"] = calendar_year_returns(r)
    m.update(trade_stats(trades))
    r12 = r[r.index >= LAST12]
    t12 = [t for t in trades if pd.Timestamp(t["exit_time"]) >= LAST12]
    m12 = perf_metrics(r12, PPY, exposure=exposure)
    m12.update(trade_stats(t12))
    m["last_12m"] = m12
    return m


def main():
    btc = indicators(to_4h(load_ohlcv("BTCUSDT", "1h")[WARM:]))
    btc_gate = (btc["close"] > btc["ema50"])
    results = {"description": __doc__, "assets": {}}
    md = ["Execution: 4h close signal, next 4h open fill; stops intrabar; costs per side on notional. "
          "Window 2022-01-01 -> 2026-09-23; 'L12M' = 2025-09-24 -> 2026-09-23. PF = profit factor; WR = win rate; "
          "avg R = mean (exit-entry)/initial risk. B&H = buy and hold of the same asset over the same window.\n"]
    for sym in ["SOLUSDT", "ETHUSDT"]:
        d = indicators(to_4h(load_ohlcv(sym, "1h")[WARM:]))
        d["btc_gate"] = btc_gate.reindex(d.index).ffill().fillna(False).astype(bool)
        res = {}
        # buy and hold reference
        bh = d.loc[START:, "close"]
        bh_eq = bh / d.loc[START:, "open"].iloc[0]
        bh_m = perf_metrics(bh_eq.pct_change().dropna(), PPY)
        bh_m["calendar_years"] = calendar_year_returns(bh_eq.pct_change().dropna())
        bh_m["last_12m"] = perf_metrics(bh_eq.pct_change().dropna()[LAST12:], PPY)
        res["buy_and_hold"] = bh_m
        for gate in ["own", "own+btc"]:
            for sizing in ["risk1pct", "fixed100"]:
                for cname, cost in COSTS.items():
                    eq, trades = simulate(d, gate == "own+btc", cost, sizing)
                    expo = pd.Series(0.0, index=eq.index)
                    for t in trades:
                        expo[(expo.index >= pd.Timestamp(t["entry_time"])) & (expo.index < pd.Timestamp(t["exit_time"]))] = t["notional_frac"]
                    m = metrics_from_equity(eq, trades, expo)
                    m["trades"] = trades if cname == PRIMARY[sym] else None
                    res[f"gate={gate}|size={sizing}|cost={cname}"] = m
        results["assets"][sym] = {"primary_cost": PRIMARY[sym], "variants": res}
        pc = PRIMARY[sym]
        md.append(f"\n### {sym} (primary cost {pc})\n")
        rows = []
        bm = res["buy_and_hold"]
        rows.append(["buy_and_hold", "-", fmt_pct(bm["cagr"]), fmt_pct(bm["ann_vol"]), fmt_num(bm["sharpe"]),
                     fmt_pct(bm["max_drawdown"]), "100%", "-", "-", "-", "-",
                     fmt_pct(bm["calendar_years"].get("2022")), fmt_pct(bm["calendar_years"].get("2025")),
                     fmt_pct(bm["calendar_years"].get("2026")), fmt_pct(bm["last_12m"].get("cagr")),
                     fmt_num(bm["last_12m"].get("sharpe")), fmt_pct(bm["last_12m"].get("max_drawdown")), "-", "-"])
        for gate in ["own", "own+btc"]:
            for sizing in ["risk1pct", "fixed100"]:
                m = res[f"gate={gate}|size={sizing}|cost={pc}"]
                cy = m["calendar_years"]
                l = m["last_12m"]
                rows.append([gate, sizing, fmt_pct(m["cagr"]), fmt_pct(m["ann_vol"]), fmt_num(m["sharpe"]),
                             fmt_pct(m["max_drawdown"]), fmt_pct(m["time_in_market"]), m["n_trades"],
                             fmt_pct(m["win_rate"]), fmt_num(m["profit_factor"]), fmt_num(m["avg_R"]),
                             fmt_pct(cy.get("2022")), fmt_pct(cy.get("2025")), fmt_pct(cy.get("2026")),
                             fmt_pct(l.get("cagr")), fmt_num(l.get("sharpe")), fmt_pct(l.get("max_drawdown")),
                             l.get("n_trades"), fmt_num(l.get("profit_factor"))])
        md.append(md_table(["gate", "sizing", "CAGR", "vol", "Sharpe", "MaxDD", "time in mkt", "trades", "WR", "PF",
                            "avg R", "2022", "2025", "2026 YTD", "L12M CAGR", "L12M Sharpe", "L12M MaxDD",
                            "L12M trades", "L12M PF"], rows))
        rows = []
        for gate in ["own", "own+btc"]:
            for sizing in ["risk1pct", "fixed100"]:
                rows.append([gate, sizing] + [f"{fmt_pct(res[f'gate={gate}|size={sizing}|cost={c}']['cagr'])} / "
                                              f"{fmt_num(res[f'gate={gate}|size={sizing}|cost={c}']['sharpe'])} / "
                                              f"{fmt_num(res[f'gate={gate}|size={sizing}|cost={c}']['profit_factor'])}"
                                              for c in COSTS])
        md.append("\nCost sensitivity (CAGR / Sharpe / PF):\n")
        md.append(md_table(["gate", "sizing"] + list(COSTS), rows))
        m = res[f"gate=own|size=fixed100|cost={pc}"]
        md.append(f"\nExit reasons (own gate, fixed100, {pc}): {m['exit_reasons']}; avg bars held {fmt_num(m['avg_bars'],1)}; "
                  f"avg return on notional per trade {fmt_pct(m['avg_ret_on_notional'],2)}.\n")
        years = sorted({y for v in res.values() for y in v["calendar_years"]})
        rows = [["buy_and_hold", "-"] + [fmt_pct(res["buy_and_hold"]["calendar_years"].get(y)) for y in years]]
        for gate in ["own", "own+btc"]:
            for sizing in ["risk1pct", "fixed100"]:
                cy = res[f"gate={gate}|size={sizing}|cost={pc}"]["calendar_years"]
                rows.append([gate, sizing] + [fmt_pct(cy.get(y)) for y in years])
        md.append(f"\nCalendar-year returns at {pc}:\n")
        md.append(md_table(["gate", "sizing"] + years, rows))
    dump("study2", results, "\n".join(md))
    print("\n".join(md))


if __name__ == "__main__":
    main()
