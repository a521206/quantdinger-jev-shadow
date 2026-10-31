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
    signal = response.get("signal_alignment")
    regime = response.get("market_regime")
    assessment = response.get("trade_assessment")
    if (
        not isinstance(signal, Mapping)
        or not isinstance(regime, Mapping)
        or not isinstance(assessment, Mapping)
    ):
        return "invalid_response"
    allowed = {
        "signal_alignment": {"aligned", "mixed", "conflict", "insufficient"},
        "market_regime": {"favorable", "neutral", "adverse", "insufficient"},
        "trade_assessment": {"worth_taking", "not_worth_taking", "insufficient"},
    }
    for name, choices in allowed.items():
        answer = response[name]
        if answer.get("choice") not in choices:
            return "invalid_response"
        confidence = answer.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            return "invalid_response"
    if assessment.get("choice") == "not_worth_taking":
        return "would_reject"
    confidences = [signal.get("confidence"), regime.get("confidence"), assessment.get("confidence")]
    if any(not isinstance(value, (int, float)) or float(value) < min_confidence for value in confidences):
        return "would_review"
    if assessment.get("choice") == "insufficient" or signal.get("choice") == "insufficient" or regime.get("choice") == "insufficient":
        return "would_review"
    if signal.get("choice") == "conflict" or regime.get("choice") == "adverse":
        return "would_review"
    if assessment.get("choice") == "worth_taking" and signal.get("choice") in {"aligned", "mixed"}:
        return "would_pass"
    return "would_review"
