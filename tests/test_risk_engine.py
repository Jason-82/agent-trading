"""Risk engine table tests: every brake on both sides of its threshold, directive prefix parsing,
fail-closed limits, tighten-only merge, canary gating and ramp reset, sizing binding caps,
loop guards, consecutive-failure pause, flatten triggers from the rolling 30-day peak."""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from tiller.models import SOL_MINT, DayStats, ExitRule, OwnerLimits, Position
from tiller.portfolio.allocator import OrderIntent
from tiller.risk.engine import (
    Limits,
    advance_canary,
    brake_state,
    check_entry,
    effective_limits,
    load_local_overrides,
    must_flatten,
    parse_directive,
    read_kill_file,
    record_failure,
    record_success,
    size_order,
)
from tiller.state import AgentState, BrakeState, CanaryState
from wpd_helpers import LAMPORTS, NOW, TOKEN_X, make_cfg, make_snapshot

CFG = make_cfg()
RISK = CFG.risk
FRESH = timedelta(hours=1)


def limits(**kw: object) -> Limits:
    return Limits(**kw)  # type: ignore[arg-type]


def owner(
    max_pos: str | None = None,
    daily: str | None = None,
    instructions: str | None = None,
    readable: bool = True,
) -> OwnerLimits:
    return OwnerLimits(
        max_position_usd=None if max_pos is None else Decimal(max_pos),
        daily_limit_usd=None if daily is None else Decimal(daily),
        instructions=instructions,
        readable=readable,
        read_at=NOW,
    )


def buy(
    usd: str = "500", mint: str = SOL_MINT, strategy: str = "core", exit: ExitRule | None = None
) -> OrderIntent:
    return OrderIntent(mint=mint, side="buy", usd=Decimal(usd), strategy=strategy, reason="t", exit=exit)


# --------------------------------------------------------------------------- directives


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (None, None),
        ("", None),
        ("Pause.", "pause"),
        ("pause trading for now", "pause"),
        ("Keep positions small. Stop trading until Monday.", "pause"),
        ("please do not pause", None),
        ("do not liquidate anything", None),
        ("LIQUIDATE", "liquidate"),
        ("sell all positions now", "liquidate"),
        ("pause; then liquidate", "pause"),  # "liquidate" is not sentence-initial here
        ("pause. liquidate", "liquidate"),
        ("paused is a word not a directive", None),
        ("pauses", None),
        ("unpause", None),
        ("the pause button", None),
        ("stop trading", "pause"),
        ("stop tradingx", None),
    ],
)
def test_parse_directive(text: str | None, expected: str | None) -> None:
    assert parse_directive(text) == expected


def test_load_local_overrides(tmp_path: Path) -> None:
    assert load_local_overrides(None, NOW) is None
    assert load_local_overrides(tmp_path / "missing.json", NOW) is None
    p = tmp_path / "o.json"
    p.write_text(json.dumps({"maxPositionUsd": 50, "dailyLimitUsd": None, "instructions": "pause"}))
    o = load_local_overrides(p, NOW)
    assert o is not None and o.readable and o.max_position_usd == Decimal(50) and o.daily_limit_usd is None
    assert o.instructions == "pause"
    p.write_text("{not json")
    bad = load_local_overrides(p, NOW)
    assert bad is not None and not bad.readable  # fail closed


# --------------------------------------------------------------------------- limits


def test_effective_limits_fail_closed_when_unreadable() -> None:
    canary = CanaryState()
    assert effective_limits(RISK, owner(readable=False), None, canary, NOW) is None
    assert effective_limits(RISK, owner("100"), owner(readable=False), canary, NOW) is None
    # no owner source at all (familiars disabled) is not fail-closed
    lim = effective_limits(RISK, None, None, canary, NOW, canary_enabled=False)
    assert lim is not None and lim.max_position_usd is None and lim.stage == "paper"


def test_effective_limits_tighten_only_min() -> None:
    lim = effective_limits(
        RISK, owner("100", "600"), owner("50", "1000", "pause"), CanaryState(), NOW, canary_enabled=False
    )
    assert lim is not None
    assert lim.max_position_usd == Decimal(50) and lim.daily_limit_usd == Decimal(600)
    assert lim.directive == "pause" and lim.sources == ["owner", "local"]
    lim2 = effective_limits(
        RISK, owner("100"), owner(instructions="liquidate"), CanaryState(), NOW, canary_enabled=False
    )
    assert lim2 is not None and lim2.directive == "liquidate" and lim2.max_position_usd == Decimal(100)


def test_canary_caps_until_verified_then_ramp() -> None:
    c = CanaryState()
    lim = effective_limits(RISK, owner("1000", "5000"), None, c, NOW)
    assert lim is not None and lim.stage == "canary"
    assert lim.max_position_usd == Decimal(10) and lim.daily_limit_usd == Decimal(30)
    # owner tighter than canary still wins
    lim_t = effective_limits(RISK, owner("5", "20"), None, c, NOW)
    assert lim_t is not None and lim_t.max_position_usd == Decimal(5) and lim_t.daily_limit_usd == Decimal(20)
    v = CanaryState(
        verified_buy=True, verified_sell=True, verified_post=True, ramp_step=0, ramp_step_started=NOW
    )
    lim0 = effective_limits(RISK, owner("1000", "5000"), None, v, NOW)
    assert lim0 is not None and lim0.stage == "ramp" and lim0.multiplier == Decimal("0.25")
    assert lim0.max_position_usd == Decimal(1000)
    lim2 = effective_limits(RISK, owner("1000"), None, v.model_copy(update={"ramp_step": 2}), NOW)
    assert lim2 is not None and lim2.stage == "full" and lim2.multiplier == Decimal(1)
    # missing one of the three verifications keeps the canary caps even with a ramp step
    partial = CanaryState(verified_buy=True, verified_sell=True, verified_post=False, ramp_step=2)
    limp = effective_limits(RISK, owner("1000"), None, partial, NOW)
    assert limp is not None and limp.stage == "canary"


def test_advance_canary_gating_weekly_steps_and_reset() -> None:
    cc = RISK.canary
    s = advance_canary(CanaryState(), (True, False, False), False, NOW, cc)
    assert s.verified_buy and not s.verified_sell and s.ramp_step == 0 and s.ramp_step_started is None
    s = advance_canary(s, (False, True, False), False, NOW, cc)
    assert s.ramp_step == 0 and s.ramp_step_started is None
    s = advance_canary(s, (False, False, True), False, NOW, cc)
    assert s.verified_post and s.ramp_step == 0 and s.ramp_step_started == NOW
    s = advance_canary(s, (False, False, False), False, NOW + timedelta(days=6), cc)
    assert s.ramp_step == 0
    s = advance_canary(s, (False, False, False), False, NOW + timedelta(days=7), cc)
    assert s.ramp_step == 1 and s.ramp_step_started == NOW + timedelta(days=7)
    s = advance_canary(s, (False, False, False), False, NOW + timedelta(days=14), cc)
    assert s.ramp_step == 2
    s = advance_canary(s, (False, False, False), False, NOW + timedelta(days=30), cc)
    assert s.ramp_step == 2  # never beyond the last step
    tripped = advance_canary(s, (False, False, False), True, NOW + timedelta(days=31), cc)
    assert tripped.ramp_step == 1 and tripped.ramp_step_started == NOW + timedelta(days=31)
    again = advance_canary(tripped, (False, False, False), True, NOW + timedelta(days=32), cc)
    again = advance_canary(again, (False, False, False), True, NOW + timedelta(days=33), cc)
    assert again.ramp_step == 0
    # verified flags are sticky: a later False never clears them
    assert again.verified_buy and again.verified_sell and again.verified_post


# --------------------------------------------------------------------------- brakes


def _brakes(acct: object, **kw: object) -> BrakeState:
    args = dict(cfg=RISK, data_age=FRESH, limits=limits(), error_rate=0.0, state=AgentState(), now=NOW)
    args.update(kw)
    return brake_state(acct, **args)  # type: ignore[arg-type]


def test_no_brake_in_normal_conditions() -> None:
    b = _brakes(make_snapshot())
    assert not b.entries_blocked and b.reasons == []


@pytest.mark.parametrize(("loss", "blocked"), [("0.049", False), ("0.05", True), ("0.10", True)])
def test_daily_loss_brake(loss: str, blocked: bool) -> None:
    start = Decimal(10_000)
    eq = start * (Decimal(1) - Decimal(loss))
    acct = make_snapshot(
        equity=eq,
        day=DayStats(start_equity=start, buys_usd=Decimal(0), swaps=0, entries=0),
        peak_7d=eq,
        peak_30d=eq,
    )
    b = _brakes(acct)
    assert b.entries_blocked is blocked
    assert any(r.startswith("daily_loss") for r in b.reasons) is blocked


@pytest.mark.parametrize(("dd", "blocked"), [("0.149", False), ("0.15", True)])
def test_dd7_brake(dd: str, blocked: bool) -> None:
    peak = Decimal(10_000)
    acct = make_snapshot(equity=peak * (1 - Decimal(dd)), peak_7d=peak, peak_30d=peak * Decimal("1.0"))
    b = _brakes(acct)
    assert any(r.startswith("dd7") for r in b.reasons) is blocked


@pytest.mark.parametrize(("dd", "blocked"), [("0.199", False), ("0.20", True)])
def test_dd30_entry_brake(dd: str, blocked: bool) -> None:
    peak = Decimal(10_000)
    eq = peak * (1 - Decimal(dd))
    acct = make_snapshot(equity=eq, peak_7d=eq, peak_30d=peak)
    b = _brakes(acct)
    assert any(r.startswith("dd30") for r in b.reasons) is blocked


@pytest.mark.parametrize(("hours", "blocked"), [(25.9, False), (26.1, True)])
def test_data_stale_brake(hours: float, blocked: bool) -> None:
    b = _brakes(make_snapshot(), data_age=timedelta(hours=hours))
    assert any(r.startswith("data_stale") for r in b.reasons) is blocked


def test_owner_limits_unreadable_and_directive_pause() -> None:
    assert "owner_limits_unreadable" in _brakes(make_snapshot(), limits=None).reasons
    assert "directive_pause" in _brakes(make_snapshot(), limits=limits(directive="pause")).reasons
    assert _brakes(make_snapshot(), limits=limits(directive="liquidate")).reasons == []  # flatten handles it


@pytest.mark.parametrize(("rate", "blocked"), [(0.5, False), (0.51, True)])
def test_error_rate_brake(rate: float, blocked: bool) -> None:
    b = _brakes(make_snapshot(), error_rate=rate)
    assert any(r.startswith("error_rate") for r in b.reasons) is blocked


def test_paused_halted_reconcile_and_kill_file() -> None:
    st = AgentState(brakes=BrakeState(paused_until=NOW + timedelta(minutes=1)))
    assert any(r.startswith("paused_until") for r in _brakes(make_snapshot(), state=st).reasons)
    expired = AgentState(brakes=BrakeState(paused_until=NOW - timedelta(minutes=1)))
    b = _brakes(make_snapshot(), state=expired)
    assert b.reasons == [] and b.paused_until is None
    assert any(
        r.startswith("halted")
        for r in _brakes(make_snapshot(), state=AgentState(halted=True, halt_reason="x")).reasons
    )
    assert _brakes(make_snapshot(), state=AgentState(reconcile_mismatch_ticks=1)).reasons == []
    assert any(
        r.startswith("reconcile_mismatch")
        for r in _brakes(make_snapshot(), state=AgentState(reconcile_mismatch_ticks=2)).reasons
    )
    assert "kill_file" in _brakes(make_snapshot(), kill_file=True).reasons


def test_record_failure_pauses_after_three() -> None:
    s = BrakeState()
    s = record_failure(s, NOW, RISK)
    s = record_failure(s, NOW, RISK)
    assert s.consecutive_failures == 2 and s.paused_until is None
    s = record_failure(s, NOW, RISK)
    assert s.paused_until == NOW + timedelta(minutes=60) and s.consecutive_failures == 0
    assert record_success(record_failure(BrakeState(), NOW, RISK)).consecutive_failures == 0


# --------------------------------------------------------------------------- entries


def test_check_entry_swaps_per_day_and_entries_per_tick() -> None:
    acct = make_snapshot(usdc=Decimal(6000))
    ok = check_entry(
        buy(),
        acct,
        BrakeState(),
        limits(),
        CFG,
        DayStats(start_equity=acct.equity_usd, buys_usd=Decimal(0), swaps=5, entries=0),
        set(),
    )
    assert ok.allowed
    full = check_entry(
        buy(),
        acct,
        BrakeState(),
        limits(),
        CFG,
        DayStats(start_equity=acct.equity_usd, buys_usd=Decimal(0), swaps=6, entries=0),
        set(),
    )
    assert not full.allowed and any("swaps_today" in r for r in full.reasons)
    tick = check_entry(buy(), acct, BrakeState(), limits(), CFG, acct.day, set(), entries_this_tick=1)
    assert not tick.allowed and any("entries_this_tick" in r for r in tick.reasons)


def test_check_entry_blocked_brakes_blocklist_positions_floor_reserve() -> None:
    acct = make_snapshot(usdc=Decimal(6000))
    d = check_entry(
        buy(), acct, BrakeState(entries_blocked=True, reasons=["dd7"]), limits(), CFG, acct.day, set()
    )
    assert not d.allowed and d.reasons == ["dd7"]
    d = check_entry(buy(mint=TOKEN_X), acct, BrakeState(), limits(), CFG, acct.day, {TOKEN_X})
    assert not d.allowed and "mint blocklisted" in d.reasons
    poss = [
        Position(mint=f"M{i}", amount_base=1, cost_usd=Decimal(1), opened_at=NOW, strategy="s")
        for i in range(4)
    ]
    d = check_entry(
        buy(mint=TOKEN_X),
        make_snapshot(usdc=Decimal(6000), positions=poss),
        BrakeState(),
        limits(),
        CFG,
        acct.day,
        set(),
    )
    assert not d.allowed and any("positions 4 >= 4" in r for r in d.reasons)
    # adding to an existing position is not a new position
    poss[0] = poss[0].model_copy(update={"mint": TOKEN_X})
    d = check_entry(
        buy(mint=TOKEN_X),
        make_snapshot(usdc=Decimal(6000), positions=poss),
        BrakeState(),
        limits(),
        CFG,
        acct.day,
        set(),
    )
    assert d.allowed
    # cash floor: equity ~9907.5, floor 35% = 3467.6; usdc 6000 - 2600 = 3400 < floor
    d = check_entry(buy("2600"), acct, BrakeState(), limits(), CFG, acct.day, set())
    assert not d.allowed and any("cash floor" in r for r in d.reasons)
    d = check_entry(
        buy(),
        make_snapshot(usdc=Decimal(6000), sol_lamports=10_000_000),
        BrakeState(),
        limits(),
        CFG,
        acct.day,
        set(),
    )
    assert not d.allowed and "SOL below gas reserve" in d.reasons
    d = check_entry(buy("9"), acct, BrakeState(), limits(), CFG, acct.day, set())
    assert not d.allowed and any("min" in r for r in d.reasons)
    sell = OrderIntent(mint=SOL_MINT, side="sell", usd=Decimal(100), strategy="core", reason="r")
    assert check_entry(
        sell, acct, BrakeState(entries_blocked=True, reasons=["x"]), limits(), CFG, acct.day, set()
    ).allowed


# --------------------------------------------------------------------------- sizing


def test_size_order_each_binding_cap() -> None:
    acct = make_snapshot(usdc=Decimal(6000))  # equity ~ 9907.5
    day = DayStats(start_equity=acct.equity_usd, buys_usd=Decimal(0), swaps=0, entries=0)
    eq = acct.equity_usd
    s = size_order(buy("500"), acct, limits(), CFG, None, day, None)
    assert s is not None and s.usd == Decimal(500) and s.binding_cap == "target"
    s = size_order(buy("5000"), acct, limits(), CFG, None, day, Decimal("0.20"))
    assert s is not None and s.binding_cap == "stop_risk"
    assert s.usd == (eq * Decimal("0.01") / Decimal("0.30")).quantize(Decimal("0.000001"))
    s = size_order(buy("5000", mint=TOKEN_X, strategy="copy_consensus"), acct, limits(), CFG, None, day, None)
    assert (
        s is not None
        and s.binding_cap == "max_token_pct"
        and s.usd == (Decimal("0.30") * eq).quantize(Decimal("0.000001"))
    )
    s = size_order(buy("5000"), acct, limits(), CFG, Decimal(100_000), day, None)
    assert s is not None and s.binding_cap == "pool_liquidity" and s.usd == Decimal(1000)
    s = size_order(buy("5000"), acct, limits(max_position_usd=Decimal(250)), CFG, None, day, None)
    assert s is not None and s.binding_cap == "max_position" and s.usd == Decimal(250)
    spent = DayStats(start_equity=eq, buys_usd=Decimal(400), swaps=2, entries=2)
    s = size_order(buy("5000"), acct, limits(daily_limit_usd=Decimal(600)), CFG, None, spent, None)
    assert s is not None and s.binding_cap == "daily_limit" and s.usd == Decimal(200)
    s = size_order(buy("5000"), acct, limits(), CFG, None, day, None, sleeve_budget_usd=Decimal(333))
    assert s is not None and s.binding_cap == "sleeve_budget" and s.usd == Decimal(333)
    s = size_order(buy("500"), acct, limits(multiplier=Decimal("0.25"), stage="ramp"), CFG, None, day, None)
    assert s is not None and s.binding_cap == "ramp" and s.usd == Decimal(125)
    assert size_order(buy("500"), acct, limits(max_position_usd=Decimal(9)), CFG, None, day, None) is None
    assert size_order(buy("30"), acct, limits(multiplier=Decimal("0.25")), CFG, None, day, None) is None
    # hold mint is exempt from the per-token cap
    s = size_order(buy("4000"), acct, limits(), CFG, None, day, None)
    assert s is not None and s.binding_cap == "target"


# --------------------------------------------------------------------------- flatten


def test_must_flatten_from_rolling_30d_peak_not_all_time() -> None:
    st = AgentState()
    acct = make_snapshot(equity=Decimal(750), peak_30d=Decimal(1000), peak_7d=Decimal(800))
    assert must_flatten(acct, CFG, None, False, st) is not None
    acct = make_snapshot(equity=Decimal(751), peak_30d=Decimal(1000), peak_7d=Decimal(800))
    assert must_flatten(acct, CFG, None, False, st) is None
    # an all-time peak of 2000 that has rolled out of the 30-day window does not count
    acct = make_snapshot(equity=Decimal(1200), peak_30d=Decimal(1500), peak_7d=Decimal(1500))
    assert must_flatten(acct, CFG, None, False, st) is None
    assert must_flatten(acct, CFG, "liquidate", False, st) == "owner directive: liquidate"
    assert must_flatten(acct, CFG, "pause", False, st) is None
    assert "KILL" in (must_flatten(acct, CFG, None, True, st) or "")


def test_read_kill_file(tmp_path: Path) -> None:
    k = tmp_path / "KILL"
    assert read_kill_file(k) == (False, False)
    k.touch()
    assert read_kill_file(k) == (True, False)
    k.write_text("LIQUIDATE now\n")
    assert read_kill_file(k) == (True, True)


def test_snapshot_drawdown_helper() -> None:
    acct = make_snapshot(equity=Decimal(90), peak_30d=Decimal(100))
    assert acct.drawdown_from(Decimal(100)) == Decimal("0.1")
    assert acct.drawdown_from(Decimal(0)) == 0
    assert make_snapshot(sol_lamports=LAMPORTS).drawdown_from(Decimal(1)) == 0
