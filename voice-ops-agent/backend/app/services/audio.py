"""Secure handling of uploaded audio: validation, safe storage, deletion.

Uploads are streamed to disk in chunks (so a huge file never sits in memory), stored under a random
name outside any static file route, and checked for type, size, real audio content and duration.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

from ..config import ALLOWED_AUDIO_EXTENSIONS, Settings

logger = logging.getLogger("voiceops.audio")
CHUNK = 1024 * 1024
MIN_DURATION_SECONDS = 0.3


class AudioValidationError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


@dataclass
class SavedAudio:
    path: Path
    size: int
    extension: str
    duration_seconds: float | None


def _looks_like(ext: str, head: bytes) -> bool:
    if ext == "wav":
        return head[:4] == b"RIFF" and head[8:12] == b"WAVE"
    if ext == "mp3":
        return head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0)
    if ext == "m4a":
        return head[4:8] == b"ftyp"
    if ext == "webm":
        return head[:4] == b"\x1a\x45\xdf\xa3"
    if ext == "ogg":
        return head[:4] == b"OggS"
    return False


def probe_duration(path: Path) -> float | None:
    """Duration in seconds via ffprobe. None when unknown (no ffprobe, or a streamed WebM with no length)."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        logger.warning("ffprobe not found; skipping duration and corruption check")
        return None
    try:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired as exc:
        raise AudioValidationError(422, "The audio file could not be read in time. It may be corrupted.") from exc
    if proc.returncode != 0:
        raise AudioValidationError(422, "The file could not be read as audio. It may be corrupted or incomplete.")
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return None


def delete_file(path: str | Path | None) -> None:
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not delete an uploaded file")


async def save_upload(file: UploadFile, settings: Settings) -> SavedAudio:
    """Validate and store an upload. Raises AudioValidationError with a user-safe message."""
    name = file.filename or ""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in ALLOWED_AUDIO_EXTENSIONS:
        shown = f".{ext}" if ext else "(no extension)"
        raise AudioValidationError(
            415, f"Unsupported file type {shown}. Allowed: {', '.join('.' + e for e in ALLOWED_AUDIO_EXTENSIONS)}."
        )

    upload_dir = settings.upload_dir
    upload_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(upload_dir, 0o700)
    except OSError:
        pass
    path = upload_dir / f"{uuid.uuid4().hex}.{ext}"

    size = 0
    head = b""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as out:
            while chunk := await file.read(CHUNK):
                if not head:
                    head = chunk[:16]
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise AudioValidationError(413, f"File is too large. The limit is {settings.max_upload_mb} MB.")
                out.write(chunk)
        if size == 0:
            raise AudioValidationError(400, "The uploaded file is empty.")
        if not _looks_like(ext, head):
            raise AudioValidationError(422, f"The file does not look like a real .{ext} recording. It may be corrupted or renamed.")
        duration = probe_duration(path)
        if duration is None and ext != "webm" and shutil.which("ffprobe"):
            # Only streamed WebM (browser recordings) may legitimately lack a length.
            raise AudioValidationError(422, "The recording contains no audible content.")
        if duration is not None:
            if duration < MIN_DURATION_SECONDS:
                raise AudioValidationError(422, "The recording contains no audible content.")
            if duration > settings.max_audio_seconds:
                raise AudioValidationError(
                    422,
                    f"The recording is too long ({duration / 60:.1f} min). The limit is {settings.max_audio_seconds / 60:.0f} min.",
                )
    except BaseException:
        delete_file(path)
        raise
    return SavedAudio(path=path, size=size, extension=ext, duration_seconds=duration)
