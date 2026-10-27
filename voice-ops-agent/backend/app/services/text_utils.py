"""Text helpers used to verify AI output against the transcript and to redact contact details."""
from __future__ import annotations

import re
import unicodedata

_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)


def norm(text: str) -> str:
    """Lowercase, strip punctuation and collapse whitespace. Used for all transcript comparisons."""
    return _NON_WORD.sub(" ", unicodedata.normalize("NFKC", text).lower()).strip()


def _stem(token: str) -> str:
    return token[:-1] if len(token) > 3 and token.endswith("s") else token


def _tokens(text_norm: str) -> set[str]:
    return {_stem(t) for t in text_norm.split()}


def evidence_found(excerpt: str, transcript_norm: str) -> bool:
    e = norm(excerpt)
    return bool(e) and f" {e} " in f" {transcript_norm} "


def verify_evidence(excerpts: list[str], transcript_norm: str, limit: int = 5) -> list[dict]:
    """Mark each supporting excerpt as verified only if it really occurs in the transcript."""
    out: list[dict] = []
    seen: set[str] = set()
    for raw in excerpts:
        text = " ".join(str(raw).split())
        if not text or text in seen:
            continue
        seen.add(text)
        out.append({"text": text[:400], "verified": evidence_found(text, transcript_norm)})
        if len(out) >= limit:
            break
    return out


def entity_supported(value: str, transcript_norm: str) -> bool:
    """True if a name/reference/location value can be traced to the transcript.

    Either the whole value appears as a phrase, or every word of it appears somewhere in the transcript.
    """
    v = norm(value)
    if not v:
        return False
    if f" {v} " in f" {transcript_norm} ":
        return True
    t_tokens = _tokens(transcript_norm)
    tokens = [_stem(t) for t in v.split() if len(t) > 1 or t.isdigit()]
    return bool(tokens) and all(t in t_tokens for t in tokens)


def item_supported(item: str, transcript_norm: str) -> bool:
    """Looser check for equipment and parts: at least one meaningful word must appear in the transcript."""
    v = norm(item)
    tokens = [_stem(t) for t in v.split() if len(t) >= 3]
    if not tokens:
        return entity_supported(item, transcript_norm)
    t_tokens = _tokens(transcript_norm)
    return any(t in t_tokens for t in tokens)


# ------------------------------------------------------------------------------ redaction
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<![\w-])\+?\d[\d \-]{7,}\d(?![\w-])")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _phone_sub(match: re.Match[str]) -> str:
    text = match.group(0)
    digits = re.sub(r"\D", "", text)
    if len(digits) < 9 or _ISO_DATE.match(text.strip()):
        return text
    return "[PHONE]"


def redact_contact_details(text: str) -> tuple[str, int]:
    """Replace email addresses and phone numbers with placeholders. Returns (text, replacements)."""
    count = 0

    def email_sub(m: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return "[EMAIL]"

    out = _EMAIL.sub(email_sub, text)

    def phone_sub(m: re.Match[str]) -> str:
        nonlocal count
        replaced = _phone_sub(m)
        if replaced != m.group(0):
            count += 1
        return replaced

    out = _PHONE.sub(phone_sub, out)
    return out, count
