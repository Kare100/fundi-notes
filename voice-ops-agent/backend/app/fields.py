"""Single source of truth for the job fields, statuses and review states.

Nothing in here imports from the rest of the app, so every other module can use it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

JOB_STATUSES = (
    "completed",
    "in_progress",
    "partially_completed",
    "awaiting_return_visit",
    "blocked",
    "unknown",
)

STATUS_LABELS = {
    "completed": "Completed",
    "in_progress": "In progress",
    "partially_completed": "Partially completed",
    "awaiting_return_visit": "Awaiting return visit",
    "blocked": "Blocked",
    "unknown": "Not stated",
}

# How statuses roll up on the dashboard and in the daily summary (kept identical in both places).
GROUP_COMPLETED = ("completed",)
GROUP_IN_PROGRESS = ("in_progress", "awaiting_return_visit")
GROUP_BLOCKED_OR_INCOMPLETE = ("blocked", "partially_completed")

FLAG_TYPES = ("missing", "contradiction", "ambiguous", "confirmation")
FLAG_STATES = ("outstanding", "confirmed", "corrected", "not_applicable")
REVIEW_STATES = ("unreviewed", "confirmed", "corrected", "not_applicable", "outstanding")

REPORT_STATES = ("transcribing", "extracting", "done", "failed")

# Fields whose value must literally appear in the transcript (guards against invented names).
ENTITY_KEYS = ("technician", "customer", "job_reference", "location")
# List fields whose individual items must be traceable to the transcript.
SUPPORTED_ITEM_KEYS = ("equipment", "parts_materials")


@dataclass(frozen=True)
class FieldDef:
    key: str
    label: str
    kind: str  # "text" | "list" | "enum"
    help: str = ""


FIELD_DEFS: tuple[FieldDef, ...] = (
    FieldDef("customer", "Customer", "text", "Customer or organisation name"),
    FieldDef("technician", "Technician", "text"),
    FieldDef("job_reference", "Job reference", "text"),
    FieldDef("activity_date", "Date of activity", "text", "As stated in the recording"),
    FieldDef("location", "Site or location", "text"),
    FieldDef("job_status", "Status", "enum", "Only 'Completed' if the technician explicitly said so"),
    FieldDef("work_performed", "Work completed", "list", "One item per line"),
    FieldDef("equipment", "Equipment involved", "list", "One item per line"),
    FieldDef("parts_materials", "Parts or materials used", "list", "One item per line"),
    FieldDef("issues", "Issues encountered", "list", "One item per line"),
    FieldDef("outstanding_work", "Outstanding work", "list", "One item per line"),
    FieldDef("customer_concerns", "Customer concerns or unresolved questions", "list", "One item per line"),
    FieldDef("testing_confirmed", "Testing or verification", "text", "Was the work tested, and the result"),
)
FIELD_BY_KEY = {f.key: f for f in FIELD_DEFS}
FIELD_KEYS = tuple(f.key for f in FIELD_DEFS)

_MISSING_WORDS = {
    "",
    "unknown",
    "not provided",
    "not mentioned",
    "not stated",
    "unspecified",
    "n/a",
    "na",
    "none",
    "null",
    "nil",
}


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    if text.lower().strip(".") in _MISSING_WORDS:
        return None
    return text


def clean_value(kind: str, value: Any) -> Any:
    """Normalise a field value. Returns None (text/enum), [] -> None (list becomes None too when empty)."""
    if kind == "list":
        if value is None:
            return []
        items = value if isinstance(value, (list, tuple)) else str(value).splitlines()
        out: list[str] = []
        for item in items:
            cleaned = _clean_text(item)
            if cleaned and cleaned not in out:
                out.append(cleaned)
        return out
    if kind == "enum":
        text = _clean_text(value)
        if text is None:
            return "unknown"
        normalised = text.lower().replace("-", "_").replace(" ", "_")
        return normalised if normalised in JOB_STATUSES else "unknown"
    return _clean_text(value)


def is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (list, tuple)):
        return len(value) == 0
    if isinstance(value, str):
        return _clean_text(value) is None
    return False
