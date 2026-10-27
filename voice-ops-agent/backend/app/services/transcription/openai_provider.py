"""OpenAI speech-to-text adapter (audio file is sent to OpenAI)."""
from __future__ import annotations

import logging
import math
from pathlib import Path

import httpx

from .base import NoSpeechDetected, TranscriptionError, TranscriptionProvider, TranscriptionResult

logger = logging.getLogger("voiceops.transcription")

URL = "https://api.openai.com/v1/audio/transcriptions"
MIME = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "m4a": "audio/mp4",
    "webm": "audio/webm",
    "ogg": "audio/ogg",
}


class OpenAITranscriber(TranscriptionProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str = "whisper-1", client: httpx.Client | None = None, timeout: float = 180):
        self._key = api_key
        self.model = model
        self._client = client or httpx.Client(timeout=timeout)

    def transcribe(self, path: Path, *, language: str | None = None, prompt: str | None = None) -> TranscriptionResult:
        verbose = self.model == "whisper-1"  # only whisper-1 returns per segment confidence data
        data = {"model": self.model, "response_format": "verbose_json" if verbose else "json"}
        if language:
            data["language"] = language
        if prompt:
            data["prompt"] = prompt
        mime = MIME.get(path.suffix.lstrip(".").lower(), "application/octet-stream")
        try:
            with open(path, "rb") as fh:
                resp = self._client.post(
                    URL, headers={"Authorization": f"Bearer {self._key}"}, data=data, files={"file": (path.name, fh, mime)}
                )
        except httpx.TimeoutException as exc:
            raise TranscriptionError("The transcription service timed out. Try again, or upload a shorter recording.") from exc
        except httpx.HTTPError as exc:
            raise TranscriptionError("Could not reach the transcription service. Check the server's internet connection.") from exc

        if resp.status_code in (401, 403):
            raise TranscriptionError("The transcription service rejected the API key. Check OPENAI_API_KEY.")
        if resp.status_code == 429:
            raise TranscriptionError("The transcription service is rate limited or out of quota. Try again later.")
        if resp.status_code == 413:
            raise TranscriptionError("The transcription service says the file is too large.")
        if resp.status_code == 400:
            raise TranscriptionError("The transcription service could not read this audio. It may be corrupted or unsupported.")
        if resp.status_code >= 400:
            logger.warning("Transcription provider returned HTTP %s", resp.status_code)
            raise TranscriptionError(f"The transcription service returned an error (HTTP {resp.status_code}).")

        try:
            payload = resp.json()
        except ValueError as exc:
            raise TranscriptionError("The transcription service returned an unreadable response.") from exc

        text = (payload.get("text") or "").strip()
        segments = payload.get("segments") or []
        if segments and all(float(s.get("no_speech_prob", 0)) > 0.8 for s in segments):
            text = ""
        if not text:
            raise NoSpeechDetected("No speech was detected in this recording.")

        confidence = None
        probs = [math.exp(float(s["avg_logprob"])) for s in segments if "avg_logprob" in s]
        if probs:
            confidence = round(sum(probs) / len(probs), 3)
        return TranscriptionResult(text=text, language=payload.get("language"), confidence=confidence)
