#!/usr/bin/env python3
"""Supply-chain check for the lockfiles (spec execution rule 10). Stdlib only, runs in the image build.

Fails (exit 1) when a lockfile
* is missing or has a requirement line without at least one ``--hash=`` (no unhashed install, ever);
* names a package on the typosquat denylist (solana-keypair, semantic-types, solana-publickey, ...);
* pins a runtime dependency at a version different from ``pyproject.toml``;
* contains a name that is not in the reviewed allowlist below (add a name here ON PURPOSE after
  reading its source; the 14-day cooldown applies).

Usage: ``python tools/check_lock.py requirements.lock [requirements-dev.lock ...]``
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Known typosquats / hijacked names around the Solana + Python ecosystem (never installed).
DENYLIST = {
    "solana-keypair",
    "solana-publickey",
    "solana-py-keypair",
    "semantic-types",
    "solana-utils",
    "solders-py",
    "pysolana",
    "solana-sdk",
    "solana-web3",
    "spl-token-py",
    "pydantic-core-py",
    "httpx-client",
    "numpy-py",
    "pandas-py",
    "anthropic-sdk",
    "anthropics",
    "requests-oauthlib2",
    "colorama-py",
}

#: Every distribution name the locks may contain (runtime, transitive, dev, llm). Reviewed set.
ALLOWLIST = {
    # runtime (pyproject dependencies)
    "solders",
    "httpx",
    "pydantic",
    "numpy",
    "pandas",
    # runtime transitive
    "annotated-types",
    "anyio",
    "certifi",
    "h11",
    "httpcore",
    "idna",
    "jsonalias",
    "pydantic-core",
    "python-dateutil",
    "six",
    "typing-extensions",
    "typing-inspection",
    "sniffio",
    # dev
    "pytest",
    "pytest-asyncio",
    "respx",
    "ruff",
    "mypy",
    "mypy-extensions",
    "iniconfig",
    "packaging",
    "pluggy",
    "pygments",
    "pathspec",
    "librt",
    "ast-serialize",
    # llm extra (anthropic and its transitive set)
    "anthropic",
    "jiter",
    "distro",
    "docstring-parser",
    "httpx2",
    "httpcore2",
    "truststore",
}

_REQ = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?==([^\s\\]+)")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def runtime_pins() -> dict[str, str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pins: dict[str, str] = {}
    for dep in data["project"]["dependencies"]:
        m = _REQ.match(dep.strip())
        if m:
            pins[_norm(m.group(1))] = m.group(3)
    return pins


def check(path: Path, pins: dict[str, str]) -> list[str]:
    problems: list[str] = []
    if not path.exists():
        return [f"{path}: missing"]
    lines = path.read_text(encoding="utf-8").splitlines()
    i = 0
    seen: dict[str, str] = {}
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.strip() or line.lstrip().startswith("#") or line.startswith((" ", "\t")):
            continue
        m = _REQ.match(line)
        if m is None:
            problems.append(f"{path}:{i}: unparseable requirement line {line!r}")
            continue
        name, version = _norm(m.group(1)), m.group(3)
        hashes = "--hash=" in line
        while i < len(lines) and lines[i].startswith((" ", "\t")):
            if "--hash=" in lines[i]:
                hashes = True
            i += 1
        if not hashes:
            problems.append(f"{path}: {name}=={version} has no --hash= (unhashed install refused)")
        if name in DENYLIST:
            problems.append(f"{path}: {name} is on the typosquat denylist")
        elif name not in ALLOWLIST:
            problems.append(f"{path}: {name} is not in the reviewed allowlist (tools/check_lock.py)")
        if name in pins and pins[name] != version:
            problems.append(f"{path}: {name}=={version} differs from pyproject pin =={pins[name]}")
        seen[name] = version
    for name in pins:
        if name not in seen:
            problems.append(f"{path}: runtime dependency {name} is not pinned in the lock")
    return problems


def main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv] or [ROOT / "requirements.lock", ROOT / "requirements-dev.lock"]
    pins = runtime_pins()
    problems: list[str] = []
    for p in paths:
        problems.extend(check(p if p.is_absolute() else ROOT / p, pins))
    for msg in problems:
        print(f"check_lock: {msg}", file=sys.stderr)
    if problems:
        return 1
    print(f"check_lock: {len(paths)} lockfile(s) OK (hashes present, no denylisted or unreviewed names)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
