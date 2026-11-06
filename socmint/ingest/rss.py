"""RSS/Atom feeds (fact-checkers and news). Stores metadata + short summary only, never article bodies."""

from __future__ import annotations

import calendar
import re
from datetime import UTC, datetime
from typing import Any

import feedparser

from socmint.core.http import HttpError, OfflineError
from socmint.core.logging import get_logger
from socmint.core.schemas import FactCheckIn, FetchResult, ItemIn
from socmint.ingest.base import Source, TermMatcher, clean_summary, register

log = get_logger(__name__)
_IMG_RE = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']", re.I)


def _entry_time(e: Any) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = e.get(key)
        if t:
            return datetime.fromtimestamp(calendar.timegm(t), UTC).replace(tzinfo=None)
    return None


def _image_urls(e: Any) -> list[str]:
    urls: list[str] = []
    for m in e.get("media_thumbnail", []) or []:
        urls.append(m.get("url", ""))
    for m in e.get("media_content", []) or []:
        if (m.get("medium") == "image") or str(m.get("type", "")).startswith("image"):
            urls.append(m.get("url", ""))
    for enc in e.get("enclosures", []) or []:
        if str(enc.get("type", "")).startswith("image"):
            urls.append(enc.get("href", ""))
    for c in e.get("content", []) or []:
        urls.extend(_IMG_RE.findall(c.get("value", ""))[:1])
    urls.extend(_IMG_RE.findall(e.get("summary", ""))[:1])
    out = []
    for u in urls:
        if u and u.startswith("http") and u not in out:
            out.append(u)
    return out[:2]


@register
class RSSSource(Source):
    """cfg: url, publisher, lang, kind (factcheck|news), hash_images (bool), max_images (int)."""

    type_name = "rss"
    capabilities = {"terms"}

    def search_terms(self, terms_by_lang: dict[str, list[str]], since: datetime | None, limit: int) -> FetchResult:
        """Keyword filter over the feed (feeds hold ~10-50 recent entries, so this only sees recent news)."""
        matcher = TermMatcher(terms_by_lang)
        res = self.fetch(since)
        out = FetchResult()
        for it in res.items:
            hits = matcher.match(it.text)
            if hits and (since is None or it.published_at is None or it.published_at >= since):
                it.meta["matched_terms"] = hits
                out.items.append(it)
        out.factchecks = [f for f in res.factchecks if matcher.match(f"{f.title} {f.claim_text or ''}")]
        out.items = out.items[:limit]
        return out

    def fetch(self, since: datetime | None) -> FetchResult:
        body = self.client.get(self.cfg["url"], cache_ttl=self.cfg.get("cache_ttl_s", 900))
        feed = feedparser.parse(body)
        if feed.bozo and not feed.entries:
            raise ValueError(f"unparseable feed: {feed.get('bozo_exception')}")
        kind = self.cfg.get("kind", "news")
        publisher = self.cfg.get("publisher") or feed.feed.get("title", self.name)
        lang = self.cfg.get("lang")
        res = FetchResult()
        images_left = int(self.cfg.get("max_images", 10)) if self.cfg.get("hash_images") else 0
        for e in feed.entries:
            link = e.get("link")
            if not link:
                continue
            published = _entry_time(e)
            title = clean_summary(e.get("title", ""), 500)
            summary = clean_summary(e.get("summary", ""))
            if kind == "factcheck":
                hashes: list[str] = []
                if images_left > 0:
                    for img in _image_urls(e):
                        h = self._hash_remote_image(img)
                        if h:
                            hashes.append(h)
                        images_left -= 1
                res.factchecks.append(
                    FactCheckIn(
                        publisher=publisher, title=title, url=link, claim_text=summary or title,
                        lang=lang, published_at=published, source=self.name, image_hashes=hashes,
                    )
                )
            else:
                res.items.append(
                    ItemIn(
                        source=self.name, source_type="news", external_id=e.get("id") or link, url=link,
                        channel=publisher, published_at=published, lang=lang,
                        text=f"{title}. {summary}".strip(". "), meta={"publisher": publisher}, urls=[link],
                    )
                )
        return res

    def _hash_remote_image(self, url: str) -> str | None:
        """Download an image into memory, return its pHash; the image itself is never written to disk."""
        from socmint.understand.media import phash_bytes

        try:
            data = self.client.get(url, cache_ttl=0, check_robots=True)
            return phash_bytes(data)
        except (HttpError, OfflineError, OSError, ValueError) as e:
            log.debug("image_hash_failed", extra={"url": url, "error": str(e)})
            return None
