# Tiller

An autonomous, risk-managed crypto trading agent for one Solana wallet, built to be
run unattended on a small VPS (or from a Claude Code routine) and to publish its trades
on [familiars.family](https://familiars.family/#/home), the "FOMO for AI agents" board.

Tiller was designed from a sourced research pass over every strategy family a retail
agent could run (trend, mean reversion, carry, market making, memecoin launches,
on-chain and CEX copy-trading, LLM-driven trading) and an adversarial review of each
candidate. Read `docs/RESEARCH.md` for the evidence, `docs/SPEC.md` for the resulting
design, and `docs/RUNBOOK.md` before you fund anything.

## What it trades

Two live sleeves, both long-only SOL versus USDC, executed as Jupiter Swap V2 swaps
from the agent wallet, evaluated once per day on closed daily bars:

| Sleeve | Rule | Allocation |
|---|---|---|
| `sol_trend_ensemble` | Seven Donchian breakout sleeves (10 to 250 days) with midpoint trailing stops, sized to a 25% annualised volatility target | 40% |
| `sol_regime_switch` | Hold SOL when the close is above its 200-day SMA (2% hysteresis band), else USDC | 25% |
| cash floor | Raw USDC plus a 0.05 SOL gas reserve, never deployed | 30% |
| `copy_consensus` | Copy-trading of familiars agents and on-chain wallets. **Shadow mode only:** records leaders' trades and hypothetical copier P&L, holds no capital until a promotion gate is met | 5% budget, 0% deployed |

The allocator nets both sleeves into one SOL target weight, caps portfolio SOL beta at
`min(0.50, 0.30 / realised 90-day vol)`, rebalances only when the target moves by more
than 2% of equity, and never breaches the cash floor. A risk engine sits above all of
it: daily-loss and drawdown brakes, owner limits read from familiars that fail closed,
a canary ramp for the first live weeks, a `KILL` file, loop guards, and a
simulate-before-sign guard on every transaction.

## Measured, not promised

The two sleeves are exactly the rules measured offline on real Binance daily data
(SOL/USDT 2021-04 to 2026-09-23, 30 bps per side). `tiller backtest` reproduces these
numbers from the bundled history with the same functions the live agent runs, and the
test suite asserts them.

| Rule | CAGR | Sharpe | Max drawdown | 2022 | 2025 | 2026 YTD | Last 24m Sharpe |
|---|---|---|---|---|---|---|---|
| Buy and hold SOL | 26.2% | 0.74 | -96.3% | -94.1% | -34.2% | -7.7% | 0.23 |
| `sol_regime_switch` | 57.9% | 0.98 | -69.1% | -20.7% | -17.4% | +34.3% | 0.44 |
| Donchian ensemble, unscaled | 60.8% | 1.20 | -45.2% | -23.1% | -10.7% | +7.2% | 0.17 |
| `sol_trend_ensemble` (25% vol target) | 14.8% | 1.20 | -14.2% | -6.0% | -3.6% | +4.5% | 0.27 |
| **Combined default (what the allocator runs)** | **19.3%** | **0.95** | **-25.3%** | **-10.7%** | **-4.9%** | **+12.3%** | **0.39** |

Full tables (cost sensitivity, BTC confirmation gate, rebalance band) are in
`docs/BACKTEST.md`. The BTC-confirmation gate defaults to **off** by a deterministic
rule recorded there: it improved the ensemble but slightly worsened the regime switch
over the last 24 months.

Honest expectations for a $500 to $20,000 wallet: roughly 8 to 25% a year in trending
years, about 0 to 8% in whipsaw years like 2025, 15 to 25% maximum drawdown, and flat
spells of 6 to 12 months. Trend following halves drawdowns mainly by sitting in USDC
through bear markets; it lags buy-and-hold in manias. Nothing here is investment advice.

## What it will not do

- It will not rank near the top of familiars' absolute-USD leaderboard. On
  2026-09-24 only 37 of about 1,350 agents were profitable, and the winners were
  concentrated narrative bets or agents' own tokens (which the platform's skill
  forbids). A braked small wallet cannot compete with that and does not try.
- It will not copy-trade with real money by default. Every copy-trading variant was
  refuted by the evidence review; the shadow tracker exists to build the missing
  evidence and prints a report (`tiller shadow-report`). Promotion requires 60 days,
  100 shadow trades, positive expectancy after modelled costs, and your explicit
  config change.
- It will not snipe launches, trade tokens under 24 hours old, use leverage, or
  trade perps. Hyperliquid and CEX modules exist only as disabled stubs that print a
  jurisdiction warning; Hyperliquid's terms bar US and Ontario persons.
- It will not let an LLM size, enter or exit trades. The optional Claude narrator only
  writes the mandatory post text under a daily cost cap; the default narrator is a
  deterministic template.

## Risk statement

The agent wallet is a hot key. Solana has no delegation primitive, so whoever holds
the key controls the funds. Keep only working capital in it (10 to 20% of what you
trade), sweep profits to cold storage with `tiller sweep`, keep the key in a 0600 file
outside the repo, and pin dependencies (the supply-chain attacks on
`@solana/web3.js` in Dec 2024 and on npm in Sep 2025 targeted exactly this kind of
bot). Every swap is a taxable event in most jurisdictions; `tiller export-tax` dumps
the ledger.

## Quick start

```
make install                     # creates .venv, installs pinned deps
make test                        # 400+ offline tests, no network
tiller init                      # writes config/tiller.toml from the example
tiller backtest                  # regenerates docs/BACKTEST.md from bundled data
tiller keygen                    # new agent wallet (0600 file outside the repo)
tiller run --mode paper          # real quotes, simulated fills, no signing
```

Then follow `docs/RUNBOOK.md`: at least 14 days of paper trading, the familiars
`skill.md` checklist, `tiller register`, `tiller preflight --record`, a canary live
run at tiny owner limits, and a weekly ramp.

## Layout

```
src/tiller/            the package (config, models, ledger, state, strategies, portfolio,
                       risk, execution, data, familiars, copy, engine, cli)
tests/                 offline tests with recorded-shape fixtures and in-memory fakes
data/history/          bundled daily BTC/ETH/SOL history with provenance
config/                tiller.example.toml, owner_overrides.example.json
docs/                  RESEARCH.md, SPEC.md, BACKTEST.md, RUNBOOK.md, COPY-SHADOW.md,
                       familiars-api.md
deploy/                systemd unit; Dockerfile at the root
```

## Status

Built in September 2026 in an offline sandbox. All API fixtures are hand-built from the
documented contracts; nothing was recorded against live endpoints. Before any live
trade, run `tools/record_fixtures.py` from a networked machine and re-read
`https://familiars.family/skill.md`; the open questions that only that document can
answer are listed at the end of `docs/SPEC.md`.
