"""``python -m tiller.backtest``: run the parameter grid over the bundled history and write the report."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tiller.backtest.runner import load_csv_candles, render_report, run_grid


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tiller.backtest")
    ap.add_argument("--csv-dir", type=Path, default=Path("data/history"))
    ap.add_argument("--cost-bps", default="5,10,30", help="comma-separated per-side costs")
    ap.add_argument("--out", type=Path, default=Path("docs/BACKTEST.md"))
    ap.add_argument("--no-band", action="store_true", help="skip the rebalance-band variants")
    args = ap.parse_args(argv)
    costs = [int(x) for x in args.cost_bps.split(",") if x.strip()]
    sol = load_csv_candles(args.csv_dir / "SOLUSDT_1d.csv")
    btc = load_csv_candles(args.csv_dir / "BTCUSDT_1d.csv")
    results = run_grid(sol, btc, cost_bps_list=costs, bands=(None,) if args.no_band else (None, 0.02))
    text = render_report(results, primary_cost_bps=30 if 30 in costs else costs[-1])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text)
    print(f"wrote {args.out} ({len(results)} variants)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
