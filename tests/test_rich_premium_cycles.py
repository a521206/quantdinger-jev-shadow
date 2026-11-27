import numpy as np
import pandas as pd
import pytest

from jev_shadow.rich_premium.cycles import (
    SKIP_NO_BOTH_LEGS,
    SKIP_NOT_LISTED,
    build_cycles,
    pick_atm,
)


def _row(day: str, expiry: str, strike: float, kind: str, close: float, spot: float) -> dict:
    return {
        "date": pd.Timestamp(day),
        "expiry": pd.Timestamp(expiry),
        "strike": strike,
        "type": kind,
        "close": close,
        "settle": np.nan,
        "traded": not np.isnan(close),
        "underlying_close": spot,
        "lot_size": 75.0,
    }


def _chain(day: str, expiry: str, spot: float, legs: dict[float, tuple[float, float]]) -> list:
    rows = []
    for strike, (call, put) in legs.items():
        rows.append(_row(day, expiry, strike, "CE", call, spot))
        rows.append(_row(day, expiry, strike, "PE", put, spot))
    return rows


def test_atm_pick_skips_strikes_missing_a_traded_leg() -> None:
    chain = pd.DataFrame(
        _chain("2026-01-05", "2026-01-06", 100.0, {100.0: (5.0, np.nan), 105.0: (3.0, 8.0)})
    )
    assert pick_atm(chain, 100.0) == (105.0, 3.0, 8.0)


def test_atm_pick_breaks_ties_to_the_lower_strike() -> None:
    chain = pd.DataFrame(
        _chain("2026-01-05", "2026-01-06", 102.5, {100.0: (5.0, 2.0), 105.0: (3.0, 6.0)})
    )
    assert pick_atm(chain, 102.5) == (100.0, 5.0, 2.0)


def test_atm_pick_returns_none_without_a_two_legged_strike() -> None:
    chain = pd.DataFrame(
        _chain("2026-01-05", "2026-01-06", 100.0, {100.0: (5.0, np.nan), 105.0: (np.nan, 8.0)})
    )
    assert pick_atm(chain, 100.0) is None


def test_cycle_enters_the_trading_day_before_expiry_and_scores_the_payoff() -> None:
    rows = _chain("2026-01-02", "2026-01-06", 100.0, {100.0: (9.0, 9.0)})
    rows += _chain("2026-01-05", "2026-01-06", 101.0, {100.0: (4.0, 3.0), 105.0: (1.0, 6.0)})
    rows += _chain("2026-01-06", "2026-01-06", 110.0, {100.0: (10.0, 0.05)})
    vix = pd.Series({pd.Timestamp("2026-01-05"): 16.0})
    cycles, _ = build_cycles(pd.DataFrame(rows), vix, "nifty", "2026-01-01", "2026-01-31")
    cycle = cycles.iloc[0]
    assert cycle["entry"] == pd.Timestamp("2026-01-05")
    assert (cycle["strike"], cycle["premium"], cycle["settle_px"]) == (100.0, 7.0, 110.0)
    assert cycle["gross_pts"] == pytest.approx(7.0 - 10.0)
    assert cycle["cost1_pts"] == pytest.approx(4.0 + 7.0 * 0.001)
    assert cycle["ratio"] == pytest.approx(7.0 / (101.0 * 0.16 * np.sqrt(1 / 252)))


def test_cycle_skip_reasons_are_recorded() -> None:
    rows = _chain("2026-01-05", "2026-01-06", 100.0, {100.0: (5.0, np.nan)})
    rows += _chain("2026-01-06", "2026-01-06", 100.0, {100.0: (1.0, 1.0)})
    rows += _chain("2026-01-06", "2026-01-08", 100.0, {100.0: (1.0, 1.0)})
    rows += _chain("2026-01-08", "2026-01-08", 100.0, {100.0: (1.0, 1.0)})
    rows += _chain("2026-01-07", "2026-01-13", 100.0, {100.0: (1.0, 1.0)})
    vix = pd.Series(dtype=float)
    cycles, _ = build_cycles(pd.DataFrame(rows), vix, "nifty", "2026-01-06", "2026-01-08")
    assert list(cycles["skip"]) == [SKIP_NO_BOTH_LEGS, SKIP_NOT_LISTED]
