"""Leader eligibility, lag-adjusted replay scoring, clustering, sticky pool and demotion.

Everything here is pure and works from OUR logged :class:`LeaderTrade` rows. Board-provided
``pnl`` / ``winRate`` fields are never read: a leader's score is the rank of the P&L *we*
would have made copying them with a 60 s lag, worst-of pricing and modelled costs, exited by
our own rules.

Units: percentages are fractions (``Decimal('0.01')`` = 1%); prices and USD are ``Decimal``;
times are tz-aware UTC; ``lag_s`` is seconds.
"""

from __future__ import annotations

import bisect
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from statistics import median
from typing import Any, Protocol, runtime_checkable

from tiller.copy.models import LeaderScore, LeaderTrade
from tiller.models import AgentDetail, ExitRule, TokenInfo

ONE = Decimal(1)
ZERO = Decimal(0)
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)
PF_CAP = 999.0
SORTINO_CAP = 100.0


# --------------------------------------------------------------------------- marks


@runtime_checkable
class MarkStore(Protocol):
    """Historical USD marks per mint: the last known price at or before ``ts``."""

    def price_at(self, mint: str, ts: datetime) -> Decimal | None: ...

    def series_after(self, mint: str, ts: datetime) -> list[tuple[datetime, Decimal]]: ...


class SeriesMarkStore:
    """In-memory :class:`MarkStore` over ``{mint: [(ts, price), ...]}`` (sorted on construction)."""

    def __init__(self, series: Mapping[str, Iterable[tuple[datetime, Decimal]]] | None = None) -> None:
        self._ts: dict[str, list[datetime]] = {}
        self._px: dict[str, list[Decimal]] = {}
        for mint, rows in (series or {}).items():
            for ts, px in rows:
                self.add(mint, ts, px)

    def add(self, mint: str, ts: datetime, price: Decimal) -> None:
        ts_list = self._ts.setdefault(mint, [])
        px_list = self._px.setdefault(mint, [])
        i = bisect.bisect_right(ts_list, ts)
        ts_list.insert(i, ts)
        px_list.insert(i, Decimal(price))

    def price_at(self, mint: str, ts: datetime) -> Decimal | None:
        ts_list = self._ts.get(mint)
        if not ts_list:
            return None
        i = bisect.bisect_right(ts_list, ts) - 1
        return self._px[mint][i] if i >= 0 else None

    def series_after(self, mint: str, ts: datetime) -> list[tuple[datetime, Decimal]]:
        ts_list = self._ts.get(mint)
        if not ts_list:
            return []
        i = bisect.bisect_right(ts_list, ts)
        return list(zip(ts_list[i:], self._px[mint][i:], strict=True))

    def mints(self) -> set[str]:
        return set(self._ts)


# --------------------------------------------------------------------------- exit rules


def copy_exit_rule(entry_price: Decimal, now: datetime, cfg: Any) -> ExitRule:
    """Our exit rule for a copied position: stop -20%, trail 25% armed from +30%, time stop 48 h."""
    return ExitRule(
        stop_price=entry_price * (ONE - Decimal(cfg.stop_pct)),
        trail_pct=Decimal(cfg.trail_pct),
        trail_from_gain_pct=Decimal(cfg.trail_from_gain_pct),
        time_stop_at=now + timedelta(hours=float(cfg.time_stop_h)),
    )


def tighten_for_leader_sell(rule: ExitRule, tight_trail_pct: Decimal = Decimal("0.15")) -> ExitRule:
    """A leader sell never exits for us; it arms a tighter trail (15% from the running high) immediately."""
    trail = rule.trail_pct if rule.trail_pct is not None else tight_trail_pct
    return rule.model_copy(update={"trail_pct": min(trail, tight_trail_pct), "trail_from_gain_pct": ZERO})


def exit_reason(
    price: Decimal,
    high: Decimal,
    entry_price: Decimal,
    rule: ExitRule,
    now: datetime,
    *,
    liquidity_usd: Decimal | None = None,
    entry_liquidity_usd: Decimal | None = None,
    liquidity_collapse_pct: Decimal = Decimal("0.60"),
) -> str | None:
    """Which of our exit rules fires at ``price`` (``high`` = running high since entry), if any.

    Order: stop, trail, time stop, liquidity collapse. ``None`` means hold.
    """
    if rule.stop_price is not None and price <= rule.stop_price:
        return "stop"
    if rule.trail_pct is not None:
        armed_at = entry_price * (ONE + (rule.trail_from_gain_pct or ZERO))
        if high >= armed_at and price <= high * (ONE - rule.trail_pct):
            return "trail"
    if rule.time_stop_at is not None and now >= rule.time_stop_at:
        return "time_stop"
    if (
        liquidity_usd is not None
        and entry_liquidity_usd is not None
        and entry_liquidity_usd > 0
        and liquidity_usd <= entry_liquidity_usd * (ONE - liquidity_collapse_pct)
    ):
        return "liquidity_collapse"
    return None


# --------------------------------------------------------------------------- round trips


@dataclass(frozen=True)
class RoundTrip:
    """A buy matched (FIFO, by amount) to a later sell of the same mint. ``pnl_usd`` realised."""

    mint: str
    buy_ts: datetime
    sell_ts: datetime
    amount: Decimal
    buy_usd: Decimal
    sell_usd: Decimal

    @property
    def hold(self) -> timedelta:
        return self.sell_ts - self.buy_ts

    @property
    def pnl_usd(self) -> Decimal:
        return self.sell_usd - self.buy_usd


def round_trips(trades: Sequence[LeaderTrade]) -> tuple[list[RoundTrip], dict[str, tuple[Decimal, Decimal]]]:
    """FIFO-match buys to sells per mint. Returns (closed round trips, open remainder {mint: (amount, cost_usd)})."""
    lots: dict[str, list[tuple[datetime, Decimal, Decimal]]] = defaultdict(list)  # (ts, amount, usd)
    closed: list[RoundTrip] = []
    for t in sorted(trades, key=lambda x: (x.ts, x.signature)):
        if t.side == "buy":
            lots[t.mint].append((t.ts, t.amount, t.usd_value))
            continue
        remaining = t.amount
        usd_remaining = t.usd_value
        while remaining > 0 and lots[t.mint]:
            b_ts, b_amt, b_usd = lots[t.mint][0]
            take = min(remaining, b_amt)
            sell_usd = usd_remaining * (take / remaining) if remaining > 0 else ZERO
            buy_usd = b_usd * (take / b_amt) if b_amt > 0 else ZERO
            closed.append(RoundTrip(t.mint, b_ts, t.ts, take, buy_usd, sell_usd))
            remaining -= take
            usd_remaining -= sell_usd
            if take >= b_amt:
                lots[t.mint].pop(0)
            else:
                lots[t.mint][0] = (b_ts, b_amt - take, b_usd - buy_usd)
    open_lots: dict[str, tuple[Decimal, Decimal]] = {}
    for mint, rows in lots.items():
        if rows:
            open_lots[mint] = (sum((r[1] for r in rows), ZERO), sum((r[2] for r in rows), ZERO))
    return closed, open_lots


# --------------------------------------------------------------------------- eligibility


def platform_drawdown(detail: AgentDetail | None) -> float | None:
    """Max drawdown of deposit-adjusted equity over the board's history snapshots (fraction), or None."""
    if detail is None or not detail.history:
        return None
    pts = sorted(detail.history, key=lambda p: p.ts)
    base_dep = pts[0].net_deposits_usd
    peak: Decimal | None = None
    worst = ZERO
    for p in pts:
        adj = p.equity_usd - (p.net_deposits_usd - base_dep)
        if peak is None or adj > peak:
            peak = adj
        if peak and peak > 0:
            dd = (peak - adj) / peak
            worst = max(worst, dd)
    return float(worst)


def transfer_near_pnl_jump(
    detail: AgentDetail | None, window: timedelta = DAY, jump_frac: float = 0.20
) -> bool:
    """True when a deposit (net deposits rising) sits within ``window`` of a P&L jump > ``jump_frac`` of equity."""
    if detail is None or len(detail.history) < 2:
        return False
    pts = sorted(detail.history, key=lambda p: p.ts)
    deposits = [
        pts[i].ts for i in range(1, len(pts)) if pts[i].net_deposits_usd > pts[i - 1].net_deposits_usd
    ]
    if not deposits:
        return False
    for i in range(1, len(pts)):
        prev, cur = pts[i - 1], pts[i]
        if prev.equity_usd <= 0:
            continue
        if (cur.pnl_usd - prev.pnl_usd) / prev.equity_usd > Decimal(str(jump_frac)):
            if any(abs(cur.ts - d) <= window for d in deposits):
                return True
    return False


def _mint_age_ok(
    mint: str, at: datetime, token_meta: Mapping[str, TokenInfo], min_age: timedelta
) -> bool | None:
    info = token_meta.get(mint)
    if info is None or info.first_pool_created_at is None:
        return None
    return at - info.first_pool_created_at >= min_age


def eligible(
    trades: Sequence[LeaderTrade],
    detail: AgentDetail | None,
    token_meta: Mapping[str, TokenInfo],
    cfg: Any,
    now: datetime,
    first_seen: datetime,
    *,
    marks: Mapping[str, Decimal] | None = None,
    allowlist: Iterable[str] = (),
) -> tuple[bool, list[str]]:
    """Eligibility from OUR logs only. Returns ``(ok, reasons)``; every failing rule is listed.

    * logged for >= ``min_days_logged`` days (``now - first_seen``);
    * >= ``min_round_trips`` closed round trips and >= ``min_distinct_mints`` distinct mints bought;
    * median hold >= ``min_median_hold_h`` hours;
    * no single mint > ``max_single_mint_pnl_share`` of gross positive P&L (open lots marked at ``marks``);
    * platform snapshot drawdown < ``max_platform_dd`` (if ``detail`` has history);
    * zero buys of mints younger than 24 h at buy time (unknown age = fail closed, allowlisted mints exempt);
    * no mint whose ``dev`` equals the leader wallet (own token);
    * no deposit within 24 h of a P&L jump (if ``detail`` has history).
    """
    reasons: list[str] = []
    allow = set(allowlist)
    if now - first_seen < timedelta(days=float(cfg.min_days_logged)):
        reasons.append("insufficient_history")
    verified = [t for t in trades if t.chain_verified]
    closed, open_lots = round_trips(verified)
    if len(closed) < int(cfg.min_round_trips):
        reasons.append("too_few_round_trips")
    buys = [t for t in verified if t.side == "buy"]
    if len({t.mint for t in buys}) < int(cfg.min_distinct_mints):
        reasons.append("too_few_mints")
    if closed:
        med_hold = median(rt.hold.total_seconds() for rt in closed)
        if med_hold < float(cfg.min_median_hold_h) * 3600:
            reasons.append("median_hold_too_short")
    else:
        reasons.append("median_hold_too_short")
    pnl_by_mint: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for rt in closed:
        pnl_by_mint[rt.mint] += rt.pnl_usd
    if marks:
        for mint, (amt, cost) in open_lots.items():
            px = marks.get(mint)
            if px is not None:
                pnl_by_mint[mint] += amt * px - cost
    gross_pos = sum((v for v in pnl_by_mint.values() if v > 0), ZERO)
    if gross_pos > 0:
        top = max(pnl_by_mint.values())
        if top / gross_pos > Decimal(str(cfg.max_single_mint_pnl_share)):
            reasons.append("concentrated_pnl")
    dd = platform_drawdown(detail)
    if dd is not None and dd >= float(cfg.max_platform_dd):
        reasons.append("platform_drawdown")
    wallet = verified[0].wallet if verified else (detail.agent.wallet if detail else "")
    for t in buys:
        if t.mint in allow:
            continue
        age_ok = _mint_age_ok(t.mint, t.ts, token_meta, DAY)
        if age_ok is None:
            reasons.append("unknown_mint_age")
            break
        if not age_ok:
            reasons.append("young_mint_buy")
            break
    for t in verified:
        info = token_meta.get(t.mint)
        if info is not None and info.dev and wallet and info.dev == wallet:
            reasons.append("own_token")
            break
    if transfer_near_pnl_jump(detail):
        reasons.append("transfer_near_pnl_jump")
    return (not reasons, reasons)


# --------------------------------------------------------------------------- replay


@dataclass
class ReplayTrade:
    """One replayed copy of a leader buy (``ret`` net of modelled costs, as a fraction)."""

    signature: str
    mint: str
    entry_ts: datetime
    exit_ts: datetime
    entry_price: Decimal
    exit_price: Decimal
    ret: Decimal
    reason: str


@dataclass
class ReplayResult:
    n: int
    pf: float
    expectancy_pct: Decimal
    sortino: float
    weekly_positive_share: float
    trades: list[ReplayTrade] = field(default_factory=list)

    @property
    def returns(self) -> list[Decimal]:
        return [t.ret for t in self.trades]


def profit_factor(returns: Sequence[Decimal]) -> float:
    gains = sum((r for r in returns if r > 0), ZERO)
    losses = -sum((r for r in returns if r < 0), ZERO)
    if losses > 0:
        return min(float(gains / losses), PF_CAP)
    return PF_CAP if gains > 0 else 0.0


def sortino(returns: Sequence[Decimal]) -> float:
    if not returns:
        return 0.0
    rs = [float(r) for r in returns]
    mean = sum(rs) / len(rs)
    downside = math.sqrt(sum(min(r, 0.0) ** 2 for r in rs) / len(rs))
    if downside > 0:
        return max(min(mean / downside, SORTINO_CAP), -SORTINO_CAP)
    return SORTINO_CAP if mean > 0 else 0.0


def weekly_positive_share(trades: Sequence[ReplayTrade]) -> float:
    if not trades:
        return 0.0
    weeks: dict[tuple[int, int], Decimal] = defaultdict(lambda: ZERO)
    for t in trades:
        iso = t.exit_ts.isocalendar()
        weeks[(iso.year, iso.week)] += t.ret
    return sum(1 for v in weeks.values() if v > 0) / len(weeks)


def worst_of_entry(
    quote: Decimal | None, leader_price: Decimal | None, leader_markup: Decimal = Decimal("0.01")
) -> Decimal | None:
    """Entry basis = worst of (our quote at detection, leader price * 1.01); None if neither is known."""
    cands = [
        p
        for p in (quote, None if leader_price is None else leader_price * (ONE + leader_markup))
        if p is not None
    ]
    return max(cands) if cands else None


def replay_one(
    trade: LeaderTrade,
    marks: MarkStore,
    exit_rules: ExitRule,
    lag_s: int,
    slip: Decimal,
    fee: Decimal,
    leader_sells: Sequence[datetime] = (),
) -> ReplayTrade | None:
    """Replay a single leader BUY through our exit rules over the mark series.

    Entry at ``ts + lag_s`` at worst-of(quote, leader*1.01) * (1 + slip + fee); the series is walked
    forward applying stop / trail / time stop; leader sells of the same mint tighten the trail (never exit).
    A series ending before an exit is closed at its last mark (mark-to-market, reason ``'open'``).
    """
    entry_ts = trade.ts + timedelta(seconds=lag_s)
    quote = marks.price_at(trade.mint, entry_ts)
    basis = worst_of_entry(quote, trade.price_usd)
    if basis is None or basis <= 0:
        return None
    cost = slip + fee
    entry = basis * (ONE + cost)
    rule = exit_rules.model_copy(update={"stop_price": basis * (ONE - _stop_frac(exit_rules, basis))})
    if exit_rules.time_stop_at is not None:
        rule = rule.model_copy(
            update={"time_stop_at": entry_ts + (exit_rules.time_stop_at - _TEMPLATE_EPOCH)}
        )
    series = marks.series_after(trade.mint, entry_ts)
    if not series:
        return None
    high = basis
    sells = sorted(s for s in leader_sells if s > entry_ts)
    exit_px: Decimal | None = None
    exit_ts = series[-1][0]
    reason = "open"
    for ts, px in series:
        while sells and sells[0] <= ts:
            rule = tighten_for_leader_sell(rule)
            sells.pop(0)
        high = max(high, px)
        why = exit_reason(px, high, basis, rule, ts)
        if why is not None:
            exit_px, exit_ts, reason = px, ts, why
            break
    if exit_px is None:
        exit_px = series[-1][1]
    exit_net = exit_px * (ONE - cost)
    return ReplayTrade(
        trade.signature, trade.mint, entry_ts, exit_ts, entry, exit_net, exit_net / entry - ONE, reason
    )


def _stop_frac(rule: ExitRule, basis: Decimal) -> Decimal:
    """Recover the stop distance encoded in ``rule.stop_price`` relative to a unit basis (default 20%)."""
    if rule.stop_price is None:
        return Decimal("0.20")
    # exit_rules passed to replay() are a template built on entry_price=1, so stop_price is (1 - stop_pct)
    return ONE - rule.stop_price


#: Reference instant the replay exit template's ``time_stop_at`` is built from.
_TEMPLATE_EPOCH = datetime(2000, 1, 1, tzinfo=UTC)


def exit_template(cfg: Any) -> ExitRule:
    """Exit-rule template for :func:`replay`: stop/time-stop relative to a unit basis and a fixed epoch."""
    return ExitRule(
        stop_price=ONE - Decimal(cfg.stop_pct),
        trail_pct=Decimal(cfg.trail_pct),
        trail_from_gain_pct=Decimal(cfg.trail_from_gain_pct),
        time_stop_at=_TEMPLATE_EPOCH + timedelta(hours=float(cfg.time_stop_h)),
    )


def replay(
    trades: Sequence[LeaderTrade],
    marks: MarkStore,
    exit_rules: ExitRule,
    lag_s: int = 60,
    slip: Decimal = Decimal("0.01"),
    fee: Decimal = Decimal("0.001"),
) -> ReplayResult:
    """Lag-adjusted copied-P&L replay of every chain-verified leader buy (deterministic)."""
    sells_by_mint: dict[str, list[datetime]] = defaultdict(list)
    for t in trades:
        if t.side == "sell" and t.chain_verified:
            sells_by_mint[t.mint].append(t.ts)
    out: list[ReplayTrade] = []
    for t in sorted(trades, key=lambda x: (x.ts, x.signature)):
        if t.side != "buy" or not t.chain_verified:
            continue
        rt = replay_one(
            t, marks, exit_rules, lag_s, Decimal(slip), Decimal(fee), sells_by_mint.get(t.mint, ())
        )
        if rt is not None:
            out.append(rt)
    rets = [t.ret for t in out]
    expectancy = (sum(rets, ZERO) / len(rets)) if rets else ZERO
    return ReplayResult(
        len(out), profit_factor(rets), expectancy, sortino(rets), weekly_positive_share(out), out
    )


# --------------------------------------------------------------------------- scoring


def _ranks(values: Sequence[float]) -> list[float]:
    """Average ranks (1 = worst ... n = best) with ties averaged."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + 1 + j + 1) / 2
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def score_leaders(
    candidates: Mapping[str, Sequence[LeaderTrade]],
    details: Mapping[str, AgentDetail | None],
    marks: MarkStore,
    token_meta: Mapping[str, TokenInfo],
    cfg: Any,
    now: datetime,
    first_seen: Mapping[str, datetime],
    *,
    open_marks: Mapping[str, Decimal] | None = None,
    allowlist: Iterable[str] = (),
) -> list[LeaderScore]:
    """Eligibility + replay per leader, then score = mean rank(PF, Sortino, weekly-positive share).

    A leader qualifies only when eligible AND replay PF >= ``min_replay_pf`` on >= ``min_replay_signals``
    signals. Unqualified leaders get score 0. Board pnl/winRate are never read.
    """
    keys = sorted(candidates)
    rows: dict[str, tuple[bool, list[str], ReplayResult, str]] = {}
    for key in keys:
        trades = list(candidates[key])
        detail = details.get(key)
        wallet = trades[0].wallet if trades else (detail.agent.wallet if detail else "")
        seen = first_seen.get(key, now)
        ok, reasons = eligible(
            trades, detail, token_meta, cfg, now, seen, marks=open_marks, allowlist=allowlist
        )
        rep = replay(
            trades, marks, exit_template(cfg), int(cfg.replay_lag_s), cfg.replay_slip, cfg.replay_fee
        )
        if rep.n < int(cfg.min_replay_signals):
            ok, reasons = False, [*reasons, "too_few_replayed_signals"]
        elif rep.pf < float(cfg.min_replay_pf):
            ok, reasons = False, [*reasons, "replay_pf_below_min"]
        rows[key] = (ok, reasons, rep, wallet)
    qualified = [k for k in keys if rows[k][0]]
    pf_r = _ranks([rows[k][2].pf for k in qualified])
    so_r = _ranks([rows[k][2].sortino for k in qualified])
    wp_r = _ranks([rows[k][2].weekly_positive_share for k in qualified])
    score_of = {
        k: (pf_r[i] + so_r[i] + wp_r[i]) / 3 / max(len(qualified), 1) for i, k in enumerate(qualified)
    }
    out: list[LeaderScore] = []
    for i, key in enumerate(keys):
        ok, reasons, rep, wallet = rows[key]
        out.append(
            LeaderScore(
                key=key,
                wallet=wallet,
                qualified=ok,
                reasons=reasons,
                score=score_of.get(key, 0.0),
                cluster_id=i,
                replay_pf=rep.pf if rep.n else None,
                replay_expectancy_pct=rep.expectancy_pct if rep.n else None,
                n_replayed=rep.n,
            )
        )
    return out


# --------------------------------------------------------------------------- clustering


def mint_overlap(a: set[str], b: set[str]) -> float:
    """Share of the smaller mint set contained in the other (1.0 = one is a subset of the other)."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def cluster(
    scores: Sequence[LeaderScore],
    trades_by_key: Mapping[str, Sequence[LeaderTrade]],
    overlap: float = 0.8,
) -> list[LeaderScore]:
    """Sybil clustering: leaders whose bought-mint sets overlap by more than ``overlap`` share a cluster id.

    Transitive (union-find); the cluster id is the smallest member index in ``scores`` order.
    """
    keys = [s.key for s in scores]
    mints = {k: {t.mint for t in trades_by_key.get(k, ()) if t.side == "buy"} for k in keys}
    parent = list(range(len(keys)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            if mint_overlap(mints[keys[i]], mints[keys[j]]) > overlap:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[max(ri, rj)] = min(ri, rj)
    return [s.model_copy(update={"cluster_id": find(i)}) for i, s in enumerate(scores)]


# --------------------------------------------------------------------------- sticky pool


def select_pool(scores: Sequence[LeaderScore], prev_pool: set[str], n: int = 20) -> set[str]:
    """Sticky pool: enter in the top ``n`` by score, stay while ranked within ``1.5 n`` and still qualified."""
    ranked = sorted((s for s in scores if s.qualified), key=lambda s: (-s.score, s.key))
    rank_of = {s.key: i + 1 for i, s in enumerate(ranked)}
    keep_limit = math.floor(1.5 * n)
    pool = {s.key for s in ranked[:n]}
    for key in prev_pool:
        r = rank_of.get(key)
        if r is not None and r <= keep_limit:
            pool.add(key)
    return pool


# --------------------------------------------------------------------------- demotion


def should_demote(
    ls: LeaderScore,
    copied_pnl_4w: Decimal,
    recent: Sequence[LeaderTrade],
    token_meta: Mapping[str, TokenInfo],
    last_seen: datetime,
    now: datetime,
    *,
    platform_dd: float | None = None,
    max_platform_dd: float = 0.30,
    absent: timedelta = timedelta(hours=48),
) -> str | None:
    """First demotion reason that applies, or ``None``.

    Reasons: ``absent_48h``, ``negative_4w_copied_pnl``, ``young_mint_buy`` (< 24 h at buy time or unknown age),
    ``own_token`` (Tokens V2 dev == leader wallet), ``drawdown_over_max`` (platform snapshot DD > 30%).
    """
    if now - last_seen > absent:
        return "absent_48h"
    if copied_pnl_4w < 0:
        return "negative_4w_copied_pnl"
    for t in recent:
        if t.side != "buy":
            continue
        age_ok = _mint_age_ok(t.mint, t.ts, token_meta, DAY)
        if age_ok is None or not age_ok:
            return "young_mint_buy"
    for t in recent:
        info = token_meta.get(t.mint)
        if info is not None and info.dev and info.dev == ls.wallet:
            return "own_token"
    if platform_dd is not None and platform_dd > max_platform_dd:
        return "drawdown_over_max"
    return None
