"""Missing-information checker.

Rule flags (origin="rule") are recomputed from the current report values every time something is
saved. A flag the manager has explicitly set (confirmed / not applicable) is never overridden.
Flags that stop applying because the manager filled in the value are closed automatically.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from ..config import Checklist, get_checklist
from ..fields import FIELD_BY_KEY, is_empty
from ..models import Flag, Report, utcnow

_NEGATED_TEST = re.compile(
    r"\buntested\b|\bno\s+(?:test|testing)\b|"
    r"\b(?:not|never|haven't|have not|didn't|did not|yet to|wasn't|was not)\b\W+(?:\w+\W+){0,2}?test",
    re.IGNORECASE,
)
_RELATIVE_DATE = re.compile(
    r"\b(today|yesterday|tomorrow|tonight|this morning|this afternoon|last week|this week|kesho|jana|leo)\b",
    re.IGNORECASE,
)


@dataclass
class FlagDraft:
    code: str
    type: str
    field_key: str | None
    detail: str
    question: str
    severity: str = "medium"
    evidence: list[dict] = field(default_factory=list)
    origin: str = "rule"


def field_state(report: Report) -> tuple[dict[str, Any], set[str]]:
    values = {f.key: f.value for f in report.fields}
    values["job_status"] = values.get("job_status") or "unknown"
    not_applicable = {f.key for f in report.fields if f.review_state == "not_applicable"}
    return values, not_applicable


def compute_rule_flags(
    values: dict[str, Any], not_applicable: set[str], actions: list[dict], checklist: Checklist
) -> list[FlagDraft]:
    status = values.get("job_status") or "unknown"
    drafts: list[FlagDraft] = []

    # 1. Required information that is simply absent.
    for item in checklist.required_fields:
        if item.when_status is not None and status not in item.when_status:
            continue
        if item.field in not_applicable:
            continue
        missing = status == "unknown" if item.field == "job_status" else is_empty(values.get(item.field))
        if missing:
            label = item.label or FIELD_BY_KEY[item.field].label
            drafts.append(
                FlagDraft(
                    code=f"missing:{item.field}",
                    type="missing",
                    field_key=item.field,
                    detail=f"{label} was not provided in the recording.",
                    question=item.question,
                    severity=item.severity,
                )
            )

    # 2. Contradictions between fields.
    outstanding = values.get("outstanding_work") or []
    if status == "completed" and outstanding and "job_status" not in not_applicable:
        listed = "; ".join(outstanding[:3])
        drafts.append(
            FlagDraft(
                code="contradiction:completed_with_outstanding",
                type="contradiction",
                field_key="job_status",
                detail=f"Status is Completed, but outstanding work is also reported: {listed}.",
                question=f"The job is recorded as completed, but outstanding work was mentioned ({listed}). Is the job fully complete?",
                severity="high",
            )
        )
    testing = values.get("testing_confirmed")
    if status == "completed" and isinstance(testing, str) and _NEGATED_TEST.search(testing):
        drafts.append(
            FlagDraft(
                code="contradiction:completed_but_untested",
                type="contradiction",
                field_key="testing_confirmed",
                detail=f'Status is Completed, but testing is reported as: "{testing}".',
                question="The job is recorded as completed but testing was not done or failed. Should it stay Completed?",
                severity="high",
            )
        )

    # 3. Ambiguous dates ("today", "kesho") cannot be placed on a calendar without the recording date.
    date_value = values.get("activity_date")
    if isinstance(date_value, str) and "activity_date" not in not_applicable:
        match = _RELATIVE_DATE.search(date_value)
        if match:
            drafts.append(
                FlagDraft(
                    code="ambiguous:relative_date",
                    type="ambiguous",
                    field_key="activity_date",
                    detail=f'The date was given as "{date_value}", which is relative to when the note was recorded.',
                    question=f'Which calendar date does "{match.group(0)}" refer to?',
                    severity="low",
                )
            )

    # 4. Follow-up actions with nobody responsible.
    if checklist.require_action_owner:
        for a in actions:
            if not a["completed"] and is_empty(a.get("owner")):
                desc = a["description"]
                short = desc if len(desc) <= 80 else desc[:77] + "..."
                drafts.append(
                    FlagDraft(
                        code=f"missing:action_owner:{a['id']}",
                        type="missing",
                        field_key=None,
                        detail=f'No person or team was named for the action "{short}".',
                        question=f"Who is responsible for: {short}",
                        severity="medium",
                    )
                )
    return drafts


def sync_rule_flags(db: Session, report: Report) -> None:
    """Reconcile stored rule flags with what the checklist says right now."""
    values, not_applicable = field_state(report)
    actions = [
        {"id": a.id, "description": a.description, "owner": a.owner, "completed": a.completed}
        for a in report.actions
    ]
    drafts = {d.code: d for d in compute_rule_flags(values, not_applicable, actions, get_checklist())}
    existing = {f.code: f for f in report.flags if f.origin == "rule"}
    now = utcnow()

    for code, draft in drafts.items():
        flag = existing.get(code)
        if flag is None:
            report.flags.append(
                Flag(
                    code=draft.code,
                    type=draft.type,
                    field_key=draft.field_key,
                    severity=draft.severity,
                    detail=draft.detail,
                    question=draft.question,
                    evidence=draft.evidence,
                    origin="rule",
                )
            )
            continue
        system_closed = flag.state == "corrected" and flag.resolved_by == "system"
        if flag.state == "outstanding" or system_closed:
            flag.detail, flag.question = draft.detail, draft.question
        if system_closed:  # the problem came back, e.g. the manager cleared a value again
            flag.state, flag.resolved_by, flag.resolved_at = "outstanding", None, None

    for code, flag in existing.items():
        if code not in drafts and flag.state == "outstanding":
            flag.state, flag.resolved_by, flag.resolved_at = "corrected", "system", now
            flag.note = flag.note or "Closed automatically because the report was updated."
    db.flush()
