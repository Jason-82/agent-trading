"""Token metadata (Jupiter Tokens V2, Shield), mint accounts via RPC and the PURE token gate.

The RPC mint-account parse (:func:`tiller.execution.guard.check_mint`) is binding and
independent of Tokens V2. Tokens V2 unreachable => ``token_info`` returns ``None`` => block.
Shield unreachable => ``shield`` returns ``None`` => block for every non-allowlisted mint.
Every raw Tokens V2 / Shield response is persisted to ``ledger.raw_snapshots`` before parsing.
"""

from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from tiller.clock import Clock
from tiller.execution.guard import check_mint, mint_decimals, mint_info
from tiller.execution.http import MonotonicFn, RateLimitedHttp, SleepFn
from tiller.execution.rpc import Rpc
from tiller.ledger import Ledger
from tiller.models import SOL_DECIMALS, SOL_MINT, USDC_DECIMALS, USDC_MINT, TokenInfo

SHIELD_BLOCKING_WARNINGS: frozenset[str] = frozenset(
    {"HAS_FREEZE_AUTHORITY", "HAS_MINT_AUTHORITY", "LOW_ORGANIC_ACTIVITY", "NEW_LISTING"}
)


class TokenGateCfg(BaseModel):
    """Thresholds per profile. ``min_liquidity_usd`` USD, ``min_age_h`` hours, pct as percent
    (35 = 35%), ``max_round_trip_pct`` a fraction (0.015 = 1.5%)."""

    model_config = ConfigDict(extra="forbid")

    profile: Literal["established", "copy"]
    min_organic: float
    min_liquidity_usd: Decimal
    min_age_h: int
    max_top_holders_pct: float
    max_round_trip_pct: Decimal


class GateResult(BaseModel):
    """Outcome of the token gate. ``reasons`` is empty when ``ok``."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    reasons: list[str]
    liquidity_usd: Decimal | None = None


class TokenData:
    """Tokens V2 search, Shield and mint account fetch with raw persistence."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        rpc: Rpc,
        base_url: str,
        api_key: str | None,
        rps: float,
        ledger: Ledger,
        clock: Clock,
        *,
        sleep: SleepFn = asyncio.sleep,
        monotonic: MonotonicFn = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        self.rpc = rpc
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key or None
        self.ledger = ledger
        self.clock = clock
        self._http = RateLimitedHttp(client, rps, sleep=sleep, monotonic=monotonic, rng=rng)

    def _headers(self) -> dict[str, str]:
        h = {"accept": "application/json"}
        if self._api_key:
            h["x-api-key"] = self._api_key
        return h

    async def token_info(self, mint: str) -> TokenInfo | None:
        """``GET /tokens/v2/search?query=<mint>``; ``None`` when unreachable, absent or unparseable."""
        try:
            resp = await self._http.request(
                "GET", f"{self.base_url}/tokens/v2/search", params={"query": mint}, headers=self._headers()
            )
        except httpx.HTTPError:
            return None
        self.ledger.add_raw_snapshot(self.clock.now(), "jupiter.tokens_v2", mint, resp.text)
        if resp.status_code >= 400:
            return None
        try:
            payload = resp.json()
        except ValueError:
            return None
        if not isinstance(payload, list):
            return None
        for item in payload:
            if isinstance(item, dict) and item.get("id") == mint:
                try:
                    return TokenInfo.from_wire(item)
                except ValidationError:
                    return None
        return None

    async def shield(self, mints: list[str]) -> dict[str, list[str]] | None:
        """``GET /ultra/v1/shield?mints=`` -> ``{mint: [warning types]}`` (every requested mint
        present, possibly empty); ``None`` on any error."""
        try:
            resp = await self._http.request(
                "GET",
                f"{self.base_url}/ultra/v1/shield",
                params={"mints": ",".join(mints)},
                headers=self._headers(),
            )
        except httpx.HTTPError:
            return None
        self.ledger.add_raw_snapshot(self.clock.now(), "jupiter.shield", ",".join(mints), resp.text)
        if resp.status_code >= 400:
            return None
        try:
            payload = resp.json()
        except ValueError:
            return None
        return parse_shield(payload, mints)

    async def mint_account(self, mint: str) -> dict[str, Any] | None:
        """jsonParsed ``getAccountInfo`` of the mint (``None`` if it does not exist)."""
        return await self.rpc.get_account_info(mint)

    async def decimals(self, mint: str) -> int:
        """Decimals of ``mint`` (SOL/USDC known without a call)."""
        if mint == SOL_MINT:
            return SOL_DECIMALS
        if mint == USDC_MINT:
            return USDC_DECIMALS
        acc = await self.mint_account(mint)
        if acc is None:
            raise ValueError(f"mint {mint} not found")
        return mint_decimals(acc)

    async def token_program(self, mint: str) -> str:
        """Owner program of the mint (Token or Token-2022)."""
        acc = await self.mint_account(mint)
        if acc is None:
            raise ValueError(f"mint {mint} not found")
        program, _info = mint_info(acc)
        if program is None:
            raise ValueError(f"mint {mint}: no owner program")
        return program


def parse_shield(payload: Any, mints: list[str]) -> dict[str, list[str]] | None:
    """``{"warnings": {mint: [{"type": ...}]}}`` -> ``{mint: [types]}``; None if the shape is off."""
    if not isinstance(payload, dict):
        return None
    warnings = payload.get("warnings")
    if not isinstance(warnings, dict):
        return None
    out: dict[str, list[str]] = {}
    for mint in mints:
        entries = warnings.get(mint, [])
        if not isinstance(entries, list):
            return None
        types: list[str] = []
        for e in entries:
            if isinstance(e, dict) and isinstance(e.get("type"), str):
                types.append(e["type"])
            elif isinstance(e, str):
                types.append(e)
            else:
                return None
        out[mint] = types
    return out


def evaluate_token_gate(
    mint: str,
    info: TokenInfo | None,
    shield_warnings: list[str] | None,
    mint_account: dict[str, Any] | None,
    cfg: TokenGateCfg,
    our_wallet: str,
    own_token_mints: set[str],
    blocklist: set[str],
    now: datetime,
    allowlist: set[str],
) -> GateResult:
    """Pure L1 token gate. Allowlisted mints pass immediately; everything else must clear every rule.

    All reasons are collected (not just the first) so the ledger explains a block fully.
    """
    if mint in allowlist:
        return GateResult(ok=True, reasons=[], liquidity_usd=info.liquidity_usd if info else None)
    reasons: list[str] = []
    if mint in blocklist:
        reasons.append("blocklist: mint is on the failed-simulation blocklist")
    if mint in own_token_mints:
        reasons.append("own_token: mint is in own_token_mints")

    mint_reason = check_mint(mint_account)
    if mint_reason is not None:
        reasons.append(mint_reason)

    liquidity: Decimal | None = None
    if info is None:
        reasons.append("tokens_v2: no token info (unreachable or unknown mint)")
    else:
        liquidity = info.liquidity_usd
        if info.dev is not None and info.dev == our_wallet:
            reasons.append("own_token: Tokens V2 dev is our wallet")
        if info.organic_score is None:
            reasons.append("tokens_v2: organic score missing")
        elif info.organic_score < cfg.min_organic:
            reasons.append(f"tokens_v2: organic score {info.organic_score} < {cfg.min_organic}")
        if liquidity is None:
            reasons.append("tokens_v2: liquidity missing")
        elif liquidity < cfg.min_liquidity_usd:
            reasons.append(f"tokens_v2: liquidity {liquidity} < {cfg.min_liquidity_usd}")
        if info.first_pool_created_at is None:
            reasons.append("tokens_v2: first pool age unknown")
        else:
            age = now - info.first_pool_created_at
            if age < timedelta(hours=cfg.min_age_h):
                reasons.append(f"tokens_v2: age {age.total_seconds() / 3600:.1f}h < {cfg.min_age_h}h")
        audit = info.audit
        if audit is None:
            reasons.append("tokens_v2: audit block missing")
        else:
            if audit.top_holders_pct is None:
                reasons.append("tokens_v2: top holders share missing")
            elif audit.top_holders_pct > cfg.max_top_holders_pct:
                reasons.append(
                    f"tokens_v2: top holders {audit.top_holders_pct}% > {cfg.max_top_holders_pct}%"
                )
            if audit.is_sus:
                reasons.append("tokens_v2: audit isSus")
            if audit.mint_authority_disabled is False:
                reasons.append("tokens_v2: mint authority enabled")
            if audit.freeze_authority_disabled is False:
                reasons.append("tokens_v2: freeze authority enabled")

    if shield_warnings is None:
        reasons.append("shield: unreachable")
    else:
        bad = sorted(set(shield_warnings) & SHIELD_BLOCKING_WARNINGS)
        if bad:
            reasons.append(f"shield: {','.join(bad)}")

    return GateResult(ok=not reasons, reasons=reasons, liquidity_usd=liquidity)


def gate_cfg_from_thresholds(profile: Literal["established", "copy"], thresholds: Any) -> TokenGateCfg:
    """Build a :class:`TokenGateCfg` from a ``config.TokenGateThresholds``-like object."""
    return TokenGateCfg(
        profile=profile,
        min_organic=float(thresholds.min_organic),
        min_liquidity_usd=Decimal(thresholds.min_liquidity_usd),
        min_age_h=int(thresholds.min_age_h),
        max_top_holders_pct=float(thresholds.max_top_holders_pct),
        max_round_trip_pct=Decimal(thresholds.max_round_trip_pct),
    )


__all__ = [
    "GateResult",
    "TokenData",
    "TokenGateCfg",
    "evaluate_token_gate",
    "gate_cfg_from_thresholds",
    "parse_shield",
]
