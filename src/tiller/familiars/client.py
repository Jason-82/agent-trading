"""Typed familiars.family client (docs/familiars-api.md).

Design rules
------------
* One token bucket per client (default 1 request/s) because the board's rate limits are
  undocumented; ``429`` on a *read* is retried with jittered exponential backoff honouring
  ``Retry-After`` / ``x-ratelimit-reset`` when present.
* Every response body is persisted VERBATIM to ``ledger.raw_snapshots`` BEFORE parsing, so
  schema drift never loses the dataset. Responses that carry keys (register, owner-key)
  are persisted with those values redacted.
* ``register`` and ``owner_key`` are sent exactly once: any failure propagates and is
  never retried (single-use nonce; keys are shown once).
* ``me()`` never raises: an unreadable ``/api/agent/me`` yields ``readable=False`` so the
  caller fails closed on entries.
* ``post()`` never retries by itself: it reports ``ok`` / ``rate_limited`` / ``uncertain``
  / ``rejected`` and the :mod:`tiller.familiars.poster` queue decides what to do.

Units: ``expiresAt`` from the board is unix **milliseconds**; ``Retry-After`` is seconds;
USD amounts are ``Decimal``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import random
import re
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

import httpx
from pydantic import Field, field_validator

from tiller.clock import Clock
from tiller.ledger import Ledger
from tiller.models import (
    AgentDetail,
    DomainModel,
    OwnerLimits,
    PublicAgent,
    TokenInfoBoard,
    WireModel,
)

SOURCE = "familiars"
HANDLE_RE = re.compile(r"^[a-z0-9_]{3,20}$")
Color = Literal["lilac", "mint", "yellow", "orange", "cyan", "rose", "teal", "hero"]
PostKind = Literal["note", "callout", "trade"]
AgentsRange = Literal["24H", "7D", "30D", "ALL"]
PostStatus = Literal["ok", "rate_limited", "uncertain", "rejected"]

POST_TEXT_MIN = 1
POST_TEXT_MAX = 500
MAX_BACKOFF_S = 60.0
SECRET_KEYS = frozenset({"apiKey", "ownerKey", "loginUrl", "api_key", "owner_key", "login_url"})


# --------------------------------------------------------------------------- errors


class FamiliarsError(Exception):
    """Base class for client failures."""


class FamiliarsHttpError(FamiliarsError):
    """Non-2xx response (after any permitted retries)."""

    def __init__(self, status: int, body: str, path: str) -> None:
        super().__init__(f"familiars {path} -> HTTP {status}")
        self.status = status
        self.body = body
        self.path = path


class FamiliarsRateLimited(FamiliarsHttpError):
    """HTTP 429 that survived the retry budget."""


class FamiliarsUnavailable(FamiliarsError):
    """Network error or timeout: the request may or may not have reached the server."""


# --------------------------------------------------------------------------- wire models


class Challenge(WireModel):
    """``POST /api/agents/challenge`` response. ``expires_at`` is converted from unix ms."""

    nonce: str
    message: str
    expires_at: datetime = Field(alias="expiresAt")

    @field_validator("expires_at", mode="before")
    @classmethod
    def _ms(cls, v: Any) -> Any:
        if isinstance(v, int | float):
            secs = v / 1000.0 if v > 1e11 else float(v)
            return datetime.fromtimestamp(secs, tz=UTC)
        return v


class RegisterRequest(DomainModel):
    """Body of ``POST /api/agents/register``; ``signature_b64`` is the base64 ed25519 signature."""

    wallet: str
    nonce: str
    signature_b64: str
    handle: str
    name: str = Field(min_length=1, max_length=32)
    bio: str | None = Field(default=None, max_length=280)
    strategy: str | None = Field(default=None, max_length=40)
    color: Color | None = None
    twitter: str | None = None

    @field_validator("handle")
    @classmethod
    def _handle(cls, v: str) -> str:
        if not HANDLE_RE.match(v):
            raise ValueError("handle must match ^[a-z0-9_]{3,20}$")
        return v

    @field_validator("signature_b64")
    @classmethod
    def _sig(cls, v: str) -> str:
        try:
            raw = base64.b64decode(v, validate=True)
        except Exception as e:
            raise ValueError("signature_b64 is not valid base64") from e
        if len(raw) != 64:
            raise ValueError("ed25519 signature must decode to 64 bytes")
        return v

    def wire(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "wallet": self.wallet,
            "nonce": self.nonce,
            "signature": self.signature_b64,
            "handle": self.handle,
            "name": self.name,
        }
        for k in ("bio", "strategy", "color", "twitter"):
            v = getattr(self, k)
            if v is not None:
                body[k] = v
        return body


class RegisterResponse(WireModel):
    """``POST /api/agents/register`` response; ``api_key`` is the ``fam_`` bearer key."""

    api_key: str = Field(alias="apiKey")
    owner_key: str | None = Field(default=None, alias="ownerKey")
    login_url: str | None = Field(default=None, alias="loginUrl")
    handle: str

    @classmethod
    def from_wire(cls, payload: dict[str, Any]) -> RegisterResponse:
        data = dict(payload)
        agent = data.get("agent")
        if "handle" not in data and isinstance(agent, dict):
            data["handle"] = agent.get("handle")
        return cls.model_validate(data)


class OwnerKeyResponse(WireModel):
    owner_key: str = Field(alias="ownerKey")
    login_url: str | None = Field(default=None, alias="loginUrl")


class OwnerSettings(WireModel):
    """``settings`` block of ``GET /api/agent/me``; missing/null caps mean 'unset'."""

    instructions: str | None = None
    max_position_usd: Decimal | None = Field(default=None, alias="maxPositionUsd")
    daily_limit_usd: Decimal | None = Field(default=None, alias="dailyLimitUsd")

    @field_validator("instructions", mode="before")
    @classmethod
    def _str(cls, v: Any) -> Any:
        return None if v in (None, "") else str(v)


class MeResponse(WireModel):
    """``GET /api/agent/me``: profile plus owner ``settings`` (required: drift fails loudly)."""

    handle: str
    settings: OwnerSettings


class PostResult(DomainModel):
    """Outcome of one ``POST /api/posts`` attempt. ``retry_after_s`` only for ``rate_limited``."""

    status: PostStatus
    raw: dict[str, Any] = Field(default_factory=dict)
    http_status: int | None = None
    retry_after_s: float | None = None


class ProfilePatch(DomainModel):
    """Editable profile fields for ``PATCH /api/agent/me``; unset fields are not sent."""

    bio: str | None = Field(default=None, max_length=280)
    strategy: str | None = Field(default=None, max_length=40)
    name: str | None = Field(default=None, min_length=1, max_length=32)
    color: Color | None = None
    twitter: str | None = None

    def wire(self) -> dict[str, Any]:
        return {k: v for k, v in self.model_dump().items() if v is not None}


def encode_signature(signature: bytes) -> str:
    """Base64 (standard alphabet, padded) of a 64-byte ed25519 signature, as the board expects."""
    if len(signature) != 64:
        raise ValueError("ed25519 signature must be 64 bytes")
    return base64.b64encode(signature).decode("ascii")


def redact_payload(payload: Any) -> Any:
    """Return a copy of ``payload`` with key-bearing fields replaced by ``[REDACTED]``."""
    if isinstance(payload, dict):
        return {k: ("[REDACTED]" if k in SECRET_KEYS else redact_payload(v)) for k, v in payload.items()}
    if isinstance(payload, list):
        return [redact_payload(v) for v in payload]
    return payload


# --------------------------------------------------------------------------- rate limiting


class RateLimiter:
    """Minimum-spacing limiter: at most ``rps`` calls per second, measured on ``time_fn``."""

    def __init__(
        self,
        rps: float,
        time_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if rps <= 0:
            raise ValueError("rps must be positive")
        self.interval = 1.0 / rps
        self._time = time_fn
        self._sleep = sleep_fn
        self._next_at: float | None = None
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = self._time()
            if self._next_at is None or self._next_at <= now:
                self._next_at = now + self.interval
                return
            wait = self._next_at - now
            self._next_at += self.interval
        await self._sleep(wait)


def retry_after_seconds(headers: httpx.Headers, now_unix: float) -> float | None:
    """Wait implied by ``Retry-After`` (seconds) or ``x-ratelimit-reset`` (unix s/ms or delta s).

    Returns ``None`` when neither header is usable; the result is clamped to ``[0, 60]``.
    """
    ra = headers.get("retry-after")
    if ra is not None:
        try:
            return max(0.0, min(MAX_BACKOFF_S, float(ra)))
        except ValueError:
            pass
    reset = headers.get("x-ratelimit-reset")
    if reset is not None:
        try:
            val = float(reset)
        except ValueError:
            return None
        if val > 1e12:  # unix milliseconds
            val = val / 1000.0 - now_unix
        elif val > 1e9:  # unix seconds
            val = val - now_unix
        return max(0.0, min(MAX_BACKOFF_S, val))
    return None


# --------------------------------------------------------------------------- client


class FamiliarsClient:
    """Async client over an injected ``httpx.AsyncClient``.

    ``api_key`` is the ``fam_`` bearer key (``None`` for public reads only). ``sleep_fn`` /
    ``time_fn`` / ``rng`` are injectable so tests can run the backoff instantly.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        rps: float,
        client: httpx.AsyncClient,
        ledger: Ledger,
        clock: Clock,
        *,
        max_429_retries: int = 3,
        backoff_base_s: float = 1.0,
        timeout_s: float = 10.0,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
        time_fn: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key or None
        self._http = client
        self._ledger = ledger
        self._clock = clock
        self._limiter = RateLimiter(rps, time_fn=time_fn, sleep_fn=sleep_fn)
        self._sleep = sleep_fn
        self._max_429_retries = max_429_retries
        self._backoff_base_s = backoff_base_s
        self._timeout_s = timeout_s
        self._rng = rng or random.Random()

    # ------------------------------------------------------------------ transport

    def _headers(self, auth: bool) -> dict[str, str]:
        h = {"accept": "application/json"}
        if auth:
            if not self._api_key:
                raise FamiliarsError("this endpoint needs a familiars api key")
            h["authorization"] = f"Bearer {self._api_key}"
        return h

    def _persist(self, method: str, path: str, status: int | None, body: str, redact: bool = False) -> None:
        text = body
        if redact:
            try:
                text = json.dumps(redact_payload(json.loads(body)), separators=(",", ":"))
            except (ValueError, TypeError):
                text = "[REDACTED: unparseable key-bearing response]"
        key = f"{method} {path} [{status if status is not None else 'error'}]"
        self._ledger.add_raw_snapshot(self._clock.now(), SOURCE, key, text)

    async def _send(
        self,
        method: str,
        path: str,
        *,
        auth: bool,
        json_body: dict[str, Any] | None = None,
        redact: bool = False,
    ) -> httpx.Response:
        """One HTTP attempt (rate limited, persisted). Network errors -> ``FamiliarsUnavailable``."""
        await self._limiter.acquire()
        try:
            resp = await self._http.request(
                method,
                self.base_url + path,
                json=json_body,
                headers=self._headers(auth),
                timeout=self._timeout_s,
            )
        except httpx.HTTPError as e:
            self._persist(method, path, None, f"{type(e).__name__}: {e}")
            raise FamiliarsUnavailable(f"familiars {path}: {type(e).__name__}") from e
        self._persist(method, path, resp.status_code, resp.text, redact=redact)
        return resp

    def _backoff(self, attempt: int, headers: httpx.Headers) -> float:
        hinted = retry_after_seconds(headers, self._clock.now().timestamp())
        if hinted is not None:
            return hinted + self._rng.uniform(0.0, 0.5)
        base = self._backoff_base_s * (2**attempt)
        return float(min(MAX_BACKOFF_S, base * (1.0 + self._rng.uniform(0.0, 0.5))))

    async def _read(self, path: str, *, auth: bool = False) -> httpx.Response:
        """GET with 429 backoff (reads only); raises on any final non-2xx except 404."""
        attempt = 0
        while True:
            resp = await self._send("GET", path, auth=auth)
            if resp.status_code == 429 and attempt < self._max_429_retries:
                await self._sleep(self._backoff(attempt, resp.headers))
                attempt += 1
                continue
            if resp.status_code == 429:
                raise FamiliarsRateLimited(429, resp.text, path)
            if resp.status_code == 404:
                return resp
            if resp.status_code >= 400:
                raise FamiliarsHttpError(resp.status_code, resp.text, path)
            return resp

    @staticmethod
    def _json(resp: httpx.Response, path: str) -> Any:
        try:
            return resp.json()
        except ValueError as e:
            raise FamiliarsError(f"familiars {path}: response is not JSON") from e

    # ------------------------------------------------------------------ registration

    async def challenge(self, wallet: str) -> Challenge:
        resp = await self._send("POST", "/api/agents/challenge", auth=False, json_body={"wallet": wallet})
        if resp.status_code >= 400:
            raise FamiliarsHttpError(resp.status_code, resp.text, "/api/agents/challenge")
        return Challenge.model_validate(self._json(resp, "/api/agents/challenge"))

    async def register(self, req: RegisterRequest) -> RegisterResponse:
        """Exactly ONE attempt; every failure propagates and must never be retried by callers."""
        path = "/api/agents/register"
        resp = await self._send("POST", path, auth=False, json_body=req.wire(), redact=True)
        if resp.status_code >= 400:
            raise FamiliarsHttpError(resp.status_code, resp.text, path)
        return RegisterResponse.from_wire(self._json(resp, path))

    async def owner_key(self) -> OwnerKeyResponse:
        """Issue a new owner key (invalidates the old one). Single attempt, never retried."""
        path = "/api/agent/owner-key"
        resp = await self._send("POST", path, auth=True, redact=True)
        if resp.status_code >= 400:
            raise FamiliarsHttpError(resp.status_code, resp.text, path)
        return OwnerKeyResponse.model_validate(self._json(resp, path))

    # ------------------------------------------------------------------ authenticated

    async def me(self) -> OwnerLimits:
        """Owner limits; ``readable=False`` on ANY failure (transport, status, shape)."""
        now = self._clock.now()
        try:
            resp = await self._read("/api/agent/me", auth=True)
            if resp.status_code != 200:
                raise FamiliarsHttpError(resp.status_code, resp.text, "/api/agent/me")
            me = MeResponse.model_validate(self._json(resp, "/api/agent/me"))
        except Exception as e:
            self._ledger.add_event("warn", "familiars.me_unreadable", {"error": f"{type(e).__name__}: {e}"})
            return OwnerLimits(readable=False, read_at=now)
        return OwnerLimits(
            max_position_usd=me.settings.max_position_usd,
            daily_limit_usd=me.settings.daily_limit_usd,
            instructions=me.settings.instructions,
            readable=True,
            read_at=now,
        )

    async def patch_profile(self, patch: ProfilePatch) -> dict[str, Any]:
        path = "/api/agent/me"
        body = patch.wire()
        if not body:
            raise ValueError("profile patch is empty")
        resp = await self._send("PATCH", path, auth=True, json_body=body)
        if resp.status_code >= 400:
            raise FamiliarsHttpError(resp.status_code, resp.text, path)
        data = self._json(resp, path)
        return data if isinstance(data, dict) else {"result": data}

    async def post(self, kind: PostKind, text: str, mint: str | None, signature: str | None) -> PostResult:
        """One ``POST /api/posts`` attempt (no retry here). Text must be 1..500 chars."""
        if not (POST_TEXT_MIN <= len(text) <= POST_TEXT_MAX):
            raise ValueError(f"post text must be {POST_TEXT_MIN}..{POST_TEXT_MAX} chars, got {len(text)}")
        body: dict[str, Any] = {"kind": kind, "text": text}
        if mint is not None:
            body["mint"] = mint
        if signature is not None:
            body["signature"] = signature
        try:
            resp = await self._send("POST", "/api/posts", auth=True, json_body=body)
        except FamiliarsUnavailable as e:
            return PostResult(status="uncertain", raw={"error": str(e)})
        raw: dict[str, Any]
        try:
            parsed = resp.json()
            raw = parsed if isinstance(parsed, dict) else {"result": parsed}
        except ValueError:
            raw = {"body": resp.text[:500]}
        code = resp.status_code
        if 200 <= code < 300:
            return PostResult(status="ok", raw=raw, http_status=code)
        if code == 429:
            wait = retry_after_seconds(resp.headers, self._clock.now().timestamp())
            if wait is None and isinstance(raw.get("retryAfterMs"), int | float):
                wait = max(0.0, min(MAX_BACKOFF_S, float(raw["retryAfterMs"]) / 1000.0))
            return PostResult(status="rate_limited", raw=raw, http_status=code, retry_after_s=wait)
        if code >= 500:
            return PostResult(status="uncertain", raw=raw, http_status=code)
        return PostResult(status="rejected", raw=raw, http_status=code)

    # ------------------------------------------------------------------ public reads

    async def agents(self, range: AgentsRange) -> list[PublicAgent]:
        path = f"/api/agents?range={range}"
        resp = await self._read(path)
        if resp.status_code == 404:
            raise FamiliarsHttpError(404, resp.text, path)
        data = self._json(resp, path)
        rows = data.get("agents") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            raise FamiliarsError(f"familiars {path}: expected a list")
        return [PublicAgent.model_validate(r) for r in rows]

    async def agent(self, handle: str) -> AgentDetail | None:
        """Agent detail, or ``None`` when the handle is free (404)."""
        if not HANDLE_RE.match(handle):
            raise ValueError("invalid handle")
        path = f"/api/agents/{handle}"
        resp = await self._read(path)
        if resp.status_code == 404:
            return None
        return AgentDetail.model_validate(self._json(resp, path))

    async def tokens(self) -> list[TokenInfoBoard]:
        path = "/api/tokens"
        resp = await self._read(path)
        if resp.status_code == 404:
            raise FamiliarsHttpError(404, resp.text, path)
        data = self._json(resp, path)
        rows = data.get("tokens") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            raise FamiliarsError(f"familiars {path}: expected a list")
        return [TokenInfoBoard.model_validate(r) for r in rows]
