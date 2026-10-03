"""Language identification for en / hi / mr / Hinglish (hi-Latn).

langdetect alone confuses Hindi and Marathi on short messages and cannot see Romanised Hindi,
so we combine script share, small marker-word lists, and langdetect as a tie-breaker.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from socmint.understand.normalize import devanagari_ratio, match_text

MARATHI_MARKERS = {
    "आहे", "आहेत", "नाही", "आणि", "तुमचे", "तुमचा", "तुमची", "तुमच्या", "आपले", "आपला", "मध्ये", "साठी",
    "झाले", "होईल", "केले", "करावे", "करून", "येथे", "आता", "लवकर", "होणार", "मिळेल", "पाठवा",
    "खाते", "तुम्हाला", "आम्ही", "हा", "व", "जाईल", "केला", "शकता", "कृपया", "वीज", "उद्या", "करा", "आजच", "लगेच", "यांना", "वर",
}
HINDI_MARKERS = {
    "है", "हैं", "नहीं", "और", "आपका", "आपके", "आपकी", "की", "के", "में", "से", "को", "गया", "होगा", "रहा",
    "रही", "करें", "कीजिए", "तुरंत", "अभी", "यह", "वह", "था", "थे", "हो", "जाएगा", "मिलेगा", "भेजें", "खाता",
    "आपको", "हम", "पर", "लिए", "कल", "बिजली", "कृपया", "जल्दी",
}
HINGLISH_MARKERS = {
    "hai", "hain", "nahi", "nahin", "aapka", "aapke", "aapki", "apka", "apke", "karo", "kare", "karein", "kijiye",
    "paise", "rupaye", "turant", "abhi", "mein", "hoga", "jaldi", "khata", "bhai", "kya", "aap", "aapko",
    "milega", "milenge", "kamao", "ghar", "baithe", "bhejo", "band", "ho", "gaya", "jayega", "jaega", "yeh",
    "kal", "se", "ka", "ki", "ke", "ko", "par", "liye", "sirf", "bas", "lakh", "hazaar", "rupay",
}
_WORD = re.compile(r"[\wऀ-ॿ]+")


@dataclass
class LangResult:
    code: str          # en | hi | mr | hi-Latn | other ISO code
    script: str        # Latn | Deva | mixed
    confidence: float
    method: str


def _langdetect(text: str) -> tuple[str, float]:
    try:
        from langdetect import DetectorFactory, detect_langs

        DetectorFactory.seed = 0
        res = detect_langs(text)
        return (res[0].lang, float(res[0].prob)) if res else ("", 0.0)
    except Exception:  # noqa: BLE001 (LangDetectException on empty / symbol-only text)
        return "", 0.0


def detect_lang(text: str) -> LangResult:
    """Detect language of a tipline message or item."""
    t = match_text(text)
    words = _WORD.findall(t)
    if not words:
        return LangResult("en", "Latn", 0.0, "default")
    deva = devanagari_ratio(t)
    if deva >= 0.5:
        mr = sum(w in MARATHI_MARKERS for w in words)
        hi = sum(w in HINDI_MARKERS for w in words)
        if mr != hi:
            code = "mr" if mr > hi else "hi"
            return LangResult(code, "Deva", round(abs(mr - hi) / max(mr + hi, 1), 2), "markers")
        ld, p = _langdetect(t)
        return LangResult("mr" if ld == "mr" else "hi", "Deva", p, "langdetect")
    hing = sum(w in HINGLISH_MARKERS for w in words)
    if hing >= 2 and hing / len(words) >= 0.12:
        return LangResult("hi-Latn", "Latn" if deva == 0 else "mixed", round(min(1.0, hing / len(words) * 3), 2), "markers")
    ld, p = _langdetect(t)
    if ld in ("en", "hi", "mr"):
        return LangResult(ld, "Latn" if deva < 0.1 else "mixed", p, "langdetect")
    # Short Latin-script text often gets misdetected (so/tl/id...); English is the safe default.
    return LangResult("en", "Latn", p, "default")


def reply_language(code: str) -> str:
    """Reply template language: en/hi/mr. Romanised Hindi gets English (see DECISIONS.md)."""
    return code if code in ("hi", "mr") else "en"
