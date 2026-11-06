"""Reddit API (app-only OAuth). Requires REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET.

Only subreddits listed in sources.yaml (or in the investigation config); non-commercial prototype use under
Reddit's Data API terms.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any

from socmint.core.schemas import AccountIn, FetchResult, ItemIn
from socmint.core.settings import get_settings
from socmint.ingest.base import Source, TermMatcher, clean_summary, flat_terms, register, text_entities

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API = "https://oauth.reddit.com"


def _ts(v: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(v), UTC).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def post_item(source: str, p: dict[str, Any]) -> ItemIn:
    body = f"{p.get('title', '')}. {p.get('selftext', '')}"
    ents = text_entities(body)
    urls = ents["urls"]
    link = p.get("url")
    if link and not p.get("is_self") and link not in urls:
        urls = [link, *urls]
    author = p.get("author")
    return ItemIn(
        source=source, source_type="reddit", external_id=p.get("name", ""),
        url=f"https://www.reddit.com{p.get('permalink', '')}", channel=f"r/{p.get('subreddit', '')}",
        author_handle=author, author_id=author if author and author != "[deleted]" else None,
        published_at=_ts(p.get("created_utc")), text=clean_summary(body),
        hashtags=ents["hashtags"], mentions=ents["mentions"], urls=urls,
        engagement={"score": p.get("score"), "replies": p.get("num_comments"), "upvote_ratio": p.get("upvote_ratio")},
        forwarded_from=(p.get("crosspost_parent_list") or [{}])[0].get("author"),
        meta={"score": p.get("score"), "num_comments": p.get("num_comments"), "link_url": link})


@register
class RedditSource(Source):
    """cfg: subreddits (list), limit (<=100)."""

    type_name = "reddit"
    required_settings = ("reddit_client_id", "reddit_client_secret")
    capabilities = {"terms", "accounts"}

    def hosts(self) -> list[str]:
        return [API + "/r/"]

    def _token(self) -> str:
        s = get_settings()
        basic = base64.b64encode(f"{s.reddit_client_id}:{s.reddit_client_secret}".encode()).decode()
        raw = self.client.get(TOKEN_URL, method="POST", data={"grant_type": "client_credentials"},
                              headers={"Authorization": f"Basic {basic}"}, cache_ttl=0, check_robots=False)
        return json.loads(raw)["access_token"]

    def _listing(self, token: str, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        data = self.client.get_json(f"{API}{path}", params=params, headers={"Authorization": f"bearer {token}"},
                                    cache_ttl=600, check_robots=False, cache_key_extra=path)
        return [c.get("data", {}) for c in data.get("data", {}).get("children", [])]

    def fetch(self, since: datetime | None) -> FetchResult:
        token = self._token()
        res = FetchResult()
        cutoff = since.timestamp() if since else 0
        for sub in self.cfg.get("subreddits", []):
            for p in self._listing(token, f"/r/{sub}/new", {"limit": int(self.cfg.get("limit", 100))}):
                if float(p.get("created_utc", 0)) < cutoff:
                    continue
                res.items.append(post_item(self.name, {**p, "subreddit": p.get("subreddit") or sub}))
        return res

    def search_terms(self, terms_by_lang: dict[str, list[str]], since: datetime | None, limit: int) -> FetchResult:
        """Search *within* the configured subreddits only (never site-wide)."""
        token = self._token()
        matcher = TermMatcher(terms_by_lang)
        q = " OR ".join(f'"{t}"' if " " in t else t for t in (x.lstrip("#") for x in flat_terms(terms_by_lang)))
        res = FetchResult()
        cutoff = since.timestamp() if since else 0
        for sub in self.cfg.get("subreddits", []):
            for p in self._listing(token, f"/r/{sub}/search", {"q": q, "restrict_sr": 1, "sort": "new", "t": "month",
                                                               "limit": min(100, limit)}):
                if float(p.get("created_utc", 0)) < cutoff:
                    continue
                it = post_item(self.name, {**p, "subreddit": p.get("subreddit") or sub})
                it.meta["matched_terms"] = matcher.match(it.text, it.hashtags)
                res.items.append(it)
                if it.author_id:
                    res.accounts.append(AccountIn(platform="reddit", platform_id=it.author_id, handle=it.author_id,
                                                  url=f"https://www.reddit.com/user/{it.author_id}"))
        if not self.cfg.get("subreddits"):
            res.notes.append("no subreddits configured; Reddit is searched only within listed subreddits")
        return res

    def account_profile(self, handle: str) -> AccountIn:
        name = handle.removeprefix("u/").lstrip("@")
        d = self.client.get_json(f"{API}/user/{name}/about", headers={"Authorization": f"bearer {self._token()}"},
                                 cache_ttl=600, check_robots=False).get("data", {})
        return AccountIn(platform="reddit", platform_id=d.get("name", name), handle=d.get("name", name),
                         url=f"https://www.reddit.com/user/{d.get('name', name)}", created_at=_ts(d.get("created_utc")),
                         description=clean_summary((d.get("subreddit") or {}).get("public_description"), 1000) or None,
                         meta={"link_karma": d.get("link_karma"), "comment_karma": d.get("comment_karma")})

    def account_posts(self, handle: str, since: datetime | None, limit: int) -> FetchResult:
        profile = self.account_profile(handle)
        token = self._token()
        res = FetchResult(accounts=[profile])
        cutoff = since.timestamp() if since else 0
        for p in self._listing(token, f"/user/{profile.platform_id}/submitted", {"limit": min(100, limit), "sort": "new"}):
            if float(p.get("created_utc", 0)) >= cutoff:
                res.items.append(post_item(self.name, p))
        return res
