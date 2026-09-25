# Offline backtest results (data through 2026-09-23)

All studies: offline, deterministic, Binance spot OHLCV (USDT), signals on closed bars, fills at the next bar open unless stated. Costs are charged per side on turnover: 30 bps for Solana DEX spot (primary for SOL), 5 bps for perps/CEX (primary for BTC, ETH), 10 bps for reference. No leverage, long-only (study 3 is delta-neutral carry). Annualisation: 365 days, 2190 4h-bars, 8760 hourly / 1095 8-hourly funding intervals per year. Sharpe uses 0% risk-free. Nothing was optimised; every parameter was fixed in advance as specified in the task.


## Study 1: Daily trend following on SOL / BTC / ETH

Script: `analysis/study1_daily_trend.py`

Method:

```
Study 1 - daily trend following on SOL / BTC / ETH (long-only, no leverage).

Execution: signal on the daily CLOSE of bar t, filled at the OPEN of bar t+1.
Costs: charged per side on |change in exposure|; reported at 5 / 10 / 30 bps.
Primary cost: SOL 30 bps (Solana DEX spot), BTC/ETH 5 bps (perp / CEX spot).
Warm-up: the first 250 daily bars per asset are excluded from evaluation so that
every rule (max lookback 250) and buy-and-hold share the same window.
Parameters are fixed a priori (no optimisation); the walk-forward check simply
reports the window up to 2024-09-23 ("in-sample") and the last 24 months
(2024-09-24 -> 2026-09-23) separately.
```

Execution: signal at daily close t, fill at open t+1; costs per side on |change in exposure|. Evaluation starts after a 250-bar warm-up (SOL 2021-04-18, BTC/ETH 2018-04-24) and ends 2026-09-23. Walk-forward: 'IS' = eval start -> 2024-09-23, 'last 24m' = 2024-09-24 -> 2026-09-23 (parameters fixed a priori, nothing optimised).


### SOLUSDT (primary cost 30bps; window 2021-04-18 -> 2026-09-23)

| rule | CAGR | vol | Sharpe | MaxDD | time in mkt | avg exp | round trips | turnover/yr | 2022 | 2025 | 2026 YTD | IS Sharpe | IS CAGR | 24m Sharpe | 24m CAGR | 24m MaxDD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| buy_and_hold | 26.2% | 102.6% | 0.74 | -96.3% | 100.0% | 1.00 | 1 | 0.2 | -94.1% | -34.2% | -7.7% | 0.96 | 54.6% | 0.23 | -10.9% | -76.3% |
| sma200_hyst2pct | 57.9% | 75.3% | 0.98 | -69.1% | 48.4% | 0.48 | 16 | 5.7 | -20.7% | -17.4% | 34.3% | 1.20 | 94.9% | 0.44 | 9.9% | -40.8% |
| sma100_hyst2pct | 9.0% | 73.3% | 0.48 | -87.0% | 48.2% | 0.48 | 36 | 13.1 | -42.8% | -7.0% | 14.1% | 0.58 | 14.8% | 0.24 | -0.3% | -50.2% |
| donchian_ensemble | 60.8% | 50.3% | 1.20 | -45.2% | 53.9% | 0.27 | 161 | 7.8 | -23.1% | -10.7% | 7.2% | 1.54 | 110.5% | 0.17 | 1.3% | -35.6% |
| donchian_ensemble_voltarget25 | 14.8% | 12.1% | 1.20 | -14.2% | 53.9% | 0.07 | 161 | 2.4 | -6.0% | -3.6% | 4.5% | 1.60 | 22.8% | 0.27 | 2.1% | -14.2% |

Cost sensitivity (CAGR / Sharpe):

| rule | 5bps | 10bps | 30bps |
|---|---|---|---|
| buy_and_hold | 26.3% / 0.74 | 26.3% / 0.74 | 26.2% / 0.74 |
| sma200_hyst2pct | 60.2% / 1.00 | 59.7% / 1.00 | 57.9% / 0.98 |
| sma100_hyst2pct | 12.6% / 0.53 | 11.9% / 0.52 | 9.0% / 0.48 |
| donchian_ensemble | 64.0% / 1.23 | 63.3% / 1.23 | 60.8% / 1.20 |
| donchian_ensemble_voltarget25 | 15.5% / 1.25 | 15.3% / 1.24 | 14.8% / 1.20 |

Calendar-year returns at 30bps:

| rule | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|
| buy_and_hold | 424.2% | -94.1% | 920.3% | 86.1% | -34.2% | -7.7% |
| sma200_hyst2pct | 424.2% | -20.7% | 182.8% | -8.2% | -17.4% | 34.3% |
| sma100_hyst2pct | 102.5% | -42.8% | 64.6% | -21.2% | -7.0% | 14.1% |
| donchian_ensemble | 220.3% | -23.1% | 323.6% | 32.3% | -10.7% | 7.2% |
| donchian_ensemble_voltarget25 | 27.5% | -6.0% | 57.6% | 11.2% | -3.6% | 4.5% |

### BTCUSDT (primary cost 5bps; window 2018-04-24 -> 2026-09-23)

| rule | CAGR | vol | Sharpe | MaxDD | time in mkt | avg exp | round trips | turnover/yr | 2022 | 2025 | 2026 YTD | IS Sharpe | IS CAGR | 24m Sharpe | 24m CAGR | 24m MaxDD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| buy_and_hold | 29.4% | 61.8% | 0.73 | -76.6% | 100.0% | 1.00 | 1 | 0.1 | -64.2% | -6.3% | -3.7% | 0.78 | 34.1% | 0.54 | 15.4% | -53.0% |
| sma200_hyst2pct | 39.5% | 42.9% | 0.99 | -53.4% | 52.9% | 0.53 | 17 | 3.7 | 0.0% | -6.6% | 15.5% | 1.06 | 46.5% | 0.72 | 19.3% | -27.7% |
| sma100_hyst2pct | 52.9% | 43.3% | 1.20 | -42.8% | 54.3% | 0.54 | 23 | 5.1 | -21.4% | 6.1% | 12.6% | 1.23 | 59.7% | 1.13 | 32.8% | -27.2% |
| donchian_ensemble | 29.0% | 30.4% | 0.99 | -39.8% | 67.9% | 0.36 | 266 | 8.5 | -16.9% | -4.4% | -2.1% | 1.09 | 35.8% | 0.55 | 9.5% | -23.5% |
| donchian_ensemble_voltarget25 | 14.7% | 13.5% | 1.08 | -16.2% | 67.9% | 0.17 | 266 | 4.4 | -6.8% | -3.9% | -0.7% | 1.25 | 17.9% | 0.46 | 4.9% | -16.2% |

Cost sensitivity (CAGR / Sharpe):

| rule | 5bps | 10bps | 30bps |
|---|---|---|---|
| buy_and_hold | 29.4% / 0.73 | 29.4% / 0.73 | 29.4% / 0.73 |
| sma200_hyst2pct | 39.5% / 0.99 | 39.3% / 0.99 | 38.2% / 0.97 |
| sma100_hyst2pct | 52.9% / 1.20 | 52.5% / 1.19 | 50.9% / 1.17 |
| donchian_ensemble | 29.0% / 0.99 | 28.5% / 0.97 | 26.3% / 0.92 |
| donchian_ensemble_voltarget25 | 14.7% / 1.08 | 14.4% / 1.07 | 13.4% / 1.00 |

Calendar-year returns at 5bps:

| rule | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|---|
| buy_and_hold | -61.5% | 94.4% | 302.0% | 59.8% | -64.2% | 155.6% | 121.3% | -6.3% | -3.7% |
| sma200_hyst2pct | 0.0% | 57.5% | 169.4% | 8.0% | 0.0% | 96.4% | 70.0% | -6.6% | 15.5% |
| sma100_hyst2pct | -34.8% | 140.8% | 224.5% | 86.0% | -21.4% | 90.0% | 111.0% | 6.1% | 12.6% |
| donchian_ensemble | -11.9% | 69.2% | 165.7% | 29.4% | -16.9% | 39.3% | 53.8% | -4.4% | -2.1% |
| donchian_ensemble_voltarget25 | -4.3% | 32.9% | 60.1% | 11.2% | -6.8% | 22.4% | 28.6% | -3.9% | -0.7% |

### ETHUSDT (primary cost 5bps; window 2018-04-24 -> 2026-09-23)

| rule | CAGR | vol | Sharpe | MaxDD | time in mkt | avg exp | round trips | turnover/yr | 2022 | 2025 | 2026 YTD | IS Sharpe | IS CAGR | 24m Sharpe | 24m CAGR | 24m MaxDD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| buy_and_hold | 17.3% | 82.8% | 0.61 | -89.8% | 100.0% | 1.00 | 1 | 0.1 | -67.5% | -11.0% | -9.7% | 0.68 | 23.0% | 0.35 | 0.7% | -67.6% |
| sma200_hyst2pct | 36.3% | 59.5% | 0.83 | -74.2% | 50.7% | 0.51 | 18 | 3.9 | -13.0% | -10.2% | 19.1% | 0.93 | 47.1% | 0.36 | 6.8% | -38.7% |
| sma100_hyst2pct | 46.0% | 58.9% | 0.95 | -66.2% | 53.0% | 0.53 | 25 | 5.6 | -39.9% | 58.4% | 19.6% | 0.91 | 44.0% | 1.18 | 52.4% | -35.2% |
| donchian_ensemble | 35.6% | 39.0% | 0.97 | -35.7% | 61.9% | 0.31 | 248 | 7.8 | -5.2% | 19.3% | -2.2% | 1.09 | 44.8% | 0.47 | 9.7% | -32.9% |
| donchian_ensemble_voltarget25 | 12.5% | 13.0% | 0.98 | -13.5% | 61.9% | 0.11 | 248 | 3.0 | -1.6% | 7.7% | -0.1% | 1.10 | 15.1% | 0.49 | 4.8% | -13.5% |

Cost sensitivity (CAGR / Sharpe):

| rule | 5bps | 10bps | 30bps |
|---|---|---|---|
| buy_and_hold | 17.3% / 0.61 | 17.3% / 0.61 | 17.2% / 0.61 |
| sma200_hyst2pct | 36.3% / 0.83 | 36.1% / 0.82 | 35.0% / 0.81 |
| sma100_hyst2pct | 46.0% / 0.95 | 45.6% / 0.94 | 44.0% / 0.92 |
| donchian_ensemble | 35.6% / 0.97 | 35.0% / 0.96 | 33.0% / 0.92 |
| donchian_ensemble_voltarget25 | 12.5% / 0.98 | 12.4% / 0.96 | 11.7% / 0.92 |

Calendar-year returns at 5bps:

| rule | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|---|
| buy_and_hold | -81.3% | -1.7% | 470.2% | 399.2% | -67.5% | 90.8% | 46.3% | -11.0% | -9.7% |
| sma200_hyst2pct | -14.8% | 1.3% | 128.7% | 303.8% | -13.0% | 42.9% | 28.3% | -10.2% | 19.1% |
| sma100_hyst2pct | -17.8% | 68.7% | 119.7% | 225.5% | -39.9% | 61.8% | 32.5% | 58.4% | 19.6% |
| donchian_ensemble | -11.1% | 25.6% | 133.0% | 224.0% | -5.2% | 12.9% | 23.3% | 19.3% | -2.2% |
| donchian_ensemble_voltarget25 | -2.9% | 9.7% | 39.3% | 42.7% | -1.6% | 6.5% | 13.4% | 7.7% | -0.1% |

**Interpretation.** Every trend rule cut the maximum drawdown by roughly half or more versus buy-and-hold (SOL -96% -> -45% Donchian / -69% SMA200; BTC -77% -> -40% / -53%; ETH -90% -> -36% / -74%) and lifted the Sharpe from ~0.6-0.7 to ~1.0-1.2, almost entirely by being in cash for most of 2022 (SOL 2022: -94% B&H vs -21% / -23%). Headline CAGRs at the primary cost are SOL 58% (SMA200) and 61% (Donchian ensemble) vs 26% B&H, BTC 53% (SMA100) vs 29%, ETH 46% (SMA100) / 36% (Donchian) vs 17% - but SMA100 on SOL is poor (9% CAGR, 36 round trips of whipsaw) while SMA200 on SOL is excellent, and the ranking flips on BTC/ETH, so the lookback sensitivity is large and no single number should be read as robust. The walk-forward split is sobering: in the last 24 months (2024-09-24 -> 2026-09-23) the SOL Donchian Sharpe fell from 1.54 to 0.17 and SMA200 from 1.20 to 0.44; only SMA100 on BTC/ETH held up (1.13 / 1.18). 2025 was negative for almost every rule and asset (-4% to -17%); 2026 YTD the SMA rules are positive (+13% to +34%) because they stepped aside during the H1-2026 decline. Vol-targeting to 25% delivers what it promises (12-14% realised vol, -14% to -16% max DD, same Sharpe) but leaves 83-93% of capital idle on average (avg exposure 0.07-0.17); the CAGR shown assumes zero yield on that cash. Costs barely matter at daily frequency (30 bps vs 5 bps costs 1-3 CAGR points at 4-13x annual turnover).

**Caveats.** One realised path of 5.4 years (SOL) / 8.4 years (BTC, ETH) dominated by two or three regime changes (2021 bull, 2022 bear, 2023-24 bull), so the effective sample is a handful of trends. Trend rules under-perform buy-and-hold badly in strong up-years (SOL 2023: +183% / +324% vs +920%; 2024: -8% / +32% vs +86%). Prices are Binance spot; fills at the next daily open with a flat per-side cost ignore slippage and the spot/DEX basis for SOL. Parameters were fixed a priori but are the community-standard values, which is a mild form of selection.


## Study 2: 4h breakout on SOL and ETH

Script: `analysis/study2_4h_breakout.py`

Method:

```
Study 2 - 4h breakout on SOL and ETH (long-only, no leverage).

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
```

Execution: 4h close signal, next 4h open fill; stops intrabar; costs per side on notional. Window 2022-01-01 -> 2026-09-23; 'L12M' = 2025-09-24 -> 2026-09-23. PF = profit factor; WR = win rate; avg R = mean (exit-entry)/initial risk. B&H = buy and hold of the same asset over the same window.


### SOLUSDT (primary cost 30bps)

| gate | sizing | CAGR | vol | Sharpe | MaxDD | time in mkt | trades | WR | PF | avg R | 2022 | 2025 | 2026 YTD | L12M CAGR | L12M Sharpe | L12M MaxDD | L12M trades | L12M PF |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| buy_and_hold | - | -8.3% | 93.5% | 0.37 | -95.1% | 100% | - | - | - | - | -94.2% | -34.2% | -7.7% | -46.1% | -0.59 | -73.9% | - | - |
| own | risk1pct | 5.3% | 9.4% | 0.59 | -11.9% | 23.6% | 90 | 26.7% | 1.52 | 0.41 | -0.1% | 1.3% | 0.1% | -1.4% | -0.19 | -6.7% | 18 | 0.75 |
| own | fixed100 | 16.5% | 47.3% | 0.56 | -59.2% | 23.6% | 90 | 26.7% | 1.20 | 0.41 | -8.6% | -4.3% | -6.9% | -14.2% | -0.41 | -31.0% | 18 | 0.54 |
| own+btc | risk1pct | 5.8% | 9.5% | 0.65 | -11.2% | 22.8% | 87 | 26.4% | 1.62 | 0.45 | -0.1% | 1.3% | -0.7% | -2.1% | -0.32 | -6.7% | 18 | 0.66 |
| own+btc | fixed100 | 19.4% | 46.8% | 0.61 | -56.1% | 22.8% | 87 | 26.4% | 1.25 | 0.45 | -8.6% | -4.3% | -11.2% | -18.2% | -0.62 | -31.5% | 18 | 0.45 |

Cost sensitivity (CAGR / Sharpe / PF):

| gate | sizing | 5bps | 10bps | 30bps |
|---|---|---|---|---|
| own | risk1pct | 7.1% / 0.77 / 1.81 | 6.7% / 0.74 / 1.75 | 5.3% / 0.59 / 1.52 |
| own | fixed100 | 28.2% / 0.76 / 1.40 | 25.8% / 0.72 / 1.35 | 16.5% / 0.56 / 1.20 |
| own+btc | risk1pct | 7.6% / 0.82 / 1.94 | 7.3% / 0.79 / 1.86 | 5.8% / 0.65 / 1.62 |
| own+btc | fixed100 | 31.0% / 0.81 / 1.46 | 28.6% / 0.77 / 1.41 | 19.4% / 0.61 / 1.25 |

Exit reasons (own gate, fixed100, 30bps): {'ema50': 43, 'stop': 42, 'time': 5}; avg bars held 27.1; avg return on notional per trade 1.73%.


Calendar-year returns at 30bps:

| gate | sizing | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|
| buy_and_hold | - | -94.2% | 920.3% | 86.1% | -34.2% | -7.7% |
| own | risk1pct | -0.1% | 29.0% | -2.3% | 1.3% | 0.1% |
| own | fixed100 | -8.6% | 194.3% | -14.1% | -4.3% | -6.9% |
| own+btc | risk1pct | -0.1% | 30.4% | -0.2% | 1.3% | -0.7% |
| own+btc | fixed100 | -8.6% | 203.5% | -1.7% | -4.3% | -11.2% |

### ETHUSDT (primary cost 5bps)

| gate | sizing | CAGR | vol | Sharpe | MaxDD | time in mkt | trades | WR | PF | avg R | 2022 | 2025 | 2026 YTD | L12M CAGR | L12M Sharpe | L12M MaxDD | L12M trades | L12M PF |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| buy_and_hold | - | -6.7% | 66.2% | 0.23 | -76.2% | 100% | - | - | - | - | -67.9% | -11.0% | -9.7% | -35.5% | -0.43 | -67.3% | - | - |
| own | risk1pct | 6.1% | 7.4% | 0.83 | -12.9% | 26.9% | 100 | 27.0% | 1.63 | 0.32 | 2.6% | 11.7% | -3.4% | -5.1% | -0.77 | -11.9% | 27 | 0.59 |
| own | fixed100 | 19.3% | 31.3% | 0.72 | -47.4% | 26.9% | 100 | 27.0% | 1.35 | 0.32 | 11.9% | 42.5% | -18.7% | -25.4% | -0.95 | -43.3% | 27 | 0.47 |
| own+btc | risk1pct | 6.5% | 7.4% | 0.89 | -12.9% | 26.7% | 97 | 27.8% | 1.71 | 0.35 | 3.4% | 12.9% | -2.8% | -4.4% | -0.66 | -11.9% | 26 | 0.63 |
| own+btc | fixed100 | 21.8% | 31.1% | 0.79 | -47.4% | 26.7% | 97 | 27.8% | 1.42 | 0.35 | 16.5% | 49.2% | -16.1% | -23.0% | -0.84 | -43.3% | 26 | 0.50 |

Cost sensitivity (CAGR / Sharpe / PF):

| gate | sizing | 5bps | 10bps | 30bps |
|---|---|---|---|---|
| own | risk1pct | 6.1% / 0.83 / 1.63 | 5.6% / 0.77 / 1.56 | 3.6% / 0.51 / 1.32 |
| own | fixed100 | 19.3% / 0.72 / 1.35 | 16.8% / 0.65 / 1.30 | 7.4% / 0.38 / 1.12 |
| own+btc | risk1pct | 6.5% / 0.89 / 1.71 | 6.0% / 0.83 / 1.64 | 4.1% / 0.58 / 1.38 |
| own+btc | fixed100 | 21.8% / 0.79 / 1.42 | 19.3% / 0.72 / 1.37 | 9.9% / 0.46 / 1.17 |

Exit reasons (own gate, fixed100, 5bps): {'ema50': 48, 'stop': 42, 'time': 10}; avg bars held 27.9; avg return on notional per trade 1.20%.


Calendar-year returns at 5bps:

| gate | sizing | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|
| buy_and_hold | - | -67.9% | 90.8% | 46.3% | -11.0% | -9.7% |
| own | risk1pct | 2.6% | 2.9% | 16.0% | 11.7% | -3.4% |
| own | fixed100 | 11.9% | 2.1% | 74.6% | 42.5% | -18.7% |
| own+btc | risk1pct | 3.4% | 2.3% | 16.0% | 12.9% | -2.8% |
| own+btc | fixed100 | 16.5% | -0.2% | 74.6% | 49.2% | -16.1% |

**Interpretation.** The 4h breakout is marginal. SOL with the own-asset gate, 100% notional and 30 bps per side returns 16.5% CAGR, Sharpe 0.56, profit factor 1.20, 27% win rate, max DD -59% over 90 trades - and the entire edge is 2023 (+194%); 2024, 2025 and 2026 YTD are all negative (-14%, -4%, -7%) and the last 12 months show -14% CAGR with PF 0.54. ETH at 5 bps is a bit better on paper (19% CAGR, Sharpe 0.72, PF 1.35, 100 trades) but also lost 25% in the last 12 months (PF 0.47). The 1%-risk sizing caps notional at <= 25% of equity (because the stop distance is floored at 4%) and therefore is mostly a de-leveraging: 5-6% CAGR, ~9% vol, -12% max DD, Sharpe 0.6-0.9. The BTC regime gate removes only 3 trades and nudges PF up (SOL 1.52 -> 1.62 risk-sized, ETH 1.63 -> 1.71) - helpful but not decisive. Costs are the dominant sensitivity for SOL: going from 5 to 30 bps per side cuts CAGR from 28% to 16.5% and PF from 1.40 to 1.20 (90 round trips x 60 bps = 54% of notional over 4.7 years), so on a DEX with real slippage the strategy is fragile.

**Caveats.** 90-100 trades in total (18-27 per year) gives very wide confidence intervals on PF and win rate; a single year (2023 for SOL, 2024-25 for ETH) carries the result. Binance spot 1h bars stand in for DEX/perp prices; stops are assumed filled exactly at the stop (or the open when gapped through) with only the flat cost as slippage; hourly exchange-downtime gaps are aggregated into the 4h bar that contains them. R-milestones (break-even, trail, +0.3R) use bar closes, which is conservative relative to using highs; a no-re-entry-on-exit-bar rule is a simplification.


## Study 3: Funding carry (short perp / long spot)

Script: `analysis/study3_funding_carry.py`

Method:

```
Study 3 - funding carry (short perp / long spot collects funding when positive).

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
```

APRs are annualized by interval count (Hyperliquid 8760 x 1h, Binance 1095 x 8h per year). 'always-in' = collect every interval. Rule = trailing 7-day mean annualized funding > 8% -> in the trade next interval; net of 23 bps per round trip (46 bps sensitivity). Blended = rule APR + 5% on idle time.

| venue | sym | start | end | yrs | always-in APR | % neg | 30d p5 | 30d p50 | 30d p95 | 30d>8% | rule time-in | rule RTs | rule gross APR | rule net (23bps) | rule net (46bps) | blended (5% idle) | APR while in |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| hyperliquid | BTC | 2024-11-17 | 2026-09-23 | 1.85 | 9.73% | 13.8% | 0.5% | 8.6% | 19.2% | 52.5% | 55.5% | 22 | 7.55% | 4.82% | 2.09% | 7.05% | 13.6% |
| hyperliquid | ETH | 2025-08-19 | 2026-09-23 | 1.10 | 6.55% | 16.0% | 0.7% | 7.0% | 9.7% | 33.4% | 39.9% | 17 | 3.64% | 0.08% | -3.48% | 3.09% | 9.1% |
| hyperliquid | SOL | 2025-08-19 | 2026-09-23 | 1.10 | 0.89% | 34.2% | -13.9% | 1.1% | 10.5% | 9.9% | 21.7% | 13 | 1.93% | -0.80% | -3.52% | 3.12% | 8.9% |
| binance | BTC | 2020-01-01 | 2026-09-20 | 5.78 | 11.84% | 14.8% | -1.2% | 6.3% | 53.7% | 36.0% | 41.3% | 37 | 9.91% | 8.44% | 6.97% | 11.38% | 24.0% |
| binance | ETH | 2020-01-01 | 2026-09-20 | 5.03 | 15.22% | 15.6% | -1.7% | 6.7% | 72.0% | 42.6% | 45.2% | 28 | 13.82% | 12.54% | 11.26% | 15.28% | 30.6% |
| binance | SOL | 2025-08-19 | 2026-09-20 | 1.02 | -1.41% | 45.5% | -12.6% | -0.2% | 4.7% | 0.0% | 1.2% | 1 | 0.09% | -0.14% | -0.36% | 4.80% | 6.9% |

Binance BTC by calendar year (coverage = fraction of the year's 8h intervals present):

| year | APR to shorts | % neg | coverage |
|---|---|---|---|
| 2020 | 17.19% | 14.3% | 100.3% |
| 2021 | 30.61% | 7.3% | 100.0% |
| 2022 | 4.16% | 22.1% | 100.0% |
| 2023 | 7.87% | 10.1% | 100.0% |
| 2024 | 15.08% | 0.7% | 12.2% |
| 2025 | 5.13% | 12.9% | 100.0% |
| 2026 | 2.45% | 29.0% | 65.6% |

Binance BTC gaps (days): [('2023-12-31', '2024-11-17', 321.5), ('2026-08-21', '2026-09-13', 23.3)]

Hyperliquid by calendar year:

| sym | year | APR to shorts | % neg | coverage |
|---|---|---|---|---|
| BTC | 2024 | 30.47% | 0.5% | 12.3% |
| BTC | 2025 | 10.63% | 8.9% | 100.0% |
| BTC | 2026 | 4.98% | 22.7% | 72.9% |
| ETH | 2025 | 8.35% | 9.4% | 36.9% |
| ETH | 2026 | 5.63% | 19.4% | 72.9% |
| SOL | 2025 | 3.24% | 23.7% | 36.9% |
| SOL | 2026 | -0.29% | 39.5% | 72.9% |

**Interpretation.** Hyperliquid BTC funding paid to shorts averaged 9.7% APR from 2024-11-17 to 2026-09-23 but is decaying fast: ~30% annualised in the Nov-Dec 2024 bull tail, 10.6% in 2025, 5.0% in 2026 YTD; 14% of hours were negative and the rolling-30-day APR sits at p5 / p50 / p95 = 0.5% / 8.6% / 19.2% (above 8% only 52% of the time). The 7-day > 8% rule is in the trade 55% of the time and earns 13.6% APR while in, i.e. 7.6% gross on capital, but 22 round trips at 23 bps cost 2.7% APR, leaving 4.8% net (2.1% at 46 bps) - below a flat 5% stablecoin yield. Even giving the idle capital 5% ('blended' 7.1%) the excess over just holding stables is ~2 points, before basis and execution risk. ETH and SOL on Hyperliquid (from 2025-08-19) are worse: 6.6% and 0.9% always-in, and the rule nets 0.1% and -0.8%; SOL funding was negative 34% of the time, so there is no SOL carry at all in this sample. Binance BTC 2020-2026 shows 11.8% always-in and 8.4% net for the rule, but that is concentrated in 2020-21 (17% and 31% APR); 2022-2026 ran 4%, 8%, ~15% (only 12% coverage), 5%, 2.5%, so the post-2021 carry sits at or below the 5% hurdle once costs are included.

**Caveats.** This is funding only: spot/perp basis moves, the cost of the perp margin (USDC collateral on Hyperliquid earns nothing while posted), exchange/counterparty risk, liquidation risk on the short leg during squeezes, and the 4 fills of a real two-leg entry/exit (23 bps round trip is optimistic) are not modelled. The Hyperliquid BTC sample starts at a funding peak (Nov 2024), which front-loads the average. Binance has no data for 2024-01-01 -> 2024-11-17 and 2026-08-21 -> 2026-09-13; APRs are annualised by interval count and the rolling windows straddle those gaps.


## Study 4: Pump.fun call base rates and a naive exit policy

Script: `analysis/study4_pumpfun.py`

Method:

```
Study 4 - pump.fun base rates from two Telegram *call* datasets.

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
```

Peak multiple = max price after call / call price (not a path). Policy = sell half at 2x, rest at 0.65 x peak; <2x tokens sold at -30%. OPTIMISTIC UPPER BOUND (see caveats).


### smugcalls: n=8084, 2026-06-30 -> 2026-09-24, median peak 1.59x, share >=2x 35.7%, share >=10x 5.1%, share never above call price (peak<=1.0) 6.4%

| peak bucket | <1.0 | 1.0-1.5 | 1.5-2 | 2-3 | 3-5 | 5-10 | >10 |
|---|---|---|---|---|---|---|---|
| share | 0.2% | 45.7% | 18.4% | 13.6% | 10.4% | 6.6% | 5.1% |
| count | 16 | 3695 | 1491 | 1102 | 840 | 530 | 410 |

By market cap at call:

| mcap bucket | n | median peak | peak<=1 | >=2x | >=5x | >=10x |
|---|---|---|---|---|---|---|
| <10k | 2280 | 1.47 | 9.0% | 30.5% | 9.6% | 4.0% |
| 10-30k | 5593 | 1.64 | 5.1% | 37.7% | 12.4% | 5.5% |
| 30-100k | 209 | 1.64 | 14.8% | 36.4% | 12.9% | 4.3% |
| >100k | 2 | 1.23 | 0.0% | 0.0% | 0.0% | 0.0% |

By month:

| month | n | median peak | peak<=1 | >=2x | >=10x |
|---|---|---|---|---|---|
| 2026-06 | 14 | 4.90 | 0.0% | 100.0% | 28.6% |
| 2026-07 | 2672 | 1.64 | 4.9% | 36.7% | 4.9% |
| 2026-08 | 3378 | 1.57 | 6.6% | 35.3% | 5.3% |
| 2026-09 | 2020 | 1.53 | 8.3% | 34.4% | 4.9% |

Naive exit policy (optimistic upper bound):

| round-trip cost | expectancy/trade | median trade | win rate | P(month>0) boot 20 trades | p5 month/trade | p95 month/trade | calendar months >0 | seq. 20-trade blocks >0 |
|---|---|---|---|---|---|---|---|---|
| 0.6pct_rt (30bps/side) | 101.8% | -30.4% | 35.7% | 94.8% | -0.2% | 252.2% | 100.0% | 94.8% (404 blocks) |
| 2.0pct_rt (1%/side) | 99.0% | -31.4% | 35.7% | 94.1% | -1.7% | 237.2% | 100.0% | 93.8% (404 blocks) |

Tail dependence (0.6% cost): mean peak 4.63x, p99 peak 36.08x, max peak 6195x; top 1% of trades supply 59.7% of total policy P&L (top 5%: 82.1%); expectancy excluding the top 1% = 41.4%, excluding the top 5% = 19.2%.

Capped-peak sensitivity (0.6% cost; no trade can realise more than the cap):

| cap | expectancy/trade | P(month>0) 20 trades | p5 month/trade | p50 month/trade |
|---|---|---|---|---|
| cap_3x | 12.2% | 81.7% | -7.7% | 11.9% |
| cap_5x | 22.4% | 91.4% | -4.2% | 22.1% |
| cap_10x | 34.4% | 94.4% | -0.8% | 33.3% |
| cap_20x | 44.9% | 94.8% | -0.2% | 41.7% |

### nikolan17_devcabal: n=188, 2026-04-30 -> 2026-09-24, median peak 1.71x, share >=2x 42.6%, share >=10x 9.0%, share never above call price (peak<=1.0) 0.5%

| peak bucket | <1.0 | 1.0-1.5 | 1.5-2 | 2-3 | 3-5 | 5-10 | >10 |
|---|---|---|---|---|---|---|---|
| share | 0.0% | 39.9% | 17.6% | 17.0% | 9.0% | 7.4% | 9.0% |
| count | 0 | 75 | 33 | 32 | 17 | 14 | 17 |

By market cap at call:

| mcap bucket | n | median peak | peak<=1 | >=2x | >=5x | >=10x |
|---|---|---|---|---|---|---|
| <10k | 4 | 589.79 | 0.0% | 100.0% | 100.0% | 100.0% |
| 10-30k | 1 | 97.28 | 0.0% | 100.0% | 100.0% | 100.0% |
| 30-100k | 3 | 44.27 | 0.0% | 100.0% | 100.0% | 100.0% |
| >100k | 180 | 1.64 | 0.6% | 40.0% | 12.8% | 5.0% |

By month:

| month | n | median peak | peak<=1 | >=2x | >=10x |
|---|---|---|---|---|---|
| 2026-04 | 1 | 1.10 | 0.0% | 0.0% | 0.0% |
| 2026-05 | 33 | 1.47 | 0.0% | 33.3% | 0.0% |
| 2026-06 | 27 | 2.26 | 0.0% | 63.0% | 11.1% |
| 2026-07 | 33 | 1.56 | 3.0% | 33.3% | 6.1% |
| 2026-08 | 40 | 1.70 | 0.0% | 42.5% | 22.5% |
| 2026-09 | 54 | 1.80 | 0.0% | 44.4% | 5.6% |

Naive exit policy (optimistic upper bound):

| round-trip cost | expectancy/trade | median trade | win rate | P(month>0) boot 20 trades | p5 month/trade | p95 month/trade | calendar months >0 | seq. 20-trade blocks >0 |
|---|---|---|---|---|---|---|---|---|
| 0.6pct_rt (30bps/side) | 578.9% | -30.4% | 42.6% | 98.6% | 15.1% | 1979.8% | 100.0% | 100.0% (9 blocks) |
| 2.0pct_rt (1%/side) | 569.4% | -31.4% | 42.6% | 98.6% | 14.0% | 1946.0% | 100.0% | 100.0% (9 blocks) |

Tail dependence (0.6% cost): mean peak 19.26x, p99 peak 579.97x, max peak 700x; top 1% of trades supply 38.7% of total policy P&L (top 5%: 91.5%); expectancy excluding the top 1% = 358.8%, excluding the top 5% = 51.8%.

Capped-peak sensitivity (0.6% cost; no trade can realise more than the cap):

| cap | expectancy/trade | P(month>0) 20 trades | p5 month/trade | p50 month/trade |
|---|---|---|---|---|
| cap_3x | 20.8% | 93.6% | -0.7% | 20.1% |
| cap_5x | 33.0% | 97.3% | 4.8% | 32.6% |
| cap_10x | 51.9% | 98.5% | 10.9% | 50.8% |
| cap_20x | 77.5% | 98.6% | 14.9% | 74.1% |

Reality check (this dataset also has current mcap): hold-to-now mean multiple 0.98x, median 0.14x, 70.7% of calls now below 0.5x; median minutes to peak 331.


**Interpretation.** Base rates from 8,084 smugcalls calls (2026-06-30 -> 2026-09-24): median peak 1.59x, 46% never exceed 1.5x, 35.7% reach 2x, 5.1% reach 10x, 6.4% never trade above the call price; the monthly figures are stable (34-37% reach 2x each month). Calls below $10k market cap do worse (30.5% reach 2x, 9% never trade above the call) than $10-30k calls (37.7%); the $30-100k and >$100k buckets have too few observations to say much. The naive half-at-2x / rest-at-0.65x-peak policy shows +102% expectancy per trade at 0.6% round-trip cost with a 35.7% win rate, but this is a fat-tail artefact rather than a tradeable number: the mean peak is 4.6x against a median of 1.6x, the p99 peak is 36x and the max 6,195x, the top 1% of trades supply 60% of total policy P&L (top 5%: 82%), and excluding the top 5% the expectancy drops to 19%. Capping the realisable peak at 3x / 5x / 10x gives 12% / 22% / 34% per trade and the bootstrap probability of a positive 20-trade month falls from 95% to 82% at the 3x cap. The devcabal set (188 calls) is even more tail-driven (a 700x outlier) and its own current-market-cap column is the reality check: the median call is now at 0.14x its entry cap and 71% are below 0.5x, i.e. anyone who did not sell near the peak lost most of the money.

**Caveats.** The policy is an OPTIMISTIC UPPER BOUND: peak multiple is a single number, not a path, so it assumes (i) a token that reaches 2x does so before any -30% stop is hit, (ii) the second half is filled exactly 35% below the eventual peak rather than on a gap-down or rug, (iii) sub-2x tokens lose exactly 30% when many go to ~0 in one candle, (iv) entry at the call price although the price at the time a follower can actually fill is usually already higher, and (v) costs of 30 bps per side when pump.fun / DEX / priority fees plus slippage are usually well above 1% per side (the 2% round-trip sensitivity barely moves the mean because the mean is tail-driven). Both datasets are calls posted in a Telegram channel (selection bias in what gets posted and survivorship of channels that post), cover ~3-5 months in a single regime, and are not a sample of all launches; a realistic follower expectancy is plausibly near zero or negative.


## Study 5: Cross-asset sanity

Script: `analysis/study5_cross_asset.py`

Method:

```
Study 5 - cross-asset sanity: daily close-to-close return correlations SOL/BTC/ETH (2024-01-01 -> 2026-09-23),
SOL max drawdown (full history and 2024+) and worst calendar month / worst 30-day window.
```

Daily close-to-close return correlations, 2024-01-01 -> 2026-09-23 (997 days):

|  | SOLUSDT | BTCUSDT | ETHUSDT |
|---|---|---|---|
| SOLUSDT | 1.000 | 0.785 | 0.772 |
| BTCUSDT | 0.785 | 1.000 | 0.819 |
| ETHUSDT | 0.772 | 0.819 | 1.000 |

By year (SOL/BTC, SOL/ETH, BTC/ETH):

| year | SOL/BTC | SOL/ETH | BTC/ETH |
|---|---|---|---|
| 2024 | 0.747 | 0.695 | 0.795 |
| 2025 | 0.800 | 0.791 | 0.816 |
| 2026 | 0.882 | 0.881 | 0.911 |

SOL rolling-90d corr with BTC: min 0.61, median 0.80, max 0.93. SOL beta to BTC 1.31, to ETH 0.90. Ann. vol 2024-26: SOLUSDT 79.5%, BTCUSDT 47.4%, ETHUSDT 68.3%.

| SOL drawdown stat | value | dates |
|---|---|---|
| max DD full history (2020-08+) | -96.3% | 2021-11-06 -> 2022-12-29 (258.4 -> 9.6) |
| max DD 2024-2026 | -76.3% | 2025-01-18 -> 2026-06-06 (262.0 -> 62.2) |
| worst calendar month full | -56.5% | 2022-11 |
| worst calendar month 2024-26 | -37.4% | 2024-04 |
| worst 30-day window full | -62.5% | ending 2022-12-05 |
| worst 30-day window 2024-26 | -46.1% | ending 2026-02-12 |

SOL five worst months: 2022-11 -56.5%, 2020-10 -46.5%, 2022-05 -45.9%, 2022-01 -41.5%, 2020-09 -38.9%

**Interpretation.** Daily return correlations in 2024-01-01 -> 2026-09-23 are high: SOL/BTC 0.79, SOL/ETH 0.77, BTC/ETH 0.82, and rising (2026 YTD 0.88-0.91); the rolling-90-day SOL/BTC correlation never fell below 0.61. SOL has a beta of 1.31 to BTC and 80% annualised vol versus 47% for BTC, so it behaves like a leveraged BTC position with extra idiosyncratic risk rather than a diversifier - trend or breakout strategies run on all three will be highly correlated with each other. SOL's full-history max drawdown is -96% (2021-11-06 -> 2022-12-29, 258 -> 9.6), and even within 2024-2026 it is -76% (2025-01-18 -> 2026-06-06, 262 -> 62); the worst calendar month was -56.5% (Nov 2022), the worst in 2024-26 -37.4% (Apr 2024), and the worst 30-day window in 2024-26 was -46% ending 2026-02-12. Any un-hedged SOL strategy has to be sized for a 40-50% monthly hit.

**Caveats.** Close-to-close Binance spot returns; correlations are regime-dependent (they rise in sell-offs), so the average understates crash-time co-movement.
