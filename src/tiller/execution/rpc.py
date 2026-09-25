"""Minimal Solana JSON-RPC over httpx: primary/fallback endpoints, token bucket, typed results.

Only the handful of methods tiller needs are implemented (getBalance, getTokenAccountsByOwner,
getAccountInfo, simulateTransaction, getSignatureStatuses, getTransaction,
getSignaturesForAddress). Amounts are base units (lamports for SOL). Every RPC response the
guard relies on (simulation, transactions) can be persisted raw to the ledger when one is given.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from typing import Any, Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, ConfigDict, Field

from tiller.clock import Clock, SystemClock
from tiller.execution.http import MonotonicFn, RateLimitedHttp, SleepFn
from tiller.ledger import Ledger
from tiller.models import TOKEN_2022_PROGRAM, TOKEN_PROGRAM, TokenAccount

PROGRAM_NAMES = {"spl-token": TOKEN_PROGRAM, "spl-token-2022": TOKEN_2022_PROGRAM}


class RpcError(Exception):
    """A JSON-RPC level error (``{"error": {...}}``) returned by every endpoint."""

    def __init__(self, message: str, code: int | None = None, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


class RpcUnavailable(Exception):
    """Every configured endpoint failed at the transport level (timeout, 5xx, bad JSON)."""


class SimResult(BaseModel):
    """Parsed ``simulateTransaction`` result.

    ``accounts`` is aligned with ``accounts_requested`` (the ``accounts.addresses`` we asked
    for), each entry the post-simulation account (jsonParsed when the node could parse it)
    or ``None`` if it does not exist after the simulated transaction.
    """

    model_config = ConfigDict(extra="ignore")

    err: Any | None = None
    logs: list[str] = Field(default_factory=list)
    units_consumed: int | None = None
    accounts: list[dict[str, Any] | None] = Field(default_factory=list)
    accounts_requested: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class Rpc(Protocol):
    """The RPC surface the rest of tiller depends on (see :class:`HttpRpc`, ``tests.fakes.FakeRpc``)."""

    async def get_balance(self, pubkey: str) -> int: ...

    async def get_token_accounts_by_owner(self, owner: str) -> list[TokenAccount]: ...

    async def get_account_info(self, pubkey: str) -> dict[str, Any] | None: ...

    async def simulate(self, tx_b64: str, accounts: list[str]) -> SimResult: ...

    async def get_signature_statuses(self, sigs: list[str]) -> list[dict[str, Any] | None]: ...

    async def get_transaction(self, sig: str) -> dict[str, Any] | None: ...

    async def get_signatures_for_address(
        self, addr: str, limit: int = 25, until: str | None = None
    ) -> list[dict[str, Any]]: ...

    def error_rate(self) -> float: ...


def parse_token_account_value(pubkey: str, account: dict[str, Any]) -> TokenAccount | None:
    """Turn one jsonParsed ``getTokenAccountsByOwner`` / ``getAccountInfo`` value into a TokenAccount.

    Returns ``None`` if the value is not a parsed token account (unknown shape is not guessed).
    """
    data = account.get("data")
    if not isinstance(data, dict):
        return None
    parsed = data.get("parsed")
    if not isinstance(parsed, dict) or parsed.get("type") != "account":
        return None
    info = parsed.get("info") or {}
    amount = info.get("tokenAmount", {}).get("amount")
    if amount is None or "mint" not in info or "owner" not in info:
        return None
    program = data.get("program")
    program_id = PROGRAM_NAMES.get(str(program), str(account.get("owner") or program))
    return TokenAccount(
        pubkey=pubkey,
        mint=str(info["mint"]),
        amount_base=int(amount),
        owner=str(info["owner"]),
        delegate=info.get("delegate"),
        close_authority=info.get("closeAuthority"),
        program=program_id,
    )


class HttpRpc:
    """JSON-RPC client over httpx with endpoint failover and a shared token bucket."""

    def __init__(
        self,
        urls: list[str],
        rps: float,
        client: httpx.AsyncClient,
        ledger: Ledger | None = None,
        *,
        sleep: SleepFn = asyncio.sleep,
        monotonic: MonotonicFn = time.monotonic,
        rng: random.Random | None = None,
        timeout_s: float = 20.0,
        clock: Clock | None = None,
    ) -> None:
        if not urls:
            raise ValueError("at least one RPC url is required")
        self.urls = list(urls)
        self.ledger = ledger
        self.clock = clock or SystemClock()
        self._http = RateLimitedHttp(
            client, rps, sleep=sleep, monotonic=monotonic, rng=rng, timeout_s=timeout_s
        )
        self._id = 0

    # ------------------------------------------------------------------ transport

    async def call(self, method: str, params: list[Any]) -> Any:
        """Send one JSON-RPC call, trying each endpoint in order on transport/5xx failure."""
        self._id += 1
        body = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        last_error: Exception | None = None
        for url in self.urls:
            try:
                resp = await self._http.request("POST", url, json=body)
            except httpx.HTTPError as e:
                last_error = e
                continue
            if resp.status_code >= 500 or resp.status_code == 429:
                last_error = RpcUnavailable(f"{url}: HTTP {resp.status_code}")
                continue
            if resp.status_code >= 400:
                raise RpcError(f"{method}: HTTP {resp.status_code}", code=resp.status_code)
            try:
                payload = resp.json()
            except json.JSONDecodeError as e:
                last_error = e
                self._http.errors.mark_last(True)
                continue
            if not isinstance(payload, dict):
                last_error = RpcUnavailable(f"{url}: non-object response")
                continue
            if "error" in payload and payload["error"] is not None:
                err = payload["error"]
                self._http.errors.mark_last(True)
                if isinstance(err, dict):
                    raise RpcError(str(err.get("message")), code=err.get("code"), data=err.get("data"))
                raise RpcError(str(err))
            return payload.get("result")
        raise RpcUnavailable(f"all RPC endpoints failed for {method}: {last_error!r}")

    def error_rate(self) -> float:
        """Error share over the last ten HTTP calls (transport errors, 4xx/5xx, RPC errors)."""
        return self._http.error_rate()

    # ------------------------------------------------------------------ methods

    async def get_balance(self, pubkey: str) -> int:
        """Lamports held by ``pubkey`` at ``confirmed`` commitment."""
        result = await self.call("getBalance", [pubkey, {"commitment": "confirmed"}])
        return int(result["value"])

    async def get_token_accounts_by_owner(self, owner: str) -> list[TokenAccount]:
        """All token accounts of ``owner`` under both token programs (jsonParsed)."""
        out: list[TokenAccount] = []
        for program in (TOKEN_PROGRAM, TOKEN_2022_PROGRAM):
            result = await self.call(
                "getTokenAccountsByOwner",
                [owner, {"programId": program}, {"encoding": "jsonParsed", "commitment": "confirmed"}],
            )
            for entry in result.get("value") or []:
                acc = parse_token_account_value(str(entry.get("pubkey")), entry.get("account") or {})
                if acc is not None:
                    out.append(acc)
        return out

    async def get_account_info(self, pubkey: str) -> dict[str, Any] | None:
        """jsonParsed account value (``None`` when the account does not exist)."""
        result = await self.call(
            "getAccountInfo", [pubkey, {"encoding": "jsonParsed", "commitment": "confirmed"}]
        )
        value = result.get("value") if isinstance(result, dict) else None
        return value if isinstance(value, dict) else None

    async def simulate(self, tx_b64: str, accounts: list[str]) -> SimResult:
        """simulateTransaction with sigVerify=false, replaceRecentBlockhash=true, processed, jsonParsed accounts."""
        params = [
            tx_b64,
            {
                "sigVerify": False,
                "replaceRecentBlockhash": True,
                "commitment": "processed",
                "encoding": "base64",
                "accounts": {"encoding": "jsonParsed", "addresses": list(accounts)},
            },
        ]
        result = await self.call("simulateTransaction", params)
        value = result.get("value") if isinstance(result, dict) else None
        if not isinstance(value, dict):
            raise RpcError("simulateTransaction: malformed result")
        if self.ledger is not None:
            self.ledger.add_raw_snapshot(
                self.clock.now(), "rpc.simulate", accounts[0] if accounts else "", value
            )
        acc = value.get("accounts")
        acc_list: list[dict[str, Any] | None] = []
        if isinstance(acc, list):
            acc_list = [a if isinstance(a, dict) else None for a in acc]
        return SimResult(
            err=value.get("err"),
            logs=[str(x) for x in (value.get("logs") or [])],
            units_consumed=value.get("unitsConsumed"),
            accounts=acc_list,
            accounts_requested=list(accounts),
            raw=value,
        )

    async def get_signature_statuses(self, sigs: list[str]) -> list[dict[str, Any] | None]:
        result = await self.call("getSignatureStatuses", [list(sigs), {"searchTransactionHistory": True}])
        values = result.get("value") if isinstance(result, dict) else None
        if not isinstance(values, list):
            raise RpcError("getSignatureStatuses: malformed result")
        return [v if isinstance(v, dict) else None for v in values]

    async def get_transaction(self, sig: str) -> dict[str, Any] | None:
        result = await self.call(
            "getTransaction",
            [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0, "commitment": "confirmed"}],
        )
        if result is None:
            return None
        if not isinstance(result, dict):
            raise RpcError("getTransaction: malformed result")
        if self.ledger is not None:
            self.ledger.add_raw_snapshot(self.clock.now(), "rpc.transaction", sig, result)
        return result

    async def get_signatures_for_address(
        self, addr: str, limit: int = 25, until: str | None = None
    ) -> list[dict[str, Any]]:
        opts: dict[str, Any] = {"limit": int(limit), "commitment": "confirmed"}
        if until is not None:
            opts["until"] = until
        result = await self.call("getSignaturesForAddress", [addr, opts])
        if not isinstance(result, list):
            raise RpcError("getSignaturesForAddress: malformed result")
        return [r for r in result if isinstance(r, dict)]
