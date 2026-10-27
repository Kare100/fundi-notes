"""DEMO MODE extractor. This is NOT an AI model.

* For the built-in sample transcripts it returns the pre-written extraction stored in samples.json.
* For any other text it runs a few conservative regular expressions and leaves everything else unknown.

It exists so the whole application can be run and tested without API keys. The UI labels reports
produced by it as demo data.
"""
from __future__ import annotations

import json
import re

from ..samples import find_sample_by_text
from .base import LLMProvider

_TRANSCRIPT_RE = re.compile(r"<transcript>\n?(.*?)\n?</transcript>", re.DOTALL)
_WORK = re.compile(
    r"\b(installed|replaced|repaired|serviced|fixed|changed|checked|cleaned|tested|fitted|configured|mounted|connected|inspected)\b",
    re.IGNORECASE,
)
_NEGATION = re.compile(r"\b(not|couldn't|could not|haven't|have not|didn't|did not|unable|never)\b", re.IGNORECASE)
_COMPLETE = re.compile(
    r"\b(?:job|work|installation|repair|service)(?:'s|\s+is|\s+was)?\s+(?:now\s+)?(?:complete|completed|done|finished)\b",
    re.IGNORECASE,
)
_BLOCKED = re.compile(r"\b(?:could not|couldn't|unable to)\s+(?:start|proceed|continue|finish)\b", re.IGNORECASE)
_RETURN = re.compile(r"\b(?:come back|go back|return visit|will be back|nitarudi)\b", re.IGNORECASE)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def _blank() -> dict:
    return {"value": None, "basis": "stated", "evidence": []}


def _list(items: list[str] | None = None, evidence: list[str] | None = None) -> dict:
    return {"value": items or [], "basis": "stated", "evidence": evidence or []}


def heuristic_extraction(text: str) -> dict:
    sentences = _sentences(text)
    out = {
        "technician": _blank(), "customer": _blank(), "job_reference": _blank(), "activity_date": _blank(),
        "location": _blank(), "job_status": {"value": "unknown", "basis": "stated", "evidence": []},
        "work_performed": _list(), "equipment": _list(), "parts_materials": _list(), "issues": _list(),
        "outstanding_work": _list(), "customer_concerns": _list(), "testing_confirmed": _blank(),
        "actions": [], "contradictions": [], "ambiguities": [], "summary": "",
    }

    m = re.search(r"\b(?i:this is|it's|it is|i am|i'm|my name is)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", text)
    if m:
        out["technician"] = {"value": m.group(1), "basis": "stated", "evidence": [m.group(0)]}
    m = re.search(r"\b(?:job|ticket|reference|ref)(?:\s+(?:number|no\.?|reference))?\s*[:#\-]?\s*([A-Z]{0,4}-?\d{3,8})\b", text, re.IGNORECASE)
    if m:
        out["job_reference"] = {"value": m.group(1), "basis": "stated", "evidence": [m.group(0)]}

    for s in sentences:
        if _COMPLETE.search(s):
            out["job_status"] = {"value": "completed", "basis": "stated", "evidence": [s]}
            break
        if _BLOCKED.search(s):
            out["job_status"] = {"value": "blocked", "basis": "interpreted", "evidence": [s]}
            break
        if _RETURN.search(s):
            out["job_status"] = {"value": "awaiting_return_visit", "basis": "interpreted", "evidence": [s]}
            break

    done = [s for s in sentences if _WORK.search(s) and not _NEGATION.search(s)]
    if done:
        out["work_performed"] = {"value": done[:5], "basis": "stated", "evidence": done[:5]}
    problems = [s for s in sentences if _NEGATION.search(s) and len(s.split()) > 3]
    if problems:
        out["issues"] = {"value": problems[:3], "basis": "interpreted", "evidence": problems[:3]}

    snippet = " ".join(done[:2]) if done else "No work performed could be identified."
    out["summary"] = f"Demo extractor summary: {snippet}"
    out["ambiguities"].append(
        {
            "field": None,
            "detail": "This report was produced by the limited demo extractor, which only recognises a few patterns.",
            "question": "Please review every field against the transcript, or configure a real LLM provider.",
            "evidence": [],
        }
    )
    return out


class MockLLM(LLMProvider):
    name = "mock"
    is_demo = True

    def complete_json(self, system: str, user: str) -> str:
        match = _TRANSCRIPT_RE.search(user)
        text = match.group(1) if match else user
        sample = find_sample_by_text(text)
        data = sample["extraction"] if sample else heuristic_extraction(text)
        return json.dumps(data, ensure_ascii=False)
