"""YouTube Data API v3: keyword search + top comments. Requires YOUTUBE_API_KEY.

Quota: search.list costs 100 units, commentThreads.list 1 unit; daily free quota is 10,000.
`max_units_per_run` in config keeps us well under it.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from socmint.core.logging import get_logger
from socmint.core.models import utcnow
from socmint.core.schemas import FetchResult, ItemIn
from socmint.core.settings import get_settings
from socmint.ingest.base import Source, clean_summary, register

log = get_logger(__name__)
API = "https://www.googleapis.com/youtube/v3"


def _dt(s: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


@register
class YouTubeSource(Source):
    """cfg: keywords, region (IN), max_results, comments_per_video, max_units_per_run, relevance_language."""

    type_name = "youtube"
    required_settings = ("youtube_api_key",)

    def hosts(self) -> list[str]:
        return [API + "/search"]

    def fetch(self, since: datetime | None) -> FetchResult:
        key = get_settings().youtube_api_key
        budget = int(self.cfg.get("max_units_per_run", 1500))
        after = (since or utcnow() - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        res = FetchResult()
        for kw in self.cfg.get("keywords", []):
            if budget < 100:
                log.warning("youtube_quota_budget_reached", extra={"source": self.name})
                break
            params = {"part": "snippet", "q": kw, "type": "video", "regionCode": self.cfg.get("region", "IN"),
                      "maxResults": int(self.cfg.get("max_results", 25)), "publishedAfter": after, "order": "date", "key": key}
            if self.cfg.get("relevance_language"):
                params["relevanceLanguage"] = self.cfg["relevance_language"]
            data = self.client.get_json(f"{API}/search", params=params, cache_ttl=3600, check_robots=False)
            budget -= 100
            for it in data.get("items", []):
                vid = (it.get("id") or {}).get("videoId")
                sn = it.get("snippet", {})
                if not vid:
                    continue
                url = f"https://www.youtube.com/watch?v={vid}"
                res.items.append(ItemIn(source=self.name, source_type="youtube", external_id=f"video:{vid}", url=url,
                                        channel=sn.get("channelTitle"), author_handle=sn.get("channelId"),
                                        published_at=_dt(sn.get("publishedAt")),
                                        text=clean_summary(f"{sn.get('title', '')}. {sn.get('description', '')}"),
                                        meta={"keyword": kw}))
                n = int(self.cfg.get("comments_per_video", 20))
                if n and budget >= 1:
                    budget -= 1
                    try:
                        cd = self.client.get_json(f"{API}/commentThreads", params={"part": "snippet", "videoId": vid,
                                                  "maxResults": n, "order": "relevance", "textFormat": "plainText", "key": key},
                                                  cache_ttl=3600, check_robots=False)
                    except Exception as e:  # noqa: BLE001 (comments disabled etc.)
                        log.debug("youtube_comments_failed", extra={"video": vid, "error": str(e)})
                        continue
                    for ct in cd.get("items", []):
                        top = ct.get("snippet", {}).get("topLevelComment", {})
                        cs = top.get("snippet", {})
                        res.items.append(ItemIn(source=self.name, source_type="youtube", external_id=f"comment:{top.get('id')}",
                                                url=url, channel=sn.get("channelTitle"),
                                                author_handle=(cs.get("authorChannelId") or {}).get("value"),
                                                published_at=_dt(cs.get("publishedAt")),
                                                text=clean_summary(cs.get("textOriginal", "")), meta={"video_id": vid, "kind": "comment"}))
        return res
