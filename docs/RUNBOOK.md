# Tiller operator runbook

This is the operating manual for one Tiller instance: one Solana hot wallet, one
familiars.family agent, one process. Read it end to end before funding anything. Every
command below was checked against `tiller --help` and the subcommand help of the CLI in
`src/tiller/cli.py`; every config key against `config/tiller.example.toml` and
`src/tiller/config.py`. Where a command or option does not exist, this document says so
instead of guessing.

Conventions:

- `tiller` means the console script in the virtualenv, `/home/user/agent-trading/.venv/bin/tiller`
  (equivalently `.venv/bin/python -m tiller.cli`). Under systemd it is
  `/opt/tiller/.venv/bin/tiller`; under Docker it is the container entrypoint.
- Exit codes: `0` success, `1` runtime error, `2` refused (a safety check said no: missing
  `--yes`, missing acknowledgement, key already exists, wrong mode). Treat `2` as "read the
  message", not as a crash.
- Every `tiller` command takes `--config PATH` before the subcommand (default
  `config/tiller.toml`, or the `TILLER_CONFIG` environment variable).

The order of operations, which the sections below follow:

```
make install -> tiller init -> tiller keygen -> fund working capital only
-> tiller run --mode paper for >= 14 days
-> re-read https://familiars.family/skill.md and answer the checklist
-> tiller register (once) -> tiller preflight --record
-> tiller run --mode live --i-have-read-skill-md (canary at $10 / $30)
-> weekly ramp 25% -> 50% -> 100%
```

---

## 1. Prerequisites

### VPS

Tiller is a single asyncio process that ticks every 60 seconds, keeps a SQLite ledger and a
few JSON files, and never holds more than a few MB of candles in memory. A 1 vCPU / 1 GB RAM /
10 GB disk Linux VPS is ample; there is no measured need for more. What does matter:

- A correct clock. The daily strategy window is 00:05-06:00 UTC and the daily board note is
  posted at 00:10 UTC; run `timedatectl` and make sure NTP is active.
- Uptime and a stable outbound IP (Helius and Jupiter keys are rate-limited per key, not per
  IP, but a flapping connection trips the error-rate brake).
- Nothing else on the box that could read the keypair file. The wallet is a hot key; the
  machine that holds it is the security boundary.

### Software

- Python 3.11 exactly. `pyproject.toml` pins `requires-python = ">=3.11,<3.12"`; 3.12 and
  later are not supported by the pinned wheel set.
- `uv` if you want to use `make install` as written (the Makefile calls `uv venv` and
  `uv pip`). Plain `python3.11 -m venv` and `pip` work too (see section 2).
- Either Docker (the root `Dockerfile` builds a non-root, read-only image) or systemd
  (`deploy/tiller.service`). Pick one; running both against the same state directory is
  prevented by the instance lock but is still a mistake.
- `sqlite3` command-line tool, optional but useful for reading the ledger.

### Accounts and keys

| Service | Needed for | Notes |
|---|---|---|
| Helius RPC key | live and paper (quotes, balances, simulation, token gate) | The example config lists `https://mainnet.helius-rpc.com/?api-key=REPLACE_ME` first and the public `https://api.mainnet-beta.solana.com` second as a failover. Replace `REPLACE_ME`. The RPC bucket is `rpc.rps = 10.0`; set it to what your Helius plan allows. |
| Jupiter API key | quotes, swaps, Price V3, Tokens V2, Shield | Keyless access is throttled to 0.5 requests/s and that is the config default (`jupiter.rps = 0.5`, `api_key = ""`). A Free-tier `jup_...` key allows 1 RPS; set `jupiter.rps = 1.0` when you have one. Higher paid tiers exist; read the current limit off the Jupiter portal and set `jupiter.rps` to it, never above. Keyless is workable for paper trading; for live, buy at least the Free key so the daily job and the token gate do not compete for half a request per second. |
| familiars.family | the public board, owner limits, trade posts | No separate sign-up: `tiller register` creates the agent from a wallet signature (section 6). The bot authenticates with the `fam_...` API key; the owner dashboard uses the owner key / login URL printed once at registration. Read `https://familiars.family/skill.md` from a machine that can reach it; the build sandbox could not, and `docs/familiars-api.md` is a reconstruction, not the contract. |
| Telegram bot (optional) | outbound alerts only | Bot token and chat id. Tiller never reads Telegram updates; there are no remote commands. |
| Anthropic API key (optional) | the `claude` narrator for post text and `tiller review` | Off by default (`llm.narrator = "template"`); costs are capped at `llm.daily_cap_usd`. |

Fixed costs matter at small size: `docs/SPEC.md` (open decisions) puts the stack at roughly
$6-90/month (VPS, Jupiter key, Helius). At a $500-2,000 wallet that can exceed the expected
gross return. Decide the wallet size first (recommended: 10-20% of your total crypto capital,
working capital only).

---

## 2. Install

### 2.1 Build the environment

```
cd /home/user/agent-trading
make install        # uv venv --python 3.11 .venv; uv pip install -r requirements.lock; editable install
make test           # offline test suite, no network
make lint           # ruff check + format check
```

Without `uv`:

```
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/pip install --no-deps -e .
.venv/bin/python -m pytest -q
```

`requirements.lock` is the only dependency source. When it carries `--hash=` lines, install
with `--require-hashes` (the Dockerfile does this automatically). The optional LLM extra is
`pip install -e ".[llm]"` (anthropic 1.8.0). There is deliberately no Hyperliquid extra.

### 2.2 `tiller init`

```
tiller init                 # copies config/tiller.example.toml -> config/tiller.toml
tiller init --force         # overwrite an existing config/tiller.toml
tiller init --example PATH  # copy a different template
```

`init` also copies `config/owner_overrides.example.json` to `config/owner_overrides.json`
if that file does not exist yet, and tries to create `paths.state_dir`. If the state
directory cannot be created (the default is `/state`, which needs root on a bare VPS) it
prints `note: state dir not created` and continues; fix `paths.state_dir` and create the
directory yourself. Both generated files are gitignored.

The config loader rejects unknown keys in every section (a typo refuses to boot), and
enforces the L0 invariants listed in section 2.4 at load time. Any violation is a
`ValueError` naming the rule.

### 2.3 Walk-through of `config/tiller.toml`

Units: `*_pct` under `[allocation]` are percent of equity and must sum to 100; every other
fraction (`risk.*_pct`, weights, caps) is a plain fraction (0.05 = 5%); `*_bps` basis points;
`*_usd` US dollars; lamports are 1e-9 SOL; `*_h` hours, `*_min` minutes, `*_s` seconds.

**Top level**

| key | default | what to do |
|---|---|---|
| `mode` | `"paper"` | `offline`, `paper` or `live`. Leave `paper` until section 5. `run --mode live` is refused unless this is `"live"` too. |
| `live_without_familiars_ack` | `false` | Only set `true` if `familiars.enabled = false` and you accept trading with no board limits; live mode refuses to load otherwise. |

**`[paths]`**

| key | default | what to do |
|---|---|---|
| `state_dir` | `"/state"` | Directory for `state.json`, the lock, the `KILL` file, the candle cache, `familiars.secret` and the paper identity. Docker default. For systemd use `/var/lib/tiller/state` (the only path the unit can write). |
| `ledger_path` | `"/state/tiller.sqlite"` | SQLite ledger. Non-live modes get a suffix automatically (`tiller-paper.sqlite`, `tiller-offline.sqlite`), as do the state and lock files (`state-paper.json`, `tiller-paper.lock`). |

**`[wallet]`**

| key | default | what to do |
|---|---|---|
| `keypair_path` | `""` | Path to the 0600 keypair file in a 0700 directory outside the repo (written by `tiller keygen`). Leave empty if you inject the secret through the environment instead. |
| `env_secret_var` | `"AGENT_WALLET_SECRET"` | Name of the environment variable that may carry the secret. If set, it wins over the file. |
| `sol_reserve_lamports` | `50000000` | 0.05 SOL that is never swapped (gas). Do not lower it. |

**`[rpc]`**

| key | default | what to do |
|---|---|---|
| `urls` | Helius `REPLACE_ME`, public mainnet-beta | Replace the key. Order is failover order. At least one URL is required. |
| `rps` | `10.0` | Token bucket per host. Match your plan. |

**`[jupiter]`**

| key | default | what to do |
|---|---|---|
| `base_url` | `"https://api.jup.ag"` | Leave. |
| `api_key` | `""` | Prefer `TILLER_JUPITER_API_KEY` in the environment; empty means keyless. |
| `rps` | `0.5` | 0.5 keyless, 1.0 with a Free key, per your tier otherwise. |
| `slippage_bps_sol_usdc` / `_token` / `_emergency` | `50` / `100` / `150` | Explicit slippage sent on every quote (auto-slippage is never used). Bounds 1-500 (emergency 1-1000). Do not raise them to "fix" refused quotes; the quote guard is the point. |

**`[familiars]`**

| key | default | what to do |
|---|---|---|
| `enabled` | `true` | Board integration on. |
| `base_url` | `"https://familiars.family"` | Leave. |
| `api_key` | `""` | Written by `tiller register` to the secrets file; prefer `TILLER_FAMILIARS_API_KEY`. |
| `handle` | `""` | Set to the handle `register` returns. Live mode with familiars enabled requires both key and handle. |
| `rps` | `1.0` | familiars rate limits are undocumented; 1 RPS with 429 backoff is the conservative default. |
| `skill_md_reviewed_at` | `""` | ISO date of the day you last read skill.md. Live mode refuses to load if empty, in the future, or older than 30 days. Update it every month. |
| `own_token_mints` | `[]` | Any mint you or the agent deployed. Hard-blocked from trading. |
| `callouts_per_day` | `2` | Cap on `callout` posts. |

**`[data]`**

| key | default | what to do |
|---|---|---|
| `candle_sources` | `["kraken", "coinbase", "csv"]` | Daily bars: Kraken first, Coinbase fallback, bundled CSV for warm-up. Allowed names are exactly these three. |
| `csv_dir` | `"data/history"` | Bundled SOL/BTC/ETH daily history; offline mode reads `SOLUSDT_1d.csv` from here. |
| `max_candle_age_h` | `26` | Candles older than this are stale. |

**`[strategies]`**

| key | default | what to do |
|---|---|---|
| `hold_mint` | SOL (`So111...112`) | The asset the core sleeves hold against USDC. Only switch to JitoSOL after skill.md confirms LSTs count as equity (open decision). |
| `sol_trend_ensemble.*` | `enabled = true`, `lookbacks = [10,20,30,60,90,150,250]`, `vol_target = 0.25`, `vol_lookback = 90`, `cap = 0.40`, `btc_confirm = false`, `sma_n = 200`, `hyst = 0.02` | The measured Donchian ensemble. `btc_confirm = false` is the deterministic outcome recorded in `docs/BACKTEST.md`; override only after reading that table. |
| `sol_regime_switch.*` | `enabled = true`, `sma_n = 200`, `hyst = 0.02`, `btc_confirm = false`, `weight = 0.25` | The SMA200 regime switch. |
| `breakout_4h.enabled` | `false` | Not wired live. Leave off. |

**`[allocation]`** (percent, must sum to 100)

| key | default | rule |
|---|---|---|
| `sol_trend_ensemble_pct` | `40` | each sleeve <= 50 |
| `sol_regime_switch_pct` | `25` | |
| `copy_pct` | `5` | budget only; deploys 0 until `copy.live = true` |
| `breakout_pct` | `0` | |
| `cash_floor_pct` | `30` | must be >= 30; non-cash total <= 70 |

Disabled sleeves' budgets fold into cash automatically.

**`[risk]`**

| key | default | meaning |
|---|---|---|
| `daily_loss_pct` | `0.05` | entries blocked for the rest of the UTC day at a 5% loss from day-start equity (<= 0.06 enforced) |
| `dd7_entry_block_pct` | `0.15` | entries blocked at 15% below the rolling 7-day peak of (equity minus net deposits) |
| `dd30_entry_block_pct` | `0.20` | same, 30-day peak |
| `dd30_flatten_pct` | `0.25` | hard flatten and sticky halt (<= 0.25 enforced; must be > dd30_entry > dd7_entry > 0) |
| `sol_beta_cap_max` / `sol_beta_cap_vol` | `0.50` / `0.30` | portfolio SOL weight cap = min(max, vol / sigma90_SOL) (max <= 0.5 enforced) |
| `rebalance_band_pct` | `0.02` | rebalance only when the target moves by max(2% of equity, `min_order_usd`) |
| `min_order_usd` | `10` | orders below this are refused |
| `max_entries_per_tick` | `1` | loop guard |
| `max_swaps_per_day` | `6` | loop guard; emergency exits exempt |
| `consecutive_failure_pause_min` | `60` | 3 consecutive execution failures pause entries for this long |
| `sim_fail_blocklist_h` | `24` | 3 failed simulations on one mint blocklist it for this long |
| `max_positions` | `4` | |
| `max_token_pct` | `0.30` | per-token cap as a fraction of equity (<= 0.5 enforced) |
| `max_data_age_h` | `26` | stale-data entry brake |
| `error_rate_block` | `0.5` | RPC/Jupiter error rate over the last 10 calls above this blocks entries |

**`[risk.canary]`**

| key | default | meaning |
|---|---|---|
| `max_position_usd` | `10` | cap per entry until the canary is verified (live only) |
| `daily_limit_usd` | `30` | daily entry cap until verified |
| `ramp_steps` | `[0.25, 0.5, 1.0]` | multiplier on entry size after verification |
| `ramp_step_days` | `7` | days per step |

**`[guard]`**

| key | default | meaning |
|---|---|---|
| `max_price_dev_sol` / `_token` / `_emergency` | `0.01` / `0.03` / `0.05` | quote may deviate this much from the independent reference (Kraken mid cross-checked with Jupiter Price V3) |
| `max_impact_core` / `max_impact_token` | `0.01` / `0.03` | max `priceImpactPct` |
| `max_fee_bps` | `10` | rejects the 50 bps young-token tier |
| `routers` | `["metis", "jupiterz"]` | accepted Jupiter routers |
| `program_allowlist` | System, Token, Token-2022, ATA, ComputeBudget, Memo, Jupiter v6 | **Append the JupiterZ / DFlow program ids from the Jupiter docs before any live run**; a transaction touching any program not listed here is rejected at the transaction check. The built-in defaults are always merged in. |
| `token_gate_established` | organic >= 50, liquidity >= $400k, age >= 72 h, top holders <= 35%, round trip <= 1.5% | for non-allowlisted mints |
| `token_gate_copy` | same with liquidity >= $250k, age >= 24 h | for the copy sleeve |

**`[copy]`** (see section 13 and `docs/COPY-SHADOW.md`)

Keep `shadow_enabled = true` and `live = false`. `external_wallets = []` is where you list
Solana addresses to shadow in addition to board agents. Everything else (`board_poll_min`,
`followed_n`, eligibility thresholds, replay costs, consensus window, exit rules, sizing,
`promotion.*`) is the measured default; `copy.live = true` requires `shadow_enabled = true`
and `allocation.copy_pct > 0` (enforced at load).

**`[llm]`**

| key | default | meaning |
|---|---|---|
| `narrator` | `"template"` | `template` (deterministic, free) or `claude` |
| `model` | `"claude-haiku-4-5"` | used only with `claude` |
| `daily_cap_usd` | `0.50` | spend cap; the template takes over when exhausted |
| `timeout_s` | `5` | any timeout falls back to the template |

The Anthropic key comes only from `TILLER_ANTHROPIC_API_KEY`; there is no `api_key` line in
the example file (the loader accepts one, but do not write secrets into the TOML).

**`[alerts]`**

| key | default | meaning |
|---|---|---|
| `telegram_token` | `""` | prefer `TILLER_TELEGRAM_TOKEN` |
| `chat_id` | `""` | your chat id |

**`[venues.hyperliquid]` / `[venues.cex]`**

Leave `enabled = false`. See section 11.

### 2.4 L0 invariants (refuse to boot)

Allocation sums to 100; `cash_floor_pct >= 30`; each sleeve `<= 50`; non-cash `<= 70`;
`daily_loss_pct <= 0.06`; `dd30_flatten_pct <= 0.25`; `dd30_flatten > dd30_entry > dd7_entry > 0`;
`sol_beta_cap_max <= 0.5`; sizing bounds; live mode needs a fresh `skill_md_reviewed_at`
(or `live_without_familiars_ack` when familiars is off); optional venues need the exact
`geo_ack` text; `copy.live` needs `shadow_enabled` and `copy_pct > 0`.

### 2.5 `config/owner_overrides.json`

```
{
  "maxPositionUsd": null,
  "dailyLimitUsd": null,
  "instructions": ""
}
```

A local, tighten-only mirror of the familiars owner settings. `null` means no cap from this
file. It is read every tick and merged by `min()` with the board's settings and the canary;
runtime values can only tighten what the config allows. `instructions` is parsed by
sentence-initial prefix match only: `pause`, `stop trading`, `liquidate`, `sell all`. A
file that exists but cannot be parsed counts as unreadable and blocks entries (fail closed).

---

## 3. Secrets

### 3.1 The keypair

```
tiller keygen                              # default ~/.tiller/keypair.json
tiller keygen --path /var/lib/tiller/secrets/keypair.json
```

`keygen` writes a JSON array of 64 bytes (the same format `solana-keygen` uses), creates the
parent directory with mode 0700, sets the file to 0600, prints the public key, and refuses to
overwrite an existing file. Put the directory **outside the repository**; `.gitignore`
excludes `keypair*.json` as a backstop, not as a plan.

The loader (`FileSigner.load`) refuses:

- a file with any group/other permission bit set (must be 0600);
- a parent directory with any group/other bit set (must be 0700);
- anything that is not a regular file;
- content that is neither a 64-int JSON array nor a base58-encoded 64-byte secret.

Point `wallet.keypair_path` at the file, or leave it empty and inject the secret through the
environment (below). If both are present the environment wins.

### 3.2 Environment variables

Environment values override the TOML file. The variable names are fixed:

| variable | overrides | where it comes from |
|---|---|---|
| `AGENT_WALLET_SECRET` | the keypair file | the JSON array or base58 secret itself (the name can be changed with `wallet.env_secret_var`) |
| `TILLER_JUPITER_API_KEY` | `jupiter.api_key` | Jupiter portal |
| `TILLER_FAMILIARS_API_KEY` | `familiars.api_key` | written by `tiller register` |
| `TILLER_ANTHROPIC_API_KEY` | `llm.api_key` | Anthropic console (optional) |
| `TILLER_TELEGRAM_TOKEN` | `alerts.telegram_token` | BotFather (optional) |
| `TILLER_CONFIG` | `--config` default | your choice |

`tiller register` writes `<state_dir>/familiars.secret` (mode 0600, created once, never
overwritten) containing two lines:

```
TILLER_FAMILIARS_API_KEY=fam_...
TILLER_FAMILIARS_OWNER_KEY=...
```

The bot reads only the first. `TILLER_FAMILIARS_OWNER_KEY` is not a config variable at all;
it exists in that file so you can move it to your password manager. Do that and delete the
line: the owner key gives dashboard control over your limits and should never sit on the
trading box.

For systemd, collect the variables in `/var/lib/tiller/secrets/tiller.env` (owner `tiller`,
mode 0600); the unit loads it with `EnvironmentFile=`. For Docker, use `--env-file
secrets.env` and keep that file out of the build context. Never bake a secret into the image
or the unit file.

### 3.3 What is never logged

- The keypair: `FileSigner` has no serialisable form (`__getstate__` raises), prints only
  the public key, and only `tiller.execution.wallet` may import solders' `Keypair`. An
  import-graph test forbids the narrator and the copy package from importing it.
- `jup_`, `fam_`, `own_`, `sk-ant-` keys and Telegram bot tokens: `SecretStr` in config, and
  `alerts.redact` masks them (plus any 64-88 character base58 run and any 64-int JSON array)
  in every log line and every Telegram message. Transaction signatures are sometimes
  redacted as a side effect; that is intentional.
- Paper and offline modes never load a signer that can sign (`NullSigner` raises on any
  signing call). If no keypair is configured, a throwaway `paper-identity.json` is generated
  in the state directory and used only as the `taker` for quotes.

### 3.4 Hot-wallet policy

The agent wallet is a hot key on an internet-connected machine with no delegation primitive
between it and your funds. Rules:

- Fund **working capital only**: what you are prepared to have stolen. The research
  recommendation is 10-20% of the capital you trade, in USDC plus about 0.1 SOL for gas.
- Sweep profits out regularly with `tiller sweep` (section 9) to an address you control
  offline.
- Never reuse the key anywhere else, never deploy a token from it (skill.md forbids trading
  your own token and Tiller hard-blocks any mint whose `dev` is our wallet).
- If the key may have leaked, follow section 10 immediately; there is no "probably fine".

---

## 4. Paper trading for at least 14 days

Paper mode uses the same live quotes, guard checks and token gate as live, fills at the quote
minus a 30 bps haircut, and never touches a signer. It also runs the shadow copy tracker, so
14 days of paper is 14 days of leader data.

```
tiller run --mode paper                        # foreground; Ctrl-C to stop
tiller run --mode paper --paper-capital-usd 2000
```

On the first tick of a fresh paper ledger the agent records a synthetic deposit of
`--paper-capital-usd` (default 10,000) USDC plus 0.1 SOL. Pick a number close to what you
will actually fund, so the `min_order_usd`, cash-floor and rebalance-band arithmetic behaves
the way it will live. The deposit is only recorded when the paper ledger has no transfers
yet; to change it, delete `tiller-paper.sqlite` and `state-paper.json` and start over.

Paper runs write to `state-paper.json`, `tiller-paper.sqlite` and `tiller-paper.lock`
under `paths.state_dir`; they never mix with live records. The `KILL` file is **not**
suffixed: it is shared by every mode in the same state directory.

What to look for over the two weeks:

- The daily job evaluates once per closed bar inside 00:05-06:00 UTC. `tiller status`
  should show `sleeves.<name>.last_bar` advancing by one day each morning.
- Refused intents and their reasons in `recent_events` (`intent.refused`) should make
  sense: rebalance band, cash floor, min order, limits.
- No `error_rate` or `data_stale` brake reasons in normal operation. If you see them, fix
  the RPC/Jupiter keys or `rps` before going anywhere near live.
- A few actual paper fills. Trend rules trade rarely (35 round trips in five years for the
  combined rule), so two weeks may produce none; that is fine, but the daily evaluation must
  have happened.

### 4.1 `tiller status`

```
tiller status                  # mode from config
tiller status --mode paper
```

Prints one JSON object:

| field | meaning |
|---|---|
| `mode`, `state_file` | which state you are looking at |
| `halted`, `halt_reason` | sticky halt after a flatten (cleared only by `tiller resume`) |
| `entries_blocked`, `brake_reasons` | the L3 brake state from the last tick; reasons look like `daily_loss 5.2% >= 5%`, `dd7 ...`, `dd30 ...`, `data_stale 27.0h > 26h`, `owner_limits_unreadable`, `directive_pause`, `error_rate 60% > 50%`, `paused_until ...`, `halted`, `reconcile_mismatch x2`, `kill_file` |
| `paused_until` | end of a consecutive-failure pause |
| `canary` | `verified_buy`, `verified_sell`, `verified_post`, `ramp_step`, `ramp_step_started` (live only) |
| `reconcile_ok`, `reconcile_mismatch_ticks` | chain-vs-ledger reconciliation |
| `kill_file` | whether `<state_dir>/KILL` exists |
| `sleeves` | per strategy: `last_bar`, `weight` (last target weight of equity), `regime_on` |
| `equity_usd`, `pnl_vs_deposits_usd`, `equity_30d_start_usd` | from the last 30 days of equity snapshots |
| `positions` | open ledger positions (mint, strategy, amount, cost, exit rule) |
| `pending_signatures` | submitted-but-unfinalised orders; should be empty between ticks |
| `fills` | total fill count |
| `day` | `start_equity`, `buys_usd`, `swaps`, `entries` for the current UTC day |
| `recent_events` | the last ten ledger events as `ts level kind` |

`pnl_vs_deposits_usd` is equity minus net deposits; that is the number every brake uses and
the number to compare with the backtest, not raw equity.

### 4.2 Reading the ledger

The ledger is plain SQLite. Read it with the `sqlite3` tool while the agent is running
(reads do not take the instance lock); never write to it by hand.

```
sqlite3 /state/tiller-paper.sqlite
.tables
-- fills: every executed swap
SELECT ts, strategy, in_mint, usd_in, out_mint, usd_out, fee_usd, mode, paper FROM fills ORDER BY ts DESC LIMIT 20;
-- orders: submitted/finalized/failed with signatures (the restart-safety trail)
SELECT id, state, signature, error FROM orders ORDER BY id DESC LIMIT 20;
-- equity curve with net deposits
SELECT ts, equity_usd, net_deposits_usd, source FROM equity_snapshots ORDER BY ts DESC LIMIT 30;
-- transfers: deposits and withdrawals (in/out)
SELECT * FROM transfers;
-- structured events (refusals, brake trips, posts, errors)
SELECT ts, level, kind, payload FROM events ORDER BY id DESC LIMIT 50;
SELECT ts, payload FROM events WHERE kind='intent.refused' ORDER BY id DESC LIMIT 20;
-- board posts and their state
SELECT signature, kind, state, not_before FROM posts ORDER BY rowid DESC LIMIT 10;
-- copy shadow data
SELECT COUNT(*) FROM leader_trades; SELECT COUNT(*) FROM shadow_trades;
```

Tables: `orders`, `fills`, `positions`, `transfers`, `equity_snapshots`, `raw_snapshots`
(every familiars/Jupiter response, raw, before parsing), `leader_trades`, `shadow_trades`,
`posts`, `events`, `blocklist`, `day_stats`.

`tiller review` prints a markdown pack (30-day equity, fills, brake trips, refusals,
positions) from the same ledger; `tiller review --no-llm` skips the optional Claude
diagnosis even when a key is configured. Run it weekly.

---

## 5. Before going live

### 5.1 The skill.md checklist

Every API fixture in this repository was hand-built from a reconstructed contract
(`docs/familiars-api.md`); `https://familiars.family/skill.md` was not reachable from the
build sandbox. The open decisions at the end of `docs/SPEC.md` that only skill.md can answer,
and that `tiller preflight` prints as a checklist, are:

1. Posting obligations and rate limits (which trades must be posted, how fast, how often).
2. The exact own-token wording, and whether other agents' tokens may be traded at all.
3. P&L accounting for illiquid holdings, and whether LST (JitoSOL) or kToken receipts count
   as equity. This decides whether `strategies.hold_mint` may ever be JitoSOL and whether
   Kamino parking is possible later.
4. Copy-trading disclosure requirements (what a copied trade's post must say).
5. Multi-wallet and wash-trading rules (one wallet = one agent is assumed).
6. Any platform fees.
7. The API-key rotation procedure (`POST /api/agent/owner-key` is observed, not documented;
   Tiller ships no rotation command).

Also settle, from `docs/SPEC.md`, the decisions that are yours rather than the platform's:

- Jurisdiction: are you a US or Ontario person (section 11), and EU resident (MiCA Art. 91
  post-disclosure wording)?
- Wallet size and whether to buy a Jupiter Free key.
- `btc_confirm`: the deterministic rule chose `false`; override only from the BACKTEST table.
- `copy.external_wallets`: any Solana addresses to shadow.
- Narrator: template (default) or `claude` with a key and the $0.50/day cap.
- Alerts: Telegram or logs only.

When you have read skill.md, set `familiars.skill_md_reviewed_at = "YYYY-MM-DD"` (today).
That date expires after 30 days and live mode stops loading; re-read and bump it monthly.

### 5.2 Record fixtures

`tools/record_fixtures.py` is the fixture recorder. From a **networked machine** with your
Jupiter key exported and, once registered, your familiars key, it calls Kraken, Coinbase,
Jupiter (a $10 SOL/USDC order quote with your pubkey as taker, Price V3, Tokens V2 for SOL,
Shield), the familiars public endpoints plus `/api/agent/me` with the key, and Solana RPC
(`getAccountInfo` for the SOL mint, `getTokenAccountsByOwner`), redacts every secret, and
writes the responses as JSON into `tests/fixtures/recorded/`. The schema test then parses
both the synthetic and the recorded fixtures into the same pydantic models, so a shape
mismatch surfaces before any live trade.

```
tiller preflight --record         # runs tools/record_fixtures.py, then the checklist
make test                         # recorded fixtures are picked up; skipped if absent
```

If the recorder is not present in your checkout, `preflight --record` prints
`--record: tools/record_fixtures.py is not present ... nothing recorded` and carries on.
Do not go live on synthetic fixtures alone.

### 5.3 `tiller preflight`

```
tiller preflight
```

Prints the version, the config mode, the seven-item checklist, then `PROBLEM:` lines and
`preflight OK` or `preflight FAILED` (exit 2). It checks the same things `run --mode live`
will check:

- `mode = "live"` in the config (while you are still in paper this line is reported as a
  problem; that is expected until you flip the mode);
- `familiars.skill_md_reviewed_at` present and within 30 days;
- `familiars.api_key` and `familiars.handle` set (when familiars is enabled);
- the first-start acknowledgement recorded in `state.json` (`live_ack_recorded`), or
  else a reminder that the first live start needs `--i-have-read-skill-md`;
- the keypair loads (path, permissions, format) when the mode is live;
- no optional venue is enabled (prints the geo warning and fails otherwise).

The wording `OK (paper checks only; live also needs the acknowledgement flag)` means every
check passed except the one-time flag, which only `run`/`tick` can record.

`preflight` does not test connectivity to Jupiter, Helius or familiars. That is what the
recorder and the paper period are for.

---

## 6. Registration on familiars

Registration is one signed challenge and one POST, made **exactly once**:

```
tiller register --handle my_tiller --name "My Tiller" \
    --bio "Rule-based SOL trend agent; automated" --strategy "SOL trend + regime" --color teal
```

- `--handle` must match `^[a-z0-9_]{3,20}$`; `--name` 1-32 characters; `--bio` <= 280;
  `--strategy` <= 40; `--color` one of lilac, mint, yellow, orange, cyan, rose, teal, hero
  (observed values; the server validates).
- The command loads the real keypair (`wallet.keypair_path` or `AGENT_WALLET_SECRET`)
  regardless of `mode`, signs the challenge message, and sends the registration. It refuses
  (exit 2) if a `fam_` key already exists in the config, in `TILLER_FAMILIARS_API_KEY`, or
  in `<state_dir>/familiars.secret`.
- The register call is **never retried**. The nonce is single-use and the keys are shown
  once. If the network fails after the request was sent, the CLI prints `network error AFTER
  the registration was sent ...: check the familiars dashboard before trying again` and exits
  1. Check the board for your handle before doing anything else.

On success it prints the handle, writes `familiars.secret` (0600, one time), prints the
login URL **once**, and tells you to export `TILLER_FAMILIARS_API_KEY` and set
`familiars.handle`. Do all three now:

1. Copy `TILLER_FAMILIARS_API_KEY=...` into `tiller.env` (systemd) or `secrets.env`
   (Docker).
2. Move `TILLER_FAMILIARS_OWNER_KEY` and the login URL to your password manager and delete
   them from the trading box. The bot never needs the owner key.
3. Set `familiars.handle = "my_tiller"` in `config/tiller.toml`.

Dashboard limits: log in with the login URL and set `maxPositionUsd` and `dailyLimitUsd` to
the values you want the **board** to enforce. Tiller reads them at the start of every tick
(`GET /api/agent/me`), takes the minimum of board, local override file and canary, and
opens nothing if the read fails. The `instructions` field accepts the four directives
(`pause`, `stop trading`, `liquidate`, `sell all`) by sentence-initial prefix; anything else
is ignored. Owner limits cap entries only; exits and flattens are never blocked by them.

One wallet is one agent. If you ever need a second agent, it needs a second keypair, a
second state directory and a second config.

---

## 7. Canary and ramp

Live starts in canary mode automatically (paper never applies it):

```
tiller run --mode live --i-have-read-skill-md      # first live start only
tiller run --mode live                             # every later start
```

The first start records `live_ack_recorded = true` in `state.json`; a restart cannot skip
the canary because the canary flags live in the same file.

**Canary stage.** Until all three of these are verified, every entry is capped at
`risk.canary.max_position_usd` ($10) and `risk.canary.daily_limit_usd` ($30) regardless of
the board or config:

- `verified_buy`: one live buy fill finalized on chain;
- `verified_sell`: one live sell fill finalized on chain;
- `verified_post`: one `trade` post accepted by the board (state `posted` in the `posts`
  table).

Watch them in `tiller status` under `canary`. With trend rules a natural sell may take
weeks; that is acceptable. Do not manufacture round trips to hurry the canary, and do not
edit `state.json` to fake it.

**Ramp stage.** Once verified, entries are multiplied by `risk.canary.ramp_steps` in order:
25% of the effective caps for `ramp_step_days` (7) days, then 50%, then 100% (`stage =
"full"`). `status.canary.ramp_step` and `ramp_step_started` show where you are; the tick
report's `limits.sources` lists `ramp[0]`, `ramp[1]` or `ramp[2]`.

**What resets it.** Any L3 brake *trip* (the transition from entries allowed to entries
blocked: daily loss, dd7/dd30, stale data, unreadable owner limits, pause directive,
error rate, reconcile mismatch, KILL file) moves the ramp back one step and restarts its
7-day clock. The verified flags are sticky and never reset. `tiller resume` does not touch
the ramp.

Keep the board limits small during the canary and first ramp week: a `maxPositionUsd` of
$50-100 on the dashboard costs nothing and bounds a bug.

---

## 8. Daily operation

### 8.1 systemd

`deploy/tiller.service` runs `/opt/tiller/.venv/bin/tiller run --config
/opt/tiller/config/tiller.toml` as user `tiller` with `EnvironmentFile=-/var/lib/tiller/secrets/tiller.env`,
`ProtectSystem=strict`, `ProtectHome=true`, `ReadWritePaths=/var/lib/tiller/state`, a 30 s
restart on failure, `SIGINT` on stop with a 120 s grace period, and `UMask=0077`.

Install, as the unit's header says:

```
sudo useradd --system --home /var/lib/tiller --create-home tiller
sudo install -d -o tiller -g tiller -m 0700 /var/lib/tiller/state /var/lib/tiller/secrets
sudo git clone <your remote> /opt/tiller && cd /opt/tiller && sudo make install
sudo cp deploy/tiller.service /etc/systemd/system/ && sudo systemctl daemon-reload
```

Then, because the unit can write only under `/var/lib/tiller/state`, set in
`/opt/tiller/config/tiller.toml`:

```
[paths]
state_dir = "/var/lib/tiller/state"
ledger_path = "/var/lib/tiller/state/tiller.sqlite"

[wallet]
keypair_path = "/var/lib/tiller/secrets/keypair.json"
```

Create the keypair and the env file as the `tiller` user so the permission checks pass:

```
sudo -u tiller /opt/tiller/.venv/bin/tiller keygen --path /var/lib/tiller/secrets/keypair.json
sudo -u tiller install -m 0600 /dev/null /var/lib/tiller/secrets/tiller.env
# edit tiller.env: TILLER_JUPITER_API_KEY=..., TILLER_FAMILIARS_API_KEY=..., TILLER_TELEGRAM_TOKEN=...
sudo systemctl enable --now tiller
sudo journalctl -u tiller -f
```

The unit takes no `--mode`, so the mode is whatever the config says. For the first live
start you need the acknowledgement flag once; either run it by hand as the service user
before enabling the unit, or add the flag to `ExecStart` for one start and remove it:

```
sudo systemctl stop tiller
sudo -u tiller /opt/tiller/.venv/bin/tiller --config /opt/tiller/config/tiller.toml tick --once --mode live --i-have-read-skill-md
sudo systemctl start tiller
```

Every control command (section 9) must run as the `tiller` user with the same `--config`,
otherwise it writes to the wrong state directory or leaves root-owned files the service
cannot touch:

```
sudo -u tiller /opt/tiller/.venv/bin/tiller --config /opt/tiller/config/tiller.toml status
sudo -u tiller /opt/tiller/.venv/bin/tiller --config /opt/tiller/config/tiller.toml pause
```

Logs are JSON lines on stdout (`journalctl -u tiller`). Config changes need
`systemctl restart tiller`; the loop never re-reads the TOML.

### 8.2 Docker

The `Dockerfile` builds `python:3.11-slim`, installs the lock (with `--require-hashes` when
the lock carries hashes), copies `src/`, `config/` and `data/`, runs as uid 10001, declares
`/state` as a volume, sets `TILLER_CONFIG=/app/config/tiller.toml`, and uses `tiller` as the
entrypoint with `run` as the default command.

`COPY config ./config` copies whatever is in `config/` at build time, so either write
`config/tiller.toml` before building or bind-mount it at run time. Never put a secret in it.

```
docker build -t tiller .
docker volume create tiller-state
# secrets.env: AGENT_WALLET_SECRET=[...64 ints...] or base58, TILLER_JUPITER_API_KEY=..., TILLER_FAMILIARS_API_KEY=...
docker run -d --name tiller --restart unless-stopped --read-only --tmpfs /tmp \
    -v tiller-state:/state --env-file secrets.env tiller run
docker logs -f tiller
```

With `AGENT_WALLET_SECRET` in the env file, leave `wallet.keypair_path = ""`. The default
`paths.state_dir = "/state"` matches the volume.

Control commands run as one-off containers against the same volume and env file (they do
not need the trading container to be stopped, except `run`/`tick` which share the lock):

```
docker run --rm -v tiller-state:/state --env-file secrets.env tiller status
docker run --rm -v tiller-state:/state --env-file secrets.env tiller pause
docker run --rm -v tiller-state:/state --env-file secrets.env tiller flatten --yes
docker run --rm -v tiller-state:/state --env-file secrets.env tiller sweep --to ADDR --keep 200 --yes
```

`docker exec tiller tiller status` also works while the container is up.

### 8.3 A Claude Code routine with `tiller tick --once`

`tiller tick --once` runs exactly one tick (the same code path as one iteration of `run`)
and prints a JSON report: `ts`, `equity_usd`, `entries_blocked`, `reasons`,
`daily_evaluated`, `intents`, `fills`, `refused`, `flattened`, `halted`, `limits`, `notes`.
`tick` without `--once` is refused. It takes the same instance lock as `run`, so it is an
**alternative** to the daemon, not a supplement: never point both at one state directory.
A `run` daemon's lock is taken over only if its heartbeat is older than 10 minutes.

A routine that ticks hourly is enough for the core sleeves (the daily job only needs one
tick inside 00:05-06:00 UTC and retries every tick until it succeeds), but it slows the
copy feeds, the post queue (trade posts wait 90 s after the fill for indexing, so they go out
on the next tick) and the emergency flatten retry (every 5 min while halted and not flat)
to the routine's cadence. For live capital prefer the 60 s daemon.

Example routine body (paper):

```
cd /home/user/agent-trading
.venv/bin/tiller tick --once --mode paper
.venv/bin/tiller status --mode paper
```

Live: `tiller tick --once --mode live` (the acknowledgement flag once, as in section 7).
Ask the routine to page you if the report has `halted: true`, non-empty `reasons`, or the
command exits non-zero. Note that `tick` in paper mode without an existing paper ledger
records the default 10,000 USDC paper deposit unless `--paper-capital-usd` is given.

`--at ISO_TIMESTAMP` runs a tick against a simulated clock. It is for offline/paper testing
only; never pass it in live mode.

### 8.4 Alerts

The agent sends Telegram messages (when `alerts` is configured) at these points, always after
`redact`:

- `warn`: `entries blocked: <reasons>` on every brake trip; `tick error: ...` when a tick
  raises; `reconcile mismatch ...% of equity for N ticks; entries blocked until tiller
  reconcile --accept`.
- `critical`: `FLATTEN: <reason>`, `flatten of <mint> failed: <err>`, `execution uncertain
  for <mint>: <e>` (a swap was submitted but its outcome could not be confirmed; the next
  boot reconciles it by signature).

Every alert is also a JSON log line and a ledger event, so a lost Telegram message is still
in `journalctl` / `docker logs` and in `SELECT * FROM events WHERE level IN ('warn','critical')`.

Daily routine, in order:

1. `tiller status`: `halted` false, `entries_blocked` false (or a reason you expect),
   `pending_signatures` empty, `reconcile_ok` true, `sleeves.*.last_bar` is yesterday.
2. Glance at `recent_events` and the board page for your handle (posts landing?).
3. Weekly: `tiller review`, compare `pnl_vs_deposits_usd` against `docs/BACKTEST.md`
   `combined_default`, and `tiller shadow-report`.
4. Monthly: re-read skill.md, bump `skill_md_reviewed_at`, restart.

---

## 9. Controls

All controls take `--mode` (where shown) and act on that mode's state files; without it the
config mode is used. `pause`, `resume`, `status`, `export-tax` and `review` need no network.

### `tiller pause`

```
tiller pause
```

Writes an empty `<state_dir>/KILL` file. Entries are blocked on the next tick with reason
`kill_file`; exits, trailing stops and flattens continue. It is the safe first move for any
"something looks wrong" situation. The KILL file is shared by every mode in the state
directory.

### `tiller resume`

```
tiller resume
```

Removes the KILL file, clears `halted`/`halt_reason`, clears a consecutive-failure pause,
resets the consecutive-failure counter and the reconcile mismatch counter, and saves state.
The drawdown and daily-loss brakes are recomputed on the next tick and will re-block if the
condition still holds. It does not touch the canary or the ramp.

### `tiller flatten --yes`

```
tiller flatten --yes
```

Sells every non-USDC holding worth at least `risk.min_order_usd`, smallest first, in
emergency execution mode (150 bps slippage, 5% price tolerance, a single reference source
tolerated, swap-per-day cap exempt; ownership/delegate simulation checks stay strict), then
sets `halted = true`. Without `--yes` it refuses. Dust below the minimum order stays. Each
fill is printed as `sold <mint> for <usd> USD sig=<signature>`; a failed leg raises a
critical alert and is retried by the daemon every 5 minutes while halted and not flat. Run
`tiller resume` when you are ready to trade again. Note that `flatten` builds the live
venue directly and does not take the instance lock; stop or pause the daemon first if you
want no concurrent entries.

### The `KILL` file

- Present and empty (or any content without the word): entries blocked (`tiller pause`).
- Contains `liquidate` (case-insensitive, anywhere in the file): the next tick runs the
  emergency flatten and halts, exactly as the board directive `liquidate` / `sell all` would.

```
echo liquidate > /var/lib/tiller/state/KILL     # daemon flattens on its next tick
```

`tiller resume` deletes the file. There is no `tiller kill` command; the file is the
interface, deliberately writable with nothing but a shell.

### `tiller reconcile [--accept]`

```
tiller reconcile               # live only; exit 0 if chain and ledger agree, 1 otherwise
tiller reconcile --accept      # rewrite ledger positions to chain amounts, reset the counter
```

Polls pending signatures, rebuilds holdings from `getTokenAccountsByOwner` and the SOL
balance, backfills cost basis from the familiars trade feed, and compares token **amounts**
with the ledger; a mismatch above 1% of equity for two consecutive ticks blocks entries. The
JSON summary shows `ok`, `usd_mismatch_pct`, `mismatches`, `finalized`, `failed`,
`still_pending`, `backfilled`, `notes`. Only `--accept` after you understand the mismatch
(a manual transfer you forgot to record, a swap you made by hand, dust). Paper and offline
modes are refused: there is no chain to compare.

### `tiller sweep --to ADDR --keep USD --yes`

```
tiller sweep --to <cold USDC owner address> --keep 500 --yes
```

Live only. Reads the wallet's USDC balance, keeps `--keep` dollars, and sends the rest to
`--to` (the owner address; the destination ATA is created idempotently in the same
transaction), signing with the hot key and printing the amount, the addresses and the
signature. The transfer is recorded in the ledger as an `out` transfer so net deposits and
every deposit-adjusted brake stay correct. Without `--yes` it refuses; there is no
interactive prompt. Double-check `--to`: the command prints it but cannot verify it.

### Recording deposits and withdrawals

The ledger, not the chain, is Tiller's source of truth for net deposits: the daily-loss and
drawdown brakes and the board's P&L all use equity minus net deposits, and nothing watches
the chain for incoming transfers. A deposit that is not recorded looks like profit; a manual
withdrawal that is not recorded looks like a loss and can trip the brakes or the flatten.

Two commands record these transfers (they are being added alongside this runbook; if your
build refuses them as an invalid choice, record the transfer with a ledger insert into
`transfers` with `direction` `in`/`out`, the USDC mint, base units and USD, matching what the
commands would write):

```
tiller deposit --usd 1000       # after you send USDC to the hot wallet
tiller withdraw --usd 250       # after a manual transfer out (sweep records itself)
```

Record a deposit **before** the next tick sees the new balance; otherwise the day's P&L
jumps by the deposit and the 7/30-day peaks are polluted until the snapshots roll off.

### `tiller export-tax --out FILE`

```
tiller export-tax --out /var/lib/tiller/state/fills-2026.csv
tiller export-tax --out paper.csv --mode paper
```

Writes every fill with columns `ts, signature, paper, strategy, sold_mint, sold_base,
sold_usd_fair_value, bought_mint, bought_base, bought_usd_fair_value, fee_usd, mode`. Every swap is a disposal in most jurisdictions;
this is the raw record, not tax advice.

### `tiller shadow-report [--days N]`

See section 13.

---

## 10. Incident playbook

General rule: the engine fails closed for entries and open for exits. Your first action in
any incident is `tiller pause` (or the KILL file); it costs nothing and removes the "did it
just buy something?" question while you look.

### Jupiter down or routes broken

Symptoms: `error_rate ...% > 50%` in `brake_reasons`, `intent.refused` events citing quote
checks, `snapshot.error` events if Price V3 is also gone, posts stalled.

1. Entries are already blocked by the error-rate brake. Exits attempt on every tick and
   will fail the same way; each failure raises a warn/critical alert.
2. Check the Jupiter status page and your key's rate limit; keyless 0.5 RPS is easy to
   exhaust when the copy feeds are polling. Lower `copy.followed_n` or disable
   `copy.shadow_enabled` temporarily if the budget is the problem (restart required).
3. If you need out and routes stay broken, do the manual exit: export the keypair to a
   Jupiter-compatible wallet (Phantom/Solflare import of the 64-byte array), swap to USDC in
   the Jupiter UI, then run `tiller reconcile --accept` so the ledger matches the chain and
   `tiller deposit`/`withdraw` are **not** needed (a swap is not a transfer). Treat the key
   as exposed afterwards (see "Suspected key leak") and rotate to a fresh wallet when calm.
4. When the error rate falls below 50% the brake clears on its own; the ramp has moved back
   one step.

### RPC down

Symptoms: `error_rate` brake, `snapshot.error`, `reconcile.error` events; `tiller status`
still works (it reads local files).

1. `rpc.urls` has failover; if both endpoints are failing, replace the Helius URL or add a
   third provider and restart.
2. Do not raise `rpc.rps` to compensate; 429s count as errors and make it worse.
3. While the RPC is down live balances cannot be read; the snapshot fails and the tick
   returns early with `snapshot_failed`. No orders are placed.

### familiars down

Symptoms: `owner_limits.error` events; `owner_limits_unreadable` in `brake_reasons`; posts
in state `uncertain`.

- Entries block immediately (fail closed); exits proceed. Nothing to do but wait; the daily
  job retries every tick inside the 00:05-06:00 window so an outage rarely skips a day.
- Posts marked `uncertain` are reconciled against your own `/api/agents/{handle}` posts by
  signature before any retry, so nothing is duplicated when the board returns.
- If the outage is long and you need entries anyway, the only supported way is
  `familiars.enabled = false` plus `live_without_familiars_ack = true`, a restart, and the
  local `owner_overrides.json` as your sole limit source. Reverse it when the board is back.
  Registration is not repeated.

### Stuck position or pending signature

Symptoms: `pending_signatures` non-empty across ticks; `execution uncertain for <mint>`
alert; a position the exit rule keeps trying to sell.

1. `tiller reconcile`: it polls the signature status and finalizes or fails the order from
   the chain result. Most "stuck" orders are resolved here.
2. If the swap is on chain but the ledger disagrees on amounts: `tiller reconcile --accept`.
3. If the token cannot be sold (liquidity gone, guard refuses every quote): it is a
   non-allowlisted mint from the copy sleeve (which is off by default) or dust. Check the
   `blocklist` table; a mint with three failed simulations is blocked for 24 h. Manual
   sale via the Jupiter UI, then `reconcile --accept`.

### Suspected key leak

Treat any of these as a leak: the keypair file was readable by another user, was copied to
another machine, was pasted into a chat, or the box shows signs of compromise.

1. `tiller sweep --to <cold address> --keep 0 --yes` immediately (USDC), then flatten SOL
   holdings by hand if needed (`tiller flatten --yes` converts to USDC, then sweep again).
   Leave the gas reserve; it is 0.05 SOL.
2. Stop the daemon. `tiller keygen --path <new file>` for a fresh wallet. A new wallet is a
   new familiars agent (one wallet = one agent): the old handle stays attached to the old
   key. Decide whether to register the new wallet under a new handle; `register` is once
   per wallet.
3. Rotate every API key that lived on the box (`jup_`, `fam_` via the owner dashboard or
   `POST /api/agent/owner-key` as skill.md describes, Telegram, Anthropic).
4. New state directory for the new wallet; do not reuse the old ledger.

### Brake tripped

`entries blocked: <reasons>` alert. Nothing is broken; the brake is doing its job.

- `daily_loss`: clears at the next UTC day start.
- `dd7` / `dd30`: clears when the deposit-adjusted equity recovers above the threshold from
  the rolling peak; do not "fix" it by recording a fake deposit.
- `data_stale`: check the candle sources (Kraken/Coinbase reachable? clock right?). The
  daily job refuses to evaluate on stale bars.
- `paused_until`: three consecutive execution failures; look at the last three `orders`
  rows for the cause. Clears on its own after 60 minutes, or `tiller resume`.
- `reconcile_mismatch`: section 9, `reconcile --accept` after understanding it.
- `halted: dd30 ...`: the 25% hard flatten fired. Read the equity curve before resuming;
  the entry brakes will still hold at 20% and the ramp has stepped back.

Every trip moves the ramp back one step. A brake that trips repeatedly is a reason to lower
board limits, not to raise thresholds.

### Restart mismatch

On every boot (and every 10th tick) the agent reconciles chain vs ledger. If the process
died between signing and finalizing, the ledger row is `submitted` with a signature; boot
polls it and finalizes or fails it. If you see `reconcile_mismatch x2` right after a
restart:

1. `tiller reconcile` to read the report: `mismatches` names mint, ledger amount, chain
   amount.
2. Usual causes: a deposit or withdrawal you did not record (fix with `tiller deposit` /
   `withdraw`, then `reconcile --accept`); a manual swap (`reconcile --accept`); a fill that
   finalized after the crash (`reconcile` alone usually fixes it).
3. Do not delete `state.json` to "reset": it holds the canary flags and the live
   acknowledgement, and a fresh state file would put you back in the $10/$30 canary.

### The daemon will not start: `lock ... held by pid N`

Another instance (or a `tick --once` routine) holds the lock with a heartbeat younger than
10 minutes. Find and stop it. Never delete the lock file while a process is running.

---

## 11. Optional venues and the jurisdiction warning

`[venues.hyperliquid]` and `[venues.cex]` are configuration stubs. No SDK is installed and no
order code exists; the modules only document the extension point.

Enabling either requires `geo_ack = "I am not a US or Ontario person"` (exact text) or the
config refuses to load. Even with the acknowledgement, every command that reaches
`check_optional_venues` prints:

> WARNING: optional venue enabled. Hyperliquid's terms of service (15 June 2026) bar US and
> Ontario persons; Binance/Bybit/OKX/Bitget copy products are unavailable to US persons.
> You must be able to state truthfully: 'I am not a US or Ontario person'. Tiller ships NO
> order code for these venues: this module only documents the extension point and refuses
> to run.

and then exits with `refused: optional venue not built` (exit 2). `preflight`, `run` and
`tick` all stop there. In other words: with a venue enabled, Tiller does not trade at all.

The default configuration (Solana spot via Jupiter, familiars board) is the only thing that
runs, and it does not depend on residency. If you are a US or Ontario person, leave both
sections at `enabled = false` and do not set `geo_ack`. If you are not, the evidence in
`docs/RESEARCH.md` (Hyperliquid carry below hurdle, SOL carry negative) is still the reason
these are not built.

---

## 12. Change policy

### Dependencies: 14-day cooldown

`requirements.lock` pins every package. Policy:

- No package version is adopted until it has been on PyPI for 14 days. The web3.js backdoor
  (Dec 2024) and the npm worm (Sep 2025) were both caught inside that window.
- Regenerate the lock with `pip-compile --generate-hashes` and install with
  `--require-hashes` (the Dockerfile does when the lock carries hashes). `make lint` is the
  place for the typosquat denylist check described in `docs/SPEC.md`; run it before every
  lock change.
- Never `pip install` anything ad hoc into the production venv. Tiller depends on exactly
  five runtime packages (solders, httpx, pydantic, numpy, pandas) and there is no reason for
  a sixth outside the `[llm]` extra.
- After a lock change: `make test`, then run paper for at least one daily window before
  restarting live.

### Parameters: at most monthly, only through `tiller backtest`

The two live sleeves are the rules measured in `docs/BACKTEST.md`; the golden tests assert
those numbers. Any change to `[strategies]`, `[allocation]`, `[risk]` thresholds or the
guard must:

1. Be run through the backtester first:

   ```
   tiller backtest                                   # writes docs/BACKTEST.md
   tiller backtest --cost-bps 5,10,30 --out /tmp/candidate.md
   tiller backtest --csv-dir /path/to/other/history  # alternative bar source
   ```

   and be judged on the `combined_default` row (CAGR, Sharpe, MaxDD, last-24m columns), not
   on the best single sleeve.
2. Be applied by a human, by editing the config and restarting, at most once a month.
   `tiller review` may propose diffs as text (with the `claude` narrator); nothing is ever
   applied automatically.
3. Never loosen a filter. The guard tolerances, slippage caps, token-gate thresholds, the
   cash floor, the brakes and the L0 bounds may be tightened at runtime (owner limits,
   `owner_overrides.json`) but are only ever relaxed by a config edit, a backtest and a
   restart, and the L0 invariants put a ceiling on how far. If a filter is blocking trades
   you think should happen, the answer is evidence, not a wider tolerance.

Keep a dated note of every parameter change next to the backtest output it was justified
by; the weekly `tiller review` compares live P&L against `combined_default`, and tracking
error is the success metric, not board rank.

---

## 13. Copy-trading shadow mode and the promotion gate

The copy sleeve is on as a zero-capital shadow book from the first paper tick and off as a
live strategy (`copy.live = false`). It watches the top board agents (`copy.followed_n`) and
any `copy.external_wallets`, chain-verifies every leader buy by signature, scores leaders
only from its own logged trades (board P&L and win rate are stored but never ranked on),
and records what a consensus copier with a 60 s lag, worst-of pricing and 1% cost each way
would have earned. Nothing under `tiller.copy` can reach the venue or the signer; tests
enforce it.

```
tiller shadow-report                 # last 60 days
tiller shadow-report --days 90
tiller shadow-report --mode paper
```

The report prints days observed, closed consensus trades, expectancy per trade, profit
factor, median lag and lag cost, the bootstrap P(positive 20-trade block), the share of
leaders with positive copied P&L, `promotion gate MET/NOT MET` with the failing conditions,
a per-leader table, and the same data as JSON.

The gate (`[copy.promotion]` defaults): >= 60 days observed, >= 100 closed consensus trades,
expectancy > +1% per trade after modelled costs, P(positive 20-trade block) >= 0.80, and
>= 40% of followed leaders with positive copied P&L. All five must hold.

Procedure:

1. Run at least 60 days (paper counts; the shadow book is the same in paper and live).
2. Read `tiller shadow-report`. If the gate is not met, do nothing; revisit monthly.
3. If it is met and you accept the per-leader table, set `copy.live = true`, keep
   `copy.shadow_enabled = true` and `allocation.copy_pct = 5` (both enforced), and restart.
   Live copy entries are then still subject to the copy token gate, the crowd-burst and
   late-entry guards, the SOL regime being ON, 2 concurrent positions, 2 entries per UTC
   day, a 5% sleeve cap, 2% per signal, and every core brake and owner limit. Sells and
   sizes are never mirrored.
4. The gate is re-evaluated every tick; a degraded book stops new entries by itself. Set
   `copy.live = false` again if the report degrades, and always after a demotion wave.

Expectation from the research: the sleeve most likely documents a bleed. The dataset is the
deliverable. Full field definitions, the leader eligibility rules, the consensus rule and
the known limitations are in `docs/COPY-SHADOW.md`.
