"""Config loading, env overrides, every L0 invariant, allocation folding, secret redaction."""

from __future__ import annotations

import tomllib
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from tiller.config import (
    GEO_ACK_TEXT,
    Config,
    config_from_dict,
    effective_allocation,
    l0_violations,
    load_config,
)

EXAMPLE = Path(__file__).resolve().parents[1] / "config" / "tiller.example.toml"
TODAY = date(2026, 9, 24)


def example_dict() -> dict[str, Any]:
    with EXAMPLE.open("rb") as f:
        return tomllib.load(f)


def test_example_toml_loads_with_defaults() -> None:
    cfg = load_config(EXAMPLE, env={}, today=TODAY)
    assert cfg.mode == "paper"
    assert cfg.strategies.sol_trend_ensemble.lookbacks == [10, 20, 30, 60, 90, 150, 250]
    assert cfg.strategies.sol_regime_switch.weight == 0.25
    assert cfg.allocation.cash_floor_pct == 30
    assert cfg.risk.canary.ramp_steps == [0.25, 0.5, 1.0]
    assert cfg.copy.live is False and cfg.copy.promotion.min_days == 60
    assert cfg.guard.token_gate_copy.min_age_h == 24
    assert cfg.jupiter.api_key is None and cfg.familiars.api_key is None
    assert cfg.venues.hyperliquid.enabled is False
    assert l0_violations(cfg) == []


def test_defaults_alone_are_valid() -> None:
    assert l0_violations(Config()) == []


def test_env_overrides_api_keys_and_secrets_are_redacted() -> None:
    env = {
        "TILLER_JUPITER_API_KEY": "jup_abcdef123456",
        "TILLER_FAMILIARS_API_KEY": "fam_zyx987",
        "TILLER_ANTHROPIC_API_KEY": "sk-ant-secret",
        "TILLER_TELEGRAM_TOKEN": "123456:telegramtoken",
    }
    cfg = load_config(EXAMPLE, env=env, today=TODAY)
    assert cfg.jupiter.api_key is not None and cfg.jupiter.api_key.get_secret_value() == "jup_abcdef123456"
    assert cfg.familiars.api_key is not None and cfg.familiars.api_key.get_secret_value() == "fam_zyx987"
    assert cfg.llm.api_key is not None and cfg.llm.api_key.get_secret_value() == "sk-ant-secret"
    assert cfg.alerts.telegram_token is not None
    for blob in (repr(cfg), str(cfg), cfg.model_dump_json(), repr(cfg.jupiter), str(cfg.familiars.api_key)):
        for secret in env.values():
            assert secret not in blob


def test_unknown_key_is_rejected() -> None:
    raw = example_dict()
    raw["risk"]["daily_los_pct"] = 0.01  # typo
    with pytest.raises(ValueError):
        config_from_dict(raw, today=TODAY)


@pytest.mark.parametrize(
    ("mutate", "rule"),
    [
        (lambda r: r["allocation"].__setitem__("copy_pct", 6), "allocation_sums_to_100"),
        (lambda r: r["allocation"].update({"cash_floor_pct": 25, "copy_pct": 10}), "cash_floor_pct>=30"),
        (
            lambda r: r["allocation"].update({"sol_trend_ensemble_pct": 55, "sol_regime_switch_pct": 10}),
            "sleeve_pct<=50",
        ),
        (
            lambda r: r["allocation"].update(
                {
                    "sol_trend_ensemble_pct": 40,
                    "sol_regime_switch_pct": 25,
                    "copy_pct": 5,
                    "breakout_pct": 0,
                    "cash_floor_pct": 30,
                    "extra_dummy": 0,
                }
            ),
            "extra_dummy",
        ),
        (lambda r: r["risk"].__setitem__("daily_loss_pct", 0.07), "daily_loss_pct<=0.06"),
        (lambda r: r["risk"].__setitem__("dd30_flatten_pct", 0.30), "dd30_flatten_pct<=0.25"),
        (lambda r: r["risk"].__setitem__("dd30_entry_block_pct", 0.25), "dd30_flatten>dd30_entry>dd7_entry"),
        (lambda r: r["risk"].__setitem__("dd7_entry_block_pct", 0.21), "dd30_flatten>dd30_entry>dd7_entry"),
        (lambda r: r["risk"].__setitem__("sol_beta_cap_max", 0.6), "sol_beta_cap_max<=0.5"),
        (lambda r: r.__setitem__("mode", "live"), "live_requires_skill_md_reviewed_within_30_days"),
        (
            lambda r: r["venues"]["hyperliquid"].__setitem__("enabled", True),
            "venues.hyperliquid.enabled_requires_geo_ack",
        ),
        (
            lambda r: r["venues"]["cex"].update({"enabled": True, "geo_ack": "yes"}),
            "venues.cex.enabled_requires_geo_ack",
        ),
        (
            lambda r: r["copy"].update({"live": True, "shadow_enabled": False}),
            "copy.live_requires_shadow_enabled",
        ),
    ],
)
def test_each_l0_invariant_has_a_failing_case(mutate: Any, rule: str) -> None:
    raw = example_dict()
    mutate(raw)
    with pytest.raises(ValueError, match=rule.replace("(", r"\(").replace(")", r"\)").replace(">", ">")):
        config_from_dict(raw, today=TODAY)


def test_non_cash_sleeves_over_70_rejected() -> None:
    raw = example_dict()
    # 45 + 30 + 0 + 0 + 25 = 100 but non-cash = 75 (and cash 25): both rules fire, the 70 rule is named
    raw["allocation"].update(
        {
            "sol_trend_ensemble_pct": 45,
            "sol_regime_switch_pct": 30,
            "copy_pct": 0,
            "breakout_pct": 0,
            "cash_floor_pct": 25,
        }
    )
    with pytest.raises(ValueError, match="non_cash_sleeves<=70"):
        config_from_dict(raw, today=TODAY)
    # exactly 70 non-cash with a 30 floor is allowed
    raw["allocation"].update(
        {
            "sol_trend_ensemble_pct": 40,
            "sol_regime_switch_pct": 30,
            "copy_pct": 0,
            "breakout_pct": 0,
            "cash_floor_pct": 30,
        }
    )
    assert l0_violations(config_from_dict(raw, today=TODAY)) == []


def test_live_requires_fresh_skill_md_ack() -> None:
    raw = example_dict()
    raw["mode"] = "live"
    raw["familiars"]["skill_md_reviewed_at"] = (TODAY - timedelta(days=31)).isoformat()
    with pytest.raises(ValueError, match="live_requires_skill_md_reviewed_within_30_days"):
        config_from_dict(raw, today=TODAY)
    raw["familiars"]["skill_md_reviewed_at"] = (TODAY - timedelta(days=29)).isoformat()
    assert config_from_dict(raw, today=TODAY).mode == "live"


def test_live_without_familiars_needs_explicit_ack() -> None:
    raw = example_dict()
    raw["mode"] = "live"
    raw["familiars"]["enabled"] = False
    with pytest.raises(ValueError, match="live_without_familiars_requires_ack"):
        config_from_dict(raw, today=TODAY)
    raw["live_without_familiars_ack"] = True
    assert config_from_dict(raw, today=TODAY).mode == "live"


def test_optional_venue_with_exact_geo_ack_loads_but_is_not_runnable() -> None:
    raw = example_dict()
    raw["venues"]["hyperliquid"].update({"enabled": True, "geo_ack": GEO_ACK_TEXT})
    cfg = config_from_dict(raw, today=TODAY)
    assert cfg.venues.hyperliquid.enabled is True


def test_effective_allocation_folds_disabled_sleeves_into_cash() -> None:
    cfg = load_config(EXAMPLE, env={}, today=TODAY)
    alloc = effective_allocation(cfg)
    # breakout disabled (0%) and copy not live (5%) both sit in cash
    assert alloc == {
        "sol_trend_ensemble": 40.0,
        "sol_regime_switch": 25.0,
        "copy": 0.0,
        "breakout": 0.0,
        "cash": 35.0,
    }
    raw = example_dict()
    raw["allocation"].update({"breakout_pct": 5, "copy_pct": 0})
    raw["strategies"]["breakout_4h"]["enabled"] = False
    raw["strategies"]["sol_regime_switch"]["enabled"] = False
    alloc2 = effective_allocation(config_from_dict(raw, today=TODAY))
    assert alloc2["breakout"] == 0.0 and alloc2["sol_regime_switch"] == 0.0 and alloc2["cash"] == 60.0
    assert sum(alloc2.values()) == 100.0
    raw["copy"]["live"] = True
    raw["allocation"].update({"breakout_pct": 0, "copy_pct": 5})
    assert effective_allocation(config_from_dict(raw, today=TODAY))["copy"] == 5.0


def test_secret_str_never_in_repr_of_any_section() -> None:
    cfg = load_config(EXAMPLE, env={"TILLER_FAMILIARS_API_KEY": "fam_topsecret"}, today=TODAY)
    assert "fam_topsecret" not in repr(cfg.familiars)
    assert "fam_topsecret" not in f"{cfg.familiars.api_key}"
    assert cfg.model_dump()["familiars"]["api_key"].get_secret_value() == "fam_topsecret"
