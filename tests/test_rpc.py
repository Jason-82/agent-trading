"""HttpRpc: fixture parsing per method, failover, token-bucket spacing, error-rate window."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
import respx

from fakes import respx_router
from tiller.execution.http import ErrorWindow, TokenBucket
from tiller.execution.rpc import HttpRpc, RpcError, RpcUnavailable
from tiller.models import TOKEN_2022_PROGRAM, TOKEN_PROGRAM

RPC_A = "https://rpc-a.invalid/"
RPC_B = "https://rpc-b.invalid/"


@pytest.fixture
def router() -> Iterator[respx.MockRouter]:
    with respx_router() as r:
        yield r


class Recorder:
    def __init__(self) -> None:
        self.sleeps: list[float] = []
        self.t = 0.0

    async def sleep(self, s: float) -> None:
        self.sleeps.append(s)

    def monotonic(self) -> float:
        return self.t


@pytest.fixture
async def rpc() -> Any:
    async with httpx.AsyncClient() as client:
        rec = Recorder()
        r = HttpRpc([RPC_A, RPC_B], rps=1000.0, client=client, sleep=rec.sleep, monotonic=rec.monotonic)
        r.rec = rec  # type: ignore[attr-defined]
        yield r


def _method(request: httpx.Request) -> str:
    return str(json.loads(request.content)["method"])


async def test_get_balance_parses_fixture(
    router: respx.MockRouter, rpc: HttpRpc, load_fixture: Callable[[str], Any]
) -> None:
    route = router.post(RPC_A).mock(return_value=httpx.Response(200, json=load_fixture("rpc/balance")))
    assert await rpc.get_balance("FAe4sisG95oZ42w7buUn5qEE4TAnfTTFPiguZUHmhiF") == 2_000_000_000
    body = json.loads(route.calls[0].request.content)
    assert body["method"] == "getBalance" and body["params"][1]["commitment"] == "confirmed"


async def test_token_accounts_both_programs(
    router: respx.MockRouter, rpc: HttpRpc, load_fixture: Callable[[str], Any]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["method"] == "getTokenAccountsByOwner"
        assert body["params"][2]["encoding"] == "jsonParsed"
        program = body["params"][1]["programId"]
        name = "rpc/token_accounts_spl" if program == TOKEN_PROGRAM else "rpc/token_accounts_t2022"
        return httpx.Response(200, json=load_fixture(name))

    router.post(RPC_A).mock(side_effect=handler)
    accounts = await rpc.get_token_accounts_by_owner("FAe4sisG95oZ42w7buUn5qEE4TAnfTTFPiguZUHmhiF")
    assert len(accounts) == 3
    programs = {a.program for a in accounts}
    assert programs == {TOKEN_PROGRAM, TOKEN_2022_PROGRAM}
    usdc = next(a for a in accounts if a.mint == "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
    assert usdc.amount_base == 500_000_000 and usdc.delegate is None and usdc.close_authority is None


async def test_get_account_info(
    router: respx.MockRouter, rpc: HttpRpc, load_fixture: Callable[[str], Any]
) -> None:
    router.post(RPC_A).mock(
        side_effect=[
            httpx.Response(200, json=load_fixture("rpc/mint_spl")),
            httpx.Response(200, json=load_fixture("rpc/mint_missing")),
        ]
    )
    value = await rpc.get_account_info("J2xccRtuG43drESLYznHhLhQkLTdfepcKYbiQ9BsJVaf")
    assert value is not None and value["owner"] == TOKEN_PROGRAM
    assert value["data"]["parsed"]["info"]["decimals"] == 6
    assert await rpc.get_account_info("missing") is None


async def test_simulate_params_and_parse(
    router: respx.MockRouter, rpc: HttpRpc, load_fixture: Callable[[str], Any]
) -> None:
    fixture = load_fixture("rpc/sim_ok")
    route = router.post(RPC_A).mock(return_value=httpx.Response(200, json=fixture))
    addresses = fixture["_meta"]["addresses"]
    sim = await rpc.simulate("AQID", addresses)
    body = json.loads(route.calls[0].request.content)
    cfg = body["params"][1]
    assert body["method"] == "simulateTransaction"
    assert cfg["sigVerify"] is False and cfg["replaceRecentBlockhash"] is True
    assert cfg["commitment"] == "processed" and cfg["encoding"] == "base64"
    assert cfg["accounts"] == {"encoding": "jsonParsed", "addresses": addresses}
    assert sim.err is None and sim.units_consumed == 93000
    assert sim.accounts_requested == addresses and len(sim.accounts) == 4
    assert sim.accounts[3] is None and sim.accounts[0]["lamports"] == 2_000_000_000 + 665_800_000 - 5000


async def test_simulate_persists_raw(
    router: respx.MockRouter, load_fixture: Callable[[str], Any], tmp_ledger: Any, sim_clock: Any
) -> None:
    fixture = load_fixture("rpc/sim_ok")
    router.post(RPC_A).mock(return_value=httpx.Response(200, json=fixture))
    async with httpx.AsyncClient() as client:
        r = HttpRpc([RPC_A], rps=1000.0, client=client, ledger=tmp_ledger, clock=sim_clock)
        await r.simulate("AQID", fixture["_meta"]["addresses"])
    snaps = tmp_ledger.raw_snapshots(source="rpc.simulate")
    assert len(snaps) == 1 and json.loads(snaps[0]["json"])["unitsConsumed"] == 93000


async def test_statuses_transaction_signatures(
    router: respx.MockRouter, rpc: HttpRpc, load_fixture: Callable[[str], Any]
) -> None:
    router.post(RPC_A).mock(
        side_effect=[
            httpx.Response(200, json=load_fixture("rpc/statuses_finalized")),
            httpx.Response(200, json=load_fixture("rpc/statuses_pending")),
            httpx.Response(200, json=load_fixture("rpc/tx_swap_buy")),
            httpx.Response(200, json=load_fixture("rpc/tx_missing")),
            httpx.Response(200, json=load_fixture("rpc/signatures_for_address")),
        ]
    )
    st = await rpc.get_signature_statuses(["sig"])
    assert st[0] is not None and st[0]["confirmationStatus"] == "finalized"
    assert (await rpc.get_signature_statuses(["sig"])) == [None]
    tx = await rpc.get_transaction("sig")
    assert tx is not None and tx["meta"]["fee"] == 5000 and tx["blockTime"] == 1790208300
    assert await rpc.get_transaction("sig") is None
    rows = await rpc.get_signatures_for_address("addr", limit=10, until="x")
    assert len(rows) == 2 and rows[0]["confirmationStatus"] == "finalized"


async def test_get_transaction_request_shape(
    router: respx.MockRouter, rpc: HttpRpc, load_fixture: Callable[[str], Any]
) -> None:
    route = router.post(RPC_A).mock(return_value=httpx.Response(200, json=load_fixture("rpc/tx_swap_buy")))
    await rpc.get_transaction("sig")
    params = json.loads(route.calls[0].request.content)["params"]
    assert params[1]["encoding"] == "jsonParsed" and params[1]["maxSupportedTransactionVersion"] == 0


async def test_failover_on_5xx_and_timeout(
    router: respx.MockRouter, rpc: HttpRpc, load_fixture: Callable[[str], Any]
) -> None:
    a = router.post(RPC_A).mock(side_effect=[httpx.Response(503, text="down"), httpx.ConnectTimeout("slow")])
    b = router.post(RPC_B).mock(return_value=httpx.Response(200, json=load_fixture("rpc/balance")))
    assert await rpc.get_balance("x") == 2_000_000_000
    assert await rpc.get_balance("x") == 2_000_000_000
    assert a.call_count == 2 and b.call_count == 2


async def test_all_endpoints_down_raises(router: respx.MockRouter, rpc: HttpRpc) -> None:
    router.post(RPC_A).mock(return_value=httpx.Response(502))
    router.post(RPC_B).mock(side_effect=httpx.ReadTimeout("t"))
    with pytest.raises(RpcUnavailable):
        await rpc.get_balance("x")


async def test_rpc_error_is_typed_and_not_failed_over(
    router: respx.MockRouter, rpc: HttpRpc, load_fixture: Callable[[str], Any]
) -> None:
    a = router.post(RPC_A).mock(return_value=httpx.Response(200, json=load_fixture("rpc/error_response")))
    b = router.post(RPC_B).mock(
        return_value=httpx.Response(200, json={"jsonrpc": "2.0", "result": 1, "id": 1})
    )
    with pytest.raises(RpcError) as ei:
        await rpc.get_account_info("x")
    assert ei.value.code == -32602
    assert a.call_count == 1 and b.call_count == 0


async def test_error_rate_window(
    router: respx.MockRouter, rpc: HttpRpc, load_fixture: Callable[[str], Any]
) -> None:
    ok = httpx.Response(200, json=load_fixture("rpc/balance"))
    bad = httpx.Response(200, json=load_fixture("rpc/error_response"))
    router.post(RPC_A).mock(side_effect=[bad] * 6 + [ok] * 10)
    assert rpc.error_rate() == 0.0
    for _ in range(6):
        with pytest.raises(RpcError):
            await rpc.get_balance("x")
    assert rpc.error_rate() == pytest.approx(1.0)
    for _ in range(5):
        await rpc.get_balance("x")
    # last 10 = 5 errors + 5 ok
    assert rpc.error_rate() == pytest.approx(0.5)
    for _ in range(5):
        await rpc.get_balance("x")
    assert rpc.error_rate() == pytest.approx(0.0)


async def test_token_bucket_spacing() -> None:
    rec = Recorder()
    bucket = TokenBucket(rps=2.0, monotonic=rec.monotonic, sleep=rec.sleep)
    await bucket.acquire()
    await bucket.acquire()
    await bucket.acquire()
    assert rec.sleeps == [0.5, 0.5]
    rec.t = 10.0  # long idle refills exactly one token (capacity 1)
    await bucket.acquire()
    await bucket.acquire()
    assert rec.sleeps == [0.5, 0.5, 0.5]


def test_error_window_math() -> None:
    w = ErrorWindow(size=3)
    assert w.rate() == 0.0
    for e in (True, True, False, False):
        w.record(e)
    assert w.rate() == pytest.approx(1 / 3)
