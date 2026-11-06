"""Mastodon public REST API (no key): hashtag timelines for events, account lookup + statuses for subjects.

Verified live on mastodon.social 2026-11-06 (see SOURCES.md): `/api/v1/timelines/tag/:hashtag`,
`/api/v1/accounts/lookup` and `/api/v1/accounts/:id/statuses` answer without auth; the unauthenticated
limit is 300 requests / 5 min per IP, so the default spacing is 2 s per host.

Opt-out: accounts with `noindex: true` (asked search engines not to index them) or `indexable: false`
(opted out of search) are skipped entirely: no profile, no posts.
Full-text status search needs a logged-in user, so events are collected through hashtags only.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import quote

from socmint.core.logging import get_logger
from socmint.core.schemas import AccountIn, FetchResult, ItemIn
from socmint.ingest.base import OptedOut, Source, TermMatcher, clean_summary, flat_terms, parse_iso, register

log = get_logger(__name__)

_SPAN_RE = re.compile(r"</?span[^>]*>", re.I)
_ANCHOR_RE = re.compile(r"<a\s([^>]*)>", re.I)
_HREF_RE = re.compile(r'href="([^"]+)"', re.I)
_CLASS_RE = re.compile(r'class="([^"]*)"', re.I)
_NONWORD = re.compile(r"[^\wऀ-ॿ]+")


def opted_out(account: dict[str, Any] | None) -> bool:
    a = account or {}
    return a.get("noindex") is True or a.get("indexable") is False


def term_to_tag(term: str) -> str:
    """'#PuneRains' -> 'PuneRains'; 'Pune flood' -> 'Puneflood'; 'पुणे पूर' -> 'पुणेपूर'."""
    return _NONWORD.sub("", term.lstrip("#").strip())


def full_acct(acct: str, instance: str) -> str:
    acct = acct.lstrip("@")
    return acct if "@" in acct else f"{acct}@{instance}"


def html_to_text(content: str) -> str:
    """Strip HTML; link text survives intact (Mastodon splits URLs across invisible spans)."""
    return clean_summary(_SPAN_RE.sub("", content or ""), 3000)


def content_links(content: str) -> list[str]:
    out = []
    for attrs in _ANCHOR_RE.findall(content or ""):
        cls = (_CLASS_RE.search(attrs) or [None, ""])[1]
        href = (_HREF_RE.search(attrs) or [None, ""])[1]
        if href and "mention" not in cls and "hashtag" not in cls:
            out.append(href.replace("&amp;", "&"))
    return out


def account_from(a: dict[str, Any], instance: str, synthetic: bool = False) -> AccountIn:
    acct = full_acct(a.get("acct") or a.get("username", ""), instance)
    return AccountIn(
        platform="mastodon", platform_id=acct, handle=acct, display_name=a.get("display_name") or None,
        url=a.get("url"), created_at=parse_iso(a.get("created_at")), followers=a.get("followers_count"),
        following=a.get("following_count"), posts_count=a.get("statuses_count"),
        description=html_to_text(a.get("note", "")) or None,
        meta={"bot": a.get("bot"), "group": a.get("group"), "instance": acct.split("@", 1)[1]}, synthetic=synthetic)


def parse_status(st: dict[str, Any], instance: str, source: str, matched_terms: list[str] | None = None,
                 synthetic: bool = False) -> tuple[ItemIn, AccountIn] | None:
    """Mastodon Status -> (post, author). None for opted-out authors or non-public posts."""
    a = st.get("account") or {}
    if opted_out(a) or st.get("visibility") not in ("public", "unlisted", None):
        return None
    acct = full_acct(a.get("acct", ""), instance)
    mentions = [m for m in st.get("mentions") or []]
    meta: dict[str, Any] = {"mention_ids": [full_acct(m.get("acct", ""), instance) for m in mentions],
                            "instance": instance, "status_id": st.get("id")}
    if st.get("in_reply_to_id"):
        target = st.get("in_reply_to_account_id")
        if target == a.get("id"):
            meta["reply_to_author_id"] = acct
        else:
            hit = next((m for m in mentions if m.get("id") == target), None)
            if hit:
                meta["reply_to_author_id"] = full_acct(hit.get("acct", ""), instance)
    if matched_terms:
        meta["matched_terms"] = matched_terms
    urls = content_links(st.get("content", ""))
    card = (st.get("card") or {}).get("url")
    if card:
        urls.append(card)
    item = ItemIn(
        source=source, source_type="mastodon", external_id=st.get("uri") or st.get("url") or str(st.get("id")),
        url=st.get("url") or st.get("uri"), author_handle=acct, author_id=acct,
        author_display_name=a.get("display_name") or None, published_at=parse_iso(st.get("created_at")),
        lang=st.get("language"), text=html_to_text(st.get("content", "")),
        hashtags=[t.get("name") for t in st.get("tags") or [] if t.get("name")],
        mentions=["@" + full_acct(m.get("acct", ""), instance) for m in mentions], urls=list(dict.fromkeys(urls)),
        engagement={"likes": st.get("favourites_count"), "reposts": st.get("reblogs_count"),
                    "replies": st.get("replies_count"), "quotes": st.get("quotes_count")},
        reply_to_external_id=f"{instance}:{st['in_reply_to_id']}" if st.get("in_reply_to_id") else None,
        media=[{"type": m.get("type"), "url": m.get("preview_url") or m.get("url"), "phash": None}
               for m in st.get("media_attachments") or []],
        meta=meta, synthetic=synthetic)
    return item, account_from(a, instance, synthetic)


@register
class MastodonSource(Source):
    """cfg: instances (list of hosts), page_limit (pages of 40 per tag/account), hashtags (optional, global run)."""

    type_name = "mastodon"
    capabilities = {"terms", "accounts"}

    def instances(self) -> list[str]:
        return list(self.cfg.get("instances") or ["mastodon.social"])

    def hosts(self) -> list[str]:
        return [f"https://{i}/" for i in self.instances()]

    def fetch(self, since: datetime | None) -> FetchResult:
        tags = self.cfg.get("hashtags") or []
        return self.search_terms({"any": tags}, since, int(self.cfg.get("limit", 200))) if tags else FetchResult()

    def _pages(self, url: str, since: datetime | None, limit: int, params: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        max_id = None
        for _ in range(int(self.cfg.get("page_limit", 3))):
            p = dict(params, limit=40)
            if max_id:
                p["max_id"] = max_id
            page = self.client.get_json(url, params=p, cache_ttl=300)
            if not page:
                break
            for st in page:
                created = parse_iso(st.get("created_at"))
                if since and created and created < since:
                    return out
                out.append(st)
                if len(out) >= limit:
                    return out
            max_id = page[-1].get("id")
        return out

    def search_terms(self, terms_by_lang: dict[str, list[str]], since: datetime | None, limit: int) -> FetchResult:
        matcher = TermMatcher(terms_by_lang)
        tags: dict[str, list[str]] = {}
        for t in flat_terms(terms_by_lang):
            tag = term_to_tag(t)
            if tag:
                tags.setdefault(tag.lower(), []).append(t)
        res = FetchResult()
        seen: set[str] = set()
        skipped = 0
        per_tag = max(20, limit // max(len(tags), 1))
        for inst in self.instances():
            for tag, terms in tags.items():
                url = f"https://{inst}/api/v1/timelines/tag/{quote(tag)}"
                for st in self._pages(url, since, per_tag, {}):
                    hits = matcher.match(html_to_text(st.get("content", "")),
                                         [t.get("name", "") for t in st.get("tags") or []]) or terms
                    parsed = parse_status(st, inst, self.name, hits)
                    if parsed is None:
                        skipped += 1
                        continue
                    if parsed[0].external_id in seen:
                        continue
                    seen.add(parsed[0].external_id)
                    res.items.append(parsed[0])
                    res.accounts.append(parsed[1])
        res.notes.append(f"hashtags searched: {', '.join('#' + t for t in tags) or '-'} on {', '.join(self.instances())}")
        if skipped:
            res.notes.append(f"{skipped} posts skipped: author opted out (noindex / not indexable) or not public")
        return res

    def _lookup(self, handle: str) -> tuple[str, dict[str, Any]]:
        user, _, domain = handle.lstrip("@").partition("@")
        inst = domain or self.instances()[0]
        data = self.client.get_json(f"https://{inst}/api/v1/accounts/lookup", params={"acct": user}, cache_ttl=600)
        if opted_out(data):
            raise OptedOut(f"mastodon account {handle} has noindex / indexable=false; not collected")
        return inst, data

    def account_profile(self, handle: str) -> AccountIn:
        inst, data = self._lookup(handle)
        return account_from(data, inst)

    def account_posts(self, handle: str, since: datetime | None, limit: int) -> FetchResult:
        inst, data = self._lookup(handle)
        res = FetchResult(accounts=[account_from(data, inst)])
        url = f"https://{inst}/api/v1/accounts/{data['id']}/statuses"
        for st in self._pages(url, since, limit, {"exclude_reblogs": "true"}):
            parsed = parse_status(st, inst, self.name)
            if parsed:
                res.items.append(parsed[0])
        return res
