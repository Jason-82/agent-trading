"""Copy-module models: leader trades, leader scores, shadow trades and the shadow report.

Percentages are fractions (0.01 = 1%), USD values are ``Decimal``, times are UTC.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator

from tiller.models import DomainModel, ExitRule, _to_utc


class LeaderTrade(DomainModel):
    """A leader's swap as observed by one of our feeds.

    ``key`` is ``'fam:<handle>'`` or ``'wallet:<addr>'``; ``chain_verified`` is True only
    after ``getTransaction`` confirmed the signature.
    """

    key: str
    wallet: str
    signature: str
    ts: datetime
    detected_at: datetime
    side: Literal["buy", "sell"]
    mint: str
    usd_value: Decimal
    amount: Decimal
    price_usd: Decimal | None = None
    source: Literal["familiars", "wallet"]
    chain_verified: bool = False

    @field_validator("ts", "detected_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _to_utc(v)


class LeaderScore(DomainModel):
    """Result of eligibility + replay scoring for one leader (board pnl/winRate never used)."""

    key: str
    wallet: str
    qualified: bool
    reasons: list[str] = Field(default_factory=list)
    score: float
    cluster_id: int
    replay_pf: float | None = None
    replay_expectancy_pct: Decimal | None = None
    n_replayed: int = 0


class ShadowTrade(DomainModel):
    """A hypothetical copied trade (zero capital) opened by the shadow tracker."""

    id: str
    leader_key: str
    signal_ts: datetime
    mint: str
    leader_price: Decimal
    entry_price: Decimal
    lag_s: float
    lag_cost_pct: Decimal
    size_usd: Decimal
    exit: ExitRule
    marks: dict[str, Decimal] = Field(default_factory=dict)
    exit_price: Decimal | None = None
    exit_ts: datetime | None = None
    exit_reason: str | None = None
    pnl_pct: Decimal | None = None

    @field_validator("signal_ts")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _to_utc(v)


class LeaderStats(DomainModel):
    """Per-leader aggregate of copied shadow P&L."""

    key: str
    n_trades: int
    copied_pnl_pct: Decimal
    positive: bool


class ShadowReport(DomainModel):
    """Promotion-gate report over the shadow book."""

    days: int
    n_trades: int
    expectancy_pct: Decimal
    profit_factor: float
    median_lag_s: float
    median_lag_cost_pct: Decimal
    p_positive_block20: float
    leaders_positive_share: float
    per_leader: list[LeaderStats] = Field(default_factory=list)
    promotion_gate_met: bool
    reasons: list[str] = Field(default_factory=list)
