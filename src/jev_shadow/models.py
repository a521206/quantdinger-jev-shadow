"""Typed, JSON-ready records for Jev shadow evaluation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def context_hash(context: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(context).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ShadowRecord:
    """One request/response observation; it never changes the trading decision."""

    request_id: str
    candidate_id: str
    context_hash: str
    context: Mapping[str, Any]
    response: Mapping[str, Any] | None
    status: str
    shadow_convergence: str
    provider: str
    model: str
    latency_ms: float | None = None
    error: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "candidate_id": self.candidate_id,
            "context_hash": self.context_hash,
            "context": dict(self.context),
            "response": dict(self.response) if self.response is not None else None,
            "status": self.status,
            "shadow_convergence": self.shadow_convergence,
            "provider": self.provider,
            "model": self.model,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "metadata": dict(self.metadata),
        }


def converge(response: Mapping[str, Any] | None, *, min_confidence: float = 0.55) -> str:
    """Convert a Jev response into an analytical label, never an execution command."""
    if response is None:
        return "unavailable"
    risk = response.get("risk_check")
    execution = response.get("execution_quality")
    signal = response.get("signal_alignment")
    regime = response.get("market_regime")
    if (
        not isinstance(risk, Mapping)
        or not isinstance(execution, Mapping)
        or not isinstance(signal, Mapping)
        or not isinstance(regime, Mapping)
    ):
        return "invalid_response"
    if risk.get("choice") == "block" or execution.get("choice") == "block":
        return "would_reject"
    confidences = [risk.get("confidence"), execution.get("confidence")]
    if any(not isinstance(value, (int, float)) or float(value) < min_confidence for value in confidences):
        return "would_review"
    directional_block = signal.get("choice") == "conflict" and regime.get("choice") == "adverse"
    if directional_block or execution.get("choice") == "caution" or risk.get("choice") == "caution":
        return "would_review"
    return "would_pass"
