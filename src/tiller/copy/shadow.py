"""Always-on shadow copier: hypothetical fills, own exits, marks and the promotion-gate report.

The tracker has **no order authority by construction**: it imports neither the venue nor the
signer, and the only Jupiter call it makes is a read-only ``/order`` quote used as the
detection price. Every shadow trade is persisted in ``ledger.shadow_trades``.

Units: prices and USD are ``Decimal``; percentages are fractions (``0.01`` = 1%);
``lag_s`` is seconds between the leader's fill and our detection quote; times are UTC.

Book keeping conventions
------------------------
* ``id`` = ``'consensus:<mint>:<unix s>'`` for a consensus signal and ``'leader:<signature>'`` for a
  followed-leader buy (used for per-leader statistics only).
* ``entry_price`` = worst-of(detection quote, leader price * 1.01) * (1 + cost); ``exit_price`` =
  mark * (1 - cost) with cost = ``cfg.replay_slip`` (1%), so ``pnl_pct`` is net of modelled costs.
* ``marks`` holds ``'1h'``/``'6h'``/``'24h'`` price marks plus ``'high'`` (running high) and
  ``'liq_entry'`` (pool liquidity in USD at entry, when known).
* The promotion-gate statistics use CONSENSUS trades only (what the live strategy would trade);
  per-leader positive share uses the ``leader:`` trades.
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from statistics import median
from typing import Any, Protocol

from tiller.clock import Clock
from tiller.copy.leaders import (
    ZERO,
    copy_exit_rule,
    exit_reason,
    profit_factor,
    tighten_for_leader_sell,
    worst_of_entry,
)
from tiller.copy.models import LeaderStats, LeaderTrade, ShadowReport, ShadowTrade
from tiller.ledger import Ledger
from tiller.models import USDC_DECIMALS, USDC_MINT, SwapRequest, TokenInfo

ONE = Decimal(1)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
MARK_HORIZONS: dict[str, timedelta] = {
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "24h": timedelta(hours=24),
}
CONSENSUS_KEY = "consensus"


class _QuoteSource(Protocol):
    async def order(self, req: SwapRequest, taker: str, slippage_bps: int) -> Any: ...


class _PriceSource(Protocol):
    async def usd_prices(self, mints: list[str]) -> dict[str, Decimal]: ...


class _TokenSource(Protocol):
    async def token_info(self, mint: str) -> TokenInfo | None: ...

    async def decimals(self, mint: str) -> int: ...


def bootstrap_p_positive_block(
    returns: Sequence[Decimal], block: int = 20, draws: int = 5000, seed: int = 42
) -> float:
    """Seeded bootstrap: share of ``draws`` random ``block``-trade samples (with replacement) whose summed return > 0."""
    if not returns or block <= 0 or draws <= 0:
        return 0.0
    rng = random.Random(seed)
    rs = [float(r) for r in returns]
    n = len(rs)
    positive = 0
    for _ in range(draws):
        total = 0.0
        for _ in range(block):
            total += rs[rng.randrange(n)]
        if total > 0:
            positive += 1
    return positive / draws


class ShadowTracker:
    """Hypothetical copier book. Zero capital; nothing here can sign or send."""

    def __init__(
        self,
        jup: _QuoteSource | None,
        prices: _PriceSource,
        tokens: _TokenSource,
        ledger: Ledger,
        cfg: Any,
        clock: Clock,
        rng_seed: int = 42,
        *,
        equity_usd: Decimal | Callable[[], Decimal] = Decimal(1000),
        taker: str = "",
        quote_slippage_bps: int = 100,
    ) -> None:
        self.jup = jup
        self.prices = prices
        self.tokens = tokens
        self.ledger = ledger
        self.cfg = cfg
        self.clock = clock
        self.rng_seed = rng_seed
        self._equity = equity_usd
        self.taker = taker
        self.quote_slippage_bps = quote_slippage_bps
        self.cost = Decimal(cfg.replay_slip)
        self._open: dict[str, ShadowTrade] = {}
        self._ids: set[str] = set()
        for t in ledger.shadow_trades(since=EPOCH):
            self._ids.add(t.id)
            if t.exit_ts is None:
                self._open[t.id] = t

    # ------------------------------------------------------------------ helpers

    def equity_usd(self) -> Decimal:
        return Decimal(self._equity()) if callable(self._equity) else Decimal(self._equity)

    def open_trades(self) -> list[ShadowTrade]:
        return sorted(self._open.values(), key=lambda t: (t.signal_ts, t.id))

    def _persist(self, t: ShadowTrade) -> None:
        self.ledger.upsert_shadow_trade(t)
        self._ids.add(t.id)
        if t.exit_ts is None:
            self._open[t.id] = t
        else:
            self._open.pop(t.id, None)

    async def _detection_quote(self, mint: str, size_usd: Decimal) -> Decimal | None:
        """USD price per token at detection: Jupiter /order for ``size_usd`` of USDC, else Price V3."""
        if self.jup is not None:
            try:
                req = SwapRequest(
                    input_mint=USDC_MINT,
                    output_mint=mint,
                    amount_base=max(int(size_usd * (10**USDC_DECIMALS)), 1),
                    strategy="copy_shadow",
                    reason="detection quote (shadow, never executed)",
                )
                order = await self.jup.order(req, self.taker, self.quote_slippage_bps)
                dec = await self.tokens.decimals(mint)
                out_units = Decimal(int(order.out_amount)) / (Decimal(10) ** dec)
                if out_units > 0:
                    return size_usd / out_units
            except Exception as exc:
                self.ledger.add_event(
                    "info", "shadow_quote_fallback", {"mint": mint, "error": str(exc)[:200]}
                )
        try:
            return (await self.prices.usd_prices([mint])).get(mint)
        except Exception as exc:
            self.ledger.add_event("warn", "shadow_no_price", {"mint": mint, "error": str(exc)[:200]})
            return None

    async def _liquidity(self, mint: str) -> Decimal | None:
        try:
            info = await self.tokens.token_info(mint)
        except Exception:
            return None
        return None if info is None else info.liquidity_usd

    async def _open_trade(
        self, trade_id: str, leader_key: str, mint: str, leader_price: Decimal | None, leader_ts: datetime
    ) -> ShadowTrade | None:
        now = self.clock.now()
        size_usd = self.equity_usd() * Decimal(str(self.cfg.per_signal_pct))
        quote = await self._detection_quote(mint, size_usd)
        basis = worst_of_entry(quote, leader_price)
        if basis is None or basis <= 0:
            self.ledger.add_event("warn", "shadow_skipped", {"id": trade_id, "reason": "no_price"})
            return None
        entry = basis * (ONE + self.cost)
        lag_cost = (quote / leader_price - ONE) if (quote is not None and leader_price) else ZERO
        marks: dict[str, Decimal] = {"high": basis}
        liq = await self._liquidity(mint)
        if liq is not None:
            marks["liq_entry"] = liq
        t = ShadowTrade(
            id=trade_id,
            leader_key=leader_key,
            signal_ts=now,
            mint=mint,
            leader_price=leader_price if leader_price is not None else basis,
            entry_price=entry,
            lag_s=max((now - leader_ts).total_seconds(), 0.0),
            lag_cost_pct=lag_cost,
            size_usd=size_usd,
            exit=copy_exit_rule(basis, now, self.cfg),
            marks=marks,
        )
        self._persist(t)
        return t

    # ------------------------------------------------------------------ contract

    async def on_leader_trades(
        self, trades: Sequence[LeaderTrade], pool: set[str], consensus_mints: Iterable[str]
    ) -> list[ShadowTrade]:
        """Open shadow trades: one per consensus mint and one per followed-leader buy; leader sells tighten trails."""
        opened: list[ShadowTrade] = []
        verified = [t for t in trades if t.chain_verified]
        buys = [t for t in verified if t.side == "buy" and t.key in pool]
        now = self.clock.now()
        for mint in dict.fromkeys(consensus_mints):
            if any(t.mint == mint and t.leader_key == CONSENSUS_KEY for t in self._open.values()):
                continue
            mint_buys = sorted((t for t in buys if t.mint == mint), key=lambda t: (t.ts, t.signature))
            earliest = next((t for t in mint_buys if t.price_usd is not None), None)
            leader_ts = mint_buys[0].ts if mint_buys else now
            trade_id = f"{CONSENSUS_KEY}:{mint}:{int(now.timestamp())}"
            if trade_id in self._ids:
                continue
            t = await self._open_trade(
                trade_id, CONSENSUS_KEY, mint, earliest.price_usd if earliest else None, leader_ts
            )
            if t is not None:
                opened.append(t)
        for lt in buys:
            trade_id = f"leader:{lt.signature}"
            if trade_id in self._ids:
                continue
            t = await self._open_trade(trade_id, lt.key, lt.mint, lt.price_usd, lt.ts)
            if t is not None:
                opened.append(t)
        for lt in verified:
            if lt.side != "sell" or lt.key not in pool:
                continue
            for st in list(self._open.values()):
                if st.mint == lt.mint and st.leader_key in (lt.key, CONSENSUS_KEY):
                    self._persist(st.model_copy(update={"exit": tighten_for_leader_sell(st.exit)}))
        return opened

    async def mark_and_exit(self) -> None:
        """Mark every open trade (1h/6h/24h + running high) and close those whose exit rule fires."""
        now = self.clock.now()
        open_trades = self.open_trades()
        if not open_trades:
            return
        mints = sorted({t.mint for t in open_trades})
        try:
            table = await self.prices.usd_prices(mints)
        except Exception as exc:
            self.ledger.add_event("warn", "shadow_mark_failed", {"error": str(exc)[:200]})
            return
        for t in open_trades:
            px = table.get(t.mint)
            if px is None or px <= 0:
                continue
            marks = dict(t.marks)
            marks["high"] = max(marks.get("high", t.entry_price), px)
            elapsed = now - t.signal_ts
            for key, horizon in MARK_HORIZONS.items():
                if key not in marks and elapsed >= horizon:
                    marks[key] = px
            liq = await self._liquidity(t.mint) if "liq_entry" in marks else None
            basis = t.entry_price / (ONE + self.cost)
            why = exit_reason(
                px,
                marks["high"],
                basis,
                t.exit,
                now,
                liquidity_usd=liq,
                entry_liquidity_usd=marks.get("liq_entry"),
                liquidity_collapse_pct=Decimal(self.cfg.liquidity_collapse_pct),
            )
            update: dict[str, Any] = {"marks": marks}
            if why is not None:
                exit_px = px * (ONE - self.cost)
                update.update(
                    exit_price=exit_px, exit_ts=now, exit_reason=why, pnl_pct=exit_px / t.entry_price - ONE
                )
            self._persist(t.model_copy(update=update))

    def report(self, days: int = 60) -> ShadowReport:
        """Promotion-gate report over the last ``days`` days of CLOSED consensus trades."""
        now = self.clock.now()
        everything = self.ledger.shadow_trades(since=EPOCH)
        observed_days = (
            int((now - min(t.signal_ts for t in everything)).total_seconds() // 86400) if everything else 0
        )
        recent = [t for t in everything if t.signal_ts >= now - timedelta(days=days)]
        closed = [t for t in recent if t.pnl_pct is not None]
        cons = [t for t in closed if t.leader_key == CONSENSUS_KEY]
        rets = [t.pnl_pct for t in cons if t.pnl_pct is not None]
        expectancy = (sum(rets, ZERO) / len(rets)) if rets else ZERO
        by_leader: dict[str, list[ShadowTrade]] = {}
        for t in closed:
            if t.leader_key != CONSENSUS_KEY:
                by_leader.setdefault(t.leader_key, []).append(t)
        per_leader = [
            LeaderStats(
                key=k,
                n_trades=len(v),
                copied_pnl_pct=sum((x.pnl_pct or ZERO for x in v), ZERO),
                positive=sum((x.pnl_pct or ZERO for x in v), ZERO) > 0,
            )
            for k, v in sorted(by_leader.items())
        ]
        leaders_share = (sum(1 for s in per_leader if s.positive) / len(per_leader)) if per_leader else 0.0
        p_block = bootstrap_p_positive_block(rets, 20, 5000, self.rng_seed)
        p = self.cfg.promotion
        reasons: list[str] = []
        if observed_days < int(p.min_days):
            reasons.append(f"days {observed_days} < {int(p.min_days)}")
        if len(cons) < int(p.min_trades):
            reasons.append(f"trades {len(cons)} < {int(p.min_trades)}")
        if expectancy <= Decimal(str(p.min_expectancy_pct)):
            reasons.append(f"expectancy {expectancy:.4f} <= {Decimal(str(p.min_expectancy_pct))}")
        if p_block < float(p.min_p_positive_block20):
            reasons.append(f"p_positive_block20 {p_block:.3f} < {float(p.min_p_positive_block20)}")
        if leaders_share < float(p.min_leaders_positive_share):
            reasons.append(
                f"leaders_positive_share {leaders_share:.3f} < {float(p.min_leaders_positive_share)}"
            )
        return ShadowReport(
            days=observed_days,
            n_trades=len(cons),
            expectancy_pct=expectancy,
            profit_factor=profit_factor(rets),
            median_lag_s=float(median(t.lag_s for t in cons)) if cons else 0.0,
            median_lag_cost_pct=median(t.lag_cost_pct for t in cons) if cons else ZERO,
            p_positive_block20=p_block,
            leaders_positive_share=leaders_share,
            per_leader=per_leader,
            promotion_gate_met=not reasons,
            reasons=reasons,
        )


# --------------------------------------------------------------------------- rendering


def _pct(x: Decimal | float) -> str:
    return f"{float(x) * 100:+.2f}%"


def render_shadow_report(report: ShadowReport, fmt: str = "both") -> str:
    """Render for ``tiller shadow-report``: a plain-text table (``'text'``), JSON (``'json'``) or both."""
    if fmt not in ("text", "json", "both"):
        raise ValueError("fmt must be text, json or both")
    lines: list[str] = []
    if fmt in ("text", "both"):
        rows = [
            ("days observed", str(report.days)),
            ("consensus trades (closed)", str(report.n_trades)),
            ("expectancy / trade", _pct(report.expectancy_pct)),
            ("profit factor", f"{report.profit_factor:.2f}"),
            ("median lag", f"{report.median_lag_s:.0f} s"),
            ("median lag cost", _pct(report.median_lag_cost_pct)),
            ("P(positive 20-trade block)", f"{report.p_positive_block20:.3f}"),
            ("leaders with positive copied P&L", f"{report.leaders_positive_share * 100:.0f}%"),
            ("promotion gate", "MET" if report.promotion_gate_met else "NOT MET"),
        ]
        w = max(len(k) for k, _ in rows)
        lines.append("SHADOW COPY REPORT (zero capital; live copy stays off until the gate is met)")
        lines.append("-" * (w + 20))
        lines.extend(f"{k.ljust(w)}  {v}" for k, v in rows)
        if report.reasons:
            lines.append("gate not met because:")
            lines.extend(f"  - {r}" for r in report.reasons)
        if report.per_leader:
            lines.append("")
            kw = max(len(s.key) for s in report.per_leader)
            lines.append(f"{'leader'.ljust(kw)}  trades  copied P&L  positive")
            for s in report.per_leader:
                lines.append(
                    f"{s.key.ljust(kw)}  {s.n_trades:6d}  {_pct(s.copied_pnl_pct):>10}  {'yes' if s.positive else 'no'}"
                )
    if fmt == "json":
        return json.dumps(json.loads(report.model_dump_json()), indent=2, sort_keys=True)
    if fmt == "both":
        lines.append("")
        lines.append(json.dumps(json.loads(report.model_dump_json()), indent=2, sort_keys=True))
    return "\n".join(lines)
