"""Demo mode transcriber.

It NEVER returns text. Audio is not transcribed in demo mode, and the application must not pretend
otherwise. Demo mode works on built-in sample transcripts instead (see services/samples.py).
"""
from __future__ import annotations

from pathlib import Path

from .base import TranscriptionProvider, TranscriptionResult, TranscriptionUnavailable


class DemoTranscriber(TranscriptionProvider):
    name = "demo"
    is_demo = True

    def transcribe(self, path: Path, *, language: str | None = None, prompt: str | None = None) -> TranscriptionResult:
        raise TranscriptionUnavailable(
            "Demo mode: no speech-to-text service is configured, so audio cannot be transcribed. "
            "Pick a sample transcript instead, or set TRANSCRIPTION_PROVIDER and its API key in backend/.env."
        )
