"""Copy feeds: pure swap parsing, familiars chain verification, wallet polling (all offline)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from fakes import FakeRpc
from fakes_familiars import FakeFamiliars
from tiller.clock import SimClock
from tiller.copy.feeds import (
    DEFAULT_STABLE_MINTS,
    FamiliarsFeed,
    WalletFeed,
    chain_confirms,
    lamport_delta,
    parse_swap,
    token_deltas,
)
from tiller.copy.models import LeaderTrade
from tiller.ledger import Ledger
from tiller.models import SOL_MINT, USDC_MINT

FIXTURES = Path(__file__).parent / "fixtures"
IDS = json.loads((FIXTURES / "copy" / "identities.json").read_text())
LEADER = IDS["leader_wallet"]
SIGS = IDS["signatures"]
OUR_WALLET = "FAe4sisG95oZ42w7buUn5qEE4TAnfTTFPiguZUHmhiF"
TKX = "J2xccRtuG43drESLYznHhLhQkLTdfepcKYbiQ9BsJVaf"
TKY = "5Z6Ay5NEcbg3xhopc522sBCRXQujkTiuDRnHGfQdcnSf"


def _tx(load_fixture: Callable[[str], Any], name: str) -> dict[str, Any]:
    return load_fixture(f"rpc/{name}")["result"]


# ----------------------------------------------------------------------------- parse_swap


def test_parse_buy_token_with_usdc(load_fixture: Callable[[str], Any]) -> None:
    t = parse_swap(_tx(load_fixture, "tx_leader_buy_token"), LEADER, DEFAULT_STABLE_MINTS, SOL_MINT)
    assert t is not None
    assert t.side == "buy" and t.mint == TKX
    assert t.amount == Decimal(1000) and t.usd_value == Decimal(120)
    assert t.price_usd == Decimal("0.12")
    assert t.signature == SIGS["leader_buy_token"]
    assert t.ts == datetime.fromtimestamp(1790200000, tz=UTC)
    assert t.key == f"wallet:{LEADER}" and t.source == "wallet" and t.chain_verified


def test_parse_sell_token_for_usdc(load_fixture: Callable[[str], Any]) -> None:
    t = parse_swap(_tx(load_fixture, "tx_leader_sell_token"), LEADER, DEFAULT_STABLE_MINTS, SOL_MINT)
    assert t is not None
    assert t.side == "sell" and t.mint == TKX
    assert t.amount == Decimal(400) and t.usd_value == Decimal(60)


@pytest.mark.parametrize(
    "name",
    ["tx_leader_airdrop", "tx_leader_token_for_token", "tx_leader_failed"],
)
def test_parse_rejects_non_swaps(load_fixture: Callable[[str], Any], name: str) -> None:
    assert (
        parse_swap(_tx(load_fixture, name), LEADER, DEFAULT_STABLE_MINTS, SOL_MINT, sol_usd=Decimal(200))
        is None
    )


def test_parse_plain_transfer_is_none(load_fixture: Callable[[str], Any]) -> None:
    tx = _tx(load_fixture, "tx_transfer")
    assert parse_swap(tx, OUR_WALLET, DEFAULT_STABLE_MINTS, SOL_MINT, sol_usd=Decimal(200)) is None


def test_parse_multihop_resolves_net_legs(load_fixture: Callable[[str], Any]) -> None:
    """USDC -> wrapped SOL -> TKX in one transaction nets to a TKX buy for 50 USDC."""
    t = parse_swap(_tx(load_fixture, "tx_multihop"), OUR_WALLET, DEFAULT_STABLE_MINTS, SOL_MINT)
    assert t is not None
    assert t.side == "buy" and t.mint == TKX
    assert t.usd_value == Decimal(50)
    assert t.amount == Decimal("1180")


def test_parse_sol_quoted_buy_needs_sol_price(load_fixture: Callable[[str], Any]) -> None:
    tx = _tx(load_fixture, "tx_leader_buy_sol_quoted")
    assert parse_swap(tx, LEADER, DEFAULT_STABLE_MINTS, SOL_MINT) is None  # fail closed without a SOL price
    t = parse_swap(tx, LEADER, DEFAULT_STABLE_MINTS, SOL_MINT, sol_usd=Decimal(200))
    assert t is not None
    assert t.side == "buy" and t.mint == TKX
    assert t.usd_value == Decimal(100)  # 0.5 SOL * 200 (fee added back, wrapped-SOL account folded in)
    assert t.amount == Decimal(2000)


def test_parse_sol_vs_usdc_swap_is_not_a_token_trade(load_fixture: Callable[[str], Any]) -> None:
    """Both legs are quote assets: nothing to copy (SOL is the core hold asset, not a copy target)."""
    assert (
        parse_swap(
            _tx(load_fixture, "tx_swap_buy"), OUR_WALLET, DEFAULT_STABLE_MINTS, SOL_MINT, sol_usd=Decimal(150)
        )
        is None
    )


def test_parse_for_other_wallet_is_none(load_fixture: Callable[[str], Any]) -> None:
    assert (
        parse_swap(_tx(load_fixture, "tx_leader_buy_token"), OUR_WALLET, DEFAULT_STABLE_MINTS, SOL_MINT)
        is None
    )


def test_token_deltas_and_lamports(load_fixture: Callable[[str], Any]) -> None:
    tx = _tx(load_fixture, "tx_leader_buy_token")
    assert token_deltas(tx, LEADER) == {USDC_MINT: (-120_000_000, 6), TKX: (1_000_000_000, 6)}
    assert lamport_delta(tx, LEADER) == -2_039_280  # ATA rent only; the 5000 fee is added back


def test_chain_confirms_rules(load_fixture: Callable[[str], Any]) -> None:
    buy = _tx(load_fixture, "tx_leader_buy_token")
    assert chain_confirms(None, LEADER, TKX, "buy") == (False, "pending")
    assert chain_confirms(buy, LEADER, TKX, "buy") == (True, "ok")
    assert chain_confirms(buy, LEADER, TKX, "sell") == (False, "side_mismatch")
    assert chain_confirms(buy, LEADER, TKY, "buy") == (False, "mint_not_in_tx")
    assert chain_confirms(buy, OUR_WALLET, TKX, "buy") == (False, "wallet_not_signer")
    assert chain_confirms(_tx(load_fixture, "tx_leader_failed"), LEADER, TKX, "buy") == (
        False,
        "failed_on_chain",
    )


# ----------------------------------------------------------------------------- FamiliarsFeed


@pytest.fixture
def leader_detail(load_fixture: Callable[[str], Any]) -> dict[str, Any]:
    return load_fixture("familiars/agents_leader_alpha_ape")


def _fam(detail: dict[str, Any], clock: SimClock) -> FakeFamiliars:
    return FakeFamiliars(details={"alpha_ape": detail}, now=clock.now)


async def test_familiars_feed_emits_only_chain_verified(
    leader_detail: dict[str, Any], load_fixture: Callable[[str], Any], sim_clock: SimClock, tmp_ledger: Ledger
) -> None:
    rpc = FakeRpc(
        transactions={
            SIGS["leader_buy_token"]: _tx(load_fixture, "tx_leader_buy_token"),
            SIGS["leader_sell_token"]: _tx(load_fixture, "tx_leader_sell_token"),
        }
    )
    feed = FamiliarsFeed(_fam(leader_detail, sim_clock), rpc, tmp_ledger, sim_clock)
    out = await feed.poll(["alpha_ape"])
    assert {t.signature for t in out} == {SIGS["leader_buy_token"], SIGS["leader_sell_token"]}
    assert all(t.chain_verified and t.source == "familiars" and t.key == "fam:alpha_ape" for t in out)
    buy = next(t for t in out if t.side == "buy")
    assert buy.price_usd == Decimal("0.12") and buy.wallet == LEADER
    assert buy.detected_at == sim_clock.now()
    # the 'swap' kind row is ignored; the pending one waits for the chain
    assert set(feed.pending) == {SIGS["leader_pending"]}
    stored = tmp_ledger.leader_trades(since=datetime(2020, 1, 1, tzinfo=UTC))
    assert {t.signature for t in stored} == {SIGS["leader_buy_token"], SIGS["leader_sell_token"]}
    assert all(t.chain_verified for t in stored)
    # every emitted trade is cross-verifiable by signature via the RPC
    fetched = {c[1] for c in rpc.calls if c[0] == "get_transaction"}
    assert {t.signature for t in out} <= fetched


async def test_familiars_feed_unverified_then_verified_once(
    leader_detail: dict[str, Any], load_fixture: Callable[[str], Any], sim_clock: SimClock, tmp_ledger: Ledger
) -> None:
    rpc = FakeRpc(transactions={})
    feed = FamiliarsFeed(_fam(leader_detail, sim_clock), rpc, tmp_ledger, sim_clock)
    assert await feed.poll(["alpha_ape"]) == []
    sim_clock.advance(timedelta(minutes=2))
    # the transaction lands: emitted exactly once, then deduped on every later poll
    rpc.transactions[SIGS["leader_buy_token"]] = _tx(load_fixture, "tx_leader_buy_token")
    out = await feed.poll(["alpha_ape"])
    assert [t.signature for t in out] == [SIGS["leader_buy_token"]]
    assert await feed.poll(["alpha_ape"]) == []
    assert await feed.poll(["alpha_ape"]) == []
    assert len(tmp_ledger.leader_trades(since=datetime(2020, 1, 1, tzinfo=UTC))) == 1


async def test_familiars_feed_drops_after_five_minutes(
    leader_detail: dict[str, Any], load_fixture: Callable[[str], Any], sim_clock: SimClock, tmp_ledger: Ledger
) -> None:
    rpc = FakeRpc(transactions={})
    feed = FamiliarsFeed(_fam(leader_detail, sim_clock), rpc, tmp_ledger, sim_clock)
    await feed.poll(["alpha_ape"])
    assert len(feed.pending) == 3
    sim_clock.advance(timedelta(minutes=4, seconds=59))
    await feed.poll(["alpha_ape"])
    assert len(feed.pending) == 3
    sim_clock.advance(timedelta(seconds=1))
    assert await feed.poll(["alpha_ape"]) == []
    assert feed.pending == {}
    kinds = [e["kind"] for e in tmp_ledger.events(kind="leader_trade_dropped")]
    assert len(kinds) == 3
    # a dropped signature is not re-queued even if the board keeps listing it, and never counts later
    rpc.transactions[SIGS["leader_buy_token"]] = _tx(load_fixture, "tx_leader_buy_token")
    assert await feed.poll(["alpha_ape"]) == []
    assert tmp_ledger.leader_trades(since=datetime(2020, 1, 1, tzinfo=UTC)) == []


async def test_familiars_feed_rejects_board_chain_mismatch(
    leader_detail: dict[str, Any], load_fixture: Callable[[str], Any], sim_clock: SimClock, tmp_ledger: Ledger
) -> None:
    """The board says 'buy' but the chain shows a sell under that signature: dropped, never counted."""
    detail = json.loads(json.dumps(leader_detail))
    detail["trades"] = [dict(detail["trades"][2], kind="sell")]
    rpc = FakeRpc(transactions={SIGS["leader_buy_token"]: _tx(load_fixture, "tx_leader_buy_token")})
    feed = FamiliarsFeed(_fam(detail, sim_clock), rpc, tmp_ledger, sim_clock)
    assert await feed.poll(["alpha_ape"]) == []
    assert feed.pending == {}
    ev = tmp_ledger.events(kind="leader_trade_dropped")
    assert ev and ev[0]["payload"]["reason"] == "side_mismatch"


async def test_familiars_feed_survives_board_outage_and_restart(
    leader_detail: dict[str, Any], load_fixture: Callable[[str], Any], sim_clock: SimClock, tmp_ledger: Ledger
) -> None:
    rpc = FakeRpc(transactions={SIGS["leader_buy_token"]: _tx(load_fixture, "tx_leader_buy_token")})
    fam = _fam(leader_detail, sim_clock)
    feed = FamiliarsFeed(fam, rpc, tmp_ledger, sim_clock)
    fam.fail_reads = True
    assert await feed.poll(["alpha_ape"]) == []
    assert tmp_ledger.events(kind="leader_feed_error")
    fam.fail_reads = False
    assert len(await feed.poll(["alpha_ape"])) == 1
    # a fresh feed over the same ledger does not re-emit persisted signatures
    feed2 = FamiliarsFeed(fam, rpc, tmp_ledger, sim_clock)
    assert await feed2.poll(["alpha_ape"]) == []


# ----------------------------------------------------------------------------- WalletFeed


async def test_wallet_feed_polls_since_last_seen_and_dedupes(
    load_fixture: Callable[[str], Any], sim_clock: SimClock, tmp_ledger: Ledger
) -> None:
    rows = [
        {
            "signature": SIGS["leader_sell_token"],
            "slot": 2,
            "err": None,
            "blockTime": 1790207200,
            "confirmationStatus": "finalized",
        },
        {
            "signature": SIGS["leader_airdrop"],
            "slot": 1,
            "err": None,
            "blockTime": 1790200100,
            "confirmationStatus": "finalized",
        },
        {
            "signature": SIGS["leader_buy_token"],
            "slot": 0,
            "err": None,
            "blockTime": 1790200000,
            "confirmationStatus": "finalized",
        },
    ]
    rpc = FakeRpc(
        signatures={LEADER: rows},
        transactions={
            SIGS["leader_sell_token"]: _tx(load_fixture, "tx_leader_sell_token"),
            SIGS["leader_airdrop"]: _tx(load_fixture, "tx_leader_airdrop"),
            SIGS["leader_buy_token"]: _tx(load_fixture, "tx_leader_buy_token"),
        },
    )
    feed = WalletFeed(rpc, tmp_ledger, sim_clock)
    out = await feed.poll([LEADER])
    assert [(t.side, t.mint) for t in out] == [("sell", TKX), ("buy", TKX)]
    assert all(
        t.key == f"wallet:{LEADER}" and t.chain_verified and t.detected_at == sim_clock.now() for t in out
    )
    # second poll: `until` = newest signature, nothing new
    assert await feed.poll([LEADER]) == []
    calls = [c for c in rpc.calls if c[0] == "get_signatures_for_address"]
    assert calls[0][3] is None and calls[1][3] == SIGS["leader_sell_token"]
    # a new transaction shows up at the head of the list
    rpc.signatures[LEADER].insert(
        0,
        {
            "signature": SIGS["leader_buy_sol_quoted"],
            "slot": 3,
            "err": None,
            "blockTime": 1790200200,
            "confirmationStatus": "finalized",
        },
    )
    rpc.transactions[SIGS["leader_buy_sol_quoted"]] = _tx(load_fixture, "tx_leader_buy_sol_quoted")
    assert await feed.poll([LEADER]) == []  # SOL-quoted, no price source: fail closed
    assert len(tmp_ledger.leader_trades(since=datetime(2020, 1, 1, tzinfo=UTC))) == 2


async def test_wallet_feed_values_sol_leg_with_price_source(
    load_fixture: Callable[[str], Any], sim_clock: SimClock, tmp_ledger: Ledger
) -> None:
    from fakes import FakePriceSource

    rows = [{"signature": SIGS["leader_buy_sol_quoted"], "slot": 3, "err": None, "blockTime": 1790200200}]
    rpc = FakeRpc(
        signatures={LEADER: rows},
        transactions={SIGS["leader_buy_sol_quoted"]: _tx(load_fixture, "tx_leader_buy_sol_quoted")},
    )
    feed = WalletFeed(rpc, tmp_ledger, sim_clock, prices=FakePriceSource({SOL_MINT: Decimal(180)}))
    out = await feed.poll([LEADER])
    assert len(out) == 1 and out[0].usd_value == Decimal(90) and out[0].price_usd == Decimal("0.045")


async def test_wallet_feed_skips_failed_rows_and_restart_dedupes(
    load_fixture: Callable[[str], Any], sim_clock: SimClock, tmp_ledger: Ledger
) -> None:
    rows = [
        {
            "signature": SIGS["leader_failed"],
            "slot": 5,
            "err": {"InstructionError": [2, {"Custom": 6001}]},
            "blockTime": 1790200400,
        },
        {"signature": SIGS["leader_buy_token"], "slot": 0, "err": None, "blockTime": 1790200000},
    ]
    rpc = FakeRpc(
        signatures={LEADER: rows},
        transactions={SIGS["leader_buy_token"]: _tx(load_fixture, "tx_leader_buy_token")},
    )
    assert len(await WalletFeed(rpc, tmp_ledger, sim_clock).poll([LEADER])) == 1
    assert ("get_transaction", SIGS["leader_failed"]) not in rpc.calls
    assert await WalletFeed(rpc, tmp_ledger, sim_clock).poll([LEADER]) == []


def test_leader_trade_roundtrip_through_ledger(tmp_ledger: Ledger, sim_clock: SimClock) -> None:
    t = LeaderTrade(
        key="fam:x",
        wallet=LEADER,
        signature="s" * 64,
        ts=sim_clock.now(),
        detected_at=sim_clock.now(),
        side="buy",
        mint=TKX,
        usd_value=Decimal("10.5"),
        amount=Decimal(3),
        price_usd=Decimal("3.5"),
        source="familiars",
        chain_verified=True,
    )
    assert tmp_ledger.upsert_leader_trades([t]) == 1
    assert tmp_ledger.upsert_leader_trades([t]) == 0
    assert tmp_ledger.leader_trades(since=sim_clock.now() - timedelta(days=1)) == [t]
