"""Normalize raw bhavcopies into per-index daily option, futures and index panels."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .paths import DataPaths

NSE_SYMBOLS = ("NIFTY", "BANKNIFTY")
OLD_FORMAT_LOTS = {"NIFTY": np.array([25, 50, 75]), "BANKNIFTY": np.array([15, 20, 25, 30, 40])}
UDIFF_MAP = {
    "TradDt": "date",
    "TckrSymb": "symbol",
    "FininstrmActlXpryDt": "expiry",
    "XpryDt": "expiry_nominal",
    "StrkPric": "strike",
    "OptnTp": "type",
    "OpnPric": "open",
    "HghPric": "high",
    "LwPric": "low",
    "ClsPric": "close_published",
    "LastPric": "last",
    "SttlmPric": "settle",
    "OpnIntrst": "oi_units",
    "ChngInOpnIntrst": "chg_oi_units",
    "TtlTradgVol": "volume_units",
    "TtlTrfVal": "turnover_rs",
    "TtlNbOfTxsExctd": "num_trades",
    "UndrlygPric": "underlying_in_file",
    "NewBrdLotQty": "lot_size",
    "FinInstrmTp": "instrument",
}
OPT_COLS = [
    "date", "expiry", "strike", "type", "open", "high", "low", "close", "settle", "contracts",
    "oi_contracts", "chg_oi_contracts", "underlying_close", "traded", "close_published", "last",
    "oi_units", "chg_oi_units", "lot_size", "turnover_rs", "dte_calendar", "expiry_nominal",
    "num_trades", "underlying_in_file", "source_format",
]  # fmt: skip
FUT_COLS = [c for c in OPT_COLS if c not in ("strike", "type")]
PARTICIPANTS = ("CLIENT", "FII", "DII", "PRO", "TOTAL")
POI_COLUMNS = [
    "CLIENT_TYPE", "Future Index Long", "Future Index Short", "Future Stock Long",
    "Future Stock Short", "Option Index Call Long", "Option Index Put Long",
    "Option Index Call Short", "Option Index Put Short", "Option Stock Call Long",
    "Option Stock Put Long", "Option Stock Call Short", "Option Stock Put Short",
    "Total Long Contracts", "Total Short Contracts",
]  # fmt: skip


def parse_dmy(values: pd.Series) -> pd.Series:
    values = values.astype(str).str.strip()
    long = pd.to_datetime(values, format="%d-%b-%Y", errors="coerce")
    return long.fillna(pd.to_datetime(values, format="%d-%b-%y", errors="coerce"))


def read_old(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as archive:
        df = pd.read_csv(io.BytesIO(archive.read(archive.namelist()[0])))
    df.columns = [c.strip() for c in df.columns]
    df = df[df["SYMBOL"].isin(NSE_SYMBOLS) & df["INSTRUMENT"].isin(["FUTIDX", "OPTIDX"])].copy()
    out = pd.DataFrame(
        {
            "date": parse_dmy(df["TIMESTAMP"]),
            "symbol": df["SYMBOL"],
            "instrument": df["INSTRUMENT"].map({"FUTIDX": "FUT", "OPTIDX": "OPT"}),
            "expiry": parse_dmy(df["EXPIRY_DT"]),
            "strike": df["STRIKE_PR"].astype(float),
            "type": df["OPTION_TYP"].replace("XX", None),
            "open": df["OPEN"],
            "high": df["HIGH"],
            "low": df["LOW"],
            "close_published": df["CLOSE"],
            "settle": df["SETTLE_PR"],
            "contracts": df["CONTRACTS"].astype("int64"),
            "turnover_rs": df["VAL_INLAKH"] * 1e5,
            "oi_units": df["OPEN_INT"].astype("int64"),
            "chg_oi_units": df["CHG_IN_OI"].astype("int64"),
            "source_format": "old",
        }
    )
    out["lot_size"] = implied_lot(out)
    return out


def implied_lot(df: pd.DataFrame) -> pd.Series:
    """Old files carry no lot size; infer it from turnover / (contracts x price)."""
    traded = df[df["contracts"] > 0].copy()
    mid = (traded["high"] + traded["low"]) / 2
    traded["notional_units"] = traded["contracts"] * np.where(
        traded["instrument"] == "FUT", mid, traded["strike"] + mid
    )
    agg = traded.groupby(["symbol", "expiry"])[["turnover_rs", "notional_units"]].sum()
    est = (agg["turnover_rs"] / agg["notional_units"]).rename("lot_est").reset_index()
    out = df[["symbol", "expiry"]].merge(est, on=["symbol", "expiry"], how="left")["lot_est"]
    return pd.Series(out.to_numpy(), index=df.index)


def window_mode(window: np.ndarray) -> float:
    window = window[~np.isnan(window)]
    if len(window) == 0:
        return np.nan
    vals, counts = np.unique(window, return_counts=True)
    if counts.max() > len(window) / 2 or len(window) < 3:
        return vals[counts.argmax()]
    return window[len(window) // 2]


def _snap(lots: pd.Series, allfo: pd.DataFrame, old: pd.Series) -> None:
    for symbol, valid in OLD_FORMAT_LOTS.items():
        mask = old & (allfo["symbol"] == symbol) & lots.notna()
        nearest = np.abs(lots[mask].to_numpy()[:, None] - valid[None, :]).argmin(axis=1)
        lots[mask] = valid[nearest]


def snap_old_lots(allfo: pd.DataFrame) -> pd.Series:
    old = allfo["source_format"] == "old"
    lots = allfo["lot_size"].copy()
    _snap(lots, allfo, old)
    key = allfo.loc[old, ["symbol", "expiry", "date"]].assign(lot=lots[old])
    daily = key.drop_duplicates(["symbol", "expiry", "date"]).sort_values(
        ["symbol", "expiry", "date"]
    )
    daily["lot_smooth"] = daily.groupby(["symbol", "expiry"])["lot"].transform(
        lambda s: s.rolling(5, center=True, min_periods=1).apply(window_mode, raw=True)
    )
    day_mode = (
        daily.dropna(subset=["lot_smooth"])
        .groupby(["symbol", "date"])["lot_smooth"]
        .agg(lambda s: s.mode().iloc[0])
    )
    fill = daily.set_index(["symbol", "date"]).index.map(day_mode.get)
    daily["lot_smooth"] = daily["lot_smooth"].fillna(
        pd.Series(np.asarray(fill, dtype=float), index=daily.index)
    )
    snapped = key.merge(
        daily[["symbol", "expiry", "date", "lot_smooth"]],
        on=["symbol", "expiry", "date"],
        how="left",
    )["lot_smooth"]
    lots[old] = snapped.to_numpy()
    _snap(lots, allfo, old)
    return lots


def read_market_activity(path: Path) -> pd.DataFrame:
    stamp = path.stem
    day = datetime.strptime(stamp, "%Y%m%d").strftime("%d%m%Y")
    with zipfile.ZipFile(path) as archive:
        names = {n.lower(): n for n in archive.namelist()}
        options = pd.read_csv(io.BytesIO(archive.read(names[f"op{day}.csv"])), dtype=str)
        futures = pd.read_csv(io.BytesIO(archive.read(names[f"fo{day}.csv"])), dtype=str)
    frames = []
    for df, inst in ((options, "OPT"), (futures, "FUT")):
        df.columns = [c.strip().rstrip("*") for c in df.columns]
        df = df.apply(lambda c: c.str.strip())
        df = df[df["SYMBOL"].isin(NSE_SYMBOLS) & df["INSTRUMENT"].isin(["FUTIDX", "OPTIDX"])]

        def num(column: str, frame: pd.DataFrame = df) -> pd.Series:
            return pd.to_numeric(frame[column], errors="coerce")

        out = pd.DataFrame(
            {
                "date": pd.Timestamp(datetime.strptime(stamp, "%Y%m%d")),
                "symbol": df["SYMBOL"],
                "instrument": inst,
                "expiry": pd.to_datetime(df["EXP_DATE"], format="%d/%m/%Y"),
                "strike": num("STR_PRICE") if inst == "OPT" else 0.0,
                "type": df["OPT_TYPE"] if inst == "OPT" else None,
                "open": num("OPEN_PRICE"),
                "high": num("HI_PRICE"),
                "low": num("LO_PRICE"),
                "close_published": num("CLOSE_PRICE"),
                "settle": np.nan,
                "contracts": num("NO_OF_CONT").astype("int64"),
                "oi_units": num("OPEN_INT").astype("int64"),
                "chg_oi_units": np.nan,
                "num_trades": num("NO_OF_TRADE"),
                "turnover_rs": num("NOTION_VAL") if inst == "OPT" else num("TRD_VAL"),
                "source_format": "market_activity",
            }
        )
        out["lot_size"] = (num("TRD_QTY") / num("NO_OF_CONT")).round()
        frames.append(out)
    out = pd.concat(frames, ignore_index=True)
    out["lot_size"] = out.groupby(["symbol", "expiry"])["lot_size"].transform("median")
    return out


def read_udiff(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as archive:
        df = pd.read_csv(io.BytesIO(archive.read(archive.namelist()[0])))
    return normalize_udiff(df, NSE_SYMBOLS)


def read_nse_fo(path: Path) -> pd.DataFrame:
    return read_old(path) if path.stem.endswith("_old") else read_udiff(path)


def normalize_udiff(df: pd.DataFrame, symbols: Iterable[str]) -> pd.DataFrame:
    df = df[df["TckrSymb"].isin(list(symbols)) & df["FinInstrmTp"].isin(["IDF", "IDO"])]
    out = df[list(UDIFF_MAP)].rename(columns=UDIFF_MAP)
    out["instrument"] = out["instrument"].map({"IDF": "FUT", "IDO": "OPT"})
    for column in ("date", "expiry", "expiry_nominal"):
        out[column] = pd.to_datetime(out[column])
    out["type"] = out["type"].where(out["instrument"] == "OPT", None)
    out["strike"] = out["strike"].fillna(0).astype(float)
    out["contracts"] = (out["volume_units"] / out["lot_size"]).round().astype("int64")
    out["source_format"] = "udiff"
    return out


def read_bse_fo(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    return normalize_udiff(df.rename(columns={"Rsvd01": "Rsvd1"}), ("SENSEX",))


def load_index(raw: Path, name: str) -> pd.DataFrame:
    rows = []
    for path in sorted((raw / "indices" / name).glob("*.json")):
        rows += json.loads(path.read_text())
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["EOD_TIMESTAMP"], format="%d-%b-%Y")
    out = df.rename(
        columns={
            "EOD_OPEN_INDEX_VAL": "open",
            "EOD_HIGH_INDEX_VAL": "high",
            "EOD_LOW_INDEX_VAL": "low",
            "EOD_CLOSE_INDEX_VAL": "close",
        }
    )
    columns = ["date", "open", "high", "low", "close"]
    if name == "INDIA_VIX":
        out = out.rename(columns={"EOD_PREV_CLOSE": "prev_close"})
        columns.append("prev_close")
    out = out[columns].drop_duplicates("date").sort_values("date").reset_index(drop=True)
    for column in columns[1:]:
        out[column] = pd.to_numeric(out[column], errors="coerce").replace(0, np.nan)
    return out


def finalize(df: pd.DataFrame) -> pd.DataFrame:
    """Blank prices on untraded rows and derive contract-denominated OI and calendar DTE."""
    df = df.copy()
    df["traded"] = df["contracts"] > 0
    for column in ("open", "high", "low"):
        df[column] = df[column].where(df["traded"])
    df["close"] = df["close_published"].where(df["traded"])
    df["oi_contracts"] = (df["oi_units"] / df["lot_size"]).round()
    df["chg_oi_contracts"] = (df["chg_oi_units"] / df["lot_size"]).round()
    df["dte_calendar"] = (df["expiry"] - df["date"]).dt.days
    return df


def build_panels(paths: DataPaths) -> None:
    raw, root = paths.raw, paths.panel
    root.mkdir(parents=True, exist_ok=True)
    frames = []
    files = sorted((raw / "fo").glob("*.zip"))
    for i, path in enumerate(files):
        frames.append(read_nse_fo(path))
        if i % 250 == 0:
            print("read", i, path.name, flush=True)
    have = {path.name[:8] for path in files}
    for path in sorted((raw / "fo_mkt").glob("*.zip")):
        if path.stem not in have:
            frames.append(read_market_activity(path))
    allfo = pd.concat(frames, ignore_index=True)
    allfo["chg_oi_units"] = pd.to_numeric(allfo["chg_oi_units"], errors="coerce")
    allfo["lot_size"] = snap_old_lots(allfo)
    allfo = allfo.drop_duplicates(
        ["date", "symbol", "instrument", "expiry", "strike", "type"], keep="last"
    )
    if "expiry_nominal" in allfo:
        allfo["expiry_nominal"] = allfo["expiry_nominal"].fillna(allfo["expiry"])
    else:
        allfo["expiry_nominal"] = allfo["expiry"]

    nifty = load_index(raw, "NIFTY_50")
    bank = load_index(raw, "NIFTY_BANK")
    vix = load_index(raw, "INDIA_VIX")
    nifty.to_parquet(root / "nifty50_spot_daily.parquet", index=False)
    bank.to_parquet(root / "niftybank_spot_daily.parquet", index=False)
    vix.to_parquet(root / "india_vix_daily.parquet", index=False)

    for symbol, spot, stem in (("NIFTY", nifty, "nifty"), ("BANKNIFTY", bank, "banknifty")):
        sub = finalize(allfo[allfo["symbol"] == symbol])
        sub = sub.merge(
            spot[["date", "close"]].rename(columns={"close": "underlying_close"}),
            on="date",
            how="left",
        )
        opt = (
            sub[sub["instrument"] == "OPT"]
            .reindex(columns=OPT_COLS)
            .sort_values(["date", "expiry", "strike", "type"])
        )
        fut = (
            sub[sub["instrument"] == "FUT"]
            .reindex(columns=FUT_COLS)
            .sort_values(["date", "expiry"])
        )
        opt.to_parquet(root / f"{stem}_options_daily.parquet", index=False)
        fut.to_parquet(root / f"{stem}_futures_daily.parquet", index=False)
        print(stem, "options", len(opt), "futures", len(fut), flush=True)

    build_sensex(raw, root)
    build_participant_oi(raw, root)


def sensex_options(frame: pd.DataFrame) -> pd.DataFrame:
    """BSE files carry the index close per row, used as the underlying close."""
    sx = finalize(frame)
    sx["underlying_close"] = sx["underlying_in_file"]
    return sx[sx["instrument"] == "OPT"]


def build_sensex(raw: Path, root: Path) -> None:
    files = sorted((raw / "bse_fo").glob("*.csv"))
    if not files:
        return
    sx = sensex_options(pd.concat([read_bse_fo(path) for path in files], ignore_index=True))
    opt = sx.reindex(columns=OPT_COLS).sort_values(["date", "expiry", "strike", "type"])
    opt.to_parquet(root / "sensex_options_daily.parquet", index=False)
    print("sensex options", len(opt), flush=True)


def _read_participant_oi(path: Path) -> pd.DataFrame:
    lines = path.read_text(errors="replace").splitlines()
    header = next(
        (
            i
            for i, line in enumerate(lines)
            if line.upper().replace(" ", "_").startswith("CLIENT_TYPE")
        ),
        None,
    )
    if header is None:
        body = [
            line for line in lines if line.split(",")[0].strip().strip('"').upper() in PARTICIPANTS
        ]
        df = pd.read_csv(io.StringIO("\n".join(body)), header=None, names=POI_COLUMNS, dtype=str)
    else:
        df = pd.read_csv(io.StringIO("\n".join(lines[header:])), thousands=",", dtype=str)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    df = df.dropna(how="all", axis=1)
    df["client_type"] = df["client_type"].astype(str).str.strip().str.upper()
    for column in df.columns[1:]:
        df[column] = pd.to_numeric(df[column].str.replace(",", "").str.strip(), errors="coerce")
    df.insert(0, "date", datetime.strptime(path.stem, "%Y%m%d"))
    return df


def build_participant_oi(raw: Path, root: Path) -> None:
    rows = [_read_participant_oi(path) for path in sorted((raw / "participant_oi").glob("*.csv"))]
    if rows:
        poi = pd.concat(rows, ignore_index=True)
        poi = poi[poi["client_type"].isin(PARTICIPANTS)]
        poi.to_parquet(root / "participant_oi_daily.parquet", index=False)
        print("participant oi rows", len(poi), flush=True)
