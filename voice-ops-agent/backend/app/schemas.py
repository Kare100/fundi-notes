"""Request and response schemas for the HTTP API (Pydantic v2)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, model_validator

from .fields import FLAG_STATES, JOB_STATUSES, REVIEW_STATES


def _utc_iso(value: datetime) -> str:
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


UtcDatetime = Annotated[datetime, PlainSerializer(_utc_iso, return_type=str, when_used="json")]


class EvidenceOut(BaseModel):
    text: str
    verified: bool


class FieldOut(BaseModel):
    key: str
    label: str
    kind: str
    help: str = ""
    ai_value: Any = None
    value: Any = None
    basis: str
    evidence: list[EvidenceOut] = []
    review_state: str
    edited_at: UtcDatetime | None = None


class FlagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    type: str
    field_key: str | None
    severity: str
    detail: str
    question: str
    evidence: list[EvidenceOut] = []
    origin: str
    state: str
    resolved_by: str | None
    note: str | None
    resolved_at: UtcDatetime | None


class ActionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    description: str
    owner: str | None
    deadline: str | None
    evidence: list[EvidenceOut] = []
    source: str
    completed: bool
    completed_at: UtcDatetime | None
    created_at: UtcDatetime


class TranscriptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    original_text: str
    provider: str
    language: str | None
    confidence: float | None
    uncertain: bool
    warnings: list[str]
    is_demo: bool
    created_at: UtcDatetime


class ReportListItem(BaseModel):
    id: int
    created_at: UtcDatetime
    reported_on: date
    state: str
    error_message: str | None
    label: str
    customer: str | None
    job_reference: str | None
    technician: str | None
    location: str | None
    status: str
    status_label: str
    is_demo: bool
    reviewed: bool
    open_actions: int
    outstanding_flags: dict[str, int]
    outstanding_flag_count: int
    missing_info: bool
    action_required: bool
    summary: str | None


class ReportDetail(ReportListItem):
    updated_at: UtcDatetime
    source_type: str
    source_filename: str | None
    sample_id: str | None
    error_stage: str | None
    transcription_provider: str | None
    llm_provider: str | None
    summary_ai: str | None
    transcript: TranscriptOut | None
    fields: list[FieldOut]
    flags: list[FlagOut]
    actions: list[ActionOut]
    job_report: dict[str, Any]
    report_text: str
    audio_available: bool


class StatsOut(BaseModel):
    total_reports: int
    processed: int
    failed: int
    processing: int
    completed: int
    in_progress: int
    blocked_or_incomplete: int
    status_not_stated: int
    with_missing_info: int
    open_actions: int
    reports_with_open_actions: int
    unreviewed: int


# ------------------------------------------------------------------------------------ requests
class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DemoCreate(_Strict):
    sample_id: str | None = None
    text: str | None = Field(default=None, min_length=20, max_length=20000)
    reported_on: date | None = None

    @model_validator(mode="after")
    def _one_source(self) -> "DemoCreate":
        if bool(self.sample_id) == bool(self.text):
            raise ValueError("Provide exactly one of sample_id or text.")
        return self


class ReportPatch(_Strict):
    job_status: Literal[JOB_STATUSES] | None = None  # type: ignore[valid-type]
    reported_on: date | None = None
    summary: str | None = Field(default=None, min_length=1, max_length=1500)
    reviewed: bool | None = None


class FieldPatch(_Strict):
    value: str | list[str] | None = None
    review_state: Literal[REVIEW_STATES] | None = None  # type: ignore[valid-type]


class FlagPatch(_Strict):
    state: Literal[FLAG_STATES]  # type: ignore[valid-type]
    note: str | None = Field(default=None, max_length=500)


class ActionCreate(_Strict):
    description: str = Field(min_length=3, max_length=500)
    owner: str | None = Field(default=None, max_length=120)
    deadline: str | None = Field(default=None, max_length=120)


class ActionPatch(_Strict):
    description: str | None = Field(default=None, min_length=3, max_length=500)
    owner: str | None = Field(default=None, max_length=120)
    deadline: str | None = Field(default=None, max_length=120)
    completed: bool | None = None
