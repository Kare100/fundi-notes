"""Chooses the configured provider implementations.

This is the only place that knows which concrete classes exist. To add a provider, import it here and
add a branch (and a value to the Literal in config.py). Tests replace these functions with fakes.
"""
from __future__ import annotations

from ..config import ConfigurationError, get_settings
from .llm.anthropic_provider import AnthropicLLM
from .llm.base import LLMProvider
from .llm.mock import MockLLM
from .llm.openai_provider import OpenAILLM
from .transcription.base import TranscriptionProvider
from .transcription.demo import DemoTranscriber
from .transcription.openai_provider import OpenAITranscriber


def get_transcriber() -> TranscriptionProvider:
    s = get_settings()
    if s.transcription_provider == "openai":
        if not s.openai_api_key:
            raise ConfigurationError("OPENAI_API_KEY is not set.")
        return OpenAITranscriber(s.openai_api_key, s.openai_transcribe_model)
    return DemoTranscriber()


def get_llm() -> LLMProvider:
    s = get_settings()
    if s.llm_provider == "anthropic":
        if not s.anthropic_api_key:
            raise ConfigurationError("ANTHROPIC_API_KEY is not set.")
        return AnthropicLLM(s.anthropic_api_key, s.anthropic_model)
    if s.llm_provider == "openai":
        if not s.openai_api_key or not s.openai_llm_model:
            raise ConfigurationError("OPENAI_API_KEY and OPENAI_LLM_MODEL must be set.")
        return OpenAILLM(s.openai_api_key, s.openai_llm_model)
    return MockLLM()
