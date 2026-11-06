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
