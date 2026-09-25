"""Atomic state persistence and the single-instance lock."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tiller.clock import SimClock
from tiller.state import (
    AgentState,
    BrakeState,
    DonchianSleeveState,
    InstanceLock,
    LockHeld,
    SleeveState,
    atomic_write_text,
    load_state,
    save_state,
)


def test_missing_state_file_gives_defaults(tmp_state: Path) -> None:
    s = load_state(tmp_state)
    assert s == AgentState()
    assert s.halted is False and s.canary.ramp_step == 0


def test_state_round_trip(tmp_state: Path) -> None:
    s = AgentState(
        sleeves={
            "sol_trend_ensemble": SleeveState(
                last_bar_ts=datetime(2026, 9, 23, tzinfo=UTC),
                donchian={"10": DonchianSleeveState(n=10, long=True, stop=180.25)},
                last_weight=0.1428,
                rv90=0.7,
            )
        },
        brakes=BrakeState(
            entries_blocked=True, reasons=["dd7"], paused_until=datetime(2026, 9, 24, 1, tzinfo=UTC)
        ),
        halted=True,
        halt_reason="dd30",
        reconcile_mismatch_ticks=1,
    )
    save_state(tmp_state, s)
    assert load_state(tmp_state) == s
    assert oct(tmp_state.stat().st_mode & 0o777) == "0o600"
    assert not [p for p in tmp_state.parent.iterdir() if ".tmp-" in p.name]


def test_interrupted_write_leaves_old_file_intact(tmp_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    save_state(tmp_state, AgentState(halted=False, halt_reason="first"))
    before = tmp_state.read_text()

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("disk full before rename")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_text(tmp_state, "garbage")
    assert tmp_state.read_text() == before
    assert json.loads(before)["halt_reason"] == "first"
    assert not [p for p in tmp_state.parent.iterdir() if ".tmp-" in p.name]


def test_lock_contention_and_release(tmp_path: Path) -> None:
    clock = SimClock(datetime(2026, 9, 24, tzinfo=UTC))
    a = InstanceLock(tmp_path / "lock", clock)
    b = InstanceLock(tmp_path / "lock", clock)
    a.acquire()
    with pytest.raises(LockHeld):
        b.acquire()
    clock.advance(timedelta(minutes=5))
    a.heartbeat()
    clock.advance(timedelta(minutes=6))  # 6 min since heartbeat: still fresh
    with pytest.raises(LockHeld):
        b.acquire()
    a.release()
    assert not (tmp_path / "lock").exists()
    b.acquire()
    b.release()


def test_stale_lock_is_taken_over(tmp_path: Path) -> None:
    clock = SimClock(datetime(2026, 9, 24, tzinfo=UTC))
    a = InstanceLock(tmp_path / "lock", clock)
    a.acquire()
    clock.advance(timedelta(minutes=11))  # heartbeat older than 10 min
    b = InstanceLock(tmp_path / "lock", clock)
    b.acquire()
    with pytest.raises(LockHeld):
        a.heartbeat()  # the old holder notices it lost the lock
    a.release()  # must not delete b's lock
    assert (tmp_path / "lock").exists()
    b.release()


def test_corrupt_lock_file_is_replaced(tmp_path: Path) -> None:
    (tmp_path / "lock").write_text("not json")
    lock = InstanceLock(tmp_path / "lock", SimClock(datetime(2026, 9, 24, tzinfo=UTC)))
    lock.acquire()
    lock.release()
