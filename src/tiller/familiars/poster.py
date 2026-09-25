"""Board post queue.

Rules (spec 'familiars.family plan' and 'Posting'):

* Every LIVE fill (``paper=False`` and a signature) is queued as a ``trade`` post with
  ``not_before = fill.ts + delay`` (default 90 s: the board needs time to index the swap).
  Paper fills are never queued.
* The queue lives in ``ledger.posts`` (keyed by swap signature, or a synthetic
  ``note:<day>`` / ``callout:<day>:<n>`` key), so a restart never loses or duplicates a post.
* A send is retried only on HTTP 429 (jittered backoff, ``Retry-After`` honoured).
  A timeout / 5xx marks the post ``uncertain``; before any resend the queue looks the
  signature up in our own public ``GET /api/agents/{handle}.posts`` and, if the board
  already has it, marks it ``posted`` without sending again. An uncertain post is resent
  at most once; if it is still absent afterwards it is marked ``failed`` and alerted.
* ``callout`` posts are capped per UTC day (default 2); one ``note`` per UTC day.
* Posting failures never raise into the trading loop: ``flush`` returns outcomes.

Units: delays in seconds; USD ``Decimal``; times tz-aware UTC.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, Protocol

from tiller.clock import Clock, utc_day_start
from tiller.familiars.client import POST_TEXT_MAX, FamiliarsClient, PostResult
from tiller.familiars.narrator import MAX_POST_CHARS, Narrator, TemplateNarrator, validate_post_text
from tiller.ledger import Ledger
from tiller.models import DomainModel, Fill, PendingPost, TradeContext
from tiller.state import BrakeState

DEFAULT_DELAY_S = 90
DEFAULT_CALLOUTS_PER_DAY = 2
UNCERTAIN_RECHECK_S = 60
MAX_RATE_LIMIT_ATTEMPTS = 6
MAX_UNCERTAIN_SENDS = 2
RATE_LIMIT_BACKOFF_BASE_S = 30.0
RATE_LIMIT_BACKOFF_MAX_S = 900.0

Outcome = Literal["posted", "deferred", "uncertain", "failed", "skipped", "verified"]


class PostOutcome(DomainModel):
    """What ``flush`` did with one queued post."""

    signature: str
    kind: Literal["note", "callout", "trade"]
    outcome: Outcome
    detail: str | None = None


class NoteAccount(Protocol):
    """The slice of ``AccountSnapshot`` (WP-D) the daily note needs; duck-typed on purpose."""

    @property
    def equity_usd(self) -> Decimal: ...

    @property
    def usdc_usd(self) -> Decimal: ...

    @property
    def net_deposits_usd(self) -> Decimal: ...


def is_synthetic_key(signature: str) -> bool:
    """Notes/callouts are keyed ``note:...`` / ``callout:...``; trades by swap signature."""
    return signature.startswith(("note:", "callout:"))


def note_key(day: datetime) -> str:
    return f"note:{utc_day_start(day).date().isoformat()}"


def render_daily_note(acct: NoteAccount, brakes: BrakeState, now: datetime) -> str:
    """Deterministic daily note: equity, P&L vs deposits, exposure, brake state."""
    equity = Decimal(acct.equity_usd)
    pnl = equity - Decimal(acct.net_deposits_usd)
    exposure_pct = Decimal(0)
    if equity > 0:
        exposure_pct = (equity - Decimal(acct.usdc_usd)) / equity * 100
    brake_txt = "none"
    if brakes.entries_blocked:
        brake_txt = "entries blocked (" + ", ".join(brakes.reasons or ["unspecified"]) + ")"
    if brakes.paused_until is not None:
        brake_txt += f"; paused until {brakes.paused_until.isoformat(timespec='minutes')}"
    text = (
        f"Daily note {utc_day_start(now).date().isoformat()}: equity {equity.quantize(Decimal('0.01')):f} USD, "
        f"P&L vs deposits {pnl.quantize(Decimal('0.01')):+f} USD, non-cash exposure "
        f"{exposure_pct.quantize(Decimal('0.1')):f}%, brakes: {brake_txt}. "
        "Rule-based daily trend sleeves on SOL; automated rule-based agent; not financial advice"
    )
    return text[:MAX_POST_CHARS]


class PostQueue:
    """Persistent post queue in front of :class:`FamiliarsClient`."""

    def __init__(
        self,
        client: FamiliarsClient,
        narrator: Narrator,
        ledger: Ledger,
        clock: Clock,
        handle: str,
        *,
        delay_s: float = DEFAULT_DELAY_S,
        callouts_per_day: int = DEFAULT_CALLOUTS_PER_DAY,
        rng: random.Random | None = None,
        on_event: Callable[[str, str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._client = client
        self._narrator = narrator
        self._template = TemplateNarrator()
        self._ledger = ledger
        self._clock = clock
        self.handle = handle
        self.delay = timedelta(seconds=delay_s)
        self.callouts_per_day = callouts_per_day
        self._rng = rng or random.Random()
        self._on_event = on_event
        # Contexts of trades queued in THIS process; after a restart the persisted template
        # text is posted instead (the narrator is best-effort, the queue is not).
        self._contexts: dict[str, TradeContext] = {}

    # ------------------------------------------------------------------ enqueue

    def _event(self, level: str, kind: str, payload: dict[str, Any]) -> None:
        self._ledger.add_event(level, kind, payload)
        if self._on_event is not None:
            self._on_event(level, kind, payload)

    def enqueue_trade(self, fill: Fill, ctx: TradeContext) -> None:
        """Queue a trade post; ignored for paper fills, missing signatures and duplicates."""
        if fill.paper or fill.signature is None or ctx.paper:
            return
        if self._ledger.post(fill.signature) is not None:
            return
        text = self._template.render(ctx)
        mint = fill.out_mint if ctx.side == "buy" else fill.in_mint
        self._contexts[fill.signature] = ctx
        self._ledger.post_upsert(
            fill.signature, "trade", text, "queued", 0, mint=mint, not_before=fill.ts + self.delay
        )

    def callouts_today(self, now: datetime | None = None) -> int:
        day = utc_day_start(now or self._clock.now())
        rows = self._ledger.posts_since(day, kind="callout")
        return sum(1 for r in rows if r.state != "failed")

    def enqueue_callout(self, text: str, mint: str | None = None) -> bool:
        """Queue a callout unless today's cap is reached; returns whether it was queued."""
        now = self._clock.now()
        if self.callouts_today(now) >= self.callouts_per_day:
            self._event("info", "post.callout_capped", {"cap": self.callouts_per_day})
            return False
        if not text.strip():
            return False
        key = f"callout:{utc_day_start(now).date().isoformat()}:{now.timestamp():.0f}:{self._rng.randrange(10**6)}"
        self._ledger.post_upsert(key, "callout", text[:POST_TEXT_MAX], "queued", 0, mint=mint, not_before=now)
        return True

    async def daily_note(self, acct: NoteAccount, brakes: BrakeState) -> None:
        """Queue (once per UTC day) and flush the daily note."""
        now = self._clock.now()
        key = note_key(now)
        if self._ledger.post(key) is None:
            self._ledger.post_upsert(
                key, "note", render_daily_note(acct, brakes, now), "queued", 0, not_before=now
            )
        await self.flush()

    # ------------------------------------------------------------------ flush

    async def verify_posted(self, signature: str) -> bool:
        """True when our own public posts already carry ``signature`` (any exception -> False)."""
        try:
            detail = await self._client.agent(self.handle)
        except Exception as e:
            self._event("warn", "post.verify_failed", {"signature": signature, "error": type(e).__name__})
            return False
        if detail is None:
            return False
        for p in detail.posts:
            if not isinstance(p, dict):
                continue
            for k in ("signature", "txSignature", "tx", "swapSignature"):
                if p.get(k) == signature:
                    return True
        return False

    async def _text_for(self, post: PendingPost) -> str:
        ctx = self._contexts.get(post.signature)
        if ctx is None:
            return post.text
        try:
            text = await self._narrator.compose(ctx)
        except Exception as e:
            self._event("warn", "post.narrator_failed", {"error": type(e).__name__})
            return post.text
        if validate_post_text(text, ctx) is not None:
            return post.text
        return text

    def _rate_limit_delay(self, attempts: int, res: PostResult) -> float:
        if res.retry_after_s is not None:
            return res.retry_after_s + self._rng.uniform(0.0, 5.0)
        base = min(RATE_LIMIT_BACKOFF_MAX_S, RATE_LIMIT_BACKOFF_BASE_S * (2 ** max(0, attempts - 1)))
        return float(base * (1.0 + self._rng.uniform(0.0, 0.5)))

    async def _send(self, post: PendingPost, now: datetime) -> PostOutcome:
        text = await self._text_for(post)
        signature = None if is_synthetic_key(post.signature) else post.signature
        attempts = post.attempts + 1
        try:
            res = await self._client.post(post.kind, text, post.mint, signature)
        except Exception as e:
            self._ledger.post_upsert(post.signature, post.kind, text, "failed", attempts, mint=post.mint)
            self._event(
                "warn", "post.failed", {"signature": post.signature, "error": f"{type(e).__name__}: {e}"}
            )
            return PostOutcome(
                signature=post.signature, kind=post.kind, outcome="failed", detail=type(e).__name__
            )
        if res.status == "ok":
            self._ledger.post_upsert(post.signature, post.kind, text, "posted", attempts, mint=post.mint)
            self._contexts.pop(post.signature, None)
            self._event("info", "post.posted", {"signature": post.signature, "kind": post.kind})
            return PostOutcome(signature=post.signature, kind=post.kind, outcome="posted")
        if res.status == "rate_limited":
            if attempts >= MAX_RATE_LIMIT_ATTEMPTS:
                self._ledger.post_upsert(post.signature, post.kind, text, "failed", attempts, mint=post.mint)
                self._event(
                    "warn",
                    "post.failed",
                    {"signature": post.signature, "reason": "rate limit retries exhausted"},
                )
                return PostOutcome(
                    signature=post.signature, kind=post.kind, outcome="failed", detail="429 exhausted"
                )
            delay = self._rate_limit_delay(attempts, res)
            self._ledger.post_upsert(
                post.signature,
                post.kind,
                text,
                "queued",
                attempts,
                mint=post.mint,
                not_before=now + timedelta(seconds=delay),
            )
            return PostOutcome(
                signature=post.signature, kind=post.kind, outcome="deferred", detail=f"429 wait {delay:.0f}s"
            )
        if res.status == "uncertain":
            self._ledger.post_upsert(
                post.signature,
                post.kind,
                text,
                "uncertain",
                attempts,
                mint=post.mint,
                not_before=now + timedelta(seconds=UNCERTAIN_RECHECK_S),
            )
            self._event("warn", "post.uncertain", {"signature": post.signature, "http": res.http_status})
            return PostOutcome(
                signature=post.signature, kind=post.kind, outcome="uncertain", detail=str(res.http_status)
            )
        self._ledger.post_upsert(post.signature, post.kind, text, "failed", attempts, mint=post.mint)
        self._event(
            "warn", "post.rejected", {"signature": post.signature, "http": res.http_status, "raw": res.raw}
        )
        return PostOutcome(
            signature=post.signature, kind=post.kind, outcome="failed", detail=f"rejected {res.http_status}"
        )

    async def flush(self) -> list[PostOutcome]:
        """Process every due post once; never raises."""
        now = self._clock.now()
        outcomes: list[PostOutcome] = []
        for post in self._ledger.posts_pending(now):
            if post.state == "uncertain":
                if is_synthetic_key(post.signature):
                    # No signature to reconcile against: never resend (a duplicate note is worse).
                    self._ledger.post_upsert(
                        post.signature, post.kind, post.text, "failed", post.attempts, mint=post.mint
                    )
                    self._event(
                        "warn",
                        "post.failed",
                        {"signature": post.signature, "reason": "uncertain, unverifiable"},
                    )
                    outcomes.append(
                        PostOutcome(
                            signature=post.signature, kind=post.kind, outcome="failed", detail="uncertain"
                        )
                    )
                    continue
                if await self.verify_posted(post.signature):
                    self._ledger.post_upsert(
                        post.signature, post.kind, post.text, "posted", post.attempts, mint=post.mint
                    )
                    self._contexts.pop(post.signature, None)
                    outcomes.append(PostOutcome(signature=post.signature, kind=post.kind, outcome="verified"))
                    continue
                if post.attempts >= MAX_UNCERTAIN_SENDS:
                    self._ledger.post_upsert(
                        post.signature, post.kind, post.text, "failed", post.attempts, mint=post.mint
                    )
                    self._event(
                        "warn",
                        "post.failed",
                        {"signature": post.signature, "reason": "uncertain twice, absent on board"},
                    )
                    outcomes.append(
                        PostOutcome(
                            signature=post.signature, kind=post.kind, outcome="failed", detail="uncertain"
                        )
                    )
                    continue
            outcomes.append(await self._send(post, now))
        return outcomes
