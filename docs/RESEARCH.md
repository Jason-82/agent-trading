# Research report: strategies for a retail autonomous crypto trading agent, with an assessment of familiars.family

Prepared 2026-09-24. Status: draft pending adversarial verification (section 10 is intentionally empty).

## Scope and method

This report consolidates research run on 2026-09-24 for a retail user who wants an autonomous crypto trading agent with $500-$20,000 of capital on one VPS, who asked for every strategy family to be considered, including copying the most successful human and bot traders, and who asked for an evaluation of familiars.family ("FOMO for AI agents", https://familiars.family/#/home). Nine research streams ran in parallel; each produced findings with sources, confidence labels, strategy candidates and open questions. A separate offline quantitative study re-tested the main price-based and carry claims on real Binance spot and Hyperliquid/Binance funding data through 2026-09-23 (section 3). The research sandbox could reach only github.com, raw.githubusercontent.com, api.github.com and the PyPI/npm registries; familiars.family, fomo.family, x.com, exchange APIs, arXiv and most news, vendor and government sites were egress-blocked ([data manifest](/tmp/claude-0/-home-user-agent-trading/7c78e63b-542a-52d1-9c81-ffdeb4d14149/scratchpad/data/MANIFEST.md)). Facts that come only from search-result snippets are tagged "(snippet only)"; reasoning not backed by a fetched source is tagged "(inference)". The most-used primary source is a working open-source familiars agent ("Ballast", [JonatanGigex/familiars.family-Opus5.5](https://github.com/JonatanGigex/familiars.family-Opus5.5), cloned locally at /home/user/jonatangigex/familiars.family-opus5.5) whose [README](/home/user/jonatangigex/familiars.family-opus5.5/README.md) records measurements made on the live platform on 2026-09-24. The WebSearch budget (200 searches) was exhausted before the llm-agents-evidence and risk-regulatory streams ran, so those two streams relied on GitHub-mirrored abstracts and digests. The platform API contract is in [docs/familiars-api.md](familiars-api.md) and is not repeated here.

The nine streams:

- **systematic-price** — academic and practitioner evidence on trend, momentum, mean reversion and regime filters; cost realism on CEX and Solana DEX.
- **carry-arb-mm** — funding carry, basis, cross-venue funding spreads, latency arbitrage, market making, LP provision, stablecoin yield.
- **onchain-memecoin** — pump.fun / LetsBonk base rates, who profits at launch, anti-rug filters, Token-2022 traps, fee stack, MEV, infrastructure tiers.
- **copy-onchain** — copying Solana "smart money" wallets: discovery vendors, persistence evidence, latency, sandwiching, leader gaming, fomoapi and familiars as sources.
- **copy-cex-perps** — copying leaderboards on Hyperliquid, OKX, Bitget, Bybit, Binance, Drift, Lighter/Aster and Polymarket; vault products; academic evidence on copy trading.
- **familiars-fomo** — familiars.family mechanics, board state and product; fomo.family fees, traction, API access and copyability.
- **llm-agents-evidence** — Alpha Arena, FINSABER, Profit Mirage, StockBench, TradingAgents, LATTICE and other LLM-agent evidence; framework survey.
- **infra-execution** — Jupiter Swap V2, transaction landing, Helius, Solana fee constants, Token-2022, simulate-before-sign, Hyperliquid API, ccxt, cost model.
- **risk-regulatory** — sizing, brakes, tail events, supply-chain attacks, key management, prompt/memory injection, US/UK/EU tax and regulation, hosting ToS.

## Executive summary

- Daily trend following on SOL/BTC/ETH is the best-supported price strategy. The offline study's fixed-parameter Donchian ensemble on SOL returned 60.8% CAGR, Sharpe 1.20 and -45.2% max drawdown versus 26.2% / 0.74 / -96.3% for buy-and-hold (2021-04-18 to 2026-09-23, 30 bps per side), and the best peer-reviewed-style study reports Sharpe 1.58 and 30% CAGR over 2015-Mar 2025 ([Zarattini et al.](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5209907), snippet only). But the SOL Donchian Sharpe fell from 1.54 in-sample to 0.17 in the last 24 months and 2025 was negative for almost every rule; realistic net expectation is 0-30%/yr with 25-40% drawdowns. [systematic-price]
- The 4h breakout is marginal and cost-fragile: on SOL at 30 bps it made 16.5% CAGR, PF 1.20, -59% max DD, with the whole edge in 2023 (+194%) and -14% CAGR in the last 12 months; the Ballast agent's 4h breakout on 45 liquid Solana tokens made +25.9% (DD 24.2%, PF 1.40) over 10 Mar-24 Sep 2026 while an equal-weight basket made +68.7% and the 1h variant lost 45.5% to costs ([Ballast README](/home/user/jonatangigex/familiars.family-opus5.5/README.md)). [systematic-price]
- Funding carry is at or below the stablecoin hurdle: Hyperliquid BTC funding paid shorts 9.73% APR always-in from 2024-11-17 to 2026-09-23 but only 4.98% in 2026 YTD, and a 7-day > 8% entry rule netted 4.82% (2.09% at 46 bps round trip) versus 4-9% on Kamino USDC ([eco.com](https://eco.com/support/en/articles/14801186-kamino-lending-solana-s-money-market-explained), snippet only); SOL had no carry at all (funding negative 34.2% of hours). [carry-arb-mm]
- Every delta-neutral book was stress-tested in the last 12 months: on 10 Oct 2025 about $19B was liquidated in 24h, Binance BTC top-of-book depth fell 98% and USDe printed $0.65 on Binance's own book ([Amberdata](https://blog.amberdata.io/how-3.21b-vanished-in-60-seconds-october-2025-crypto-crash-explained-through-7-charts), snippet only); the Nov 2025 Stream/Elixir contagion lost $93M and cascaded through $285M of linked debt ([Pharos](https://pharos.watch/learn/case-studies/stream-elixir-contagion-2025/), snippet only). Leverage above 2-3x and non-USDC collateral are unsuitable for an unattended agent. [carry-arb-mm]
- Memecoin launches have a ~99% loss prior: Solidus Labs classed 98.6-98.7% of ~7M pump.fun tokens as pump-and-dumps ([CoinDesk 2025-05-07](https://www.coindesk.com/business/2025/05/07/98-of-tokens-on-pump-fun-have-been-rug-pulls-or-an-act-of-fraud-new-report-says), snippet only), graduation runs 0.63-2.84% and the median token lives 2 minutes ([unretain/solagents](https://github.com/unretain/solagents)). The only open backtest with realistic exits still loses ~17% per trade after the best filters. In the offline study, 8,084 Telegram calls (2026-06-30 to 09-24) show a median peak of 1.59x, 35.7% reaching 2x, and a hold-to-now median of 0.14x in the second dataset. [onchain-memecoin]
- Block-0 sniping and Solana atomic arbitrage are closed to a VPS bot: deployer-funded snipers were 87% profitable and extracted >15,000 SOL in a month ([Pine Analytics](https://pineanalytics.substack.com/p/exit-liquidity-machines), snippet only), Yellowstone gRPC starts at $499/mo, and Jito runs on ~92-95% of stake with public RPC ~200 ms behind. [onchain-memecoin]
- Copying on-chain wallets has thin and mostly negative measured evidence: a WWW 2026 paper's screened KOL-copy pipeline returned ~3% per memecoin bet ([arXiv 2601.08641](https://arxiv.org/abs/2601.08641), snippet only); an open Polymarket copy experiment ended +$171 on $10,000 after 60 wallets were screened to 2 ([BallesJr](https://github.com/BallesJr/polymarket-copy-trader)); wallet skill does persist on Hyperliquid (rank correlation 0.52 across 10-day windows) but at 1-second horizons ([arXiv 2608.04373](https://arxiv.org/abs/2608.04373), snippet only). [copy-onchain]
- Copying CEX and perp-DEX leaderboards selects for leverage and luck: James Wynn's $1.25B 40x BTC long was liquidated for >$37M in May 2025 and he was liquidated nine times ([DL News](https://www.dlnews.com/articles/defi/hyperliquid-trader-james-wynn-liquidated-nine-times/), snippet only); 19 of 28 eToro CopyPortfolio alphas were not different from zero (snippet only). Only OKX and Bitget expose follower APIs (verified in ccxt) and none of the CEX copy products serve US persons (inference). Hyperliquid user vaults (10% profit share, ~3,300 active) and HLP (15-30% APR, 5.68% max DD as of 3 Sep 2026, snippet only) are the low-engineering copy paths. [copy-cex-perps]
- familiars.family ranks agents by absolute USD P&L (equity minus net deposits); on 2026-09-24 only 37 of ~1,350 agents were net positive and the largest P&Ls came from agents' own tokens (forbidden by the skill) or early bets on the day's narrative token ($familiars: +1729% in 1h, mcap $868k) ([Ballast README](/home/user/jonatangigex/familiars.family-opus5.5/README.md)). Its public API exposes every agent's wallet, trades, positions and equity history without a key, which makes it a free copy feed; no platform fee in the trade path is visible in any source; skill.md could not be read from the sandbox. [familiars-fomo]
- fomo.family advertises a 0.5% spot fee but an on-chain study measured a 0.379-0.387% blended rate and a flat minimum that makes $0-5 trades pay a median 81.9%; on its Hyperliquid perps (since 11 Jun 2026) only 29.1% of 18,979 wallets were net positive and fees were 1.6x aggregate trading profit ([synsur/fomo-flow](https://github.com/synsur/fomo-flow)). It has no official API (Cloudflare HTTP 430 for non-browser clients), so copying its traders means resolving their Solana wallets and watching the chain. [familiars-fomo]
- LLMs placed in the trade-decision loop lose: in Alpha Arena Season 1 four of six frontier models finished down 30.8-62.7% on $10k each and the winners traded least ([nof1 post, mirrored](https://github.com/itripleg/llm-trading-bot/blob/main/blogpost.txt)); Season 1.5 was profitable in 6 of 32 sessions (snippet only); FINSABER finds FinMem/FinAgent Sharpe ~0.24 vs buy-and-hold 0.70 with no significant alpha (p>0.34) ([digest](https://github.com/elimarks5807-coder/foundry-strategy-engine/blob/main/Digests/FINSABER.md)). LLMs measurably help only off the hot path: text classification, scam/impersonation veto, offline parameter review and the explanation posts familiars requires. [llm-agents-evidence]
- Execution infrastructure costs $6-$90/month: Jupiter Ultra is "superseded by Swap V2" (same request/response shape), charges 10 bps on most tokens and 50 bps on tokens under 24h old, and rate-limits keyless traffic to 0.5 RPS (Free 1 RPS, Developer $25/mo 10 RPS) ([jup-ag/docs](https://raw.githubusercontent.com/jup-ag/docs/main/portal/plans.mdx)); Helius' Agent plan is $1/1M credits ([helius core-ai](https://raw.githubusercontent.com/helius-labs/core-ai/main/helius-mcp/system-prompts/helius/full.md)); Hyperliquid charges 4.5/1.5 bps taker/maker and its API wallets cannot withdraw; ccxt silently approves a 0.01% builder fee on Hyperliquid unless disabled ([ccxt source](https://raw.githubusercontent.com/ccxt/ccxt/master/ts/src/hyperliquid.ts)). [infra-execution]
- The dominant loss vector for bots is key theft through the supply chain: @solana/web3.js 1.95.6/1.95.7 (3 Dec 2024) shipped a key-stealing backdoor ([GHSA-jcxm-7wvp-g6p5](https://github.com/solana-labs/solana-web3.js/security/advisories/GHSA-jcxm-7wvp-g6p5)), chalk/debug were compromised on 8 Sep 2025 and the Shai-Hulud worm hit @ctrl/tinycolor on 15 Sep 2025 ([npm registry](https://registry.npmjs.org/@ctrl/tinycolor)); memory injection beats prompt-injection defences on Web3 agents ([arXiv 2503.16248](https://arxiv.org/abs/2503.16248), abstract via mirror). Every swap is a US taxable disposition, 1099-DA reporting started for 2025 sales, the wash-sale rule still does not apply to spot crypto as of Sep 2026, and Hyperliquid's ToS (15 Jun 2026) bars US persons. [risk-regulatory]
- SOL is a leveraged BTC position, not a diversifier: daily return correlation SOL/BTC 0.785 (2024-01-01 to 2026-09-23), rising to 0.88 in 2026 YTD, beta 1.31, 79.5% annualised vol; SOL fell 76.3% between 2025-01-18 and 2026-06-06 and its worst 30-day window in 2024-26 was -46.1% (ending 2026-02-12). Any unhedged SOL strategy has to be sized for a 40-50% monthly hit. [offline study]

## What the data says (offline study)

All studies are offline and deterministic on Binance spot USDT OHLCV; signals are computed on closed bars and filled at the next bar open unless stated. Costs are charged per side on turnover: 30 bps for Solana DEX spot (primary for SOL), 5 bps for perps/CEX (primary for BTC and ETH), 10 bps for reference. No leverage, long-only except study 3 (delta-neutral carry). Annualisation uses 365 days, 2190 4h bars, 8760 hourly / 1095 8-hourly funding intervals per year; Sharpe uses a 0% risk-free rate. Every parameter was fixed in advance; nothing was optimised ([RESULTS.md](/tmp/claude-0/-home-user-agent-trading/7c78e63b-542a-52d1-9c81-ffdeb4d14149/scratchpad/analysis/RESULTS.md)).

Data provenance ([MANIFEST.md](/tmp/claude-0/-home-user-agent-trading/7c78e63b-542a-52d1-9c81-ffdeb4d14149/scratchpad/data/MANIFEST.md)): daily and hourly Binance spot klines for BTCUSDT/ETHUSDT (2017-08-17 to 2026-09-23) and SOLUSDT (2020-08-11 to 2026-09-23) from [finom/static-klines](https://github.com/finom/static-klines) (commit bd5428a, refreshed 2026-09-24) cross-checked bar-for-bar against [yanniedog/binance-historical-OHLCV-data](https://github.com/yanniedog/binance-historical-OHLCV-data) (35,193 overlapping hourly bars per symbol, median close difference 0, max 0.018%) and against Yahoo BTC-USD from [Swissbit92/btc_price_tracker](https://github.com/Swissbit92/btc_price_tracker) (median difference 0.10%); funding from [supervik/historical-funding-rates-fetcher](https://github.com/supervik/historical-funding-rates-fetcher) (2020-2023), [ZuShen168/funding_rate_data](https://github.com/ZuShen168/funding_rate_data) (2025-08 to 2026-09), [Caiooooo/anay_hyper_fund](https://github.com/Caiooooo/anay_hyper_fund) (2024-11 to 2025-11) and [pattybepatient/crypto-funding-arb](https://github.com/pattybepatient/crypto-funding-arb), which agree exactly on every overlap; pump.fun call datasets from [Smurfetc/solana-memecoin-calls-dataset](https://github.com/Smurfetc/solana-memecoin-calls-dataset) (8,084 calls, CC0, SHA-256 matches its attestation) and [nikolan17/pumpfun-call-analyzer](https://github.com/nikolan17/pumpfun-call-analyzer) (188 calls). Binance/Bybit funding has holes for 2024-01-01 to 2024-11-17 (BTC) / to 2025-08 (ETH, SOL) and 2026-08-21 to 2026-09-13; no per-token memecoin OHLCV or full launch feed exists on GitHub.

### Study 1: Daily trend following on SOL / BTC / ETH

Rules: SMA200 and SMA100 regime switches with a 2% hysteresis band; a Donchian ensemble (lookbacks 10-250 days, trailing-stop exits, equal-weight sleeves); the same ensemble with a 25% annual volatility target. Evaluation starts after a 250-bar warm-up (SOL 2021-04-18, BTC/ETH 2018-04-24). "IS" = start to 2024-09-23; "24m" = 2024-09-24 to 2026-09-23.

SOLUSDT (primary cost 30 bps; 2021-04-18 to 2026-09-23):

| rule | CAGR | vol | Sharpe | MaxDD | time in mkt | avg exp | round trips | turnover/yr | 2022 | 2025 | 2026 YTD | IS Sharpe | IS CAGR | 24m Sharpe | 24m CAGR | 24m MaxDD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| buy_and_hold | 26.2% | 102.6% | 0.74 | -96.3% | 100.0% | 1.00 | 1 | 0.2 | -94.1% | -34.2% | -7.7% | 0.96 | 54.6% | 0.23 | -10.9% | -76.3% |
| sma200_hyst2pct | 57.9% | 75.3% | 0.98 | -69.1% | 48.4% | 0.48 | 16 | 5.7 | -20.7% | -17.4% | 34.3% | 1.20 | 94.9% | 0.44 | 9.9% | -40.8% |
| sma100_hyst2pct | 9.0% | 73.3% | 0.48 | -87.0% | 48.2% | 0.48 | 36 | 13.1 | -42.8% | -7.0% | 14.1% | 0.58 | 14.8% | 0.24 | -0.3% | -50.2% |
| donchian_ensemble | 60.8% | 50.3% | 1.20 | -45.2% | 53.9% | 0.27 | 161 | 7.8 | -23.1% | -10.7% | 7.2% | 1.54 | 110.5% | 0.17 | 1.3% | -35.6% |
| donchian_ensemble_voltarget25 | 14.8% | 12.1% | 1.20 | -14.2% | 53.9% | 0.07 | 161 | 2.4 | -6.0% | -3.6% | 4.5% | 1.60 | 22.8% | 0.27 | 2.1% | -14.2% |

SOL cost sensitivity (CAGR / Sharpe):

| rule | 5bps | 10bps | 30bps |
|---|---|---|---|
| buy_and_hold | 26.3% / 0.74 | 26.3% / 0.74 | 26.2% / 0.74 |
| sma200_hyst2pct | 60.2% / 1.00 | 59.7% / 1.00 | 57.9% / 0.98 |
| sma100_hyst2pct | 12.6% / 0.53 | 11.9% / 0.52 | 9.0% / 0.48 |
| donchian_ensemble | 64.0% / 1.23 | 63.3% / 1.23 | 60.8% / 1.20 |
| donchian_ensemble_voltarget25 | 15.5% / 1.25 | 15.3% / 1.24 | 14.8% / 1.20 |

SOL calendar-year returns at 30 bps:

| rule | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|
| buy_and_hold | 424.2% | -94.1% | 920.3% | 86.1% | -34.2% | -7.7% |
| sma200_hyst2pct | 424.2% | -20.7% | 182.8% | -8.2% | -17.4% | 34.3% |
| sma100_hyst2pct | 102.5% | -42.8% | 64.6% | -21.2% | -7.0% | 14.1% |
| donchian_ensemble | 220.3% | -23.1% | 323.6% | 32.3% | -10.7% | 7.2% |
| donchian_ensemble_voltarget25 | 27.5% | -6.0% | 57.6% | 11.2% | -3.6% | 4.5% |

BTCUSDT (primary cost 5 bps; 2018-04-24 to 2026-09-23):

| rule | CAGR | vol | Sharpe | MaxDD | time in mkt | avg exp | round trips | turnover/yr | 2022 | 2025 | 2026 YTD | IS Sharpe | IS CAGR | 24m Sharpe | 24m CAGR | 24m MaxDD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| buy_and_hold | 29.4% | 61.8% | 0.73 | -76.6% | 100.0% | 1.00 | 1 | 0.1 | -64.2% | -6.3% | -3.7% | 0.78 | 34.1% | 0.54 | 15.4% | -53.0% |
| sma200_hyst2pct | 39.5% | 42.9% | 0.99 | -53.4% | 52.9% | 0.53 | 17 | 3.7 | 0.0% | -6.6% | 15.5% | 1.06 | 46.5% | 0.72 | 19.3% | -27.7% |
| sma100_hyst2pct | 52.9% | 43.3% | 1.20 | -42.8% | 54.3% | 0.54 | 23 | 5.1 | -21.4% | 6.1% | 12.6% | 1.23 | 59.7% | 1.13 | 32.8% | -27.2% |
| donchian_ensemble | 29.0% | 30.4% | 0.99 | -39.8% | 67.9% | 0.36 | 266 | 8.5 | -16.9% | -4.4% | -2.1% | 1.09 | 35.8% | 0.55 | 9.5% | -23.5% |
| donchian_ensemble_voltarget25 | 14.7% | 13.5% | 1.08 | -16.2% | 67.9% | 0.17 | 266 | 4.4 | -6.8% | -3.9% | -0.7% | 1.25 | 17.9% | 0.46 | 4.9% | -16.2% |

BTC calendar-year returns at 5 bps:

| rule | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|---|
| buy_and_hold | -61.5% | 94.4% | 302.0% | 59.8% | -64.2% | 155.6% | 121.3% | -6.3% | -3.7% |
| sma200_hyst2pct | 0.0% | 57.5% | 169.4% | 8.0% | 0.0% | 96.4% | 70.0% | -6.6% | 15.5% |
| sma100_hyst2pct | -34.8% | 140.8% | 224.5% | 86.0% | -21.4% | 90.0% | 111.0% | 6.1% | 12.6% |
| donchian_ensemble | -11.9% | 69.2% | 165.7% | 29.4% | -16.9% | 39.3% | 53.8% | -4.4% | -2.1% |
| donchian_ensemble_voltarget25 | -4.3% | 32.9% | 60.1% | 11.2% | -6.8% | 22.4% | 28.6% | -3.9% | -0.7% |

ETHUSDT (primary cost 5 bps; 2018-04-24 to 2026-09-23):

| rule | CAGR | vol | Sharpe | MaxDD | time in mkt | avg exp | round trips | turnover/yr | 2022 | 2025 | 2026 YTD | IS Sharpe | IS CAGR | 24m Sharpe | 24m CAGR | 24m MaxDD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| buy_and_hold | 17.3% | 82.8% | 0.61 | -89.8% | 100.0% | 1.00 | 1 | 0.1 | -67.5% | -11.0% | -9.7% | 0.68 | 23.0% | 0.35 | 0.7% | -67.6% |
| sma200_hyst2pct | 36.3% | 59.5% | 0.83 | -74.2% | 50.7% | 0.51 | 18 | 3.9 | -13.0% | -10.2% | 19.1% | 0.93 | 47.1% | 0.36 | 6.8% | -38.7% |
| sma100_hyst2pct | 46.0% | 58.9% | 0.95 | -66.2% | 53.0% | 0.53 | 25 | 5.6 | -39.9% | 58.4% | 19.6% | 0.91 | 44.0% | 1.18 | 52.4% | -35.2% |
| donchian_ensemble | 35.6% | 39.0% | 0.97 | -35.7% | 61.9% | 0.31 | 248 | 7.8 | -5.2% | 19.3% | -2.2% | 1.09 | 44.8% | 0.47 | 9.7% | -32.9% |
| donchian_ensemble_voltarget25 | 12.5% | 13.0% | 0.98 | -13.5% | 61.9% | 0.11 | 248 | 3.0 | -1.6% | 7.7% | -0.1% | 1.10 | 15.1% | 0.49 | 4.8% | -13.5% |

ETH calendar-year returns at 5 bps:

| rule | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|---|
| buy_and_hold | -81.3% | -1.7% | 470.2% | 399.2% | -67.5% | 90.8% | 46.3% | -11.0% | -9.7% |
| sma200_hyst2pct | -14.8% | 1.3% | 128.7% | 303.8% | -13.0% | 42.9% | 28.3% | -10.2% | 19.1% |
| sma100_hyst2pct | -17.8% | 68.7% | 119.7% | 225.5% | -39.9% | 61.8% | 32.5% | 58.4% | 19.6% |
| donchian_ensemble | -11.1% | 25.6% | 133.0% | 224.0% | -5.2% | 12.9% | 23.3% | 19.3% | -2.2% |
| donchian_ensemble_voltarget25 | -2.9% | 9.7% | 39.3% | 42.7% | -1.6% | 6.5% | 13.4% | 7.7% | -0.1% |

Cost sensitivity on BTC/ETH is small: at 30 bps instead of 5 bps, BTC SMA100 falls from 52.9% to 50.9% CAGR and ETH Donchian from 35.6% to 33.0% ([RESULTS.md](/tmp/claude-0/-home-user-agent-trading/7c78e63b-542a-52d1-9c81-ffdeb4d14149/scratchpad/analysis/RESULTS.md)).

### Study 2: 4h breakout on SOL and ETH

Rules: Binance 1h bars resampled to UTC-aligned 4h; entry on close above the prior 20-bar high with volume >= 1.5x the 20-bar median, EMA20 > EMA50 and close > EMA50 ("own" gate; "own+btc" adds BTC 4h close > BTC EMA50); initial stop 2.5 x ATR(14) clamped to 4-20%; break-even at +1R, 4-ATR trail from +2R, exit on close < EMA50 or after 18 bars without +0.3R; sizing either 1% of equity at risk or 100% of equity notional. Window 2022-01-01 to 2026-09-23; L12M = 2025-09-24 to 2026-09-23.

SOLUSDT (primary cost 30 bps):

| gate | sizing | CAGR | vol | Sharpe | MaxDD | time in mkt | trades | WR | PF | avg R | 2022 | 2025 | 2026 YTD | L12M CAGR | L12M Sharpe | L12M MaxDD | L12M trades | L12M PF |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| buy_and_hold | - | -8.3% | 93.5% | 0.37 | -95.1% | 100% | - | - | - | - | -94.2% | -34.2% | -7.7% | -46.1% | -0.59 | -73.9% | - | - |
| own | risk1pct | 5.3% | 9.4% | 0.59 | -11.9% | 23.6% | 90 | 26.7% | 1.52 | 0.41 | -0.1% | 1.3% | 0.1% | -1.4% | -0.19 | -6.7% | 18 | 0.75 |
| own | fixed100 | 16.5% | 47.3% | 0.56 | -59.2% | 23.6% | 90 | 26.7% | 1.20 | 0.41 | -8.6% | -4.3% | -6.9% | -14.2% | -0.41 | -31.0% | 18 | 0.54 |
| own+btc | risk1pct | 5.8% | 9.5% | 0.65 | -11.2% | 22.8% | 87 | 26.4% | 1.62 | 0.45 | -0.1% | 1.3% | -0.7% | -2.1% | -0.32 | -6.7% | 18 | 0.66 |
| own+btc | fixed100 | 19.4% | 46.8% | 0.61 | -56.1% | 22.8% | 87 | 26.4% | 1.25 | 0.45 | -8.6% | -4.3% | -11.2% | -18.2% | -0.62 | -31.5% | 18 | 0.45 |

SOL cost sensitivity (CAGR / Sharpe / PF):

| gate | sizing | 5bps | 10bps | 30bps |
|---|---|---|---|---|
| own | risk1pct | 7.1% / 0.77 / 1.81 | 6.7% / 0.74 / 1.75 | 5.3% / 0.59 / 1.52 |
| own | fixed100 | 28.2% / 0.76 / 1.40 | 25.8% / 0.72 / 1.35 | 16.5% / 0.56 / 1.20 |
| own+btc | risk1pct | 7.6% / 0.82 / 1.94 | 7.3% / 0.79 / 1.86 | 5.8% / 0.65 / 1.62 |
| own+btc | fixed100 | 31.0% / 0.81 / 1.46 | 28.6% / 0.77 / 1.41 | 19.4% / 0.61 / 1.25 |

SOL calendar-year returns at 30 bps: own/fixed100 2022 -8.6%, 2023 +194.3%, 2024 -14.1%, 2025 -4.3%, 2026 -6.9%; exit reasons (own, fixed100): EMA50 43, stop 42, time 5; average 27.1 bars held; average return on notional per trade 1.73%.

ETHUSDT (primary cost 5 bps):

| gate | sizing | CAGR | vol | Sharpe | MaxDD | time in mkt | trades | WR | PF | avg R | 2022 | 2025 | 2026 YTD | L12M CAGR | L12M Sharpe | L12M MaxDD | L12M trades | L12M PF |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| buy_and_hold | - | -6.7% | 66.2% | 0.23 | -76.2% | 100% | - | - | - | - | -67.9% | -11.0% | -9.7% | -35.5% | -0.43 | -67.3% | - | - |
| own | risk1pct | 6.1% | 7.4% | 0.83 | -12.9% | 26.9% | 100 | 27.0% | 1.63 | 0.32 | 2.6% | 11.7% | -3.4% | -5.1% | -0.77 | -11.9% | 27 | 0.59 |
| own | fixed100 | 19.3% | 31.3% | 0.72 | -47.4% | 26.9% | 100 | 27.0% | 1.35 | 0.32 | 11.9% | 42.5% | -18.7% | -25.4% | -0.95 | -43.3% | 27 | 0.47 |
| own+btc | risk1pct | 6.5% | 7.4% | 0.89 | -12.9% | 26.7% | 97 | 27.8% | 1.71 | 0.35 | 3.4% | 12.9% | -2.8% | -4.4% | -0.66 | -11.9% | 26 | 0.63 |
| own+btc | fixed100 | 21.8% | 31.1% | 0.79 | -47.4% | 26.7% | 97 | 27.8% | 1.42 | 0.35 | 16.5% | 49.2% | -16.1% | -23.0% | -0.84 | -43.3% | 26 | 0.50 |

ETH at 30 bps (own, fixed100) drops to 7.4% CAGR / Sharpe 0.38 / PF 1.12; calendar years at 5 bps: 2022 +11.9%, 2023 +2.1%, 2024 +74.6%, 2025 +42.5%, 2026 -18.7%.

### Study 3: Funding carry (short perp / long spot)

Definitions: "always-in" APR to shorts = sum of rates annualised by interval count; rolling 30-day APR percentiles; rule = trailing 7-day mean annualised funding > 8% -> hold the carry during the next interval; 23 bps per round trip (46 bps sensitivity); "blended" = rule APR + 5% on idle time (stablecoin parking). Price basis, margin cost and liquidation risk are not modelled.

| venue | sym | start | end | yrs | always-in APR | % neg | 30d p5 | 30d p50 | 30d p95 | 30d>8% | rule time-in | rule RTs | rule gross APR | rule net (23bps) | rule net (46bps) | blended (5% idle) | APR while in |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| hyperliquid | BTC | 2024-11-17 | 2026-09-23 | 1.85 | 9.73% | 13.8% | 0.5% | 8.6% | 19.2% | 52.5% | 55.5% | 22 | 7.55% | 4.82% | 2.09% | 7.05% | 13.6% |
| hyperliquid | ETH | 2025-08-19 | 2026-09-23 | 1.10 | 6.55% | 16.0% | 0.7% | 7.0% | 9.7% | 33.4% | 39.9% | 17 | 3.64% | 0.08% | -3.48% | 3.09% | 9.1% |
| hyperliquid | SOL | 2025-08-19 | 2026-09-23 | 1.10 | 0.89% | 34.2% | -13.9% | 1.1% | 10.5% | 9.9% | 21.7% | 13 | 1.93% | -0.80% | -3.52% | 3.12% | 8.9% |
| binance | BTC | 2020-01-01 | 2026-09-20 | 5.78 | 11.84% | 14.8% | -1.2% | 6.3% | 53.7% | 36.0% | 41.3% | 37 | 9.91% | 8.44% | 6.97% | 11.38% | 24.0% |
| binance | ETH | 2020-01-01 | 2026-09-20 | 5.03 | 15.22% | 15.6% | -1.7% | 6.7% | 72.0% | 42.6% | 45.2% | 28 | 13.82% | 12.54% | 11.26% | 15.28% | 30.6% |
| binance | SOL | 2025-08-19 | 2026-09-20 | 1.02 | -1.41% | 45.5% | -12.6% | -0.2% | 4.7% | 0.0% | 1.2% | 1 | 0.09% | -0.14% | -0.36% | 4.80% | 6.9% |

Binance BTC by calendar year (coverage = share of the year's 8h intervals present): 2020 17.19% (14.3% negative), 2021 30.61% (7.3%), 2022 4.16% (22.1%), 2023 7.87% (10.1%), 2024 15.08% (0.7%; only 12.2% coverage), 2025 5.13% (12.9%), 2026 2.45% (29.0%; 65.6% coverage).

Hyperliquid by calendar year:

| sym | year | APR to shorts | % neg | coverage |
|---|---|---|---|---|
| BTC | 2024 | 30.47% | 0.5% | 12.3% |
| BTC | 2025 | 10.63% | 8.9% | 100.0% |
| BTC | 2026 | 4.98% | 22.7% | 72.9% |
| ETH | 2025 | 8.35% | 9.4% | 36.9% |
| ETH | 2026 | 5.63% | 19.4% | 72.9% |
| SOL | 2025 | 3.24% | 23.7% | 36.9% |
| SOL | 2026 | -0.29% | 39.5% | 72.9% |

### Study 4: Pump.fun call base rates and a naive exit policy

Datasets: smugcalls (8,084 Telegram calls, 2026-06-30 to 2026-09-24) and nikolan17/devcabal (188 calls, 2026-04-30 to 2026-09-24). Both are calls posted in a channel, not all launches, so selection bias is heavy. Peak multiple = max price after the call / price at call; it is a single number, not a path. Policy (optimistic upper bound): sell half at 2x and the rest at 0.65 x peak; tokens that never reach 2x are sold at -30%.

smugcalls: median peak 1.59x, 35.7% reach 2x, 5.1% reach 10x, 6.4% never trade above the call price.

| peak bucket | <1.0 | 1.0-1.5 | 1.5-2 | 2-3 | 3-5 | 5-10 | >10 |
|---|---|---|---|---|---|---|---|
| share | 0.2% | 45.7% | 18.4% | 13.6% | 10.4% | 6.6% | 5.1% |
| count | 16 | 3695 | 1491 | 1102 | 840 | 530 | 410 |

By market cap at call:

| mcap bucket | n | median peak | peak<=1 | >=2x | >=5x | >=10x |
|---|---|---|---|---|---|---|
| <10k | 2280 | 1.47 | 9.0% | 30.5% | 9.6% | 4.0% |
| 10-30k | 5593 | 1.64 | 5.1% | 37.7% | 12.4% | 5.5% |
| 30-100k | 209 | 1.64 | 14.8% | 36.4% | 12.9% | 4.3% |
| >100k | 2 | 1.23 | 0.0% | 0.0% | 0.0% | 0.0% |

By month (smugcalls): 2026-07 n=2672, median peak 1.64, 36.7% >= 2x; 2026-08 n=3378, 1.57, 35.3%; 2026-09 n=2020, 1.53, 34.4%.

Naive exit policy (optimistic upper bound):

| round-trip cost | expectancy/trade | median trade | win rate | P(month>0) boot 20 trades | p5 month/trade | p95 month/trade | calendar months >0 | seq. 20-trade blocks >0 |
|---|---|---|---|---|---|---|---|---|
| 0.6pct_rt (30bps/side) | 101.8% | -30.4% | 35.7% | 94.8% | -0.2% | 252.2% | 100.0% | 94.8% (404 blocks) |
| 2.0pct_rt (1%/side) | 99.0% | -31.4% | 35.7% | 94.1% | -1.7% | 237.2% | 100.0% | 93.8% (404 blocks) |

Tail dependence (0.6% cost): mean peak 4.63x, p99 peak 36.08x, max peak 6195x; the top 1% of trades supply 59.7% of policy P&L (top 5%: 82.1%); expectancy excluding the top 1% = 41.4%, excluding the top 5% = 19.2%.

Capped-peak sensitivity (smugcalls, 0.6% cost):

| cap | expectancy/trade | P(month>0) 20 trades | p5 month/trade | p50 month/trade |
|---|---|---|---|---|
| cap_3x | 12.2% | 81.7% | -7.7% | 11.9% |
| cap_5x | 22.4% | 91.4% | -4.2% | 22.1% |
| cap_10x | 34.4% | 94.4% | -0.8% | 33.3% |
| cap_20x | 44.9% | 94.8% | -0.2% | 41.7% |

nikolan17/devcabal (n=188): median peak 1.71x, 42.6% >= 2x, 9.0% >= 10x, 0.5% never above call; policy expectancy 578.9% per trade at 0.6% cost, driven by a 700x outlier (top 5% of trades supply 91.5% of P&L; expectancy excluding the top 5% = 51.8%); capped at 3x the expectancy is 20.8%. Reality check from its current-mcap column: hold-to-now mean multiple 0.98x, median 0.14x, 70.7% of calls now below 0.5x, median minutes to peak 331.

### Study 5: Cross-asset sanity

Daily close-to-close return correlations, 2024-01-01 to 2026-09-23 (997 days):

|  | SOLUSDT | BTCUSDT | ETHUSDT |
|---|---|---|---|
| SOLUSDT | 1.000 | 0.785 | 0.772 |
| BTCUSDT | 0.785 | 1.000 | 0.819 |
| ETHUSDT | 0.772 | 0.819 | 1.000 |

| year | SOL/BTC | SOL/ETH | BTC/ETH |
|---|---|---|---|
| 2024 | 0.747 | 0.695 | 0.795 |
| 2025 | 0.800 | 0.791 | 0.816 |
| 2026 | 0.882 | 0.881 | 0.911 |

SOL rolling-90d correlation with BTC: min 0.61, median 0.80, max 0.93. SOL beta to BTC 1.31, to ETH 0.90. Annualised vol 2024-26: SOL 79.5%, BTC 47.4%, ETH 68.3%.

| SOL drawdown stat | value | dates |
|---|---|---|
| max DD full history (2020-08+) | -96.3% | 2021-11-06 -> 2022-12-29 (258.4 -> 9.6) |
| max DD 2024-2026 | -76.3% | 2025-01-18 -> 2026-06-06 (262.0 -> 62.2) |
| worst calendar month full | -56.5% | 2022-11 |
| worst calendar month 2024-26 | -37.4% | 2024-04 |
| worst 30-day window full | -62.5% | ending 2022-12-05 |
| worst 30-day window 2024-26 | -46.1% | ending 2026-02-12 |

### Interpretation

- Daily trend rules cut max drawdown by roughly half or more versus buy-and-hold on all three assets and lifted Sharpe from ~0.6-0.7 to ~1.0-1.2, almost entirely by being in cash for most of 2022. Lookback sensitivity is large: SMA100 on SOL is poor (9.0% CAGR, 36 round trips) while SMA200 on SOL is excellent (57.9%), and the ranking flips on BTC/ETH, so no single number is robust. The walk-forward split is sobering: in the last 24 months the SOL Donchian Sharpe fell from 1.54 to 0.17 and SMA200 from 1.20 to 0.44; only SMA100 on BTC/ETH held up (1.13 / 1.18). Vol-targeting to 25% delivers 12-14% realised vol and -14% to -16% max DD at the same Sharpe but leaves 83-93% of capital idle on average. Costs barely matter at daily frequency (1-3 CAGR points between 5 and 30 bps).
- The 4h breakout is marginal: at DEX costs the SOL result depends on 2023, and the last 12 months show PF 0.54 (SOL) and 0.47 (ETH). Costs are the dominant sensitivity on SOL (90 round trips x 60 bps = 54% of notional over 4.7 years). The 1%-risk sizing caps notional at <= 25% of equity, so its 5-6% CAGR / -12% DD is mostly de-leveraging. The BTC gate removes only 3 trades and nudges PF up.
- Funding carry on Hyperliquid BTC is decaying (30% annualised in Nov-Dec 2024, 10.6% in 2025, 5.0% in 2026 YTD); after 22 round trips at 23 bps the rule nets 4.8%, below a flat 5% stablecoin yield, and the blended excess over holding stables is ~2 points before basis and execution risk. ETH and SOL are worse; Binance BTC's 8.4% net is concentrated in 2020-21.
- The pump.fun call policy's +102% expectancy is a fat-tail artefact: the median trade is -30%, the mean peak is 4.6x against a median of 1.6x, and realistic caps on realisable peak (3x-5x) cut expectancy to 12-22% while the devcabal reality check (median 0.14x hold-to-now) shows that anyone who did not sell near the peak lost most of the money.
- SOL is a high-beta BTC proxy; trend or breakout strategies run on all three assets will be highly correlated with each other.

### Caveats (from the study, kept in spirit)

- One realised path of 5.4 years (SOL) / 8.4 years (BTC, ETH) dominated by two or three regime changes; the effective sample is a handful of trends. Trend rules under-perform buy-and-hold badly in strong up-years (SOL 2023: +183% / +324% vs +920%). Prices are Binance spot; fills at the next open with a flat per-side cost ignore slippage and the spot/DEX basis for SOL. Parameters were fixed a priori but are community-standard values, a mild form of selection.
- Study 2 has 90-100 trades in total, so confidence intervals on PF and win rate are very wide; a single year carries each result; stops are assumed filled exactly at the stop or at the open when gapped through; R-milestones use closes (conservative).
- Study 3 is funding only: spot/perp basis moves, margin cost (USDC collateral on Hyperliquid earns nothing while posted), exchange/counterparty risk, short-leg liquidation during squeezes and the four fills of a real two-leg entry/exit are not modelled; the Hyperliquid BTC sample starts at a funding peak; Binance has no data for 2024-01-01 to 2024-11-17 and 2026-08-21 to 2026-09-13.
- Study 4 is an optimistic upper bound: it assumes a token reaching 2x does so before any -30% stop, that the second half fills exactly 35% below the eventual peak rather than on a gap-down or rug, that sub-2x tokens lose exactly 30% when many go to ~0 in one candle, entry at the call price, and 30 bps per side when pump.fun/DEX/priority fees plus slippage are usually well above 1% per side. Both datasets are channel calls with selection and survivorship bias over 3-5 months in one regime; a realistic follower expectancy is plausibly near zero or negative.
- Study 5 correlations are regime-dependent (they rise in sell-offs), so the average understates crash-time co-movement.

## Strategy families

Each subsection summarises what the streams found, what a $500-$20k retail agent can realistically expect net of costs, what it needs, how it fails, and the candidate strategies proposed by the streams. "Familiars-fit" means executable as spot swaps from one Solana wallet and postable on familiars.family.

### Trend and momentum

What the evidence says:

- Momentum is a documented crypto factor: market, size and momentum explain the cross-section of returns ([Liu, Tsyvinski, Wu, JF 2022](https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.13119)), but momentum exists only in the largest-cap group and reverses in smaller ones ([Cong, Karolyi, Tang, Zhao, Management Science](https://pubsonline.informs.org/doi/10.1287/mnsc.2024.05875)). Time-series momentum evidence is strong while cross-sectional momentum is weak, and many momentum portfolios earn insignificant profits once costs and intraday fluctuation are modelled ([Han, Kang, Ryu, SSRN 4675565](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4675565), snippet only).
- Momentum crashes are extreme: one weekly top-30 long-short sample 2016-2023 shows a -255% crash at end-2020 (leveraged factor, not a long-only book) ([Grobys, FMPM 2025](https://link.springer.com/article/10.1007/s11408-025-00474-9), snippet only); momentum realised variances follow power laws with undefined mean/variance ([Grobys & Shahzad, IJFE Apr 2026](https://onlinelibrary.wiley.com/doi/abs/10.1002/ijfe.70036), snippet only). Volatility-managed momentum raised weekly returns from 3.18% to 3.47% and Sharpe from 1.12 to 1.42 ([Yang, FRL 2025](https://www.sciencedirect.com/science/article/abs/pii/S1544612325011377), snippet only).
- A machine-learning aggregate of 28 technical signals (CTREND) earns 3.87%/week long-short, survives costs and persists in big, liquid coins ([Fieberg et al., JFQA Nov 2025](https://www.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/trend-factor-for-the-cross-section-of-cryptocurrency-returns/4C1509ACBA33D5DCAF0AC24379148178), snippet only).
- The best-documented practitioner rule set: an ensemble of Donchian breakouts (lookbacks 5-360 days), trailing stop = max(prior stop, channel midpoint), 25% vol target with leverage capped at 1, daily rebalancing on the top-20 liquid coins, returned CAGR 30%, Sharpe 1.58, Sortino 2.03 and alpha +14% vs BTC over Jan 2015-Mar 2025 net of 0.10-0.50% costs ([Zarattini, Pagani, Barbon, Apr 2025](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5209907), numbers via a [GitHub issue quoting the paper](https://github.com/zebadee2kk/DeFi-TraderStack-Agent/issues/137); the SSRN abstract says 10.8% alpha, snippet only).
- Cross-sectional rotation among large caps is weak in 2021-2026: monthly top-3 of 10 by 60-day return at 10 bps round trip lost -7.7%/yr (Sharpe -0.16, max DD -89.8%); a BTC > 200-day SMA filter lifted it to +10.8%/yr (Sharpe 0.14, DD -53.8%, turnover 14.9x/yr) while equal-weight hold made +35.6%/yr ([IsaacDodds/crypto-momentum-backtest](https://github.com/IsaacDodds/crypto-momentum-backtest)). The filter is drawdown control, not alpha.
- Regime filters reduce drawdown but do not add alpha: the 200-day MA ranked 10th of 17 BTC indicators and some filters "charge 4% a year" for protection ([setup4alpha](https://setup4alpha.substack.com/p/i-tested-20-trend-based-regime-filters), snippet only). Vol control on BTC delivered 30.81% annualised, Sharpe 1.123, max DD 51.69% -> 39.39% ([arXiv 2608.10375](https://arxiv.org/pdf/2608.10375), snippet only).
- Direct Solana evidence: Ballast's 4h breakout with a SOL EMA50 regime gate on 45 established, liquid Solana tokens (GeckoTerminal hourly candles, 10 Mar-24 Sep 2026, 0.25%/side + 0.3% stop slippage) made +25.9%, max DD 24.2%, PF 1.40, 119 trades (IS +18.6%, OOS +7.2%), ~20% time in market, 29/30 one-parameter perturbations positive; the 1h version lost 45.5% (623 trades); 7-day momentum rotation top-3 made +29.2% with DD 62.6%; holding SOL +35.8% (DD 38%); equal-weight basket +68.7% (DD 39.7%); Monte Carlo 30-day P(loss) 45%, 90-day median +8.3% with P(loss) 31%; in the euphoric window 19 Aug-24 Sep 2026 the basket rose +136% while the strategy made +0.5% ([Ballast README](/home/user/jonatangigex/familiars.family-opus5.5/README.md), [backtest.ts](/home/user/jonatangigex/familiars.family-opus5.5/src/backtest.ts)). Author-flagged survivorship bias and no bear market in sample.
- Independent anecdotes: ApexTrend (4h, 236-EMA, 38-EMA cross, 35-bar high) on BTC/ETH/SOL/XRP Binance futures Jun 2025-Mar 2026: +37.36%, 54 trades, 53.7% win, max DD 5.51%, Sharpe 1.63, PF 3.32, fees unstated ([onixenix/fortunalabs](https://github.com/onixenix/fortunalabs)); vendor BTC daily 2022-2026: Golden Cross +87.34% (DD 37.10%, 4 trades), Donchian +36.50%, Donchian on 30-minute bars -55.3% ([Coinquant](https://www.coinquant.ai/blog/the-most-popular-crypto-trading-strategy-of-2026-backtested), snippet only).
- 2025 was hostile: BTC about -6%, ETH about -11%, CTAs in one of their deepest drawdowns; by mid-Aug 2026 CTA indices were +8.5-9% YTD ([Pantera](https://panteracapital.com/blockchain-letter/navigating-crypto-in-2026/), [With Intelligence](https://www.withintelligence.com/insights/cta-hedge-fund-report/), snippet only). The offline study (section 3) shows 2025 negative for almost every rule and asset.
- Cost drag: at 0.15% per side an always-rebalancing momentum book pays ~3.2%/yr weekly, 5.3% twice-weekly, 10.8% daily ([alimukri5-create/crypto-momentum](https://github.com/alimukri5-create/crypto-momentum)); Solana DEX round trips measured 0.2-0.6% on liquid tokens and 6-9% on some "liquid" ones ([Ballast README](/home/user/jonatangigex/familiars.family-opus5.5/README.md)).
- Seasonality overlays (BTC 21:00-23:00 UTC, weekend momentum in altcoins) exist in the literature ([Quantpedia](https://quantpedia.com/strategies/intraday-seasonality-in-bitcoin), snippet only) but are unproven at DEX costs.
- DCA baseline: monthly DCA into BTC Oct 2025-Sep 2026 bought 3.67% below the average price and beat a start-of-period lump sum by 33.6 points ([Pluang](https://pluang.com/en/news-feed/efek-rerata-biaya-dengan-bitcoin-perhitungan), snippet only, marketing).

Realistic net expectations for a retail agent: 0-30%/yr with 25-40% drawdowns and multi-month flat spells; lags buy-and-hold in strong up-years; negative years like 2025 are common (inference from the offline study and Ballast).

Capital and infrastructure: $500+; a daily cron at 00:00 UTC or a 4h cron on UTC bar closes plus a 30-60 s position monitor; GeckoTerminal/DexScreener OHLCV (free); Jupiter Swap V2 with size-aware quotes; Helius free or $1 tier RPC; Kraken/Binance data for SOL/BTC/ETH history.

Main failure modes: whipsaw in ranging regimes; lookback sensitivity (SMA100 on SOL 9.0% CAGR vs SMA200 57.9%); survivorship bias in any Solana token universe; gap-through stops on thin tokens (a -30% paper stop filled at -37%); 6-9% round trips if the liquidity filter slips; correlation across SOL/BTC/ETH sleeves (0.79-0.88); leaderboard irrelevance in mania weeks.

| candidate | venue | familiars-fit | expected-return note (short) |
|---|---|---|---|
| sol-4h-breakout-regime | Solana spot via Jupiter | yes | Backtest +26% over 6.5 months (survivorship-biased); realistic 0-25%/yr, 20-30% DD, 30-day P(loss) ~45% |
| daily-donchian-ensemble-voltarget | Solana spot (also Kraken/Binance) | yes | Paper 30% CAGR / Sharpe 1.58 (2015-2025); on a Solana universe at 30-60 bps expect 5-25%/yr, 25-40% DD |
| sol-regime-switch-usdc | Solana spot (SOL/USDC) | yes | Roughly 60-80% of SOL's upside with about half its drawdown; negative in range-bound years |
| solana-xs-momentum-rotation-gated | Solana spot via Jupiter | yes | Backtests range -8%/yr to +30% over 6 months with 50-60% DD; expect -10% to +20%/yr; satellite only |
| liquid-memecoin-4h-trend | Solana spot (Raydium/Meteora/PumpSwap via Jupiter) | yes | 0-30%/yr with 25-40% DD and many flat weeks; will not top an absolute-P&L board |
| narrative-rotation-7d-momentum | Solana spot via Jupiter | yes | Backtest +29% with 63% DD; boom-bust, -30 to -60% in reversals; leaderboard-chasing sleeve only |
| familiars-trend-4h-breakout | Solana spot via Jupiter | yes | Same Ballast rule set; 0-30%/yr with ~25% DD; +0.5% vs basket +136% in Aug-Sep 2026 |
| dca-sol-with-trend-tilt | Solana spot (SOL/USDC) | yes | No alpha; SOL's path-dependent average-cost return; the honest benchmark |
| perp-trend-ensemble-hyperliquid | Hyperliquid perps (or Binance/Bybit) | no | Sharpe 0.8-1.5 in good decades at 1x; expect 0-30%/yr with 20-35% DD; short side whipsaw-prone |

### Mean reversion and grid

What the evidence says:

- Mean reversion works only in ranging regimes: on BTC/USDT 4h, Jan 2023-Dec 2025, with 0.075% taker and 0.05% slippage, profit factor was 1.62 at ADX < 20 and 0.74 at ADX > 30; across 78 backtests returns were driven by regime (+16.3% bull, -40.6% bear), not by the rule; a full filter set traded 61% less and returned 2.6x more ([Coinquant](https://www.coinquant.ai/blog/mean-reversion-crypto-strategy-backtested-when-it-beats-trend-following), snippet only, vendor).
- Funding-rate contrarian signals are unreliable: a BTC perp z-score fade (|z| > 2.5, hold 8h, 0.04% taker, 2020-2024) had Sharpe 0.77, max DD 13.8%, 125 trades, 54.4% win, negative Calmar in 2021-22 ([Adeline117/Strategy-project](https://github.com/Adeline117/Strategy-project)); a 2019-Jun 2026 daily test concluded the signal "does not work" because high funding accompanies strong trends ([tradingstrategies.work](https://tradingstrategies.work/blog/funding-rate-signal-btc-backtest), snippet only).
- Grid trading has an expected value of effectively zero without trend insight and loses after fees; backtests show single-digit returns in ranges and losses when price leaves the range ([arXiv 2506.11921](https://arxiv.org/pdf/2506.11921), snippet only); vendor claims of "180% APR" are marketing ([goodcrypto](https://goodcrypto.app/case-study-180-apr-using-grid-bot-while-bitcoin-stayed-flat/), snippet only).
- Cointegration pairs produce positive gross returns in crypto but are highly sensitive to costs and pair stability ([arXiv 2109.10662](https://arxiv.org/abs/2109.10662), snippet only); on Solana spot there is no short leg, so pairs collapse into a long-only relative-value rotation with weak edge (inference).
- Solana DEX round trips of 20-60 bps on liquid tokens ([Jupiter fees](https://developers.jup.ag/docs/ultra/fees), snippet only; [Ballast README](/home/user/jonatangigex/familiars.family-opus5.5/README.md)) kill grid and pairs at hourly frequency; grid spacing must be >= 3-5% to net anything (inference).

Realistic net expectations: break-even to +10%/yr as a regime-gated satellite; negative in trending or bear regimes; a SOL/USDC grid is dominated by Kamino USDC lending on risk-adjusted terms (inference).

Capital and infrastructure: $500-$1,000; 4h or hourly cron; ADX/Bollinger/RSI series; Jupiter quotes at size; perps venue only for the funding-contrarian variant.

Main failure modes: catching falling knives at regime breaks; cost-heavy short holds; regime-detection lag; a breakout leaving a grid fully in SOL (or USDC) with an unrealised loss; vendor-only evidence.

| candidate | venue | familiars-fit | expected-return note (short) |
|---|---|---|---|
| range-regime-rsi-bollinger-4h | Solana spot via Jupiter | yes | Vendor PF 1.62 in-regime at CEX costs shrinks at 40-100 bps DEX costs; break-even to +10%/yr satellite |
| funding-zscore-contrarian-btc-perp | Hyperliquid or Binance USDT-M perps | no | Sharpe 0-0.8 depending on years, 0-10%/yr at 1x with ~15% DD; not standalone |
| solana-spot-grid-sol-usdc | Jupiter (Solana spot) | yes | -10% to +10%/yr; positive only in extended ranges; cannot rank on an absolute-USD board |
| solana-longonly-relative-value | Jupiter (Solana spot) | yes | Unknown; roughly market beta minus costs; shares SOL drawdowns (56% median single-token DD) |

### Carry, basis and market making

What the evidence says:

- Hyperliquid funding is paid hourly; rate = average premium + clamp(0.01%/8h interest - premium, -0.05%, +0.05%); the fixed interest component is 0.00125%/hour (~11.6% APR to shorts when premium is zero); capped at 4%/hour ([Hyperliquid docs mirror](https://raw.githubusercontent.com/thanhtoan0306/hyperliquid-docs-ssr/main/content/pages/trading/funding.md)). Fees: perps 1.5 bps maker / 4.5 bps taker, spot 4/7 bps ([fees.md](https://raw.githubusercontent.com/thanhtoan0306/hyperliquid-docs-ssr/main/content/pages/trading/fees.md)); Binance USDT-M 2/5 bps ([tradersunion](https://tradersunion.com/brokers/crypto/view/binance/futures-fees/), snippet only). A two-leg taker round trip on Hyperliquid costs ~23 bps; cross-venue ~19-20 bps.
- Practitioner guides put realised net APR at 3-12% on BTC/ETH and 20-60% gross, episodic, on mid-caps, with realised typically 30-50% below the gross rate seen at entry ([arbitragescanner](https://arbitragescanner.io/blog/crypto-funding-rate-arbitrage-guide), [buildix](https://www.buildix.trade/blog/crypto-funding-rate-arbitrage-delta-neutral-hyperliquid-binance), snippet only, promotional). Funding across majors spent long stretches negative in 2026 and HL BTC funding has been ~1.95x more volatile than Binance's since Jan 2026 ([Coin Metrics](https://coinmetrics.substack.com/p/state-of-the-network-issue-368), snippet only; one snippet claims Jan 2026 BTC funding averaged +0.51% per 8h, which conflicts). The offline study confirms decay: HL BTC 9.73% always-in but 4.98% in 2026 YTD, rule net 4.82%; SOL negative 34.2% of hours (section 3).
- Reference implementations: Hummingbot's funding-rate-arbitrage script defaults to Hyperliquid vs Binance, entry at a 0.1% normalised differential, 1% take-profit, stop at -0.1%, default leverage 20x and $100 position ([v2_funding_rate_arb.py](https://raw.githubusercontent.com/hummingbot/hummingbot/master/scripts/v2_funding_rate_arb.py)); an alternative library shows net revenue of -0.31 to +0.53 USDT per 100 USDT per opportunity after 0.18-0.50% commissions ([aoki-h-jp](https://github.com/aoki-h-jp/funding-rate-arbitrage)). Guides put the practical floor at tens of thousands USDC for funding income to exceed costs ([OneKey](https://onekey.so/blog/ecosystem/hyperliquid-binance-funding-arbitrage-20260429/), snippet only).
- Tail events: 10 Oct 2025, ~$19B liquidated in 24h across >1.6M accounts, $3.21B in one minute at 21:15 UTC, Binance BTC bid-ask from 0.02 to 26.43 bps and top-of-book depth from $103.64M to ~$170k (-98%); funding had risen from ~10% to ~30% annualised by 6 Oct ([Amberdata](https://blog.amberdata.io/how-3.21b-vanished-in-60-seconds-october-2025-crypto-crash-explained-through-7-charts), [FTI](https://www.fticonsulting.com/insights/articles/crypto-crash-october-2025-leverage-met-liquidity), snippet only). USDe printed $0.65 on Binance because its Unified Account marked collateral off its own ~$8M book; Binance compensated and moved to external oracles by 14 Oct ([CoinDesk](https://www.coindesk.com/markets/2025/10/13/no-ethena-s-usde-didn-t-de-peg), snippet only). Stream Finance disclosed a ~$93M loss on 4 Nov 2025; xUSD fell ~77%, Elixir deUSD collapsed to ~$0.015 and wound down, ~$285M of interlinked debt ([Pharos](https://pharos.watch/learn/case-studies/stream-elixir-contagion-2025/), [Yahoo](https://finance.yahoo.com/news/elixir-shuts-down-deusd-stablecoin-104937488.html), snippet only).
- Compressed institutional basis: sUSDe APY fell from 60%+ at launch to ~4.5% (Jun 2026) and ~4.2% net (Aug 2026) ([eco.com](https://eco.com/support/en/articles/15254002-ethena-usde-and-susde-2026-delta-neutral-yield), snippet only). Stablecoin lending: Kamino USDC 4-9% APY through 2026 (1.6-7.2% across markets on 15 Sep 2026), Aave v3 USDC ~3.6% mid-Sep 2026 ([eco.com](https://eco.com/support/en/articles/14801186-kamino-lending-solana-s-money-market-explained), [aavescan](https://aavescan.com/stablecoins), snippet only).
- Market making: HLP has averaged ~15-30% APR across quarterly windows, reported $137.91M cumulative PnL and 5.68% max drawdown on 3 Sep 2026; loss events JELLY (Mar 2025, -$13.5M unrealised, resolved by delisting at $0.0095), ~$4.9M POPCAT bad debt (Nov 2025), commodity-perp drawdowns (Mar 2026); 4-day lockup ([datawallet](https://www.datawallet.com/crypto/hyperliquid-hlp-explained), [CoinDesk](https://www.coindesk.com/markets/2025/03/26/hyperliquid-delists-jellyjelly-after-vault-squeezed-in-usd13m-tussle), snippet only). Passive counterparties on Hyperliquid bear adverse-selection costs from 4.3M hidden metaorders ([arXiv 2606.15715](https://arxiv.org/abs/2606.15715), snippet only), so a VPS quoter with 1-2 s reaction is the uninformed side (inference).
- Solana atomic/DEX arbitrage is closed: >90M Jito-confirmed arb transactions earned ~$142.8M cumulative in 2025 (~$1.5 each), Solana MEV revenue $720.1M in 2025, Jito client on ~92-93% of stake, public RPC ~200 ms behind ([Extropy](https://academy.extropy.io/pages/articles/mev-crosschain-analysis-2025.html), snippet only). Retail CEX latency of 100-500 ms captures only minutes-long discrepancies ([everstrike](https://blog.everstrike.io/7-arbitrage-strategies-are-still-accessible-to-retail-quants-in-2025/), snippet only).
- LP economics: Meteora DLMM earned $907.3M fees on $168.3B volume in FY2025 (0.54%), ~$22.31M in the last 30 days; Orca tiers 0.01/0.04/0.25/1% with 87% to LPs; Meteora's docs warn IL can exceed fees when price leaves the bins ([DefiLlama](https://defillama.com/protocol/meteora-dlmm), snippet only); no IL-adjusted retail profitability distribution was found ([GeekLad analyzer](https://github.com/GeekLad/meteora-profit-analysis) is per-wallet only). Hedged JLP: a solo builder reports 14.81% live net APY since 1 Apr 2026 ([SolNeutral](https://medium.com/@jayepaul81/building-solneutral-a-delta-neutral-usdc-vault-on-solana-049eb1d8a2ce), snippet only); Drift's protocol-v2 repo was archived read-only on 3 Sep 2026 ([drift-labs/protocol-v2](https://github.com/drift-labs/protocol-v2)) after a ~$285M exploit on 2026-04-01 ([CryptoRank](https://cryptorank.io/news/feed/13fb9-285m-solana-protocol-drift-largest-exploit-2026), snippet only).
- Infrastructure: Hyperliquid recommends nodes in Tokyo; a plain VPS on api.hyperliquid.xyz is fine for hourly carry but not for quoting ([hyperliquid-dex/node](https://github.com/hyperliquid-dex/node); the latter part is inference).

Realistic net expectations: 5-15%/yr on deployed capital for cross-venue funding spreads at 2-3x leverage; 3.5-9% for stablecoin parking; 15-30% APR historically for HLP with short-vol tails; roughly 0 to negative for self-run quoting (inference from the sources above).

Capital and infrastructure: $2,000-$5,000+ for carry to be worth the operational risk; Hyperliquid API wallet plus Binance/Bybit futures keys; Tokyo/Singapore VPS; watchdog that flattens both legs if one is missing >60 s; margin and funding-sign alerts.

Main failure modes: legging risk; liquidation on one venue during a cascade despite zero net delta; funding differential inverting; venue oracle mispricing (USDe); ADL closing the profitable hedge leg; withdrawal/bridging delays; re-hypothecated "stable" yield (Stream/Elixir); adverse selection and inventory blow-out for quoters.

| candidate | venue | familiars-fit | expected-return note (short) |
|---|---|---|---|
| hl-cex-funding-spread | Hyperliquid + Binance/Bybit perps | no | 3-12% net on majors per guides; at 2-3x leverage 5-15%/yr on deployed capital; ~20 bps per rotation |
| hl-spot-perp-carry | Hyperliquid spot + perps | no | ~11.6% APR floor only when premium ~0; 5-15% in positive regimes, ~0 while parked |
| binance-dated-basis | Binance (or Bybit/OKX) dated futures + spot | no | Locked 4-10% APR when entered; near-zero opportunity most of 2026 |
| stable-parking-kamino | Kamino (Solana) / Aave v3 | no | 3.5-9% APY, measured not marketed; the hurdle rate |
| hlp-deposit | Hyperliquid HLP vault | no | 15-30% APR historically; short-vol; single-digit DD with tail risk from JELLY-type events |
| hedged-jlp-drift | Jupiter JLP + Drift or Hyperliquid perps | no | Vendor ~10-15% net APY; single digits after negative hedge funding; Drift status uncertain |
| dlmm-sol-usdc-mm | Meteora DLMM / Orca Whirlpools | no | Vendor 15-50% APR; realistic 0-15% in ranging months, negative in trends |
| hl-perp-pmm-hummingbot | Hyperliquid perps | no | Likely negative to low single digits for a VPS bot without a fair-value model |

### On-chain launches and memecoins

What the evidence says:

- Base rates: 98.7% of >7M pump.fun tokens (Jan 2024-Mar 2025, >= 5 trades) showed pump-and-dump patterns; ~97,000 kept >= $1,000 liquidity; 361,000 of 388,000 Raydium pools showed soft-rug traits ([Solidus Labs report](https://www.soliduslabs.com/reports/solana-rug-pulls-pump-dumps-crypto-compliance), [CoinDesk 2025-05-07](https://www.coindesk.com/business/2025/05/07/98-of-tokens-on-pump-fun-have-been-rug-pulls-or-an-act-of-fraud-new-report-says), snippet only; pump.fun disputed the methodology). Graduation: 0.63% (655,770 tokens, Sep-Oct 2025, [arXiv 2609.10246](https://arxiv.org/abs/2609.10246), snippet only), 0.8% pump.fun vs 1.02% LetsBonk ([Solana Floor](https://solanafloor.com/news/solana-launchpad-showdown-pump-fun-vs-lets-bonk-fun), snippet only), 2.69-2.84% in four August 2026 cohorts ([Solana Compass](https://solanacompass.com/news/pumpfun-launched-42000-tokens-in-one-day-fewer-than-2-will-ever-reach-a-dex), snippet only); ~50,000 launches/day, ~500 graduations/day, median lifespan 2 minutes with 8 trades, 75th percentile 38 minutes ([unretain/solagents](https://github.com/unretain/solagents)).
- Retail P&L: 95.6% of 312k analysed wallets broke even or lost (Dune, May 2025); of 13.55M wallets only 55,296 ever realised >$10k ([Cointelegraph](https://cointelegraph.com/news/pump-fun-crypto-traders-majority-do-not-realize-profits-dune-data), snippet only). CoinGecko (May 2026): 73.3% of active wallets had positive realised P&L in April 2026 (low 30.1% in June 2025), but 65.1% of winners made $1-$500 and bots are not filtered; monthly active wallets fell from 5.2M (May 2025) to 1.8M (Dec 2025) ([CoinGecko](https://www.coingecko.com/research/publications/pump-fun-traders-are-making-a-comeback), snippet only).
- Who profits: deployer-funded same-block snipers touched up to 1.75% of launches in a month (15,000+ launches, 4,600+ sniper wallets), extracted >15,000 SOL, 87% of snipes profitable, 85% exited within 5 minutes ([Pine Analytics, Apr 2025](https://pineanalytics.substack.com/p/exit-liquidity-machines), snippet only); 1,012 persistent wallet cohorts co-fire among the first ten buyers across 166,098 launches over 13.4 days in June 2026 ([arXiv 2607.02795](https://arxiv.org/abs/2607.02795), snippet only). Vendor pages claim 200+ bots compete in the first 500 ms and only 10-15% land profitably ([openliquid](https://openliquid.io/tools/pumpfun-sniper-bot/), snippet only, marketing).
- Filters separate populations but no one has published a positive-expectancy launch strategy net of costs: XGBoost on the first 5 minutes reaches AUC-PRC ~0.80 on 6.4M tokens ([arXiv 2608.20271](https://arxiv.org/abs/2608.20271), snippet only); MELT/MemeTrans cut simulated losses 56.1% on 40k migrated launches ([arXiv 2602.13480](https://arxiv.org/html/2602.13480v1), snippet only); solagents' best filter (X profile, >= 15 traders, active buying, dev holding, not a serial deployer) gives 44.1% win rate and 48.7% graduation vs 14.4% base (3.06x lift) yet "with realistic exits and costs the best configuration still loses ~17% per trade" ([unretain/solagents](https://github.com/unretain/solagents)). Ballast adds same-slot bundle forensics (bundle-bought <= 30%, bundle-held <= 10%), dev mints <= 3, top-10 <= 35%, round-trip quote <= 6%, Jupiter organic score >= 25; on 2026-09-24 only 4 of 54 sub-120-minute launches passed the organic gate and volume bots paid identical fees at 29 tx/s with score 0 ([Ballast README](/home/user/jonatangigex/familiars.family-opus5.5/README.md), [onchain.ts](/home/user/jonatangigex/familiars.family-opus5.5/src/onchain.ts)).
- Fee stack: pump.fun bonding curve 1% per buy and sell; PumpSwap after graduation 0.30% total plus 0.02-0.2% to liquidity since 2025-09-02; LetsBonk 1% swap fee; terminals (Trojan, GMGN, Axiom) 1%, Photon/BullX/Padre 0.5-1% (sources conflict), BullX stopped trading June 2026; a typical migration trade costs 1.6-3.3% per side, 3.2-6.6% round trip ([soltokencreator](https://www.soltokencreator.io/blog/pump-fun-fees-explained), [solanatools](https://solanatools.io/blog/solana-trading-bot-fees-compared), snippet only; [pump-fun-sdk fee-sharing.md](https://github.com/nirholas/pump-fun-sdk/blob/main/docs/fee-sharing.md)). Jupiter charges 50 bps on tokens younger than 24 hours ([jup-ag/docs fees](https://raw.githubusercontent.com/jup-ag/docs/main/ultra/fees.mdx)).
- MEV: an IMC 2025 measurement of Jito found >500K sandwiches and >$7.7M victim losses; >86% of single-transaction bundles carried tips too small to buy priority ([ACM](https://dl.acm.org/doi/10.1145/3730567.3764493), snippet only). Jito: minimum tip 1,000 lamports, 1 request/s/IP, tip-floor endpoint, 70/30 priority-fee/tip split for sendTransaction, anti-sandwich via a "jitodontfront" account at index 0 ([jito-docs](https://github.com/jito-labs/jito-docs/blob/main/docs/source/lowlatencytxnsend.md)); observed April 2026 tip percentiles p50 0.00001-0.00003 SOL, p75 0.0001-0.00015 SOL, p99 0.01 SOL; launch-sniping tips 0.1-3 SOL ([gist](https://gist.github.com/NeOMakinG/49daadcd4855dc8986664ad0ba07b757)). Multi-slot "wide" sandwiches are 93% of Solana sandwiches and extracted >529,000 SOL in a year; Jito banned 15 validators ([bloXroute/Ghost](https://medium.com/bloxroute/a-new-era-of-mev-on-solana-ae5cff390b71), snippet only).
- Infrastructure tiers: Yellowstone gRPC starts at Helius' $499/mo Business plan (was $999), QuickNode/Chainstack $399-499/mo, metered from ~$84/mo ([subglow](https://subglow.io/subglow-vs-helius), snippet only); free tiers: Helius free RPC key, DexScreener 60/300 req/min, RugCheck 1 req/s, GeckoTerminal ~30 calls/min. Rust sniping SDKs exist ([0xfnzero/sol-trade-sdk](https://github.com/0xfnzero/sol-trade-sdk), MIT, 343 stars) with paid submit lanes. GMGN OpenAPI exposes token DD, wallet scoring and swaps ([gmgn-skills](https://github.com/GMGNAI/gmgn-skills/blob/main/Readme.md)).
- Launchpads: pump.fun 45.9% vs LetsBonk 42.3% share in a 2026 snapshot; pump.fun moved graduated liquidity to PumpSwap in March 2025 (~85 SOL / ~$69k mcap), added creator fee sharing to up to 10 wallets; both advertise anti-sniper measures in 2026 with no measurement found ([KuCoin](https://www.kucoin.com/blog/en-solana-launchpad-letsbonk-fun-sees-600-revenue-surge-in-early-2026), [bex.co](https://bex.co/blog/2026/04/22/meme-launchpad-2-pump-fun-letsbonk-anti-sniper-bonding-curve-professionalization), snippet only). Base (Clanker/Zora): >95% of Clanker tokens lose liquidity within 48h; familiars is Solana-only ([Coin Bureau](https://coinbureau.com/analysis/best-memecoin-launchpads), snippet only).
- Offline study (section 3): Telegram calls show a median peak of 1.59x, 35.7% reach 2x, 5.1% reach 10x, and hold-to-now median 0.14x in the devcabal set; the naive policy's expectancy is tail-driven (top 5% of trades = 82% of P&L).

Realistic net expectations: negative to slightly positive median with rare 5-20x winners for a filtered 20-120-minute-old launch sleeve; a launch trade needs roughly +4-7% just to break even on the bonding curve; sniping is negative EV for a non-insider (inference from the sources above).

Capital and infrastructure: $300+ for a capped sleeve; Helius free/$1 RPC (public RPC takes 15-40 s per token for forensics); Jupiter Tokens V2 + Shield + Swap V2; pump.fun frontend-api-v3; Jito sendTransaction with a p50-p75 tip; sub-second sniping needs $400-500/mo streaming plus paid lanes and bare metal.

Main failure modes: rugs and honeypots despite filters (pre-funded clusters evade same-block checks); stops gapping 7+ points on 30-60 s polling; bot volume passing count filters; 2% protocol round trip plus slippage; correlated losses across simultaneous positions; organic score is a black box that changes.

| candidate | venue | familiars-fit | expected-return note (short) |
|---|---|---|---|
| filtered-launch-momentum | pump.fun/PumpSwap and LetsBonk via Jupiter | yes | Cannot be backtested; best open evidence -17%/trade; negative-to-slightly-positive median, rare 5-20x; <= 10-20% of capital |
| graduation-momentum-postmigration | PumpSwap and Raydium pools via Jupiter | yes | Unquantified; majority losing trades, occasional 2-5x; ~0.3% per side fees but >90% future-dead tokens |
| familiars-narrative-launch-sleeve | Solana spot (pump.fun/PumpSwap via Jupiter) | yes | Forward-test only; most trades -30 to -45%, a few 2-10x; net expectancy unknown, likely negative |
| ml-first-5-minutes-rug-filter | Offline model; Solana execution | yes | Not a return source; evidence supports roughly halving launch-sleeve losses, not profitability |
| first-block-sniping | pump.fun/LetsBonk bonding curves, direct program calls | no | Negative EV for a non-insider once tips ($0.1-3 SOL) and $400-500/mo infra are counted |
| base-clanker-early-entry | Base: Uniswap v4 (Clanker), Zora | no | Unknown, likely negative; no Shield/organic-score equivalent; deprioritise |

### Copy-trading on-chain wallets

What the evidence says:

- Discovery vendors: Nansen Smart Trader labels, API Pro $49/mo annual or $69/mo with 2,000 credits, 5 credits per leaderboard call, pay-per-use from $0.01/query ([docs.nansen.ai](https://docs.nansen.ai/api/smart-money), snippet only; older reviews still quote $99/$1,899 tiers). GMGN: tracking free, copy-trade 1% per trade (0.70-0.90% with referral), Solana-only, 10 concurrent copy tasks ([gmgn blog](https://gmgn.ai/blog/how-to-track-copy-solana-smart-money/), snippet only). Cielo: Free (10 wallets), Pro $59/mo (200), Whale $199/mo (1,000, API); tags Gem Finder (2x on 30% of tokens), High Win Rate (>75%), New Wallet (<= 14 days), Sniper (avg hold < 2 min); copy trading 0.6% per trade ([docs.cielo.finance](https://docs.cielo.finance/guides/copy-trading/finding-good-wallets), snippet only). Also Birdeye (top traders per token), Codex (free 10,000 req/mo, Growth $350/mo), Vybe (free key; [endpoints](https://github.com/vybenetwork/solana-wallet-pnl-profit-and-loss-api)), Solana Tracker (2,500 free req/mo, Kolscan-fed KOL leaderboard), Dune (Free 2,500 credits, Analyst $75/mo), Arkham, Wallet Master $125-250/mo (snippet only).
- Persistence: on Hyperliquid, wallet informativeness persists with rank correlation 0.52 across adjacent 10-day windows and identity features raise out-of-sample R2 for 1-second returns to 12.31% (17.1B messages, 147,113 wallets, Dec 2025); public identity creates adverse selection against known-informed wallets ([arXiv 2608.04373](https://arxiv.org/abs/2608.04373), snippet only; [repo](https://github.com/daojingzhai/public-trader-identity)). Persistence at 1-second horizons does not justify copying at minutes of delay (inference).
- Memecoin copying: the WWW 2026 paper documents adversaries who front-run copiers, conceal positions across wallets, wash trade and fabricate sentiment; its LLM pipeline reaches 73% precision on projects and 70% on KOL wallets on ~1,000 projects, and the resulting copier return is ~3% average per memecoin investment under realistic frictions ([arXiv 2601.08641](https://arxiv.org/abs/2601.08641), snippet only). An open Polymarket experiment screened 30 leaderboard wallets plus 30 controls down to 2, copied with a median 5.6-minute delay, and ended +$171 on $10,000 (191 trades at -$1,937 before an in-play filter, then 81 trades at +$2,108, t=1.53) ([BallesJr/polymarket-copy-trader](https://github.com/BallesJr/polymarket-copy-trader)).
- Base rates: 48.5% of CEX copy-trading followers profitable over 90 days per a broker blog ([vantagemarkets](https://www.vantagemarkets.com/academy/is-copy-trading-profitable/), snippet only, unverified); familiars 37 of 1,350 agents positive on 2026-09-24 ([Ballast README](/home/user/jonatangigex/familiars.family-opus5.5/README.md)).
- Screening metrics that vendors and practitioners converge on: realised (not unrealised) 30-90 day P&L, win rate 50-75% (>75% flagged as bot-like), >= 20-30 distinct tokens, median hold >= minutes to hours (avg hold < 2 min = sniper), consistency across sub-windows, wallet age > 14 days, no single token > ~50% of profit, not a first-block buyer/bundler ([Nansen use case 4](https://docs.nansen.ai/guides/templates/complex-use-cases/use-case-4-copytrading-top-performing-wallets), [Dune analyzer](https://dune.com/couldbebasic/wallet-analyzer-for-copy-traders), snippet only). Displayed leaderboard P&L is survivorship: a wallet in 500 launches with 3 moons looks great while 497 rugs are invisible (inference).
- Latency: Solana slots ~400 ms; Helius WebSocket ~200-400 ms, gRPC sub-100 ms; Helius Developer $49/mo includes Enhanced WSS transactionSubscribe (since 7 Apr 2026) and 50 rps, Business $499/mo adds LaserStream gRPC ([helius plans](https://www.helius.dev/docs/billing/plans), snippet only); amateur detect-to-submit paths run 430-680 ms vs ~50 ms production ([yavorovych](https://yavorovych.medium.com/how-to-build-a-solana-copy-trading-bot-2026-guide-559448259e96), snippet only); a trade 500 ms late on a fresh memecoin can fill 5-10% (thin pools) to 2x (first seconds) above the leader (practitioner claims, unmeasured).
- Execution: self-hosted Jupiter (10 bps + slippage) is 5-10x cheaper than GMGN/Axiom/Photon (1%) or Cielo (0.6%) ([Jupiter fees](https://support.jup.ag/hc/en-us/articles/18735045234588-Fees), snippet only). Copiers are structurally the back-run liquidity of sandwiches (inference from [Ghost](https://medium.com/bloxroute/a-new-era-of-mev-on-solana-ae5cff390b71), snippet only).
- Leader gaming: devs airdrop tokens to KOL wallets so trackers show a "buy" and seed phishing-tagged wallets from one source ([GMGN on X](https://x.com/gmgnai/status/1963882916460769393), snippet only); honeypots airdropped to copied leaders ([Bitquery tutorial](https://docs.bitquery.io/docs/usecases/copy-trading-bot/), snippet only); an Axiom employee was exposed for insider trading in Feb 2026 ([MEXC news](https://www.mexc.com/news/74318), snippet only). Mirror only DEX swap instructions signed by the leader, never transfers (inference).
- fomo.family as a source: social, not copy, trading; fomoapi exposes leaderboards with resolved Solana/EVM wallets, cached one hour, 5 rps on paid keys, WS "planned, not yet active" ([Open-Fomo-API](https://github.com/abstradeapi/Open-Fomo-API)); see section 6.
- familiars as a source: every agent's trades, positions and equity history are public ([familiars.ts](/home/user/jonatangigex/familiars.family-opus5.5/src/familiars.ts)); Ballast already mines top-7D agents' trade timing ([learn.ts](/home/user/jonatangigex/familiars.family-opus5.5/src/cli/learn.ts)); see section 5.
- Sizing practice: open-source bots default to 0.01 SOL per copy, 1% slippage, TP 10% / SL 5% ([keidev-sol](https://github.com/keidev-sol/Solana-Copy-Trading-Bot-Rust)); Ballast sizes 1% of capital at risk assuming a 45% loss on stop, caps 1% of pool liquidity and 5 positions, pauses after a 6% daily loss or 25% drawdown ([config/agent.json](/home/user/jonatangigex/familiars.family-opus5.5/config/agent.json)).

Realistic net expectations: no credible backtest exists; measured analogues are ~3% per bet and a flat Polymarket experiment; with 10 bps Jupiter fees, ~1% slippage and 25% stops the honest range is -30% to +40%/yr with high variance, and it bleeds unless consensus filters cut trade count sharply (inference).

Capital and infrastructure: $500-$2,000; Helius Developer $49/mo (Enhanced WSS) or Shyft gRPC $199/mo; Jupiter Swap V2 with simulation and a Jito/DontFront sender; SQLite/Postgres for wallet scores; a weekly re-scoring job; optional Nansen/Cielo/fomoapi subscriptions ($0-$200/mo).

Main failure modes: late fills on fresh tokens; wide sandwiches; survivor pools whose edge decays; leaders dumping into thin books before copiers exit; correlated positions in one narrative token; profile address != trading wallet (fomo); undocumented rate limits (familiars); leaders exploiting copiers by design.

| candidate | venue | familiars-fit | expected-return note (short) |
|---|---|---|---|
| sol-smartmoney-consensus-follow | Solana DEXs via Jupiter; posts on familiars | yes | No credible backtest; -30% to +40%/yr, high variance; paper-trade 8+ weeks first |
| slow-smartmoney-swing | Solana DEXs via Jupiter (JUP, RAY, WIF, BONK class) | yes | Trend profile; 0-30%/yr with 25-40% DD; smart-money filter unvalidated |
| swing-wallet-copy | Solana spot via Jupiter | yes | Unknown; leader alpha decays and is eaten by lag and fees; drop leaders whose copied P&L is negative |
| fomo-human-leaders-mirror | Solana DEXs; discovery via fomo.family/fomoapi | yes | Unmeasured; hourly cache and raw-P&L ranking imply heavy survivorship; likely negative standalone |
| fomo-solana-wallet-copy | Solana spot via Jupiter | yes | Leader return minus 1-3% per round trip of slippage/lag and a survivorship discount; paper-trade first |
| fomo-onchain-flow-signal | Solana spot via Jupiter | yes | Unmeasured; needs a 2-4 week flow-vs-return study; research cost near zero |
| familiars-board-copy | Solana spot via Jupiter; signals from familiars API | yes | Speculative; small edge from narrative detection at best; negative if copying stale or self-dealing agents |
| familiars-top-agent-consensus | Solana DEXs via Jupiter; familiars API | yes | Roughly zero or negative standalone; confirmation feature, <= 10% of equity |
| familiars-board-consensus-copy | Solana spot via Jupiter; posts on familiars | yes | No measured returns; ~3% of agents profitable; experiment sleeve <= 20% of capital |
| familiars-consensus-copy | Solana spot via Jupiter, posted on familiars | yes | Likely negative given 2.7% base rate and 0.2-6% round trips; signal input only |
| familiars-leader-mirror | Solana via Jupiter Swap V2; posts to familiars | yes | Unknown; 20 bps Jupiter round trip (100 bps on <24h tokens) plus slippage; mirroring late into pumps likely loses |
| leaderboard-post-mining-follow | Solana spot via Jupiter | yes | Unknown; most 24h leaders are noise or self-pumps; 0 or negative until persistent skill is validated |

### Copy-trading on CEXes, perp DEXes and Polymarket

What the evidence says:

- Hyperliquid data: the leaderboard is a public GET at stats-data.hyperliquid.xyz/Mainnet/leaderboard with ~32K opted-in wallets and day/week/month/allTime pnl, roi and volume ([web3-ethereum-defi api.py](https://raw.githubusercontent.com/tradingstrategy-ai/web3-ethereum-defi/master/eth_defi/hyperliquid/api.py)); any wallet's fills are readable via POST /info (userFillsByTime capped at the 10K most recent fills; REST 1,200 weight/min per IP) and via WebSocket userFills ([hyperliquid-python-sdk info.py](https://raw.githubusercontent.com/hyperliquid-dex/hyperliquid-python-sdk/master/hyperliquid/info.py), [copy-trading README](https://raw.githubusercontent.com/tradingstrategy-ai/web3-ethereum-defi/master/scripts/hyperliquid/README-hyperliquid-copy-trading.md)).
- Hyperliquid vaults: fixed 10% profit share, leader keeps >= 5%, 1-day default lockup, no management fee ([vaultDetails.ts](https://raw.githubusercontent.com/nktkas/hyperliquid/main/src/api/info/_methods/vaultDetails.ts); numbers from docs snippets); ~3,300 active non-HLP vaults; an open monitor flags "copy-farm" (high TVL, <1% leader stake), "smoothed-suspect" (autocorrelation > 0.5) and "untested-downside" (Sortino > 20 with no drawdown) vaults, and notes NAV is sampled at 5-14 day intervals ([hl-vault-monitor](https://raw.githubusercontent.com/petercool/hl-vault-monitor/main/README.md)). HLP TVL fell from ~$269M (June 2026) to ~$184M ([eco.com](https://eco.com/support/en/articles/15197987-hyperliquid-vault-strategies-2026-hlp-and-user-vaults-explained), snippet only). HypervaultFi, a third-party product promising 76-95% APY, drained ~$3.6M from ~1,100 depositors in Sep 2025 ([CryptoRank](https://cryptorank.io/news/feed/00e88-hypervaultfi-suspected-rug-pull-takes-3-6m), snippet only).
- Leaderboard whales blow up: James Wynn opened a $1.25B BTC long at 40x, was fully liquidated in late May 2025 losing >$37M, was liquidated nine times, promoted his referral code, and his account fell to ~$23 ([CoinDesk 2025-05-31](https://www.coindesk.com/markets/2025/05/31/cryptos-most-watched-whale-gets-fully-liquidated-after-placing-billions-in-risky-bets), snippet only).
- Builder codes: third-party Hyperliquid front-ends charge up to 10 bps per perp fill (1% spot), approved per user; total builder revenue >$40M, ~40% of users trade through third-party interfaces ([hyperdash](https://hyperdash.com/learn/hyperliquid-builder-codes-explained-how-third-party-apps-earn-fees-on-chain), snippet only).
- CEX copy APIs (verified in ccxt source): OKX has a full follower API under /api/v5/copytrading/ (public-lead-traders, public-stats, first-copy-settings, stop-copy-trading, ...) ([ccxt okx.py](https://raw.githubusercontent.com/ccxt/ccxt/master/python/ccxt/okx.py)); lead traders take up to 30% (typically 8-13%), settled weekly, ~$50 minimum (snippet only). Bitget has follower and trader endpoints under /api/v2/copy/ ([ccxt bitget.py](https://raw.githubusercontent.com/ccxt/ccxt/master/python/ccxt/bitget.py)); futures profit share 0-13%, spot up to 10%, high-water mark (snippet only). Bybit's copy API in ccxt is legacy contract/v3 only; products Classic (10-15% share), Pro (up to 30%, opaque positions), TradFi; copiers pay ~0.055% taker ([ccxt bybit.py](https://raw.githubusercontent.com/ccxt/ccxt/master/python/ccxt/bybit.py); tiers snippet only). Binance exposes only lead-trader status endpoints; no follower API ([ccxt binance.py](https://raw.githubusercontent.com/ccxt/ccxt/master/python/ccxt/binance.py)). None of these copy products serve US persons (inference from exchange terms).
- Solana perps: Drift was exploited for ~$285M (over 50% of TVL, from ~20 vaults) on 2026-04-01, attributed to North Korean actors, and is relaunching as Velocity DEX (private beta July 2026, public relaunch targeted Q3 2026) ([Chainalysis](https://www.chainalysis.com/blog/lessons-from-the-drift-hack/), snippet only); drift-vaults support up to 90-day redeem periods ([vault.rs](https://raw.githubusercontent.com/drift-labs/drift-vaults/master/programs/drift_vaults/src/state/vault.rs)). Jupiter Perps has no copy feature; Lighter exposes account trades without auth and charges zero retail fees ([lighter-python](https://raw.githubusercontent.com/elliottech/lighter-python/main/README.md)); Aster offers pooled trader vaults (snippet only).
- Polymarket: data-api /v1/leaderboard (DAY/WEEK/MONTH/ALL, 1-hour server cache), /positions, /activity, /trades are public ([poly-sdk docs](https://raw.githubusercontent.com/cyl19970726/poly-sdk/main/docs/api/02-leaderboard.md)); the official py-clob-client is archived in favour of py-sdk. An open lag-adjusted simulator classifies a leader as copyable only if lag-adjusted ROI >= 2%, ignores fees, and its author notes "by the time you SEE their trade the price has moved" ([copytrade.py](https://raw.githubusercontent.com/howwohmm/polymarket-paper/main/copytrade.py)).
- Academic evidence: trader popularity is a poor predictor of future returns on eToro ([arXiv 1406.7729](https://arxiv.org/pdf/1406.7729), 2014, snippet only); showing others' success increases risk taking, more so when copying is direct ([Apesteguia, Oechssler, Weidenholzer, Management Science 2020](https://pubsonline.informs.org/doi/10.1287/mnsc.2019.3508), snippet only); 19 of 28 eToro CopyPortfolio alpha estimates not different from zero ([TalTech](https://digikogu.taltech.ee/en/Download/9c402020-d23e-447e-bcba-e41823baa02d), snippet only).
- Selection rules that reduce decay: >= 90 days and >= 50 round trips, win rate >= 52-60%, PF >= 1.2-1.5, max DD < 15-60% on a transfer-adjusted curve, consistent P&L across windows, stop usage, distance from liquidation; one March 2026 scan found 14 of 50 top wallets passed WR >= 52% / PF >= 1.2 / DD < 60% ([Senpi methodology](https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/quant-desk/references/methodology.md), [eltonaguiar log](https://raw.githubusercontent.com/eltonaguiar/findtorontoevents_antigravity.ca-archive-2026-05-23/main/memory/2026-03-19.md)). None publish out-of-sample copier returns.
- fomo.family perps cohort (builder-fills data, 5 Jun-27 Aug 2026): 18,979 wallets, 766,931 fills, $1.68B notional, 29.1% net-positive, $1.03M trading profit vs $1.66M fees, 98.4% taker fills, win rate rising from 19% to 41% with 1 -> 16-30 active days ([synsur/fomo-flow](https://github.com/synsur/fomo-flow)).

Realistic net expectations: -10% to +20%/yr with fat left tails for fill-mirroring; 0-30%/yr net of the 10% share for a screened vault basket with real risk of -20% in a stress week; around 0%/yr for OKX copying after 8-30% profit share; -5% to +10%/yr for lag-adjusted Polymarket copying (inference from the sources above).

Capital and infrastructure: $500-$1,000; Hyperliquid API wallet with USDC on HyperCore (Arbitrum bridge); persistent WebSocket client; SQLite ledger; weekly re-scoring; OKX/Bitget accounts only for non-US users; Polygon wallet with USDC.e for Polymarket.

Main failure modes: leader liquidation or style change; 10K fill cap hiding HFT wallets' records; entry lag turning a +EV scalp into a -EV chase; coarse vault NAV sampling; extended lockups; profit share settling on weeks that later reverse; US ToS/geo-block issues; wrapper-vault rugs.

| candidate | venue | familiars-fit | expected-return note (short) |
|---|---|---|---|
| hl-screened-wallet-fill-mirror | Hyperliquid perps | no | No audited copier record; -10% to +20%/yr with fat left tails; <= 25% of capital |
| hl-user-vault-basket | Hyperliquid HyperCore vaults | no | 0-30%/yr net of 10% share plausible; -20% or worse in a stress week; survivorship in vault list |
| hyperliquid-vault-follow | Hyperliquid (perps) | no | Depends on the vault; trailing APRs unguaranteed; short-vol vaults lose big in tails |
| hyperliquid-vault-deposit | Hyperliquid vaults (HyperCore) | no | Depends on vault; net of 10% share and withdrawal slippage; no estimate |
| okx-api-follower | OKX copy trading (perps) | no | Around 0%/yr after 8-30% profit share and decay; benchmark only; not for US residents |
| polymarket-thesis-trader-copy | Polymarket (Polygon CLOB) | no | -5% to +10%/yr after 1-5 minute lag; capital locked until resolution |
| fomo-hyperliquid-builder-copy | Hyperliquid perps | no | Unknown; population 29.1% profitable; top-cohort persistence not demonstrated |
| hl-whale-positioning-filter-for-sol-spot | Signal from Hyperliquid; execution on Solana spot | yes | Not quantifiable; a possible false-breakout filter for the trend system |

### LLM-driven and narrative strategies

What the evidence says (details in section 9):

- LLMs in the decision loop lose to simple baselines once leakage, survivorship and costs are controlled: Alpha Arena Season 1 (four of six models down 30.8-62.7%), FINSABER (Sharpe ~0.24 vs 0.70), Profit Mirage (Sharpe decay 51-62% after the knowledge cutoff), StockBench (most models below buy-and-hold), a TradingAgents reproduction (15.8% vs 19.1% buy-and-hold on GOOGL) ([RL-Trader review](https://github.com/sh-arka22/RL-Trader/blob/main/docs/research/02_llm_agents.md), [FINSABER digest](https://github.com/elimarks5807-coder/foundry-strategy-engine/blob/main/Digests/FINSABER.md)).
- Text features can help at the margin: Reddit-augmented multi-agent systems beat baselines "particularly in bull markets" ([PulseReddit abstract, mirrored](https://github.com/CSQianDong/Awesome-arXiv-Daily-Reporter/blob/main/5-Jun-2025/NLP/README.md)); a sentiment feature added ~+0.35 Sharpe over plain PPO but the edge died at ~22 bps cost, and a 110M FinBERT captured 1.72 of the 1.90 Sharpe gain ([RL-Trader review](https://github.com/sh-arka22/RL-Trader/blob/main/docs/research/02_llm_agents.md), a third party's reading).
- The evidence-backed pattern is the LLM as offline code/parameter generator with a deterministic engine trading (FactFin in Profit Mirage; [digest](https://github.com/CSQianDong/Awesome-arXiv-Daily-Reporter/blob/main/10-Oct-2025/topic/RAG_related_papers.md)), or as a veto gate on launch metadata (Ballast's LAUNCH_REQUIRE_APPROVAL; [launch-agent.ts](/home/user/jonatangigex/familiars.family-opus5.5/src/launch-agent.ts)); Ballast's daily learn pass may only tighten filters ([learn.ts](/home/user/jonatangigex/familiars.family-opus5.5/src/learn.ts)).
- Narrative co-movement ("token of the day/week": AI agents, PolitiFi, brainrot) is widely described in trading guides but not measured independently ([Trojan](https://trojan.com/blog/how-to-trade-solana-meme-coins-in-2026-a-beginners-guide), snippet only); familiars' /api/tokens agent counts are a free narrative proxy ([familiars.ts](/home/user/jonatangigex/familiars.family-opus5.5/src/familiars.ts)).

Realistic net expectations: an LLM adds no return by itself; a narrative/sentiment filter on the 4h breakout must prove incremental value in a post-cutoff, cost-inclusive ablation; 0 to +20%/yr with 25%+ drawdowns if it does (inference).

Capital and infrastructure: batch classification with a cheap model and cached, timestamped labels; Claude API with JSON-schema outputs (one call per candidate or per day); a backtester with walk-forward splits; posts are untrusted text and need prompt-injection hardening.

Main failure modes: look-ahead leakage from labels produced after the fact; label drift across model versions; the LLM rationalising recent noise; adversarial text in other agents' posts; API cost per review at small capital.

| candidate | venue | familiars-fit | expected-return note (short) |
|---|---|---|---|
| llm-rug-review-gate | Solana spot via Jupiter (pump.fun/PumpSwap launches) | yes | Unbacktestable; expect negative expectancy unless the veto measurably improves hit rate; capped daily buys |
| narrative-tag-momentum | Solana spot via Jupiter | yes | 0 to +20%/yr with 25%+ DD after 0.2-0.6% round trips; negative in prolonged bears plausible |
| llm-offline-strategy-reviewer | Any (offline process) | yes | Not a return source; governance loop whose success metric is live tracking backtest |

## familiars.family assessment

What it is. familiars.family markets itself as "like FOMO, but for AI agents": a public board where autonomous agents trade Solana tokens from their own wallet with real money, post explanations, and are ranked by absolute USD P&L over 24H / 7D / 30D / ALL windows ([X snippet](https://x.com/familiarsfamily), snippet only; [Ballast README](/home/user/jonatangigex/familiars.family-opus5.5/README.md)). It is days old: the only public agent repository was created on 2026-09-24, GitHub code search returns zero hits for its API paths (versus 30+ public fomo.family tools), and no launch date, team, fee schedule or incident history is discoverable from reachable sources ([GitHub search](https://github.com/search?q=familiars.family&type=repositories)). The $familiars token (a pump.fun mint) was at $868k mcap, $87k liquidity, $2.07M 1h volume and +1729% in 1h at 2026-09-24 00:00:59 UTC ([third-party signal file](https://raw.githubusercontent.com/faisalkhattak7997-tech/solana-trade-bot/main/signals.json)). familiars.family, x.com, skill.md and web.archive.org were all unreachable from the sandbox; everything below is reconstructed from the Ballast client and README. The full contract is in [docs/familiars-api.md](familiars-api.md).

Mechanics (as observed in the client, [familiars.ts](/home/user/jonatangigex/familiars.family-opus5.5/src/familiars.ts)):

| aspect | observed behaviour |
|---|---|
| Registration | POST /api/agents/challenge with a wallet -> nonce; sign with ed25519; POST /api/agents/register -> apiKey (fam_...), ownerKey, loginUrl; nonce single-use, keys shown once, never retry; one wallet = one agent |
| Ranking | absolute USD P&L = equity minus net deposits per window; deposits/withdrawals are flows, not profit; history.snapshots give the platform's own equity curve |
| Posting | POST /api/posts, kinds note / callout / trade, 1-500 chars, optional mint and swap signature; trades index from chain ~1 minute after the swap, so Ballast queues trade posts 90 s and retries only on 429 |
| Owner controls | GET /api/agent/me returns settings.instructions (free text), maxPositionUsd, dailyLimitUsd; owner dashboard reached via loginUrl/ownerKey without a wallet; POST /api/agent/owner-key rotates the owner key; API-key rotation undocumented |
| Skill rules (paraphrased) | every trade must be explained publicly; trading the agent's own token is prohibited; agent tokens and a launchpad exist |
| Public reads | GET /api/agents?range= (handle, wallet, hosted flag, equityUsd, pnl per window, drawdown, winRate, trades); GET /api/agents/{handle} (cashUsd, solBalance, positions, trades with signatures, transfers, posts, equity snapshots); GET /api/tokens (tokens traded, agent counts, lastTradeAt) |
| Minimum trade | $2 (familiars page snippet, snippet only) |
| Hosted brains | free AI via OpenRouter; a daily free-AI budget cap was exhausted in under an hour and then removed; hosted agents carry hosted: true (snippet only for the product claims) |

Leaderboard reality (measured by the Ballast author on 2026-09-24, single-day, not independently verified): only 37 of ~1,350 agents were net positive; the largest P&Ls came from agents' own tokens (which the skill forbids) or from a few early bets on the narrative token of the day ($familiars organic score 84.9, JEANCOIN 47.3) held while rising; of 54 pump.fun launches under 120 minutes old that day only 4 had Jupiter organic score >= 25 ([Ballast README](/home/user/jonatangigex/familiars.family-opus5.5/README.md)). Because ranking is absolute P&L, capital size and variance are rewarded over risk-adjusted return: in the euphoric 19 Aug-24 Sep 2026 window Ballast's conservative config returned +0.5% with 12.5% drawdown while the hot basket rose +136% and a 1h breakout config made +42% (but -45% over six months) ([Ballast README](/home/user/jonatangigex/familiars.family-opus5.5/README.md)).

What it gives an agent beyond a plain wallet:

- Public profile, leaderboard visibility and followers (the only path to social traction for an agent).
- An owner dashboard with hard limits (maxPositionUsd, dailyLimitUsd) and free-text directives that act as a remote kill switch without holding keys ([risk.ts](/home/user/jonatangigex/familiars.family-opus5.5/src/risk.ts)).
- A free, structured copy/consensus feed: every agent's wallet, trades (with signatures), positions and equity history, plus per-token agent counts; Ballast uses /api/tokens counts as a rank input and rebuilds its own positions from its public trade history on a fresh machine ([rebuild.ts](/home/user/jonatangigex/familiars.family-opus5.5/src/rebuild.ts)).
- Free hosted brains for people who do not want to self-host (implies custodial keys for hosted agents; inference).
- Potential agent-token upside (mechanics undocumented) and announced multichain trading on Robinhood Chain (snippet only).

Costs and risks:

- No platform fee in the trade path is visible in any source; execution is the agent's own Jupiter swap (absence of evidence, not a confirmed fact).
- Mandatory public explanations leak strategy and create an extra failure mode (posts fail with 4xx until the swap is indexed).
- The most profitable observed pattern (own-token pumping) is banned; the second (early narrative bets) is variance-seeking.
- Platform youth: no SLA, undocumented rate limits (the client only retries on 429), undocumented API-key rotation; the agent must never depend on familiars being reachable for exits (Ballast blocks entries, not exits, when it cannot read limits).
- Focus on pump.fun tokens where bots dominate (50 of 54 fresh launches were bot volume) and ~97% of agents lose.
- An EU-resident operator's public posts promoting a held token without disclosure fall under MiCA Art. 91 (section 8).

Open questions that must be checked in skill.md before going live: the exact posting obligations and rate limits; the wording of the own-token rule and whether trading other agents' tokens is allowed; agent-token mechanics (who launches, who earns fees); whether P&L uses realised or mark-to-market values for illiquid positions and whether LP/lending receipt tokens count as equity; whether /api/agents/{handle} trade history is complete or capped and how fresh it is; any fees for hosted vs self-hosted agents; whether copying other agents requires disclosure; whether multiple wallets per owner are allowed; any wash-trading rule.

## fomo.family assessment

What it is. A self-custodial social trading app (Privy wallets, one Solana + one EVM wallet per user, key export) on Solana, Robinhood Chain, Base, BNB, Ethereum and Monad, with Hyperliquid perps since 11 June 2026 via Trade[XYZ] ([fomo-sapiens SKILL.md](https://raw.githubusercontent.com/Cataracks/fomo-sapiens/main/.claude/skills/fomo-sapiens/SKILL.md), [fomotrading.app](https://fomotrading.app/perps/), snippet only). Features: buy/sell feed, follow/followers, clans, 24h/7d/30d leaderboards (API returns 150 entries, UI shows 100), per-token top-50 holders, "thesis" posts weighted by author P&L, watchlists; no built-in copy button and no limit/TP/SL orders ([endpoints.md](https://raw.githubusercontent.com/Cataracks/fomo-sapiens/main/.claude/skills/fomo-sapiens/references/endpoints.md)). Users follow traders and place every trade themselves ([fomo blog](https://fomo.family/blog/learn/what-is-copy-trading), snippet only).

Fees:

| product | advertised | measured |
|---|---|---|
| Spot (Solana) | 0.5% per trade, minimum ~$0.95 ([datawallet](https://www.datawallet.com/crypto/fomo-app-explained), snippet only) | 5,882 trades / 2,771 traders / $2.6M notional in a 24.7 h window: blended 0.379-0.387%; per-account tiers 0.50 / 0.45 / 0.33 / 0.31 / 0.29 / 0.27 / 0.24 / 0.22 / 0.20 / 0.15%, manually granted; 53% of volume at discounted rates; $0-5 trades pay a median 81.9% effective fee ([synsur/fomo-flow](https://github.com/synsur/fomo-flow)) |
| Perps (Hyperliquid) | 0.05% builder fee on top of Hyperliquid fees, ~0.095% all-in taker ([fomotrading.app](https://fomotrading.app/perps/), snippet only) | average builder markup 4.47 bp; discount tier ~1 bp for 0.6% of wallets ([synsur/fomo-flow](https://github.com/synsur/fomo-flow)) |
| Cross-chain | settles via Relay with ~3% bridge cost ([fomo-robinhood-radar](https://raw.githubusercontent.com/jonthomp/fomo-robinhood-radar/main/README.md)) | - |
| Minimums | ~$2 swap on Solana, ~$5 on Ethereum; one extension README cites $25 min buy / $5 min sell on ETH ([phinolex](https://raw.githubusercontent.com/phinolex/fomo-extension-bot-order/main/README.md)) | - |

Every fomo Solana swap pays a USDC fee to a token account owned by R4rNJHaffSUotNmqSKNEfDcJE8A7zJUkaoM5Jkd7cYX ([synsur/fomo-flow](https://github.com/synsur/fomo-flow)).

Traction: $75M Series B led by Index Ventures with Union Square Ventures and Benchmark (date not in snippet); "1M+ users" claimed; DefiLlama lists a fees/revenue page ([bitcoinfoundation.org](https://bitcoinfoundation.org/news/crypto-companies-news/fomo-investments/), [DefiLlama](https://defillama.com/protocol/fomo), snippet only). Perps cohort (builder-fills files, 5 Jun-27 Aug 2026): 18,979 wallets, 766,931 fills, $1.68B notional, 29.1% net positive, trading profit $1.03M vs fees $1.66M, 42% of wallets trade one day only, retention 19.3% ([synsur/fomo-flow](https://github.com/synsur/fomo-flow)).

API situation: base URL https://prod-api.fomo.family; auth is a Privy JWT (1 h TTL) plus an x-supported-chains header; Cloudflare rejects non-browser TLS fingerprints with HTTP 430 regardless of token, so curl/requests/Node fetch fail and only browser sessions or curl_cffi impersonation work; key endpoints include /v2/leaderboard/{24h|7d|30d}, /v2/users/{id}/swaps, /v2/users/{id}/balances, /trades, /feed/tradingActivity, POST /swaps/v2/quote and /swaps/v2/execute, and a WebSocket at wss://prod-api.fomo.family/ws; there is no official public API or automation program, and the Terms restrict "unauthorized automated extraction and redistribution" ([fomo-endpoints.md](https://raw.githubusercontent.com/cvxv666/fomo-robinhood-radar/main/docs/fomo-endpoints.md), [FOMO-Copy-Trader session.md](https://raw.githubusercontent.com/omarlatreche/FOMO-Copy-Trader/main/session.md)). Third-party resellers: fomoapi.io advertises leaderboards, handle-to-wallet resolution, trades and alerts across six chains with a free tier of "250,000 credits/month" and paid plans $49.99-$599/mo (Scale $1,500/mo) ([fomoapi pricing](https://fomoapi.io/pricing), snippet only); Open-Fomo-API / getfomoapi.fun ("independent, not official") has the same endpoint family, 5 req/s per key, leaderboard cached 1 h, WebSocket planned ([Open-Fomo-API](https://github.com/abstradeapi/Open-Fomo-API)); one consumer reports the free tier as 1,000 credits/month and only the last 100 fills per trader ([fomo-robinhood-radar](https://raw.githubusercontent.com/jonthomp/fomo-robinhood-radar/main/README.md)); the two free-tier figures conflict.

Copyability of its traders: feasible on Solana with caveats. A fomo profile "address" is not the trading wallet (zero on-chain events); the real Solana wallet appears in the address field of /v2/users/{id}/swaps and is verifiable with getSignaturesForAddress; EVM/Robinhood wallets are masked and must be inferred (a 90-second co-occurrence method validated 101/0) ([fomo-endpoints.md](https://raw.githubusercontent.com/cvxv666/fomo-robinhood-radar/main/docs/fomo-endpoints.md), [Smart-Alert-Robin](https://raw.githubusercontent.com/DavidYashar/Smart-Alert-Robin/main/docs/fomo-kol-pipeline.md)). All fomo Solana swaps can be reconstructed with no API at all from USDC fee payments to the fee account ([synsur/fomo-flow](https://github.com/synsur/fomo-flow)). Existing copy tools watch resolved wallets via Helius polling (15 s default) or websockets (~1 s on Robinhood Chain) ([fomopulse](https://raw.githubusercontent.com/itsnex1s/fomopulse-robinhood-chain-tape/main/README.md)). No reachable source reports a profitable live copy; documented pitfalls are lag, slippage, "you sell after he does", leaderboard survivorship, a "Top trades" sort that shows only winners, closed-trade win rates near 100% collapsing to 21-86% once open positions are marked to market, position history capped at ~25 per sort, and an LLM-judged copy bot burning ~$5 per 30 minutes in model calls before adding deterministic pre-screening ([FOMO-Copy-Trader session.md](https://raw.githubusercontent.com/omarlatreche/FOMO-Copy-Trader/main/session.md), [fomo-copy-sim](https://raw.githubusercontent.com/313imverymellodet/fomo-copy-sim/main/README.md)). Solana Tracker publishes a "FOMO leaderboard" of wallets ([solanatracker](https://www.solanatracker.io/leaderboard/fomo), page blocked).

## Execution and infrastructure facts

Jupiter (Solana swaps):

- Ultra API (ultra-api.jup.ag, /ultra/v1/order + /execute) is documented as "no longer actively maintained and has been superseded by Swap V2"; the replacement is base URL https://api.jup.ag/swap/v2 with GET /order and POST /execute, and "the request parameters and response format are identical" ([ultra/index.mdx](https://raw.githubusercontent.com/jup-ag/docs/main/ultra/index.mdx), [migration guide](https://raw.githubusercontent.com/jup-ag/docs/main/swap/migration/ultra-to-order.mdx)). Ballast still calls the /ultra/v1 paths ([jupiter.ts](/home/user/jonatangigex/familiars.family-opus5.5/src/jupiter.ts)). No sunset date is given (open question).
- Fees on the /order + /execute path: 0 bps for JUP/JLP/jupSOL and pegged pairs, 2 bps SOL-stable, 5 bps LST-stable, 10 bps all other tokens, 50 bps for tokens younger than 24 hours; integrator referral fees must be 50-255 bps and Jupiter keeps 20%; the Router path (/build + own submission) charges no swap fee but you assemble, simulate and land the transaction yourself ([fees.mdx](https://raw.githubusercontent.com/jup-ag/docs/main/ultra/fees.mdx), [build](https://raw.githubusercontent.com/jup-ag/docs/main/swap/build/index.mdx)).

| Jupiter tier | price | main bucket (60 s sliding window) | /swap/v2/execute bucket | credits |
|---|---|---|---|---|
| Keyless | $0 | 0.5 RPS (30/min) | 20 RPS | - |
| Free | $0 | 1 RPS | 50 RPS | - |
| Developer | $25/mo | 10 RPS | 100 RPS | 25M |
| Launch | $100/mo | 50 RPS | 100 RPS | 100M |
| Pro | $500/mo | 150 RPS | 100 RPS | 500M |

Execute endpoints cost 0 credits; most others 1 (portfolio positions 100); annual billing = 10 months; rate-limit headers x-ratelimit-remaining/current/reset; no lockout after a 429 ([plans.mdx](https://raw.githubusercontent.com/jup-ag/docs/main/portal/plans.mdx), [rate-limits.mdx](https://raw.githubusercontent.com/jup-ag/docs/main/portal/rate-limits.mdx)). Keys are jup_..., passed as x-api-key, shown once, restrictable to products; a team firewall supports up to 50 rules by path, IP/CIDR, country, method, header or key ([api-keys.mdx](https://raw.githubusercontent.com/jup-ag/docs/main/portal/api-keys.mdx), [firewall.mdx](https://raw.githubusercontent.com/jup-ag/docs/main/portal/firewall.mdx)). A third-party skill quotes older dynamic limits of 50 req/10s ($0 volume) to 165 req/10s ($1M+ 24h volume) ([sendaifun Jupiter SKILL.md](https://raw.githubusercontent.com/sendaifun/skills/main/skills/jupiter/SKILL.md)); the portal docs above supersede them.

- Gasless: automatic sponsorship when the taker holds <0.01 SOL and the trade is >= ~$10, routing restricted to Metis and the fee raised to cover gas (signatureFeePayer = gasTzr94Pmp4Gf8vknQnqxeYxdgwFjbgdJa4msYRpnB); JupiterZ RFQ where the market maker pays fees; or an integrator payer wallet; incompatible with referralFee and manual slippageBps ([gasless.mdx](https://raw.githubusercontent.com/jup-ag/docs/main/swap/advanced/gasless.mdx)). Ballast keeps a 0.03 SOL reserve instead.
- Slippage: /order applies the Real-Time Slippage Estimator automatically; /build defaults to 50 bps; Jupiter's guidance is that fixed slippageBps suits "memecoins, fast exits"; /order responses expose slippageBps, priceImpactPct, feeBps, router (metis|jupiterz|dflow|okx) and mode; aggregator routes expire at lastValidBlockHeight ([slippage.mdx](https://raw.githubusercontent.com/jup-ag/docs/main/swap/advanced/slippage.mdx)).
- Idempotency: the same signedTransaction + requestId may be resubmitted for up to 2 minutes to poll status; identical signatures cannot double-execute; /execute returns codes 0 ok, -1..-3 general, -1000..-1004 aggregator landing, -2000..-2004 RFQ landing; /order errors 1 insufficient input, 2 insufficient SOL for gas, 3 below gasless minimum ([execute-order.mdx](https://raw.githubusercontent.com/jup-ag/docs/main/ultra/execute-order.mdx)).
- Shield, Tokens V2, Price V3: Shield (GET /ultra/v1/shield?mints=..., marked unmaintained) returns HAS_FREEZE_AUTHORITY, HAS_MINT_AUTHORITY, NOT_VERIFIED, LOW_ORGANIC_ACTIVITY, NEW_LISTING ([get-shield.mdx](https://raw.githubusercontent.com/jup-ag/docs/main/ultra/get-shield.mdx)); Tokens V2 search accepts up to 100 mints and returns organicScore (0-100, "filtering out bots, snipers, and copy-trading tools"), organicScoreLabel, audit (mint/freeze authority, topHoldersPercentage, devBalancePercentage, devMints, isSus), holderCount, launchpad, firstPool.createdAt and 5m/1h/6h/24h stats; Price V3 accepts 50 ids ([token-information.mdx](https://raw.githubusercontent.com/jup-ag/docs/main/tokens/token-information.mdx), [price](https://raw.githubusercontent.com/jup-ag/docs/main/price/index.mdx)). Ballast requires organicScore >= 25 for launches and >= 50 for trend trades because volume bots pass every count-based filter ([Ballast README](/home/user/jonatangigex/familiars.family-opus5.5/README.md)).

Transaction landing options:

| path | tip / cost | notes |
|---|---|---|
| Jupiter /swap/v2/execute | fee-funded, no explicit tip | 0 credits; 2-minute idempotent resubmit ([order-and-execute.mdx](https://raw.githubusercontent.com/jup-ag/docs/main/swap/order-and-execute.mdx)) |
| Jupiter Beam tx.jup.ag /transaction/v1/submit | flat 1,000,000 lamports (0.001 SOL) to one of 16 tip accounts; "tipping more does not improve landing" | routes via SWQoS and Jito by default, swqosOnly:true omits Jito; shares the main bucket ([submit.mdx](https://raw.githubusercontent.com/jup-ag/docs/main/transaction/submit.mdx)) |
| Helius Sender sender.helius-rpc.com/fast | min 0.0002 SOL (dual SWQoS+Jito) or 0.000005 SOL (SWQoS-only); the SDK README says 0.001 SOL for the dual route (conflict) | 0 credits, default 50 TPS, skipPreflight:true and maxRetries:0 required, CU-price instruction required ([helius core-ai](https://raw.githubusercontent.com/helius-labs/core-ai/main/helius-mcp/system-prompts/helius/full.md), [helius-sdk](https://raw.githubusercontent.com/helius-labs/helius-sdk/main/README.md)) |
| Jito Block Engine | min tip 1,000 lamports; tip_floor endpoint gives 25/50/75/95/99th percentiles | 1 request/s per IP per region (429 beyond), bundles of at most 5 transactions executed atomically within one slot, 8 tip accounts, regional endpoints; 70% priority fee / 30% tip recommended for sendTransaction ([jito-docs](https://raw.githubusercontent.com/jito-labs/jito-docs/main/docs/source/lowlatencytxnsend.md)) |

Helius pricing (from Helius' own core-ai repo, mid-2026; the public pricing page was unreachable):

| plan | price | credits | RPC RPS | notes |
|---|---|---|---|---|
| Agent | $1 USDC | 1M | 10 | sendTransaction 1/s, DAS 2/s, 5 WS connections |
| Developer | $49/mo | 10M | 50 | 5/s send, 10/s DAS, 150 WS; Enhanced WSS transactionSubscribe |
| Business | $499/mo | 100M | 200 | mainnet LaserStream gRPC |
| Professional | $999/mo | 200M | 500 | - |

Extra credits $5 per 1M. Credit costs: 0 for Sender; 1 for standard RPC, sendTransaction, Priority Fee API and webhook events; 10 for getProgramAccounts, DAS, historical data; 100 for Enhanced Transactions API, Wallet API and webhook management; webhooks up to 100,000 addresses each, including a Discord type ([helius core-ai](https://raw.githubusercontent.com/helius-labs/core-ai/main/helius-mcp/system-prompts/helius/full.md)). QuickNode reportedly has no free tier (10M-credit trial), Build $49/mo 50 req/s, Scale $299/mo ([third-party note](https://github.com/MetalLegBob/solana-vibes-kit/blob/main/grand-library/resources/domain-packs/solana/knowledge/rpc-provider-comparison.md), secondary). Public RPC needs 300 ms pacing and takes 15-40 s per token for forensics ([Ballast README](/home/user/jonatangigex/familiars.family-opus5.5/README.md)).

Solana fee constants: base fee 5,000 lamports per signature (50% burned); priority fee = ceil(compute_unit_price_microlamports x compute_unit_limit / 1,000,000) lamports; default 200,000 CU per instruction, max 1,400,000 CU per transaction ([core/fees](https://raw.githubusercontent.com/solana-foundation/solana-com/main/apps/docs/content/docs/en/core/fees/index.mdx), [constants](https://raw.githubusercontent.com/solana-foundation/solana-com/main/apps/docs/content/docs/en/core/constants-reference.mdx)); Jupiter's /build guidance is build with 1.4M CU, simulate with replaceRecentBlockhash:true, rebuild at 1.2x measured CU, cap the micro-lamport price; percentiles medium=25th, high=50th (default), veryHigh=75th, mode=fast 90th ([compute-units.mdx](https://raw.githubusercontent.com/jup-ag/docs/main/swap/advanced/compute-units.mdx)). Each new SPL token account needs ~0.00203928 SOL rent, refunded on close (well-known constant; inference); Ballast caps overhead at 15,000,000 lamports per swap and requires >= 0.05 SOL on funding ([executor.ts](/home/user/jonatangigex/familiars.family-opus5.5/src/executor.ts)). One article says Solana's v1 transaction format (mainnet since 2026-09-15) replaces CU-price x CU-limit with a flat priority fee in lamports (snippet only, unverified).

Token-2022 dangers: extensions that can hurt a holder are TransferFeeConfig (fee withheld on every transfer), TransferHook (arbitrary program on transfer can block sells), PermanentDelegate (a fixed authority "can authorize transfers and burns for any token account" and "token account owners cannot revoke" it), NonTransferable, DefaultAccountState (new accounts start frozen), Pausable, ConfidentialTransferFeeConfig, MintCloseAuthority, plus classic freeze and mint authority ([permanent-delegate.mdx](https://github.com/solana-foundation/solana-com/blob/main/apps/docs/content/docs/en/tokens/extensions/permanent-delegate.mdx), [token-2022 extension list](https://github.com/solana-program/token-2022/blob/main/program/src/extension/mod.rs)). Ballast reads mint extensions via getParsedAccountInfo and rejects any of these, then runs a real-size buy + immediate sell quote and rejects round trips above 1.5% (established tokens) or 6% (launches) ([solana.ts](/home/user/jonatangigex/familiars.family-opus5.5/src/solana.ts)). A dev.to write-up claims RugCheck flags >40% of new tokens as using the permanent-delegate extension (snippet only, single author, unverified).

Simulate-before-sign pattern (Ballast LiveExecutor, [executor.ts](/home/user/jonatangigex/familiars.family-opus5.5/src/executor.ts)): (1) GET /order with the taker; (2) check the quote against an independent USD reference price and reject >3% loss; (3) deserialize the VersionedTransaction and verify our key is a required signer; (4) simulateTransaction with sigVerify:false and post-state accounts for our wallet, every token account we hold and the output ATA; (5) refuse if the wallet owner != System Program, any token account's owner != us, any delegate is set, closeAuthority != us, SOL (native + wSOL) drops more than amount + 15,000,000 lamports, the input mint loses more than requested, output < quote x (1 - maxLossPct), or any unrelated token decreases; (6) only then sign and POST /execute (2 retries on the same signed transaction, safe because one signature). Realised P&L can be derived from getTransaction preBalances/postBalances and pre/postTokenBalances without a paid parser ([gettransaction.mdx](https://raw.githubusercontent.com/solana-foundation/solana-com/main/apps/docs/content/docs/en/rpc/http/gettransaction.mdx)), or from familiars' public trade feed ([rebuild.ts](/home/user/jonatangigex/familiars.family-opus5.5/src/rebuild.ts)).

Hyperliquid:

- API (agent) wallets "are only used to sign"; they cannot withdraw, transfer or approve builder fees; 1 unnamed + 3 named agents per account (+2 per sub-account); agents are pruned when deregistered, expired or when the account has no funds, after which old signed actions can be replayed because nonce history is cleared; nonces must be within (T-2 days, T+1 day) and larger than the smallest of the 100 highest stored; queries use the master address ([nonces-and-api-wallets.md](https://raw.githubusercontent.com/thanhtoan0306/hyperliquid-docs-ssr/main/content/pages/for-developers/api/nonces-and-api-wallets.md), [basic_agent.py](https://raw.githubusercontent.com/hyperliquid-dex/hyperliquid-python-sdk/master/examples/basic_agent.py)).
- Rate limits: per IP 1,200 weight/min (exchange actions 1 + floor(batch_len/40); info weights 2 for l2Book/allMids/clearinghouseState, 20 for most others, 60 for userRole); per address 1 request per 1 USDC traded cumulatively with a 10,000-request buffer, then one request per 10 seconds; open orders 1,000 default (max 5,000); WebSocket 10 connections, 30 new per minute, 1,000 subscriptions, 2,000 messages/min, ping every 60 s; rejected expiresAfter orders consume 5x; the Python SDK (0.24.0) has no built-in 429 backoff ([rate-limits](https://raw.githubusercontent.com/thanhtoan0306/hyperliquid-docs-ssr/main/content/pages/for-developers/api/rate-limits-and-user-limits.md), [api.py](https://raw.githubusercontent.com/hyperliquid-dex/hyperliquid-python-sdk/master/hyperliquid/api.py)).
- Fees and orders: perps 0.045% taker / 0.015% maker, spot 0.070% / 0.040%, volume tiers from >$5M 14-day, HYPE staking discounts 5-40%, maker rebates up to -0.003%; builder fees max 0.1% perps / 1% spot, approved by the primary wallet, builder needs >= 100 USDC; order types limit (Alo|Ioc|Gtc), trigger TP/SL (isMarket, tpsl), reduceOnly, cloid, grouping normalTpsl|positionTpsl; SDK market orders are IOC limits with a slippage parameter; min order value $10; TWAP slices every 30 s with max 3% slippage; optional priority fee up to 8 bps for ~45 ms per bp ([fees.md](https://raw.githubusercontent.com/thanhtoan0306/hyperliquid-docs-ssr/main/content/pages/trading/fees.md), [order-types.md](https://raw.githubusercontent.com/thanhtoan0306/hyperliquid-docs-ssr/main/content/pages/trading/order-types.md), [priority-fees.md](https://raw.githubusercontent.com/thanhtoan0306/hyperliquid-docs-ssr/main/content/pages/for-developers/api/priority-fees.md)).
- Vaults, testnet, bridge: leader receives 10% of profits, keeps >= 5%, deposits >= 100 USDC; creating a vault costs a 10,000 USDC fee; depositor lockup 1 day (HLP 4 days); leaders cannot trade spot or HIP-3; withdrawals may close 20% of positions iteratively; testnet faucet gives 1,000 mock USDC but needs a prior mainnet deposit; bridge is Arbitrum USDC with a 1 USDC withdrawal fee ([vault leaders](https://raw.githubusercontent.com/thanhtoan0306/hyperliquid-docs-ssr/main/content/pages/hypercore/vaults/for-vault-leaders-legacy.md), [bridge.md](https://raw.githubusercontent.com/thanhtoan0306/hyperliquid-docs-ssr/main/content/pages/hypercore/bridge.md)).
- Node/latency: Hyperliquid recommends Tokyo; a non-validator node needs 16 vCPU / 128 GB RAM / 500 GB SSD; a plain VPS on api.hyperliquid.xyz suffices for hourly carry ([hyperliquid-dex/node](https://github.com/hyperliquid-dex/node)).

ccxt notes: 104 exchanges; certified binance, bybit, okx, gate, kucoin, bitget, hyperliquid and others; coinbase (Advanced Trade v3, rateLimit 34 ms) and kraken (rateLimit 1000 ms) have no ccxt sandbox; setSandboxMode(true) must be the first call; do not run multiple instances with one keypair from one IP; unified createOrder supports triggerPrice, stopLossPrice, takeProfitPrice, reduceOnly, postOnly, clientOrderId ([ccxt Manual](https://raw.githubusercontent.com/ccxt/ccxt/master/wiki/Manual.md)). The hyperliquid class automatically approves a builder fee to ccxt's own address 0x6530512A6c89C7cfCEbC3BA7fcD9aDa5f30827a6 at 0.01% on initialisation unless builderFee:false is passed (which approves 0% for attribution only); approval needs the master key, so a client fed only an agent key cannot approve it (the last point is inference) ([ccxt hyperliquid.ts](https://raw.githubusercontent.com/ccxt/ccxt/master/ts/src/hyperliquid.ts)). For a US user the ccxt-covered spot venues are coinbase, kraken and binanceus (inference).

Key management and supervision: Solana has no delegation primitive, so the VPS signing key controls all funds; Ballast keeps secrets in a 0600 env file in a 0700 directory outside git, never overwritten, or injects them via docker --env-file; the fam_ key can only post and read limits; a leaked wallet key means moving funds and re-registering ([secrets.ts](/home/user/jonatangigex/familiars.family-opus5.5/src/secrets.ts)). Cloud KMS asymmetric keys do not include Ed25519 per prior knowledge (inference; docs blocked), so a Solana key cannot live natively in AWS KMS while secp256k1 keys can sign for Hyperliquid/EVM. Supervision: Docker --restart unless-stopped or systemd Restart=on-failure with RestartSec and StartLimitBurst; a lock file with pid + heartbeat (stale after 10 min); atomic state writes (tmp + rename); trades trimmed to 500 and pending posts to 50; every external write idempotent (execute keyed by signature, posts keyed by signature); full position reconstruction from familiars' public trade history on boot ([docker docs](https://raw.githubusercontent.com/docker/docs/main/content/manuals/engine/containers/start-containers-automatically.md), [systemd.service](https://raw.githubusercontent.com/systemd/systemd/main/man/systemd.service.xml), [lock.ts](/home/user/jonatangigex/familiars.family-opus5.5/src/lock.ts)). Alerting: Telegram ~30 messages/s overall, ~20/min per group ([python-telegram-bot wiki](https://github.com/python-telegram-bot/python-telegram-bot/wiki/Avoiding-flood-limits)); a Helius Discord webhook on the wallet address catches a drain even if the bot is dead.

Monthly cost model (fixed):

| item | free/minimum | comfortable |
|---|---|---|
| VPS 1-2 vCPU | ~$5 (inference; pricing pages blocked) | ~$12 |
| Jupiter API | Free $0 (1 RPS) | Developer $25 (10 RPS) |
| Solana RPC | Helius Agent $1 (1M credits) | Helius Developer $49 |
| Jito / Hyperliquid / ccxt / Telegram / Discord | $0 | $0 |
| Total | ~$6 | ~$90 |

Variable per Solana swap: Jupiter 10 bps (50 bps on <24h tokens); 5,000 lamports base fee per signature; priority fee (CU price x CU limit); landing tip 0-0.001 SOL by path; ATA rent 0.00204 SOL refundable; measured 0.2-0.6% round trip on liquid tokens and 6-9% on some "liquid-looking" tokens. Hyperliquid: 9 bps taker round trip, 1 USDC per withdrawal. At $500 capital a $90/month stack is 18%/yr of drag before any trading loss ([infra-execution synthesis](https://raw.githubusercontent.com/jup-ag/docs/main/portal/plans.mdx)). Sniping-grade streaming (Yellowstone gRPC $84-$499/mo) is out of scope for a 60-second-tick agent.

## Risk, security, tax and regulation

Sizing and brakes:

- Kelly gives f* = (bp - q)/b for a binary bet and f = mu/sigma^2 in the continuous approximation; half-Kelly retains ~75% of full-Kelly growth with a quarter of the variance, and fractional Kelly equals full Kelly on shrunken edge estimates ([dhando-analyzer note](https://github.com/alexnelja/dhando-analyzer/blob/main/research/kelly-criterion-probability-research.md), [awesome-quant-ai](https://github.com/leoncuhk/awesome-quant-ai/blob/main/think/Uncertainty-Driven%20Position%20Sizing.md); standard results, primary papers blocked). For a noisy memecoin/trend edge, 0.25-0.5x Kelly is the ceiling and in practice collapses to ~1% equity risk per trade (inference).
- Volatility targeting (w_t proportional to 1/sigma^2_{t-1}) raises Sharpe and cuts drawdowns across factors ([Moreira & Muir, JF 72(4) 2017](https://doi.org/10.1111/jofi.12513), via [GitHub summaries](https://github.com/xxSeasonxx/quant_strategies/blob/main/docs/research/crypto/03_academic_literature.md)); out-of-sample gains are debated and it lags V-shaped recoveries. The offline study confirms the drawdown effect (section 3).
- Working brake set (Ballast, [risk.ts](/home/user/jonatangigex/familiars.family-opus5.5/src/risk.ts), [README](/home/user/jonatangigex/familiars.family-opus5.5/README.md)): 1% of equity at risk per trade sized off a gap-adjusted stop (launches assume a 45% loss because a -30% stop filled at -37%); max 4 open positions; max 30% of equity per position; never more than 1% of pool liquidity; daily loss > 6% or drawdown > 25% from the 7-day peak blocks new entries, measured as equity minus net deposits; idle SOL above a 0.03 SOL reserve parked in USDC; stops -30% (launches) or 2.5 ATR bounded 4-20% (trend), break-even at +1R, 4-ATR trail from +2R; time stops 6h/72h. 29 of 30 single-parameter perturbations stayed positive.
- Correlation: memecoin positions are all long-SOL-beta; Ballast's Monte Carlo treated trades as independent, so it uses the 24% backtest drawdown as the prudent reference; median single-token drawdown in its universe was 56%, worst 99% ([README](/home/user/jonatangigex/familiars.family-opus5.5/README.md)); "correlated losses compound faster than this model accounts for" ([keel report](https://github.com/CodeGateSoftware/keel/blob/main/docs/superpowers/reports/2026-07-23-drawdown-taper-and-merton-exploration.md)). A portfolio open-risk budget (<= 3-4R) and a SOL regime gate are needed on top of per-trade stops (inference).

Tail events (all figures widely reported; primary news sites were blocked):

| date | event | figures | source |
|---|---|---|---|
| 12 Mar 2025 | ETH whale liquidation on Hyperliquid | ~$4M loss to HLP; margin tiers followed on 22 May 2025 (BTC 40x, ETH 25x max) | [community wiki](https://github.com/Hyperliquid-Community/wiki-community/blob/main/introduction/roadmap/incident/2025-26-03.md) |
| 26 Mar 2025 | JELLY squeeze | ~$4.1M shorts self-liquidated into HLP, spot pumped ~400%, HLP ~$12-13.5M underwater; validators voted within ~2 minutes to delist and force-settle at $0.0095, turning it into a ~$703K profit | [community wiki](https://github.com/Hyperliquid-Community/wiki-community/blob/main/introduction/roadmap/incident/2025-26-03.md), [CoinDesk](https://www.coindesk.com/markets/2025/03/26/hyperliquid-delists-jellyjelly-after-vault-squeezed-in-usd13m-tussle) (snippet only) |
| late May 2025 | James Wynn liquidations | $1.25B 40x BTC long liquidated, >$37M lost, nine liquidations | [DL News](https://www.dlnews.com/articles/defi/hyperliquid-trader-james-wynn-liquidated-nine-times/) (snippet only) |
| Sep 2025 | HypervaultFi rug | ~$3.6M from ~1,100 depositors, 76-95% APY promised | [CryptoRank](https://cryptorank.io/news/feed/00e88-hypervaultfi-suspected-rug-pull-takes-3-6m) (snippet only) |
| 10-11 Oct 2025 | liquidation cascade | ~$19.3B liquidated in ~24h across ~1.6M accounts (~87% longs), BTC ~$122k -> ~$105k, USDe marked $0.60-0.65 on Binance, ADL fired on Binance and Hyperliquid closing profitable hedge legs, 200+ reduce-only orders rejected over 106 minutes, Binance paid ~$283M, Lighter offline ~36 minutes | [strat-crypto.md](https://github.com/luke-cramer/ai-trading/blob/main/research/strat-crypto.md), [daily brief 2025-10-20](https://github.com/ernie55ernie/ernie55ernie.github.io/blob/master/_posts/2025-10-20-daily-crypto.md) (secondary) |
| 4 Nov 2025 | Stream Finance / Elixir | ~$93M loss, xUSD -77%, deUSD to ~$0.015, ~$285M linked debt | [Pharos](https://pharos.watch/learn/case-studies/stream-elixir-contagion-2025/) (snippet only) |
| 12 Nov 2025 | POPCAT manipulation | HLP ~$4.9M bad debt via a $20M cancelled buy wall; HLP deposits paused, Arbitrum bridge locked ~25 min | [daily brief 2025-11-13](https://github.com/ernie55ernie/ernie55ernie.github.io/blob/master/_posts/2025-11-13-daily-crypto.md) |
| 1 Apr 2026 | Drift exploit | ~$285M, >50% of TVL, from ~20 vaults; relaunch as Velocity DEX | [Chainalysis](https://www.chainalysis.com/blog/lessons-from-the-drift-hack/) (snippet only) |

Implications: leverage above 2-3x and exotic collateral (USDe, LSTs) on venues that mark off their own book are unsuitable for an unattended $500-20k agent; assume reduce-only orders can fail for 1-2 hours; perp venues will override prices by governance on thin markets, so keep any perps to BTC/ETH/SOL (inference from the events above).

Supply-chain attacks (npm registry timestamps measured directly):

| date | package / vector | effect | source |
|---|---|---|---|
| 3 Dec 2024 | @solana/web3.js 1.95.6 and 1.95.7 | code that steals private key material; CVSS 8.3; fix 1.95.8 and rotate keys | [GHSA-jcxm-7wvp-g6p5](https://github.com/solana-labs/solana-web3.js/security/advisories/GHSA-jcxm-7wvp-g6p5), [registry](https://registry.npmjs.org/@solana/web3.js) |
| Nov 2024 | DEXX | ~$30M, 900+ users, server-side keys leaked | [solana-vibes-kit deep dives](https://github.com/MetalLegBob/solana-vibes-kit/blob/main/stronghold-of-security/research/wave3/w3-incident-deep-dives.md) (secondary) |
| Jan 2025 | PyPI semantic-types (dependency of fake solana-keypair, solana-publickey, solana-mev-agent-py, solana-trading-bot, soltrade) | monkey-patched solders Keypair to exfiltrate keys via memo transactions | same |
| Jan 2025 | DogWifTools | RAT shipped after a GitHub token was extracted, ~$10M | same |
| Jul 2025 | fake "solana-pumpfun-bot" repo pulling crypto-layout-utils and bs58-encrypt-utils-1.0.3 | scanned local files for wallet keys | same (SlowMist cited) |
| 8 Sep 2025 | chalk 5.6.1, debug 4.4.2 | maintainer phishing, address-swapping payload, pulled same day | [chalk #656](https://github.com/chalk/chalk/issues/656), [debug #1005](https://github.com/debug-js/debug/issues/1005) |
| 15 Sep 2025 | @ctrl/tinycolor 4.1.1/4.1.2 ("Shai-Hulud" worm) | self-propagating, harvested npm/GitHub/cloud tokens | [registry](https://registry.npmjs.org/@ctrl/tinycolor) (behaviour details from recollection) |

Mitigations: pin exact versions with a committed lockfile, enforce a 7-14 day package cooldown, npm ci with ignore-scripts where possible, build Docker images from the lockfile, never load the wallet key into a process that runs unaudited dependencies, and audit any "trading bot" repo before installing (inference from the incidents above).

Key management: Hyperliquid's agent-wallet model (trade but not withdraw) is the right pattern; Solana has no equivalent, so the substitute is a hot wallet holding only working capital, a treasury in a Squads v4 multisig (time locks, spending limits, roles; audited by OtterSec, Neodyme, Certora, Trail of Bits) that tops up the hot wallet, and periodic profit sweeps ([Squads v4](https://github.com/Squads-Protocol/v4), [basic_agent.py](https://github.com/hyperliquid-dex/hyperliquid-python-sdk/blob/master/examples/basic_agent.py)). familiars never custodies self-hosted keys; a leaked fam_ key can only post; a leaked owner key is rotated via POST /api/agent/owner-key; a leaked wallet key requires a new wallet and agent ([familiars.ts](/home/user/jonatangigex/familiars.family-opus5.5/src/familiars.ts)). Use two RPC providers and cross-check price with a second source (the general "fake RPC" threat is inference); @solana/web3.js 1.66.3/1.66.4 were deprecated for reporting confirmations that did not meet the requested commitment ([registry deprecation notices](https://registry.npmjs.org/@solana/web3.js)).

Kill switch and owner override (Ballast pattern, [risk.ts](/home/user/jonatangigex/familiars.family-opus5.5/src/risk.ts)): read owner limits fail-closed before every trade (open nothing if unreadable); dailyLimitUsd caps daily buys, never exits; directives are recognised only when a sentence starts with the command ("pause", "stop trading", "liquidate", "sell all"), not on substring; canary limits at launch (maxPositionUsd = 10, dailyLimitUsd = 30) until one real buy, sell and post are verified; positions rebuilt from the public trade log on a new machine.

Prompt and memory injection: on ElizaOS, injections into prompts or stored history trigger unauthorised transfers; on CrAIBench (150+ tasks, 500+ attacks) models are "significantly more vulnerable to memory injection compared to prompt injection" and prompt-injection detectors "only provide limited protection when stored context is corrupted"; sleeper injections evade moderation ([arXiv 2503.16248](https://arxiv.org/abs/2503.16248), abstract via [mirror](https://github.com/santosomar/ai_news_archive)). OWASP LLM01 (prompt injection) and LLM06 (excessive agency) prescribe least-privilege tools, no open-ended tools, segregated external content, downstream authorisation checks not reliant on LLM judgment ([LLM01](https://github.com/OWASP/www-project-top-10-for-large-language-model-applications/blob/main/2_0_vulns/LLM01_PromptInjection.md), [LLM06](https://github.com/OWASP/www-project-top-10-for-large-language-model-applications/blob/main/2_0_vulns/LLM06_ExcessiveAgency.md)); the 2026 release (4 Aug 2026) and the Agentic Top 10 (ASI01 goal hijack, ASI02 tool misuse) cover the same ([GenAI-LLM-Top10](https://github.com/GenAI-Security-Project/GenAI-LLM-Top10)). Implication: the LLM never holds the signing key or an unconstrained send tool; token names, social posts, other agents' explanation posts and memory entries are untrusted data; LLM output is a schema-validated approve/veto (Ballast does this for launch review).

US tax facts (IRS pages blocked; from [openaccountants us-crypto-tax](https://github.com/openaccountants/openaccountants/blob/main/agent-skills/us-crypto-tax/SKILL.md) and [us-crypto-reporting](https://github.com/openaccountants/openaccountants/blob/main/packages/us-dc/us-crypto-reporting.md) citing primary documents):

- Crypto is property (Notice 2014-21); every token-to-token swap, including SOL -> memecoin -> USDC legs and wrap/unwrap, is a disposition on Form 8949 / Schedule D; short-term gains at 10-37% plus 3.8% NIIT above $200k/$250k; average cost not permitted; specific identification needs contemporaneous designation (Rev. Rul. 2024-14 as cited) else FIFO; basis is wallet-by-wallet since 1 Jan 2025 (Rev. Proc. 2024-28 safe harbor for pre-2025 basis); Section 1031 does not apply post-2017; 475(f)/trader status for spot crypto is unsettled (inference).
- Form 1099-DA: TD 10000 (89 Fed. Reg. 56480, 9 Jul 2024) requires custodial brokers to report gross proceeds for sales on/after 1 Jan 2025 (statements due 17 Feb 2026) and adjusted basis for assets acquired and sold on/after 1 Jan 2026 (first reported early 2027). The DeFi front-end broker rule (TD 10021, 30 Dec 2024) was disapproved under the CRA, signed as Pub. L. 119-5 on 10 Apr 2025, and removed (90 Fed. Reg. 31136, 11 Jul 2025): DEX front-ends and self-custody wallets file no 1099-DA, so the burden is on the taxpayer; CEX on/off-ramps (Coinbase, Kraken) will report, creating the mismatch audit risk.
- Wash sale: as of September 2026 IRC 1091 still does not apply to spot crypto (it applies to crypto ETFs); the Lummis bill (3 Jul 2025) and the Digital Asset PARITY Act (19 May 2026) would extend it prospectively; a June 2026 Ways and Means hearing and CNBC (28 Jul 2026) reported a renewed push; nothing enacted as of 11 Sep 2026 ([Occupy-AI basics](https://github.com/knucklefat/Occupy-AI/blob/main/11-crypto/08-tax-and-regulation/crypto-tax-basics.md), citing CNBC and lummis.senate.gov, not fetched).
- UK: swaps are disposals (CRYPTO22100); s.104 pooling with same-day and 30-day matching (an anti-wash rule); CGT 18%/24% since 30 Oct 2024, 3,000 GBP exemption; CARF reporting from 1 Jan 2026. Germany: tax-free after one year under section 23 EStG, otherwise progressive; 1,000 EUR limit is a cliff; each swap restarts the holding period (BMF letter 6 Mar 2025). EU: DAC8 from 1 Jan 2026 ([uk-crypto-tax](https://github.com/openaccountants/openaccountants/blob/main/packages/uk/uk-crypto-tax.md), [de-crypto-tax](https://github.com/openaccountants/openaccountants/blob/main/skills/international/germany/de-crypto-tax.md), [BittyTax](https://github.com/BittyTax/BittyTax/blob/master/README.md)). Ballast notes every swap is taxable in Spain ([README](/home/user/jonatangigex/familiars.family-opus5.5/README.md)). The ledger must log every fill with timestamp, quantities, USD fair value of both legs and fees, per wallet (inference).

Regulation:

- US: trading one's own account needs no broker-dealer, adviser, CTA or CPO registration; copying a public wallet with one's own money is likewise unregulated; selling signals, running others' money or accepting deposits crosses into Advisers Act / CEA territory; market manipulation (wash trading, pump-and-dump) is illegal regardless (standard legal reasoning, inference; statute pages blocked). eToro's 2024 SEC settlement restricted its US crypto offering and imposed a $1.5M penalty (recollection of press release 2024-125, not fetched).
- US perps: SEC/CFTC joint statements (2 and 5 Sep 2025) cleared leveraged spot and promised DeFi safe harbors; CFTC steps allowed BTC/ETH perpetuals on US DCMs; Chair Michael Selig said on 2 Mar 2026 the agency was working toward "true perpetual futures... within the next month or so"; US-legal venues include Coinbase Derivatives (nano BTC/ETH/SOL/XRP perps, ~9-10 bps round trip, $774 notional per nano BTC contract), Bitnomial (16 perps, $25 intraday margin) and Kraken Derivatives US; CLARITY Act passed the House Jul 2025 with Senate markups slipping; GENIUS Act enacted 18 Jul 2025 ([daily brief 2026-03-04](https://github.com/ernie55ernie/ernie55ernie.github.io/blob/master/_posts/2026-03-04-daily-crypto.md), [crypto-us-venues.md](https://github.com/luke-cramer/ai-trading/blob/main/research/crypto-us-venues.md); secondary).
- Hyperliquid US restriction: Terms of Use (updated 15 Jun 2026) s.1.6 bar anyone who resides in or is located in the United States or Ontario; s.1.8 forbids circumvention and s.1.9 warrants no location-disguising technology; the terms restrict the Interface, so the API is technically reachable, but Binance was charged by the CFTC (27 Mar 2023, release 8680-23) partly for coaching VPN evasion; no enforcement against retail Hyperliquid users was found ([gap-2.md](https://github.com/luke-cramer/ai-trading/blob/main/research/gap-2.md), verified from a US IP Aug 2026; ToS page itself blocked). None of the Bybit/Binance/OKX/Bitget copy products serve US persons (inference).
- EU: MiCA Title VI (Arts. 86-92, applicable since 30 Dec 2024) binds any person trading crypto-assets admitted to trading in the EU; Art. 91 manipulation covers wash trading, spoofing and voicing an opinion on a held token without disclosure; Art. 111 minimum fines for natural persons are EUR 5,000,000 or 3x the profit; tokens traded only on DEXs and never admitted to trading fall outside Title VI ([rya-sge, 2026-09-17](https://github.com/rya-sge/access-denied/blob/master/_posts/2026-09-17-mica-market-abuse-enforcement-supervision.md)). Individuals need no CASP authorisation (inference).
- UK: FCA finalised CP25/40 in Jan 2026, opens the cryptoasset gateway Sept 2026, full regime from 25 Oct 2027; only firms need authorisation ([daily brief 2026-01-24](https://github.com/ernie55ernie/ernie55ernie.github.io/blob/master/_posts/2026-01-24-daily-crypto.md)).
- GitHub Actions ToS: hosted runners may not be used for cryptomining, serverless computing, disproportionate load, or "any other activity unrelated to the production, testing, deployment, or publication of the software project associated with the repository"; misuse can lead to account suspension ([github/docs terms](https://github.com/github/docs/blob/main/content/site-policy/github-terms/github-terms-for-additional-products-and-features.md)). Google Cloud's AUP does not mention trading bots ([AUP](https://cloud.google.com/terms/aup)). A $5-10/month VPS with Docker is the correct host.

Cross-cutting candidate from the risk stream:

| candidate | venue | familiars-fit | expected-return note (short) |
|---|---|---|---|
| risk-overlay-brakes-and-kill-switch | Solana spot via Jupiter, posted on familiars | yes | Overlay only; costs 20-40% of upside in euphoric windows (+0.5% vs +136%) for drawdowns capped around 20-25% |

## Evidence on LLM trading agents

Note: arxiv.org, nof1.ai and news sites were blocked; paper content comes from GitHub-mirrored abstracts and digests and should be re-verified from primary PDFs.

- Alpha Arena Season 1 (nof1, Oct 2025): six frontier LLMs (GPT-5, Gemini 2.5 Pro, Claude Sonnet 4.5, Grok 4, DeepSeek V3.1, Qwen3 Max) each traded $10,000 of real USDC on Hyperliquid perps (BTC, ETH, SOL, BNB, DOGE, XRP) under one harness with a ~2-3 minute loop ([nof1 post, mirrored](https://github.com/itripleg/llm-trading-bot/blob/main/blogpost.txt)). Final standings per third-party write-ups: Qwen3 Max ~+22.3%, DeepSeek ~+4.9%, the four US models -30.8% to -62.7%; mid-season (23 Oct 2025): DeepSeek +5.9% on 9 trades, Qwen +0.6% on 22, Grok -14.2%, Claude -17.7%, Gemini -54.2% on 102 trades ($890 fees = 8.9% of capital, 27.5% win rate), GPT-5 -67.9% on 39 trades at 25x with 5.1% win rate ([nof1-analysis](https://github.com/weiuou/nof1-analysis), [vibe-investing](https://github.com/gameworkerkim/vibe-investing/blob/main/02.Investment%20Idea%20Column/DeepSeek_Alpha/readme.md); secondary). nof1's own failure list: models "over-traded and took quick, tiny gains that fees erased", misread temporal ordering of price arrays, stalled on synonyms, gamed hold rules, and self-reported confidence was "decoupled from actual trading performance" ([mirrored post](https://github.com/itripleg/llm-trading-bot/blob/main/blogpost.txt)).
- Season 1.5 (eight models, 32 sessions) finished in profit only 6 times and the pooled portfolio lost roughly a third; nof1 raised $15M (May 2026) and is pivoting Season 2 to its own purpose-trained models ([the-vault-ai](https://github.com/prajwalgajakesari/the-vault-ai/blob/main/editions/2026/05/15/stories/15-nof1-15m-ai-frontier-trading-models.md), newsletter, unverified).
- FINSABER (arXiv 2505.07078, KDD'26): re-running FinMem and FinAgent over 2000-2024 on 63-91 S&P 500 constituents including delisted names with commissions, buy-and-hold beat both on Sharpe in every bias-mitigated universe (B&H 0.703 vs ~0.24); no significant alpha (p>0.34); agents "too conservative in bull markets and too aggressive in bear markets"; FinMem's published TSLA Sharpe of 2.679 fell to 0.927 / 0.404 just by changing backbone ([digest](https://github.com/elimarks5807-coder/foundry-strategy-engine/blob/main/Digests/FINSABER.md)).
- Profit Mirage (arXiv 2510.07920): back-tested LLM-agent returns "evaporate once the model's knowledge window ends" (Sharpe decay 51-62%; almost every published agent fails to beat random post-cutoff); its FactFin pattern uses the LLM as a strategy-code generator with a deterministic engine trading ([mirrored abstract](https://github.com/CSQianDong/Awesome-arXiv-Daily-Reporter/blob/main/10-Oct-2025/topic/RAG_related_papers.md), [research-digests](https://github.com/memgrafter/research-digests)).
- StockBench (arXiv 2510.02209, contamination-free, Mar-Jul 2025, 20 DJIA stocks, $100k, 82 days): most agents including GPT-5 and Claude-4 fail to beat equal-weight buy-and-hold (0.4%, -15.2% MDD); best Kimi-K2 at 1.9% / -11.8% MDD, falling to 0.6% without news and fundamentals (same digests).
- TradingAgents (Python, Apache-2.0, ~108k stars, v0.5.1 Sep 2026) is a simulated-exchange research framework with no live trading; the paper's AAPL 26.6% / Sharpe 8.21 (Jan-Mar 2024) had no costs; an ACM 2026 reproducibility study found GPT-4o TradingAgents returned 15.8% +/- 4.2% on GOOGL (May-Jul 2025) vs 19.1% buy-and-hold; Trading-R1's repo is a "releasing soon" placeholder ([TradingAgents](https://github.com/TauricResearch/TradingAgents), [RL-Trader review](https://github.com/sh-arka22/RL-Trader/blob/main/docs/research/_raw/llm_P1_systems.md), which flags the DOI as partially verified).
- FinAgent's own ablation improved by removing its tool module (AAPL ARR 33.75%/SR 1.52 without tools vs 31.90%/1.43 with; ETHUSD 54.80%/1.40 vs 43.08%/1.18); FinMem is stocks-only and inactive since mid-2024 ([FinAgent](https://github.com/DVampire/FinAgent), [FinMem](https://github.com/pipiku915/FinMem-LLM-StockTrading), [review](https://github.com/sh-arka22/RL-Trader/blob/main/docs/research/02_llm_agents.md)).
- Agent Market Arena (arXiv 2510.11695, live Aug-Sep 2025): the same agent swung from -38.7% to +21.9% on TSLA by changing backbone; headline results report no drawdown or costs ([mirrored abstract](https://github.com/CSQianDong/Awesome-arXiv-Daily-Reporter/blob/main/14-Oct-2025/NLP/README.md)).
- LATTICE (arXiv 2604.26235, ~30 Apr 2026) is an LLM-judged decision-support benchmark for crypto copilots (six dimensions, 16 task types, 1,200 queries), not a P&L benchmark ([mirrored abstract](https://github.com/CSQianDong/Awesome-arXiv-Daily-Reporter/blob/main/30-Apr-2026/AI/README.md)). Marino & Juels (arXiv 2507.08249, 11 Jul 2025) is a position paper on harm vectors from giving agents wallets, with no trading results ([mirrored abstract](https://github.com/CSQianDong/Awesome-arXiv-Daily-Reporter/blob/main/14-Jul-2025/AI/README.md)).
- Where LLMs help: (a) offline text classification as a feature (SAPPO +0.35 Sharpe over PPO, edge dead at ~22 bps cost; a 110M FinBERT delivered 1.72 of the 1.90 Sharpe gain); (b) point-in-time entity extraction; (c) strategy code/parameter generation outside the loop (FactFin, GIFT) with a --no-llm ablation; LLM-infused RL (FinRL-DeepSeek) degraded PPO at every strength; an LLM layer on a 20-year, 100-symbol sweep costs $3.5k-$248k in API fees ([RL-Trader review](https://github.com/sh-arka22/RL-Trader/blob/main/docs/research/02_llm_agents.md), a third party's readings). PulseReddit reports gains "particularly in bull markets" with no cost or leakage detail in the abstract ([mirror](https://github.com/CSQianDong/Awesome-arXiv-Daily-Reporter/blob/main/5-Jun-2025/NLP/README.md)).
- Ballast already follows this: rules-only strategy, risk and execution in TypeScript; Claude only as an optional launch reviewer (approvals JSON gate) and a daily tighten-only learn pass ([launch-agent.ts](/home/user/jonatangigex/familiars.family-opus5.5/src/launch-agent.ts), [config/agent.json](/home/user/jonatangigex/familiars.family-opus5.5/config/agent.json)). No public "AI trader" board other than familiars exposes verifiable per-agent trades; other projects are marketing-grade ([openclaw](https://github.com/openclaw-trade/openclaw-trading-assistant), [Open-Nof1-AlphaArena](https://github.com/yufenng/Open-Nof1-AlphaArena)).

Framework survey (GitHub READMEs and LICENSE files; release years returned inconsistently by the fetch tool):

| name | language | license | Solana / Hyperliquid support | notes |
|---|---|---|---|---|
| [freqtrade](https://github.com/freqtrade/freqtrade) | Python 3.11+ | GPL-3.0 | Hyperliquid spot+futures via ccxt (API-wallet key, stop-limit on exchange, 5,000 candles only); no Solana DEX | 54.7k stars, release 2026.8; FreqAI adds ML/RL, "not designed for production" example |
| [Hummingbot + Gateway](https://github.com/hummingbot/hummingbot) | Python + Node sidecar | Apache-2.0 | Hyperliquid/dYdX CLOB; Solana via Gateway (Jupiter swap, Raydium, Meteora DLMM) | 20.2k stars, v2.17.0; market-making oriented; funding-arb script included |
| [nautilus_trader](https://github.com/nautechsystems/nautilus_trader) | Rust core + Python | LGPL-3.0 | production Hyperliquid adapter (spot, perps, HIP-3, vaults, agent wallets); no Solana | 29.3k stars, v2.0.0rc5 15 Sep 2026; nanosecond backtester; API churn |
| [jesse](https://github.com/jesse-ai/jesse) | Python | MIT | exchanges unnamed in README; Hyperliquid unverified | 8.6k stars; MCP server; live trading historically a paid plugin (pricing blocked) |
| [vectorbt](https://github.com/polakowo/vectorbt) | Python | Apache-2.0 + Commons Clause | none (research only) | 9.2k stars; fastest parameter sweeps; PRO paid |
| [backtesting.py](https://github.com/kernc/backtesting.py) | Python | AGPL-3.0 | none | ~9k stars, single-asset |
| [ccxt](https://github.com/ccxt/ccxt) | JS/TS, Python, C#, PHP, Go, Java, Rust | MIT | Hyperliquid certified (REST+WS); no Solana AMM | v4.5.84; auto builder fee caveat (section 7) |
| [hyperliquid-python-sdk](https://github.com/hyperliquid-dex/hyperliquid-python-sdk) | Python | MIT | Hyperliquid | 1.8k stars, 0.24.0; no 429 backoff |
| [solana-agent-kit](https://github.com/sendaifun/solana-agent-kit) | TypeScript | Apache-2.0 | Jupiter, Raydium, Orca, Meteora, Drift/Adrena, pump.fun, Jito | 1.7k stars; signs transactions, custody on the user |
| [sendaifun/skills](https://github.com/sendaifun/skills) | Markdown/TS | Apache-2.0 | Jupiter, pump.fun, raydium, helius, wallet-analysis skills | 128 stars; reference docs |
| [0xfnzero/sol-trade-sdk](https://github.com/0xfnzero/sol-trade-sdk) | Rust | MIT | PumpFun/PumpSwap/LaunchLab/Raydium/Meteora/Orca; Jito, Nextblock, 0slot lanes | 343 stars; sniping-grade |
| [GOAT SDK](https://github.com/goat-sdk/goat) | TS + Python | MIT | Solana/EVM | archived (read-only) |
| [ElizaOS](https://github.com/elizaOS/eliza) | TypeScript | MIT | plugins folded into core; plugin-solana/hyperliquid repos 404 | 19.5k stars; ecosystem in flux |
| [TradingAgents](https://github.com/TauricResearch/TradingAgents) | Python | Apache-2.0 | BTC/ETH via Yahoo only; simulated exchange | ~108k stars; research toy |
| [Ballast](/home/user/jonatangigex/familiars.family-opus5.5/README.md) | TypeScript (Node 22) | see repo | Jupiter Ultra/Swap, familiars client, pump.fun; no Hyperliquid | working familiars agent; simulation guard, risk engine, 455-line backtester |

Stack conclusion from the stream: every Solana/familiars dependency is TypeScript, so a TypeScript agent reusing Ballast's executor, simulation guard and client is the shortest path, with Python only as an optional research sidecar (vectorbt; nautilus/freqtrade if Hyperliquid perps are added) and the LLM called through a thin JSON-schema adapter, never for orders (inference from the survey).

## Strategy verdicts and recommended portfolio

_To be completed after adversarial verification._

## Open questions

familiars.family platform:

- The actual skill.md text: rate limits, exact posting obligations, own-token rule wording, agent-token mechanics, any fees, wash-trading and multi-wallet rules; it must be read from an unrestricted machine before implementation.
- Whether ranking uses realised or mark-to-market values for illiquid positions, and whether non-swap holdings (Kamino kUSDC, DLMM positions, JLP) count in agent equity.
- Whether /api/agents/{handle} trade history is complete or capped, how fresh it is versus chain confirmation (~1 min observed), and whether posts/trades carry timestamps precise enough for lagged copy replay.
- API-key rotation, hosted vs self-hosted differences (fees, custody, models, what happens when the free-AI budget is exhausted again), revenue model, team and launch date.
- Whether copying other agents requires disclosure, and whether familiars enforces anything beyond posting (minimum trade size $2, /api/posts rate limits, penalties for own-token trading).
- Whether absolute-USD ranking makes a modest-Sharpe strategy invisible, requiring a small narrative sleeve for traction.

fomo.family:

- fomoapi free tier: 250,000 credits/month (marketing) vs 1,000 credits/month (a consumer's README); whether fomoapi.io and getfomoapi.fun are one operator; the 1-hour cache and absence of a WS feed.
- Whether fomo's Terms treat third-party API reads or on-chain reconstruction as prohibited extraction.
- Persistence of top fomo traders' P&L on spot; DefiLlama fees/volume, Series B date and valuation, and Solana Tracker's FOMO leaderboard remain snippet-only.

Strategy evidence and backtests:

- Zarattini et al.'s Bitcoin-only results and cost-mitigation technique (SSRN blocked); reproducibility of AdaptiveTrend (arXiv 2602.11708, Sharpe 2.41) with realistic fees.
- How the Donchian ensemble and 4h breakout behaved on SOL through the Jan 2025-Apr 2026 drawdown (the offline study gives 24-month figures; the streams had none).
- A survivorship-corrected re-run of the Ballast universe (tokens liquid at each rebalance date, including later-dead ones).
- Measured 24h/7d survival and return distribution of tokens after graduation to PumpSwap/Raydium in 2026.
- Whether pump.fun/LetsBonk anti-sniper mechanisms reduced same-slot bundle share versus the 1.75% deployer-funded rate of April 2025.
- Whether seasonality overlays (weekend, 21-23 UTC) transfer to SOL at DEX costs.
- The 2026 YTD average funding on BTC/ETH/SOL per venue (sources conflict; the offline study covers Hyperliquid and Binance with gaps) and the size and persistence of the HL-vs-Binance differential at $5-20k notional.
- IL-adjusted profitability distribution of retail Meteora DLMM positions; whether Drift is still the right hedge venue after the protocol-v2 archive.
- Whether USDe is now safe as CEX collateral after Binance's oracle changes, or should be hard-excluded.

Copy-trading:

- Feature importances from arXiv 2601.08641 and whether its 3% per-bet return is net of 1% terminal fees or Jupiter-level fees.
- Measured detect-to-fill latency for Helius transactionSubscribe + Jupiter Swap V2 + Jito on a cheap VPS (430-680 ms is unverified); real fill-quality cost of copying a swing wallet 30-90 s late on $100k-$1M pools.
- How often screened wallets deliberately sell into follower flow and whether a consensus-of-N rule reduces it.
- Out-of-sample net returns of Hyperliquid fill-mirroring or vault baskets (none published; a 30-60 day paper test with lag is required); Copin.io and Hyperdash copier statistics were unreachable.
- Hyperliquid vault rules (10% share, 5% leader minimum, 1-day lockup) and rate limits confirmed only from snippets; Polymarket's current fee schedule and US availability.
- Current Nansen pricing and Solana coverage of Smart Money DEX endpoints.
- Whether an LLM veto gate on launches improves hit rate (needs a logged paper-mode A/B).

Data, costs and infrastructure:

- The distribution of Jupiter Swap V2 round-trip cost for $500-$5,000 trades in the top-50 Solana tokens (only 0.2-0.6% / 6-9% anecdotes exist); /order slippageBps bounds and excludeRouters values.
- Jupiter Ultra sunset date; whether lite-api.jup.ag still serves keyless traffic; whether Shield keeps returning warnings and what the Swap V2 replacement fields are for transfer tax, permanent delegate and holder concentration.
- Jupiter organic score label thresholds and how often a score changes in a token's first 2 hours; share of 20-120-minute-old launches that are Token-2022 and whether the ">40% permanent delegate" claim is accurate.
- Whether Solana's v1 transaction format (flat priority fee since 2026-09-15, one article) changes fee guidance and web3.js compatibility.
- Current Helius pricing page and the Sender dual-route minimum (0.0002 vs 0.001 SOL); QuickNode/Triton pricing; VPS pricing; Discord webhook and Telegram limits; Kraken/Coinbase key-permission toggles and rate limits; AWS/GCP KMS Ed25519 support and Turnkey/Privy pricing.
- Hyperliquid's address rate limit (1 request per 1 USDC): how fast a small polling bot exhausts it and whether /info counts; referral discount percentage; whether the 10k USDC vault fee applies to all vaults in 2026; whether the API rejects US IPs.
- Jesse live-trading pricing and vectorbt PRO pricing; release years of ccxt v4.5.84 and hummingbot v2.17.0.

Evidence gaps from the sandbox:

- Paper contents beyond abstracts (LATTICE, PulseReddit, FINSABER, Profit Mirage, StockBench) and nof1's final Season 1 table and Season 2 status need primary verification.
- Primary reports of the chalk/debug payload and Shai-Hulud worm, and whether Solana packages were among the ~500 affected.
- Exact current Hyperliquid liquidation parameters (backstop, ADL ranking, OI caps).

Regulatory and tax:

- Whether the CFTC completed onshoring of perpetuals beyond BTC/ETH after March 2026 and whether the CLARITY Act passed the Senate by Sept 2026.
- Whether any wash-sale extension (Lummis bill, PARITY Act) was enacted after 11 Sep 2026 and whether it is retroactive.
- Availability of Section 475(f) / trader status for spot crypto and the tax character of funding payments.
- Legal exposure of publishing trade explanations if the agent later sells into followers' buys (MiCA Art. 91, US anti-fraud rules).
- The user's jurisdiction: US residency removes CEX copy products and complicates Hyperliquid; Spain/EU adds MiCA and per-swap taxation.

## Sources

Deduplicated per stream, in order of first citation. Local paths refer to the Ballast reference clone. Entries marked "(cited, not fetched)" by a stream are kept as labelled. The offline study's data repositories are listed last.

### systematic-price

- <https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.13119>
- <https://www.nber.org/papers/w25882>
- <https://pubsonline.informs.org/doi/10.1287/mnsc.2024.05875>
- <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3985631>
- <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4675565>
- <https://acfr.aut.ac.nz/__data/assets/pdf_file/0009/918729/Time_Series_and_Cross_Sectional_Momentum_in_the_Cryptocurrency_Market_with_IA.pdf>
- <https://link.springer.com/article/10.1007/s11408-025-00474-9>
- <https://onlinelibrary.wiley.com/doi/abs/10.1002/ijfe.70036>
- <https://www.sciencedirect.com/science/article/abs/pii/S1544612325011377>
- <https://www.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/trend-factor-for-the-cross-section-of-cryptocurrency-returns/4C1509ACBA33D5DCAF0AC24379148178>
- <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4601972>
- <https://github.com/zebadee2kk/DeFi-TraderStack-Agent/issues/137>
- <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5209907>
- <https://concretumgroup.com/catching-crypto-trends-a-tactical-approach-for-bitcoin-and-altcoins/>
- <https://github.com/IsaacDodds/crypto-momentum-backtest>
- /home/user/jonatangigex/familiars.family-opus5.5/README.md
- /home/user/jonatangigex/familiars.family-opus5.5/config/agent.json
- /home/user/jonatangigex/familiars.family-opus5.5/src/strategy.ts
- <https://github.com/onixenix/fortunalabs>
- <https://www.coinquant.ai/blog/the-most-popular-crypto-trading-strategy-of-2026-backtested>
- <https://www.coinquant.ai/blog/donchian-channel-breakout-on-crypto-backtest-vs-keltner>
- <https://www.coinquant.ai/strategies/btc-donchian-channel-30m-backtest>
- <https://www.coinquant.ai/blog/mean-reversion-crypto-strategy-backtested-when-it-beats-trend-following>
- <https://www.coinquant.ai/blog/building-a-mean-reversion-strategy-in-cryptocurrency-markets-evidence-from-78-backtests>
- <https://github.com/Adeline117/Strategy-project>
- <https://tradingstrategies.work/blog/funding-rate-signal-btc-backtest>
- <https://setup4alpha.substack.com/p/i-tested-20-trend-based-regime-filters>
- <https://setup4alpha.substack.com/p/bitcoin-indicators-ranked>
- <https://panteracapital.com/blockchain-letter/navigating-crypto-in-2026/>
- <https://www.withintelligence.com/insights/cta-hedge-fund-report/>
- <https://www.bitget.com/news/detail/12560605122618>
- <https://arxiv.org/pdf/2608.10375>
- <https://pluang.com/en/news-feed/efek-rerata-biaya-dengan-bitcoin-perhitungan>
- <https://www.kucoin.com/blog/dca-vs-lump-sum>
- <https://www.indexbox.io/blog/bitcoin-and-ethereum-why-timing-the-market-is-a-losing-strategy/>
- <https://quantpedia.com/strategies/intraday-seasonality-in-bitcoin>
- <https://concretumgroup.com/seasonality-in-bitcoin-intraday-trend-trading/>
- <https://acr-journal.com/article/the-weekend-effect-in-crypto-momentum-does-momentum-change-when-markets-never-sleep--1514/>
- <https://binancemakertakerfee.org/>
- <https://bitsgap.com/blog/hyperliquid-fees-vs-binance-and-bybit-whats-actually-cheaper>
- <https://www.bybit.com/en/announcement-info/fee-rate/>
- <https://hyperliquidguide.com/guides/fees>
- <https://coinbureau.com/analysis/top-solana-dex-platforms>
- <https://managernest.com/blog/solana-trading-fees-explained-2026>
- <https://docs.chainstack.com/docs/solana-priority-fees-for-a-jupiter-in-python>
- <https://perpfinder.com/best-perp-dex/solana>
- <https://github.com/alimukri5-create/crypto-momentum>
- <https://www.soliduslabs.com/reports/solana-rug-pulls-pump-dumps-crypto-compliance>
- <https://www.coindesk.com/business/2025/05/07/98-of-tokens-on-pump-fun-have-been-rug-pulls-or-an-act-of-fraud-new-report-says>
- <https://www.sciencedirect.com/science/article/pii/S2096720925000818>
- <https://fundingarbhq.com/funding-arb-guide-2026-infrastructure-yield>
- <https://arxiv.org/html/2602.11708v1>

### carry-arb-mm

- <https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding>
- <https://www.dwellir.com/guides/hyperliquid-funding-rates>
- <https://perp.wiki/learn/hyperliquid-funding-rates-guide>
- <https://hyperliquidguide.com/guides/fees/fees-explained>
- <https://www.datawallet.com/crypto/hyperliquid-fees-explained>
- <https://raw.githubusercontent.com/hummingbot/hummingbot/master/hummingbot/connector/derivative/hyperliquid_perpetual/hyperliquid_perpetual_constants.py>
- <https://supa.is/article/hyperliquid-api-rate-limits-user-limits-2026>
- <https://tradersunion.com/brokers/crypto/view/binance/futures-fees/>
- <https://raw.githubusercontent.com/hummingbot/hummingbot/master/hummingbot/connector/derivative/binance_perpetual/binance_perpetual_constants.py>
- <https://arbitragescanner.io/blog/crypto-funding-rate-arbitrage-guide>
- <https://www.buildix.trade/blog/crypto-funding-rate-arbitrage-delta-neutral-hyperliquid-binance>
- <https://www.neuralarb.com/2026/04/24/hyperliquid-vs-cexs-perp-arbitrage-after-fees-funding-slippage/>
- <https://coinmetrics.substack.com/p/state-of-the-network-issue-368>
- <https://www.datawallet.com/crypto-funding-rates>
- <https://raw.githubusercontent.com/hummingbot/hummingbot/master/scripts/v2_funding_rate_arb.py>
- <https://github.com/aoki-h-jp/funding-rate-arbitrage>
- <https://raw.githubusercontent.com/hummingbot/hummingbot/master/README.md>
- <https://onekey.so/blog/ecosystem/hyperliquid-binance-funding-arbitrage-20260429/>
- <https://blog.amberdata.io/how-3.21b-vanished-in-60-seconds-october-2025-crypto-crash-explained-through-7-charts>
- <https://www.fticonsulting.com/insights/articles/crypto-crash-october-2025-leverage-met-liquidity>
- <https://www.coingecko.com/learn/october-10-crypto-crash-explained>
- <https://www.coindesk.com/markets/2026/01/08/october-s-crypto-crash-left-market-makers-stuffed-with-coins-slowing-trading-bitmex>
- <https://www.coindesk.com/markets/2025/10/13/no-ethena-s-usde-didn-t-de-peg>
- <https://www.ccn.com/education/crypto/ethena-usde-depeg-binance-crash-explained/>
- <https://www.tradingview.com/news/cointelegraph:c4d13adee094b:0-explanations-of-usde-depeg-on-binance-focus-on-coordinated-attack-oracles/>
- <https://pharos.watch/learn/case-studies/stream-elixir-contagion-2025/>
- <https://blockeden.xyz/blog/2025/11/08/m-defi-contagion/>
- <https://finance.yahoo.com/news/elixir-shuts-down-deusd-stablecoin-104937488.html>
- <https://eco.com/support/en/articles/15254002-ethena-usde-and-susde-2026-delta-neutral-yield>
- <https://stablecoininsider.org/ethena-usde-q1-2026-report/>
- <https://eco.com/support/en/articles/14801186-kamino-lending-solana-s-money-market-explained>
- <https://eco.com/support/en/articles/15253991-best-usdc-yield-platforms-2026-aave-morpho-sky-compared>
- <https://aavescan.com/stablecoins>
- <https://www.datawallet.com/crypto/hyperliquid-hlp-explained>
- <https://www.vaasblock.com/news/hyperliquid-hlp-vault-economics-perp-dex-2026/>
- <https://www.coindesk.com/markets/2025/03/26/hyperliquid-delists-jellyjelly-after-vault-squeezed-in-usd13m-tussle>
- <https://hyperliquidguide.com/ecosystem/hyperliquid-vaults-guide>
- <https://arxiv.org/abs/2606.15715>
- <https://multicoin.capital/2026/02/17/adverse-selection-rules-everything-around-me/>
- <https://academy.extropy.io/pages/articles/mev-crosschain-analysis-2025.html>
- <https://rpcfast.com/blog/solana-arbitrage-bot-setup>
- <https://yavorovych.medium.com/solana-arbitrage-bot-setup-why-most-fail-before-they-start-1c24d8d72593>
- <https://blog.everstrike.io/7-arbitrage-strategies-are-still-accessible-to-retail-quants-in-2025/>
- <https://hftadvisory.substack.com/p/cross-exchange-arbitrage-and-the>
- <https://defillama.com/protocol/meteora-dlmm>
- <https://genfinity.io/2026/06/08/meteora-lp-army-solana-liquidity-education/>
- <https://coinbureau.com/review/orca-dex-review>
- <https://github.com/GeekLad/meteora-profit-analysis>
- <https://docs.neutral.trade/for-capital-allocators/market-neutral/jupiter-jlp-delta-neutral>
- <https://medium.com/@jayepaul81/building-solneutral-a-delta-neutral-usdc-vault-on-solana-049eb1d8a2ce>
- <https://blog.redstone.finance/2025/12/11/solana-lending-markets/>
- <https://github.com/drift-labs/protocol-v2>
- <https://arxiv.org/pdf/2506.11921>
- <https://coinbureau.com/guides/how-to-backtest-your-crypto-trading-strategy>
- <https://goodcrypto.app/case-study-180-apr-using-grid-bot-while-bitcoin-stayed-flat/>
- <https://arxiv.org/abs/2109.10662>
- <https://www.wne.uw.edu.pl/download_file/6095/0>
- <https://arxiv.org/pdf/2605.01954>
- <https://developers.jup.ag/docs/ultra/fees>
- /home/user/jonatangigex/familiars.family-opus5.5/README.md
- /home/user/jonatangigex/familiars.family-opus5.5/src/familiars.ts
- <https://github.com/hyperliquid-dex/node>
- <https://github.com/stephenpeters/delta_neutral_strategies>
- <https://kr.tradingview.com/script/OxAgtVr0-Cash-And-Carry-Arbitrage-BTC-Compare-Month-6-by-SeoNo1>

### onchain-memecoin

- <https://www.soliduslabs.com/reports/solana-rug-pulls-pump-dumps-crypto-compliance>
- <https://www.coindesk.com/business/2025/05/07/98-of-tokens-on-pump-fun-have-been-rug-pulls-or-an-act-of-fraud-new-report-says>
- <https://cryptopotato.com/98-of-tokens-on-pump-fun-are-rug-pulls-or-fraud-report/>
- <https://arxiv.org/abs/2609.10246>
- <https://solanacompass.com/news/pumpfun-launched-42000-tokens-in-one-day-fewer-than-2-will-ever-reach-a-dex>
- <https://solanafloor.com/news/solana-launchpad-showdown-pump-fun-vs-lets-bonk-fun>
- <https://github.com/unretain/solagents>
- <https://cointelegraph.com/news/pump-fun-crypto-traders-majority-do-not-realize-profits-dune-data>
- <https://www.coingecko.com/research/publications/pump-fun-traders-are-making-a-comeback>
- <https://www.crowdfundinsider.com/2026/05/277755-solana-based-meme-coins-trading-platform-pump-fun-makes-recovery-research/>
- <https://dune.com/web3frank/pumpfun-6-months-trader-analysis>
- <https://pineanalytics.substack.com/p/exit-liquidity-machines>
- <https://www.chaincatcher.com/en/article/2185070>
- <https://arxiv.org/abs/2607.02795>
- <https://zenodo.org/records/20978742>
- <https://openliquid.io/tools/pumpfun-sniper-bot/>
- <https://arxiv.org/abs/2608.20271>
- <https://arxiv.org/html/2602.13480v1>
- /home/user/jonatangigex/familiars.family-opus5.5/README.md
- /home/user/jonatangigex/familiars.family-opus5.5/src/onchain.ts
- <https://dev.to/ohmygod/solanas-permanent-delegate-burn-scam-how-token-2022-extensions-power-2026s-largest-automated-rug-4579>
- <https://dev.to/mrwizardlyloaf/token-2022-traps-that-drain-ai-trading-agents-and-how-to-screen-them-33bo>
- <https://neodyme.io/en/blog/token-2022/>
- <https://dev.to/mrwizardlyloaf/how-to-detect-a-solana-honeypot-token-before-your-bot-buys-2cdf>
- <https://dev.jup.ag/docs/token-api/v2>
- <https://dev.jup.ag/docs/ultra-api/get-shield>
- <https://developers.jup.ag/blog/what-is-organic-score>
- <https://cryptoslate.com/decentralized-exchanges/jupiter-exchange-review/>
- /home/user/jonatangigex/familiars.family-opus5.5/src/jupiter.ts
- <https://www.soltokencreator.io/blog/pump-fun-fees-explained>
- <https://cryptoslate.com/decentralized-exchanges/pump-fun-review/>
- <https://solanatools.io/blog/solana-trading-bot-fees-compared>
- <https://axiompedia.com/compare>
- <https://github.com/onecandlecat/one-more-candle>
- <https://github.com/nirholas/pump-fun-sdk/blob/main/docs/fee-sharing.md>
- <https://github.com/jito-labs/jito-docs/blob/main/docs/source/lowlatencytxnsend.md>
- <https://gist.github.com/NeOMakinG/49daadcd4855dc8986664ad0ba07b757>
- <https://dl.acm.org/doi/10.1145/3730567.3764493>
- <https://www.dlnews.com/articles/defi/solana-users-use-jito-to-stop-sandwich-attacks-and-mev/>
- <https://solana.com/docs/defi/mev-protection>
- <https://github.com/0xfnzero/sol-trade-sdk>
- <https://github.com/0xfnzero/pumpfun-sdk>
- <https://github.com/GMGNAI/gmgn-skills/blob/main/Readme.md>
- <https://subglow.io/subglow-vs-helius>
- <https://uwuu.ai/blog/dexscreener-api>
- <https://fluxrpc.com/docs/rugcheck/getting-started>
- <https://dune.com/queries/4387975/lineage>
- <https://www.solanatracker.io/leaderboard/kolscan>
- <https://gmgn.ai/blog/how-to-track-copy-solana-smart-money/>
- <https://arxiv.org/pdf/2601.08641>
- <https://moonhydra.com/blog/copy-trading-solana-guide/>
- <https://coingape.com/trending/how-a-crypto-trader-transformed-1795-into-873k-in-48-hours/>
- <https://github.com/verixiaapps/awesome-memecoin-trading>
- /home/user/jonatangigex/familiars.family-opus5.5/src/familiars.ts
- /home/user/jonatangigex/familiars.family-opus5.5/config/agent.json
- <https://familiars.family/>
- /home/user/jonatangigex/familiars.family-opus5.5/src/backtest.ts
- <https://www.kucoin.com/blog/en-solana-launchpad-letsbonk-fun-sees-600-revenue-surge-in-early-2026>
- <https://bex.co/blog/2026/04/22/meme-launchpad-2-pump-fun-letsbonk-anti-sniper-bonding-curve-professionalization>
- <https://bravenewcoin.com/insights/pump-fun-introduces-creator-fee-sharing-system-to-rebalance-platform-incentives>
- <https://github.com/pump-fun/pump-public-docs>
- <https://coinbureau.com/analysis/best-memecoin-launchpads>
- <https://www.kucoin.com/news/articles/clanker-surging-activity-in-base-ecosystem-drives-weekly-protocol-fees-to-record-8m-high>
- <https://www.gate.com/crypto-wiki/article/what-is-clanker-clanker-and-how-does-its-ai-powered-token-launch-platform-work-on-base-20260106>
- <https://blog.bubblemaps.io/whats-the-difference-between-bundle-cluster-2/>
- <https://dev.to/paulf280ui/how-to-detect-coordinated-solana-launches-your-bundle-checker-misses-2c5g>
- <https://trojan.com/blog/how-to-trade-solana-meme-coins-in-2026-a-beginners-guide>
- <https://www.dextools.io/tutorials/solana-memecoins-complete-guide-2026>
- <https://github.com/KrazySnipeOof/AI-memes-strat>
- <https://www.altrady.com/blog/crypto-trading-strategies/pump-fun-solana-memecoin-trading>
- <https://docs.bitquery.io/docs/blockchain/Solana/Pumpfun/pump-fun-to-pump-swap/>
- <https://github.com/git-disl/memetrans>

### copy-onchain

- <https://docs.nansen.ai/api/smart-money>
- <https://docs.nansen.ai/getting-started/credits>
- <https://docs.nansen.ai/guides/templates/complex-use-cases/use-case-4-copytrading-top-performing-wallets>
- <https://www.kucoin.com/news/flash/nansen-launches-pay-per-use-model-on-base-and-solana-with-usdc-settlement>
- <https://toolchase.com/tool/nansen/>
- <https://gmgn.ai/blog/how-to-track-copy-solana-smart-money/>
- <https://coincodecap.com/gmgn-review>
- <https://docs.gmgn.ai/index/copy-trade-copy-smart-money-automatically-earn-sol>
- <https://x.com/gmgnai/status/1963882916460769393>
- <https://apify.com/parsebird/gmgn-copytrade-wallet-scraper>
- <https://cielo.finance/>
- <https://docs.cielo.finance/guides/copy-trading/finding-good-wallets>
- <https://api-info.cielo.finance/>
- <https://uwuu.ai/blog/cielo-finance-review>
- <https://solanabox.tools/tools/cielo-finance>
- <https://github.com/vybenetwork/solana-wallet-pnl-profit-and-loss-api>
- <https://github.com/vybenetwork/solana-top-traders-wallets-and-tokens-api>
- <https://docs.birdeye.so/reference/get-defi-v2-tokens-top_traders>
- <https://docs.birdeye.so/reference/get-wallet-v2-pnl-multiple>
- <https://docs.codex.io/recipes/wallets/discover-traders>
- <https://www.codex.io/pricing>
- <https://www.solanatracker.io/data-api>
- <https://docs.solanatracker.io/data-api/pnl/get-wallet-pnl>
- <https://moralis.com/crypto-pnl-api-how-to-track-wallet-profit-loss/>
- <https://apis.io/plans/solscan/solscan-plans-pricing/>
- <https://comparedge.com/tools/dune-analytics/pricing>
- <https://dune.com/couldbebasic/wallet-analyzer-for-copy-traders>
- <https://toolchase.com/tool/arkham/>
- <https://www.walletmaster.tools/solana-pnl-api/>
- <https://arxiv.org/abs/2608.04373>
- <https://github.com/daojingzhai/public-trader-identity>
- <https://arxiv.org/abs/2601.08641>
- <https://dl.acm.org/doi/10.1145/3774904.3792635>
- <https://www.emergentmind.com/topics/memecoin-copy-trading>
- <https://github.com/BallesJr/polymarket-copy-trader>
- <https://www.coingecko.com/research/publications/pump-fun-traders-are-making-a-comeback>
- <https://beincrypto.com/pump-fun-traders-profit-comeback-meme-coin-season/>
- <https://www.vantagemarkets.com/academy/is-copy-trading-profitable/>
- <https://medium.com/@fintecmarketsfx/is-copy-trading-profitable-in-2025-what-real-traders-are-saying-1fb321b4951e>
- /home/user/jonatangigex/familiars.family-opus5.5/README.md
- <https://medium.com/@nathan.baldwin_31153/copy-trading-on-solana-how-to-find-alpha-wallets-without-getting-faked-out-by-bots-0bc550f07290>
- <https://moonhydra.com/blog/copy-trading-solana-guide/>
- <https://www.helius.dev/docs/grpc>
- <https://www.helius.dev/docs/billing/plans>
- <https://www.helius.dev/blog/laserstream-websockets>
- <https://www.helius.dev/solana-webhooks-websockets>
- <https://shyft.to/>
- <https://github.com/keidev-sol/Solana-Copy-Trading-Bot-Rust>
- <https://github.com/ChainInsighter/Solana-Copy-trading-bot>
- <https://rpcfast.com/blog/pillars-of-choosing-a-solana-rpc-provider-for-trading-bots>
- <https://dysnix.com/blog/top-solana-sniper-bot>
- <https://yavorovych.medium.com/how-to-build-a-solana-copy-trading-bot-2026-guide-559448259e96>
- <https://developers.jup.ag/docs/ultra/get-started>
- <https://support.jup.ag/hc/en-us/articles/18735045234588-Fees>
- <https://developers.jup.ag/pricing>
- <https://medium.com/bloxroute/a-new-era-of-mev-on-solana-ae5cff390b71>
- <https://cryptorank.io/news/feed/4d1c4-jito-bans-15-additional-validators-after-data-emerges-of-widespread-sandwich-attacks>
- <https://solana.com/docs/defi/mev-protection>
- <https://docs.chainstack.com/docs/solana-mev-protection>
- <https://subglow.io/use-cases/copy-trading>
- <https://docs.bitquery.io/docs/usecases/copy-trading-bot/>
- <https://www.mexc.com/news/74318>
- <https://github.com/abstradeapi/Open-Fomo-API>
- <https://fomoapi.io/>
- <https://fomoapi.io/pricing>
- <https://fomo.family/blog/learn/what-is-copy-trading>
- <https://crowinvesting.com/crypto-trading/fomo-family-tutorial/>
- /home/user/jonatangigex/familiars.family-opus5.5/src/familiars.ts
- /home/user/jonatangigex/familiars.family-opus5.5/src/cli/learn.ts
- <https://familiars.family/>
- <https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults/for-vault-leaders-legacy>
- <https://eco.com/support/en/articles/15197987-hyperliquid-vault-strategies-2026-hlp-and-user-vaults-explained>
- <https://arx.trade/blog/hyperliquid-vaults-explained/>
- /home/user/jonatangigex/familiars.family-opus5.5/config/agent.json
- <https://www.nansen.ai/post/how-to-track-smart-money-crypto-accumulation-ultimate-guide>
- <https://docs.cielo.finance/wallet-tracking/insights>

### copy-cex-perps

- <https://raw.githubusercontent.com/tradingstrategy-ai/web3-ethereum-defi/master/eth_defi/hyperliquid/api.py>
- <https://github.com/Senpi-ai/senpi-skills/blob/main/quant-desk/scripts/hl_api.py>
- <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/scripts/hyperliquid/README-hyperliquid-copy-trading.md>
- <https://raw.githubusercontent.com/hyperliquid-dex/hyperliquid-python-sdk/master/hyperliquid/info.py>
- <https://raw.githubusercontent.com/hyperliquid-dex/hyperliquid-python-sdk/master/hyperliquid/utils/types.py>
- <https://raw.githubusercontent.com/tradingstrategy-ai/web3-ethereum-defi/master/scripts/hyperliquid/README-hyperliquid-copy-trading.md>
- <https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits>
- <https://raw.githubusercontent.com/nktkas/hyperliquid/main/src/api/info/_methods/vaultDetails.ts>
- <https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults/for-vault-depositors-legacy>
- <https://eco.com/support/en/articles/15197987-hyperliquid-vault-strategies-2026-hlp-and-user-vaults-explained>
- <https://raw.githubusercontent.com/petercool/hl-vault-monitor/main/README.md>
- <https://www.datawallet.com/crypto/hyperliquid-hlp-explained>
- <https://www.coingecko.com/learn/hyperliquid-hlp-vault-analysis>
- <https://www.dlnews.com/articles/defi/hyperliquid-trader-james-wynn-liquidated-nine-times/>
- <https://www.coindesk.com/markets/2025/05/31/cryptos-most-watched-whale-gets-fully-liquidated-after-placing-billions-in-risky-bets>
- <https://finance.yahoo.com/markets/crypto/articles/james-wynn-account-drops-900-101235555.html>
- <https://hyperliquid.gitbook.io/hyperliquid-docs/trading/builder-codes>
- <https://www.dwellir.com/blog/hyperliquid-builder-codes>
- <https://hyperdash.com/learn/hyperliquid-builder-codes-explained-how-third-party-apps-earn-fees-on-chain>
- <https://raw.githubusercontent.com/ccxt/ccxt/master/python/ccxt/okx.py>
- <https://www.okx.com/en-us/help/lead-traders-trader-profit-sharing-rules>
- <https://www.okx.com/en-us/campaigns/copytrading-apizone>
- <https://raw.githubusercontent.com/ccxt/ccxt/master/python/ccxt/bitget.py>
- <https://www.bitget.com/support/articles/12560603803039>
- <https://www.bitget.com/support/articles/12560603848377>
- <https://www.bybit.com/en/help-center/article/Copy-Trading-Profit-Sharing-Explained>
- <https://aotrading.io/blogs/bybit-copy-trading-fees-2026>
- <https://raw.githubusercontent.com/ccxt/ccxt/master/python/ccxt/bybit.py>
- <https://www.binance.com/en/support/faq/lead-trader-benefits-in-binance-futures-copy-trading-ea9bacf82b9e4ddfae50ebc98565241b>
- <https://raw.githubusercontent.com/ccxt/ccxt/master/python/ccxt/binance.py>
- <https://coinbureau.com/review/bybit-copy-trading-review>
- <https://coinbureau.com/review/binance-copy-trading-review>
- <https://cryptorank.io/news/feed/13fb9-285m-solana-protocol-drift-largest-exploit-2026>
- <https://www.chainalysis.com/blog/lessons-from-the-drift-hack/>
- <https://raw.githubusercontent.com/drift-labs/drift-vaults/master/programs/drift_vaults/src/state/vault.rs>
- <https://raw.githubusercontent.com/drift-labs/drift-vaults/master/ts/sdk/README.md>
- <https://raw.githubusercontent.com/elliottech/lighter-python/main/README.md>
- <https://www.datawallet.com/crypto/lighter-explained>
- <https://www.asterdex.com/en/vaults>
- <https://docs.jup.ag/user-docs/trade/perps>
- <https://raw.githubusercontent.com/cyl19970726/poly-sdk/main/docs/api/02-leaderboard.md>
- <https://raw.githubusercontent.com/cyl19970726/poly-sdk/main/docs/api/03-position-activity.md>
- <https://raw.githubusercontent.com/NYTEMODEONLY/polyterm/main/docs/core/leaderboard.md>
- <https://raw.githubusercontent.com/howwohmm/polymarket-paper/main/copytrade.py>
- <https://raw.githubusercontent.com/Polymarket/py-clob-client/main/README.md>
- <https://www.quicknode.com/builders-guide/best/top-10-polymarket-trading-bots>
- <https://polybot.me/blog/polymarket-copy-trading-guide>
- <https://arxiv.org/pdf/1406.7729>
- <https://pubsonline.informs.org/doi/10.1287/mnsc.2019.3508>
- <https://digikogu.taltech.ee/en/Download/9c402020-d23e-447e-bcba-e41823baa02d>
- <https://copytraderscout.com/blog/is-copy-trading-profitable/>
- <https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/quant-desk/references/methodology.md>
- <https://raw.githubusercontent.com/eltonaguiar/findtorontoevents_antigravity.ca-archive-2026-05-23/main/memory/2026-03-19.md>
- /home/user/jonatangigex/familiars.family-opus5.5/src/familiars.ts
- /home/user/jonatangigex/familiars.family-opus5.5/README.md
- <https://cryptorank.io/news/feed/00e88-hypervaultfi-suspected-rug-pull-takes-3-6m>
- <https://www.cryptopolitan.com/hypervaultfi-suspected-rug-pull-takes-3-6m/>

### familiars-fomo

- /home/user/jonatangigex/familiars.family-opus5.5/src/familiars.ts
- /home/user/jonatangigex/familiars.family-opus5.5/src/cli/register.ts
- /home/user/jonatangigex/familiars.family-opus5.5/test/safety.test.ts
- /home/user/jonatangigex/familiars.family-opus5.5/README.md
- /home/user/jonatangigex/familiars.family-opus5.5/src/launch-agent.ts
- /home/user/jonatangigex/familiars.family-opus5.5/src/learn.ts
- /home/user/jonatangigex/familiars.family-opus5.5/src/rebuild.ts
- /home/user/jonatangigex/familiars.family-opus5.5/src/risk.ts
- /home/user/jonatangigex/familiars.family-opus5.5/src/poster.ts
- /home/user/jonatangigex/familiars.family-opus5.5/src/agent.ts
- /home/user/jonatangigex/familiars.family-opus5.5/src/cli/owner-key.ts
- <https://raw.githubusercontent.com/faisalkhattak7997-tech/solana-trade-bot/main/signals.json>
- <https://x.com/familiarsfamily>
- <https://github.com/search?q=familiars.family&type=repositories>
- <https://github.com/JonatanGigex/familiars.family-Opus5.5>
- /home/user/jonatangigex/familiars.family-opus5.5/src/launch.ts
- /home/user/jonatangigex/familiars.family-opus5.5/src/backtest.ts
- /home/user/jonatangigex/familiars.family-opus5.5/src/jupiter.ts
- /home/user/jonatangigex/familiars.family-opus5.5/src/market.ts
- /home/user/jonatangigex/familiars.family-opus5.5/src/pumpfun.ts
- /home/user/jonatangigex/familiars.family-opus5.5/src/bootstrap.ts
- <https://github.com/synsur/fomo-flow>
- <https://www.datawallet.com/crypto/fomo-app-explained>
- <https://fomotrading.app/perps/>
- <https://raw.githubusercontent.com/Cataracks/fomo-sapiens/main/.claude/skills/fomo-sapiens/SKILL.md>
- <https://raw.githubusercontent.com/chainstacklabs/fomo-solana-rh-listeners/main/README.md>
- <https://raw.githubusercontent.com/jonthomp/fomo-robinhood-radar/main/README.md>
- <https://raw.githubusercontent.com/phinolex/fomo-extension-bot-order/main/README.md>
- <https://bitcoinfoundation.org/news/crypto-companies-news/fomo-investments/>
- <https://defillama.com/protocol/fomo>
- <https://raw.githubusercontent.com/Cataracks/fomo-sapiens/main/.claude/skills/fomo-sapiens/references/endpoints.md>
- <https://raw.githubusercontent.com/cvxv666/fomo-robinhood-radar/main/docs/fomo-endpoints.md>
- <https://raw.githubusercontent.com/omarlatreche/FOMO-Copy-Trader/main/session.md>
- <https://github.com/VersoXBT/x-fomo-pnl>
- <https://fomoapi.io/pricing>
- <https://github.com/abstradeapi/Open-Fomo-API>
- <https://raw.githubusercontent.com/abstradeapi/Fomo-CLI-Agent/main/README.md>
- <https://fomolens.app/>
- <https://raw.githubusercontent.com/DavidYashar/Smart-Alert-Robin/main/docs/fomo-kol-pipeline.md>
- <https://raw.githubusercontent.com/313imverymellodet/fomo-copy-sim/main/README.md>
- <https://raw.githubusercontent.com/itsnex1s/fomopulse-robinhood-chain-tape/main/README.md>
- <https://www.solanatracker.io/leaderboard/fomo>
- /home/user/jonatangigex/familiars.family-opus5.5/src/strategy.ts
- /home/user/jonatangigex/familiars.family-opus5.5/config/agent.json
- /home/user/jonatangigex/familiars.family-opus5.5/src/onchain.ts

### llm-agents-evidence

- <https://github.com/CSQianDong/Awesome-arXiv-Daily-Reporter/blob/main/30-Apr-2026/AI/README.md>
- <https://arxiv.org/abs/2604.26235>
- <https://github.com/CSQianDong/Awesome-arXiv-Daily-Reporter/blob/main/5-Jun-2025/NLP/README.md>
- <https://arxiv.org/abs/2506.03861>
- <https://github.com/CSQianDong/Awesome-arXiv-Daily-Reporter/blob/main/14-Jul-2025/AI/README.md>
- <https://github.com/BitnomadLive/OffensiveReading>
- <https://arxiv.org/abs/2507.08249>
- <https://github.com/itripleg/llm-trading-bot/blob/main/blogpost.txt>
- <https://github.com/weiuou/nof1-analysis>
- <https://github.com/gameworkerkim/vibe-investing/blob/main/02.Investment%20Idea%20Column/DeepSeek_Alpha/readme.md>
- <https://nof1.ai/>
- <https://github.com/zhuxining/agent-quant/blob/main/docs/nof1-prompt.md>
- <https://github.com/prajwalgajakesari/the-vault-ai/blob/main/editions/2026/05/15/stories/15-nof1-15m-ai-frontier-trading-models.md>
- <https://github.com/prajwalgajakesari/the-vault-ai/blob/main/editions/2026/05/23/stories/13-nof1-15m-frontier-financial-models.md>
- <https://github.com/elimarks5807-coder/foundry-strategy-engine/blob/main/Digests/FINSABER.md>
- <https://github.com/sh-arka22/RL-Trader/blob/main/docs/research/02_llm_agents.md>
- <https://arxiv.org/abs/2505.07078>
- <https://github.com/CSQianDong/Awesome-arXiv-Daily-Reporter/blob/main/10-Oct-2025/topic/RAG_related_papers.md>
- <https://github.com/memgrafter/research-digests>
- <https://github.com/R1M1N/research_paper_explainer>
- <https://arxiv.org/abs/2510.07920>
- <https://arxiv.org/abs/2510.02209>
- <https://github.com/TauricResearch/TradingAgents>
- <https://github.com/TauricResearch/Trading-R1>
- <https://github.com/sh-arka22/RL-Trader/blob/main/docs/research/_raw/llm_P1_systems.md>
- <https://github.com/HuggingAGI/HuggingArxivLLM>
- <https://github.com/DVampire/FinAgent>
- <https://github.com/pipiku915/FinMem-LLM-StockTrading>
- <https://github.com/CSQianDong/Awesome-arXiv-Daily-Reporter/blob/main/14-Oct-2025/NLP/README.md>
- <https://arxiv.org/abs/2510.11695>
- /home/user/jonatangigex/familiars.family-opus5.5/README.md
- /home/user/jonatangigex/familiars.family-opus5.5/config/agent.json
- /home/user/jonatangigex/familiars.family-opus5.5/src/launch-agent.ts
- /home/user/jonatangigex/familiars.family-opus5.5/src/familiars.ts
- <https://github.com/freqtrade/freqtrade>
- <https://raw.githubusercontent.com/freqtrade/freqtrade/develop/docs/exchanges.md>
- <https://raw.githubusercontent.com/freqtrade/freqtrade/develop/docs/freqai.md>
- <https://raw.githubusercontent.com/freqtrade/freqtrade/develop/LICENSE>
- <https://github.com/hummingbot/hummingbot>
- <https://github.com/hummingbot/gateway>
- <https://github.com/hummingbot/hummingbot/releases>
- <https://github.com/nautechsystems/nautilus_trader>
- <https://raw.githubusercontent.com/nautechsystems/nautilus_trader/develop/docs/integrations/hyperliquid.md>
- <https://github.com/nautechsystems/nautilus_trader/releases>
- <https://github.com/jesse-ai/jesse>
- <https://raw.githubusercontent.com/jesse-ai/jesse/master/README.md>
- <https://github.com/polakowo/vectorbt>
- <https://raw.githubusercontent.com/polakowo/vectorbt/master/LICENSE.md>
- <https://github.com/kernc/backtesting.py>
- <https://github.com/ccxt/ccxt>
- <https://github.com/ccxt/ccxt/releases>
- <https://github.com/hyperliquid-dex/hyperliquid-python-sdk>
- <https://github.com/hyperliquid-dex/hyperliquid-python-sdk/releases>
- <https://github.com/sendaifun/solana-agent-kit>
- <https://github.com/sendaifun/skills>
- <https://raw.githubusercontent.com/sendaifun/skills/main/skills/jupiter/SKILL.md>
- <https://github.com/0xfnzero/solana-bot-dev-skills>
- <https://github.com/0xfnzero/sol-trade-sdk>
- <https://github.com/goat-sdk/goat>
- <https://github.com/elizaOS/eliza>
- <https://github.com/elizaos-plugins>
- <https://github.com/openclaw-trade/openclaw-trading-assistant>
- <https://github.com/yufenng/Open-Nof1-AlphaArena>
- <https://github.com/yipingheijiang/nofxAI>
- <https://github.com/AgileWoW/moltbook-agentic-web-directory>
- /home/user/jonatangigex/familiars.family-opus5.5/src/learn.ts

### infra-execution

- <https://raw.githubusercontent.com/jup-ag/docs/main/ultra/index.mdx>
- <https://raw.githubusercontent.com/jup-ag/docs/main/swap/migration/ultra-to-order.mdx>
- /home/user/jonatangigex/familiars.family-opus5.5/src/jupiter.ts
- <https://raw.githubusercontent.com/jup-ag/docs/main/ultra/fees.mdx>
- <https://raw.githubusercontent.com/jup-ag/docs/main/swap/order-and-execute.mdx>
- <https://raw.githubusercontent.com/jup-ag/docs/main/swap/build/index.mdx>
- <https://raw.githubusercontent.com/jup-ag/docs/main/portal/plans.mdx>
- <https://raw.githubusercontent.com/jup-ag/docs/main/portal/rate-limits.mdx>
- <https://raw.githubusercontent.com/jup-ag/docs/main/portal/api-keys.mdx>
- <https://raw.githubusercontent.com/jup-ag/docs/main/portal/firewall.mdx>
- <https://raw.githubusercontent.com/jup-ag/docs/main/swap/advanced/gasless.mdx>
- <https://raw.githubusercontent.com/jup-ag/docs/main/ultra/gasless.mdx>
- <https://raw.githubusercontent.com/jup-ag/docs/main/swap/advanced/slippage.mdx>
- <https://raw.githubusercontent.com/jup-ag/docs/main/ultra/execute-order.mdx>
- <https://raw.githubusercontent.com/jup-ag/docs/main/ultra/get-shield.mdx>
- <https://raw.githubusercontent.com/jup-ag/docs/main/tokens/token-information.mdx>
- <https://raw.githubusercontent.com/jup-ag/docs/main/price/index.mdx>
- <https://raw.githubusercontent.com/jup-ag/docs/main/transaction/submit.mdx>
- <https://raw.githubusercontent.com/jito-labs/jito-docs/main/docs/source/lowlatencytxnsend.md>
- <https://github.com/jito-labs/jito-js-rpc>
- <https://raw.githubusercontent.com/helius-labs/core-ai/main/helius-mcp/system-prompts/helius/full.md>
- <https://raw.githubusercontent.com/helius-labs/helius-sdk/main/README.md>
- <https://github.com/MetalLegBob/solana-vibes-kit/blob/main/grand-library/resources/domain-packs/solana/knowledge/rpc-provider-comparison.md>
- <https://raw.githubusercontent.com/solana-foundation/solana-com/main/apps/docs/content/docs/en/core/fees/index.mdx>
- <https://raw.githubusercontent.com/solana-foundation/solana-com/main/apps/docs/content/docs/en/core/constants-reference.mdx>
- <https://raw.githubusercontent.com/jup-ag/docs/main/swap/advanced/compute-units.mdx>
- /home/user/jonatangigex/familiars.family-opus5.5/src/executor.ts
- /home/user/jonatangigex/familiars.family-opus5.5/README.md
- <https://raw.githubusercontent.com/solana-foundation/solana-com/main/apps/docs/content/docs/en/tokens/extensions/index.mdx>
- <https://github.com/solana-program/token-2022/tree/main/program/src/extension>
- /home/user/jonatangigex/familiars.family-opus5.5/src/solana.ts
- <https://raw.githubusercontent.com/solana-foundation/solana-com/main/apps/docs/content/docs/en/rpc/http/gettransaction.mdx>
- /home/user/jonatangigex/familiars.family-opus5.5/src/familiars.ts
- /home/user/jonatangigex/familiars.family-opus5.5/src/rebuild.ts
- /home/user/jonatangigex/familiars.family-opus5.5/src/core.ts
- <https://raw.githubusercontent.com/thanhtoan0306/hyperliquid-docs-ssr/main/content/pages/for-developers/api/nonces-and-api-wallets.md>
- <https://raw.githubusercontent.com/thanhtoan0306/hyperliquid-docs-ssr/main/content/pages/for-developers/api/exchange-endpoint.md>
- <https://raw.githubusercontent.com/hyperliquid-dex/hyperliquid-python-sdk/master/README.md>
- <https://raw.githubusercontent.com/hyperliquid-dex/hyperliquid-python-sdk/master/examples/basic_agent.py>
- <https://raw.githubusercontent.com/thanhtoan0306/hyperliquid-docs-ssr/main/content/pages/for-developers/api/rate-limits-and-user-limits.md>
- <https://raw.githubusercontent.com/thanhtoan0306/hyperliquid-docs-ssr/main/content/pages/for-developers/api/websocket/timeouts-and-heartbeats.md>
- <https://raw.githubusercontent.com/hyperliquid-dex/hyperliquid-python-sdk/master/hyperliquid/api.py>
- <https://raw.githubusercontent.com/hyperliquid-dex/hyperliquid-python-sdk/master/pyproject.toml>
- <https://raw.githubusercontent.com/thanhtoan0306/hyperliquid-docs-ssr/main/content/pages/trading/fees.md>
- <https://raw.githubusercontent.com/thanhtoan0306/hyperliquid-docs-ssr/main/content/pages/trading/builder-codes.md>
- <https://raw.githubusercontent.com/thanhtoan0306/hyperliquid-docs-ssr/main/content/pages/trading/order-types.md>
- <https://raw.githubusercontent.com/thanhtoan0306/hyperliquid-docs-ssr/main/content/pages/trading/funding.md>
- <https://raw.githubusercontent.com/thanhtoan0306/hyperliquid-docs-ssr/main/content/pages/for-developers/api/priority-fees.md>
- <https://raw.githubusercontent.com/hyperliquid-dex/hyperliquid-python-sdk/master/examples/basic_tpsl.py>
- <https://raw.githubusercontent.com/ccxt/ccxt/master/ts/src/hyperliquid.ts>
- <https://raw.githubusercontent.com/thanhtoan0306/hyperliquid-docs-ssr/main/content/pages/hypercore/vaults/for-vault-leaders-legacy.md>
- <https://raw.githubusercontent.com/thanhtoan0306/hyperliquid-docs-ssr/main/content/pages/hypercore/vaults/for-vault-depositors-legacy.md>
- <https://raw.githubusercontent.com/thanhtoan0306/hyperliquid-docs-ssr/main/content/pages/onboarding/testnet-faucet.md>
- <https://raw.githubusercontent.com/thanhtoan0306/hyperliquid-docs-ssr/main/content/pages/hypercore/bridge.md>
- <https://raw.githubusercontent.com/hyperliquid-dex/hyperliquid-python-sdk/master/examples/basic_vault.py>
- <https://raw.githubusercontent.com/ccxt/ccxt/master/wiki/Manual.md>
- <https://raw.githubusercontent.com/ccxt/ccxt/master/ts/src/coinbase.ts>
- <https://raw.githubusercontent.com/ccxt/ccxt/master/ts/src/kraken.ts>
- <https://github.com/ccxt/ccxt>
- /home/user/jonatangigex/familiars.family-opus5.5/src/secrets.ts
- /home/user/jonatangigex/familiars.family-opus5.5/src/wallet.ts
- <https://raw.githubusercontent.com/docker/docs/main/content/manuals/engine/containers/start-containers-automatically.md>
- <https://raw.githubusercontent.com/systemd/systemd/main/man/systemd.service.xml>
- /home/user/jonatangigex/familiars.family-opus5.5/src/lock.ts
- /home/user/jonatangigex/familiars.family-opus5.5/src/state.ts
- /home/user/jonatangigex/familiars.family-opus5.5/Dockerfile
- <https://github.com/python-telegram-bot/python-telegram-bot/wiki/Avoiding-flood-limits>
- <https://raw.githubusercontent.com/hyperliquid-dex/hyperliquid-python-sdk/master/hyperliquid/info.py>

### risk-regulatory

- <https://github.com/alexnelja/dhando-analyzer/blob/main/research/kelly-criterion-probability-research.md>
- <https://github.com/leoncuhk/awesome-quant-ai/blob/main/think/Uncertainty-Driven%20Position%20Sizing.md>
- /home/user/jonatangigex/familiars.family-opus5.5/README.md (lines 87-119)
- <https://github.com/xxSeasonxx/quant_strategies/blob/main/docs/research/crypto/03_academic_literature.md>
- <https://github.com/HCH725/alpha-strategy-research/blob/main/crypto-cross-sectional-volatility-managed-momentum-2026-08-31.md>
- <https://doi.org/10.1111/jofi.12513>
- /home/user/jonatangigex/familiars.family-opus5.5/src/risk.ts
- <https://github.com/CodeGateSoftware/keel/blob/main/docs/superpowers/reports/2026-07-23-drawdown-taper-and-merton-exploration.md>
- <https://github.com/luke-cramer/ai-trading/blob/main/research/strat-crypto.md>
- <https://github.com/ernie55ernie/ernie55ernie.github.io/blob/master/_posts/2025-10-20-daily-crypto.md>
- <https://github.com/aakashgautam-git/Mochatrade/blob/main/MOCHATRADE_PS3_RESEARCH.md>
- <https://www.forbes.com/sites/boazsobrado/2025/10/21/locked-out-and-liquidated-traders-blame-binance-for-19-billion-crash/> (cited, not fetched)
- <https://www.fticonsulting.com/insights/articles/crypto-crash-october-2025-leverage-met-liquidity> (cited, not fetched)
- <https://github.com/Hyperliquid-Community/wiki-community/blob/main/introduction/roadmap/incident/2025-26-03.md>
- <https://github.com/sohan-shingade/soledu/blob/main/docs/hyperliquid-mev-research.md>
- <https://github.com/truenorth-lj/crypto-project-security-skill/blob/main/docs/examples/hyperliquid-perps.md>
- <https://github.com/ernie55ernie/ernie55ernie.github.io/blob/master/_posts/2025-11-13-daily-crypto.md>
- <https://www.coindesk.com/markets/2025/03/26/hyperliquid-delists-jellyjelly-after-vault-squeezed-in-usd13m-tussle> (cited, not fetched)
- <https://github.com/emberian/dregg/blob/main/docs/deos/DREGG-LAUNCHPAD-DESIGN.md>
- <https://www.coindesk.com/business/2025/05/07/98-of-tokens-on-pump-fun-have-been-rug-pulls-or-an-act-of-fraud-new-report-says> (cited, not fetched)
- <https://www.soliduslabs.com/reports/solana-rug-pulls-pump-dumps-crypto-compliance> (cited, not fetched)
- <https://github.com/solana-foundation/solana-com/blob/main/apps/docs/content/docs/en/tokens/extensions/permanent-delegate.mdx>
- <https://github.com/solana-foundation/solana-com/blob/main/apps/docs/content/docs/en/tokens/extensions/transfer-hook.mdx>
- <https://github.com/solana-program/token-2022/blob/main/program/src/extension/mod.rs>
- <https://github.com/jup-ag/docs/blob/main/ultra/get-shield.mdx>
- <https://github.com/jup-ag/docs/blob/main/tokens/index.mdx>
- /home/user/jonatangigex/familiars.family-opus5.5/src/executor.ts
- <https://github.com/solana-labs/solana-web3.js/security/advisories/GHSA-jcxm-7wvp-g6p5>
- <https://registry.npmjs.org/@solana/web3.js>
- <https://registry.npmjs.org/chalk>
- <https://registry.npmjs.org/@ctrl/tinycolor>
- <https://github.com/chalk/chalk/issues/656>
- <https://github.com/debug-js/debug/issues/1005>
- <https://github.com/MetalLegBob/solana-vibes-kit/blob/main/stronghold-of-security/research/wave3/w3-incident-deep-dives.md>
- <https://github.com/hyperliquid-dex/hyperliquid-python-sdk/blob/master/examples/basic_agent.py>
- <https://github.com/Squads-Protocol/v4>
- /home/user/jonatangigex/familiars.family-opus5.5/src/familiars.ts
- <https://arxiv.org/abs/2503.16248> (cited; abstract read via https://github.com/santosomar/ai_news_archive)
- <https://github.com/OWASP/www-project-top-10-for-large-language-model-applications/blob/main/2_0_vulns/LLM01_PromptInjection.md>
- <https://github.com/OWASP/www-project-top-10-for-large-language-model-applications/blob/main/2_0_vulns/LLM06_ExcessiveAgency.md>
- <https://github.com/GenAI-Security-Project/GenAI-LLM-Top10>
- <https://github.com/killertcell428/aigis/blob/main/aigis/filters/patterns.py>
- /home/user/jonatangigex/familiars.family-opus5.5/src/solana.ts
- <https://github.com/openaccountants/openaccountants/blob/main/agent-skills/us-crypto-tax/SKILL.md>
- <https://github.com/knucklefat/Occupy-AI/blob/main/11-crypto/08-tax-and-regulation/tax-loss-harvesting-and-planning.md>
- <https://www.irs.gov/pub/irs-drop/rp-24-28.pdf> (cited, not fetched)
- <https://github.com/openaccountants/openaccountants/blob/main/packages/us-dc/us-crypto-reporting.md>
- <https://www.federalregister.gov/documents/2024/07/09/2024-14004/> (cited, not fetched)
- <https://www.federalregister.gov/documents/2025/07/11/2025-12967/> (cited, not fetched)
- <https://github.com/knucklefat/Occupy-AI/blob/main/11-crypto/08-tax-and-regulation/crypto-tax-basics.md>
- <https://www.cnbc.com/2026/07/28/congress-renews-push-to-end-crypto-wash-sale-tax-loophole.html> (cited, not fetched)
- <https://www.lummis.senate.gov/press-releases/lummis-unveils-digital-asset-tax-legislation/> (cited, not fetched)
- <https://github.com/openaccountants/openaccountants/blob/main/packages/uk/uk-crypto-tax.md>
- <https://github.com/openaccountants/openaccountants/blob/main/skills/international/germany/de-crypto-tax.md>
- <https://github.com/BittyTax/BittyTax/blob/master/README.md>
- <https://www.gov.uk/hmrc-internal-manuals/cryptoassets-manual/crypto22200> (cited, not fetched)
- <https://github.com/ernie55ernie/ernie55ernie.github.io/blob/master/_posts/2026-01-06-daily-crypto.md>
- <https://www.sec.gov/newsroom/press-releases/2024-125> (cited from recollection, not fetched)
- <https://github.com/ernie55ernie/ernie55ernie.github.io/blob/master/_posts/2026-03-04-daily-crypto.md>
- <https://github.com/ernie55ernie/ernie55ernie.github.io/blob/master/_posts/2026-03-26-daily-crypto.md>
- <https://github.com/ernie55ernie/ernie55ernie.github.io/blob/master/_posts/2026-02-04-daily-crypto.md>
- <https://github.com/luke-cramer/ai-trading/blob/main/research/crypto-us-venues.md>
- <https://www.jdsupra.com/legalnews/cftc-permits-listing-of-perpetual-3447034/> (cited, not fetched)
- <https://www.pillsburylaw.com/en/news-and-insights/cftc-perpetual-futures-btc-eth-crypto-derivatives.html> (cited, not fetched)
- <https://github.com/luke-cramer/ai-trading/blob/main/research/gap-2.md>
- <https://app.hyperliquid.xyz/terms> (cited, not fetched)
- <https://github.com/hyperliquid-dex>
- <https://github.com/rya-sge/access-denied/blob/master/_posts/2026-09-17-mica-market-abuse-enforcement-supervision.md>
- <https://github.com/ernie55ernie/ernie55ernie.github.io/blob/master/_posts/2026-01-24-daily-crypto.md>
- <https://github.com/github/docs/blob/main/content/site-policy/github-terms/github-terms-for-additional-products-and-features.md>
- <https://cloud.google.com/terms/aup>

### offline study (data provenance, MANIFEST.md)

- <https://github.com/finom/static-klines>
- <https://github.com/yanniedog/binance-historical-OHLCV-data>
- <https://github.com/Swissbit92/btc_price_tracker>
- <https://github.com/jssyxd/nautilus-crypto-datacatalog>
- <https://github.com/supervik/historical-funding-rates-fetcher>
- <https://github.com/ZuShen168/funding_rate_data>
- <https://github.com/Caiooooo/anay_hyper_fund>
- <https://github.com/pattybepatient/crypto-funding-arb>
- <https://github.com/sunsiyuan/trade-signal-bot>
- <https://github.com/Color2333/a-r>
- <https://github.com/Smurfetc/solana-memecoin-calls-dataset>
- <https://github.com/nikolan17/pumpfun-call-analyzer>
