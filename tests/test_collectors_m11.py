"""M11: existing collectors are investigation-aware and fill the post fields (offline; HTTP is faked)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from socmint.core.http import HttpError
from socmint.ingest.base import UnsupportedCapability

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>News</title>
<item><title>Pune flood: Khadakwasla dam discharge raised</title><link>https://news.example/1</link>
<description>Roads closed.</description><pubDate>Tue, 21 Jul 2026 06:30:05 +0000</pubDate></item>
<item><title>पुणे पूर: शाळांना सुट्टी</title><link>https://news.example/2</link>
<description>मराठी बातमी</description><pubDate>Tue, 21 Jul 2026 07:30:05 +0000</pubDate></item>
<item><title>Cricket final tonight</title><link>https://news.example/3</link><description>x</description>
<pubDate>Tue, 21 Jul 2026 08:30:05 +0000</pubDate></item>
</channel></rss>""".encode()


class Fake:
    """Fake PoliteClient: `route(url, params)` returns bytes / JSON-able / Exception."""

    user_agent = "test"

    def __init__(self, route):
        self.route, self.calls = route, []

    def set_rate_limit(self, host, seconds):
        pass

    def forget(self, url, params=None, **kw):
        pass

    def get(self, url, params=None, **kw):
        self.calls.append((url, params or {}))
        out = self.route(url, params or {})
        if isinstance(out, Exception):
            raise out
        return out if isinstance(out, bytes) else json.dumps(out).encode()

    def get_json(self, url, **kw):
        return json.loads(self.get(url, **kw))


def test_rss_search_terms_filters_multilingual_and_sets_urls():
    from socmint.ingest.rss import RSSSource

    src = RSSSource({"name": "n", "type": "rss", "kind": "news", "url": "https://news.example/feed"},
                    client=Fake(lambda u, p: RSS))
    res = src.search_terms({"en": ["Pune flood"], "mr": ["पुणे पूर"]}, None, 50)
    assert [i.url for i in res.items] == ["https://news.example/1", "https://news.example/2"]
    assert res.items[0].urls == ["https://news.example/1"] and res.items[1].meta["matched_terms"] == ["पुणे पूर"]
    assert src.search_terms({"en": ["Pune flood"]}, datetime(2026, 7, 21, 7), 50).items == []  # since filter


def test_rss_has_no_account_capability():
    from socmint.ingest.rss import RSSSource

    src = RSSSource({"name": "n", "type": "rss", "url": "https://x.example"}, client=Fake(lambda u, p: RSS))
    assert "accounts" not in src.capabilities
    with pytest.raises(UnsupportedCapability):
        src.account_posts("someone", None, 5)


def test_gdelt_event_query_and_start_datetime():
    from socmint.ingest.gdelt import GdeltSource, event_query

    assert event_query(["Pune flood", "#PuneRains", "pu"]) == '("Pune flood" OR PuneRains)'
    body = {"articles": [{"url": "https://n.example/a", "title": "Pune flood update", "seendate": "20260721T081500Z",
                          "domain": "n.example", "language": "English"}]}
    client = Fake(lambda u, p: body)
    res = GdeltSource({"name": "g", "type": "gdelt", "event_query_suffix": "sourcecountry:IN"}, client=client) \
        .search_terms({"en": ["Pune flood", "Khadakwasla dam"]}, datetime(2026, 7, 20), 100)
    q = client.calls[0][1]
    assert q["query"] == '("Pune flood" OR "Khadakwasla dam") sourcecountry:IN' and "startdatetime" in q
    assert res.items[0].meta["matched_terms"] == ["Pune flood"] and res.items[0].urls == ["https://n.example/a"]


def test_gdelt_plaintext_answers_fail_cleanly():
    from socmint.core.http import Throttled
    from socmint.ingest.gdelt import GdeltSource

    # a plain-text query error is an error; a plain-text "limit requests" answer is a throttle (M17.2 cooldown)
    src = GdeltSource({"name": "g", "type": "gdelt"}, client=Fake(lambda u, p: b"The specified phrase is too short."))
    with pytest.raises(RuntimeError, match="no JSON"):
        src.search_terms({"en": ["Pune flood"]}, None, 10)
    src = GdeltSource({"name": "g", "type": "gdelt"}, client=Fake(lambda u, p: b"Please limit requests"))
    with pytest.raises(Throttled, match="next try after"):
        src.search_terms({"en": ["Pune flood"]}, None, 10)


YT_SEARCH = {"items": [{"id": {"videoId": "vid1"}, "snippet": {
    "publishedAt": "2026-07-21T09:00:00Z", "channelId": "UCdemo000000000000000001", "channelTitle": "Demo Tips (fictional)",
    "title": "Pune flood live #PuneRains", "description": "Join https://t.me/demo_tips for updates"}}]}
YT_STATS = {"items": [{"id": "vid1", "statistics": {"viewCount": "1500", "likeCount": "40", "commentCount": "3"}}]}
YT_COMMENTS = {"items": [{"snippet": {"totalReplyCount": 1, "topLevelComment": {"id": "c1", "snippet": {
    "authorChannelId": {"value": "UCviewer00000000000000001"}, "authorDisplayName": "Viewer (fictional)",
    "publishedAt": "2026-07-21T09:30:00Z", "textOriginal": "Is the dam safe? @demo_tips", "likeCount": 2}}}}]}
YT_CHANNEL = {"items": [{"id": "UCdemo000000000000000001", "snippet": {
    "title": "Demo Tips (fictional)", "customUrl": "@demotips", "publishedAt": "2025-01-01T00:00:00Z",
    "description": "Fictional channel"}, "statistics": {"subscriberCount": "900", "videoCount": "12", "viewCount": "5000"},
    "contentDetails": {"relatedPlaylists": {"uploads": "UUdemo000000000000000001"}}}]}
YT_UPLOADS = {"items": [{"snippet": {"publishedAt": "2026-07-22T09:00:00Z", "title": "DEMOPUMP target",
                                     "description": "", "resourceId": {"videoId": "vid2"}}},
                        {"snippet": {"publishedAt": "2026-01-01T09:00:00Z", "title": "old", "description": "",
                                     "resourceId": {"videoId": "vid0"}}}]}


def _yt_route(url, params):
    for k, v in {"/search": YT_SEARCH, "/videos": YT_STATS, "/commentThreads": YT_COMMENTS, "/channels": YT_CHANNEL,
                 "/playlistItems": YT_UPLOADS}.items():
        if url.endswith(k):
            return v
    return HttpError("404", 404)


def test_youtube_terms_fill_post_fields_engagement_comments(monkeypatch):
    from socmint.ingest.youtube import YouTubeSource

    monkeypatch.setenv("YOUTUBE_API_KEY", "k")
    from socmint.core.settings import reset_settings

    reset_settings()
    res = YouTubeSource({"name": "yt", "type": "youtube"}, client=Fake(_yt_route)).search_terms(
        {"en": ["#PuneRains"]}, None, 25)
    video, comment = res.items
    assert video.author_id == "UCdemo000000000000000001" and video.hashtags == ["PuneRains"]
    assert video.engagement == {"views": 1500, "likes": 40, "replies": 3} and video.urls == ["https://t.me/demo_tips"]
    assert video.meta["matched_terms"] == ["#PuneRains"]
    assert comment.reply_to_external_id == "video:vid1" and comment.meta["reply_to_author_id"] == video.author_id
    assert comment.mentions == ["@demo_tips"] and {a.platform_id for a in res.accounts} >= {video.author_id}


def test_youtube_channel_profile_and_uploads(monkeypatch):
    from socmint.core.settings import reset_settings
    from socmint.ingest.youtube import YouTubeSource

    monkeypatch.setenv("YOUTUBE_API_KEY", "k")
    reset_settings()
    client = Fake(_yt_route)
    res = YouTubeSource({"name": "yt", "type": "youtube", "comments_per_video": 0}, client=client).account_posts(
        "@demotips", datetime(2026, 7, 1), 20)
    acc = res.accounts[0]
    assert acc.handle == "@demotips" and acc.followers == 900 and acc.posts_count == 12
    assert [i.external_id for i in res.items] == ["video:vid2"]
    assert client.calls[0][1]["forHandle"] == "@demotips"


def test_reddit_search_is_restricted_to_listed_subreddits(monkeypatch):
    from socmint.core.settings import reset_settings
    from socmint.ingest.reddit import RedditSource

    monkeypatch.setenv("REDDIT_CLIENT_ID", "i")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "s")
    reset_settings()
    listing = {"data": {"children": [{"data": {
        "name": "t3_a", "permalink": "/r/demo/comments/a/x", "subreddit": "demo", "author": "demo_user",
        "created_utc": datetime(2026, 7, 21, tzinfo=UTC).timestamp(), "title": "Pune flood photos",
        "selftext": "see relief.example", "score": 5, "num_comments": 2, "is_self": True}}]}}

    def route(url, params):
        return {"access_token": "t"} if "access_token" in url else listing

    client = Fake(route)
    res = RedditSource({"name": "r", "type": "reddit", "subreddits": ["demo"]}, client=client).search_terms(
        {"en": ["Pune flood", "#PuneRains"]}, None, 50)
    url, params = client.calls[-1]
    assert url.endswith("/r/demo/search") and params["restrict_sr"] == 1 and params["q"] == '"Pune flood" OR PuneRains'
    it = res.items[0]
    assert it.author_id == "demo_user" and it.urls == ["http://relief.example"] and it.engagement["replies"] == 2
    empty = RedditSource({"name": "r", "type": "reddit", "subreddits": []}, client=client).search_terms(
        {"en": ["x"]}, None, 5)
    assert not empty.items and "only within listed subreddits" in empty.notes[0]


def test_telegram_message_parsing_forward_and_engagement():
    from socmint.ingest.telegram_channels import message_item

    m = SimpleNamespace(id=42, message="DEMOPUMP sure shot! join t.me/demo_vip #DEMOPUMP",
                        date=datetime(2026, 7, 22, 9, tzinfo=UTC), views=900, forwards=12,
                        fwd_from=SimpleNamespace(from_id=SimpleNamespace(channel_id=777), from_name=None),
                        reply_to=None, replies=SimpleNamespace(replies=4),
                        reactions=SimpleNamespace(results=[SimpleNamespace(count=3), SimpleNamespace(count=2)]),
                        media=None, photo=None)
    it = message_item(m, "multibagger_tips_demo", "1001", "telegram_public")
    assert it.external_id == "multibagger_tips_demo:42" and it.author_id == "1001" and it.forwarded_from == "777"
    assert it.engagement == {"views": 900, "forwards": 12, "replies": 4, "reactions": 5}
    assert it.hashtags == ["DEMOPUMP"] and it.published_at == datetime(2026, 7, 22, 9)
    assert message_item(SimpleNamespace(id=1, message="  "), "c", "1", "t") is None
