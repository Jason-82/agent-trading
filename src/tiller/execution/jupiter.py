"""Jupiter Swap V2 client: ``GET /swap/v2/order``, ``POST /swap/v2/execute``, round-trip probe.

Conventions
-----------
* ``slippageBps`` is ALWAYS sent explicitly (RTSE / auto-slippage is never used).
* Amounts are base units (lamports, 1e-6 USDC, ...); ``priceImpactPct`` is a fraction as
  Jupiter returns it (``"0.0012"`` = 0.12%); ``feeBps`` is basis points.
* Every raw ``/order`` and ``/execute`` response is persisted to the ledger before parsing.
* Any non-2xx or an error body raises :class:`JupiterError` with the upstream ``code``
  (0 when Jupiter did not supply one); transport errors propagate as ``httpx.HTTPError`` so
  the venue can resubmit the SAME signed bytes.
"""

from __future__ import annotations

import asyncio
import random
import time
from decimal import ROUND_DOWN, Decimal
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from tiller.clock import Clock, SystemClock
from tiller.execution.http import MonotonicFn, RateLimitedHttp, SleepFn
from tiller.ledger import Ledger
from tiller.models import USDC_DECIMALS, USDC_MINT, Order, SwapRequest


class JupiterError(Exception):
    """Typed Jupiter API error. ``code`` is Jupiter's numeric code (0 if none), ``status`` the HTTP status."""

    def __init__(self, message: str, code: int = 0, status: int | None = None, raw: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.raw = raw


class ExecResult(BaseModel):
    """Parsed ``/swap/v2/execute`` response."""

    model_config = ConfigDict(extra="ignore")

    status: Literal["Success", "Failed"]
    signature: str | None = None
    code: int = 0
    error: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class JupiterSwapV2:
    """Typed Swap V2 client over a rate-limited httpx client."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        rps: float,
        client: httpx.AsyncClient,
        ledger: Ledger | None = None,
        *,
        clock: Clock | None = None,
        sleep: SleepFn = asyncio.sleep,
        monotonic: MonotonicFn = time.monotonic,
        rng: random.Random | None = None,
        timeout_s: float = 20.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key or None
        self.ledger = ledger
        self.clock = clock or SystemClock()
        self._http = RateLimitedHttp(
            client, rps, sleep=sleep, monotonic=monotonic, rng=rng, timeout_s=timeout_s
        )

    # ------------------------------------------------------------------ helpers

    def _headers(self) -> dict[str, str]:
        h = {"accept": "application/json"}
        if self._api_key:
            h["x-api-key"] = self._api_key
        return h

    def _persist(self, source: str, key: str, resp: httpx.Response) -> None:
        if self.ledger is None:
            return
        self.ledger.add_raw_snapshot(self.clock.now(), source, key, resp.text)

    @staticmethod
    def _raise_for_error(resp: httpx.Response, payload: Any, what: str) -> None:
        if isinstance(payload, dict) and payload.get("error"):
            code = payload.get("code")
            raise JupiterError(
                f"{what}: {payload.get('error')}",
                code=int(code) if isinstance(code, int) else 0,
                status=resp.status_code,
                raw=payload,
            )
        if resp.status_code >= 400:
            raise JupiterError(
                f"{what}: HTTP {resp.status_code}", code=0, status=resp.status_code, raw=payload
            )

    def error_rate(self) -> float:
        """Error share over the last ten HTTP calls."""
        return self._http.error_rate()

    # ------------------------------------------------------------------ API

    async def order(self, req: SwapRequest, taker: str, slippage_bps: int) -> Order:
        """Fetch an unsigned swap transaction for ``req``.

        ``taker`` is our wallet pubkey; ``slippage_bps`` is sent explicitly. Raises
        :class:`JupiterError` on any error body (Jupiter codes 1..3 = routing / amount /
        invalid request) and ``httpx.HTTPError`` on transport failure.
        """
        if slippage_bps <= 0:
            raise ValueError("slippage_bps must be explicit and positive")
        params = {
            "inputMint": req.input_mint,
            "outputMint": req.output_mint,
            "amount": str(req.amount_base),
            "taker": taker,
            "slippageBps": str(int(slippage_bps)),
        }
        resp = await self._http.request(
            "GET", f"{self.base_url}/swap/v2/order", params=params, headers=self._headers()
        )
        self._persist("jupiter.order", f"{req.input_mint}>{req.output_mint}:{req.amount_base}", resp)
        payload = _json_or_none(resp)
        self._raise_for_error(resp, payload, "order")
        if not isinstance(payload, dict):
            raise JupiterError("order: non-object response", status=resp.status_code, raw=payload)
        if not payload.get("transaction"):
            raise JupiterError("order: no transaction in response (route unavailable?)", code=0, raw=payload)
        data = dict(payload)
        data["raw"] = payload
        order = Order.model_validate(data)
        if order.slippage_bps != int(slippage_bps):
            raise JupiterError(
                f"order: slippageBps echoed {order.slippage_bps} != requested {slippage_bps}", raw=payload
            )
        return order

    async def execute(self, signed_tx_b64: str, request_id: str) -> ExecResult:
        """Submit the signed transaction. Transport errors propagate (caller resubmits same bytes)."""
        body = {"signedTransaction": signed_tx_b64, "requestId": request_id}
        resp = await self._http.request(
            "POST", f"{self.base_url}/swap/v2/execute", json=body, headers=self._headers()
        )
        self._persist("jupiter.execute", request_id, resp)
        payload = _json_or_none(resp)
        if not isinstance(payload, dict):
            raise JupiterError("execute: non-object response", status=resp.status_code, raw=payload)
        status = payload.get("status")
        if status not in ("Success", "Failed"):
            if resp.status_code >= 400 or payload.get("error"):
                self._raise_for_error(resp, payload, "execute")
            raise JupiterError("execute: unknown status", status=resp.status_code, raw=payload)
        code = payload.get("code")
        return ExecResult(
            status=status,
            signature=payload.get("signature"),
            code=int(code) if isinstance(code, int) else 0,
            error=payload.get("error"),
            raw=payload,
        )

    async def round_trip_cost(
        self, mint: str, usd_notional: Decimal, taker: str, usdc_price: Decimal = Decimal(1)
    ) -> Decimal:
        """Quote USDC->``mint`` at ``usd_notional`` then ``mint``->USDC of the quoted output.

        Returns ``1 - sell_out / buy_in`` as a fraction (0.012 = 1.2% round trip). ``usdc_price``
        is the USD price of one USDC (normally 1). Never cached: it is a real-size probe.
        """
        if usd_notional <= 0:
            raise ValueError("usd_notional must be positive")
        buy_in = int((usd_notional / usdc_price * Decimal(10**USDC_DECIMALS)).to_integral_value(ROUND_DOWN))
        if buy_in <= 0:
            raise ValueError("usd_notional too small for one USDC base unit")
        buy = await self.order(
            SwapRequest(
                input_mint=USDC_MINT,
                output_mint=mint,
                amount_base=buy_in,
                strategy="probe",
                reason="round_trip",
            ),
            taker,
            slippage_bps=100,
        )
        sell = await self.order(
            SwapRequest(
                input_mint=mint,
                output_mint=USDC_MINT,
                amount_base=buy.out_amount,
                strategy="probe",
                reason="round_trip",
            ),
            taker,
            slippage_bps=100,
        )
        return Decimal(1) - Decimal(sell.out_amount) / Decimal(buy.in_amount)


def _json_or_none(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return None


__all__ = ["ExecResult", "JupiterError", "JupiterSwapV2"]
