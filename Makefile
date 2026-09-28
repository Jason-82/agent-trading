PY ?= .venv/bin/python
UV ?= uv

.PHONY: install test lint lock check-lock backtest venv

venv:
	@test -d .venv || $(UV) venv --python 3.11 .venv

# Hash-verified install only: an unhashed lock fails the build (spec execution rule 10).
install: venv check-lock
	$(UV) pip install --python $(PY) --require-hashes -r requirements-dev.lock
	$(UV) pip install --python $(PY) --no-deps -e .

# Regenerate both locks (needs network); review the diff and honour the 14-day cooldown policy.
lock:
	$(UV) pip compile --python-version 3.11 --generate-hashes pyproject.toml -o requirements.lock
	$(UV) pip compile --python-version 3.11 --generate-hashes --extra dev --extra llm pyproject.toml -o requirements-dev.lock
	$(PY) tools/check_lock.py requirements.lock requirements-dev.lock

check-lock:
	python3 tools/check_lock.py requirements.lock requirements-dev.lock

test:
	$(PY) -m pytest -q

lint: check-lock
	$(PY) -m ruff check src tests tools
	$(PY) -m ruff format --check src tests

backtest:
	$(PY) -m tiller.backtest --out docs/BACKTEST.md
