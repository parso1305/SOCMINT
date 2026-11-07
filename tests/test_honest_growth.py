"""M20.2: growth is only rated once an investigation has `narratives.min_baseline_hours` of collection history.
Before that, key findings name the most active narrative and say how much history there is."""

from __future__ import annotations

from datetime import timedelta

import pytest

from socmint.core.db import session_scope
from socmint.core.models import CollectionRun, utcnow
from socmint.investigate import report, runner
from socmint.investigate.config import InvestigationConfig
from socmint.showcase.data import growth_note, key_findings


def summary(history: float, baseline: float = 72) -> dict:
    return {"coordination_groups": [], "factcheck_matches": [], "indicators": [], "market_alerts": [],
            "narratives": [{"keywords": ["smog", "delhi", "aqi", "mask", "cough"], "count_24h": 10, "growth_rate": 20.0,
                            "size": 12},
                           {"keywords": ["cricket"], "count_24h": 4, "growth_rate": 50.0, "size": 5},
                           {"keywords": ["old"], "count_24h": 0, "growth_rate": 90.0, "size": 9}],
            "by_category": {}, "counts": {"posts": 21}, "by_hour": [],
            "history_hours": history, "baseline_hours": baseline}


def test_unrated_growth_names_the_most_active_narrative_and_the_history():
    out = key_findings({"summary": summary(3.2)})
    assert ("Most active narrative: smog, delhi, aqi, mask (10 posts in the last 24 h; growth not rated yet: "
            "3 h of history, needs 72 h).") in out
    assert not any("growth x" in f or "Fastest" in f for f in out)


def test_under_an_hour_of_history_is_said_so():
    out = key_findings({"summary": summary(0.2)})
    assert any("growth not rated yet: under 1 h of history, needs 72 h" in f for f in out)


def test_growth_is_rated_after_the_baseline():
    out = key_findings({"summary": summary(80)})
    assert "Fastest-growing narrative: cricket (4 posts in the last 24 h, growth x50.0)." in out
    assert not any(f.startswith("Most active narrative") for f in out)


def test_growth_note_for_the_narratives_table():
    assert growth_note(summary(3.2)) == ("Growth not rated yet: 3 h of collection history, a baseline needs 72 h "
                                         "(narratives.min_baseline_hours).")
    assert growth_note(summary(72)) is None


@pytest.mark.parametrize("hours_ago, expected", [(None, 0.0), (3, 3.0)])
def test_summary_reports_collection_history(hours_ago, expected):
    iid = runner.create(InvestigationConfig(kind="event", name="hist", purpose="test", terms={"en": ["smog"]},
                                            sources=["news_rss"]))
    if hours_ago is not None:
        with session_scope() as s:
            s.add(CollectionRun(investigation_id=iid, source="feed", status="ok",
                                started_at=utcnow() - timedelta(hours=hours_ago)))
    s = report.summary(iid)
    assert s["history_hours"] == pytest.approx(expected, abs=0.05)
    assert s["baseline_hours"] == 72


def test_backdated_runs_are_for_synthetic_investigations_only():
    from socmint import scenario

    iid = runner.create(InvestigationConfig(kind="event", name="real", purpose="test", terms={"en": ["smog"]},
                                            sources=["news_rss"]))
    with pytest.raises(ValueError, match="synthetic"):
        scenario.backdate_collection_runs(iid)
