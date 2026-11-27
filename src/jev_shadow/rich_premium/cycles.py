"""Per-expiry cycle table: entry-day ATM straddle, premium ratio, expiry payoff and cost."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .rule import (
    DAYS_BEFORE_EXPIRY,
    LEG_COST_PTS,
    cost_pts,
    gross_payoff,
    premium_ratio,
    stt_rate,
    trailing_threshold,
)

PANEL_COLS = ["date", "expiry", "strike", "type", "close", "settle", "traded", "underlying_close", "lot_size"]  # fmt: skip
INDICES = ("nifty", "banknifty", "sensex")
START_BY_INDEX = {"nifty": "2012-01-01", "banknifty": "2012-01-01", "sensex": "2024-01-01"}
END_BY_INDEX = {"nifty": "2026-11-23", "banknifty": "2024-11-30", "sensex": "2026-11-23"}
WEEKLY_START = {"nifty": "2019-02-14", "banknifty": "2016-06-02", "sensex": "2024-01-05"}
SKIP_NOT_TRADING_DAY = "expiry not a trading day in panel"
SKIP_NOT_LISTED = "expiry not listed on entry day"
SKIP_NO_BOTH_LEGS = "no strike with both legs traded"


def load_options(panel_dir: Path, index: str) -> pd.DataFrame:
    return pd.read_parquet(panel_dir / f"{index}_options_daily.parquet", columns=PANEL_COLS)


def load_vix_close(panel_dir: Path) -> pd.Series:
    return pd.read_parquet(panel_dir / "india_vix_daily.parquet").set_index("date")["close"]


def unify_expiry_labels(opts: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Map contracts whose label moved (holiday shifts) onto the date they actually expired."""
    trading_days = np.array(sorted(opts["date"].unique()))
    data_end = trading_days[-1]
    span = opts.groupby("expiry")["date"].agg(["min", "max"])
    relabel: dict[Any, Any] = {}
    notes: list[str] = []
    for label, (first, last) in span.iterrows():
        if last >= label or label > data_end:
            continue
        near = (span.index - label).map(abs) <= pd.Timedelta(days=7)
        later = span[(span.index != label) & (span["min"] > last) & near]
        if not later.empty:
            target = later.index[np.argmin(np.abs((later.index - label).days))]
            notes.append(f"{label.date()} listed to {last.date()} continues as {target.date()}")
        else:
            next_day = (
                trading_days[np.searchsorted(trading_days, last, side="right")]
                if last < data_end
                else None
            )
            if next_day is not None and next_day <= label:
                notes.append(
                    f"{label.date()} listed to {last.date()}, dropped "
                    "(no continuation, not a holiday shift)"
                )
                relabel[label] = pd.NaT
                continue
            target = last
            notes.append(f"{label.date()} expired early on {last.date()} (holiday shift)")
        relabel[label] = target
    if relabel:
        mapped = opts["expiry"].map(relabel)
        opts = opts.assign(expiry=mapped.where(opts["expiry"].isin(relabel.keys()), opts["expiry"]))
        opts = opts[opts["expiry"].notna()]
    return opts, notes


def pick_atm(chain: pd.DataFrame, spot: float) -> tuple[float, float, float] | None:
    """Strike nearest spot among strikes where both CE and PE traded; ties go to the lower strike."""
    traded = chain[chain["traded"] & chain["close"].notna() & (chain["close"] > 0)]
    both = traded.pivot_table(
        index="strike", columns="type", values="close", aggfunc="first"
    ).dropna()
    if both.empty or not {"CE", "PE"} <= set(both.columns):
        return None
    strikes = both.index.to_numpy()
    order = np.lexsort((strikes, np.abs(strikes - spot)))
    strike = float(strikes[order[0]])
    return strike, float(both.loc[strike, "CE"]), float(both.loc[strike, "PE"])


def build_cycles(
    opts: pd.DataFrame,
    vix_close: pd.Series,
    index: str,
    start: str | None = None,
    end: str | None = None,
    days_before: int = DAYS_BEFORE_EXPIRY,
) -> tuple[pd.DataFrame, list[str]]:
    """One row per expiry in [start, end]; skipped cycles carry a non-empty ``skip`` reason."""
    opts, notes = unify_expiry_labels(opts)
    trading_days = np.array(sorted(opts["date"].unique()))
    day_pos = {d: i for i, d in enumerate(trading_days)}
    lo = pd.Timestamp(start or START_BY_INDEX[index])
    hi = pd.Timestamp(end or END_BY_INDEX[index])
    weekly_start = pd.Timestamp(WEEKLY_START[index])
    expiries = sorted(e for e in opts["expiry"].unique() if lo <= e <= hi)
    on_expiry = opts[opts["date"] == opts["expiry"]]
    expiry_close = on_expiry.groupby("date")["underlying_close"].first()
    entry_days = [
        trading_days[day_pos[e] - days_before]
        for e in map(pd.Timestamp, expiries)
        if e in day_pos and day_pos[e] >= days_before
    ]
    near_entry = opts[opts["date"].isin(entry_days)]
    chains = {key: group for key, group in near_entry.groupby(["date", "expiry"], sort=False)}

    rows: list[dict[str, Any]] = []
    for raw_expiry in expiries:
        expiry = pd.Timestamp(raw_expiry)
        kind = "weekly_era" if expiry >= weekly_start else "monthly_era"
        base: dict[str, Any] = {"expiry": expiry, "kind": kind}
        if expiry not in day_pos or expiry not in expiry_close.index:
            rows.append({**base, "skip": SKIP_NOT_TRADING_DAY})
            continue
        entry_idx = day_pos[expiry] - days_before
        if entry_idx < 0:
            continue
        entry = pd.Timestamp(trading_days[entry_idx])
        base["entry"] = entry
        chain = chains.get((entry, expiry))
        if chain is None or chain.empty:
            rows.append({**base, "skip": SKIP_NOT_LISTED})
            continue
        spot = float(chain["underlying_close"].iloc[0])
        atm = pick_atm(chain, spot)
        if atm is None:
            rows.append({**base, "skip": SKIP_NO_BOTH_LEGS})
            continue
        strike, call, put = atm
        lot = float(chain.loc[chain["strike"] == strike, "lot_size"].iloc[0])
        premium = call + put
        settle_px = float(expiry_close.loc[expiry])
        vix = float(vix_close.get(entry, np.nan))
        cost = cost_pts(premium, entry)
        rows.append(
            {
                **base,
                "skip": "",
                "spot": spot,
                "strike": strike,
                "strike_dist": strike - spot,
                "call": call,
                "put": put,
                "premium": premium,
                "vix": vix,
                "ratio": premium_ratio(premium, spot, vix),
                "settle_px": settle_px,
                "gross_pts": gross_payoff(premium, settle_px, strike),
                "cost1_pts": cost,
                "cost2_pts": 2 * (2 * LEG_COST_PTS + premium * stt_rate(entry)),
                "stt_rate": stt_rate(entry),
                "lot_size": lot,
            }
        )
    df = pd.DataFrame(rows)
    if "ratio" not in df:
        df["ratio"] = np.nan
    df["thr67"] = trailing_threshold(df["ratio"])
    return df, notes


def build_index_cycles(panel_dir: Path, index: str) -> tuple[pd.DataFrame, list[str]]:
    return build_cycles(load_options(panel_dir, index), load_vix_close(panel_dir), index)
