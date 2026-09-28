"""Scored sticky leader pool wired into the copy adapter (closes the WP-D copy gap).

* before any re-score the adapter uses a provisional pool (every followed key, one cluster each);
* ``LeaderPool.rescore`` runs eligibility + replay + clustering + sticky selection from OUR logged
  trades and persists the pool, so a restart continues from it;
* once scored, consensus is computed over the scored pool with sybil clusters merged (two leaders
  with > 80% mint overlap count once), and ``copy.live`` stays false regardless.
"""

from __future__ import annotations

import random
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from test_leaders import MINTS, T0, make_leader, meta_for, price_paths
from tiller.cli import _CopyFeedAdapter, _ShadowAdapter
from tiller.clock import SimClock
from tiller.copy.models import LeaderTrade
from tiller.copy.pool import LeaderPool
from tiller.ledger import Ledger
from wpd_helpers import TOKEN_X, make_cfg

NOW = T0 + timedelta(days=40)


class _Feed:
    def __init__(self, batches: list[list[LeaderTrade]]) -> None:
        self.batches = batches

    async def poll(self, keys: Any) -> list[LeaderTrade]:
        return self.batches.pop(0) if self.batches else []


class _Shadow:
    def __init__(self) -> None:
        self.calls: list[tuple[set[str], list[str]]] = []

    async def on_leader_trades(self, trades: Any, pool: set[str], consensus_mints: Any) -> list[Any]:
        self.calls.append((set(pool), list(consensus_mints)))
        return []


def _buy(key: str, mint: str, ts: Any, i: int) -> LeaderTrade:
    return LeaderTrade(
        key=key,
        wallet=f"wallet-{key}",
        signature=f"{key}-{mint[:4]}-{i}" + "x" * 60,
        ts=ts,
        detected_at=ts,
        side="buy",
        mint=mint,
        usd_value=Decimal(100),
        amount=Decimal(1000),
        price_usd=Decimal("0.1"),
        source="familiars",
        chain_verified=True,
    )


async def _seeded_pool(tmp_path: Path) -> tuple[Ledger, LeaderPool, SimClock]:
    """Ledger with three eligible leaders (a and b are sybils: identical mint sets) plus a thin one."""
    clock = SimClock(NOW)
    ledger = Ledger(tmp_path / "l.sqlite", clock=clock)
    rng = random.Random(7)
    marks = price_paths(rng, drift=0.004)
    streams = {
        "fam:a": make_leader(rng, "fam:a", "WalletA", marks),
        "fam:b": make_leader(rng, "fam:b", "WalletB", marks),  # same mints as a -> one cluster
        "fam:c": make_leader(rng, "fam:c", "WalletC", marks, mints=MINTS[4:14]),  # 80% overlap: not a sybil
        "fam:d": make_leader(rng, "fam:d", "WalletD", marks, n=5),  # too few signals
    }
    for rows in streams.values():
        ledger.upsert_leader_trades(rows)
    meta = meta_for(MINTS)

    async def token_info(mint: str) -> Any:
        return meta.get(mint)

    pool = LeaderPool(ledger, make_cfg().copy, clock, token_info_fn=token_info)
    return ledger, pool, clock


async def test_rescore_scores_clusters_and_persists(tmp_path: Path) -> None:
    ledger, pool, clock = await _seeded_pool(tmp_path)
    assert not pool.scored
    selected = await pool.rescore(NOW)
    assert pool.scored and selected == pool.pool
    assert "fam:d" not in selected, "a leader with too few replayed signals never enters the pool"
    assert pool.clusters["fam:a"] == pool.clusters["fam:b"], "sybils share a cluster id"
    assert pool.clusters["fam:c"] != pool.clusters["fam:a"]
    assert ledger.events(kind="copy.rescored")
    # a fresh instance (restart) reloads the persisted pool and clusters
    again = LeaderPool(ledger, make_cfg().copy, clock)
    assert again.pool == pool.pool and again.clusters == pool.clusters and again.scored
    ledger.close()


async def test_adapter_uses_scored_pool_and_merged_clusters(tmp_path: Path) -> None:
    ledger, pool, clock = await _seeded_pool(tmp_path)
    await pool.rescore(NOW)
    cfg = make_cfg()
    shadow = _Shadow()
    t = NOW + timedelta(hours=1)
    clock.set(t)
    # a and b are one cluster: with c they make only TWO clusters -> no consensus
    feed = _Feed(
        [
            [_buy("fam:a", TOKEN_X, t, 1)],
            [_buy("fam:b", TOKEN_X, t + timedelta(minutes=1), 2)],
            [_buy("fam:c", TOKEN_X, t + timedelta(minutes=2), 3)],
        ]
    )

    async def keys() -> list[str]:
        return ["a", "b", "c"]

    adapter = _CopyFeedAdapter(feed, keys, shadow, cfg, ledger=ledger, pool=pool)
    for i in range(3):
        await adapter.poll(t + timedelta(minutes=i, seconds=5))
    assert shadow.calls and all(TOKEN_X not in mints for _pool, mints in shadow.calls)
    assert all(p == pool.pool for p, _m in shadow.calls), "the scored pool is what the tracker sees"
    # an outsider key (not in the scored pool) cannot add a cluster either
    feed.batches.append([_buy("fam:zzz", TOKEN_X, t + timedelta(minutes=3), 4)])
    await adapter.poll(t + timedelta(minutes=3, seconds=5))
    assert TOKEN_X not in shadow.calls[-1][1]
    ledger.close()


async def test_adapter_provisional_pool_before_first_rescore(tmp_path: Path) -> None:
    clock = SimClock(NOW)
    ledger = Ledger(tmp_path / "l.sqlite", clock=clock)
    pool = LeaderPool(ledger, make_cfg().copy, clock)
    shadow = _Shadow()
    t = NOW
    feed = _Feed([[_buy(f"fam:l{i}", TOKEN_X, t + timedelta(minutes=i), i)] for i in range(3)])

    async def keys() -> list[str]:
        return ["l0", "l1", "l2"]

    adapter = _CopyFeedAdapter(feed, keys, shadow, make_cfg(), ledger=ledger, pool=pool)
    for i in range(3):
        await adapter.poll(t + timedelta(minutes=i, seconds=5))
    assert any(TOKEN_X in mints for _p, mints in shadow.calls), "provisional pool: 3 keys = 3 clusters"
    ledger.close()


async def test_shadow_adapter_rescore_hook_and_live_stays_false(tmp_path: Path) -> None:
    ledger, pool, _clock = await _seeded_pool(tmp_path)

    class _Tracker:
        async def mark_and_exit(self) -> None:
            return None

        def report(self, days: int = 60) -> str:
            return "report"

    adapter = _ShadowAdapter(_Tracker(), pool)
    await adapter.rescore(NOW)
    assert pool.scored and adapter.report() == "report"
    assert make_cfg().copy.live is False
    ledger.close()
