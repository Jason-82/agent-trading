"""Shared fixtures: no-network guard (autouse), SimClock, tmp ledger/state, fixture loaders."""

from __future__ import annotations

import json
import socket
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import respx

from tiller.backtest.runner import load_csv_candles
from tiller.clock import SimClock
from tiller.ledger import Ledger
from tiller.models import Candle

FIXTURES = Path(__file__).parent / "fixtures"


class NetworkDisabled(RuntimeError):
    """Raised when test code tries to open a real socket."""


def _blocked(*_a: Any, **_k: Any) -> Any:
    raise NetworkDisabled("network access is disabled in tests; mock it with respx or a fake")


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Every test runs offline: unmocked httpx calls fail (respx) and raw sockets refuse to connect."""
    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
    with respx.mock(assert_all_mocked=True, assert_all_called=False):
        yield


@pytest.fixture
def sim_clock() -> SimClock:
    return SimClock(datetime(2026, 9, 24, 0, 5, tzinfo=UTC))


@pytest.fixture
def tmp_ledger(tmp_path: Path, sim_clock: SimClock) -> Iterator[Ledger]:
    ledger = Ledger(tmp_path / "ledger.sqlite", clock=sim_clock)
    yield ledger
    ledger.close()


@pytest.fixture
def tmp_state(tmp_path: Path) -> Path:
    return tmp_path / "state.json"


@pytest.fixture
def load_fixture() -> Callable[[str], Any]:
    """``load_fixture('jupiter/order_ok')`` -> parsed JSON."""

    def _load(name: str) -> Any:
        p = FIXTURES / f"{name}.json"
        with p.open() as f:
            return json.load(f)

    return _load


@pytest.fixture(scope="session")
def sol_daily() -> list[Candle]:
    return load_csv_candles(FIXTURES / "ohlcv" / "SOLUSDT_1d.csv")


@pytest.fixture(scope="session")
def btc_daily() -> list[Candle]:
    return load_csv_candles(FIXTURES / "ohlcv" / "BTCUSDT_1d.csv")


@pytest.fixture(scope="session")
def eth_daily() -> list[Candle]:
    return load_csv_candles(FIXTURES / "ohlcv" / "ETHUSDT_1d.csv")


@pytest.fixture(scope="session")
def study1_expected() -> dict[str, Any]:
    with (FIXTURES / "expected" / "study1_sol.json").open() as f:
        return json.load(f)
