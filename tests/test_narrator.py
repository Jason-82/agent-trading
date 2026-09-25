"""TemplateNarrator output, validate_post_text rules, ClaudeNarrator fallbacks (no anthropic import)."""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from tiller.clock import SimClock
from tiller.familiars.narrator import (
    DISCLOSURE,
    MAX_POST_CHARS,
    ClaudeNarrator,
    TemplateNarrator,
    build_prompt,
    context_numbers,
    fmt_price,
    validate_post_text,
)
from tiller.models import SOL_MINT, TradeContext

FOREIGN_MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
SIG = "9i7GF9gy1MdqeY2TSAjuzhbyxHa4dVkZWy63wAWDLHJEkUULZ1GJUi8eXkquV2N1jHJGQVV4MYQYNx9Czj6zhkJd"


def ctx(**over: Any) -> TradeContext:
    kw: dict[str, Any] = dict(
        strategy="sol_trend_ensemble",
        rule="donchian 20-day breakout; 3 of 7 sleeves long",
        side="buy",
        symbol="SOL",
        mint=SOL_MINT,
        notional_usd=Decimal("123.45"),
        price_usd=Decimal("213.78"),
        stop_price=Decimal("190.10"),
        regime_on=True,
        brake_state="none",
        paper=False,
        signature=SIG,
    )
    kw.update(over)
    return TradeContext(**kw)


def good_text(c: TradeContext) -> str:
    return TemplateNarrator().render(c)


# --------------------------------------------------------------------------- template


def test_template_output_valid_and_complete() -> None:
    c = ctx()
    text = good_text(c)
    assert validate_post_text(text, c) is None
    assert len(text) <= MAX_POST_CHARS
    for needle in (
        "BUY SOL",
        "123.45",
        "213.78",
        "sol_trend_ensemble",
        "donchian 20-day",
        "stop 190.10",
        "regime on",
        "brakes: none",
        DISCLOSURE,
    ):
        assert needle in text
    assert "paper" not in text


def test_template_is_deterministic_and_marks_paper_and_sell() -> None:
    c = ctx(side="sell", paper=True, stop_price=None, regime_on=None, brake_state="dd7 blocked")
    a, b = good_text(c), good_text(c)
    assert a == b
    assert a.startswith("SELL SOL")
    assert "paper" in a and "stop" not in a and "regime" not in a and "dd7 blocked" in a
    assert validate_post_text(a, c) is None


async def test_template_compose_matches_render() -> None:
    c = ctx()
    assert await TemplateNarrator().compose(c) == TemplateNarrator().render(c)


def test_template_truncates_long_rule_within_limit() -> None:
    c = ctx(rule="long rule text " * 40)
    text = good_text(c)
    assert len(text) <= MAX_POST_CHARS
    assert text.endswith(DISCLOSURE)
    assert validate_post_text(text, c) is None


def test_fmt_price_small_values() -> None:
    assert fmt_price(Decimal("0.00001234")) == "0.00001234"
    assert fmt_price(Decimal("213.7")) == "213.70"
    assert fmt_price(Decimal("1")) == "1.00"


def test_context_numbers_include_every_rendering() -> None:
    nums = context_numbers(ctx(notional_usd=Decimal("1500"), price_usd=Decimal("0.5")))
    assert {"1500", "00", "0", "5", "50"} <= nums
    assert "20" in nums and "7" in nums  # from the rule


# --------------------------------------------------------------------------- validator


@pytest.mark.parametrize(
    "mutate,reason_prefix",
    [
        (lambda t: "", "empty"),
        (lambda t: t + " " + "a" * (MAX_POST_CHARS + 1), "too long"),
        (lambda t: t.replace("sol_trend_ensemble", "trend"), "missing strategy"),
        (lambda t: t.replace(DISCLOSURE, "automated"), "missing automated-agent disclosure"),
        (lambda t: t + " see https://example.com/x", "contains URL"),
        (lambda t: t + " see www.example.com", "contains URL"),
        (lambda t: t + " " + FOREIGN_MINT, "contains a base58"),
        (lambda t: t + " " + SIG, "contains a base58"),
        (lambda t: t + " pnl +999", "contains a number not in context"),
        (lambda t: t + " up 42%", "contains a number not in context"),
        (lambda t: t + " it will pump", "contains promotional phrase: 'will'"),
        (lambda t: t + " to the MOON", "contains promotional phrase: 'moon'"),
        (lambda t: t + " target reached", "contains promotional phrase: 'target'"),
        (lambda t: t + " 2x by friday", "contains promotional phrase: 'x by'"),
        (lambda t: t + " guaranteed", "contains promotional phrase: 'guaranteed'"),
    ],
)
def test_validator_rejects(mutate, reason_prefix) -> None:
    c = ctx()
    reason = validate_post_text(mutate(good_text(c)), c)
    assert reason is not None and reason.startswith(reason_prefix), reason


def test_validator_allows_own_mint_and_context_numbers() -> None:
    c = ctx()
    text = good_text(c) + f" mint {SOL_MINT} size 123.45 stop 190.10"
    assert validate_post_text(text, c) is None


def test_validator_word_boundary_for_phrases() -> None:
    c = ctx(rule="willow breakout")  # 'will' inside a word is fine
    assert validate_post_text(good_text(c), c) is None


# --------------------------------------------------------------------------- claude narrator


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, kind: str, payload: dict[str, Any]) -> None:
        self.events.append((kind, payload))


def make_claude(clock: SimClock, complete, **kw: Any) -> tuple[ClaudeNarrator, Recorder]:
    rec = Recorder()
    n = ClaudeNarrator(
        "sk-ant-test-not-real",
        "claude-haiku-4-5",
        Decimal("0.50"),
        TemplateNarrator(),
        clock,
        complete=complete,
        timeout_s=0.05,
        log=rec,
        **kw,
    )
    return n, rec


async def test_claude_uses_valid_llm_text(sim_clock) -> None:
    c = ctx()
    valid = f"Bought SOL for 123.45 USD at 213.78 under sol_trend_ensemble; stop 190.10. {DISCLOSURE}"

    async def complete(prompt: str) -> str:
        assert "sol_trend_ensemble" in prompt and "123.45" in prompt
        return valid

    n, rec = make_claude(sim_clock, complete)
    assert await n.compose(c) == valid
    kinds = [k for k, _ in rec.events]
    assert kinds == ["narrator.call", "narrator.response"]
    assert n.spent_today_usd == Decimal("0.005")


async def test_claude_falls_back_on_invalid_text(sim_clock) -> None:
    c = ctx()

    async def complete(prompt: str) -> str:
        return "SOL will moon to 500! sol_trend_ensemble " + DISCLOSURE

    n, rec = make_claude(sim_clock, complete)
    assert await n.compose(c) == good_text(c)
    assert any(k == "narrator.fallback" and p["reason"].startswith("invalid") for k, p in rec.events)


async def test_claude_falls_back_on_timeout(sim_clock) -> None:
    c = ctx()

    async def complete(prompt: str) -> str:
        await asyncio.sleep(1.0)
        return "never"

    n, rec = make_claude(sim_clock, complete)
    assert await n.compose(c) == good_text(c)
    assert ("narrator.fallback", {"reason": "timeout"}) in rec.events


async def test_claude_falls_back_on_error(sim_clock) -> None:
    c = ctx()

    async def complete(prompt: str) -> str:
        raise RuntimeError("api down")

    n, rec = make_claude(sim_clock, complete)
    assert await n.compose(c) == good_text(c)
    assert any(p.get("reason") == "error: RuntimeError" for _, p in rec.events)


async def test_claude_daily_cap_exhausted_then_resets_next_day(sim_clock) -> None:
    c = ctx()
    calls = 0
    valid = f"Bought SOL 123.45 USD at 213.78, sol_trend_ensemble. {DISCLOSURE}"

    async def complete(prompt: str) -> str:
        nonlocal calls
        calls += 1
        return valid

    n, rec = make_claude(sim_clock, complete, cost_per_call_usd=Decimal("0.20"))
    assert await n.compose(c) == valid
    assert await n.compose(c) == valid
    assert await n.compose(c) == good_text(c)  # 0.40 + 0.20 > 0.50 -> fallback, no call
    assert calls == 2
    assert any(p.get("reason") == "daily cap exhausted" for _, p in rec.events)
    sim_clock.advance(timedelta(days=1))
    assert await n.compose(c) == valid
    assert calls == 3 and n.spent_today_usd == Decimal("0.20")


def test_claude_narrator_never_imports_anthropic_at_construction(sim_clock) -> None:
    ClaudeNarrator("sk-ant-x", "m", Decimal("1"), TemplateNarrator(), sim_clock)
    assert "anthropic" not in sys.modules


def test_build_prompt_contains_only_context() -> None:
    p = build_prompt(ctx())
    assert "sol_trend_ensemble" in p and DISCLOSURE in p and "213.78" in p
    assert SIG not in p


def test_sim_clock_fixture_is_utc(sim_clock) -> None:
    assert sim_clock.now().tzinfo is UTC
    assert isinstance(sim_clock.now(), datetime)
