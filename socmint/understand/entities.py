"""Entity extraction: URLs/domains, Indian phone numbers, UPI IDs, Telegram handles, stock tickers, brands.

All extractors work on mixed-script text (Latin + Devanagari, Devanagari digits).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from socmint.core.patterns import (
    BARE_DOMAIN_RE,
    CASHTAG_RE,
    DEVANAGARI_DIGITS,
    EMAIL_RE,
    LANDLINE_RE,
    MOBILE_RE,
    TELEGRAM_HANDLE_RE,
    TOLLFREE_RE,
    UPI_RE,
    URL_RE,
)
from socmint.core.settings import load_config
from socmint.understand.normalize import match_text
from socmint.understand.urls import host_of, is_valid_domain, normalize_url, registered_domain

# File extensions that are also TLDs and would otherwise turn "photo.zip" into a domain.
_FILE_EXT_TLDS = {"zip", "mov", "mp", "py", "sh", "rs", "pl", "md", "pdf", "jpg", "png", "apk"}


@dataclass
class Entities:
    urls: list[str] = field(default_factory=list)          # normalised URLs
    domains: list[str] = field(default_factory=list)       # hosts (lower-case)
    phones: list[str] = field(default_factory=list)        # canonical: +91XXXXXXXXXX / 1800XXXXXXX / 0XXXXXXXXXX
    upi_ids: list[str] = field(default_factory=list)
    telegram_handles: list[str] = field(default_factory=list)
    tickers: list[str] = field(default_factory=list)
    brands: list[str] = field(default_factory=list)        # brand keys
    emails: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: list(v) for k, v in self.__dict__.items()}


def _uniq(seq: list[str]) -> list[str]:
    out: list[str] = []
    for s in seq:
        if s and s not in out:
            out.append(s)
    return out


def canonical_phone(raw: str) -> str:
    """Canonical form: +91 + 10 digits for mobiles; bare digits for toll-free/landline/short codes."""
    digits = re.sub(r"\D", "", raw.translate(DEVANAGARI_DIGITS))
    if len(digits) == 12 and digits.startswith("91") and digits[2] in "6789":
        return "+" + digits
    if len(digits) == 11 and digits.startswith("0") and digits[1] in "6789":
        return "+91" + digits[1:]
    if len(digits) == 10 and digits[0] in "6789":
        return "+91" + digits
    return digits


def extract_phones(text: str) -> list[str]:
    t = text.translate(DEVANAGARI_DIGITS)
    found: list[str] = []
    for m in TOLLFREE_RE.finditer(t):
        found.append(canonical_phone(m.group(0)))
    t2 = TOLLFREE_RE.sub(" ", t)
    for m in MOBILE_RE.finditer(t2):
        found.append(canonical_phone(m.group(0)))
    for m in LANDLINE_RE.finditer(MOBILE_RE.sub(" ", t2)):
        found.append(canonical_phone(m.group(0)))
    return _uniq(found)


def extract_urls(text: str) -> tuple[list[str], list[str]]:
    """Return (normalised URLs, hosts). Bare domains like `sbi-kyc.in/x` count as URLs."""
    t = EMAIL_RE.sub(" ", text)
    t = UPI_RE.sub(" ", t)
    urls: list[str] = []
    for m in URL_RE.finditer(t):
        urls.append(normalize_url(m.group(1)))
    rest = URL_RE.sub(" ", t)
    for m in BARE_DOMAIN_RE.finditer(rest):
        host = m.group(1).lower()
        tld = host.rsplit(".", 1)[-1]
        if tld in _FILE_EXT_TLDS or tld not in _bare_tlds() or not is_valid_domain(host):
            continue
        urls.append(normalize_url(host + (m.group(2) or "")))
    urls = _uniq(urls)
    hosts = _uniq([h for h in (host_of(u) for u in urls) if h and registered_domain(h)])
    return [u for u in urls if host_of(u) in hosts], hosts


@lru_cache(maxsize=1)
def _bare_tlds() -> frozenset[str]:
    """TLDs accepted for scheme-less domains (avoids 'ok.how' style false positives)."""
    return frozenset(t.lower() for t in load_config("scam_rules").get("bare_domain_tlds", []))


def extract_upi(text: str) -> list[str]:
    t = EMAIL_RE.sub(" ", text)
    return _uniq([m.group(1).lower() for m in UPI_RE.finditer(t)])


def extract_telegram(text: str) -> list[str]:
    out = []
    for m in TELEGRAM_HANDLE_RE.finditer(text):
        h = (m.group(1) or m.group(2) or "").lower()
        if h:
            out.append("@" + h)
    return _uniq(out)


@lru_cache(maxsize=1)
def _ticker_cfg() -> tuple[frozenset[str], frozenset[str]]:
    cfg = load_config("scam_rules").get("tickers", {}) or {}
    ctx = frozenset(match_text(w) for w in cfg.get("context_words", []))
    stop = frozenset(w.upper() for w in cfg.get("stoplist", []))
    return ctx, stop


def extract_tickers(text: str, symbols: set[str] | frozenset[str] | None) -> list[str]:
    """$SYM / #SYM always (validated against the symbol list when we have one);
    bare UPPERCASE words only when they are listed symbols AND a stock context word is present."""
    symbols = symbols or set()
    ctx, stop = _ticker_cfg()
    found: list[str] = []
    for m in CASHTAG_RE.finditer(text):
        sym = m.group(1).upper()
        if (not symbols or sym in symbols) and sym not in stop:
            found.append(sym)
    if symbols:
        mt = match_text(text)
        has_ctx = any((c in mt) if not c.isascii() else re.search(rf"(?<![a-z0-9]){re.escape(c)}(?![a-z0-9])", mt)
                      for c in ctx)
        if has_ctx:
            for m in re.finditer(r"(?<![\w$#])([A-Z][A-Z0-9&\-]{2,19})(?![\w])", text):
                sym = m.group(1)
                if sym in symbols and sym not in stop:
                    found.append(sym)
    return _uniq(found)


@lru_cache(maxsize=1)
def _brand_patterns() -> list[tuple[str, list[re.Pattern[str]], list[str]]]:
    from socmint.ingest.registry import brand_configs, flat_aliases

    out = []
    for b in brand_configs():
        latin, deva = [], []
        for a in flat_aliases(b):
            a_n = match_text(a)
            if not a_n:
                continue
            if a_n.isascii():
                latin.append(re.compile(rf"(?<![a-z0-9]){re.escape(a_n)}(?![a-z0-9])"))
            else:
                deva.append(a_n)
        out.append((b["key"], latin, deva))
    return out


def extract_brands(text: str) -> list[str]:
    mt = match_text(text)
    hits = []
    for key, latin, deva in _brand_patterns():
        if any(p.search(mt) for p in latin) or any(d in mt for d in deva):
            hits.append(key)
    return hits


@lru_cache(maxsize=1)
def known_symbols() -> frozenset[str]:
    """Symbols from the DB (NSE list + synthetic demo symbols). Cached; call cache_clear() after ingest."""
    try:
        from sqlalchemy import select

        from socmint.core.db import session_scope
        from socmint.core.models import Symbol

        with session_scope() as s:
            return frozenset(s.scalars(select(Symbol.symbol)).all())
    except Exception:  # noqa: BLE001  (DB not initialised yet)
        return frozenset()


def extract_all(text: str, symbols: set[str] | frozenset[str] | None = None) -> Entities:
    """Run every extractor on raw (un-redacted) text."""
    urls, hosts = extract_urls(text)
    return Entities(
        urls=urls, domains=hosts, phones=extract_phones(text), upi_ids=extract_upi(text),
        telegram_handles=extract_telegram(text),
        tickers=extract_tickers(text, symbols if symbols is not None else known_symbols()),
        brands=extract_brands(text), emails=_uniq([m.group(0).lower() for m in EMAIL_RE.finditer(text)]),
    )


def clear_caches() -> None:
    known_symbols.cache_clear()
    _brand_patterns.cache_clear()
    _ticker_cfg.cache_clear()
    _bare_tlds.cache_clear()
