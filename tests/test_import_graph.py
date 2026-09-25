"""Import-graph guarantees.

* ``tiller.familiars.*``, ``tiller.alerts`` and ``tiller.strategies.*`` never import
  ``tiller.execution`` (in particular ``wallet`` / ``venue``) or ``anthropic`` at module
  import time (static AST scan of top-level imports + a subprocess runtime check).
* ``tiller.copy.*`` never imports ``tiller.execution.wallet`` / ``tiller.execution.venue``
  (scans whatever WP-E has put on disk).
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "tiller"

ISOLATED_PACKAGES = ("familiars", "strategies")
ISOLATED_MODULES = ("alerts.py",)
FORBIDDEN_ISOLATED = ("tiller.execution", "anthropic")
FORBIDDEN_COPY = ("tiller.execution.wallet", "tiller.execution.venue")


def _module_files(rel: str) -> list[Path]:
    p = SRC / rel
    if p.is_file():
        return [p]
    return sorted(p.rglob("*.py")) if p.is_dir() else []


def _top_level_imports(path: Path) -> list[tuple[str, int]]:
    """(module name, line) for every import at module level, including those under try/if."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[str, int]] = []

    def visit(nodes: list[ast.stmt]) -> None:
        for node in nodes:
            if isinstance(node, ast.Import):
                found.extend((a.name, node.lineno) for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                found.append((mod, node.lineno))
                found.extend((f"{mod}.{a.name}", node.lineno) for a in node.names)
            elif isinstance(node, ast.If | ast.Try | ast.With):
                visit(list(node.body))
                for extra in ("orelse", "finalbody", "handlers"):
                    for sub in getattr(node, extra, []) or []:
                        visit(list(sub.body) if isinstance(sub, ast.ExceptHandler) else [sub])

    visit(list(tree.body))
    return found


def _modname(p: Path) -> str:
    return "tiller." + p.relative_to(SRC).with_suffix("").as_posix().replace("/", ".")


def _hits(files: list[Path], forbidden: tuple[str, ...]) -> list[str]:
    bad = []
    for f in files:
        for name, line in _top_level_imports(f):
            if any(name == x or name.startswith(x + ".") for x in forbidden):
                bad.append(f"{f.relative_to(SRC)}:{line} imports {name}")
    return bad


def test_isolated_modules_exist() -> None:
    files = [f for rel in (*ISOLATED_PACKAGES, *ISOLATED_MODULES) for f in _module_files(rel)]
    names = {f.name for f in files}
    assert {"client.py", "poster.py", "narrator.py", "alerts.py", "sol_trend.py"} <= names


@pytest.mark.parametrize("rel", [*ISOLATED_PACKAGES, *ISOLATED_MODULES])
def test_no_execution_or_anthropic_import_static(rel: str) -> None:
    assert _hits(_module_files(rel), FORBIDDEN_ISOLATED) == []


def test_copy_modules_never_import_wallet_or_venue_static() -> None:
    files = _module_files("copy")
    assert files, "tiller.copy package missing"
    assert _hits(files, FORBIDDEN_COPY) == []


def test_runtime_import_does_not_load_execution_or_anthropic() -> None:
    mods = [
        "tiller.familiars.client",
        "tiller.familiars.poster",
        "tiller.familiars.narrator",
        "tiller.alerts",
    ]
    mods += [_modname(p) for p in _module_files("strategies") if p.name != "__init__.py"]
    mods += [_modname(p) for p in _module_files("copy") if p.name != "__init__.py"]
    code = (
        "import importlib, sys\n"
        f"for m in {mods!r}:\n"
        "    importlib.import_module(m)\n"
        "loaded = sorted(m for m in sys.modules if m == 'anthropic' or m.startswith('anthropic.') "
        "or m.startswith('tiller.execution'))\n"
        "print(loaded)\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "[]", proc.stdout


def test_copy_runtime_never_loads_wallet_or_venue() -> None:
    mods = [_modname(p) for p in _module_files("copy") if p.name != "__init__.py"]
    code = (
        "import importlib, sys\n"
        f"for m in {mods!r}:\n"
        "    importlib.import_module(m)\n"
        "print(sorted(m for m in sys.modules if m in ('tiller.execution.wallet', 'tiller.execution.venue')))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "[]", proc.stdout
