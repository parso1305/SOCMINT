"""M5: narrative clustering + keywords + growth + fact-check linking; Chain Coherence Score v0."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from socmint import fixtures
from socmint.core.db import session_scope
from socmint.core.models import Alert, CoordinationGroup, Narrative, utcnow


@pytest.fixture(autouse=True)
def demo():
    from socmint.analyze import claim_match

    fixtures.load_factchecks()
    fixtures.load_items()
    claim_match.reset_index()
    yield


def test_narratives_clusters_keywords_growth_and_debunk_link():
    from socmint.analyze.narratives import run

    out = run()
    assert out["clusters"] >= 4
    snake_fc = fixtures.factcheck_url("fc_snake")
    with session_scope() as s:
        rows = s.scalars(select(Narrative)).all()
        assert all(n.keywords for n in rows)
        snake = max((n for n in rows if "snake" in n.keywords), key=lambda n: n.size)
        assert snake.count_24h >= 5 and snake.growth_rate >= 2
        from socmint.core.models import FactCheck

        linked_urls = {s.get(FactCheck, i).url for i in snake.matched_factcheck_ids}
        assert snake_fc in linked_urls
        high = s.scalars(select(Alert).where(Alert.module == "narratives", Alert.severity == "high")).all()
        assert any("snake" in a.evidence["keywords"] for a in high)
        dam = [n for n in rows if "dam" in n.keywords]
        assert dam and not any(n.matched_factcheck_ids for n in dam)  # no false debunk link
        a = high[0]
        assert {"count_24h", "prev_72h", "growth_rate", "sample_item_ids", "matched_factchecks"} <= set(a.evidence)


def test_ctfidf_keywords_multiscript():
    from socmint.analyze.narratives import ctfidf_keywords

    kw = ctfidf_keywords({0: ["डेमो नदी में विशाल सांप", "विशाल सांप दिखा नदी में"], 1: ["bus travel free", "free bus city"]}, 3)
    assert "सांप" in kw[0] and "विशाल" in kw[0] and "bus" in kw[1]


def test_coordination_detects_demo_set_with_evidence():
    from socmint.analyze.coordination import run

    out = run()
    assert out["alerts"] >= 1
    with session_scope() as s:
        groups = s.scalars(select(CoordinationGroup).order_by(CoordinationGroup.score.desc())).all()
        top = groups[0]
        assert "Demonagar dam" in top.sample_text
        assert top.n_accounts == 8 and top.n_channels == 3 and top.synchrony == 1.0 and top.score >= 0.8
        assert len(top.edges) == 28 and len(top.evidence["posts"]) == 8
        control = next(g for g in groups if "bridge repair" in g.sample_text)
        assert control.score < 0.6  # organic resharing over days is not flagged
        alert = s.scalar(select(Alert).where(Alert.module == "coordination"))
        assert alert.title.startswith("Possible coordination") and "bot" not in alert.title.lower()
        assert "not proof" in alert.evidence["label"]


def test_group_metrics_score_components():
    from socmint.analyze.coordination import group_metrics

    t0 = utcnow()
    tight = group_metrics([t0 + timedelta(minutes=i) for i in range(5)], [f"a{i}" for i in range(5)], ["c1", "c2", "c3", "c1", "c2"])
    loose = group_metrics([t0 + timedelta(days=i) for i in range(5)], [f"a{i}" for i in range(5)], ["c1"] * 5)
    assert tight["score"] > loose["score"] and tight["synchrony"] == 1.0 and loose["components"]["burst"] == 0.0


def test_hate_module_disabled():
    from socmint.analyze import hate

    assert not hate.is_enabled() and hate.get_classifier().classify("x") is None and hate.run()["enabled"] is False


def test_narrative_keywords_skip_filler_words():
    """P3 (B3): the demo dam rumour is described by what it is about, not by "going, getting, worse"."""
    from socmint import scenario
    from socmint.analyze.narratives import ctfidf_keywords

    posts = scenario.event_posts()
    rumour = [p.text for p in posts if p.meta["fixture"] == "rumour" and p.lang == "en"]
    civic = [p.text for p in posts if p.meta["fixture"] == "civic"]
    kw = ctfidf_keywords({0: rumour, 1: civic}, 6)
    assert {"demosagar", "dam", "burst"} & set(kw[0][:4]), kw[0]
    assert not {"going", "getting", "worse", "is", "this", "hai", "ka"} & set(kw[0] + kw[1]), kw
    assert all(len(t) >= 3 for t in kw[0] + kw[1])
    hinglish = ctfidf_keywords({0: ["Viman Nagar mein bahut paani bhar gaya hai, ab kya hoga", "paani bhar gaya hai yaar"],
                                1: ["free bus travel", "bus free city"]}, 4)
    assert not {"hai", "mein", "kya", "hoga", "yaar", "bahut"} & set(hinglish[0]), hinglish


def test_ctfidf_ties_prefer_hashtags_and_names():
    from socmint.analyze.narratives import ctfidf_keywords

    kw = ctfidf_keywords({0: ["water entered houses #Kharadi", "Kharadi water entered houses"],
                          1: ["bus fare free", "free bus fare"]}, 4)
    assert kw[0][0] == "kharadi", kw
