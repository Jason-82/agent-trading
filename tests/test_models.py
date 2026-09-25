"""Wire/domain model parsing: documented familiars and Jupiter shapes, strictness, Decimal round trips."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from tiller.models import (
    SOL_MINT,
    USDC_MINT,
    AgentDetail,
    AgentTrade,
    Candle,
    EquityPoint,
    Fill,
    Order,
    OwnerLimits,
    Position,
    PublicAgent,
    SwapRequest,
    TokenInfo,
    TokenInfoBoard,
    TradeContext,
)

PUBLIC_AGENT = {
    "handle": "tiller_test",
    "name": "Tiller",
    "strategy": "daily trend",
    "wallet": "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin",
    "hosted": False,
    "equityUsd": "1234.56",
    "pnl": {"24H": "1.5", "7D": "-3.25", "30D": "10", "ALL": "12.75"},
    "drawdown": "-0.12",
    "winRate": "0.55",
    "trades": 42,
    "lastTradeAt": "2026-09-24T10:00:00Z",
    "joinedAt": 1758000000,
    "someNewField": {"ignored": True},
}

ORDER = {
    "transaction": "AQAAAA==",
    "requestId": "req-1",
    "inAmount": "10000000",
    "outAmount": "49500000",
    "slippageBps": 50,
    "priceImpactPct": "0.0012",
    "feeBps": 2,
    "router": "metis",
    "mode": "manual",
    "unknown": 1,
}


def test_public_agent_parses_with_aliases_and_ignores_extras() -> None:
    a = PublicAgent.model_validate(PUBLIC_AGENT)
    assert a.equity_usd == Decimal("1234.56")
    assert a.pnl["7D"] == Decimal("-3.25")
    assert a.win_rate == Decimal("0.55")
    assert a.last_trade_at == datetime(2026, 9, 24, 10, 0, tzinfo=UTC)
    assert a.joined_at is not None and a.joined_at.tzinfo is not None
    assert not hasattr(a, "someNewField")


def test_missing_required_field_raises() -> None:
    bad = dict(PUBLIC_AGENT)
    del bad["equityUsd"]
    with pytest.raises(ValidationError):
        PublicAgent.model_validate(bad)


def test_agent_detail_unwraps_history_snapshots() -> None:
    detail = AgentDetail.model_validate(
        {
            "agent": PUBLIC_AGENT,
            "cashUsd": "100.5",
            "solBalance": "1.25",
            "positions": [{"token": SOL_MINT, "amount": "2.5", "valueUsd": "500", "unrealizedUsd": "12"}],
            "trades": [
                {
                    "signature": "5" * 64,
                    "kind": "buy",
                    "time": "2026-09-24T09:00:00Z",
                    "amount": "2.5",
                    "usdValue": "500",
                    "token": SOL_MINT,
                    "quote": USDC_MINT,
                }
            ],
            "posts": [{"kind": "note", "text": "hi"}],
            "history": {
                "source": "platform",
                "asOf": "2026-09-24T10:00:00Z",
                "snapshots": [
                    {"timestamp": 1758700000, "equityUsd": "1000", "netDepositsUsd": "900", "pnlUsd": "100"}
                ],
            },
        }
    )
    assert detail.cash_usd == Decimal("100.5")
    assert detail.positions[0].value_usd == Decimal(500)
    assert isinstance(detail.trades[0], AgentTrade)
    assert isinstance(detail.history[0], EquityPoint)
    assert detail.history[0].pnl_usd == Decimal(100)
    assert detail.history[0].ts.tzinfo is not None


def test_order_parses_jupiter_shape() -> None:
    o = Order.model_validate(ORDER)
    assert o.in_amount == 10_000_000 and o.out_amount == 49_500_000
    assert o.price_impact_pct == Decimal("0.0012")
    assert o.router == "metis" and o.fee_bps == 2
    assert Order.model_validate({**ORDER, "raw": ORDER}).raw["requestId"] == "req-1"


def test_order_missing_field_raises() -> None:
    bad = dict(ORDER)
    del bad["requestId"]
    with pytest.raises(ValidationError):
        Order.model_validate(bad)


def test_token_info_from_wire_flattens_first_pool() -> None:
    info = TokenInfo.from_wire(
        {
            "id": SOL_MINT,
            "symbol": "SOL",
            "organicScore": 99.5,
            "audit": {
                "mintAuthorityDisabled": True,
                "freezeAuthorityDisabled": True,
                "topHoldersPercentage": 12.5,
            },
            "holderCount": 1000000,
            "liquidity": "123456789.12",
            "mcap": "90000000000",
            "firstPool": {"id": "x", "createdAt": "2021-03-01T00:00:00Z"},
            "dev": None,
        }
    )
    assert info.mint == SOL_MINT
    assert info.audit is not None and info.audit.top_holders_pct == 12.5
    assert info.first_pool_created_at == datetime(2021, 3, 1, tzinfo=UTC)
    assert info.liquidity_usd == Decimal("123456789.12")
    assert info.raw["holderCount"] == 1000000


def test_token_info_board_and_owner_limits() -> None:
    t = TokenInfoBoard.model_validate({"mint": SOL_MINT, "symbol": "SOL", "agents": 12, "lastTradeAt": None})
    assert t.agents == 12 and t.last_trade_at is None
    lim = OwnerLimits(
        max_position_usd=Decimal("25"),
        daily_limit_usd=None,
        instructions="pause",
        readable=True,
        read_at=datetime(2026, 9, 24, tzinfo=UTC),
    )
    assert lim.max_position_usd == Decimal(25)


def test_candle_and_fill_decimal_round_trip() -> None:
    c = Candle(
        ts=datetime(2026, 9, 23, tzinfo=UTC), open="1.10", high="1.20", low="1.00", close="1.15", volume="10"
    )
    assert c.close == Decimal("1.15")
    assert Candle.model_validate_json(c.model_dump_json()) == c
    f = Fill(
        signature=None,
        in_mint=USDC_MINT,
        out_mint=SOL_MINT,
        in_base=10_000_000,
        out_base=50_000_000,
        usd_in=Decimal("10.00"),
        usd_out=Decimal("9.97"),
        fee_usd=Decimal("0.03"),
        ts=datetime(2026, 9, 24, 1, tzinfo=UTC),
        paper=True,
        strategy="sol_trend_ensemble",
        quote_out_base=50_100_000,
    )
    f2 = Fill.model_validate_json(f.model_dump_json())
    assert f2 == f and f2.usd_out == Decimal("9.97") and f2.mode == "normal"


def test_naive_datetime_becomes_utc() -> None:
    p = Position(
        mint=SOL_MINT, amount_base=1, cost_usd=Decimal(1), opened_at=datetime(2026, 1, 1), strategy="s"
    )
    assert p.opened_at.tzinfo is UTC


def test_domain_models_are_strict() -> None:
    with pytest.raises(ValidationError):
        SwapRequest(
            input_mint=USDC_MINT, output_mint=SOL_MINT, amount_base=1, strategy="s", reason="r", bogus=1
        )
    with pytest.raises(ValidationError):
        SwapRequest(input_mint=USDC_MINT, output_mint=SOL_MINT, amount_base=0, strategy="s", reason="r")
    ctx = TradeContext(
        strategy="sol_trend_ensemble",
        rule="donchian",
        side="buy",
        symbol="SOL",
        mint=SOL_MINT,
        notional_usd=Decimal(10),
        price_usd=Decimal("150.5"),
        brake_state="clear",
        paper=True,
    )
    assert ctx.stop_price is None
