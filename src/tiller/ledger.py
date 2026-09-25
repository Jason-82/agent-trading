"""Append-only SQLite ledger (stdlib ``sqlite3``, WAL).

The ledger is the source of truth for orders, fills, positions, transfers (deposits and
withdrawals), equity snapshots, raw API snapshots, copy-module data, board posts, events
and the per-mint blocklist. USD values are stored as text and parsed back to ``Decimal``;
timestamps are stored as ISO-8601 UTC strings; base units as integers.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from tiller.clock import ensure_utc
from tiller.copy.models import LeaderTrade, ShadowTrade
from tiller.models import (
    USDC_MINT,
    DayStats,
    EquityPoint,
    ExitRule,
    Fill,
    Order,
    PendingPost,
    Position,
    SwapRequest,
)

STABLE_MINTS = frozenset({USDC_MINT})

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  req TEXT NOT NULL,
  order_json TEXT,
  state TEXT NOT NULL CHECK (state IN ('submitted','filled','failed')),
  signature TEXT,
  error TEXT,
  finalized_ts TEXT
);
CREATE INDEX IF NOT EXISTS orders_state ON orders(state);
CREATE TABLE IF NOT EXISTS fills (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  signature TEXT,
  in_mint TEXT NOT NULL, out_mint TEXT NOT NULL,
  in_base INTEGER NOT NULL, out_base INTEGER NOT NULL,
  usd_in TEXT NOT NULL, usd_out TEXT NOT NULL, fee_usd TEXT NOT NULL,
  paper INTEGER NOT NULL, strategy TEXT NOT NULL,
  quote_out_base INTEGER NOT NULL, round_trip_probe_pct TEXT,
  mode TEXT NOT NULL DEFAULT 'normal'
);
CREATE UNIQUE INDEX IF NOT EXISTS fills_sig ON fills(signature) WHERE signature IS NOT NULL;
CREATE INDEX IF NOT EXISTS fills_ts ON fills(ts);
CREATE TABLE IF NOT EXISTS positions (
  mint TEXT NOT NULL, strategy TEXT NOT NULL,
  amount_base INTEGER NOT NULL, cost_usd TEXT NOT NULL,
  opened_at TEXT NOT NULL, exit_json TEXT,
  PRIMARY KEY (mint, strategy)
);
CREATE TABLE IF NOT EXISTS transfers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, mint TEXT NOT NULL, amount_base INTEGER NOT NULL,
  usd TEXT NOT NULL, direction TEXT NOT NULL CHECK (direction IN ('in','out')),
  signature TEXT
);
CREATE TABLE IF NOT EXISTS equity_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, equity_usd TEXT NOT NULL, net_deposits_usd TEXT NOT NULL, source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS equity_ts ON equity_snapshots(ts);
CREATE TABLE IF NOT EXISTS raw_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, source TEXT NOT NULL, key TEXT NOT NULL, json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS raw_source ON raw_snapshots(source, ts);
CREATE TABLE IF NOT EXISTS leader_trades (
  signature TEXT PRIMARY KEY,
  key TEXT NOT NULL, wallet TEXT NOT NULL, ts TEXT NOT NULL, detected_at TEXT NOT NULL,
  side TEXT NOT NULL, mint TEXT NOT NULL, usd_value TEXT NOT NULL, amount TEXT NOT NULL,
  price_usd TEXT, source TEXT NOT NULL, chain_verified INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS leader_ts ON leader_trades(ts);
CREATE TABLE IF NOT EXISTS shadow_trades (
  id TEXT PRIMARY KEY, signal_ts TEXT NOT NULL, leader_key TEXT NOT NULL, mint TEXT NOT NULL, json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS posts (
  signature TEXT PRIMARY KEY,
  kind TEXT NOT NULL, text TEXT NOT NULL, mint TEXT,
  state TEXT NOT NULL CHECK (state IN ('queued','posted','uncertain','failed')),
  attempts INTEGER NOT NULL DEFAULT 0, not_before TEXT, updated_ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, level TEXT NOT NULL, kind TEXT NOT NULL, json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS blocklist (
  mint TEXT PRIMARY KEY, until TEXT NOT NULL, reason TEXT
);
CREATE TABLE IF NOT EXISTS day_stats (
  day TEXT PRIMARY KEY, start_equity TEXT NOT NULL
);
"""


def _iso(t: datetime) -> str:
    return ensure_utc(t).isoformat()


def _dt(s: str) -> datetime:
    return ensure_utc(datetime.fromisoformat(s))


def _dec(s: str | None) -> Decimal:
    return Decimal(s) if s not in (None, "") else Decimal(0)


def _json_default(o: Any) -> Any:
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, datetime):
        return _iso(o)
    if isinstance(o, Path):
        return str(o)
    raise TypeError(f"not JSON serialisable: {type(o).__name__}")


def dumps(obj: Any) -> str:
    return json.dumps(obj, default=_json_default, sort_keys=True, separators=(",", ":"))


class Ledger:
    """One SQLite file; every public method is a single transaction."""

    def __init__(self, path: Path, clock: Any | None = None) -> None:
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self.path), isolation_level=None, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript(SCHEMA)
        self._clock = clock

    def close(self) -> None:
        self._db.close()

    def _now(self) -> datetime:
        return self._clock.now() if self._clock is not None else datetime.now(tz=UTC)

    # ------------------------------------------------------------------ orders

    def record_order(self, req: SwapRequest, order: Order | None, signature: str | None) -> int:
        """Insert an order row with ``state='submitted'`` BEFORE the transaction is sent."""
        cur = self._db.execute(
            "INSERT INTO orders (ts, req, order_json, state, signature) VALUES (?,?,?,?,?)",
            (
                _iso(self._now()),
                req.model_dump_json(),
                None if order is None else order.model_dump_json(by_alias=True),
                "submitted",
                signature,
            ),
        )
        return int(cur.lastrowid or 0)

    def finalize_order(self, order_id: int, fill: Fill | None, error: str | None) -> None:
        """Mark filled (recording the fill) or failed; idempotent for an already finalized row."""
        row = self._db.execute("SELECT state FROM orders WHERE id=?", (order_id,)).fetchone()
        if row is None:
            raise KeyError(f"order {order_id} not found")
        if row["state"] != "submitted":
            return
        with self._db:
            self._db.execute("BEGIN")
            if fill is not None:
                self._record_fill(fill)
                self._db.execute(
                    "UPDATE orders SET state='filled', signature=COALESCE(?, signature), finalized_ts=? WHERE id=?",
                    (fill.signature, _iso(self._now()), order_id),
                )
            else:
                self._db.execute(
                    "UPDATE orders SET state='failed', error=?, finalized_ts=? WHERE id=?",
                    (error or "unknown", _iso(self._now()), order_id),
                )
            self._db.execute("COMMIT")

    def pending_signatures(self) -> list[tuple[int, str]]:
        rows = self._db.execute(
            "SELECT id, signature FROM orders WHERE state='submitted' AND signature IS NOT NULL ORDER BY id"
        ).fetchall()
        return [(int(r["id"]), str(r["signature"])) for r in rows]

    def orders(self, state: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM orders" + ("" if state is None else " WHERE state=?") + " ORDER BY id"
        rows = self._db.execute(q, () if state is None else (state,)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ fills / positions

    def record_fill(self, fill: Fill) -> None:
        """Store a fill (idempotent by signature) and update the positions table."""
        with self._db:
            self._db.execute("BEGIN")
            self._record_fill(fill)
            self._db.execute("COMMIT")

    def _record_fill(self, fill: Fill) -> None:
        if fill.signature is not None:
            dup = self._db.execute("SELECT 1 FROM fills WHERE signature=?", (fill.signature,)).fetchone()
            if dup is not None:
                return
        self._db.execute(
            "INSERT INTO fills (ts, signature, in_mint, out_mint, in_base, out_base, usd_in, usd_out, fee_usd, "
            "paper, strategy, quote_out_base, round_trip_probe_pct, mode) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                _iso(fill.ts),
                fill.signature,
                fill.in_mint,
                fill.out_mint,
                fill.in_base,
                fill.out_base,
                str(fill.usd_in),
                str(fill.usd_out),
                str(fill.fee_usd),
                int(fill.paper),
                fill.strategy,
                fill.quote_out_base,
                None if fill.round_trip_probe_pct is None else str(fill.round_trip_probe_pct),
                fill.mode,
            ),
        )
        if fill.out_mint not in STABLE_MINTS:
            self._position_add(fill.out_mint, fill.strategy, fill.out_base, fill.usd_in, fill.ts)
        if fill.in_mint not in STABLE_MINTS:
            self._position_sub(fill.in_mint, fill.strategy, fill.in_base)

    def _position_add(self, mint: str, strategy: str, base: int, usd: Decimal, ts: datetime) -> None:
        row = self._db.execute(
            "SELECT amount_base, cost_usd FROM positions WHERE mint=? AND strategy=?", (mint, strategy)
        ).fetchone()
        if row is None:
            self._db.execute(
                "INSERT INTO positions (mint, strategy, amount_base, cost_usd, opened_at) VALUES (?,?,?,?,?)",
                (mint, strategy, base, str(usd), _iso(ts)),
            )
        else:
            self._db.execute(
                "UPDATE positions SET amount_base=?, cost_usd=? WHERE mint=? AND strategy=?",
                (int(row["amount_base"]) + base, str(_dec(row["cost_usd"]) + usd), mint, strategy),
            )

    def _position_sub(self, mint: str, strategy: str, base: int) -> None:
        row = self._db.execute(
            "SELECT amount_base, cost_usd FROM positions WHERE mint=? AND strategy=?", (mint, strategy)
        ).fetchone()
        if row is None:
            return
        have = int(row["amount_base"])
        remaining = have - base
        if remaining <= 0:
            self._db.execute("DELETE FROM positions WHERE mint=? AND strategy=?", (mint, strategy))
            return
        cost = _dec(row["cost_usd"]) * Decimal(remaining) / Decimal(have)
        self._db.execute(
            "UPDATE positions SET amount_base=?, cost_usd=? WHERE mint=? AND strategy=?",
            (remaining, str(cost), mint, strategy),
        )

    def positions(self) -> list[Position]:
        rows = self._db.execute("SELECT * FROM positions ORDER BY opened_at, mint").fetchall()
        out: list[Position] = []
        for r in rows:
            exit_rule = ExitRule.model_validate_json(r["exit_json"]) if r["exit_json"] else None
            out.append(
                Position(
                    mint=r["mint"],
                    amount_base=int(r["amount_base"]),
                    cost_usd=_dec(r["cost_usd"]),
                    opened_at=_dt(r["opened_at"]),
                    strategy=r["strategy"],
                    exit=exit_rule,
                )
            )
        return out

    def set_position_exit(self, mint: str, strategy: str, exit_rule: ExitRule | None) -> None:
        self._db.execute(
            "UPDATE positions SET exit_json=? WHERE mint=? AND strategy=?",
            (None if exit_rule is None else exit_rule.model_dump_json(), mint, strategy),
        )

    def replace_positions(self, positions: Iterable[Position]) -> None:
        """Overwrite the positions table (reconcile ``--accept``)."""
        with self._db:
            self._db.execute("BEGIN")
            self._db.execute("DELETE FROM positions")
            for p in positions:
                self._db.execute(
                    "INSERT INTO positions (mint, strategy, amount_base, cost_usd, opened_at, exit_json) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        p.mint,
                        p.strategy,
                        p.amount_base,
                        str(p.cost_usd),
                        _iso(p.opened_at),
                        None if p.exit is None else p.exit.model_dump_json(),
                    ),
                )
            self._db.execute("COMMIT")

    def fills(self, since: datetime | None = None, until: datetime | None = None) -> list[Fill]:
        q = "SELECT * FROM fills"
        args: list[str] = []
        conds = []
        if since is not None:
            conds.append("ts >= ?")
            args.append(_iso(since))
        if until is not None:
            conds.append("ts < ?")
            args.append(_iso(until))
        if conds:
            q += " WHERE " + " AND ".join(conds)
        rows = self._db.execute(q + " ORDER BY ts, id", args).fetchall()
        return [self._row_to_fill(r) for r in rows]

    @staticmethod
    def _row_to_fill(r: sqlite3.Row) -> Fill:
        return Fill(
            signature=r["signature"],
            in_mint=r["in_mint"],
            out_mint=r["out_mint"],
            in_base=int(r["in_base"]),
            out_base=int(r["out_base"]),
            usd_in=_dec(r["usd_in"]),
            usd_out=_dec(r["usd_out"]),
            fee_usd=_dec(r["fee_usd"]),
            ts=_dt(r["ts"]),
            paper=bool(r["paper"]),
            strategy=r["strategy"],
            quote_out_base=int(r["quote_out_base"]),
            round_trip_probe_pct=None
            if r["round_trip_probe_pct"] is None
            else Decimal(r["round_trip_probe_pct"]),
            mode=r["mode"],
        )

    # ------------------------------------------------------------------ transfers / equity

    def record_transfer(
        self,
        ts: datetime,
        mint: str,
        amount_base: int,
        usd: Decimal,
        direction: Literal["in", "out"],
        signature: str | None = None,
    ) -> None:
        if signature is not None:
            dup = self._db.execute("SELECT 1 FROM transfers WHERE signature=?", (signature,)).fetchone()
            if dup is not None:
                return
        self._db.execute(
            "INSERT INTO transfers (ts, mint, amount_base, usd, direction, signature) VALUES (?,?,?,?,?,?)",
            (_iso(ts), mint, amount_base, str(usd), direction, signature),
        )

    def net_deposits_usd(self) -> Decimal:
        """Deposits minus withdrawals in USD at transfer time; fills never count."""
        rows = self._db.execute("SELECT usd, direction FROM transfers").fetchall()
        total = Decimal(0)
        for r in rows:
            total += _dec(r["usd"]) if r["direction"] == "in" else -_dec(r["usd"])
        return total

    def add_equity_snapshot(
        self, ts: datetime, equity_usd: Decimal, net_deposits_usd: Decimal, source: str
    ) -> None:
        self._db.execute(
            "INSERT INTO equity_snapshots (ts, equity_usd, net_deposits_usd, source) VALUES (?,?,?,?)",
            (_iso(ts), str(equity_usd), str(net_deposits_usd), source),
        )

    def equity_curve(
        self, days: int, source: str | None = None, now: datetime | None = None
    ) -> list[EquityPoint]:
        """Snapshots within the last ``days`` days (all sources unless ``source`` is given)."""
        since = (now or self._now()) - timedelta(days=days)
        q = "SELECT * FROM equity_snapshots WHERE ts >= ?"
        args: list[str] = [_iso(since)]
        if source is not None:
            q += " AND source=?"
            args.append(source)
        rows = self._db.execute(q + " ORDER BY ts, id", args).fetchall()
        return [
            EquityPoint(
                ts=_dt(r["ts"]),
                equity_usd=_dec(r["equity_usd"]),
                net_deposits_usd=_dec(r["net_deposits_usd"]),
                pnl_usd=_dec(r["equity_usd"]) - _dec(r["net_deposits_usd"]),
            )
            for r in rows
        ]

    # ------------------------------------------------------------------ raw snapshots / events

    def add_raw_snapshot(
        self, ts: datetime, source: str, key: str, payload: dict[str, Any] | list[Any] | str
    ) -> None:
        """Persist a response verbatim (a str is stored as-is, anything else JSON-encoded)."""
        text = payload if isinstance(payload, str) else json.dumps(payload, separators=(",", ":"))
        self._db.execute(
            "INSERT INTO raw_snapshots (ts, source, key, json) VALUES (?,?,?,?)",
            (_iso(ts), source, key, text),
        )

    def raw_snapshots(self, source: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        q = (
            "SELECT * FROM raw_snapshots"
            + ("" if source is None else " WHERE source=?")
            + " ORDER BY id DESC LIMIT ?"
        )
        args: tuple[Any, ...] = (limit,) if source is None else (source, limit)
        return [dict(r) for r in self._db.execute(q, args).fetchall()]

    def add_event(self, level: str, kind: str, payload: dict[str, Any] | None = None) -> None:
        self._db.execute(
            "INSERT INTO events (ts, level, kind, json) VALUES (?,?,?,?)",
            (_iso(self._now()), level, kind, dumps(payload or {})),
        )

    def events(self, limit: int = 100, kind: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM events" + ("" if kind is None else " WHERE kind=?") + " ORDER BY id DESC LIMIT ?"
        args: tuple[Any, ...] = (limit,) if kind is None else (kind, limit)
        out = []
        for r in self._db.execute(q, args).fetchall():
            d = dict(r)
            d["payload"] = json.loads(d.pop("json"))
            out.append(d)
        return out

    # ------------------------------------------------------------------ day stats

    def swaps_today(self, day_start: datetime, exclude_emergency: bool = True) -> int:
        """Fills with ``day_start <= ts < day_start + 24h`` (emergency exits exempt by default)."""
        q = "SELECT COUNT(*) AS n FROM fills WHERE ts >= ? AND ts < ?"
        if exclude_emergency:
            q += " AND mode != 'emergency'"
        row = self._db.execute(q, (_iso(day_start), _iso(day_start + timedelta(days=1)))).fetchone()
        return int(row["n"])

    def set_day_start_equity(self, day_start: datetime, equity_usd: Decimal) -> None:
        """Record the first equity of a UTC day (only the first call per day sticks)."""
        self._db.execute(
            "INSERT OR IGNORE INTO day_stats (day, start_equity) VALUES (?,?)",
            (ensure_utc(day_start).date().isoformat(), str(equity_usd)),
        )

    def day_stats(self, day_start: datetime) -> DayStats:
        """Start equity (day_stats row, else the first snapshot of the day, else the last before it)."""
        day = ensure_utc(day_start).date().isoformat()
        end = day_start + timedelta(days=1)
        row = self._db.execute("SELECT start_equity FROM day_stats WHERE day=?", (day,)).fetchone()
        if row is not None:
            start = _dec(row["start_equity"])
        else:
            snap = self._db.execute(
                "SELECT equity_usd FROM equity_snapshots WHERE ts >= ? AND ts < ? ORDER BY ts, id LIMIT 1",
                (_iso(day_start), _iso(end)),
            ).fetchone()
            if snap is None:
                snap = self._db.execute(
                    "SELECT equity_usd FROM equity_snapshots WHERE ts < ? ORDER BY ts DESC, id DESC LIMIT 1",
                    (_iso(day_start),),
                ).fetchone()
            start = _dec(snap["equity_usd"]) if snap is not None else Decimal(0)
        fills = self._db.execute(
            "SELECT out_mint, in_mint, usd_in, mode FROM fills WHERE ts >= ? AND ts < ?",
            (_iso(day_start), _iso(end)),
        ).fetchall()
        buys = Decimal(0)
        entries = 0
        swaps = 0
        for f in fills:
            if f["mode"] != "emergency":
                swaps += 1
            if f["out_mint"] not in STABLE_MINTS:
                buys += _dec(f["usd_in"])
                entries += 1
        return DayStats(start_equity=start, buys_usd=buys, swaps=swaps, entries=entries)

    # ------------------------------------------------------------------ blocklist

    def blocklist_add(self, mint: str, until: datetime, reason: str | None = None) -> None:
        self._db.execute(
            "INSERT INTO blocklist (mint, until, reason) VALUES (?,?,?) "
            "ON CONFLICT(mint) DO UPDATE SET until=excluded.until, reason=excluded.reason",
            (mint, _iso(until), reason),
        )

    def blocklist_active(self, now: datetime) -> set[str]:
        rows = self._db.execute("SELECT mint FROM blocklist WHERE until > ?", (_iso(now),)).fetchall()
        return {str(r["mint"]) for r in rows}

    # ------------------------------------------------------------------ copy module

    def upsert_leader_trades(self, trades: list[LeaderTrade]) -> int:
        """Insert new trades (dedupe by signature); returns the number actually inserted."""
        inserted = 0
        with self._db:
            self._db.execute("BEGIN")
            for t in trades:
                cur = self._db.execute(
                    "INSERT OR IGNORE INTO leader_trades (signature, key, wallet, ts, detected_at, side, mint, "
                    "usd_value, amount, price_usd, source, chain_verified) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        t.signature,
                        t.key,
                        t.wallet,
                        _iso(t.ts),
                        _iso(t.detected_at),
                        t.side,
                        t.mint,
                        str(t.usd_value),
                        str(t.amount),
                        None if t.price_usd is None else str(t.price_usd),
                        t.source,
                        int(t.chain_verified),
                    ),
                )
                inserted += cur.rowcount if cur.rowcount > 0 else 0
                if cur.rowcount == 0 and t.chain_verified:
                    self._db.execute(
                        "UPDATE leader_trades SET chain_verified=1 WHERE signature=?", (t.signature,)
                    )
            self._db.execute("COMMIT")
        return inserted

    def leader_trades(self, since: datetime, keys: list[str] | None = None) -> list[LeaderTrade]:
        q = "SELECT * FROM leader_trades WHERE ts >= ?"
        args: list[Any] = [_iso(since)]
        if keys:
            q += " AND key IN (" + ",".join("?" * len(keys)) + ")"
            args.extend(keys)
        rows = self._db.execute(q + " ORDER BY ts, signature", args).fetchall()
        return [
            LeaderTrade(
                key=r["key"],
                wallet=r["wallet"],
                signature=r["signature"],
                ts=_dt(r["ts"]),
                detected_at=_dt(r["detected_at"]),
                side=r["side"],
                mint=r["mint"],
                usd_value=_dec(r["usd_value"]),
                amount=_dec(r["amount"]),
                price_usd=None if r["price_usd"] is None else Decimal(r["price_usd"]),
                source=r["source"],
                chain_verified=bool(r["chain_verified"]),
            )
            for r in rows
        ]

    def upsert_shadow_trade(self, t: ShadowTrade) -> None:
        self._db.execute(
            "INSERT INTO shadow_trades (id, signal_ts, leader_key, mint, json) VALUES (?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET json=excluded.json",
            (t.id, _iso(t.signal_ts), t.leader_key, t.mint, t.model_dump_json()),
        )

    def shadow_trades(self, since: datetime) -> list[ShadowTrade]:
        rows = self._db.execute(
            "SELECT json FROM shadow_trades WHERE signal_ts >= ? ORDER BY signal_ts, id", (_iso(since),)
        ).fetchall()
        return [ShadowTrade.model_validate_json(r["json"]) for r in rows]

    # ------------------------------------------------------------------ posts

    def post_upsert(
        self,
        signature: str,
        kind: Literal["note", "callout", "trade"],
        text: str,
        state: Literal["queued", "posted", "uncertain", "failed"],
        attempts: int,
        mint: str | None = None,
        not_before: datetime | None = None,
    ) -> None:
        self._db.execute(
            "INSERT INTO posts (signature, kind, text, mint, state, attempts, not_before, updated_ts) "
            "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(signature) DO UPDATE SET kind=excluded.kind, text=excluded.text, "
            "mint=excluded.mint, state=excluded.state, attempts=excluded.attempts, not_before=excluded.not_before, "
            "updated_ts=excluded.updated_ts",
            (
                signature,
                kind,
                text,
                mint,
                state,
                attempts,
                None if not_before is None else _iso(not_before),
                _iso(self._now()),
            ),
        )

    def posts_pending(self, now: datetime) -> list[PendingPost]:
        """Queued/uncertain posts whose ``not_before`` has passed."""
        rows = self._db.execute(
            "SELECT * FROM posts WHERE state IN ('queued','uncertain') AND (not_before IS NULL OR not_before <= ?) "
            "ORDER BY updated_ts, signature",
            (_iso(now),),
        ).fetchall()
        return [self._row_to_post(r) for r in rows]

    def post(self, signature: str) -> PendingPost | None:
        r = self._db.execute("SELECT * FROM posts WHERE signature=?", (signature,)).fetchone()
        return None if r is None else self._row_to_post(r)

    def posts_since(self, since: datetime, kind: str | None = None) -> list[PendingPost]:
        q = "SELECT * FROM posts WHERE updated_ts >= ?"
        args: list[Any] = [_iso(since)]
        if kind is not None:
            q += " AND kind=?"
            args.append(kind)
        rows = self._db.execute(q + " ORDER BY updated_ts", args).fetchall()
        return [self._row_to_post(r) for r in rows]

    @staticmethod
    def _row_to_post(r: sqlite3.Row) -> PendingPost:
        return PendingPost(
            signature=r["signature"],
            kind=r["kind"],
            text=r["text"],
            mint=r["mint"],
            state=r["state"],
            attempts=int(r["attempts"]),
            not_before=None if r["not_before"] is None else _dt(r["not_before"]),
        )

    # ------------------------------------------------------------------ tax export

    TAX_COLUMNS = (
        "ts",
        "signature",
        "paper",
        "strategy",
        "sold_mint",
        "sold_base",
        "sold_usd_fair_value",
        "bought_mint",
        "bought_base",
        "bought_usd_fair_value",
        "fee_usd",
        "mode",
    )

    def export_tax_csv(self, path: Path) -> int:
        """Write every fill with the USD fair value of both legs; returns the row count."""
        fills = self.fills()
        with Path(path).open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(self.TAX_COLUMNS)
            for x in fills:
                w.writerow(
                    [
                        x.ts.isoformat(),
                        x.signature or "",
                        int(x.paper),
                        x.strategy,
                        x.in_mint,
                        x.in_base,
                        str(x.usd_in),
                        x.out_mint,
                        x.out_base,
                        str(x.usd_out),
                        str(x.fee_usd),
                        x.mode,
                    ]
                )
        return len(fills)
