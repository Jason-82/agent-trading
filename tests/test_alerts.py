"""Alerts: secret redaction in logs and Telegram payloads; sendMessage call shape; never raises."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from decimal import Decimal

import httpx
import pytest
import respx

from tiller.alerts import REDACTED, Alerts, redact

FAM = "fam_abcDEF123456789xyz"
JUP = "jup_0123456789abcdef"
ANT = "sk-ant-api03-abcdefghijklmnop"
OWN = "own_test_11111111111111111111"
TG = "1234567890:AAF0bXyzabcdefghijklmnopqrstuvwxyz12345"
B58_KEY = "3" * 20 + "b8Ky7mNpQrStUvWxYz2A4C5D6E7F8G9HjKmNpQrSt" + "u" * 27  # 88 chars base58
SIG_64 = "9i7GF9gy1MdqeY2TSAjuzhbyxHa4dVkZWy63wAWDLHJEkUULZ1GJUi8eXkquV2N1jHJGQVV4MYQYNx9Czj6zhkJd"[:64]
MINT = "So11111111111111111111111111111111111111112"  # 43 chars: must NOT be redacted
KEYPAIR_JSON = "[" + ",".join(str(i) for i in range(64)) + "]"


@pytest.mark.parametrize("secret", [FAM, JUP, ANT, OWN, TG, B58_KEY, SIG_64, KEYPAIR_JSON])
def test_redact_masks_each_secret_kind(secret: str) -> None:
    out = redact(f"key={secret} end")
    assert secret not in out
    assert REDACTED in out and out.endswith(" end")


def test_redact_keeps_mints_and_plain_text() -> None:
    text = f"bought {MINT} for 12.5 USD; family=ok jupiter=fine"
    assert redact(text) == text


@pytest.fixture
def caplog_json(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.INFO, logger="tiller")
    return caplog


def test_log_emits_redacted_json_line(caplog_json) -> None:
    Alerts(None, None, None).log("order.filled", key=FAM, usd=Decimal("12.50"), nested={"k": JUP})
    assert len(caplog_json.records) == 1
    rec = json.loads(caplog_json.records[0].getMessage())
    assert rec["kind"] == "order.filled" and rec["usd"] == "12.50" and "ts" in rec
    assert rec["key"] == REDACTED and rec["nested"]["k"] == REDACTED
    assert FAM not in caplog_json.text and JUP not in caplog_json.text


def test_log_level_field_maps_to_logging_level(caplog_json) -> None:
    Alerts(None, None, None).log("brake", level="critical", reason="dd30")
    assert caplog_json.records[0].levelno == logging.CRITICAL


@pytest.fixture
def router() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as r:
        yield r


async def test_send_without_telegram_only_logs(caplog_json, router) -> None:
    a = Alerts(None, None, None)
    await a.send("warn", f"limits unreadable key={FAM}")
    assert not a.telegram_enabled and a.sent == 0
    assert FAM not in caplog_json.text and "limits unreadable" in caplog_json.text


async def test_send_telegram_call_shape(router, caplog_json) -> None:
    route = router.post(f"https://api.telegram.org/bot{TG}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    async with httpx.AsyncClient() as http:
        a = Alerts(TG, "987654", http)
        assert a.telegram_enabled
        await a.send("critical", f"flatten triggered; key {JUP} sig {SIG_64}")
    assert route.call_count == 1 and a.sent == 1
    body = json.loads(route.calls[0].request.content)
    assert body["chat_id"] == "987654"
    assert body["text"].startswith("[CRITICAL] flatten triggered")
    assert JUP not in body["text"] and SIG_64 not in body["text"]
    assert body["disable_web_page_preview"] is True
    assert route.calls[0].request.method == "POST"
    # the token in the URL never reaches the log line
    assert TG not in caplog_json.text


async def test_send_never_raises_on_http_error_or_network(router, caplog_json) -> None:
    route = router.post(f"https://api.telegram.org/bot{TG}/sendMessage").mock(
        side_effect=[httpx.Response(401, json={"ok": False}), httpx.ConnectError("down")]
    )
    async with httpx.AsyncClient() as http:
        a = Alerts(TG, "1", http)
        await a.send("info", "one")
        await a.send("info", "two")
    assert route.call_count == 2 and a.failed == 2 and a.sent == 0
    assert "alert.telegram_failed" in caplog_json.text
    assert TG not in caplog_json.text


def test_no_inbound_command_surface() -> None:
    assert not any(n.startswith(("poll", "get_updates", "handle", "receive")) for n in dir(Alerts))
