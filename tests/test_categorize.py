"""M12: rule-based categories with evidence, hate_communal off by default, analyst overrides, zero-shot degrade."""

from __future__ import annotations

import dataclasses
import re

import pytest
from sqlalchemy import select

from socmint.analyze import categorize as cz
from socmint.analyze.categorize import Signals, rule_labels
from socmint.core.db import session_scope
from socmint.core.models import Item, ItemLabel


def labels(text, sig=None):
    return {lab.label: lab for lab in rule_labels(text, sig)}


@pytest.mark.parametrize("text,label,sub", [
    ("Roads closed near Khadakwasla dam, NDRF rescue teams deployed", "civic_safety", "disaster"),
    ("खडकवासला धरणातून विसर्ग वाढवला, नदीकाठच्या नागरिकांचे स्थलांतर", "civic_safety", "disaster"),
    ("Bridge collapse on the highway, two injured", "civic_safety", "accident"),
    ("Section 144 imposed, police patrol in the old city", "civic_safety", "law_and_order"),
    ("Traders call a bandh and dharna tomorrow", "civic_safety", "protest"),
    ("This is an old video from 2019, not from today", "misinformation", "old_or_out_of_context_media"),
    ("पुराना वीडियो फिर से वायरल, बांध टूट गया बताया जा रहा", "misinformation", "old_or_out_of_context_media"),
    ("Khadakwasla dam has burst! share before this is deleted", "misinformation", "fabricated_claim"),
    ("This photo is morphed and photoshopped", "misinformation", "manipulated_media"),
    ("Voting at the polling booth tomorrow, carry your voter id", "election_political", None),
    ("पुराच्या पाण्यामुळे डेंग्यू आणि कॉलरा वाढण्याची भीती, पाणी उकळून प्या", "health", None),
])
def test_keyword_rules_fire_with_evidence(text, label, sub):
    lab = labels(text)[label]
    assert lab.method == "rule" and lab.sublabel == sub and 0.5 <= lab.confidence <= 0.85
    assert lab.evidence["rule"] == "keywords" and (lab.evidence["keywords"] or lab.evidence["sublabel_keywords"])


def test_signal_rules_scam_factcheck_impersonation_market():
    sig = Signals(scam_score=85, scam_band="likely", scam_categories=["investment"], scam_reasons=["category:investment"],
                  factchecks=[{"id": 3, "title": "Old dam video", "url": "https://fc.example/1", "score": 0.81}],
                  impersonation=[{"brand": "demobank", "type": "domain", "value": "demobank-relief.example"}],
                  tickers=["DEMOPUMP"], promo_keywords=["sure shot", "multibagger"])
    got = labels("anything", sig)
    assert got["scam"].sublabel == "investment" and got["scam"].evidence["score"] == 85 and got["scam"].confidence == 0.9
    assert got["misinformation"].sublabel == "factcheck_matched" and got["misinformation"].evidence["factchecks"][0]["id"] == 3
    assert got["brand_impersonation"].sublabel == "demobank" and got["brand_impersonation"].evidence["findings"]
    assert got["market_manipulation"].evidence["tickers"] == ["DEMOPUMP"]
    assert "other" not in got


def test_other_is_the_fallback_only():
    assert list(labels("Lovely sunset at the beach today")) == ["other"]
    assert "other" not in labels("Heavy rain and flooding in the city")


def test_compute_signals_end_to_end_relief_scam():
    sig, _ = cz.compute_signals("URGENT flood relief: DemoBank donation verification, pay Rs 10 now to "
                                "punerelief.help@okdemo or visit demobank-relief.example, KYC update today", claim_matching=False)
    got = labels("URGENT flood relief: DemoBank donation verification", sig)
    assert sig.scam_band in ("likely", "suspicious") and "scam" in got
    assert any(f["brand"] == "demobank" for f in sig.impersonation) and "brand_impersonation" in got


def test_hate_communal_is_analyst_only_and_off_by_default(monkeypatch):
    tax = cz.taxonomy()
    hate = tax["hate_communal"]
    assert hate.enabled is False and hate.analyst_only is True
    assert "hate_communal" not in cz.enabled_categories(include_analyst_only=True)
    armed = dataclasses.replace(hate, latin=[("demoslur", re.compile(r"(?<![a-z0-9])demoslur(?![a-z0-9])"))])
    monkeypatch.setitem(tax, "hate_communal", armed)
    assert "hate_communal" not in labels("demoslur demoslur")  # keywords present but category disabled
    monkeypatch.setitem(tax, "hate_communal", dataclasses.replace(armed, enabled=True))
    assert "hate_communal" in labels("demoslur")  # only an explicit analyst/deployment decision turns it on
    assert "hate_communal" not in cz.enabled_categories()  # and it never appears in non-analyst views


def test_analyst_labels_override_rule_labels():
    with session_scope() as s:
        it = Item(source="t", source_type="bluesky", external_id="x", text="t")
        s.add(it)
        s.flush()
        cz.store_labels(s, it.id, rule_labels("old video from 2019, heavy rain flooding"), "rule")
        cz.set_analyst_label(s, it.id, "misinformation", present=False, actor="tester")
        cz.set_analyst_label(s, it.id, "health", present=True, actor="tester", note="boil-water advisory")
        iid = it.id
    with session_scope() as s:
        rows = s.scalars(select(ItemLabel).where(ItemLabel.item_id == iid)).all()
        eff = {e["label"]: e for e in cz.effective_labels(rows)}
        assert "misinformation" not in eff and eff["health"]["method"] == "analyst"
        assert eff["civic_safety"]["method"] == "rule"
        # re-running the rules never touches analyst decisions
        cz.store_labels(s, iid, rule_labels("old video from 2019"), "rule")
    with session_scope() as s:
        rows = s.scalars(select(ItemLabel).where(ItemLabel.item_id == iid)).all()
        assert {r.label for r in rows if r.method == "analyst"} == {"misinformation", "health"}
        assert "misinformation" not in {e["label"] for e in cz.effective_labels(rows)}


def test_zero_shot_raises_unavailable_when_model_missing(monkeypatch):
    def boom():
        raise cz.ZeroShotUnavailable("transformers not installed (install the [ml] extra)")

    monkeypatch.setattr(cz, "_zero_shot_pipeline", boom)
    with pytest.raises(cz.ZeroShotUnavailable):
        cz.zero_shot_labels(["Heavy rain in Pune"])


def test_investigation_terms_do_not_count_toward_a_category():
    """P2 (B3): a post collected because it says "flood" is not civic_safety just for saying "flood"."""
    terms = {"en": ["Pune flood", "#PuneRains", "Demosagar dam"], "hi": ["बाढ़"], "mr": ["पूर"]}
    only_terms = rule_labels("Pune flood: the Demosagar dam looks scary today #PuneRains", investigation_terms=terms)
    assert [lab.label for lab in only_terms] == ["other"]
    assert set(only_terms[0].evidence["matched_investigation_term"]["civic_safety"]) == {"flood", "dam"}
    assert [lab.label for lab in rule_labels("पुणे में बाढ़ का पानी", investigation_terms=terms)] == ["other"]
    beyond = rule_labels("Pune flood: NDRF rescue teams deployed, helpline 1077 active", investigation_terms=terms)
    civic = next(lab for lab in beyond if lab.label == "civic_safety")
    assert {"ndrf", "rescue", "helpline"} <= set(civic.evidence["keywords"] + sum(
        civic.evidence["sublabel_keywords"].values(), []))
    assert "flood" not in sum(civic.evidence["sublabel_keywords"].values(), civic.evidence["keywords"])
    assert civic.evidence["matched_investigation_term"] == ["flood"]
    # without investigation terms (global / tipline categorisation) nothing changes
    assert rule_labels("Pune flood: the Demosagar dam looks scary today")[0].label == "civic_safety"
