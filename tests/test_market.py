"""M6: market-manipulation risk alerts (evidence only, never recommendations)."""

from __future__ import annotations

import re

import pytest
from sqlalchemy import select

from socmint import fixtures
from socmint.core.db import session_scope
from socmint.core.models import Alert, MarketDaily

# Recommendation / prediction phrasing that generated market text must never contain.
FORBIDDEN = [
    r"\bbuy\b", r"\bsell\b", r"\bhold\b", r"\baccumulate\b", r"\bexit\b", r"\bbook profits?\b", r"\bstrong buy\b",
    r"\btarget(?: price)?\b", r"\bprice target\b", r"\bupside\b", r"\bwill (?:rise|fall|go up|go down|reach)\b",
    r"\bexpected to\b", r"\boutperform\b", r"\bunderperform\b", r"\brecommend", r"\bstop[- ]loss\b",
    r"खरीद", r"बेच", r"खरेदी", r"विक्री", r"टारगेट", r"टार्गेट",
]


@pytest.fixture()
def market_run():
    from socmint.analyze.market import run

    fixtures.load_symbols()
    fixtures.load_items()
    fixtures.load_market()
    return run()


def _generated_texts(a: Alert) -> list[str]:
    """Everything the module itself wrote (quoted promoter posts are excluded on purpose)."""
    ev = a.evidence
    return [a.title, ev["summary"], ev["disclaimer"], *map(str, ev["risk_factors"])]


def test_pumped_symbol_flagged_with_evidence(market_run):
    assert market_run["alerts"] == 1
    with session_scope() as s:
        a = s.scalar(select(Alert).where(Alert.module == "market"))
        ev = a.evidence
        assert ev["symbol"] == "DEMOPUMP" and a.title.startswith("Manipulation-risk alert")
        assert ev["mention_z"] >= 3 and ev["volume_z"] >= 3 and ev["mentions"] >= 30
        assert {"small_or_illiquid", "promotional_language", "sebi_claim_unverified", "price_jump"} <= set(ev["risk_factors"])
        assert ev["sebi_check"]["not_in_registry"] == ["INH999000777"]
        assert ev["quoted_posts"] and ev["mention_item_ids"]
        row = s.scalar(select(MarketDaily).where(MarketDaily.symbol == "DEMOPUMP").order_by(MarketDaily.date.desc()))
        assert row.mentions >= 30 and row.mention_z >= 3 and row.volume_z >= 3


def test_volume_spike_without_chatter_not_flagged(market_run):
    with session_scope() as s:
        syms = {a.evidence["symbol"] for a in s.scalars(select(Alert).where(Alert.module == "market")).all()}
        steady = s.scalar(select(MarketDaily).where(MarketDaily.symbol == "DEMOSTEADY").order_by(MarketDaily.date.desc()))
    assert "DEMOSTEADY" not in syms and "DEMOLARGE" not in syms
    assert steady.mentions == 0  # big volume but no chatter: never evaluated as a pump


def test_market_alert_text_never_recommends(market_run):
    with session_scope() as s:
        alerts = s.scalars(select(Alert).where(Alert.module == "market")).all()
    assert alerts
    for a in alerts:
        for text in _generated_texts(a):
            for pat in FORBIDDEN:
                assert not re.search(pat, text, re.I), (pat, text)
        assert "not investment advice" in a.evidence["disclaimer"]


def test_templates_never_recommend():
    from socmint.analyze import market

    for text in (market.DISCLAIMER, market.SUMMARY_TEMPLATE):
        for pat in FORBIDDEN:
            assert not re.search(pat, text, re.I), (pat, text)


def test_rerun_does_not_duplicate(market_run):
    from socmint.analyze.market import run

    assert run()["alerts"] == 0
