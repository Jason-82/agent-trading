"""Token gate table test (one fixture per rejection), allowlist bypass, Shield None rule, TokenData client."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx

from fakes import FakeRpc, respx_router
from tiller.data.tokens import (
    GateResult,
    TokenData,
    TokenGateCfg,
    evaluate_token_gate,
    gate_cfg_from_thresholds,
    parse_shield,
)
from tiller.models import SOL_MINT, TOKEN_PROGRAM, USDC_MINT, TokenInfo

WALLET = "FAe4sisG95oZ42w7buUn5qEE4TAnfTTFPiguZUHmhiF"
TOKEN_X = "J2xccRtuG43drESLYznHhLhQkLTdfepcKYbiQ9BsJVaf"
NOW = datetime(2026, 9, 24, 0, 5, tzinfo=UTC)
ALLOWLIST = {SOL_MINT, USDC_MINT}
ESTABLISHED = TokenGateCfg(
    profile="established",
    min_organic=50,
    min_liquidity_usd=Decimal(400_000),
    min_age_h=72,
    max_top_holders_pct=35,
    max_round_trip_pct=Decimal("0.015"),
)
COPY = TokenGateCfg(
    profile="copy",
    min_organic=50,
    min_liquidity_usd=Decimal(250_000),
    min_age_h=24,
    max_top_holders_pct=35,
    max_round_trip_pct=Decimal("0.015"),
)


@pytest.fixture
def router() -> Iterator[respx.MockRouter]:
    with respx_router() as r:
        yield r


def info(load_fixture: Callable[[str], Any], name: str = "jupiter/tokens_v2_ok") -> TokenInfo | None:
    rows = load_fixture(name)
    return TokenInfo.from_wire(rows[0]) if rows else None


def mint(load_fixture: Callable[[str], Any], name: str = "rpc/mint_spl") -> dict[str, Any] | None:
    return load_fixture(name)["result"]["value"]


def gate(
    load_fixture: Callable[[str], Any],
    *,
    info_name: str = "jupiter/tokens_v2_ok",
    mint_name: str = "rpc/mint_spl",
    shield: list[str] | None = ("NOT_VERIFIED",),
    cfg: TokenGateCfg = ESTABLISHED,
    own: set[str] | None = None,
    blocklist: set[str] | None = None,
    token: str = TOKEN_X,
    allowlist: set[str] = ALLOWLIST,
) -> GateResult:  # type: ignore[assignment]
    return evaluate_token_gate(
        token,
        info(load_fixture, info_name),
        None if shield is None else list(shield),
        mint(load_fixture, mint_name),
        cfg,
        WALLET,
        own or set(),
        blocklist or set(),
        NOW,
        allowlist,
    )


def test_established_and_copy_pass(load_fixture: Callable[[str], Any]) -> None:
    r = gate(load_fixture)
    assert r.ok and r.reasons == [] and r.liquidity_usd == Decimal("1250000.0")
    assert gate(load_fixture, cfg=COPY).ok
    assert gate(load_fixture, mint_name="rpc/mint_t2022_clean").ok
    assert gate(load_fixture, shield=[]).ok


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"info_name": "jupiter/tokens_v2_low_organic"}, "organic score 12.0 < 50"),
        ({"info_name": "jupiter/tokens_v2_low_liquidity"}, "liquidity"),
        ({"info_name": "jupiter/tokens_v2_young"}, "age 4.1h < 72h"),
        ({"info_name": "jupiter/tokens_v2_young", "cfg": COPY}, "age 4.1h < 24h"),
        ({"info_name": "jupiter/tokens_v2_top_holders"}, "top holders 61.0% > 35"),
        ({"info_name": "jupiter/tokens_v2_sus"}, "isSus"),
        ({"info_name": "jupiter/tokens_v2_own"}, "dev is our wallet"),
        ({"info_name": "jupiter/tokens_v2_empty"}, "no token info"),
        ({"own": {TOKEN_X}}, "own_token_mints"),
        ({"blocklist": {TOKEN_X}}, "blocklist"),
        ({"shield": None}, "shield: unreachable"),
        ({"shield": ["HAS_FREEZE_AUTHORITY", "NEW_LISTING"]}, "shield: HAS_FREEZE_AUTHORITY,NEW_LISTING"),
        ({"shield": ["HAS_MINT_AUTHORITY"]}, "shield: HAS_MINT_AUTHORITY"),
        ({"shield": ["LOW_ORGANIC_ACTIVITY"]}, "shield: LOW_ORGANIC_ACTIVITY"),
        ({"mint_name": "rpc/mint_missing"}, "mint: account not found"),
        ({"mint_name": "rpc/mint_not_token_program"}, "not a token program"),
        ({"mint_name": "rpc/mint_spl_mint_authority"}, "mint authority present"),
        ({"mint_name": "rpc/mint_spl_freeze_authority"}, "freeze authority present"),
        ({"mint_name": "rpc/mint_t2022_transfer_fee"}, "transferFeeConfig"),
        ({"mint_name": "rpc/mint_t2022_transfer_hook"}, "transferHook"),
        ({"mint_name": "rpc/mint_t2022_permanent_delegate"}, "permanentDelegate"),
        ({"mint_name": "rpc/mint_t2022_non_transferable"}, "nonTransferable"),
        ({"mint_name": "rpc/mint_t2022_default_frozen"}, "defaultAccountState frozen"),
        ({"mint_name": "rpc/mint_t2022_pausable"}, "pausable"),
        ({"mint_name": "rpc/mint_t2022_confidential"}, "confidentialTransferMint"),
        ({"mint_name": "rpc/mint_t2022_close_authority"}, "mintCloseAuthority"),
    ],
)
def test_each_rejection_reason(
    load_fixture: Callable[[str], Any], kwargs: dict[str, Any], expected: str
) -> None:
    r = gate(load_fixture, **kwargs)
    assert not r.ok
    assert any(expected in reason for reason in r.reasons), r.reasons


def test_mint_parse_is_binding_even_when_tokens_v2_looks_clean(load_fixture: Callable[[str], Any]) -> None:
    r = gate(load_fixture, mint_name="rpc/mint_spl_freeze_authority")
    assert not r.ok and r.reasons == ["mint: freeze authority present"]


def test_allowlist_bypass_and_shield_none_only_blocks_non_allowlisted(
    load_fixture: Callable[[str], Any],
) -> None:
    for m in (SOL_MINT, USDC_MINT):
        r = evaluate_token_gate(m, None, None, None, ESTABLISHED, WALLET, set(), set(), NOW, ALLOWLIST)
        assert r.ok and r.reasons == []
    hold = "5Z6Ay5NEcbg3xhopc522sBCRXQujkTiuDRnHGfQdcnSf"
    assert evaluate_token_gate(
        hold, None, None, None, ESTABLISHED, WALLET, set(), set(), NOW, {*ALLOWLIST, hold}
    ).ok
    assert not gate(load_fixture, shield=None).ok
    assert gate(load_fixture, shield=None, token=SOL_MINT).ok


def test_gate_cfg_from_config_thresholds() -> None:
    from tiller.config import TokenGateThresholds

    cfg = gate_cfg_from_thresholds(
        "copy", TokenGateThresholds(min_liquidity_usd=Decimal(250_000), min_age_h=24)
    )
    assert cfg == COPY


def test_parse_shield_shapes(load_fixture: Callable[[str], Any]) -> None:
    assert parse_shield(load_fixture("jupiter/shield_warn"), [TOKEN_X]) == {
        TOKEN_X: ["HAS_FREEZE_AUTHORITY", "NEW_LISTING"]
    }
    assert parse_shield(load_fixture("jupiter/shield_clean"), [TOKEN_X, SOL_MINT]) == {
        TOKEN_X: [],
        SOL_MINT: [],
    }
    assert parse_shield({"nope": 1}, [TOKEN_X]) is None
    assert parse_shield({"warnings": {TOKEN_X: "bad"}}, [TOKEN_X]) is None


# --------------------------------------------------------------------------- TokenData client

BASE = "https://jup.invalid"


@pytest.fixture
async def tokens(load_fixture: Callable[[str], Any], tmp_ledger: Any, sim_clock: Any) -> Any:
    rpc = FakeRpc(accounts={TOKEN_X: mint(load_fixture), SOL_MINT: mint(load_fixture, "rpc/mint_spl_sol")})
    async with httpx.AsyncClient() as client:
        yield TokenData(client, rpc, BASE, "jup_synthetic", 1000.0, tmp_ledger, sim_clock)


async def test_token_info_parses_and_persists(
    router: respx.MockRouter, tokens: TokenData, load_fixture: Callable[[str], Any], tmp_ledger: Any
) -> None:
    route = router.get(f"{BASE}/tokens/v2/search").mock(
        return_value=httpx.Response(200, json=load_fixture("jupiter/tokens_v2_ok"))
    )
    ti = await tokens.token_info(TOKEN_X)
    assert ti is not None and ti.mint == TOKEN_X and ti.symbol == "TKX" and ti.organic_score == 82.5
    assert (
        ti.audit is not None
        and ti.audit.top_holders_pct == 18.4
        and ti.first_pool_created_at == datetime(2025, 3, 1, 12, tzinfo=UTC)
    )
    assert ti.raw["tokenProgram"] == TOKEN_PROGRAM
    assert dict(route.calls[0].request.url.params) == {"query": TOKEN_X}
    assert route.calls[0].request.headers["x-api-key"] == "jup_synthetic"
    assert len(tmp_ledger.raw_snapshots(source="jupiter.tokens_v2")) == 1


async def test_token_info_unreachable_or_unknown_is_none(
    router: respx.MockRouter, tokens: TokenData, load_fixture: Callable[[str], Any]
) -> None:
    router.get(f"{BASE}/tokens/v2/search").mock(
        side_effect=[
            httpx.Response(503),
            httpx.ConnectError("x"),
            httpx.Response(200, json=load_fixture("jupiter/tokens_v2_empty")),
            httpx.Response(200, text="not json"),
        ]
    )
    for _ in range(4):
        assert await tokens.token_info(TOKEN_X) is None


async def test_shield_parse_and_none_on_error(
    router: respx.MockRouter, tokens: TokenData, load_fixture: Callable[[str], Any], tmp_ledger: Any
) -> None:
    route = router.get(f"{BASE}/ultra/v1/shield").mock(
        side_effect=[
            httpx.Response(200, json=load_fixture("jupiter/shield_ok")),
            httpx.Response(500),
            httpx.ConnectTimeout("t"),
        ]
    )
    assert await tokens.shield([TOKEN_X]) == {TOKEN_X: ["NOT_VERIFIED"]}
    assert dict(route.calls[0].request.url.params) == {"mints": TOKEN_X}
    assert await tokens.shield([TOKEN_X]) is None
    assert await tokens.shield([TOKEN_X]) is None
    assert len(tmp_ledger.raw_snapshots(source="jupiter.shield")) == 2


async def test_mint_account_decimals_program(tokens: TokenData) -> None:
    acc = await tokens.mint_account(TOKEN_X)
    assert acc is not None and acc["owner"] == TOKEN_PROGRAM
    assert (
        await tokens.decimals(TOKEN_X) == 6
        and await tokens.decimals(SOL_MINT) == 9
        and await tokens.decimals(USDC_MINT) == 6
    )
    assert await tokens.token_program(TOKEN_X) == TOKEN_PROGRAM
    with pytest.raises(ValueError):
        await tokens.decimals("missing")
