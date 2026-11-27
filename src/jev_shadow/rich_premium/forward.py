"""Forward paper tracker for the frozen rule: append-only ledger, status and self-test replay.

Paper only. Decisions use the entry-day close alone; a past ledger row is never changed.
"""

from __future__ import annotations

import csv
import json
import math
from collections.abc import Callable
from datetime import date, datetime
from functools import partial
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .cycles import PANEL_COLS, SKIP_NOT_TRADING_DAY, build_cycles, build_index_cycles, load_options
from .fetch import fetch_bse_day, fetch_fo_day, fetch_indices, has_fo_file, trading_days
from .panel import finalize, load_index, read_bse_fo, read_nse_fo, sensex_options
from .paths import DataPaths
from .rule import THRESHOLD
from .session import Polite

IST = ZoneInfo("Asia/Kolkata")
BOOKS = {"nifty": "NIFTY", "sensex": "SENSEX"}
LANE_START = {"nifty": pd.Timestamp("2012-01-01"), "sensex": pd.Timestamp("2024-01-01")}
FORWARD_AFTER = pd.Timestamp("2026-11-23")
FORWARD_SCOPE_END = pd.Timestamp("2100-01-01")
PANEL_SEED_FROM = pd.Timestamp("2022-06-01")
LOOKBACK = pd.Timedelta(days=3 * 365)
REVIEW_TRADES = 40
PASS_T = 2.0
KILL_PREMIUM_MULTIPLE = 3.0
REPLAY_START, REPLAY_END = "2025-01-01", "2026-11-23"
HOLIDAY_URL = "https://www.nseindia.com/api/holiday-master?type=trading"
HOLIDAY_REFERER = "https://www.nseindia.com/resources/exchange-communication-holidays"
SKIP_TOO_FEW_RATIOS = "fewer than 52 past ratios"

DECISION_FIELDS = [
    "entry_date", "spot", "vix", "call", "put", "premium", "strike", "ratio", "thr67", "fire",
    "lot_size", "cost_pts", "skip",
]  # fmt: skip
SETTLE_FIELDS = ["settle_px", "gross_pts", "net_pts", "net_rs_lot", "premium_rs_lot"]
LEDGER_COLUMNS = (
    ["row", "recorded_at", "mode", "book", "expiry", "event"]
    + DECISION_FIELDS
    + SETTLE_FIELDS
    + ["note"]
)

Scope = tuple[pd.Timestamp, pd.Timestamp]


def fmt(value: Any) -> str:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ""
    if isinstance(value, pd.Timestamp):
        return pd.Timestamp(value).date().isoformat()
    if isinstance(value, (float, np.floating)):
        return f"{float(value):.6f}"
    return str(value)


class Ledger:
    """Append-only CSV; the latest decision, correction, settle or void row for a cycle counts."""

    def __init__(self, path: Path, mode: str) -> None:
        self.path = path
        self.mode = mode
        self.rows: list[dict[str, str]] = []
        if path.exists():
            with path.open() as stream:
                self.rows = list(csv.DictReader(stream))

    def append(
        self,
        recorded_at: str,
        book: str,
        expiry: pd.Timestamp,
        event: str,
        record: dict[str, str],
        note: str = "",
    ) -> None:
        row = {c: "" for c in LEDGER_COLUMNS}
        row.update({k: v for k, v in record.items() if k in row})
        row.update(
            {
                "row": str(len(self.rows) + 1),
                "recorded_at": recorded_at,
                "mode": self.mode,
                "book": BOOKS[book],
                "expiry": fmt(expiry),
                "event": event,
                "note": note,
            }
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new_file = not self.path.exists()
        with self.path.open("a", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=LEDGER_COLUMNS)
            if new_file:
                writer.writeheader()
            writer.writerow(row)
        self.rows.append(row)

    def state(self) -> dict[tuple[str, str], dict[str, Any]]:
        out: dict[tuple[str, str], dict[str, Any]] = {}
        for row in self.rows:
            key = (row["book"], row["expiry"])
            flags = {"pending": False, "decided": False, "settled": False, "void": False}
            cur = out.setdefault(key, flags)
            if row["event"] == "pending":
                cur["pending"] = True
            elif row["event"] in ("decision", "correction"):
                cur.update({f: row[f] for f in DECISION_FIELDS + SETTLE_FIELDS})
                cur["decided"] = True
                cur["settled"] = row["event"] == "correction" and row["settle_px"] != ""
                cur["void"] = False
            elif row["event"] == "settle":
                cur.update({f: row[f] for f in SETTLE_FIELDS})
                cur["settled"] = True
            elif row["event"] == "void":
                cur["void"] = True
        return out


def cycle_record(row: pd.Series, settled: bool) -> dict[str, str]:
    if row["skip"]:
        return {"entry_date": fmt(row.get("entry")), "fire": "0", "skip": row["skip"]}
    has_threshold = not pd.isna(row[THRESHOLD])
    fire = bool(row["ratio"] > row[THRESHOLD]) if has_threshold else False
    record = {
        "entry_date": fmt(row["entry"]),
        "spot": fmt(row["spot"]),
        "vix": fmt(row["vix"]),
        "call": fmt(row["call"]),
        "put": fmt(row["put"]),
        "premium": fmt(row["premium"]),
        "strike": fmt(row["strike"]),
        "ratio": fmt(row["ratio"]),
        "thr67": fmt(row[THRESHOLD]),
        "fire": "1" if fire else "0",
        "lot_size": fmt(row["lot_size"]),
        "cost_pts": fmt(row["cost1_pts"]),
        "skip": "" if has_threshold else SKIP_TOO_FEW_RATIOS,
    }
    if settled:
        net = row["gross_pts"] - row["cost1_pts"]
        record.update(
            {
                "settle_px": fmt(row["settle_px"]),
                "gross_pts": fmt(row["gross_pts"]),
                "net_pts": fmt(net),
                "net_rs_lot": fmt(net * row["lot_size"]),
                "premium_rs_lot": fmt(row["premium"] * row["lot_size"]),
            }
        )
    return record


def differs(current: dict[str, Any], record: dict[str, str], fields: list[str]) -> bool:
    return any(current.get(f, "") != record.get(f, "") for f in fields)


def is_next_trading_day(
    day: pd.Timestamp, target: pd.Timestamp, holidays: set[pd.Timestamp]
) -> bool:
    gap = pd.date_range(day + pd.Timedelta(days=1), target - pd.Timedelta(days=1))
    return all(d.weekday() >= 5 or d in holidays for d in gap)


def evaluate_book(
    book: str, opts: pd.DataFrame, vix: pd.DataFrame
) -> tuple[pd.DataFrame, pd.Timestamp, pd.Timestamp | None]:
    """Cycles as of the last panel day, including the next expiry's entry-day decision."""
    last_day = opts["date"].max()
    start = max(LANE_START[book], last_day - LOOKBACK)
    window = opts[opts["date"] >= start]
    listed = window.loc[window["date"] == last_day, "expiry"]
    upcoming = listed[listed > last_day]
    next_expiry = upcoming.min() if not upcoming.empty else None
    if next_expiry is not None:
        # Stand-in row for the unseen expiry day so the cycle build computes its entry-day decision.
        stand_in = {
            "date": next_expiry, "expiry": next_expiry, "strike": 0.0, "type": "CE",
            "close": np.nan, "settle": np.nan, "traded": False,
            "underlying_close": np.nan, "lot_size": np.nan,
        }  # fmt: skip
        window = pd.concat([window, pd.DataFrame([stand_in])], ignore_index=True)
    vix_close = vix[vix["date"] <= last_day].set_index("date")["close"]
    end = str((next_expiry or last_day).date())
    cycles, _ = build_cycles(window[PANEL_COLS], vix_close, book, str(start.date()), end)
    cycles["skip"] = cycles["skip"].fillna("")
    return cycles, last_day, next_expiry


def step(
    book: str,
    opts: pd.DataFrame,
    vix: pd.DataFrame,
    holidays: set[pd.Timestamp],
    ledger: Ledger,
    recorded_at: str,
    scope: Scope,
) -> dict[str, Any]:
    cycles, last_day, next_expiry = evaluate_book(book, opts, vix)
    state = ledger.state()
    lo, hi = scope
    seen = set()
    provisional = None
    for _, row in cycles.iterrows():
        expiry = row["expiry"]
        if not (lo <= expiry <= hi):
            continue
        settled = expiry <= last_day
        if not settled:
            if expiry != next_expiry or row["skip"] == SKIP_NOT_TRADING_DAY:
                continue
            record = cycle_record(row, settled=False)
            is_entry_day = row.get("entry") == last_day
            if not is_next_trading_day(last_day, expiry, holidays) or not is_entry_day:
                provisional = record
                continue
        else:
            record = cycle_record(row, settled=not row["skip"])
        seen.add(expiry)
        current = state.get((BOOKS[book], fmt(expiry)), {})
        if not current.get("decided"):
            note = "recorded after expiry close" if settled else ""
            ledger.append(recorded_at, book, expiry, "decision", record, note)
            if settled and not record["skip"]:
                ledger.append(recorded_at, book, expiry, "settle", record)
        elif differs(current, record, DECISION_FIELDS):
            ledger.append(
                recorded_at, book, expiry, "correction", record, "entry-day inputs changed"
            )
        elif settled and not record["skip"] and not current.get("settled"):
            ledger.append(recorded_at, book, expiry, "settle", record)
        elif settled and current.get("settled") and differs(current, record, SETTLE_FIELDS):
            note = "settlement inputs changed"
            ledger.append(recorded_at, book, expiry, "correction", record, note)
    for (name, expiry_s), current in ledger.state().items():
        expiry = pd.Timestamp(expiry_s)
        if (
            name == BOOKS[book]
            and current["decided"]
            and not current["void"]
            and lo <= expiry < last_day
            and expiry not in seen
        ):
            ledger.append(
                recorded_at, book, expiry, "void", {}, "expiry no longer in exchange data"
            )
    if (
        next_expiry is not None
        and lo <= next_expiry <= hi
        and (BOOKS[book], fmt(next_expiry)) not in ledger.state()
    ):
        note = f"awaiting close of the trading day before {next_expiry.date()}"
        ledger.append(recorded_at, book, next_expiry, "pending", {}, note)
    return {"last_day": last_day, "next_expiry": next_expiry, "provisional": provisional}


def mean_t(x: pd.Series) -> tuple[float, float]:
    if len(x) == 0:
        return float("nan"), float("nan")
    mean = float(x.mean())
    if len(x) < 2 or x.std(ddof=1) == 0:
        return mean, float("nan")
    return mean, mean / (x.std(ddof=1) / math.sqrt(len(x)))


def summarize(state: dict[tuple[str, str], dict[str, Any]], scope: Scope) -> dict[str, Any]:
    """Pass/kill verdict fixed in the forward pre-registration."""
    trades = []
    for (name, expiry_s), cur in state.items():
        expiry = pd.Timestamp(expiry_s)
        in_scope = scope[0] <= expiry <= scope[1]
        if not in_scope or cur["void"] or not cur["settled"] or cur.get("skip"):
            continue
        trades.append(
            {
                "book": name,
                "expiry": expiry,
                "fire": cur["fire"] == "1",
                "net_pts": float(cur["net_pts"]),
                "net_rs": float(cur["net_rs_lot"]),
                "premium_rs": float(cur["premium_rs_lot"]),
            }
        )
    columns = ["book", "expiry", "fire", "net_pts", "net_rs", "premium_rs"]
    df = pd.DataFrame(trades, columns=columns).sort_values(["expiry", "book"])
    rule = df[df["fire"]].reset_index(drop=True)
    verdict, verdict_note = "RUNNING", ""
    for i in range(len(rule)):
        so_far = rule.iloc[: i + 1]
        last_expiry = so_far["expiry"].iloc[-1].date()
        if i + 1 < REVIEW_TRADES:
            limit = -KILL_PREMIUM_MULTIPLE * so_far["premium_rs"].median()
            if so_far["net_rs"].sum() < limit:
                verdict = "KILLED"
                verdict_note = (
                    f"cumulative loss Rs {so_far['net_rs'].sum():,.0f} per lot passed "
                    f"Rs {limit:,.0f} at trade {i + 1} ({last_expiry})"
                )
                break
            continue
        control = df[df["expiry"] <= so_far["expiry"].iloc[-1]]
        mean, t = mean_t(so_far["net_rs"])
        passed = mean > 0 and t >= PASS_T and mean > control["net_rs"].mean()
        verdict = "PASS" if passed else "KILLED"
        verdict_note = (
            f"at trade {REVIEW_TRADES} ({last_expiry}): rule mean Rs {mean:,.0f}/lot, t {t:.2f}, "
            f"control mean Rs {control['net_rs'].mean():,.0f}/lot over {len(control)} cycles"
        )
        break
    return {"all": df, "rule": rule, "verdict": verdict, "verdict_note": verdict_note}


def describe(label: str, df: pd.DataFrame) -> list[str]:
    lines = [f"{label}: {len(df)} trades"]
    if len(df):
        mean, t = mean_t(df["net_rs"])
        lines[0] += (
            f", mean after cost Rs {mean:,.0f} per lot, one-sided t {t:.2f}, "
            f"total Rs {df['net_rs'].sum():,.0f} per lot"
        )
        for name, part in df.groupby("book"):
            pts_mean, pts_t = mean_t(part["net_pts"])
            lines.append(
                f"  {name}: {len(part)} trades, mean {pts_mean:.1f} points (t {pts_t:.2f}), "
                f"mean Rs {part['net_rs'].mean():,.0f} per lot"
            )
    return lines


def write_status(
    path: Path, ledger: Ledger, scope: Scope, books: dict[str, dict[str, Any]], as_of: str
) -> None:
    state = ledger.state()
    s = summarize(state, scope)
    rule, control = s["rule"], s["all"]
    lines = [
        f"Forward paper tracker status, written {as_of}",
        f"Scope: expiries {scope[0].date()} to {scope[1].date()}",
        "",
    ]
    lines += describe(
        "Rule (fires only when the ratio is above the trailing 52-cycle 67th percentile)", rule
    )
    lines += describe("Control (every expiry, same days)", control)
    lines.append("")
    lines.append(
        f"Review point: {min(len(rule), REVIEW_TRADES)} of {REVIEW_TRADES} filtered trades, "
        f"{max(REVIEW_TRADES - len(rule), 0)} to go"
    )
    if len(rule):
        median_premium = rule["premium_rs"].median()
        limit = -KILL_PREMIUM_MULTIPLE * median_premium
        lines.append(
            f"Kill switch: cumulative rule result Rs {rule['net_rs'].sum():,.0f} per lot against "
            f"a limit of Rs {limit:,.0f} (3x median premium Rs {median_premium:,.0f})"
        )
    else:
        lines.append("Kill switch: no settled rule trades yet")
    lines.append(
        f"Verdict: {s['verdict']}" + (f", {s['verdict_note']}" if s["verdict_note"] else "")
    )
    lines.append("")
    for book, info in books.items():
        name = BOOKS[book]
        lines.append(f"{name}: data through {info['last_day'].date()}")
        open_trades = [
            k[1]
            for k, v in state.items()
            if k[0] == name
            and v["decided"]
            and not v["settled"]
            and not v["void"]
            and not v.get("skip")
        ]
        for expiry in sorted(open_trades):
            cur = state[(name, expiry)]
            action = "SOLD (paper)" if cur["fire"] == "1" else "no rule trade, control sold"
            lines.append(
                f"  Open: expiry {expiry}, entry {cur['entry_date']}, {action}, "
                f"strike {float(cur['strike']):.0f}, premium {float(cur['premium']):.2f}, "
                f"ratio {float(cur['ratio']):.4f} vs threshold {float(cur['thr67']):.4f}"
            )
        nxt = info["next_expiry"]
        if nxt is None:
            lines.append("  Next expiry: none listed")
        elif (name, fmt(nxt)) in state and state[(name, fmt(nxt))]["decided"]:
            lines.append(f"  Next expiry: {nxt.date()}, decision recorded (see open trades)")
        else:
            lines.append(
                f"  Next expiry: {nxt.date()}, signal pending until the close of the trading day before it"
            )
            p = info["provisional"]
            if p and p.get("ratio"):
                verb = "would fire" if p["fire"] == "1" else "would not fire"
                lines.append(
                    f"  Early reading on the {p['entry_date']} close, which is not the entry day, "
                    "so the premium still holds extra days and the ratio runs high; not the signal: "
                    f"ratio {float(p['ratio']):.4f} vs threshold {float(p['thr67']):.4f}, {verb}"
                )
    path.write_text("\n".join(lines) + "\n")


def fetch_missing(raw: Path, polite: Polite) -> None:
    fetch_indices(raw, Polite(warm_home=True))
    fo_dir, bse_dir = raw / "fo", raw / "bse_fo"
    fo_dir.mkdir(parents=True, exist_ok=True)
    bse_dir.mkdir(parents=True, exist_ok=True)
    for day in [d for d in trading_days(raw) if d > FORWARD_AFTER.date()]:
        if not has_fo_file(fo_dir, day) and fetch_fo_day(polite, fo_dir, day) is None:
            print("NSE F&O file not published yet", day, flush=True)
        if not (bse_dir / f"{day:%Y%m%d}.csv").exists():
            missing_status = fetch_bse_day(polite, bse_dir, day)
            if missing_status is not None:
                print("BSE F&O file not published yet", day, missing_status, flush=True)


def load_holidays(cache: Path, polite: Polite) -> set[pd.Timestamp]:
    cached = json.loads(cache.read_text()) if cache.exists() else {}
    if cached.get("fetched") != date.today().isoformat():
        response = polite.get(
            HOLIDAY_URL, headers={"Accept": "application/json", "Referer": HOLIDAY_REFERER}
        )
        try:
            days = sorted(
                {
                    datetime.strptime(h["tradingDate"], "%d-%b-%Y").date().isoformat()
                    for h in response.json()["FO"]
                }
            )
            merged = sorted(set(cached.get("days", [])) | set(days))
            cached = {"fetched": date.today().isoformat(), "days": merged}
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(cached, indent=1))
        except (ValueError, KeyError, TypeError) as error:
            print("holiday list unavailable, using cache and weekends only:", error, flush=True)
    return {pd.Timestamp(d) for d in cached.get("days", [])}


def new_nifty_day(path: Path, spot: pd.DataFrame) -> pd.DataFrame:
    df = finalize(read_nse_fo(path))
    df = df[(df["symbol"] == "NIFTY") & (df["instrument"] == "OPT")]
    spot_close = spot[["date", "close"]].rename(columns={"close": "underlying_close"})
    return df.merge(spot_close, on="date", how="left")[PANEL_COLS]


def new_sensex_day(path: Path) -> pd.DataFrame:
    return sensex_options(read_bse_fo(path))[PANEL_COLS]


def update_panel(book: str, paths: DataPaths, forward_dir: Path) -> pd.DataFrame:
    """Seeded once from the shared panel up to the forward start, then grown from new raw files."""
    panel_path = forward_dir / "panel" / f"{book}_options.parquet"
    if panel_path.exists():
        panel = pd.read_parquet(panel_path)
    else:
        shared = load_options(paths.panel, book)
        panel = shared[(shared["date"] >= PANEL_SEED_FROM) & (shared["date"] <= FORWARD_AFTER)]
    have = set(panel["date"].dt.date)
    reader: Callable[[Path], pd.DataFrame]
    if book == "nifty":
        spot = load_index(paths.raw, "NIFTY_50")
        files = {
            datetime.strptime(f.name[:8], "%Y%m%d").date(): f
            for f in sorted((paths.raw / "fo").glob("*.zip"))
        }
        reader = partial(new_nifty_day, spot=spot)
    else:
        files = {
            datetime.strptime(f.stem, "%Y%m%d").date(): f
            for f in sorted((paths.raw / "bse_fo").glob("*.csv"))
        }
        reader = new_sensex_day
    fresh = [
        reader(f) for d, f in sorted(files.items()) if d > FORWARD_AFTER.date() and d not in have
    ]
    if fresh:
        panel = (
            pd.concat([panel, *fresh], ignore_index=True)
            .sort_values(["date", "expiry", "strike", "type"])
            .reset_index(drop=True)
        )
    if fresh or not panel_path.exists():
        panel_path.parent.mkdir(parents=True, exist_ok=True)
        panel.to_parquet(panel_path, index=False)
    return panel


def run_forward(paths: DataPaths, forward_dir: Path) -> str:
    polite = Polite(warm_home=True)
    fetch_missing(paths.raw, polite)
    holidays = load_holidays(forward_dir / "holidays.json", polite)
    vix = load_index(paths.raw, "INDIA_VIX")
    ledger = Ledger(forward_dir / "ledger.csv", "forward")
    scope = (FORWARD_AFTER + pd.Timedelta(days=1), FORWARD_SCOPE_END)
    recorded_at = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    books = {}
    for book in BOOKS:
        panel = update_panel(book, paths, forward_dir)
        books[book] = step(book, panel, vix, holidays, ledger, recorded_at, scope)
    status_path = forward_dir / "status.txt"
    write_status(status_path, ledger, scope, books, recorded_at)
    return status_path.read_text()


def run_replay(
    paths: DataPaths,
    out: Path,
    lanes: dict[str, pd.DataFrame] | None = None,
    start: str = REPLAY_START,
    end: str = REPLAY_END,
) -> str:
    """Replay the tracker day by day over history and compare it with the backtest cycles."""
    out.mkdir(parents=True, exist_ok=True)
    for name in ("ledger.csv", "status.txt", "compare.txt"):
        (out / name).unlink(missing_ok=True)
    ledger = Ledger(out / "ledger.csv", "replay")
    scope = (pd.Timestamp(start), pd.Timestamp(end))
    vix = load_index(paths.raw, "INDIA_VIX")
    books = {}
    for book in BOOKS:
        full = load_options(paths.panel, book)
        first_day = scope[0] - LOOKBACK - pd.Timedelta(days=30)
        full = full[(full["date"] >= first_day) & (full["date"] <= scope[1])]
        days = sorted(full["date"].unique())
        weekdays = pd.bdate_range(days[0], days[-1])
        holidays = set(weekdays) - set(pd.DatetimeIndex(days))
        for day in [d for d in days if d >= scope[0] - pd.Timedelta(days=14)]:
            recorded_at = f"{pd.Timestamp(day).date()} (replayed)"
            books[book] = step(
                book, full[full["date"] <= day], vix, holidays, ledger, recorded_at, scope
            )
        print("replayed", book, flush=True)
    write_status(out / "status.txt", ledger, scope, books, f"replay of {start} to {end}")
    if lanes is None:
        lanes = {book: build_index_cycles(paths.panel, book)[0] for book in BOOKS}
    return compare(ledger, scope, out / "compare.txt", lanes)


def load_lanes(lane_dir: Path) -> dict[str, pd.DataFrame]:
    return {
        book: pd.read_csv(lane_dir / f"cycles_{book}_d1.csv", parse_dates=["expiry", "entry"])
        for book in BOOKS
    }


def close_enough(a: str, b: str) -> bool:
    if a == b:
        return True
    try:
        return abs(float(a) - float(b)) < 1e-6
    except ValueError:
        return False


def compare(ledger: Ledger, scope: Scope, path: Path, lanes: dict[str, pd.DataFrame]) -> str:
    state = ledger.state()
    first_decision: dict[tuple[str, str], dict[str, str]] = {}
    for row in ledger.rows:
        if row["event"] == "decision":
            first_decision.setdefault((row["book"], row["expiry"]), row)
    lines = []
    for book, name in BOOKS.items():
        lane = lanes[book]
        lane = lane[
            (lane["skip"].fillna("") == "")
            & lane[THRESHOLD].notna()
            & (lane["expiry"] >= scope[0])
            & (lane["expiry"] <= scope[1])
        ]
        lane_net = lane["gross_pts"] - lane["cost1_pts"]
        mismatches, early_mismatches, corrected = [], [], 0
        for (_, r), net in zip(lane.iterrows(), lane_net, strict=True):
            key = (name, fmt(r["expiry"]))
            cur = state.get(key)
            lane_fire = "1" if r["ratio"] > r[THRESHOLD] else "0"
            if cur is None or not cur["settled"]:
                mismatches.append(f"{key[1]} missing from replay ledger")
                continue
            checks = {
                "entry_date": (cur["entry_date"], fmt(r["entry"])),
                "fire": (cur["fire"], lane_fire),
                "strike": (cur["strike"], fmt(r["strike"])),
                "premium": (cur["premium"], fmt(r["premium"])),
                "net_pts": (cur["net_pts"], fmt(net)),
            }
            bad = [
                f"{k} replay={a} lane={b}" for k, (a, b) in checks.items() if not close_enough(a, b)
            ]
            if bad:
                mismatches.append(f"{key[1]}: " + "; ".join(bad))
            first = first_decision.get(key)
            if first and (first["fire"] != lane_fire or first["entry_date"] != fmt(r["entry"])):
                early_mismatches.append(
                    f"{key[1]}: first decision fire={first['fire']} entry={first['entry_date']}, "
                    f"lane fire={lane_fire} entry={fmt(r['entry'])}"
                )
            if any(
                x["event"] == "correction" and (x["book"], x["expiry"]) == key for x in ledger.rows
            ):
                corrected += 1
        lane_expiries = set(lane["expiry"])
        extra = [
            k
            for k, v in state.items()
            if k[0] == name
            and v["settled"]
            and not v.get("skip")
            and pd.Timestamp(k[1]) not in lane_expiries
        ]
        fires = int((lane["ratio"] > lane[THRESHOLD]).sum())
        lines.append(
            f"{name}: {len(lane)} lane cycles in scope ({fires} rule trades), "
            f"{len(lane) - len(mismatches)} match on entry, fire, strike, premium and net, "
            f"{len(mismatches)} mismatch, {len(extra)} settled replay cycles absent from the lane, "
            f"{corrected} corrected after the first decision, "
            f"{len(early_mismatches)} whose first decision differed from the final one"
        )
        lines += [f"  MISMATCH {m}" for m in mismatches]
        lines += [f"  EXTRA {k[1]}" for k in extra]
        lines += [f"  FIRST-DECISION {m}" for m in early_mismatches]
    path.write_text("\n".join(lines) + "\n")
    return path.read_text()
