# Research artefacts

Offline quantitative studies that backed the strategy decisions in `docs/RESEARCH.md`
and `docs/SPEC.md`. Everything here runs without network access.

- `analysis/` : the study scripts (`study1_daily_trend.py`, `study2_4h_breakout.py`,
  `study3_funding_carry.py`, `study4_pumpfun.py`, `study5_cross_asset.py`, shared
  helpers in `common.py`), `run_all.sh` to reproduce, and the outputs `RESULTS.md` /
  `results.json`. Conventions: signals on closed bars, fills at next bar open, cost per
  side on turnover, no leverage, long-only unless stated.
- `data/MANIFEST.md` : provenance and validation of every dataset used (Binance spot
  klines via GitHub mirrors, Hyperliquid/Binance/Bybit funding, pump.fun call datasets).
- `data/funding/` : merged funding-rate series (`timestamp,funding_rate,source`).
- `data/memecoin/pumpfun_calls_smugcalls.csv` : 8,084 pump.fun calls with peak multiple
  after call (CC0, SHA-256 verified against the source repo's OpenTimestamps attestation).

The daily/hourly OHLCV the studies read live in `data/history/` (daily) at the repo root;
hourly files (~10 MB) were not committed, see the manifest for the source repositories.
Some sources state no license; they are redistributed here as factual price data with
attribution, for research reproduction only.
(paths note: scripts expect ../data/normalized relative to research/; daily CSVs are in data/history, hourly not committed)
