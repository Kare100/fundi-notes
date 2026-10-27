"""Report endpoints: create (upload or demo), list, inspect, edit, flags, actions, reprocess, delete."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..config import ConfigurationError, Settings, get_settings
from ..database import get_db
from ..fields import FIELD_BY_KEY, JOB_STATUSES, clean_value
from ..models import Action, ExtractedField, Flag, Report, utcnow
from ..schemas import (
    ActionCreate,
    ActionPatch,
    DemoCreate,
    FieldPatch,
    FlagPatch,
    ReportDetail,
    ReportListItem,
    ReportPatch,
    StatsOut,
)
from ..services import audio as audio_service
from ..services import registry, samples, views
from ..services.checker import sync_rule_flags
from ..services.pipeline import create_report, create_text_report, purge_expired_audio, run_processing
from ..services.summary import LOAD, build_summary, compute_stats, load_reports

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)], tags=["reports"])

DEMO_UPLOAD_MESSAGE = (
    "Demo mode: no speech-to-text service is configured, so this audio cannot be transcribed and nothing was processed. "
    "Pick a sample transcript instead, or set TRANSCRIPTION_PROVIDER and its API key in backend/.env."
)


# ----------------------------------------------------------------------------------- helpers
def get_report_or_404(db: Session, report_id: int) -> Report:
    report = db.scalars(select(Report).options(*LOAD).where(Report.id == report_id)).first()
    if report is None:
        raise HTTPException(404, "Report not found.")
    return report


def detail(report: Report) -> dict[str, Any]:
    d = views.list_item(report)
    d.update(
        {
            "updated_at": report.updated_at,
            "source_type": report.source_type,
            "source_filename": report.source_filename,
            "sample_id": report.sample_id,
            "error_stage": report.error_stage,
            "transcription_provider": report.transcription_provider,
            "llm_provider": report.llm_provider,
            "summary_ai": report.summary_ai,
            "transcript": report.transcript,
            "fields": views.field_rows(report),
            "flags": sorted(report.flags, key=lambda f: ({"high": 0, "medium": 1, "low": 2}.get(f.severity, 3), f.id)),
            "actions": report.actions,
            "job_report": views.job_report(report),
            "report_text": views.render_report_text(report),
            "audio_available": bool(report.audio_path and Path(report.audio_path).exists()),
        }
    )
    return ReportDetail.model_validate(d).model_dump(mode="json")


def commit_and_detail(db: Session, report: Report) -> dict[str, Any]:
    if report.state == "done":
        sync_rule_flags(db, report)
    db.commit()
    db.refresh(report)
    report = get_report_or_404(db, report.id)
    return detail(report)


def require_done(report: Report) -> None:
    if report.state != "done":
        raise HTTPException(409, "This report has not been processed successfully, so it has no fields to edit.")


def apply_field_value(report: Report, key: str, value: Any, review_state: str | None) -> None:
    fd = FIELD_BY_KEY[key]
    row = report.field(key)
    if row is None:
        row = ExtractedField(key=key, ai_value=None, value=None, basis="missing", evidence=[], review_state="unreviewed")
        report.fields.append(row)
    cleaned = clean_value(fd.kind, value)
    changed = cleaned != row.value
    row.value = cleaned
    if review_state:
        row.review_state = review_state
    elif changed:
        row.review_state = "confirmed" if cleaned == row.ai_value else "corrected"
    if changed:
        row.edited_at = utcnow()


# ----------------------------------------------------------------------------------- create
@router.post("/reports/upload", status_code=202)
async def upload_report(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    reported_on: date | None = Form(default=None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    try:
        saved = await audio_service.save_upload(file, settings)
    except audio_service.AudioValidationError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    try:
        transcriber = registry.get_transcriber()
    except ConfigurationError as exc:
        audio_service.delete_file(saved.path)
        raise HTTPException(503, "The transcription provider is not configured correctly. Check backend/.env.") from exc
    if transcriber.is_demo:
        audio_service.delete_file(saved.path)
        raise HTTPException(409, DEMO_UPLOAD_MESSAGE)

    report = create_report(
        db,
        source_type="audio",
        state="transcribing",
        reported_on=reported_on,
        filename=Path(file.filename or "recording").name[:255],
    )
    db.commit()
    background.add_task(run_processing, report.id, str(saved.path))
    return {"id": report.id, "state": report.state}


@router.post("/reports/demo", status_code=202)
def create_demo_report(body: DemoCreate, background: BackgroundTasks, db: Session = Depends(get_db)):
    """Demo path: process a built-in sample (or typed text). No audio and no speech-to-text service is involved."""
    if body.sample_id:
        sample = samples.get_sample(body.sample_id)
        if sample is None:
            raise HTTPException(404, "Unknown sample.")
        text, sample_id = sample["transcript"], sample["id"]
    else:
        text, sample_id = (body.text or "").strip(), None
    report = create_text_report(db, text=text, sample_id=sample_id, reported_on=body.reported_on)
    background.add_task(run_processing, report.id, None)
    return {"id": report.id, "state": report.state}


# ----------------------------------------------------------------------------------- read
@router.get("/reports", response_model=list[ReportListItem])
def list_reports(
    search: str | None = None,
    status: str | None = Query(default=None, description="A job status, or 'failed' / 'processing'"),
    technician: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    missing_info: bool | None = None,
    action_required: bool | None = None,
    db: Session = Depends(get_db),
):
    rows = []
    needle = (search or "").strip().lower()
    for r in load_reports(db, date_from, date_to):
        item = views.list_item(r)
        if status:
            if status == "failed":
                ok = r.state == "failed"
            elif status == "processing":
                ok = r.state in ("transcribing", "extracting")
            else:
                ok = r.state == "done" and item["status"] == status
            if not ok:
                continue
        if technician and (item["technician"] or "").lower() != technician.lower():
            continue
        if missing_info is not None and item["missing_info"] != missing_info:
            continue
        if action_required is not None and item["action_required"] != action_required:
            continue
        if needle:
            haystack = " ".join(
                str(x or "")
                for x in (
                    item["label"], item["customer"], item["technician"], item["job_reference"], item["location"],
                    item["summary"], r.transcript.original_text if r.transcript else "",
                )
            ).lower()
            if needle not in haystack:
                continue
        rows.append(item)
    return rows


@router.get("/technicians")
def list_technicians(db: Session = Depends(get_db)):
    names = {v for r in load_reports(db) if (v := r.value("technician"))}
    return sorted(names, key=str.lower)


@router.get("/stats", response_model=StatsOut)
def stats(date_from: date | None = None, date_to: date | None = None, db: Session = Depends(get_db)):
    return compute_stats(load_reports(db, date_from, date_to))


@router.get("/reports/{report_id}", response_model=ReportDetail)
def get_report(report_id: int, db: Session = Depends(get_db)):
    return detail(get_report_or_404(db, report_id))


@router.get("/reports/{report_id}/audio")
def get_audio(report_id: int, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    """Authenticated playback of retained audio. Only exists when AUDIO_RETENTION=keep_days."""
    report = get_report_or_404(db, report_id)
    path = Path(report.audio_path) if report.audio_path else None
    upload_dir = settings.upload_dir.resolve()
    if path is None or not path.exists() or upload_dir not in path.resolve().parents:
        raise HTTPException(404, "The original audio is not available (deleted according to the retention policy).")
    return FileResponse(path, headers={"Cache-Control": "no-store"})


# ----------------------------------------------------------------------------------- edit
@router.patch("/reports/{report_id}", response_model=ReportDetail)
def patch_report(report_id: int, body: ReportPatch, db: Session = Depends(get_db)):
    report = get_report_or_404(db, report_id)
    sent = body.model_fields_set
    if "job_status" in sent and body.job_status is not None:
        require_done(report)
        apply_field_value(report, "job_status", body.job_status, None)
    if "reported_on" in sent and body.reported_on is not None:
        report.reported_on = body.reported_on
    if "summary" in sent and body.summary is not None:
        require_done(report)
        report.summary = body.summary.strip()
    if "reviewed" in sent and body.reviewed is not None:
        report.reviewed = body.reviewed
        report.reviewed_at = utcnow() if body.reviewed else None
    return commit_and_detail(db, report)


@router.patch("/reports/{report_id}/fields/{key}", response_model=ReportDetail)
def patch_field(report_id: int, key: str, body: FieldPatch, db: Session = Depends(get_db)):
    if key not in FIELD_BY_KEY:
        raise HTTPException(404, "Unknown field.")
    report = get_report_or_404(db, report_id)
    require_done(report)
    if "value" in body.model_fields_set:
        if key == "job_status" and body.value not in JOB_STATUSES:
            raise HTTPException(422, f"Status must be one of: {', '.join(JOB_STATUSES)}.")
        apply_field_value(report, key, body.value, body.review_state)
    elif body.review_state:
        row = report.field(key)
        if row is None:
            raise HTTPException(404, "Field has no value to review.")
        row.review_state = body.review_state
    else:
        raise HTTPException(422, "Provide a value or a review_state.")
    return commit_and_detail(db, report)


@router.patch("/flags/{flag_id}", response_model=ReportDetail)
def patch_flag(flag_id: int, body: FlagPatch, db: Session = Depends(get_db)):
    flag = db.get(Flag, flag_id)
    if flag is None:
        raise HTTPException(404, "Item not found.")
    flag.state = body.state
    flag.note = body.note if body.note is not None else flag.note
    if body.state == "outstanding":
        flag.resolved_by, flag.resolved_at = None, None
    else:
        flag.resolved_by, flag.resolved_at = "manager", utcnow()
    return commit_and_detail(db, get_report_or_404(db, flag.report_id))


@router.post("/reports/{report_id}/actions", response_model=ReportDetail, status_code=201)
def add_action(report_id: int, body: ActionCreate, db: Session = Depends(get_db)):
    report = get_report_or_404(db, report_id)
    report.actions.append(
        Action(
            description=" ".join(body.description.split()),
            owner=clean_value("text", body.owner),
            deadline=clean_value("text", body.deadline),
            source="manager",
        )
    )
    db.flush()
    return commit_and_detail(db, report)


@router.patch("/actions/{action_id}", response_model=ReportDetail)
def patch_action(action_id: int, body: ActionPatch, db: Session = Depends(get_db)):
    action = db.get(Action, action_id)
    if action is None:
        raise HTTPException(404, "Action not found.")
    sent = body.model_fields_set
    if "description" in sent and body.description:
        action.description = " ".join(body.description.split())
    if "owner" in sent:
        action.owner = clean_value("text", body.owner)
    if "deadline" in sent:
        action.deadline = clean_value("text", body.deadline)
    if "completed" in sent and body.completed is not None:
        action.completed = body.completed
        action.completed_at = utcnow() if body.completed else None
    return commit_and_detail(db, get_report_or_404(db, action.report_id))


@router.delete("/actions/{action_id}", response_model=ReportDetail)
def delete_action(action_id: int, db: Session = Depends(get_db)):
    action = db.get(Action, action_id)
    if action is None:
        raise HTTPException(404, "Action not found.")
    report = get_report_or_404(db, action.report_id)
    report.actions.remove(action)
    db.flush()
    return commit_and_detail(db, report)


# ----------------------------------------------------------------------------------- lifecycle
@router.post("/reports/{report_id}/reprocess", status_code=202)
def reprocess(report_id: int, background: BackgroundTasks, db: Session = Depends(get_db)):
    """Re-run extraction on the saved transcript. Replaces AI output and DISCARDS manager edits."""
    report = get_report_or_404(db, report_id)
    if report.transcript is None:
        raise HTTPException(409, "There is no saved transcript to reprocess. Upload the recording again.")
    if report.state in ("transcribing", "extracting"):
        raise HTTPException(409, "This report is already being processed.")
    report.state, report.error_stage, report.error_message = "extracting", None, None
    db.commit()
    background.add_task(run_processing, report.id, None)
    return {"id": report.id, "state": report.state}


@router.delete("/reports/{report_id}", status_code=204)
def delete_report(report_id: int, db: Session = Depends(get_db)):
    report = get_report_or_404(db, report_id)
    audio_service.delete_file(report.audio_path)
    db.delete(report)
    db.commit()
    return Response(status_code=204)


@router.post("/maintenance/purge-audio")
def purge_audio(db: Session = Depends(get_db)):
    return {"purged": purge_expired_audio(db)}


# ----------------------------------------------------------------------------------- summary
@router.get("/summary")
def daily_summary(
    day: date = Query(alias="date", description="Start date, YYYY-MM-DD"),
    end: date | None = Query(default=None, description="Optional end date for a multi-day period"),
    db: Session = Depends(get_db),
):
    if end is not None and end < day:
        raise HTTPException(422, "End date cannot be before the start date.")
    return build_summary(db, day, end)
