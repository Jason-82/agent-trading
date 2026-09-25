"""JupiterSwapV2: order parse, typed errors, execute, 429 backoff, round trip, explicit slippage."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx

from fakes import respx_router
from tiller.execution.jupiter import JupiterError, JupiterSwapV2
from tiller.models import SOL_MINT, USDC_MINT, SwapRequest

BASE = "https://jup.invalid"
WALLET = "FAe4sisG95oZ42w7buUn5qEE4TAnfTTFPiguZUHmhiF"
TOKEN_X = "J2xccRtuG43drESLYznHhLhQkLTdfepcKYbiQ9BsJVaf"


@pytest.fixture
def router() -> Iterator[respx.MockRouter]:
    with respx_router() as r:
        yield r


class Recorder:
    def __init__(self) -> None:
        self.sleeps: list[float] = []

    async def sleep(self, s: float) -> None:
        self.sleeps.append(s)


def buy_req(amount: int = 100_000_000) -> SwapRequest:
    return SwapRequest(
        input_mint=USDC_MINT, output_mint=SOL_MINT, amount_base=amount, strategy="t", reason="r"
    )


@pytest.fixture
async def jup(tmp_ledger: Any, sim_clock: Any) -> Any:
    async with httpx.AsyncClient() as client:
        rec = Recorder()
        j = JupiterSwapV2(
            BASE,
            "jup_synthetic_key",
            rps=1000.0,
            client=client,
            ledger=tmp_ledger,
            clock=sim_clock,
            sleep=rec.sleep,
        )
        j.rec = rec  # type: ignore[attr-defined]
        yield j


async def test_order_parses_fixture_and_sends_explicit_slippage(
    router: respx.MockRouter, jup: JupiterSwapV2, load_fixture: Callable[[str], Any], tmp_ledger: Any
) -> None:
    route = router.get(f"{BASE}/swap/v2/order").mock(
        return_value=httpx.Response(200, json=load_fixture("jupiter/order_ok"))
    )
    order = await jup.order(buy_req(), WALLET, 50)
    req = route.calls[0].request
    params = dict(req.url.params)
    assert params == {
        "inputMint": USDC_MINT,
        "outputMint": SOL_MINT,
        "amount": "100000000",
        "taker": WALLET,
        "slippageBps": "50",
    }
    assert req.headers["x-api-key"] == "jup_synthetic_key"
    assert order.in_amount == 100_000_000 and order.out_amount == 665_800_000
    assert order.slippage_bps == 50 and order.fee_bps == 2 and order.router == "metis"
    assert order.price_impact_pct == Decimal("0.0012") and order.request_id.startswith("11111111")
    assert order.transaction_b64 and order.raw["swapType"] == "aggregator"
    snaps = tmp_ledger.raw_snapshots(source="jupiter.order")
    assert len(snaps) == 1 and json.loads(snaps[0]["json"])["requestId"] == order.request_id


async def test_no_api_key_means_no_header(
    router: respx.MockRouter, load_fixture: Callable[[str], Any]
) -> None:
    route = router.get(f"{BASE}/swap/v2/order").mock(
        return_value=httpx.Response(200, json=load_fixture("jupiter/order_ok"))
    )
    async with httpx.AsyncClient() as client:
        j = JupiterSwapV2(BASE, None, rps=1000.0, client=client)
        await j.order(buy_req(), WALLET, 50)
    assert "x-api-key" not in route.calls[0].request.headers


@pytest.mark.parametrize(
    ("fixture", "code"),
    [("jupiter/order_error", 1), ("jupiter/order_error_amount", 2), ("jupiter/order_error_invalid", 3)],
)
async def test_order_error_codes_are_typed(
    router: respx.MockRouter, jup: JupiterSwapV2, load_fixture: Callable[[str], Any], fixture: str, code: int
) -> None:
    router.get(f"{BASE}/swap/v2/order").mock(return_value=httpx.Response(400, json=load_fixture(fixture)))
    with pytest.raises(JupiterError) as ei:
        await jup.order(buy_req(), WALLET, 50)
    assert ei.value.code == code and ei.value.status == 400


async def test_order_http_error_without_body_is_typed(router: respx.MockRouter, jup: JupiterSwapV2) -> None:
    router.get(f"{BASE}/swap/v2/order").mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(JupiterError) as ei:
        await jup.order(buy_req(), WALLET, 50)
    assert ei.value.status == 500 and ei.value.code == 0
    assert jup.error_rate() == 1.0


async def test_order_refuses_zero_slippage_and_echo_mismatch(
    router: respx.MockRouter, jup: JupiterSwapV2, load_fixture: Callable[[str], Any]
) -> None:
    with pytest.raises(ValueError):
        await jup.order(buy_req(), WALLET, 0)
    router.get(f"{BASE}/swap/v2/order").mock(
        return_value=httpx.Response(200, json=load_fixture("jupiter/order_ok"))
    )
    with pytest.raises(JupiterError, match="slippageBps echoed"):
        await jup.order(buy_req(), WALLET, 150)


async def test_execute_success_and_failed(
    router: respx.MockRouter, jup: JupiterSwapV2, load_fixture: Callable[[str], Any], tmp_ledger: Any
) -> None:
    route = router.post(f"{BASE}/swap/v2/execute").mock(
        side_effect=[
            httpx.Response(200, json=load_fixture("jupiter/execute_ok")),
            httpx.Response(200, json=load_fixture("jupiter/execute_fail")),
        ]
    )
    ok = await jup.execute("c2lnbmVk", "req-1")
    body = json.loads(route.calls[0].request.content)
    assert body == {"signedTransaction": "c2lnbmVk", "requestId": "req-1"}
    assert ok.status == "Success" and ok.signature and ok.code == 0
    failed = await jup.execute("c2lnbmVk", "req-1")
    assert failed.status == "Failed" and failed.code == 6001 and "0x1771" in (failed.error or "")
    assert len(tmp_ledger.raw_snapshots(source="jupiter.execute")) == 2


async def test_execute_transport_error_propagates(router: respx.MockRouter, jup: JupiterSwapV2) -> None:
    router.post(f"{BASE}/swap/v2/execute").mock(side_effect=httpx.ConnectError("reset"))
    with pytest.raises(httpx.HTTPError):
        await jup.execute("c2lnbmVk", "req-1")


async def test_429_backoff_honours_retry_after(
    router: respx.MockRouter, jup: JupiterSwapV2, load_fixture: Callable[[str], Any]
) -> None:
    route = router.get(f"{BASE}/swap/v2/order").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "3"}),
            httpx.Response(429, headers={"x-ratelimit-reset": "2"}),
            httpx.Response(200, json=load_fixture("jupiter/order_ok")),
        ]
    )
    order = await jup.order(buy_req(), WALLET, 50)
    assert order.out_amount == 665_800_000 and route.call_count == 3
    sleeps = [x for x in jup.rec.sleeps if x >= 0.5]  # type: ignore[attr-defined]  # drop bucket waits
    assert len(sleeps) == 2 and sleeps[0] >= 3.0 and sleeps[1] >= 2.0


async def test_429_exhaustion_raises_typed(
    router: respx.MockRouter, load_fixture: Callable[[str], Any]
) -> None:
    router.get(f"{BASE}/swap/v2/order").mock(return_value=httpx.Response(429))
    rec = Recorder()
    async with httpx.AsyncClient() as client:
        j = JupiterSwapV2(BASE, None, rps=1000.0, client=client, sleep=rec.sleep)
        with pytest.raises(JupiterError) as ei:
            await j.order(buy_req(), WALLET, 50)
    assert ei.value.status == 429


async def test_round_trip_arithmetic(
    router: respx.MockRouter, jup: JupiterSwapV2, load_fixture: Callable[[str], Any]
) -> None:
    base = load_fixture("jupiter/order_ok")

    def handler(request: httpx.Request) -> httpx.Response:
        p = dict(request.url.params)
        assert p["slippageBps"] == "100"
        if p["inputMint"] == USDC_MINT:
            assert p["amount"] == "25000000" and p["outputMint"] == TOKEN_X
            return httpx.Response(
                200, json={**base, "inAmount": "25000000", "outAmount": "593000000", "slippageBps": 100}
            )
        assert p["inputMint"] == TOKEN_X and p["amount"] == "593000000"
        return httpx.Response(
            200, json={**base, "inAmount": "593000000", "outAmount": "24850000", "slippageBps": 100}
        )

    router.get(f"{BASE}/swap/v2/order").mock(side_effect=handler)
    cost = await jup.round_trip_cost(TOKEN_X, Decimal("25"), WALLET)
    assert cost == Decimal(1) - Decimal(24_850_000) / Decimal(25_000_000)
    assert cost == Decimal("0.006")
