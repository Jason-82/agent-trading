"""Explanation text for board posts.

``TemplateNarrator`` is the default and is fully deterministic. ``ClaudeNarrator`` is
optional: it calls an injected async ``complete(prompt) -> str`` callable (the ``anthropic``
package is imported lazily, only when no callable is injected AND the narrator is actually
used), validates the answer with :func:`validate_post_text` and falls back to the template
on timeout, error, invalid text or an exhausted daily spend cap. Posting therefore never
depends on the LLM.

This module must not import ``tiller.execution`` (import-graph test).

Units: ``notional_usd`` / ``price_usd`` / ``stop_price`` are USD ``Decimal``; the daily cap
is USD; the timeout is seconds.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from datetime import date
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from tiller.clock import Clock
from tiller.models import TradeContext

MAX_POST_CHARS = 400
DISCLOSURE = "automated rule-based agent; not financial advice"
PROMO_PHRASES = ("will", "moon", "target", "x by", "guaranteed")

_URL_RE = re.compile(r"(https?://|www\.|[A-Za-z0-9-]+\.(com|io|xyz|fun|app|net|org|family)(/|\b))", re.I)
_BASE58_RE = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,}")
_DIGITS_RE = re.compile(r"\d+")
_WORD_PHRASES = {p: re.compile(r"\b" + re.escape(p) + r"\b", re.I) for p in PROMO_PHRASES}
# "10x by Friday": the multiplier glues a digit to the x, so no leading word boundary there.
_WORD_PHRASES["x by"] = re.compile(r"(?:\d\s*|\b)x by\b", re.I)

LogFn = Callable[[str, dict[str, Any]], None]
CompleteFn = Callable[[str], Awaitable[str]]


@runtime_checkable
class Narrator(Protocol):
    async def compose(self, ctx: TradeContext) -> str:  # pragma: no cover - protocol
        ...


# --------------------------------------------------------------------------- helpers


def fmt_usd(x: Decimal) -> str:
    """Two-decimal USD rendering without thousands separators (keeps digit runs stable)."""
    return f"{x.quantize(Decimal('0.01')):f}"


def fmt_price(x: Decimal) -> str:
    """Price rendering: 2 dp for >= 1, else up to 8 significant decimals, trailing zeros trimmed."""
    if x >= 1:
        return fmt_usd(x)
    s = f"{x.normalize():f}"
    if "." in s:
        head, tail = s.split(".", 1)
        return f"{head}.{tail[:8]}"
    return s


def _digit_runs(*parts: str) -> set[str]:
    out: set[str] = set()
    for p in parts:
        out.update(_DIGITS_RE.findall(p))
    return out


def context_numbers(ctx: TradeContext) -> set[str]:
    """Digit runs a post may legitimately contain (every rendering of every context number)."""
    parts = [ctx.strategy, ctx.rule, ctx.symbol, ctx.mint, ctx.brake_state]
    for val in (ctx.notional_usd, ctx.price_usd, ctx.stop_price):
        if val is None:
            continue
        parts.extend([str(val), f"{val:f}", fmt_usd(val), fmt_price(val), str(int(val))])
    if ctx.signature:
        parts.append(ctx.signature)
    return _digit_runs(*parts)


def validate_post_text(text: str, ctx: TradeContext) -> str | None:
    """Return ``None`` when ``text`` is acceptable, else the reason it is not.

    Rules: non-empty and <= 400 chars; names ``ctx.strategy``; keeps the disclosure line;
    no URL; no base58 run of 32+ chars other than ``ctx.mint``; every digit run appears in
    the context (anti-hallucinated P&L); no price-target / promotional phrase.
    """
    if not text or not text.strip():
        return "empty text"
    if len(text) > MAX_POST_CHARS:
        return f"too long ({len(text)} > {MAX_POST_CHARS} chars)"
    if ctx.strategy not in text:
        return "missing strategy name"
    if DISCLOSURE not in text:
        return "missing automated-agent disclosure"
    if _URL_RE.search(text):
        return "contains URL"
    stripped = text.replace(ctx.mint, " ")
    if _BASE58_RE.search(stripped):
        return "contains a base58 string other than the traded mint"
    allowed = context_numbers(ctx)
    for run in _DIGITS_RE.findall(stripped):
        if run not in allowed:
            return f"contains a number not in context: {run}"
    for phrase, rx in _WORD_PHRASES.items():
        if rx.search(text):
            return f"contains promotional phrase: {phrase!r}"
    return None


# --------------------------------------------------------------------------- template


class TemplateNarrator:
    """Deterministic explanation: strategy, rule, size, price, stop, regime, brakes, disclosure."""

    def render(self, ctx: TradeContext) -> str:
        verb = "BUY" if ctx.side == "buy" else "SELL"
        parts = [
            f"{verb} {ctx.symbol} {fmt_usd(ctx.notional_usd)} USD at {fmt_price(ctx.price_usd)}",
            f"strategy {ctx.strategy}",
            f"rule: {ctx.rule}",
        ]
        if ctx.stop_price is not None:
            parts.append(f"stop {fmt_price(ctx.stop_price)}")
        if ctx.regime_on is not None:
            parts.append("regime " + ("on" if ctx.regime_on else "off"))
        parts.append(f"brakes: {ctx.brake_state}")
        if ctx.paper:
            parts.append("paper")
        parts.append(DISCLOSURE)
        text = " | ".join(parts)
        if len(text) > MAX_POST_CHARS:
            # Shorten the free-text rule first; everything else is fixed-width.
            overflow = len(text) - MAX_POST_CHARS
            rule = ctx.rule[: max(0, len(ctx.rule) - overflow - 1)] + "~"
            parts[2] = f"rule: {rule}"
            text = " | ".join(parts)
        return text[:MAX_POST_CHARS]

    async def compose(self, ctx: TradeContext) -> str:
        return self.render(ctx)


# --------------------------------------------------------------------------- claude (optional)


def build_prompt(ctx: TradeContext) -> str:
    """Prompt built ONLY from our own numbers and enums; never third-party text."""
    lines = [
        "Write one plain-text explanation (max 350 characters, no URLs, no emojis, no hype,",
        "no price predictions, no words like will/moon/target/guaranteed) of this automated trade.",
        f"You must include the exact strategy name '{ctx.strategy}' and end with the sentence",
        f"'{DISCLOSURE}'. Use only the numbers given below, formatted exactly as given.",
        f"side={ctx.side} symbol={ctx.symbol} notional_usd={fmt_usd(ctx.notional_usd)}",
        f"price_usd={fmt_price(ctx.price_usd)} rule={ctx.rule}",
        f"stop={'none' if ctx.stop_price is None else fmt_price(ctx.stop_price)}",
        f"regime={'unknown' if ctx.regime_on is None else ('on' if ctx.regime_on else 'off')}",
        f"brakes={ctx.brake_state} paper={'yes' if ctx.paper else 'no'}",
    ]
    return "\n".join(lines)


class ClaudeNarrator:
    """LLM narrator with validation, timeout, daily USD cap and template fallback.

    ``complete`` is an async ``prompt -> text`` callable. When omitted, the ``anthropic``
    package is imported lazily on first use (never at module import). ``cost_per_call_usd``
    is a conservative flat estimate charged against ``daily_cap_usd`` per attempt.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        daily_cap_usd: Decimal,
        fallback: Narrator,
        clock: Clock,
        *,
        complete: CompleteFn | None = None,
        timeout_s: float = 5.0,
        cost_per_call_usd: Decimal = Decimal("0.005"),
        log: LogFn | None = None,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self.daily_cap_usd = Decimal(daily_cap_usd)
        self._fallback = fallback
        self._clock = clock
        self._complete = complete
        self.timeout_s = timeout_s
        self.cost_per_call_usd = Decimal(cost_per_call_usd)
        self._log = log or (lambda _kind, _payload: None)
        self._spent_usd = Decimal(0)
        self._spent_day: date | None = None

    @property
    def spent_today_usd(self) -> Decimal:
        self._roll_day()
        return self._spent_usd

    def _roll_day(self) -> None:
        today = self._clock.now().date()
        if self._spent_day != today:
            self._spent_day = today
            self._spent_usd = Decimal(0)

    def _cap_exhausted(self) -> bool:
        self._roll_day()
        return self._spent_usd + self.cost_per_call_usd > self.daily_cap_usd

    async def _default_complete(self, prompt: str) -> str:
        import anthropic  # type: ignore[import-not-found]  # lazy: only when narrator=claude and nothing injected

        client = anthropic.AsyncAnthropic(api_key=self._api_key)
        msg = await client.messages.create(
            model=self.model, max_tokens=300, messages=[{"role": "user", "content": prompt}]
        )
        chunks = [getattr(b, "text", "") for b in msg.content]
        return "".join(chunks).strip()

    async def compose(self, ctx: TradeContext) -> str:
        if self._cap_exhausted():
            self._log(
                "narrator.fallback", {"reason": "daily cap exhausted", "spent_usd": str(self._spent_usd)}
            )
            return await self._fallback.compose(ctx)
        prompt = build_prompt(ctx)
        complete = self._complete or self._default_complete
        self._spent_usd += self.cost_per_call_usd
        self._log("narrator.call", {"model": self.model, "prompt": prompt})
        try:
            text = await asyncio.wait_for(complete(prompt), timeout=self.timeout_s)
        except TimeoutError:
            self._log("narrator.fallback", {"reason": "timeout"})
            return await self._fallback.compose(ctx)
        except Exception as e:
            self._log("narrator.fallback", {"reason": f"error: {type(e).__name__}"})
            return await self._fallback.compose(ctx)
        text = (text or "").strip()
        self._log("narrator.response", {"text": text})
        reason = validate_post_text(text, ctx)
        if reason is not None:
            self._log("narrator.fallback", {"reason": f"invalid: {reason}"})
            return await self._fallback.compose(ctx)
        return text
