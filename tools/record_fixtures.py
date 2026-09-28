#!/usr/bin/env python3
"""ONLINE fixture recorder: run this from a networked machine, never from the agent container.

It captures the public API responses tiller parses, redacts anything key-like, and writes them
to ``tests/fixtures/recorded/`` so ``tests/test_fixture_schema.py`` can prove the synthetic
fixtures and the live shapes parse into the same pydantic models.

    python tools/record_fixtures.py [--wallet PUBKEY] [--rpc-url URL] [--handle HANDLE] [--out DIR]

Environment (all optional): ``TILLER_JUPITER_API_KEY`` (Jupiter free tier), ``TILLER_FAMILIARS_API_KEY``
(records ``/api/agent/me``, redacted), ``TILLER_RPC_URL`` (defaults to the public mainnet endpoint).

Captured: Kraken OHLC SOLUSD + XBTUSD and Ticker SOLUSD; Coinbase SOL-USD daily candles; Jupiter
Swap V2 ``/order`` for $10 USDC -> SOL WITHOUT a taker (quote only, no transaction), Price V3 for
SOL + USDC, Tokens V2 search for SOL, Shield for SOL; familiars ``/api/agents?range=7D``,
``/api/tokens``, one ``/api/agents/{handle}``, ``/api/agent/me`` when a key is present; Solana RPC
``getAccountInfo`` for the SOL mint and ``getTokenAccountsByOwner`` for ``--wallet``.

Redaction: query-string credentials, ``x-api-key`` headers, ``fam_``/``jup_``/``own_`` tokens, base58
runs of 64+ chars (signatures and keys look alike), 64-int keypair arrays and the familiars secret
keys (``apiKey``, ``ownerKey``, ``loginUrl``) are all masked before anything touches disk. Nothing
here signs, sends or writes to any API.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tiller.alerts import redact  # noqa: E402
from tiller.familiars.client import redact_payload  # noqa: E402
from tiller.models import SOL_MINT, TOKEN_PROGRAM, USDC_MINT  # noqa: E402

DEFAULT_OUT = ROOT / "tests" / "fixtures" / "recorded"
JUP = "https://api.jup.ag"
KRAKEN = "https://api.kraken.com"
COINBASE = "https://api.exchange.coinbase.com"
FAMILIARS = "https://familiars.family"
PUBLIC_RPC = "https://api.mainnet-beta.solana.com"
_QUERY_SECRET = re.compile(r"(?i)([?&](?:api[-_]?key|apikey|token|key|secret)=)[^&\s]+")


def _redact_url(url: str) -> str:
    return _QUERY_SECRET.sub(lambda m: m.group(1) + "[REDACTED]", url)


def _scrub(body: Any) -> Any:
    """Key-name redaction, then pattern redaction over the serialised text."""
    text = json.dumps(redact_payload(body), separators=(",", ":"), default=str)
    return json.loads(redact(text))


class Recorder:
    def __init__(self, out: Path, client: httpx.Client) -> None:
        self.out = out
        self.client = client
        self.written: list[str] = []
        self.failed: list[str] = []

    def capture(
        self,
        name: str,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        try:
            resp = self.client.request(method, url, params=params, json=json_body, headers=headers)
        except httpx.HTTPError as e:
            self.failed.append(f"{name}: {type(e).__name__}")
            print(f"  {name}: FAILED ({type(e).__name__})")
            return None
        try:
            body: Any = resp.json()
        except ValueError:
            body = resp.text[:20000]
        record = {
            "_meta": {
                "synthetic": False,
                "recorded_at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
                "method": method,
                "url": _redact_url(str(resp.request.url)),
                "status": resp.status_code,
                "note": "recorded by tools/record_fixtures.py; redacted before writing",
            },
            "body": _scrub(body),
        }
        path = self.out / f"{name}.json"
        path.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        self.written.append(name)
        print(f"  {name}: HTTP {resp.status_code} -> {path.relative_to(ROOT)}")
        return body


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument(
        "--wallet", default=os.environ.get("TILLER_RECORD_WALLET"), help="pubkey for getTokenAccountsByOwner"
    )
    ap.add_argument("--rpc-url", default=os.environ.get("TILLER_RPC_URL", PUBLIC_RPC))
    ap.add_argument(
        "--handle", default=None, help="familiars handle for /api/agents/{handle} (default: first listed)"
    )
    ap.add_argument("--timeout", type=float, default=20.0)
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    jup_key = os.environ.get("TILLER_JUPITER_API_KEY")
    fam_key = os.environ.get("TILLER_FAMILIARS_API_KEY")
    jup_headers = {"accept": "application/json", **({"x-api-key": jup_key} if jup_key else {})}

    with httpx.Client(timeout=args.timeout) as client:
        rec = Recorder(out, client)
        print("kraken / coinbase (public)")
        rec.capture(
            "kraken_ohlc_solusd",
            "GET",
            f"{KRAKEN}/0/public/OHLC",
            params={"pair": "SOLUSD", "interval": "1440"},
        )
        rec.capture(
            "kraken_ohlc_xbtusd",
            "GET",
            f"{KRAKEN}/0/public/OHLC",
            params={"pair": "XBTUSD", "interval": "1440"},
        )
        rec.capture("kraken_ticker_solusd", "GET", f"{KRAKEN}/0/public/Ticker", params={"pair": "SOLUSD"})
        rec.capture(
            "coinbase_candles_solusd",
            "GET",
            f"{COINBASE}/products/SOL-USD/candles",
            params={"granularity": "86400"},
        )
        print("jupiter (quote only, no taker, nothing is signed)")
        rec.capture(
            "jupiter_order_sol_usdc",
            "GET",
            f"{JUP}/swap/v2/order",
            params={
                "inputMint": USDC_MINT,
                "outputMint": SOL_MINT,
                "amount": "10000000",
                "slippageBps": "50",
            },
            headers=jup_headers,
        )
        rec.capture(
            "jupiter_price_v3",
            "GET",
            f"{JUP}/price/v3",
            params={"ids": f"{SOL_MINT},{USDC_MINT}"},
            headers=jup_headers,
        )
        rec.capture(
            "jupiter_tokens_v2_sol",
            "GET",
            f"{JUP}/tokens/v2/search",
            params={"query": SOL_MINT},
            headers=jup_headers,
        )
        rec.capture(
            "jupiter_shield_sol",
            "GET",
            f"{JUP}/ultra/v1/shield",
            params={"mints": SOL_MINT},
            headers=jup_headers,
        )
        print("familiars (public)")
        agents = rec.capture("familiars_agents_7d", "GET", f"{FAMILIARS}/api/agents", params={"range": "7D"})
        rec.capture("familiars_tokens", "GET", f"{FAMILIARS}/api/tokens")
        handle = args.handle
        if handle is None and isinstance(agents, list) and agents and isinstance(agents[0], dict):
            handle = str(agents[0].get("handle") or "")
        if handle is None and isinstance(agents, dict):
            rows = agents.get("agents") or []
            handle = str(rows[0].get("handle")) if rows and isinstance(rows[0], dict) else None
        if handle:
            rec.capture("familiars_agent_detail", "GET", f"{FAMILIARS}/api/agents/{handle}")
        if fam_key:
            print("familiars (authenticated: /api/agent/me, redacted)")
            rec.capture(
                "familiars_me",
                "GET",
                f"{FAMILIARS}/api/agent/me",
                headers={"Authorization": f"Bearer {fam_key}"},
            )
        else:
            print("familiars /api/agent/me skipped (TILLER_FAMILIARS_API_KEY not set)")
        print("solana rpc (read only)")
        rec.capture(
            "rpc_account_info_sol_mint",
            "POST",
            args.rpc_url,
            json_body={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [SOL_MINT, {"encoding": "jsonParsed", "commitment": "confirmed"}],
            },
        )
        if args.wallet:
            rec.capture(
                "rpc_token_accounts_by_owner",
                "POST",
                args.rpc_url,
                json_body={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "getTokenAccountsByOwner",
                    "params": [
                        args.wallet,
                        {"programId": TOKEN_PROGRAM},
                        {"encoding": "jsonParsed", "commitment": "confirmed"},
                    ],
                },
            )
        else:
            print("getTokenAccountsByOwner skipped (pass --wallet PUBKEY)")
    print(f"wrote {len(rec.written)} fixture(s) to {out}; failures: {rec.failed or 'none'}")
    print("review the files before committing: everything key-like is masked, but read them anyway")
    return 1 if rec.failed and not rec.written else 0


if __name__ == "__main__":
    sys.exit(main())
