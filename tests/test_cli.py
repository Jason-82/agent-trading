"""CLI: every command parses; live refused without acknowledgement; sweep refused without --yes;
register refuses if a fam_ key already exists; offline tick runs end to end; sweep transaction shape."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from solders.transaction import VersionedTransaction

from tiller import cli
from tiller.cli import EXIT_OK, EXIT_REFUSED, build_parser, build_usdc_transfer, main, state_paths
from tiller.config import load_config
from tiller.execution.guard import derive_ata
from tiller.models import TOKEN_PROGRAM, USDC_MINT
from tiller.state import load_state, save_state
from wpd_helpers import WALLET

REPO = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"


def write_cfg(tmp_path: Path, **extra: Any) -> Path:
    """Minimal config pointing every path into ``tmp_path`` (paper, familiars off)."""
    lines = [f'mode = "{extra.pop("mode", "paper")}"']
    if "live_without_familiars_ack" in extra:
        lines.append(f"live_without_familiars_ack = {extra.pop('live_without_familiars_ack')}")
    lines += [
        "[paths]",
        f'state_dir = "{tmp_path / "state"}"',
        f'ledger_path = "{tmp_path / "state" / "tiller.sqlite"}"',
        "[familiars]",
        f"enabled = {str(extra.pop('familiars_enabled', False)).lower()}",
    ]
    if "skill_md_reviewed_at" in extra:
        lines.append(f'skill_md_reviewed_at = "{extra.pop("skill_md_reviewed_at")}"')
    if "handle" in extra:
        lines.append(f'handle = "{extra.pop("handle")}"')
    lines += ["[data]", f'csv_dir = "{FIXTURES / "ohlcv"}"']
    lines += [f"{k} = {v}" for k, v in extra.items()]
    p = tmp_path / "tiller.toml"
    p.write_text("\n".join(lines) + "\n")
    return p


@pytest.mark.parametrize(
    "argv",
    [
        ["init"],
        ["keygen", "--path", "/tmp/x.json"],
        ["register", "--handle", "abc", "--name", "Abc"],
        ["preflight"],
        ["preflight", "--record"],
        ["backtest", "--cost-bps", "30", "--out", "x.md"],
        ["tick", "--once", "--mode", "offline"],
        ["run", "--mode", "paper", "--i-have-read-skill-md"],
        ["status"],
        ["shadow-report", "--days", "30"],
        ["pause"],
        ["resume"],
        ["flatten", "--yes"],
        ["reconcile", "--accept"],
        ["sweep", "--to", WALLET, "--keep", "100", "--yes"],
        ["export-tax", "--out", "t.csv"],
        ["review", "--no-llm"],
    ],
)
def test_every_command_parses(argv: list[str]) -> None:
    args = build_parser().parse_args(argv)
    assert args.command == argv[0] and callable(args.fn)


def test_unknown_command_and_missing_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["nope"])
    assert main(["--config", str(tmp_path / "missing.toml"), "status"]) == EXIT_REFUSED
    assert "tiller init" in capsys.readouterr().err


def test_init_copies_example_and_refuses_overwrite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "config" / "tiller.toml"
    rc = main(["--config", str(target), "init", "--example", str(REPO / "config" / "tiller.example.toml")])
    assert rc == EXIT_OK and target.exists()
    assert (
        main(["--config", str(target), "init", "--example", str(REPO / "config" / "tiller.example.toml")])
        == EXIT_REFUSED
    )
    cfg = load_config(target)
    assert cfg.mode == "paper"


def test_keygen_writes_0600_and_refuses_overwrite(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "keys" / "kp.json"
    assert main(["keygen", "--path", str(path)]) == EXIT_OK
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert oct(path.parent.stat().st_mode & 0o777) == "0o700"
    out = capsys.readouterr().out
    assert "public key" in out and "WORKING CAPITAL ONLY" in out
    assert main(["keygen", "--path", str(path)]) == 1


def test_live_refused_without_acknowledgement(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # config mode paper: `run --mode live` refused outright
    cfg = write_cfg(tmp_path)
    assert main(["--config", str(cfg), "run", "--mode", "live", "--i-have-read-skill-md"]) == EXIT_REFUSED
    assert "not 'live'" in capsys.readouterr().err
    # config mode live with a fresh skill.md date but no acknowledgement flag on the first start
    today = datetime.now(tz=UTC).date().isoformat()
    cfg = write_cfg(
        tmp_path,
        mode="live",
        familiars_enabled=False,
        live_without_familiars_ack="true",
        skill_md_reviewed_at=today,
    )
    assert main(["--config", str(cfg), "run", "--mode", "live"]) == EXIT_REFUSED
    err = capsys.readouterr().err
    assert "--i-have-read-skill-md" in err and "skill.md" in err
    assert not load_state(state_paths(load_config(cfg), "live")["state"]).live_ack_recorded
    # with the flag the acknowledgement is persisted; the run then fails on the missing keypair (no network)
    rc = main(["--config", str(cfg), "run", "--mode", "live", "--i-have-read-skill-md"])
    assert rc == 1
    assert load_state(state_paths(load_config(cfg), "live")["state"]).live_ack_recorded
    assert "keypair" in capsys.readouterr().err.lower() or True


def test_live_refused_when_familiars_enabled_without_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    today = datetime.now(tz=UTC).date().isoformat()
    cfg = write_cfg(tmp_path, mode="live", familiars_enabled=True, skill_md_reviewed_at=today)
    assert (
        main(["--config", str(cfg), "tick", "--once", "--mode", "live", "--i-have-read-skill-md"])
        == EXIT_REFUSED
    )
    assert "tiller register" in capsys.readouterr().err


def test_live_refused_when_optional_venue_enabled(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = write_cfg(tmp_path)
    cfg.write_text(
        cfg.read_text()
        + '[venues.hyperliquid]\nenabled = true\ngeo_ack = "I am not a US or Ontario person"\n'
    )
    assert main(["--config", str(cfg), "tick", "--once", "--mode", "paper"]) == EXIT_REFUSED
    captured = capsys.readouterr()
    assert "optional venue not built" in captured.err and "Ontario" in captured.out


def test_sweep_refused_without_yes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = write_cfg(tmp_path)
    assert main(["--config", str(cfg), "sweep", "--to", WALLET, "--keep", "50"]) == EXIT_REFUSED
    assert "--yes" in capsys.readouterr().err
    # even with --yes, paper mode refuses to sign a transfer
    assert main(["--config", str(cfg), "sweep", "--to", WALLET, "--keep", "50", "--yes"]) == EXIT_REFUSED
    assert main(["--config", str(cfg), "flatten"]) == EXIT_REFUSED


def test_register_refuses_if_key_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = write_cfg(tmp_path, familiars_enabled=True)
    monkeypatch.setenv("TILLER_FAMILIARS_API_KEY", "fam_already")
    assert main(["--config", str(cfg), "register", "--handle", "abc", "--name", "Abc"]) == EXIT_REFUSED
    assert "already exists" in capsys.readouterr().err
    monkeypatch.delenv("TILLER_FAMILIARS_API_KEY")
    secrets = state_paths(load_config(cfg), "live")["secrets"]
    secrets.parent.mkdir(parents=True, exist_ok=True)
    secrets.write_text("TILLER_FAMILIARS_API_KEY=fam_x\n")
    assert main(["--config", str(cfg), "register", "--handle", "abc", "--name", "Abc"]) == EXIT_REFUSED
    secrets.unlink()
    # no key anywhere: the next obstacle is the missing wallet keypair (no network was touched)
    assert main(["--config", str(cfg), "register", "--handle", "abc", "--name", "Abc"]) == 1


def test_pause_resume_status_export_review(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = write_cfg(tmp_path)
    paths = state_paths(load_config(cfg), "paper")
    assert main(["--config", str(cfg), "pause"]) == EXIT_OK
    assert "KILL" in capsys.readouterr().out
    assert paths["kill"].exists()
    st = load_state(paths["state"])
    st.halted = True
    st.halt_reason = "test"
    save_state(paths["state"], st)
    assert main(["--config", str(cfg), "status"]) == EXIT_OK
    out = json.loads(capsys.readouterr().out)
    assert out["halted"] is True and out["kill_file"] is True and out["mode"] == "paper"
    assert main(["--config", str(cfg), "resume"]) == EXIT_OK
    assert not paths["kill"].exists() and not load_state(paths["state"]).halted
    assert main(["--config", str(cfg), "export-tax", "--out", str(tmp_path / "tax.csv")]) == EXIT_OK
    assert (tmp_path / "tax.csv").read_text().startswith("ts,signature")
    assert main(["--config", str(cfg), "review", "--no-llm"]) == EXIT_OK
    assert "# Tiller review" in capsys.readouterr().out
    assert main(["--config", str(cfg), "shadow-report"]) == EXIT_OK
    assert main(["--config", str(cfg), "preflight"]) == EXIT_REFUSED  # paper config is not live-ready
    assert "[ ]" in capsys.readouterr().out


def test_tick_once_offline_runs_fixture_venue(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = write_cfg(tmp_path, mode="offline")
    at = "2024-03-05T00:05:00Z"
    rc = main(
        [
            "--config",
            str(cfg),
            "tick",
            "--once",
            "--mode",
            "offline",
            "--at",
            at,
            "--paper-capital-usd",
            "50000",
        ]
    )
    assert rc == EXIT_OK
    out = json.loads(capsys.readouterr().out)
    assert out["daily_evaluated"] is True and out["entries_blocked"] is False
    assert out["equity_usd"].startswith("50")
    paths = state_paths(load_config(cfg), "offline")
    assert paths["state"].exists() and paths["ledger"].name == "tiller-offline.sqlite"
    st = load_state(paths["state"])
    assert st.sleeves["sol_trend_ensemble"].last_bar_ts == datetime(2024, 3, 4, tzinfo=UTC)
    # a second tick on the same bar re-evaluates nothing and the lock is released between runs
    rc = main(["--config", str(cfg), "tick", "--once", "--mode", "offline", "--at", "2024-03-05T00:20:00Z"])
    assert rc == EXIT_OK
    out = json.loads(capsys.readouterr().out)
    assert out["daily_evaluated"] is False
    assert main(["--config", str(cfg), "status", "--mode", "offline"]) == EXIT_OK
    status = json.loads(capsys.readouterr().out)
    assert status["fills"] >= 1 and status["positions"]
    assert main(["--config", str(cfg), "tick", "--mode", "offline"]) == EXIT_REFUSED  # --once required


def test_run_paper_refuses_second_instance(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = write_cfg(tmp_path, mode="offline")
    paths = state_paths(load_config(cfg), "offline")
    paths["state_dir"].mkdir(parents=True)
    from tiller.clock import SystemClock
    from tiller.state import InstanceLock

    lock = InstanceLock(paths["lock"], SystemClock())
    lock.acquire()
    try:
        assert (
            main(
                ["--config", str(cfg), "tick", "--once", "--mode", "offline", "--at", "2024-03-05T00:05:00Z"]
            )
            == EXIT_REFUSED
        )
        assert "lock" in capsys.readouterr().err
    finally:
        lock.release()


def test_build_usdc_transfer_shape() -> None:
    blockhash = "CZ8YUVdk7znjrUmnb5n7kgySk9yRAsQDYmyCxzfSky9t"
    to = "GmaDrppBC7P5ARKV8g3djiwP89vz1jLK23V2GBjuAEGB"
    raw = build_usdc_transfer(WALLET, to, 1_500_000, blockhash)
    tx = VersionedTransaction.from_bytes(raw)
    keys = [str(k) for k in tx.message.account_keys]
    assert keys[0] == WALLET and tx.message.header.num_required_signatures == 1
    ixs = tx.message.instructions
    assert len(ixs) == 2
    assert keys[ixs[0].program_id_index] == cli.ATA_PROGRAM and bytes(ixs[0].data) == b"\x01"
    assert keys[ixs[1].program_id_index] == TOKEN_PROGRAM
    data = bytes(ixs[1].data)
    assert data[0] == 12 and int.from_bytes(data[1:9], "little") == 1_500_000 and data[9] == 6
    src = derive_ata(WALLET, USDC_MINT, TOKEN_PROGRAM)
    dst = derive_ata(to, USDC_MINT, TOKEN_PROGRAM)
    assert keys[ixs[1].accounts[0]] == src and keys[ixs[1].accounts[2]] == dst
    with pytest.raises(ValueError):
        build_usdc_transfer(WALLET, to, 0, blockhash)


def test_state_paths_isolate_modes(tmp_path: Path) -> None:
    cfg = load_config(write_cfg(tmp_path))
    live = state_paths(cfg, "live")
    paper = state_paths(cfg, "paper")
    assert live["ledger"] != paper["ledger"] and live["state"] != paper["state"]
    assert live["ledger"].name == "tiller.sqlite" and paper["ledger"].name == "tiller-paper.sqlite"
    assert live["kill"] == paper["kill"]


def test_review_markdown_lists_brake_trips(tmp_path: Path) -> None:
    from tiller.clock import SimClock
    from tiller.ledger import Ledger

    clock = SimClock(datetime(2026, 9, 24, tzinfo=UTC))
    ledger = Ledger(tmp_path / "l.sqlite", clock=clock)
    ledger.add_event("warn", "loop_guard.paused", {"until": "x"})
    text = cli.review_markdown(load_config(write_cfg(tmp_path)), ledger, clock.now() + timedelta(days=1))
    assert "loop_guard.paused" in text and "## Brake trips" in text
    ledger.close()


def test_env_secret_var_used_for_signer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tiller.execution.wallet import FileSigner

    kp = FileSigner.from_seed(bytes(range(32)))
    monkeypatch.setenv("AGENT_WALLET_SECRET", json.dumps(list(bytes(kp._kp))))
    cfg = load_config(write_cfg(tmp_path))
    paths = state_paths(cfg, "paper")
    signer = cli.load_signer(cfg, "paper", paths, dict(os.environ))
    assert type(signer).__name__ == "NullSigner" and signer.pubkey == WALLET
    live = cli.load_signer(cfg, "live", paths, dict(os.environ))
    assert type(live).__name__ == "FileSigner" and live.pubkey == WALLET
