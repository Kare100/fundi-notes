"""Health, public configuration for the UI, and sample transcripts."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import require_auth
from ..config import ALLOWED_AUDIO_EXTENSIONS, Settings, get_checklist, get_settings
from ..fields import FIELD_BY_KEY, FIELD_DEFS, FLAG_STATES, JOB_STATUSES, REVIEW_STATES, STATUS_LABELS
from ..services import samples

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health(settings: Settings = Depends(get_settings)):
    """Unauthenticated. Reveals only whether login is required and whether demo mode is active."""
    return {
        "status": "ok",
        "auth_required": settings.auth_enabled,
        "demo_transcription": settings.transcription_provider == "mock",
        "demo_llm": settings.llm_provider == "mock",
    }


def data_sharing(settings: Settings) -> list[str]:
    lines: list[str] = []
    if settings.transcription_provider == "openai":
        lines.append("Audio files you upload are sent to OpenAI for transcription.")
    if settings.llm_provider in ("anthropic", "openai"):
        who = "Anthropic" if settings.llm_provider == "anthropic" else "OpenAI"
        redact = (
            "Emails and phone numbers are replaced with placeholders first."
            if settings.redact_contact_details
            else "Contact details are NOT redacted."
        )
        lines.append(f"Transcript text is sent to {who} for extraction. {redact}")
    if not lines:
        lines.append("Demo mode: nothing is sent to any third-party service.")
    lines.append("Everything else (reports, edits, transcripts) stays in this server's own database.")
    return lines


@router.get("/config", dependencies=[Depends(require_auth)])
def public_config(settings: Settings = Depends(get_settings)):
    return {
        "demo": {
            "transcription": settings.transcription_provider == "mock",
            "llm": settings.llm_provider == "mock",
        },
        "providers": {"transcription": settings.transcription_provider, "llm": settings.llm_provider},
        "upload": {
            "extensions": list(ALLOWED_AUDIO_EXTENSIONS),
            "max_mb": settings.max_upload_mb,
            "max_minutes": round(settings.max_audio_seconds / 60, 1),
        },
        "retention": {"mode": settings.audio_retention, "days": settings.audio_retention_days},
        "data_sharing": data_sharing(settings),
        "fields": [{"key": f.key, "label": f.label, "kind": f.kind, "help": f.help} for f in FIELD_DEFS],
        "job_statuses": [{"value": s, "label": STATUS_LABELS[s]} for s in JOB_STATUSES],
        "checklist": [
            {
                "field": c.field,
                "label": c.label or FIELD_BY_KEY[c.field].label,
                "severity": c.severity,
                "when_status": c.when_status,
            }
            for c in get_checklist().required_fields
        ],
        "flag_states": list(FLAG_STATES),
        "review_states": list(REVIEW_STATES),
    }


@router.get("/samples", dependencies=[Depends(require_auth)])
def list_samples():
    return samples.public_list()
