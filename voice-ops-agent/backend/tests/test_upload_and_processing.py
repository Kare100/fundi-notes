"""Audio upload, validation, transcription, retention, provider failures and demo mode."""
from __future__ import annotations

import os
import shutil
from datetime import timedelta

import pytest

from app.database import get_sessionmaker
from app.models import Report, utcnow
from app.services.llm.base import LLMError
from app.services.samples import get_sample
from app.services.transcription.base import NoSpeechDetected, TranscriptionError
from helpers import create_demo, extraction, field, make_wav, wait_done

needs_ffprobe = pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe not installed")


def upload(client, data: bytes, name: str = "note.wav", **extra):
    return client.post("/api/reports/upload", files={"file": (name, data, "application/octet-stream")}, **extra)


# --------------------------------------------------------------------- successful processing
def test_successful_upload_and_processing(client, fake_transcriber, upload_dir):
    sample = get_sample("clean-installation")
    fake_transcriber.text = sample["transcript"]
    fake_transcriber.confidence = 0.93

    r = upload(client, make_wav())
    assert r.status_code == 202
    report = wait_done(client, r.json()["id"])

    assert report["state"] == "done"
    assert report["source_type"] == "audio"
    assert report["source_filename"] == "note.wav"
    assert report["transcript"]["original_text"] == sample["transcript"]
    assert report["transcript"]["provider"] == "fake"
    assert report["transcript"]["uncertain"] is False
    assert report["customer"] == "Greenfield Dental Clinic"
    assert report["status"] == "completed"
    assert "JOB REPORT" in report["report_text"]
    assert fake_transcriber.calls, "the transcription provider must actually have been called"
    # default retention: the audio is gone once processing is finished
    assert not list(upload_dir.glob("*")), "audio should be deleted after processing"
    assert report["audio_available"] is False


def test_keep_days_retention_keeps_audio_behind_authentication(client, anon_client, fake_transcriber, set_env, upload_dir):
    set_env(AUDIO_RETENTION="keep_days", AUDIO_RETENTION_DAYS="3")
    fake_transcriber.text = get_sample("clean-installation")["transcript"]
    report = wait_done(client, upload(client, make_wav()).json()["id"])

    assert report["audio_available"] is True
    assert len(list(upload_dir.glob("*.wav"))) == 1
    assert client.get(f"/api/reports/{report['id']}/audio").status_code == 200
    assert anon_client.get(f"/api/reports/{report['id']}/audio").status_code == 401
    assert "audio_path" not in client.get(f"/api/reports/{report['id']}").text

    # once the retention date passes, the purge job deletes the file
    with get_sessionmaker()() as db:
        db.get(Report, report["id"]).audio_expires_at = utcnow() - timedelta(minutes=1)
        db.commit()
    assert client.post("/api/maintenance/purge-audio").json() == {"purged": 1}
    assert not list(upload_dir.glob("*"))
    assert client.get(f"/api/reports/{report['id']}/audio").status_code == 404


# --------------------------------------------------------------------- upload validation
def test_rejects_unsupported_file_type(client, fake_transcriber, upload_dir):
    r = upload(client, b"MZ\x90\x00 not audio", name="virus.exe")
    assert r.status_code == 415
    assert "Unsupported file type" in r.json()["detail"]
    assert client.get("/api/reports").json() == []
    assert not list(upload_dir.glob("*"))


def test_rejects_file_without_extension(client, fake_transcriber):
    assert upload(client, make_wav(), name="recording").status_code == 415


def test_rejects_empty_file(client, fake_transcriber):
    r = upload(client, b"")
    assert r.status_code == 400
    assert "empty" in r.json()["detail"].lower()


def test_rejects_oversized_file(client, fake_transcriber, set_env, upload_dir):
    set_env(MAX_UPLOAD_MB="1")
    r = upload(client, make_wav(seconds=60, rate=16000))  # about 1.9 MB
    assert r.status_code == 413
    assert "1 MB" in r.json()["detail"]
    assert not list(upload_dir.glob("*")), "partial file must be removed"


def test_rejects_file_that_is_not_really_audio(client, fake_transcriber):
    r = upload(client, os.urandom(2048), name="fake.wav")
    assert r.status_code == 422
    assert "does not look like" in r.json()["detail"]


def test_rejects_renamed_file(client, fake_transcriber):
    r = upload(client, make_wav(), name="actually_a_wav.mp3")
    assert r.status_code == 422


@needs_ffprobe
def test_rejects_corrupted_audio_with_valid_header(client, fake_transcriber, upload_dir):
    r = upload(client, b"RIFF\x00\x00\x00\x00WAVE" + os.urandom(4096))
    assert r.status_code == 422
    assert not list(upload_dir.glob("*"))


@needs_ffprobe
def test_rejects_recording_that_is_too_long(client, fake_transcriber, set_env):
    set_env(MAX_AUDIO_SECONDS="5")
    r = upload(client, make_wav(seconds=8))
    assert r.status_code == 422
    assert "too long" in r.json()["detail"]


@needs_ffprobe
def test_rejects_silent_zero_length_recording(client, fake_transcriber):
    r = upload(client, make_wav(seconds=0.0))
    assert r.status_code == 422


def test_requires_a_file(client, fake_transcriber):
    assert client.post("/api/reports/upload").status_code == 422


# --------------------------------------------------------------------- demo mode (no credentials)
def test_audio_upload_in_demo_mode_is_refused_not_faked(client, upload_dir):
    """With TRANSCRIPTION_PROVIDER=mock the app must not pretend audio was transcribed."""
    r = upload(client, make_wav())
    assert r.status_code == 409
    assert "Demo mode" in r.json()["detail"]
    assert client.get("/api/reports").json() == []
    assert not list(upload_dir.glob("*"))


def test_demo_mode_reports_and_labels_itself(client):
    cfg = client.get("/api/config").json()
    assert cfg["demo"] == {"transcription": True, "llm": True}
    assert "nothing is sent" in " ".join(cfg["data_sharing"]).lower()
    health = client.get("/api/health").json()
    assert health["demo_transcription"] is True and health["demo_llm"] is True


def test_demo_sample_processes_end_to_end_without_credentials(client):
    report = create_demo(client, sample_id="blocked-waiting-for-part")
    assert report["state"] == "done"
    assert report["is_demo"] is True
    assert report["transcript"]["is_demo"] is True
    assert report["status"] == "blocked"
    notes = " ".join(report["job_report"]["source_confidence"]["notes"])
    assert "Demo data" in notes and "mock demo extractor" in notes


def test_demo_with_typed_text_uses_limited_heuristics_and_says_so(client):
    text = "This is Grace Wambui, job 4412. I replaced the faulty contactor and tested the motor."
    report = create_demo(client, text=text)
    assert report["state"] == "done"
    assert report["technician"] == "Grace Wambui"
    assert report["job_reference"] == "4412"
    assert report["customer"] is None
    assert report["status"] == "unknown", "must not infer completion"
    assert any("demo extractor" in f["detail"].lower() for f in report["flags"])
    assert report["transcript"]["original_text"] == text


def test_demo_rejects_bad_requests(client):
    assert client.post("/api/reports/demo", json={"sample_id": "nope"}).status_code == 404
    assert client.post("/api/reports/demo", json={}).status_code == 422
    assert client.post("/api/reports/demo", json={"sample_id": "clean-installation", "text": "x" * 30}).status_code == 422
    assert client.post("/api/reports/demo", json={"text": "too short"}).status_code == 422


# --------------------------------------------------------------------- provider failures
def test_transcription_failure_is_reported_clearly(client, fake_transcriber):
    fake_transcriber.error = TranscriptionError("The transcription service timed out.")
    report = wait_done(client, upload(client, make_wav()).json()["id"])
    assert report["state"] == "failed"
    assert report["error_stage"] == "transcription"
    assert report["error_message"] == "The transcription service timed out."
    assert report["transcript"] is None
    assert report["action_required"] is True


def test_no_speech_detected_fails_without_inventing_a_transcript(client, fake_transcriber):
    fake_transcriber.error = NoSpeechDetected("No speech was detected in this recording.")
    report = wait_done(client, upload(client, make_wav()).json()["id"])
    assert report["state"] == "failed"
    assert report["transcript"] is None
    assert "No speech" in report["error_message"]


def test_audio_is_deleted_even_when_transcription_fails(client, fake_transcriber, upload_dir):
    fake_transcriber.error = TranscriptionError("boom")
    wait_done(client, upload(client, make_wav()).json()["id"])
    assert not list(upload_dir.glob("*"))


def test_low_confidence_transcription_is_flagged_uncertain(client, fake_transcriber):
    fake_transcriber.confidence = 0.31
    report = wait_done(client, upload(client, make_wav()).json()["id"])
    assert report["state"] == "done"
    assert report["transcript"]["uncertain"] is True
    assert report["job_report"]["source_confidence"]["transcription_uncertain"] is True
    assert any("confidence is low" in n for n in report["job_report"]["source_confidence"]["notes"])
    assert any(f["type"] == "confirmation" and "speech-to-text" in f["detail"] for f in report["flags"])


def test_unexpected_transcriber_error_does_not_leak_details(client, fake_transcriber):
    fake_transcriber.error = RuntimeError("secret-api-key-123 leaked in a stack trace")
    report = wait_done(client, upload(client, make_wav()).json()["id"])
    assert report["state"] == "failed"
    assert report["error_stage"] == "internal"
    assert "secret-api-key-123" not in str(report)


def test_llm_failure_keeps_transcript_and_can_be_reprocessed(client, fake_transcriber, fake_llm):
    fake_llm.queue(LLMError("The AI service timed out. Try reprocessing the report."))
    report = wait_done(client, upload(client, make_wav()).json()["id"])
    assert report["state"] == "failed"
    assert report["error_stage"] == "extraction"
    assert report["transcript"]["original_text"] == fake_transcriber.text, "transcript must survive an AI failure"

    fake_llm.replies.clear()
    fake_llm.queue(extraction(customer=field("Acme Ltd", evidence=["at Acme Ltd"])))
    assert client.post(f"/api/reports/{report['id']}/reprocess").status_code == 202
    again = wait_done(client, report["id"])
    assert again["state"] == "done"
    assert again["customer"] == "Acme Ltd"
    assert again["error_message"] is None


def test_unconfigured_provider_gives_safe_error(client, fake_transcriber, monkeypatch):
    from app.config import ConfigurationError
    from app.services import registry

    def boom():
        raise ConfigurationError("ANTHROPIC_API_KEY is not set.")

    monkeypatch.setattr(registry, "get_llm", boom)
    report = wait_done(client, upload(client, make_wav()).json()["id"])
    assert report["state"] == "failed"
    assert "not configured correctly" in report["error_message"]


def test_reprocess_requires_a_transcript(client, fake_transcriber):
    fake_transcriber.error = TranscriptionError("boom")
    report = wait_done(client, upload(client, make_wav()).json()["id"])
    assert client.post(f"/api/reports/{report['id']}/reprocess").status_code == 409
