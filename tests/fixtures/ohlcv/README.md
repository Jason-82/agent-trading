# OHLCV fixtures

Byte-identical copies of the research dataset (Binance spot USDT klines, columns
`timestamp,open,high,low,close,volume`, timestamp = bar open, unix seconds UTC):

* `SOLUSDT_1d.csv`, `BTCUSDT_1d.csv`, `ETHUSDT_1d.csv` — same files as `data/history/`
  (provenance in `data/history/README.md`); used by the Study-1 golden tests.
* `SOLUSDT_1h.csv` — hourly SOL, 2020-08-11 06:00 -> 2026-09-24 07:00 UTC, merged from
  `yanniedog/binance-historical-OHLCV-data` (before 2022-01-01) and `finom/static-klines`
  (from 2022-01-01); 10 small exchange-downtime gaps are left as missing bars. Used only by
  the Study-2 breakout regression (`tests/test_breakout_golden.py`), which skips if absent.

These are real market data, not synthetic. No license is stated by the source repositories.
