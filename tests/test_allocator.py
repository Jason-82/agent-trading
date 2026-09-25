"""Allocator: A+B netting, beta cap, cash floor scaling, band, exits first, one entry, disabled sleeve."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from tiller.models import SOL_MINT, ExitRule, Position
from tiller.portfolio import allocator as alloc
from tiller.portfolio.allocator import (
    CORE_STRATEGY,
    OrderIntent,
    exit_fired,
    net_targets,
    plan,
    ratchet_trailing_stop,
    rebalance_band_usd,
    sol_beta_cap,
)
from tiller.strategies.base import TargetExposure
from wpd_helpers import LAMPORTS, NOW, TOKEN_X, TOKEN_Y, make_cfg, make_snapshot


def tgt(
    weight: str, strategy: str = "sol_trend_ensemble", mint: str = SOL_MINT, exit: ExitRule | None = None
) -> TargetExposure:
    return TargetExposure(mint=mint, weight=Decimal(weight), strategy=strategy, reason="t", exit=exit)


def test_sol_beta_cap() -> None:
    cfg = make_cfg().risk
    assert sol_beta_cap(0.5, cfg) == pytest.approx(0.5)
    assert sol_beta_cap(0.8, cfg) == pytest.approx(0.375)
    assert sol_beta_cap(0.01, cfg) == pytest.approx(0.5)  # sigma floored at 0.05
    assert sol_beta_cap(None, cfg) == pytest.approx(0.5)


def test_band_is_max_of_pct_and_min_order() -> None:
    cfg = make_cfg().risk
    assert rebalance_band_usd(Decimal(10_000), cfg) == Decimal(200)
    assert rebalance_band_usd(Decimal(100), cfg) == Decimal(10)


def test_a_plus_b_netting_into_one_core_intent() -> None:
    cfg = make_cfg()
    acct = make_snapshot(usdc=Decimal(10_000), sol_lamports=50_000_000)  # only the reserve
    out = plan([tgt("0.30"), tgt("0.25", "sol_regime_switch")], acct, 0.5, cfg)
    assert len(out) == 1
    o = out[0]
    assert o.side == "buy" and o.mint == SOL_MINT and o.strategy == CORE_STRATEGY
    # 0.55 capped by the beta cap 0.5 (sigma 0.5 -> min(0.5, 0.6)); equity = 10000 + 7.5 (reserve)
    assert o.usd == pytest.approx(Decimal("0.5") * acct.equity_usd, abs=Decimal("0.001"))
    assert "beta cap" in o.reason


def test_beta_cap_binds_with_high_vol() -> None:
    cfg = make_cfg()
    netted = net_targets([tgt("0.40"), tgt("0.25", "sol_regime_switch")], 0.8, cfg)
    assert netted[SOL_MINT].weight == Decimal(str(sol_beta_cap(0.8, cfg.risk)))
    assert netted[SOL_MINT].weight == pytest.approx(Decimal("0.375"))


def test_sleeve_budget_clips_each_strategy() -> None:
    cfg = make_cfg()
    netted = net_targets([tgt("0.90"), tgt("0.90", "sol_regime_switch")], 0.1, cfg)
    # A clipped to 40%, B to 25%, sum 65% then beta cap 0.5
    assert netted[SOL_MINT].weight == Decimal("0.5")
    netted2 = net_targets([tgt("0.90")], 0.1, cfg)
    assert netted2[SOL_MINT].weight == Decimal("0.4")


def test_cash_floor_scaling_pro_rata(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_cfg(copy={"live": True})
    monkeypatch.setattr(alloc, "cash_floor_fraction", lambda _cfg: Decimal("0.80"))
    netted = net_targets([tgt("0.30"), tgt("0.05", "copy_consensus", mint=TOKEN_X)], 0.1, cfg)
    total = sum(n.weight for n in netted.values())
    assert total == pytest.approx(Decimal("0.20"))
    # pro rata: 0.30/0.35 and 0.05/0.35 of 0.20
    assert netted[SOL_MINT].weight == pytest.approx(Decimal("0.20") * Decimal("0.30") / Decimal("0.35"))
    assert netted[TOKEN_X].weight == pytest.approx(Decimal("0.20") * Decimal("0.05") / Decimal("0.35"))
    assert any("cash floor scaling" in r for r in netted[SOL_MINT].reasons)


def test_per_token_cap_applies_to_non_hold_mints_only() -> None:
    cfg = make_cfg(
        copy={"live": True},
        allocation={
            "copy_pct": 50,
            "sol_trend_ensemble_pct": 20,
            "sol_regime_switch_pct": 0,
            "cash_floor_pct": 30,
        },
    )
    netted = net_targets([tgt("0.50", "copy_consensus", mint=TOKEN_X), tgt("0.20")], 0.1, cfg)
    assert netted[TOKEN_X].weight == Decimal("0.3")
    assert netted[SOL_MINT].weight == Decimal("0.2")


def test_band_suppresses_small_deltas() -> None:
    cfg = make_cfg()
    acct = make_snapshot(usdc=Decimal(6000), sol_lamports=20 * LAMPORTS + 50_000_000)  # 20 SOL = 3000 USD
    equity = acct.equity_usd  # 9007.5
    current_w = Decimal(3000) / equity
    just_inside = current_w + Decimal("0.019")
    assert plan([tgt(str(just_inside))], acct, 0.1, cfg) == []
    just_outside = current_w + Decimal("0.021")
    out = plan([tgt(str(just_outside))], acct, 0.1, cfg)
    assert len(out) == 1 and out[0].side == "buy"
    # below the target: a sell of the delta, not a full exit
    lower = current_w - Decimal("0.05")
    out = plan([tgt(str(lower))], acct, 0.1, cfg)
    assert len(out) == 1 and out[0].side == "sell" and not out[0].is_exit


def test_zero_target_is_a_full_exit_keeping_the_reserve() -> None:
    cfg = make_cfg()
    acct = make_snapshot(usdc=Decimal(6000), sol_lamports=26 * LAMPORTS + 50_000_000)
    out = plan([tgt("0")], acct, 0.1, cfg)
    assert len(out) == 1
    assert out[0].side == "sell" and out[0].is_exit and out[0].amount_base == 26 * LAMPORTS


def test_exits_before_entries_and_exit_wins_over_buy() -> None:
    cfg = make_cfg(copy={"live": True})
    pos = Position(
        mint=TOKEN_X,
        amount_base=1_000_000_000,
        cost_usd=Decimal(100),
        opened_at=NOW - timedelta(days=1),
        strategy="copy_consensus",
        exit=ExitRule(stop_price=Decimal("0.09")),
    )
    acct = make_snapshot(
        usdc=Decimal(6000),
        sol_lamports=50_000_000,
        holdings={TOKEN_X: 1_000_000_000},
        marks={TOKEN_X: Decimal("0.05")},
        positions=[pos],
    )
    out = plan([tgt("0.30"), tgt("0.04", "copy_consensus", mint=TOKEN_X)], acct, 0.1, cfg)
    assert [o.side for o in out] == ["sell", "buy"]
    assert out[0].mint == TOKEN_X and out[0].is_exit and out[0].amount_base == 1_000_000_000
    assert out[1].mint == SOL_MINT
    assert not any(o.mint == TOKEN_X and o.side == "buy" for o in out)


def test_only_one_entry_per_tick_largest_first() -> None:
    cfg = make_cfg(copy={"live": True})
    acct = make_snapshot(usdc=Decimal(10_000), sol_lamports=50_000_000)
    out = plan([tgt("0.30"), tgt("0.04", "copy_consensus", mint=TOKEN_X)], acct, 0.1, cfg)
    assert len(out) == 1 and out[0].mint == SOL_MINT
    cfg2 = make_cfg(copy={"live": True}, risk={"max_entries_per_tick": 2})
    out2 = plan([tgt("0.30"), tgt("0.04", "copy_consensus", mint=TOKEN_X)], acct, 0.1, cfg2)
    assert [o.mint for o in out2] == [SOL_MINT, TOKEN_X]


def test_disabled_sleeve_contributes_zero() -> None:
    cfg = make_cfg(strategies={"sol_regime_switch": {"enabled": False}})
    acct = make_snapshot(usdc=Decimal(10_000), sol_lamports=50_000_000)
    out = plan([tgt("0.30"), tgt("0.25", "sol_regime_switch")], acct, 0.1, cfg)
    assert len(out) == 1
    assert out[0].usd == pytest.approx(Decimal("0.30") * acct.equity_usd, abs=Decimal("0.001"))
    # copy targets are ignored while copy.live is false; unknown strategies never get budget
    assert plan([tgt("0.04", "copy_consensus", mint=TOKEN_X)], acct, 0.1, cfg) == []
    assert plan([tgt("0.04", "mystery", mint=TOKEN_Y)], acct, 0.1, cfg) == []


def test_buy_clipped_to_cash_headroom() -> None:
    cfg = make_cfg()
    # an untargeted token holding (3000 USD) eats the cash headroom: usdc 3700 vs floor 35% of ~10157
    token = {TOKEN_X: 60_000_000_000}  # 60000 units x 0.05 = 3000 USD
    acct = make_snapshot(
        usdc=Decimal(3700),
        sol_lamports=23 * LAMPORTS + 50_000_000,
        holdings=token,
        marks={TOKEN_X: Decimal("0.05")},
    )
    assert acct.usdc_usd - Decimal("0.35") * acct.equity_usd < rebalance_band_usd(acct.equity_usd, cfg.risk)
    assert plan([tgt("0.40"), tgt("0.25", "sol_regime_switch")], acct, 0.1, cfg) == []
    acct2 = make_snapshot(
        usdc=Decimal(4000),
        sol_lamports=23 * LAMPORTS + 50_000_000,
        holdings=token,
        marks={TOKEN_X: Decimal("0.05")},
    )
    out = plan([tgt("0.40"), tgt("0.25", "sol_regime_switch")], acct2, 0.1, cfg)
    assert len(out) == 1 and out[0].side == "buy"
    assert out[0].usd <= acct2.usdc_usd - Decimal("0.35") * acct2.equity_usd
    assert out[0].usd < Decimal("0.5") * acct2.equity_usd - acct2.sol_usd  # clipped below the raw delta


def test_exit_fired_and_trailing_ratchet() -> None:
    pos = Position(
        mint=SOL_MINT,
        amount_base=LAMPORTS,
        cost_usd=Decimal(100),
        opened_at=NOW,
        strategy="x",
        exit=ExitRule(stop_price=Decimal(90), trail_pct=Decimal("0.25"), trail_from_gain_pct=Decimal("0.30")),
    )
    assert exit_fired(pos, Decimal(95), NOW) is None
    assert exit_fired(pos, Decimal(89), NOW) is not None
    timed = pos.model_copy(update={"exit": ExitRule(time_stop_at=NOW - timedelta(minutes=1))})
    assert "time stop" in (exit_fired(timed, Decimal(95), NOW) or "")
    assert ratchet_trailing_stop(pos, Decimal(120)) is None  # +20% < 30% threshold
    r = ratchet_trailing_stop(pos, Decimal(140))
    assert r is not None and r.stop_price == Decimal(140) * Decimal("0.75")
    assert ratchet_trailing_stop(pos.model_copy(update={"exit": r}), Decimal(130)) is None  # never lowers


def test_order_intent_model() -> None:
    o = OrderIntent(mint=SOL_MINT, side="buy", usd=Decimal(10), strategy="core", reason="r")
    assert not o.is_exit and o.amount_base is None
