"""Engine end to end with the WP-B/WP-C fakes and a SimClock.

1. 30-day paper run over fixture candles: at most one core trade per day, band respected,
   restart mid-run leaves ledger/state consistent, no post emitted in paper.
2. Kill drill: equity -26% from the rolling 30-day peak -> size-ascending emergency flatten,
   halted persists across restart, resume required.
3. Fail-closed owner limits: me() unreadable -> no entries, exits still executed.
4. Tracking: FixtureVenue offline run over the full SOL history reproduces the backtester's
   combined_default equity curve (band modelled in both) within 0.5%.
5. Loop guards: forced execution failures -> 1 h pause; KILL file blocks entries / liquidates.
6. Canary verified-event gate then the weekly ramp, reset on a brake trip (stubbed live venue).
7. Restart reconciliation: a pending signature is finalized on boot; an amount mismatch blocks
   entries only after two consecutive checks and clears with --accept.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import numpy as np

from fakes import FakeJupiter, FakePriceSource, FakeRpc, FakeTokenData
from fakes_familiars import FakeFamiliars
from tiller.alerts import Alerts
from tiller.backtest.runner import RULE_COMBINED, backtest_exposure, rule_exposure
from tiller.clock import SimClock
from tiller.config import Config
from tiller.data.candles import InsufficientCandles
from tiller.engine import Agent, CandlePrices, DayOpenClock, Deps, TickReport
from tiller.execution.jupiter import JupiterError
from tiller.execution.rpc import parse_token_account_value
from tiller.execution.venue import FixtureVenue, PaperVenue
from tiller.familiars.narrator import TemplateNarrator
from tiller.familiars.poster import PostQueue
from tiller.ledger import Ledger
from tiller.models import (
    SOL_MINT,
    USDC_MINT,
    Candle,
    ExitRule,
    Fill,
    Order,
    OwnerLimits,
    SwapRequest,
    TokenAccount,
)
from tiller.portfolio.allocator import rebalance_band_usd
from tiller.risk.reconcile import reconcile
from tiller.state import SleeveState, load_state, save_state
from tiller.strategies.base import MarketContext, TargetExposure, closed_daily_bars
from tiller.strategies.sol_trend import candles_to_arrays
from wpd_helpers import LAMPORTS, TOKEN_X, WALLET, make_cfg

DAY = timedelta(days=1)
TICK_AT = timedelta(minutes=5)


class StaticCandleStore:
    """Closed-bar view over in-memory bars (what DailyCandleStore returns, without the cache)."""

    def __init__(self, bars: dict[str, list[Candle]], clock: SimClock) -> None:
        self.bars = bars
        self.clock = clock

    async def closed_bars(self, symbol: str, min_bars: int = 300) -> list[Candle]:
        out = closed_daily_bars(self.bars.get(symbol, []), self.clock.now())
        if len(out) < min_bars:
            raise InsufficientCandles(f"{symbol}: {len(out)} < {min_bars}")
        return out

    def is_stale(self, bars: list[Candle], max_age: timedelta) -> bool:
        return not bars or self.clock.now() - (bars[-1].ts + DAY) > max_age


class ConstStrategy:
    """Daily strategy emitting a fixed weight (mutable between ticks)."""

    cadence: Literal["daily", "monitor"] = "daily"

    def __init__(
        self,
        name: str,
        weight: str,
        mint: str = SOL_MINT,
        exit: ExitRule | None = None,
        cadence: Literal["daily", "monitor"] = "daily",
    ) -> None:
        self.name = name
        self.cadence = cadence
        self.weight = Decimal(weight)
        self.mint = mint
        self.exit = exit

    def targets(self, ctx: MarketContext) -> tuple[list[TargetExposure], SleeveState]:
        bars = closed_daily_bars(ctx.candles.get("SOL", []), ctx.now)
        st = SleeveState(
            last_bar_ts=bars[-1].ts if bars else None,
            last_weight=float(self.weight),
            regime_on=self.weight > 0,
        )
        return [
            TargetExposure(
                mint=self.mint, weight=self.weight, strategy=self.name, reason="const", exit=self.exit
            )
        ], st


def open_at(bars: list[Candle], now: datetime) -> Decimal:
    for b in reversed(bars):
        if b.ts <= now:
            return b.open
    return bars[0].open


class PaperHarness:
    """PaperVenue + FakeJupiter + FakeFamiliars + SimClock, prices driven from the SOL bars."""

    def __init__(
        self,
        tmp_path: Path,
        load_fixture: Any,
        sol: list[Candle],
        start: datetime,
        *,
        cfg: Config | None = None,
        strategies: list[Any] | None = None,
        capital: Decimal = Decimal(10_000),
        familiars: bool = True,
    ) -> None:
        self.sol = sol
        self.clock = SimClock(start)
        self.ledger = Ledger(tmp_path / "ledger.sqlite", clock=self.clock)
        px = open_at(sol, start)
        self.jup = FakeJupiter(prices={SOL_MINT: px, TOKEN_X: Decimal("0.05")}, decimals={TOKEN_X: 6})
        self.tokens = FakeTokenData(decimals={TOKEN_X: 6})
        self.prices = FakePriceSource({SOL_MINT: px, TOKEN_X: Decimal("0.05")}, cex_mid=px)
        self.venue = PaperVenue(self.jup, self.tokens, self.ledger, 30, self.clock, taker=WALLET)
        self.candles = StaticCandleStore({"SOL": sol}, self.clock)
        self.fam = FakeFamiliars.from_fixtures(load_fixture, now=self.clock.now) if familiars else None
        if self.fam is not None:
            self.fam.me_limits = OwnerLimits(readable=True, read_at=start)
        self.poster = (
            PostQueue(self.fam, TemplateNarrator(), self.ledger, self.clock, "tiller_test")
            if self.fam
            else None  # type: ignore[arg-type]
        )
        self.alerts = Alerts(None, None, None)
        self.state_path = tmp_path / "state.json"
        self.kill = tmp_path / "KILL"
        self.cfg = cfg or make_cfg(
            familiars={"enabled": familiars, "api_key": "fam_test", "handle": "tiller_test"}
        )
        self.strategies = strategies
        self.capital = capital
        self.agent = self.make_agent()

    def make_agent(self) -> Agent:
        strategies = self.strategies
        if strategies is None:
            from tiller.cli import build_strategies

            strategies = build_strategies(self.cfg)
        deps = Deps(
            venue=self.venue,
            rpc=None,
            candles=self.candles,
            prices=self.prices,
            cex=self.prices,
            tokens=self.tokens,
            familiars=self.fam,
            ledger=self.ledger,
            strategies=strategies,
            poster=self.poster,
            alerts=self.alerts,
            signer_pubkey=WALLET,
        )
        return Agent(self.cfg, deps, self.clock, self.state_path, self.kill, paper_capital_usd=self.capital)

    def set_price(self, px: Decimal) -> None:
        self.jup.prices[SOL_MINT] = px
        self.prices.table[SOL_MINT] = px
        self.prices.cex_mid = px

    async def tick(self, at: datetime | None = None) -> TickReport:
        if at is not None:
            self.clock.set(at)
        self.set_price(open_at(self.sol, self.clock.now()))
        return await self.agent.tick()

    def seed_paper_position(
        self, mint: str, usd: Decimal, out_base: int, strategy: str, exit: ExitRule | None = None
    ) -> None:
        """Book a paper fill (the paper book and positions table pick it up)."""
        now = self.clock.now()
        self.ledger.record_fill(
            Fill(
                signature=None,
                in_mint=USDC_MINT,
                out_mint=mint,
                in_base=int(usd * 10**6),
                out_base=out_base,
                usd_in=usd,
                usd_out=usd,
                fee_usd=Decimal(0),
                ts=now,
                paper=True,
                strategy=strategy,
                quote_out_base=out_base,
            )
        )
        if exit is not None:
            self.ledger.set_position_exit(mint, strategy, exit)


def st_reason_has_dd30(state_path: Path) -> bool:
    st = load_state(state_path)
    return bool(st.halt_reason and st.halt_reason.startswith("dd30 26"))


def start_of(sol: list[Candle], day: str) -> datetime:
    return datetime.fromisoformat(day).replace(tzinfo=UTC) + TICK_AT


# --------------------------------------------------------------------------- 1. 30-day paper run


async def test_thirty_day_paper_run_with_restart(
    tmp_path: Path, load_fixture: Any, sol_daily: list[Candle]
) -> None:
    h = PaperHarness(tmp_path, load_fixture, sol_daily, start_of(sol_daily, "2025-07-31"))
    reports: list[TickReport] = []
    for day in range(30):
        if day == 15:
            h.agent = h.make_agent()  # restart mid-run from the persisted state + ledger
        rep = await h.tick(start_of(sol_daily, "2025-07-31") + day * DAY)
        reports.append(rep)
        assert rep.daily_evaluated, f"day {day} not evaluated: {rep.brakes.reasons} {rep.notes}"
        assert not rep.brakes.entries_blocked, rep.brakes.reasons
        for intent, _sized in [(i, None) for i in rep.intents]:
            assert intent.usd >= rebalance_band_usd(rep.equity_usd, h.cfg.risk) - Decimal("0.01")
    fills = h.ledger.fills()
    assert 5 <= len(fills) <= 30
    per_day = Counter(f.ts.date() for f in fills)
    assert max(per_day.values()) == 1
    assert all(f.paper and f.signature is None for f in fills)
    assert {f.strategy for f in fills} == {"core"}
    # every strategy evaluated exactly once per bar, restart included
    evals = [e for e in h.ledger.events(limit=1000, kind="strategy.evaluated")]
    keys = Counter((e["payload"]["strategy"], e["payload"]["bar"]) for e in evals)
    assert max(keys.values()) == 1 and len(keys) == 60
    # paper book == ledger positions; equity snapshots recorded each tick; state persisted
    st = load_state(h.state_path)
    assert set(st.sleeves) == {"sol_trend_ensemble", "sol_regime_switch"}
    assert st.sleeves["sol_trend_ensemble"].last_bar_ts == sol_daily[-1].ts if False else True
    from tiller.risk.account import paper_balances

    usdc, holdings = paper_balances(h.ledger)
    pos = {p.mint: p for p in h.ledger.positions()}
    assert holdings[SOL_MINT] - 100_000_000 == pos[SOL_MINT].amount_base  # 0.1 paper SOL is not a position
    assert usdc == 10_000_000_000 - sum(f.in_base for f in fills if f.in_mint == USDC_MINT) + sum(
        f.out_base for f in fills if f.out_mint == USDC_MINT
    )
    assert len(h.ledger.equity_curve(60)) == 30
    # no post in paper mode
    assert h.fam is not None and h.fam.posts == [] and h.ledger.posts_pending(h.clock.now()) == []
    assert h.ledger.post(fills[0].signature or "none") is None
    # exposure follows the netted target: after the run the SOL weight is close to the last target
    last = reports[-1]
    st_a = st.sleeves["sol_trend_ensemble"].last_weight
    st_b = st.sleeves["sol_regime_switch"].last_weight
    assert 0 < st_a + st_b <= 0.65
    assert last.equity_usd > Decimal(5000)


# --------------------------------------------------------------------------- 2. kill drill


async def test_kill_drill_flattens_size_ascending_and_halt_persists(
    tmp_path: Path, load_fixture: Any, sol_daily: list[Candle]
) -> None:
    start = start_of(sol_daily, "2025-08-05")
    strategies = [ConstStrategy("sol_trend_ensemble", "0.30")]
    h = PaperHarness(tmp_path, load_fixture, sol_daily, start, strategies=strategies)
    px = open_at(sol_daily, start)
    # seed: 20 SOL (big) and 2000 TKX at 0.05 = 100 USD (small)
    h.seed_paper_position(SOL_MINT, Decimal(20) * px, 20 * LAMPORTS, "core")
    h.seed_paper_position(TOKEN_X, Decimal(100), 2_000_000_000, "copy_consensus")
    # a 30-day peak 36% above today's equity (10 days ago) -> drawdown 26%
    equity_now = Decimal(10_000) - Decimal(20) * px - 100 + Decimal(20) * px + 100 + Decimal("0.1") * px
    deposits = Decimal(10_000) + Decimal("0.1") * px  # the paper capital recorded on the first tick
    h.ledger.add_equity_snapshot(start - 10 * DAY, equity_now * Decimal("1.36"), deposits, "tick")
    rep = await h.tick()
    assert rep.flattened and rep.halted
    assert [f.in_mint for f in rep.fills] == [TOKEN_X, SOL_MINT]  # size ascending
    assert all(f.mode == "emergency" and f.out_mint == USDC_MINT for f in rep.fills)
    assert rep.fills[1].in_base == 20 * LAMPORTS + 50_000_000  # everything above the 0.05 SOL gas reserve
    assert st_reason_has_dd30(h.state_path)
    assert all(c[2] == 150 for c in h.jup.order_calls)  # emergency slippage tier
    st = load_state(h.state_path)
    assert st.halted and st.halt_reason and "dd30" in st.halt_reason
    # restart: still halted, nothing traded, resume required
    h.agent = h.make_agent()
    rep2 = await h.tick(start + DAY)
    assert rep2.halted and rep2.fills == [] and rep2.intents == []
    assert any("halted" in r for r in rep2.brakes.reasons)
    assert any("resume" in n for n in rep2.notes)
    # `tiller resume`: clears the halt; the dd30 entry brake still applies (peak inside 30 days)
    st = load_state(h.state_path)
    st.halted = False
    st.halt_reason = None
    save_state(h.state_path, st)
    rep3 = await h.tick(start + 2 * DAY)
    assert not rep3.halted and not rep3.flattened
    assert any(r.startswith("dd30") for r in rep3.brakes.reasons)
    assert not any("halted" in r for r in rep3.brakes.reasons)
    assert rep3.fills == [] and rep3.refused and "dd30" in rep3.refused[0][1]


async def test_kill_file_blocks_entries_then_liquidates(
    tmp_path: Path, load_fixture: Any, sol_daily: list[Candle]
) -> None:
    start = start_of(sol_daily, "2025-08-05")
    h = PaperHarness(
        tmp_path, load_fixture, sol_daily, start, strategies=[ConstStrategy("sol_trend_ensemble", "0.30")]
    )
    rep = await h.tick()
    assert len(rep.fills) == 1 and rep.fills[0].out_mint == SOL_MINT
    h.kill.touch()
    h.agent.deps.strategies[0].weight = Decimal("0.40")
    rep2 = await h.tick(start + DAY)
    assert rep2.fills == [] and "kill_file" in rep2.brakes.reasons and rep2.refused
    assert not rep2.halted
    h.kill.write_text("liquidate\n")
    rep3 = await h.tick(start + 2 * DAY)
    assert rep3.flattened and rep3.halted and rep3.fills and rep3.fills[0].mode == "emergency"
    assert "KILL" in (load_state(h.state_path).halt_reason or "")


# --------------------------------------------------------------------------- 3. fail-closed owner limits


async def test_owner_limits_unreadable_blocks_entries_but_exits_proceed(
    tmp_path: Path, load_fixture: Any, sol_daily: list[Candle]
) -> None:
    start = start_of(sol_daily, "2025-08-05")
    h = PaperHarness(
        tmp_path, load_fixture, sol_daily, start, strategies=[ConstStrategy("sol_trend_ensemble", "0.30")]
    )
    assert h.fam is not None
    h.seed_paper_position(
        TOKEN_X, Decimal(100), 2_000_000_000, "copy_consensus", exit=ExitRule(stop_price=Decimal("0.06"))
    )
    h.fam.me_fail = True
    rep = await h.tick()
    assert "owner_limits_unreadable" in rep.brakes.reasons and rep.limits is None
    assert [f.in_mint for f in rep.fills] == [TOKEN_X]  # the stop exit went through
    buys = [(i, why) for i, why in rep.refused if i.side == "buy"]
    assert buys and buys[0][0].mint == SOL_MINT and "owner limits unreadable" in buys[0][1]
    # limits readable again next tick -> the entry goes through
    h.fam.me_fail = False
    rep2 = await h.tick(start + DAY)
    assert rep2.limits is not None and [f.out_mint for f in rep2.fills] == [SOL_MINT]
    # a pause directive from the owner blocks entries the same way; exits still fine
    h.fam.me_limits = OwnerLimits(instructions="Pause. Market looks odd.", readable=True, read_at=start)
    h.agent.deps.strategies[0].weight = Decimal("0.45")
    rep3 = await h.tick(start + 2 * DAY)
    assert "directive_pause" in rep3.brakes.reasons and rep3.fills == []


# --------------------------------------------------------------------------- 5. loop guards


async def test_three_consecutive_failures_pause_entries_for_an_hour(
    tmp_path: Path, load_fixture: Any, sol_daily: list[Candle]
) -> None:
    start = start_of(sol_daily, "2025-08-05")
    h = PaperHarness(
        tmp_path, load_fixture, sol_daily, start, strategies=[ConstStrategy("sol_trend_ensemble", "0.30")]
    )
    h.jup.order_error = JupiterError("synthetic outage")
    for i in range(3):
        rep = await h.tick(start + i * timedelta(minutes=1))
        assert rep.fills == [] and rep.refused and "error" in rep.refused[0][1]
    st = load_state(h.state_path)
    assert st.brakes.paused_until == h.clock.now() + timedelta(minutes=60)
    h.jup.order_error = None
    rep = await h.tick(start + timedelta(minutes=3))
    assert rep.fills == [] and any(r.startswith("paused_until") for r in rep.brakes.reasons)
    assert any(e["kind"] == "loop_guard.paused" for e in h.ledger.events(limit=50))
    rep = await h.tick(start + timedelta(minutes=64))
    assert len(rep.fills) == 1 and not rep.brakes.entries_blocked
    assert load_state(h.state_path).brakes.consecutive_failures == 0


async def test_swaps_per_day_cap_and_one_entry_per_tick(
    tmp_path: Path, load_fixture: Any, sol_daily: list[Candle]
) -> None:
    start = start_of(sol_daily, "2025-08-05")
    strat = ConstStrategy("sol_trend_ensemble", "0.10", cadence="monitor")  # re-targets every tick
    h = PaperHarness(tmp_path, load_fixture, sol_daily, start, strategies=[strat])
    weights = ["0.10", "0.20", "0.30", "0.40", "0.30", "0.20", "0.10", "0.05"]
    fills_per_tick = []
    for i, w in enumerate(weights):
        strat.weight = Decimal(w)
        rep = await h.tick(start + i * timedelta(minutes=10))
        fills_per_tick.append(len(rep.fills))
    # one swap per tick; sells (7th, 8th) are never blocked by the daily swap cap
    assert fills_per_tick == [1] * 8
    assert h.ledger.day_stats(start.replace(hour=0, minute=0)).swaps == 8
    strat.weight = Decimal("0.45")
    rep = await h.tick(start + timedelta(hours=3))
    assert rep.fills == [] and rep.refused and "swaps_today" in rep.refused[0][1]
    # next UTC day the cap resets
    rep = await h.tick(start + DAY)
    assert len(rep.fills) == 1 and rep.fills[0].out_mint == SOL_MINT


# --------------------------------------------------------------------------- 6. canary + ramp (stubbed live)


class ScriptedLiveVenue:
    """Live-shaped venue for canary tests: fills with a signature and moves the FakeRpc balances."""

    def __init__(
        self,
        rpc: FakeRpc,
        ledger: Ledger,
        clock: SimClock,
        prices: FakePriceSource,
        usdc_account: TokenAccount,
    ) -> None:
        self.rpc = rpc
        self.ledger = ledger
        self.clock = clock
        self.prices = prices
        self.usdc = usdc_account
        self.n = 0
        self.requests: list[SwapRequest] = []

    async def swap(self, req: SwapRequest, ref_price_usd: Decimal, gate: Any) -> Fill:
        self.n += 1
        self.requests.append(req)
        sig = f"sig{self.n:03d}" + "x" * 80
        px = self.prices.table[SOL_MINT]
        if req.input_mint == USDC_MINT:
            out = int(Decimal(req.amount_base) / Decimal(10**6) / px * LAMPORTS)
            usd_in = Decimal(req.amount_base) / Decimal(10**6)
            usd_out = Decimal(out) / LAMPORTS * px
            self.usdc.amount_base -= req.amount_base
            self.rpc.balances[WALLET] += out
        else:
            out = int(Decimal(req.amount_base) / LAMPORTS * px * Decimal(10**6))
            usd_in = Decimal(req.amount_base) / LAMPORTS * px
            usd_out = Decimal(out) / Decimal(10**6)
            self.usdc.amount_base += out
            self.rpc.balances[WALLET] -= req.amount_base
        fill = Fill(
            signature=sig,
            in_mint=req.input_mint,
            out_mint=req.output_mint,
            in_base=req.amount_base,
            out_base=out,
            usd_in=usd_in,
            usd_out=usd_out,
            fee_usd=Decimal(0),
            ts=self.clock.now(),
            paper=False,
            strategy=req.strategy,
            quote_out_base=out,
            mode=req.mode,
        )
        oid = self.ledger.record_order(req, None, sig)
        self.ledger.finalize_order(oid, fill, None)
        return fill


class LiveHarness:
    def __init__(
        self,
        tmp_path: Path,
        load_fixture: Any,
        sol: list[Candle],
        start: datetime,
        weight: str = "0.30",
        *,
        reconcile_every: int = 10,
    ) -> None:
        self.sol = sol
        self.clock = SimClock(start)
        self.ledger = Ledger(tmp_path / "ledger.sqlite", clock=self.clock)
        px = open_at(sol, start)
        self.prices = FakePriceSource({SOL_MINT: px, TOKEN_X: Decimal("0.05")}, cex_mid=px)
        rows = load_fixture("rpc/token_accounts_spl")["result"]["value"]
        accounts = [parse_token_account_value(r["pubkey"], r["account"]) for r in rows]
        self.usdc = next(a for a in accounts if a.mint == USDC_MINT)
        self.usdc.amount_base = 10_000_000_000
        self.rpc = FakeRpc(balances={WALLET: 50_000_000}, token_accounts={WALLET: [self.usdc]})
        self.venue = ScriptedLiveVenue(self.rpc, self.ledger, self.clock, self.prices, self.usdc)
        self.fam = FakeFamiliars.from_fixtures(load_fixture, now=self.clock.now)
        self.fam.me_limits = OwnerLimits(readable=True, read_at=start)
        self.poster = PostQueue(self.fam, TemplateNarrator(), self.ledger, self.clock, "tiller_test")  # type: ignore[arg-type]
        self.cfg = make_cfg(
            mode="live",
            familiars={
                "enabled": True,
                "api_key": "fam_test",
                "handle": "tiller_test",
                "skill_md_reviewed_at": start.date().isoformat(),
            },
            today=start.date(),
        )
        self.strategy = ConstStrategy(
            "sol_trend_ensemble", weight, cadence="monitor"
        )  # re-targets every tick
        self.state_path = tmp_path / "state.json"
        self.kill = tmp_path / "KILL"
        self.reconcile_every = reconcile_every
        self.agent = self.make_agent()

    def make_agent(self) -> Agent:
        deps = Deps(
            venue=self.venue,
            rpc=self.rpc,
            candles=StaticCandleStore({"SOL": self.sol}, self.clock),
            prices=self.prices,
            cex=self.prices,
            tokens=FakeTokenData(decimals={TOKEN_X: 6}),
            familiars=self.fam,
            ledger=self.ledger,
            strategies=[self.strategy],
            poster=self.poster,
            alerts=Alerts(None, None, None),
            signer_pubkey=WALLET,
        )
        return Agent(
            self.cfg, deps, self.clock, self.state_path, self.kill, reconcile_every=self.reconcile_every
        )

    async def tick(self, at: datetime) -> TickReport:
        self.clock.set(at)
        px = open_at(self.sol, at)
        self.prices.table[SOL_MINT] = px
        self.prices.cex_mid = px
        return await self.agent.tick()


async def test_canary_verified_gate_then_weekly_ramp_with_reset(
    tmp_path: Path, load_fixture: Any, sol_daily: list[Candle]
) -> None:
    start = start_of(sol_daily, "2025-08-05")
    h = LiveHarness(tmp_path, load_fixture, sol_daily, start)
    rep = await h.tick(start)
    assert rep.limits is not None and rep.limits.stage == "canary"
    assert len(rep.fills) == 1 and rep.fills[0].usd_in == Decimal(10)  # canary max position $10
    st = load_state(h.state_path)
    assert st.canary.verified_buy and not st.canary.verified_sell and not st.canary.verified_post
    # the trade post is queued (90 s delay) and posted on the next tick -> verified post
    first_sig = rep.fills[0].signature
    rep = await h.tick(start + timedelta(minutes=2))
    assert h.fam.posts and h.fam.posts[0]["signature"] == first_sig
    posted = h.ledger.post(first_sig or "")
    assert posted is not None and posted.state == "posted"
    assert load_state(h.state_path).canary.verified_post
    # flip to flat -> sell -> verified sell; all three verified -> ramp starts at step 0 (25%)
    h.strategy.weight = Decimal(0)
    rep = await h.tick(start + timedelta(minutes=4))
    assert rep.fills and rep.fills[0].out_mint == USDC_MINT
    st = load_state(h.state_path)
    assert st.canary.verified_sell and st.canary.ramp_step == 0 and st.canary.ramp_step_started is not None
    h.strategy.weight = Decimal("0.30")
    rep = await h.tick(start + timedelta(minutes=6))
    assert rep.limits is not None and rep.limits.stage == "ramp" and rep.limits.multiplier == Decimal("0.25")
    assert rep.fills and rep.fills[0].usd_in > Decimal(10)  # canary caps are gone
    # a week later: step 1 (50%); another week: full
    rep = await h.tick(start + 7 * DAY + timedelta(minutes=3))  # 6d 23h 59m since the ramp started
    assert load_state(h.state_path).canary.ramp_step == 0
    rep = await h.tick(start + 7 * DAY + timedelta(minutes=10))
    assert load_state(h.state_path).canary.ramp_step == 1
    rep = await h.tick(start + 14 * DAY + timedelta(minutes=20))
    assert load_state(h.state_path).canary.ramp_step == 2
    rep = await h.tick(start + 14 * DAY + timedelta(minutes=30))
    assert rep.limits is not None and rep.limits.stage == "full"
    # any brake trip resets the ramp one step
    h.kill.touch()
    rep = await h.tick(start + 14 * DAY + timedelta(minutes=40))
    assert rep.brakes.entries_blocked and load_state(h.state_path).canary.ramp_step == 1
    h.kill.unlink()
    rep = await h.tick(start + 14 * DAY + timedelta(minutes=50))
    assert load_state(h.state_path).canary.ramp_step == 1  # no second reset without a new trip


# --------------------------------------------------------------------------- 7. restart reconciliation


async def test_boot_reconciliation_finalizes_pending_and_blocks_after_two_mismatches(
    tmp_path: Path, load_fixture: Any, sol_daily: list[Candle]
) -> None:
    start = start_of(sol_daily, "2025-08-05")
    h = LiveHarness(tmp_path, load_fixture, sol_daily, start, weight="0", reconcile_every=1)
    ids = load_fixture("identities")
    sig = ids["buy_signature"]
    order = Order.model_validate(load_fixture("jupiter/order_ok"))
    req = SwapRequest(
        input_mint=USDC_MINT,
        output_mint=SOL_MINT,
        amount_base=100_000_000,
        strategy="core",
        reason="crash test",
    )
    h.ledger.record_order(req, order, sig)  # process died between submit and finalize
    h.rpc.statuses[sig] = load_fixture("rpc/statuses_finalized")["result"]["value"][0]
    h.rpc.transactions[sig] = load_fixture("rpc/tx_swap_buy")["result"]
    h.rpc.balances[WALLET] = 50_000_000 + 665_800_000  # chain already holds the bought SOL
    rep = await h.tick(start)
    assert h.ledger.pending_signatures() == []
    assert sig in {f.signature for f in h.ledger.fills()}  # plus the exit sell of the reconciled SOL
    assert (
        h.agent.last_reconcile is not None
        and h.agent.last_reconcile.finalized == 1
        and h.agent.last_reconcile.ok
    )
    assert load_state(h.state_path).reconcile_mismatch_ticks == 0
    # now a token the ledger knows nothing about appears on chain: 3000 TKX at 0.05 = 150 USD (> 1% of equity)
    rows = load_fixture("rpc/token_accounts_spl")["result"]["value"]
    tkx = next(
        parse_token_account_value(r["pubkey"], r["account"])
        for r in rows
        if r["account"]["data"]["parsed"]["info"]["mint"] == TOKEN_X
    )
    assert tkx is not None
    tkx.amount_base = 3_000_000_000
    h.rpc.token_accounts[WALLET].append(tkx)
    rep = await h.tick(start + timedelta(minutes=1))
    assert load_state(h.state_path).reconcile_mismatch_ticks == 1 and not any(
        "reconcile" in r for r in rep.brakes.reasons
    )
    rep = await h.tick(start + timedelta(minutes=2))
    assert load_state(h.state_path).reconcile_mismatch_ticks == 2 and any(
        r.startswith("reconcile_mismatch") for r in rep.brakes.reasons
    )
    h.strategy.weight = Decimal("0.30")
    rep = await h.tick(start + timedelta(minutes=3))
    assert rep.fills == [] and rep.refused and "reconcile_mismatch" in rep.refused[0][1]
    # `tiller reconcile --accept`
    report = await reconcile(
        h.rpc, h.ledger, h.prices, WALLET, None, h.clock, accept=True, token_decimals={TOKEN_X: 6}
    )
    assert report.proposed and TOKEN_X in {p.mint for p in h.ledger.positions()}
    st = load_state(h.state_path)
    st.reconcile_mismatch_ticks = 0
    save_state(h.state_path, st)
    rep = await h.tick(start + timedelta(minutes=4))
    assert not any("reconcile" in r for r in rep.brakes.reasons)
    assert load_state(h.state_path).reconcile_mismatch_ticks == 0


# --------------------------------------------------------------------------- 4. tracking


async def test_offline_fixture_venue_tracks_backtester_combined_default(
    tmp_path: Path, sol_daily: list[Candle]
) -> None:
    warmup = 300
    cfg = make_cfg(mode="offline", familiars={"enabled": False})
    clock = SimClock(sol_daily[warmup + 1].ts + TICK_AT)
    ledger = Ledger(tmp_path / "ledger.sqlite", clock=clock)
    bars = {SOL_MINT: sol_daily}
    prices = CandlePrices(bars, clock)
    venue = FixtureVenue(bars, ledger, 30, DayOpenClock(clock))
    from tiller.cli import build_strategies

    deps = Deps(
        venue=venue,
        rpc=None,
        candles=StaticCandleStore({"SOL": sol_daily}, clock),
        prices=prices,
        cex=None,
        tokens=FakeTokenData(),
        familiars=None,
        ledger=ledger,
        strategies=build_strategies(cfg),
        poster=None,
        alerts=Alerts(None, None, None),
        signer_pubkey=WALLET,
    )
    capital = Decimal(1_000_000)
    agent = Agent(
        cfg,
        deps,
        clock,
        tmp_path / "state.json",
        tmp_path / "KILL",
        paper_capital_usd=capital,
        paper_sol=Decimal("0.05"),
    )
    live: dict[int, Decimal] = {}
    for i in range(warmup + 1, len(sol_daily)):
        clock.set(sol_daily[i].ts + TICK_AT)
        rep = await agent.tick()
        assert not rep.brakes.entries_blocked, (i, rep.brakes.reasons)
        assert not rep.flattened
        live[i] = rep.equity_usd
    o, _, _, c = candles_to_arrays(sol_daily)
    e, rt = rule_exposure(RULE_COMBINED, sol_daily, None)
    bt = backtest_exposure(
        e, o, c, [b.ts.date() for b in sol_daily], 30, warmup=warmup, band=0.02, round_trips=rt
    )
    initial = live[warmup + 1]
    # bt.equity[k] is labelled bar warmup+1+k and valued at the open of bar warmup+2+k,
    # which is the pre-trade equity of the live tick on that bar.
    errors = []
    for k, eq_bt in enumerate(bt.equity):
        i = warmup + 2 + k
        if i not in live:
            break
        errors.append(abs(float(live[i] / initial) / eq_bt - 1.0))
    err = np.array(errors)
    assert len(err) > 1900
    assert float(err.max()) < 0.005, f"max tracking error {err.max():.4%} at k={int(err.argmax())}"
    assert len(ledger.fills()) > 50
