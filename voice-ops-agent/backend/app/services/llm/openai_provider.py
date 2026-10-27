"""OpenAI chat completions adapter (transcript text is sent to OpenAI)."""
from __future__ import annotations

import httpx

from .base import LLMError, LLMProvider, raise_for_status

URL = "https://api.openai.com/v1/chat/completions"


class OpenAILLM(LLMProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str, client: httpx.Client | None = None, timeout: float = 90):
        self._key = api_key
        self.model = model
        self._client = client or httpx.Client(timeout=timeout)

    def complete_json(self, system: str, user: str) -> str:
        body = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        try:
            resp = self._client.post(URL, headers={"Authorization": f"Bearer {self._key}"}, json=body)
        except httpx.TimeoutException as exc:
            raise LLMError("The AI service timed out. Try reprocessing the report.") from exc
        except httpx.HTTPError as exc:
            raise LLMError("Could not reach the AI service. Check the server's internet connection.") from exc
        raise_for_status(resp, "The AI service")
        try:
            text = resp.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("The AI service returned an unreadable response.") from exc
        if not text or not text.strip():
            raise LLMError("The AI service returned an empty response.")
        return text
