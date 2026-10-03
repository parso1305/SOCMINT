"""Text normalisation for matching: NFC, ASCII digits, lowercase Latin, keep Devanagari, strip emoji."""

from __future__ import annotations

import re
import unicodedata

from socmint.core.patterns import DEVANAGARI_DIGITS

_WS = re.compile(r"\s+")
# Nukta and zero-width joiners vary between keyboards (फ़ / फ, क्‍ / क्) - drop them for matching.
_DEVANAGARI_NOISE = dict.fromkeys(map(ord, "़‌‍️"), None)


def normalize_text(text: str) -> str:
    """NFC, Devanagari digits -> ASCII, lowercase (affects Latin only), collapse whitespace."""
    t = unicodedata.normalize("NFC", text or "")
    t = t.translate(DEVANAGARI_DIGITS)
    return _WS.sub(" ", t.lower()).strip()


def strip_emoji(text: str) -> str:
    """Remove emoji / pictographs / symbols (Unicode category So/Sk/Cs) and variation selectors."""
    return "".join(
        ch for ch in text
        if unicodedata.category(ch) not in ("So", "Sk", "Cs") and not (0xFE00 <= ord(ch) <= 0xFE0F)
        and not (0x1F000 <= ord(ch) <= 0x1FAFF)
    )


def match_text(text: str) -> str:
    """Canonical form used by keyword rules and alias matching."""
    t = strip_emoji(normalize_text(text)).translate(_DEVANAGARI_NOISE)
    return _WS.sub(" ", t).strip()


def devanagari_ratio(text: str) -> float:
    """Share of letters that are Devanagari (0..1)."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if "ऀ" <= c <= "ॿ") / len(letters)
