# Test fixtures

**synthetic: true** for every JSON file under `jupiter/`, `rpc/`, `kraken/` and `coinbase/`.
They were hand-built to the documented API shapes (Jupiter Swap V2 `/order` + `/execute`,
Price V3, Tokens V2 search, Shield, Solana JSON-RPC, Kraken OHLC/Ticker, Coinbase candles)
by `scratchpad/build_fixtures.py`. Nothing in them was recorded from a live API, no real key
or wallet appears, and every address is a valid base58 32-byte pubkey derived from fixed
seeds (`tests/fixtures/identities.json`):

| name | value | how |
|---|---|---|
| wallet | `FAe4sisG95oZ42w7buUn5qEE4TAnfTTFPiguZUHmhiF` | `Keypair.from_seed(bytes(range(32)))` |
| foreign | `GmaDrppBC7P5ARKV8g3djiwP89vz1jLK23V2GBjuAEGB` | `Keypair.from_seed(bytes([7]*32))` |
| token_x (SPL, "good") | `J2xccRtuG43drESLYznHhLhQkLTdfepcKYbiQ9BsJVaf` | seed `[9]*32` |
| token_y (Token-2022) | `5Z6Ay5NEcbg3xhopc522sBCRXQujkTiuDRnHGfQdcnSf` | seed `[10]*32` |
| ATAs | see identities.json | `guard.derive_ata(wallet, mint, program)` |

The transactions inside `jupiter/order_*.json` are real serialised `VersionedTransaction`s
(legacy message, fee payer = wallet, ComputeBudget + ATA create + a Jupiter v6 "route"
instruction with arbitrary data + CloseAccount of the wallet's wrapped-SOL ATA). The
`execute_ok.json` signature is the wallet's genuine ed25519 signature over that message, so
`guard.signature_of(signed)` matches it.

Simulation fixtures (`rpc/sim_*.json`) carry a `_meta.addresses` list giving the request order
their `accounts` array is aligned with; the RPC client ignores unknown top-level keys.

`ohlcv/` holds real market data (see `ohlcv/README.md`); `expected/` holds pinned study numbers.
`recorded/` (absent by default) is where `tools/record_fixtures.py` writes redacted live captures.
