"""Per-host rate limiting and 429 backoff shared by every HTTP client in tiller.

* :class:`TokenBucket` spaces calls at ``rps`` requests per second (burst 1 by default).
* :class:`RateLimitedHttp` wraps an ``httpx.AsyncClient``: every request goes through the
  bucket, a 429 is retried with jittered exponential backoff honouring ``Retry-After`` /
  ``x-ratelimit-reset`` headers, and a sliding window of the last ten outcomes feeds the
  error-rate brake. Transport errors and 5xx responses are NOT retried here; callers decide
  (RPC fails over to the next endpoint, the venue resubmits identical bytes).

All sleeps are injectable so tests run instantly with a recording ``sleep``.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

SleepFn = Callable[[float], Awaitable[None]]
MonotonicFn = Callable[[], float]

ERROR_WINDOW = 10


class TokenBucket:
    """Classic token bucket: ``rps`` tokens per second, capacity ``burst``.

    ``acquire`` waits (via the injected ``sleep``) until a token is available. Time is read
    from the injected ``monotonic`` (seconds, float) so tests can drive it deterministically.
    """

    def __init__(
        self,
        rps: float,
        burst: int = 1,
        *,
        monotonic: MonotonicFn = time.monotonic,
        sleep: SleepFn = asyncio.sleep,
    ) -> None:
        if rps <= 0:
            raise ValueError("rps must be positive")
        self.rps = float(rps)
        self.capacity = float(max(1, burst))
        self._tokens = self.capacity
        self._monotonic = monotonic
        self._sleep = sleep
        self._last = monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = self._monotonic()
        elapsed = max(0.0, now - self._last)
        self._last = now
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rps)

    async def acquire(self) -> float:
        """Take one token, sleeping if needed. Returns the seconds waited."""
        async with self._lock:
            self._refill()
            waited = 0.0
            if self._tokens < 1.0:
                need = (1.0 - self._tokens) / self.rps
                await self._sleep(need)
                waited = need
                # After the injected sleep the clock may or may not have moved; credit the
                # token we waited for explicitly so fake clocks behave like real ones.
                self._refill()
                self._tokens = max(self._tokens, 1.0)
            self._tokens -= 1.0
            return waited


class ErrorWindow:
    """Sliding window of the last ``size`` call outcomes (True = error)."""

    def __init__(self, size: int = ERROR_WINDOW) -> None:
        self._events: deque[bool] = deque(maxlen=size)

    def record(self, error: bool) -> None:
        self._events.append(error)

    def mark_last(self, error: bool = True) -> None:
        """Overwrite the outcome of the most recent call (e.g. an RPC-level error in a 200 body)."""
        if self._events:
            self._events[-1] = error
        else:
            self._events.append(error)

    def rate(self) -> float:
        """Fraction of errors over the window (0.0 when no calls were made)."""
        if not self._events:
            return 0.0
        return sum(1 for e in self._events if e) / len(self._events)


def retry_after_seconds(resp: httpx.Response) -> float | None:
    """Seconds to wait as announced by ``Retry-After`` or ``x-ratelimit-reset`` (None if absent)."""
    ra = resp.headers.get("retry-after")
    if ra is not None:
        try:
            return max(0.0, float(ra))
        except ValueError:
            return None
    reset = resp.headers.get("x-ratelimit-reset")
    if reset is not None:
        try:
            v = float(reset)
        except ValueError:
            return None
        # Either a delta in seconds or an absolute unix timestamp.
        if v > 1e9:
            return max(0.0, v - time.time())
        return max(0.0, v)
    return None


class RateLimitedHttp:
    """``httpx.AsyncClient`` wrapper with a token bucket, 429 backoff and an error window."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        rps: float,
        *,
        max_429_retries: int = 4,
        base_backoff_s: float = 1.0,
        max_backoff_s: float = 30.0,
        sleep: SleepFn = asyncio.sleep,
        monotonic: MonotonicFn = time.monotonic,
        rng: random.Random | None = None,
        timeout_s: float = 20.0,
    ) -> None:
        self.client = client
        self.bucket = TokenBucket(rps, monotonic=monotonic, sleep=sleep)
        self.errors = ErrorWindow()
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._max_429 = max_429_retries
        self._base = base_backoff_s
        self._max = max_backoff_s
        self._timeout = timeout_s

    def backoff_delay(self, attempt: int, hint: float | None) -> float:
        """Jittered exponential delay for retry ``attempt`` (0-based), never below the server hint."""
        exp = float(min(self._max, self._base * float(2**attempt)))
        jittered = float(exp * (0.5 + self._rng.random()))
        if hint is not None:
            return float(max(hint, jittered))
        return jittered

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Send one request; retries only on 429. Raises ``httpx.HTTPError`` on transport failure.

        Any 5xx/4xx (other than 429) is returned to the caller unraised but counted as an error.
        """
        kwargs.setdefault("timeout", self._timeout)
        attempt = 0
        while True:
            await self.bucket.acquire()
            try:
                resp = await self.client.request(method, url, **kwargs)
            except httpx.HTTPError:
                self.errors.record(True)
                raise
            if resp.status_code == 429 and attempt < self._max_429:
                delay = self.backoff_delay(attempt, retry_after_seconds(resp))
                attempt += 1
                await self._sleep(delay)
                continue
            self.errors.record(resp.status_code >= 400)
            return resp

    def error_rate(self) -> float:
        return self.errors.rate()
