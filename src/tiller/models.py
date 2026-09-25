"""Shared pydantic models for wire data and domain objects.

Conventions
-----------
* USD amounts and prices are ``Decimal``; token quantities in base units are ``int``
  (lamports for SOL, 1e-6 units for USDC); all datetimes are tz-aware UTC.
* Wire models (anything parsed from an HTTP response) use ``extra='ignore'`` with the
  fields we rely on marked required, so upstream schema drift fails loudly at parse time
  instead of silently producing zeros.
* Unix timestamps arriving as ints are interpreted by pydantic (seconds, or milliseconds
  when the magnitude says so); ISO-8601 strings are accepted as well.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SYSTEM_PROGRAM = "11111111111111111111111111111111"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"

LAMPORTS_PER_SOL = 1_000_000_000
USDC_DECIMALS = 6
SOL_DECIMALS = 9


def _to_utc(v: datetime) -> datetime:
    if v.tzinfo is None:
        return v.replace(tzinfo=UTC)
    return v.astimezone(UTC)


class WireModel(BaseModel):
    """Base for everything parsed from an external API."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, frozen=False)


class DomainModel(BaseModel):
    """Base for objects tiller creates itself (strict: unknown keys are a bug)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --------------------------------------------------------------------------- market data


class Candle(DomainModel):
    """One OHLCV bar. ``ts`` is the bar OPEN time (UTC); prices in USD(T), volume in base asset."""

    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    @field_validator("ts")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _to_utc(v)


# --------------------------------------------------------------------------- orders / fills


class SwapRequest(DomainModel):
    """Intent to swap ``amount_base`` base units of ``input_mint`` into ``output_mint``."""

    input_mint: str
    output_mint: str
    amount_base: int = Field(gt=0)
    strategy: str
    reason: str
    mode: Literal["normal", "emergency"] = "normal"


class Order(WireModel):
    """Parsed Jupiter Swap V2 ``/order`` response. Amounts in base units, impact as a fraction."""

    transaction_b64: str = Field(alias="transaction")
    request_id: str = Field(alias="requestId")
    in_amount: int = Field(alias="inAmount")
    out_amount: int = Field(alias="outAmount")
    slippage_bps: int = Field(alias="slippageBps")
    price_impact_pct: Decimal = Field(alias="priceImpactPct")
    fee_bps: int = Field(alias="feeBps")
    router: str
    raw: dict[str, Any] = Field(default_factory=dict)


class Fill(DomainModel):
    """A realised (or paper) swap. ``usd_in``/``usd_out`` are fair USD values of each leg."""

    signature: str | None
    in_mint: str
    out_mint: str
    in_base: int
    out_base: int
    usd_in: Decimal
    usd_out: Decimal
    fee_usd: Decimal
    ts: datetime
    paper: bool
    strategy: str
    quote_out_base: int
    round_trip_probe_pct: Decimal | None = None
    mode: Literal["normal", "emergency"] = "normal"

    @field_validator("ts")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _to_utc(v)


class ExitRule(DomainModel):
    """Exit parameters attached to a position. Percentages are fractions (0.20 = 20%)."""

    stop_price: Decimal | None = None
    trail_pct: Decimal | None = None
    trail_from_gain_pct: Decimal | None = None
    time_stop_at: datetime | None = None
    ema_exit: bool = False


class Position(DomainModel):
    """Open holding of ``amount_base`` base units of ``mint`` with USD cost basis."""

    mint: str
    amount_base: int
    cost_usd: Decimal
    opened_at: datetime
    strategy: str
    exit: ExitRule | None = None

    @field_validator("opened_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _to_utc(v)


class TokenAccount(WireModel):
    """A parsed SPL / Token-2022 token account owned by some wallet."""

    pubkey: str
    mint: str
    amount_base: int
    owner: str
    delegate: str | None = None
    close_authority: str | None = None
    program: str


# --------------------------------------------------------------------------- token metadata


class AuditInfo(WireModel):
    """Jupiter Tokens V2 ``audit`` block."""

    mint_authority_disabled: bool | None = Field(default=None, alias="mintAuthorityDisabled")
    freeze_authority_disabled: bool | None = Field(default=None, alias="freezeAuthorityDisabled")
    top_holders_pct: float | None = Field(default=None, alias="topHoldersPercentage")
    dev_balance_pct: float | None = Field(default=None, alias="devBalancePercentage")
    is_sus: bool | None = Field(default=None, alias="isSus")


class TokenInfo(WireModel):
    """Jupiter Tokens V2 search result (one token)."""

    mint: str = Field(alias="id")
    symbol: str
    organic_score: float | None = Field(default=None, alias="organicScore")
    audit: AuditInfo | None = None
    holder_count: int | None = Field(default=None, alias="holderCount")
    liquidity_usd: Decimal | None = Field(default=None, alias="liquidity")
    mcap_usd: Decimal | None = Field(default=None, alias="mcap")
    first_pool_created_at: datetime | None = Field(default=None, alias="firstPoolCreatedAt")
    launchpad: str | None = None
    dev: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_wire(cls, payload: dict[str, Any]) -> TokenInfo:
        """Parse a raw Tokens V2 object, flattening ``firstPool.createdAt`` and keeping ``raw``."""
        data = dict(payload)
        first_pool = data.get("firstPool")
        if isinstance(first_pool, dict) and "firstPoolCreatedAt" not in data:
            data["firstPoolCreatedAt"] = first_pool.get("createdAt")
        data["raw"] = payload
        return cls.model_validate(data)


# --------------------------------------------------------------------------- familiars wire


class PublicAgent(WireModel):
    """One row of ``GET /api/agents``."""

    handle: str
    name: str | None = None
    wallet: str
    hosted: bool
    equity_usd: Decimal = Field(alias="equityUsd")
    pnl: dict[str, Decimal]
    drawdown: Decimal | None = None
    win_rate: Decimal | None = Field(default=None, alias="winRate")
    trades: int
    last_trade_at: datetime | None = Field(default=None, alias="lastTradeAt")
    joined_at: datetime | None = Field(default=None, alias="joinedAt")


class AgentTrade(WireModel):
    """One trade from ``AgentDetail.trades``."""

    signature: str
    kind: Literal["buy", "sell", "swap"]
    time: datetime
    amount: Decimal
    usd_value: Decimal = Field(alias="usdValue")
    token: str
    quote: str | None = None


class AgentPosition(WireModel):
    """One open position from ``AgentDetail.positions``."""

    token: str
    amount: Decimal
    value_usd: Decimal | None = Field(default=None, alias="valueUsd")
    unrealized_usd: Decimal | None = Field(default=None, alias="unrealizedUsd")


class EquityPoint(WireModel):
    """Equity snapshot: ``pnl_usd`` = equity minus net deposits."""

    ts: datetime = Field(alias="timestamp")
    equity_usd: Decimal = Field(alias="equityUsd")
    net_deposits_usd: Decimal = Field(alias="netDepositsUsd")
    pnl_usd: Decimal = Field(alias="pnlUsd")

    @field_validator("ts")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _to_utc(v)


class AgentDetail(WireModel):
    """``GET /api/agents/{handle}``."""

    agent: PublicAgent
    cash_usd: Decimal | None = Field(default=None, alias="cashUsd")
    sol_balance: Decimal | None = Field(default=None, alias="solBalance")
    positions: list[AgentPosition] = Field(default_factory=list)
    trades: list[AgentTrade] = Field(default_factory=list)
    posts: list[dict[str, Any]] = Field(default_factory=list)
    history: list[EquityPoint] = Field(default_factory=list)

    @field_validator("history", mode="before")
    @classmethod
    def _unwrap_history(cls, v: Any) -> Any:
        # The board returns {source, asOf, snapshots: [...]}; we keep only the snapshots.
        if isinstance(v, dict):
            return v.get("snapshots") or []
        return v


class TokenInfoBoard(WireModel):
    """One row of familiars ``GET /api/tokens``."""

    mint: str
    symbol: str | None = None
    agents: int
    last_trade_at: datetime | None = Field(default=None, alias="lastTradeAt")
    price_usd: Decimal | None = Field(default=None, alias="priceUsd")
    liquidity_usd: Decimal | None = Field(default=None, alias="liquidityUsd")


class OwnerLimits(DomainModel):
    """Owner-set caps read from ``/api/agent/me`` (or the local override file).

    ``readable=False`` means the source could not be read; entries must then fail closed.
    """

    max_position_usd: Decimal | None = None
    daily_limit_usd: Decimal | None = None
    instructions: str | None = None
    readable: bool
    read_at: datetime

    @field_validator("read_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _to_utc(v)


class TradeContext(DomainModel):
    """Numbers-and-enums-only context handed to the narrator (never third-party text)."""

    strategy: str
    rule: str
    side: Literal["buy", "sell"]
    symbol: str
    mint: str
    notional_usd: Decimal
    price_usd: Decimal
    stop_price: Decimal | None = None
    regime_on: bool | None = None
    brake_state: str
    paper: bool
    signature: str | None = None


# --------------------------------------------------------------------------- ledger helpers


class DayStats(DomainModel):
    """Per-UTC-day ledger statistics used by the risk engine."""

    start_equity: Decimal
    buys_usd: Decimal
    swaps: int
    entries: int


class PendingPost(DomainModel):
    """A queued board post keyed by swap signature (or a synthetic key for notes)."""

    signature: str
    kind: Literal["note", "callout", "trade"]
    text: str
    mint: str | None = None
    state: Literal["queued", "posted", "uncertain", "failed"]
    attempts: int = 0
    not_before: datetime | None = None
