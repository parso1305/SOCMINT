"""Privacy utilities: sender pseudonymisation, PII redaction, media retention."""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from socmint.core.patterns import (
    AADHAAR_RE,
    DEVANAGARI_DIGITS,
    EMAIL_RE,
    LANDLINE_RE,
    MOBILE_RE,
    PAN_RE,
    TOLLFREE_RE,
    UPI_RE,
)
from socmint.core.settings import get_settings


class PrivacyConfigError(RuntimeError):
    """Raised when privacy-critical configuration (the HMAC salt) is missing."""


def sender_hash(chat_id: str | int, salt: str | None = None) -> str:
    """Return HMAC-SHA256(salt, chat_id) as hex. The raw chat id is never stored.

    Raises PrivacyConfigError if no salt is configured, so we never fall back to a weak hash.
    """
    salt = salt if salt is not None else get_settings().privacy_salt
    if not salt:
        raise PrivacyConfigError("PRIVACY_SALT is not set in .env; refusing to hash sender ids without a salt.")
    return hmac.new(salt.encode("utf-8"), str(chat_id).encode("utf-8"), hashlib.sha256).hexdigest()


@dataclass
class RedactionResult:
    text: str
    counts: dict[str, int] = field(default_factory=dict)


# Order matters: longer / more specific patterns first.
_REDACTIONS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", EMAIL_RE),
    ("UPI", UPI_RE),
    ("AADHAAR", AADHAAR_RE),
    ("PAN", PAN_RE),
    ("PHONE", TOLLFREE_RE),
    ("PHONE", MOBILE_RE),
    ("PHONE", LANDLINE_RE),
]


def redact_pii(text: str) -> RedactionResult:
    """Replace phone numbers, e-mails, UPI IDs, Aadhaar-like and PAN-like numbers with placeholders.

    Devanagari digits are converted to ASCII first so they cannot slip through.
    Scam artifacts must be extracted from the *raw* text before calling this (see tipline pipeline).
    """
    out = (text or "").translate(DEVANAGARI_DIGITS)
    counts: dict[str, int] = {}
    for label, pattern in _REDACTIONS:
        out, n = pattern.subn(f"[{label}]", out)
        if n:
            counts[label] = counts.get(label, 0) + n
    return RedactionResult(out, counts)


def purge_media(media_dir: Path | None = None, retention_days: int | None = None) -> list[Path]:
    """Delete raw media files older than the retention period. Returns deleted paths."""
    import time

    s = get_settings()
    media_dir = media_dir or s.media_dir
    retention = timedelta(days=retention_days if retention_days is not None else s.retention_days)
    cutoff = time.time() - retention.total_seconds()
    deleted: list[Path] = []
    if not media_dir.exists():
        return deleted
    for p in media_dir.rglob("*"):
        if p.is_file() and p.stat().st_mtime < cutoff:
            p.unlink(missing_ok=True)
            deleted.append(p)
    return deleted
