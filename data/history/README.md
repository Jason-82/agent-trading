# Bundled daily history (warm-up data)

`SOLUSDT_1d.csv`, `BTCUSDT_1d.csv`, `ETHUSDT_1d.csv` are Binance **spot** USDT daily
klines, columns `timestamp,open,high,low,close,volume`, where `timestamp` is the bar
**open** time in unix seconds UTC, prices are in USDT and volume is in the base asset.
Coverage: BTC/ETH 2017-08-17 -> 2026-09-23, SOL 2020-08-11 -> 2026-09-23, no gaps.

Provenance (see the research dataset MANIFEST assembled 2026-09-24): the daily series
come from the public GitHub repository `finom/static-klines` (commit
`bd5428a273959183d1f06302b082c3e81a2d581c`, a community cache of the Binance public REST
API) and were cross-checked bar-for-bar against `yanniedog/binance-historical-OHLCV-data`
(commit `646927e67424160b328658cd43c8a135e639374e`); the two unrelated repos agree on
every overlapping daily bar. Neither repository states a license; the underlying market
data is Binance public API output. Treat the files as reference data, not audit-grade.

These files are used for strategy warm-up (`data.csv_dir` in `config/tiller.toml`) and,
byte-identical copies under `tests/fixtures/ohlcv/`, for the golden regression tests that
certify the live signal code against the offline study numbers. `tiller` appends newly
closed bars fetched from Kraken/Coinbase to a cache directory, never to these files.
