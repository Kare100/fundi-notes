"""Pydantic schema for the structured JSON the LLM must return, plus strict parsing.

Anything that does not match this schema is rejected before it reaches the database.
"""
from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .fields import JOB_STATUSES


class ExtractionValidationError(ValueError):
    """The LLM output was not valid JSON or did not match the schema. Message never contains the raw output."""


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


Basis = Literal["stated", "interpreted"]


class TextField(_Base):
    value: str | None = None
    basis: Basis = "stated"
    evidence: list[str] = Field(default_factory=list)


class ListField(_Base):
    value: list[str] = Field(default_factory=list)
    basis: Basis = "stated"
    evidence: list[str] = Field(default_factory=list)


class StatusField(_Base):
    value: Literal[JOB_STATUSES]  # type: ignore[valid-type]
    basis: Basis = "stated"
    evidence: list[str] = Field(default_factory=list)

    @field_validator("value", mode="before")
    @classmethod
    def _normalise(cls, v):
        if isinstance(v, str):
            return v.strip().lower().replace("-", "_").replace(" ", "_")
        return v


class ActionExtraction(_Base):
    description: str = Field(min_length=3, max_length=500)
    owner: str | None = None
    deadline: str | None = None
    evidence: list[str] = Field(default_factory=list)


class IssueExtraction(_Base):
    field: str | None = None
    detail: str = Field(min_length=3, max_length=600)
    question: str | None = Field(default=None, max_length=400)
    evidence: list[str] = Field(default_factory=list)


class ExtractionResult(_Base):
    technician: TextField
    customer: TextField
    job_reference: TextField
    activity_date: TextField
    location: TextField
    job_status: StatusField
    work_performed: ListField = Field(default_factory=ListField)
    equipment: ListField = Field(default_factory=ListField)
    parts_materials: ListField = Field(default_factory=ListField)
    issues: ListField = Field(default_factory=ListField)
    outstanding_work: ListField = Field(default_factory=ListField)
    customer_concerns: ListField = Field(default_factory=ListField)
    testing_confirmed: TextField = Field(default_factory=TextField)
    actions: list[ActionExtraction] = Field(default_factory=list)
    contradictions: list[IssueExtraction] = Field(default_factory=list)
    ambiguities: list[IssueExtraction] = Field(default_factory=list)
    summary: str = Field(min_length=1, max_length=1500)


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_extraction(raw: str) -> ExtractionResult:
    """Parse and validate raw LLM text. Raises ExtractionValidationError with a short, safe message."""
    if not raw or not raw.strip():
        raise ExtractionValidationError("The AI returned an empty response.")
    text = _FENCE.sub("", raw.strip()).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ExtractionValidationError("The AI response did not contain a JSON object.")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ExtractionValidationError(f"The AI response was not valid JSON ({exc.msg}).") from exc
    try:
        return ExtractionResult.model_validate(data)
    except ValidationError as exc:
        # Only locations and messages, never the offending input values (they may hold customer data).
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:6])
        raise ExtractionValidationError(f"The AI response did not match the required format ({problems}).") from exc
