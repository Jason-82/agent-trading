PY ?= .venv/bin/python
UV ?= uv

.PHONY: install test lint backtest venv

venv:
	@test -d .venv || $(UV) venv --python 3.11 .venv

install: venv
	$(UV) pip install --python $(PY) -r requirements.lock
	$(UV) pip install --python $(PY) --no-deps -e .

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check src tests
	$(PY) -m ruff format --check src tests

backtest:
	$(PY) -m tiller.backtest --out docs/BACKTEST.md
