"""Build point-in-time Jev contexts from NiftyZoning candidate artifacts."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timedelta
from datetime import time as dt_time
from datetime import timezone as dt_timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from zoneinfo import ZoneInfo

_POINT_IN_TIME_FIELDS = (
    "candidate_id", "date", "time", "session", "cell", "direction", "eligibility",
    "score", "score_factors", "candidate_intent", "entry_price", "stop_basis",
    "stop_distance", "requested_quantity", "effective_quantity", "instrument",
    "required_inputs", "sizing_diagnostics", "causal_features", "feature_provenance",
)
def _records(payload: object) -> list[Mapping[str, Any]]:
    if isinstance(payload, Mapping) and isinstance(payload.get("records"), list):
        payload = payload["records"]
    if not isinstance(payload, list):
        raise TypeError("candidate artifact must contain a records array")
    records: list[Mapping[str, Any]] = []
    for index, record in enumerate(payload):
        if not isinstance(record, Mapping):
            raise TypeError(f"candidate record {index} must be a JSON object")
        records.append(record)
    return records


def context_from_candidate(record: Mapping[str, Any]) -> dict[str, Any]:
    """Project one candidate record without copying post-decision outcomes."""
    candidate_id = str(record.get("candidate_id") or "")
    if not candidate_id:
        raise ValueError("candidate record is missing candidate_id")
    return {
        field: record[field]
        for field in _POINT_IN_TIME_FIELDS
        if field in record and record[field] is not None
    }


def write_contexts(input_path: Path, output_path: Path) -> int:
    """Convert a NiftyZoning candidate-trace artifact to appendable JSONL."""
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    contexts = [context_from_candidate(record) for record in _records(payload)]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as stream:
        for context in contexts:
            stream.write(json.dumps(context, sort_keys=True, separators=(",", ":")) + "\n")
    return len(contexts)


def orderflow_contexts(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Build contexts at oracle signal times without copying oracle labels."""
    target_date = str(payload.get("date") or "")
    contract = payload.get("contract_identity")
    if not target_date or not isinstance(contract, list) or not contract:
        raise ValueError("orderflow trace is missing date or contract identity")
    timeframe_sections = payload.get("timeframes") or {}
    if len(timeframe_sections) > 1 and isinstance(timeframe_sections, Mapping):
        return _multitimeframe_contexts(payload, timeframe_sections, target_date, contract)
    contexts: list[dict[str, Any]] = []
    for timeframe, section in timeframe_sections.items():
        if not isinstance(section, Mapping):
            continue
        traces = section.get("trace")
        opportunities = section.get("oracle_trades")
        if not isinstance(traces, list) or not isinstance(opportunities, list):
            continue
        by_time = {
            str(item.get("time")): item for item in traces if isinstance(item, Mapping)
        }
        for ordinal, opportunity in enumerate(opportunities, 1):
            if not isinstance(opportunity, Mapping):
                raise TypeError("oracle opportunity must be a JSON object")
            signal_time = str(opportunity.get("signal_time") or "")
            trace = by_time.get(signal_time)
            if not signal_time or trace is None:
                raise ValueError(f"no causal trace found for oracle signal {signal_time!r}")
            context: dict[str, Any] = {
                "candidate_id": f"orderflow-{target_date}-{timeframe}-{signal_time}-{ordinal}",
                "date": target_date,
                "time": signal_time,
                "instrument": "_".join(str(part) for part in contract if part),
                "timeframe": timeframe,
                "market": {
                    "bar": trace.get("bar"),
                    "candle_features": trace.get("candle_features"),
                    "volume_multiple_vs_prior_20": trace.get("volume_multiple_vs_prior_20"),
                    "volume_rank_vs_prior_20": trace.get("volume_rank_vs_prior_20"),
                    "volume_percentile_vs_prior_20": trace.get("volume_percentile_vs_prior_20"),
                    "range": trace.get("range"),
                    "body": trace.get("body"),
                    "clv": trace.get("clv"),
                    "delta_proxy": trace.get("delta_proxy"),
                    "volume_multiple_vs_prior_5": trace.get("volume_multiple_vs_prior_5"),
                    "range_multiple_vs_prior_5": trace.get("range_multiple_vs_prior_5"),
                    "oi_change_vs_prior_bar": trace.get("oi_change_vs_prior_bar"),
                    "target_range": trace.get("target_range"),
                    "target_clv": trace.get("target_clv"),
                    "target_delta_proxy": trace.get("target_delta_proxy"),
                    "cumulative_delta_proxy": trace.get("cumulative_delta_proxy"),
                },
                "causal_features": trace.get("causal_features"),
                "causal_locations": trace.get("causal_locations"),
                "source": {
                    "artifact_type": "orderflow_full_day_trace",
                    "trace_scope": f"complete bars available through {signal_time}",
                    "feature_window": "09:15 <= bar time <= signal time",
                    "volume_rank_scope": "prior 20 bars only",
                },
            }
            contexts.append(context)
    return contexts


def _as_datetime(value: object, date: str, timezone: str) -> datetime | None:
    text = str(value or "")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.combine(datetime.fromisoformat(date).date(), dt_time.fromisoformat(text))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        try:
            tz = ZoneInfo(timezone)
        except Exception:  # noqa: BLE001 - systems without tzdata still need deterministic offsets.
            offsets = {"UTC": timedelta(0), "Asia/Kolkata": timedelta(hours=5, minutes=30)}
            tz = dt_timezone(offsets.get(timezone, timedelta(0)), name=timezone)
        parsed = parsed.replace(tzinfo=tz)
    return parsed


def _trace_datetime(trace: Mapping[str, Any], date: str, timezone: str) -> datetime | None:
    bar = trace.get("bar")
    value = trace.get("bar_close_time") or trace.get("as_of")
    if value is None and isinstance(bar, Mapping):
        value = bar.get("Time") or bar.get("time")
    return _as_datetime(value or trace.get("time"), date, timezone)


def _timeframe_view(
    timeframe: str,
    trace: Mapping[str, Any] | None,
    decision_time: datetime,
    date: str,
    timezone: str,
) -> dict[str, Any]:
    if trace is None:
        return {
            "as_of": None,
            "bar_status": "missing",
            "market": {},
            "features": {},
            "causal_locations": {},
            "source": {"status": "missing", "timeframe": timeframe, "timezone": timezone},
        }
    close = _trace_datetime(trace, date, timezone)
    partial = bool(trace.get("partial_bar") or trace.get("bar_status") == "partial")
    status = str(trace.get("source_status") or "ok")
    if trace.get("stale") or close is None or (close < decision_time and timeframe == "1m"):
        status = "stale"
    if partial or (close is not None and close > decision_time):
        status = "partial"
    market = {
        key: trace.get(key)
        for key in (
            "bar", "candle_features", "volume_multiple_vs_prior_20", "volume_rank_vs_prior_20",
            "volume_percentile_vs_prior_20", "range", "body", "clv", "delta_proxy",
            "volume_multiple_vs_prior_5", "range_multiple_vs_prior_5", "oi_change_vs_prior_bar",
            "target_range", "target_clv", "target_delta_proxy", "cumulative_delta_proxy",
        )
        if key in trace
    }
    market = {key: deepcopy(value) for key, value in market.items()}
    causal_features = trace.get("causal_features") or {}
    return {
        "as_of": close.isoformat() if close else None,
        "bar_status": "closed" if status == "ok" else status,
        "market": market,
        "features": deepcopy(causal_features),
        "causal_locations": deepcopy(trace.get("causal_locations") or {}),
        "source": {
            "status": status,
            "timeframe": timeframe,
            "bar_close_time": close.isoformat() if close else None,
            "timezone": timezone,
            "trace_scope": f"complete bars available through {close.isoformat()}" if close else None,
            "feature_window": trace.get("feature_window", f"through {close.isoformat()}" if close else None),
            "lookback_scope": trace.get("lookback_scope") or causal_features.get("causal_n_bars"),
        },
    }


def _multitimeframe_contexts(
    payload: Mapping[str, Any], sections: Mapping[str, Any], date: str, contract: list[Any],
) -> list[dict[str, Any]]:
    timezone = str(payload.get("timezone") or "Asia/Kolkata")
    primary = str(payload.get("primary_timeframe") or ("1m" if "1m" in sections else next(iter(sections))))
    primary_section = sections.get(primary)
    if not isinstance(primary_section, Mapping) or not isinstance(primary_section.get("oracle_trades"), list):
        raise TypeError(f"primary timeframe {primary!r} has no oracle trades")
    traces_by_time: dict[str, list[tuple[datetime, Mapping[str, Any]]]] = {}
    for timeframe, section in sections.items():
        if not isinstance(section, Mapping) or not isinstance(section.get("trace"), list):
            traces_by_time[str(timeframe)] = []
            continue
        entries = []
        for trace in section["trace"]:
            if isinstance(trace, Mapping):
                stamp = _trace_datetime(trace, date, timezone)
                if stamp is not None:
                    entries.append((stamp, trace))
        traces_by_time[str(timeframe)] = sorted(entries, key=lambda item: item[0])
    contexts: list[dict[str, Any]] = []
    for ordinal, opportunity in enumerate(primary_section["oracle_trades"], 1):
        if not isinstance(opportunity, Mapping):
            raise TypeError("oracle opportunity must be a JSON object")
        signal = str(opportunity.get("signal_time") or "")
        decision = _as_datetime(signal, date, timezone)
        if not signal or decision is None:
            raise ValueError(f"invalid primary signal time {signal!r}")
        selected: dict[str, dict[str, Any]] = {}
        for timeframe in sections:
            candidates = [item for item in traces_by_time.get(str(timeframe), []) if item[0] <= decision]
            selected_trace = candidates[-1][1] if candidates else None
            selected[str(timeframe)] = _timeframe_view(str(timeframe), selected_trace, decision, date, timezone)
        primary_view = selected[primary]
        context: dict[str, Any] = {
            "candidate_id": f"orderflow-{date}-{primary}-{signal}-{ordinal}",
            "date": date,
            "time": signal,
            "instrument": "_".join(str(part) for part in contract if part),
            "timeframe": primary,
            "context_schema": "jev-shadow.context.v2",
            "decision_time": decision.isoformat(),
            "primary_timeframe": primary,
            "timeframes": selected,
            "market": primary_view["market"],
            "causal_features": primary_view["features"],
            "causal_locations": primary_view["causal_locations"],
            "source": primary_view["source"],
        }
        cross = opportunity.get("cross_timeframe_features") or primary_section.get("cross_timeframe_features")
        if cross is not None:
            context["cross_timeframe_features"] = {
            "values": deepcopy(cross),
                "provenance": deepcopy(opportunity.get("cross_timeframe_provenance")) or {
                    "source_timeframes": list(selected), "formula_version": "unspecified",
                },
            }
        contexts.append(context)
    return contexts


def orderflow_audit_records(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Build post-hoc oracle metadata without putting it in Jev state."""
    target_date = str(payload.get("date") or "")
    records: list[dict[str, Any]] = []
    for timeframe, section in (payload.get("timeframes") or {}).items():
        if not isinstance(section, Mapping) or not isinstance(section.get("oracle_trades"), list):
            continue
        for ordinal, opportunity in enumerate(section["oracle_trades"], 1):
            if not isinstance(opportunity, Mapping):
                raise TypeError("oracle opportunity must be a JSON object")
            signal_time = str(opportunity.get("signal_time") or "")
            if not signal_time:
                raise ValueError("oracle opportunity is missing signal_time")
            record = dict(opportunity)
            record.update({
                "candidate_id": f"orderflow-{target_date}-{timeframe}-{signal_time}-{ordinal}",
                "date": target_date,
                "timeframe": str(timeframe),
                "oracle_trade_present": True,
            })
            records.append(record)
    return records


def write_orderflow_contexts(input_path: Path, output_path: Path) -> int:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise TypeError("orderflow trace must be a JSON object")
    contexts = orderflow_contexts(payload)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as stream:
        for context in contexts:
            stream.write(json.dumps(context, sort_keys=True, separators=(",", ":")) + "\n")
    return len(contexts)


def write_orderflow_audit(input_path: Path, output_path: Path) -> int:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise TypeError("orderflow trace must be a JSON object")
    records = orderflow_audit_records(payload)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    return len(records)


def run_orderflow_trace(
    niftyzoning_root: Path, date: str, output_path: Path, audit_output_path: Path | None = None,
) -> int:
    """Run NiftyZoning's research trace, then project its oracle times causally."""
    trace_script = niftyzoning_root / "scripts/orderflow/experiments/trace_orderflow_day.py"
    python_path = niftyzoning_root / ".venv/Scripts/python.exe"
    with TemporaryDirectory(prefix="jev-orderflow-") as trace_root:
        trace_dir = Path(trace_root)
        subprocess.run(
            [str(python_path if python_path.is_file() else Path(sys.executable)),
             str(trace_script), "--date", date, "--output", str(trace_dir)],
            cwd=niftyzoning_root, check=True,
        )
        trace_path = trace_dir / f"orderflow-{date[:4]}-{date[4:6]}-{date[6:]}-full-day-trace.json"
        count = write_orderflow_contexts(trace_path, output_path)
        if audit_output_path is not None:
            write_orderflow_audit(trace_path, audit_output_path)
        return count


__all__ = [
    "context_from_candidate",
    "orderflow_audit_records",
    "orderflow_contexts",
    "run_orderflow_trace",
    "write_contexts",
    "write_orderflow_audit",
    "write_orderflow_contexts",
]
