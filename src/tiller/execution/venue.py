"""Venues: the one guarded pipeline every order goes through.

* :class:`LiveSolanaVenue` — order -> check_quote -> (non-allowlisted entry) round-trip probe
  -> check_transaction -> simulate -> check_simulation -> ledger row ``submitted`` (signature
  derived locally) -> sign -> execute (same bytes resubmitted at most ``execute_resubmits``
  times within ``resubmit_window_s`` on transport errors; never a fresh quote) -> confirm ->
  getTransaction -> :func:`realised_fill_from_tx` -> ledger finalize.
* :class:`PaperVenue` — steps 1-4 with live quotes, fills at ``out_amount * (1 - haircut)``;
  takes no signer at all.
* :class:`FixtureVenue` — fills at the next bar open from bundled candles (offline tracking).

Units: base units for amounts; ``ref_price_usd`` is the USD price of the non-USDC leg.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from typing import Any, Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, ConfigDict, Field

from tiller.clock import Clock
from tiller.data.tokens import GateResult, TokenData
from tiller.execution.guard import (
    LAMPORT_FEE_ALLOWANCE,
    GuardCfg,
    SimExpectations,
    check_quote,
    check_simulation,
    check_transaction,
    derive_ata,
    is_core_pair,
    signature_of,
)
from tiller.execution.jupiter import JupiterSwapV2
from tiller.execution.rpc import Rpc
from tiller.execution.wallet import Signer
from tiller.ledger import Ledger
from tiller.models import (
    LAMPORTS_PER_SOL,
    SOL_DECIMALS,
    SOL_MINT,
    TOKEN_PROGRAM,
    USDC_DECIMALS,
    USDC_MINT,
    Candle,
    Fill,
    SwapRequest,
)

SleepFn = Callable[[float], Awaitable[None]]


@runtime_checkable
class Venue(Protocol):
    """Anything that can turn a SwapRequest into a Fill (live, paper or fixture)."""

    async def swap(self, req: SwapRequest, ref_price_usd: Decimal, gate: GateResult | None) -> Fill: ...


class GuardRejected(Exception):
    """A pre-sign check failed; nothing was signed or sent. ``stage`` names the check."""

    def __init__(self, stage: str, reason: str) -> None:
        super().__init__(f"{stage}: {reason}")
        self.stage = stage
        self.reason = reason


class ExecutionFailed(Exception):
    """Jupiter or the chain reported the transaction failed; the ledger row is finalized failed."""


class ExecutionUncertain(Exception):
    """The transaction was sent but its outcome is unknown; the ledger row stays ``submitted``
    (signature recorded) for boot reconciliation."""


class ExecCfg(BaseModel):
    """Execution parameters (bps, lamports, seconds)."""

    model_config = ConfigDict(extra="forbid")

    slippage_bps_sol_usdc: int = 50
    slippage_bps_token: int = 100
    slippage_bps_emergency: int = 150
    sol_reserve_lamports: int = 50_000_000
    allowlist_mints: set[str] = Field(default_factory=lambda: {SOL_MINT, USDC_MINT})
    max_round_trip_token: Decimal = Decimal("0.015")
    execute_resubmits: int = 2
    resubmit_window_s: int = 120
    resubmit_delay_s: float = 2.0
    confirm_timeout_s: float = 90.0
    poll_interval_s: float = 2.0


def slippage_for(req: SwapRequest, cfg: ExecCfg) -> int:
    """Explicit slippage tier: emergency > core pair > token."""
    if req.mode == "emergency":
        return cfg.slippage_bps_emergency
    if is_core_pair(req.input_mint, req.output_mint):
        return cfg.slippage_bps_sol_usdc
    return cfg.slippage_bps_token


def non_usdc_leg(req: SwapRequest) -> str:
    """The mint whose USD price ``ref_price_usd`` refers to (exactly one leg must be USDC)."""
    if req.input_mint == USDC_MINT and req.output_mint != USDC_MINT:
        return req.output_mint
    if req.output_mint == USDC_MINT and req.input_mint != USDC_MINT:
        return req.input_mint
    raise GuardRejected("pair", "exactly one leg must be USDC")


def _units(base: int, decimals: int) -> Decimal:
    return Decimal(base) / Decimal(10**decimals)


def _base(units: Decimal, decimals: int) -> int:
    return int((units * Decimal(10**decimals)).to_integral_value(ROUND_DOWN))


class LiveSolanaVenue:
    """Signed execution through Jupiter Swap V2 with every guard in the spec."""

    def __init__(
        self,
        jup: JupiterSwapV2,
        rpc: Rpc,
        signer: Signer,
        tokens: TokenData,
        ledger: Ledger,
        cfg: ExecCfg,
        guard: GuardCfg,
        clock: Clock,
        *,
        sleep: SleepFn = asyncio.sleep,
    ) -> None:
        self.jup = jup
        self.rpc = rpc
        self.signer = signer
        self.tokens = tokens
        self.ledger = ledger
        self.cfg = cfg
        self.guard = guard
        self.clock = clock
        self._sleep = sleep

    @property
    def wallet(self) -> str:
        return self.signer.pubkey

    async def _decimals(self, mint: str) -> int:
        return await self.tokens.decimals(mint)

    async def _token_program(self, mint: str) -> str:
        if mint in (SOL_MINT, USDC_MINT):
            return TOKEN_PROGRAM
        return await self.tokens.token_program(mint)

    def _event(self, level: str, kind: str, payload: dict[str, Any]) -> None:
        self.ledger.add_event(level, kind, payload)

    def _reject(self, stage: str, reason: str, req: SwapRequest) -> GuardRejected:
        self._event("warning", "guard_rejected", {"stage": stage, "reason": reason, "req": req.model_dump()})
        return GuardRejected(stage, reason)

    async def swap(self, req: SwapRequest, ref_price_usd: Decimal, gate: GateResult | None) -> Fill:
        """Run the full pipeline; raises GuardRejected / ExecutionFailed / ExecutionUncertain."""
        wallet = self.wallet
        leg = non_usdc_leg(req)
        entry_into_token = req.output_mint not in self.cfg.allowlist_mints
        if entry_into_token and (gate is None or not gate.ok):
            raise self._reject("gate", "token gate not passed for non-allowlisted entry", req)

        slippage = slippage_for(req, self.cfg)
        in_dec = await self._decimals(req.input_mint)
        out_dec = await self._decimals(req.output_mint)

        # 1-2. quote and quote checks
        order = await self.jup.order(req, wallet, slippage)
        reason = check_quote(req, order, ref_price_usd, in_dec, out_dec, self.guard)
        if reason is not None:
            raise self._reject("quote", reason, req)

        # 3. round-trip probe for non-allowlisted entries (never cached, real size)
        probe: Decimal | None = None
        if entry_into_token:
            usd_notional = _units(order.in_amount, USDC_DECIMALS)
            probe = await self.jup.round_trip_cost(leg, usd_notional, wallet)
            limit = self.cfg.max_round_trip_token
            if probe > limit:
                raise self._reject("probe", f"round trip {probe:.4%} above {limit:.2%}", req)

        # 5. static transaction checks
        owned = await self.rpc.get_token_accounts_by_owner(wallet)
        tx_bytes = base64.b64decode(order.transaction_b64)
        reason = check_transaction(tx_bytes, wallet, [a.pubkey for a in owned], self.guard)
        if reason is not None:
            raise self._reject("transaction", reason, req)

        # SOL reserve is never spent
        pre_lamports = await self.rpc.get_balance(wallet)
        sol_spend = order.in_amount if req.input_mint == SOL_MINT else 0
        if pre_lamports - sol_spend - LAMPORT_FEE_ALLOWANCE < self.cfg.sol_reserve_lamports:
            raise self._reject("reserve", "order would breach the SOL gas reserve", req)

        # 6. simulation with account snapshots
        input_account = derive_ata(wallet, req.input_mint, await self._token_program(req.input_mint))
        output_account = derive_ata(wallet, req.output_mint, await self._token_program(req.output_mint))
        addresses = list(dict.fromkeys([wallet, *[a.pubkey for a in owned], input_account, output_account]))
        sim = await self.rpc.simulate(order.transaction_b64, addresses)
        expect = SimExpectations(
            wallet=wallet,
            input_mint=req.input_mint,
            output_mint=req.output_mint,
            in_base=order.in_amount,
            min_out_base=order.out_amount * (10_000 - slippage) // 10_000,
            max_lamport_drop=sol_spend + LAMPORT_FEE_ALLOWANCE,
            owned_token_accounts=owned,
            pre_lamports=pre_lamports,
            input_account=input_account,
            output_account=output_account,
        )
        reason = check_simulation(sim, expect)
        if reason is not None:
            self._event("warning", "sim_rejected", {"mint": leg, "reason": reason})
            raise self._reject("simulation", reason, req)
        self._event("info", "sim_ok", {"mint": leg, "units_consumed": sim.units_consumed})

        # 7. sign, record submitted, execute (identical bytes on retry), confirm, finalize
        signed = self.signer.sign_transaction(tx_bytes)
        signature = signature_of(signed)
        order_id = self.ledger.record_order(req, order, signature)
        signed_b64 = base64.b64encode(signed).decode()
        result = await self._execute_with_resubmit(signed_b64, order.request_id, order_id)
        if result.status != "Success":
            err = f"execute failed code {result.code}: {result.error}"
            self.ledger.finalize_order(order_id, None, err)
            raise ExecutionFailed(err)
        if result.signature and result.signature != signature:
            self._event("warning", "signature_mismatch", {"local": signature, "remote": result.signature})

        await self._confirm(signature, order_id)
        tx = await self._fetch_transaction(signature)
        marks = {leg: ref_price_usd, USDC_MINT: Decimal(1)}
        fill = realised_fill_from_tx(tx, wallet, req, marks)
        fill = fill.model_copy(
            update={"quote_out_base": order.out_amount, "round_trip_probe_pct": probe, "mode": req.mode}
        )
        self.ledger.finalize_order(order_id, fill, None)
        return fill

    async def _execute_with_resubmit(self, signed_b64: str, request_id: str, order_id: int) -> Any:
        started = self.clock.now()
        attempt = 0
        while True:
            try:
                return await self.jup.execute(signed_b64, request_id)
            except httpx.HTTPError as e:
                attempt += 1
                elapsed = (self.clock.now() - started).total_seconds()
                self._event("warning", "execute_transport_error", {"attempt": attempt, "error": repr(e)})
                if attempt > self.cfg.execute_resubmits or elapsed > self.cfg.resubmit_window_s:
                    raise ExecutionUncertain(
                        f"execute transport error after {attempt} attempts: {e!r}"
                    ) from e
                await self._sleep(self.cfg.resubmit_delay_s)

    async def _confirm(self, signature: str, order_id: int) -> None:
        started = self.clock.now()
        while True:
            statuses = await self.rpc.get_signature_statuses([signature])
            st = statuses[0] if statuses else None
            if st is not None:
                if st.get("err") is not None:
                    err = f"on-chain error {st['err']!r}"
                    self.ledger.finalize_order(order_id, None, err)
                    raise ExecutionFailed(err)
                if st.get("confirmationStatus") in ("confirmed", "finalized"):
                    return
            if (self.clock.now() - started).total_seconds() > self.cfg.confirm_timeout_s:
                raise ExecutionUncertain(
                    f"no confirmation for {signature} within {self.cfg.confirm_timeout_s}s"
                )
            await self._sleep(self.cfg.poll_interval_s)

    async def _fetch_transaction(self, signature: str) -> dict[str, Any]:
        started = self.clock.now()
        while True:
            tx = await self.rpc.get_transaction(signature)
            if tx is not None:
                return tx
            if (self.clock.now() - started).total_seconds() > self.cfg.confirm_timeout_s:
                raise ExecutionUncertain(f"confirmed {signature} but getTransaction returned nothing")
            await self._sleep(self.cfg.poll_interval_s)


class PaperVenue:
    """Live quotes and guards, no signer, fills at ``out_amount * (1 - haircut_bps/1e4)``."""

    def __init__(
        self,
        jup: JupiterSwapV2,
        tokens: TokenData,
        ledger: Ledger,
        haircut_bps: int = 30,
        clock: Clock | None = None,
        *,
        taker: str,
        cfg: ExecCfg | None = None,
        guard: GuardCfg | None = None,
    ) -> None:
        if clock is None:
            raise ValueError("PaperVenue needs a clock")
        self.jup = jup
        self.tokens = tokens
        self.ledger = ledger
        self.haircut_bps = haircut_bps
        self.clock = clock
        self.taker = taker
        self.cfg = cfg or ExecCfg()
        self.guard = guard or GuardCfg()

    async def swap(self, req: SwapRequest, ref_price_usd: Decimal, gate: GateResult | None) -> Fill:
        leg = non_usdc_leg(req)
        entry_into_token = req.output_mint not in self.cfg.allowlist_mints
        if entry_into_token and (gate is None or not gate.ok):
            raise GuardRejected("gate", "token gate not passed for non-allowlisted entry")
        slippage = slippage_for(req, self.cfg)
        in_dec = await self.tokens.decimals(req.input_mint)
        out_dec = await self.tokens.decimals(req.output_mint)
        order = await self.jup.order(req, self.taker, slippage)
        reason = check_quote(req, order, ref_price_usd, in_dec, out_dec, self.guard)
        if reason is not None:
            self.ledger.add_event("warning", "guard_rejected", {"stage": "quote", "reason": reason})
            raise GuardRejected("quote", reason)
        probe: Decimal | None = None
        if entry_into_token:
            probe = await self.jup.round_trip_cost(leg, _units(order.in_amount, USDC_DECIMALS), self.taker)
            if probe > self.cfg.max_round_trip_token:
                raise GuardRejected(
                    "probe", f"round trip {probe:.4%} above {self.cfg.max_round_trip_token:.2%}"
                )
        out_base = order.out_amount * (10_000 - self.haircut_bps) // 10_000
        marks = {leg: ref_price_usd, USDC_MINT: Decimal(1)}
        usd_in = _units(order.in_amount, in_dec) * marks[req.input_mint]
        usd_out = _units(out_base, out_dec) * marks[req.output_mint]
        fill = Fill(
            signature=None,
            in_mint=req.input_mint,
            out_mint=req.output_mint,
            in_base=order.in_amount,
            out_base=out_base,
            usd_in=usd_in,
            usd_out=usd_out,
            fee_usd=_units(order.out_amount - out_base, out_dec) * marks[req.output_mint],
            ts=self.clock.now(),
            paper=True,
            strategy=req.strategy,
            quote_out_base=order.out_amount,
            round_trip_probe_pct=probe,
            mode=req.mode,
        )
        self.ledger.record_fill(fill)
        return fill


class FixtureVenue:
    """Offline venue: fills at the open of the first bar with ``ts >= now`` minus ``cost_bps``.

    ``bars`` is keyed by MINT (the non-USDC leg); ``decimals`` defaults to 9 for SOL and can be
    extended per mint. The cost is charged on the output leg so equity drops by
    ``notional * cost_bps / 1e4`` per side, matching the backtester's per-side turnover cost.
    """

    def __init__(
        self,
        bars: dict[str, list[Candle]],
        ledger: Ledger,
        cost_bps: int,
        clock: Clock,
        *,
        decimals: dict[str, int] | None = None,
    ) -> None:
        self.bars = {k: sorted(v, key=lambda b: b.ts) for k, v in bars.items()}
        self.ledger = ledger
        self.cost_bps = cost_bps
        self.clock = clock
        self.decimals = {SOL_MINT: SOL_DECIMALS, USDC_MINT: USDC_DECIMALS, **(decimals or {})}

    def next_open(self, mint: str, now: datetime) -> tuple[datetime, Decimal]:
        bars = self.bars.get(mint)
        if not bars:
            raise GuardRejected("fixture", f"no bars for {mint}")
        for b in bars:
            if b.ts >= now:
                return b.ts, b.open
        raise GuardRejected("fixture", f"no bar at or after {now.isoformat()} for {mint}")

    async def swap(self, req: SwapRequest, ref_price_usd: Decimal, gate: GateResult | None) -> Fill:
        leg = non_usdc_leg(req)
        ts, price = self.next_open(leg, self.clock.now())
        cost = Decimal(self.cost_bps) / Decimal(10_000)
        leg_dec = self.decimals.get(leg)
        if leg_dec is None:
            raise GuardRejected("fixture", f"unknown decimals for {leg}")
        if req.input_mint == USDC_MINT:
            usd_in = _units(req.amount_base, USDC_DECIMALS)
            gross_units = usd_in / price
            out_units = gross_units * (Decimal(1) - cost)
            out_base = _base(out_units, leg_dec)
            quote_out = _base(gross_units, leg_dec)
            usd_out = out_units * price
        else:
            in_units = _units(req.amount_base, leg_dec)
            gross_usd = in_units * price
            usd_out = gross_usd * (Decimal(1) - cost)
            out_base = _base(usd_out, USDC_DECIMALS)
            quote_out = _base(gross_usd, USDC_DECIMALS)
            usd_in = gross_usd
        fill = Fill(
            signature=None,
            in_mint=req.input_mint,
            out_mint=req.output_mint,
            in_base=req.amount_base,
            out_base=out_base,
            usd_in=usd_in,
            usd_out=usd_out,
            fee_usd=usd_in * cost,
            ts=ts,
            paper=True,
            strategy=req.strategy,
            quote_out_base=quote_out,
            round_trip_probe_pct=None,
            mode=req.mode,
        )
        self.ledger.record_fill(fill)
        return fill


# --------------------------------------------------------------------------- realised fills


def _account_keys(message: dict[str, Any]) -> list[str]:
    keys = message.get("accountKeys") or []
    out: list[str] = []
    for k in keys:
        if isinstance(k, dict):
            out.append(str(k.get("pubkey")))
        else:
            out.append(str(k))
    return out


def realised_fill_from_tx(
    tx: dict[str, Any], wallet: str, req: SwapRequest, marks: dict[str, Decimal]
) -> Fill:
    """Realised swap legs from ``getTransaction`` pre/post balances (no paid parser).

    SOL legs are measured in wallet lamports net of the transaction fee (wrapped-SOL token
    balances are ignored); token legs are the net change over every token account owned by
    ``wallet`` for that mint. ``marks`` are USD prices per whole token for both legs and,
    optionally, SOL (for ``fee_usd``; 0 when absent). Raises ``ValueError`` when the transaction
    failed or does not show the requested legs.
    """
    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        raise ValueError(f"transaction failed on chain: {meta['err']!r}")
    message = (tx.get("transaction") or {}).get("message") or {}
    keys = _account_keys(message)
    if wallet not in keys:
        raise ValueError("wallet not among transaction account keys")
    idx = keys.index(wallet)
    fee = int(meta.get("fee") or 0)
    pre_bal = meta.get("preBalances") or []
    post_bal = meta.get("postBalances") or []
    if len(pre_bal) <= idx or len(post_bal) <= idx:
        raise ValueError("balances missing for wallet")
    lamport_delta = int(post_bal[idx]) - int(pre_bal[idx]) + (fee if idx == 0 else 0)

    decimals: dict[str, int] = {SOL_MINT: SOL_DECIMALS, USDC_MINT: USDC_DECIMALS}
    token_delta: dict[str, int] = {}

    def _accumulate(entries: list[dict[str, Any]], sign: int) -> None:
        for e in entries:
            if str(e.get("owner")) != wallet:
                continue
            mint = str(e.get("mint"))
            if mint == SOL_MINT:
                continue
            ui = e.get("uiTokenAmount") or {}
            amount = int(ui.get("amount") or 0)
            if "decimals" in ui:
                decimals[mint] = int(ui["decimals"])
            token_delta[mint] = token_delta.get(mint, 0) + sign * amount

    _accumulate(meta.get("preTokenBalances") or [], -1)
    _accumulate(meta.get("postTokenBalances") or [], +1)

    def _delta(mint: str) -> int:
        if mint == SOL_MINT:
            return lamport_delta
        return token_delta.get(mint, 0)

    in_base = -_delta(req.input_mint)
    out_base = _delta(req.output_mint)
    if in_base <= 0 or out_base <= 0:
        raise ValueError(f"transaction does not show the requested swap legs (in {in_base}, out {out_base})")
    if req.input_mint not in decimals or req.output_mint not in decimals:
        raise ValueError("token decimals unknown for a swap leg")
    if req.input_mint not in marks or req.output_mint not in marks:
        raise ValueError("USD marks missing for a swap leg")

    usd_in = _units(in_base, decimals[req.input_mint]) * marks[req.input_mint]
    usd_out = _units(out_base, decimals[req.output_mint]) * marks[req.output_mint]
    sol_mark = marks.get(SOL_MINT)
    fee_usd = (Decimal(fee) / Decimal(LAMPORTS_PER_SOL)) * sol_mark if sol_mark is not None else Decimal(0)
    block_time = tx.get("blockTime")
    if block_time is None:
        raise ValueError("transaction has no blockTime")
    ts = datetime.fromtimestamp(int(block_time), tz=UTC)
    sigs = (tx.get("transaction") or {}).get("signatures") or []
    signature = str(sigs[0]) if sigs else None
    return Fill(
        signature=signature,
        in_mint=req.input_mint,
        out_mint=req.output_mint,
        in_base=in_base,
        out_base=out_base,
        usd_in=usd_in,
        usd_out=usd_out,
        fee_usd=fee_usd,
        ts=ts,
        paper=False,
        strategy=req.strategy,
        quote_out_base=0,
        round_trip_probe_pct=None,
        mode=req.mode,
    )
