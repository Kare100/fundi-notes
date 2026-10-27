"""Real provider adapters, exercised against a mocked HTTP transport (no network, no API keys)."""
from __future__ import annotations

import json

import httpx
import pytest

from app.config import ConfigurationError, Settings, check_startup
from app.services import registry
from app.services.llm.anthropic_provider import AnthropicLLM
from app.services.llm.base import LLMError
from app.services.llm.openai_provider import OpenAILLM
from app.services.transcription.base import NoSpeechDetected, TranscriptionError
from app.services.transcription.openai_provider import OpenAITranscriber
from helpers import extraction, make_wav


def client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def wav(tmp_path):
    p = tmp_path / "n.wav"
    p.write_bytes(make_wav())
    return p


# ------------------------------------------------------------------ transcription
def test_openai_transcriber_success_with_confidence(wav):
    seen = {}

    def handler(req: httpx.Request):
        seen["auth"] = req.headers["authorization"]
        seen["body"] = req.content
        return httpx.Response(
            200,
            json={"text": " Hello there ", "language": "english", "segments": [{"avg_logprob": -0.1, "no_speech_prob": 0.01}]},
        )

    res = OpenAITranscriber("sk-test", client=client_for(handler)).transcribe(wav, language="en", prompt="Kenya")
    assert res.text == "Hello there" and res.language == "english"
    assert 0.9 < res.confidence < 0.91
    assert seen["auth"] == "Bearer sk-test"
    assert b'name="language"' in seen["body"] and b"Kenya" in seen["body"]


@pytest.mark.parametrize(
    "status, fragment",
    [(401, "API key"), (429, "rate limited"), (413, "too large"), (400, "could not read"), (500, "HTTP 500")],
)
def test_openai_transcriber_maps_errors_without_leaking_body(wav, status, fragment):
    def handler(req):
        return httpx.Response(status, text="internal detail sk-test-secret")

    with pytest.raises(TranscriptionError) as exc:
        OpenAITranscriber("sk-test-secret", client=client_for(handler)).transcribe(wav)
    assert fragment in str(exc.value)
    assert "secret" not in str(exc.value)


def test_openai_transcriber_timeout_and_network_errors(wav):
    def timeout(req):
        raise httpx.ReadTimeout("slow")

    def down(req):
        raise httpx.ConnectError("no route")

    with pytest.raises(TranscriptionError, match="timed out"):
        OpenAITranscriber("k", client=client_for(timeout)).transcribe(wav)
    with pytest.raises(TranscriptionError, match="Could not reach"):
        OpenAITranscriber("k", client=client_for(down)).transcribe(wav)


def test_openai_transcriber_empty_or_silent_audio(wav):
    with pytest.raises(NoSpeechDetected):
        OpenAITranscriber("k", client=client_for(lambda r: httpx.Response(200, json={"text": "  "}))).transcribe(wav)
    silent = {"text": "you", "segments": [{"avg_logprob": -2, "no_speech_prob": 0.97}]}
    with pytest.raises(NoSpeechDetected):
        OpenAITranscriber("k", client=client_for(lambda r: httpx.Response(200, json=silent))).transcribe(wav)


def test_openai_transcriber_unreadable_response(wav):
    with pytest.raises(TranscriptionError, match="unreadable"):
        OpenAITranscriber("k", client=client_for(lambda r: httpx.Response(200, text="<html>"))).transcribe(wav)


# ------------------------------------------------------------------ LLMs
def test_anthropic_adapter_request_and_response():
    seen = {}

    def handler(req: httpx.Request):
        seen["headers"], seen["json"] = req.headers, json.loads(req.content)
        return httpx.Response(200, json={"content": [{"type": "text", "text": '{"a": 1}'}]})

    out = AnthropicLLM("sk-ant", "model-x", client=client_for(handler)).complete_json("SYS", "USER")
    assert out == '{"a": 1}'
    assert seen["headers"]["x-api-key"] == "sk-ant"
    assert seen["json"]["model"] == "model-x" and seen["json"]["system"] == "SYS"
    assert seen["json"]["messages"] == [{"role": "user", "content": "USER"}]


def test_openai_llm_adapter_request_and_response():
    seen = {}

    def handler(req: httpx.Request):
        seen["json"] = json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"b": 2}'}}]})

    assert OpenAILLM("k", "m", client=client_for(handler)).complete_json("S", "U") == '{"b": 2}'
    assert seen["json"]["response_format"] == {"type": "json_object"}
    assert [m["role"] for m in seen["json"]["messages"]] == ["system", "user"]


@pytest.mark.parametrize("cls_args", [(AnthropicLLM, ("k", "m")), (OpenAILLM, ("k", "m"))])
@pytest.mark.parametrize(
    "status, fragment", [(401, "rejected the API key"), (429, "rate limited"), (404, "model"), (400, "HTTP 400"), (503, "HTTP 503")]
)
def test_llm_http_errors_are_safe(cls_args, status, fragment):
    cls, args = cls_args
    llm = cls(*args, client=client_for(lambda r: httpx.Response(status, text="leaky transcript text")))
    with pytest.raises(LLMError) as exc:
        llm.complete_json("s", "u")
    assert fragment in str(exc.value) and "leaky" not in str(exc.value)


@pytest.mark.parametrize("body", [{}, {"content": []}, {"content": [{"type": "text", "text": "  "}]}, {"choices": []}])
def test_llm_empty_or_malformed_responses(body):
    for llm in (
        AnthropicLLM("k", "m", client=client_for(lambda r: httpx.Response(200, json=body))),
        OpenAILLM("k", "m", client=client_for(lambda r: httpx.Response(200, json=body))),
    ):
        with pytest.raises(LLMError):
            llm.complete_json("s", "u")


def test_llm_timeout():
    def timeout(req):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(LLMError, match="timed out"):
        AnthropicLLM("k", "m", client=client_for(timeout)).complete_json("s", "u")


# ------------------------------------------------------------------ registry and configuration
def test_registry_selects_providers_from_settings(set_env):
    assert registry.get_transcriber().name == "demo" and registry.get_llm().name == "mock"
    set_env(TRANSCRIPTION_PROVIDER="openai", OPENAI_API_KEY="k", LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY="k2")
    assert registry.get_transcriber().name == "openai" and registry.get_llm().name == "anthropic"
    set_env(LLM_PROVIDER="openai", OPENAI_LLM_MODEL="some-model")
    assert registry.get_llm().name == "openai"


def test_startup_check_rejects_missing_keys_without_printing_them(set_env):
    set_env(TRANSCRIPTION_PROVIDER="openai", OPENAI_API_KEY="")
    from app.config import get_settings

    with pytest.raises(ConfigurationError, match="OPENAI_API_KEY"):
        check_startup(get_settings())


def test_production_refuses_a_weak_password(set_env):
    set_env(APP_ENV="production", AUTH_PASSWORD="change-me-please")
    from app.config import get_settings

    with pytest.raises(ConfigurationError, match="AUTH_PASSWORD"):
        check_startup(get_settings())


def test_data_sharing_statement_reflects_real_providers(client, set_env):
    set_env(TRANSCRIPTION_PROVIDER="openai", OPENAI_API_KEY="k", LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY="k2")
    cfg = client.get("/api/config").json()
    text = " ".join(cfg["data_sharing"])
    assert "Audio files you upload are sent to OpenAI" in text
    assert "Transcript text is sent to Anthropic" in text
    assert cfg["demo"] == {"transcription": False, "llm": False}
