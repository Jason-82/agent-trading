"""Strategy protocol: pure, synchronous targets over a market context."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal, Protocol, runtime_checkable

from pydantic import Field, field_validator

from tiller.models import Candle, DomainModel, ExitRule, Position, _to_utc
from tiller.state import SleeveState

ONE_DAY = timedelta(days=1)


class TargetExposure(DomainModel):
    """Desired holding of ``mint`` as a fraction of TOTAL equity (0.25 = 25%)."""

    mint: str
    weight: Decimal
    strategy: str
    reason: str
    exit: ExitRule | None = None


class MarketContext(DomainModel):
    """Everything a strategy may look at. ``candles`` is keyed by symbol ('SOL', 'BTC', ...)."""

    now: datetime
    candles: dict[str, list[Candle]]
    equity_usd: Decimal
    positions: list[Position] = Field(default_factory=list)
    sleeve_state: dict[str, SleeveState] = Field(default_factory=dict)
    regime_on: bool | None = None

    @field_validator("now")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _to_utc(v)


@runtime_checkable
class Strategy(Protocol):
    name: str
    cadence: Literal["daily", "monitor"]

    def targets(self, ctx: MarketContext) -> tuple[list[TargetExposure], SleeveState]:  # pragma: no cover
        ...


def closed_daily_bars(bars: list[Candle], now: datetime) -> list[Candle]:
    """Bars whose full 24 h have elapsed at ``now`` (``ts + 1 day <= now``), in ts order."""
    ordered = sorted(bars, key=lambda b: b.ts)
    return [b for b in ordered if b.ts + ONE_DAY <= now]
