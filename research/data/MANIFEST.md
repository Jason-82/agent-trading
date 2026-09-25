# MANIFEST — offline historical crypto dataset for backtester validation

Assembled 2026-09-24 inside a sandbox whose only reachable hosts were github.com / raw.githubusercontent.com / api.github.com (plus PyPI/npm). Every file below was fetched from a public GitHub repository (git protocol, raw.githubusercontent.com, or a GitHub Release asset) and then validated with pandas. No exchange or aggregator API was used. Timestamps everywhere are **UTC**; normalized files use **unix seconds**.

## 0. Summary of what is here

| Priority | Want | Got | Coverage | Notes |
|---|---|---|---|---|
| 1 | Daily OHLCV BTC/ETH/SOL | **Yes** — Binance spot USDT klines | BTC/ETH 2017-08-17 → 2026-09-23, SOL 2020-08-11 → 2026-09-23 | zero gaps; two independent repos agree bar-for-bar; third (Yahoo BTC-USD) agrees within venue basis |
| 2 | Hourly OHLCV BTC/ETH/SOL | **Yes** — Binance spot USDT klines | BTC/ETH 2017-08-17 → 2026-09-24 07:00, SOL 2020-08-11 → 2026-09-24 07:00 | 2022+ from static-klines, pre-2022 from yanniedog; overlap (35k bars/symbol) identical |
| 2b | Bonus: 1-minute perp OHLCV | Yes — Binance USDT-M perps, 2024 Q1 only | 2024-01-01 → 2024-03-31 (131,040 bars each) | from a GitHub Release zip (jssyxd), resampled-to-1h matches spot within 0.05% median |
| 3 | Perp funding history | **Yes** — Binance, Bybit, Hyperliquid (+OKX BTC) | Binance BTC 2020-01→2023-12 and 2024-11→2026-09; ETH 2020-01→2023-12 and 2025-08→2026-09; SOL 2025-08→2026-09; Bybit BTC/ETH 2021-01→2023-12 and 2025-08→2026-09, SOL 2025-08→2026-09; Hyperliquid BTC hourly 2024-11-17→2026-09-24, ETH/SOL hourly 2025-08-19→2026-09-24 | gaps: Binance/Bybit 2024-01-01→2024-11-17 (BTC) / →2025-08 (ETH, SOL, Bybit) and 2026-08-21→2026-09-13; four independent repos agree exactly on every overlap |
| 4 | Solana memecoin / pump.fun data | **Partial** — two pump.fun *call* datasets (8,084 + 188 tokens with mint, call time, entry mcap, peak multiple) | 2026-04-30 → 2026-09-24 | **No per-token OHLCV** and no full launch feed was found committed to GitHub (the one repo that computes per-token OHLCV git-ignores it) |

## 1. Directory layout

```
data/
  MANIFEST.md                      this file
  validation_ohlcv.json            machine-readable validation stats (OHLCV + 1m perps)
  validation_funding.json          machine-readable validation stats (funding, cross-checks, merges)
  validation_memecoin.json         machine-readable validation stats (memecoin)
  normalized/                      timestamp,open,high,low,close,volume  (unix seconds UTC)
    <SYMBOL>_1d.csv, <SYMBOL>_1h.csv         primary deliverables (best merged series)
    <SYMBOL>-PERP_1m_2024Q1.csv              bonus 1-minute perp bars
    by_source/<repo>/<SYMBOL>_<TF>.csv       per-source normalized copies (provenance)
    funding/*.csv                            timestamp,funding_rate[,mark_price] ; *_MERGED.csv adds a source column
    memecoin/*.csv                           pump.fun call datasets
  raw/<repo>/                       original files exactly as fetched (+ README/LICENSE where present)
```

## 2. Normalized OHLCV files (primary deliverables)

Columns: `timestamp,open,high,low,close,volume` — timestamp = bar **open** time, unix seconds UTC; prices in USDT; volume in base asset (BTC/ETH/SOL). Source: Binance **spot** `BTCUSDT`/`ETHUSDT`/`SOLUSDT` klines.

| File | Symbol | TF | Rows | First | Last | Built from | Gaps |
|---|---|---|---|---|---|---|---|
| normalized/BTCUSDT_1d.csv | BTCUSDT | 1d | 3325 | 2017-08-17 00:00 | 2026-09-23 00:00 | static-klines only | none |
| normalized/BTCUSDT_1h.csv | BTCUSDT | 1h | 79676 | 2017-08-17 04:00 | 2026-09-24 07:00 | yanniedog rows before 2022-01-01 00:00:00 + static-klines from 2022-01-01 00:00:00 | 28 gaps / ~128 missing bars (max 34h) |
| normalized/ETHUSDT_1d.csv | ETHUSDT | 1d | 3325 | 2017-08-17 00:00 | 2026-09-23 00:00 | static-klines only | none |
| normalized/ETHUSDT_1h.csv | ETHUSDT | 1h | 79676 | 2017-08-17 04:00 | 2026-09-24 07:00 | yanniedog rows before 2022-01-01 00:00:00 + static-klines from 2022-01-01 00:00:00 | 28 gaps / ~128 missing bars (max 34h) |
| normalized/SOLUSDT_1d.csv | SOLUSDT | 1d | 2235 | 2020-08-11 00:00 | 2026-09-23 00:00 | static-klines only | none |
| normalized/SOLUSDT_1h.csv | SOLUSDT | 1h | 53622 | 2020-08-11 06:00 | 2026-09-24 07:00 | yanniedog rows before 2022-01-01 00:00:00 + static-klines from 2022-01-01 00:00:00 | 10 gaps / ~20 missing bars (max 5h) |

Gap detail: daily series have **no gaps**. Hourly gaps are Binance exchange downtimes (e.g. 2018-02-08 34h maintenance, 2019-05-15 11h, 2023-03-24 12:00-14:00 2h) present identically in both source repos; SOL 1h has 10 small gaps (2-5h) in 2020-2021. Bars are not forward-filled.

### 2b. Bonus 1-minute perpetual bars (Binance USDT-M futures, 2024 Q1)

| File | Symbol | TF | Rows | First | Last | Cross-check vs spot 1h (median / p99 rel. close diff) |
|---|---|---|---|---|---|---|
| normalized/BTCUSDT-PERP_1m_2024Q1.csv | BTCUSDT perp | 1m | 131040 (=91d x 1440, 0 gaps) | 2024-01-01 00:00 | 2024-03-31 23:59 | 0.053% / 0.15% |
| normalized/ETHUSDT-PERP_1m_2024Q1.csv | ETHUSDT perp | 1m | 131040 (=91d x 1440, 0 gaps) | 2024-01-01 00:00 | 2024-03-31 23:59 | 0.053% / 0.15% |
| normalized/SOLUSDT-PERP_1m_2024Q1.csv | SOLUSDT perp | 1m | 131040 (=91d x 1440, 0 gaps) | 2024-01-01 00:00 | 2024-03-31 23:59 | 0.063% / 0.20% |

## 3. Source files (raw) and provenance

| Repo (URL) | Commit / ref | Path in repo | Local raw copy | Symbol(s) | TF | Rows (as fetched) | First → Last | Columns (as in source) | License | Used for |
|---|---|---|---|---|---|---|---|---|---|---|
| https://github.com/finom/static-klines | `bd5428a273959183d1f06302b082c3e81a2d581c` (2026-09-24 08:30 UTC, daily GitHub-Actions refresh) | `.klines-cache/<SYMBOL>/1d/<YYYY-01-01>.json` (2-year windows) and `.klines-cache/<SYMBOL>/1h/<YYYY-MM-01>.json` (monthly) | raw/static-klines/<SYMBOL>/{1d,1h}/*.json | BTCUSDT, ETHUSDT, SOLUSDT (Binance spot) | 1d, 1h | 1d: 3325 BTC/ETH, 2235 SOL; 1h: 41455 each | 1d 2017-08-17 00:00:00 → 2026-09-23 00:00:00; 1h 2022-01-01 00:00:00 → 2026-09-24 07:00:00 | Binance native 12-tuple per bar: `[open_time_ms, open, high, low, close, volume, close_time_ms, quote_volume, n_trades, taker_buy_base, taker_buy_quote, ignore]` (prices as strings) | **not stated** (no LICENSE file, no license field in package.json). README: community cache of Binance public REST API, 'experimental — not audit-grade' | primary 1d series; 1h from 2022-01-01 |
| https://github.com/yanniedog/binance-historical-OHLCV-data | `646927e67424160b328658cd43c8a135e639374e` (2026-07-29) | `<SYMBOL>_1d.csv`, `<SYMBOL>_1h.csv` (repo root; also has 15m/4h/3d/1w not fetched) | raw/yanniedog/*.csv | BTCUSDT, ETHUSDT, SOLUSDT (Binance spot) | 1d, 1h | 1d: 3065 BTC/ETH, 1975 SOL; 1h: 73414 BTC/ETH, 47360 SOL | 1d 2017-08-17 00:00:00 → 2026-01-06 00:00:00; 1h 2017-08-17 04:00:00 → 2026-01-06 09:00:00 | `timestamp,open,high,low,close,volume` (timestamp as `YYYY-MM-DD[ HH:MM:SS]`, UTC) | **not stated** (repo has no README or LICENSE) | 1h before 2022-01-01; independent cross-check of static-klines |
| https://github.com/Swissbit92/btc_price_tracker | `707e0f00c5cef66db2ac5eb6767ba1b781630a36` (2026-09-23) | `daily_history.csv` | raw/swissbit92/daily_history.csv | BTC-USD (Yahoo Finance composite via yfinance) | 1d | 3441 | 2016-01-01 00:00:00 → 2025-06-02 00:00:00 | yfinance MultiIndex export: `Date,Close,High,Low,Open,Volume` with a 2nd header row `,BTC-USD,...`; volume in USD | MIT (per README) | independent, non-Binance cross-check only (normalized/by_source/yfinance-swissbit92/BTCUSD_1d.csv). README warns that the repo's *MongoDB* candles are partial before 2026-07-19; this yfinance file is unaffected but treat as secondary |
| https://github.com/jssyxd/nautilus-crypto-datacatalog | Release **v1.0.0** asset (tag `a31150889df01967005390fda4ec1af25ad1a91a`), zip built 2026-09-23 | `releases/download/v1.0.0/NautilusTrader_Crypto_Catalog_2024_Q1_Bundle.zip` → `catalog/data/bar/<SYM>-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL/<SYM>-PERP.BINANCE/2024/2024_1-minute.parquet` | raw/nautilus/*.zip (22.4 MB) | BTCUSDT-PERP, ETHUSDT-PERP, SOLUSDT-PERP (Binance USDT-M futures, from Binance Vision archive) | 1m | 131,040 each | 2024-01-01 00:00 → 2024-03-31 23:59 | `bar_type, ts_event(ns), ts_init(ns), open, high, low, close, volume, quote_volume, trades_count, taker_buy_base_volume, taker_buy_quote_volume` | MIT | bonus 1m perp bars |
| https://github.com/supervik/historical-funding-rates-fetcher | `66a085bc68147df2dd3360a25ac6e9f38e7077b5` (2024-01-31) | `data/BTC-USDT/BTC-USDT_{binance,bybit}_*_funding_history.csv`, `data/ETH-USDT/...` (dydx/gate/htx/kucoin/mexc also exist, not fetched) | raw/supervik/*.csv | BTC-USDT, ETH-USDT perps | 8h funding | binance 4383, bybit 3285 per symbol | binance 2020-01-01 03:00:00 → 2023-12-31 19:00:00; bybit 2021-01-01 03:00:00 → 2023-12-31 19:00:00 | `Symbol,Date,Funding Rate` (decimal rate; Date = settlement, UTC) | MIT | funding 2020/2021 → 2023 |
| https://github.com/ZuShen168/funding_rate_data | `941df7afff38ada436d95565d8285642a14a3744` (2026-09-24) | `data/funding/venue=<binance|bybit|hyperliquid|hyperliquid-xyz>/data.parquet` (Hive-partitioned parquet, ~35-50 symbols per venue) | raw/zushen/funding_*.parquet | BTC, ETH, SOL extracted (USDT perps on Binance/Bybit; USDC perps on Hyperliquid) | 8h (Binance/Bybit), 1h (Hyperliquid) | 1116 per Binance/Bybit symbol; 9579 per Hyperliquid symbol | Binance/Bybit 2025-08-21 08:00:00 → 2026-09-20 00:00:00; Hyperliquid 2025-08-21 05:00:00 → 2026-09-24 07:00:00 | `venue, venue_symbol, canonical_symbol, settlement_ts(UTC), rate_raw, interval_hours, rate_apr, is_predicted(all False), contract_type, mark_price(Binance only), index_price, open_interest_usd, ingested_at` | MIT (README 'Licence' section) | funding 2025-08 → 2026-09 (all three venues), Binance mark price |
| https://github.com/Caiooooo/anay_hyper_fund | `07bc6e3d30702e2d23314a1ea5a5f02d7d5191ca` (2026-01-27) | `output/Binance_<SYM>USDT_funding_rate.csv`, `output/Hyperliquid_<SYM>_funding_rate.csv`, `output/BTCUSDT_funding_rate.csv` | raw/caiooooo/*.csv | BTC, ETH, SOL | 8h (Binance), 1h (Hyperliquid) | Binance BTC 1095, ETH/SOL 270; Hyperliquid BTC 8759, ETH/SOL 1721 | Binance BTC 2024-11-17 08:00:00 → 2025-11-17 00:00:00, ETH/SOL 2025-08-19 08:00:00 → 2025-11-17 00:00:00; Hyperliquid BTC 2024-11-17 04:00:00 → 2025-11-17 03:00:00, ETH/SOL 2025-08-19 07:00:00 → 2025-11-17 06:00:00 | UTF-8-BOM, Chinese headers: `交易所(exchange),交易对(symbol),时间(time),资金费率(funding_rate),标记价格(mark_price)[,时间差]`; `BTCUSDT_funding_rate.csv` lacks the exchange column and contains every row twice | **not stated** (no README/LICENSE) | funding 2024-11-17 → 2025-11-17 (fills part of the 2024-25 gap); ETH/SOL Hyperliquid files have two ~9-day holes (2025-09-09→09-18, 2025-10-09→10-18) which ZuShen168 covers |
| https://github.com/pattybepatient/crypto-funding-arb | `143ca0d5e8f39d179646ab8e26d935017e2857b8` (2026-08-24) | `data/funding_hyperliquid_btc.csv`, `data/funding_okx_btc.csv` | raw/pattybepatient/*.csv | BTC | 1h (Hyperliquid), 8h (OKX BTC-USD-SWAP) | 2367 / 281 | 2026-03-06 00:00:00 → 2026-06-12 14:00:00 / 2026-03-06 08:00:00 → 2026-06-07 16:00:00 | `timestamp,exchange,symbol,funding_rate` | **not stated** | Hyperliquid cross-check (matches ZuShen168 100%); only OKX funding sample in the set |
| https://github.com/sunsiyuan/trade-signal-bot | `6cd5f33e9ae20d3826fc7b678893fcb6db303329` (2026-05-29) | `data/oi_history/hyperliquid__<BTC|ETH|SOL>_USDC_USDC.csv` | raw/sunsiyuan/*.csv | BTC, ETH, SOL (Hyperliquid USDC perps) | 1h bot snapshots | 3712 each | 2025-12-14 17:00:00 → 2026-05-29 01:00:00 | `timestamp_utc,exchange,symbol,oi,funding_rate,mark_price` | **not stated** | **open interest + mark price** (normalized/funding/<SYM>_hyperliquid_oi_mark_1h_sunsiyuan.csv). Its `funding_rate` is the live/predicted rate at snapshot time, NOT realized settlement (only ~29% of values match the realized history) — kept for reference, excluded from MERGED files |
| https://github.com/Color2333/a-r | `a53a34e7cfd0db00e1488f3a26f648b497faba17` (2026-02-03) | `data/raw/funding_rates/<binance|bybit>_<SYM>USDT_funding_rate.csv` | raw/color2333/*.csv | BTC, ETH, SOL | 8h | 90-200 each | 2025-11-09 / 2025-12-27 → 2026-01-26 | `timestamp,funding_rate` | **not stated** | cross-check only (100% match with ZuShen168); windows fully inside ZuShen168 coverage so not merged |
| https://github.com/Smurfetc/solana-memecoin-calls-dataset | `4fe2264f01a478905d3f19ab9465beda9e1de48d` (2026-09-24) | `calls.jsonl`, `attest.json` (+ OpenTimestamps receipts `calls-a.ots`, `calls-b.ots` not fetched) | raw/smurfetc/calls.jsonl | 8084 pump.fun tokens (mint addresses) | event list | 8084 | 2026-06-30 13:24:43 → 2026-09-24 02:00:19 | JSONL: `mint, sym, t(unix s), utc, mc(USD mcap at call), peak(max multiple after call), tg(Telegram msg id)` | **CC0 1.0** | memecoin dataset; SHA-256 of the file matches attest.json (6209157f9de4…) |
| https://github.com/nikolan17/pumpfun-call-analyzer | `bb7389cf9167d78e579d895574e9dec56adb1a03` (2026-09-24, daily Action) | `calls_analysis.csv`, `calls_detected.csv` (per-token `ohlcv/*.csv` is git-ignored — not available) | raw/nikolan17/*.csv | 188 pump.fun tokens | event list | 188 | 2026-04-30 20:00:18 → 2026-09-24 03:03:12 | `symbol,mint,channel,msg_id,call_time_utc,pool_addr,timeframe,supply,earliest_mc,earliest_ts_utc,entry_mc,peak_mc,peak_ts_utc,now_mc,peak_gain_pct,now_gain_pct,drawdown_pct,minutes_to_peak,error` (mcap from GeckoTerminal OHLCV, 1e9 supply) | **not stated** | memecoin dataset (secondary) |

## 4. Funding-rate files (normalized/funding/)

Columns: `timestamp,funding_rate[,mark_price]` — timestamp = settlement time floored to the hour (unix s UTC); `funding_rate` is the raw per-interval decimal rate exactly as the venue reports it (Binance/Bybit per 8h, Hyperliquid per 1h; multiply by 100 for %). `*_MERGED.csv` files add a `source` column and are the recommended series; per-source files are kept for provenance.

| File | Venue | Symbol | Interval | Rows | First | Last | Sources (priority) | Coverage gaps |
|---|---|---|---|---|---|---|---|---|
| funding/BTCUSDT_funding_binance_8h_MERGED.csv | Binance USDT-M | BTCUSDT | 8h | 6330 | 2020-01-01 03:00:00 | 2026-09-20 00:00:00 | zushen, caiooooo, supervik ({'supervik': 4383, 'zushen': 1116, 'caiooooo': 831}) | 2023-12-31 19:00:00 → 2024-11-17 08:00:00 (7717h); 2026-08-21 00:00:00 → 2026-09-13 08:00:00 (560h) |
| funding/ETHUSDT_funding_binance_8h_MERGED.csv | Binance USDT-M | ETHUSDT | 8h | 5505 | 2020-01-01 03:00:00 | 2026-09-20 00:00:00 | zushen, caiooooo, supervik ({'supervik': 4383, 'zushen': 1116, 'caiooooo': 6}) | 2023-12-31 19:00:00 → 2025-08-19 08:00:00 (14317h); 2026-08-21 00:00:00 → 2026-09-13 08:00:00 (560h) |
| funding/SOLUSDT_funding_binance_8h_MERGED.csv | Binance USDT-M | SOLUSDT | 8h | 1122 | 2025-08-19 08:00:00 | 2026-09-20 00:00:00 | zushen, caiooooo ({'zushen': 1116, 'caiooooo': 6}) | 2026-08-21 00:00:00 → 2026-09-13 08:00:00 (560h) |
| funding/BTCUSDT_funding_bybit_8h_MERGED.csv | Bybit linear | BTCUSDT | 8h | 4401 | 2021-01-01 03:00:00 | 2026-09-20 00:00:00 | zushen, supervik ({'supervik': 3285, 'zushen': 1116}) | 2023-12-31 19:00:00 → 2025-08-21 08:00:00 (14365h); 2026-08-21 00:00:00 → 2026-09-13 08:00:00 (560h) |
| funding/ETHUSDT_funding_bybit_8h_MERGED.csv | Bybit linear | ETHUSDT | 8h | 4401 | 2021-01-01 03:00:00 | 2026-09-20 00:00:00 | zushen, supervik ({'supervik': 3285, 'zushen': 1116}) | 2023-12-31 19:00:00 → 2025-08-21 08:00:00 (14365h); 2026-08-21 00:00:00 → 2026-09-13 08:00:00 (560h) |
| funding/SOLUSDT_funding_bybit_8h_MERGED.csv | Bybit linear | SOLUSDT | 8h | 1116 | 2025-08-21 08:00:00 | 2026-09-20 00:00:00 | zushen ({'zushen': 1116}) | 2026-08-21 00:00:00 → 2026-09-13 08:00:00 (560h) |
| funding/BTC_funding_hyperliquid_1h_MERGED.csv | Hyperliquid | BTC-USDC | 1h | 16228 | 2024-11-17 04:00:00 | 2026-09-24 07:00:00 | zushen, caiooooo, pattybepatient ({'zushen': 9579, 'caiooooo': 6649}) | none |
| funding/ETH_funding_hyperliquid_1h_MERGED.csv | Hyperliquid | ETH-USDC | 1h | 9625 | 2025-08-19 07:00:00 | 2026-09-24 07:00:00 | zushen, caiooooo ({'zushen': 9579, 'caiooooo': 46}) | none |
| funding/SOL_funding_hyperliquid_1h_MERGED.csv | Hyperliquid | SOL-USDC | 1h | 9625 | 2025-08-19 07:00:00 | 2026-09-24 07:00:00 | zushen, caiooooo ({'zushen': 9579, 'caiooooo': 46}) | none |

Per-source files present: `*_supervik.csv` (2020/21→2023), `*_zushen.csv` (2025-08→2026-09), `*_caiooooo.csv` (2024-11→2025-11 BTC; 2025-08→2025-11 ETH/SOL), `BTC_funding_hyperliquid_1h_pattybepatient.csv` (2026-03→2026-06), `BTC_funding_okx_8h_pattybepatient.csv` (OKX, 2026-03→2026-06), `*_sunsiyuan.csv` (snapshot funding, see caveat) and `<SYM>_hyperliquid_oi_mark_1h_sunsiyuan.csv` (`timestamp,oi,mark_price`, 2025-12-14→2026-05-29, hourly, 15 small gaps incl. a 2026-05-18→05-29 stretch of daily-only points).

## 5. Memecoin files (normalized/memecoin/)

* `pumpfun_calls_smugcalls.csv` — 8084 rows, `timestamp,mint,symbol,market_cap_usd_at_call,peak_multiple_after_call,telegram_msg_id`; 2026-06-30 13:24:43 → 2026-09-24 02:00:19; sorted by time, mints unique, 84% of mints end in `pump`; median mcap at call $12,296, median peak 1.59x, 35.7% reached 2x, 5.1% reached 10x (matches the README's published figures). Caveat: `mc` has a minimum of 0 for a handful of rows.
* `pumpfun_calls_nikolan17.csv` — 188 rows from one Telegram channel (devcabal), same columns as source plus unix `timestamp`; 2026-04-30 20:00:18 → 2026-09-24 03:03:12; 0 rows flagged with an error.
* Not obtained: per-token OHLCV candles for pump.fun tokens, bonding-curve trade logs, or a full launch feed. Code/repo searches for such CSV/parquet on GitHub returned only scrapers/API clients (Bitquery, SolanaTracker, Apify) whose data lives off-GitHub.

## 6. Validation performed (all checks passed unless noted)

For every OHLCV file: parse; strictly increasing timestamps; zero duplicates; no NaN; no zero/negative prices or negative volume; OHLC envelope (`high >= max(open,close)`, `low <= min(open,close)`); gap census against the nominal bar size; largest bar-to-bar return; and **price anchors** at known dates (BTC: 2017-12-17 ≈ 19.1k, 2018-12-15 ≈ 3.2k, 2020-03-12 ≈ 4.85k, 2021-04-14 ≈ 63k, 2021-11-09 ≈ 67k, 2022-11-21 ≈ 15.8k, 2024-03-13 ≈ 73k, 2025-01-20 ≈ 102k; ETH: 2018-01-13 ≈ 1.39k, 2021-11-09 ≈ 4.7k, 2022-06-18 ≈ 1.0k, 2024-03-12 ≈ 4.0k; SOL: 2021-11-06 ≈ 258, 2022-12-29 ≈ 9.9, 2025-01-19 ≈ 261) — every anchor within 15% (most within 0.5%). The only >30% daily moves are real events (2020-03-12 crash: BTC -39.5%, ETH -44.6%; the first weeks of SOL trading in Aug 2020).

Independent cross-checks (evidence the data is real, not synthetic):

* static-klines vs yanniedog (two unrelated repos, both Binance spot): 1h overlap 35193 bars/symbol (2022-01-01 00:00:00 → 2026-01-06 09:00:00), median close difference 0, max 0.018% (BTC); 1d overlap 3065 bars, all identical except the final bar of yanniedog (2026-01-06, a partial day when that repo last ran: BTC 0.15%, ETH 1.69%, SOL 1.18%). Volumes identical.
* Binance BTCUSDT 1d vs Yahoo BTC-USD 1d (yfinance, different venue aggregate): 2847 overlapping days, median close difference 0.10%, p99 2.6%, max 9.5% — consistent with USDT/USD basis and venue differences, inconsistent with fabricated data.
* 1m perp bars resampled to 1h vs spot 1h: 2184 hours, median close difference 0.053%, p99 0.15% (perp/spot basis).
* Funding: Caiooooo vs ZuShen168 exact-match fraction 1.0 on all six overlaps (264 Binance BTC rows, 2110 Hyperliquid BTC rows, …); Color2333 vs ZuShen168 exact-match 1.0 on all six overlaps; pattybepatient vs ZuShen168 Hyperliquid BTC exact-match 1.0 over 2367 hours (and only ~19% at ±1h shift, confirming identical timestamp semantics). ZuShen168 Binance `mark_price` vs Binance spot open at the same hour: median 0.04%.
* sunsiyuan Hyperliquid `funding_rate` vs realized history: exact-match only 29% → classified as snapshot/predicted, excluded from merged series.

## 7. Rejected, skipped, and not found

* `mouadja02/bitcoin-technical-indicators-dataset` (hourly BTC + indicators) and `Gendo90/Crypto-Historical-Prices` (ETH 1d/1h, BTC 1-min 2012-2020): files are **Git LFS pointers** on raw.githubusercontent.com — skipped per instructions.
* `jssyxd/multi-asset-ohlcv` and `JasonleeQAQ/multi-asset-ohlcv` (advertised 24-asset 1m→2h parquet, BTC/ETH from 2017-08 to 2026-02): the repos contain only README/reports; the 3.5 GB bundle is not committed to GitHub.
* `benjaminpo/finance-dataset` (yfinance daily/1m crypto CSVs): `data/` holds only a `.gitkeep`; data is pushed to Kaggle, not GitHub.
* `IQUXAe/daily-crypto-commit`, `jhirgit/daily-prices`: daily price *snapshots* (single price per day / equities focus), not OHLCV — skipped.
* `wirewave/Hyperliquid-Funding-History`: browser tool fetching the Hyperliquid API live; no data committed.
* Binance/Bybit funding for **2024-01-01 → 2024-11-17 (BTC)** and **2024-01-01 → 2025-08 (ETH, SOL, all Bybit)** was not found in any committed CSV/parquet on GitHub (several repos found by code search were tiny or covered only late-2025). Binance/Bybit also have a **2026-08-21 → 2026-09-13** hole in the ZuShen168 snapshot (a back-fill lapse in that repo).
* Memecoin per-token OHLCV / pump.fun launch feed: not found on GitHub (see §5).

## 8. Caveats for the backtester

* All OHLCV is Binance **spot** (USDT quote); the only perp price data is 2024 Q1 1m bars. Static-klines is a daily REST cache: Binance occasionally restates candles and a cache cannot reflect that (README warning). Data in the hourly series is as-closed at fetch time (last bar 2026-09-24 07:00 UTC).
* Hourly gaps are exchange downtimes and are left as missing bars (not filled). Daily series are gap-free.
* Funding merges concatenate different collectors; overlaps were verified identical, but the 2024 hole means annual-carry statistics across 2024 cannot be computed from this set.
* Hyperliquid rates are per **hour** (Hyperliquid settles hourly and reports the hourly rate); Binance/Bybit rates are per **8 h**. Do not compare raw values across venues without normalising to a common period.
* Licensing: only supervik, ZuShen168, jssyxd (MIT), Swissbit92 (MIT) and Smurfetc (CC0) state a license; static-klines, yanniedog, Caiooooo, Color2333, pattybepatient, sunsiyuan, nikolan17 do not. Underlying market data is Binance/Bybit/Hyperliquid public API output.

## 9. Reproduction

Scripts used (in the scratchpad root, one level above `data/`): `dl1.sh`, `dl2.sh`, `dl3.sh` (curl from raw.githubusercontent.com), `ohlcv_norm.py`, `nautilus_norm.py`, `funding_norm.py`, `zushen_norm.py`, `caiooooo_norm.py`, `memecoin_norm.py`, `manifest.py`. static-klines JSON was obtained with `git clone --filter=blob:none --no-checkout --depth 1` followed by `git checkout HEAD -- .klines-cache/<SYM>/{1d,1h}`; the nautilus zip via the GitHub Releases download URL (served from release-assets.githubusercontent.com). pandas 3.0.6 + pyarrow 25.0.1.
