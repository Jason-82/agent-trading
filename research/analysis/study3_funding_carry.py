"""Study 3 - funding carry (short perp / long spot collects funding when positive).

Hyperliquid hourly funding (BTC 2024-11-17 -> 2026-09-23, ETH/SOL 2025-08-19 -> 2026-09-23) and
Binance 8h funding (BTC 2020-01 -> 2026-09 with gaps; ETH/SOL Binance shown for reference).

Definitions
* realized annualized funding to shorts = sum(rate) * (intervals_per_year / n_intervals)  [interval-count based,
  so gaps in the Binance series do not distort the annualization; coverage is reported].
* rolling 30-day annualized funding = rolling sum over 30 days of intervals * (intervals_per_year / intervals_per_30d).
* rule: at the end of interval t compute the trailing 7-day MEAN rate (annualized); if > 8% hold the carry
  position during interval t+1 and collect rate[t+1] (no look-ahead). Each round trip (entry + exit) costs
  23 bps (primary); 46 bps sensitivity (= 23 bps per toggle). Price basis / spot-perp PnL is ignored (a
  delta-neutral carry only earns funding), which is optimistic: basis slippage and liquidation risk are not modelled.
* comparison: flat 5% stablecoin yield. 'blended' = rule APR + 5% x (fraction of time idle),
  i.e. capital sits in stables at 5% when the rule is out of the trade.
"""
import os
import numpy as np
import pandas as pd
from common import DATA, END_DATE, dump, md_table, fmt_pct, fmt_num

FUND = os.path.join(DATA, "funding")
SERIES = {
    "hyperliquid": {"files": {"BTC": "BTC_funding_hyperliquid_1h_MERGED.csv",
                              "ETH": "ETH_funding_hyperliquid_1h_MERGED.csv",
                              "SOL": "SOL_funding_hyperliquid_1h_MERGED.csv"},
                    "ipy": 24 * 365, "per_day": 24},
    "binance": {"files": {"BTC": "BTCUSDT_funding_binance_8h_MERGED.csv",
                          "ETH": "ETHUSDT_funding_binance_8h_MERGED.csv",
                          "SOL": "SOLUSDT_funding_binance_8h_MERGED.csv"},
                "ipy": 3 * 365, "per_day": 3},
}
THRESH = 0.08
STABLE = 0.05
RT_COSTS = {"23bps_per_roundtrip": 0.0023, "46bps_per_roundtrip": 0.0046}


def load(fn):
    df = pd.read_csv(os.path.join(FUND, fn))
    df["ts"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    df = df.set_index("ts").sort_index()
    df = df[df.index <= END_DATE]
    return df["funding_rate"].astype(float)


def gaps(s, hours):
    d = s.index.to_series().diff().dt.total_seconds() / 3600
    g = d[d > hours * 1.5]
    return [(str((i - pd.Timedelta(hours=v)).date()), str(i.date()), float(v / 24)) for i, v in g.items()]


def analyse(rate, ipy, per_day, venue, sym):
    n = len(rate)
    out = {"venue": venue, "symbol": sym, "start": str(rate.index[0]), "end": str(rate.index[-1]),
           "n_intervals": int(n), "years_covered": n / ipy, "gaps_days": gaps(rate, 24 / per_day)}
    out["realized_apr_to_shorts"] = float(rate.sum() * ipy / n)
    out["mean_rate_per_interval"] = float(rate.mean())
    out["frac_intervals_negative"] = float((rate < 0).mean())
    out["frac_intervals_positive"] = float((rate > 0).mean())
    # by calendar year (interval-count annualized within the year)
    by_year = rate.groupby(rate.index.year).agg(["sum", "count", lambda x: (x < 0).mean()])
    out["by_year"] = {str(y): {"apr": float(r["sum"] * ipy / r["count"]), "n": int(r["count"]),
                               "coverage": float(r["count"] / ipy), "frac_negative": float(r["<lambda_0>"])}
                      for y, r in by_year.iterrows()}
    w30 = 30 * per_day
    roll30 = rate.rolling(w30).sum() * ipy / w30
    out["rolling_30d_apr"] = {"p5": float(roll30.quantile(0.05)), "p25": float(roll30.quantile(0.25)),
                              "p50": float(roll30.quantile(0.5)), "p75": float(roll30.quantile(0.75)),
                              "p95": float(roll30.quantile(0.95)), "mean": float(roll30.mean()),
                              "frac_above_8pct": float((roll30 > THRESH).mean()),
                              "frac_above_5pct": float((roll30 > STABLE).mean())}
    # rule: trailing 7d mean annualized > 8% -> hold next interval
    w7 = 7 * per_day
    sig = (rate.rolling(w7).mean() * ipy > THRESH).astype(float)
    pos = sig.shift(1).fillna(0.0)  # decided at end of t, applied to t+1
    collected = (pos * rate).sum()
    entries = int(((pos == 1) & (pos.shift(1).fillna(0) == 0)).sum())
    time_in = float(pos.mean())
    rule = {"threshold_apr": THRESH, "time_in_trade": time_in, "round_trips": entries,
            "gross_apr": float(collected * ipy / n),
            "gross_apr_while_in_trade": float(collected / max(pos.sum(), 1) * ipy)}
    for cname, rc in RT_COSTS.items():
        net = collected - rc * entries
        apr = float(net * ipy / n)
        rule[cname] = {"net_apr": apr, "cost_drag_apr": float(rc * entries * ipy / n),
                       "blended_with_5pct_idle": apr + STABLE * (1 - time_in),
                       "excess_over_5pct_flat": apr - STABLE,
                       "blended_excess_over_5pct": apr + STABLE * (1 - time_in) - STABLE}
    out["rule_7d_gt_8pct"] = rule
    out["always_in_apr_minus_5pct"] = out["realized_apr_to_shorts"] - STABLE
    return out


def main():
    results = {"description": __doc__, "series": {}}
    md = ["APRs are annualized by interval count (Hyperliquid 8760 x 1h, Binance 1095 x 8h per year). "
          "'always-in' = collect every interval. Rule = trailing 7-day mean annualized funding > 8% -> in the trade next "
          "interval; net of 23 bps per round trip (46 bps sensitivity). Blended = rule APR + 5% on idle time.\n"]
    rows = []
    for venue, cfg in SERIES.items():
        for sym, fn in cfg["files"].items():
            rate = load(fn)
            res = analyse(rate, cfg["ipy"], cfg["per_day"], venue, sym)
            results["series"][f"{venue}_{sym}"] = res
            r30 = res["rolling_30d_apr"]
            rule = res["rule_7d_gt_8pct"]
            rows.append([venue, sym, res["start"][:10], res["end"][:10], fmt_num(res["years_covered"], 2),
                         fmt_pct(res["realized_apr_to_shorts"], 2), fmt_pct(res["frac_intervals_negative"]),
                         fmt_pct(r30["p5"]), fmt_pct(r30["p50"]), fmt_pct(r30["p95"]), fmt_pct(r30["frac_above_8pct"]),
                         fmt_pct(rule["time_in_trade"]), rule["round_trips"], fmt_pct(rule["gross_apr"], 2),
                         fmt_pct(rule["23bps_per_roundtrip"]["net_apr"], 2),
                         fmt_pct(rule["46bps_per_roundtrip"]["net_apr"], 2),
                         fmt_pct(rule["23bps_per_roundtrip"]["blended_with_5pct_idle"], 2),
                         fmt_pct(rule["gross_apr_while_in_trade"], 1)])
    md.append(md_table(["venue", "sym", "start", "end", "yrs", "always-in APR", "% neg", "30d p5", "30d p50", "30d p95",
                        "30d>8%", "rule time-in", "rule RTs", "rule gross APR", "rule net (23bps)", "rule net (46bps)",
                        "blended (5% idle)", "APR while in"], rows))
    md.append("\nBinance BTC by calendar year (coverage = fraction of the year's 8h intervals present):\n")
    by = results["series"]["binance_BTC"]["by_year"]
    md.append(md_table(["year", "APR to shorts", "% neg", "coverage"],
                       [[y, fmt_pct(v["apr"], 2), fmt_pct(v["frac_negative"]), fmt_pct(v["coverage"])] for y, v in by.items()]))
    md.append("\nBinance BTC gaps (days): " + str([(a, b, round(c, 1)) for a, b, c in results["series"]["binance_BTC"]["gaps_days"]]))
    md.append("\nHyperliquid by calendar year:\n")
    rows = []
    for sym in ["BTC", "ETH", "SOL"]:
        for y, v in results["series"][f"hyperliquid_{sym}"]["by_year"].items():
            rows.append([sym, y, fmt_pct(v["apr"], 2), fmt_pct(v["frac_negative"]), fmt_pct(v["coverage"])])
    md.append(md_table(["sym", "year", "APR to shorts", "% neg", "coverage"], rows))
    dump("study3", results, "\n".join(md))
    print("\n".join(md))


if __name__ == "__main__":
    main()
