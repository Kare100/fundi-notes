"""The processing pipeline: transcript -> LLM extraction -> safety guards -> stored fields, flags, actions.

Safety guards (the "do not invent anything" rules) are enforced here in code, not just in the prompt:
* names, references and locations must appear in the transcript, otherwise they are dropped
* equipment and parts must be traceable to words in the transcript, otherwise they are dropped
* a job is only "completed" when a quote supporting it is found in the transcript and it was stated
* owners and deadlines must appear in the transcript, otherwise they are dropped
* every supporting quote is checked word for word against the transcript and marked verified or not
Whenever a guard removes something, a flag tells the manager what the AI proposed and why it was removed.
"""
from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import ConfigurationError, Settings, get_settings, local_today
from ..database import get_sessionmaker
from ..extraction_schema import ExtractionResult, ExtractionValidationError, parse_extraction
from ..fields import ENTITY_KEYS, FIELD_BY_KEY, FIELD_DEFS, STATUS_LABELS, SUPPORTED_ITEM_KEYS, clean_value, is_empty
from ..models import Action, ExtractedField, Flag, Report, Transcript, utcnow
from . import audio as audio_service
from . import registry
from .checker import sync_rule_flags
from .llm.base import LLMError, LLMProvider
from .prompts import SYSTEM_PROMPT, build_repair_prompt, build_user_prompt
from .text_utils import entity_supported, item_supported, norm, redact_contact_details, verify_evidence
from .transcription.base import TranscriptionError

logger = logging.getLogger("voiceops.pipeline")


class ProcessingError(Exception):
    """A pipeline stage failed. `message` is safe to show to the user."""

    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage
        self.message = message


# ----------------------------------------------------------------------------------- creation
def create_report(
    db: Session,
    *,
    source_type: str,
    state: str,
    reported_on: date | None = None,
    filename: str | None = None,
    sample_id: str | None = None,
    is_demo: bool = False,
) -> Report:
    report = Report(
        source_type=source_type,
        state=state,
        reported_on=reported_on or local_today(),
        source_filename=filename,
        sample_id=sample_id,
        is_demo=is_demo,
    )
    db.add(report)
    db.flush()
    return report


def attach_transcript(
    db: Session,
    report: Report,
    *,
    text: str,
    provider: str,
    language: str | None = None,
    confidence: float | None = None,
    uncertain: bool = False,
    warnings: list[str] | None = None,
    is_demo: bool = False,
) -> None:
    report.transcript = Transcript(
        original_text=text,
        provider=provider,
        language=language,
        confidence=confidence,
        uncertain=uncertain,
        warnings=warnings or [],
        is_demo=is_demo,
    )
    db.flush()


def create_text_report(db: Session, *, text: str, sample_id: str | None, reported_on: date | None) -> Report:
    """Demo path: a report from sample or typed text. No audio and no speech-to-text service is involved."""
    report = create_report(
        db,
        source_type="demo_sample" if sample_id else "typed_text",
        state="extracting",
        reported_on=reported_on,
        sample_id=sample_id,
        is_demo=True,
    )
    report.transcription_provider = "demo-text"
    attach_transcript(db, report, text=text, provider="demo-text", is_demo=True)
    db.commit()
    return report


# ----------------------------------------------------------------------------------- LLM step
def call_llm(llm: LLMProvider, transcript_text: str, settings: Settings) -> tuple[ExtractionResult, str]:
    """Call the LLM and validate its output. One automatic repair attempt on a schema failure."""
    sent = transcript_text
    if settings.redact_contact_details:
        sent, _ = redact_contact_details(sent)
    user = build_user_prompt(sent)
    raw = llm.complete_json(SYSTEM_PROMPT, user)
    try:
        return parse_extraction(raw), sent
    except ExtractionValidationError as first:
        logger.warning("LLM output failed validation, retrying once: %s", first)
        raw = llm.complete_json(SYSTEM_PROMPT, build_repair_prompt(user, str(first)))
        return parse_extraction(raw), sent  # a second failure propagates


@dataclass
class _Guard:
    field_key: str | None
    detail: str
    question: str
    action: Action | None = None
    kind: str = "field"  # field | owner | deadline


def apply_extraction(db: Session, report: Report, result: ExtractionResult, sent_text: str, llm_name: str) -> None:
    """Validate against the transcript and persist fields, actions and flags (replacing earlier AI output)."""
    t_norm = norm(sent_text)
    report.fields.clear()
    report.flags.clear()
    report.actions.clear()
    db.flush()

    guards: list[_Guard] = []

    for fd in FIELD_DEFS:
        ext = getattr(result, fd.key)
        value = clean_value(fd.kind, ext.value)
        evidence = verify_evidence(ext.evidence, t_norm)
        has_verified_quote = any(e["verified"] for e in evidence)
        basis = ext.basis

        if fd.key in ENTITY_KEYS and value and not entity_supported(value, t_norm):
            guards.append(
                _Guard(
                    fd.key,
                    f'The AI proposed {fd.label.lower()} "{value}", but it was not found in the transcript, so it was left blank.',
                    f"Please confirm the {fd.label.lower()}.",
                )
            )
            value, evidence = None, []

        if fd.key in SUPPORTED_ITEM_KEYS and value:
            kept = [i for i in value if item_supported(i, t_norm)]
            removed = [i for i in value if i not in kept]
            if removed:
                guards.append(
                    _Guard(
                        fd.key,
                        f"The AI listed {', '.join(repr(r) for r in removed)} under {fd.label.lower()}, but this was not found in the transcript, so it was removed.",
                        f"Were these actually used or involved: {', '.join(removed)}?",
                    )
                )
            value = kept

        if fd.key == "job_status" and value != "unknown":
            if not has_verified_quote:
                guards.append(
                    _Guard(
                        "job_status",
                        f'The AI suggested the status "{STATUS_LABELS[value]}", but no supporting statement was found in the transcript, so the status was left as not stated.',
                        "What is the current status of this job?",
                    )
                )
                value, evidence = "unknown", []
            elif value == "completed" and basis != "stated":
                guards.append(
                    _Guard(
                        "job_status",
                        "The AI inferred that the job is complete, but the technician did not clearly say so, so the status was left as not stated.",
                        "Did the technician confirm that the whole job is finished?",
                    )
                )
                value, evidence = "unknown", []

        if is_empty(value) or (fd.kind == "enum" and value == "unknown"):
            basis, evidence = "missing", []
        elif not has_verified_quote:
            basis = "interpreted"

        report.fields.append(
            ExtractedField(
                key=fd.key,
                ai_value=copy.deepcopy(value),
                value=copy.deepcopy(value),
                basis=basis,
                evidence=evidence,
                review_state="unreviewed",
            )
        )

    new_actions: list[Action] = []
    for a in result.actions:
        owner = clean_value("text", a.owner)
        deadline = clean_value("text", a.deadline)
        description = " ".join(a.description.split())
        row = Action(description=description, source="ai", evidence=verify_evidence(a.evidence, t_norm))
        if owner and not entity_supported(owner, t_norm):
            guards.append(
                _Guard(None, f'The AI named "{owner}" as responsible for "{description}", but this is not in the transcript, so no owner was set.',
                       f"Who is responsible for: {description}", row, "owner")
            )
            owner = None
        if deadline and not entity_supported(deadline, t_norm):
            guards.append(
                _Guard(None, f'The AI proposed the deadline "{deadline}" for "{description}", but no such deadline was stated, so it was left blank.',
                       f"Is there a deadline for: {description}", row, "deadline")
            )
            deadline = None
        row.owner, row.deadline = owner, deadline
        report.actions.append(row)
        new_actions.append(row)
    db.flush()  # assigns action ids needed by the checker

    sync_rule_flags(db, report)

    # Merge guard notes into an existing flag on the same field, or raise a confirmation flag.
    open_by_field = {f.field_key: f for f in report.flags if f.field_key and f.state == "outstanding"}
    for n, g in enumerate(guards):
        if g.kind == "owner" and g.action is not None:
            owner_flag = next((f for f in report.flags if f.code == f"missing:action_owner:{g.action.id}"), None)
            if owner_flag:
                owner_flag.detail = f"{owner_flag.detail} {g.detail}"
                continue
        target = open_by_field.get(g.field_key) if g.field_key else None
        if target:
            target.detail = f"{target.detail} {g.detail}"
            continue
        flag = Flag(
            code=f"guard:{g.field_key or 'action'}:{n}",
            type="confirmation",
            field_key=g.field_key,
            severity="medium",
            detail=g.detail,
            question=g.question,
            origin="ai",
        )
        report.flags.append(flag)
        if g.field_key:
            open_by_field[g.field_key] = flag

    # The AI's own contradictions and ambiguities, skipping fields that already carry a question.
    flagged = {f.field_key for f in report.flags if f.field_key and f.state == "outstanding"}
    n = 0
    for kind, items in (("contradiction", result.contradictions), ("ambiguous", result.ambiguities)):
        for item in items:
            key = item.field if item.field in FIELD_BY_KEY else None
            if key and key in flagged:
                continue
            report.flags.append(
                Flag(
                    code=f"ai:{kind}:{n}",
                    type=kind,
                    field_key=key,
                    severity="high" if kind == "contradiction" else "medium",
                    detail=item.detail.strip(),
                    question=(item.question or f"Please clarify: {item.detail}").strip(),
                    evidence=verify_evidence(item.evidence, t_norm),
                    origin="ai",
                )
            )
            if key:
                flagged.add(key)
            n += 1

    t = report.transcript
    if t is not None and t.uncertain and not t.is_demo:
        report.flags.append(
            Flag(
                code="confirmation:low_transcription_confidence",
                type="confirmation",
                field_key=None,
                severity="medium",
                detail="The speech-to-text service reported low confidence in this transcription.",
                question="Please check the key facts in this report against the recording, or ask the technician to confirm them.",
                origin="rule",
            )
        )

    report.summary = report.summary_ai = result.summary.strip()
    report.llm_provider = llm_name
    report.state = "done"
    report.error_stage = report.error_message = None
    db.flush()


def extract_and_store(db: Session, report: Report) -> None:
    settings = get_settings()
    if report.transcript is None:
        raise ProcessingError("extraction", "There is no transcript to analyse.")
    try:
        llm = registry.get_llm()
        result, sent = call_llm(llm, report.transcript.original_text, settings)
    except ConfigurationError as exc:
        raise ProcessingError("extraction", "The AI provider is not configured correctly. Check backend/.env.") from exc
    except LLMError as exc:
        raise ProcessingError("extraction", str(exc)) from exc
    except ExtractionValidationError as exc:
        raise ProcessingError(
            "extraction_validation",
            f"{exc} The transcript is saved; you can use Reprocess to try again.",
        ) from exc
    apply_extraction(db, report, result, sent, llm.name)


# ----------------------------------------------------------------------------------- orchestration
def _fail(db: Session, report: Report | None, stage: str, message: str) -> None:
    if report is None:
        return
    report.state = "failed"
    report.error_stage = stage
    report.error_message = message
    db.commit()


def _transcribe(db: Session, report: Report, audio_path: str, settings: Settings) -> None:
    try:
        transcriber = registry.get_transcriber()
    except ConfigurationError as exc:
        raise ProcessingError("transcription", "The transcription provider is not configured correctly. Check backend/.env.") from exc
    try:
        res = transcriber.transcribe(
            Path(audio_path),
            language=settings.transcribe_language or None,
            prompt=settings.transcribe_prompt or None,
        )
    except TranscriptionError as exc:
        raise ProcessingError("transcription", str(exc)) from exc
    text = (res.text or "").strip()
    if not text:
        raise ProcessingError("transcription", "No speech was detected in this recording.")
    warnings = list(res.warnings)
    if len(text.split()) < 6:
        warnings.append("Very short transcript: the note may be incomplete or mostly silence.")
    uncertain = res.confidence is not None and res.confidence < settings.transcription_min_confidence
    report.transcription_provider = transcriber.name
    attach_transcript(
        db,
        report,
        text=text,
        provider=transcriber.name,
        language=res.language,
        confidence=res.confidence,
        uncertain=uncertain,
        warnings=warnings,
        is_demo=transcriber.is_demo,
    )
    report.state = "extracting"
    db.commit()


def run_processing(report_id: int, audio_path: str | None = None) -> None:
    """Background job. Safe to call for new audio (transcribe first) or for an existing transcript (reprocess)."""
    settings = get_settings()
    with get_sessionmaker()() as db:
        report = db.get(Report, report_id)
        if report is None:
            return
        try:
            if report.transcript is None:
                if not audio_path:
                    raise ProcessingError("transcription", "There is no transcript or audio for this report.")
                _transcribe(db, report, audio_path, settings)
            extract_and_store(db, report)
            db.commit()
        except ProcessingError as exc:
            db.rollback()
            _fail(db, db.get(Report, report_id), exc.stage, exc.message)
        except Exception:  # noqa: BLE001 - last resort, never leak details to the client
            logger.exception("Unexpected error while processing report %s", report_id)
            db.rollback()
            _fail(db, db.get(Report, report_id), "internal", "Unexpected error while processing this report. Check the server log.")
        finally:
            _finish_audio(db, report_id, audio_path, settings)


def _finish_audio(db: Session, report_id: int, audio_path: str | None, settings: Settings) -> None:
    """Apply the retention policy to the uploaded audio once processing is over (success or failure)."""
    if not audio_path:
        return
    report = db.get(Report, report_id)
    if report is not None and settings.audio_retention == "keep_days":
        report.audio_path = audio_path
        report.audio_expires_at = utcnow() + timedelta(days=settings.audio_retention_days)
        db.commit()
    else:
        audio_service.delete_file(audio_path)


# ----------------------------------------------------------------------------------- maintenance
def mark_interrupted(db: Session) -> int:
    """After a crash or restart, reports stuck mid-processing can never finish: mark them failed."""
    stuck = db.scalars(select(Report).where(Report.state.in_(("transcribing", "extracting")))).all()
    for r in stuck:
        r.state, r.error_stage = "failed", "internal"
        r.error_message = "Processing was interrupted by a server restart."
    db.commit()
    return len(stuck)


def purge_expired_audio(db: Session) -> int:
    now = utcnow()
    expired = db.scalars(select(Report).where(Report.audio_path.is_not(None), Report.audio_expires_at <= now)).all()
    for r in expired:
        audio_service.delete_file(r.audio_path)
        r.audio_path = r.audio_expires_at = None
    db.commit()
    return len(expired)
