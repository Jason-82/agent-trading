"""Injectable time.

Every timestamp handled by tiller is a timezone-aware UTC ``datetime``. Code never calls
``datetime.now()`` directly; it asks a :class:`Clock` so tests can drive time with
:class:`SimClock`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Source of the current time (tz-aware UTC)."""

    def now(self) -> datetime:  # pragma: no cover - protocol
        ...


class SystemClock:
    """Wall clock in UTC."""

    def now(self) -> datetime:
        return datetime.now(tz=UTC)


class SimClock:
    """Manually driven clock for tests and offline runs."""

    def __init__(self, start: datetime) -> None:
        self._t = ensure_utc(start)

    def now(self) -> datetime:
        return self._t

    def advance(self, delta: timedelta) -> None:
        """Move the clock forward by ``delta`` (negative deltas are refused)."""
        if delta < timedelta(0):
            raise ValueError("SimClock cannot move backwards")
        self._t = self._t + delta

    def set(self, t: datetime) -> None:
        self._t = ensure_utc(t)


def ensure_utc(t: datetime) -> datetime:
    """Return ``t`` as a tz-aware UTC datetime; naive datetimes are rejected."""
    if t.tzinfo is None or t.utcoffset() is None:
        raise ValueError("naive datetime; tiller requires tz-aware UTC datetimes")
    return t.astimezone(UTC)


def utc_day_start(t: datetime) -> datetime:
    """00:00 UTC of the day containing ``t``."""
    t = ensure_utc(t)
    return t.replace(hour=0, minute=0, second=0, microsecond=0)
