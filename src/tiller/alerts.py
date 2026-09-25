"""Structured JSON logging plus optional Telegram alerts (send only).

* ``log(kind, **fields)`` writes one JSON line per event through the ``tiller`` logger.
* ``send(level, text)`` logs the alert and, when a bot token and chat id are configured,
  POSTs it to ``https://api.telegram.org/bot<token>/sendMessage``. It never raises and
  never reads updates: there is deliberately no inbound command handling.
* Everything that leaves the process is passed through :func:`redact`, which masks
  ``fam_`` / ``jup_`` / ``sk-ant-`` keys, Telegram bot tokens and 64-88 char base58 runs
  (private keys and transaction signatures look alike; we prefer to hide signatures over
  leaking a key).

This module imports nothing from the rest of tiller.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

import httpx

Level = Literal["info", "warn", "critical"]
REDACTED = "[REDACTED]"
TELEGRAM_API = "https://api.telegram.org"
TELEGRAM_TEXT_MAX = 4000

_SECRET_PATTERNS = (
    re.compile(r"\bfam_[A-Za-z0-9_\-]{6,}"),
    re.compile(r"\bjup_[A-Za-z0-9_\-]{6,}"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{6,}"),
    re.compile(r"\bown_[A-Za-z0-9_\-]{6,}"),
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{30,}"),  # telegram bot token
    re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{64,88}\b"),  # base58 secret key / signature
    re.compile(r"\[[\s]*(?:\d{1,3}[\s]*,[\s]*){63}\d{1,3}[\s]*\]"),  # 64-int JSON keypair array
)

_LEVEL_TO_LOGGING = {"info": logging.INFO, "warn": logging.WARNING, "critical": logging.CRITICAL}


def redact(text: str) -> str:
    """Mask anything that looks like a key or signature."""
    out = text
    for rx in _SECRET_PATTERNS:
        out = rx.sub(REDACTED, out)
    return out


def _json_default(o: Any) -> Any:
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, datetime):
        return o.isoformat()
    if hasattr(o, "model_dump"):
        return o.model_dump()
    return str(o)


class Alerts:
    """JSON event log + optional Telegram sender. Safe to construct with no Telegram config."""

    def __init__(
        self,
        telegram_token: str | None,
        chat_id: str | None,
        client: httpx.AsyncClient | None,
        *,
        logger: logging.Logger | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        self._token = telegram_token or None
        self._chat_id = chat_id or None
        self._http = client
        self._log = logger or logging.getLogger("tiller")
        self._timeout_s = timeout_s
        self.sent: int = 0
        self.failed: int = 0

    @property
    def telegram_enabled(self) -> bool:
        return bool(self._token and self._chat_id and self._http is not None)

    def log(self, kind: str, **fields: Any) -> None:
        """Emit one redacted JSON line: ``{"ts", "kind", ...fields}``."""
        level = _LEVEL_TO_LOGGING.get(str(fields.pop("level", "info")), logging.INFO)
        payload: dict[str, Any] = {"ts": datetime.now(tz=UTC).isoformat(), "kind": kind, **fields}
        line = redact(json.dumps(payload, default=_json_default, sort_keys=True, separators=(",", ":")))
        self._log.log(level, line)

    async def send(self, level: Level, text: str) -> None:
        """Log the alert and push it to Telegram when configured. Never raises."""
        safe = redact(text)
        self.log("alert", level=level, text=safe)
        if not self.telegram_enabled:
            return
        assert self._http is not None
        body = {
            "chat_id": self._chat_id,
            "text": f"[{level.upper()}] {safe}"[:TELEGRAM_TEXT_MAX],
            "disable_web_page_preview": True,
        }
        try:
            resp = await self._http.post(
                f"{TELEGRAM_API}/bot{self._token}/sendMessage", json=body, timeout=self._timeout_s
            )
            if resp.status_code >= 400:
                self.failed += 1
                self.log("alert.telegram_failed", level="warn", status=resp.status_code, body=resp.text[:200])
                return
            self.sent += 1
        except Exception as e:
            self.failed += 1
            self.log("alert.telegram_failed", level="warn", error=f"{type(e).__name__}: {e}")
