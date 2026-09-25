"""Assemble analysis/RESULTS.md and analysis/results.json from out/study{1..5}.{md,json}.
Run the study scripts first (run_all.sh does everything in order)."""
import json
import os
from common import ANALYSIS, OUT

STUDIES = {
    1: ("Daily trend following on SOL / BTC / ETH", "study1_daily_trend.py"),
    2: ("4h breakout on SOL and ETH", "study2_4h_breakout.py"),
    3: ("Funding carry (short perp / long spot)", "study3_funding_carry.py"),
    4: ("Pump.fun call base rates and a naive exit policy", "study4_pumpfun.py"),
    5: ("Cross-asset sanity", "study5_cross_asset.py"),
}

INTERP = {
1: """**Interpretation.** Every trend rule cut the maximum drawdown by roughly half or more versus buy-and-hold (SOL -96% -> -45% Donchian / -69% SMA200; BTC -77% -> -40% / -53%; ETH -90% -> -36% / -74%) and lifted the Sharpe from ~0.6-0.7 to ~1.0-1.2, almost entirely by being in cash for most of 2022 (SOL 2022: -94% B&H vs -21% / -23%). Headline CAGRs at the primary cost are SOL 58% (SMA200) and 61% (Donchian ensemble) vs 26% B&H, BTC 53% (SMA100) vs 29%, ETH 46% (SMA100) / 36% (Donchian) vs 17% - but SMA100 on SOL is poor (9% CAGR, 36 round trips of whipsaw) while SMA200 on SOL is excellent, and the ranking flips on BTC/ETH, so the lookback sensitivity is large and no single number should be read as robust. The walk-forward split is sobering: in the last 24 months (2024-09-24 -> 2026-09-23) the SOL Donchian Sharpe fell from 1.54 to 0.17 and SMA200 from 1.20 to 0.44; only SMA100 on BTC/ETH held up (1.13 / 1.18). 2025 was negative for almost every rule and asset (-4% to -17%); 2026 YTD the SMA rules are positive (+13% to +34%) because they stepped aside during the H1-2026 decline. Vol-targeting to 25% delivers what it promises (12-14% realised vol, -14% to -16% max DD, same Sharpe) but leaves 83-93% of capital idle on average (avg exposure 0.07-0.17); the CAGR shown assumes zero yield on that cash. Costs barely matter at daily frequency (30 bps vs 5 bps costs 1-3 CAGR points at 4-13x annual turnover).

**Caveats.** One realised path of 5.4 years (SOL) / 8.4 years (BTC, ETH) dominated by two or three regime changes (2021 bull, 2022 bear, 2023-24 bull), so the effective sample is a handful of trends. Trend rules under-perform buy-and-hold badly in strong up-years (SOL 2023: +183% / +324% vs +920%; 2024: -8% / +32% vs +86%). Prices are Binance spot; fills at the next daily open with a flat per-side cost ignore slippage and the spot/DEX basis for SOL. Parameters were fixed a priori but are the community-standard values, which is a mild form of selection.""",

2: """**Interpretation.** The 4h breakout is marginal. SOL with the own-asset gate, 100% notional and 30 bps per side returns 16.5% CAGR, Sharpe 0.56, profit factor 1.20, 27% win rate, max DD -59% over 90 trades - and the entire edge is 2023 (+194%); 2024, 2025 and 2026 YTD are all negative (-14%, -4%, -7%) and the last 12 months show -14% CAGR with PF 0.54. ETH at 5 bps is a bit better on paper (19% CAGR, Sharpe 0.72, PF 1.35, 100 trades) but also lost 25% in the last 12 months (PF 0.47). The 1%-risk sizing caps notional at <= 25% of equity (because the stop distance is floored at 4%) and therefore is mostly a de-leveraging: 5-6% CAGR, ~9% vol, -12% max DD, Sharpe 0.6-0.9. The BTC regime gate removes only 3 trades and nudges PF up (SOL 1.52 -> 1.62 risk-sized, ETH 1.63 -> 1.71) - helpful but not decisive. Costs are the dominant sensitivity for SOL: going from 5 to 30 bps per side cuts CAGR from 28% to 16.5% and PF from 1.40 to 1.20 (90 round trips x 60 bps = 54% of notional over 4.7 years), so on a DEX with real slippage the strategy is fragile.

**Caveats.** 90-100 trades in total (18-27 per year) gives very wide confidence intervals on PF and win rate; a single year (2023 for SOL, 2024-25 for ETH) carries the result. Binance spot 1h bars stand in for DEX/perp prices; stops are assumed filled exactly at the stop (or the open when gapped through) with only the flat cost as slippage; hourly exchange-downtime gaps are aggregated into the 4h bar that contains them. R-milestones (break-even, trail, +0.3R) use bar closes, which is conservative relative to using highs; a no-re-entry-on-exit-bar rule is a simplification.""",

3: """**Interpretation.** Hyperliquid BTC funding paid to shorts averaged 9.7% APR from 2024-11-17 to 2026-09-23 but is decaying fast: ~30% annualised in the Nov-Dec 2024 bull tail, 10.6% in 2025, 5.0% in 2026 YTD; 14% of hours were negative and the rolling-30-day APR sits at p5 / p50 / p95 = 0.5% / 8.6% / 19.2% (above 8% only 52% of the time). The 7-day > 8% rule is in the trade 55% of the time and earns 13.6% APR while in, i.e. 7.6% gross on capital, but 22 round trips at 23 bps cost 2.7% APR, leaving 4.8% net (2.1% at 46 bps) - below a flat 5% stablecoin yield. Even giving the idle capital 5% ('blended' 7.1%) the excess over just holding stables is ~2 points, before basis and execution risk. ETH and SOL on Hyperliquid (from 2025-08-19) are worse: 6.6% and 0.9% always-in, and the rule nets 0.1% and -0.8%; SOL funding was negative 34% of the time, so there is no SOL carry at all in this sample. Binance BTC 2020-2026 shows 11.8% always-in and 8.4% net for the rule, but that is concentrated in 2020-21 (17% and 31% APR); 2022-2026 ran 4%, 8%, ~15% (only 12% coverage), 5%, 2.5%, so the post-2021 carry sits at or below the 5% hurdle once costs are included.

**Caveats.** This is funding only: spot/perp basis moves, the cost of the perp margin (USDC collateral on Hyperliquid earns nothing while posted), exchange/counterparty risk, liquidation risk on the short leg during squeezes, and the 4 fills of a real two-leg entry/exit (23 bps round trip is optimistic) are not modelled. The Hyperliquid BTC sample starts at a funding peak (Nov 2024), which front-loads the average. Binance has no data for 2024-01-01 -> 2024-11-17 and 2026-08-21 -> 2026-09-13; APRs are annualised by interval count and the rolling windows straddle those gaps.""",

4: """**Interpretation.** Base rates from 8,084 smugcalls calls (2026-06-30 -> 2026-09-24): median peak 1.59x, 46% never exceed 1.5x, 35.7% reach 2x, 5.1% reach 10x, 6.4% never trade above the call price; the monthly figures are stable (34-37% reach 2x each month). Calls below $10k market cap do worse (30.5% reach 2x, 9% never trade above the call) than $10-30k calls (37.7%); the $30-100k and >$100k buckets have too few observations to say much. The naive half-at-2x / rest-at-0.65x-peak policy shows +102% expectancy per trade at 0.6% round-trip cost with a 35.7% win rate, but this is a fat-tail artefact rather than a tradeable number: the mean peak is 4.6x against a median of 1.6x, the p99 peak is 36x and the max 6,195x, the top 1% of trades supply 60% of total policy P&L (top 5%: 82%), and excluding the top 5% the expectancy drops to 19%. Capping the realisable peak at 3x / 5x / 10x gives 12% / 22% / 34% per trade and the bootstrap probability of a positive 20-trade month falls from 95% to 82% at the 3x cap. The devcabal set (188 calls) is even more tail-driven (a 700x outlier) and its own current-market-cap column is the reality check: the median call is now at 0.14x its entry cap and 71% are below 0.5x, i.e. anyone who did not sell near the peak lost most of the money.

**Caveats.** The policy is an OPTIMISTIC UPPER BOUND: peak multiple is a single number, not a path, so it assumes (i) a token that reaches 2x does so before any -30% stop is hit, (ii) the second half is filled exactly 35% below the eventual peak rather than on a gap-down or rug, (iii) sub-2x tokens lose exactly 30% when many go to ~0 in one candle, (iv) entry at the call price although the price at the time a follower can actually fill is usually already higher, and (v) costs of 30 bps per side when pump.fun / DEX / priority fees plus slippage are usually well above 1% per side (the 2% round-trip sensitivity barely moves the mean because the mean is tail-driven). Both datasets are calls posted in a Telegram channel (selection bias in what gets posted and survivorship of channels that post), cover ~3-5 months in a single regime, and are not a sample of all launches; a realistic follower expectancy is plausibly near zero or negative.""",

5: """**Interpretation.** Daily return correlations in 2024-01-01 -> 2026-09-23 are high: SOL/BTC 0.79, SOL/ETH 0.77, BTC/ETH 0.82, and rising (2026 YTD 0.88-0.91); the rolling-90-day SOL/BTC correlation never fell below 0.61. SOL has a beta of 1.31 to BTC and 80% annualised vol versus 47% for BTC, so it behaves like a leveraged BTC position with extra idiosyncratic risk rather than a diversifier - trend or breakout strategies run on all three will be highly correlated with each other. SOL's full-history max drawdown is -96% (2021-11-06 -> 2022-12-29, 258 -> 9.6), and even within 2024-2026 it is -76% (2025-01-18 -> 2026-06-06, 262 -> 62); the worst calendar month was -56.5% (Nov 2022), the worst in 2024-26 -37.4% (Apr 2024), and the worst 30-day window in 2024-26 was -46% ending 2026-02-12. Any un-hedged SOL strategy has to be sized for a 40-50% monthly hit.

**Caveats.** Close-to-close Binance spot returns; correlations are regime-dependent (they rise in sell-offs), so the average understates crash-time co-movement.""",
}

HEADLINE_NOTE = """All studies: offline, deterministic, Binance spot OHLCV (USDT), signals on closed bars, fills at the next bar open unless stated. Costs are charged per side on turnover: 30 bps for Solana DEX spot (primary for SOL), 5 bps for perps/CEX (primary for BTC, ETH), 10 bps for reference. No leverage, long-only (study 3 is delta-neutral carry). Annualisation: 365 days, 2190 4h-bars, 8760 hourly / 1095 8-hourly funding intervals per year. Sharpe uses 0% risk-free. Nothing was optimised; every parameter was fixed in advance as specified in the task."""


def main():
    md = ["# Offline backtest results (data through 2026-09-23)\n", HEADLINE_NOTE, ""]
    js = {"generated_from": "analysis/study{1..5}_*.py", "data_end": "2026-09-23", "notes": HEADLINE_NOTE, "studies": {}}
    for n, (title, script) in STUDIES.items():
        with open(os.path.join(OUT, f"study{n}.md")) as f:
            table_md = f.read()
        with open(os.path.join(OUT, f"study{n}.json")) as f:
            j = json.load(f)
        md.append(f"\n## Study {n}: {title}\n")
        md.append(f"Script: `analysis/{script}`\n")
        desc = j.get("description", "").strip()
        if desc:
            md.append("Method:\n\n```\n" + desc + "\n```\n")
        md.append(table_md)
        md.append("\n" + INTERP[n] + "\n")
        j["interpretation"] = INTERP[n]
        js["studies"][f"study{n}"] = j
    with open(os.path.join(ANALYSIS, "RESULTS.md"), "w") as f:
        f.write("\n".join(md))
    with open(os.path.join(ANALYSIS, "results.json"), "w") as f:
        json.dump(js, f, indent=1)
    print("wrote RESULTS.md and results.json")


if __name__ == "__main__":
    main()
