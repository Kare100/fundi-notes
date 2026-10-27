"""Test configuration.

Environment variables are set BEFORE the app is imported so tests never touch a developer's real
database, uploads or .env file, and never call a paid API.
"""
from __future__ import annotations

import base64
import os
import shutil
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="voiceops-tests-"))
os.environ.update(
    {
        "APP_ENV": "development",
        "DATABASE_URL": f"sqlite:///{_TMP / 'test.db'}",
        "UPLOAD_DIR": str(_TMP / "uploads"),
        "AUTH_ENABLED": "true",
        "AUTH_USERNAME": "manager",
        "AUTH_PASSWORD": "test-password",
        "TRANSCRIPTION_PROVIDER": "mock",
        "LLM_PROVIDER": "mock",
        "OPENAI_API_KEY": "",
        "ANTHROPIC_API_KEY": "",
        "OPENAI_LLM_MODEL": "",
        "AUDIO_RETENTION": "delete_after_processing",
        "REDACT_CONTACT_DETAILS": "true",
        "MAX_UPLOAD_MB": "25",
        "MAX_AUDIO_SECONDS": "600",
        "TRANSCRIPTION_MIN_CONFIDENCE": "0.6",
    }
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.database import Base, get_engine, reset_engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services import registry  # noqa: E402

from helpers import FakeLLM, FakeTranscriber  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_state():
    """Empty database and upload folder for every test."""
    get_settings.cache_clear()
    reset_engine()
    from app import models  # noqa: F401

    Base.metadata.drop_all(get_engine())
    Base.metadata.create_all(get_engine())
    shutil.rmtree(_TMP / "uploads", ignore_errors=True)
    yield
    get_settings.cache_clear()
    reset_engine()


@pytest.fixture
def upload_dir() -> Path:
    return _TMP / "uploads"


@pytest.fixture
def set_env(monkeypatch):
    """Change a setting for one test: set_env(MAX_UPLOAD_MB="1")."""

    def _set(**values: str) -> None:
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()
        reset_engine()

    return _set


@pytest.fixture
def auth() -> dict[str, str]:
    token = base64.b64encode(b"manager:test-password").decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def client(auth) -> TestClient:
    c = TestClient(app)
    c.headers.update(auth)
    return c


@pytest.fixture
def anon_client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def fake_transcriber(monkeypatch) -> FakeTranscriber:
    fake = FakeTranscriber()
    monkeypatch.setattr(registry, "get_transcriber", lambda: fake)
    return fake


@pytest.fixture
def fake_llm(monkeypatch) -> FakeLLM:
    fake = FakeLLM()
    monkeypatch.setattr(registry, "get_llm", lambda: fake)
    return fake
