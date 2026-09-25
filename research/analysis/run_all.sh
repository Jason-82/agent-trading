#!/usr/bin/env bash
# Reproduce every study and rebuild RESULTS.md / results.json. Offline, deterministic.
set -euo pipefail
cd "$(dirname "$0")"
PY=../.probe-venv/bin/python
for s in study1_daily_trend.py study2_4h_breakout.py study3_funding_carry.py study4_pumpfun.py study5_cross_asset.py; do
  echo "== $s"; "$PY" "$s" > /dev/null
done
"$PY" build_results.py
