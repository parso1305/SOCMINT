"""M10: Bluesky and Mastodon collectors parse recorded (synthetic, real-shape) responses; opt-outs are skipped."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from socmint.core.http import HttpError
from socmint.ingest.base import OptedOut, TermMatcher

FIX = Path(__file__).parent / "fixtures"


def load(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


class RouteClient:
    """Fake PoliteClient: `route(url, params)` returns a JSON-able body or raises."""

    user_agent = "test"

    def __init__(self, route):
        self.route = route
        self.calls: list[tuple[str, dict]] = []

    def set_rate_limit(self, host, seconds):
        pass

    def allowed(self, url):
        return True

    def get_json(self, url, params=None, **kw):
        self.calls.append((url, params or {}))
        out = self.route(url, params or {})
        if isinstance(out, Exception):
            raise out
        return out


# ------------------------------------------------------------------------------------------- Bluesky
def test_bluesky_post_view_parsing_facets_reply_quote_engagement():
    from socmint.ingest.bluesky import parse_post_view

    posts = load("bluesky_posts.json")["posts"]
    item, acc = parse_post_view(posts[0], "bluesky", ["#PuneRains"])
    assert item.external_id == "at://did:plc:demoalpha111/app.bsky.feed.post/3kdemo1"
    assert item.url == "https://bsky.app/profile/alpha-demo.bsky.social/post/3kdemo1"
    assert item.author_id == "did:plc:demoalpha111" and item.author_handle == "alpha-demo.bsky.social"
    assert item.hashtags == ["PuneRains"] and item.mentions == ["@beta-demo.bsky.social"]
    assert item.meta["mention_ids"] == ["did:plc:demobeta222"] and item.urls == ["https://relief.example/info"]
    assert item.reply_to_external_id.endswith("3kparent") and item.meta["reply_to_author_id"] == "did:plc:demogamma333"
    assert item.engagement == {"likes": 11, "reposts": 5, "replies": 2, "quotes": 1}
    assert item.media == [{"type": "image", "url": "https://cdn.example/f.jpg", "phash": None}]
    assert item.lang == "en" and item.published_at == datetime(2026, 7, 21, 10, 15)
    assert acc.platform == "bluesky" and acc.platform_id == "did:plc:demoalpha111"
    quote, _ = parse_post_view(posts[1], "bluesky")
    assert quote.forwarded_from == "did:plc:demoalpha111" and quote.meta["quote_of"].endswith("3kdemo1")


def test_bluesky_no_unauthenticated_label_is_skipped_everywhere():
    from socmint.ingest.bluesky import BlueskySource, parse_post_view

    assert parse_post_view(load("bluesky_posts.json")["posts"][2], "bluesky") is None
    src = BlueskySource({"name": "bluesky"}, client=RouteClient(lambda u, p: load("bluesky_profile_optout.json")))
    with pytest.raises(OptedOut):
        src.account_profile("quiet-demo.bsky.social")
    with pytest.raises(OptedOut):
        src.account_posts("quiet-demo.bsky.social", None, 10)


def test_bluesky_search_falls_back_to_jetstream_and_hydrates(monkeypatch):
    from socmint.ingest import bluesky

    posts = {p["uri"]: p for p in load("bluesky_posts.json")["posts"]}

    def route(url, params):
        if url.endswith("searchPosts"):
            return HttpError("HTTP 403", 403)
        if url.endswith("getPosts"):
            return {"posts": [posts[u] for u in params["uris"] if u in posts]}
        raise AssertionError(url)

    src = bluesky.BlueskySource({"name": "bluesky"}, client=RouteClient(route))
    monkeypatch.setenv("SOCMINT_OFFLINE", "0")
    messages = load("jetstream_messages.json")

    async def fake_listen(self, url, matcher, since, limit, max_s, max_n):
        return len(messages), bluesky.match_jetstream(messages, matcher, since, limit)

    monkeypatch.setattr(bluesky.BlueskySource, "_listen", fake_listen)
    res = src.search_terms({"en": ["#PuneRains", "Pune flood"], "hi": ["पुणे बाढ़"]}, None, 50)
    assert [i.external_id.rsplit("/", 1)[-1] for i in res.items] == ["3kdemo1", "3kdemo2"]  # opted-out 3kdemo3 dropped
    assert res.items[0].meta["matched_terms"] == ["#PuneRains"] and res.items[1].meta["matched_terms"] == ["पुणे बाढ़"]
    assert "403" in res.notes[0] and any("not returned publicly or opted out" in n for n in res.notes)


def test_jetstream_matching_ignores_deletes_noise_and_old_posts():
    from socmint.ingest.bluesky import match_jetstream

    msgs = load("jetstream_messages.json")
    got = match_jetstream(msgs, TermMatcher(["#PuneRains", "Pune flood"]), datetime(2026, 7, 21, 10, 30), 50)
    assert list(got) == ["at://did:plc:demooptout444/app.bsky.feed.post/3kdemo3"]  # 3kdemo1 is before `since`


def test_bluesky_author_feed_skips_pins_reposts_and_stops_at_since():
    from socmint.ingest.bluesky import BlueskySource

    def route(url, params):
        return load("bluesky_profile.json") if url.endswith("getProfile") else load("bluesky_author_feed.json")

    res = BlueskySource({"name": "bluesky"}, client=RouteClient(route)).account_posts(
        "alpha-demo.bsky.social", datetime(2026, 7, 1), 50)
    assert [i.external_id.rsplit("/", 1)[-1] for i in res.items] == ["3knew"]
    acc = res.accounts[0]
    assert acc.followers == 120 and acc.posts_count == 345 and acc.created_at == datetime(2024, 1, 2, 3, 4, 5)
    assert "<b>" not in acc.description


# ------------------------------------------------------------------------------------------- Mastodon
def test_mastodon_tag_timeline_parsing_and_opt_outs():
    from socmint.ingest.mastodon import MastodonSource

    client = RouteClient(lambda u, p: load("mastodon_tag.json") if "max_id" not in p else [])
    src = MastodonSource({"name": "mastodon", "instances": ["mastodon.social"]}, client=client)
    res = src.search_terms({"en": ["#PuneRains"]}, datetime(2026, 7, 20), 100)
    assert client.calls[0][0] == "https://mastodon.social/api/v1/timelines/tag/punerains"
    assert len(res.items) == 1  # noindex author, indexable=false author and the pre-window post are skipped
    it = res.items[0]
    assert it.text.startswith("@river_demo Roads closed") and "relief.example/roads?a=1&b=2" in it.text
    assert it.urls == ["https://relief.example/roads?a=1&b=2"] and it.hashtags == ["punerains"]
    assert it.mentions == ["@river_demo@mastodon.social"] and it.meta["reply_to_author_id"] == "river_demo@mastodon.social"
    assert it.engagement == {"likes": 9, "reposts": 4, "replies": 1, "quotes": 0}
    assert it.author_id == "rain_demo@social.example" and it.meta["matched_terms"] == ["#PuneRains"]
    assert it.media[0]["url"] == "https://files.example/small.jpg"
    assert any("opted out" in n for n in res.notes)
    assert res.accounts[0].followers == 40 and res.accounts[0].description == "Fictional weather volunteer"


def test_mastodon_account_lookup_statuses_and_noindex_skip():
    from socmint.ingest.mastodon import MastodonSource

    def route(url, params):
        if url.endswith("/lookup"):
            return load("mastodon_lookup_noindex.json") if params["acct"] == "private_demo" else load("mastodon_lookup.json")
        return load("mastodon_statuses.json") if "max_id" not in params else []

    src = MastodonSource({"name": "mastodon", "instances": ["mastodon.social"]}, client=RouteClient(route))
    res = src.account_posts("@river_demo@mastodon.social", None, 10)
    assert res.accounts[0].handle == "river_demo@mastodon.social" and res.accounts[0].followers == 1200
    assert [i.meta["status_id"] for i in res.items] == ["2002", "2001"]
    assert res.items[0].meta["reply_to_author_id"] == "river_demo@mastodon.social"  # self-reply thread
    with pytest.raises(OptedOut):
        src.account_profile("private_demo")


def test_term_matcher_multilingual_and_hashtags():
    m = TermMatcher({"en": ["Pune flood", "#PuneRains"], "mr": ["खडकवासला"], "hi": ["पुणे बाढ़"]})
    assert m.match("PUNE FLOOD alert near Khadakwasla") == ["Pune flood"]
    assert m.match("roads shut", ["punerains"]) == ["#PuneRains"]
    assert m.match("खडकवासला धरणातून विसर्ग") == ["खडकवासला"]
    assert m.match("पुणे बाढ़ की पुरानी वीडियो") == ["पुणे बाढ़"]
    assert m.match("Punefloods are bad") == []
