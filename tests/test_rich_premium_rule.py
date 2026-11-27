import math

import numpy as np
import pandas as pd
import pytest

from jev_shadow.rich_premium.rule import (
    LOOKBACK_CYCLES,
    cost_pts,
    fires,
    gross_payoff,
    premium_ratio,
    stt_rate,
    trailing_threshold,
)


@pytest.mark.parametrize(
    ("day", "rate"),
    [
        ("2016-05-31", 0.00017),
        ("2016-06-01", 0.0005),
        ("2023-03-31", 0.0005),
        ("2023-04-01", 0.000625),
        ("2024-09-30", 0.000625),
        ("2024-10-01", 0.001),
        ("2026-03-31", 0.001),
        ("2026-04-01", 0.0015),
    ],
)
def test_stt_schedule_switches_on_effective_dates(day: str, rate: float) -> None:
    assert stt_rate(pd.Timestamp(day)) == rate


def test_cost_is_four_points_plus_sale_stt_on_premium() -> None:
    assert cost_pts(200.0, pd.Timestamp("2026-04-01")) == pytest.approx(4.0 + 200.0 * 0.0015)
    assert cost_pts(200.0, pd.Timestamp("2015-01-01")) == pytest.approx(4.0 + 200.0 * 0.00017)


def test_payoff_is_premium_minus_settlement_distance_from_strike() -> None:
    assert gross_payoff(150.0, 22_050.0, 22_000.0) == 100.0
    assert gross_payoff(150.0, 21_800.0, 22_000.0) == -50.0


def test_ratio_scales_premium_by_vix_implied_one_day_move() -> None:
    one_day_move = 20_000 * 0.16 * math.sqrt(1 / 252)
    assert premium_ratio(one_day_move, 20_000.0, 16.0) == pytest.approx(1.0)
    assert np.isnan(premium_ratio(100.0, 20_000.0, float("nan")))


def test_threshold_needs_52_past_ratios_and_ignores_current_and_future() -> None:
    ratios = pd.Series(np.linspace(0.5, 1.5, 80))
    threshold = trailing_threshold(ratios)
    assert threshold.iloc[:LOOKBACK_CYCLES].isna().all()
    for i in (LOOKBACK_CYCLES, 60, 79):
        past = ratios.iloc[i - LOOKBACK_CYCLES : i]
        assert threshold.iloc[i] == pytest.approx(past.quantile(0.67))

    bumped = ratios.copy()
    bumped.iloc[70:] = 100.0
    assert trailing_threshold(bumped).iloc[:71].equals(threshold.iloc[:71])


def test_threshold_skips_cycles_without_a_ratio() -> None:
    ratios = pd.Series(np.arange(60, dtype=float))
    ratios.iloc[10] = np.nan
    threshold = trailing_threshold(ratios)
    assert np.isnan(threshold.iloc[10])
    assert np.isnan(threshold.iloc[52])
    expected = ratios.drop(index=10).iloc[:52].quantile(0.67)
    assert threshold.iloc[53] == pytest.approx(expected)


def test_rule_fires_only_strictly_above_threshold() -> None:
    assert fires(0.9, 0.8)
    assert not fires(0.8, 0.8)
    assert not fires(1.0, float("nan"))
