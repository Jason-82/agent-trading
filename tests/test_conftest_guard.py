"""Self-test of the offline guard: unmocked HTTP and raw sockets must fail."""

from __future__ import annotations

import socket

import httpx
import pytest

from conftest import NetworkDisabled


def test_unmocked_httpx_get_raises() -> None:
    with pytest.raises(Exception):  # noqa: B017 - respx raises its own assertion error type
        httpx.get("https://example.invalid/never")


async def test_unmocked_async_httpx_raises() -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(Exception):  # noqa: B017
            await client.get("https://api.jup.ag/never")


def test_raw_socket_connect_raises() -> None:
    with pytest.raises(NetworkDisabled):
        socket.create_connection(("example.invalid", 443), timeout=0.1)
