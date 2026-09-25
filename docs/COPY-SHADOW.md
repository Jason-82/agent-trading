# Copy-trading shadow tracker

The copy sleeve (`copy_consensus`) is **always on as a zero-capital shadow book and off as a
live strategy** (`copy.live = false`). Its job for the first months is to produce a dataset:
what copying the board's leaders with a realistic lag and realistic costs *would* have
returned. Expectation from the research (`docs/RESEARCH.md`): most likely it documents a
bleed. That dataset is the deliverable; promotion to live capital needs the gate below.

Code: `src/tiller/copy/{feeds,leaders,shadow,strategy}.py`. Nothing under `tiller.copy`
imports the venue or the signer (`tests/test_import_graph.py` and
`tests/test_shadow.py::test_shadow_module_has_no_order_authority` enforce it), so the
tracker cannot place an order by construction.

## What is recorded

### `leader_trades` (ledger table)

One row per leader swap we observed, keyed by transaction signature (dedupe).

| column | meaning |
|---|---|
| `key` | `fam:<handle>` (familiars board) or `wallet:<address>` (config `copy.external_wallets`) |
| `ts` / `detected_at` | leader fill time (chain `blockTime` or board `time`) / when our poll first saw it |
| `side`, `mint`, `amount`, `usd_value`, `price_usd` | the token leg (UI amount) and its quote-leg value in USD |
| `chain_verified` | `1` only after `getTransaction` returned the signature, succeeded, was signed by the leader wallet and (where the balance deltas can be classified) shows the same mint and side |

Rules:

* A familiars trade **never counts until the chain confirms it**. Unconfirmed rows wait in memory
  for 5 minutes and are then dropped with a `leader_trade_dropped` event; a row whose chain
  data disagrees with the board (side/mint mismatch, failed tx, wallet not a signer) is
  dropped immediately and never re-queued.
* `kind: swap` board rows (token-for-token) are ignored.
* The wallet feed classifies transactions from net balance deltas (`parse_swap`): a buy is the
  quote leg (USDC/USDT and/or SOL) going down with exactly one other mint going up; multi-hop
  routes resolve to their end legs; transfers, airdrops, token-for-token swaps and anything
  ambiguous are `None` (fail closed). A SOL-quoted swap needs a SOL price (from the price
  source wired into `WalletFeed`) or it is dropped rather than valued with a guess.
* SOL-vs-USDC swaps are not copy signals (SOL is the core hold asset).

### `shadow_trades` (ledger table, JSON per row)

One row per hypothetical position. Two kinds, told apart by `leader_key`:

* `consensus` — opened when the consensus rule fires for a mint (this is what the live
  strategy would trade; the promotion statistics use only these);
* `fam:<handle>` / `wallet:<addr>` — one per followed-leader buy, used for the per-leader
  copied-P&L statistics (and for demotion).

| field | meaning |
|---|---|
| `leader_price` | the leader's fill price (earliest priced fill for consensus trades) |
| `entry_price` | `worst-of(detection quote, leader_price x 1.01) x (1 + 1% cost)` — the detection quote is a Jupiter `/order` quote for the nominal size (falls back to Price V3) |
| `lag_s`, `lag_cost_pct` | seconds between the leader fill and our quote; `quote / leader_price - 1` (the market move during the lag, can be negative) |
| `size_usd` | nominal 2% of equity (`copy.per_signal_pct`); no capital moves |
| `exit` | stop -20%, 25% trail armed from +30%, time stop 48 h |
| `marks` | `1h`, `6h`, `24h` price marks, `high` (running high), `liq_entry` (pool liquidity at entry) |
| `exit_price`, `exit_ts`, `exit_reason`, `pnl_pct` | `exit_price = mark x (1 - 1% cost)`; `pnl_pct = exit_price / entry_price - 1` (net of modelled costs both ways) |

Exit reasons: `stop`, `trail`, `time_stop`, `liquidity_collapse` (pool liquidity at or below
40% of `liq_entry`). **Leader sells never close a shadow trade**: they arm a tighter 15% trail
from the running high immediately (`tighten_for_leader_sell`). Sizes are never mirrored.

## Leader scoring (from our own logs only)

`leaders.py` scores a leader exclusively from `leader_trades` rows we logged ourselves. The
board's `pnl` and `winRate` are stored in `raw_snapshots` but never read for ranking
(`tests/test_leaders.py::test_ranking_ignores_board_pnl_and_winrate` mutates them and asserts
identical scores).

Eligibility (`eligible`, every failing rule is listed): >= 14 days on our log; >= 30 closed
round trips (FIFO matched per mint); >= 10 distinct mints bought; median hold >= 4 h; no mint
above 50% of gross positive P&L (open lots marked at Price V3 when marks are supplied);
platform snapshot drawdown < 30% (deposit-adjusted equity from `history.snapshots`); zero buys
of mints younger than 24 h at buy time (unknown age fails closed); no mint whose Tokens V2 `dev`
is the leader wallet; no deposit within 24 h of a P&L jump > 20% of equity.

Replay (`replay`): each verified buy is re-priced at `ts + 60 s` at worst-of(our mark, leader
price x 1.01) plus 1% slippage + 10 bps, walked through **our** exit rules on the mark series
(leader sells only tighten the trail) and charged the same cost on exit. A leader qualifies
with PF >= 1.3 on >= 20 replayed signals. Score = mean rank of (PF, Sortino, weekly-positive
share) across qualified leaders. Sybil clustering merges leaders whose bought-mint sets
overlap by more than 80% (one vote per cluster). Sticky pool: enter in the top 20, stay while
ranked within 30. Demotion reasons: absent 48 h, negative 4-week copied P&L, young-mint buy,
own token, platform drawdown > 30%.

## Consensus signal (what would be traded)

`strategy.consensus`: >= 3 **distinct clusters** of pool leaders bought the same mint within
the last 30 minutes (verified buys only). The engine then applies, in order: the copy token
gate (organic >= 50, liquidity >= $250k, age >= 24 h, audit clean, round trip <= 1.5%, no
Token-2022 traps), the crowd-burst exclusion (`/api/tokens` agent count for the mint rose by
more than 15 versus the snapshot taken an hour earlier; with no hour-old snapshot yet the
signal is excluded), the late-entry guard (current price at most 15% above the earliest
leader fill) and the SOL daily regime being ON.

## Reading `tiller shadow-report`

`tiller shadow-report [--days 60]` prints a plain-text table followed by the same report as
JSON (`render_shadow_report(report, "both")`):

```
SHADOW COPY REPORT (zero capital; live copy stays off until the gate is met)
--------------------------------------------------
days observed                     63
consensus trades (closed)         120
expectancy / trade                +8.70%
profit factor                     999.00
median lag                        61 s
median lag cost                   +0.30%
P(positive 20-trade block)        1.000
leaders with positive copied P&L  67%
promotion gate                    NOT MET
gate not met because:
  - leaders_positive_share 0.333 < 0.4

leader   trades  copied P&L  positive
fam:l0       40    +348.00%  yes
...
{ "days": 63, "n_trades": 120, ... }
```

* `days observed` counts from the first shadow trade ever recorded, not from the window.
* `expectancy / trade` is the mean `pnl_pct` of closed consensus trades in the window, net of
  the 1% cost each way; `profit factor` is gross gains / gross losses (capped at 999 when
  there are no losses; 0 when there are no gains).
* `median lag cost` is the median market move between the leader's fill and our quote; a
  persistently positive number means we are systematically buying after the move.
* `P(positive 20-trade block)` is a seeded bootstrap (5,000 draws with replacement, seed 42):
  the share of random 20-trade blocks whose summed return is positive.
* `leaders with positive copied P&L` is the share of followed leaders whose per-leader shadow
  trades sum to a positive return.

## Promotion gate and procedure

`promotion_gate_met` is true only when **all** hold (config `[copy.promotion]`):

| condition | default |
|---|---|
| days observed | >= 60 |
| closed consensus trades | >= 100 |
| expectancy per trade after modelled costs | > +1% |
| bootstrap P(positive 20-trade block) | >= 0.80 |
| followed leaders with positive copied P&L | >= 40% |

Even when the gate is met nothing goes live by itself: `ConsensusCopyStrategy.targets`
returns entries only if `copy.live = true` **and** the gate is met **and** the SOL regime is ON,
with caps of 2 concurrent positions, 2 entries per UTC day (one per tick), a 5% sleeve and 2%
per signal; every entry carries the exit rule above and is still subject to the risk engine's
sizing and brakes. Procedure: run >= 60 days in paper or live core mode, read
`tiller shadow-report`, and only if the gate is met and you accept the per-leader table set
`copy.live = true` (restart required). Revisit monthly; set it back to `false` if the report
degrades (the gate is re-evaluated every tick, so a degraded book stops new entries anyway).

## Why live copy is off

* Copy P&L on absolute-USD boards is dominated by concentration and by being early; with a
  60 s lag, worst-of pricing and 1% costs each way the research expects negative expectancy.
* Every board leader is long SOL beta; copying adds correlated exposure the core sleeves
  already carry, which is why entries are gated on the SOL regime and the SOL-beta cap applies
  to the sum of sleeves.
* Board fields are self-reported and gameable (sybils, wash trades, transfers near P&L jumps);
  scoring from our own chain-verified log is slower but not spoofable by the board.

## Known limitations

* Replay marks come from the mark store the engine supplies (Price V3 snapshots); lower
  granularity than a real fill series, so replay P&L is an estimate.
* The wallet feed cannot value SOL-quoted swaps without a price source and drops them.
* Per-day entry counting in the live strategy is in memory (a restart resets it); the risk
  engine's day caps remain the binding guard.
* The report counts consensus trades only; a book with many followed leaders but few
  consensus events will show `n_trades` well below the per-leader totals.
