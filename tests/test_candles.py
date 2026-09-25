"""Candle store: Kraken/Coinbase parsing, closed-bar guarantee, fallback, staleness, cache, CSV."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from fakes import FakeCandleSource, respx_router
from tiller.clock import SimClock
from tiller.data.candles import (
    CandleDisagreement,
    CandleSourceError,
    CoinbaseCandles,
    CsvCandles,
    DailyCandleStore,
    InsufficientCandles,
    KrakenCandles,
    parse_kraken_ohlc,
    read_candles_csv,
    write_candles_csv,
)
from tiller.models import Candle

FIXTURES = Path(__file__).parent / "fixtures"
KRAKEN = "https://api.kraken.com/0/public/OHLC"
COINBASE = "https://api.exchange.coinbase.com/products/SOL-USD/candles"
T0 = datetime(2026, 9, 20, tzinfo=UTC)


@pytest.fixture
def router() -> Iterator[respx.MockRouter]:
    with respx_router() as r:
        yield r


def mk(ts: datetime, close: Decimal | int = 100) -> Candle:
    c = Decimal(close)
    return Candle(ts=ts, open=c, high=c, low=c, close=c, volume=Decimal(1))


def test_kraken_fixture_parses_with_correct_ts(load_fixture: Callable[[str], Any]) -> None:
    bars = parse_kraken_ohlc(load_fixture("kraken/ohlc_solusd"))
    assert len(bars) == 5
    assert bars[0].ts == T0 and bars[-1].ts == T0 + timedelta(days=4)
    assert bars[0].open == Decimal("148.00") and bars[0].volume == Decimal("12345.6000")
    btc = parse_kraken_ohlc(load_fixture("kraken/ohlc_xbtusd"))  # result key XXBTZUSD
    assert btc[0].close == Decimal("113200.0")
    with pytest.raises(CandleSourceError):
        parse_kraken_ohlc(load_fixture("kraken/error"))


async def test_kraken_source_and_unclosed_bar_dropped(
    router: respx.MockRouter, load_fixture: Callable[[str], Any], tmp_path: Path
) -> None:
    route = router.get(KRAKEN).mock(return_value=httpx.Response(200, json=load_fixture("kraken/ohlc_solusd")))
    clock = SimClock(datetime(2026, 9, 24, 0, 5, tzinfo=UTC))
    async with httpx.AsyncClient() as client:
        store = DailyCandleStore([KrakenCandles(client, clock, rps=1000.0)], tmp_path / "cache", clock)
        bars = await store.closed_bars("SOL", min_bars=1)
    assert route.calls[0].request.url.params["pair"] == "SOLUSD"
    assert route.calls[0].request.url.params["interval"] == "1440"
    assert [b.ts for b in bars] == [T0 + timedelta(days=i) for i in range(4)]
    assert store.last_source == "KrakenCandles"
    # at 2026-09-25 00:00 the 09-24 bar is closed
    clock.set(datetime(2026, 9, 25, tzinfo=UTC))
    async with httpx.AsyncClient() as client:
        store = DailyCandleStore([KrakenCandles(client, clock, rps=1000.0)], tmp_path / "cache", clock)
        assert len(await store.closed_bars("SOL", min_bars=1)) == 5


async def test_fallback_to_coinbase_when_kraken_5xx(
    router: respx.MockRouter, load_fixture: Callable[[str], Any], tmp_path: Path
) -> None:
    k = router.get(KRAKEN).mock(return_value=httpx.Response(503))
    c = router.get(COINBASE).mock(
        return_value=httpx.Response(200, json=load_fixture("coinbase/candles_solusd"))
    )
    clock = SimClock(datetime(2026, 9, 24, 0, 5, tzinfo=UTC))
    async with httpx.AsyncClient() as client:
        store = DailyCandleStore(
            [KrakenCandles(client, clock, rps=1000.0), CoinbaseCandles(client, clock, rps=1000.0)],
            tmp_path / "cache",
            clock,
        )
        bars = await store.closed_bars("SOL", min_bars=1)
    assert k.call_count == 1 and c.call_count == 1
    assert c.calls[0].request.url.params["granularity"] == "86400"
    kraken_rows = load_fixture("kraken/ohlc_solusd")["result"]["SOLUSD"]
    assert len(bars) == 4 and bars[0].open == Decimal("148.0") and bars[-1].high == Decimal(kraken_rows[3][2])
    assert store.last_source == "CoinbaseCandles" and store.last_errors == ["kraken: HTTP 503"]


async def test_kraken_success_skips_coinbase(
    router: respx.MockRouter, load_fixture: Callable[[str], Any], tmp_path: Path
) -> None:
    router.get(KRAKEN).mock(return_value=httpx.Response(200, json=load_fixture("kraken/ohlc_solusd")))
    c = router.get(COINBASE).mock(
        return_value=httpx.Response(200, json=load_fixture("coinbase/candles_solusd"))
    )
    clock = SimClock(datetime(2026, 9, 24, 0, 5, tzinfo=UTC))
    async with httpx.AsyncClient() as client:
        store = DailyCandleStore(
            [KrakenCandles(client, clock, rps=1000.0), CoinbaseCandles(client, clock, rps=1000.0)],
            tmp_path / "cache",
            clock,
        )
        await store.closed_bars("SOL", min_bars=1)
    assert c.call_count == 0


def test_stale_detection_at_26h(tmp_path: Path) -> None:
    bars = [mk(T0 + timedelta(days=i)) for i in range(4)]  # last close 2026-09-24 00:00
    clock = SimClock(datetime(2026, 9, 25, 1, 59, tzinfo=UTC))
    store = DailyCandleStore([], tmp_path, clock)
    assert store.is_stale(bars, timedelta(hours=26)) is False
    clock.set(datetime(2026, 9, 25, 2, 1, tzinfo=UTC))
    assert store.is_stale(bars, timedelta(hours=26)) is True
    assert store.is_stale([], timedelta(hours=26)) is True


async def test_cache_append_is_idempotent(tmp_path: Path) -> None:
    clock = SimClock(datetime(2026, 9, 24, 0, 5, tzinfo=UTC))
    src = FakeCandleSource({"SOL": [mk(T0 + timedelta(days=i), 100 + i) for i in range(5)]})
    store = DailyCandleStore([src], tmp_path / "cache", clock)
    first = await store.closed_bars("SOL", min_bars=1)
    assert len(first) == 4
    cache = store.cache_path("SOL")
    before = cache.read_bytes()
    second = await store.closed_bars("SOL", min_bars=1)
    assert second == first and cache.read_bytes() == before
    # a day later one more bar closes and exactly one row is appended
    clock.advance(timedelta(days=1))
    third = await store.closed_bars("SOL", min_bars=1)
    assert len(third) == 5 and len(read_candles_csv(cache)) == 5
    # source down: cache alone still serves
    src.fail = True
    assert len(await store.closed_bars("SOL", min_bars=1)) == 5
    assert store.last_errors == ["fake source down"]


async def test_bundled_csv_always_merged_and_history_kept(tmp_path: Path) -> None:
    clock = SimClock(datetime(2026, 9, 24, 0, 5, tzinfo=UTC))
    hist_dir = tmp_path / "hist"
    hist_dir.mkdir()
    write_candles_csv(hist_dir / "SOLUSDT_1d.csv", [mk(T0 - timedelta(days=i), 90) for i in range(1, 4)])
    live = FakeCandleSource({"SOL": [mk(T0 + timedelta(days=i), 100) for i in range(5)]})
    store = DailyCandleStore([live, CsvCandles(hist_dir)], tmp_path / "cache", clock)
    bars = await store.closed_bars("SOL", min_bars=7)
    assert len(bars) == 7 and bars[0].ts == T0 - timedelta(days=3) and bars[-1].ts == T0 + timedelta(days=3)
    assert live.calls == 1


async def test_disagreement_raises(tmp_path: Path) -> None:
    clock = SimClock(datetime(2026, 9, 24, 0, 5, tzinfo=UTC))
    cache_dir = tmp_path / "cache"
    write_candles_csv(cache_dir / "SOL_1d.csv", [mk(T0 + timedelta(days=i), 100) for i in range(3)])
    src = FakeCandleSource({"SOL": [mk(T0 + timedelta(days=i), 110) for i in range(4)]})
    store = DailyCandleStore([src], cache_dir, clock)
    with pytest.raises(CandleDisagreement):
        await store.closed_bars("SOL", min_bars=1)
    assert DailyCandleStore.disagreement([mk(T0, 100)], [mk(T0, 110)]) == abs(
        Decimal(100) / Decimal(110) - Decimal(1)
    )
    assert DailyCandleStore.disagreement([mk(T0, 100)], [mk(T0 + timedelta(days=1), 500)]) == 0


async def test_insufficient_bars_raises(tmp_path: Path) -> None:
    clock = SimClock(datetime(2026, 9, 24, 0, 5, tzinfo=UTC))
    store = DailyCandleStore([FakeCandleSource({"SOL": [mk(T0)]})], tmp_path / "cache", clock)
    with pytest.raises(InsufficientCandles):
        await store.closed_bars("SOL", min_bars=300)


async def test_csv_loader_reproduces_row_count() -> None:
    path = FIXTURES / "ohlcv" / "SOLUSDT_1d.csv"
    expected = sum(1 for _ in path.open()) - 1
    bars = await CsvCandles(FIXTURES / "ohlcv").fetch_daily("SOL", 0)
    assert len(bars) == expected and bars[0].ts < bars[-1].ts
    assert bars[0].ts == datetime(2020, 8, 11, tzinfo=UTC)
    with pytest.raises(CandleSourceError):
        await CsvCandles(FIXTURES / "nowhere").fetch_daily("SOL", 10)
