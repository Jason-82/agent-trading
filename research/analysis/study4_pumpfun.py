"""Study 4 - pump.fun base rates from two Telegram *call* datasets.

Datasets: smugcalls (8,084 calls, 2026-06-30 -> 2026-09-24) and nikolan17/devcabal (188 calls, 2026-04-30 -> 2026-09-24).
Both are calls posted in a Telegram channel, NOT all launches -> heavy selection bias (the channel
chooses what to post, and channels that survive tend to be the ones whose calls did well).
peak_multiple_after_call = max price after the call / price at call. It is a single number, NOT a price path.

Naive exit policy (OPTIMISTIC UPPER BOUND):
  if peak >= 2x : sell half at 2x, the other half at 0.65 x peak (proxy for a 35% trailing stop from the peak)
  else          : sold at -30% (stop)
This is optimistic because (i) a token whose peak is < 2x is assumed to lose exactly 30%, but many go to ~0 in a
single candle / rug; (ii) with peak >= 2x the path is assumed to hit 2x BEFORE any -30% stop; (iii) 0.65 x peak
assumes a fill at exactly 35% below the top, ignoring the gap-downs typical of these tokens; (iv) execution at the
call price ignores the front-running that follows a call. Costs: 30 bps/side as specified (=0.6% round trip) and a
2% round-trip sensitivity (pump.fun/DEX fees + priority fees + slippage are typically >1% per side).
Probability of a positive month with 20 trades: bootstrap (20,000 draws of 20 trades, seed 42) from the empirical
policy-return distribution, and empirically from the actual calendar months.
"""
import os
import numpy as np
import pandas as pd
from common import DATA, dump, md_table, fmt_pct, fmt_num

MEME = os.path.join(DATA, "memecoin")
PEAK_BINS = [(0, 1.0, "<1.0"), (1.0, 1.5, "1.0-1.5"), (1.5, 2.0, "1.5-2"), (2.0, 3.0, "2-3"), (3.0, 5.0, "3-5"),
             (5.0, 10.0, "5-10"), (10.0, np.inf, ">10")]
MC_BINS = [(0, 10e3, "<10k"), (10e3, 30e3, "10-30k"), (30e3, 100e3, "30-100k"), (100e3, np.inf, ">100k")]
ROUND_TRIP_COSTS = {"0.6pct_rt (30bps/side)": 0.006, "2.0pct_rt (1%/side)": 0.02}
SEED = 42
N_TRADES_MONTH = 20
N_BOOT = 20000


def load_smug():
    df = pd.read_csv(os.path.join(MEME, "pumpfun_calls_smugcalls.csv"))
    df["ts"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    df = df.rename(columns={"market_cap_usd_at_call": "mc", "peak_multiple_after_call": "peak"})
    return df[["ts", "mint", "symbol", "mc", "peak"]]


def load_niko():
    df = pd.read_csv(os.path.join(MEME, "pumpfun_calls_nikolan17.csv"))
    df["ts"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    df["mc"] = df["entry_mc"].astype(float)
    df["peak"] = df["peak_mc"].astype(float) / df["entry_mc"].astype(float)
    df["now_mult"] = df["now_mc"].astype(float) / df["entry_mc"].astype(float)
    return df[["ts", "mint", "symbol", "mc", "peak", "now_mult", "minutes_to_peak"]]


def bucket(v, bins):
    for lo, hi, name in bins:
        if lo <= v < hi:
            return name
    return "n/a"


def dist(peak):
    b = peak.apply(lambda v: bucket(v, PEAK_BINS))
    order = [n for _, _, n in PEAK_BINS]
    c = b.value_counts().reindex(order).fillna(0)
    return {n: {"count": int(c[n]), "share": float(c[n] / len(peak))} for n in order}


def policy_multiple(peak):
    return np.where(peak >= 2.0, 0.5 * 2.0 + 0.5 * 0.65 * peak, 0.7)


def policy_stats(peak, rt_cost, rng):
    gross = policy_multiple(peak.values)
    net = gross * (1 - rt_cost) - 1.0  # return per unit staked, net of the round-trip cost
    exp = float(net.mean())
    boot = rng.choice(net, size=(N_BOOT, N_TRADES_MONTH), replace=True).sum(axis=1)
    return {"expectancy_per_trade": exp, "median_trade": float(np.median(net)), "std_trade": float(net.std(ddof=1)),
            "win_rate": float((net > 0).mean()), "p_positive_month_20_trades_bootstrap": float((boot > 0).mean()),
            "p5_month_return_20_trades": float(np.quantile(boot, 0.05) / N_TRADES_MONTH),
            "p50_month_return_20_trades": float(np.quantile(boot, 0.5) / N_TRADES_MONTH),
            "p95_month_return_20_trades": float(np.quantile(boot, 0.95) / N_TRADES_MONTH)}, net


def analyse(df, name):
    out = {"name": name, "n": int(len(df)), "start": str(df["ts"].min()), "end": str(df["ts"].max())}
    out["mc_zero_rows"] = int((df["mc"] <= 0).sum())
    out["peak_summary"] = {"median": float(df["peak"].median()), "mean": float(df["peak"].mean()),
                           "share_peak_le_1": float((df["peak"] <= 1.0).mean()),
                           "share_ge_2x": float((df["peak"] >= 2).mean()), "share_ge_10x": float((df["peak"] >= 10).mean())}
    out["peak_distribution"] = dist(df["peak"])
    out["by_mcap_bucket"] = {}
    df = df.copy()
    df["mcb"] = df["mc"].apply(lambda v: bucket(v, MC_BINS))
    for _, _, b in MC_BINS:
        sub = df[df["mcb"] == b]
        if len(sub) == 0:
            continue
        out["by_mcap_bucket"][b] = {"n": int(len(sub)), "median_peak": float(sub["peak"].median()),
                                    "share_ge_2x": float((sub["peak"] >= 2).mean()),
                                    "share_ge_5x": float((sub["peak"] >= 5).mean()),
                                    "share_ge_10x": float((sub["peak"] >= 10).mean()),
                                    "share_peak_le_1": float((sub["peak"] <= 1).mean()),
                                    "distribution": dist(sub["peak"])}
    df["month"] = df["ts"].dt.strftime("%Y-%m")
    out["by_month"] = {}
    for m, sub in df.groupby("month"):
        out["by_month"][m] = {"n": int(len(sub)), "median_peak": float(sub["peak"].median()),
                              "share_ge_2x": float((sub["peak"] >= 2).mean()),
                              "share_ge_10x": float((sub["peak"] >= 10).mean()),
                              "share_peak_le_1": float((sub["peak"] <= 1).mean())}
    rng = np.random.default_rng(SEED)
    out["policy"] = {}
    for cname, c in ROUND_TRIP_COSTS.items():
        st, net = policy_stats(df["peak"], c, rng)
        # empirical months: equal stake per trade, month P&L = sum of net returns
        df["net"] = net
        mon = df.groupby("month")["net"].agg(["sum", "count", "mean"])
        full = mon[mon["count"] >= N_TRADES_MONTH]
        st["empirical_months"] = {m: {"n": int(r["count"]), "mean_per_trade": float(r["mean"])} for m, r in mon.iterrows()}
        st["empirical_frac_months_positive"] = float((full["sum"] > 0).mean()) if len(full) else None
        # sequential blocks of 20 trades in time order
        blocks = [net[i:i + N_TRADES_MONTH].sum() for i in range(0, len(net) - N_TRADES_MONTH + 1, N_TRADES_MONTH)]
        st["sequential_20_trade_blocks"] = {"n_blocks": len(blocks), "frac_positive": float(np.mean(np.array(blocks) > 0)),
                                            "mean_block_return_per_trade": float(np.mean(blocks) / N_TRADES_MONTH)}
        out["policy"][cname] = st
    # gross policy multiple without cost, for reference
    gross = policy_multiple(df["peak"].values)
    out["policy_gross_mean_multiple"] = float(gross.mean())
    out["peak_mean"] = float(df["peak"].mean())
    out["peak_max"] = float(df["peak"].max())
    out["peak_p99"] = float(df["peak"].quantile(0.99))
    # how much of the policy P&L comes from the top 1% / 5% of trades (tail dependence)
    net0 = gross * (1 - 0.006) - 1.0
    srt = np.sort(net0)[::-1]
    k1, k5 = max(1, int(round(0.01 * len(srt)))), max(1, int(round(0.05 * len(srt))))
    tot = net0.sum()
    out["tail_dependence"] = {"share_of_total_pnl_from_top_1pct_trades": float(srt[:k1].sum() / tot) if tot > 0 else None,
                             "share_of_total_pnl_from_top_5pct_trades": float(srt[:k5].sum() / tot) if tot > 0 else None,
                             "expectancy_excluding_top_1pct": float(srt[k1:].mean()),
                             "expectancy_excluding_top_5pct": float(srt[k5:].mean())}
    # capped-peak sensitivity: assume no trade can realise more than a cap (proxy for exit realism)
    out["policy_capped_peak"] = {}
    for cap in [3.0, 5.0, 10.0, 20.0]:
        rng_c = np.random.default_rng(SEED)
        st_c, _ = policy_stats(df["peak"].clip(upper=cap), 0.006, rng_c)
        out["policy_capped_peak"][f"cap_{int(cap)}x"] = {k: st_c[k] for k in ["expectancy_per_trade", "p_positive_month_20_trades_bootstrap",
                                                                            "p5_month_return_20_trades", "p50_month_return_20_trades"]}
    if "now_mult" in df:
        out["hold_to_now"] = {"mean_multiple": float(df["now_mult"].mean()), "median_multiple": float(df["now_mult"].median()),
                              "share_below_0.5x": float((df["now_mult"] < 0.5).mean()),
                              "median_minutes_to_peak": float(df["minutes_to_peak"].median())}
    return out


def main():
    results = {"description": __doc__, "datasets": {}}
    md = ["Peak multiple = max price after call / call price (not a path). Policy = sell half at 2x, rest at 0.65 x peak; "
          "<2x tokens sold at -30%. OPTIMISTIC UPPER BOUND (see caveats).\n"]
    for name, df in [("smugcalls", load_smug()), ("nikolan17_devcabal", load_niko())]:
        res = analyse(df, name)
        results["datasets"][name] = res
        md.append(f"\n### {name}: n={res['n']}, {res['start'][:10]} -> {res['end'][:10]}, median peak {fmt_num(res['peak_summary']['median'])}x, "
                  f"share >=2x {fmt_pct(res['peak_summary']['share_ge_2x'])}, share >=10x {fmt_pct(res['peak_summary']['share_ge_10x'])}, "
                  f"share never above call price (peak<=1.0) {fmt_pct(res['peak_summary']['share_peak_le_1'])}\n")
        pd_ = res["peak_distribution"]
        md.append(md_table(["peak bucket"] + list(pd_), [["share"] + [fmt_pct(v["share"]) for v in pd_.values()],
                                                          ["count"] + [v["count"] for v in pd_.values()]]))
        md.append("\nBy market cap at call:\n")
        rows = [[b, v["n"], fmt_num(v["median_peak"]), fmt_pct(v["share_peak_le_1"]), fmt_pct(v["share_ge_2x"]),
                 fmt_pct(v["share_ge_5x"]), fmt_pct(v["share_ge_10x"])] for b, v in res["by_mcap_bucket"].items()]
        md.append(md_table(["mcap bucket", "n", "median peak", "peak<=1", ">=2x", ">=5x", ">=10x"], rows))
        md.append("\nBy month:\n")
        rows = [[m, v["n"], fmt_num(v["median_peak"]), fmt_pct(v["share_peak_le_1"]), fmt_pct(v["share_ge_2x"]), fmt_pct(v["share_ge_10x"])]
                for m, v in res["by_month"].items()]
        md.append(md_table(["month", "n", "median peak", "peak<=1", ">=2x", ">=10x"], rows))
        md.append("\nNaive exit policy (optimistic upper bound):\n")
        rows = []
        for c, st in res["policy"].items():
            rows.append([c, fmt_pct(st["expectancy_per_trade"]), fmt_pct(st["median_trade"]), fmt_pct(st["win_rate"]),
                         fmt_pct(st["p_positive_month_20_trades_bootstrap"]), fmt_pct(st["p5_month_return_20_trades"]),
                         fmt_pct(st["p95_month_return_20_trades"]),
                         fmt_pct(st["empirical_frac_months_positive"]) if st["empirical_frac_months_positive"] is not None else "n/a",
                         f"{fmt_pct(st['sequential_20_trade_blocks']['frac_positive'])} ({st['sequential_20_trade_blocks']['n_blocks']} blocks)"])
        md.append(md_table(["round-trip cost", "expectancy/trade", "median trade", "win rate", "P(month>0) boot 20 trades",
                            "p5 month/trade", "p95 month/trade", "calendar months >0", "seq. 20-trade blocks >0"], rows))
        td = res["tail_dependence"]
        md.append(f"\nTail dependence (0.6% cost): mean peak {fmt_num(res['peak_mean'])}x, p99 peak {fmt_num(res['peak_p99'])}x, max peak {fmt_num(res['peak_max'],0)}x; "
                  f"top 1% of trades supply {fmt_pct(td['share_of_total_pnl_from_top_1pct_trades'])} of total policy P&L (top 5%: "
                  f"{fmt_pct(td['share_of_total_pnl_from_top_5pct_trades'])}); expectancy excluding the top 1% = {fmt_pct(td['expectancy_excluding_top_1pct'])}, "
                  f"excluding the top 5% = {fmt_pct(td['expectancy_excluding_top_5pct'])}.\n")
        md.append("Capped-peak sensitivity (0.6% cost; no trade can realise more than the cap):\n")
        md.append(md_table(["cap", "expectancy/trade", "P(month>0) 20 trades", "p5 month/trade", "p50 month/trade"],
                           [[c, fmt_pct(v["expectancy_per_trade"]), fmt_pct(v["p_positive_month_20_trades_bootstrap"]),
                             fmt_pct(v["p5_month_return_20_trades"]), fmt_pct(v["p50_month_return_20_trades"])]
                            for c, v in res["policy_capped_peak"].items()]))
        if "hold_to_now" in res:
            h = res["hold_to_now"]
            md.append(f"\nReality check (this dataset also has current mcap): hold-to-now mean multiple {fmt_num(h['mean_multiple'])}x, "
                      f"median {fmt_num(h['median_multiple'])}x, {fmt_pct(h['share_below_0.5x'])} of calls now below 0.5x; "
                      f"median minutes to peak {fmt_num(h['median_minutes_to_peak'],0)}.\n")
    dump("study4", results, "\n".join(md))
    print("\n".join(md))


if __name__ == "__main__":
    main()
