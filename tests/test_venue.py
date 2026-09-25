"""Venues: guarded pipeline end to end (respx + fakes), zero /execute on any rejection,
identical-bytes resubmit, submitted row before /execute, paper never signs, emergency tiers,
FixtureVenue next-open fills, realised fills from getTransaction fixtures."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx
from solders.hash import Hash
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction

from fakes import CountingSigner, FakeJupiter, FakeRpc, FakeTokenData, respx_router
from tiller.clock import SimClock
from tiller.data.tokens import GateResult
from tiller.execution.guard import GuardCfg
from tiller.execution.jupiter import JupiterSwapV2
from tiller.execution.rpc import HttpRpc, parse_token_account_value
from tiller.execution.venue import (
    ExecCfg,
    ExecutionFailed,
    ExecutionUncertain,
    FixtureVenue,
    GuardRejected,
    LiveSolanaVenue,
    PaperVenue,
    realised_fill_from_tx,
    slippage_for,
)
from tiller.execution.wallet import FileSigner, NullSigner, PaperCannotSign
from tiller.ledger import Ledger
from tiller.models import SOL_MINT, USDC_MINT, Candle, SwapRequest, TokenAccount

JUP = "https://jup.invalid"
RPC = "https://rpc.invalid/"
WALLET = "FAe4sisG95oZ42w7buUn5qEE4TAnfTTFPiguZUHmhiF"
FOREIGN = "GmaDrppBC7P5ARKV8g3djiwP89vz1jLK23V2GBjuAEGB"
TOKEN_X = "J2xccRtuG43drESLYznHhLhQkLTdfepcKYbiQ9BsJVaf"
REF = Decimal("150")


@pytest.fixture
def router() -> Iterator[respx.MockRouter]:
    with respx_router() as r:
        yield r


async def nosleep(_s: float) -> None:
    return None


def req(
    input_mint: str = USDC_MINT, output_mint: str = SOL_MINT, amount: int = 100_000_000, mode: str = "normal"
) -> SwapRequest:
    return SwapRequest(
        input_mint=input_mint,
        output_mint=output_mint,
        amount_base=amount,
        strategy="sol_trend_ensemble",
        reason="test",
        mode=mode,
    )  # type: ignore[arg-type]


def owned_accounts(load_fixture: Callable[[str], Any]) -> list[TokenAccount]:
    rows = load_fixture("rpc/token_accounts_spl")["result"]["value"]
    return [parse_token_account_value(r["pubkey"], r["account"]) for r in rows]  # type: ignore[misc]


class Harness:
    """Fake-backed LiveSolanaVenue with the buy scenario from the fixtures."""

    def __init__(
        self, load_fixture: Callable[[str], Any], ledger: Ledger, clock: SimClock, **jup_over: Any
    ) -> None:
        ids = load_fixture("identities")
        self.ids = ids
        self.sig = ids["buy_signature"]
        self.rpc = FakeRpc(
            balances={WALLET: ids["pre_lamports"]},
            token_accounts={WALLET: owned_accounts(load_fixture)},
            statuses={self.sig: load_fixture("rpc/statuses_finalized")["result"]["value"][0]},
            transactions={self.sig: load_fixture("rpc/tx_swap_buy")["result"]},
        )
        self.rpc.set_simulation_fixture(load_fixture("rpc/sim_ok"))
        kwargs: dict[str, Any] = dict(
            prices={SOL_MINT: Decimal("150.2"), TOKEN_X: Decimal("0.05")},
            decimals={TOKEN_X: 6},
            transaction_b64=load_fixture("jupiter/order_ok")["transaction"],
            signature=self.sig,
        )
        kwargs.update(jup_over)
        self.jup = FakeJupiter(**kwargs)
        self.tokens = FakeTokenData(decimals={TOKEN_X: 6})
        self.signer = CountingSigner(FileSigner.from_seed(bytes(range(32))))
        self.ledger = ledger
        self.clock = clock
        self.venue = LiveSolanaVenue(
            self.jup, self.rpc, self.signer, self.tokens, ledger, ExecCfg(), GuardCfg(), clock, sleep=nosleep
        )  # type: ignore[arg-type]


def foreign_payer_tx() -> str:
    kp = Keypair.from_seed(bytes([7] * 32))
    ix = transfer(TransferParams(from_pubkey=kp.pubkey(), to_pubkey=Pubkey.from_string(WALLET), lamports=1))
    msg = Message.new_with_blockhash([ix], kp.pubkey(), Hash.from_bytes(bytes([1] * 32)))
    return base64.b64encode(bytes(VersionedTransaction.populate(msg, [Signature.default()]))).decode()


# --------------------------------------------------------------------------- happy paths


async def test_live_happy_path_with_fakes(
    load_fixture: Callable[[str], Any], tmp_ledger: Ledger, sim_clock: SimClock
) -> None:
    h = Harness(load_fixture, tmp_ledger, sim_clock)
    fill = await h.venue.swap(req(), REF, None)
    assert fill.signature == h.sig and fill.paper is False
    assert fill.in_base == 100_000_000 and fill.out_base == 665_800_000
    assert fill.usd_in == Decimal("100") and fill.usd_out == Decimal("0.6658") * REF
    assert fill.quote_out_base == h.jup.quote_out(req()) and fill.round_trip_probe_pct is None
    assert h.signer.transaction_signs == 1 and h.signer.message_signs == 0
    assert len(h.jup.order_calls) == 1 and h.jup.order_calls[0][2] == 50
    assert len(h.jup.execute_calls) == 1
    methods = [c[0] for c in h.rpc.calls]
    assert methods == [
        "get_token_accounts_by_owner",
        "get_balance",
        "simulate",
        "get_signature_statuses",
        "get_transaction",
    ]
    sim_call = next(c for c in h.rpc.calls if c[0] == "simulate")
    assert sim_call[2] == [WALLET, h.ids["usdc_ata"], h.ids["tokenx_ata"], h.ids["wsol_ata"]]
    assert tmp_ledger.pending_signatures() == []
    orders = tmp_ledger.orders(state="filled")
    assert len(orders) == 1 and orders[0]["signature"] == h.sig
    assert len(tmp_ledger.fills()) == 1
    assert any(e["kind"] == "sim_ok" for e in tmp_ledger.events())


async def test_live_happy_path_respx_call_counts(
    router: respx.MockRouter, load_fixture: Callable[[str], Any], tmp_ledger: Ledger, sim_clock: SimClock
) -> None:
    ids = load_fixture("identities")
    sig = ids["buy_signature"]
    order_route = router.get(f"{JUP}/swap/v2/order").mock(
        return_value=httpx.Response(200, json=load_fixture("jupiter/order_ok"))
    )

    def execute_handler(request: httpx.Request) -> httpx.Response:
        # the submitted ledger row must exist BEFORE /execute is hit
        assert tmp_ledger.pending_signatures() == [(1, sig)]
        body = json.loads(request.content)
        assert body["requestId"] == load_fixture("jupiter/order_ok")["requestId"]
        assert body["signedTransaction"]
        return httpx.Response(200, json=load_fixture("jupiter/execute_ok"))

    execute_route = router.post(f"{JUP}/swap/v2/execute").mock(side_effect=execute_handler)
    counts: dict[str, int] = {}

    def rpc_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        m = body["method"]
        counts[m] = counts.get(m, 0) + 1
        if m == "getTokenAccountsByOwner":
            fx = (
                "rpc/token_accounts_spl"
                if body["params"][1]["programId"].startswith("Tokenkeg")
                else "rpc/token_accounts_empty"
            )
            return httpx.Response(200, json=load_fixture(fx))
        if m == "getBalance":
            return httpx.Response(200, json=load_fixture("rpc/balance"))
        if m == "simulateTransaction":
            assert body["params"][1]["accounts"]["addresses"] == [
                WALLET,
                ids["usdc_ata"],
                ids["tokenx_ata"],
                ids["wsol_ata"],
            ]
            return httpx.Response(200, json=load_fixture("rpc/sim_ok"))
        if m == "getSignatureStatuses":
            assert body["params"][0] == [sig]
            return httpx.Response(200, json=load_fixture("rpc/statuses_finalized"))
        if m == "getTransaction":
            return httpx.Response(200, json=load_fixture("rpc/tx_swap_buy"))
        raise AssertionError(f"unexpected rpc method {m}")

    rpc_route = router.post(RPC).mock(side_effect=rpc_handler)
    async with httpx.AsyncClient() as client:
        jup = JupiterSwapV2(
            JUP, "jup_synthetic", rps=1000.0, client=client, ledger=tmp_ledger, clock=sim_clock
        )
        rpc = HttpRpc([RPC], rps=1000.0, client=client, ledger=tmp_ledger, clock=sim_clock)
        venue = LiveSolanaVenue(
            jup,
            rpc,
            FileSigner.from_seed(bytes(range(32))),
            FakeTokenData(),
            tmp_ledger,
            ExecCfg(),
            GuardCfg(),
            sim_clock,
            sleep=nosleep,
        )  # type: ignore[arg-type]
        fill = await venue.swap(req(), REF, None)
    assert fill.signature == sig and fill.out_base == 665_800_000 and fill.quote_out_base == 665_800_000
    assert order_route.call_count == 1 and execute_route.call_count == 1 and rpc_route.call_count == 6
    assert counts == {
        "getTokenAccountsByOwner": 2,
        "getBalance": 1,
        "simulateTransaction": 1,
        "getSignatureStatuses": 1,
        "getTransaction": 1,
    }
    assert tmp_ledger.orders(state="filled")[0]["signature"] == sig
    sources = {s["source"] for s in tmp_ledger.raw_snapshots()}
    assert {"jupiter.order", "jupiter.execute", "rpc.simulate", "rpc.transaction"} <= sources


# --------------------------------------------------------------------------- rejections


@pytest.mark.parametrize(
    "case",
    [
        "quote_price",
        "quote_fee",
        "quote_router",
        "quote_impact",
        "transaction",
        "simulation",
        "gate",
        "reserve",
        "probe",
    ],
)
async def test_every_rejection_means_zero_execute(
    load_fixture: Callable[[str], Any], tmp_ledger: Ledger, sim_clock: SimClock, case: str
) -> None:
    over: dict[str, Any] = {}
    if case == "quote_price":
        over["prices"] = {SOL_MINT: Decimal("160")}
    elif case == "quote_fee":
        over["fee_bps"] = 50
    elif case == "quote_router":
        over["router"] = "mystery"
    elif case == "quote_impact":
        over["impact"] = Decimal("0.02")
    elif case == "transaction":
        over["transaction_b64"] = foreign_payer_tx()
    elif case == "probe":
        over["round_trip"] = Decimal("0.02")
    h = Harness(load_fixture, tmp_ledger, sim_clock, **over)
    r = req()
    gate: GateResult | None = None
    if case == "simulation":
        h.rpc.set_simulation_fixture(load_fixture("rpc/sim_delegate_set"))
    elif case == "gate":
        r = req(USDC_MINT, TOKEN_X)
    elif case == "reserve":
        h.rpc.balances[WALLET] = 10_000_000
    elif case == "probe":
        r = req(USDC_MINT, TOKEN_X)
        gate = GateResult(ok=True, reasons=[], liquidity_usd=Decimal(1_000_000))
    ref = Decimal("0.05") if case == "probe" else REF
    with pytest.raises(GuardRejected) as ei:
        await h.venue.swap(r, ref, gate)
    stage = case.split("_")[0]
    assert ei.value.stage == stage
    assert h.jup.execute_calls == []
    assert h.signer.transaction_signs == 0
    assert tmp_ledger.orders() == [] and tmp_ledger.pending_signatures() == []
    if case == "gate":
        assert h.jup.order_calls == []
    if case == "simulation":
        assert any(
            e["kind"] == "sim_rejected" and e["payload"]["mint"] == SOL_MINT for e in tmp_ledger.events()
        )
    assert any(e["kind"] == "guard_rejected" for e in tmp_ledger.events())


async def test_probe_passes_and_is_stored(
    load_fixture: Callable[[str], Any], tmp_ledger: Ledger, sim_clock: SimClock
) -> None:
    h = Harness(load_fixture, tmp_ledger, sim_clock, round_trip=Decimal("0.009"))
    # Simulation/tx fixtures describe a SOL buy; for the probe branch we only need to reach the probe.
    h.rpc.set_simulation_fixture(load_fixture("rpc/sim_err"))
    with pytest.raises(GuardRejected) as ei:
        await h.venue.swap(
            req(USDC_MINT, TOKEN_X), Decimal("0.05"), GateResult(ok=True, reasons=[], liquidity_usd=None)
        )
    assert ei.value.stage == "simulation"
    assert h.jup.probe_calls == [(TOKEN_X, Decimal("100"))]


# --------------------------------------------------------------------------- execute / confirm


async def test_network_error_resubmits_identical_bytes_without_requote(
    load_fixture: Callable[[str], Any], tmp_ledger: Ledger, sim_clock: SimClock
) -> None:
    h = Harness(load_fixture, tmp_ledger, sim_clock, execute_outcomes=["network_error", "success"])
    fill = await h.venue.swap(req(), REF, None)
    assert fill.signature == h.sig
    assert len(h.jup.order_calls) == 1
    assert len(h.jup.execute_calls) == 2
    assert h.jup.execute_calls[0] == h.jup.execute_calls[1]
    assert h.signer.transaction_signs == 1


async def test_resubmit_exhaustion_leaves_row_submitted(
    load_fixture: Callable[[str], Any], tmp_ledger: Ledger, sim_clock: SimClock
) -> None:
    h = Harness(load_fixture, tmp_ledger, sim_clock, execute_outcomes=["network_error"] * 5)
    with pytest.raises(ExecutionUncertain):
        await h.venue.swap(req(), REF, None)
    assert len(h.jup.execute_calls) == 3  # 1 + 2 resubmits
    assert len({c for c in h.jup.execute_calls}) == 1
    assert tmp_ledger.pending_signatures() == [(1, h.sig)]
    assert len(h.jup.order_calls) == 1


async def test_resubmit_window_two_minutes(
    load_fixture: Callable[[str], Any], tmp_ledger: Ledger, sim_clock: SimClock
) -> None:
    h = Harness(load_fixture, tmp_ledger, sim_clock, execute_outcomes=["network_error"] * 5)

    async def slow_sleep(_s: float) -> None:
        sim_clock.advance(timedelta(seconds=130))

    h.venue._sleep = slow_sleep  # type: ignore[assignment]
    with pytest.raises(ExecutionUncertain):
        await h.venue.swap(req(), REF, None)
    assert len(h.jup.execute_calls) == 2


async def test_execute_failed_finalizes_failed(
    load_fixture: Callable[[str], Any], tmp_ledger: Ledger, sim_clock: SimClock
) -> None:
    h = Harness(load_fixture, tmp_ledger, sim_clock, execute_outcomes=["failed"])
    with pytest.raises(ExecutionFailed):
        await h.venue.swap(req(), REF, None)
    assert tmp_ledger.pending_signatures() == []
    failed = tmp_ledger.orders(state="failed")
    assert len(failed) == 1 and "6001" in failed[0]["error"]
    assert tmp_ledger.fills() == []


async def test_onchain_error_status_finalizes_failed(
    load_fixture: Callable[[str], Any], tmp_ledger: Ledger, sim_clock: SimClock
) -> None:
    h = Harness(load_fixture, tmp_ledger, sim_clock)
    h.rpc.statuses[h.sig] = load_fixture("rpc/statuses_err")["result"]["value"][0]
    with pytest.raises(ExecutionFailed):
        await h.venue.swap(req(), REF, None)
    assert len(tmp_ledger.orders(state="failed")) == 1


async def test_confirmation_polls_until_confirmed(
    load_fixture: Callable[[str], Any], tmp_ledger: Ledger, sim_clock: SimClock
) -> None:
    h = Harness(load_fixture, tmp_ledger, sim_clock)
    h.rpc.status_sequence = [[None], [None], [load_fixture("rpc/statuses_confirmed")["result"]["value"][0]]]
    fill = await h.venue.swap(req(), REF, None)
    assert fill.signature == h.sig
    assert sum(1 for c in h.rpc.calls if c[0] == "get_signature_statuses") == 3


async def test_confirmation_timeout_is_uncertain(
    load_fixture: Callable[[str], Any], tmp_ledger: Ledger, sim_clock: SimClock
) -> None:
    h = Harness(load_fixture, tmp_ledger, sim_clock)
    h.rpc.statuses[h.sig] = None

    async def tick(_s: float) -> None:
        sim_clock.advance(timedelta(seconds=60))

    h.venue._sleep = tick  # type: ignore[assignment]
    with pytest.raises(ExecutionUncertain):
        await h.venue.swap(req(), REF, None)
    assert tmp_ledger.pending_signatures() == [(1, h.sig)]


# --------------------------------------------------------------------------- emergency mode


def test_slippage_tiers() -> None:
    cfg = ExecCfg()
    assert slippage_for(req(), cfg) == 50
    assert slippage_for(req(USDC_MINT, TOKEN_X), cfg) == 100
    assert slippage_for(req(mode="emergency"), cfg) == 150
    assert slippage_for(req(USDC_MINT, TOKEN_X, mode="emergency"), cfg) == 150


async def test_emergency_mode_uses_150bps_and_5pct_tolerance(
    load_fixture: Callable[[str], Any], tmp_ledger: Ledger, sim_clock: SimClock
) -> None:
    h = Harness(
        load_fixture, tmp_ledger, sim_clock, prices={SOL_MINT: Decimal("156")}
    )  # ~4% off the reference
    with pytest.raises(GuardRejected) as ei:
        await h.venue.swap(req(), REF, None)
    assert ei.value.stage == "quote"
    fill = await h.venue.swap(req(mode="emergency"), REF, None)
    assert fill.mode == "emergency" and fill.signature == h.sig
    assert h.jup.order_calls[-1][2] == 150
    assert tmp_ledger.fills()[0].mode == "emergency"


async def test_emergency_keeps_simulation_assertions_strict(
    load_fixture: Callable[[str], Any], tmp_ledger: Ledger, sim_clock: SimClock
) -> None:
    h = Harness(load_fixture, tmp_ledger, sim_clock)
    h.rpc.set_simulation_fixture(load_fixture("rpc/sim_owner_reassigned"))
    with pytest.raises(GuardRejected) as ei:
        await h.venue.swap(req(mode="emergency"), REF, None)
    assert ei.value.stage == "simulation" and h.jup.execute_calls == []


# --------------------------------------------------------------------------- paper venue


async def test_paper_venue_fills_with_haircut_and_never_signs(
    load_fixture: Callable[[str], Any], tmp_ledger: Ledger, sim_clock: SimClock
) -> None:
    jup = FakeJupiter(
        prices={SOL_MINT: Decimal("150.2")}, transaction_b64=load_fixture("jupiter/order_ok")["transaction"]
    )
    venue = PaperVenue(jup, FakeTokenData(), tmp_ledger, 30, sim_clock, taker=WALLET)  # type: ignore[arg-type]
    fill = await venue.swap(req(), REF, None)
    quoted = jup.quote_out(req())
    assert fill.out_base == quoted * 9970 // 10_000 and fill.quote_out_base == quoted
    assert fill.paper is True and fill.signature is None and fill.ts == sim_clock.now()
    assert fill.usd_in == Decimal("100")
    assert jup.execute_calls == [] and len(tmp_ledger.fills()) == 1
    null = NullSigner(WALLET)
    with pytest.raises(PaperCannotSign):
        null.sign_transaction(b"anything")


async def test_paper_venue_guards_quote_and_gate(
    load_fixture: Callable[[str], Any], tmp_ledger: Ledger, sim_clock: SimClock
) -> None:
    jup = FakeJupiter(prices={SOL_MINT: Decimal("165"), TOKEN_X: Decimal("0.05")}, decimals={TOKEN_X: 6})
    venue = PaperVenue(jup, FakeTokenData(decimals={TOKEN_X: 6}), tmp_ledger, 30, sim_clock, taker=WALLET)  # type: ignore[arg-type]
    with pytest.raises(GuardRejected) as ei:
        await venue.swap(req(), REF, None)
    assert ei.value.stage == "quote"
    with pytest.raises(GuardRejected) as ei2:
        await venue.swap(req(USDC_MINT, TOKEN_X), Decimal("0.05"), None)
    assert ei2.value.stage == "gate"
    fill = await venue.swap(
        req(USDC_MINT, TOKEN_X), Decimal("0.05"), GateResult(ok=True, reasons=[], liquidity_usd=None)
    )
    assert fill.round_trip_probe_pct == Decimal("0.004") and jup.probe_calls == [(TOKEN_X, Decimal("100"))]
    assert tmp_ledger.fills() == [fill]


# --------------------------------------------------------------------------- fixture venue


def bars() -> list[Candle]:
    out = []
    for i, o in enumerate((100, 110, 120)):
        ts = datetime(2026, 9, 22, tzinfo=UTC) + timedelta(days=i)
        out.append(
            Candle(
                ts=ts,
                open=Decimal(o),
                high=Decimal(o + 5),
                low=Decimal(o - 5),
                close=Decimal(o + 2),
                volume=Decimal(1),
            )
        )
    return out


async def test_fixture_venue_fills_at_next_open(tmp_ledger: Ledger) -> None:
    clock = SimClock(datetime(2026, 9, 23, tzinfo=UTC))  # close of the 09-22 bar
    venue = FixtureVenue({SOL_MINT: bars()}, tmp_ledger, cost_bps=30, clock=clock)
    buy = await venue.swap(req(amount=110_000_000), REF, None)
    assert buy.ts == datetime(2026, 9, 23, tzinfo=UTC)
    assert buy.out_base == 997_000_000 and buy.quote_out_base == 1_000_000_000
    assert (
        buy.usd_in == Decimal("110") and buy.usd_out == Decimal("109.67") and buy.fee_usd == Decimal("0.33")
    )
    clock.set(datetime(2026, 9, 24, tzinfo=UTC))
    sell = await venue.swap(req(SOL_MINT, USDC_MINT, amount=1_000_000_000), REF, None)
    assert sell.ts == datetime(2026, 9, 24, tzinfo=UTC) and sell.out_base == 119_640_000
    assert sell.usd_in == Decimal("120") and sell.paper is True and sell.signature is None
    assert len(tmp_ledger.fills()) == 2
    clock.set(datetime(2026, 9, 25, tzinfo=UTC))
    with pytest.raises(GuardRejected):
        await venue.swap(req(), REF, None)


# --------------------------------------------------------------------------- realised fills


def test_realised_fill_buy_sell_multihop(load_fixture: Callable[[str], Any]) -> None:
    marks = {SOL_MINT: Decimal("150"), USDC_MINT: Decimal(1), TOKEN_X: Decimal("0.0421")}
    buy = realised_fill_from_tx(load_fixture("rpc/tx_swap_buy")["result"], WALLET, req(), marks)
    assert (buy.in_base, buy.out_base) == (100_000_000, 665_800_000)
    assert buy.usd_in == Decimal("100") and buy.usd_out == Decimal("99.87")
    assert buy.fee_usd == Decimal("0.00000075") * 1000 and buy.ts == datetime(2026, 9, 24, 0, 5, tzinfo=UTC)
    assert buy.signature == load_fixture("identities")["buy_signature"] and buy.paper is False
    sell = realised_fill_from_tx(
        load_fixture("rpc/tx_swap_sell")["result"], WALLET, req(SOL_MINT, USDC_MINT, 500_000_000), marks
    )
    assert (sell.in_base, sell.out_base) == (500_000_000, 74_900_000)
    assert sell.usd_in == Decimal("75") and sell.usd_out == Decimal("74.9")
    multi = realised_fill_from_tx(
        load_fixture("rpc/tx_multihop")["result"], WALLET, req(USDC_MINT, TOKEN_X, 50_000_000), marks
    )
    assert (multi.in_base, multi.out_base) == (50_000_000, 1_180_000_000)
    assert multi.usd_out == Decimal("1180") * Decimal("0.0421")


def test_realised_fill_rejects_non_swaps(load_fixture: Callable[[str], Any]) -> None:
    marks = {SOL_MINT: Decimal("150"), USDC_MINT: Decimal(1)}
    with pytest.raises(ValueError, match="swap legs"):
        realised_fill_from_tx(load_fixture("rpc/tx_transfer")["result"], WALLET, req(), marks)
    with pytest.raises(ValueError, match="failed on chain"):
        realised_fill_from_tx(load_fixture("rpc/tx_failed")["result"], WALLET, req(), marks)
    with pytest.raises(ValueError, match="marks"):
        realised_fill_from_tx(
            load_fixture("rpc/tx_swap_buy")["result"], WALLET, req(), {USDC_MINT: Decimal(1)}
        )
    with pytest.raises(ValueError, match="account keys"):
        realised_fill_from_tx(load_fixture("rpc/tx_swap_buy")["result"], FOREIGN, req(), marks)
