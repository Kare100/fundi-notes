"""Shared test helpers: fake providers, a WAV generator and a valid-extraction builder."""
from __future__ import annotations

import io
import json
import time
import wave
from pathlib import Path

from app.services.llm.base import LLMProvider
from app.services.transcription.base import TranscriptionProvider, TranscriptionResult


def make_wav(seconds: float = 1.0, rate: int = 8000) -> bytes:
    """A real, valid PCM WAV file (silence)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


class FakeTranscriber(TranscriptionProvider):
    name = "fake"

    def __init__(self) -> None:
        self.text = "This is Peter Otieno reporting on job JOB-1 at Acme Ltd. I finished installing the pump."
        self.confidence: float | None = None
        self.error: Exception | None = None
        self.calls: list[Path] = []

    def transcribe(self, path, *, language=None, prompt=None) -> TranscriptionResult:
        self.calls.append(Path(path))
        assert Path(path).exists(), "audio must exist while transcribing"
        if self.error:
            raise self.error
        return TranscriptionResult(text=self.text, language="en", confidence=self.confidence)


class FakeLLM(LLMProvider):
    """Returns queued replies in order (strings are returned, Exceptions are raised)."""

    name = "fake-llm"

    def __init__(self) -> None:
        self.replies: list[str | Exception] = []
        self.calls: list[tuple[str, str]] = []

    def queue(self, *replies: str | Exception | dict) -> "FakeLLM":
        self.replies.extend(json.dumps(r) if isinstance(r, dict) else r for r in replies)
        return self

    def complete_json(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if not self.replies:
            raise AssertionError("FakeLLM has no queued reply")
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply

    @property
    def last_user_prompt(self) -> str:
        return self.calls[-1][1]


def field(value=None, basis="stated", evidence=None) -> dict:
    return {"value": value, "basis": basis, "evidence": evidence or []}


def extraction(**overrides) -> dict:
    """A minimal valid extraction. Pass keys to override, e.g. customer=field("Acme Ltd", evidence=["at Acme Ltd"])."""
    data = {
        "technician": field(),
        "customer": field(),
        "job_reference": field(),
        "activity_date": field(),
        "location": field(),
        "job_status": field("unknown"),
        "work_performed": field([]),
        "equipment": field([]),
        "parts_materials": field([]),
        "issues": field([]),
        "outstanding_work": field([]),
        "customer_concerns": field([]),
        "testing_confirmed": field(),
        "actions": [],
        "contradictions": [],
        "ambiguities": [],
        "summary": "A short summary.",
    }
    data.update(overrides)
    return data


def wait_done(client, report_id: int, timeout: float = 5.0) -> dict:
    """Reports process in a background task; TestClient finishes it before returning, but poll to be safe."""
    deadline = time.time() + timeout
    while True:
        body = client.get(f"/api/reports/{report_id}").json()
        if body["state"] in ("done", "failed") or time.time() > deadline:
            return body
        time.sleep(0.05)


def create_demo(client, sample_id: str | None = None, text: str | None = None, reported_on: str | None = None) -> dict:
    payload = {"sample_id": sample_id} if sample_id else {"text": text}
    if reported_on:
        payload["reported_on"] = reported_on
    r = client.post("/api/reports/demo", json=payload)
    assert r.status_code == 202, r.text
    return wait_done(client, r.json()["id"])
