"""Synthetic fixtures and (when present) the live captures from ``tools/record_fixtures.py`` parse
into the SAME pydantic models / parsers, so API drift shows up here before it reaches the engine.

Recorded fixtures live in ``tests/fixtures/recorded/`` (absent by default) wrapped as
``{"_meta": {...}, "body": ...}``; each case is skipped when its file is missing.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from tiller.data.candles import parse_coinbase_candles, parse_kraken_ohlc
from tiller.data.prices import parse_kraken_ticker_mid
from tiller.data.tokens import mint_decimals, parse_shield
from tiller.execution.rpc import parse_token_account_value
from tiller.familiars.client import MeResponse
from tiller.models import SOL_MINT, USDC_MINT, AgentDetail, Order, PublicAgent, TokenInfo, TokenInfoBoard

FIXTURES = Path(__file__).parent / "fixtures"
RECORDED = FIXTURES / "recorded"
SECRET_MARKERS = ("fam_", "own_", "jup_", "sk-ant-", "api-key=", "apiKey")


def _synthetic(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _recorded(name: str) -> Any:
    path = RECORDED / f"{name}.json"
    if not path.exists():
        pytest.skip(f"recorded fixture {path.name} absent (run tools/record_fixtures.py online)")
    text = path.read_text()
    for marker in SECRET_MARKERS:
        assert f'"{marker}' not in text.replace("[REDACTED]", ""), (
            f"{path.name} carries an unredacted {marker}"
        )
    data = json.loads(text)
    assert data["_meta"]["synthetic"] is False
    return data["body"]


# --------------------------------------------------------------------------- checkers (one per shape)


def check_kraken_ohlc(payload: Any) -> None:
    bars = parse_kraken_ohlc(payload)
    assert bars and all(b.high >= b.low and b.close > 0 for b in bars)
    assert [b.ts for b in bars] == sorted(b.ts for b in bars)


def check_kraken_ticker(payload: Any) -> None:
    assert parse_kraken_ticker_mid(payload) > 0


def check_coinbase(payload: Any) -> None:
    bars = parse_coinbase_candles(payload)
    assert bars and all(b.high >= b.low for b in bars)


def check_order(payload: Any) -> None:
    data = dict(payload)
    data.setdefault("transaction", data.get("transaction") or "AA==")  # a taker-less quote has none
    data["raw"] = payload
    order = Order.model_validate(data)
    assert order.in_amount > 0 and order.out_amount > 0 and order.router
    assert Decimal(order.price_impact_pct) >= 0


def check_price_v3(payload: Any) -> None:
    assert isinstance(payload, dict)
    sol = payload.get(SOL_MINT)
    assert isinstance(sol, dict) and Decimal(str(sol["usdPrice"])) > 0


def check_tokens_v2(payload: Any) -> None:
    assert isinstance(payload, list) and payload
    info = next(TokenInfo.from_wire(i) for i in payload if i.get("id") == SOL_MINT)
    assert info.symbol and info.mint == SOL_MINT


def check_shield(payload: Any) -> None:
    parsed = parse_shield(payload, [SOL_MINT])
    assert parsed is not None and SOL_MINT in parsed


def check_agents(payload: Any) -> None:
    rows = payload.get("agents") if isinstance(payload, dict) else payload
    assert isinstance(rows, list) and rows
    agents = [PublicAgent.model_validate(r) for r in rows]
    assert all(a.handle and a.wallet for a in agents)


def check_tokens_board(payload: Any) -> None:
    rows = payload.get("tokens") if isinstance(payload, dict) else payload
    assert isinstance(rows, list) and rows
    assert all(TokenInfoBoard.model_validate(r).mint for r in rows)


def check_agent_detail(payload: Any) -> None:
    detail = AgentDetail.model_validate(payload)
    assert detail.agent.handle


def check_me(payload: Any) -> None:
    me = MeResponse.model_validate(payload)
    assert me.handle


def check_mint_account(payload: Any) -> None:
    value = payload["result"]["value"]
    assert mint_decimals(value) == 9


def check_token_accounts(payload: Any) -> None:
    rows = payload["result"]["value"]
    parsed = [parse_token_account_value(r["pubkey"], r["account"]) for r in rows]
    assert all(p is None or p.mint for p in parsed)
    assert any(p is not None for p in parsed) or not rows


CASES: list[tuple[str, str, Any]] = [
    # (synthetic fixture, recorded fixture, checker)
    ("kraken/ohlc_solusd", "kraken_ohlc_solusd", check_kraken_ohlc),
    ("kraken/ohlc_xbtusd", "kraken_ohlc_xbtusd", check_kraken_ohlc),
    ("kraken/ticker_solusd", "kraken_ticker_solusd", check_kraken_ticker),
    ("coinbase/candles_solusd", "coinbase_candles_solusd", check_coinbase),
    ("jupiter/order_ok", "jupiter_order_sol_usdc", check_order),
    ("jupiter/price_v3", "jupiter_price_v3", check_price_v3),
    ("jupiter/tokens_v2_sol", "jupiter_tokens_v2_sol", check_tokens_v2),
    ("jupiter/shield_clean", "jupiter_shield_sol", check_shield),
    ("familiars/agents_7d", "familiars_agents_7d", check_agents),
    ("familiars/tokens", "familiars_tokens", check_tokens_board),
    ("familiars/agent_detail", "familiars_agent_detail", check_agent_detail),
    ("familiars/me", "familiars_me", check_me),
    ("rpc/mint_spl_sol", "rpc_account_info_sol_mint", check_mint_account),
    ("rpc/token_accounts_spl", "rpc_token_accounts_by_owner", check_token_accounts),
]


@pytest.mark.parametrize(("synthetic", "_recorded_name", "check"), CASES, ids=[c[0] for c in CASES])
def test_synthetic_fixture_parses(synthetic: str, _recorded_name: str, check: Any) -> None:
    check(_synthetic(synthetic))


@pytest.mark.parametrize(("_synthetic", "recorded", "check"), CASES, ids=[c[1] for c in CASES])
def test_recorded_fixture_parses_like_synthetic(_synthetic: str, recorded: str, check: Any) -> None:
    check(_recorded(recorded))


def test_shield_fixture_mentions_usdc_free_sol() -> None:
    """Sanity: the synthetic shield fixture and the SOL/USDC allowlist never disagree."""
    parsed = parse_shield(_synthetic("jupiter/shield_ok"), [SOL_MINT, USDC_MINT])
    assert parsed is None or all(isinstance(v, list) for v in parsed.values())
