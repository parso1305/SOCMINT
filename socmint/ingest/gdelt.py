"""GDELT DOC 2.0 API (no key). Article lists for India-related keyword queries; titles + metadata only."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from socmint.core.logging import get_logger
from socmint.core.models import utcnow
from socmint.core.schemas import FetchResult, ItemIn
from socmint.ingest.base import Source, TermMatcher, clean_summary, flat_terms, register

log = get_logger(__name__)

API = "https://api.gdeltproject.org/api/v2/doc/doc"
LANG_CODES = {"english": "en", "hindi": "hi", "marathi": "mr", "bengali": "bn", "tamil": "ta", "telugu": "te",
              "gujarati": "gu", "kannada": "kn", "malayalam": "ml", "punjabi": "pa", "urdu": "ur"}


def event_query(terms: list[str], suffix: str = "") -> str:
    """OR-query from event terms: phrases quoted, '#' dropped (GDELT has no hashtags), short terms skipped."""
    parts = []
    for t in terms:
        t = t.lstrip("#").strip()
        if len(t) < 3:
            continue  # GDELT rejects very short keywords
        parts.append(f'"{t}"' if " " in t else t)
    q = parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")"
    return f"{q} {suffix}".strip() if parts else ""


@register
class GdeltSource(Source):
    """cfg: queries (list[str]), timespan (e.g. '1d'), maxrecords (<=250), rate_limit_s (>=5 per GDELT),
    event_query_suffix (optional, e.g. 'sourcecountry:IN', appended to investigation queries)."""

    type_name = "gdelt"
    capabilities = {"terms"}

    def hosts(self) -> list[str]:
        return [API]

    def fetch(self, since: datetime | None) -> FetchResult:
        res = FetchResult()
        errors: list[str] = []
        for q in self.cfg.get("queries", []):
            self._query(q, {"timespan": self.cfg.get("timespan", "1d")}, res, errors)
        if errors and not res.items:
            raise RuntimeError("GDELT returned no JSON: " + "; ".join(errors))
        if errors:
            log.warning("gdelt_partial", extra={"source": self.name, "errors": errors})
        return res

    def search_terms(self, terms_by_lang: dict[str, list[str]], since: datetime | None, limit: int) -> FetchResult:
        """One request for the whole event (GDELT asks for <= 1 request per 5 s; we space them 10 s)."""
        q = event_query(flat_terms(terms_by_lang), self.cfg.get("event_query_suffix", ""))
        res = FetchResult()
        if not q:
            res.notes.append("no usable terms for GDELT (all shorter than 3 characters)")
            return res
        start = max(since or utcnow() - timedelta(days=7), utcnow() - timedelta(days=89))  # DOC API: last 3 months
        errors: list[str] = []
        self._query(q, {"startdatetime": start.strftime("%Y%m%d%H%M%S"), "maxrecords": min(250, limit)}, res, errors,
                    retries=int(self.cfg.get("event_retries", 0)))  # interactive: fail fast when throttled
        if errors:
            raise RuntimeError("GDELT returned no JSON: " + "; ".join(errors))
        matcher = TermMatcher(terms_by_lang)
        for it in res.items:
            it.meta["matched_terms"] = matcher.match(it.text)  # empty when only the article body matched
        res.notes.append(f"query: {q}")
        return res

    def _query(self, q: str, extra: dict[str, Any], res: FetchResult, errors: list[str],
               retries: int | None = None) -> None:
        params = {"query": q, "mode": "artlist", "format": "json",
                  "maxrecords": int(self.cfg.get("maxrecords", 75)), "sort": "datedesc", **extra}
        raw = self.client.get(API, params=params, cache_ttl=self.cfg.get("cache_ttl_s", 1800), retries=retries)
        try:
            data: dict[str, Any] = json.loads(raw.decode("utf-8", errors="replace") or "{}")
        except json.JSONDecodeError:
            # GDELT answers rate-limit / query errors with plain text.
            errors.append(f"{q!r}: {raw[:120]!r}")
            return
        for a in data.get("articles", []) or []:
            url = a.get("url")
            if not url:
                continue
            res.items.append(
                ItemIn(
                    source=self.name, source_type="news", external_id=url, url=url,
                    channel=a.get("domain"), published_at=_parse_seendate(a.get("seendate")),
                    lang=LANG_CODES.get(str(a.get("language", "")).lower()),
                    text=clean_summary(a.get("title", ""), 500), urls=[url],
                    meta={"query": q, "domain": a.get("domain"), "sourcecountry": a.get("sourcecountry"),
                          "gdelt_language": a.get("language")},
                )
            )


def _parse_seendate(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y%m%dT%H%M%SZ")
    except ValueError:
        return None
