"""LLM adapter interface.

To add a provider: subclass LLMProvider, implement `complete_json`, and register it in
`app/services/registry.py`. The prompts, schema validation and safety checks live elsewhere and are
shared by every provider.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import httpx


class LLMError(Exception):
    """The LLM call failed. The message is safe to show to users (no keys, no transcript content)."""


class LLMProvider(ABC):
    name: str = "base"
    is_demo: bool = False

    @abstractmethod
    def complete_json(self, system: str, user: str) -> str:
        """Send the prompts and return the raw text of the model's reply (expected to be one JSON object)."""


def raise_for_status(resp: httpx.Response, service: str) -> None:
    """Translate HTTP failures into safe, specific messages. Never includes the response body."""
    code = resp.status_code
    if code < 400:
        return
    if code in (401, 403):
        raise LLMError(f"{service} rejected the API key. Check it in backend/.env.")
    if code == 429:
        raise LLMError(f"{service} is rate limited or out of quota. Try again later.")
    if code == 400:
        raise LLMError(f"{service} rejected the request (HTTP 400). Check the configured model name.")
    if code == 404:
        raise LLMError(f"{service} could not find the configured model (HTTP 404). Check the model name in backend/.env.")
    raise LLMError(f"{service} returned an error (HTTP {code}).")
