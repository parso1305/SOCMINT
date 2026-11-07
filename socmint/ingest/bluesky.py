"""Bluesky (AT Protocol) via the public AppView (no key) and, for event terms, a bounded Jetstream listen.

Live check 2026-11-06 (see SOURCES.md): on public.api.bsky.app, getProfile / getProfiles / getAuthorFeed /
getPosts answer without auth, but searchPosts returns HTTP 403. In `search_mode: auto` the source tries
searchPosts once and, on 401/403, listens to Jetstream (the official JSON firehose) for at most
`jetstream_max_seconds` / `jetstream_max_scanned` posts, matches terms client-side, then hydrates the
matches through getPosts (public counts and labels). We never switch hosts to get around the 403.

Optional logged-in search (M18.1): with BLUESKY_HANDLE + BLUESKY_APP_PASSWORD in .env, `auto` / `search` modes
call searchPosts through the account's own PDS (Bluesky's documented route for logged-in clients; see
`bluesky_auth`). Without the keys nothing changes. A logged-in failure is reported, never routed elsewhere.

Opt-out: accounts labelled `!no-unauthenticated` asked not to be shown to logged-out viewers; their
profile and posts are never collected, logged in or not (our output leaves the platform, so it is treated
like a logged-out view). Posts the AppView will not return (deleted, opted out) are dropped.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from typing import Any

from socmint.core.http import HttpError, OfflineError, RobotsDisallowed, is_offline
from socmint.core.logging import get_logger
from socmint.core.schemas import AccountIn, FetchResult, ItemIn
from socmint.ingest import bluesky_auth
from socmint.ingest.base import OptedOut, Source, TermMatcher, clean_summary, parse_iso, register

log = get_logger(__name__)

APPVIEW = "https://public.api.bsky.app/xrpc"
JETSTREAM = "wss://jetstream2.us-east.bsky.network/subscribe"
NO_UNAUTH = "!no-unauthenticated"
POST_NSID = "app.bsky.feed.post"


def _labels(obj: dict[str, Any] | None) -> list[str]:
    return [str(lab.get("val")) for lab in (obj or {}).get("labels", []) or [] if lab.get("val")]


def opted_out(author: dict[str, Any] | None) -> bool:
    return NO_UNAUTH in _labels(author)


def did_of(uri: str | None) -> str | None:
    """at://did:plc:xyz/app.bsky.feed.post/rkey -> did:plc:xyz"""
    if not uri or not uri.startswith("at://"):
        return None
    return uri[5:].split("/", 1)[0] or None


def _byte_slice(text: str, start: int, end: int) -> str:
    return text.encode("utf-8")[start:end].decode("utf-8", errors="ignore")


def parse_facets(text: str, facets: list[dict[str, Any]] | None) -> dict[str, list[str]]:
    """Rich-text facets -> hashtags, mentions (+ DIDs) and link URLs. Indices are UTF-8 byte offsets."""
    out: dict[str, list[str]] = {"hashtags": [], "mentions": [], "mention_ids": [], "urls": []}
    for f in facets or []:
        idx = f.get("index") or {}
        for feat in f.get("features") or []:
            t = feat.get("$type", "")
            if t.endswith("#tag") and feat.get("tag"):
                out["hashtags"].append(feat["tag"])
            elif t.endswith("#mention") and feat.get("did"):
                seg = _byte_slice(text, int(idx.get("byteStart", 0)), int(idx.get("byteEnd", 0))).strip()
                out["mentions"].append(seg if seg.startswith("@") else "@" + (seg or feat["did"]))
                out["mention_ids"].append(feat["did"])
            elif t.endswith("#link") and feat.get("uri"):
                out["urls"].append(feat["uri"])
    return {k: list(dict.fromkeys(v)) if k != "mention_ids" else v for k, v in out.items()}


def _media(view: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Image/video URLs from a post-view embed (URLs only; media is never downloaded)."""
    if not view:
        return []
    t = view.get("$type", "")
    if t.startswith("app.bsky.embed.recordWithMedia"):
        return _media(view.get("media"))
    if t.startswith("app.bsky.embed.images"):
        return [{"type": "image", "url": im.get("fullsize") or im.get("thumb"), "phash": None}
                for im in view.get("images") or []]
    if t.startswith("app.bsky.embed.video"):
        return [{"type": "video", "url": view.get("playlist"), "phash": None}]
    return []


def author_account(author: dict[str, Any], synthetic: bool = False) -> AccountIn:
    handle = author.get("handle")
    return AccountIn(
        platform="bluesky", platform_id=author["did"], handle=handle, display_name=author.get("displayName") or None,
        url=f"https://bsky.app/profile/{handle or author['did']}", created_at=parse_iso(author.get("createdAt")),
        followers=author.get("followersCount"), following=author.get("followsCount"),
        posts_count=author.get("postsCount"), description=clean_summary(author.get("description"), 1000) or None,
        meta={"labels": _labels(author)}, synthetic=synthetic)


def parse_post_view(pv: dict[str, Any], source: str, matched_terms: list[str] | None = None,
                    synthetic: bool = False) -> tuple[ItemIn, AccountIn] | None:
    """app.bsky.feed.defs#postView -> (post, author). None when the author opted out."""
    author = pv.get("author") or {}
    if not author.get("did") or opted_out(author):
        return None
    rec = pv.get("record") or {}
    text = rec.get("text", "") or ""
    fx = parse_facets(text, rec.get("facets"))
    hashtags = list(dict.fromkeys([*fx["hashtags"], *(rec.get("tags") or [])]))
    urls = list(fx["urls"])
    meta: dict[str, Any] = {"labels": _labels(pv), "mention_ids": fx["mention_ids"]}
    parent = ((rec.get("reply") or {}).get("parent") or {}).get("uri")
    if parent:
        meta["reply_to_author_id"] = did_of(parent)
        meta["reply_root"] = ((rec.get("reply") or {}).get("root") or {}).get("uri")
    forwarded_from = None
    emb = rec.get("embed") or {}
    et = emb.get("$type", "")
    quoted = None
    if et.startswith("app.bsky.embed.recordWithMedia"):
        quoted = ((emb.get("record") or {}).get("record") or {}).get("uri")
    elif et.startswith("app.bsky.embed.record"):
        quoted = (emb.get("record") or {}).get("uri")
    elif et.startswith("app.bsky.embed.external"):
        ext = (emb.get("external") or {}).get("uri")
        if ext:
            urls.append(ext)
    if quoted:  # a quote shares someone else's post: recorded as a forward edge (see DECISIONS.md)
        meta["quote_of"] = quoted
        forwarded_from = did_of(quoted)
    if matched_terms:
        meta["matched_terms"] = matched_terms
    handle = author.get("handle")
    rkey = pv.get("uri", "").rsplit("/", 1)[-1]
    item = ItemIn(
        source=source, source_type="bluesky", external_id=pv["uri"],
        url=f"https://bsky.app/profile/{handle or author['did']}/post/{rkey}", channel=None, author_handle=handle,
        author_id=author["did"], author_display_name=author.get("displayName") or None,
        published_at=parse_iso(rec.get("createdAt") or pv.get("indexedAt")), lang=(rec.get("langs") or [None])[0],
        text=clean_summary(text, 3000), hashtags=hashtags, mentions=fx["mentions"], urls=list(dict.fromkeys(urls)),
        engagement={"likes": pv.get("likeCount"), "reposts": pv.get("repostCount"), "replies": pv.get("replyCount"),
                    "quotes": pv.get("quoteCount")},
        reply_to_external_id=parent, forwarded_from=forwarded_from, media=_media(pv.get("embed")), meta=meta,
        synthetic=synthetic)
    return item, author_account(author, synthetic)


def match_jetstream(messages: list[dict[str, Any]], matcher: TermMatcher, since: datetime | None,
                    limit: int) -> dict[str, list[str]]:
    """Jetstream commit events -> {at-uri: matched terms} for newly created posts that match."""
    out: dict[str, list[str]] = {}
    for m in messages:
        c = m.get("commit") or {}
        if m.get("kind") != "commit" or c.get("operation") != "create" or c.get("collection") != POST_NSID:
            continue
        rec = c.get("record") or {}
        created = parse_iso(rec.get("createdAt"))
        if since and created and created < since:
            continue
        tags = parse_facets(rec.get("text", ""), rec.get("facets"))["hashtags"] + list(rec.get("tags") or [])
        hits = matcher.match(rec.get("text", ""), tags)
        if hits:
            out[f"at://{m.get('did')}/{POST_NSID}/{c.get('rkey')}"] = hits
            if len(out) >= limit:
                break
    return out


@register
class BlueskySource(Source):
    """cfg: search_mode (auto|search|jetstream), jetstream_url, jetstream_max_seconds, jetstream_max_scanned,
    page_limit, keywords (optional, for the global `socmint ingest` run)."""

    type_name = "bluesky"
    capabilities = {"terms", "accounts"}

    def hosts(self) -> list[str]:
        return [APPVIEW + "/"] + ([bluesky_auth.ENTRYWAY + "/"] if bluesky_auth.configured() else [])

    def fetch(self, since: datetime | None) -> FetchResult:
        kws = self.cfg.get("keywords") or []
        return self.search_terms({"any": kws}, since, int(self.cfg.get("limit", 100))) if kws else FetchResult()

    # ---------------------------------------------------------------- terms
    def search_terms(self, terms_by_lang: dict[str, list[str]], since: datetime | None, limit: int) -> FetchResult:
        """`auto`: logged-in search (if the keys are set) -> public searchPosts -> bounded Jetstream listen."""
        matcher = TermMatcher(terms_by_lang)
        mode = self.cfg.get("search_mode", "auto")
        if mode in ("auto", "search") and bluesky_auth.configured():
            return self._search_logged_in(matcher, since, limit)
        if mode in ("auto", "search"):
            try:
                res = self._search_api(matcher, since, limit)
                res.mode = "search (public)"
                return res
            except HttpError as e:
                if mode == "search" or e.status not in (401, 403):
                    raise
                res = self._listen_for(matcher, since, limit)
                res.notes.insert(0, f"searchPosts on the public AppView answered HTTP {e.status}; used a bounded "
                                    "Jetstream listen instead (only posts made during the listen window)")
                return res
        return self._listen_for(matcher, since, limit)

    def _listen_for(self, matcher: TermMatcher, since: datetime | None, limit: int) -> FetchResult:
        res = self._jetstream(matcher, since, limit)
        res.mode = f"live listen {float(self.cfg.get('jetstream_max_seconds', 60)):.0f} s"
        return res

    def _search_api(self, matcher: TermMatcher, since: datetime | None, limit: int) -> FetchResult:
        def public(params: dict[str, Any]) -> Any:
            return self.client.get_json(f"{APPVIEW}/app.bsky.feed.searchPosts", params=params, cache_ttl=300)

        return self._search(matcher, since, limit, public, follow_cursor=False)

    def _search_logged_in(self, matcher: TermMatcher, since: datetime | None, limit: int) -> FetchResult:
        if is_offline():
            raise OfflineError("offline mode: refusing to log in to Bluesky")
        session = bluesky_auth.BlueskySession(self.client)
        res = self._search(matcher, since, limit, lambda p: session.get_json("app.bsky.feed.searchPosts", p),
                           follow_cursor=True)
        res.mode = "search (logged in)"
        return res

    def _search(self, matcher: TermMatcher, since: datetime | None, limit: int, call: Any,
                follow_cursor: bool) -> FetchResult:
        """searchPosts per term (sort=latest, since, limit <= 100), following the cursor up to the term's share of
        `limit` when `follow_cursor`. Opted-out authors are dropped by `parse_post_view`."""
        res = FetchResult()
        seen: set[str] = set()
        per_term = max(10, limit // max(len(matcher.terms), 1))
        for term in matcher.terms:
            fetched, cursor = 0, None
            while fetched < per_term:
                params: dict[str, Any] = {"q": term, "limit": min(100, per_term - fetched), "sort": "latest"}
                if since:
                    params["since"] = since.strftime("%Y-%m-%dT%H:%M:%SZ")
                if cursor:
                    params["cursor"] = cursor
                data = call(params)
                posts = data.get("posts") or []
                fetched += len(posts)
                for pv in posts:
                    if pv.get("uri") in seen:
                        continue
                    seen.add(pv.get("uri"))
                    parsed = parse_post_view(pv, self.name,
                                             matcher.match((pv.get("record") or {}).get("text", "")) or [term])
                    if parsed:
                        res.items.append(parsed[0])
                        res.accounts.append(parsed[1])
                cursor = data.get("cursor")
                if not follow_cursor or not cursor or not posts:
                    break
        return res

    def _jetstream(self, matcher: TermMatcher, since: datetime | None, limit: int) -> FetchResult:
        if is_offline():
            raise OfflineError("offline mode: refusing to open Jetstream")
        url = self.cfg.get("jetstream_url", JETSTREAM)
        probe = url.replace("wss://", "https://", 1)
        if not self.client.allowed(probe):
            raise RobotsDisallowed(f"robots.txt disallows {probe}")
        max_s = float(self.cfg.get("jetstream_max_seconds", 60))
        max_n = int(self.cfg.get("jetstream_max_scanned", 20000))
        scanned, matched = asyncio.run(self._listen(url, matcher, since, limit, max_s, max_n))
        res = self._hydrate(matched)
        res.notes.append(f"Jetstream: scanned {scanned} new posts in <= {max_s:.0f} s, {len(matched)} matched, "
                         f"{len(res.items)} kept after public-AppView hydration and opt-out checks")
        return res

    async def _listen(self, url: str, matcher: TermMatcher, since: datetime | None, limit: int, max_s: float,
                      max_n: int) -> tuple[int, dict[str, list[str]]]:
        import websockets

        matched: dict[str, list[str]] = {}
        scanned = 0
        deadline = time.monotonic() + max_s
        async with websockets.connect(f"{url}?wantedCollections={POST_NSID}", open_timeout=20,
                                      user_agent_header=self.client.user_agent, max_size=2**21) as ws:
            while time.monotonic() < deadline and scanned < max_n and len(matched) < limit:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.monotonic()))
                except TimeoutError:
                    break
                msg = json.loads(raw)
                scanned += 1
                matched.update(match_jetstream([msg], matcher, since, limit - len(matched)))
        return scanned, matched

    def _hydrate(self, matched: dict[str, list[str]]) -> FetchResult:
        """getPosts (25 per call) -> full public post views with counts and labels; opted-out authors dropped."""
        res = FetchResult()
        uris = list(matched)
        dropped = 0
        for i in range(0, len(uris), 25):
            batch = uris[i : i + 25]
            data = self.client.get_json(f"{APPVIEW}/app.bsky.feed.getPosts", params={"uris": batch}, cache_ttl=0)
            views = {pv.get("uri"): pv for pv in data.get("posts") or []}
            for u in batch:
                pv = views.get(u)
                parsed = parse_post_view(pv, self.name, matched[u]) if pv else None
                if parsed is None:
                    dropped += 1
                    continue
                res.items.append(parsed[0])
                res.accounts.append(parsed[1])
        if dropped:
            res.notes.append(f"{dropped} matched posts not returned publicly or opted out; dropped")
        return res

    # ---------------------------------------------------------------- accounts
    def account_profile(self, handle: str) -> AccountIn:
        data = self.client.get_json(f"{APPVIEW}/app.bsky.actor.getProfile", params={"actor": handle.lstrip("@")},
                                    cache_ttl=600)
        if opted_out(data):
            raise OptedOut(f"bluesky account {handle} carries the {NO_UNAUTH} label; not collected")
        return author_account(data)

    def account_posts(self, handle: str, since: datetime | None, limit: int) -> FetchResult:
        profile = self.account_profile(handle)
        res = FetchResult(accounts=[profile])
        cursor = None
        pages = int(self.cfg.get("page_limit", 5))
        for _ in range(pages):
            params: dict[str, Any] = {"actor": profile.platform_id, "limit": 100, "filter": "posts_with_replies"}
            if cursor:
                params["cursor"] = cursor
            data = self.client.get_json(f"{APPVIEW}/app.bsky.feed.getAuthorFeed", params=params, cache_ttl=300)
            stop = False
            for entry in data.get("feed") or []:
                if entry.get("reason"):  # a repost of someone else's post, not this account's own post
                    continue
                parsed = parse_post_view(entry.get("post") or {}, self.name)
                if parsed is None:
                    continue
                item, author = parsed
                if since and item.published_at and item.published_at < since:
                    stop = True
                    break
                res.items.append(item)
                if len(res.items) >= limit:
                    stop = True
                    break
            cursor = data.get("cursor")
            if stop or not cursor:
                break
        return res
