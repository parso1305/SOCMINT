"""GDELT DOC 2.0 API (no key). Article lists for India-related keyword queries; titles + metadata only.

Failing cleanly (M17.2): 10 s connect / 30 s read timeouts; one retry after a timeout or connection error (after
the host interval); never a retry on HTTP 429. A 429 (or GDELT's plain-text "Please limit requests" answer) starts
a cooldown (`gdelt.cooldown_minutes`, saved in data/cache/cooldowns.json); until it ends the source is skipped
with status `throttled` and sends nothing. The event query starts at the investigation's window (to the hour), so
its HTTP cache key is stable and a second "Collect now" within `cache_ttl_s` does not call GDELT again.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from typing import Any

import httpx

from socmint.core import cooldown
from socmint.core.http import HttpError, Throttled
from socmint.core.logging import get_logger
from socmint.core.models import utcnow
from socmint.core.schemas import FetchResult, ItemIn
from socmint.core.settings import threshold
from socmint.ingest.base import Source, TermMatcher, clean_summary, flat_terms, register

log = get_logger(__name__)

API = "https://api.gdeltproject.org/api/v2/doc/doc"
HOST = "api.gdeltproject.org"
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
        self._check_cooldown()
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
        self._check_cooldown()
        q = event_query(flat_terms(terms_by_lang), self.cfg.get("event_query_suffix", ""))
        res = FetchResult()
        if not q:
            res.notes.append("no usable terms for GDELT (all shorter than 3 characters)")
            return res
        errors: list[str] = []
        self._query(q, {"startdatetime": self._event_start(since).strftime("%Y%m%d%H%M%S"),
                        "maxrecords": min(250, limit)}, res, errors, interactive=True)
        if errors:
            raise RuntimeError("GDELT returned no JSON: " + "; ".join(errors))
        matcher = TermMatcher(terms_by_lang)
        for it in res.items:
            it.meta["matched_terms"] = matcher.match(it.text)  # empty when only the article body matched
        res.notes.append(f"query: {q}")
        return res

    def _event_start(self, since: datetime | None) -> datetime:
        """The investigation's window start (the runner passes `window_start`), to the hour, so the query and its
        cache key stay the same from one collection to the next. Already-stored articles are de-duplicated."""
        ws = self.cfg.get("window_start")
        start = datetime.fromisoformat(ws) if ws else (since or utcnow() - timedelta(days=7))
        oldest = (utcnow() - timedelta(days=89)).replace(hour=0, minute=0, second=0, microsecond=0)  # DOC API: 3 months
        return max(start.replace(minute=0, second=0, microsecond=0), oldest)

    def _check_cooldown(self) -> None:
        end = cooldown.until(HOST)
        if end:
            raise Throttled(f"GDELT is limiting this IP; next try after {end:%H:%M} UTC", until=end)

    def _throttled(self, reason: str) -> Throttled:
        end = cooldown.start(HOST, float(threshold("gdelt.cooldown_minutes", 15)), reason)
        log.warning("gdelt_cooldown_started", extra={"source": self.name, "reason": reason, "until": end.isoformat()})
        return Throttled(f"GDELT is limiting this IP; next try after {end:%H:%M} UTC ({reason})", until=end)

    def _get(self, params: dict[str, Any], interactive: bool) -> bytes:
        kw: dict[str, Any] = {"params": params, "cache_ttl": self.cfg.get("cache_ttl_s", 1800),
                              "timeout": httpx.Timeout(float(self.cfg.get("read_timeout_s", 30)),
                                                       connect=float(self.cfg.get("connect_timeout_s", 10)))}
        if not interactive:  # the scheduled global ingest keeps the client's retries and 429 back-off
            return self.client.get(API, **kw)
        try:
            return self.client.get(API, retries=0, **kw)
        except HttpError as e:
            if e.status is not None:
                raise
            # a timeout or connection error: wait the host interval, then one more try (never on 429)
            log.info("gdelt_retry_after_network_error", extra={"source": self.name, "error": str(e)[:200]})
            time.sleep(float(self.cfg.get("rate_limit_s", 10)))
            return self.client.get(API, retries=0, **kw)

    def _query(self, q: str, extra: dict[str, Any], res: FetchResult, errors: list[str],
               interactive: bool = False) -> None:
        params = {"query": q, "mode": "artlist", "format": "json",
                  "maxrecords": int(self.cfg.get("maxrecords", 75)), "sort": "datedesc", **extra}
        try:
            raw = self._get(params, interactive)
        except HttpError as e:
            if e.status == 429:
                raise self._throttled("HTTP 429") from None
            raise
        try:
            data: dict[str, Any] = json.loads(raw.decode("utf-8", errors="replace") or "{}")
        except json.JSONDecodeError:
            # GDELT answers rate-limit / query errors with plain text (HTTP 200), which must not stay cached.
            self.client.forget(API, params=params)
            if b"limit requests" in raw[:300].lower():
                raise self._throttled("asked to limit requests") from None
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
