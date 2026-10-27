"""Application settings (environment variables / .env) and the report checklist."""
from __future__ import annotations

import json
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .fields import FIELD_KEYS, JOB_STATUSES

BACKEND_DIR = Path(__file__).resolve().parent.parent
ALLOWED_AUDIO_EXTENSIONS = ("mp3", "wav", "m4a", "webm", "ogg")
WEAK_PASSWORDS = {"", "change-me-please", "changeme", "password", "admin"}


class ConfigurationError(RuntimeError):
    """Raised at startup when the configuration cannot work. Messages never contain secrets."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    app_env: str = "development"
    timezone: str = "Africa/Nairobi"
    cors_origins: str = "http://localhost:5173"

    auth_enabled: bool = True
    auth_username: str = "manager"
    auth_password: str = ""

    database_url: str = f"sqlite:///{BACKEND_DIR / 'data' / 'app.db'}"
    upload_dir: Path = BACKEND_DIR / "data" / "uploads"
    max_upload_mb: int = Field(25, ge=1, le=200)
    max_audio_seconds: int = Field(600, ge=5)
    audio_retention: Literal["delete_after_processing", "keep_days"] = "delete_after_processing"
    audio_retention_days: int = Field(7, ge=1)

    transcription_provider: Literal["mock", "openai"] = "mock"
    openai_api_key: str = ""
    openai_transcribe_model: str = "whisper-1"
    transcribe_language: str = ""
    transcribe_prompt: str = "Field service voice note from Kenya. The speaker may mix English, Swahili and Sheng."
    transcription_min_confidence: float = Field(0.6, ge=0, le=1)

    llm_provider: Literal["mock", "anthropic", "openai"] = "mock"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5-5"
    openai_llm_model: str = ""
    redact_contact_details: bool = True

    checklist_path: Path = BACKEND_DIR / "config" / "checklist.json"
    samples_path: Path = BACKEND_DIR / "sample_data" / "samples.json"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def local_today(settings: Settings | None = None) -> date:
    settings = settings or get_settings()
    try:
        return datetime.now(ZoneInfo(settings.timezone)).date()
    except ZoneInfoNotFoundError:
        return datetime.utcnow().date()


def check_startup(settings: Settings) -> list[str]:
    """Fail fast on unusable configuration. Returns non-fatal warnings."""
    warnings: list[str] = []
    if settings.auth_enabled and settings.auth_password in WEAK_PASSWORDS:
        if settings.app_env == "production":
            raise ConfigurationError("AUTH_PASSWORD is empty or a placeholder. Set a strong password in .env.")
        warnings.append("AUTH_PASSWORD is empty or a placeholder. Fine for local development, never for production.")
    if not settings.auth_enabled:
        warnings.append("AUTH_ENABLED=false: the API is open to anyone who can reach this server.")
    if settings.transcription_provider == "openai" and not settings.openai_api_key:
        raise ConfigurationError("TRANSCRIPTION_PROVIDER=openai but OPENAI_API_KEY is not set.")
    if settings.llm_provider == "anthropic" and not settings.anthropic_api_key:
        raise ConfigurationError("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set.")
    if settings.llm_provider == "openai":
        if not settings.openai_api_key:
            raise ConfigurationError("LLM_PROVIDER=openai but OPENAI_API_KEY is not set.")
        if not settings.openai_llm_model:
            raise ConfigurationError("LLM_PROVIDER=openai but OPENAI_LLM_MODEL is not set.")
    if settings.transcription_provider == "mock":
        warnings.append("DEMO MODE: transcription is mocked. Audio uploads are rejected; use the sample transcripts.")
    if settings.llm_provider == "mock":
        warnings.append("DEMO MODE: extraction uses the mock LLM (canned results for samples, limited heuristics otherwise).")
    return warnings


# ----------------------------------------------------------------------------- checklist
class ChecklistItem(BaseModel):
    field: str
    question: str
    label: str | None = None
    severity: Literal["high", "medium", "low"] = "medium"
    when_status: list[str] | None = None

    @model_validator(mode="after")
    def _check(self) -> "ChecklistItem":
        if self.field not in FIELD_KEYS:
            raise ValueError(f"unknown field '{self.field}' (valid: {', '.join(FIELD_KEYS)})")
        for status in self.when_status or []:
            if status not in JOB_STATUSES:
                raise ValueError(f"unknown status '{status}' in when_status")
        return self


class Checklist(BaseModel):
    required_fields: list[ChecklistItem]
    require_action_owner: bool = True


@lru_cache
def get_checklist() -> Checklist:
    path = get_settings().checklist_path
    try:
        return Checklist.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
    except FileNotFoundError as exc:
        raise ConfigurationError(f"Checklist file not found: {path}") from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Checklist file {path} is invalid: {exc}") from exc
