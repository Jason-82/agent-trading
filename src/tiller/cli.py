"""``tiller`` command line: the only place real dependencies are wired.

Commands: init | keygen | register | preflight | backtest | tick --once | run | status |
shadow-report | pause | resume | flatten --yes | reconcile [--accept] | sweep --to --keep --yes |
export-tax | review. Every command exits 0 on success, 1 on a runtime error and 2 when it
refuses to act (missing acknowledgement, missing ``--yes``, existing key, ...).

Modes: ``live`` signs with :class:`FileSigner`; ``paper`` uses live quotes with a
:class:`NullSigner` and a paper book; ``offline`` replays bundled candles through a
:class:`FixtureVenue`. Paper/offline runs use their own ledger/state files (``-<mode>``
suffix) so they never mix with live records.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import importlib
import json
import os
import shutil
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import httpx

from tiller import __version__
from tiller.alerts import Alerts
from tiller.clock import Clock, SimClock, SystemClock, ensure_utc, utc_day_start
from tiller.config import ENV_FAMILIARS_KEY, Config, load_config
from tiller.data.candles import (
    CoinbaseCandles,
    CsvCandles,
    DailyCandleStore,
    KrakenCandles,
    read_candles_csv,
)
from tiller.data.prices import JupiterPriceV3, KrakenTicker
from tiller.data.tokens import TokenData
from tiller.engine import Agent, CandlePrices, DayOpenClock, Deps, TickReport
from tiller.execution.guard import DEFAULT_PROGRAM_ALLOWLIST, GuardCfg, derive_ata
from tiller.execution.jupiter import JupiterSwapV2
from tiller.execution.rpc import HttpRpc
from tiller.execution.venue import ExecCfg, FixtureVenue, LiveSolanaVenue, PaperVenue
from tiller.execution.wallet import FileSigner, KeyLoadError, NullSigner, Signer, generate_keypair
from tiller.familiars.client import FamiliarsClient, RegisterRequest, encode_signature
from tiller.familiars.narrator import ClaudeNarrator, Narrator, TemplateNarrator
from tiller.familiars.poster import PostQueue
from tiller.ledger import Ledger
from tiller.models import SOL_MINT, TOKEN_PROGRAM, USDC_DECIMALS, USDC_MINT
from tiller.risk.reconcile import reconcile, summarize
from tiller.state import InstanceLock, LockHeld, load_state, save_state
from tiller.strategies.sol_trend import SolRegimeSwitchStrategy, SolTrendEnsembleStrategy
from tiller.venues_optional import check_optional_venues

Mode = Literal["offline", "paper", "live"]
DEFAULT_CONFIG = Path("config/tiller.toml")
EXAMPLE_CONFIG = Path("config/tiller.example.toml")
EXAMPLE_OVERRIDES = Path("config/owner_overrides.example.json")
LOCAL_OVERRIDES = Path("config/owner_overrides.json")
SECRETS_FILENAME = "familiars.secret"
KILL_FILENAME = "KILL"
ATA_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
SKILL_MD_URL = "https://familiars.family/skill.md"
SKILL_MD_CHECKLIST = (
    "posting obligations and rate limits",
    "exact own-token wording and whether other agents' tokens may be traded",
    "P&L accounting for illiquid holdings; whether LST/kToken receipts count as equity",
    "copy-trading disclosure requirements",
    "multi-wallet and wash-trading rules",
    "any platform fees",
    "API-key rotation procedure",
)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REFUSED = 2


class Refused(Exception):
    """The command refuses to act (exit code 2)."""


# --------------------------------------------------------------------------- paths


def state_paths(cfg: Config, mode: Mode) -> dict[str, Path]:
    """State, ledger, lock and KILL file locations for ``mode`` (non-live modes are suffixed)."""
    d = Path(cfg.paths.state_dir)
    suffix = "" if mode == "live" else f"-{mode}"
    ledger = Path(cfg.paths.ledger_path)
    return {
        "state_dir": d,
        "state": d / f"state{suffix}.json",
        "ledger": ledger.with_name(f"{ledger.stem}{suffix}{ledger.suffix}"),
        "lock": d / f"tiller{suffix}.lock",
        "kill": d / KILL_FILENAME,
        "candles": d / "candles",
        "secrets": d / SECRETS_FILENAME,
        "paper_key": d / "paper-identity.json",
    }


def _load_cfg(args: argparse.Namespace) -> Config:
    path = Path(args.config)
    if not path.exists():
        raise Refused(f"config {path} not found; run `tiller init` first")
    return load_config(path, os.environ)


def _mode(cfg: Config, args: argparse.Namespace) -> Mode:
    m = getattr(args, "mode", None) or cfg.mode
    if m == "offline" or m == "paper" or m == "live":
        return m
    raise Refused(f"unknown mode {m}")


def _clock(args: argparse.Namespace) -> Clock:
    at = getattr(args, "at", None)
    if at:
        return SimClock(ensure_utc(datetime.fromisoformat(at.replace("Z", "+00:00"))))
    return SystemClock()


# --------------------------------------------------------------------------- wiring


def build_guard_cfg(cfg: Config) -> GuardCfg:
    g = cfg.guard
    data = {k: v for k, v in g.model_dump().items() if k not in ("token_gate_established", "token_gate_copy")}
    data["routers"] = set(g.routers)
    data["program_allowlist"] = set(g.program_allowlist) | set(DEFAULT_PROGRAM_ALLOWLIST)
    return GuardCfg(**data)


def build_exec_cfg(cfg: Config) -> ExecCfg:
    return ExecCfg(
        slippage_bps_sol_usdc=cfg.jupiter.slippage_bps_sol_usdc,
        slippage_bps_token=cfg.jupiter.slippage_bps_token,
        slippage_bps_emergency=cfg.jupiter.slippage_bps_emergency,
        sol_reserve_lamports=cfg.wallet.sol_reserve_lamports,
        allowlist_mints={SOL_MINT, USDC_MINT, cfg.strategies.hold_mint},
        max_round_trip_token=Decimal(cfg.guard.token_gate_established.max_round_trip_pct),
    )


def build_strategies(cfg: Config) -> list[Any]:
    out: list[Any] = []
    s = cfg.strategies
    if s.sol_trend_ensemble.enabled:
        out.append(SolTrendEnsembleStrategy(s.sol_trend_ensemble, s.hold_mint))
    if s.sol_regime_switch.enabled:
        out.append(SolRegimeSwitchStrategy(s.sol_regime_switch, s.hold_mint))
    return out


def load_signer(cfg: Config, mode: Mode, paths: dict[str, Path], env: dict[str, str] | None = None) -> Signer:
    """Live: the real keypair. Paper/offline: a NullSigner carrying a pubkey (from the configured
    keypair if any, else a persisted paper identity used only for quotes)."""
    environ = os.environ if env is None else env
    secret = environ.get(cfg.wallet.env_secret_var)
    if mode == "live":
        return FileSigner.load(cfg.wallet.keypair_path, secret)
    if cfg.wallet.keypair_path or secret:
        try:
            return NullSigner(FileSigner.load(cfg.wallet.keypair_path, secret).pubkey)
        except KeyLoadError:
            pass
    key = paths["paper_key"]
    if not key.exists():
        generate_keypair(key)
    return NullSigner(FileSigner.load(key, None).pubkey)


def _narrator(cfg: Config, clock: Clock, ledger: Ledger) -> Narrator:
    if cfg.llm.narrator == "claude" and cfg.llm.api_key is not None:
        return ClaudeNarrator(
            cfg.llm.api_key.get_secret_value(),
            cfg.llm.model,
            cfg.llm.daily_cap_usd,
            TemplateNarrator(),
            clock,
            timeout_s=cfg.llm.timeout_s,
            log=lambda k, p: ledger.add_event("info", k, p),
        )
    return TemplateNarrator()


class _CopyFeedAdapter:
    """Engine hook ``poll(now)`` over WP-E's ``FamiliarsFeed`` / ``WalletFeed`` (``poll(keys)``).

    Every chain-verified leader trade is handed to the shadow tracker with the followed set as
    the pool and the consensus mints of the window. Leader SCORING (eligibility, replay, clusters,
    sticky pool) is not wired here: the pool is the followed handles / configured wallets and each
    key is its own cluster, so shadow trades are recorded but no promotion can pass without WP-E's
    scoring pass.
    """

    def __init__(self, feed: Any, keys_fn: Callable[[], Any], shadow: Any, cfg: Config) -> None:
        self.feed = feed
        self.keys_fn = keys_fn
        self.shadow = shadow
        self.cfg = cfg

    async def poll(self, now: datetime) -> None:
        from tiller.copy.strategy import consensus

        keys = list(await self.keys_fn())
        if not keys:
            return
        trades = await self.feed.poll(keys)
        if not trades:
            return
        pool = {t.key for t in trades}
        clusters = {k: i for i, k in enumerate(sorted(pool))}
        signals = consensus(
            trades,
            pool,
            clusters,
            timedelta(minutes=self.cfg.copy.window_min),
            self.cfg.copy.min_clusters,
            now,
        )
        await self.shadow.on_leader_trades(trades, pool, [s.mint for s in signals])


class _ShadowAdapter:
    """Engine hooks ``mark(now)`` / ``rescore(now)`` over WP-E's ``ShadowTracker``."""

    def __init__(self, tracker: Any) -> None:
        self.tracker = tracker

    async def mark(self, now: datetime) -> None:
        await self.tracker.mark_and_exit()

    async def rescore(self, now: datetime) -> None:
        return None  # leader re-scoring needs WP-E's mark store; see the runbook

    def report(self, days: int = 60) -> Any:
        return self.tracker.report(days)


def _copy_hooks(
    cfg: Config,
    ledger: Ledger,
    clock: Clock,
    *,
    familiars: Any | None,
    rpc: Any | None,
    prices: Any | None,
    tokens: Any | None,
    jup: Any | None,
    taker: str,
) -> tuple[Any | None, tuple[Any | None, Any | None]]:
    """WP-E shadow tracker and feeds when the copy package is installed and shadowing is on."""
    if not cfg.copy.shadow_enabled or rpc is None or prices is None or tokens is None:
        return None, (None, None)
    try:
        from tiller.copy.feeds import FamiliarsFeed, WalletFeed
        from tiller.copy.shadow import ShadowTracker
    except Exception:
        return None, (None, None)
    tracker = ShadowTracker(jup, prices, tokens, ledger, cfg.copy, clock, taker=taker)
    shadow = _ShadowAdapter(tracker)
    fam_feed = None
    if familiars is not None and cfg.familiars.handle:

        async def followed() -> list[str]:
            agents = await familiars.agents("7D")
            return [a.handle for a in agents if a.handle != cfg.familiars.handle][: cfg.copy.followed_n]

        fam_feed = _CopyFeedAdapter(FamiliarsFeed(familiars, rpc, ledger, clock), followed, tracker, cfg)
    wallet_feed = None
    if cfg.copy.external_wallets:

        async def wallets() -> list[str]:
            return list(cfg.copy.external_wallets)

        wallet_feed = _CopyFeedAdapter(WalletFeed(rpc, ledger, clock, prices=prices), wallets, tracker, cfg)
    return shadow, (fam_feed, wallet_feed)


def build_deps(
    cfg: Config, mode: Mode, clock: Clock, paths: dict[str, Path], *, http: httpx.AsyncClient | None = None
) -> tuple[Deps, Agent]:
    """Wire every real dependency for ``mode`` and return the agent."""
    paths["state_dir"].mkdir(parents=True, exist_ok=True)
    ledger = Ledger(paths["ledger"], clock=clock)
    signer = load_signer(cfg, mode, paths)
    pubkey = signer.pubkey
    client = http or httpx.AsyncClient(timeout=20.0)
    alerts = Alerts(
        cfg.alerts.telegram_token.get_secret_value() if cfg.alerts.telegram_token else None,
        cfg.alerts.chat_id,
        client,
    )
    jup_key = cfg.jupiter.api_key.get_secret_value() if cfg.jupiter.api_key else None
    strategies = build_strategies(cfg)
    familiars = None
    poster = None
    if cfg.familiars.enabled and cfg.familiars.api_key is not None:
        familiars = FamiliarsClient(
            cfg.familiars.base_url,
            cfg.familiars.api_key.get_secret_value(),
            cfg.familiars.rps,
            client,
            ledger,
            clock,
        )
        if cfg.familiars.handle:
            poster = PostQueue(
                familiars,
                _narrator(cfg, clock, ledger),
                ledger,
                clock,
                cfg.familiars.handle,
                callouts_per_day=cfg.familiars.callouts_per_day,
            )
    local = LOCAL_OVERRIDES if LOCAL_OVERRIDES.exists() else None
    venue: Any
    prices: Any
    if mode == "offline":
        csv_dir = cfg.data.csv_dir or Path("data/history")
        bars = {cfg.strategies.hold_mint: read_candles_csv(Path(csv_dir) / "SOLUSDT_1d.csv")}
        prices = CandlePrices(bars, clock)
        venue = FixtureVenue(bars, ledger, 30, DayOpenClock(clock))
        candles = DailyCandleStore([CsvCandles(Path(csv_dir))], paths["candles"], clock, fetch_limit=0)
        deps = Deps(
            venue=venue,
            rpc=None,
            candles=candles,
            prices=prices,
            cex=None,
            tokens=None,
            familiars=None,
            ledger=ledger,
            strategies=strategies,
            poster=None,
            shadow=None,
            feeds=(None, None),
            alerts=alerts,
            signer_pubkey=pubkey,
            local_overrides=local,
        )
        return deps, Agent(cfg, deps, clock, paths["state"], paths["kill"])
    rpc = HttpRpc(cfg.rpc.urls, cfg.rpc.rps, client, ledger, clock=clock)
    jup = JupiterSwapV2(cfg.jupiter.base_url, jup_key, cfg.jupiter.rps, client, ledger, clock=clock)
    tokens = TokenData(client, rpc, cfg.jupiter.base_url, jup_key, cfg.jupiter.rps, ledger, clock)
    prices = JupiterPriceV3(cfg.jupiter.base_url, jup_key, cfg.jupiter.rps, client)
    cex = KrakenTicker(client, 1.0)
    sources: list[Any] = []
    for name in cfg.data.candle_sources:
        if name == "kraken":
            sources.append(KrakenCandles(client, clock))
        elif name == "coinbase":
            sources.append(CoinbaseCandles(client, clock))
        elif name == "csv" and cfg.data.csv_dir is not None:
            sources.append(CsvCandles(Path(cfg.data.csv_dir)))
    candles = DailyCandleStore(sources, paths["candles"], clock)
    exec_cfg = build_exec_cfg(cfg)
    guard = build_guard_cfg(cfg)
    shadow, feeds = _copy_hooks(
        cfg, ledger, clock, familiars=familiars, rpc=rpc, prices=prices, tokens=tokens, jup=jup, taker=pubkey
    )
    if mode == "live":
        venue = LiveSolanaVenue(jup, rpc, signer, tokens, ledger, exec_cfg, guard, clock)
    else:
        venue = PaperVenue(jup, tokens, ledger, 30, clock, taker=pubkey, cfg=exec_cfg, guard=guard)
    deps = Deps(
        venue=venue,
        rpc=rpc,
        candles=candles,
        prices=prices,
        cex=cex,
        tokens=tokens,
        familiars=familiars,
        ledger=ledger,
        strategies=strategies,
        poster=poster,
        shadow=shadow,
        feeds=feeds,
        alerts=alerts,
        signer_pubkey=pubkey,
        local_overrides=local,
        error_rate_sources=[rpc, jup],
    )
    return deps, Agent(cfg, deps, clock, paths["state"], paths["kill"])


# --------------------------------------------------------------------------- live preflight


def live_preflight(cfg: Config, mode: Mode, paths: dict[str, Path], ack_flag: bool, clock: Clock) -> None:
    """Refuse a live start unless every acknowledgement is in place (persisting the first one)."""
    check_optional_venues(cfg)
    if mode != "live":
        return
    if cfg.mode != "live":
        raise Refused(
            "config mode is not 'live'; set mode = \"live\" (and skill_md_reviewed_at) in the config"
        )
    if cfg.familiars.enabled:
        reviewed = cfg.familiars.skill_md_reviewed_at
        if reviewed is None or (clock.now().date() - reviewed).days > 30:
            raise Refused("familiars.skill_md_reviewed_at is missing or older than 30 days")
        if cfg.familiars.api_key is None or not cfg.familiars.handle:
            raise Refused(
                "live mode with familiars enabled needs familiars.api_key and handle (run `tiller register`)"
            )
    state = load_state(paths["state"])
    if not state.live_ack_recorded:
        if not ack_flag:
            raise Refused(
                "first live start requires --i-have-read-skill-md; verify the checklist at "
                f"{SKILL_MD_URL}: " + "; ".join(SKILL_MD_CHECKLIST)
            )
        state.live_ack_recorded = True
        save_state(paths["state"], state)


# --------------------------------------------------------------------------- commands


def cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.config)
    if target.exists() and not args.force:
        raise Refused(f"{target} already exists (use --force to overwrite)")
    example = Path(args.example)
    if not example.exists():
        raise Refused(f"example config {example} not found")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(example, target)
    print(f"wrote {target}")
    if not LOCAL_OVERRIDES.exists() and EXAMPLE_OVERRIDES.exists():
        shutil.copyfile(EXAMPLE_OVERRIDES, LOCAL_OVERRIDES)
        print(f"wrote {LOCAL_OVERRIDES}")
    try:
        cfg = load_config(target, os.environ)
        Path(cfg.paths.state_dir).mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"note: state dir not created ({e})")
    return EXIT_OK


def cmd_keygen(args: argparse.Namespace) -> int:
    path = Path(args.path)
    pubkey = generate_keypair(path)
    print(f"wrote keypair to {path} (0600); public key {pubkey}")
    print("Fund WORKING CAPITAL ONLY; keep the directory 0700 and outside the repository.")
    return EXIT_OK


def _existing_fam_key(cfg: Config, paths: dict[str, Path]) -> bool:
    return (
        cfg.familiars.api_key is not None
        or bool(os.environ.get(ENV_FAMILIARS_KEY))
        or paths["secrets"].exists()
    )


def cmd_register(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args)
    paths = state_paths(cfg, "live")
    if _existing_fam_key(cfg, paths):
        raise Refused(
            "a familiars API key already exists (config, environment or secrets file); refusing to register twice"
        )
    signer = FileSigner.load(cfg.wallet.keypair_path, os.environ.get(cfg.wallet.env_secret_var))
    clock = SystemClock()
    paths["state_dir"].mkdir(parents=True, exist_ok=True)
    ledger = Ledger(paths["ledger"], clock=clock)

    async def go() -> int:
        async with httpx.AsyncClient(timeout=20.0) as http:
            client = FamiliarsClient(cfg.familiars.base_url, None, cfg.familiars.rps, http, ledger, clock)
            ch = await client.challenge(signer.pubkey)
            sig = signer.sign_message(ch.message.encode("utf-8"))
            req = RegisterRequest(
                wallet=signer.pubkey,
                nonce=ch.nonce,
                signature_b64=encode_signature(sig),
                handle=args.handle,
                name=args.name,
                bio=args.bio,
                strategy=args.strategy,
                color=args.color,
            )
            try:
                res = await client.register(req)  # exactly one attempt, never retried
            except httpx.HTTPError as e:
                print(
                    f"network error AFTER the registration was sent ({type(e).__name__}); the nonce is "
                    "single-use: check the familiars dashboard before trying again"
                )
                return EXIT_ERROR
        secrets = paths["secrets"]
        fd = os.open(secrets, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(f"{ENV_FAMILIARS_KEY}={res.api_key}\nTILLER_FAMILIARS_OWNER_KEY={res.owner_key}\n")
        print(f"registered handle {res.handle}; secrets written ONCE to {secrets} (0600)")
        print(f"login url (shown once): {res.login_url}")
        print(
            f'export {ENV_FAMILIARS_KEY} from that file and set familiars.handle = "{res.handle}" in the config'
        )
        return EXIT_OK

    return asyncio.run(go())


def cmd_preflight(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args)
    clock = SystemClock()
    paths = state_paths(cfg, "live")
    print(f"tiller {__version__}: mode={cfg.mode}, familiars={'on' if cfg.familiars.enabled else 'off'}")
    print("skill.md checklist (verify at " + SKILL_MD_URL + "):")
    for item in SKILL_MD_CHECKLIST:
        print(f"  [ ] {item}")
    problems: list[str] = []
    try:
        live_preflight(cfg, "live", paths, ack_flag=load_state(paths["state"]).live_ack_recorded, clock=clock)
    except Refused as e:
        problems.append(str(e))
    except NotImplementedError as e:
        problems.append(str(e))
    if cfg.mode == "live":
        try:
            FileSigner.load(cfg.wallet.keypair_path, os.environ.get(cfg.wallet.env_secret_var))
        except KeyLoadError as e:
            problems.append(f"keypair: {e}")
    if args.record:
        try:
            rec = importlib.import_module("tools.record_fixtures")
        except Exception:
            print("--record: tools/record_fixtures.py is not present (WP-F deliverable); nothing recorded")
        else:
            fn = getattr(rec, "main", None)
            if fn is not None:
                return int(fn([]) or 0)
    for p in problems:
        print(f"PROBLEM: {p}")
    print(
        "preflight "
        + ("FAILED" if problems else "OK (paper checks only; live also needs the acknowledgement flag)")
    )
    return EXIT_REFUSED if problems else EXIT_OK


def cmd_backtest(args: argparse.Namespace) -> int:
    from tiller.backtest.__main__ import main as backtest_main

    argv = ["--cost-bps", args.cost_bps, "--out", str(args.out)]
    if args.csv_dir:
        argv += ["--csv-dir", str(args.csv_dir)]
    return int(backtest_main(argv))


def _print_report(report: TickReport) -> None:
    payload = {
        "ts": report.ts.isoformat(),
        "equity_usd": str(report.equity_usd.quantize(Decimal("0.01"))),
        "entries_blocked": report.brakes.entries_blocked,
        "reasons": report.brakes.reasons,
        "daily_evaluated": report.daily_evaluated,
        "intents": [f"{i.side} {i.mint[:6]} {i.usd:.2f} ({i.strategy})" for i in report.intents],
        "fills": [
            f"{f.in_mint[:6]}->{f.out_mint[:6]} {f.usd_in:.2f} sig={f.signature}" for f in report.fills
        ],
        "refused": [f"{i.side} {i.mint[:6]} {i.usd:.2f}: {why}" for i, why in report.refused],
        "flattened": report.flattened,
        "halted": report.halted,
        "limits": report.limits.model_dump(mode="json") if report.limits else None,
        "notes": report.notes,
    }
    print(json.dumps(payload, indent=1, default=str))


def _run_agent(args: argparse.Namespace, *, once: bool) -> int:
    cfg = _load_cfg(args)
    mode = _mode(cfg, args)
    clock = _clock(args)
    paths = state_paths(cfg, mode)
    live_preflight(cfg, mode, paths, ack_flag=bool(getattr(args, "i_have_read_skill_md", False)), clock=clock)
    paths["state_dir"].mkdir(parents=True, exist_ok=True)
    lock = InstanceLock(paths["lock"], clock)
    try:
        lock.acquire()
    except LockHeld as e:
        raise Refused(str(e)) from e
    try:
        deps, agent = build_deps(cfg, mode, clock, paths)
        deps.lock = lock
        if getattr(args, "paper_capital_usd", None) is not None:
            agent.paper_capital_usd = Decimal(str(args.paper_capital_usd))

        async def go() -> int:
            if once:
                report = await agent.tick()
                _print_report(report)
                return EXIT_OK
            print(f"tiller run: mode={mode} wallet={deps.signer_pubkey} state={paths['state']}")
            await agent.run_forever(max_ticks=args.max_ticks)
            return EXIT_OK

        return asyncio.run(go())
    finally:
        lock.release()


def cmd_tick(args: argparse.Namespace) -> int:
    if not args.once:
        raise Refused("`tiller tick` requires --once (use `tiller run` for the loop)")
    return _run_agent(args, once=True)


def cmd_run(args: argparse.Namespace) -> int:
    return _run_agent(args, once=False)


def cmd_status(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args)
    mode = _mode(cfg, args)
    paths = state_paths(cfg, mode)
    state = load_state(paths["state"])
    out: dict[str, Any] = {
        "mode": mode,
        "state_file": str(paths["state"]),
        "halted": state.halted,
        "halt_reason": state.halt_reason,
        "entries_blocked": state.brakes.entries_blocked,
        "brake_reasons": state.brakes.reasons,
        "paused_until": state.brakes.paused_until.isoformat() if state.brakes.paused_until else None,
        "canary": state.canary.model_dump(mode="json"),
        "reconcile_ok": state.last_reconcile_ok,
        "reconcile_mismatch_ticks": state.reconcile_mismatch_ticks,
        "kill_file": paths["kill"].exists(),
        "sleeves": {
            k: {
                "last_bar": v.last_bar_ts.isoformat() if v.last_bar_ts else None,
                "weight": v.last_weight,
                "regime_on": v.regime_on,
            }
            for k, v in state.sleeves.items()
        },
    }
    if paths["ledger"].exists():
        ledger = Ledger(paths["ledger"])
        curve = ledger.equity_curve(30)
        if curve:
            last = curve[-1]
            out["equity_usd"] = str(last.equity_usd)
            out["pnl_vs_deposits_usd"] = str(last.pnl_usd)
            out["equity_30d_start_usd"] = str(curve[0].equity_usd)
        out["positions"] = [p.model_dump(mode="json") for p in ledger.positions()]
        out["pending_signatures"] = ledger.pending_signatures()
        out["fills"] = len(ledger.fills())
        out["day"] = ledger.day_stats(utc_day_start(datetime.now(tz=UTC))).model_dump(mode="json")
        out["recent_events"] = [f"{e['ts']} {e['level']} {e['kind']}" for e in ledger.events(limit=10)]
        ledger.close()
    print(json.dumps(out, indent=1, default=str))
    return EXIT_OK


def cmd_shadow_report(args: argparse.Namespace) -> int:
    try:
        from tiller.copy.shadow import ShadowTracker, render_shadow_report
    except Exception:
        print("shadow-report: the copy module (WP-E) is not installed in this build; nothing to report")
        return EXIT_OK
    cfg = _load_cfg(args)
    paths = state_paths(cfg, _mode(cfg, args))
    ledger = Ledger(paths["ledger"])

    class _NoPrices:
        async def usd_prices(self, mints: list[str]) -> dict[str, Decimal]:
            return {}

    class _NoTokens:
        async def token_info(self, mint: str) -> Any:
            return None

        async def decimals(self, mint: str) -> int:
            raise ValueError("offline report: no token data")

    try:
        tracker = ShadowTracker(None, _NoPrices(), _NoTokens(), ledger, cfg.copy, SystemClock())
        print(render_shadow_report(tracker.report(args.days)))
    finally:
        ledger.close()
    return EXIT_OK


def cmd_pause(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args)
    paths = state_paths(cfg, _mode(cfg, args))
    paths["state_dir"].mkdir(parents=True, exist_ok=True)
    paths["kill"].touch()
    print(
        f"paused: KILL file written at {paths['kill']} (entries blocked; exits continue). `tiller resume` clears it."
    )
    return EXIT_OK


def cmd_resume(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args)
    paths = state_paths(cfg, _mode(cfg, args))
    if paths["kill"].exists():
        paths["kill"].unlink()
    state = load_state(paths["state"])
    state.halted = False
    state.halt_reason = None
    state.brakes.paused_until = None
    state.brakes.consecutive_failures = 0
    state.reconcile_mismatch_ticks = 0
    save_state(paths["state"], state)
    print("resumed: KILL file removed, halt/pause cleared (drawdown brakes re-evaluate on the next tick)")
    return EXIT_OK


def cmd_flatten(args: argparse.Namespace) -> int:
    if not args.yes:
        raise Refused("flatten sells EVERY position in emergency mode; pass --yes to confirm")
    cfg = _load_cfg(args)
    mode = _mode(cfg, args)
    clock = _clock(args)
    paths = state_paths(cfg, mode)
    _deps, agent = build_deps(cfg, mode, clock, paths)
    fills = asyncio.run(agent.flatten("cli flatten --yes"))
    for f in fills:
        print(f"sold {f.in_mint[:6]} for {f.usd_out:.2f} USD sig={f.signature}")
    print(f"flatten done: {len(fills)} fills; state halted until `tiller resume`")
    return EXIT_OK


def cmd_reconcile(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args)
    mode = _mode(cfg, args)
    if mode != "live":
        raise Refused("reconcile compares the chain with the ledger and only applies to live mode")
    clock = _clock(args)
    paths = state_paths(cfg, mode)
    deps, _agent = build_deps(cfg, mode, clock, paths)

    async def go() -> int:
        detail = None
        if deps.familiars is not None and cfg.familiars.handle:
            try:
                detail = await deps.familiars.agent(cfg.familiars.handle)
            except Exception:
                detail = None
        decimals: dict[str, int] = {}
        rpc, tokens = deps.rpc, deps.tokens
        assert rpc is not None and tokens is not None  # live wiring always sets both
        for acc in await rpc.get_token_accounts_by_owner(deps.signer_pubkey):
            if acc.mint not in (SOL_MINT, USDC_MINT) and acc.mint not in decimals:
                try:
                    decimals[acc.mint] = await tokens.decimals(acc.mint)
                except Exception:
                    pass  # unknown decimals: the report fails closed for that mint
        report = await reconcile(
            rpc,
            deps.ledger,
            deps.prices,
            deps.signer_pubkey,
            detail,
            clock,
            accept=args.accept,
            reserve_lamports=cfg.wallet.sol_reserve_lamports,
            token_decimals=decimals,
        )
        print(json.dumps(summarize(report), indent=1, default=str))
        if args.accept:
            state = load_state(paths["state"])
            state.reconcile_mismatch_ticks = 0
            state.last_reconcile_ok = True
            save_state(paths["state"], state)
            print("ledger positions rewritten to chain amounts; mismatch counter reset")
        return EXIT_OK if report.ok or args.accept else EXIT_ERROR

    return asyncio.run(go())


# --------------------------------------------------------------------------- sweep


def build_usdc_transfer(owner: str, to: str, amount_base: int, blockhash: str) -> bytes:
    """Unsigned VersionedTransaction: create the destination USDC ATA (idempotent) + TransferChecked.

    Built with solders only (no SPL SDK): ATA program instruction 1 = CreateIdempotent,
    Token program instruction 12 = TransferChecked(amount u64 LE, decimals u8).
    """
    from solders.hash import Hash
    from solders.instruction import AccountMeta, Instruction
    from solders.message import Message
    from solders.pubkey import Pubkey
    from solders.signature import Signature
    from solders.transaction import VersionedTransaction

    if amount_base <= 0:
        raise ValueError("amount must be positive")
    owner_pk = Pubkey.from_string(owner)
    to_pk = Pubkey.from_string(to)
    mint_pk = Pubkey.from_string(USDC_MINT)
    src = Pubkey.from_string(derive_ata(owner, USDC_MINT, TOKEN_PROGRAM))
    dst = Pubkey.from_string(derive_ata(to, USDC_MINT, TOKEN_PROGRAM))
    create = Instruction(
        Pubkey.from_string(ATA_PROGRAM),
        bytes([1]),
        [
            AccountMeta(owner_pk, True, True),
            AccountMeta(dst, False, True),
            AccountMeta(to_pk, False, False),
            AccountMeta(mint_pk, False, False),
            AccountMeta(Pubkey.from_string("11111111111111111111111111111111"), False, False),
            AccountMeta(Pubkey.from_string(TOKEN_PROGRAM), False, False),
        ],
    )
    transfer = Instruction(
        Pubkey.from_string(TOKEN_PROGRAM),
        bytes([12]) + amount_base.to_bytes(8, "little") + bytes([USDC_DECIMALS]),
        [
            AccountMeta(src, False, True),
            AccountMeta(mint_pk, False, False),
            AccountMeta(dst, False, True),
            AccountMeta(owner_pk, True, False),
        ],
    )
    msg = Message.new_with_blockhash([create, transfer], owner_pk, Hash.from_string(blockhash))
    return bytes(VersionedTransaction.populate(msg, [Signature.default()]))


def cmd_sweep(args: argparse.Namespace) -> int:
    if not args.yes:
        raise Refused("sweep moves funds out of the hot wallet; pass --yes to confirm (non-interactive)")
    cfg = _load_cfg(args)
    mode = _mode(cfg, args)
    if mode != "live":
        raise Refused("sweep signs a real transfer and only runs in live mode")
    keep = Decimal(str(args.keep))
    if keep < 0:
        raise Refused("--keep must be >= 0")
    clock = _clock(args)
    paths = state_paths(cfg, mode)
    deps, _agent = build_deps(cfg, mode, clock, paths)
    signer = FileSigner.load(cfg.wallet.keypair_path, os.environ.get(cfg.wallet.env_secret_var))
    rpc = deps.rpc
    call = getattr(rpc, "call", None)
    if rpc is None or call is None:
        raise Refused("sweep needs a JSON-RPC client with sendTransaction support")

    async def go() -> int:
        accounts = await rpc.get_token_accounts_by_owner(signer.pubkey)
        usdc = sum(a.amount_base for a in accounts if a.mint == USDC_MINT and a.owner == signer.pubkey)
        amount = usdc - int(keep * 10**USDC_DECIMALS)
        if amount <= 0:
            print(f"nothing to sweep: USDC balance {Decimal(usdc) / 10**USDC_DECIMALS} <= keep {keep}")
            return EXIT_OK
        bh = await call("getLatestBlockhash", [{"commitment": "finalized"}])
        blockhash = str(bh["value"]["blockhash"])
        unsigned = build_usdc_transfer(signer.pubkey, args.to, amount, blockhash)
        signed = signer.sign_transaction(unsigned)
        usd = Decimal(amount) / 10**USDC_DECIMALS
        print(f"sweeping {usd} USDC from {signer.pubkey} to {args.to} (keeping {keep})")
        sig = await call("sendTransaction", [base64.b64encode(signed).decode(), {"encoding": "base64"}])
        deps.ledger.record_transfer(clock.now(), USDC_MINT, amount, usd, "out", signature=str(sig))
        print(f"sent: signature {sig}")
        return EXIT_OK

    return asyncio.run(go())


def cmd_export_tax(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args)
    paths = state_paths(cfg, _mode(cfg, args))
    ledger = Ledger(paths["ledger"])
    try:
        n = ledger.export_tax_csv(Path(args.out))
    finally:
        ledger.close()
    print(f"wrote {n} fills to {args.out}")
    return EXIT_OK


def review_markdown(cfg: Config, ledger: Ledger, now: datetime) -> str:
    """Deterministic weekly review pack (ledger summary, brake trips, tracking notes)."""
    curve = ledger.equity_curve(30, now=now)
    fills = ledger.fills()
    lines = [f"# Tiller review {now.date().isoformat()}", "", f"mode: {cfg.mode}", ""]
    if curve:
        first, last = curve[0], curve[-1]
        lines += [
            "## Equity (last 30 days)",
            "",
            f"- start {first.equity_usd} -> now {last.equity_usd} USD; P&L vs deposits {last.pnl_usd} USD",
            f"- snapshots: {len(curve)}",
            "",
        ]
    lines += [
        "## Fills",
        "",
        f"- total fills: {len(fills)}; paper: {sum(1 for f in fills if f.paper)}",
        f"- emergency fills: {sum(1 for f in fills if f.mode == 'emergency')}",
        "",
    ]
    trips = [
        e
        for e in ledger.events(limit=500)
        if e["kind"] in ("loop_guard.paused", "flatten.start", "blocklist.added")
    ]
    lines += (
        ["## Brake trips", ""]
        + ([f"- {e['ts']} {e['kind']} {json.dumps(e['payload'])}" for e in trips] or ["- none"])
        + [""]
    )
    refused = ledger.events(limit=200, kind="intent.refused")
    lines += ["## Refused intents (last 200)", "", f"- {len(refused)} refusals", ""]
    lines += (
        ["## Positions", ""]
        + (
            [f"- {p.mint} {p.strategy} {p.amount_base} cost {p.cost_usd}" for p in ledger.positions()]
            or ["- flat"]
        )
        + [""]
    )
    lines += [
        "## Tracking",
        "",
        "- compare `tiller backtest` combined_default with the live curve above (weekly review)",
        "",
    ]
    return "\n".join(lines)


def cmd_review(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args)
    paths = state_paths(cfg, _mode(cfg, args))
    ledger = Ledger(paths["ledger"])
    try:
        text = review_markdown(cfg, ledger, datetime.now(tz=UTC))
    finally:
        ledger.close()
    print(text)
    if cfg.llm.narrator == "claude" and cfg.llm.api_key is not None and not args.no_llm:
        try:
            anthropic = importlib.import_module("anthropic")
        except Exception:
            print("\n(LLM diagnosis skipped: the `anthropic` extra is not installed)")
            return EXIT_OK
        client = anthropic.Anthropic(api_key=cfg.llm.api_key.get_secret_value())
        msg = client.messages.create(
            model=cfg.llm.model,
            max_tokens=800,
            messages=[
                {
                    "role": "user",
                    "content": "Diagnose this trading review and propose config diffs as TEXT only; nothing is applied automatically.\n\n"
                    + text,
                }
            ],
        )
        print("\n## LLM diagnosis (text only, never applied)\n")
        print("".join(getattr(b, "text", "") for b in msg.content))
    return EXIT_OK


# --------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tiller", description="Tiller: rule-based Solana spot trading agent")
    p.add_argument(
        "--config", default=os.environ.get("TILLER_CONFIG", str(DEFAULT_CONFIG)), help="config TOML path"
    )
    p.add_argument("--version", action="version", version=f"tiller {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", help="copy the example config into place")
    s.add_argument("--example", default=str(EXAMPLE_CONFIG))
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("keygen", help="generate a new hot-wallet keypair (0600)")
    s.add_argument("--path", default=str(Path.home() / ".tiller" / "keypair.json"))
    s.set_defaults(fn=cmd_keygen)

    s = sub.add_parser("register", help="register the wallet on familiars.family (ONE attempt)")
    s.add_argument("--handle", required=True)
    s.add_argument("--name", required=True)
    s.add_argument("--bio")
    s.add_argument("--strategy")
    s.add_argument("--color")
    s.set_defaults(fn=cmd_register)

    s = sub.add_parser("preflight", help="print the skill.md checklist and check live readiness")
    s.add_argument("--record", action="store_true", help="re-record fixtures via tools/record_fixtures.py")
    s.set_defaults(fn=cmd_preflight)

    s = sub.add_parser("backtest", help="run the daily backtest grid and write the report")
    s.add_argument("--cost-bps", default="5,10,30")
    s.add_argument("--out", default="docs/BACKTEST.md")
    s.add_argument("--csv-dir", default=None)
    s.set_defaults(fn=cmd_backtest)

    for name, fn in (("tick", cmd_tick), ("run", cmd_run)):
        s = sub.add_parser(name, help="one tick (--once) / the 60 s monitor loop")
        if name == "tick":
            s.add_argument("--once", action="store_true")
            s.add_argument("--mode", choices=["offline", "paper", "live"])
        else:
            s.add_argument("--mode", choices=["paper", "live"])
        s.add_argument("--at", help="ISO timestamp for a simulated clock (offline/paper testing)")
        s.add_argument("--i-have-read-skill-md", action="store_true", dest="i_have_read_skill_md")
        s.add_argument("--paper-capital-usd", type=float, default=None)
        s.add_argument("--max-ticks", type=int, default=None, help=argparse.SUPPRESS)
        s.set_defaults(fn=fn)

    s = sub.add_parser("status", help="print state and ledger summary")
    s.add_argument("--mode", choices=["offline", "paper", "live"])
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("shadow-report", help="copy-trading shadow report (WP-E)")
    s.add_argument("--days", type=int, default=60)
    s.add_argument("--mode", choices=["offline", "paper", "live"])
    s.set_defaults(fn=cmd_shadow_report)

    s = sub.add_parser("pause", help="block entries (writes the KILL file)")
    s.add_argument("--mode", choices=["offline", "paper", "live"])
    s.set_defaults(fn=cmd_pause)

    s = sub.add_parser("resume", help="clear halt/pause and remove the KILL file")
    s.add_argument("--mode", choices=["offline", "paper", "live"])
    s.set_defaults(fn=cmd_resume)

    s = sub.add_parser("flatten", help="emergency exit of every position")
    s.add_argument("--yes", action="store_true")
    s.add_argument("--mode", choices=["offline", "paper", "live"])
    s.add_argument("--at")
    s.set_defaults(fn=cmd_flatten)

    s = sub.add_parser("reconcile", help="chain vs ledger reconciliation")
    s.add_argument("--accept", action="store_true")
    s.add_argument("--mode", choices=["live"])
    s.add_argument("--at")
    s.set_defaults(fn=cmd_reconcile)

    s = sub.add_parser("sweep", help="move USDC above --keep to a cold address")
    s.add_argument("--to", required=True)
    s.add_argument("--keep", required=True, type=float, help="USD to keep in the hot wallet")
    s.add_argument("--yes", action="store_true")
    s.add_argument("--mode", choices=["live"])
    s.add_argument("--at")
    s.set_defaults(fn=cmd_sweep)

    s = sub.add_parser("export-tax", help="write every fill with USD fair values to CSV")
    s.add_argument("--out", required=True)
    s.add_argument("--mode", choices=["offline", "paper", "live"])
    s.set_defaults(fn=cmd_export_tax)

    s = sub.add_parser("review", help="print the weekly review markdown pack")
    s.add_argument("--no-llm", action="store_true")
    s.add_argument("--mode", choices=["offline", "paper", "live"])
    s.set_defaults(fn=cmd_review)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    fn: Callable[[argparse.Namespace], int] = args.fn
    try:
        return fn(args)
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return EXIT_REFUSED
    except NotImplementedError as e:
        print(f"refused: {e}", file=sys.stderr)
        return EXIT_REFUSED
    except (KeyLoadError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
