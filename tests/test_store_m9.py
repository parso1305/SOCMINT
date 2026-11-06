"""M9: post fields, accounts, edges, investigation links and the additive migration (offline)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select, text

from socmint.core.db import session_scope
from socmint.core.models import Account, Edge, InvestigationAccount, InvestigationItem, Item
from socmint.core.schemas import AccountIn, ItemIn


def _post(ext: str, **kw) -> ItemIn:
    base = {"source": "bluesky", "source_type": "bluesky", "external_id": ext, "text": "Road closed #PuneRains",
            "author_id": "did:plc:a1", "author_handle": "a1.example", "published_at": datetime(2026, 7, 21, 10)}
    return ItemIn(**{**base, **kw})


def test_upsert_items_returns_ids_and_refreshes_engagement():
    from socmint.ingest.store import upsert_items

    with session_scope() as s:
        ids, new = upsert_items(s, [_post("p1", engagement={"likes": 1}), _post("p2")])
        assert len(ids) == 2 and new == set(ids.values())
    with session_scope() as s:
        ids2, new2 = upsert_items(s, [_post("p1", engagement={"likes": 9})])
        assert not new2 and ids2[("bluesky", "p1")] == ids[("bluesky", "p1")]
    with session_scope() as s:
        row = s.get(Item, ids[("bluesky", "p1")])
        assert row.engagement == {"likes": 9} and s.scalar(select(func.count(Item.id))) == 2


def test_store_accounts_upsert_keeps_known_fields():
    from socmint.ingest.store import store_accounts

    with session_scope() as s:
        ids, new = store_accounts(s, [AccountIn(platform="mastodon", platform_id="u@m.example", handle="u@m.example",
                                                followers=10, description="bio")])
        assert ids == {"mastodon:u@m.example": next(iter(new))}
    with session_scope() as s:
        ids2, new2 = store_accounts(s, [AccountIn(platform="mastodon", platform_id="u@m.example", followers=12)])
        assert not new2
        row = s.get(Account, ids2["mastodon:u@m.example"])
        assert row.followers == 12 and row.description == "bio"  # missing fields are not wiped


def test_edges_for_forward_reply_mention_url_hashtag():
    from socmint.ingest.store import store_edges, upsert_items

    p = _post("p3", hashtags=["PuneRains"], mentions=["@b.example"], urls=["https://relief.example/x"],
              forwarded_from="did:plc:c3", meta={"mention_ids": ["did:plc:b2"], "reply_to_author_id": "did:plc:d4"})
    with session_scope() as s:
        ids, _ = upsert_items(s, [p])
        assert store_edges(s, ids.values(), investigation_id=7) == 5
        assert store_edges(s, ids.values(), investigation_id=7) == 0  # idempotent
    with session_scope() as s:
        got = {(e.kind, e.dst) for e in s.scalars(select(Edge)).all()}
        assert got == {("forward", "bluesky:did:plc:c3"), ("reply", "bluesky:did:plc:d4"),
                       ("mention", "bluesky:did:plc:b2"), ("shares_url", "https://relief.example/x"),
                       ("uses_hashtag", "#punerains")}
        assert all(e.src == "bluesky:did:plc:a1" for e in s.scalars(select(Edge)).all())


def test_link_items_merges_matched_by_and_link_accounts_never_downgrades():
    from socmint.ingest.store import link_accounts, link_items, store_accounts, upsert_items

    with session_scope() as s:
        ids, _ = upsert_items(s, [_post("p4")])
        iid = ids[("bluesky", "p4")]
        link_items(s, 1, {iid: {"source": "bluesky", "terms": ["#PuneRains"]}})
    with session_scope() as s:
        assert link_items(s, 1, {iid: {"source": "bluesky", "terms": ["Pune flood"]}}) == 0
        acc, _ = store_accounts(s, [AccountIn(platform="bluesky", platform_id="did:plc:a1")])
        aid = acc["bluesky:did:plc:a1"]
        link_accounts(s, 1, [(aid, "subject", "analyst")])
        link_accounts(s, 1, [(aid, "top_author", "collector")])
    with session_scope() as s:
        row = s.scalar(select(InvestigationItem))
        assert row.matched_by["terms"] == ["#PuneRains", "Pune flood"]
        assert s.scalar(select(InvestigationAccount)).role == "subject"


def test_additive_migration_adds_new_item_columns(tmp_path, monkeypatch):
    from socmint.core import db
    from socmint.core.settings import reset_settings

    url = f"sqlite:///{(tmp_path / 'old.db').as_posix()}"
    monkeypatch.setenv("SOCMINT_DB_URL", url)
    reset_settings()
    db.get_engine.cache_clear()
    with db.get_engine(url).begin() as conn:  # an items table as it was before M9
        conn.execute(text("CREATE TABLE items (id INTEGER PRIMARY KEY, source VARCHAR(100), source_type VARCHAR(50), "
                          "external_id VARCHAR(512), url TEXT, channel VARCHAR(255), author_handle VARCHAR(255), "
                          "published_at DATETIME, ingested_at DATETIME, lang VARCHAR(16), text TEXT, "
                          "media_hash VARCHAR(64), metadata JSON, synthetic BOOLEAN)"))
        conn.execute(text("INSERT INTO items (source, source_type, external_id, text, metadata, synthetic) "
                          "VALUES ('old', 'news', 'x1', 'old row', '{}', 0)"))
    db.init_db(url)
    with db.session_scope(url) as s:
        row = s.scalar(select(Item))
        assert row.text == "old row" and row.hashtags is None and row.engagement is None
