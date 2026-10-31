"""Summarize append-only Jev shadow records into a reviewable report."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tabulate import tabulate


def load_records(input_path: Path) -> list[Mapping[str, Any]]:
    records: list[Mapping[str, Any]] = []
    for line_number, line in enumerate(input_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, Mapping):
            raise TypeError(f"record line {line_number} must be a JSON object")
        records.append(value)
    return records


def analyze_records(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    statuses = Counter(str(record.get("status", "unknown")) for record in records)
    convergence = Counter(str(record.get("shadow_convergence", "unknown")) for record in records)
    timeframes = Counter(
        str(record.get("context", {}).get("timeframe", "unknown"))
        for record in records
        if isinstance(record.get("context"), Mapping)
    )
    dimensions: dict[str, Counter[str]] = {}
    latencies = [
        float(record["latency_ms"])
        for record in records
        if isinstance(record.get("latency_ms"), (int, float))
    ]
    errors = [
        {"candidate_id": str(record.get("candidate_id", "")), "error": str(record["error"])}
        for record in records
        if record.get("error")
    ]
    for record in records:
        response = record.get("response")
        if not isinstance(response, Mapping):
            continue
        for name, answer in response.items():
            if isinstance(answer, Mapping) and "choice" in answer:
                dimensions.setdefault(str(name), Counter())[str(answer["choice"])] += 1

    latency_summary = None
    if latencies:
        latency_summary = {
            "min_ms": min(latencies),
            "mean_ms": sum(latencies) / len(latencies),
            "max_ms": max(latencies),
        }
    return {
        "total": len(records),
        "successful": statuses.get("ok", 0),
        "failed": sum(count for status, count in statuses.items() if status != "ok"),
        "statuses": dict(statuses),
        "convergence": dict(convergence),
        "timeframes": dict(sorted(timeframes.items())),
        "dimensions": {name: dict(sorted(counts.items())) for name, counts in sorted(dimensions.items())},
        "latency_ms": latency_summary,
        "errors": errors,
    }


def render_markdown(summary: Mapping[str, Any], source_name: str) -> str:
    lines = [
        "# Jev Shadow Analysis",
        "",
        f"Source: `{source_name}`",
        "",
        "## Overview",
        "",
        f"- Contexts processed: {summary['total']}",
        f"- Successful responses: {summary['successful']}",
        f"- Failed responses: {summary['failed']}",
        f"- Convergence: {_format_counts(summary['convergence'])}",
        f"- Timeframes: {_format_counts(summary['timeframes'])}",
    ]
    latency = summary.get("latency_ms")
    if latency:
        lines.append(
            f"- Latency: mean {latency['mean_ms'] / 1000:.2f}s; "
            f"range {latency['min_ms'] / 1000:.2f}s to {latency['max_ms'] / 1000:.2f}s"
        )
    dimensions = summary.get("dimensions", {})
    if dimensions:
        lines.extend(["", "## Jev Assessment", ""])
        for name, counts in dimensions.items():
            lines.append(f"- `{name}`: {_format_counts(counts)}")
    errors = summary.get("errors", [])
    if errors:
        lines.extend(["", "## Failures", ""])
        for error in errors:
            lines.append(f"- `{error['candidate_id']}`: {error['error']}")
    lines.extend([
        "",
        "## Interpretation",
        "",
        (
            "This is a read-only shadow analysis. Jev output is advisory and must not increase quantity, "
            "bypass deterministic rejection, or control execution and exits."
        ),
    ])
    return "\n".join(lines) + "\n"


def write_analysis(input_path: Path, output_path: Path) -> dict[str, Any]:
    summary = analyze_records(load_records(input_path))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_markdown(summary, input_path.name), encoding="utf-8")
    return summary


def render_terminal(summary: Mapping[str, Any], source_name: str) -> str:
    """Render an analysis as terminal-friendly tables without writing a report."""
    overview = [
        ["Source", source_name],
        ["Contexts processed", summary["total"]],
        ["Successful responses", summary["successful"]],
        ["Failed responses", summary["failed"]],
    ]
    latency = summary.get("latency_ms")
    if latency:
        overview.append(["Latency", f"{latency['mean_ms'] / 1000:.2f}s mean"])
        overview.append([
            "Latency range",
            f"{latency['min_ms'] / 1000:.2f}s to {latency['max_ms'] / 1000:.2f}s",
        ])

    lines = ["Jev Shadow Analysis", tabulate(overview, tablefmt="simple")]
    for title, values in (
        ("Statuses", summary.get("statuses", {})),
        ("Convergence", summary.get("convergence", {})),
        ("Timeframes", summary.get("timeframes", {})),
    ):
        lines.extend(["", title, tabulate(values.items(), headers=["Value", "Count"], tablefmt="simple")])

    dimensions = summary.get("dimensions", {})
    if dimensions:
        assessment_rows = [
            [dimension, choice, count]
            for dimension, choices in dimensions.items()
            for choice, count in choices.items()
        ]
        lines.extend([
            "",
            "Jev Assessment",
            tabulate(assessment_rows, headers=["Dimension", "Choice", "Count"], tablefmt="simple"),
        ])

    errors = summary.get("errors", [])
    if errors:
        lines.extend([
            "",
            "Failures",
            tabulate(
                [[error["candidate_id"], error["error"]] for error in errors],
                headers=["Candidate", "Error"],
                tablefmt="simple",
            ),
        ])
    return "\n".join(lines) + "\n"


def _format_counts(counts: Mapping[str, int]) -> str:
    return "; ".join(f"{name} {count}" for name, count in counts.items()) or "none"
