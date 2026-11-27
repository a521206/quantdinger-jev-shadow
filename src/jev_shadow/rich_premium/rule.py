"""Frozen R1 rule, variant V2 (frozen 2026-11-24 before the TEST sample was scored).

Sell one ATM straddle at the close of the trading day before each expiry when
premium / (spot * VIX/100 * sqrt(1/252)) is above the 67th percentile of the previous
52 cycle ratios; hold to expiry. None of these constants may change without a new
pre-registration.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

VARIANT = "V2"
DAYS_BEFORE_EXPIRY = 1
LOOKBACK_CYCLES = 52
THRESHOLD_QUANTILE = 0.67
THRESHOLD = "thr67"
LEG_COST_PTS = 2.0
TRADING_DAYS_PER_YEAR = 252
STT_SCHEDULE = (
    (pd.Timestamp("2016-06-01"), 0.00017),
    (pd.Timestamp("2023-04-01"), 0.0005),
    (pd.Timestamp("2024-10-01"), 0.000625),
    (pd.Timestamp("2026-04-01"), 0.001),
)
STT_RATE_FROM_2026_04 = 0.0015


def stt_rate(day: pd.Timestamp) -> float:
    """Option-sale STT on premium for a sale on ``day``; the seller pays no exercise STT."""
    for before, rate in STT_SCHEDULE:
        if day < before:
            return rate
    return STT_RATE_FROM_2026_04


def cost_pts(premium: float, entry: pd.Timestamp) -> float:
    """2 legs x 2 points plus option-sale STT on the premium."""
    return 2 * LEG_COST_PTS + premium * stt_rate(entry)


def gross_payoff(premium: float, settle_px: float, strike: float) -> float:
    return premium - abs(settle_px - strike)


def premium_ratio(premium: float, spot: float, vix: float) -> float:
    """Straddle premium relative to the VIX-implied one-day move."""
    if np.isnan(vix):
        return np.nan
    return premium / (spot * vix / 100 * np.sqrt(1 / TRADING_DAYS_PER_YEAR))


def trailing_threshold(ratios: pd.Series) -> pd.Series:
    """67th percentile of the previous 52 valid ratios; NaN until 52 exist (no lookahead)."""
    valid = ratios.notna()
    out = pd.Series(np.nan, index=ratios.index)
    history = ratios[valid].shift(1).rolling(LOOKBACK_CYCLES, min_periods=LOOKBACK_CYCLES)
    out[valid] = history.quantile(THRESHOLD_QUANTILE)
    return out


def fires(ratio: float, threshold: float) -> bool:
    return bool(ratio > threshold)
