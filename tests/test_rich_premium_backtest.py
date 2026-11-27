import pandas as pd

from jev_shadow.rich_premium.backtest import block, scorable


def test_rule_trades_only_rich_cycles_and_control_trades_every_cycle() -> None:
    expiries = pd.date_range("2024-01-04", periods=6, freq="7D")
    cycles = pd.DataFrame(
        {
            "expiry": expiries,
            "skip": ["", "", "", "", "", "no strike with both legs traded"],
            "ratio": [1.2, 0.7, 1.1, 0.6, 1.3, float("nan")],
            "thr67": [1.0, 1.0, 1.0, 1.0, float("nan"), float("nan")],
            "gross_pts": [30.0, -20.0, 10.0, -40.0, 50.0, 0.0],
            "cost1_pts": [4.0] * 6,
            "cost2_pts": [8.0] * 6,
            "lot_size": [75.0] * 6,
            "spot": [20_000.0] * 6,
            "premium": [100.0] * 6,
        }
    )
    result = block(scorable(cycles), "2024-01-01", "2024-12-31")
    assert result["rule"]["n"] == 2
    assert result["rule"]["mean"] == (26.0 + 6.0) / 2
    assert result["control_every_cycle"]["n"] == 4
    assert result["control_every_cycle"]["mean"] == (26.0 - 24.0 + 6.0 - 44.0) / 4
    assert result["selection_rate"] == 0.5
