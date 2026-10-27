"""Speech-to-text adapter interface.

To add a provider: subclass TranscriptionProvider, implement `transcribe`, and register it in
`app/services/registry.py`. Nothing else in the application needs to change.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


class TranscriptionError(Exception):
    """Transcription failed. The message is safe to show to users (no keys, no file content)."""


class TranscriptionUnavailable(TranscriptionError):
    """No real transcription service is configured (demo mode)."""


class NoSpeechDetected(TranscriptionError):
    """The audio was processed but contained no recognisable speech."""


@dataclass
class TranscriptionResult:
    text: str
    language: str | None = None
    confidence: float | None = None  # 0..1 when the provider can tell, else None
    warnings: list[str] = field(default_factory=list)


class TranscriptionProvider(ABC):
    name: str = "base"
    is_demo: bool = False

    @abstractmethod
    def transcribe(self, path: Path, *, language: str | None = None, prompt: str | None = None) -> TranscriptionResult:
        """Return the transcript of the audio at `path`, or raise TranscriptionError."""
