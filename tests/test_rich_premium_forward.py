import pandas as pd

from jev_shadow.rich_premium.forward import LEDGER_COLUMNS, Ledger


def _decision(premium: str, fire: str) -> dict[str, str]:
    return {"entry_date": "2026-11-30", "premium": premium, "fire": fire, "thr67": "0.8"}


def test_ledger_appends_without_rewriting_earlier_rows(tmp_path) -> None:
    path = tmp_path / "ledger.csv"
    expiry = pd.Timestamp("2026-12-01")
    ledger = Ledger(path, "forward")
    ledger.append("t1", "nifty", expiry, "pending", {}, "awaiting close")
    ledger.append("t2", "nifty", expiry, "decision", _decision("200.000000", "1"))
    before = path.read_text()

    reopened = Ledger(path, "forward")
    reopened.append("t3", "nifty", expiry, "correction", _decision("210.000000", "0"), "changed")
    after = path.read_text()

    assert after.startswith(before)
    assert after.splitlines()[0] == ",".join(LEDGER_COLUMNS)
    assert [row["row"] for row in Ledger(path, "forward").rows] == ["1", "2", "3"]


def test_latest_correction_counts_and_void_is_recorded(tmp_path) -> None:
    ledger = Ledger(tmp_path / "ledger.csv", "forward")
    expiry = pd.Timestamp("2026-12-01")
    ledger.append("t1", "nifty", expiry, "decision", _decision("200.000000", "1"))
    ledger.append("t2", "nifty", expiry, "correction", _decision("210.000000", "0"))
    state = ledger.state()[("NIFTY", "2026-12-01")]
    assert (state["premium"], state["fire"], state["decided"]) == ("210.000000", "0", True)
    assert not state["settled"]

    ledger.append("t3", "nifty", expiry, "void", {}, "expiry no longer in exchange data")
    assert ledger.state()[("NIFTY", "2026-12-01")]["void"]
