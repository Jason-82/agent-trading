"""Shared builders for the WP-D tests (allocator, risk engine, reconcile, engine E2E, CLI)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from tiller.config import Config, config_from_dict
from tiller.models import SOL_MINT, USDC_MINT, DayStats, Position
from tiller.risk.account import AccountSnapshot

WALLET = "FAe4sisG95oZ42w7buUn5qEE4TAnfTTFPiguZUHmhiF"
TOKEN_X = "J2xccRtuG43drESLYznHhLhQkLTdfepcKYbiQ9BsJVaf"
TOKEN_Y = "5Z6Ay5NEcbg3xhopc522sBCRXQujkTiuDRnHGfQdcnSf"
NOW = datetime(2026, 9, 24, 0, 5, tzinfo=UTC)
LAMPORTS = 1_000_000_000


def make_cfg(**overrides: Any) -> Config:
    """Default config with tmp-free paths; ``overrides`` are nested dict updates."""
    raw: dict[str, Any] = {"mode": "paper", "familiars": {"enabled": False}}
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(raw.get(k), dict):
            raw[k].update(v)
        else:
            raw[k] = v
    return config_from_dict(raw)


def make_snapshot(
    *,
    equity: Decimal | None = None,
    usdc: Decimal = Decimal(6000),
    sol_lamports: int = 26 * LAMPORTS + 50_000_000,
    marks: dict[str, Decimal] | None = None,
    positions: list[Position] | None = None,
    holdings: dict[str, int] | None = None,
    decimals: dict[str, int] | None = None,
    peak_7d: Decimal | None = None,
    peak_30d: Decimal | None = None,
    day: DayStats | None = None,
    net_deposits: Decimal = Decimal(0),
    ts: datetime = NOW,
) -> AccountSnapshot:
    """Snapshot with SOL at 150 by default: 26.05 SOL (~3907.5 USD) + 6000 USDC."""
    marks = {SOL_MINT: Decimal(150), USDC_MINT: Decimal(1), **(marks or {})}
    holdings = {SOL_MINT: sol_lamports, **(holdings or {})}
    decimals = {TOKEN_X: 6, TOKEN_Y: 6, **(decimals or {})}
    sol_usd = Decimal(sol_lamports) / LAMPORTS * marks[SOL_MINT]
    eq = usdc + sol_usd
    for m, base in holdings.items():
        if m == SOL_MINT:
            continue
        eq += Decimal(base) / Decimal(10 ** decimals[m]) * marks[m]
    if equity is not None:
        eq = equity
    return AccountSnapshot(
        ts=ts,
        equity_usd=eq,
        usdc_usd=usdc,
        sol_lamports=sol_lamports,
        sol_usd=sol_usd,
        positions=positions or [],
        marks=marks,
        net_deposits_usd=net_deposits,
        day=day or DayStats(start_equity=eq, buys_usd=Decimal(0), swaps=0, entries=0),
        peak_7d=peak_7d if peak_7d is not None else eq,
        peak_30d=peak_30d if peak_30d is not None else eq,
        pnl_7d_pct=Decimal(0),
        holdings=holdings,
        decimals=decimals,
        source="paper",
    )
