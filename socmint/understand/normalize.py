"""Text normalisation for matching: NFC, ASCII digits, lowercase Latin, keep Devanagari, strip emoji."""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

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


# A Devanagari keyword matches at the start of a word: not right after another Devanagari letter, sign or digit
# (the danda counts as punctuation). The next character may be a vowel sign, anusvara, chandrabindu or visarga
# (so inflections such as पूराचे still match) but not a virama or a consonant (so पूर never matches पूर्ण, बंद
# never matches बंदर). Compounds that are wanted (पूरग्रस्त) are listed as keywords of their own.
_DEVA_WORD_START = "(?<![\u0900-\u0963\u0966-\u097f])"  # Devanagari block minus the dandas (U+0964/0965)
_DEVA_NO_CONTINUATION = "(?![\u094d\u0915-\u0939\u0958-\u095f\u0978-\u097f])"  # virama, consonants


@lru_cache(maxsize=8192)
def devanagari_word(term_norm: str) -> re.Pattern[str]:
    """Pattern for a `match_text`-normalised Devanagari keyword under the word-start rule above."""
    return re.compile(_DEVA_WORD_START + re.escape(term_norm) + _DEVA_NO_CONTINUATION)


def devanagari_ratio(text: str) -> float:
    """Share of letters that are Devanagari (0..1)."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if "ऀ" <= c <= "ॿ") / len(letters)
