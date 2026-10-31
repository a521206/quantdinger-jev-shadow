"""Small OpenRouter HTTP adapter kept outside the deterministic runtime."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import httpx

QUESTIONS: dict[str, dict[str, Any]] = {
    "data_quality": {
        "type": "choice",
        "instructions": "Assess whether the supplied point-in-time market and orderflow evidence is sufficient to judge this opportunity.",
        "criteria": {
            "sufficient": "Current market and orderflow evidence is usable for a directional judgement.",
            "partial": "Some market evidence is missing or limited, but a tentative directional judgement is possible.",
            "insufficient": "The state lacks enough current market evidence to judge the opportunity.",
        },
    },
    "signal_alignment": {
        "type": "choice",
        "instructions": "Assess whether the supplied orderflow evidence forms a coherent directional opportunity.",
        "criteria": {
            "aligned": "Orderflow evidence coherently supports a directional opportunity.",
            "mixed": "Evidence is usable but orderflow indicators disagree without a strong contradiction.",
            "conflict": "Orderflow evidence materially contradicts a directional opportunity.",
            "insufficient": "There is not enough current orderflow evidence to judge alignment.",
        },
    },
    "market_regime": {
        "type": "choice",
        "instructions": "Judge whether the current market regime is suitable for this orderflow opportunity.",
        "criteria": {
            "favorable": "The regime supports the opportunity.",
            "neutral": "The regime is mixed but does not materially oppose the opportunity.",
            "adverse": "The regime materially opposes the opportunity.",
            "insufficient": "The supplied evidence is unavailable or too stale to judge the regime.",
        },
    },
    "trade_assessment": {
        "type": "choice",
        "instructions": "Based only on the supplied point-in-time market and orderflow evidence, assess whether this opportunity is worth taking.",
        "criteria": {
            "worth_taking": "The market and orderflow evidence gives a sufficiently favorable directional case.",
            "not_worth_taking": "The market and orderflow evidence gives an unfavorable or contradicted directional case.",
            "insufficient": "The supplied market and orderflow evidence is too weak or contradictory to judge.",
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
