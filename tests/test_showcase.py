"""M15: the one-page showcase renders every tab for each demo investigation, offline, with neutral wording."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "socmint" / "showcase" / "app.py"
ADVICE = re.compile(r"\b(buy|sell|hold)\b", re.IGNORECASE)


@pytest.fixture(scope="module")
def showcase_db(tmp_path_factory):
    """The synthetic showcase investigations, seeded once into an isolated DB named like the demo DB."""
    import os

    from socmint import fixtures
    from socmint.analyze import impersonation
    from socmint.core import db
    from socmint.core.settings import reset_settings
    from socmint.demo import seed_investigations
    from socmint.ingest.registry import sync_brands, sync_sources
    from socmint.understand.entities import clear_caches

    path = tmp_path_factory.mktemp("showcase") / "demo.db"
    os.environ.update({"SOCMINT_DB_URL": f"sqlite:///{path.as_posix()}", "SOCMINT_OFFLINE": "1",
                       "EMBEDDING_BACKEND": "tfidf", "PRIVACY_SALT": "t"})
    reset_settings()
    db.get_engine.cache_clear()
    db.init_db()
    sync_sources()
    sync_brands()
    impersonation.brands.cache_clear()
    fixtures.load_symbols()
    fixtures.load_market()
    clear_caches()
    out = seed_investigations()
    return os.environ["SOCMINT_DB_URL"], {k: v["id"] for k, v in out.items()}


def _use(url: str, monkeypatch) -> None:
    from socmint.core import db
    from socmint.core.settings import reset_settings

    monkeypatch.setenv("SOCMINT_DB_URL", url)
    reset_settings()
    db.get_engine.cache_clear()


def _all_text(at: AppTest) -> str:
    parts = [e.value for kind in ("title", "markdown", "caption", "subheader", "info", "warning", "error", "success")
             for e in getattr(at, kind)]
    parts += [str(m.value) for m in at.metric]
    for frame in at.dataframe:
        parts.append(frame.value.to_string())
    return "\n".join(str(p) for p in parts)


@pytest.mark.parametrize("which", ["event", "subject", "group"])
def test_every_tab_renders(showcase_db, which, monkeypatch):
    url, ids = showcase_db
    _use(url, monkeypatch)
    at = AppTest.from_file(str(APP), default_timeout=120)
    at.session_state["inv_id"] = ids[which]
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert [t.label for t in at.tabs] == ["Overview", "Posts", "People & accounts", "Network", "Evidence & export"]
    assert any("DEMO DATA (synthetic)" in w.value for w in at.warning)
    assert len(at.metric) >= 4 and at.metric[0].value != "0"
    text = _all_text(at)
    assert "Key findings" in text and "Collection log" in text
    assert not ADVICE.search(text), ADVICE.search(text)
    if which == "event":
        assert "Possible coordination: 8 accounts" in text
        assert "punerelief.help@okdemo" in text
    if which == "subject":
        assert "Manipulation-risk alert: DEMOPUMP" in text and "not investment advice" in text
        assert "Multibagger Tips" in text
    if which == "group":
        assert "Group members" in text


def test_views_are_audited_and_post_detail_works(showcase_db, monkeypatch):
    from sqlalchemy import select

    from socmint.core.db import session_scope
    from socmint.core.models import AuditLog

    url, ids = showcase_db
    _use(url, monkeypatch)
    at = AppTest.from_file(str(APP), default_timeout=120)
    at.session_state["inv_id"] = ids["event"]
    at.run()
    detail = next(sb for sb in at.selectbox if sb.label == "Post detail")
    detail.select_index(len(detail.options) - 1)
    at.run()
    assert not at.exception
    with session_scope() as s:
        views = s.scalars(select(AuditLog.target).where(AuditLog.action == "investigation_view")).all()
    assert any(f"id={ids['event']} via=showcase" in v for v in views)


def test_form_helpers_and_network(showcase_db, monkeypatch):
    from pydantic import ValidationError

    from socmint.showcase import data

    url, ids = showcase_db
    _use(url, monkeypatch)
    assert data.parse_terms("flood, बाढ़\n#PuneRains") == {"en": ["flood", "#PuneRains"], "hi": ["बाढ़"]}
    with pytest.raises(ValidationError, match="purpose is required"):
        data.build_config("Event", "x", " ", "flood", data.default_sources())
    cfg = data.build_config("Person / Account", "y", "test", "bluesky:someone.example\nmastodon:@a@b.example",
                            ["bluesky", "mastodon", "gdelt"])
    assert cfg.kind == "subject" and cfg.sources == ["bluesky", "mastodon"]
    d = data.load(ids["group"])
    dot, n = data.network_dot(d)
    assert n >= 8 and "co_posts" in dot and dot.startswith("digraph")
    findings = data.key_findings(data.load(ids["event"]))
    assert 3 <= len(findings) <= 5 and findings[0].startswith("Possible coordination")


def test_posts_table_puts_text_and_categories_first(showcase_db, monkeypatch):
    """P7 (B4): the columns an analyst reads come first and fit at 1366 px; the id is last and hidden."""
    from socmint.showcase import data

    url, ids = showcase_db
    _use(url, monkeypatch)
    d = data.load(ids["event"])
    table = data.posts_table(d["posts"])
    assert list(table.columns) == ["time (UTC)", "platform", "author", "text", "categories", "lang", "link", "id"]
    assert table["text"].str.len().max() <= 141 and table["text"].str.endswith("…").any()
    assert sum(data.POSTS_COLUMN_WIDTHS.values()) <= 880  # main area at 1366 px with the sidebar open
    at = AppTest.from_file(str(APP), default_timeout=120)
    at.session_state["inv_id"] = ids["event"]
    at.run()
    shown = next(f for f in at.dataframe if "categories" in f.value.columns)
    assert "id" not in shown.proto.column_order and list(shown.proto.column_order)[3] == "text"
    detail = "\n".join(m.value for m in at.markdown)
    assert "Evidence:" in detail  # each label's evidence is shown in the post detail


def test_posts_over_time_bucket_follows_the_span():
    """P8 (B3): hourly under 48 h, 6-hourly up to 7 days, daily beyond; the bucket is named for the chart."""
    from datetime import datetime, timedelta

    from socmint.showcase import data

    def frame(hours: int, step: int = 1):
        t0 = datetime(2026, 11, 1)
        rows = [{"hour": t0 + timedelta(hours=h), "platform": "bluesky", "posts": 2} for h in range(0, hours, step)]
        return data.posts_over_time({"summary": {"by_hour": rows}})

    df, label = frame(30)
    assert label == "hour" and len(df) == 30
    df, label = frame(5 * 24)  # the demo event spans about 5 days
    assert label == "6 hours" and len(df) == 20 and df["bluesky"].max() == 12
    df, label = frame(29 * 24, step=5)
    assert label == "day" and len(df) == 29
    assert data.posts_over_time({"summary": {"by_hour": []}})[0].empty


def test_source_chips_use_friendly_names(showcase_db, monkeypatch):
    """P10: chips say "Bluesky (demo) · 77 new", not "demo_bluesky: ok (77 new)"; live feeds use their publisher."""
    from socmint.showcase import data

    assert data.source_label("demo_bluesky") == "Bluesky (demo)"
    assert data.source_label("demo_news_rss") == "News (demo)"
    assert data.source_label("indianexpress_india") == "Indian Express"
    assert data.source_label("gdelt_india") == "GDELT"
    assert data.source_label("telegram_public") == "Telegram"
    assert data.source_label("bluesky") == "Bluesky"
    assert data.source_label("something_new") == "Something New"
    url, ids = showcase_db
    _use(url, monkeypatch)
    d = data.load(ids["event"])
    chips = data.source_status(d)
    assert {c["label"] for c in chips} >= {"Bluesky (demo)", "YouTube (demo)", "News (demo)"}
    # M20.3: a chip shows the investigation's total posts from that source, not the last run's new count
    by_source = d["summary"]["by_source"]
    assert all(c["text"] == f"{c['label']} · {by_source[c['source']]} posts" for c in chips)
    group = data.source_status(data.load(ids["group"]))  # its last runs found 0 new: the posts were already linked
    assert group and all(c["posts"] > 0 and c["text"].endswith(f" · {c['posts']} posts") for c in group)
    at = AppTest.from_file(str(APP), default_timeout=120)
    at.session_state["inv_id"] = ids["event"]
    at.run()
    md = "\n".join(m.value for m in at.markdown)
    assert f"Bluesky (demo) · {by_source['demo_bluesky']} posts" in md and "demo_bluesky:" not in md
    assert any("Collection log" in c.value for c in at.caption)  # where the last run per source is shown


def test_chip_names_a_failed_last_run_and_keeps_the_search_mode():
    from socmint.showcase import data

    d = {"summary": {"by_source": {"demo_bluesky": 5}, "collection": {"latest_by_source": {
        "demo_bluesky": {"status": "ok", "new": 0, "fetched": 3, "detail": "", "mode": "live listen 60 s"},
        "demo_mastodon": {"status": "throttled", "new": 0, "fetched": 0, "detail": "HTTP 429", "mode": None}}}}}
    chips = {c["source"]: c for c in data.source_status(d)}
    assert chips["demo_bluesky"]["text"] == "Bluesky (demo) · live listen 60 s · 5 posts"
    assert chips["demo_mastodon"]["text"] == "Mastodon (demo) · 0 posts · throttled"
    assert chips["demo_bluesky"]["last"] == "last run: ok, 0 new of 3 fetched"
