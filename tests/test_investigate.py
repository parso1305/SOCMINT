"""M13: investigation lifecycle with mocked sources (offline): create -> collect -> link -> enrich -> summary ->
export -> scoped analysis -> promote -> delete, with audit rows throughout."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from socmint.core.db import session_scope
from socmint.core.http import OfflineError
from socmint.core.models import (
    Account,
    Alert,
    AuditLog,
    CollectionRun,
    Edge,
    Indicator,
    InvestigationAccount,
    InvestigationItem,
    Item,
    ItemLabel,
    utcnow,
)
from socmint.core.schemas import AccountIn, FetchResult, ItemIn
from socmint.ingest.base import OptedOut, Source
from socmint.investigate import report, runner
from socmint.investigate.config import InvestigationConfig

NOW = utcnow()
COORD = "Khadakwasla dam has burst, evacuate Sinhagad road now, share before this is deleted #PuneRains"


def post(ext: str, text: str, author: str, minutes_ago: float = 60, **kw: Any) -> ItemIn:
    return ItemIn(**{"source": "fake_bsky", "source_type": "bluesky", "external_id": ext, "text": text,
                     "author_id": f"did:plc:{author}", "author_handle": f"{author}.example",
                     "published_at": NOW - timedelta(minutes=minutes_ago), "url": f"https://bsky.example/{ext}", **kw})


class FakeSocial(Source):
    type_name = "fake_social"
    capabilities = {"terms", "accounts"}

    def fetch(self, since):
        return FetchResult()

    def search_terms(self, terms_by_lang, since, limit):
        items = [
            post("p1", "Roads closed near Khadakwasla dam, NDRF rescue teams deployed #PuneRains", "civic1",
                 hashtags=["PuneRains"], meta={"matched_terms": ["#PuneRains"]}),
            post("p2", "URGENT Pune flood relief: DemoBank donation verification, pay Rs 10 now to "
                       "punerelief.help@okdemo or visit demobank-relief.example today", "scam1",
                 urls=["http://demobank-relief.example"], mentions=["@civic1.example"],
                 meta={"mention_ids": ["did:plc:civic1"]}),
            post("shared", "Pune flood: water level rising at Khadakwasla dam", "subj1"),
            *[post(f"c{i}", COORD + ("" if i % 2 else " 🙏"), f"coord{i}", minutes_ago=30 + i * 0.5) for i in range(8)],
        ]
        return FetchResult(items=items, accounts=[AccountIn(platform="bluesky", platform_id="did:plc:civic1",
                                                            handle="civic1.example", followers=50)],
                           notes=["fake search"])

    def account_posts(self, handle, since, limit):
        if handle == "optout.example":
            raise OptedOut("label !no-unauthenticated")
        prof = AccountIn(platform="bluesky", platform_id="did:plc:subj1", handle="subj1.example",
                         display_name="Subject One (fictional)", followers=900, posts_count=40,
                         created_at=datetime(2024, 1, 1))
        return FetchResult(items=[post("shared", "Pune flood: water level rising at Khadakwasla dam", "subj1"),
                                  post("s2", "My own update about the dam", "subj1")], accounts=[prof])


class Broken(FakeSocial):
    def search_terms(self, terms_by_lang, since, limit):
        raise RuntimeError("upstream exploded")


class Offline(FakeSocial):
    def search_terms(self, terms_by_lang, since, limit):
        raise OfflineError("offline mode")


class NeedsKey(FakeSocial):
    def availability(self):
        return False, "missing key(s) in .env: X"


def factory(logical: str, cfg: dict[str, Any]) -> Source:
    cls = {"bluesky": FakeSocial, "news_rss": Broken, "gdelt": Offline, "mastodon": NeedsKey}[logical]
    return cls({**cfg, "name": f"fake_{logical}"})


@pytest.fixture(autouse=True)
def only_one_config_per_source(monkeypatch):
    monkeypatch.setattr(runner, "resolve_sources", lambda logical: [{"name": logical, "type": logical}])


def event_cfg(**kw) -> InvestigationConfig:
    return InvestigationConfig(**{"kind": "event", "name": "Pune floods test", "purpose": "Public-safety monitoring",
                                  "terms": {"en": ["Pune flood", "#PuneRains", "Khadakwasla dam"]},
                                  "places": ["Pune", "Sinhagad"], **kw})


def test_purpose_and_targets_are_required():
    with pytest.raises(ValidationError, match="purpose is required"):
        InvestigationConfig(kind="event", name="x", purpose="  ", terms={"en": ["a"]})
    with pytest.raises(ValidationError, match="needs at least one keyword"):
        InvestigationConfig(kind="event", name="x", purpose="p")
    with pytest.raises(ValidationError, match="unknown platform"):
        InvestigationConfig(kind="subject", name="x", purpose="p", handles={"instagram": ["a"]})
    assert event_cfg().sources == ["bluesky", "mastodon", "news_rss", "gdelt"]


def test_collect_isolates_sources_links_matches_and_enriches():
    iid = runner.create(event_cfg())
    out = runner.collect(iid, factory=factory)
    status = {r["source"]: r["status"] for r in out["runs"]}
    assert status == {"bluesky": "ok", "mastodon": "no_key", "news_rss": "error", "gdelt": "offline"}
    assert out["new_posts"] == 11
    with session_scope() as s:
        links = {s.get(Item, r.item_id).external_id: r.matched_by
                 for r in s.scalars(select(InvestigationItem).where(InvestigationItem.investigation_id == iid)).all()}
        assert links["p1"]["terms"] == ["#PuneRains"] and links["p1"]["source"] == "bluesky"
        assert set(links["c1"]["terms"]) == {"Khadakwasla dam", "#PuneRains"} and "Sinhagad" in links["c1"]["places"]
        assert s.scalar(select(func.count(CollectionRun.id)).where(CollectionRun.investigation_id == iid)) == 4
        assert s.scalar(select(CollectionRun.detail).where(CollectionRun.status == "error")).startswith("RuntimeError")
        civic = s.scalar(select(Account).where(Account.platform_id == "did:plc:civic1"))
        assert civic.followers == 50 and civic.handle == "civic1.example"
        assert s.scalar(select(func.count(InvestigationAccount.id)).where(InvestigationAccount.role == "top_author")) == 11
        kinds = {e.kind for e in s.scalars(select(Edge)).all()}
        assert {"uses_hashtag", "mention", "shares_url"} <= kinds
        p2 = s.scalar(select(Item).where(Item.external_id == "p2"))
        labels = {r.label: r for r in s.scalars(select(ItemLabel).where(ItemLabel.item_id == p2.id)).all()}
        assert "scam" in labels and labels["scam"].evidence["score"] >= 40 and "brand_impersonation" in labels
        assert p2.meta["entities"]["upi_ids"] == ["punerelief.help@okdemo"]
        assert s.scalar(select(Indicator).where(Indicator.type == "upi")).value == "punerelief.help@okdemo"
        p1 = s.scalar(select(Item).where(Item.external_id == "p1"))
        assert {r.label for r in s.scalars(select(ItemLabel).where(ItemLabel.item_id == p1.id)).all()} == {"civic_safety"}
        assert p1.lang == "en"


def test_summary_export_manifest_and_audit(tmp_path):
    iid = runner.create(event_cfg())
    runner.collect(iid, factory=factory)
    summ = report.summary(iid)
    assert summ["counts"]["posts"] == 11 and summ["by_source"] == {"fake_bsky": 11}
    assert summ["by_category"]["civic_safety"] >= 1 and summ["by_category"]["scam"] >= 1
    assert ("#punerains", 1) in summ["top_hashtags"] or summ["top_hashtags"][0][0] == "#punerains"
    assert any(i["type"] == "upi" for i in summ["indicators"])
    assert summ["collection"]["latest_by_source"]["news_rss"]["status"] == "error"
    for fmt in ("json", "csv"):
        folder = report.export(iid, fmt, out_dir=tmp_path)
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        assert {f["name"] for f in manifest["files"]} == {f"posts.{fmt}", f"accounts.{fmt}", f"edges.{fmt}",
                                                          f"indicators.{fmt}", "summary.json", "investigation.json"}
        for f in manifest["files"]:
            assert hashlib.sha256((folder / f["name"]).read_bytes()).hexdigest() == f["sha256"]
        assert manifest["counts"]["posts"] == 11
        if fmt == "json":
            posts = json.loads((folder / "posts.json").read_text(encoding="utf-8"))
            assert all(p["labels"] and p["matched_by"] for p in posts)
        else:
            assert (folder / "posts.csv").read_text(encoding="utf-8-sig").startswith("id,source,platform")
    assert report.zip_bundle(folder)[:2] == b"PK"
    with session_scope() as s:
        actions = set(s.scalars(select(AuditLog.action)).all())
    assert {"investigation_create", "investigation_collect", "investigation_export"} <= actions


def test_subject_collection_opt_out_and_delete_cleans_orphans():
    ev = runner.create(event_cfg())
    runner.collect(ev, factory=factory)
    with session_scope() as s:  # a post that came from the global ingest before any investigation
        s.add(Item(source="news_feed", source_type="news", external_id="global1", text="Pune flood news"))
        s.flush()
        gid = s.scalar(select(Item.id).where(Item.external_id == "global1"))
        s.add(InvestigationItem(investigation_id=ev, item_id=gid, matched_by={"source": "manual"}))
    subj = runner.create(InvestigationConfig(kind="subject", name="Subject test", purpose="Suspected fake appeals",
                                             handles={"bluesky": ["subj1.example", "optout.example"]}))
    out = runner.collect(subj, factory=factory)
    assert "skipped optout.example" in out["runs"][0]["detail"]
    accs = report.accounts(subj)
    assert accs[0]["role"] == "subject" and accs[0]["followers"] == 900 and accs[0]["posts_in_investigation"] == 2
    removed = runner.delete_investigation(ev)
    with session_scope() as s:
        ext = set(s.scalars(select(Item.external_id)).all())
        assert ext == {"shared", "s2", "global1"}  # shared with the subject + pre-existing global post are kept
        assert s.scalar(select(func.count(InvestigationItem.id)).where(InvestigationItem.investigation_id == ev)) == 0
        assert s.scalar(select(func.count(CollectionRun.id)).where(CollectionRun.investigation_id == ev)) == 0
        assert not s.scalar(select(Account).where(Account.platform_id == "did:plc:scam1"))
        assert s.scalar(select(Account).where(Account.platform_id == "did:plc:subj1")) is not None
        assert not s.scalars(select(ItemLabel).where(ItemLabel.item_id.not_in(
            select(Item.id)))).all()  # no labels left on deleted posts
        assert "investigation_delete" in set(s.scalars(select(AuditLog.action)).all())
    assert removed["posts"] == 10


def test_scoped_coordination_finds_group_and_promotes_it():
    iid = runner.create(event_cfg())
    runner.collect(iid, factory=factory)
    out = runner.analyze(iid)
    assert out["modules"]["coordination"]["status"] == "ok"
    runner.analyze(iid)  # re-running does not duplicate co_posts pairs
    with session_scope() as s:
        assert s.scalar(select(func.count(Edge.id)).where(Edge.kind == "co_posts", Edge.investigation_id == iid)) == 28
    groups = report.summary(iid)["coordination_groups"]
    top = groups[0]
    assert top["accounts"] == 8 and top["score"] >= 0.6
    gid = runner.promote_group(top["id"], "Dam rumour copy network", "Possible coordinated rumour")
    g = runner.get(gid)
    assert g.kind == "group" and g.config["promoted_from"]["coordination_group_id"] == top["id"]
    assert len(g.config["handles"]["bluesky"]) == 8
    with session_scope() as s:
        links = s.scalars(select(InvestigationAccount).where(InvestigationAccount.investigation_id == gid)).all()
        assert len(links) == 8 and {(r.role, r.added_by) for r in links} == {("member", "coordination")}
        assert s.scalar(select(func.count(Edge.id)).where(Edge.kind == "co_posts", Edge.investigation_id == gid)) == 28
    assert report.summary(gid)["counts"]["posts"] == 8


def test_no_narrative_growth_alerts_without_a_collection_baseline():
    """P9 (D1): a first collection has no baseline, so growth ratios are inflated; narratives yes, alerts no."""
    class Busy(FakeSocial):  # two clear stories, all posted in the last few hours
        def search_terms(self, terms_by_lang, since, limit):
            items = [post(f"r{i}", COORD + f" ({i})", f"r{i}", minutes_ago=10 + i * 7) for i in range(12)]
            items += [post(f"h{i}", f"NDRF rescue boats deployed in Sinhagad road area, helpline 1077 update {i}",
                           f"h{i}", minutes_ago=15 + i * 9) for i in range(12)]
            return FetchResult(items=items)

    iid = runner.create(event_cfg(sources=["bluesky"]))
    runner.collect(iid, factory=lambda logical, cfg: Busy({**cfg, "name": "fake_busy"}))
    res = runner.analyze(iid, modules=("narratives",))["modules"]["narratives"]["result"]
    assert res["clusters"] >= 1 and res["alerts"] == 0
    assert "growth alerts held back" in res["note"]
    with session_scope() as s:
        assert s.scalar(select(func.count(Alert.id)).where(Alert.module == "narratives")) == 0
        for run in s.scalars(select(CollectionRun).where(CollectionRun.investigation_id == iid)).all():
            run.started_at -= timedelta(hours=80)  # pretend it has been collecting for more than 72 h
    res = runner.analyze(iid, modules=("narratives",))["modules"]["narratives"]["result"]
    assert res["alerts"] >= 1 and "note" not in res


def test_baseline_note_keeps_the_module_note():
    """Found in verification: with too few posts, "not enough items" was overwritten by the baseline note."""
    iid = runner.create(event_cfg(sources=["bluesky"]))
    res = runner.analyze(iid, modules=("narratives",))["modules"]["narratives"]["result"]
    assert "not enough items in window" in res["note"] and "growth alerts held back" in res["note"]
