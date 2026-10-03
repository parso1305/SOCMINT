"""GDELT DOC 2.0 API (no key). Article lists for India-related keyword queries; titles + metadata only."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from socmint.core.logging import get_logger
from socmint.core.schemas import FetchResult, ItemIn
from socmint.ingest.base import Source, clean_summary, register

log = get_logger(__name__)

API = "https://api.gdeltproject.org/api/v2/doc/doc"
LANG_CODES = {"english": "en", "hindi": "hi", "marathi": "mr", "bengali": "bn", "tamil": "ta", "telugu": "te",
              "gujarati": "gu", "kannada": "kn", "malayalam": "ml", "punjabi": "pa", "urdu": "ur"}


@register
class GdeltSource(Source):
    """cfg: queries (list[str]), timespan (e.g. '1d'), maxrecords (<=250), rate_limit_s (>=5 per GDELT)."""

    type_name = "gdelt"

    def hosts(self) -> list[str]:
        return [API]

    def fetch(self, since: datetime | None) -> FetchResult:
        res = FetchResult()
        errors: list[str] = []
        for q in self.cfg.get("queries", []):
            params = {"query": q, "mode": "artlist", "format": "json",
                      "maxrecords": int(self.cfg.get("maxrecords", 75)), "timespan": self.cfg.get("timespan", "1d"),
                      "sort": "datedesc"}
            raw = self.client.get(API, params=params, cache_ttl=self.cfg.get("cache_ttl_s", 1800))
            try:
                data: dict[str, Any] = json.loads(raw.decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                # GDELT answers rate-limit / query errors with plain text.
                errors.append(f"{q!r}: {raw[:120]!r}")
                continue
            for a in data.get("articles", []) or []:
                url = a.get("url")
                if not url:
                    continue
                res.items.append(
                    ItemIn(
                        source=self.name, source_type="news", external_id=url, url=url,
                        channel=a.get("domain"), published_at=_parse_seendate(a.get("seendate")),
                        lang=LANG_CODES.get(str(a.get("language", "")).lower()),
                        text=clean_summary(a.get("title", ""), 500),
                        meta={"query": q, "domain": a.get("domain"), "sourcecountry": a.get("sourcecountry"),
                              "gdelt_language": a.get("language")},
                    )
                )
        if errors and not res.items:
            raise RuntimeError("GDELT returned no JSON: " + "; ".join(errors))
        if errors:
            log.warning("gdelt_partial", extra={"source": self.name, "errors": errors})
        return res


def _parse_seendate(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y%m%dT%H%M%SZ")
    except ValueError:
        return None
