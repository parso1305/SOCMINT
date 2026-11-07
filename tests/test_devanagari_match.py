"""M20.1: Devanagari keywords match at the start of a word, and the next character may not be a virama or a
consonant (vowel signs, anusvara, chandrabindu, visarga and nukta may follow, so inflections still match). Applied in
`keyword_hits` (scam, categories, market) and in `TermMatcher` (event terms). Plus the new `environment` category."""

from __future__ import annotations

import re

import pytest
import yaml

from socmint.analyze.scam import _compile_keywords, keyword_hits
from socmint.core.settings import get_settings
from socmint.ingest.base import TermMatcher
from socmint.understand.normalize import match_text

CASES = [
    ("पूर", "पूर आला", True), ("पूर", "पूराचे पाणी ओसरले", True), ("पूर", "काम पूर्ण झाले", False),
    ("पूर", "महापूर आला", False),  # not at the start of a word
    ("पूर", "(पूर) आला", True), ("पूर", "नदीला।पूर", True),  # after punctuation
    ("बंद", "रस्ता बंद", True), ("बंद", "बंदी लागू", True), ("बंद", "बंदर परिसर", False),
    ("संप", "संप सुरू", True), ("संप", "संपर्क साधा", False),
    ("बाढ़", "बाढ़ आई", True), ("बाढ़", "बाढ़ों से", True),
    ("धुके", "धुके दाट", True), ("धुके", "धुक्यात", False),
]


def hits(keyword: str, text: str) -> list[str]:
    latin, deva = _compile_keywords({"x": [keyword]})
    return keyword_hits(match_text(text), latin, deva)


@pytest.mark.parametrize("keyword, text, expected", CASES)
def test_keyword_hits_match_at_a_devanagari_word_start(keyword, text, expected):
    assert bool(hits(keyword, text)) is expected


@pytest.mark.parametrize("keyword, text, expected", CASES)
def test_term_matcher_uses_the_same_rule(keyword, text, expected):
    assert bool(TermMatcher({"mr": [keyword]}).match(text)) is expected


def test_listed_compounds_still_match():
    assert hits("पूरग्रस्त", "पूरग्रस्त भागात मदत")
    assert TermMatcher({"mr": ["पूर", "पूरग्रस्त"]}).match("पूरग्रस्त भागात मदत") == ["पूरग्रस्त"]


def test_latin_matching_is_unchanged():
    assert hits("flood", "Pune flood update") and not hits("flood", "floodlights on")


# ------------------------------------------------------------------------------------------- categories
def _config(name: str) -> dict:
    return yaml.safe_load((get_settings().config_dir / f"{name}.yaml").read_text(encoding="utf-8"))


def _keywords(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _keywords(v, f"{path}.{k}")
    elif isinstance(node, list):
        for x in node:
            if isinstance(x, str):
                yield path, x


@pytest.mark.parametrize("name", ["categories", "scam_rules"])
def test_no_devanagari_keyword_is_shorter_than_three_characters(name):
    short = [(p, w) for p, w in _keywords(_config(name)) if re.search(r"[ऀ-ॿ]", w) and len(w.replace(" ", "")) < 3]
    assert short == []


def test_environment_category_is_configured():
    env = _config("categories")["categories"]["environment"]
    assert env["label"] == "Environment / air quality" and env["enabled"] and env["zero_shot_label"]
    kw = env["keywords"]
    assert {"air pollution", "aqi", "smog", "pm2.5", "parali", "grap"} <= {k.lower() for k in kw["en"]}
    assert "वायु प्रदूषण" in kw["hi"] and "पाणीटंचाई" in kw["mr"] and "pradushan" in kw["hinglish"]
    assert "लू" not in kw["hi"]
    health = _config("categories")["categories"]["health"]["keywords"]
    assert {"asthma", "breathing", "respiratory", "mask"} <= set(health["en"]) and "दमा" in health["hi"]


@pytest.mark.parametrize("text", ["Delhi AQI is 450 today, thick smog over the city",
                                  "दिल्ली में वायु प्रदूषण गंभीर स्तर पर", "पुण्यात पाणीटंचाई, टँकरने पाणीपुरवठा",
                                  "PM2.5 levels spike as stubble burning continues"])
def test_air_quality_posts_are_labelled_environment(text):
    from socmint.analyze.categorize import rule_labels, taxonomy

    taxonomy.cache_clear()
    assert "environment" in {lab.label for lab in rule_labels(text)}
