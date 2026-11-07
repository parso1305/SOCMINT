"""M19.1: suggested terms for an event (never added automatically): co-occurring hashtags, capitalised names and
places, Devanagari words/bigrams; at least 3 posts from at least 2 accounts; stopwords, generic tags, handles, URLs,
numbers, short tokens and existing terms excluded. "Add selected" goes through the audited `investigate edit` path."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from typer.testing import CliRunner

from socmint import cli
from socmint.core.db import session_scope
from socmint.core.models import AuditLog
from socmint.core.schemas import FetchResult, ItemIn
from socmint.ingest.base import Source
from socmint.investigate import runner, suggest
from socmint.investigate.config import InvestigationConfig

APP = Path(__file__).resolve().parents[1] / "socmint" / "showcase" / "app.py"


def post(n: int, author: str, text: str, tags: list[str] | None = None, lang: str = "en") -> ItemIn:
    return ItemIn(source="bluesky", source_type="bluesky", external_id=f"at://{author}/p{n}", author_id=author,
                  author_handle=f"{author}.bsky.social", text=text, hashtags=tags or [], lang=lang)


POSTS = [
    post(1, "a1", "The Supreme Court heard the Delhi smog case near Anand Vihar today @someone https://ex.am/ple 2026",
         ["AirPollution", "india", "DelhiSmog", "onlyone"]),
    post(2, "a2", "Supreme Court asks why Anand Vihar AQI stays severe", ["airpollution", "India", "twice"]),
    post(3, "a3", "Anand Vihar again: the Supreme Court wants answers", ["AIRPOLLUTION", "india", "delhismog", "twice"]),
    post(4, "a1", "Delhi smog thick this morning", ["india", "onlyone"]),
    post(5, "a1", "More Delhi smog", ["onlyone"]),
    post(6, "h1", "दिल्ली में प्रदूषण स्तर बहुत खराब", lang="hi"),
    post(7, "h2", "प्रदूषण स्तर फिर बढ़ा", lang="hi"),
    post(8, "h1", "आज भी प्रदूषण स्तर गंभीर", lang="hi"),
    post(9, "m1", "पुण्यात धुके आणि थंडी", lang="mr"),
    post(10, "m2", "सकाळी धुके दाट", lang="mr"),
    post(11, "m1", "धुके कायम", lang="mr"),
]


class Fixed(Source):
    type_name = "fixed_posts"
    capabilities = {"terms"}

    def fetch(self, since):
        return FetchResult()

    def search_terms(self, terms_by_lang, since, limit):
        return FetchResult(items=list(POSTS))


def collected() -> int:
    iid = runner.create(InvestigationConfig(kind="event", name="delhi-air", purpose="test",
                                            terms={"en": ["Delhi smog", "#DelhiSmog"]}, sources=["bluesky"]))
    runner.collect(iid, factory=lambda lg, cfg: Fixed(cfg), resolver=lambda lg: [{"name": "bluesky",
                                                                                  "type": "fixed_posts"}],
                   enrich_after=False)
    return iid


def test_suggestions_follow_the_support_and_exclusion_rules():
    iid = collected()
    got = {s["term"]: s for s in suggest.suggest_terms(iid)}
    assert {"#airpollution", "Supreme Court", "Anand Vihar", "प्रदूषण स्तर", "धुके"} <= set(got)
    assert got["#airpollution"]["posts"] == 3 and got["#airpollution"]["accounts"] == 3
    assert got["#airpollution"]["kind"] == "hashtag" and got["Supreme Court"]["kind"] == "name"
    assert got["धुके"]["lang"] == "mr" and got["प्रदूषण स्तर"]["lang"] == "hi" and got["Anand Vihar"]["lang"] == "en"
    for excluded in ["#india",      # generic tag (config list)
                     "#delhismog",  # already a term
                     "#onlyone",    # 3 posts but one account
                     "#twice",      # 2 posts
                     "Delhi", "The", "@someone", "2026", "AQI", "https://ex.am/ple", "में", "बहुत"]:
        assert excluded not in got, excluded
    assert all(len(t.lstrip("#")) >= 3 for t in got)


def test_add_selected_uses_the_audited_edit_path_and_keeps_languages():
    iid = collected()
    change = suggest.add_suggested(iid, ["#airpollution", "धुके", "Anand Vihar"], who="tester")
    cfg = runner.config_of(runner.get(iid))
    assert cfg.terms == {"en": ["Delhi smog", "#DelhiSmog", "#airpollution", "Anand Vihar"], "mr": ["धुके"]}
    assert set(change["after"]) == {"terms"}
    with session_scope() as s:
        assert len(s.scalars(select(AuditLog).where(AuditLog.action == "investigation_edit")).all()) == 1
    left = {s["term"] for s in suggest.suggest_terms(iid)}
    assert not {"#airpollution", "धुके", "Anand Vihar"} & left


def test_subjects_get_no_suggestions():
    iid = runner.create(InvestigationConfig(kind="subject", name="acct", purpose="t",
                                            handles={"bluesky": ["bsky.app"]}))
    assert suggest.suggest_terms(iid) == []


def test_investigate_show_lists_suggestions():
    collected()
    out = CliRunner().invoke(cli.app, ["investigate", "show", "delhi-air"])
    assert out.exit_code == 0, out.output
    assert "Suggested terms (add to widen collection)" in out.output and "#airpollution" in out.output
    assert "investigate edit" in out.output


def test_showcase_overview_offers_suggestions_and_adds_selected():
    from streamlit.testing.v1 import AppTest

    iid = collected()
    at = AppTest.from_file(str(APP), default_timeout=120)
    at.session_state["inv_id"] = iid
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert any(h.value == "Suggested terms (add to widen collection)" for h in at.subheader)
    pick = next(m for m in at.multiselect if m.label.startswith("Suggested terms"))
    assert next(b for b in at.button if b.label == "Add selected").disabled  # nothing picked yet
    pick.select("#airpollution").run()  # a pick reruns the page, as in the browser
    next(b for b in at.button if b.label == "Add selected").click()
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert "#airpollution" in runner.config_of(runner.get(iid)).terms["en"]
