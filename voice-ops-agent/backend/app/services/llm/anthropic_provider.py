"""Anthropic Messages API adapter (transcript text is sent to Anthropic)."""
from __future__ import annotations

import httpx

from .base import LLMError, LLMProvider, raise_for_status

URL = "https://api.anthropic.com/v1/messages"


class AnthropicLLM(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str, client: httpx.Client | None = None, timeout: float = 90, max_tokens: int = 3000):
        self._key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self._client = client or httpx.Client(timeout=timeout)

    def complete_json(self, system: str, user: str) -> str:
        body = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        headers = {"x-api-key": self._key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        try:
            resp = self._client.post(URL, headers=headers, json=body)
        except httpx.TimeoutException as exc:
            raise LLMError("The AI service timed out. Try reprocessing the report.") from exc
        except httpx.HTTPError as exc:
            raise LLMError("Could not reach the AI service. Check the server's internet connection.") from exc
        raise_for_status(resp, "The AI service")
        try:
            blocks = resp.json().get("content") or []
        except ValueError as exc:
            raise LLMError("The AI service returned an unreadable response.") from exc
        text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
        if not text.strip():
            raise LLMError("The AI service returned an empty response.")
        return text
