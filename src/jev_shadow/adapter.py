"""Build point-in-time Jev contexts from NiftyZoning candidate artifacts."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

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
    contexts: list[dict[str, Any]] = []
    for timeframe, section in (payload.get("timeframes") or {}).items():
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
                    "volume_rank_in_session": trace.get("volume_rank_in_session"),
                    "oi_change_vs_prior_bar": trace.get("oi_change_vs_prior_bar"),
                    "target_range": trace.get("target_range"),
                    "target_clv": trace.get("target_clv"),
                    "target_delta_proxy": trace.get("target_delta_proxy"),
                    "cumulative_delta_proxy": trace.get("cumulative_delta_proxy"),
                },
                "causal_features": trace.get("causal_features"),
                "source": {
                    "artifact_type": "orderflow_full_day_trace",
                    "trace_scope": f"complete bars available through {signal_time}",
                },
            }
            contexts.append(context)
    return contexts


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


def run_orderflow_trace(
    niftyzoning_root: Path, date: str, output_path: Path,
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
        return write_orderflow_contexts(trace_path, output_path)


__all__ = [
    "context_from_candidate", "orderflow_contexts", "run_orderflow_trace",
    "write_contexts", "write_orderflow_contexts",
]
