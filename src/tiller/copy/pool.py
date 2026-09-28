"""Scored, sticky leader pool: the WP-E scoring pass wired to the engine's 6 h re-score hook.

``LeaderPool`` owns the pool the shadow tracker and the consensus rule use. It is rebuilt from
OUR logged leader trades only (``ledger.leader_trades``): eligibility, lag-adjusted replay,
sybil clustering and the sticky top-N selection all come from :mod:`tiller.copy.leaders`.
The mark store for the replay is built from prices we logged ourselves (leader fill prices
and shadow-trade marks), never from board P&L fields.

Until the first re-score qualifies at least one leader (eligibility needs >= 14 days of our
own logs) the pool is *provisional*: every followed key with its own cluster id. The shadow
book keeps collecting data in that phase, but ``copy.live`` stays false and nothing here has
order authority (this module imports neither the venue nor the signer).

State (pool, cluster ids, first-seen dates, last score) is persisted as a ``raw_snapshots``
row so a restart continues from the last pool.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from tiller.clock import Clock, ensure_utc
from tiller.copy.leaders import SeriesMarkStore, cluster, score_leaders, select_pool
from tiller.copy.models import LeaderScore, LeaderTrade
from tiller.ledger import Ledger
from tiller.models import AgentDetail, TokenInfo

SOURCE = "copy_pool"
KEY = "pool"
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
DetailsFn = Callable[[str], Awaitable[AgentDetail | None]]
TokenInfoFn = Callable[[str], Awaitable[TokenInfo | None]]


def marks_from_logs(trades: Iterable[LeaderTrade], shadow_rows: Iterable[Any]) -> SeriesMarkStore:
    """Mark store from what we logged: leader fill prices and shadow-trade entry/horizon/exit marks."""
    store = SeriesMarkStore()
    for t in trades:
        if t.price_usd is not None and t.price_usd > 0:
            store.add(t.mint, t.ts, Decimal(t.price_usd))
    horizons = {"1h": timedelta(hours=1), "6h": timedelta(hours=6), "24h": timedelta(hours=24)}
    for st in shadow_rows:
        try:
            store.add(st.mint, st.signal_ts, Decimal(st.leader_price))
            for key, delta in horizons.items():
                px = st.marks.get(key)
                if px is not None and px > 0:
                    store.add(st.mint, st.signal_ts + delta, Decimal(px))
            if st.exit_price is not None and st.exit_ts is not None:
                store.add(st.mint, st.exit_ts, Decimal(st.exit_price))
        except Exception:
            continue
    return store


class LeaderPool:
    """Sticky scored pool with persisted state; ``rescore`` is the 6 h pass."""

    def __init__(
        self,
        ledger: Ledger,
        cfg: Any,
        clock: Clock,
        *,
        details_fn: DetailsFn | None = None,
        token_info_fn: TokenInfoFn | None = None,
        lookback: timedelta = timedelta(days=90),
        max_token_lookups: int = 40,
    ) -> None:
        self.ledger = ledger
        self.cfg = cfg
        self.clock = clock
        self.details_fn = details_fn
        self.token_info_fn = token_info_fn
        self.lookback = lookback
        self.max_token_lookups = max_token_lookups
        self.pool: set[str] = set()
        self.clusters: dict[str, int] = {}
        self.scored_at: datetime | None = None
        self.scores: list[LeaderScore] = []
        self._token_meta: dict[str, TokenInfo | None] = {}
        self._load()

    # ------------------------------------------------------------------ persistence

    def _load(self) -> None:
        rows = self.ledger.raw_snapshots(source=SOURCE, limit=1)
        if not rows:
            return
        try:
            data = json.loads(rows[0]["json"])
            self.pool = set(data.get("pool") or [])
            self.clusters = {str(k): int(v) for k, v in (data.get("clusters") or {}).items()}
            at = data.get("scored_at")
            self.scored_at = ensure_utc(datetime.fromisoformat(at)) if at else None
            self.scores = [LeaderScore.model_validate(r) for r in data.get("scores") or []]
        except Exception:
            self.pool, self.clusters, self.scored_at, self.scores = set(), {}, None, []

    def _persist(self, now: datetime) -> None:
        payload = {
            "pool": sorted(self.pool),
            "clusters": dict(sorted(self.clusters.items())),
            "scored_at": now.isoformat(),
            "scores": [s.model_dump(mode="json") for s in self.scores],
        }
        self.ledger.add_raw_snapshot(now, SOURCE, KEY, payload)

    # ------------------------------------------------------------------ views

    @property
    def scored(self) -> bool:
        """True once a re-score produced a non-empty qualified pool."""
        return self.scored_at is not None and bool(self.pool)

    def effective(self, provisional_keys: Iterable[str]) -> tuple[set[str], dict[str, int], bool]:
        """(pool, cluster ids, provisional?) for the consensus rule and the shadow tracker."""
        if self.scored:
            return set(self.pool), dict(self.clusters), False
        keys = sorted(set(provisional_keys))
        return set(keys), {k: i for i, k in enumerate(keys)}, True

    # ------------------------------------------------------------------ scoring

    async def _token_meta_for(self, mints: Iterable[str]) -> dict[str, TokenInfo]:
        if self.token_info_fn is not None:
            todo = [m for m in dict.fromkeys(mints) if m not in self._token_meta][: self.max_token_lookups]
            for mint in todo:
                try:
                    self._token_meta[mint] = await self.token_info_fn(mint)
                except Exception:
                    self._token_meta[mint] = None
        return {m: info for m, info in self._token_meta.items() if info is not None}

    async def _details_for(self, keys: Iterable[str]) -> dict[str, AgentDetail | None]:
        out: dict[str, AgentDetail | None] = {}
        for key in keys:
            out[key] = None
            if self.details_fn is None or not key.startswith("fam:"):
                continue
            try:
                out[key] = await self.details_fn(key[4:])
            except Exception:
                out[key] = None
        return out

    async def rescore(self, now: datetime | None = None) -> set[str]:
        """Score every leader we have logged, cluster sybils, update the sticky pool and persist."""
        now = now or self.clock.now()
        trades = self.ledger.leader_trades(since=now - self.lookback)
        by_key: dict[str, list[LeaderTrade]] = {}
        first_seen: dict[str, datetime] = {}
        for t in trades:
            by_key.setdefault(t.key, []).append(t)
            seen = first_seen.get(t.key)
            first_seen[t.key] = t.detected_at if seen is None else min(seen, t.detected_at)
        if not by_key:
            self.scored_at = now
            self._persist(now)
            return set(self.pool)
        details = await self._details_for(by_key)
        token_meta = await self._token_meta_for(t.mint for t in trades if t.side == "buy")
        shadow_rows = self.ledger.shadow_trades(since=now - self.lookback)
        marks = marks_from_logs(trades, shadow_rows)
        open_marks = {m: px for m in marks.mints() if (px := marks.price_at(m, now)) is not None}
        scores = score_leaders(
            by_key, details, marks, token_meta, self.cfg, now, first_seen, open_marks=open_marks
        )
        scores = cluster(scores, by_key, float(self.cfg.cluster_overlap))
        self.scores = scores
        self.clusters = {s.key: s.cluster_id for s in scores}
        self.pool = select_pool(scores, self.pool, int(self.cfg.followed_n))
        self.scored_at = now
        self._persist(now)
        self.ledger.add_event(
            "info",
            "copy.rescored",
            {
                "leaders": len(scores),
                "qualified": sum(1 for s in scores if s.qualified),
                "pool": sorted(self.pool),
                "clusters": len(set(self.clusters.values())),
            },
        )
        return set(self.pool)
