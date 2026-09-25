"""Small hot state as atomic JSON plus a single-instance lock with heartbeat.

``save_state`` writes to a temp file in the same directory, fsyncs it and renames it over
the target, so a crash mid-write leaves the previous file intact. ``InstanceLock`` writes
a JSON heartbeat; a lock whose heartbeat is older than ``stale_after`` is taken over.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator

from tiller.clock import Clock, ensure_utc
from tiller.models import DomainModel, OwnerLimits, PendingPost, _to_utc


class DonchianSleeveState(DomainModel):
    """Per-lookback Donchian sleeve: whether it is long and its trailing stop (USD price)."""

    n: int
    long: bool = False
    stop: float | None = None


class SleeveState(DomainModel):
    """Persisted per-strategy state so a restart continues from the last closed bar.

    ``last_bar_ts`` is the OPEN time of the last daily bar the strategy evaluated;
    ``donchian`` is keyed by ``str(N)`` (JSON-friendly); ``last_weight`` is the last
    emitted target weight (fraction of total equity).
    """

    last_bar_ts: datetime | None = None
    donchian: dict[str, DonchianSleeveState] = Field(default_factory=dict)
    regime_on: bool = False
    btc_regime_on: bool = False
    last_weight: float = 0.0
    rv90: float | None = None

    @field_validator("last_bar_ts")
    @classmethod
    def _utc(cls, v: datetime | None) -> datetime | None:
        return None if v is None else _to_utc(v)


class CanaryState(DomainModel):
    verified_buy: bool = False
    verified_sell: bool = False
    verified_post: bool = False
    ramp_step: int = 0
    ramp_step_started: datetime | None = None


class BrakeState(DomainModel):
    entries_blocked: bool = False
    reasons: list[str] = Field(default_factory=list)
    paused_until: datetime | None = None
    consecutive_failures: int = 0


class AgentState(DomainModel):
    """Everything the agent must remember across restarts that is not in the ledger."""

    sleeves: dict[str, SleeveState] = Field(default_factory=dict)
    pending_posts: list[PendingPost] = Field(default_factory=list)
    canary: CanaryState = Field(default_factory=CanaryState)
    brakes: BrakeState = Field(default_factory=BrakeState)
    halted: bool = False
    halt_reason: str | None = None
    last_reconcile_ok: bool = True
    reconcile_mismatch_ticks: int = 0
    last_owner_limits: OwnerLimits | None = None
    live_ack_recorded: bool = False


def load_state(path: Path) -> AgentState:
    """Load state; a missing file yields defaults, a corrupt file raises."""
    if not path.exists():
        return AgentState()
    with path.open("rb") as f:
        raw = json.load(f)
    return AgentState.model_validate(raw)


def atomic_write_text(path: Path, text: str) -> None:
    """tmp file + fsync + rename; never leaves a partially written target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        finally:
            pass
        raise
    _fsync_dir(path.parent)


def _fsync_dir(d: Path) -> None:
    try:
        dfd = os.open(d, os.O_RDONLY)
    except OSError:  # pragma: no cover - platform dependent
        return
    try:
        os.fsync(dfd)
    except OSError:  # pragma: no cover
        pass
    finally:
        os.close(dfd)


def save_state(path: Path, s: AgentState) -> None:
    atomic_write_text(path, s.model_dump_json(indent=1))


class LockHeld(RuntimeError):
    """Another live instance holds the lock and its heartbeat is fresh."""


class InstanceLock:
    """Single-instance lock file with a heartbeat; stale locks are taken over."""

    def __init__(self, path: Path, clock: Clock, stale_after: timedelta = timedelta(minutes=10)) -> None:
        self.path = path
        self.clock = clock
        self.stale_after = stale_after
        self.token = f"{os.getpid()}-{os.urandom(4).hex()}"
        self._held = False

    def _read(self) -> dict[str, Any] | None:
        try:
            with self.path.open("rb") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        return data if isinstance(data, dict) else None

    def _write(self) -> None:
        payload = {"token": self.token, "pid": os.getpid(), "heartbeat": self.clock.now().isoformat()}
        atomic_write_text(self.path, json.dumps(payload))

    def acquire(self) -> None:
        now = self.clock.now()
        existing = self._read()
        if existing is not None and existing.get("token") != self.token:
            hb_raw = existing.get("heartbeat")
            fresh = False
            if isinstance(hb_raw, str):
                try:
                    hb = ensure_utc(datetime.fromisoformat(hb_raw))
                    fresh = now - hb < self.stale_after
                except ValueError:
                    fresh = False
            if fresh:
                raise LockHeld(f"lock {self.path} held by pid {existing.get('pid')} (heartbeat {hb_raw})")
        self._write()
        self._held = True

    def heartbeat(self) -> None:
        if not self._held:
            raise RuntimeError("heartbeat on a lock that is not held")
        current = self._read()
        if current is not None and current.get("token") != self.token:
            raise LockHeld(f"lock {self.path} was taken over by pid {current.get('pid')}")
        self._write()

    def release(self) -> None:
        if not self._held:
            return
        current = self._read()
        if current is not None and current.get("token") == self.token:
            self.path.unlink(missing_ok=True)
        self._held = False

    def __enter__(self) -> InstanceLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
