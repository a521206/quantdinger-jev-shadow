"""Run Jev against a prepared point-in-time context JSONL stream."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .artifacts import append_record
from .client import JevClient
from .config import ShadowConfig
from .models import ShadowRecord, context_hash, converge


def run_jsonl(input_path: Path, output_path: Path, config: ShadowConfig) -> int:
    """Evaluate contexts without changing the source run or its database."""
    if not config.jev_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required for an OpenRouter shadow run")
    client = JevClient(
        config.jev_base_url, config.jev_model, config.timeout_seconds, config.jev_api_key,
    )
    count = 0
    try:
        for line_number, line in enumerate(input_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, Mapping):
                raise TypeError(f"context line {line_number} must be a JSON object")
            context = dict(value)
            candidate_id = str(context.get("candidate_id") or "")
            if not candidate_id:
                raise ValueError(f"context line {line_number} is missing candidate_id")
            digest = context_hash(context)
            started = time.perf_counter()
            response: Mapping[str, Any] | None = None
            status = "ok"
            error: str | None = None
            try:
                response = client.evaluate(context)
                convergence = converge(response)
            except Exception as exc:  # noqa: BLE001 - provider failures are persisted.
                convergence = "unavailable"
                status = "error"
                error = f"{type(exc).__name__}: {exc}"
            append_record(output_path, ShadowRecord(
                request_id=f"{candidate_id}:{digest[:16]}",
                candidate_id=candidate_id,
                context_hash=digest,
                context=context,
                response=response,
                status=status,
                shadow_convergence=convergence,
                provider="openrouter",
                model=config.jev_model,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                error=error,
            ))
            count += 1
    finally:
        client.close()
    return count
