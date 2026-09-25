"""FamiliarsClient against respx-mocked fixtures: parsing, raw persistence, single-shot register,
fail-closed me(), post outcomes, token-bucket spacing and 429 backoff."""

from __future__ import annotations

import base64
import json
import random
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx
from pydantic import ValidationError

from tiller.clock import SimClock
from tiller.familiars.client import (
    FamiliarsClient,
    FamiliarsHttpError,
    FamiliarsRateLimited,
    FamiliarsUnavailable,
    ProfilePatch,
    RateLimiter,
    RegisterRequest,
    encode_signature,
    redact_payload,
    retry_after_seconds,
)
from tiller.ledger import Ledger
from tiller.models import SOL_MINT

BASE = "https://familiars.family"
WALLET = "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin"
SIG_B64 = base64.b64encode(bytes(range(64))).decode()


class FakeTime:
    """Virtual monotonic clock: ``sleep`` advances it and records the requested waits."""

    def __init__(self) -> None:
        self.t = 1000.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    async def sleep(self, d: float) -> None:
        self.sleeps.append(d)
        self.t += d


@pytest.fixture
def router() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_mocked=True, assert_all_called=False, base_url=BASE) as r:
        yield r


@pytest.fixture
def ftime() -> FakeTime:
    return FakeTime()


@pytest.fixture
async def http() -> Any:
    async with httpx.AsyncClient() as c:
        yield c


@pytest.fixture
def make_client(
    http: httpx.AsyncClient, tmp_ledger: Ledger, sim_clock: SimClock, ftime: FakeTime
) -> Callable[..., FamiliarsClient]:
    def _make(api_key: str | None = "fam_test_key_0000", rps: float = 1.0, **kw: Any) -> FamiliarsClient:
        return FamiliarsClient(
            BASE,
            api_key,
            rps,
            http,
            tmp_ledger,
            sim_clock,
            sleep_fn=ftime.sleep,
            time_fn=ftime.now,
            rng=random.Random(1),
            **kw,
        )

    return _make


def _register_req(**over: Any) -> RegisterRequest:
    kw: dict[str, Any] = dict(
        wallet=WALLET,
        nonce="n_7f3a9c1e2b4d5f60",
        signature_b64=SIG_B64,
        handle="tiller_test",
        name="Tiller",
        bio="rule based",
        strategy="daily trend",
        color="teal",
    )
    kw.update(over)
    return RegisterRequest(**kw)


# --------------------------------------------------------------------------- registration


async def test_challenge_parses_fixture(router, make_client, load_fixture) -> None:
    route = router.post("/api/agents/challenge").mock(
        return_value=httpx.Response(200, json=load_fixture("familiars/challenge"))
    )
    ch = await make_client(api_key=None).challenge(WALLET)
    assert ch.nonce == "n_7f3a9c1e2b4d5f60"
    assert "Nonce" in ch.message
    assert ch.expires_at == datetime(2026, 9, 24, 0, 15, tzinfo=UTC)
    assert json.loads(route.calls[0].request.content) == {"wallet": WALLET}
    assert "authorization" not in route.calls[0].request.headers


async def test_register_parses_and_redacts_raw(router, make_client, load_fixture, tmp_ledger) -> None:
    route = router.post("/api/agents/register").mock(
        return_value=httpx.Response(200, json=load_fixture("familiars/register"))
    )
    res = await make_client(api_key=None).register(_register_req())
    assert res.api_key.startswith("fam_")
    assert res.owner_key is not None and res.login_url is not None
    assert res.handle == "tiller_test"
    body = json.loads(route.calls[0].request.content)
    assert body["signature"] == SIG_B64 and body["color"] == "teal" and "twitter" not in body
    snaps = tmp_ledger.raw_snapshots("familiars")
    assert len(snaps) == 1 and "/api/agents/register" in snaps[0]["key"]
    assert "fam_" not in snaps[0]["json"] and "own_" not in snaps[0]["json"]
    assert "[REDACTED]" in snaps[0]["json"]


async def test_register_single_attempt_on_5xx(router, make_client) -> None:
    route = router.post("/api/agents/register").mock(return_value=httpx.Response(502, text="bad gateway"))
    with pytest.raises(FamiliarsHttpError) as ei:
        await make_client(api_key=None).register(_register_req())
    assert ei.value.status == 502
    assert route.call_count == 1


async def test_register_single_attempt_on_network_error(router, make_client, ftime) -> None:
    route = router.post("/api/agents/register").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(FamiliarsUnavailable):
        await make_client(api_key=None).register(_register_req())
    assert route.call_count == 1
    assert ftime.sleeps == []


def test_register_request_validation() -> None:
    with pytest.raises(ValidationError):
        _register_req(handle="Bad-Handle")
    with pytest.raises(ValidationError):
        _register_req(handle="ab")
    with pytest.raises(ValidationError):
        _register_req(name="")
    with pytest.raises(ValidationError):
        _register_req(name="x" * 33)
    with pytest.raises(ValidationError):
        _register_req(bio="b" * 281)
    with pytest.raises(ValidationError):
        _register_req(strategy="s" * 41)
    with pytest.raises(ValidationError):
        _register_req(color="purple")
    with pytest.raises(ValidationError):
        _register_req(signature_b64=base64.b64encode(b"short").decode())
    with pytest.raises(ValidationError):
        _register_req(signature_b64="not*base64")


def test_encode_signature_roundtrip() -> None:
    raw = bytes(range(64))
    assert base64.b64decode(encode_signature(raw)) == raw
    with pytest.raises(ValueError):
        encode_signature(b"short")


def test_redact_payload_masks_keys_recursively() -> None:
    out = redact_payload({"apiKey": "fam_x", "nested": [{"ownerKey": "own_y", "keep": 1}], "loginUrl": "u"})
    assert out == {
        "apiKey": "[REDACTED]",
        "nested": [{"ownerKey": "[REDACTED]", "keep": 1}],
        "loginUrl": "[REDACTED]",
    }


async def test_owner_key_single_attempt(router, make_client, load_fixture, tmp_ledger) -> None:
    route = router.post("/api/agent/owner-key").mock(
        return_value=httpx.Response(200, json=load_fixture("familiars/owner_key"))
    )
    res = await make_client().owner_key()
    assert res.owner_key.startswith("own_")
    assert route.call_count == 1
    assert route.calls[0].request.headers["authorization"] == "Bearer fam_test_key_0000"
    assert "own_" not in tmp_ledger.raw_snapshots("familiars")[0]["json"]


# --------------------------------------------------------------------------- me()


async def test_me_parses_settings(router, make_client, load_fixture) -> None:
    router.get("/api/agent/me").mock(return_value=httpx.Response(200, json=load_fixture("familiars/me")))
    lim = await make_client().me()
    assert lim.readable is True
    assert lim.max_position_usd == Decimal("250")
    assert lim.daily_limit_usd == Decimal("600.50")
    assert lim.instructions == "Keep positions small this week."


async def test_me_pause_fixture(router, make_client, load_fixture) -> None:
    router.get("/api/agent/me").mock(
        return_value=httpx.Response(200, json=load_fixture("familiars/me_pause"))
    )
    lim = await make_client().me()
    assert lim.readable and lim.max_position_usd is None and lim.daily_limit_usd is None
    assert lim.instructions is not None and lim.instructions.startswith("pause")


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="oops"),
        httpx.Response(401, json={"error": "unauthorized"}),
        httpx.Response(200, json={"handle": "tiller_test"}),  # settings block missing: drift
        httpx.Response(200, text="<html>not json</html>"),
    ],
)
async def test_me_unreadable_on_any_error(router, make_client, tmp_ledger, response) -> None:
    router.get("/api/agent/me").mock(return_value=response)
    lim = await make_client(max_429_retries=0).me()
    assert lim.readable is False
    assert lim.max_position_usd is None and lim.daily_limit_usd is None
    assert tmp_ledger.events(kind="familiars.me_unreadable")


async def test_me_unreadable_on_timeout(router, make_client) -> None:
    router.get("/api/agent/me").mock(side_effect=httpx.ReadTimeout("slow"))
    lim = await make_client().me()
    assert lim.readable is False


async def test_me_without_key_is_unreadable(router, make_client) -> None:
    router.get("/api/agent/me").mock(return_value=httpx.Response(200, json={}))
    lim = await make_client(api_key=None).me()
    assert lim.readable is False


# --------------------------------------------------------------------------- post()


async def test_post_ok(router, make_client, load_fixture) -> None:
    route = router.post("/api/posts").mock(
        return_value=httpx.Response(201, json=load_fixture("familiars/post_ok"))
    )
    res = await make_client().post("trade", "bought SOL", SOL_MINT, "sig123")
    assert res.status == "ok" and res.http_status == 201
    body = json.loads(route.calls[0].request.content)
    assert body == {"kind": "trade", "text": "bought SOL", "mint": SOL_MINT, "signature": "sig123"}
    assert route.calls[0].request.headers["authorization"].startswith("Bearer fam_")


async def test_post_omits_null_mint_and_signature(router, make_client) -> None:
    route = router.post("/api/posts").mock(return_value=httpx.Response(200, json={"id": "p"}))
    await make_client().post("note", "daily note", None, None)
    assert json.loads(route.calls[0].request.content) == {"kind": "note", "text": "daily note"}


async def test_post_429_is_rate_limited_not_retried(router, make_client, load_fixture, ftime) -> None:
    route = router.post("/api/posts").mock(
        return_value=httpx.Response(
            429, json=load_fixture("familiars/post_429"), headers={"retry-after": "7"}
        )
    )
    res = await make_client().post("trade", "x", SOL_MINT, "sig")
    assert res.status == "rate_limited"
    assert res.retry_after_s == 7.0
    assert route.call_count == 1 and ftime.sleeps == []


async def test_post_429_body_retry_after_ms(router, make_client, load_fixture) -> None:
    router.post("/api/posts").mock(return_value=httpx.Response(429, json=load_fixture("familiars/post_429")))
    res = await make_client().post("trade", "x", SOL_MINT, "sig")
    assert res.status == "rate_limited" and res.retry_after_s == 5.0


async def test_post_timeout_is_uncertain(router, make_client, tmp_ledger) -> None:
    router.post("/api/posts").mock(side_effect=httpx.ReadTimeout("slow"))
    res = await make_client().post("trade", "x", SOL_MINT, "sig")
    assert res.status == "uncertain"
    snaps = tmp_ledger.raw_snapshots("familiars")
    assert snaps and "[error]" in snaps[0]["key"]


async def test_post_5xx_is_uncertain_and_4xx_rejected(router, make_client) -> None:
    router.post("/api/posts").mock(return_value=httpx.Response(503, text="down"))
    assert (await make_client().post("trade", "x", SOL_MINT, "sig")).status == "uncertain"
    router.post("/api/posts").mock(return_value=httpx.Response(400, json={"error": "bad mint"}))
    res = await make_client().post("trade", "x", SOL_MINT, "sig")
    assert res.status == "rejected" and res.raw == {"error": "bad mint"}


async def test_post_text_length_enforced_before_send(router, make_client) -> None:
    route = router.post("/api/posts").mock(return_value=httpx.Response(200, json={}))
    c = make_client()
    with pytest.raises(ValueError):
        await c.post("note", "", None, None)
    with pytest.raises(ValueError):
        await c.post("note", "x" * 501, None, None)
    assert route.call_count == 0
    assert (await c.post("note", "x" * 500, None, None)).status == "ok"


async def test_patch_profile(router, make_client, load_fixture) -> None:
    route = router.patch("/api/agent/me").mock(
        return_value=httpx.Response(200, json=load_fixture("familiars/me"))
    )
    out = await make_client().patch_profile(ProfilePatch(bio="new bio", color="mint"))
    assert out["handle"] == "tiller_test"
    assert json.loads(route.calls[0].request.content) == {"bio": "new bio", "color": "mint"}
    with pytest.raises(ValueError):
        await make_client().patch_profile(ProfilePatch())


# --------------------------------------------------------------------------- public reads


@pytest.mark.parametrize("rng_name,fixture", [("7D", "agents_7d"), ("30D", "agents_30d")])
async def test_agents_parse_fixture(router, make_client, load_fixture, tmp_ledger, rng_name, fixture) -> None:
    router.get("/api/agents", params={"range": rng_name}).mock(
        return_value=httpx.Response(200, json=load_fixture(f"familiars/{fixture}"))
    )
    rows = await make_client(api_key=None).agents(rng_name)
    assert len(rows) == 4
    ours = next(a for a in rows if a.handle == "tiller_test")
    assert ours.wallet == WALLET and ours.hosted is False
    assert ours.pnl["30D"] == Decimal("10")
    snap = tmp_ledger.raw_snapshots("familiars")[0]
    assert snap["key"] == f"GET /api/agents?range={rng_name} [200]"
    assert json.loads(snap["json"]) == load_fixture(f"familiars/{fixture}")


async def test_agent_detail_parses_fixture(router, make_client, load_fixture) -> None:
    router.get("/api/agents/tiller_test").mock(
        return_value=httpx.Response(200, json=load_fixture("familiars/agent_detail"))
    )
    d = await make_client(api_key=None).agent("tiller_test")
    assert d is not None
    assert d.agent.handle == "tiller_test"
    assert d.cash_usd == Decimal("700.10") and d.sol_balance == Decimal("2.5")
    assert [t.kind for t in d.trades] == ["buy", "sell", "swap"]
    assert d.trades[0].usd_value == Decimal("213.78")
    assert len(d.history) == 2 and d.history[-1].pnl_usd == Decimal("234.56")
    assert d.history[0].ts.tzinfo is not None
    assert d.positions[0].token == SOL_MINT
    assert any(p.get("signature") == d.trades[0].signature for p in d.posts)


async def test_agent_404_is_none(router, make_client) -> None:
    router.get("/api/agents/free_handle").mock(return_value=httpx.Response(404, json={"error": "not found"}))
    assert await make_client(api_key=None).agent("free_handle") is None
    with pytest.raises(ValueError):
        await make_client(api_key=None).agent("Not Valid!")


@pytest.mark.parametrize("fixture,wif_agents", [("tokens", 12), ("tokens_burst", 40)])
async def test_tokens_parse_fixture(router, make_client, load_fixture, fixture, wif_agents) -> None:
    router.get("/api/tokens").mock(
        return_value=httpx.Response(200, json=load_fixture(f"familiars/{fixture}"))
    )
    toks = await make_client(api_key=None).tokens()
    wif = next(t for t in toks if t.symbol == "WIF")
    assert wif.agents == wif_agents
    assert wif.liquidity_usd == Decimal("900000") and wif.price_usd == Decimal("0.71")
    assert wif.last_trade_at is not None and wif.last_trade_at.tzinfo is not None


async def test_raw_snapshot_written_before_parse_failure(router, make_client, tmp_ledger) -> None:
    bad = [{"handle": "x", "wallet": "w"}]  # missing required fields -> ValidationError
    router.get("/api/agents", params={"range": "7D"}).mock(return_value=httpx.Response(200, json=bad))
    with pytest.raises(ValidationError):
        await make_client(api_key=None).agents("7D")
    snaps = tmp_ledger.raw_snapshots("familiars")
    assert len(snaps) == 1 and json.loads(snaps[0]["json"]) == bad


async def test_read_5xx_raises_and_persists(router, make_client, tmp_ledger) -> None:
    router.get("/api/tokens").mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(FamiliarsHttpError):
        await make_client(api_key=None).tokens()
    assert tmp_ledger.raw_snapshots("familiars")[0]["key"] == "GET /api/tokens [500]"


# --------------------------------------------------------------------------- rate limiting


async def test_bucket_spacing(router, make_client, ftime) -> None:
    router.get("/api/tokens").mock(return_value=httpx.Response(200, json=[]))
    c = make_client(api_key=None, rps=2.0)  # 0.5 s spacing
    for _ in range(3):
        await c.tokens()
    assert ftime.sleeps == pytest.approx([0.5, 0.5])


async def test_bucket_does_not_wait_when_idle() -> None:
    ft = FakeTime()
    rl = RateLimiter(1.0, time_fn=ft.now, sleep_fn=ft.sleep)
    await rl.acquire()
    ft.t += 5.0
    await rl.acquire()
    assert ft.sleeps == []
    await rl.acquire()
    assert ft.sleeps == [pytest.approx(1.0)]


async def test_read_429_backoff_then_success(router, make_client, ftime) -> None:
    route = router.get("/api/tokens").mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "3"}),
            httpx.Response(429),
            httpx.Response(200, json=[]),
        ]
    )
    assert await make_client(api_key=None, rps=1000).tokens() == []
    assert route.call_count == 3
    # first wait honours Retry-After (+ jitter <= 0.5 s), second is exponential (2 s * jitter)
    assert 3.0 <= ftime.sleeps[0] <= 3.5
    assert 2.0 <= ftime.sleeps[1] <= 3.0


async def test_read_429_exhausted_raises(router, make_client) -> None:
    route = router.get("/api/tokens").mock(return_value=httpx.Response(429))
    with pytest.raises(FamiliarsRateLimited):
        await make_client(api_key=None, rps=1000, max_429_retries=2).tokens()
    assert route.call_count == 3


def test_retry_after_seconds_header_forms() -> None:
    now = 1_758_672_000.0
    assert retry_after_seconds(httpx.Headers({"retry-after": "4"}), now) == 4.0
    assert retry_after_seconds(httpx.Headers({"retry-after": "999"}), now) == 60.0
    assert retry_after_seconds(httpx.Headers({"x-ratelimit-reset": "5"}), now) == 5.0
    assert retry_after_seconds(httpx.Headers({"x-ratelimit-reset": str(int(now) + 10)}), now) == 10.0
    assert retry_after_seconds(httpx.Headers({"x-ratelimit-reset": str((int(now) + 10) * 1000)}), now) == 10.0
    assert retry_after_seconds(httpx.Headers({"x-ratelimit-reset": "garbage"}), now) is None
    assert retry_after_seconds(httpx.Headers({}), now) is None
