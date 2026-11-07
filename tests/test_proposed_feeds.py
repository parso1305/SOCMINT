"""M18.3: official publisher news feeds are only *proposed*: present in sources.yaml, disabled, with publisher and
language recorded, never used by an event until the analyst enables them. No Google News RSS."""

from __future__ import annotations

from socmint.core.settings import get_settings, load_config
from socmint.investigate.runner import resolve_sources


def proposed() -> list[dict]:
    return [c for c in load_config("sources")["sources"]
            if str(c.get("disabled_reason", "")).startswith("PROPOSED - awaiting approval")]


def test_proposed_feeds_are_disabled_official_rss_with_publisher_and_language():
    feeds = proposed()
    assert 1 <= len(feeds) <= 10
    for c in feeds:
        assert c["enabled"] is False and c["type"] == "rss" and c["kind"] == "news", c["name"]
        assert c["publisher"] and c["lang"] in ("en", "hi", "mr"), c["name"]
        assert c["url"].startswith("https://") and "news.google" not in c["url"], c["name"]
        assert c.get("terms_note"), c["name"]  # what the publisher's terms say, for the approval decision


def test_proposed_feeds_are_never_used_by_an_event():
    names = {c["name"] for c in resolve_sources("news_rss")}
    assert names and not names & {c["name"] for c in proposed()}


def test_each_proposed_feed_carries_the_marker_comment():
    text = (get_settings().config_dir / "sources.yaml").read_text(encoding="utf-8")
    for c in proposed():
        block = text[text.index(f"name: {c['name']}") - 200: text.index(f"name: {c['name']}")]
        assert "# PROPOSED - awaiting approval" in block, c["name"]


def test_no_google_news_anywhere():
    text = (get_settings().config_dir / "sources.yaml").read_text(encoding="utf-8")
    assert "news.google." not in text
