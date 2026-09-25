"""Price V3 / Kraken ticker parsing and the reference_price cross-check rules."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx

from fakes import respx_router
from tiller.data.prices import (
    FixturePrices,
    JupiterPriceV3,
    KrakenTicker,
    PriceSourceError,
    ReferenceDisagreement,
    ReferenceUnavailable,
    parse_kraken_ticker_mid,
    reference_price,
)
from tiller.models import SOL_MINT, USDC_MINT

BASE = "https://jup.invalid"
TOKEN_X = "J2xccRtuG43drESLYznHhLhQkLTdfepcKYbiQ9BsJVaf"
UNKNOWN = "5Z6Ay5NEcbg3xhopc522sBCRXQujkTiuDRnHGfQdcnSf"


@pytest.fixture
def router() -> Iterator[respx.MockRouter]:
    with respx_router() as r:
        yield r


async def test_price_v3_parse(router: respx.MockRouter, load_fixture: Callable[[str], Any]) -> None:
    route = router.get(f"{BASE}/price/v3").mock(
        return_value=httpx.Response(200, json=load_fixture("jupiter/price_v3"))
    )
    async with httpx.AsyncClient() as client:
        src = JupiterPriceV3(BASE, "jup_synthetic", 1000.0, client)
        prices = await src.usd_prices([SOL_MINT, USDC_MINT, TOKEN_X, UNKNOWN, SOL_MINT])
    assert prices == {SOL_MINT: Decimal("150.12"), USDC_MINT: Decimal("0.9999"), TOKEN_X: Decimal("0.0421")}
    req = route.calls[0].request
    assert req.url.params["ids"] == f"{SOL_MINT},{USDC_MINT},{TOKEN_X},{UNKNOWN}"
    assert req.headers["x-api-key"] == "jup_synthetic"


async def test_price_v3_error_raises(router: respx.MockRouter) -> None:
    router.get(f"{BASE}/price/v3").mock(return_value=httpx.Response(500))
    async with httpx.AsyncClient() as client:
        src = JupiterPriceV3(BASE, None, 1000.0, client)
        with pytest.raises(PriceSourceError):
            await src.usd_prices([SOL_MINT])
        assert src.error_rate() == 1.0


async def test_kraken_ticker_parse(router: respx.MockRouter, load_fixture: Callable[[str], Any]) -> None:
    route = router.get("https://api.kraken.com/0/public/Ticker").mock(
        return_value=httpx.Response(200, json=load_fixture("kraken/ticker_solusd"))
    )
    async with httpx.AsyncClient() as client:
        mid = await KrakenTicker(client, rps=1000.0).sol_usd_mid()
    assert mid == Decimal("150.15")
    assert route.calls[0].request.url.params["pair"] == "SOLUSD"


def test_kraken_ticker_bad_bodies(load_fixture: Callable[[str], Any]) -> None:
    with pytest.raises(PriceSourceError):
        parse_kraken_ticker_mid(load_fixture("kraken/error"))
    with pytest.raises(PriceSourceError):
        parse_kraken_ticker_mid({"error": [], "result": {}})
    with pytest.raises(PriceSourceError):
        parse_kraken_ticker_mid(
            {"error": [], "result": {"SOLUSD": {"a": ["100", "1", "1"], "b": ["120", "1", "1"]}}}
        )


def test_reference_price_rules() -> None:
    tol = Decimal("0.05")
    assert reference_price(Decimal("150"), Decimal("152"), tol) == Decimal("151")
    with pytest.raises(ReferenceDisagreement):
        reference_price(Decimal("150"), Decimal("160"), tol)
    assert reference_price(Decimal("150"), Decimal("160"), Decimal("0.10")) == Decimal("155")
    with pytest.raises(ReferenceUnavailable):
        reference_price(None, Decimal("150"), tol)
    with pytest.raises(ReferenceUnavailable):
        reference_price(Decimal("150"), None, tol)
    with pytest.raises(ReferenceUnavailable):
        reference_price(None, None, tol, allow_single_source=True)
    warnings: list[str] = []
    assert reference_price(None, Decimal("150"), tol, allow_single_source=True, warnings=warnings) == Decimal(
        "150"
    )
    assert reference_price(Decimal("151"), None, tol, allow_single_source=True, warnings=warnings) == Decimal(
        "151"
    )
    assert warnings == ["single reference source used: cex", "single reference source used: jupiter"]
    with pytest.raises(ReferenceUnavailable):
        reference_price(Decimal("0"), None, tol, allow_single_source=True)


async def test_fixture_prices() -> None:
    src = FixturePrices({SOL_MINT: Decimal("150")})
    assert await src.usd_prices([SOL_MINT, TOKEN_X]) == {SOL_MINT: Decimal("150")}
