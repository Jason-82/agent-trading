"""Configuration: TOML (stdlib tomllib) + environment overrides into pydantic models.

L0 invariants are enforced at load time; an unsafe configuration refuses to boot with a
``ValueError`` (pydantic ``ValidationError``) that names the violated rule.

Units: ``*_pct`` under ``[allocation]`` are percent (sum to 100); risk thresholds,
weights and caps are fractions; ``*_bps`` basis points; ``*_usd`` dollars; lamports
are 1e-9 SOL. Secrets are ``SecretStr`` and are never printed by ``repr``/``str``.
"""

from __future__ import annotations

import tomllib
import warnings
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from tiller.models import SOL_MINT
from tiller.strategies.breakout_4h import BreakoutParams
from tiller.strategies.sol_trend import RegimeParams, SolTrendParams

GEO_ACK_TEXT = "I am not a US or Ontario person"
SKILL_MD_MAX_AGE_DAYS = 30

ENV_JUPITER_KEY = "TILLER_JUPITER_API_KEY"
ENV_FAMILIARS_KEY = "TILLER_FAMILIARS_API_KEY"
ENV_ANTHROPIC_KEY = "TILLER_ANTHROPIC_API_KEY"
ENV_TELEGRAM_TOKEN = "TILLER_TELEGRAM_TOKEN"
ENV_WALLET_SECRET = "AGENT_WALLET_SECRET"


class _Cfg(BaseModel):
    """Config section base: unknown keys are a typo and refuse to load."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def _secret_or_none(v: Any) -> Any:
    if v is None or v == "":
        return None
    return v


def _date_or_none(v: Any) -> Any:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    return v


class WalletCfg(_Cfg):
    keypair_path: Path | None = None
    env_secret_var: str = ENV_WALLET_SECRET
    sol_reserve_lamports: int = Field(default=50_000_000, ge=0)

    @field_validator("keypair_path", mode="before")
    @classmethod
    def _p(cls, v: Any) -> Any:
        return None if v in (None, "") else v


class RpcCfg(_Cfg):
    urls: list[str] = Field(min_length=1)
    rps: float = Field(default=10.0, gt=0)


class JupiterCfg(_Cfg):
    base_url: str = "https://api.jup.ag"
    api_key: SecretStr | None = None
    rps: float = Field(default=0.5, gt=0)
    slippage_bps_sol_usdc: int = Field(default=50, ge=1, le=500)
    slippage_bps_token: int = Field(default=100, ge=1, le=500)
    slippage_bps_emergency: int = Field(default=150, ge=1, le=1000)

    @field_validator("api_key", mode="before")
    @classmethod
    def _k(cls, v: Any) -> Any:
        return _secret_or_none(v)


class FamiliarsCfg(_Cfg):
    enabled: bool = True
    base_url: str = "https://familiars.family"
    api_key: SecretStr | None = None
    handle: str | None = None
    rps: float = Field(default=1.0, gt=0)
    skill_md_reviewed_at: date | None = None
    own_token_mints: list[str] = Field(default_factory=list)
    callouts_per_day: int = Field(default=2, ge=0)

    @field_validator("api_key", mode="before")
    @classmethod
    def _k(cls, v: Any) -> Any:
        return _secret_or_none(v)

    @field_validator("handle", mode="before")
    @classmethod
    def _h(cls, v: Any) -> Any:
        return None if v in (None, "") else v

    @field_validator("skill_md_reviewed_at", mode="before")
    @classmethod
    def _d(cls, v: Any) -> Any:
        return _date_or_none(v)


CandleSourceName = Literal["kraken", "coinbase", "csv"]


def _default_candle_sources() -> list[CandleSourceName]:
    return ["kraken", "coinbase", "csv"]


class DataCfg(_Cfg):
    candle_sources: list[CandleSourceName] = Field(default_factory=_default_candle_sources)
    csv_dir: Path | None = Path("data/history")
    max_candle_age_h: int = Field(default=26, ge=1)

    @field_validator("csv_dir", mode="before")
    @classmethod
    def _p(cls, v: Any) -> Any:
        return None if v in (None, "") else v


class StrategiesCfg(_Cfg):
    hold_mint: str = SOL_MINT
    sol_trend_ensemble: SolTrendParams = Field(default_factory=SolTrendParams)
    sol_regime_switch: RegimeParams = Field(default_factory=RegimeParams)
    breakout_4h: BreakoutParams = Field(default_factory=BreakoutParams)


class AllocationCfg(_Cfg):
    """Percent of equity per sleeve; must sum to 100."""

    sol_trend_ensemble_pct: float = 40
    sol_regime_switch_pct: float = 25
    copy_pct: float = 5
    breakout_pct: float = 0
    cash_floor_pct: float = 30

    @property
    def sleeves(self) -> dict[str, float]:
        return {
            "sol_trend_ensemble": self.sol_trend_ensemble_pct,
            "sol_regime_switch": self.sol_regime_switch_pct,
            "copy": self.copy_pct,
            "breakout": self.breakout_pct,
        }


class CanaryCfg(_Cfg):
    max_position_usd: Decimal = Decimal(10)
    daily_limit_usd: Decimal = Decimal(30)
    ramp_steps: list[float] = Field(default_factory=lambda: [0.25, 0.5, 1.0])
    ramp_step_days: int = 7


class RiskCfg(_Cfg):
    daily_loss_pct: float = 0.05
    dd7_entry_block_pct: float = 0.15
    dd30_entry_block_pct: float = 0.20
    dd30_flatten_pct: float = 0.25
    sol_beta_cap_max: float = 0.50
    sol_beta_cap_vol: float = 0.30
    rebalance_band_pct: float = 0.02
    min_order_usd: Decimal = Decimal(10)
    max_entries_per_tick: int = 1
    max_swaps_per_day: int = 6
    consecutive_failure_pause_min: int = 60
    sim_fail_blocklist_h: int = 24
    max_positions: int = 4
    max_token_pct: float = 0.30
    max_data_age_h: int = 26
    error_rate_block: float = 0.5
    canary: CanaryCfg = Field(default_factory=CanaryCfg)


class TokenGateThresholds(_Cfg):
    min_organic: float = 50
    min_liquidity_usd: Decimal = Decimal(400_000)
    min_age_h: int = 72
    max_top_holders_pct: float = 35
    max_round_trip_pct: Decimal = Decimal("0.015")


class GuardSection(_Cfg):
    """``[guard]`` values; ``tiller.execution.guard.GuardCfg`` is built from these."""

    max_price_dev_sol: Decimal = Decimal("0.01")
    max_price_dev_token: Decimal = Decimal("0.03")
    max_price_dev_emergency: Decimal = Decimal("0.05")
    max_impact_core: Decimal = Decimal("0.01")
    max_impact_token: Decimal = Decimal("0.03")
    max_fee_bps: int = 10
    routers: list[str] = Field(default_factory=lambda: ["metis", "jupiterz"])
    program_allowlist: list[str] = Field(default_factory=list)
    token_gate_established: TokenGateThresholds = Field(default_factory=TokenGateThresholds)
    token_gate_copy: TokenGateThresholds = Field(
        default_factory=lambda: TokenGateThresholds(min_liquidity_usd=Decimal(250_000), min_age_h=24)
    )


class PromotionCfg(_Cfg):
    min_days: int = 60
    min_trades: int = 100
    min_expectancy_pct: float = 0.01
    min_p_positive_block20: float = 0.80
    min_leaders_positive_share: float = 0.40


class CopyCfg(_Cfg):
    shadow_enabled: bool = True
    live: bool = False
    external_wallets: list[str] = Field(default_factory=list)
    board_poll_min: int = 15
    detail_poll_s: int = 60
    wallet_poll_s: int = 60
    rescore_h: int = 6
    followed_n: int = 20
    min_days_logged: int = 14
    min_round_trips: int = 30
    min_distinct_mints: int = 10
    min_median_hold_h: float = 4
    max_single_mint_pnl_share: float = 0.5
    max_platform_dd: float = 0.30
    replay_lag_s: int = 60
    replay_slip: Decimal = Decimal("0.01")
    replay_fee: Decimal = Decimal("0.001")
    min_replay_pf: float = 1.3
    min_replay_signals: int = 20
    cluster_overlap: float = 0.8
    min_clusters: int = 3
    window_min: int = 30
    crowd_burst_max: int = 15
    late_entry_max_above: Decimal = Decimal("0.15")
    stop_pct: Decimal = Decimal("0.20")
    trail_pct: Decimal = Decimal("0.25")
    trail_from_gain_pct: Decimal = Decimal("0.30")
    time_stop_h: int = 48
    liquidity_collapse_pct: Decimal = Decimal("0.60")
    max_concurrent: int = 2
    max_entries_per_day: int = 2
    sleeve_cap_pct: float = 0.05
    per_signal_pct: float = 0.02
    promotion: PromotionCfg = Field(default_factory=PromotionCfg)


class LlmCfg(_Cfg):
    narrator: Literal["template", "claude"] = "template"
    model: str = "claude-haiku-4-5"
    daily_cap_usd: Decimal = Decimal("0.50")
    timeout_s: float = 5
    api_key: SecretStr | None = None

    @field_validator("api_key", mode="before")
    @classmethod
    def _k(cls, v: Any) -> Any:
        return _secret_or_none(v)


class AlertsCfg(_Cfg):
    telegram_token: SecretStr | None = None
    chat_id: str | None = None

    @field_validator("telegram_token", mode="before")
    @classmethod
    def _k(cls, v: Any) -> Any:
        return _secret_or_none(v)

    @field_validator("chat_id", mode="before")
    @classmethod
    def _c(cls, v: Any) -> Any:
        return None if v in (None, "") else str(v)


class OptVenue(_Cfg):
    enabled: bool = False
    geo_ack: str | None = None

    @field_validator("geo_ack", mode="before")
    @classmethod
    def _g(cls, v: Any) -> Any:
        return None if v in (None, "") else v


class VenuesCfg(_Cfg):
    hyperliquid: OptVenue = Field(default_factory=OptVenue)
    cex: OptVenue = Field(default_factory=OptVenue)


class PathsCfg(_Cfg):
    state_dir: Path = Path("/state")
    ledger_path: Path = Path("/state/tiller.sqlite")


# `copy` is the spec-mandated section name; it shadows the deprecated BaseModel.copy().
warnings.filterwarnings("ignore", message=r'Field name "copy" in "Config"', category=UserWarning)


class Config(_Cfg):
    mode: Literal["offline", "paper", "live"] = "paper"
    live_without_familiars_ack: bool = False
    paths: PathsCfg = Field(default_factory=PathsCfg)
    wallet: WalletCfg = Field(default_factory=WalletCfg)
    rpc: RpcCfg = Field(default_factory=lambda: RpcCfg(urls=["https://api.mainnet-beta.solana.com"]))
    jupiter: JupiterCfg = Field(default_factory=JupiterCfg)
    familiars: FamiliarsCfg = Field(default_factory=FamiliarsCfg)
    data: DataCfg = Field(default_factory=DataCfg)
    strategies: StrategiesCfg = Field(default_factory=StrategiesCfg)
    allocation: AllocationCfg = Field(default_factory=AllocationCfg)
    risk: RiskCfg = Field(default_factory=RiskCfg)
    guard: GuardSection = Field(default_factory=GuardSection)
    copy: CopyCfg = Field(default_factory=CopyCfg)  # type: ignore[assignment]
    llm: LlmCfg = Field(default_factory=LlmCfg)
    alerts: AlertsCfg = Field(default_factory=AlertsCfg)
    venues: VenuesCfg = Field(default_factory=VenuesCfg)
    today: date | None = Field(default=None, exclude=True, repr=False)
    """Reference date for the skill.md freshness check (tests inject; default = wall clock)."""

    @model_validator(mode="after")
    def _l0_invariants(self) -> Config:
        errors = [rule for rule in l0_violations(self)]
        if errors:
            raise ValueError("L0 config invariants violated: " + "; ".join(errors))
        return self


def l0_violations(cfg: Config) -> list[str]:
    """Return every violated L0 rule (empty list = safe to boot). Pure."""
    out: list[str] = []
    a = cfg.allocation
    total = (
        a.sol_trend_ensemble_pct + a.sol_regime_switch_pct + a.copy_pct + a.breakout_pct + a.cash_floor_pct
    )
    if abs(total - 100.0) > 0.01:
        out.append(f"allocation_sums_to_100 (got {total:g})")
    if a.cash_floor_pct < 30:
        out.append(f"cash_floor_pct>=30 (got {a.cash_floor_pct:g})")
    for name, pct in a.sleeves.items():
        if pct < 0:
            out.append(f"sleeve_pct_non_negative ({name}={pct:g})")
        if pct > 50:
            out.append(f"sleeve_pct<=50 ({name}={pct:g})")
    non_cash = sum(a.sleeves.values())
    if non_cash > 70:
        out.append(f"non_cash_sleeves<=70 (got {non_cash:g})")
    r = cfg.risk
    if r.daily_loss_pct > 0.06:
        out.append(f"daily_loss_pct<=0.06 (got {r.daily_loss_pct:g})")
    if r.dd30_flatten_pct > 0.25:
        out.append(f"dd30_flatten_pct<=0.25 (got {r.dd30_flatten_pct:g})")
    if not (r.dd30_flatten_pct > r.dd30_entry_block_pct > r.dd7_entry_block_pct > 0):
        out.append("dd30_flatten>dd30_entry>dd7_entry>0")
    if r.sol_beta_cap_max > 0.5:
        out.append(f"sol_beta_cap_max<=0.5 (got {r.sol_beta_cap_max:g})")
    if r.rebalance_band_pct < 0 or r.max_token_pct > 0.5 or r.min_order_usd <= 0:
        out.append("risk_sizing_bounds")
    if cfg.mode == "live":
        if cfg.familiars.enabled:
            reviewed = cfg.familiars.skill_md_reviewed_at
            today = cfg.today or datetime.now(tz=UTC).date()
            if reviewed is None or (today - reviewed).days > SKILL_MD_MAX_AGE_DAYS or reviewed > today:
                out.append("live_requires_skill_md_reviewed_within_30_days")
        elif not cfg.live_without_familiars_ack:
            out.append("live_without_familiars_requires_ack")
    for name, v in (("hyperliquid", cfg.venues.hyperliquid), ("cex", cfg.venues.cex)):
        if v.enabled and v.geo_ack != GEO_ACK_TEXT:
            out.append(f"venues.{name}.enabled_requires_geo_ack")
    if cfg.copy.live and not cfg.copy.shadow_enabled:
        out.append("copy.live_requires_shadow_enabled")
    if cfg.copy.live and cfg.allocation.copy_pct <= 0:
        out.append("copy.live_requires_copy_pct>0")
    return out


def effective_allocation(cfg: Config) -> dict[str, float]:
    """Allocation in percent with disabled sleeves' budgets folded into ``cash``.

    A sleeve is disabled when its strategy is ``enabled=false`` (A, B, breakout) or, for
    the copy sleeve, when ``copy.live`` is false (shadow tracking deploys zero capital).
    """
    a = cfg.allocation
    s = cfg.strategies
    out = {
        "sol_trend_ensemble": a.sol_trend_ensemble_pct if s.sol_trend_ensemble.enabled else 0.0,
        "sol_regime_switch": a.sol_regime_switch_pct if s.sol_regime_switch.enabled else 0.0,
        "copy": a.copy_pct if cfg.copy.live else 0.0,
        "breakout": a.breakout_pct if s.breakout_4h.enabled else 0.0,
    }
    out["cash"] = 100.0 - sum(out.values())
    return out


def _apply_env(raw: dict[str, Any], env: Mapping[str, str]) -> dict[str, Any]:
    def setdefault_section(name: str) -> dict[str, Any]:
        sec = raw.get(name)
        if not isinstance(sec, dict):
            sec = {}
            raw[name] = sec
        return sec

    if env.get(ENV_JUPITER_KEY):
        setdefault_section("jupiter")["api_key"] = env[ENV_JUPITER_KEY]
    if env.get(ENV_FAMILIARS_KEY):
        setdefault_section("familiars")["api_key"] = env[ENV_FAMILIARS_KEY]
    if env.get(ENV_ANTHROPIC_KEY):
        setdefault_section("llm")["api_key"] = env[ENV_ANTHROPIC_KEY]
    if env.get(ENV_TELEGRAM_TOKEN):
        setdefault_section("alerts")["telegram_token"] = env[ENV_TELEGRAM_TOKEN]
    return raw


def load_config(path: Path, env: Mapping[str, str] | None = None, today: date | None = None) -> Config:
    """Parse ``path`` (TOML) with environment overrides for secrets and validate L0 invariants.

    ``env`` defaults to an empty mapping (callers pass ``os.environ``); ``today`` fixes the
    reference date of the skill.md freshness check (tests).
    """
    with path.open("rb") as f:
        raw: dict[str, Any] = tomllib.load(f)
    raw = _apply_env(raw, env or {})
    if today is not None:
        raw["today"] = today
    return Config.model_validate(raw)


def config_from_dict(raw: Mapping[str, Any], today: date | None = None) -> Config:
    """Validate an in-memory mapping (tests and tooling)."""
    data = dict(raw)
    if today is not None:
        data["today"] = today
    return Config.model_validate(data)
