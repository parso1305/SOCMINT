"""YouTube Data API v3: keyword search, channel uploads and top comments. Requires YOUTUBE_API_KEY.

Quota: search.list costs 100 units; channels.list, playlistItems.list, videos.list and commentThreads.list
cost 1 unit each. The daily free quota is 10,000; `max_units_per_run` in config keeps us well under it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from socmint.core.logging import get_logger
from socmint.core.models import utcnow
from socmint.core.schemas import AccountIn, FetchResult, ItemIn
from socmint.core.settings import get_settings
from socmint.ingest.base import (
    Source,
    TermMatcher,
    clean_summary,
    flat_terms,
    parse_iso,
    register,
    text_entities,
)

log = get_logger(__name__)
API = "https://www.googleapis.com/youtube/v3"


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def video_item(source: str, vid: str, sn: dict[str, Any], meta: dict[str, Any]) -> ItemIn:
    ents = text_entities(f"{sn.get('title', '')} {sn.get('description', '')}")
    return ItemIn(source=source, source_type="youtube", external_id=f"video:{vid}",
                  url=f"https://www.youtube.com/watch?v={vid}", channel=sn.get("channelTitle"),
                  author_handle=sn.get("channelId"), author_id=sn.get("channelId"),
                  author_display_name=sn.get("channelTitle"), published_at=parse_iso(sn.get("publishedAt")),
                  text=clean_summary(f"{sn.get('title', '')}. {sn.get('description', '')}"),
                  hashtags=ents["hashtags"], mentions=ents["mentions"], urls=ents["urls"],
                  media=[{"type": "video", "url": f"https://www.youtube.com/watch?v={vid}", "phash": None}], meta=meta)


def comment_item(source: str, vid: str, video_channel: dict[str, Any], ct: dict[str, Any]) -> ItemIn:
    top = ct.get("snippet", {}).get("topLevelComment", {})
    cs = top.get("snippet", {})
    author = (cs.get("authorChannelId") or {}).get("value")
    ents = text_entities(cs.get("textOriginal", ""))
    return ItemIn(source=source, source_type="youtube", external_id=f"comment:{top.get('id')}",
                  url=f"https://www.youtube.com/watch?v={vid}", channel=video_channel.get("channelTitle"),
                  author_handle=author, author_id=author, author_display_name=cs.get("authorDisplayName"),
                  published_at=parse_iso(cs.get("publishedAt")), text=clean_summary(cs.get("textOriginal", "")),
                  hashtags=ents["hashtags"], mentions=ents["mentions"], urls=ents["urls"],
                  engagement={"likes": cs.get("likeCount"), "replies": ct.get("snippet", {}).get("totalReplyCount")},
                  reply_to_external_id=f"video:{vid}",
                  meta={"video_id": vid, "kind": "comment", "reply_to_author_id": video_channel.get("channelId")})


def channel_account(ch: dict[str, Any]) -> AccountIn:
    sn, st = ch.get("snippet", {}), ch.get("statistics", {})
    return AccountIn(platform="youtube", platform_id=ch["id"], handle=sn.get("customUrl") or ch["id"],
                     display_name=sn.get("title"), url=f"https://www.youtube.com/channel/{ch['id']}",
                     created_at=parse_iso(sn.get("publishedAt")),
                     followers=None if st.get("hiddenSubscriberCount") else _int(st.get("subscriberCount")),
                     posts_count=_int(st.get("videoCount")), description=clean_summary(sn.get("description"), 1000) or None,
                     meta={"views": _int(st.get("viewCount")), "country": sn.get("country"),
                           "uploads": (ch.get("contentDetails") or {}).get("relatedPlaylists", {}).get("uploads")})


@register
class YouTubeSource(Source):
    """cfg: keywords, region (IN), max_results, comments_per_video, max_units_per_run, relevance_language."""

    type_name = "youtube"
    required_settings = ("youtube_api_key",)
    capabilities = {"terms", "accounts"}

    def hosts(self) -> list[str]:
        return [API + "/search"]

    def _get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        return self.client.get_json(f"{API}/{endpoint}", params={**params, "key": get_settings().youtube_api_key},
                                    cache_ttl=3600, check_robots=False)

    def fetch(self, since: datetime | None) -> FetchResult:
        return self._search(self.cfg.get("keywords", []), since, int(self.cfg.get("max_results", 25)))

    def search_terms(self, terms_by_lang: dict[str, list[str]], since: datetime | None, limit: int) -> FetchResult:
        matcher = TermMatcher(terms_by_lang)
        res = self._search([t.lstrip("#") for t in flat_terms(terms_by_lang)], since, min(50, limit))
        for it in res.items:
            it.meta["matched_terms"] = matcher.match(it.text, it.hashtags) or [it.meta.get("keyword", "")]
        return res

    def _search(self, keywords: list[str], since: datetime | None, max_results: int) -> FetchResult:
        budget = int(self.cfg.get("max_units_per_run", 1500))
        after = (since or utcnow() - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        res = FetchResult()
        for kw in keywords:
            if budget < 100:
                log.warning("youtube_quota_budget_reached", extra={"source": self.name})
                res.notes.append("per-run quota budget reached; remaining keywords skipped")
                break
            params = {"part": "snippet", "q": kw, "type": "video", "regionCode": self.cfg.get("region", "IN"),
                      "maxResults": max_results, "publishedAfter": after, "order": "date"}
            if self.cfg.get("relevance_language"):
                params["relevanceLanguage"] = self.cfg["relevance_language"]
            data = self._get("search", params)
            budget -= 100
            vids = []
            for it in data.get("items", []):
                vid = (it.get("id") or {}).get("videoId")
                sn = it.get("snippet", {})
                if not vid:
                    continue
                vids.append(vid)
                res.items.append(video_item(self.name, vid, sn, {"keyword": kw}))
                if sn.get("channelId"):
                    res.accounts.append(AccountIn(platform="youtube", platform_id=sn["channelId"],
                                                  display_name=sn.get("channelTitle"),
                                                  url=f"https://www.youtube.com/channel/{sn['channelId']}"))
            budget = self._enrich_videos(res, vids, budget)
        return res

    def _enrich_videos(self, res: FetchResult, vids: list[str], budget: int) -> int:
        """videos.list statistics (1 unit per 50 ids) and top comments (1 unit per video)."""
        by_id = {i.external_id[6:]: i for i in res.items if i.external_id.startswith("video:")}
        for i in range(0, len(vids), 50):
            if budget < 1:
                break
            budget -= 1
            try:
                data = self._get("videos", {"part": "statistics", "id": ",".join(vids[i : i + 50])})
            except Exception as e:  # noqa: BLE001
                log.debug("youtube_stats_failed", extra={"error": str(e)})
                continue
            for v in data.get("items", []):
                st = v.get("statistics", {})
                if v.get("id") in by_id:
                    by_id[v["id"]].engagement = {"views": _int(st.get("viewCount")), "likes": _int(st.get("likeCount")),
                                                 "replies": _int(st.get("commentCount"))}
        n = int(self.cfg.get("comments_per_video", 20))
        for vid in vids:
            if not n or budget < 1:
                break
            budget -= 1
            video = by_id.get(vid)
            vch = {"channelId": video.author_id, "channelTitle": video.channel} if video else {}
            try:
                cd = self._get("commentThreads", {"part": "snippet", "videoId": vid, "maxResults": n,
                                                  "order": "relevance", "textFormat": "plainText"})
            except Exception as e:  # noqa: BLE001 (comments disabled etc.)
                log.debug("youtube_comments_failed", extra={"video": vid, "error": str(e)})
                continue
            for ct in cd.get("items", []):
                c = comment_item(self.name, vid, vch, ct)
                res.items.append(c)
                if c.author_id:
                    res.accounts.append(AccountIn(platform="youtube", platform_id=c.author_id,
                                                  display_name=c.author_display_name))
        return budget

    # ---------------------------------------------------------------- accounts
    def account_profile(self, handle: str) -> AccountIn:
        params: dict[str, Any] = {"part": "snippet,statistics,contentDetails"}
        if handle.startswith("UC"):
            params["id"] = handle
        else:
            params["forHandle"] = handle if handle.startswith("@") else "@" + handle
        items = self._get("channels", params).get("items") or []
        if not items:
            raise LookupError(f"YouTube channel not found: {handle}")
        return channel_account(items[0])

    def account_posts(self, handle: str, since: datetime | None, limit: int) -> FetchResult:
        profile = self.account_profile(handle)
        res = FetchResult(accounts=[profile])
        uploads = profile.meta.get("uploads")
        if not uploads:
            return res
        data = self._get("playlistItems", {"part": "snippet", "playlistId": uploads, "maxResults": min(50, limit)})
        vids = []
        for it in data.get("items", []):
            sn = it.get("snippet", {})
            vid = (sn.get("resourceId") or {}).get("videoId")
            when = parse_iso(sn.get("publishedAt"))
            if not vid or (since and when and when < since):
                continue
            vids.append(vid)
            res.items.append(video_item(self.name, vid, {**sn, "channelId": profile.platform_id,
                                                         "channelTitle": profile.display_name}, {"uploads": uploads}))
        self._enrich_videos(res, vids, int(self.cfg.get("max_units_per_run", 1500)))
        return res
