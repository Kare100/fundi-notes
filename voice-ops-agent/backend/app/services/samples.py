"""Built-in sample transcripts for demo mode (and for seeding the sample dataset)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..config import get_settings
from .text_utils import norm


@lru_cache
def _load(path: str) -> tuple[dict[str, Any], ...]:
    return tuple(json.loads(Path(path).read_text(encoding="utf-8")))


def load_samples() -> list[dict[str, Any]]:
    return list(_load(str(get_settings().samples_path)))


def get_sample(sample_id: str) -> dict[str, Any] | None:
    return next((s for s in load_samples() if s["id"] == sample_id), None)


def find_sample_by_text(text: str) -> dict[str, Any] | None:
    target = norm(text)
    return next((s for s in load_samples() if norm(s["transcript"]) == target), None)


def public_list() -> list[dict[str, Any]]:
    """Sample metadata for the UI. The canned extraction is deliberately not exposed."""
    return [
        {"id": s["id"], "title": s["title"], "description": s["description"], "transcript": s["transcript"]}
        for s in load_samples()
    ]
