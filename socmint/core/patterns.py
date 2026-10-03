"""Shared regular expressions for Indian identifiers (used by privacy redaction and entity extraction)."""

from __future__ import annotations

import re

# Devanagari digits ०-९ -> 0-9 (numbers in Hindi/Marathi messages are often written this way).
DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}(?![\w-])")

# UPI VPA: handle@psp, psp has no dot (that is what distinguishes it from an e-mail address).
UPI_RE = re.compile(r"(?<![\w.+-])([A-Za-z0-9][\w.\-]{1,255}@[A-Za-z][A-Za-z0-9]{1,63})(?![\w@.-]*\.[A-Za-z])(?![\w-])")

# 12-digit Aadhaar-like number (first digit 2-9), optionally grouped 4-4-4.
AADHAAR_RE = re.compile(r"(?<![\d\w])[2-9]\d{3}[\s-]?\d{4}[\s-]?\d{4}(?![\d\w])")

# PAN-like: AAAAA9999A
PAN_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{5}[0-9]{4}[A-Za-z](?![A-Za-z0-9])")

# Indian mobile: optional +91 / 91 / 0 prefix, 10 digits starting 6-9, optional single space/hyphen separators.
MOBILE_RE = re.compile(r"(?<![\d+])(?:(?:\+|00)?91[\s-]?|0)?([6-9](?:[\s-]?\d){9})(?![\d])")

# Toll-free / premium service numbers: 1800 / 1860 / 1900 + 6-7 digits.
TOLLFREE_RE = re.compile(r"(?<!\d)(1[89]00|1860)[\s-]?(\d{3})[\s-]?(\d{3,4})(?!\d)")

# Landline with STD code: 0 + 2-4 digit code + 6-8 digit number (separator required to limit false positives).
LANDLINE_RE = re.compile(r"(?<!\d)0[1-9]\d{1,3}[\s-]\d{6,8}(?!\d)")

URL_RE = re.compile(
    r"(?i)\b((?:https?://|www\.)[^\s<>\"'()\[\]{}]+)",
)
# Bare domains such as "sbi-kyc-update.in" (no scheme). Validated with tldextract afterwards.
BARE_DOMAIN_RE = re.compile(
    r"(?i)(?<![@\w.-])((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24})(/[^\s<>\"']*)?(?![\w@-])"
)

TELEGRAM_HANDLE_RE = re.compile(r"(?i)(?:t\.me/|telegram\.me/)([a-z0-9_]{5,32})|(?<![\w@])@([a-z][a-z0-9_]{4,31})\b")

CASHTAG_RE = re.compile(r"(?<![\w$])[$#]([A-Z][A-Z0-9&\-]{1,19})\b")
