# Copy-module (WP-E) fixtures

**synthetic: true** — nothing here was recorded from a live API. Built by
`scratchpad/build_copy_fixtures.py`; the leader wallet is `identities.json` "foreign"
(`Keypair.from_seed(bytes([7]*32))`), its token accounts are seeds `[21..24]*32`, and every
signature is base58 of `sha512(<name>)` (see `identities.json` in this directory).

| file | what it shows |
|---|---|
| `rpc/tx_leader_buy_token.json` | leader buys 1,000 TKX for 120 USDC (`parse_swap` -> buy) |
| `rpc/tx_leader_sell_token.json` | leader sells 400 TKX for 60 USDC (-> sell) |
| `rpc/tx_leader_airdrop.json` | TKY appears, nothing else moves (-> None) |
| `rpc/tx_leader_buy_sol_quoted.json` | 0.5 SOL -> 2,000 TKX through a wrapped-SOL account (buy only with `sol_usd`) |
| `rpc/tx_leader_token_for_token.json` | TKX -> TKY, no quote leg (-> None) |
| `rpc/tx_leader_failed.json` | `meta.err` set (-> None, never verifies) |
| `familiars/agents_leader_alpha_ape.json` | `GET /api/agents/alpha_ape` whose `trades` reference the buy/sell above plus one signature that is not on chain yet |

`tests/fixtures/rpc/tx_multihop.json` (WP-B) doubles as the multi-hop case for our wallet
(USDC -> wrapped SOL -> TKX nets to a TKX buy).
