"""Daily executive summary and dashboard statistics.

The summary is built deterministically from stored data. No LLM is involved, so totals can never be
invented, and every line says whether it is reviewed or unverified AI output. Anything that could make
the picture incomplete (failed or still-processing reports, unreviewed reports) is stated up front.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import get_settings
from ..fields import GROUP_BLOCKED_OR_INCOMPLETE, GROUP_COMPLETED, GROUP_IN_PROGRESS, STATUS_LABELS
from ..models import Report
from .views import open_actions, outstanding_flags, report_label, status_of

LOAD = (
    selectinload(Report.transcript),
    selectinload(Report.fields),
    selectinload(Report.flags),
    selectinload(Report.actions),
)


def load_reports(db: Session, start: date | None = None, end: date | None = None) -> list[Report]:
    stmt = select(Report).options(*LOAD).order_by(Report.reported_on.desc(), Report.id.desc())
    if start:
        stmt = stmt.where(Report.reported_on >= start)
    if end:
        stmt = stmt.where(Report.reported_on <= end)
    return list(db.scalars(stmt).all())


# ---------------------------------------------------------------------------------- statistics
def compute_stats(reports: list[Report]) -> dict[str, int]:
    done = [r for r in reports if r.state == "done"]
    statuses = [status_of(r) for r in done]
    actions = [a for r in reports for a in open_actions(r)]
    return {
        "total_reports": len(reports),
        "processed": len(done),
        "failed": sum(1 for r in reports if r.state == "failed"),
        "processing": sum(1 for r in reports if r.state in ("transcribing", "extracting")),
        "completed": sum(1 for s in statuses if s in GROUP_COMPLETED),
        "in_progress": sum(1 for s in statuses if s in GROUP_IN_PROGRESS),
        "blocked_or_incomplete": sum(1 for s in statuses if s in GROUP_BLOCKED_OR_INCOMPLETE),
        "status_not_stated": sum(1 for s in statuses if s == "unknown"),
        "with_missing_info": sum(1 for r in done if any(f.type == "missing" for f in outstanding_flags(r))),
        "open_actions": len(actions),
        "reports_with_open_actions": sum(1 for r in reports if open_actions(r)),
        "unreviewed": sum(1 for r in done if not r.reviewed),
    }


# ---------------------------------------------------------------------------------- daily summary
def _item(r: Report) -> dict[str, Any]:
    s = status_of(r)
    return {
        "report_id": r.id,
        "label": report_label(r),
        "technician": r.value("technician"),
        "status": s,
        "status_label": STATUS_LABELS[s],
        "reviewed": r.reviewed,
        "verification": "reviewed" if r.reviewed else "unverified AI output",
        "is_demo": r.is_demo,
    }


def _overdue(deadline: str | None, today: date) -> bool:
    if not deadline:
        return False
    try:
        return date.fromisoformat(deadline.strip()) < today
    except ValueError:
        return False  # free text such as "Monday" cannot be compared reliably


def build_summary(db: Session, start: date, end: date | None = None) -> dict[str, Any]:
    end = end or start
    reports = load_reports(db, start, end)
    stats = compute_stats(reports)
    done = [r for r in reports if r.state == "done"]
    failed = [r for r in reports if r.state == "failed"]

    completed = [{**_item(r), "work": (r.value("work_performed") or [])[:3]} for r in done if status_of(r) in GROUP_COMPLETED]
    in_progress = [
        {**_item(r), "remaining": (r.value("outstanding_work") or [])[:3]}
        for r in done
        if status_of(r) in GROUP_IN_PROGRESS
    ]
    blocked = []
    for r in done:
        if status_of(r) in GROUP_BLOCKED_OR_INCOMPLETE:
            reasons = (r.value("issues") or []) or (r.value("outstanding_work") or [])
            blocked.append({**_item(r), "reasons": reasons[:3] or ["No reason was reported."]})
    not_stated = [_item(r) for r in done if status_of(r) == "unknown"]

    missing, to_confirm = [], []
    for r in done:
        flags = outstanding_flags(r)
        m = [f.question for f in flags if f.type == "missing"]
        c = [f.question for f in flags if f.type != "missing"]
        if m:
            missing.append({**_item(r), "questions": m})
        if c:
            to_confirm.append({**_item(r), "questions": c})

    today = min(end, datetime.now(_tz()).date())
    actions = []
    for r in reports:
        for a in open_actions(r):
            actions.append(
                {
                    **_item(r),
                    "action_id": a.id,
                    "description": a.description,
                    "owner": a.owner,
                    "deadline": a.deadline,
                    "overdue": _overdue(a.deadline, today),
                }
            )

    intervention = []
    for r in reports:
        reasons: list[str] = []
        if r.state == "failed":
            reasons.append(f"Processing failed: {r.error_message or 'unknown error'}")
        if r.state == "done":
            if status_of(r) == "blocked":
                why = (r.value("issues") or ["no reason reported"])[0]
                reasons.append(f"Blocked: {why}")
            reasons += [f"Conflicting information: {f.question}" for f in outstanding_flags(r) if f.type == "contradiction"]
            reasons += [f"Customer concern: {c}" for c in (r.value("customer_concerns") or [])]
        for a in open_actions(r):
            if not a.owner:
                reasons.append(f"Action has no owner: {a.description}")
            if _overdue(a.deadline, today):
                reasons.append(f"Overdue action ({a.deadline}): {a.description}")
        if reasons:
            intervention.append({**_item(r), "reasons": reasons})

    notes: list[str] = []
    if not reports:
        notes.append("No reports were received for this period.")
    else:
        if stats["failed"]:
            notes.append(f"{stats['failed']} report(s) failed to process and are NOT reflected in the sections below.")
        if stats["processing"]:
            notes.append(f"{stats['processing']} report(s) are still being processed and are NOT reflected yet.")
        if stats["unreviewed"]:
            notes.append(
                f"{stats['unreviewed']} of {stats['processed']} processed report(s) have not been reviewed by a manager. "
                "Their content is unverified AI output."
            )
        if any(r.is_demo for r in reports):
            notes.append("Some reports are demo data built from sample or typed text, not real recordings.")
    notes.append("This summary covers only recordings uploaded to this system. It cannot show jobs that were never uploaded.")

    result = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "generated_at": datetime.now(_tz()).strftime("%Y-%m-%d %H:%M"),
        "timezone": get_settings().timezone,
        "totals": {**stats, "reviewed": stats["processed"] - stats["unreviewed"]},
        "notes": notes,
        "sections": {
            "reported": [_item(r) | {"state": r.state} for r in reports],
            "completed": completed,
            "in_progress": in_progress,
            "blocked_or_partial": blocked,
            "status_not_stated": not_stated,
            "failed": [{**_item(r), "error": r.error_message} for r in failed],
            "missing_information": missing,
            "to_confirm": to_confirm,
            "outstanding_actions": actions,
            "needs_intervention": intervention,
        },
    }
    result["text"] = render_summary_text(result)
    return result


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(get_settings().timezone)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _tag(item: dict[str, Any]) -> str:
    return "[reviewed]" if item["reviewed"] else "[AI, unreviewed]"


def _who(item: dict[str, Any]) -> str:
    return item["technician"] or "technician not stated"


def render_summary_text(s: dict[str, Any]) -> str:
    sec, t = s["sections"], s["totals"]
    period = s["start"] if s["start"] == s["end"] else f"{s['start']} to {s['end']}"
    L: list[str] = [
        "DAILY EXECUTIVE SUMMARY",
        f"Period: {period}",
        f"Generated: {s['generated_at']} ({s['timezone']})",
        "",
        f"Reports received: {t['total_reports']} | Processed: {t['processed']} | Failed: {t['failed']} | "
        f"Still processing: {t['processing']} | Reviewed by manager: {t['reviewed']}",
        "",
        "DATA QUALITY NOTES",
    ]
    L += [f"- {n}" for n in s["notes"]]

    def block(title: str, lines: list[str], empty: str = "None") -> None:
        L.extend(["", title])
        L.extend(lines or [f"- {empty}"])

    block(
        f"1. JOBS REPORTED ({len(sec['reported'])})",
        [f"- {i['label']} ({_who(i)}): {i['status_label'] if i['state'] == 'done' else 'processing ' + i['state']} {_tag(i)}" for i in sec["reported"]],
    )
    block(
        f"2. WORK COMPLETED ({len(sec['completed'])})",
        [f"- {i['label']} ({_who(i)}) {_tag(i)}" + "".join(f"\n    * {w}" for w in i["work"]) for i in sec["completed"]],
    )
    block(
        f"3. JOBS STILL IN PROGRESS ({len(sec['in_progress'])})",
        [f"- {i['label']} ({_who(i)}), {i['status_label']} {_tag(i)}" + "".join(f"\n    * Remaining: {w}" for w in i["remaining"]) for i in sec["in_progress"]],
    )
    block(
        f"4. BLOCKED OR PARTIALLY COMPLETED JOBS ({len(sec['blocked_or_partial'])})",
        [f"- {i['label']} ({_who(i)}), {i['status_label']} {_tag(i)}" + "".join(f"\n    * Reason: {w}" for w in i["reasons"]) for i in sec["blocked_or_partial"]],
    )
    if sec["status_not_stated"]:
        block(
            f"   Status not stated ({len(sec['status_not_stated'])})",
            [f"- {i['label']} ({_who(i)}) {_tag(i)}" for i in sec["status_not_stated"]],
        )
    if sec["failed"]:
        block(
            f"   Failed to process ({len(sec['failed'])})",
            [f"- Report #{i['report_id']}: {i['error'] or 'unknown error'}" for i in sec["failed"]],
        )
    missing_lines = [f"- {i['label']} ({_who(i)}) {_tag(i)}" + "".join(f"\n    * {q}" for q in i["questions"]) for i in sec["missing_information"]]
    confirm_lines = [f"- {i['label']} ({_who(i)}) {_tag(i)}" + "".join(f"\n    * {q}" for q in i["questions"]) for i in sec["to_confirm"]]
    block(f"5. MISSING INFORMATION NEEDING ATTENTION ({len(sec['missing_information'])} report(s))", missing_lines)
    if confirm_lines:
        block(f"   Contradictions, unclear points and items to confirm ({len(sec['to_confirm'])} report(s))", confirm_lines)
    block(
        f"6. OUTSTANDING ACTIONS ({len(sec['outstanding_actions'])})",
        [
            f"- {a['description']} | {a['label']} | Owner: {a['owner'] or 'not stated'} | "
            f"Deadline: {a['deadline'] or 'not stated'}{' (OVERDUE)' if a['overdue'] else ''} {_tag(a)}"
            for a in sec["outstanding_actions"]
        ],
    )
    block(
        f"7. NEEDS MANAGER INTERVENTION ({len(sec['needs_intervention'])})",
        [f"- {i['label']} ({_who(i)})" + "".join(f"\n    * {r}" for r in i["reasons"]) for i in sec["needs_intervention"]],
    )
    return "\n".join(L) + "\n"
