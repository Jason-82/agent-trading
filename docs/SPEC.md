# Tiller build specification

Synthesized from a three-proposal, three-judge design panel run on 2026-09-24 over the research in `docs/RESEARCH.md`. This is the contract the code is built against.

## Decision summary

FINAL SPEC = Tiller (mvp-first, highest score) as the base, with the judge-requested grafts from Bulwark and Keel and every critique resolved. Package/CLI name: `tiller`, repo /home/user/agent-trading, Python 3.11 only.

Core decisions:
1. Two LIVE sleeves, both exactly the rules measured offline in scratchpad/analysis/RESULTS.md (Study 1), so the golden regression tests certify the live code: (A) `sol_trend_ensemble` = donchian_ensemble_voltarget25 (7 Donchian sleeves, midpoint trailing stops, 0.25 vol target on rv90, exposure of TOTAL equity clipped to a 40% sleeve cap that rarely binds); (B) `sol_regime_switch` = sma200_hyst2pct on SOL, 25% of equity when ON. The allocator nets A+B into ONE SOL target weight and applies the portfolio SOL-beta cap min(0.50, 0.30/sigma90_SOL) (Bulwark), so the worst observed 30-day SOL window (-46%) costs at most ~17% of equity, inside the 20% entry brake and under the 25% hard flatten. Resolves quant critique (a) (untested default combination): no unmeasured combination is the default. `btc_confirm` (BTC SMA200 gate) is a config flag; WP-A's backtester reports it; WP-F sets the default to true ONLY if docs/BACKTEST.md shows last-24m Sharpe and max DD both not worse than the unconfirmed variant, else false (deterministic rule, documented).
2. Rebalance band = max(2% of equity, $10) on the netted SOL target (resolves quant critique (b): one Donchian step is ~4.4% of equity at SOL vol, so every step trades); the backtester models the band and reports with/without.
3. Cash floor 30% (L0 invariant), copy sleeve 5% budget (0 deployed until promoted, live=false), breakout 4h sleeve NOT wired live this session (pure signal + Study-2 regression only, stretch), disabled sleeves' budgets revert to cash by construction (Keel). Allocation table sums to 100 and is validated at load.
4. Copy-trading: always-on shadow tracker with zero capital (user requirement); leaders scored ONLY from our own logged trades via lag-adjusted copied-P&L replay (Bulwark), board pnl/winRate stored but never used for ranking; eligibility needs >=14 days on our log; every familiars leader trade is cross-verified on chain by signature before it counts (Keel); consensus of >=3 unrelated clusters (verifier), crowd-burst exclusion (+>15 agents/1h on /api/tokens), late-entry guard (<=15% above earliest leader fill), own exits only (leader sells only tighten the trail, never trigger an exit: resolves the 'partial sell mirroring' critique), promotion gate = >=60 days AND >=100 shadow trades AND expectancy >+1%/trade after modelled costs AND bootstrap P(positive 20-trade block)>=0.80 AND >=40% of followed leaders with positive copied P&L.
5. Execution: Tiller's guarded pipeline plus Keel's instruction program-id allow-list and 'no instruction targets our ATAs as delegate/close/owner' check, plus an EMERGENCY exit mode (risk-officer gap none of the three solved): flatten/exit orders use a wider price tolerance (5% vs 1%) and slippage 150 bps against a live Kraken mid reference while ownership/delegate/closeAuthority simulation assertions stay strict; quote reference for SOL/USDC is a live CEX mid (Kraken ticker) cross-checked with Jupiter Price V3, never the stale daily candle.
6. Risk engine grafts: config invariants refuse to boot on unsafe caps (L0); loop guards (max 1 entry per tick, max 6 swaps per UTC day with emergency exits exempt, 3 consecutive execution failures -> 1 h pause, 3 failed simulations on one mint -> 24 h blocklist); owner limits fail closed IMMEDIATELY for entries (the daily job retries every 15 min inside a 00:05-06:00 UTC window, so a blip does not skip the day); hard flatten from the rolling 30-day peak (not all-time: resolves Bulwark critique); canary = verified-event gate (one buy, one sell, one accepted post on chain/board) THEN a 25/50/100% weekly ramp with reset on any brake trip; restart reconciliation compares token AMOUNTS chain-vs-ledger (not USD marks) and blocks entries only if the mismatch exceeds 1% of equity for 2 consecutive ticks (resolves Bulwark false-halt critique); tighten-only rule for runtime overrides; Shield unreachable = block for non-allowlisted mints; RPC mint-account parse is the binding token gate independent of Tokens V2.
7. Dependencies pinned to what pypi serves in this sandbox (verified 2026-09-24): solders 0.29.0, httpx 0.28.1, pydantic 2.13.5, numpy 2.4.6, pandas 3.0.6 (backtest reports only), pytest 9.1.1, pytest-asyncio 1.4.0, respx 0.23.1; NO solana-py, NO ccxt, NO pyyaml (config is TOML via stdlib tomllib), anthropic 1.8.0 optional extra. Resolves engineer critique (e).
8. Ops: `tiller sweep --to ADDR --keep USD --yes` non-interactive (engineer critique (f)); `tiller tick --once` for Claude Code routines; `tools/record_fixtures.py` (secret-redacting) is a required WP-F deliverable, not stretch (critique (h)); raw persistence of every familiars/Jupiter read before parsing (Keel).
9. Scope reality: 6 work packages, ~1-1.5 h each at 2 concurrent agents; the breakout satellite, JitoSOL hold asset and Hyperliquid read-only feed are explicitly disabled-by-default/stretch; Hyperliquid/CEX execution is config-only (enabled=false, geo_ack required, raises NotImplementedError) with no SDK dependency, so no untested order code ships.
10. Honest expectations to print in README: combined core 8-25%/yr in trending years, roughly 0 to +8% in 2025-type years, 6-12 month flat spells, portfolio max DD 15-25%; copy sleeve expected to document a bleed; no path to the top of an absolute-USD board at $500-20k.

## Language and stack

Python 3.11, one package `tiller/` under src layout, one test runner, no Node, no research sidecar. Justification (all three judges concur): (1) constraint 2 (clean room) removes the only argument for TypeScript, which was reusing the Ballast client/executor; (2) the measured evidence, the data (scratchpad/data/normalized) and the study conventions (signal on closed bar, fill at next open, cost per side on turnover; scratchpad/analysis/common.py) are Python, so the live signal functions ARE the functions the golden regression tests call, eliminating backtest-vs-live drift; (3) the Solana surface is HTTPS JSON + one ed25519 message signature + one VersionedTransaction signature, which solders covers in ~30 lines; (4) supply-chain: ~15 transitive packages (solders is one Rust wheel) vs hundreds for a web3.js tree (Dec-2024 web3.js backdoor, Sept-2025 chalk/debug/Shai-Hulud, PyPI solders monkey-patch precedents); (5) the only optional second venue (Hyperliquid) is Python-SDK-native.

Pinned versions (verified installable from pypi.org in this sandbox on 2026-09-24): solders==0.29.0 (Keypair, Pubkey, VersionedTransaction, Signature; signing and deserialisation ONLY), httpx==0.28.1 (all HTTP: Jupiter Swap V2 / Price V3 / Tokens V2 / Shield, Solana JSON-RPC, familiars, Kraken/Coinbase public), pydantic==2.13.5 (config + every wire model, extra='ignore' with required fields so drift fails loudly), numpy==2.4.6 (indicators, hot path), pandas==3.0.6 (only imported inside tiller.backtest for reports), stdlib sqlite3 (ledger), stdlib tomllib (config). Dev: pytest==9.1.1, pytest-asyncio==1.4.0, respx==0.23.1, ruff. Optional extras: [llm] anthropic==1.8.0; [hl] hyperliquid-python-sdk (NOT installed by default, no code uses it this session). Explicitly excluded: solana-py (four JSON-RPC methods over httpx instead, FakeRpc trivial), ccxt (Kraken/Coinbase public OHLC is two GETs), pyyaml, hypothesis (no-lookahead tests are parametrised instead). Lockfile: requirements.lock generated with pip-compile --generate-hashes; install with --require-hashes; 14-day dependency cooldown policy in the runbook. Runtime: single asyncio process, Docker python:3.11-slim non-root read-only FS except /state, or systemd unit; `tiller tick --once` for a Claude Code routine.

## Repository layout

```
/home/user/agent-trading
├── pyproject.toml                # package `tiller`, console script `tiller`, extras [dev],[llm]
├── requirements.lock             # pip-compile --generate-hashes output (pinned versions above)
├── Makefile                      # make install|test|lint|backtest
├── Dockerfile                    # python:3.11-slim, non-root, read-only except /state
├── deploy/tiller.service         # systemd unit (Restart=on-failure)
├── config/tiller.example.toml    # full default config with comments (copied to config/tiller.toml by `tiller init`)
├── config/owner_overrides.example.json  # local maxPositionUsd/dailyLimitUsd/instructions for users without a familiars account
├── docs/RESEARCH.md              # existing sourced report (unchanged)
├── docs/familiars-api.md         # existing API contract (unchanged)
├── docs/SPEC.md                  # this specification rendered as markdown (WP-F)
├── docs/BACKTEST.md              # generated by `tiller backtest` (WP-A)
├── docs/RUNBOOK.md               # VPS/Docker, secrets, keys, register, paper 14 d, canary+ramp, skill.md checklist, geo warning, sweep, tax, incidents (WP-F)
├── docs/COPY-SHADOW.md           # what the shadow tracker records, promotion gate, how to read `tiller shadow-report` (WP-E)
├── README.md                     # what it is, measured numbers, expectations, what it will not do
├── src/tiller/__init__.py
├── src/tiller/config.py          # TOML+env settings, L0 invariants, allocation table
├── src/tiller/models.py          # shared pydantic wire/domain models
├── src/tiller/clock.py           # Clock protocol, SystemClock, SimClock
├── src/tiller/ledger.py          # SQLite ledger (orders, fills, positions, transfers, equity, raw_snapshots, leader_trades, shadow_trades, posts, events)
├── src/tiller/state.py           # atomic JSON hot state + InstanceLock
├── src/tiller/alerts.py          # JSON logging + optional Telegram (alerts only, no commands)
├── src/tiller/data/candles.py    # Kraken primary / Coinbase fallback / CSV; closed daily bars; stale check
├── src/tiller/data/prices.py     # Jupiter Price V3 + Kraken ticker mid; reference_price()
├── src/tiller/data/tokens.py     # Tokens V2, Shield, mint account fetch, evaluate_token_gate()
├── src/tiller/strategies/indicators.py  # pure numpy: sma, ema, atr_wilder, donchian_*, realized_vol, hysteresis_state
├── src/tiller/strategies/base.py        # TargetExposure, ExitRule, MarketContext, Strategy protocol
├── src/tiller/strategies/sol_trend.py   # sleeve A (Donchian ensemble vol-target) + sleeve B (SMA200 hysteresis) + vectorised exposure functions
├── src/tiller/strategies/breakout_4h.py # STRETCH: pure 4h breakout signal (Study 2), no live wiring
├── src/tiller/portfolio/allocator.py    # nets sleeve targets, SOL-beta cap, cash floor, rebalance band, exits-first order plan
├── src/tiller/risk/account.py           # AccountSnapshot builder (equity minus net deposits, peaks, day stats)
├── src/tiller/risk/engine.py            # brakes, sizing, loop guards, canary+ramp, directives, must_flatten (pure)
├── src/tiller/risk/reconcile.py         # chain vs ledger vs familiars reconciliation (amount-based)
├── src/tiller/execution/rpc.py          # minimal JSON-RPC over httpx, primary/fallback, token bucket
├── src/tiller/execution/jupiter.py      # Swap V2 /order /execute, round-trip probe, typed errors
├── src/tiller/execution/guard.py        # pure pre-sign checks: quote, signers, instruction allow-list, simulation, mint
├── src/tiller/execution/wallet.py       # Signer protocol, FileSigner (only solders signing importer), NullSigner
├── src/tiller/execution/venue.py        # Venue protocol, LiveSolanaVenue, PaperVenue, FixtureVenue, realised_fill_from_tx
├── src/tiller/familiars/client.py       # typed client, token bucket, raw snapshot persistence, no register retry
├── src/tiller/familiars/poster.py       # post queue, 90 s delay, dedupe/uncertain reconciliation, daily note
├── src/tiller/familiars/narrator.py     # TemplateNarrator default, ClaudeNarrator optional, validate_post_text
├── src/tiller/copy/models.py            # LeaderTrade, LeaderScore, ShadowTrade, ShadowReport
├── src/tiller/copy/feeds.py             # FamiliarsFeed (signature-verified), WalletFeed, parse_swap()
├── src/tiller/copy/leaders.py           # eligibility from own logs, replay scoring, clustering, sticky pool, demotion
├── src/tiller/copy/shadow.py            # ShadowTracker: hypothetical fills, own exits, marks, lag cost, promotion gate, bootstrap
├── src/tiller/copy/strategy.py          # ConsensusCopyStrategy (live=false default), consensus(), crowd_burst(), late_entry_ok()
├── src/tiller/engine.py                 # Agent.tick/run_forever/flatten; job scheduling
├── src/tiller/backtest/runner.py        # vectorised daily backtester sharing the live exposure functions; report renderer
├── src/tiller/cli.py                    # argparse CLI; the only place real deps are wired
├── src/tiller/venues_optional.py        # Hyperliquid/CEX config gate: geo warning, raises NotImplementedError (no SDK)
├── tools/record_fixtures.py             # user runs online: re-records fixtures with secrets redacted
├── tests/conftest.py                    # no-network guard, SimClock, tmp ledger/state, fixture loaders
├── tests/fixtures/ohlcv/{SOLUSDT,BTCUSDT,ETHUSDT}_1d.csv   # copied from scratchpad/data/normalized (full history)
├── tests/fixtures/ohlcv/SOLUSDT_1h.csv                   # only if breakout stretch is built
├── tests/fixtures/jupiter/*.json        # order_ok, order_high_impact, order_fee50, execute_ok, execute_fail, price_v3, tokens_v2_*, shield_*
├── tests/fixtures/rpc/*.json            # sim_ok, sim_delegate_set, sim_owner_reassigned, sim_close_authority, sim_sol_overdraw, sim_output_short, sim_unrelated_decrease, tx_swap_buy, tx_swap_sell, tx_transfer, tx_multihop, mint_spl, mint_t2022_*
├── tests/fixtures/familiars/*.json      # challenge, register, me, me_pause, agents_7d, agents_30d, agent_detail, tokens, tokens_burst, post_ok, post_429
├── tests/fixtures/kraken/*.json         # ohlc_solusd, ohlc_xbtusd, ticker_solusd
├── tests/fixtures/expected/study1_sol.json  # pinned Study-1 numbers for golden tests
├── tests/fakes.py                       # FakeRpc, FakeJupiter, FakeFamiliars, FakePrices (in-process, scriptable)
├── tests/test_*.py                      # per module (see module contracts)
└── .gitignore                           # existing (secrets, state, caches)
```

## Strategy portfolio

| Strategy | Role | Allocation | Venue |
|---|---|---|---|
| sol_trend_ensemble (daily Donchian ensemble, 25% vol target; = Study 1 donchian_ensemble_voltarget25) | core | 40% | Solana spot hold_mint (SOL default; JitoSOL only after the user verifies board eligibility) vs USDC via Jupiter Swap V2 from the familiars wallet; paper mode uses the same live quotes unsigned |
| sol_regime_switch (daily SMA200 with 2% hysteresis; = Study 1 sma200_hyst2pct) | core | 25% | Same wallet, same SOL/USDC Jupiter Swap V2 route (2 bps Jupiter fee; 5-15 bps measured round trip) |
| Netting + portfolio SOL-beta cap (allocator, not a sleeve) | core | 0% | Allocator |
| USDC cash floor (raw USDC in wallet + 0.05 SOL gas reserve) | core | 30% | Wallet balance; Kamino/Aave parking deliberately NOT built (board eligibility of kUSDC unverified, withdrawal-liquidity risk, no Python SDK) |
| copy_consensus: familiars board leaders + on-chain wallet feed, always-on shadow tracking, live disabled | disabled-by-default | 5% | Signals: familiars public API (/api/agents 7D/30D every 15 min, /api/agents/{handle} for followed handles every 60 s, /api/tokens every 60 s) + Solana RPC getSignaturesForAddress/getTransaction for config copy.external_wallets; execution (only if promoted and copy.live=true): Solana spot via Jupiter Swap V2, 10 bps fee tier only (never <24 h tokens) |
| breakout_4h liquid-token satellite | disabled-by-default | 0% | Not wired live this session; pure signal + Study-2 regression only (stretch in WP-A). Post-MVP: GeckoTerminal 4h bars, Tokens V2 universe screen, paper-only until 100 paper fills with realised round trip <= 1% and PF > 1.2 |
| Hyperliquid perps / CEX spot modules | disabled-by-default | 0% | Config-only stubs (venues.hyperliquid.enabled=false, venues.cex.enabled=false); enabling requires geo_ack='I am not a US or Ontario person', prints the ToS warning (Hyperliquid ToS 15 Jun 2026 bars US/Ontario persons; Binance/Bybit/OKX/Bitget copy products unavailable to US persons) and raises NotImplementedError; no SDK installed |

### sol_trend_ensemble (daily Donchian ensemble, 25% vol target; = Study 1 donchian_ensemble_voltarget25)

Inputs: closed daily UTC bars for SOL (Kraken primary, Coinbase fallback, bundled CSV warm-up), >=300 bars. Seven sleeves N in {10,20,30,60,90,150,250}: sleeve enters when close_t > max(high[t-N..t-1]); stop = Donchian midpoint of the last N bars at entry, then max(prev stop, midpoint) each day; exits when close_t < stop. frac = long sleeves / 7. Vol scale s = min(1, 0.25 / rv90) with rv90 = stdev(90 daily log returns)*sqrt(365). Target weight of TOTAL equity = min(0.40, frac * s). Evaluated once per closed bar inside the 00:05-06:00 UTC daily window (retries every 15 min if data/limits unavailable); never on an unclosed bar; refuses if candles older than 26 h. Optional btc_confirm (BTC SMA200 2% hysteresis gate on NEW sleeve entries only) default per the BACKTEST.md rule in decision_summary. Measured (SOL 2021-04-18..2026-09-23, 30 bps/side, full equity): CAGR 14.8%, vol 12.1%, Sharpe 1.20, MaxDD -14.2%, 2022 -6.0%, 2025 -3.6%, 2026 YTD +4.5%, last-24m CAGR 2.1%/Sharpe 0.27, avg exposure 0.07, turnover 2.4x/yr. Expectation: 5-15%/yr in trending years, ~0 in chop, 6-12 month flat spells.

### sol_regime_switch (daily SMA200 with 2% hysteresis; = Study 1 sma200_hyst2pct)

State per asset: OFF->ON when close > SMA200*1.02; ON->OFF when close < SMA200*0.98; else unchanged. Target weight = 0.25 when ON else 0. Optional btc_confirm: SOL ON requires BTC state ON for entries (exit on SOL only). One evaluation per closed bar, at most one switch per day. Measured (SOL, 30 bps): CAGR 57.9%, Sharpe 0.98, MaxDD -69.1% (full exposure; at 25% weight and under the beta cap the portfolio contribution to DD is bounded by ~17%), 16 round trips in 5.4 years, 2022 -20.7%, 2025 -17.4%, 2026 YTD +34.3%, last-24m CAGR 9.9%/Sharpe 0.44. Role: cheapest public SOL-beta track record on the board plus the regime gate under which every other long sleeve (copy, breakout) runs.

### Netting + portfolio SOL-beta cap (allocator, not a sleeve)

target_SOL = min(A + B, min(0.50, 0.30/sigma90_SOL)); at sigma90 = 0.80 the cap is 0.375, so the worst observed 30-day SOL window (-46%) costs at most ~17% of equity. Non-cash total <= 70%. Rebalance only when |target - actual| >= max(2% of equity, $10). Expected combined: 8-25%/yr in trending years, roughly 0 to +8% in 2025-type years, portfolio max DD 15-25%. The backtester reports the netted 'combined_default' rule alongside its components so README expectations come from measured numbers.

### USDC cash floor (raw USDC in wallet + 0.05 SOL gas reserve)

Hard floor: no entry may take USDC below 30% of equity (L0 invariant, allocator scaling, and risk.check_entry). Disabled sleeves' budgets (copy 5% until promoted, breakout 0%) sit here. 0.05 SOL never swapped. Hurdle rate is 0% in the MVP; the runbook lists Kamino main-market parking as a post-MVP option once skill.md confirms eligibility.

### copy_consensus: familiars board leaders + on-chain wallet feed, always-on shadow tracking, live disabled

SHADOW (default, zero capital): every leader buy counted only after chain verification by signature (drop if unconfirmed after 5 min); eligibility computed from OUR logs only after >=14 days: >=30 closed round trips, >=10 distinct mints, median hold >=4 h, no mint >50% of gross MTM P&L, platform snapshot DD <30%, zero buys of <24 h mints, no own-token (Tokens V2 dev == leader wallet), no transfer-in near P&L jumps. Score = mean rank(replay PF, replay Sortino, weekly-positive share) where replay = each buy re-priced at detection+60 s at worst-of(our quote, leader price*1.01) + 1% slippage + 10 bps, exited by our rules; require PF>=1.3 on >=20 signals; board pnl/winRate stored, never ranked on; sybil clustering (>80% mint overlap = one vote); sticky pool top-20 in / below 30 out. Consensus signal = >=3 distinct clusters buying mint M within 30 min AND token gate (copy profile: organic>=50, liquidity>=$250k, age>=24 h, audit clean, round trip<=1.5%, no Token-2022 traps) AND no crowd burst (/api/tokens agent count +<=15 in 1 h) AND late-entry guard (price <= 15% above earliest leader fill) AND SOL regime ON. Shadow trades opened at worst-of price minus 1% cost, marked 1h/6h/24h, exited by stop -20% / trail 25% from +30% / time stop 48 h / liquidity collapse 60%; leader sells only tighten the trail to 15%. LIVE (copy.live=true AND promotion gate met: >=60 days, >=100 shadow trades, expectancy >+1%, bootstrap P(positive 20-trade block)>=0.80, >=40% of followed leaders positive): size min(2% equity, 1%/(20% stop*1.5), 1% pool liquidity, maxPositionUsd), max 2 concurrent, 2 entries/day, sleeve cap 5%, sells never mirrored, sizes never mirrored. Demotion: negative 4-week copied P&L, young-mint buy, own token, DD>30%, absent 48 h. Expected: most likely documents a bleed; that dataset is the deliverable.

### breakout_4h liquid-token satellite

Entry at 4h close: close > 20-bar prior high AND volume >= 1.5x median(20) AND EMA20 > EMA50 AND close > EMA50 AND SOL daily regime ON; stop 2.5xATR(14) clamped [4%,20%]; break-even at +1R; trail 4xATR from +2R; exit close < EMA50; time stop 18 bars without +0.3R; 1% risk sizing, 10% sleeve cap, max 2 positions. Measured on SOL 4h (30 bps): risk1pct CAGR 5.3%, Sharpe 0.59, PF 1.52, 90 trades, last-12m PF 0.75. Budget reverts to cash.

### Hyperliquid perps / CEX spot modules

Documented extension point only. Evidence: HL BTC carry rule netted 4.8% (below hurdle), SOL carry negative; perp trend needs non-US residency and cannot count on familiars.

## Configuration schema

```
config/tiller.example.toml (all defaults; `tiller init` copies it to config/tiller.toml; env vars override secrets: TILLER_JUPITER_API_KEY, TILLER_FAMILIARS_API_KEY, TILLER_ANTHROPIC_API_KEY, TILLER_TELEGRAM_TOKEN, AGENT_WALLET_SECRET):

mode = "paper"                      # offline | paper | live
live_without_familiars_ack = false  # only if familiars.enabled=false and you accept trading without board limits

[paths]
state_dir = "/state"               # lock, state.json, KILL file, candle cache
ledger_path = "/state/tiller.sqlite"

[wallet]
keypair_path = ""                   # 0600 file in a 0700 dir outside the repo; or AGENT_WALLET_SECRET env
sol_reserve_lamports = 50000000     # 0.05 SOL never spent

[rpc]
urls = ["https://mainnet.helius-rpc.com/?api-key=...", "https://api.mainnet-beta.solana.com"]
rps = 10.0

[jupiter]
base_url = "https://api.jup.ag"
api_key = ""                        # jup_...; empty = keyless 0.5 RPS
rps = 0.5
slippage_bps_sol_usdc = 50
slippage_bps_token = 100
slippage_bps_emergency = 150

[familiars]
enabled = true
base_url = "https://familiars.family"
api_key = ""                        # fam_... written by `tiller register`
handle = ""
rps = 1.0
skill_md_reviewed_at = ""           # ISO date; live mode requires within 30 days
own_token_mints = []                # any mint the agent/owner deployed (hard-blocked)
callouts_per_day = 2

[data]
candle_sources = ["kraken", "coinbase", "csv"]
csv_dir = "data/history"            # bundled SOL/BTC/ETH daily CSVs for warm-up
max_candle_age_h = 26

[strategies]
hold_mint = "So11111111111111111111111111111111111111112"   # JitoSOL only after board eligibility is verified

[strategies.sol_trend_ensemble]
enabled = true
lookbacks = [10, 20, 30, 60, 90, 150, 250]
vol_target = 0.25
vol_lookback = 90
cap = 0.40
btc_confirm = false                 # WP-F sets per docs/BACKTEST.md rule
sma_n = 200
hyst = 0.02

[strategies.sol_regime_switch]
enabled = true
sma_n = 200
hyst = 0.02
btc_confirm = false
weight = 0.25

[strategies.breakout_4h]
enabled = false                     # not wired live this session

[allocation]                        # must sum to 100; disabled sleeves fold into cash
sol_trend_ensemble_pct = 40
sol_regime_switch_pct = 25
copy_pct = 5
breakout_pct = 0
cash_floor_pct = 30

[risk]
daily_loss_pct = 0.05               # entries blocked for the UTC day
dd7_entry_block_pct = 0.15          # from rolling 7-day peak of (equity - net deposits)
dd30_entry_block_pct = 0.20         # from rolling 30-day peak
dd30_flatten_pct = 0.25             # hard flatten + halt (sticky until `tiller resume`)
sol_beta_cap_max = 0.50
sol_beta_cap_vol = 0.30             # cap = min(max, vol / sigma90_SOL)
rebalance_band_pct = 0.02
min_order_usd = 10
max_entries_per_tick = 1
max_swaps_per_day = 6               # emergency exits exempt
consecutive_failure_pause_min = 60
sim_fail_blocklist_h = 24
max_positions = 4
max_token_pct = 0.30
max_data_age_h = 26
error_rate_block = 0.5

[risk.canary]
max_position_usd = 10
daily_limit_usd = 30
ramp_steps = [0.25, 0.5, 1.0]
ramp_step_days = 7

[guard]
max_price_dev_sol = 0.01
max_price_dev_token = 0.03
max_price_dev_emergency = 0.05
max_impact_core = 0.01
max_impact_token = 0.03
max_fee_bps = 10
routers = ["metis", "jupiterz"]
program_allowlist = ["11111111111111111111111111111111", "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb", "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL", "ComputeBudget111111111111111111111111111111", "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr", "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4", "<jupiterz/dflow program ids: fill from Jupiter docs before live>"]
token_gate_established = { min_organic = 50, min_liquidity_usd = 400000, min_age_h = 72, max_top_holders_pct = 35, max_round_trip_pct = 0.015 }
token_gate_copy = { min_organic = 50, min_liquidity_usd = 250000, min_age_h = 24, max_top_holders_pct = 35, max_round_trip_pct = 0.015 }

[copy]
shadow_enabled = true
live = false
external_wallets = []               # user-curated Solana addresses (e.g. resolved fomo.family leaders)
board_poll_min = 15
detail_poll_s = 60
wallet_poll_s = 60
rescore_h = 6
followed_n = 20
min_days_logged = 14
min_round_trips = 30
min_distinct_mints = 10
min_median_hold_h = 4
max_single_mint_pnl_share = 0.5
max_platform_dd = 0.30
replay_lag_s = 60
replay_slip = 0.01
replay_fee = 0.001
min_replay_pf = 1.3
min_replay_signals = 20
cluster_overlap = 0.8
min_clusters = 3
window_min = 30
crowd_burst_max = 15
late_entry_max_above = 0.15
stop_pct = 0.20
trail_pct = 0.25
trail_from_gain_pct = 0.30
time_stop_h = 48
liquidity_collapse_pct = 0.60
max_concurrent = 2
max_entries_per_day = 2
sleeve_cap_pct = 0.05
per_signal_pct = 0.02
promotion = { min_days = 60, min_trades = 100, min_expectancy_pct = 0.01, min_p_positive_block20 = 0.80, min_leaders_positive_share = 0.40 }

[llm]
narrator = "template"               # template | claude
model = "claude-haiku-4-5"          # only used if narrator = claude
daily_cap_usd = 0.50
timeout_s = 5

[alerts]
telegram_token = ""
chat_id = ""

[venues.hyperliquid]
enabled = false
geo_ack = ""                        # must equal "I am not a US or Ontario person"; module raises NotImplementedError
[venues.cex]
enabled = false
geo_ack = ""

config/owner_overrides.example.json: {"maxPositionUsd": null, "dailyLimitUsd": null, "instructions": ""}  (local, tighten-only, merged with familiars settings by min())
```

## Risk rules

Layered, each layer may only veto or shrink; evaluated in order every tick; exits are never blocked (only entries), except that the hard flatten is itself an exit.

L0 Config invariants (load time, refuse to boot): allocation sums to 100; cash_floor >= 30%; each sleeve <= 50%; non-cash sleeves <= 70%; daily_loss <= 6%; dd30_flatten <= 25% and > dd30_entry > dd7_entry; beta cap max <= 0.5; live needs fresh skill.md ack; optional venues need geo_ack and are not runnable.

L1 Token gate (data/tokens.evaluate_token_gate): allowlist fast path for SOL/USDC/hold_mint; every other mint: RPC mint-account parse is BINDING (spl-token or token-2022 without TransferFeeConfig/TransferHook/PermanentDelegate/NonTransferable/DefaultAccountState/Pausable/ConfidentialTransfer*/MintCloseAuthority; mint and freeze authority null), Tokens V2 unreachable => block, Shield unreachable => block for non-allowlisted, organic/liquidity/age/top-holder thresholds per profile, own-token blocklist (config own_token_mints + Tokens V2 dev == our wallet), per-mint 24 h blocklist after 3 failed simulations, and a real-size round-trip probe (<=0.3% SOL/USDC, <=1.5% tokens) run at order time, never cached.

L2 Allocation and sizing (portfolio/allocator + risk/engine.size_order): netted SOL target capped by min(0.50, 0.30/sigma90_SOL); non-cash <= 70%; per-token <= 30% of equity; stop-managed sleeves size 1% of equity at risk off a gap-adjusted stop (stop x 1.5); then min with 1% of pool liquidity, effective maxPositionUsd, remaining dailyLimitUsd, remaining sleeve budget; orders < $10 refused; rebalance band max(2% equity, $10); binding cap logged.

L3 Brakes (entries blocked): daily loss >= 5% of day-start equity (equity minus net deposits, day start 00:00 UTC); drawdown >= 15% from rolling 7-day peak; drawdown >= 20% from rolling 30-day peak; candles older than 26 h; owner limits unreadable (immediate fail-closed; daily job retries every 15 min inside 00:05-06:00 UTC); RPC/Jupiter error rate > 50% over last 10 calls; reconcile amount mismatch > 1% of equity for 2 consecutive ticks; directive 'pause'/'stop trading'; KILL file present (empty).

L4 Loop guards: max 1 entry per tick; max 6 swaps per UTC day (emergency exits exempt); 3 consecutive execution failures -> 1 h pause; 3 failed simulations on one mint -> 24 h blocklist; single-instance lock with 10-min-stale heartbeat; every external write idempotent (execute keyed by signature, posts keyed by signature).

L5 Hard flatten (sticky): drawdown >= 25% from the rolling 30-day peak, directive 'liquidate'/'sell all', or KILL file containing 'liquidate' -> sell every non-USDC position (size-ascending) in EMERGENCY execution mode (see execution rules), set halted=true in state, alert critical, keep retrying every 5 min with alerts until flat; halted persists across restarts until `tiller resume`.

L6 Owner overrides and tighten-only: familiars settings (maxPositionUsd, dailyLimitUsd, instructions) read each tick; local config/owner_overrides.json; effective = min() of all sources; directives parsed by sentence-initial prefix match only ('pause', 'stop trading', 'liquidate', 'sell all'; substrings ignored; never parsed by an LLM); runtime can only tighten (owner values below config), never raise; config changes require restart.

L7 Canary and ramp: first live run uses maxPositionUsd=$10 / dailyLimitUsd=$30 until one buy, one sell and one accepted trade post are verified on chain and on the board (persisted in state so a restart cannot skip it); then weekly ramp 25% -> 50% -> 100% of configured limits; any L3 brake trip resets the ramp one step.

L8 Restart safety and reconciliation: signed transactions are written to the ledger (state=submitted, signature) before /execute; on boot poll pending signatures, rebuild positions from chain (getTokenAccountsByOwner + getBalance), backfill cost basis from the familiars trade feed, compare token amounts (not USD marks) against the ledger; mismatch > 1% of equity (Price V3 marks) for 2 consecutive ticks blocks entries and alerts until `tiller reconcile --accept`; atomic state writes; SOL reserve 0.05 never spent.

Correlation note: every sleeve is long-SOL-beta by construction (copy population is long SOL beta), so all satellite entries are additionally gated on the SOL daily regime being ON, and the beta cap is applied to the SUM of SOL exposure across sleeves.

Success metric: live-vs-backtest tracking error (FixtureVenue test + weekly review), not leaderboard rank.

## Execution safety rules

Every order goes through LiveSolanaVenue.swap; PaperVenue runs steps 1-4 and fills at quote minus 30 bps haircut without ever touching a signer (NullSigner raises); FixtureVenue fills at next bar open for offline tracking tests.

1. Quote: GET https://api.jup.ag/swap/v2/order with inputMint, outputMint, amount (base units), taker=our pubkey, slippageBps EXPLICIT (50 SOL/USDC, 100 tokens, 150 emergency; RTSE auto-slippage never used), x-api-key if configured; parse transaction (base64), requestId, inAmount, outAmount, slippageBps, priceImpactPct, feeBps, router; raw response persisted to the ledger.
2. Quote checks (guard.check_quote, pure): implied price within 1% (SOL/USDC) / 3% (tokens) / 5% (emergency mode) of the independent reference = reference_price(Jupiter Price V3, Kraken SOL/USD ticker mid) which itself aborts on >5% disagreement (>10% in emergency; in emergency a single available source is accepted with a warning so the kill switch cannot be blocked by its own sanity check); priceImpactPct <= 1% core / 3% tokens; feeBps <= 10 (rejects the 50 bps <24 h tier); router in {metis, jupiterz}.
3. Round-trip probe before any NON-allowlisted entry: quote buy at size, then quote sell of the quoted outAmount; require 1 - sellOut/buyIn <= 1.5%; stored on the fill.
4. Token gate for non-allowlisted mints (L1 above) including the binding RPC mint parse.
5. Transaction checks (guard.check_transaction, pure, on VersionedTransaction.from_bytes): our pubkey is the fee payer (account_keys[0]) and a required signer; every other required-signer slot already carries a non-zero signature (JupiterZ RFQ pre-signed) else reject; every instruction program id is in the allow-list (System, Token, Token-2022, ATA, ComputeBudget, Memo, Jupiter v6 aggregator, JupiterZ/DFlow ids from config); no Token/Token-2022 instruction of type Approve/ApproveChecked/SetAuthority/CloseAccount targets one of our token accounts; recent blockhash present.
6. simulateTransaction over JSON-RPC (sigVerify false, replaceRecentBlockhash true, commitment processed, accounts jsonParsed = our wallet, every token account we own, input ATA, output ATA); guard.check_simulation rejects if err set, our wallet owner != System Program, any returned token account has owner != us or delegate set or closeAuthority not in {null, us}, lamport drop > (SOL input if selling SOL else 0) + 0.015 SOL, input token drops by more than inAmount, output token rises by less than outAmount*(1 - slippageBps/10000), or any unrelated token account decreases; consumed CU logged. These ownership/delegate assertions stay strict in emergency mode.
7. Ledger row written with state=submitted (signature derived locally after signing, before send) BEFORE POST /swap/v2/execute {signedTransaction, requestId}; on network error/timeout the SAME signed bytes are resubmitted at most twice within 2 minutes (idempotent by signature); a fresh quote is never signed for a retry; execute status codes mapped to typed errors; confirmation via getSignatureStatuses (confirmed then finalized) then getTransaction; realised fill from pre/postTokenBalances and pre/postBalances (no paid parser); ledger finalized; a crash between submit and finalize is reconciled on boot by signature.
8. Rate limits: token buckets per host (Jupiter 0.5 RPS keyless / 1 RPS Free, familiars 1 RPS, RPC 10 RPS, Kraken 1 RPS) with jittered exponential backoff on 429 honouring x-ratelimit-* headers; two RPC endpoints with failover; error-rate window feeds the brake.
9. Keys: keypair loaded only by execution/wallet.FileSigner from a 0600 file in a 0700 directory outside the repo (or AGENT_WALLET_SECRET injected via docker --env-file); loader refuses world-readable files; never logged, never serialised into state; import-graph test: tiller.familiars.narrator and tiller.copy.* cannot import tiller.execution.wallet; paper/offline modes use NullSigner; hot wallet holds working capital only; `tiller sweep --to ADDR --keep USD --yes` (non-interactive, address printed) moves profits to cold storage; fam_/jup_/anthropic keys are SecretStr, redacted in logs and alerts.
10. Supply chain and ops: requirements.lock with hashes, --require-hashes install, 14-day cooldown policy, typosquat name check in `make lint` (rejects packages named solana-keypair, semantic-types, solana-publickey, etc.), Docker non-root read-only FS, single-instance lock, atomic state writes, position reconstruction on boot, every external write idempotent, Telegram alerts only (no remote commands).
11. Emergency execution mode (flatten/exit only): slippage 150 bps, price tolerance 5%, single reference source tolerated, swaps/day cap exempt, retries every 5 min with critical alerts; the runbook documents the manual exit procedure (Jupiter UI with the exported key) if routes stay broken.

## familiars.family plan

familiars is the public board where the one trading wallet is registered; it is not a venue and never a return objective (absolute-USD ranking rewards concentration and capital; measured 37/1,350 agents positive on 2026-09-24).

Registration (`tiller register`, once): POST /api/agents/challenge {wallet} -> sign message bytes with FileSigner.sign_message -> base64 -> POST /api/agents/register {wallet, nonce, signature, handle (^[a-z0-9_]{3,20}$), name (1-32), bio (<=280), strategy (<=40), color}; ONE attempt, never retried (single-use nonce; on a network error after send the CLI says to check the dashboard); the fam_ apiKey/ownerKey/loginUrl are written once to the 0600 secrets file and printed once; the command refuses to run if a fam_ key already exists.

Owner limits: GET /api/agent/me at the start of every tick; settings.maxPositionUsd and dailyLimitUsd are hard caps on entries (never exits) merged by min() with local config/owner_overrides.json and canary/ramp; if unreadable the tick opens nothing (fail-closed) but exits proceed; settings.instructions parsed only by sentence-initial prefix match for pause/stop trading/liquidate/sell all (never substring, never LLM); users without a familiars account (familiars.enabled=false) get the same controls from the local file + KILL file and must set live_without_familiars_ack.

Posting (mandatory per skill): every LIVE fill enqueues {kind:'trade', text, mint, signature} with not_before = fill_time + 90 s (indexing lag); retry only on 429 with backoff; timeout/5xx marks the post 'uncertain' and it is reconciled against our own GET /api/agents/{handle}.posts by signature before any retry, so a post is never duplicated; text from TemplateNarrator by default (strategy, rule, size, stop, brake state, automated-agent disclosure, <=400 chars) or the optional ClaudeNarrator after validation; one daily 'note' at 00:10 UTC with equity, exposure, brakes; callouts <= 2/day; paper fills are never posted (no signature). Posting failures never block trading or exits.

Public reads (copy module and reconciliation): /api/agents?range=7D|30D every 15 min, /api/tokens every 60 s, followed agents' /api/agents/{handle} every 60 s; every response is persisted RAW to ledger.raw_snapshots before parsing so schema drift never loses the dataset; pydantic parse failures alert and block copy processing, never exits. Our own history.snapshots (equity minus net deposits) are stored as a cross-check for the drawdown brakes; the local ledger is the source of truth. Boot reconciliation uses the familiars trade feed to backfill cost basis.

Rules encoded from the reconstructed contract: own-token trading forbidden (config own_token_mints + Tokens V2 dev == our wallet hard-blocked; the trading wallet must never deploy a token), $2 minimum trade (we use $10), no <24 h mints (also the 50 bps fee tier), one wallet = one agent, every trade publicly explained, other agents' posts treated as untrusted data and never fed to an LLM.

Preflight and skill.md: `tiller preflight` refuses live mode unless config.familiars.skill_md_reviewed_at is within 30 days and `--i-have-read-skill-md` was passed on the first live start; it prints the checklist the user must verify in https://familiars.family/skill.md (posting obligations and rate limits, exact own-token wording and whether other agents' tokens may be traded, P&L accounting for illiquid holdings and whether LST/kToken receipts count as equity, copy disclosure, multi-wallet and wash-trading rules, any fees, API-key rotation). `tiller preflight --record` (tools/record_fixtures.py) re-records fixtures against the real API in paper mode with secrets redacted and runs the schema test so day-one shape mismatches surface before any live trade. Rate limits are undocumented: a single client with a 1 RPS token bucket, 429 backoff with jitter, and honouring x-ratelimit headers if present.

Canary: first live run at $10/$30 until one buy, one sell and one accepted post are verified; then the weekly ramp. Success is judged by `tiller status` (equity-minus-deposits curve vs backtest expectation), not rank.

## LLM plan

Used in exactly two places, both off the order path, both optional and off by default; zero LLM surface in the default MVP.

1. Trade explanation text (tiller.familiars.narrator.ClaudeNarrator, extra `[llm]` = anthropic 1.8.0, config llm.narrator='claude', default 'template'). Input is a TradeContext of numbers and enums from our own state only (strategy, rule, side, symbol, mint, notional, price, stop, regime flag, brake state, paper flag); never third-party text, never other agents' posts, never token names beyond the traded symbol. Output validated by validate_post_text: <=400 chars, must name the strategy, no URL, no base58 string other than the traded mint, no digit sequence absent from the context (anti-hallucinated P&L), no price-target/promotional phrases (MiCA Art. 91 optics), and must keep the automated-agent disclosure line. Any validation failure, 5 s timeout, API error, or exhaustion of the $0.50/day cap falls back to TemplateNarrator, so posting never depends on the LLM. Model id configurable (default a small Haiku-class model); every call and response logged to ledger.events.

2. Weekly offline review (`tiller review`): prints a deterministic markdown pack (ledger summary, live-vs-backtest tracking error, brake trips, shadow report) and, if llm.narrator='claude' and a key is present, asks the model for a diagnosis and proposed config diffs as TEXT only; nothing is applied automatically; any parameter change must pass `tiller backtest` on the frozen fixtures and is applied by a human commit at most once a month (policy in the runbook).

Deliberately NOT used: signal generation, entries, exits, sizing, regime detection, token safety decisions, leader scoring, parsing of other agents' explanation posts or token metadata (prompt/memory-injection surface; CrAIBench), owner directive parsing (deterministic prefix match), the launch-metadata veto (no launch sleeve exists), and anything with access to the signing key, RPC or executor. Enforced structurally: tiller.familiars.narrator cannot import tiller.execution.wallet or tiller.execution.venue (import-graph test); the narrator receives no client objects besides its own HTTP client. Evidence basis: in-loop LLM agents lost 31-68% in Alpha Arena Season 1, show no alpha in FINSABER/Profit Mirage/StockBench, and memory injection beats prompt-injection defences on Web3 agents.

## Module contracts

### `src/tiller/config.py`

**Responsibility.** Load config/tiller.toml (stdlib tomllib) plus env overrides into pydantic models; enforce L0 invariants at load; expose the allocation table with disabled sleeves' budgets folded into cash.

**Interface.**

```
def load_config(path: Path, env: Mapping[str, str] | None = None) -> Config
class Config(BaseModel): mode: Literal['offline','paper','live']; wallet: WalletCfg(keypair_path: Path|None, env_secret_var: str='AGENT_WALLET_SECRET', sol_reserve_lamports: int=50_000_000); rpc: RpcCfg(urls: list[str], rps: float=10); jupiter: JupiterCfg(base_url: str='https://api.jup.ag', api_key: SecretStr|None, rps: float=0.5, slippage_bps_sol_usdc: int=50, slippage_bps_token: int=100, slippage_bps_emergency: int=150); familiars: FamiliarsCfg(enabled: bool, base_url: str='https://familiars.family', api_key: SecretStr|None, handle: str|None, rps: float=1.0, skill_md_reviewed_at: date|None, own_token_mints: list[str]=[]); data: DataCfg(candle_sources: list[Literal['kraken','coinbase','csv']], csv_dir: Path|None, max_candle_age_h: int=26); strategies: StrategiesCfg(sol_trend_ensemble: SolTrendParams, sol_regime_switch: RegimeParams, breakout_4h: BreakoutParams(enabled=False), hold_mint: str=SOL_MINT); allocation: AllocationCfg(sol_trend_ensemble_pct: float=40, sol_regime_switch_pct: float=25, copy_pct: float=5, breakout_pct: float=0, cash_floor_pct: float=30); risk: RiskCfg(daily_loss_pct=0.05, dd7_entry_block_pct=0.15, dd30_entry_block_pct=0.20, dd30_flatten_pct=0.25, sol_beta_cap_max=0.50, sol_beta_cap_vol=0.30, rebalance_band_pct=0.02, min_order_usd=10, max_entries_per_tick=1, max_swaps_per_day=6, consecutive_failure_pause_min=60, sim_fail_blocklist_h=24, max_positions=4, max_token_pct=0.30, canary: CanaryCfg(max_position_usd=10, daily_limit_usd=30, ramp_steps=[0.25,0.5,1.0], ramp_step_days=7)); copy: CopyCfg(shadow_enabled=True, live=False, external_wallets: list[str]=[], followed_n=20, min_clusters=3, window_min=30, ... all thresholds from strategy_portfolio); llm: LlmCfg(narrator: Literal['template','claude']='template', model: str, daily_cap_usd: Decimal='0.50'); venues: VenuesCfg(hyperliquid: OptVenue(enabled=False, geo_ack: str|None), cex: OptVenue(enabled=False, geo_ack: str|None)); alerts: AlertsCfg(telegram_token: SecretStr|None, chat_id: str|None); paths: PathsCfg(state_dir: Path, ledger_path: Path)
L0 invariants (ValueError with the rule name): allocation pcts sum to 100 ±0.01; cash_floor_pct >= 30; each sleeve pct <= 50; sum of non-cash sleeve pcts <= 70; daily_loss_pct <= 0.06; dd30_flatten_pct <= 0.25 and > dd30_entry_block_pct > dd7_entry_block_pct; sol_beta_cap_max <= 0.5; mode=='live' requires familiars.skill_md_reviewed_at within 30 days OR familiars.enabled==False plus an explicit config flag live_without_familiars_ack=True; venues.*.enabled requires geo_ack == 'I am not a US or Ontario person' and always raises NotImplementedError('optional venue not built') when any code path tries to use it; copy.live requires copy.shadow_enabled
def effective_allocation(cfg: Config) -> dict[str, float]  # disabled sleeves' pct moved into 'cash'
```

**Depends on:** src/tiller/models.py

**Tests.** tests/test_config.py: example TOML loads; each invariant has a failing case; disabled breakout pct folds into cash; live without skill_md_reviewed_at rejected; hyperliquid enabled without geo_ack rejected; env override of api keys; SecretStr never appears in repr/str.

### `src/tiller/models.py`

**Responsibility.** Shared pydantic models for wire data and domain objects. Decimal for USD/prices, int for base units, tz-aware UTC datetimes.

**Interface.**

```
SOL_MINT='So11111111111111111111111111111111111111112'; USDC_MINT='EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
class Candle(BaseModel): ts: datetime; open: Decimal; high: Decimal; low: Decimal; close: Decimal; volume: Decimal
class SwapRequest(BaseModel): input_mint: str; output_mint: str; amount_base: int; strategy: str; reason: str; mode: Literal['normal','emergency']='normal'
class Order(BaseModel): transaction_b64: str; request_id: str; in_amount: int; out_amount: int; slippage_bps: int; price_impact_pct: Decimal; fee_bps: int; router: str; raw: dict
class Fill(BaseModel): signature: str | None; in_mint: str; out_mint: str; in_base: int; out_base: int; usd_in: Decimal; usd_out: Decimal; fee_usd: Decimal; ts: datetime; paper: bool; strategy: str; quote_out_base: int; round_trip_probe_pct: Decimal | None
class Position(BaseModel): mint: str; amount_base: int; cost_usd: Decimal; opened_at: datetime; strategy: str; exit: ExitRule | None
class ExitRule(BaseModel): stop_price: Decimal | None; trail_pct: Decimal | None; trail_from_gain_pct: Decimal | None; time_stop_at: datetime | None; ema_exit: bool=False
class TokenAccount(BaseModel): pubkey: str; mint: str; amount_base: int; owner: str; delegate: str | None; close_authority: str | None; program: str
class TokenInfo(BaseModel): mint: str; symbol: str; organic_score: float | None; audit: AuditInfo | None; holder_count: int | None; liquidity_usd: Decimal | None; mcap_usd: Decimal | None; first_pool_created_at: datetime | None; launchpad: str | None; dev: str | None; raw: dict
class AuditInfo(BaseModel): mint_authority_disabled: bool | None; freeze_authority_disabled: bool | None; top_holders_pct: float | None; dev_balance_pct: float | None; is_sus: bool | None
class PublicAgent(BaseModel): handle: str; name: str|None; wallet: str; hosted: bool; equity_usd: Decimal; pnl: dict[str, Decimal]; drawdown: Decimal|None; win_rate: Decimal|None; trades: int; last_trade_at: datetime|None; joined_at: datetime|None
class AgentTrade(BaseModel): signature: str; kind: Literal['buy','sell','swap']; time: datetime; amount: Decimal; usd_value: Decimal; token: str; quote: str|None
class EquityPoint(BaseModel): ts: datetime; equity_usd: Decimal; net_deposits_usd: Decimal; pnl_usd: Decimal
class AgentDetail(BaseModel): agent: PublicAgent; cash_usd: Decimal|None; sol_balance: Decimal|None; positions: list[AgentPosition]; trades: list[AgentTrade]; posts: list[dict]; history: list[EquityPoint]
class OwnerLimits(BaseModel): max_position_usd: Decimal|None; daily_limit_usd: Decimal|None; instructions: str|None; readable: bool; read_at: datetime
class TradeContext(BaseModel): strategy: str; rule: str; side: Literal['buy','sell']; symbol: str; mint: str; notional_usd: Decimal; price_usd: Decimal; stop_price: Decimal|None; regime_on: bool|None; brake_state: str; paper: bool; signature: str|None
```

**Depends on:** nothing

**Tests.** tests/test_models.py: fixtures for every familiars/Jupiter shape parse; missing required field raises; extra fields ignored; Decimal round-trips.

### `src/tiller/clock.py`

**Responsibility.** Injectable time.

**Interface.**

```
class Clock(Protocol): def now(self) -> datetime  # tz-aware UTC
class SystemClock(Clock)
class SimClock(Clock): def __init__(self, start: datetime); def advance(self, delta: timedelta) -> None; def set(self, t: datetime) -> None
```

**Depends on:** nothing

**Tests.** covered by users.

### `src/tiller/ledger.py`

**Responsibility.** Append-only SQLite (stdlib sqlite3, WAL). Tables: orders(id, ts, req json, state submitted|filled|failed, signature, error), fills, positions (derived view), transfers(ts, mint, amount_base, usd, direction), equity_snapshots(ts, equity_usd, net_deposits_usd, source), raw_snapshots(ts, source, key, json), leader_trades, shadow_trades, posts(signature, kind, state queued|posted|uncertain|failed, attempts), events(ts, level, kind, json), blocklist(mint, until), day_stats. Tax CSV export with USD fair values of both legs.

**Interface.**

```
class Ledger:
  def __init__(self, path: Path)
  def record_order(self, req: SwapRequest, order: Order | None, signature: str | None) -> int  # state=submitted
  def finalize_order(self, order_id: int, fill: Fill | None, error: str | None) -> None
  def pending_signatures(self) -> list[tuple[int, str]]
  def record_fill(self, fill: Fill) -> None
  def positions(self) -> list[Position]
  def record_transfer(self, ts, mint, amount_base, usd, direction) -> None
  def net_deposits_usd(self) -> Decimal
  def add_equity_snapshot(self, ts, equity_usd, net_deposits_usd, source) -> None
  def equity_curve(self, days: int) -> list[EquityPoint]
  def add_raw_snapshot(self, ts, source: str, key: str, payload: dict | list) -> None
  def swaps_today(self, day_start: datetime) -> int
  def day_stats(self, day_start: datetime) -> DayStats(start_equity: Decimal, buys_usd: Decimal, swaps: int, entries: int)
  def blocklist_add(self, mint, until) / blocklist_active(self, now) -> set[str]
  def upsert_leader_trades(self, trades: list[LeaderTrade]) -> int  # dedupe by signature
  def leader_trades(self, since: datetime, keys: list[str] | None = None) -> list[LeaderTrade]
  def upsert_shadow_trade(self, t: ShadowTrade) -> None; def shadow_trades(self, since) -> list[ShadowTrade]
  def post_upsert(self, signature: str, kind, text, state, attempts) / posts_pending(now) -> list[PendingPost]
  def add_event(self, level, kind, payload) -> None
  def export_tax_csv(self, path: Path) -> int
```

**Depends on:** src/tiller/models.py, src/tiller/copy/models.py

**Tests.** tests/test_ledger.py: order submitted->finalized round trip; pending signatures after simulated crash; net deposits excludes fills; day stats; blocklist expiry; leader trade dedupe by signature; tax CSV columns and row count; raw snapshot stored verbatim.

### `src/tiller/state.py`

**Responsibility.** Small hot state as atomic JSON (tmp + fsync + rename) and single-instance lock with heartbeat.

**Interface.**

```
class AgentState(BaseModel): sleeves: dict[str, SleeveState]  # SleeveState: donchian stops per N, regime_on flags
  pending_posts: list[PendingPost]; canary: CanaryState(verified_buy: bool, verified_sell: bool, verified_post: bool, ramp_step: int, ramp_step_started: datetime|None); brakes: BrakeState(entries_blocked: bool, reasons: list[str], paused_until: datetime|None, consecutive_failures: int); halted: bool; halt_reason: str|None; last_reconcile_ok: bool; reconcile_mismatch_ticks: int; last_owner_limits: OwnerLimits|None
def load_state(path: Path) -> AgentState  # missing file -> defaults
def save_state(path: Path, s: AgentState) -> None
class InstanceLock: def __init__(self, path: Path, clock: Clock, stale_after: timedelta=timedelta(minutes=10)); def acquire(self) -> None  # raises LockHeld; def heartbeat(self) -> None; def release(self) -> None
```

**Depends on:** src/tiller/models.py, src/tiller/clock.py

**Tests.** tests/test_state.py: round trip; interrupted write (exception before rename) leaves old file intact; lock contention; stale lock (heartbeat > 10 min) is taken over.

### `src/tiller/data/candles.py`

**Responsibility.** Daily UTC candles from Kraken public OHLC (primary), Coinbase Exchange public candles (fallback), CSV (fixtures/bundled history); returns CLOSED bars only; local CSV cache appends new bars to bundled history; staleness check; cross-source disagreement check.

**Interface.**

```
class CandleSource(Protocol): async def fetch_daily(self, symbol: Literal['SOL','BTC','ETH'], limit: int) -> list[Candle]
class KrakenCandles(CandleSource): def __init__(self, client: httpx.AsyncClient, clock: Clock)  # GET https://api.kraken.com/0/public/OHLC?pair=SOLUSD|XBTUSD|ETHUSD&interval=1440
class CoinbaseCandles(CandleSource)  # GET https://api.exchange.coinbase.com/products/SOL-USD/candles?granularity=86400
class CsvCandles(CandleSource): def __init__(self, dir: Path)  # scratchpad-format CSV: timestamp(unix s open),open,high,low,close,volume
class DailyCandleStore:
  def __init__(self, sources: list[CandleSource], cache_dir: Path, clock: Clock)
  async def closed_bars(self, symbol: str, min_bars: int = 300) -> list[Candle]  # drops any bar whose ts+24h > now
  def is_stale(self, bars: list[Candle], max_age: timedelta) -> bool
  @staticmethod def disagreement(a: list[Candle], b: list[Candle], n: int = 5) -> Decimal  # max |close_a/close_b - 1|
```

**Depends on:** src/tiller/models.py, src/tiller/clock.py

**Tests.** tests/test_candles.py (respx): Kraken fixture parses to Candles with correct ts; unclosed last bar dropped; fallback used when Kraken 5xx; stale detection at 26 h; cache append is idempotent; CSV loader reproduces row count of SOLUSDT_1d.csv.

### `src/tiller/data/prices.py`

**Responsibility.** USD marks and independent reference: Jupiter Price V3 (mints) and Kraken public ticker mid (SOL/USD); reference_price() cross-checks and aborts on >5% disagreement (non-emergency) or >10% (emergency).

**Interface.**

```
class PriceSource(Protocol): async def usd_prices(self, mints: list[str]) -> dict[str, Decimal]
class JupiterPriceV3(PriceSource): def __init__(self, base_url, api_key, rps, client)  # GET /price/v3?ids=
class KrakenTicker: async def sol_usd_mid(self) -> Decimal  # GET /0/public/Ticker?pair=SOLUSD
class FixturePrices(PriceSource): def __init__(self, table: dict[str, Decimal])
def reference_price(jup: Decimal | None, cex_mid: Decimal | None, max_disagreement: Decimal) -> Decimal  # raises ReferenceDisagreement; if one side missing returns the other with a logged warning ONLY for exits
```

**Depends on:** src/tiller/models.py

**Tests.** tests/test_prices.py: Price V3 fixture parse; ticker parse; disagreement raises; single-source rule.

### `src/tiller/data/tokens.py`

**Responsibility.** Jupiter Tokens V2 search, Shield, mint account fetch via RPC, and the PURE token gate shared by copy/breakout. Allowlist fast path for SOL/USDC (and hold_mint). Mint-account parse is binding; Tokens V2 unreachable => block; Shield unreachable => block for non-allowlisted mints.

**Interface.**

```
class TokenData:
  def __init__(self, client: httpx.AsyncClient, rpc: Rpc, base_url: str, api_key: str|None, rps: float, ledger: Ledger, clock: Clock)
  async def token_info(self, mint: str) -> TokenInfo | None  # GET /tokens/v2/search?query=<mint>; raw persisted
  async def shield(self, mints: list[str]) -> dict[str, list[str]] | None  # GET /ultra/v1/shield?mints=; None on error
  async def mint_account(self, mint: str) -> dict  # getAccountInfo jsonParsed
class TokenGateCfg(BaseModel): profile: Literal['established','copy']; min_organic: float; min_liquidity_usd: Decimal; min_age_h: int; max_top_holders_pct: float; max_round_trip_pct: Decimal
class GateResult(BaseModel): ok: bool; reasons: list[str]; liquidity_usd: Decimal | None
def evaluate_token_gate(mint: str, info: TokenInfo | None, shield_warnings: list[str] | None, mint_account: dict | None, cfg: TokenGateCfg, our_wallet: str, own_token_mints: set[str], blocklist: set[str], now: datetime, allowlist: set[str]) -> GateResult
  # rules: allowlist -> ok; mint_account None -> block; program not in {spl-token, token-2022} -> block; token-2022 with any of TransferFeeConfig, TransferHook, PermanentDelegate, NonTransferable, DefaultAccountState(frozen), Pausable, ConfidentialTransfer*, MintCloseAuthority -> block; mintAuthority or freezeAuthority present -> block; info None -> block; organic < min, liquidity < min, age < min, top_holders > max, is_sus -> block; shield None -> block; shield has HAS_FREEZE_AUTHORITY|HAS_MINT_AUTHORITY|LOW_ORGANIC_ACTIVITY|NEW_LISTING -> block; mint in own_token_mints or info.dev == our_wallet -> block; mint in blocklist -> block
```

**Depends on:** src/tiller/models.py, src/tiller/execution/rpc.py, src/tiller/ledger.py

**Tests.** tests/test_token_gate.py: table test with one fixture per rejection reason plus a passing established token and a passing copy token; allowlist bypass; Shield None blocks non-allowlisted only.

### `src/tiller/strategies/indicators.py`

**Responsibility.** Pure numpy indicators with the analysis conventions (Wilder ATR via ewm alpha=1/n, EMA span adjust=False, prior-N high uses bars t-N..t-1).

**Interface.**

```
def sma(x: np.ndarray, n: int) -> np.ndarray
def ema(x: np.ndarray, n: int) -> np.ndarray
def atr_wilder(high, low, close, n: int) -> np.ndarray
def donchian_prior_high(high: np.ndarray, n: int) -> np.ndarray  # max(high[t-n..t-1]), nan before
def donchian_mid(high, low, n) -> np.ndarray  # (max(high[t-n+1..t]) + min(low[t-n+1..t]))/2
def realized_vol(close: np.ndarray, lookback: int = 90, periods_per_year: int = 365) -> np.ndarray  # std ddof=1 of log returns * sqrt(ppy)
def hysteresis_state(close: np.ndarray, ref: np.ndarray, hyst: float = 0.02) -> np.ndarray  # 0/1 state machine, nan ref -> 0
```

**Depends on:** nothing

**Tests.** tests/test_indicators.py: hand-computed values for n=3 series; hysteresis on/off/hold; no-lookahead: appending future bars never changes earlier outputs (parametrised over 20 random series).

### `src/tiller/strategies/base.py`

**Responsibility.** Strategy protocol: pure synchronous targets over a MarketContext.

**Interface.**

```
class TargetExposure(BaseModel): mint: str; weight: Decimal  # fraction of TOTAL equity
  strategy: str; reason: str; exit: ExitRule | None = None
class MarketContext(BaseModel): now: datetime; candles: dict[str, list[Candle]]; equity_usd: Decimal; positions: list[Position]; sleeve_state: dict[str, SleeveState]; regime_on: bool | None
class Strategy(Protocol): name: str; cadence: Literal['daily','monitor']; def targets(self, ctx: MarketContext) -> tuple[list[TargetExposure], SleeveState]
```

**Depends on:** src/tiller/models.py, src/tiller/state.py

**Tests.** covered by strategy tests.

### `src/tiller/strategies/sol_trend.py`

**Responsibility.** Sleeve A and sleeve B exactly as Study 1, sharing vectorised exposure functions with the backtester.

**Interface.**

```
class SolTrendParams(BaseModel): lookbacks: list[int] = [10,20,30,60,90,150,250]; vol_target: float = 0.25; vol_lookback: int = 90; cap: float = 0.40; btc_confirm: bool = False; sma_n: int = 200; hyst: float = 0.02
class RegimeParams(BaseModel): sma_n: int = 200; hyst: float = 0.02; btc_confirm: bool = False; weight: float = 0.25
def donchian_sleeve_state(high, low, close, n) -> tuple[np.ndarray, np.ndarray]  # (state 0/1 per bar, stop per bar); identical logic to study1.donchian_sleeve
def ensemble_exposure(high, low, close, p: SolTrendParams, btc_close: np.ndarray | None = None) -> np.ndarray  # frac*min(1, vol_target/rv90) clipped to cap; if btc_confirm: new entries only when BTC hysteresis state==1
def regime_exposure(close, p: RegimeParams, btc_close=None) -> np.ndarray  # state * weight
class SolTrendEnsembleStrategy(Strategy): name='sol_trend_ensemble'; cadence='daily'; def __init__(self, p: SolTrendParams, hold_mint: str)
class SolRegimeSwitchStrategy(Strategy): name='sol_regime_switch'; cadence='daily'; def __init__(self, p: RegimeParams, hold_mint: str)
  # both evaluate ONLY on closed bars, at most once per UTC day (SleeveState.last_bar_ts guards re-evaluation), and emit a single TargetExposure for hold_mint
```

**Depends on:** src/tiller/strategies/indicators.py, src/tiller/strategies/base.py

**Tests.** tests/test_sol_trend_golden.py: on tests/fixtures/ohlcv/SOLUSDT_1d.csv with the backtester at 30 bps, warmup 250: donchian_ensemble (cap=1, vol_target=None) CAGR 60.8% ±1pt, Sharpe 1.20 ±0.02, MaxDD -45.2% ±1pt, 161 round trips exact; voltarget25 CAGR 14.8% ±0.5, MaxDD -14.2% ±0.5, vol 12.1% ±0.5; sma200_hyst2pct CAGR 57.9% ±1, MaxDD -69.1% ±1, 16 round trips exact; BTC sma200 17 round trips. Strategy wrapper: same exposure as vectorised function when fed bar by bar with SimClock (state persistence test); no evaluation twice on the same bar.

### `src/tiller/strategies/breakout_4h.py`

**Responsibility.** STRETCH, disabled: pure 4h breakout signal + simulator reproducing Study 2 on SOL; no live wiring, no GeckoTerminal source this session.

**Interface.**

```
class BreakoutParams(BaseModel): enabled: bool=False; lookback=20; vol_mult=1.5; atr_n=14; atr_stop=2.5; stop_clamp=(0.04,0.20); trail_atr=4.0; time_stop_bars=18; risk_per_trade=0.01; cap=0.10; max_positions=2
def resample_4h(bars_1h: list[Candle]) -> list[Candle]
def breakout_backtest(bars4h: list[Candle], p: BreakoutParams, cost_bps: int) -> BreakoutResult(trades: int, pf: float, cagr: float, max_dd: float)
```

**Depends on:** src/tiller/strategies/indicators.py

**Tests.** tests/test_breakout_golden.py (skipped unless SOLUSDT_1h.csv fixture present): SOL own-gate risk1pct 30 bps: 90 trades ±2, PF 1.52 ±0.1.

### `src/tiller/portfolio/allocator.py`

**Responsibility.** Nets strategy targets per mint, applies SOL-beta cap, cash floor, per-token caps, rebalance band and minimum order; produces an ordered plan (exits first, then at most one entry).

**Interface.**

```
class OrderIntent(BaseModel): mint: str; side: Literal['buy','sell']; usd: Decimal; strategy: str; reason: str; exit: ExitRule | None; is_exit: bool
def sol_beta_cap(sigma90_sol: float, cfg: RiskCfg) -> float  # min(cfg.sol_beta_cap_max, cfg.sol_beta_cap_vol / max(sigma90_sol, 0.05))
def plan(targets: list[TargetExposure], acct: AccountSnapshot, sigma90_sol: float, cfg: Config) -> list[OrderIntent]
  # 1) sum weights per mint; 2) hold_mint total = min(sum, sol_beta_cap); 3) total non-cash <= 1 - cash_floor (scale pro rata); 4) per-token cap; 5) delta = target_usd - current_usd; trade only if |delta| >= max(rebalance_band_pct*equity, min_order_usd); 6) sells first, then at most max_entries_per_tick buys; 7) an exit intent for any position whose ExitRule fired
```

**Depends on:** src/tiller/strategies/base.py, src/tiller/risk/account.py, src/tiller/config.py

**Tests.** tests/test_allocator.py: A+B netting; beta cap binds and scales pro rata; cash floor scaling; band suppresses small deltas; exits before entries; only one entry per tick; disabled sleeve contributes 0.

### `src/tiller/risk/account.py`

**Responsibility.** Builds AccountSnapshot from chain balances, marks, ledger and optional familiars history.

**Interface.**

```
class AccountSnapshot(BaseModel): ts: datetime; equity_usd: Decimal; usdc_usd: Decimal; sol_lamports: int; sol_usd: Decimal; positions: list[Position]; marks: dict[str, Decimal]; net_deposits_usd: Decimal; day: DayStats; peak_7d: Decimal; peak_30d: Decimal; pnl_7d_pct: Decimal
async def build_snapshot(rpc: Rpc, prices: PriceSource, ledger: Ledger, wallet: str, clock: Clock, familiars_history: list[EquityPoint] | None = None) -> AccountSnapshot  # equity = USDC + SOL*mark + sum(pos*mark); peaks over (equity - net_deposits); familiars history used as cross-check only, ledger is source of truth
```

**Depends on:** src/tiller/execution/rpc.py, src/tiller/data/prices.py, src/tiller/ledger.py

**Tests.** tests/test_account.py with FakeRpc/FixturePrices: equity arithmetic; a deposit does not raise pnl; peaks computed on equity minus deposits.

### `src/tiller/risk/engine.py`

**Responsibility.** All pure risk decisions: brake state, entry check, sizing with binding cap, loop guards, canary + ramp, directive parsing, hard-flatten decision, tighten-only override merge.

**Interface.**

```
class Decision(BaseModel): allowed: bool; reasons: list[str]
class SizedOrder(BaseModel): usd: Decimal; binding_cap: str
def parse_directive(instructions: str | None) -> Literal['pause','liquidate'] | None  # sentence-initial, case-insensitive prefix match on 'pause','stop trading','liquidate','sell all'; substrings ignored
def effective_limits(cfg: RiskCfg, owner: OwnerLimits | None, local: OwnerLimits | None, canary: CanaryState, now: datetime) -> Limits | None  # None = fail closed (owner enabled but unreadable); min() of all sources (tighten-only); canary/ramp multiplier applied
def brake_state(acct: AccountSnapshot, cfg: RiskCfg, data_age: timedelta, limits: Limits | None, error_rate: float, state: AgentState, now: datetime) -> BrakeState  # entries blocked if: day loss >= daily_loss_pct; dd from peak_7d >= dd7; dd from peak_30d >= dd30_entry; data stale; limits None; error_rate > 0.5; paused_until > now; halted; reconcile_mismatch_ticks >= 2; directive pause
def check_entry(intent: OrderIntent, acct, brakes: BrakeState, limits: Limits, cfg, ledger_day: DayStats, blocklist: set[str]) -> Decision  # also: swaps_today < max_swaps_per_day; entries this tick < max; positions < max_positions; mint not blocklisted; post-trade USDC >= cash floor; post-trade SOL >= reserve
def size_order(intent: OrderIntent, acct, limits: Limits, cfg, pool_liquidity_usd: Decimal | None, day: DayStats, stop_pct: Decimal | None) -> SizedOrder | None  # min(intent.usd, [1%/(stop_pct*1.5)] if stop, max_token_pct*equity, 1% pool liquidity, limits.max_position_usd, limits.daily_limit_usd - day.buys_usd, remaining sleeve budget); None if < min_order_usd
def must_flatten(acct, cfg, directive, kill_file: bool, state: AgentState) -> str | None  # reason if dd from peak_30d >= dd30_flatten, directive=='liquidate', or KILL file
def advance_canary(state: CanaryState, verified: tuple[bool,bool,bool], brake_tripped: bool, now, cfg) -> CanaryState  # step 0 until all verified; then weekly steps 25/50/100%; any brake trip -> step-1
def record_failure(state: BrakeState, now, cfg) -> BrakeState  # 3 consecutive -> paused_until now+60min
```

**Depends on:** src/tiller/risk/account.py, src/tiller/state.py, src/tiller/config.py

**Tests.** tests/test_risk_engine.py: table tests for every brake on both sides of threshold; directive prefix vs 'please do not pause'; fail-closed None; tighten-only min; canary gating and ramp reset; sizing picks binding cap (each cap once); swaps/day and entries/tick limits; consecutive failure pause; flatten triggers; 30-day-peak not all-time.

### `src/tiller/risk/reconcile.py`

**Responsibility.** Boot/tick reconciliation: poll pending signatures, rebuild positions from chain token accounts, compare AMOUNTS against ledger (and familiars trades as backfill for cost basis), decide mismatch.

**Interface.**

```
class ReconcileReport(BaseModel): ok: bool; mismatches: list[Mismatch(mint, chain_base, ledger_base, usd_diff)]; usd_mismatch_pct: Decimal; backfilled: int
async def reconcile(rpc: Rpc, ledger: Ledger, prices: PriceSource, wallet: str, familiars_detail: AgentDetail | None, clock: Clock, accept: bool = False) -> ReconcileReport  # 1) for each pending signature: getSignatureStatuses/getTransaction -> finalize or mark failed; 2) chain positions = getTokenAccountsByOwner + getBalance; 3) mismatch if |chain-ledger| > 0.5% of amount per mint; usd_mismatch_pct at Price V3; accept=True rewrites ledger positions to chain
```

**Depends on:** src/tiller/execution/rpc.py, src/tiller/ledger.py, src/tiller/data/prices.py

**Tests.** tests/test_reconcile.py: pending signature finalized after crash; mismatch > 1% flagged; accept rewrites; equal amounts with different USD marks is NOT a mismatch; familiars backfill of cost basis.

### `src/tiller/execution/rpc.py`

**Responsibility.** Minimal Solana JSON-RPC over httpx with primary/fallback, token bucket, typed results; error-rate window.

**Interface.**

```
class SimResult(BaseModel): err: Any | None; logs: list[str]; units_consumed: int | None; accounts: list[dict | None]
class Rpc(Protocol):
  async def get_balance(self, pubkey: str) -> int
  async def get_token_accounts_by_owner(self, owner: str) -> list[TokenAccount]  # both token programs, jsonParsed
  async def get_account_info(self, pubkey: str) -> dict | None  # jsonParsed
  async def simulate(self, tx_b64: str, accounts: list[str]) -> SimResult  # sigVerify False, replaceRecentBlockhash True, commitment processed, accounts encoding jsonParsed
  async def get_signature_statuses(self, sigs: list[str]) -> list[dict | None]
  async def get_transaction(self, sig: str) -> dict | None  # jsonParsed, maxSupportedTransactionVersion 0
  async def get_signatures_for_address(self, addr: str, limit: int = 25, until: str | None = None) -> list[dict]
  def error_rate(self) -> float  # last 10 calls
class HttpRpc(Rpc): def __init__(self, urls: list[str], rps: float, client: httpx.AsyncClient, ledger: Ledger | None = None)
```

**Depends on:** src/tiller/models.py

**Tests.** tests/test_rpc.py (respx): each method parses its fixture; failover on 5xx/timeout; token bucket spacing; error_rate window.

### `src/tiller/execution/jupiter.py`

**Responsibility.** Jupiter Swap V2 client: GET /swap/v2/order, POST /swap/v2/execute, round-trip probe; explicit slippageBps always (no RTSE); typed errors.

**Interface.**

```
class ExecResult(BaseModel): status: Literal['Success','Failed']; signature: str | None; code: int; error: str | None; raw: dict
class JupiterError(Exception): code: int
class JupiterSwapV2:
  def __init__(self, base_url: str, api_key: str | None, rps: float, client: httpx.AsyncClient, ledger: Ledger | None)
  async def order(self, req: SwapRequest, taker: str, slippage_bps: int) -> Order  # x-api-key header if key; raw persisted
  async def execute(self, signed_tx_b64: str, request_id: str) -> ExecResult
  async def round_trip_cost(self, mint: str, usd_notional: Decimal, taker: str, usdc_price: Decimal) -> Decimal  # buy quote then sell quote of quoted out; 1 - sell_out/buy_in
```

**Depends on:** src/tiller/models.py

**Tests.** tests/test_jupiter.py (respx): order fixture parse; error code 1-3 raise typed; execute success/fail; 429 backoff; round trip arithmetic; slippageBps always sent.

### `src/tiller/execution/guard.py`

**Responsibility.** Pure pre-sign checks with no I/O.

**Interface.**

```
class GuardCfg(BaseModel): max_price_dev_sol: Decimal='0.01'; max_price_dev_token: Decimal='0.03'; max_price_dev_emergency: Decimal='0.05'; max_impact_core: Decimal='0.01'; max_impact_token: Decimal='0.03'; max_fee_bps: int=10; routers: set[str]={'metis','jupiterz'}; program_allowlist: set[str]  # System, Token, Token-2022, ATA, ComputeBudget, Memo, Jupiter v6 aggregator, JupiterZ/DFlow ids from config
class SimExpectations(BaseModel): wallet: str; input_mint: str; output_mint: str; in_base: int; min_out_base: int; max_lamport_drop: int; owned_token_accounts: list[TokenAccount]
def check_quote(req: SwapRequest, order: Order, ref_price_usd: Decimal, in_decimals: int, out_decimals: int, cfg: GuardCfg) -> str | None  # implied price vs reference within tier; impact; fee_bps <= max (rejects 50 bps <24h tier); router allowlisted
def check_transaction(tx_bytes: bytes, our_pubkey: str, owned_token_accounts: list[str], cfg: GuardCfg) -> str | None  # VersionedTransaction.from_bytes; our key is fee payer (account_keys[0]) and a required signer; every other required-signer slot already carries a non-zero signature; every instruction program id in allowlist; no instruction whose program is Token/Token-2022 has data[0] in {Approve(4), SetAuthority(6), CloseAccount(9), ApproveChecked(13)} targeting one of our token accounts
def check_simulation(sim: SimResult, expect: SimExpectations) -> str | None  # err set; wallet owner != System Program; any token account owner != us, delegate set, close_authority not in {None, us}; lamport drop > max; input drop > in_base; output rise < min_out; any unrelated token decrease
def check_mint(mint_account: dict) -> str | None  # same rules as token gate mint part (shared helper)
```

**Depends on:** src/tiller/models.py

**Tests.** tests/test_guard.py: quote deviation each tier, impact, fee 50 rejected, router; transaction built with solders in-test: fee payer mismatch, unsigned foreign signer, program not allowlisted, Approve targeting our ATA each rejected, happy path passes; simulation fixtures for every rejection branch; token-2022 extension table.

### `src/tiller/execution/wallet.py`

**Responsibility.** Key loading (0600 file or env var), challenge message signing, transaction signing with solders; NullSigner for paper/offline. Only module allowed to import solders signing.

**Interface.**

```
class Signer(Protocol): pubkey: str; def sign_message(self, msg: bytes) -> bytes; def sign_transaction(self, tx_bytes: bytes) -> bytes
class FileSigner(Signer): @classmethod def load(cls, path: Path | None, env_secret: str | None) -> 'FileSigner'  # JSON byte array or base58; refuses files not 0600 or dirs not 0700; never logs
class NullSigner(Signer): pubkey: str  # raises PaperCannotSign on any sign
def generate_keypair(path: Path) -> str  # for `tiller keygen`, writes 0600, returns pubkey
```

**Depends on:** nothing

**Tests.** tests/test_wallet.py: sign_message verified with solders Signature.verify; 0644 file refused; env load; NullSigner raises; keygen permissions.

### `src/tiller/execution/venue.py`

**Responsibility.** Venue protocol and three implementations; the single guarded pipeline for every order.

**Interface.**

```
class Venue(Protocol): async def swap(self, req: SwapRequest, ref_price_usd: Decimal, gate: GateResult | None) -> Fill
class LiveSolanaVenue(Venue): def __init__(self, jup: JupiterSwapV2, rpc: Rpc, signer: Signer, tokens: TokenData, ledger: Ledger, cfg: ExecCfg, guard: GuardCfg, clock: Clock)
  # pipeline: slippage tier by mode -> order -> check_quote -> (non-allowlisted mint) round-trip probe -> check_transaction -> simulate -> check_simulation -> ledger.record_order(state=submitted, signature derived pre-send) -> sign -> execute (same signed bytes resubmitted at most twice within 2 min on network error; never a fresh quote) -> poll getSignatureStatuses then getTransaction -> realised_fill_from_tx -> ledger.finalize_order
class PaperVenue(Venue): def __init__(self, jup, tokens, ledger, haircut_bps: int = 30, clock)  # order + check_quote + probe, fills at out_amount*(1-haircut), signature None, never touches a signer
class FixtureVenue(Venue): def __init__(self, bars: dict[str, list[Candle]], ledger, cost_bps: int, clock)  # fills at next bar open for offline tracking test
def realised_fill_from_tx(tx: dict, wallet: str, req: SwapRequest, marks: dict[str, Decimal]) -> Fill  # from pre/postTokenBalances + pre/postBalances
```

**Depends on:** src/tiller/execution/jupiter.py, src/tiller/execution/rpc.py, src/tiller/execution/guard.py, src/tiller/execution/wallet.py, src/tiller/data/tokens.py, src/tiller/ledger.py

**Tests.** tests/test_venue.py: happy path walks order->simulate->execute->getTransaction and yields the expected Fill (respx call counts); each guard rejection => zero /execute calls; network error retry resubmits identical bytes, no second /order; ledger row exists with state=submitted before /execute (respx side_effect asserts); PaperVenue with NullSigner never signs; emergency mode uses 150 bps and 5% tolerance; FixtureVenue fills at next open.

### `src/tiller/familiars/client.py`

**Responsibility.** Typed familiars client with token bucket (1 RPS), 429 backoff with jitter, raw persistence of every read, register never retried.

**Interface.**

```
class FamiliarsClient:
  def __init__(self, base_url: str, api_key: str | None, rps: float, client: httpx.AsyncClient, ledger: Ledger, clock: Clock)
  async def challenge(self, wallet: str) -> Challenge(nonce, message, expires_at)
  async def register(self, req: RegisterRequest(wallet, nonce, signature_b64, handle, name, bio, strategy, color)) -> RegisterResponse(api_key, owner_key, login_url, handle)  # single attempt, any exception propagates
  async def me(self) -> OwnerLimits  # readable=False on any error
  async def post(self, kind: Literal['note','callout','trade'], text: str, mint: str | None, signature: str | None) -> PostResult(status: Literal['ok','rate_limited','uncertain','rejected'], raw)
  async def agents(self, range: Literal['24H','7D','30D','ALL']) -> list[PublicAgent]
  async def agent(self, handle: str) -> AgentDetail | None
  async def tokens(self) -> list[TokenInfoBoard(mint, symbol, agents: int, last_trade_at, price_usd, liquidity_usd)]
```

**Depends on:** src/tiller/models.py, src/tiller/ledger.py

**Tests.** tests/test_familiars_client.py (respx): each endpoint parses fixture; raw snapshot written before parse (even on parse failure); register called exactly once on 5xx; me() unreadable on 500; post 429 -> rate_limited, timeout -> uncertain; bucket spacing.

### `src/tiller/familiars/poster.py`

**Responsibility.** Post queue: trade posts with not_before = fill+90 s, retry only on 429, uncertain outcomes reconciled against our own public posts by signature before any retry, callouts <= 2/day, one daily note, nothing posted in paper mode.

**Interface.**

```
class PostQueue:
  def __init__(self, client: FamiliarsClient, narrator: Narrator, ledger: Ledger, clock: Clock, handle: str)
  def enqueue_trade(self, fill: Fill, ctx: TradeContext) -> None  # ignored if fill.paper or signature None
  async def flush(self) -> list[PostOutcome]
  async def daily_note(self, acct: AccountSnapshot, brakes: BrakeState) -> None
  async def verify_posted(self, signature: str) -> bool  # GET agent(handle).posts
```

**Depends on:** src/tiller/familiars/client.py, src/tiller/familiars/narrator.py, src/tiller/ledger.py

**Tests.** tests/test_poster.py: 90 s delay honoured with SimClock; 429 retried with backoff; uncertain then found on board -> no duplicate; uncertain then absent -> retried once; paper fills never enqueued; callout cap.

### `src/tiller/familiars/narrator.py`

**Responsibility.** Explanation text. TemplateNarrator default; ClaudeNarrator optional with validation, 5 s timeout, daily spend cap, fallback. Cannot import execution modules.

**Interface.**

```
class Narrator(Protocol): async def compose(self, ctx: TradeContext) -> str
class TemplateNarrator(Narrator)  # deterministic <=400 chars, includes strategy, rule, size, stop, brake state, 'automated rule-based agent; not advice'
class ClaudeNarrator(Narrator): def __init__(self, api_key: str, model: str, daily_cap_usd: Decimal, fallback: Narrator, clock: Clock)
def validate_post_text(text: str, ctx: TradeContext) -> str | None  # reasons: >400 chars, missing strategy name, contains URL, contains a base58 string of 32-44 chars other than ctx.mint, contains a number not present in ctx (digits check), contains price-target phrases ('will', 'moon', 'target', 'x by', 'guaranteed')
```

**Depends on:** src/tiller/models.py

**Tests.** tests/test_narrator.py: template output valid; validator rejects each case; ClaudeNarrator with a fake client falls back on timeout/invalid/cap exhausted; import-graph test that tiller.familiars.narrator and tiller.copy never import tiller.execution.wallet.

### `src/tiller/copy/models.py`

**Responsibility.** Copy-module models.

**Interface.**

```
class LeaderTrade(BaseModel): key: str  # 'fam:<handle>' or 'wallet:<addr>'
  wallet: str; signature: str; ts: datetime; detected_at: datetime; side: Literal['buy','sell']; mint: str; usd_value: Decimal; amount: Decimal; price_usd: Decimal | None; source: Literal['familiars','wallet']; chain_verified: bool
class LeaderScore(BaseModel): key: str; wallet: str; qualified: bool; reasons: list[str]; score: float; cluster_id: int; replay_pf: float | None; replay_expectancy_pct: Decimal | None; n_replayed: int
class ShadowTrade(BaseModel): id: str; leader_key: str; signal_ts: datetime; mint: str; leader_price: Decimal; entry_price: Decimal  # worst-of(quote at detection, leader_price*1.01) then +1% cost haircut
  lag_s: float; lag_cost_pct: Decimal; size_usd: Decimal; exit: ExitRule; marks: dict[str, Decimal]  # '1h','6h','24h'
  exit_price: Decimal | None; exit_ts: datetime | None; exit_reason: str | None; pnl_pct: Decimal | None
class ShadowReport(BaseModel): days: int; n_trades: int; expectancy_pct: Decimal; profit_factor: float; median_lag_s: float; median_lag_cost_pct: Decimal; p_positive_block20: float; leaders_positive_share: float; per_leader: list[LeaderStats]; promotion_gate_met: bool; reasons: list[str]
```

**Depends on:** src/tiller/models.py

**Tests.** parse tests.

### `src/tiller/copy/feeds.py`

**Responsibility.** Two feeds producing LeaderTrade: familiars agent detail polling (signature dedupe, chain verification via getTransaction before a trade counts; unverified after 5 min are dropped) and RPC wallet polling with a pure swap parser.

**Interface.**

```
class FamiliarsFeed: def __init__(self, client: FamiliarsClient, rpc: Rpc, ledger: Ledger, clock: Clock); async def poll(self, handles: list[str]) -> list[LeaderTrade]
class WalletFeed: def __init__(self, rpc: Rpc, ledger: Ledger, clock: Clock); async def poll(self, wallets: list[str]) -> list[LeaderTrade]  # getSignaturesForAddress(limit 25, until last seen) then getTransaction
def parse_swap(tx: dict, wallet: str, stable_mints: set[str], sol_mint: str) -> LeaderTrade | None  # buy: stable/SOL balance down AND token up in same tx; sell: reverse; transfers/airdrops -> None; multi-hop resolved by net deltas
```

**Depends on:** src/tiller/copy/models.py, src/tiller/familiars/client.py, src/tiller/execution/rpc.py, src/tiller/ledger.py

**Tests.** tests/test_copy_feeds.py: parse_swap on buy/sell/transfer/multihop fixtures; dedupe by signature across polls; unverified familiars trade not emitted, then emitted once getTransaction confirms; dropped after 5 min.

### `src/tiller/copy/leaders.py`

**Responsibility.** Eligibility from OUR logs only (>=14 days), lag-adjusted copied-P&L replay scoring, exclusions, clustering (>80% mint overlap counts once), sticky pool (enter top-N, leave below 1.5N), demotion.

**Interface.**

```
def eligible(trades: list[LeaderTrade], detail: AgentDetail | None, token_meta: dict[str, TokenInfo], cfg: CopyCfg, now: datetime, first_seen: datetime) -> tuple[bool, list[str]]  # now-first_seen >= 14 d; >=30 closed round trips; >=10 distinct mints; median hold >= 4 h; no single mint > 50% of gross realised P&L (MTM at Price V3 for open); platform snapshot drawdown < 30% (if detail); zero buys of mints < 24 h old at buy time; no mint whose dev/creator == leader wallet (own token); no transfer-in within 24 h of a P&L jump (if detail)
def replay(trades: list[LeaderTrade], marks: MarkStore, exit_rules: ExitRule, lag_s: int = 60, slip: Decimal = '0.01', fee: Decimal = '0.001') -> ReplayResult(n, pf, expectancy_pct, sortino, weekly_positive_share)
def score_leaders(candidates: dict[str, list[LeaderTrade]], details: dict[str, AgentDetail | None], marks: MarkStore, token_meta, cfg, now, first_seen: dict[str, datetime]) -> list[LeaderScore]  # score = mean rank(pf, sortino, weekly_positive_share); requires pf >= 1.3 on >= 20 replayed signals to qualify; board pnl/winRate NEVER used
def cluster(scores: list[LeaderScore], trades_by_key, overlap: float = 0.8) -> list[LeaderScore]
def select_pool(scores, prev_pool: set[str], n: int = 20) -> set[str]  # sticky
def should_demote(ls: LeaderScore, copied_pnl_4w: Decimal, recent: list[LeaderTrade], token_meta, last_seen: datetime, now) -> str | None  # negative 4-week copied P&L; young-mint buy; own-token; drawdown > 30%; absent 48 h
```

**Depends on:** src/tiller/copy/models.py, src/tiller/data/tokens.py

**Tests.** tests/test_leaders.py: synthetic seeded streams: each eligibility rule positive/negative; replay deterministic (same PF twice); ranking ignores board pnl (mutating it changes nothing); clustering merges sybils; sticky pool hysteresis; each demotion reason.

### `src/tiller/copy/shadow.py`

**Responsibility.** Always-on hypothetical copier book; promotion-gate report with seeded bootstrap.

**Interface.**

```
class ShadowTracker:
  def __init__(self, jup: JupiterSwapV2 | None, prices: PriceSource, tokens: TokenData, ledger: Ledger, cfg: CopyCfg, clock: Clock, rng_seed: int = 42)
  async def on_leader_trades(self, trades: list[LeaderTrade], pool: set[str], consensus_mints: list[str]) -> list[ShadowTrade]  # opens one shadow trade per consensus signal (and, for per-leader stats, one per followed-leader buy) at worst-of(detection quote, leader_price*1.01) minus 1% cost; size 2% equity nominal
  async def mark_and_exit(self) -> None  # marks 1h/6h/24h, applies exit rules: stop -20%, trail 25% from +30%, time stop 48 h, liquidity collapse 60%
  def report(self, days: int = 60) -> ShadowReport  # promotion_gate_met = days>=60 and n>=100 and expectancy>1% and p_positive_block20>=0.8 and leaders_positive_share>=0.4
def bootstrap_p_positive_block(returns: list[Decimal], block: int = 20, draws: int = 5000, seed: int = 42) -> float
```

**Depends on:** src/tiller/copy/models.py, src/tiller/execution/jupiter.py, src/tiller/data/prices.py, src/tiller/ledger.py

**Tests.** tests/test_shadow.py: SimClock 60-day synthetic run with FakePrices: lag cost computed, exits fire correctly, report totals match hand-computed, gate closed at 59 days / 99 trades / negative expectancy, bootstrap deterministic with seed.

### `src/tiller/copy/strategy.py`

**Responsibility.** Live consensus copy strategy (disabled by default) and the pure consensus/crowd-burst/late-entry helpers used by the shadow tracker too.

**Interface.**

```
def consensus(trades: list[LeaderTrade], pool: set[str], clusters: dict[str, int], window: timedelta, min_clusters: int, now: datetime) -> list[ConsensusSignal(mint, earliest_leader_price, cluster_count, first_ts)]
def crowd_burst(prev_tokens: list[TokenInfoBoard], cur_tokens: list[TokenInfoBoard], mint: str, max_delta: int = 15) -> bool
def late_entry_ok(current_price: Decimal, earliest_leader_price: Decimal, max_above: Decimal = '0.15') -> bool
class ConsensusCopyStrategy(Strategy): name='copy_consensus'; cadence='monitor'; def __init__(self, cfg: CopyCfg, tracker: ShadowTracker, gate: Callable, regime_on: Callable[[], bool]); def targets(self, ctx) -> ...  # returns [] unless cfg.live and tracker.report().promotion_gate_met and regime_on(); entries carry ExitRule(stop -20%, trail 25% from +30%, time stop 48 h); max 2 concurrent, 2 entries/day, sleeve cap 5%
```

**Depends on:** src/tiller/copy/shadow.py, src/tiller/copy/leaders.py, src/tiller/strategies/base.py

**Tests.** tests/test_copy_strategy.py: consensus needs 3 distinct clusters within window; burst exclusion; late entry; targets empty when live=false, when gate unmet, when regime off; caps.

### `src/tiller/engine.py`

**Responsibility.** The tick and scheduler. Tick order: lock heartbeat -> load state -> reconcile (boot and every 10th monitor tick) -> owner limits -> candles -> account snapshot -> brakes -> must_flatten (emergency exits) -> position exit rules -> daily strategies (inside 00:05-06:00 UTC window, once per bar) -> allocator plan -> risk check/size -> venue swap -> ledger/state -> post queue flush -> board snapshots (15 min) -> copy feeds (60 s) + shadow marks -> alerts -> save state. run_forever: 60 s monitor loop; daily job window retry every 15 min until done; 15 min board snapshot; 6 h leader re-score.

**Interface.**

```
class Deps(BaseModel, arbitrary_types_allowed=True): venue: Venue; rpc: Rpc; candles: DailyCandleStore; prices: PriceSource; cex: KrakenTicker | None; tokens: TokenData; familiars: FamiliarsClient | None; ledger: Ledger; strategies: list[Strategy]; poster: PostQueue | None; shadow: ShadowTracker | None; feeds: tuple[FamiliarsFeed | None, WalletFeed | None]; alerts: Alerts; signer_pubkey: str
class TickReport(BaseModel): ts: datetime; equity_usd: Decimal; brakes: BrakeState; intents: list[OrderIntent]; fills: list[Fill]; refused: list[tuple[OrderIntent, str]]; flattened: bool
class Agent: def __init__(self, cfg: Config, deps: Deps, clock: Clock, state_path: Path, kill_file: Path); async def tick(self) -> TickReport; async def run_forever(self) -> None; async def flatten(self, reason: str) -> list[Fill]  # emergency mode, size-ascending, keeps retrying every 5 min with alerts until flat; sets halted=True
```

**Depends on:** all modules above

**Tests.** tests/test_engine_e2e.py: (1) 30-day SimClock paper run over fixture candles with FakeJupiter: at most one core trade per day, band respected, restart mid-run leaves ledger/state consistent, no post emitted in paper; (2) kill drill: equity -26% -> flatten in size-ascending order, halted persists across restart, resume required; (3) fail-closed owner limits: me() 500 -> no entries, exits still executed; (4) tracking test: FixtureVenue offline run over the full SOL history reproduces the backtester equity curve within 0.5% (band modelled in both); (5) loop guards: forced failures -> 1 h pause.

### `src/tiller/backtest/runner.py`

**Responsibility.** Vectorised daily backtester using the same exposure functions as live; conventions from analysis/common.py (signal on close t, fill open t+1, cost per side on |Δexposure|, warmup 250, IS/last-24m split); parameter grid; markdown report.

**Interface.**

```
class Metrics(BaseModel): cagr: float; vol: float; sharpe: float; max_dd: float; time_in_market: float; avg_exposure: float; round_trips: int; turnover_per_year: float
class BacktestResult(BaseModel): full: Metrics; in_sample: Metrics; last_24m: Metrics; calendar_years: dict[int, float]; equity: list[float]; dates: list[str]
def backtest_exposure(exposure: np.ndarray, opens: np.ndarray, closes: np.ndarray, dates, cost_bps: int, warmup: int = 250, band: float | None = None, oos_start: date = date(2024,9,24)) -> BacktestResult
def run_grid(sol: list[Candle], btc: list[Candle], cost_bps_list=[5,10,30], vol_targets=[0.25, None], btc_confirm=[False, True], bands=[None, 0.02]) -> dict[str, BacktestResult]  # rules: buy_and_hold, sma200_hyst2pct, donchian_ensemble, donchian_ensemble_voltarget25, combined_default (A+B netted with beta cap)
def render_report(results: dict[str, BacktestResult]) -> str  # docs/BACKTEST.md
```

**Depends on:** src/tiller/strategies/sol_trend.py, src/tiller/strategies/indicators.py

**Tests.** tests/test_backtest.py: golden numbers as in sol_trend tests; buy_and_hold 26.2% CAGR / -96.3% DD; band variant has lower turnover; report renders.

### `src/tiller/alerts.py`

**Responsibility.** Structured JSON logging plus optional Telegram alerts (send only; no command handling). Secrets redacted.

**Interface.**

```
class Alerts: def __init__(self, telegram_token: str | None, chat_id: str | None, client: httpx.AsyncClient | None); async def send(self, level: Literal['info','warn','critical'], text: str) -> None; def log(self, kind: str, **fields) -> None
```

**Depends on:** nothing

**Tests.** tests/test_alerts.py: redaction of fam_/jup_/base58-64-char secrets; Telegram call shape (respx).

### `src/tiller/cli.py`

**Responsibility.** argparse CLI; the only place real dependencies are wired.

**Interface.**

```
tiller init | keygen [--path] | register --handle H --name N [--bio --strategy --color] | preflight [--record] | backtest [--cost-bps 5,10,30] [--out docs/BACKTEST.md] | tick --once [--mode offline|paper|live] | run [--mode paper|live] | status | shadow-report [--days 60] | pause | resume | flatten --yes | reconcile [--accept] | sweep --to ADDR --keep USD --yes | export-tax --out FILE | review (prints the markdown diagnosis input; LLM optional)
# `run --mode live` requires: config mode live, skill_md_reviewed_at fresh, `--i-have-read-skill-md` on the first live start (persisted), canary state; prints the geo warning if any optional venue is enabled and aborts
```

**Depends on:** src/tiller/engine.py, src/tiller/config.py

**Tests.** tests/test_cli.py: each command parses; live refused without acknowledgement; sweep refused without --yes; register refuses if a fam_ key already exists.

### `tests/conftest.py + tests/fakes.py`

**Responsibility.** No-network guard (respx assert_all_mocked plus a socket-level guard so any unmocked host raises), SimClock, tmp ledger/state, fixture loaders, in-process fakes.

**Interface.**

```
fixtures: no_network (autouse), sim_clock, tmp_ledger, tmp_state, load_fixture(name) -> dict, sol_daily, btc_daily, eth_daily -> list[Candle]
class FakeRpc(Rpc): scriptable balances, token accounts, mint accounts (spl/token-2022 with chosen extensions), simulate returning a chosen fixture, statuses, transactions
class FakeJupiter: order(price, impact, fee_bps, router), execute(outcome)
class FakeFamiliars: canned agents/detail/tokens/me with mutable state
class FakePrices(PriceSource)
```

**Depends on:** nothing

**Tests.** self-test that an unmocked httpx GET raises.


## Work packages

### WP-A: Foundation, strategies and backtester (golden tests)

**Depends on:** nothing

**Files:** `pyproject.toml`, `requirements.lock`, `Makefile`, `.gitignore (extend)`, `config/tiller.example.toml`, `config/owner_overrides.example.json`, `src/tiller/__init__.py`, `src/tiller/config.py`, `src/tiller/models.py`, `src/tiller/clock.py`, `src/tiller/ledger.py`, `src/tiller/state.py`, `src/tiller/copy/models.py`, `src/tiller/strategies/indicators.py`, `src/tiller/strategies/base.py`, `src/tiller/strategies/sol_trend.py`, `src/tiller/strategies/breakout_4h.py (stretch)`, `src/tiller/backtest/runner.py`, `data/history/{SOLUSDT,BTCUSDT,ETHUSDT}_1d.csv`, `tests/conftest.py`, `tests/fixtures/ohlcv/*`, `tests/fixtures/expected/study1_sol.json`, `tests/test_config.py`, `tests/test_models.py`, `tests/test_ledger.py`, `tests/test_state.py`, `tests/test_indicators.py`, `tests/test_sol_trend_golden.py`, `tests/test_backtest.py`, `docs/BACKTEST.md`

Create the package skeleton (src layout, console script `tiller` pointing at tiller.cli:main which other WPs fill; ship a placeholder main that prints 'not wired'). Pin exactly: solders==0.29.0 httpx==0.28.1 pydantic==2.13.5 numpy==2.4.6 pandas==3.0.6; dev: pytest==9.1.1 pytest-asyncio==1.4.0 respx==0.23.1 ruff; extras [llm]: anthropic==1.8.0. Generate requirements.lock (pip-compile --generate-hashes; if pip-tools install fails, a plain pinned requirements.lock with a TODO is acceptable). Copy the three daily CSVs from /tmp/claude-0/-home-user-agent-trading/7c78e63b-542a-52d1-9c81-ffdeb4d14149/scratchpad/data/normalized/ into data/history/ and tests/fixtures/ohlcv/ (they are ~100 KB each). Implement models, config with ALL L0 invariants and effective_allocation, clock, ledger (full schema incl. copy tables and raw_snapshots), state + InstanceLock, indicators, strategies/base, sol_trend (vectorised functions + Strategy wrappers with per-bar state), backtest runner with the common.py conventions (signal on close t, fill at open t+1, cost per side on |Δexposure|, warmup 250, IS/last-24m split at 2024-09-24, calendar years) plus the rebalance-band variant and the combined_default rule (A+B netted with beta cap min(0.50,0.30/rv90)). Golden tests must reproduce RESULTS.md Study 1 SOL numbers within the tolerances in the module contract (donchian_ensemble 60.8%/1.20/-45.2%/161 RT; voltarget25 14.8%/-14.2%; sma200 57.9%/-69.1%/16 RT) and BTC sma200 17 RT. Write conftest with the autouse no-network guard (respx.mock(assert_all_mocked=True) plus a socket guard) and fixture loaders. Run `tiller backtest` logic via a module entry (python -m tiller.backtest) to write docs/BACKTEST.md covering cost 5/10/30, btc_confirm on/off, band on/off, combined_default; the report must include the calendar-year table and last-24m metrics. Stretch only if everything else is green: breakout_4h pure signal with the Study-2 regression (90 trades ±2). Everything must run offline; `pytest -q` green is the acceptance.

### WP-B: Execution layer: RPC, Jupiter Swap V2, guard, wallet, venues, token gate, prices

**Depends on:** WP-A

**Files:** `src/tiller/execution/rpc.py`, `src/tiller/execution/jupiter.py`, `src/tiller/execution/guard.py`, `src/tiller/execution/wallet.py`, `src/tiller/execution/venue.py`, `src/tiller/data/tokens.py`, `src/tiller/data/prices.py`, `src/tiller/data/candles.py`, `tests/fakes.py`, `tests/fixtures/jupiter/*.json`, `tests/fixtures/rpc/*.json`, `tests/fixtures/kraken/*.json`, `tests/test_rpc.py`, `tests/test_jupiter.py`, `tests/test_guard.py`, `tests/test_wallet.py`, `tests/test_venue.py`, `tests/test_token_gate.py`, `tests/test_prices.py`, `tests/test_candles.py`

Implement against the interfaces in module_contracts using only models.py/ledger.py/clock.py from WP-A (start from the WP-A branch once models land; if WP-A is not finished, code against the contract and reconcile). Hand-build fixtures to the documented shapes: Jupiter /swap/v2/order response (transaction b64, requestId, inAmount, outAmount, slippageBps, priceImpactPct, feeBps, router, mode), /execute (status, signature, code), Price V3 ({mint: {usdPrice}}), Tokens V2 search (organicScore, audit{mintAuthorityDisabled, freezeAuthorityDisabled, topHoldersPercentage, devBalancePercentage, isSus}, holderCount, launchpad, firstPool{createdAt}, dev, liquidity, mcap), Shield ({warnings: {mint: [{type}]}}), Kraken OHLC and Ticker, Coinbase candles, RPC getBalance/getTokenAccountsByOwner(jsonParsed)/getAccountInfo for spl mint and token-2022 mints with each dangerous extension/simulateTransaction post-states (ok, delegate set, owner reassigned, closeAuthority changed, SOL overdraw, output short, unrelated decrease, err)/getSignatureStatuses/getTransaction with pre/post balances (buy, sell, transfer, multihop). Label every fixture file with a 'synthetic: true' top-level marker comment file (fixtures/README.md). Build real VersionedTransactions in tests with solders (Message with our key as fee payer, a System transfer instruction, plus adversarial variants: foreign unsigned signer, non-allowlisted program, Token Approve targeting our ATA) so check_transaction is tested on real bytes. LiveSolanaVenue must: never call /execute when any guard rejects (respx call-count assertions), write the submitted ledger row before /execute, resubmit identical bytes on network error without a second /order, support mode='emergency' tiers, and compute the realised Fill from the getTransaction fixture. PaperVenue must never touch the signer (NullSigner raises). FixtureVenue fills at next open. Token gate: table test per rejection reason; Shield None blocks only non-allowlisted; mint parse binding. Candle store: closed-bar guarantee, stale check, fallback, cache append. All offline, `pytest -q` green.

### WP-C: familiars client, poster, narrator, alerts

**Depends on:** WP-A

**Files:** `src/tiller/familiars/client.py`, `src/tiller/familiars/poster.py`, `src/tiller/familiars/narrator.py`, `src/tiller/alerts.py`, `tests/fixtures/familiars/*.json`, `tests/test_familiars_client.py`, `tests/test_poster.py`, `tests/test_narrator.py`, `tests/test_alerts.py`, `tests/test_import_graph.py`

Implement the typed familiars client per docs/familiars-api.md and the module contract: 1 RPS token bucket, 429 backoff with jitter honouring x-ratelimit headers, raw persistence of every response via ledger.add_raw_snapshot BEFORE parsing, register with exactly one attempt (test: 5xx -> exception, respx count == 1), me() returning readable=False on any error, post outcomes ok/rate_limited/uncertain/rejected. PostQueue with SimClock-tested 90 s delay, 429-only retry, uncertain reconciliation via agent(handle).posts by signature, callout cap 2/day, daily note, paper fills never enqueued. TemplateNarrator (deterministic, <=400 chars, includes strategy/rule/size/stop/brake state and the disclosure line 'automated rule-based agent; not financial advice') and validate_post_text with every rule in the contract; ClaudeNarrator behind an injected async callable (so tests never import anthropic) with 5 s timeout, daily cap, fallback. Alerts: JSON logging with secret redaction (fam_, jup_, sk-ant-, 64-88 char base58) and optional Telegram sendMessage via httpx (respx-tested); no inbound command handling. tests/test_import_graph.py: parse imports of tiller.familiars.narrator and every tiller.copy module and assert none import tiller.execution.wallet or tiller.execution.venue (this test is owned here but covers WP-E code; write it to scan whatever exists). Fixtures hand-built to the contract shapes (PublicAgent, AgentDetail with trades/positions/history.snapshots/posts, tokens with agents counts, me with settings, challenge/register responses) and marked synthetic.

### WP-D: Risk engine, allocator, reconciliation, engine tick/scheduler, CLI

**Depends on:** WP-A, WP-B, WP-C

**Files:** `src/tiller/risk/account.py`, `src/tiller/risk/engine.py`, `src/tiller/risk/reconcile.py`, `src/tiller/portfolio/allocator.py`, `src/tiller/engine.py`, `src/tiller/cli.py`, `src/tiller/venues_optional.py`, `deploy/tiller.service`, `Dockerfile`, `tests/test_account.py`, `tests/test_risk_engine.py`, `tests/test_reconcile.py`, `tests/test_allocator.py`, `tests/test_engine_e2e.py`, `tests/test_cli.py`

Implement the pure risk engine exactly per risk_rules (L2-L8) with table tests on both sides of every threshold, directive prefix parsing ('please do not pause' must not pause), tighten-only effective_limits with fail-closed None, canary verified-event gate then 25/50/100 ramp with reset on brake trip, sizing that reports the binding cap, loop guards (1 entry/tick, 6 swaps/day with emergency exempt, 3 failures -> 1 h pause, 3 sim failures -> 24 h blocklist), must_flatten from the rolling 30-day peak. Allocator: net A+B, SOL-beta cap, cash floor pro-rata scaling, per-token cap, band max(2% equity, $10), exits first then one entry, ExitRule evaluation for stop-managed positions. Reconcile: pending-signature finalisation, chain rebuild, amount-based mismatch, familiars cost-basis backfill, --accept. Engine tick in the documented order with the daily job window (00:05-06:00 UTC, retry every 15 min until evaluated once per bar), 60 s monitor loop, 15 min board snapshot, 6 h re-score hooks (call into WP-E objects if present, else no-op), flatten in emergency mode retrying every 5 min, halted persisted. CLI per contract (argparse; wire real deps only here; `tick --once`; `run --mode live` refuses without fresh skill_md_reviewed_at and --i-have-read-skill-md on first start; sweep needs --yes; register refuses if key exists; hyperliquid/cex enabled -> geo warning + NotImplementedError). E2E tests with tests/fakes.py and SimClock: 30-day paper run (one core trade/day max, band respected, restart mid-run consistent, no posts), kill drill (equity -26% -> size-ascending flatten, halted survives restart, resume clears), fail-closed owner limits (500 -> no entries, exits proceed), loop-guard pause, and the tracking test: `tick --once --mode offline` with FixtureVenue over the full SOL history (SimClock stepping days) reproduces the backtester combined_default equity curve within 0.5% (both model the band). Dockerfile non-root read-only except /state; systemd unit Restart=on-failure.

### WP-E: Copy-trading module: feeds, leader scoring, shadow tracker, consensus strategy, shadow-report

**Depends on:** WP-A, WP-B, WP-C

**Files:** `src/tiller/copy/feeds.py`, `src/tiller/copy/leaders.py`, `src/tiller/copy/shadow.py`, `src/tiller/copy/strategy.py`, `docs/COPY-SHADOW.md`, `tests/test_copy_feeds.py`, `tests/test_leaders.py`, `tests/test_shadow.py`, `tests/test_copy_strategy.py`, `tests/fixtures/familiars/agents_*.json (extend)`, `tests/fixtures/rpc/tx_*.json (extend)`

Implement per module_contracts and strategy_portfolio: FamiliarsFeed (poll followed handles, dedupe by signature, chain verification via rpc.get_transaction before a trade counts, drop after 5 min unverified), WalletFeed (getSignaturesForAddress since last seen + getTransaction), pure parse_swap (net balance deltas; transfers/airdrops -> None; multi-hop resolved). leaders.py: eligibility from OUR logs only (first_seen >= 14 days, >=30 round trips, >=10 mints, median hold >=4 h, single-mint share <=50% at MTM marks, platform DD <30%, no <24 h buys, own-token via Tokens V2 dev, transfer-in check), replay scoring (lag 60 s, worst-of price, +1% slip +10 bps, our exit rules), score = mean rank(PF, Sortino, weekly-positive share) with PF>=1.3 on >=20 signals, board pnl/winRate never used (test mutates it and asserts no change), clustering by >80% mint overlap, sticky pool 20/30, demotion reasons. shadow.py: ShadowTracker opening trades at worst-of(detection quote, leader*1.01) minus 1%, per-trade lag and lag cost, 1h/6h/24h marks, own exits (stop -20%, trail 25% from +30%, time 48 h, liquidity collapse 60%; leader sells only tighten trail to 15%), report with expectancy, PF, median lag, seeded bootstrap P(positive 20-trade block), leaders_positive_share and the promotion gate (60 d, 100 trades, >1%, >=0.80, >=40%). strategy.py: consensus (>=3 distinct clusters within 30 min), crowd_burst from /api/tokens diffs (+>15 in 1 h), late_entry_ok (<=15%), ConsensusCopyStrategy returning [] unless live AND gate met AND SOL regime ON, entries with ExitRule, caps 2 concurrent / 2 per day / 5% sleeve / 2% per signal. Tests use seeded synthetic leader streams (generate in-test with random.Random(42)) and FakePrices/FakeRpc; a 60-day SimClock run must keep the gate closed at negative expectancy, at 59 days, and at 99 trades; replay must be deterministic. Write docs/COPY-SHADOW.md (what is recorded, how to read `tiller shadow-report`, why live is off, promotion procedure, expected bleed). Must not import tiller.execution.wallet/venue (WP-C import-graph test).

### WP-F: Docs, runbook, fixture recorder, hardening review, backtest-driven default, commit and push

**Depends on:** WP-D, WP-E

**Files:** `README.md`, `docs/RUNBOOK.md`, `docs/SPEC.md`, `docs/BACKTEST.md (regenerate)`, `tools/record_fixtures.py`, `tests/test_fixture_schema.py`, `config/tiller.example.toml (btc_confirm default per rule)`, `Makefile (lint target with typosquat check)`, `any bug fixes from the review`

1) Apply the deterministic default rule: read docs/BACKTEST.md; set strategies.*.btc_confirm default true in config/tiller.example.toml and the pydantic defaults ONLY if the btc_confirm=true variant has last-24m Sharpe >= and max DD >= (less negative) than the false variant for BOTH rules; record the decision and numbers in README. 2) README: what it is, the two measured sleeves with the RESULTS.md numbers and calendar-year table, honest expectations (8-25% trending years, ~0-8% chop years, 15-25% DD, 6-12 month flat spells), what it will not do (rank on the board, copy live by default, trade launches, run perps), hot-key risk statement, tax note. 3) RUNBOOK: VPS + Docker/systemd, secrets file layout and permissions, Helius/Jupiter keys, `tiller keygen` -> fund working capital only -> `tiller run --mode paper` >= 14 days -> re-read skill.md checklist -> `tiller register` -> `tiller preflight --record` -> `tiller run --mode live --i-have-read-skill-md` canary -> ramp; pause/resume/flatten/reconcile/sweep/export-tax; geo warning for optional venues; dependency cooldown policy; monthly parameter-change policy; incident playbook incl. manual exit if Jupiter routes break; Claude Code routine usage via `tiller tick --once`. 4) tools/record_fixtures.py: online script (user runs) that hits Kraken/Coinbase/Jupiter (order for $10 SOL/USDC with taker, price v3, tokens v2 for SOL, shield)/familiars public endpoints (+ me with key) and Solana RPC (getAccountInfo for SOL mint, getTokenAccountsByOwner) and writes redacted JSON into tests/fixtures/recorded/; tests/test_fixture_schema.py parses both synthetic and recorded fixtures (recorded skipped if absent) into the same pydantic models. 5) Adversarial review pass over guard.py, risk/engine.py, venue.py, wallet.py, poster.py, config.py: look for a path where /execute can be reached without simulation, an entry without limits, a secret in logs, a retry that re-quotes, a directive substring match, a post duplicate; fix or log as known limitation in docs/SPEC.md. 6) `make lint` adds a typosquat check of requirements.lock against a small denylist. 7) Full `pytest -q` green, ruff clean, `git add -A && git commit` and push to the configured remote (ask the orchestrator for the remote if none is set).


## Open decisions for the user

- Residency/jurisdiction: confirm whether you are a US or Ontario person; the default (Solana spot only) is legal everywhere, and the Hyperliquid/CEX stubs stay off unless you set geo_ack. Also confirm EU residency for MiCA Art. 91 post-disclosure wording.
- Read https://familiars.family/skill.md from an unrestricted machine before live and answer the preflight checklist: posting obligations and rate limits, exact own-token wording, whether other agents' tokens may be traded, whether LST (JitoSOL) or kToken holdings count as equity, copy-trading disclosure, multi-wallet and wash-trading rules, any fees, API-key rotation.
- Hold asset: keep SOL (default) or switch strategies.hold_mint to JitoSOL for ~6-8% staking yield once skill.md confirms LSTs count as equity (Jupiter fee rises from 2 to 5 bps; round-trip probe must stay <=0.3%).
- Capital and fixed costs: at $500-2k the ~$6-90/month stack (VPS, Jupiter Free/Developer, Helius Agent) can exceed expected gross return; decide the wallet size (recommended: 10-20% of total crypto capital, working capital only, sweep profits) and whether to buy a Jupiter Free key (1 RPS) instead of keyless 0.5 RPS.
- btc_confirm default: WP-F applies the deterministic rule from docs/BACKTEST.md; you may override in config after reading the table.
- Copy-trading external wallets: provide any Solana addresses (e.g. resolved fomo.family leaders) for copy.external_wallets; leave empty to shadow-track only familiars agents. Decide, after >=60 days, whether to set copy.live=true if the promotion gate is met (expectation: it will most likely document a bleed).
- LLM narrator: keep the deterministic template (default, zero cost/risk) or enable narrator='claude' with an Anthropic key and a $0.50/day cap.
- Alerts: provide a Telegram bot token and chat id (alerts only, no remote commands) or rely on logs.
- Git remote: confirm the remote/branch to push to (repo currently has no remote configured in this sandbox).
- Post-MVP options to prioritise later: Kamino USDC parking (needs eligibility answer), the 4h liquid-token breakout satellite in paper mode (needs GeckoTerminal source and point-in-time universe), Hyperliquid read-only leaderboard feed.
