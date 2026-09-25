"""Daily backtester with the offline-study conventions, plus the report renderer.

Conventions (``scratchpad/analysis/common.py``, reproduced exactly):

* exposure[t] is decided on the CLOSE of bar t and established at the OPEN of bar t+1
  (the last bar is marked at its own close);
* the period return labelled with bar t+1 is ``exposure[t] * (px[t+1]/px[t] - 1)`` minus
  ``cost_per_side * |exposure[t] - exposure[t-1]|`` (turnover charged per side);
* the first ``warmup`` (250) bars carry zero exposure and are excluded from evaluation,
  so the entry cost at bar 250 is inside the evaluation window;
* annualisation uses 365 periods per year, Sharpe uses 0% risk-free, vol is ddof=1;
* ``in_sample`` = evaluation start -> the day before ``oos_start``; ``last_24m`` = from
  ``oos_start`` (2024-09-24) to the end;
* round trips are flat->long transitions of the (pre-warmup) exposure series; for the
  Donchian ensemble the caller passes the sum over sleeves.

The optional ``band`` models the live rebalance band: the held weight drifts with price
and is only traded back to target when ``|target - held| >= band`` (fraction of equity).

pandas is deliberately not used: the report is plain markdown.
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import numpy as np
from pydantic import BaseModel, ConfigDict

from tiller.models import Candle
from tiller.strategies.indicators import realized_vol
from tiller.strategies.sol_trend import (
    RegimeParams,
    SolTrendParams,
    align_closes,
    candles_to_arrays,
    combined_exposure,
    count_round_trips,
    ensemble_detail,
    regime_exposure,
)

PERIODS_PER_YEAR = 365
DEFAULT_WARMUP = 250
DEFAULT_OOS_START = date(2024, 9, 24)

RULE_BUY_AND_HOLD = "buy_and_hold"
RULE_SMA200 = "sma200_hyst2pct"
RULE_DONCHIAN = "donchian_ensemble"
RULE_DONCHIAN_VT = "donchian_ensemble_voltarget25"
RULE_COMBINED = "combined_default"
RULES = [RULE_BUY_AND_HOLD, RULE_SMA200, RULE_DONCHIAN, RULE_DONCHIAN_VT, RULE_COMBINED]


class Metrics(BaseModel):
    """Performance summary. Fractions, not percent (0.148 = 14.8%); ``sharpe`` annualised."""

    model_config = ConfigDict(extra="forbid")

    cagr: float
    vol: float
    sharpe: float
    max_dd: float
    time_in_market: float
    avg_exposure: float
    round_trips: int
    turnover_per_year: float
    total_return: float = 0.0
    n_periods: int = 0
    start: str | None = None
    end: str | None = None


class BacktestResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full: Metrics
    in_sample: Metrics
    last_24m: Metrics
    calendar_years: dict[int, float]
    equity: list[float]
    dates: list[str]


class GridSpec(BaseModel):
    """One cell of the parameter grid."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule: str
    cost_bps: int
    btc_confirm: bool = False
    band: float | None = None

    @property
    def key(self) -> str:
        return grid_key(self.rule, self.cost_bps, self.btc_confirm, self.band)


def grid_key(rule: str, cost_bps: int, btc_confirm: bool = False, band: float | None = None) -> str:
    band_s = "none" if band is None else f"{band:g}"
    return f"{rule}|cost={cost_bps}|btc_confirm={int(btc_confirm)}|band={band_s}"


# --------------------------------------------------------------------------- data loading


def load_csv_candles(path: Path, end: datetime | None = None) -> list[Candle]:
    """Read a ``timestamp,open,high,low,close,volume`` CSV (unix seconds, bar open) into Candles."""
    out: list[Candle] = []
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = datetime.fromtimestamp(int(row["timestamp"]), tz=UTC)
            if end is not None and ts > end:
                continue
            out.append(
                Candle(
                    ts=ts,
                    open=Decimal(row["open"]),
                    high=Decimal(row["high"]),
                    low=Decimal(row["low"]),
                    close=Decimal(row["close"]),
                    volume=Decimal(row["volume"]),
                )
            )
    out.sort(key=lambda b: b.ts)
    return out


# --------------------------------------------------------------------------- core simulation


def _execution_prices(opens: np.ndarray, closes: np.ndarray) -> np.ndarray:
    """Fill price for a signal formed at close t: open of t+1; the last bar marks at its close."""
    px = np.empty_like(opens)
    px[:-1] = opens[1:]
    px[-1] = closes[-1]
    return px


def simulate_returns(
    exposure: np.ndarray, px: np.ndarray, cost_per_side: float, band: float | None = None
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-period returns, realised held weight and per-bar turnover.

    Returns ``(r, held, turnover)`` with ``len(r) == len(px) - 1`` (period t spans
    ``px[t] -> px[t+1]``), ``held[t]`` the weight actually carried over period t and
    ``turnover[t]`` the absolute weight traded at ``px[t]`` (defined for every bar).
    """
    e = np.where(np.isnan(exposure), 0.0, np.asarray(exposure, dtype=np.float64))
    n = e.shape[0]
    fwd = px[1:] / px[:-1] - 1.0
    if band is None:
        prev = np.concatenate(([0.0], e[:-1]))
        turnover = np.abs(e - prev)
        r = e[:-1] * fwd - cost_per_side * turnover[:-1]
        return r, e[:-1].copy(), turnover
    held = np.zeros(n)
    turnover = np.zeros(n)
    r = np.zeros(n - 1)
    w = 0.0
    for t in range(n):
        target = e[t]
        if abs(target - w) >= band:
            turnover[t] = abs(target - w)
            w = target
        if t == n - 1:
            break
        held[t] = w
        r[t] = w * fwd[t] - cost_per_side * turnover[t]
        growth = 1.0 + w * fwd[t]
        w = w * (1.0 + fwd[t]) / growth if growth > 0 else 0.0
    return r, held[:-1], turnover


def _metrics(
    r: np.ndarray, ex: np.ndarray, dates: Sequence[date], turnover_sum: float, round_trips: int
) -> Metrics:
    if r.shape[0] == 0:
        return Metrics(
            cagr=0.0,
            vol=0.0,
            sharpe=0.0,
            max_dd=0.0,
            time_in_market=0.0,
            avg_exposure=0.0,
            round_trips=round_trips,
            turnover_per_year=0.0,
            total_return=0.0,
            n_periods=0,
        )
    eq = np.cumprod(1.0 + r)
    n_years = r.shape[0] / PERIODS_PER_YEAR
    total = float(eq[-1])
    cagr = total ** (1.0 / n_years) - 1.0
    sd = float(np.std(r, ddof=1)) if r.shape[0] > 1 else 0.0
    vol = sd * np.sqrt(PERIODS_PER_YEAR)
    sharpe = float(np.mean(r) / sd * np.sqrt(PERIODS_PER_YEAR)) if sd > 0 else 0.0
    dd = eq / np.maximum.accumulate(eq) - 1.0
    return Metrics(
        cagr=float(cagr),
        vol=float(vol),
        sharpe=sharpe,
        max_dd=float(dd.min()),
        time_in_market=float(np.mean(ex > 0)),
        avg_exposure=float(np.mean(ex)),
        round_trips=round_trips,
        turnover_per_year=float(turnover_sum / n_years),
        total_return=total - 1.0,
        n_periods=int(r.shape[0]),
        start=dates[0].isoformat(),
        end=dates[-1].isoformat(),
    )


def backtest_exposure(
    exposure: np.ndarray,
    opens: np.ndarray,
    closes: np.ndarray,
    dates: Sequence[date],
    cost_bps: int,
    warmup: int = DEFAULT_WARMUP,
    band: float | None = None,
    oos_start: date = DEFAULT_OOS_START,
    round_trips: int | None = None,
) -> BacktestResult:
    """Evaluate a target-exposure series with the study conventions (see module docstring).

    ``dates`` are bar OPEN dates aligned with the arrays; ``cost_bps`` is per side.
    """
    e_raw = np.asarray(exposure, dtype=np.float64)
    o = np.asarray(opens, dtype=np.float64)
    c = np.asarray(closes, dtype=np.float64)
    n = e_raw.shape[0]
    if not (o.shape[0] == c.shape[0] == n == len(dates)):
        raise ValueError("exposure, opens, closes and dates must have equal length")
    if n <= warmup + 1:
        raise ValueError("not enough bars for the warm-up window")
    e = np.where(np.isnan(e_raw), 0.0, e_raw).copy()
    e[:warmup] = 0.0
    px = _execution_prices(o, c)
    cost = cost_bps / 10_000.0
    r_all, held_all, turnover = simulate_returns(e, px, cost, band)
    r = r_all[warmup:]
    ex = held_all[warmup:]
    labels = [_as_date(d) for d in dates[warmup + 1 :]]
    rt_full = count_round_trips(e_raw) if round_trips is None else int(round_trips)
    full = _metrics(r, ex, labels, float(turnover[warmup:].sum()), rt_full)
    label_arr = np.array([d.toordinal() for d in labels])
    is_mask = label_arr < oos_start.toordinal()
    oos_mask = ~is_mask
    tv = turnover[warmup:-1] if turnover.shape[0] > warmup + 1 else turnover[warmup:]
    in_sample = _metrics(
        r[is_mask],
        ex[is_mask],
        [d for d, m in zip(labels, is_mask, strict=True) if m],
        float(tv[is_mask].sum()),
        count_round_trips(ex[is_mask]),
    )
    last_24m = _metrics(
        r[oos_mask],
        ex[oos_mask],
        [d for d, m in zip(labels, oos_mask, strict=True) if m],
        float(tv[oos_mask].sum()),
        count_round_trips(ex[oos_mask]),
    )
    years: dict[int, float] = {}
    for y in sorted({d.year for d in labels}):
        mask = np.array([d.year == y for d in labels])
        years[y] = float(np.prod(1.0 + r[mask]) - 1.0)
    return BacktestResult(
        full=full,
        in_sample=in_sample,
        last_24m=last_24m,
        calendar_years=years,
        equity=np.cumprod(1.0 + r).tolist(),
        dates=[d.isoformat() for d in labels],
    )


def _as_date(d: date | datetime) -> date:
    return d.date() if isinstance(d, datetime) else d


# --------------------------------------------------------------------------- rule exposures


def rule_exposure(
    rule: str,
    sol: list[Candle],
    btc: list[Candle] | None,
    btc_confirm: bool = False,
    trend: SolTrendParams | None = None,
    regime: RegimeParams | None = None,
    beta_cap_max: float = 0.50,
    beta_cap_vol: float = 0.30,
) -> tuple[np.ndarray, int]:
    """Target exposure series and round-trip count for one named rule.

    ``trend``/``regime`` override the live defaults (used by the Study-1 reproduction
    with cap=1 / vol_target=None). BTC closes are aligned to the SOL bars by timestamp.
    """
    _, h, lo, c = candles_to_arrays(sol)
    btc_close = align_closes(sol, btc) if (btc_confirm and btc is not None) else None
    if rule == RULE_BUY_AND_HOLD:
        return np.ones(c.shape[0]), 1
    if rule == RULE_SMA200:
        pr = (regime or RegimeParams(weight=1.0)).model_copy(update={"btc_confirm": btc_confirm})
        e = regime_exposure(c, pr, btc_close)
        return e, count_round_trips(e)
    if rule == RULE_DONCHIAN:
        pt = (trend or SolTrendParams(vol_target=None, cap=1.0)).model_copy(
            update={"btc_confirm": btc_confirm}
        )
        d = ensemble_detail(h, lo, c, pt, btc_close)
        return d.exposure, d.round_trips
    if rule == RULE_DONCHIAN_VT:
        pv = (trend or SolTrendParams(vol_target=0.25, cap=1.0)).model_copy(
            update={"btc_confirm": btc_confirm}
        )
        d = ensemble_detail(h, lo, c, pv, btc_close)
        return d.exposure, d.round_trips
    if rule == RULE_COMBINED:
        pa = (trend or SolTrendParams()).model_copy(update={"btc_confirm": btc_confirm})
        pb = (regime or RegimeParams()).model_copy(update={"btc_confirm": btc_confirm})
        a = ensemble_detail(h, lo, c, pa, btc_close).exposure
        b = regime_exposure(c, pb, btc_close)
        e = combined_exposure(a, b, realized_vol(c, pa.vol_lookback), beta_cap_max, beta_cap_vol)
        return e, count_round_trips(e)
    raise ValueError(f"unknown rule {rule!r}")


def run_grid(
    sol: list[Candle],
    btc: list[Candle],
    cost_bps_list: Sequence[int] = (5, 10, 30),
    vol_targets: Sequence[float | None] = (0.25, None),
    btc_confirm: Sequence[bool] = (False, True),
    bands: Sequence[float | None] = (None, 0.02),
    warmup: int = DEFAULT_WARMUP,
    oos_start: date = DEFAULT_OOS_START,
) -> dict[str, BacktestResult]:
    """Every rule x cost x btc_confirm x band. Keys from :func:`grid_key`.

    ``vol_targets`` selects which Donchian variants run: 0.25 -> ``donchian_ensemble_voltarget25``,
    None -> ``donchian_ensemble``. ``combined_default`` uses the live parameters
    (cap 0.40, vol target 0.25, regime weight 0.25, beta cap min(0.50, 0.30/rv90)).
    """
    o, _, _, c = candles_to_arrays(sol)
    dates = [b.ts.date() for b in sol]
    rules = [RULE_BUY_AND_HOLD, RULE_SMA200]
    if None in vol_targets:
        rules.append(RULE_DONCHIAN)
    if 0.25 in vol_targets:
        rules.append(RULE_DONCHIAN_VT)
    rules.append(RULE_COMBINED)
    out: dict[str, BacktestResult] = {}
    for rule in rules:
        for confirm in btc_confirm:
            if rule == RULE_BUY_AND_HOLD and confirm:
                continue
            e, rt = rule_exposure(rule, sol, btc, btc_confirm=confirm)
            for cost in cost_bps_list:
                for band in bands:
                    out[grid_key(rule, cost, confirm, band)] = backtest_exposure(
                        e, o, c, dates, cost, warmup=warmup, band=band, oos_start=oos_start, round_trips=rt
                    )
    return out


# --------------------------------------------------------------------------- report


def _pct(x: float | None, d: int = 1) -> str:
    return "n/a" if x is None else f"{100 * x:.{d}f}%"


def _num(x: float | None, d: int = 2) -> str:
    return "n/a" if x is None else f"{x:.{d}f}"


def _table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    lines.extend("| " + " | ".join(str(c) for c in row) + " |" for row in rows)
    return "\n".join(lines)


def btc_confirm_decision(results: dict[str, BacktestResult], cost_bps: int = 30) -> tuple[bool, list[str]]:
    """The deterministic WP-F rule: default btc_confirm=true ONLY if, for BOTH live rules
    (voltarget25 and sma200), the confirmed variant's last-24m Sharpe >= the unconfirmed
    one's and its last-24m max drawdown is not worse (>=, i.e. less negative)."""
    notes: list[str] = []
    ok = True
    for rule in (RULE_DONCHIAN_VT, RULE_SMA200):
        off = results.get(grid_key(rule, cost_bps, False, None))
        on = results.get(grid_key(rule, cost_bps, True, None))
        if off is None or on is None:
            notes.append(f"{rule}: variant missing -> rule cannot pass")
            ok = False
            continue
        sharpe_ok = on.last_24m.sharpe >= off.last_24m.sharpe
        dd_ok = on.last_24m.max_dd >= off.last_24m.max_dd
        notes.append(
            f"{rule}: last-24m Sharpe {_num(on.last_24m.sharpe)} vs {_num(off.last_24m.sharpe)} "
            f"({'ok' if sharpe_ok else 'worse'}); last-24m MaxDD {_pct(on.last_24m.max_dd)} vs "
            f"{_pct(off.last_24m.max_dd)} ({'ok' if dd_ok else 'worse'})"
        )
        ok = ok and sharpe_ok and dd_ok
    return ok, notes


def render_report(results: dict[str, BacktestResult], primary_cost_bps: int = 30) -> str:
    """Markdown report (docs/BACKTEST.md)."""
    costs = sorted({int(k.split("|cost=")[1].split("|")[0]) for k in results})
    if primary_cost_bps not in costs and costs:
        primary_cost_bps = costs[-1]
    rules = [r for r in RULES if grid_key(r, primary_cost_bps) in results]
    md: list[str] = []
    md.append("# Tiller backtest report\n")
    md.append(
        "Generated by `python -m tiller.backtest` from `data/history/SOLUSDT_1d.csv` (+ BTC for the "
        "confirmation gate) using the SAME exposure functions the live agent runs "
        "(`tiller.strategies.sol_trend`). Conventions: signal on the daily close of bar t, fill at the "
        "open of bar t+1, cost per side on |change in exposure|, 250-bar warm-up excluded from evaluation, "
        "365 periods/year, Sharpe at 0% risk-free. 'IS' = evaluation start -> 2024-09-23; 'last 24m' = "
        "2024-09-24 -> end of data. Weights are fractions of TOTAL equity; `combined_default` is the netted "
        "A+B SOL weight under the portfolio beta cap min(0.50, 0.30/rv90), i.e. what the allocator runs.\n"
    )
    base = results[grid_key(rules[0], primary_cost_bps)]
    md.append(
        f"Evaluation window: {base.full.start} -> {base.full.end} ({base.full.n_periods} daily periods).\n"
    )

    md.append(f"## Headline at {primary_cost_bps} bps/side (no band, btc_confirm off)\n")
    rows: list[Sequence[object]] = []
    for rule in rules:
        m = results[grid_key(rule, primary_cost_bps)]
        f, i, o = m.full, m.in_sample, m.last_24m
        rows.append(
            [
                rule,
                _pct(f.cagr),
                _pct(f.vol),
                _num(f.sharpe),
                _pct(f.max_dd),
                _pct(f.time_in_market),
                _num(f.avg_exposure),
                f.round_trips,
                _num(f.turnover_per_year, 1),
                _num(i.sharpe),
                _pct(i.cagr),
                _num(o.sharpe),
                _pct(o.cagr),
                _pct(o.max_dd),
            ]
        )
    md.append(
        _table(
            [
                "rule",
                "CAGR",
                "vol",
                "Sharpe",
                "MaxDD",
                "time in mkt",
                "avg exp",
                "round trips",
                "turnover/yr",
                "IS Sharpe",
                "IS CAGR",
                "24m Sharpe",
                "24m CAGR",
                "24m MaxDD",
            ],
            rows,
        )
    )

    years = sorted({y for rule in rules for y in results[grid_key(rule, primary_cost_bps)].calendar_years})
    md.append(f"\n## Calendar-year returns at {primary_cost_bps} bps\n")
    rows = []
    for rule in rules:
        cy = results[grid_key(rule, primary_cost_bps)].calendar_years
        rows.append([rule] + [_pct(cy.get(y)) for y in years])
    md.append(_table(["rule"] + [str(y) for y in years], rows))

    md.append("\n## Cost sensitivity (CAGR / Sharpe / MaxDD, no band, btc_confirm off)\n")
    rows = []
    for rule in rules:
        cells = []
        for cst in costs:
            mc = results.get(grid_key(rule, cst))
            cells.append(
                "n/a"
                if mc is None
                else f"{_pct(mc.full.cagr)} / {_num(mc.full.sharpe)} / {_pct(mc.full.max_dd)}"
            )
        rows.append([rule, *cells])
    md.append(_table(["rule"] + [f"{c} bps" for c in costs], rows))

    md.append(f"\n## btc_confirm on vs off at {primary_cost_bps} bps (no band)\n")
    rows = []
    for rule in rules:
        for confirm in (False, True):
            mb = results.get(grid_key(rule, primary_cost_bps, confirm))
            if mb is None:
                continue
            m = mb
            rows.append(
                [
                    rule,
                    "on" if confirm else "off",
                    _pct(m.full.cagr),
                    _num(m.full.sharpe),
                    _pct(m.full.max_dd),
                    m.full.round_trips,
                    _num(m.last_24m.sharpe),
                    _pct(m.last_24m.cagr),
                    _pct(m.last_24m.max_dd),
                ]
            )
    md.append(
        _table(
            [
                "rule",
                "btc_confirm",
                "CAGR",
                "Sharpe",
                "MaxDD",
                "round trips",
                "24m Sharpe",
                "24m CAGR",
                "24m MaxDD",
            ],
            rows,
        )
    )
    ok, notes = btc_confirm_decision(results, primary_cost_bps)
    md.append(
        "\nDeterministic default rule (docs/SPEC.md decision 1): btc_confirm defaults to true ONLY if, for both "
        "`donchian_ensemble_voltarget25` and `sma200_hyst2pct`, the confirmed variant has last-24m Sharpe >= and "
        "last-24m MaxDD not worse than the unconfirmed variant.\n"
    )
    md.extend(f"- {n}" for n in notes)
    md.append(f"\n**Rule outcome: btc_confirm default = {'true' if ok else 'false'}.**\n")

    bands = sorted({k.split("|band=")[1] for k in results})
    if len(bands) > 1:
        md.append(f"\n## Rebalance band on vs off at {primary_cost_bps} bps (btc_confirm off)\n")
        rows = []
        for rule in rules:
            for band_s in bands:
                band = None if band_s == "none" else float(band_s)
                mbd = results.get(grid_key(rule, primary_cost_bps, False, band))
                if mbd is None:
                    continue
                m = mbd
                rows.append(
                    [
                        rule,
                        band_s,
                        _pct(m.full.cagr),
                        _num(m.full.sharpe),
                        _pct(m.full.max_dd),
                        _num(m.full.turnover_per_year, 2),
                        _num(m.last_24m.sharpe),
                        _pct(m.last_24m.cagr),
                    ]
                )
        md.append(
            _table(["rule", "band", "CAGR", "Sharpe", "MaxDD", "turnover/yr", "24m Sharpe", "24m CAGR"], rows)
        )
        md.append(
            "\nThe band variant lets the held weight drift with price and only trades when |target - held| >= "
            "band (2% of equity; the $10 minimum is not representable in a fraction-of-equity backtest)."
        )

    md.append("\n## Last-24-month detail (all variants)\n")
    rows = []
    for key in sorted(results):
        ml = results[key].last_24m
        rows.append(
            [
                key,
                _pct(ml.cagr),
                _pct(ml.vol),
                _num(ml.sharpe),
                _pct(ml.max_dd),
                _num(ml.avg_exposure),
                ml.round_trips,
            ]
        )
    md.append(
        _table(
            ["variant", "24m CAGR", "24m vol", "24m Sharpe", "24m MaxDD", "24m avg exp", "24m round trips"],
            rows,
        )
    )
    md.append("")
    return "\n".join(md)
