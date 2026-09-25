"""In-process, scriptable fakes for the execution and data layers (no network, no threads).

Reusable by WP-D/WP-E engine and copy tests. Everything is plain Python: construct, mutate the
public attributes between ticks, inspect the recorded calls.

    FakeRpc(balances={wallet: lamports}, token_accounts={owner: [TokenAccount]},
            accounts={pubkey: jsonParsed value}, statuses={sig: status|None},
            transactions={sig: getTransaction result}, signatures={addr: [rows]})
      .set_simulation(err=None, accounts={pubkey: post-state|None}, units=93000)
      .calls -> list[(method, args...)]
    FakeJupiter(prices={mint: Decimal usd}, decimals={mint: int}, impact, fee_bps, router,
                transaction_b64, execute_outcomes=['success'|'failed'|'network_error', ...],
                round_trip=Decimal, signature=str)
      .order_calls / .execute_calls / .probe_calls
    FakePriceSource(table) - PriceSource + KrakenTicker-like sol_usd_mid()
    FakeCandleSource(bars={symbol: [Candle]}, fail=False)
    FakeTokenData(infos={mint: TokenInfo|None}, shields={mint: [warnings]}|None,
                  mint_accounts={mint: value}, decimals={mint: int})
    CountingSigner(inner) - wraps a Signer and counts sign calls
    respx_router() - context manager yielding a started respx router; wrap it in a ``router``
             fixture (the conftest's no-network router is nested, so ``respx.get`` never matches).
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from decimal import ROUND_DOWN, Decimal
from typing import Any

import httpx
import respx

from tiller.execution.jupiter import ExecResult, JupiterError
from tiller.execution.rpc import SimResult
from tiller.execution.wallet import Signer
from tiller.models import (
    SOL_DECIMALS,
    SOL_MINT,
    TOKEN_2022_PROGRAM,
    TOKEN_PROGRAM,
    USDC_DECIMALS,
    USDC_MINT,
    Candle,
    Order,
    SwapRequest,
    TokenAccount,
    TokenInfo,
)


@contextmanager
def respx_router() -> Iterator[respx.MockRouter]:
    """Started respx router for HTTP mocks (assert_all_mocked=True, unused routes allowed).

    The conftest's no-network guard is a nested router, so routes registered on the global
    ``respx`` object never match; test modules wrap this in a ``router`` fixture instead.
    """
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as r:
        yield r


class FakeRpc:
    """Scriptable Rpc. ``accounts`` doubles as the getAccountInfo table (mints, anything)."""

    def __init__(
        self,
        *,
        balances: dict[str, int] | None = None,
        token_accounts: dict[str, list[TokenAccount]] | None = None,
        accounts: dict[str, dict[str, Any] | None] | None = None,
        statuses: dict[str, dict[str, Any] | None] | None = None,
        transactions: dict[str, dict[str, Any] | None] | None = None,
        signatures: dict[str, list[dict[str, Any]]] | None = None,
        error_rate: float = 0.0,
    ) -> None:
        self.balances = dict(balances or {})
        self.token_accounts = {k: list(v) for k, v in (token_accounts or {}).items()}
        self.accounts = dict(accounts or {})
        self.statuses = dict(statuses or {})
        self.transactions = dict(transactions or {})
        self.signatures = {k: list(v) for k, v in (signatures or {}).items()}
        self._error_rate = error_rate
        self.sim_err: Any | None = None
        self.sim_accounts: dict[str, dict[str, Any] | None] = {}
        self.sim_units: int | None = 93_000
        self.sim_logs: list[str] = []
        self.calls: list[tuple[Any, ...]] = []
        self.status_sequence: list[list[dict[str, Any] | None]] = []

    # scripting helpers -------------------------------------------------------------
    def set_simulation(
        self,
        *,
        err: Any | None = None,
        accounts: dict[str, dict[str, Any] | None] | None = None,
        units: int | None = 93_000,
        logs: Iterable[str] = (),
    ) -> FakeRpc:
        self.sim_err = err
        self.sim_accounts = dict(accounts or {})
        self.sim_units = units
        self.sim_logs = list(logs)
        return self

    def set_simulation_fixture(self, fixture: dict[str, Any]) -> FakeRpc:
        """Load a ``rpc/sim_*.json`` fixture (uses its ``_meta.addresses`` alignment)."""
        value = fixture["result"]["value"]
        addresses = fixture["_meta"]["addresses"]
        accounts = value.get("accounts") or []
        table = {addr: (accounts[i] if i < len(accounts) else None) for i, addr in enumerate(addresses)}
        return self.set_simulation(
            err=value.get("err"),
            accounts=table,
            units=value.get("unitsConsumed"),
            logs=value.get("logs") or [],
        )

    def wallet_account(
        self, lamports: int, owner: str = "11111111111111111111111111111111"
    ) -> dict[str, Any]:
        return {"lamports": lamports, "owner": owner, "data": ["", "base64"], "executable": False, "space": 0}

    @staticmethod
    def token_account_value(
        acc: TokenAccount,
        *,
        amount: int | None = None,
        owner: str | None = None,
        delegate: str | None = None,
        close_authority: str | None = None,
        state: str = "initialized",
    ) -> dict[str, Any]:
        """jsonParsed post-state for ``acc`` with optional mutations (for set_simulation)."""
        amt = acc.amount_base if amount is None else amount
        info: dict[str, Any] = {
            "isNative": acc.mint == SOL_MINT,
            "mint": acc.mint,
            "owner": owner or acc.owner,
            "state": state,
            "tokenAmount": {"amount": str(amt), "decimals": 0, "uiAmount": None, "uiAmountString": str(amt)},
        }
        if delegate:
            info["delegate"] = delegate
        if close_authority:
            info["closeAuthority"] = close_authority
        program = "spl-token-2022" if acc.program == TOKEN_2022_PROGRAM else "spl-token"
        return {
            "lamports": 2_039_280,
            "owner": acc.program,
            "data": {"program": program, "parsed": {"type": "account", "info": info}, "space": 165},
            "executable": False,
            "space": 165,
        }

    # Rpc protocol ------------------------------------------------------------------
    async def get_balance(self, pubkey: str) -> int:
        self.calls.append(("get_balance", pubkey))
        return int(self.balances.get(pubkey, 0))

    async def get_token_accounts_by_owner(self, owner: str) -> list[TokenAccount]:
        self.calls.append(("get_token_accounts_by_owner", owner))
        return list(self.token_accounts.get(owner, []))

    async def get_account_info(self, pubkey: str) -> dict[str, Any] | None:
        self.calls.append(("get_account_info", pubkey))
        return self.accounts.get(pubkey)

    async def simulate(self, tx_b64: str, accounts: list[str]) -> SimResult:
        self.calls.append(("simulate", tx_b64, list(accounts)))
        return SimResult(
            err=self.sim_err,
            logs=list(self.sim_logs),
            units_consumed=self.sim_units,
            accounts=[self.sim_accounts.get(a) for a in accounts],
            accounts_requested=list(accounts),
        )

    async def get_signature_statuses(self, sigs: list[str]) -> list[dict[str, Any] | None]:
        self.calls.append(("get_signature_statuses", list(sigs)))
        if self.status_sequence:
            return self.status_sequence.pop(0)
        return [self.statuses.get(s) for s in sigs]

    async def get_transaction(self, sig: str) -> dict[str, Any] | None:
        self.calls.append(("get_transaction", sig))
        return self.transactions.get(sig)

    async def get_signatures_for_address(
        self, addr: str, limit: int = 25, until: str | None = None
    ) -> list[dict[str, Any]]:
        self.calls.append(("get_signatures_for_address", addr, limit, until))
        rows = self.signatures.get(addr, [])
        if until is not None:
            out: list[dict[str, Any]] = []
            for r in rows:
                if r.get("signature") == until:
                    break
                out.append(r)
            rows = out
        return rows[:limit]

    def error_rate(self) -> float:
        return self._error_rate


class FakeJupiter:
    """Quote engine over a price table; execute outcomes are scripted in order."""

    def __init__(
        self,
        *,
        prices: dict[str, Decimal] | None = None,
        decimals: dict[str, int] | None = None,
        impact: Decimal = Decimal("0.001"),
        fee_bps: int = 2,
        router: str = "metis",
        transaction_b64: str = "",
        execute_outcomes: Iterable[str] = ("success",),
        round_trip: Decimal = Decimal("0.004"),
        signature: str | None = None,
        order_error: JupiterError | None = None,
    ) -> None:
        self.prices = {USDC_MINT: Decimal(1), **{k: Decimal(v) for k, v in (prices or {}).items()}}
        self.decimals = {SOL_MINT: SOL_DECIMALS, USDC_MINT: USDC_DECIMALS, **(decimals or {})}
        self.impact = Decimal(impact)
        self.fee_bps = fee_bps
        self.router = router
        self.transaction_b64 = transaction_b64
        self.execute_outcomes = list(execute_outcomes)
        self.round_trip = Decimal(round_trip)
        self.signature = signature
        self.order_error = order_error
        self.order_calls: list[tuple[SwapRequest, str, int]] = []
        self.execute_calls: list[tuple[str, str]] = []
        self.probe_calls: list[tuple[str, Decimal]] = []
        self._error_rate = 0.0

    def quote_out(self, req: SwapRequest) -> int:
        in_px = self.prices[req.input_mint]
        out_px = self.prices[req.output_mint]
        in_units = Decimal(req.amount_base) / Decimal(10 ** self.decimals[req.input_mint])
        out_units = in_units * in_px / out_px * (Decimal(1) - self.impact)
        return int((out_units * Decimal(10 ** self.decimals[req.output_mint])).to_integral_value(ROUND_DOWN))

    async def order(self, req: SwapRequest, taker: str, slippage_bps: int) -> Order:
        self.order_calls.append((req, taker, slippage_bps))
        if self.order_error is not None:
            raise self.order_error
        tx = self.transaction_b64 or base64.b64encode(b"\x00" * 8).decode()
        return Order.model_validate(
            {
                "transaction": tx,
                "requestId": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{len(self.order_calls)}")),
                "inAmount": req.amount_base,
                "outAmount": self.quote_out(req),
                "slippageBps": slippage_bps,
                "priceImpactPct": str(self.impact),
                "feeBps": self.fee_bps,
                "router": self.router,
                "raw": {"synthetic": True},
            }
        )

    async def execute(self, signed_tx_b64: str, request_id: str) -> ExecResult:
        self.execute_calls.append((signed_tx_b64, request_id))
        outcome = self.execute_outcomes.pop(0) if self.execute_outcomes else "success"
        if outcome == "network_error":
            raise httpx.ConnectError("synthetic connection reset")
        if outcome == "failed":
            return ExecResult(status="Failed", signature=self.signature, code=6001, error="slippage", raw={})
        return ExecResult(status="Success", signature=self.signature, code=0, error=None, raw={})

    async def round_trip_cost(
        self, mint: str, usd_notional: Decimal, taker: str, usdc_price: Decimal = Decimal(1)
    ) -> Decimal:
        self.probe_calls.append((mint, Decimal(usd_notional)))
        return self.round_trip

    def error_rate(self) -> float:
        return self._error_rate


class FakePriceSource:
    """PriceSource over a static table; also answers ``sol_usd_mid`` like a KrakenTicker."""

    def __init__(self, table: dict[str, Decimal], cex_mid: Decimal | None = None) -> None:
        self.table = {k: Decimal(v) for k, v in table.items()}
        self.cex_mid = cex_mid
        self.calls = 0

    async def usd_prices(self, mints: list[str]) -> dict[str, Decimal]:
        self.calls += 1
        return {m: self.table[m] for m in mints if m in self.table}

    async def sol_usd_mid(self) -> Decimal:
        if self.cex_mid is None:
            raise RuntimeError("cex mid unavailable")
        return self.cex_mid


FakePrices = FakePriceSource  # name used by the spec's tests/fakes.py contract


class FakeCandleSource:
    """CandleSource returning canned bars per symbol (raise when ``fail``)."""

    bundled = False

    def __init__(self, bars: dict[str, list[Candle]], fail: bool = False) -> None:
        self.bars = {k: list(v) for k, v in bars.items()}
        self.fail = fail
        self.calls = 0

    async def fetch_daily(self, symbol: str, limit: int) -> list[Candle]:
        self.calls += 1
        if self.fail:
            from tiller.data.candles import CandleSourceError

            raise CandleSourceError("fake source down")
        return self.bars.get(symbol, [])[-limit:]


class FakeTokenData:
    """TokenData stand-in with static tables (``shields=None`` means Shield unreachable)."""

    def __init__(
        self,
        *,
        infos: dict[str, TokenInfo | None] | None = None,
        shields: dict[str, list[str]] | None = None,
        mint_accounts: dict[str, dict[str, Any] | None] | None = None,
        decimals: dict[str, int] | None = None,
        programs: dict[str, str] | None = None,
    ) -> None:
        self.infos = dict(infos or {})
        self.shields = None if shields is None else dict(shields)
        self.mint_accounts = dict(mint_accounts or {})
        self.decimals_table = {SOL_MINT: SOL_DECIMALS, USDC_MINT: USDC_DECIMALS, **(decimals or {})}
        self.programs = {SOL_MINT: TOKEN_PROGRAM, USDC_MINT: TOKEN_PROGRAM, **(programs or {})}

    async def token_info(self, mint: str) -> TokenInfo | None:
        return self.infos.get(mint)

    async def shield(self, mints: list[str]) -> dict[str, list[str]] | None:
        if self.shields is None:
            return None
        return {m: list(self.shields.get(m, [])) for m in mints}

    async def mint_account(self, mint: str) -> dict[str, Any] | None:
        return self.mint_accounts.get(mint)

    async def decimals(self, mint: str) -> int:
        if mint not in self.decimals_table:
            raise ValueError(f"unknown decimals for {mint}")
        return self.decimals_table[mint]

    async def token_program(self, mint: str) -> str:
        return self.programs.get(mint, TOKEN_PROGRAM)


class CountingSigner:
    """Wraps a Signer and counts signing calls (paper tests assert zero)."""

    def __init__(self, inner: Signer) -> None:
        self.inner = inner
        self.message_signs = 0
        self.transaction_signs = 0

    @property
    def pubkey(self) -> str:
        return self.inner.pubkey

    def sign_message(self, msg: bytes) -> bytes:
        self.message_signs += 1
        return self.inner.sign_message(msg)

    def sign_transaction(self, tx_bytes: bytes) -> bytes:
        self.transaction_signs += 1
        return self.inner.sign_transaction(tx_bytes)
