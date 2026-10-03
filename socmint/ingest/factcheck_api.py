"""Google Fact Check Tools API (claims:search). Requires GOOGLE_FACTCHECK_API_KEY."""

from __future__ import annotations

from datetime import datetime

from socmint.core.schemas import FactCheckIn, FetchResult
from socmint.core.settings import get_settings
from socmint.ingest.base import Source, clean_summary, register

API = "https://factchecktools.googleapis.com/v1alpha1/claims:search"


def _dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


@register
class GoogleFactCheckSource(Source):
    """cfg: queries (list), languages (list of codes), max_age_days, page_size."""

    type_name = "google_factcheck"
    required_settings = ("google_factcheck_api_key",)

    def hosts(self) -> list[str]:
        return [API]

    def fetch(self, since: datetime | None) -> FetchResult:
        key = get_settings().google_factcheck_api_key
        res = FetchResult()
        for lang in self.cfg.get("languages", ["en"]):
            for q in self.cfg.get("queries", []):
                params = {"query": q, "languageCode": lang, "pageSize": int(self.cfg.get("page_size", 50)),
                          "maxAgeDays": int(self.cfg.get("max_age_days", 30)), "key": key}
                data = self.client.get_json(API, params=params, cache_ttl=21600, check_robots=False)
                for c in data.get("claims", []) or []:
                    for rv in c.get("claimReview", []) or []:
                        if not rv.get("url"):
                            continue
                        res.factchecks.append(FactCheckIn(
                            publisher=(rv.get("publisher") or {}).get("name") or (rv.get("publisher") or {}).get("site", ""),
                            title=clean_summary(rv.get("title") or c.get("text", ""), 500), url=rv["url"],
                            claim_text=clean_summary(c.get("text", "")), rating=rv.get("textualRating"),
                            lang=rv.get("languageCode") or lang, published_at=_dt(rv.get("reviewDate") or c.get("claimDate")),
                            source=self.name))
        return res
