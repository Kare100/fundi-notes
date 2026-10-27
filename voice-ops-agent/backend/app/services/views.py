"""Build the structured JOB REPORT and the dashboard row for a report.

Everything here is derived from the *current* field values, so manager edits show up immediately.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from ..fields import FIELD_BY_KEY, FIELD_DEFS, STATUS_LABELS
from ..models import Report

NOT_PROVIDED = "Not provided"


def report_label(report: Report) -> str:
    return report.value("customer") or report.value("job_reference") or f"Report #{report.id}"


def status_of(report: Report) -> str:
    return (report.value("job_status") or "unknown") if report.state == "done" else "unknown"


def outstanding_flags(report: Report) -> list:
    return [f for f in report.flags if f.state == "outstanding"]


def open_actions(report: Report) -> list:
    return [a for a in report.actions if not a.completed]


def list_item(report: Report) -> dict[str, Any]:
    flags = outstanding_flags(report)
    by_type = Counter(f.type for f in flags)
    n_actions = len(open_actions(report))
    failed = report.state == "failed"
    return {
        "id": report.id,
        "created_at": report.created_at,
        "reported_on": report.reported_on,
        "state": report.state,
        "error_message": report.error_message,
        "label": report_label(report),
        "customer": report.value("customer"),
        "job_reference": report.value("job_reference"),
        "technician": report.value("technician"),
        "location": report.value("location"),
        "status": status_of(report),
        "status_label": STATUS_LABELS[status_of(report)],
        "is_demo": report.is_demo,
        "reviewed": report.reviewed,
        "open_actions": n_actions,
        "outstanding_flags": dict(by_type),
        "outstanding_flag_count": len(flags),
        "missing_info": by_type.get("missing", 0) > 0,
        "action_required": failed or n_actions > 0 or len(flags) > 0,
        "summary": report.summary,
    }


def _source_confidence(report: Report) -> dict[str, Any]:
    t = report.transcript
    notes: list[str] = []
    interpreted = [
        FIELD_BY_KEY[f.key].label
        for f in report.fields
        if f.basis == "interpreted" and f.review_state not in ("confirmed", "corrected")
    ]
    unverified_quotes = sum(1 for f in report.fields for e in (f.evidence or []) if not e.get("verified"))

    if t and t.is_demo:
        notes.append("Demo data: this text came from a built-in sample or was typed in. No speech-to-text service ran.")
    if report.llm_provider == "mock":
        notes.append("Extraction ran on the mock demo extractor, not a real AI model.")
    if t and t.uncertain:
        pct = f" ({round(t.confidence * 100)}%)" if t.confidence is not None else ""
        notes.append(f"Transcription confidence is low{pct}. Check key facts against the recording if it is still available.")
    for warning in (t.warnings if t else []):
        notes.append(warning)
    if interpreted:
        notes.append("AI interpretations awaiting your review: " + ", ".join(interpreted) + ".")
    if unverified_quotes:
        notes.append(f"{unverified_quotes} supporting quote(s) could not be found word for word in the transcript.")
    if report.summary_ai is not None and report.summary != report.summary_ai:
        notes.append("The executive summary was edited by a manager.")
    elif report.summary:
        notes.append("The executive summary is AI generated.")
    notes.append("Reviewed by a manager." if report.reviewed else "Not yet reviewed by a manager.")

    return {
        "transcription_provider": report.transcription_provider,
        "transcription_confidence": t.confidence if t else None,
        "transcription_uncertain": bool(t and t.uncertain),
        "interpreted_fields": interpreted,
        "unverified_quotes": unverified_quotes,
        "reviewed": report.reviewed,
        "notes": notes,
    }


def job_report(report: Report) -> dict[str, Any]:
    v = report.value
    status = status_of(report)
    activity_date = v("activity_date")
    date_text = activity_date or f"{NOT_PROVIDED} (reported on {report.reported_on.isoformat()})"
    return {
        "header": {
            "customer": v("customer") or NOT_PROVIDED,
            "technician": v("technician") or NOT_PROVIDED,
            "date": date_text,
            "job_reference": v("job_reference") or NOT_PROVIDED,
            "location": v("location") or NOT_PROVIDED,
            "status": STATUS_LABELS[status],
        },
        "executive_summary": report.summary or NOT_PROVIDED,
        "work_completed": v("work_performed") or [],
        "materials_equipment": {"equipment": v("equipment") or [], "parts_materials": v("parts_materials") or []},
        "outstanding_issues": {
            "issues": v("issues") or [],
            "outstanding_work": v("outstanding_work") or [],
            "customer_concerns": v("customer_concerns") or [],
        },
        "next_actions": [
            {"id": a.id, "description": a.description, "owner": a.owner, "deadline": a.deadline, "completed": a.completed}
            for a in report.actions
        ],
        "missing_information": [
            {"id": f.id, "type": f.type, "question": f.question} for f in outstanding_flags(report)
        ],
        "source_confidence": _source_confidence(report),
    }


def render_report_text(report: Report) -> str:
    jr = job_report(report)
    h = jr["header"]
    lines = [
        "JOB REPORT",
        "",
        f"Customer: {h['customer']}",
        f"Technician: {h['technician']}",
        f"Date: {h['date']}",
        f"Job reference: {h['job_reference']}",
        f"Location: {h['location']}",
        f"Status: {h['status']}",
        "",
        "Executive summary:",
        jr["executive_summary"],
        "",
        "Work completed:",
    ]
    lines += [f"- {i}" for i in jr["work_completed"]] or [f"- {NOT_PROVIDED}"]

    lines += ["", "Materials or equipment:"]
    me = jr["materials_equipment"]
    mat = [f"- Equipment: {i}" for i in me["equipment"]] + [f"- Part or material: {i}" for i in me["parts_materials"]]
    lines += mat or [f"- {NOT_PROVIDED}"]

    lines += ["", "Outstanding issues:"]
    oi = jr["outstanding_issues"]
    issues = (
        [f"- Issue: {i}" for i in oi["issues"]]
        + [f"- Outstanding work: {i}" for i in oi["outstanding_work"]]
        + [f"- Customer concern: {i}" for i in oi["customer_concerns"]]
    )
    lines += issues or ["- None reported"]

    lines += ["", "Next actions:"]
    acts = []
    for a in jr["next_actions"]:
        extra = f"Owner: {a['owner'] or 'not stated'}; Deadline: {a['deadline'] or 'not stated'}"
        acts.append(f"- {a['description']} ({extra}){' [done]' if a['completed'] else ''}")
    lines += acts or ["- None reported"]

    lines += ["", "Missing information:"]
    lines += [f"- {m['question']}" for m in jr["missing_information"]] or ["- None outstanding"]

    lines += ["", "Source confidence:"]
    lines += [f"- {n}" for n in jr["source_confidence"]["notes"]]
    return "\n".join(lines) + "\n"


def field_rows(report: Report) -> list[dict[str, Any]]:
    by_key = {f.key: f for f in report.fields}
    rows = []
    for fd in FIELD_DEFS:
        f = by_key.get(fd.key)
        rows.append(
            {
                "key": fd.key,
                "label": fd.label,
                "kind": fd.kind,
                "help": fd.help,
                "ai_value": f.ai_value if f else None,
                "value": f.value if f else None,
                "basis": f.basis if f else "missing",
                "evidence": (f.evidence or []) if f else [],
                "review_state": f.review_state if f else "unreviewed",
                "edited_at": f.edited_at if f else None,
            }
        )
    return rows


def iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
