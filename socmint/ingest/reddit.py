"""Reddit API (app-only OAuth). Requires REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET.

Only subreddits listed in sources.yaml; non-commercial prototype use under Reddit's Data API terms.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

from socmint.core.schemas import FetchResult, ItemIn
from socmint.core.settings import get_settings
from socmint.ingest.base import Source, clean_summary, register

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API = "https://oauth.reddit.com"


@register
class RedditSource(Source):
    """cfg: subreddits (list), limit (<=100)."""

    type_name = "reddit"
    required_settings = ("reddit_client_id", "reddit_client_secret")

    def hosts(self) -> list[str]:
        return [API + "/r/"]

    def _token(self) -> str:
        s = get_settings()
        basic = base64.b64encode(f"{s.reddit_client_id}:{s.reddit_client_secret}".encode()).decode()
        raw = self.client.get(TOKEN_URL, method="POST", data={"grant_type": "client_credentials"},
                              headers={"Authorization": f"Basic {basic}"}, cache_ttl=0, check_robots=False)
        return json.loads(raw)["access_token"]

    def fetch(self, since: datetime | None) -> FetchResult:
        token = self._token()
        res = FetchResult()
        cutoff = since.timestamp() if since else 0
        for sub in self.cfg.get("subreddits", []):
            data = self.client.get_json(f"{API}/r/{sub}/new", params={"limit": int(self.cfg.get("limit", 100))},
                                        headers={"Authorization": f"bearer {token}"}, cache_ttl=600, check_robots=False,
                                        cache_key_extra=sub)
            for child in data.get("data", {}).get("children", []):
                p = child.get("data", {})
                created = float(p.get("created_utc", 0))
                if created < cutoff:
                    continue
                res.items.append(ItemIn(
                    source=self.name, source_type="reddit", external_id=p.get("name", ""),
                    url=f"https://www.reddit.com{p.get('permalink', '')}", channel=f"r/{sub}", author_handle=p.get("author"),
                    published_at=datetime.fromtimestamp(created, UTC).replace(tzinfo=None),
                    text=clean_summary(f"{p.get('title', '')}. {p.get('selftext', '')}"),
                    meta={"score": p.get("score"), "num_comments": p.get("num_comments"), "link_url": p.get("url")}))
        return res
