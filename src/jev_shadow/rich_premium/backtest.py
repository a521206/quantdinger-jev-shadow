"""Score the frozen rule per split against every-cycle and random-half controls."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .cycles import build_index_cycles
from .rule import THRESHOLD, VARIANT

BOOTSTRAP_RESAMPLES = 10000
BOOTSTRAP_SEED = 7
RANDOM_DRAWS = 5000
RANDOM_SEED = 11
MARGIN_FRACTION = 0.10
SPLITS: dict[str, dict[str, tuple[str, str]]] = {
    "nifty": {
        "MONTHLY_2012_2019": ("2012-01-01", "2019-02-13"),
        "FIT": ("2019-02-14", "2021-06-30"),
        "CHOOSE": ("2021-07-01", "2022-12-31"),
        "TEST": ("2023-01-01", "2026-11-23"),
        "TEST_before_2026-04-01": ("2023-01-01", "2026-03-31"),
        "TEST_from_2026-04-01": ("2026-04-01", "2026-11-23"),
        "ALL_before_2020": ("2012-01-01", "2019-12-31"),
        "ALL_from_2020": ("2020-01-01", "2026-11-23"),
        "WEEKLY_ERA_ALL": ("2019-02-14", "2026-11-23"),
    },
    "banknifty": {"BANKNIFTY": ("2016-06-02", "2024-11-30")},
    "sensex": {
        "SENSEX": ("2024-01-01", "2026-11-23"),
        "SENSEX_before_2026-04-01": ("2024-01-01", "2026-03-31"),
        "SENSEX_from_2026-04-01": ("2026-04-01", "2026-11-23"),
    },
}


def scorable(cycles: pd.DataFrame) -> pd.DataFrame:
    """Cycles with a trade and at least 52 past ratios, with net results per straddle and lot."""
    df = cycles[(cycles["skip"].fillna("") == "") & cycles[THRESHOLD].notna()].copy()
    df["net1"] = df["gross_pts"] - df["cost1_pts"]
    df["net2"] = df["gross_pts"] - df["cost2_pts"]
    df["net1_rs"] = df["net1"] * df["lot_size"]
    df["margin_rs"] = MARGIN_FRACTION * df["spot"] * df["lot_size"]
    return df


def boot_ci(x: np.ndarray, seed: int = BOOTSTRAP_SEED) -> tuple[float, float]:
    """Percentile 95% interval of the mean, resampling cycles (one trade per expiry)."""
    if len(x) < 2:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    means = x[rng.integers(0, len(x), size=(BOOTSTRAP_RESAMPLES, len(x)))].mean(axis=1)
    return (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))


def max_drawdown(values: np.ndarray) -> float:
    if len(values) == 0:
        return 0.0
    curve = np.concatenate([[0.0], np.cumsum(values)])
    return float((curve - np.maximum.accumulate(curve)).min())


def summarize(trades: pd.DataFrame) -> dict[str, Any]:
    trades = trades.sort_values("expiry")
    x1, x2 = trades["net1"].to_numpy(), trades["net2"].to_numpy()
    if len(trades) == 0:
        return {"n": 0}
    worst = trades.loc[trades["net1"].idxmin()]
    lo1, hi1 = boot_ci(x1)
    lo2, hi2 = boot_ci(x2)
    return {
        "n": len(trades),
        "mean": round(float(x1.mean()), 2),
        "ci": [round(lo1, 2), round(hi1, 2)],
        "hit": round(float((x1 > 0).mean()), 3),
        "worst_pts": round(float(worst["net1"]), 1),
        "worst_rs_lot": round(float(worst["net1_rs"])),
        "worst_date": str(worst["expiry"].date()),
        "worst_pct_margin": round(float(worst["net1_rs"] / worst["margin_rs"] * 100), 1),
        "mdd_pts": round(max_drawdown(x1), 1),
        "mdd_rs_lot": round(max_drawdown(trades["net1_rs"].to_numpy())),
        "total_rs_lot": round(float(trades["net1_rs"].sum())),
        "mean_2x": round(float(x2.mean()), 2),
        "ci_2x": [round(lo2, 2), round(hi2, 2)],
        "mean_premium": round(float(trades["premium"].mean()), 1),
    }


def random_half(
    all_cycles: pd.DataFrame, rule_mean: float, seed: int = RANDOM_SEED
) -> dict[str, Any]:
    x = all_cycles["net1"].to_numpy()
    if len(x) < 4:
        return {}
    rng = np.random.default_rng(seed)
    half = len(x) // 2
    means = np.array(
        [x[rng.choice(len(x), half, replace=False)].mean() for _ in range(RANDOM_DRAWS)]
    )
    return {
        "random_half_mean": round(float(means.mean()), 2),
        "random_half_95pct": round(float(np.percentile(means, 95)), 2),
        "share_of_draws_at_or_above_rule": round(float((means >= rule_mean).mean()), 4),
    }


def block(df: pd.DataFrame, start: str, end: str) -> dict[str, Any]:
    window = df[(df["expiry"] >= start) & (df["expiry"] <= end)]
    selected_mask = window["ratio"] > window[THRESHOLD]
    selected = window[selected_mask]
    rule = summarize(selected)
    control = summarize(window)
    out: dict[str, Any] = {"rule": rule, "control_every_cycle": control}
    if rule.get("n", 0) > 0:
        out["control_random_half"] = random_half(window, rule["mean"])
        skipped = window.loc[~selected_mask, "net1"]
        diff = selected["net1"].mean() - skipped.mean() if (~selected_mask).sum() else np.nan
        out["rule_minus_skipped_mean"] = round(float(diff), 2)
        out["rule_minus_every_cycle_mean"] = round(rule["mean"] - control["mean"], 2)
        out["selection_rate"] = round(float(selected_mask.mean()), 3)
    return out


def format_row(name: str, result: dict[str, Any]) -> str:
    r, c = result["rule"], result["control_every_cycle"]
    if r.get("n", 0) == 0:
        return f"{name:<22} n=0"
    rh = result.get("control_random_half", {})
    return (
        f"{name:<22} n={r['n']:>3} mean={r['mean']:>7.2f} ci=[{r['ci'][0]:>7.2f},{r['ci'][1]:>7.2f}] "
        f"hit={r['hit']:.2f} worst={r['worst_pts']:>7.1f}pts/{r['worst_rs_lot']:>8}rs "
        f"({r['worst_date']}, {r['worst_pct_margin']}% margin) "
        f"mdd={r['mdd_pts']:>7.1f}pts/{r['mdd_rs_lot']:>8}rs 2x={r['mean_2x']:>7.2f} "
        f"[{r['ci_2x'][0]:.2f},{r['ci_2x'][1]:.2f}] | "
        f"every-cycle n={c['n']} mean={c['mean']:.2f} ci=[{c['ci'][0]:.2f},{c['ci'][1]:.2f}] "
        f"2x={c['mean_2x']:.2f} | "
        f"random-half mean={rh.get('random_half_mean')} "
        f"p(draw>=rule)={rh.get('share_of_draws_at_or_above_rule')}"
    )


def score(cycles_by_index: dict[str, pd.DataFrame]) -> dict[str, Any]:
    results: dict[str, Any] = {"variant": VARIANT}
    for index, splits in SPLITS.items():
        if index not in cycles_by_index:
            continue
        df = scorable(cycles_by_index[index])
        for split, (start, end) in splits.items():
            results[split] = block(df, start, end)
    return results


def run_backtest(panel_dir: Path, output_dir: Path, indices: list[str]) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    cycles_by_index = {}
    for index in indices:
        cycles, notes = build_index_cycles(panel_dir, index)
        cycles.to_csv(output_dir / f"cycles_{index}_d1.csv", index=False)
        (output_dir / f"expiry_relabels_{index}.txt").write_text("\n".join(notes) + "\n")
        cycles_by_index[index] = cycles
    results = score(cycles_by_index)
    lines = [format_row(name, value) for name, value in results.items() if name != "variant"]
    (output_dir / "test_results.json").write_text(json.dumps(results, indent=1))
    (output_dir / "test_output.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return results
