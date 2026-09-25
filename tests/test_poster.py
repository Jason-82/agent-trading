"""PostQueue: 90 s delay, 429-only retry, uncertain reconciliation, paper fills, callout cap, daily note."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from fakes_familiars import FakeFamiliars
from tiller.clock import SimClock
from tiller.familiars.narrator import DISCLOSURE, TemplateNarrator, validate_post_text
from tiller.familiars.poster import (
    MAX_RATE_LIMIT_ATTEMPTS,
    PostQueue,
    is_synthetic_key,
    note_key,
    render_daily_note,
)
from tiller.ledger import Ledger
from tiller.models import SOL_MINT, USDC_MINT, Fill, TradeContext
from tiller.state import BrakeState

T0 = datetime(2026, 9, 24, 10, 0, tzinfo=UTC)
SIG_A = "9i7GF9gy1MdqeY2TSAjuzhbyxHa4dVkZWy63wAWDLHJEkUULZ1GJUi8eXkquV2N1jHJGQVV4MYQYNx9Czj6zhkJd"
SIG_B = "4M5hu6LqB2x7fVdsHqbmEgZdEJNqM4TFexn2z9dqeD4p3Z2tKQu4ztNskyYNDfq3Dp4Vh8wLjuGCPphsxpL2rAcJ"


class Acct:
    equity_usd = Decimal("1234.56")
    usdc_usd = Decimal("700.10")
    net_deposits_usd = Decimal("1000")


def fill(signature: str | None = SIG_A, paper: bool = False, ts: datetime = T0, side: str = "buy") -> Fill:
    in_mint, out_mint = (USDC_MINT, SOL_MINT) if side == "buy" else (SOL_MINT, USDC_MINT)
    return Fill(
        signature=signature,
        in_mint=in_mint,
        out_mint=out_mint,
        in_base=123_450_000,
        out_base=577_000_000,
        usd_in=Decimal("123.45"),
        usd_out=Decimal("123.20"),
        fee_usd=Decimal("0.25"),
        ts=ts,
        paper=paper,
        strategy="sol_trend_ensemble",
        quote_out_base=577_500_000,
    )


def ctx(paper: bool = False, side: str = "buy", signature: str | None = SIG_A) -> TradeContext:
    return TradeContext(
        strategy="sol_trend_ensemble",
        rule="donchian 20-day breakout",
        side=side,  # type: ignore[arg-type]
        symbol="SOL",
        mint=SOL_MINT,
        notional_usd=Decimal("123.45"),
        price_usd=Decimal("213.78"),
        stop_price=Decimal("190.10"),
        regime_on=True,
        brake_state="none",
        paper=paper,
        signature=signature,
    )


@pytest.fixture
def clock() -> SimClock:
    return SimClock(T0)


@pytest.fixture
def ledger(tmp_path, clock) -> Any:
    led = Ledger(tmp_path / "l.sqlite", clock=clock)
    yield led
    led.close()


@pytest.fixture
def fam(clock) -> FakeFamiliars:
    return FakeFamiliars(handle="tiller_test", details={"tiller_test": _detail()}, now=clock.now)


def _detail() -> dict[str, Any]:
    return {
        "agent": {
            "handle": "tiller_test",
            "wallet": "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin",
            "hosted": False,
            "equityUsd": "1",
            "pnl": {},
            "trades": 0,
        },
        "posts": [],
    }


@pytest.fixture
def queue(fam, ledger, clock) -> PostQueue:
    return PostQueue(fam, TemplateNarrator(), ledger, clock, "tiller_test", rng=random.Random(7))


# --------------------------------------------------------------------------- delay / happy path


async def test_trade_post_waits_90s_then_posts(queue, fam, clock, ledger) -> None:
    queue.enqueue_trade(fill(), ctx())
    row = ledger.post(SIG_A)
    assert row is not None and row.state == "queued" and row.not_before == T0 + timedelta(seconds=90)
    assert await queue.flush() == []
    clock.advance(timedelta(seconds=89))
    assert await queue.flush() == []
    assert fam.posts == []
    clock.advance(timedelta(seconds=1))
    out = await queue.flush()
    assert [o.outcome for o in out] == ["posted"]
    assert len(fam.posts) == 1
    sent = fam.posts[0]
    assert sent["kind"] == "trade" and sent["signature"] == SIG_A and sent["mint"] == SOL_MINT
    assert validate_post_text(sent["text"], ctx()) is None
    assert ledger.post(SIG_A).state == "posted"
    # second flush does nothing
    assert await queue.flush() == [] and len(fam.posts) == 1


async def test_custom_delay_and_sell_mint(fam, ledger, clock) -> None:
    q = PostQueue(fam, TemplateNarrator(), ledger, clock, "tiller_test", delay_s=30)
    q.enqueue_trade(fill(side="sell"), ctx(side="sell"))
    assert ledger.post(SIG_A).not_before == T0 + timedelta(seconds=30)
    assert ledger.post(SIG_A).mint == SOL_MINT


async def test_paper_fills_and_missing_signature_never_enqueued(queue, ledger, fam) -> None:
    queue.enqueue_trade(fill(paper=True), ctx(paper=True))
    queue.enqueue_trade(fill(signature=None), ctx(signature=None))
    queue.enqueue_trade(fill(paper=True), ctx(paper=False))  # fill says paper -> ignored
    assert ledger.posts_pending(T0 + timedelta(days=1)) == []
    assert await queue.flush() == [] and fam.posts == []


async def test_enqueue_is_idempotent_by_signature(queue, ledger, clock) -> None:
    queue.enqueue_trade(fill(), ctx())
    queue.enqueue_trade(fill(), ctx())
    clock.advance(timedelta(seconds=90))
    assert len(ledger.posts_pending(clock.now())) == 1


# --------------------------------------------------------------------------- 429 retry


async def test_429_retried_with_backoff_only(queue, fam, clock, ledger) -> None:
    fam.post_outcomes = ["rate_limited", "rate_limited", "ok"]
    queue.enqueue_trade(fill(), ctx())
    clock.advance(timedelta(seconds=90))
    out = await queue.flush()
    assert out[0].outcome == "deferred"
    row = ledger.post(SIG_A)
    assert row.state == "queued" and row.attempts == 1
    # fake reports retry_after 5 s (+ jitter <= 5 s): not due 1 s later, due after 10 s
    clock.advance(timedelta(seconds=1))
    assert await queue.flush() == []
    clock.advance(timedelta(seconds=10))
    assert (await queue.flush())[0].outcome == "deferred"
    clock.advance(timedelta(seconds=11))
    assert (await queue.flush())[0].outcome == "posted"
    assert len(fam.posts) == 3 and ledger.post(SIG_A).attempts == 3


async def test_429_gives_up_after_max_attempts(queue, fam, clock, ledger) -> None:
    fam.post_outcomes = ["rate_limited"] * MAX_RATE_LIMIT_ATTEMPTS
    queue.enqueue_trade(fill(), ctx())
    outcomes = []
    for _ in range(MAX_RATE_LIMIT_ATTEMPTS):
        clock.advance(timedelta(minutes=30))
        outcomes.extend(await queue.flush())
    assert outcomes[-1].outcome == "failed"
    assert ledger.post(SIG_A).state == "failed"
    clock.advance(timedelta(minutes=30))
    assert await queue.flush() == []
    assert len(fam.posts) == MAX_RATE_LIMIT_ATTEMPTS


async def test_rejected_is_not_retried(queue, fam, clock, ledger) -> None:
    fam.post_outcomes = ["rejected"]
    queue.enqueue_trade(fill(), ctx())
    clock.advance(timedelta(seconds=90))
    assert (await queue.flush())[0].outcome == "failed"
    clock.advance(timedelta(hours=1))
    assert await queue.flush() == [] and len(fam.posts) == 1
    assert ledger.events(kind="post.rejected")


# --------------------------------------------------------------------------- uncertain reconciliation


async def test_uncertain_then_found_on_board_no_duplicate(queue, fam, clock, ledger) -> None:
    fam.post_outcomes = ["uncertain"]
    queue.enqueue_trade(fill(), ctx())
    clock.advance(timedelta(seconds=90))
    assert (await queue.flush())[0].outcome == "uncertain"
    assert ledger.post(SIG_A).state == "uncertain"
    # the 5xx actually created the post on the board
    fam.board_posts.append({"kind": "trade", "signature": SIG_A})
    clock.advance(timedelta(seconds=60))
    out = await queue.flush()
    assert [o.outcome for o in out] == ["verified"]
    assert ledger.post(SIG_A).state == "posted"
    assert len(fam.posts) == 1  # never re-sent
    assert "agent:tiller_test" in fam.calls


async def test_uncertain_then_absent_retried_once(queue, fam, clock, ledger) -> None:
    fam.post_outcomes = ["uncertain", "ok"]
    queue.enqueue_trade(fill(), ctx())
    clock.advance(timedelta(seconds=90))
    await queue.flush()
    clock.advance(timedelta(seconds=59))
    assert await queue.flush() == []  # recheck window not reached
    clock.advance(timedelta(seconds=1))
    out = await queue.flush()
    assert [o.outcome for o in out] == ["posted"]
    assert len(fam.posts) == 2 and ledger.post(SIG_A).state == "posted"


async def test_uncertain_twice_and_absent_is_failed(queue, fam, clock, ledger) -> None:
    fam.post_outcomes = ["uncertain", "uncertain", "ok"]
    queue.enqueue_trade(fill(), ctx())
    clock.advance(timedelta(seconds=90))
    await queue.flush()
    clock.advance(timedelta(seconds=60))
    assert (await queue.flush())[0].outcome == "uncertain"
    clock.advance(timedelta(seconds=60))
    assert (await queue.flush())[0].outcome == "failed"
    assert ledger.post(SIG_A).state == "failed" and len(fam.posts) == 2
    assert ledger.events(kind="post.failed")


async def test_uncertain_with_board_unreadable_stays_uncertain(queue, fam, clock, ledger) -> None:
    fam.post_outcomes = ["uncertain", "ok"]
    queue.enqueue_trade(fill(), ctx())
    clock.advance(timedelta(seconds=90))
    await queue.flush()
    fam.fail_reads = True
    clock.advance(timedelta(seconds=60))
    # verification failed -> not found -> allowed one resend (attempts 1 < 2)
    out = await queue.flush()
    assert out[0].outcome == "posted"
    assert ledger.events(kind="post.verify_failed")


async def test_verify_posted_matches_alternate_keys(queue, fam) -> None:
    assert await queue.verify_posted(SIG_A) is False
    fam.board_posts.append({"txSignature": SIG_A})
    assert await queue.verify_posted(SIG_A) is True
    fam.details = {}
    assert await queue.verify_posted(SIG_A) is False


# --------------------------------------------------------------------------- restart safety


async def test_queue_survives_restart(fam, ledger, clock) -> None:
    q1 = PostQueue(fam, TemplateNarrator(), ledger, clock, "tiller_test")
    q1.enqueue_trade(fill(), ctx())
    clock.advance(timedelta(seconds=90))
    q2 = PostQueue(fam, TemplateNarrator(), ledger, clock, "tiller_test")  # fresh process
    out = await q2.flush()
    assert [o.outcome for o in out] == ["posted"]
    assert DISCLOSURE in fam.posts[0]["text"]


async def test_client_exception_marks_failed_never_raises(queue, fam, clock, ledger) -> None:
    async def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("bug")

    fam.post = boom  # type: ignore[method-assign]
    queue.enqueue_trade(fill(), ctx())
    clock.advance(timedelta(seconds=90))
    out = await queue.flush()
    assert out[0].outcome == "failed" and ledger.post(SIG_A).state == "failed"


# --------------------------------------------------------------------------- narrator wiring


class CustomNarrator:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    async def compose(self, c: TradeContext) -> str:
        self.calls += 1
        return self.text


async def test_narrator_text_used_when_valid_else_template(fam, ledger, clock) -> None:
    good = f"Bought SOL 123.45 USD at 213.78 per sol_trend_ensemble; stop 190.10. {DISCLOSURE}"
    n = CustomNarrator(good)
    q = PostQueue(fam, n, ledger, clock, "tiller_test")
    q.enqueue_trade(fill(), ctx())
    clock.advance(timedelta(seconds=90))
    await q.flush()
    assert fam.posts[0]["text"] == good and n.calls == 1

    bad = CustomNarrator("SOL will moon 1000x sol_trend_ensemble")
    q2 = PostQueue(fam, bad, ledger, clock, "tiller_test")
    q2.enqueue_trade(fill(signature=SIG_B), ctx(signature=SIG_B))
    clock.advance(timedelta(seconds=90))
    await q2.flush()
    assert fam.posts[1]["text"] == TemplateNarrator().render(ctx(signature=SIG_B))


# --------------------------------------------------------------------------- callouts / daily note


async def test_callout_cap_two_per_day(queue, fam, clock, ledger) -> None:
    assert queue.enqueue_callout("first callout") is True
    assert queue.enqueue_callout("second callout") is True
    assert queue.enqueue_callout("third callout") is False
    assert queue.callouts_today() == 2
    out = await queue.flush()
    assert [o.outcome for o in out] == ["posted", "posted"]
    assert all(p["kind"] == "callout" and p["signature"] is None for p in fam.posts)
    assert queue.enqueue_callout("still capped") is False
    clock.advance(timedelta(days=1))
    assert queue.enqueue_callout("new day") is True
    assert ledger.events(kind="post.callout_capped")


async def test_daily_note_once_per_day(queue, fam, clock, ledger) -> None:
    brakes = BrakeState(entries_blocked=True, reasons=["dd7"], paused_until=None)
    await queue.daily_note(Acct(), brakes)
    await queue.daily_note(Acct(), brakes)
    assert len(fam.posts) == 1
    p = fam.posts[0]
    assert p["kind"] == "note" and p["signature"] is None and p["mint"] is None
    assert "1234.56" in p["text"] and "+234.56" in p["text"] and "43.3%" in p["text"]
    assert "entries blocked (dd7)" in p["text"] and DISCLOSURE in p["text"]
    assert ledger.post(note_key(clock.now())).state == "posted"
    clock.advance(timedelta(days=1))
    await queue.daily_note(Acct(), BrakeState())
    assert len(fam.posts) == 2 and "brakes: none" in fam.posts[1]["text"]


async def test_uncertain_note_is_never_resent(queue, fam, clock, ledger) -> None:
    fam.post_outcomes = ["uncertain"]
    await queue.daily_note(Acct(), BrakeState())
    clock.advance(timedelta(seconds=60))
    out = await queue.flush()
    assert [o.outcome for o in out] == ["failed"]
    assert len(fam.posts) == 1


def test_render_daily_note_is_short_and_handles_zero_equity() -> None:
    class Zero:
        equity_usd = Decimal(0)
        usdc_usd = Decimal(0)
        net_deposits_usd = Decimal(0)

    text = render_daily_note(Zero(), BrakeState(paused_until=T0), T0)
    assert len(text) <= 400 and "paused until" in text


def test_synthetic_keys() -> None:
    assert is_synthetic_key("note:2026-09-24") and is_synthetic_key("callout:x")
    assert not is_synthetic_key(SIG_A)
