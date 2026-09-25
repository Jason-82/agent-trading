"""Leader-trade feeds: familiars agent detail polling and on-chain wallet polling.

Both feeds emit :class:`~tiller.copy.models.LeaderTrade` rows and persist them through the
ledger (dedupe by signature). Nothing here has order authority: the module talks to the
board and to the RPC *read* surface only.

Conventions
-----------
* ``blockTime`` from ``getTransaction`` is unix **seconds**; every emitted ``ts`` is a
  tz-aware UTC datetime.
* Token amounts inside a transaction are base units (``uiTokenAmount.amount`` strings);
  ``LeaderTrade.amount`` is the *UI* amount (base units / 10**decimals) as ``Decimal``.
* ``usd_value`` is USD of the quote leg: stablecoins count 1:1, a SOL quote leg is valued
  at ``sol_usd`` (USD per SOL) which the caller must supply; without it a SOL-quoted swap
  is dropped (fail closed) rather than emitted with a made-up value.
* A familiars trade counts only after the chain confirms the signature; unconfirmed rows
  are dropped after ``verify_timeout`` (5 min by default).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from tiller.clock import Clock
from tiller.copy.models import LeaderTrade
from tiller.ledger import Ledger
from tiller.models import SOL_MINT, USDC_MINT, AgentDetail, AgentTrade

log = logging.getLogger("tiller.copy.feeds")

#: Stablecoins treated as the USD quote leg (USDC plus USDT).
DEFAULT_STABLE_MINTS: frozenset[str] = frozenset({USDC_MINT, "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"})

#: Lamport moves at or below this are treated as rent/fee noise, not a SOL leg (0.003 SOL).
SOL_DUST_LAMPORTS = 3_000_000

#: How long a familiars trade may stay unverified before it is dropped.
DEFAULT_VERIFY_TIMEOUT = timedelta(minutes=5)

LAMPORTS_PER_SOL_DEC = Decimal(1_000_000_000)


class _FeedRpc(Protocol):
    """The RPC read surface the feeds need (satisfied by ``HttpRpc`` and ``FakeRpc``)."""

    async def get_transaction(self, sig: str) -> dict[str, Any] | None: ...

    async def get_signatures_for_address(
        self, addr: str, limit: int = 25, until: str | None = None
    ) -> list[dict[str, Any]]: ...


class _FeedClient(Protocol):
    async def agent(self, handle: str) -> AgentDetail | None: ...


class _FeedPrices(Protocol):
    async def usd_prices(self, mints: list[str]) -> dict[str, Decimal]: ...


# --------------------------------------------------------------------------- pure parser


def _dec_amount(entry: Mapping[str, Any]) -> tuple[int, int]:
    ui = entry.get("uiTokenAmount") or {}
    return int(str(ui.get("amount", "0"))), int(ui.get("decimals", 0))


def _wallet_index(keys: list[Any], wallet: str) -> int | None:
    for i, k in enumerate(keys):
        pub = k.get("pubkey") if isinstance(k, dict) else k
        if pub == wallet:
            return i
    return None


def _is_signer(keys: list[Any], wallet: str) -> bool:
    for k in keys:
        if isinstance(k, dict) and k.get("pubkey") == wallet:
            return bool(k.get("signer"))
    return False


def token_deltas(tx: Mapping[str, Any], wallet: str) -> dict[str, tuple[int, int]]:
    """Net base-unit change per mint over the token accounts ``wallet`` owns: ``{mint: (delta, decimals)}``.

    Accounts absent pre-transaction start at 0; accounts absent post-transaction (closed, e.g. a
    wrapped-SOL ATA) end at 0. Mints whose net delta is zero are omitted.
    """
    meta = tx.get("meta") or {}
    pre: dict[int, tuple[str, int, int]] = {}
    post: dict[int, tuple[str, int, int]] = {}
    for entry in meta.get("preTokenBalances") or []:
        if entry.get("owner") == wallet:
            amt, dec = _dec_amount(entry)
            pre[int(entry["accountIndex"])] = (str(entry["mint"]), amt, dec)
    for entry in meta.get("postTokenBalances") or []:
        if entry.get("owner") == wallet:
            amt, dec = _dec_amount(entry)
            post[int(entry["accountIndex"])] = (str(entry["mint"]), amt, dec)
    deltas: dict[str, tuple[int, int]] = {}
    for idx in sorted(set(pre) | set(post)):
        before = pre.get(idx)
        after = post.get(idx)
        mint = (after or before)[0]  # type: ignore[index]  # at least one side exists
        dec = (after or before)[2]  # type: ignore[index]
        d = (after[1] if after else 0) - (before[1] if before else 0)
        if d == 0:
            continue
        prev, _ = deltas.get(mint, (0, dec))
        deltas[mint] = (prev + d, dec)
    return {m: v for m, v in deltas.items() if v[0] != 0}


def lamport_delta(tx: Mapping[str, Any], wallet: str) -> int:
    """Wallet lamport change net of the transaction fee (fee added back when the wallet paid it)."""
    keys = ((tx.get("transaction") or {}).get("message") or {}).get("accountKeys") or []
    idx = _wallet_index(keys, wallet)
    if idx is None:
        return 0
    meta = tx.get("meta") or {}
    pre = meta.get("preBalances") or []
    post = meta.get("postBalances") or []
    if idx >= len(pre) or idx >= len(post):
        return 0
    delta = int(post[idx]) - int(pre[idx])
    if idx == 0:
        delta += int(meta.get("fee") or 0)
    return delta


def parse_swap(
    tx: Mapping[str, Any],
    wallet: str,
    stable_mints: set[str] | frozenset[str],
    sol_mint: str = SOL_MINT,
    *,
    sol_usd: Decimal | None = None,
    key: str | None = None,
    detected_at: datetime | None = None,
) -> LeaderTrade | None:
    """Classify one ``getTransaction`` result (jsonParsed) as a leader buy/sell or ``None``.

    Rules (net balance deltas, so multi-hop routes resolve to their end legs):

    * failed transactions -> ``None``;
    * ``buy``: the quote leg (stablecoins and/or SOL) goes DOWN and exactly one other mint goes UP;
    * ``sell``: exactly one other mint goes DOWN and the quote leg goes UP;
    * plain transfers (no token delta), airdrops (token up, quote unchanged), token-for-token
      swaps, or several token mints moving at once -> ``None`` (ambiguous, fail closed);
    * a SOL quote leg needs ``sol_usd`` (USD per SOL) to value the trade, else ``None``.

    Wrapped-SOL token-account moves are folded into the wallet's lamport delta; lamport moves
    within :data:`SOL_DUST_LAMPORTS` are ignored (rent and fees).
    """
    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        return None
    deltas = token_deltas(tx, wallet)
    lamports = lamport_delta(tx, wallet)
    if sol_mint in deltas:
        lamports += deltas.pop(sol_mint)[0]
    if abs(lamports) <= SOL_DUST_LAMPORTS:
        lamports = 0

    stable_usd = Decimal(0)
    tokens: dict[str, tuple[int, int]] = {}
    for mint, (d, dec) in deltas.items():
        if mint in stable_mints:
            stable_usd += Decimal(d) / (Decimal(10) ** dec)
        else:
            tokens[mint] = (d, dec)
    if len(tokens) != 1:
        return None
    (mint, (tok_delta, tok_dec)) = next(iter(tokens.items()))

    sol_units = Decimal(lamports) / LAMPORTS_PER_SOL_DEC
    if sol_units != 0:
        if sol_usd is None or sol_usd <= 0:
            return None
        quote_usd = stable_usd + sol_units * sol_usd
    else:
        quote_usd = stable_usd
    if quote_usd == 0:
        return None
    if tok_delta > 0 and quote_usd < 0:
        side = "buy"
    elif tok_delta < 0 and quote_usd > 0:
        side = "sell"
    else:
        return None

    amount = Decimal(abs(tok_delta)) / (Decimal(10) ** tok_dec)
    usd_value = abs(quote_usd)
    block_time = tx.get("blockTime")
    if block_time is None:
        return None
    ts = datetime.fromtimestamp(int(block_time), tz=UTC)
    sigs = (tx.get("transaction") or {}).get("signatures") or []
    if not sigs:
        return None
    return LeaderTrade(
        key=key or f"wallet:{wallet}",
        wallet=wallet,
        signature=str(sigs[0]),
        ts=ts,
        detected_at=detected_at or ts,
        side=side,
        mint=mint,
        usd_value=usd_value,
        amount=amount,
        price_usd=(usd_value / amount) if amount > 0 else None,
        source="wallet",
        chain_verified=True,
    )


def chain_confirms(tx: Mapping[str, Any] | None, wallet: str, mint: str, side: str) -> tuple[bool, str]:
    """Cross-check a board-reported trade against its on-chain transaction.

    Confirmed when the transaction exists, succeeded, the leader wallet signed it and, where
    the balance deltas can be classified, the mint and side agree with the board. Returns
    ``(ok, reason)``; ``reason`` is ``'pending'`` when the transaction is not visible yet.
    """
    if tx is None:
        return False, "pending"
    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        return False, "failed_on_chain"
    keys = ((tx.get("transaction") or {}).get("message") or {}).get("accountKeys") or []
    if not _is_signer(keys, wallet):
        return False, "wallet_not_signer"
    deltas = token_deltas(tx, wallet)
    if mint not in deltas:
        if mint == SOL_MINT:
            return True, "ok"
        return False, "mint_not_in_tx"
    d = deltas[mint][0]
    if (side == "buy" and d <= 0) or (side == "sell" and d >= 0):
        return False, "side_mismatch"
    return True, "ok"


# --------------------------------------------------------------------------- feeds


def _agent_trade_to_leader(
    t: AgentTrade, handle: str, wallet: str, detected_at: datetime
) -> LeaderTrade | None:
    if t.kind not in ("buy", "sell"):
        return None
    if t.amount <= 0 or t.usd_value <= 0:
        return None
    return LeaderTrade(
        key=f"fam:{handle}",
        wallet=wallet,
        signature=t.signature,
        ts=t.time,
        detected_at=detected_at,
        side=t.kind,
        mint=t.token,
        usd_value=t.usd_value,
        amount=t.amount,
        price_usd=t.usd_value / t.amount,
        source="familiars",
        chain_verified=False,
    )


class FamiliarsFeed:
    """Poll followed handles' ``/api/agents/{handle}`` and emit chain-verified trades only."""

    def __init__(
        self,
        client: _FeedClient,
        rpc: _FeedRpc,
        ledger: Ledger,
        clock: Clock,
        *,
        verify_timeout: timedelta = DEFAULT_VERIFY_TIMEOUT,
        max_trade_age: timedelta = timedelta(days=30),
    ) -> None:
        self.client = client
        self.rpc = rpc
        self.ledger = ledger
        self.clock = clock
        self.verify_timeout = verify_timeout
        self.max_trade_age = max_trade_age
        self._known: set[str] = {
            t.signature for t in ledger.leader_trades(since=datetime(1970, 1, 1, tzinfo=UTC))
        }
        self._pending: dict[str, tuple[LeaderTrade, datetime]] = {}
        self._dropped: set[str] = set()

    @property
    def pending(self) -> dict[str, tuple[LeaderTrade, datetime]]:
        return dict(self._pending)

    async def poll(self, handles: Iterable[str]) -> list[LeaderTrade]:
        now = self.clock.now()
        for handle in handles:
            try:
                detail = await self.client.agent(handle)
            except Exception as exc:
                self.ledger.add_event(
                    "warn", "leader_feed_error", {"handle": handle, "error": str(exc)[:200]}
                )
                continue
            if detail is None:
                continue
            wallet = detail.agent.wallet
            for t in detail.trades:
                if t.signature in self._known or t.signature in self._pending or t.signature in self._dropped:
                    continue
                if now - t.time > self.max_trade_age:
                    continue
                lt = _agent_trade_to_leader(t, handle, wallet, now)
                if lt is not None:
                    self._pending[lt.signature] = (lt, now)
        return await self._verify(now)

    async def _verify(self, now: datetime) -> list[LeaderTrade]:
        emitted: list[LeaderTrade] = []
        for sig in list(self._pending):
            trade, first_seen = self._pending[sig]
            try:
                tx = await self.rpc.get_transaction(sig)
            except Exception as exc:
                self.ledger.add_event(
                    "warn", "leader_verify_error", {"signature": sig, "error": str(exc)[:200]}
                )
                tx = None
            ok, reason = chain_confirms(tx, trade.wallet, trade.mint, trade.side)
            if ok:
                verified = trade.model_copy(update={"chain_verified": True})
                self.ledger.upsert_leader_trades([verified])
                self._known.add(sig)
                del self._pending[sig]
                emitted.append(verified)
            elif reason != "pending" or now - first_seen >= self.verify_timeout:
                del self._pending[sig]
                self._dropped.add(sig)
                self.ledger.add_event(
                    "warn",
                    "leader_trade_dropped",
                    {
                        "signature": sig,
                        "key": trade.key,
                        "reason": reason if reason != "pending" else "unverified_timeout",
                    },
                )
        return emitted


class WalletFeed:
    """Poll user-curated wallets via ``getSignaturesForAddress`` + ``getTransaction``."""

    def __init__(
        self,
        rpc: _FeedRpc,
        ledger: Ledger,
        clock: Clock,
        *,
        prices: _FeedPrices | None = None,
        stable_mints: set[str] | frozenset[str] = DEFAULT_STABLE_MINTS,
        limit: int = 25,
    ) -> None:
        self.rpc = rpc
        self.ledger = ledger
        self.clock = clock
        self.prices = prices
        self.stable_mints = frozenset(stable_mints)
        self.limit = limit
        self._known: set[str] = {
            t.signature for t in ledger.leader_trades(since=datetime(1970, 1, 1, tzinfo=UTC))
        }
        self._last_seen: dict[str, str] = {}
        self._inspected: set[str] = set()

    async def _sol_usd(self) -> Decimal | None:
        if self.prices is None:
            return None
        try:
            return (await self.prices.usd_prices([SOL_MINT])).get(SOL_MINT)
        except Exception as exc:
            self.ledger.add_event("warn", "wallet_feed_price_error", {"error": str(exc)[:200]})
            return None

    async def poll(self, wallets: Iterable[str]) -> list[LeaderTrade]:
        now = self.clock.now()
        out: list[LeaderTrade] = []
        sol_usd: Decimal | None = None
        sol_usd_fetched = False
        for wallet in wallets:
            try:
                rows = await self.rpc.get_signatures_for_address(
                    wallet, self.limit, self._last_seen.get(wallet)
                )
            except Exception as exc:
                self.ledger.add_event(
                    "warn", "wallet_feed_error", {"wallet": wallet, "error": str(exc)[:200]}
                )
                continue
            if rows:
                self._last_seen[wallet] = str(rows[0].get("signature"))
            for row in rows:
                sig = str(row.get("signature") or "")
                if not sig or row.get("err") is not None or sig in self._known or sig in self._inspected:
                    continue
                self._inspected.add(sig)
                try:
                    tx = await self.rpc.get_transaction(sig)
                except Exception as exc:
                    self.ledger.add_event(
                        "warn", "wallet_feed_error", {"wallet": wallet, "error": str(exc)[:200]}
                    )
                    self._inspected.discard(sig)
                    continue
                if tx is None:
                    self._inspected.discard(sig)
                    continue
                if not sol_usd_fetched:
                    sol_usd = await self._sol_usd()
                    sol_usd_fetched = True
                trade = parse_swap(tx, wallet, self.stable_mints, SOL_MINT, sol_usd=sol_usd, detected_at=now)
                if trade is None:
                    continue
                if self.ledger.upsert_leader_trades([trade]) > 0:
                    self._known.add(sig)
                    out.append(trade)
        return out
