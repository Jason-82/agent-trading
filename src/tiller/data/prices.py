"""USD marks and the independent execution reference price.

* :class:`JupiterPriceV3` — ``GET {base}/price/v3?ids=<mint,...>`` -> ``{mint: Decimal usd}``.
* :class:`KrakenTicker` — ``GET https://api.kraken.com/0/public/Ticker?pair=SOLUSD`` -> mid of best
  bid/ask in USD.
* :func:`reference_price` — cross-checks the two; raises on disagreement above the tolerance
  (5% normal, 10% emergency per config) and refuses a single source unless the caller says
  the order is an exit (``allow_single_source=True``), in which case it returns the one
  available source and reports a warning through ``warnings``.

All prices are ``Decimal`` USD per whole token.
"""

from __future__ import annotations

import asyncio
import random
import time
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

import httpx

from tiller.execution.http import MonotonicFn, RateLimitedHttp, SleepFn

KRAKEN_BASE = "https://api.kraken.com"


class ReferenceDisagreement(Exception):
    """Jupiter Price V3 and the CEX mid disagree by more than the tolerance."""


class ReferenceUnavailable(Exception):
    """No (or not enough) reference sources available for this order."""


class PriceSourceError(Exception):
    """A price source returned an error or an unparseable body."""


@runtime_checkable
class PriceSource(Protocol):
    async def usd_prices(self, mints: list[str]) -> dict[str, Decimal]: ...


class JupiterPriceV3:
    """Jupiter Price V3 client (max 50 ids per call; we chunk)."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        rps: float,
        client: httpx.AsyncClient,
        *,
        sleep: SleepFn = asyncio.sleep,
        monotonic: MonotonicFn = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key or None
        self._http = RateLimitedHttp(client, rps, sleep=sleep, monotonic=monotonic, rng=rng)

    def _headers(self) -> dict[str, str]:
        h = {"accept": "application/json"}
        if self._api_key:
            h["x-api-key"] = self._api_key
        return h

    async def usd_prices(self, mints: list[str]) -> dict[str, Decimal]:
        """USD price per mint; mints Jupiter does not price are simply absent from the result."""
        out: dict[str, Decimal] = {}
        uniq = list(dict.fromkeys(mints))
        for i in range(0, len(uniq), 50):
            chunk = uniq[i : i + 50]
            resp = await self._http.request(
                "GET", f"{self.base_url}/price/v3", params={"ids": ",".join(chunk)}, headers=self._headers()
            )
            if resp.status_code >= 400:
                raise PriceSourceError(f"price v3: HTTP {resp.status_code}")
            payload = resp.json()
            if not isinstance(payload, dict):
                raise PriceSourceError("price v3: non-object response")
            for mint in chunk:
                entry = payload.get(mint)
                if not isinstance(entry, dict):
                    continue
                px = entry.get("usdPrice")
                if px is None:
                    continue
                value = Decimal(str(px))
                if value > 0:
                    out[mint] = value
        return out

    def error_rate(self) -> float:
        return self._http.error_rate()


class KrakenTicker:
    """Kraken public ticker: mid of best bid/ask for SOL/USD."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        rps: float = 1.0,
        base_url: str = KRAKEN_BASE,
        *,
        sleep: SleepFn = asyncio.sleep,
        monotonic: MonotonicFn = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = RateLimitedHttp(client, rps, sleep=sleep, monotonic=monotonic, rng=rng)

    async def mid(self, pair: str = "SOLUSD") -> Decimal:
        resp = await self._http.request("GET", f"{self.base_url}/0/public/Ticker", params={"pair": pair})
        if resp.status_code >= 400:
            raise PriceSourceError(f"kraken ticker: HTTP {resp.status_code}")
        payload = resp.json()
        return parse_kraken_ticker_mid(payload)

    async def sol_usd_mid(self) -> Decimal:
        return await self.mid("SOLUSD")


def parse_kraken_ticker_mid(payload: Any) -> Decimal:
    """``(best ask + best bid) / 2`` from a Kraken Ticker body; raises PriceSourceError on any oddity."""
    if not isinstance(payload, dict):
        raise PriceSourceError("kraken ticker: non-object response")
    if payload.get("error"):
        raise PriceSourceError(f"kraken ticker: {payload['error']}")
    result = payload.get("result")
    if not isinstance(result, dict) or not result:
        raise PriceSourceError("kraken ticker: empty result")
    entry = next(iter(result.values()))
    try:
        ask = Decimal(str(entry["a"][0]))
        bid = Decimal(str(entry["b"][0]))
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise PriceSourceError("kraken ticker: malformed bid/ask") from e
    if ask <= 0 or bid <= 0 or bid > ask * Decimal("1.05"):
        raise PriceSourceError("kraken ticker: implausible bid/ask")
    return (ask + bid) / Decimal(2)


class FixturePrices:
    """Static price table (tests, offline mode)."""

    def __init__(self, table: dict[str, Decimal]) -> None:
        self.table = {k: Decimal(v) for k, v in table.items()}

    async def usd_prices(self, mints: list[str]) -> dict[str, Decimal]:
        return {m: self.table[m] for m in mints if m in self.table}


def reference_price(
    jup: Decimal | None,
    cex_mid: Decimal | None,
    max_disagreement: Decimal,
    *,
    allow_single_source: bool = False,
    warnings: list[str] | None = None,
) -> Decimal:
    """Independent reference for the quote check.

    Both sources present: raise :class:`ReferenceDisagreement` if they differ by more than
    ``max_disagreement`` (fraction), else return their mean. One source missing: return it only
    when ``allow_single_source`` (exits / emergency), appending a warning; otherwise raise
    :class:`ReferenceUnavailable`. Neither: always raise.
    """
    if jup is not None and jup <= 0:
        jup = None
    if cex_mid is not None and cex_mid <= 0:
        cex_mid = None
    if jup is None and cex_mid is None:
        raise ReferenceUnavailable("no reference price source available")
    if jup is None or cex_mid is None:
        if not allow_single_source:
            raise ReferenceUnavailable("only one reference source available; entries need both")
        available = jup if jup is not None else cex_mid
        assert available is not None
        if warnings is not None:
            warnings.append(f"single reference source used: {'jupiter' if jup is not None else 'cex'}")
        return available
    disagreement = abs(jup / cex_mid - Decimal(1))
    if disagreement > max_disagreement:
        raise ReferenceDisagreement(
            f"jupiter {jup} vs cex {cex_mid} disagree by {disagreement:.4%} (limit {max_disagreement:.2%})"
        )
    return (jup + cex_mid) / Decimal(2)
