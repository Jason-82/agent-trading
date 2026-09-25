"""Study 5 - cross-asset sanity: daily close-to-close return correlations SOL/BTC/ETH (2024-01-01 -> 2026-09-23),
SOL max drawdown (full history and 2024+) and worst calendar month / worst 30-day window."""
import numpy as np
import pandas as pd
from common import load_ohlcv, dump, md_table, fmt_pct, fmt_num

START = pd.Timestamp("2024-01-01", tz="UTC")


def main():
    closes = pd.concat({s: load_ohlcv(s, "1d")["close"] for s in ["SOLUSDT", "BTCUSDT", "ETHUSDT"]}, axis=1, sort=True)
    rets = closes.pct_change().dropna()
    r = rets[rets.index >= START]
    corr = r.corr()
    by_year = {str(y): g.corr().round(3).to_dict() for y, g in r.groupby(r.index.year)}
    # rolling 90d SOL/BTC correlation range
    roll = r["SOLUSDT"].rolling(90).corr(r["BTCUSDT"]).dropna()
    sol = closes["SOLUSDT"].dropna()

    def mdd_info(px):
        dd = px / px.cummax() - 1
        trough = dd.idxmin()
        peak = px[:trough].idxmax()
        return {"max_drawdown": float(dd.min()), "peak_date": str(peak.date()), "trough_date": str(trough.date()),
                "peak_px": float(px[peak]), "trough_px": float(px[trough])}

    sol_full = mdd_info(sol)
    sol_24 = mdd_info(sol[sol.index >= START])
    monthly = (1 + rets["SOLUSDT"]).groupby(rets["SOLUSDT"].index.tz_localize(None).to_period("M")).prod() - 1
    worst_m = monthly.idxmin()
    worst_m24 = monthly[monthly.index >= "2024-01"].idxmin()
    r30 = sol.pct_change(30).dropna()
    beta = {a: float(np.cov(r["SOLUSDT"], r[a])[0, 1] / np.var(r[a], ddof=1)) for a in ["BTCUSDT", "ETHUSDT"]}
    results = {
        "description": __doc__,
        "window": {"start": str(r.index[0].date()), "end": str(r.index[-1].date()), "n_days": int(len(r))},
        "corr_daily_2024_2026": corr.round(3).to_dict(),
        "corr_by_year": by_year,
        "sol_btc_rolling90_corr": {"min": float(roll.min()), "median": float(roll.median()), "max": float(roll.max())},
        "sol_beta_2024_2026": beta,
        "ann_vol_2024_2026": {a: float(r[a].std(ddof=1) * np.sqrt(365)) for a in r.columns},
        "sol_max_drawdown_full_history": sol_full,
        "sol_max_drawdown_2024_2026": sol_24,
        "sol_worst_month_full": {"month": str(worst_m), "return": float(monthly.min())},
        "sol_worst_month_2024_2026": {"month": str(worst_m24), "return": float(monthly[monthly.index >= "2024-01"].min())},
        "sol_worst_30d_full": {"end_date": str(r30.idxmin().date()), "return": float(r30.min())},
        "sol_worst_30d_2024_2026": {"end_date": str(r30[r30.index >= START].idxmin().date()), "return": float(r30[r30.index >= START].min())},
        "sol_worst_months_top5": {str(k): float(v) for k, v in monthly.nsmallest(5).items()},
    }
    md = [f"Daily close-to-close return correlations, {results['window']['start']} -> {results['window']['end']} ({len(r)} days):\n"]
    md.append(md_table([""] + list(corr.columns), [[i] + [fmt_num(corr.loc[i, j], 3) for j in corr.columns] for i in corr.index]))
    md.append("\nBy year (SOL/BTC, SOL/ETH, BTC/ETH):\n")
    md.append(md_table(["year", "SOL/BTC", "SOL/ETH", "BTC/ETH"],
                       [[y, fmt_num(v["SOLUSDT"]["BTCUSDT"], 3), fmt_num(v["SOLUSDT"]["ETHUSDT"], 3), fmt_num(v["BTCUSDT"]["ETHUSDT"], 3)]
                        for y, v in by_year.items()]))
    md.append(f"\nSOL rolling-90d corr with BTC: min {fmt_num(roll.min())}, median {fmt_num(roll.median())}, max {fmt_num(roll.max())}. "
              f"SOL beta to BTC {fmt_num(beta['BTCUSDT'])}, to ETH {fmt_num(beta['ETHUSDT'])}. Ann. vol 2024-26: "
              + ", ".join(f"{a} {fmt_pct(v)}" for a, v in results["ann_vol_2024_2026"].items()) + ".\n")
    md.append(md_table(["SOL drawdown stat", "value", "dates"], [
        ["max DD full history (2020-08+)", fmt_pct(sol_full["max_drawdown"]), f"{sol_full['peak_date']} -> {sol_full['trough_date']} ({sol_full['peak_px']:.1f} -> {sol_full['trough_px']:.1f})"],
        ["max DD 2024-2026", fmt_pct(sol_24["max_drawdown"]), f"{sol_24['peak_date']} -> {sol_24['trough_date']} ({sol_24['peak_px']:.1f} -> {sol_24['trough_px']:.1f})"],
        ["worst calendar month full", fmt_pct(results["sol_worst_month_full"]["return"]), results["sol_worst_month_full"]["month"]],
        ["worst calendar month 2024-26", fmt_pct(results["sol_worst_month_2024_2026"]["return"]), results["sol_worst_month_2024_2026"]["month"]],
        ["worst 30-day window full", fmt_pct(results["sol_worst_30d_full"]["return"]), "ending " + results["sol_worst_30d_full"]["end_date"]],
        ["worst 30-day window 2024-26", fmt_pct(results["sol_worst_30d_2024_2026"]["return"]), "ending " + results["sol_worst_30d_2024_2026"]["end_date"]],
    ]))
    md.append("\nSOL five worst months: " + ", ".join(f"{k} {fmt_pct(v)}" for k, v in results["sol_worst_months_top5"].items()))
    dump("study5", results, "\n".join(md))
    print("\n".join(md))


if __name__ == "__main__":
    main()
