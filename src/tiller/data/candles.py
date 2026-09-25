"""Daily UTC candles: Kraken (primary), Coinbase (fallback), bundled CSV, local cache.

Guarantees
----------
* :meth:`DailyCandleStore.closed_bars` returns CLOSED bars only: a bar with open time ``ts``
  is closed when ``ts + 24h <= now``.
* Network sources are tried in order and the first that answers wins; CSV sources are always
  merged (warm-up history); the cache file ``<cache_dir>/<SYMBOL>_1d.csv`` is appended
  idempotently (union by ``ts``, existing rows never rewritten).
* A cross-source disagreement above ``max_disagreement`` on overlapping closes raises.

Candle ``ts`` is the bar OPEN in UTC; prices USD (Kraken/Coinbase) or USDT (bundled Binance
CSV); volume in the base asset.
"""

from __future__ import annotations

import asyncio
import csv
import os
import random
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

import httpx

from tiller.clock import Clock
from tiller.execution.http import MonotonicFn, RateLimitedHttp, SleepFn
from tiller.models import Candle

Symbol = Literal["SOL", "BTC", "ETH"]
DAY = timedelta(days=1)

KRAKEN_BASE = "https://api.kraken.com"
COINBASE_BASE = "https://api.exchange.coinbase.com"
KRAKEN_PAIRS: dict[str, str] = {"SOL": "SOLUSD", "BTC": "XBTUSD", "ETH": "ETHUSD"}
COINBASE_PRODUCTS: dict[str, str] = {"SOL": "SOL-USD", "BTC": "BTC-USD", "ETH": "ETH-USD"}
CSV_FILES: dict[str, str] = {"SOL": "SOLUSDT_1d.csv", "BTC": "BTCUSDT_1d.csv", "ETH": "ETHUSDT_1d.csv"}


class CandleSourceError(Exception):
    """A candle source failed (HTTP error, bad body); the store moves on to the next source."""


class InsufficientCandles(Exception):
    """Fewer closed bars than the strategy needs."""


class CandleDisagreement(Exception):
    """Fresh bars disagree with cached/bundled history beyond the tolerance."""


@runtime_checkable
class CandleSource(Protocol):
    async def fetch_daily(self, symbol: Symbol, limit: int) -> list[Candle]: ...


def _dec(v: Any) -> Decimal:
    return Decimal(str(v))


def _closed(bar: Candle, now: datetime) -> bool:
    return bar.ts + DAY <= now


class KrakenCandles:
    """``GET /0/public/OHLC?pair=<pair>&interval=1440`` (up to 720 bars; the last one is live)."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        clock: Clock,
        rps: float = 1.0,
        base_url: str = KRAKEN_BASE,
        *,
        sleep: SleepFn = asyncio.sleep,
        monotonic: MonotonicFn = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        self.clock = clock
        self.base_url = base_url.rstrip("/")
        self._http = RateLimitedHttp(client, rps, sleep=sleep, monotonic=monotonic, rng=rng)

    async def fetch_daily(self, symbol: Symbol, limit: int) -> list[Candle]:
        pair = KRAKEN_PAIRS[symbol]
        try:
            resp = await self._http.request(
                "GET", f"{self.base_url}/0/public/OHLC", params={"pair": pair, "interval": "1440"}
            )
        except httpx.HTTPError as e:
            raise CandleSourceError(f"kraken: {e!r}") from e
        if resp.status_code >= 400:
            raise CandleSourceError(f"kraken: HTTP {resp.status_code}")
        try:
            payload = resp.json()
        except ValueError as e:
            raise CandleSourceError("kraken: bad JSON") from e
        return parse_kraken_ohlc(payload)[-limit:]


def parse_kraken_ohlc(payload: Any) -> list[Candle]:
    """Kraken OHLC rows ``[time, open, high, low, close, vwap, volume, count]`` -> Candles (all bars)."""
    if not isinstance(payload, dict):
        raise CandleSourceError("kraken: non-object body")
    if payload.get("error"):
        raise CandleSourceError(f"kraken: {payload['error']}")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise CandleSourceError("kraken: no result")
    rows = None
    for key, value in result.items():
        if key != "last" and isinstance(value, list):
            rows = value
            break
    if rows is None:
        raise CandleSourceError("kraken: no OHLC rows")
    out: list[Candle] = []
    for r in rows:
        if not isinstance(r, list) or len(r) < 7:
            raise CandleSourceError("kraken: malformed OHLC row")
        out.append(
            Candle(
                ts=datetime.fromtimestamp(int(r[0]), tz=UTC),
                open=_dec(r[1]),
                high=_dec(r[2]),
                low=_dec(r[3]),
                close=_dec(r[4]),
                volume=_dec(r[6]),
            )
        )
    out.sort(key=lambda b: b.ts)
    return out


class CoinbaseCandles:
    """``GET /products/<product>/candles?granularity=86400`` (300 bars max, newest first)."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        clock: Clock,
        rps: float = 1.0,
        base_url: str = COINBASE_BASE,
        *,
        sleep: SleepFn = asyncio.sleep,
        monotonic: MonotonicFn = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        self.clock = clock
        self.base_url = base_url.rstrip("/")
        self._http = RateLimitedHttp(client, rps, sleep=sleep, monotonic=monotonic, rng=rng)

    async def fetch_daily(self, symbol: Symbol, limit: int) -> list[Candle]:
        product = COINBASE_PRODUCTS[symbol]
        try:
            resp = await self._http.request(
                "GET", f"{self.base_url}/products/{product}/candles", params={"granularity": "86400"}
            )
        except httpx.HTTPError as e:
            raise CandleSourceError(f"coinbase: {e!r}") from e
        if resp.status_code >= 400:
            raise CandleSourceError(f"coinbase: HTTP {resp.status_code}")
        try:
            payload = resp.json()
        except ValueError as e:
            raise CandleSourceError("coinbase: bad JSON") from e
        return parse_coinbase_candles(payload)[-limit:]


def parse_coinbase_candles(payload: Any) -> list[Candle]:
    """Coinbase rows ``[time, low, high, open, close, volume]`` -> Candles sorted ascending."""
    if not isinstance(payload, list):
        raise CandleSourceError("coinbase: non-list body")
    out: list[Candle] = []
    for r in payload:
        if not isinstance(r, list) or len(r) < 6:
            raise CandleSourceError("coinbase: malformed row")
        out.append(
            Candle(
                ts=datetime.fromtimestamp(int(r[0]), tz=UTC),
                open=_dec(r[3]),
                high=_dec(r[2]),
                low=_dec(r[1]),
                close=_dec(r[4]),
                volume=_dec(r[5]),
            )
        )
    out.sort(key=lambda b: b.ts)
    return out


class CsvCandles:
    """Bundled history: ``<dir>/<SYMBOL>USDT_1d.csv`` in the research format."""

    bundled = True

    def __init__(self, dir: Path) -> None:
        self.dir = Path(dir)

    async def fetch_daily(self, symbol: Symbol, limit: int) -> list[Candle]:
        path = self.dir / CSV_FILES[symbol]
        if not path.exists():
            raise CandleSourceError(f"csv: {path} missing")
        bars = read_candles_csv(path)
        return bars[-limit:] if limit > 0 else bars


def read_candles_csv(path: Path) -> list[Candle]:
    """Read ``timestamp,open,high,low,close,volume`` (unix seconds, bar open) sorted by ts."""
    out: list[Candle] = []
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            out.append(
                Candle(
                    ts=datetime.fromtimestamp(int(row["timestamp"]), tz=UTC),
                    open=Decimal(row["open"]),
                    high=Decimal(row["high"]),
                    low=Decimal(row["low"]),
                    close=Decimal(row["close"]),
                    volume=Decimal(row["volume"]),
                )
            )
    out.sort(key=lambda b: b.ts)
    return out


def write_candles_csv(path: Path, bars: list[Candle]) -> None:
    """Atomically write bars in the research CSV format (tmp + fsync + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for b in sorted(bars, key=lambda b: b.ts):
            w.writerow([int(b.ts.timestamp()), b.open, b.high, b.low, b.close, b.volume])
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class DailyCandleStore:
    """Merges sources into closed daily bars and maintains the local cache."""

    def __init__(
        self,
        sources: list[CandleSource],
        cache_dir: Path,
        clock: Clock,
        *,
        max_disagreement: Decimal = Decimal("0.03"),
        fetch_limit: int = 720,
    ) -> None:
        self.sources = list(sources)
        self.cache_dir = Path(cache_dir)
        self.clock = clock
        self.max_disagreement = max_disagreement
        self.fetch_limit = fetch_limit
        self.last_errors: list[str] = []
        self.last_source: str | None = None

    def cache_path(self, symbol: str) -> Path:
        return self.cache_dir / f"{symbol}_1d.csv"

    async def closed_bars(self, symbol: Symbol, min_bars: int = 300) -> list[Candle]:
        """Closed bars ascending (union of cache, bundled CSV and the first responding network source).

        Raises :class:`InsufficientCandles` when fewer than ``min_bars`` are available and
        :class:`CandleDisagreement` when the fresh bars contradict stored history.
        """
        now = self.clock.now()
        self.last_errors = []
        self.last_source = None
        merged: dict[datetime, Candle] = {}
        cache = self.cache_path(symbol)
        if cache.exists():
            for b in read_candles_csv(cache):
                merged[b.ts] = b

        network_done = False
        fresh: list[Candle] = []
        for src in self.sources:
            bundled = bool(getattr(src, "bundled", False))
            if network_done and not bundled:
                continue
            try:
                bars = await src.fetch_daily(symbol, self.fetch_limit)
            except CandleSourceError as e:
                self.last_errors.append(str(e))
                continue
            closed = [b for b in bars if _closed(b, now)]
            if bundled:
                for b in closed:
                    merged.setdefault(b.ts, b)
            else:
                fresh = closed
                network_done = True
                self.last_source = type(src).__name__

        if fresh:
            overlap = [(merged[b.ts], b) for b in fresh if b.ts in merged]
            if overlap:
                stored = [a for a, _ in overlap][-5:]
                incoming = [b for _, b in overlap][-5:]
                dis = self.disagreement(stored, incoming, n=5)
                if dis > self.max_disagreement:
                    raise CandleDisagreement(
                        f"{symbol}: {self.last_source} disagrees with stored history by {dis:.4%}"
                    )
            for b in fresh:
                merged.setdefault(b.ts, b)

        bars_out = [merged[k] for k in sorted(merged)]
        bars_out = [b for b in bars_out if _closed(b, now)]
        if bars_out:
            write_candles_csv(cache, bars_out)
        if len(bars_out) < min_bars:
            raise InsufficientCandles(f"{symbol}: {len(bars_out)} closed bars < {min_bars}")
        return bars_out

    def is_stale(self, bars: list[Candle], max_age: timedelta) -> bool:
        """True when the newest bar's CLOSE is older than ``max_age`` (or there are no bars)."""
        if not bars:
            return True
        last_close = max(b.ts for b in bars) + DAY
        return self.clock.now() - last_close > max_age

    @staticmethod
    def disagreement(a: list[Candle], b: list[Candle], n: int = 5) -> Decimal:
        """max |close_a/close_b - 1| over the last ``n`` bars with matching ``ts`` (0 if no overlap)."""
        by_ts = {c.ts: c for c in b}
        worst = Decimal(0)
        for c in sorted(a, key=lambda x: x.ts)[-n:]:
            other = by_ts.get(c.ts)
            if other is None or other.close == 0:
                continue
            worst = max(worst, abs(c.close / other.close - Decimal(1)))
        return worst
