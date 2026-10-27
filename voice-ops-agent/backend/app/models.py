"""Database models.

Design notes
* `Transcript.original_text` is written once and never modified, even when a manager edits fields.
* `ExtractedField` keeps `ai_value` (what the AI produced) next to `value` (the current, possibly
  manager-edited value) so edits are always auditable.
* `audio_path` is internal. It is never included in any API response.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    """Naive UTC timestamp (SQLite has no timezone support). Serialised with a trailing Z by the API."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    reported_on: Mapped[date] = mapped_column(Date, index=True)

    source_type: Mapped[str] = mapped_column(String(20))  # audio | demo_sample | typed_text
    source_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sample_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)

    state: Mapped[str] = mapped_column(String(20), default="transcribing", index=True)
    error_stage: Mapped[str | None] = mapped_column(String(30), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_ai: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    transcription_provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    llm_provider: Mapped[str | None] = mapped_column(String(40), nullable=True)

    audio_path: Mapped[str | None] = mapped_column(String(500), nullable=True)  # internal only
    audio_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    transcript: Mapped["Transcript | None"] = relationship(
        back_populates="report", uselist=False, cascade="all, delete-orphan"
    )
    fields: Mapped[list["ExtractedField"]] = relationship(
        back_populates="report", cascade="all, delete-orphan", order_by="ExtractedField.id"
    )
    flags: Mapped[list["Flag"]] = relationship(
        back_populates="report", cascade="all, delete-orphan", order_by="Flag.id"
    )
    actions: Mapped[list["Action"]] = relationship(
        back_populates="report", cascade="all, delete-orphan", order_by="Action.id"
    )

    # -- convenience accessors -------------------------------------------------------------
    def field(self, key: str) -> "ExtractedField | None":
        return next((f for f in self.fields if f.key == key), None)

    def value(self, key: str) -> Any:
        f = self.field(key)
        return f.value if f else None


class Transcript(Base):
    __tablename__ = "transcripts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), unique=True)
    original_text: Mapped[str] = mapped_column(Text)  # never modified after creation
    provider: Mapped[str] = mapped_column(String(40))
    language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    uncertain: Mapped[bool] = mapped_column(Boolean, default=False)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    report: Mapped[Report] = relationship(back_populates="transcript")


class ExtractedField(Base):
    __tablename__ = "extracted_fields"
    __table_args__ = (UniqueConstraint("report_id", "key", name="uq_field_report_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(40))
    ai_value: Mapped[Any] = mapped_column(JSON, nullable=True)  # what the AI produced (after safety guards)
    value: Mapped[Any] = mapped_column(JSON, nullable=True)  # current value (may be manager edited)
    basis: Mapped[str] = mapped_column(String(12), default="missing")  # stated | interpreted | missing
    evidence: Mapped[list[dict]] = mapped_column(JSON, default=list)  # [{"text": str, "verified": bool}]
    review_state: Mapped[str] = mapped_column(String(16), default="unreviewed")
    edited_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    report: Mapped[Report] = relationship(back_populates="fields")


class Flag(Base):
    """A missing, contradictory, ambiguous or to-be-confirmed item with a clarification question."""

    __tablename__ = "flags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(80))  # stable id for rule flags, e.g. missing:customer
    type: Mapped[str] = mapped_column(String(16))  # missing | contradiction | ambiguous | confirmation
    field_key: Mapped[str | None] = mapped_column(String(40), nullable=True)
    severity: Mapped[str] = mapped_column(String(8), default="medium")
    detail: Mapped[str] = mapped_column(Text)
    question: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list[dict]] = mapped_column(JSON, default=list)
    origin: Mapped[str] = mapped_column(String(10), default="rule")  # rule | ai
    state: Mapped[str] = mapped_column(String(16), default="outstanding")
    resolved_by: Mapped[str | None] = mapped_column(String(10), nullable=True)  # manager | system
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    report: Mapped[Report] = relationship(back_populates="flags")


class Action(Base):
    __tablename__ = "actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), index=True)
    description: Mapped[str] = mapped_column(Text)
    owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    deadline: Mapped[str | None] = mapped_column(String(120), nullable=True)  # as stated, free text
    evidence: Mapped[list[dict]] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(10), default="ai")  # ai | manager
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    report: Mapped[Report] = relationship(back_populates="actions")
