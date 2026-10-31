"""Small OpenRouter HTTP adapter kept outside the deterministic runtime."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import httpx

QUESTIONS: dict[str, dict[str, Any]] = {
    "data_quality": {
        "type": "choice",
        "instructions": "Assess whether the supplied point-in-time evidence is sufficient for a pre-trade decision.",
        "criteria": {
            "sufficient": "Market, signal, portfolio, and execution evidence are current and usable.",
            "partial": "Some evidence is missing or stale, but concrete risk checks remain possible.",
            "insufficient": "The state lacks enough current evidence for a directional or risk judgement.",
        },
    },
    "signal_alignment": {
        "type": "choice",
        "instructions": "Compare the requested action and signal reason with the supplied market evidence.",
        "criteria": {
            "aligned": "The evidence supports the requested direction.",
            "mixed": "Evidence is usable but indicators disagree without a strong contradiction.",
            "conflict": "Current evidence materially contradicts the requested direction.",
            "insufficient": "There is not enough current market evidence to judge alignment.",
        },
    },
    "market_regime": {
        "type": "choice",
        "instructions": "Judge whether the current regime is suitable for the requested entry.",
        "criteria": {
            "favorable": "The regime supports the entry.",
            "neutral": "The regime is mixed but does not materially oppose the entry.",
            "adverse": "The regime materially opposes the entry.",
            "insufficient": "The supplied evidence is unavailable or too stale to judge the regime.",
        },
    },
    "risk_check": {
        "type": "choice",
        "instructions": "Assess sizing, exposure, drawdown, recent performance, protection, and deterministic budget.",
        "criteria": {
            "clear": "No material account or portfolio risk is visible.",
            "caution": "Risk is elevated but remains within supplied limits.",
            "block": "A concrete risk requires blocking.",
            "insufficient": "Account evidence is incomplete.",
        },
    },
    "execution_quality": {
        "type": "choice",
        "instructions": "Assess price freshness, protection, order type, timing, and execution constraints.",
        "criteria": {
            "clear": "The order has no material execution concern.",
            "caution": "Execution is possible but has material caveats.",
            "block": "A concrete execution issue requires blocking.",
            "insufficient": "Execution evidence is incomplete.",
        },
    },
}


class JevClient:
    def __init__(self, base_url: str, model: str, timeout_seconds: float, api_key: str) -> None:
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self.model = model
        self._client = httpx.Client(timeout=timeout_seconds)
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/quantdinger/jev-shadow",
            "X-Title": "NiftyZoning Jev Shadow",
        }

    def evaluate(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        response = self._client.post(
            self._url,
            headers=self._headers,
            json={
                "model": self.model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Return only a JSON object with exactly these keys: "
                            f"{', '.join(QUESTIONS)}. Each value must contain a choice, "
                            "confidence from 0 to 1, and a concise rationale. "
                            "This is a read-only shadow analysis; never issue execution commands."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"state": dict(context), "questions": QUESTIONS},
                            sort_keys=True,
                        ),
                    },
                ],
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise TypeError("OpenRouter response must be a JSON object")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            raise ValueError("OpenRouter response did not contain choices")
        message = choices[0].get("message")
        if not isinstance(message, Mapping) or not isinstance(message.get("content"), str):
            raise TypeError("OpenRouter response did not contain message content")
        try:
            answers = json.loads(message["content"])
        except json.JSONDecodeError as exc:
            raise ValueError("OpenRouter response content was not valid JSON") from exc
        if isinstance(answers, Mapping) and isinstance(answers.get("answers"), Mapping):
            answers = answers["answers"]
        if not isinstance(answers, Mapping):
            raise TypeError("Jev response did not contain an answers object")
        normalized = {
            name: dict(answer)
            for name, answer in answers.items()
            if name in QUESTIONS and isinstance(answer, Mapping)
        }
        if set(normalized) != set(QUESTIONS):
            raise ValueError("Jev response did not contain every configured question")
        return normalized

    def close(self) -> None:
        self._client.close()
