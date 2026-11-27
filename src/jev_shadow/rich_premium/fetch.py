"""Download free NSE/BSE end-of-day files: index history, India VIX, F&O bhavcopies."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path

from .session import Polite

HISTORY_START = date(2012, 1, 1)
UDIFF_FROM = date(2024, 7, 8)
BSE_FROM = date(2024, 1, 1)
NSE_ARCHIVE = "https://nsearchives.nseindia.com"
NSE_INDEX_REFERER = "https://www.nseindia.com/reports-indices-historical-index-data"
BSE_FO_URL = (
    "https://www.bseindia.com/download/BhavCopy/Derivative/"
    "BhavCopy_BSE_FO_0_0_0_{d:%Y%m%d}_F_0000.CSV"
)
INDEX_SERIES = {
    "NIFTY_50": ("indicesHistory", "indexType=NIFTY%2050"),
    "NIFTY_BANK": ("indicesHistory", "indexType=NIFTY%20BANK"),
    "INDIA_VIX": ("vixhistory", None),
}


def index_chunks(today: date) -> Iterator[tuple[date, date]]:
    """Calendar quarters from 2012 so cached chunk file names never change."""
    day = HISTORY_START
    while day <= today:
        quarter_end = date(day.year + (day.month + 2) // 12, (day.month + 2) % 12 + 1, 1)
        end = min(quarter_end - timedelta(days=1), today)
        yield day, end
        day = end + timedelta(days=1)


def fetch_indices(raw: Path, polite: Polite, start: date = HISTORY_START) -> None:
    today = date.today()
    for name, (endpoint, query) in INDEX_SERIES.items():
        out = raw / "indices" / name
        out.mkdir(parents=True, exist_ok=True)
        for first, last in index_chunks(today):
            path = out / f"{first:%Y%m%d}_{last:%Y%m%d}.json"
            if last < start or (path.exists() and last < today):
                continue
            params = f"from={first:%d-%m-%Y}&to={last:%d-%m-%Y}" + (f"&{query}" if query else "")
            response = polite.get(
                f"https://www.nseindia.com/api/historicalOR/{endpoint}?{params}",
                headers={"Accept": "application/json", "Referer": NSE_INDEX_REFERER},
            )
            try:
                data = response.json()["data"]
            except (ValueError, KeyError, TypeError):
                print(
                    "FAIL", name, first, last, response.status_code, response.text[:200], flush=True
                )
                continue
            path.write_text(json.dumps(data))
            print(name, first, last, len(data), flush=True)


def trading_days(raw: Path) -> list[date]:
    """NSE trading days, taken from the downloaded NIFTY 50 history."""
    days: set[date] = set()
    for path in (raw / "indices" / "NIFTY_50").glob("*.json"):
        for row in json.loads(path.read_text()):
            days.add(datetime.strptime(row["EOD_TIMESTAMP"], "%d-%b-%Y").date())
    return sorted(d for d in days if d >= HISTORY_START)


def fo_urls(day: date) -> list[tuple[str, str]]:
    month = day.strftime("%b").upper()
    old = (
        f"{NSE_ARCHIVE}/content/historical/DERIVATIVES/{day.year}/{month}/"
        f"fo{day:%d}{month}{day.year}bhav.csv.zip"
    )
    udiff = f"{NSE_ARCHIVE}/content/fo/BhavCopy_NSE_FO_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"
    pairs = [("old", old), ("udiff", udiff)]
    return pairs if day < UDIFF_FROM else pairs[::-1]


def fetch_fo_day(polite: Polite, fo_dir: Path, day: date) -> str | None:
    """Save the NSE F&O bhavcopy zip for one day; return the URL that served it."""
    for kind, url in fo_urls(day):
        response = polite.get(url)
        if response.status_code == 200 and response.content[:2] == b"PK":
            (fo_dir / f"{day:%Y%m%d}_{kind}.zip").write_bytes(response.content)
            return url
    return None


def fetch_bse_day(polite: Polite, bse_dir: Path, day: date) -> int | None:
    """Save the BSE F&O bhavcopy for one day; return the HTTP status when it is missing."""
    response = polite.get(
        BSE_FO_URL.format(d=day), headers={"Referer": "https://www.bseindia.com/"}
    )
    if response.status_code == 200 and response.content.startswith(b"TradDt"):
        (bse_dir / f"{day:%Y%m%d}.csv").write_bytes(response.content)
        return None
    return response.status_code


def has_fo_file(fo_dir: Path, day: date) -> bool:
    return any(fo_dir.glob(f"{day:%Y%m%d}_*.zip"))


def _load_status(path: Path) -> dict[tuple[str, str], str]:
    if not path.exists():
        return {}
    with path.open() as stream:
        return {(r["date"], r["kind"]): r["status"] for r in csv.DictReader(stream)}


def _is_participant_oi(content: bytes) -> bool:
    head = content[:800].upper()
    return b"CLIENT_TYPE" in head.replace(b" ", b"_") or b"\nCLIENT," in head.replace(b"\r", b"")


def fetch_nse_daily(raw: Path, polite: Polite, start: date, retry_poi: bool = False) -> None:
    """Fetch F&O bhavcopies and participant OI, remembering days the archive does not have."""
    status_path = raw / "_fetch_status.csv"
    status = _load_status(status_path)
    new_file = not status_path.exists()
    fo_dir, poi_dir = raw / "fo", raw / "participant_oi"
    fo_dir.mkdir(parents=True, exist_ok=True)
    poi_dir.mkdir(parents=True, exist_ok=True)
    days = [d for d in trading_days(raw) if d >= start]
    if not days:
        print("no trading days on or after", start, flush=True)
        return
    print("trading days", len(days), days[0], days[-1], flush=True)
    with status_path.open("a", newline="") as log:
        writer = csv.DictWriter(log, fieldnames=["date", "kind", "status", "url"])
        if new_file:
            writer.writeheader()
        for day in days:
            iso = day.isoformat()
            if not has_fo_file(fo_dir, day) and status.get((iso, "fo")) != "missing":
                got = fetch_fo_day(polite, fo_dir, day)
                ok = "ok" if got else "missing"
                writer.writerow({"date": iso, "kind": "fo", "status": ok, "url": got or ""})
                log.flush()
                print(iso, "fo", "ok" if got else "MISSING", flush=True)
            poi_path = poi_dir / f"{day:%Y%m%d}.csv"
            if not poi_path.exists() and (status.get((iso, "poi")) != "missing" or retry_poi):
                url = f"{NSE_ARCHIVE}/content/nsccl/fao_participant_oi_{day:%d%m%Y}.csv"
                response = polite.get(url)
                ok = response.status_code == 200 and _is_participant_oi(response.content)
                if ok:
                    poi_path.write_bytes(response.content)
                writer.writerow(
                    {"date": iso, "kind": "poi", "status": "ok" if ok else "missing", "url": url}
                )
                log.flush()
                if not ok:
                    print(iso, "poi MISSING", response.status_code, flush=True)


def fetch_bse_fo(raw: Path, polite: Polite, start: date) -> None:
    out = raw / "bse_fo"
    out.mkdir(parents=True, exist_ok=True)
    missing_log = out / "_missing.txt"
    known_missing = set(missing_log.read_text().split()) if missing_log.exists() else set()
    for day in trading_days(raw):
        path = out / f"{day:%Y%m%d}.csv"
        if day < max(BSE_FROM, start) or path.exists() or day.isoformat() in known_missing:
            continue
        missing_status = fetch_bse_day(polite, out, day)
        if missing_status is not None:
            with missing_log.open("a") as stream:
                stream.write(day.isoformat() + "\n")
            print("MISSING", day, missing_status, flush=True)


def fetch_all(raw: Path, start: date = HISTORY_START, retry_poi: bool = False) -> None:
    fetch_indices(raw, Polite(warm_home=True), start)
    fetch_nse_daily(raw, Polite(), start, retry_poi)
    fetch_bse_fo(raw, Polite(), start)
