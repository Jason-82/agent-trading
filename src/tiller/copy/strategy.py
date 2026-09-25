"""Consensus copy strategy (live=false by default) and its pure helper rules.

* :func:`consensus` — >= ``min_clusters`` distinct leader clusters bought the same mint inside the window;
* :func:`crowd_burst` — the mint's ``/api/tokens`` agent count rose by more than ``max_delta`` (15) vs the
  snapshot taken ~1 h earlier (the caller supplies both snapshots; see :class:`TokenSnapshotRing`);
* :func:`late_entry_ok` — current price at most 15% above the earliest leader fill;
* :class:`ConsensusCopyStrategy` — returns no targets unless ``cfg.live`` AND the shadow promotion gate
  is met AND the SOL daily regime is ON; entries carry our exit rule and respect the caps
  (2 concurrent, 2 entries per UTC day, 5% sleeve, 2% per signal); sells and sizes are never mirrored.

Units: prices USD ``Decimal``; percentages fractions; weights are fractions of TOTAL equity.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from pydantic import field_validator

from tiller.copy.leaders import copy_exit_rule
from tiller.copy.models import LeaderTrade
from tiller.copy.shadow import ShadowTracker
from tiller.models import DomainModel, TokenInfoBoard, _to_utc
from tiller.state import SleeveState
from tiller.strategies.base import MarketContext, TargetExposure

ONE = Decimal(1)
STRATEGY_NAME = "copy_consensus"


class ConsensusSignal(DomainModel):
    """``cluster_count`` distinct clusters bought ``mint``; ``earliest_leader_price`` is the first fill's USD price."""

    mint: str
    earliest_leader_price: Decimal
    cluster_count: int
    first_ts: datetime

    @field_validator("first_ts")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _to_utc(v)


class CopyCandidate(DomainModel):
    """A consensus signal plus the facts the strategy needs to decide an entry (all computed by the engine)."""

    signal: ConsensusSignal
    current_price: Decimal
    gate_ok: bool
    crowd_burst: bool
    liquidity_usd: Decimal | None = None


# --------------------------------------------------------------------------- pure rules


def consensus(
    trades: Sequence[LeaderTrade],
    pool: set[str],
    clusters: Mapping[str, int],
    window: timedelta,
    min_clusters: int,
    now: datetime,
) -> list[ConsensusSignal]:
    """Mints bought by >= ``min_clusters`` DISTINCT clusters of pool leaders within ``window`` of ``now``.

    Only chain-verified buys count; leaders without a cluster id are ignored (fail closed); a mint whose
    earliest fill has no USD price yields no signal (the late-entry guard could not be applied).
    """
    since = now - window
    by_mint: dict[str, list[LeaderTrade]] = defaultdict(list)
    for t in trades:
        if t.side != "buy" or not t.chain_verified or t.key not in pool or t.key not in clusters:
            continue
        if since <= t.ts <= now:
            by_mint[t.mint].append(t)
    out: list[ConsensusSignal] = []
    for mint in sorted(by_mint):
        rows = sorted(by_mint[mint], key=lambda t: (t.ts, t.signature))
        distinct = {clusters[t.key] for t in rows}
        if len(distinct) < min_clusters:
            continue
        earliest = next((t for t in rows if t.price_usd is not None and t.price_usd > 0), None)
        if earliest is None:
            continue
        out.append(
            ConsensusSignal(
                mint=mint,
                earliest_leader_price=earliest.price_usd or ONE,
                cluster_count=len(distinct),
                first_ts=rows[0].ts,
            )
        )
    return out


def crowd_burst(
    prev_tokens: Sequence[TokenInfoBoard],
    cur_tokens: Sequence[TokenInfoBoard],
    mint: str,
    max_delta: int = 15,
) -> bool:
    """True when ``mint``'s agent count grew by MORE than ``max_delta`` between the two ``/api/tokens`` snapshots.

    A mint absent from ``prev_tokens`` counts from 0 (a brand-new row with 16+ agents is a burst).
    """
    prev = next((t.agents for t in prev_tokens if t.mint == mint), 0)
    cur = next((t.agents for t in cur_tokens if t.mint == mint), 0)
    return cur - prev > max_delta


def late_entry_ok(
    current_price: Decimal | None, earliest_leader_price: Decimal | None, max_above: Decimal = Decimal("0.15")
) -> bool:
    """Entry allowed only while ``current_price <= earliest_leader_price * (1 + max_above)``; unknown prices refuse."""
    if (
        current_price is None
        or earliest_leader_price is None
        or earliest_leader_price <= 0
        or current_price <= 0
    ):
        return False
    return current_price <= earliest_leader_price * (ONE + Decimal(max_above))


class TokenSnapshotRing:
    """Keeps recent ``/api/tokens`` snapshots so :func:`crowd_burst` can compare against one ``age`` old."""

    def __init__(self, keep: timedelta = timedelta(hours=2)) -> None:
        self.keep = keep
        self._rows: list[tuple[datetime, list[TokenInfoBoard]]] = []

    def add(self, ts: datetime, tokens: Iterable[TokenInfoBoard]) -> None:
        self._rows.append((ts, list(tokens)))
        self._rows = [(t, s) for t, s in self._rows if ts - t <= self.keep]

    def latest(self) -> list[TokenInfoBoard] | None:
        return self._rows[-1][1] if self._rows else None

    def at_least(self, age: timedelta, now: datetime) -> list[TokenInfoBoard] | None:
        """The newest snapshot at least ``age`` old, or ``None`` if there is none (caller must fail closed)."""
        old = [(t, s) for t, s in self._rows if now - t >= age]
        return old[-1][1] if old else None

    def burst(
        self, mint: str, now: datetime, max_delta: int = 15, age: timedelta = timedelta(hours=1)
    ) -> bool:
        """Crowd-burst check vs the snapshot ~``age`` old; ``True`` (exclude) when no such snapshot exists yet."""
        prev = self.at_least(age, now)
        cur = self.latest()
        if prev is None or cur is None:
            return True
        return crowd_burst(prev, cur, mint, max_delta)


# --------------------------------------------------------------------------- strategy


class CopyEntry(DomainModel):
    mint: str
    ts: datetime

    @field_validator("ts")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _to_utc(v)


class ConsensusCopyStrategy:
    """Live copy sleeve. ``targets`` is empty unless live AND promotion gate met AND SOL regime ON."""

    name: str = STRATEGY_NAME
    cadence: Literal["daily", "monitor"] = "monitor"

    def __init__(
        self,
        cfg: Any,
        tracker: ShadowTracker,
        gate: Callable[[str], bool],
        regime_on: Callable[[], bool],
        *,
        report_days: int = 60,
    ) -> None:
        self.cfg = cfg
        self.tracker = tracker
        self.gate = gate
        self.regime_on = regime_on
        self.report_days = report_days
        self._candidates: list[CopyCandidate] = []
        self._entries: list[CopyEntry] = []

    def propose(self, candidates: Iterable[CopyCandidate]) -> None:
        """Hand the engine's current consensus candidates to the next ``targets`` call (replaces the previous set)."""
        self._candidates = list(candidates)

    def entries_today(self, now: datetime) -> int:
        day = now.date()
        return sum(1 for e in self._entries if e.ts.date() == day)

    def enabled(self) -> tuple[bool, str]:
        """(True, 'ok') when live copy may emit entries; otherwise the first blocking reason."""
        if not bool(self.cfg.live):
            return False, "copy.live is false"
        if not bool(getattr(self.cfg, "shadow_enabled", True)):
            return False, "copy.shadow_enabled is false"
        rep = self.tracker.report(self.report_days)
        if not rep.promotion_gate_met:
            return False, "promotion gate not met: " + "; ".join(rep.reasons)
        if not self.regime_on():
            return False, "SOL regime off"
        return True, "ok"

    def targets(self, ctx: MarketContext) -> tuple[list[TargetExposure], SleeveState]:
        state = ctx.sleeve_state.get(self.name, SleeveState())
        ok, _reason = self.enabled()
        if not ok:
            return [], state
        held = [p for p in ctx.positions if p.strategy == self.name and p.amount_base > 0]
        if len(held) >= int(self.cfg.max_concurrent):
            return [], state
        if self.entries_today(ctx.now) >= int(self.cfg.max_entries_per_day):
            return [], state
        if ctx.equity_usd <= 0:
            return [], state
        used = sum((p.cost_usd for p in held), Decimal(0)) / ctx.equity_usd
        room = Decimal(str(self.cfg.sleeve_cap_pct)) - used
        weight = min(Decimal(str(self.cfg.per_signal_pct)), room)
        if weight <= 0:
            return [], state
        held_mints = {p.mint for p in held}
        for c in sorted(self._candidates, key=lambda c: (c.signal.first_ts, c.signal.mint)):
            if c.signal.mint in held_mints or not c.gate_ok or c.crowd_burst or not self.gate(c.signal.mint):
                continue
            if not late_entry_ok(
                c.current_price, c.signal.earliest_leader_price, Decimal(self.cfg.late_entry_max_above)
            ):
                continue
            rule = copy_exit_rule(c.current_price, ctx.now, self.cfg)
            self._entries.append(CopyEntry(mint=c.signal.mint, ts=ctx.now))
            self._candidates = [x for x in self._candidates if x.signal.mint != c.signal.mint]
            target = TargetExposure(
                mint=c.signal.mint,
                weight=weight,
                strategy=self.name,
                reason=f"consensus of {c.signal.cluster_count} clusters; entry <= {Decimal(self.cfg.late_entry_max_above):.0%} above first fill",
                exit=rule,
            )
            return [target], state  # one entry per tick (L4 loop guard)
        return [], state
