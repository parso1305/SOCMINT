"""M14: the synthetic showcase scenario (three investigations) through the real collect/analyze/promote path."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from socmint import scenario
from socmint.core.db import session_scope
from socmint.core.models import Account, Alert, CollectionRun, Investigation, Item, ItemLabel
from socmint.showcase.data import key_findings


def test_scenario_data_is_fictional_and_synthetic():
    posts = scenario.event_posts()
    assert 270 <= len(posts) <= 330
    assert all(p.synthetic for p in posts)
    assert {p.source_type for p in posts} == {"bluesky", "mastodon", "telegram", "reddit", "youtube", "news"}
    assert {p.lang for p in posts} >= {"en", "hi", "mr"}
    for p in posts:  # fictional handles / documentation domains only
        assert p.url.split("/")[2].endswith(".example")
        assert not p.author_handle or "demo" in p.author_handle or p.author_handle.endswith(".example")
    span = max(p.published_at for p in posts) - min(p.published_at for p in posts)
    assert span.days <= 5
    coord = [p for p in posts if p.meta["fixture"] == "coordinated"]
    assert len({p.author_id for p in coord}) == 8
    times = sorted(p.published_at for p in coord)
    assert (times[-1] - times[0]).total_seconds() <= 360
    tips = scenario.subject_posts()
    assert {a.handle for a in scenario.TIP_ACCOUNTS.values()} == {scenario.TIP_CHANNEL, scenario.TIP_YT}
    assert all(p.synthetic for v in tips.values() for p in v)
    # the stock-tip posts are promotional evidence, never phrased as buy/sell/hold advice by the system
    assert not any(w in p.text.lower().split() for v in tips.values() for p in v for w in ("buy", "sell", "hold"))


def test_seed_investigations_end_to_end():
    from socmint import fixtures
    from socmint.analyze import impersonation
    from socmint.demo import seed_investigations
    from socmint.ingest.registry import sync_brands
    from socmint.investigate import report
    from socmint.understand.entities import clear_caches

    sync_brands()
    impersonation.brands.cache_clear()
    fixtures.load_symbols()
    fixtures.load_market()
    clear_caches()
    out = seed_investigations()

    ev = report.summary(out["event"]["id"])
    assert ev["counts"]["posts"] >= 270 and len(ev["by_source"]) == 6
    assert {"civic_safety", "misinformation", "scam", "brand_impersonation"} <= set(ev["by_category"])
    assert {r["status"] for r in out["event"]["collect"]["runs"]} == {"ok"}
    top = ev["coordination_groups"][0]  # scoped coordination finds the 8-account group
    assert top["accounts"] == 8 and top["score"] >= 0.8
    assert any(i["type"] == "upi" and i["value"] == scenario.SCAM_UPI for i in ev["indicators"])
    assert any(i["value"] == scenario.SCAM_DOMAIN for i in ev["indicators"])
    assert any("Demosagar" in f["title"] for f in ev["factcheck_matches"])
    # M20.2: synthetic watch history, one run per source every 6 h across the 5-day window (demo only)
    hist = [r for r in ev["collection"]["runs"] if "backdated" in (r["detail"] or "")]
    starts = sorted({r["started_at"] for r in hist})
    assert len(starts) == 20 and all(b - a == timedelta(hours=6) for a, b in zip(starts, starts[1:], strict=False))
    assert {r["source"] for r in hist} == set(ev["by_source"]) and all(r["status"] == "ok" for r in hist)
    assert sum(r["new"] for r in hist) <= ev["counts"]["posts"]
    assert ev["history_hours"] >= 119 and ev["baseline_hours"] == 72
    assert "held back" not in (out["event"]["analyze"]["modules"]["narratives"]["result"].get("note") or "")
    assert any(f.startswith("Fastest-growing narrative") for f in key_findings({"summary": ev}))

    subj = report.summary(out["subject"]["id"])
    assert [a["role"] for a in report.accounts(out["subject"]["id"])] == ["subject", "subject"]
    assert subj["market_alerts"] and "DEMOPUMP" in subj["market_alerts"][0]["title"]
    assert "market_manipulation" in subj["by_category"]

    gid = out["group"]["id"]  # promoted from the event's top group
    with session_scope() as s:
        g = s.get(Investigation, gid)
        assert g.kind == "group" and g.config["promoted_from"]["coordination_group_id"] == top["id"]
        assert s.scalar(select(func.count(Investigation.id))) == 3
        assert all(s.scalars(select(Investigation.synthetic)).all())
        assert s.scalar(select(func.count(Item.id)).where(Item.synthetic.is_(False))) == 0
        assert s.scalar(select(func.count(Account.id)).where(Account.synthetic.is_(False))) == 0
        assert s.scalar(select(func.count(CollectionRun.id)).where(CollectionRun.synthetic.is_(False))) == 0
        assert s.scalar(select(func.count(Alert.id)).where(Alert.module == "market")) >= 1
    assert report.summary(gid)["counts"]["accounts"] == 8


def test_event_categories_are_not_just_the_event_theme():
    """P2 (B3): the demo flood event's categories say something beyond "it is about floods"."""
    from socmint import fixtures
    from socmint.analyze import impersonation
    from socmint.demo import seed_investigations
    from socmint.ingest.registry import sync_brands
    from socmint.investigate import report
    from socmint.understand.entities import clear_caches

    sync_brands()
    impersonation.brands.cache_clear()
    fixtures.load_symbols()
    fixtures.load_market()
    clear_caches()
    ev = report.summary(seed_investigations()["event"]["id"])
    cats, total = ev["by_category"], ev["counts"]["posts"]
    assert {"scam", "misinformation", "brand_impersonation", "civic_safety"} <= set(cats)
    rumour = [n for n in ev["narratives"] if {"demosagar", "dam", "burst"} & set(n["keywords"][:4])]
    assert rumour, [n["keywords"] for n in ev["narratives"]]  # P3: the dam rumour has meaningful keywords
    assert not any({"going", "getting", "worse"} & set(n["keywords"]) for n in ev["narratives"])
    assert cats["civic_safety"] / total < 0.75, cats  # was 268/288 (93%) when the event terms counted
    with session_scope() as s:  # every civic_safety label rests on a keyword beyond the event's own terms
        for lab in s.scalars(select(ItemLabel).where(ItemLabel.label == "civic_safety")).all():
            kws = lab.evidence["keywords"] + sum(lab.evidence["sublabel_keywords"].values(), [])
            assert kws and not {"flood", "floods", "dam", "पूर", "बाढ़"} & set(kws), lab.evidence
