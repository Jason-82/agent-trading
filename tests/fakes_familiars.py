"""In-process fake of :class:`tiller.familiars.client.FamiliarsClient` for WP-D / WP-E tests.

Same async surface as the real client, no HTTP. State is plain attributes so a test can
mutate the board between ticks (change owner limits, add a leader trade, bump a token's
agent count, script post outcomes).

Usage::

    fam = FakeFamiliars.from_fixtures(load_fixture)
    fam.me_limits = OwnerLimits(max_position_usd=Decimal(50), ..., readable=True, read_at=now)
    fam.post_outcomes.append("rate_limited")     # next post() returns that status
    fam.board_posts.append({"signature": sig})    # verify_posted() will find it
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from tiller.familiars.client import (
    Challenge,
    FamiliarsError,
    FamiliarsHttpError,
    OwnerKeyResponse,
    PostResult,
    ProfilePatch,
    RegisterRequest,
    RegisterResponse,
)
from tiller.models import AgentDetail, OwnerLimits, PublicAgent, TokenInfoBoard

PostStatus = Literal["ok", "rate_limited", "uncertain", "rejected"]


class FakeFamiliars:
    """Scriptable stand-in for FamiliarsClient."""

    def __init__(
        self,
        *,
        handle: str = "tiller_test",
        agents: dict[str, list[dict[str, Any]]] | None = None,
        details: dict[str, dict[str, Any]] | None = None,
        tokens: list[dict[str, Any]] | None = None,
        me: dict[str, Any] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.handle = handle
        self.agents_by_range: dict[str, list[dict[str, Any]]] = agents or {}
        self.details: dict[str, dict[str, Any]] = details or {}
        self.tokens_raw: list[dict[str, Any]] = tokens or []
        self.me_raw: dict[str, Any] | None = me
        self.me_limits: OwnerLimits | None = None  # when set, returned by me() verbatim
        self.me_fail: bool = False  # me() returns readable=False
        self.post_outcomes: list[PostStatus] = []  # consumed FIFO; default 'ok'
        self.posts: list[dict[str, Any]] = []  # every post() call as sent
        self.board_posts: list[dict[str, Any]] = []  # what agent(handle).posts returns
        self.fail_reads: bool = False  # public reads raise FamiliarsHttpError(503)
        self.registered: list[RegisterRequest] = []
        self.calls: list[str] = []
        self._now = now or (lambda: datetime.now(tz=UTC))

    # ------------------------------------------------------------------ construction

    @classmethod
    def from_fixtures(cls, load_fixture: Callable[[str], Any], **kw: Any) -> FakeFamiliars:
        """Populate from ``tests/fixtures/familiars`` via the conftest ``load_fixture`` fixture."""
        detail = load_fixture("familiars/agent_detail")
        fake = cls(
            agents={"7D": load_fixture("familiars/agents_7d"), "30D": load_fixture("familiars/agents_30d")},
            details={detail["agent"]["handle"]: detail},
            tokens=load_fixture("familiars/tokens"),
            me=load_fixture("familiars/me"),
            **kw,
        )
        fake.board_posts = list(detail.get("posts", []))
        return fake

    # ------------------------------------------------------------------ registration

    async def challenge(self, wallet: str) -> Challenge:
        self.calls.append("challenge")
        return Challenge(
            nonce="fake_nonce",
            message=f"sign in as {wallet}",
            expiresAt=int(self._now().timestamp() * 1000) + 300_000,
        )

    async def register(self, req: RegisterRequest) -> RegisterResponse:
        self.calls.append("register")
        self.registered.append(req)
        return RegisterResponse(
            apiKey="fam_fake_key_for_tests_only",
            ownerKey="own_fake",
            loginUrl="https://example.invalid/login",
            handle=req.handle,
        )

    async def owner_key(self) -> OwnerKeyResponse:
        self.calls.append("owner_key")
        return OwnerKeyResponse(ownerKey="own_fake_2", loginUrl="https://example.invalid/login2")

    # ------------------------------------------------------------------ authenticated

    async def me(self) -> OwnerLimits:
        self.calls.append("me")
        now = self._now()
        if self.me_fail:
            return OwnerLimits(readable=False, read_at=now)
        if self.me_limits is not None:
            return self.me_limits.model_copy(update={"read_at": now})
        settings = (self.me_raw or {}).get("settings")
        if not isinstance(settings, dict):
            return OwnerLimits(readable=False, read_at=now)

        def dec(v: Any) -> Decimal | None:
            return None if v in (None, "") else Decimal(str(v))

        return OwnerLimits(
            max_position_usd=dec(settings.get("maxPositionUsd")),
            daily_limit_usd=dec(settings.get("dailyLimitUsd")),
            instructions=settings.get("instructions") or None,
            readable=True,
            read_at=now,
        )

    async def patch_profile(self, patch: ProfilePatch) -> dict[str, Any]:
        self.calls.append("patch_profile")
        if self.me_raw is not None:
            self.me_raw.update(patch.wire())
        return dict(self.me_raw or {})

    async def post(self, kind: str, text: str, mint: str | None, signature: str | None) -> PostResult:
        self.calls.append("post")
        if not 1 <= len(text) <= 500:
            raise ValueError("post text must be 1..500 chars")
        status: PostStatus = self.post_outcomes.pop(0) if self.post_outcomes else "ok"
        record = {"kind": kind, "text": text, "mint": mint, "signature": signature, "status": status}
        self.posts.append(record)
        if status == "ok":
            self.board_posts.append({"kind": kind, "text": text, "mint": mint, "signature": signature})
            return PostResult(status="ok", raw={"id": f"post_{len(self.posts)}"}, http_status=200)
        if status == "rate_limited":
            return PostResult(status="rate_limited", raw={}, http_status=429, retry_after_s=5.0)
        if status == "uncertain":
            return PostResult(status="uncertain", raw={}, http_status=503)
        return PostResult(status="rejected", raw={"error": "bad request"}, http_status=400)

    # ------------------------------------------------------------------ public reads

    def _check_reads(self, path: str) -> None:
        if self.fail_reads:
            raise FamiliarsHttpError(503, "fake outage", path)

    async def agents(self, range: str) -> list[PublicAgent]:
        self.calls.append(f"agents:{range}")
        self._check_reads("/api/agents")
        if range not in self.agents_by_range:
            raise FamiliarsError(f"no fake agents for range {range}")
        return [PublicAgent.model_validate(a) for a in self.agents_by_range[range]]

    async def agent(self, handle: str) -> AgentDetail | None:
        self.calls.append(f"agent:{handle}")
        self._check_reads(f"/api/agents/{handle}")
        raw = self.details.get(handle)
        if raw is None:
            return None
        data = dict(raw)
        if handle == self.handle:
            data["posts"] = list(self.board_posts)
        return AgentDetail.model_validate(data)

    async def tokens(self) -> list[TokenInfoBoard]:
        self.calls.append("tokens")
        self._check_reads("/api/tokens")
        return [TokenInfoBoard.model_validate(t) for t in self.tokens_raw]
