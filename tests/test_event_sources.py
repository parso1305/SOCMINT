"""M18.2: key-gated sources join new events automatically; `investigate create` says which sources are used and
why others are skipped; `investigate edit` changes terms/sources/purpose with an audited before/after; the showcase
form lists sources with checkboxes (missing keys greyed out with the variable) and stores Hindi/Marathi lines under
their own language."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from socmint import cli
from socmint.core.db import session_scope
from socmint.core.models import AuditLog
from socmint.core.settings import reset_settings
from socmint.investigate import runner
from socmint.investigate.config import NO_KEY_SOURCES, InvestigationConfig, default_event_sources, source_plan

APP = Path(__file__).resolve().parents[1] / "socmint" / "showcase" / "app.py"


def keys(monkeypatch, **env: str) -> None:
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    reset_settings()


def event(name: str = "air", **kw) -> InvestigationConfig:
    return InvestigationConfig(kind="event", name=name, purpose="test", terms={"en": ["Delhi smog"]}, **kw)


def test_no_keys_means_the_no_key_sources():
    assert default_event_sources() == NO_KEY_SOURCES
    assert event().sources == NO_KEY_SOURCES


def test_youtube_and_factcheck_keys_join_new_events(monkeypatch):
    keys(monkeypatch, YOUTUBE_API_KEY="k1")
    assert event().sources == [*NO_KEY_SOURCES, "youtube"]
    keys(monkeypatch, GOOGLE_FACTCHECK_API_KEY="k2")
    assert event().sources == [*NO_KEY_SOURCES, "youtube", "google_factcheck"]


def test_reddit_and_telegram_keys_do_not_join_events_by_themselves(monkeypatch):
    keys(monkeypatch, REDDIT_CLIENT_ID="a", REDDIT_CLIENT_SECRET="b", TELEGRAM_API_ID="1", TELEGRAM_API_HASH="h")
    assert "reddit" not in event().sources and "telegram" not in event().sources


def test_source_plan_names_the_missing_variable():
    plan = dict(source_plan(event()))
    assert plan["bluesky"] == "used"
    assert plan["youtube"] == "skipped (no_key: set YOUTUBE_API_KEY)"
    assert plan["google_factcheck"] == "skipped (no_key: set GOOGLE_FACTCHECK_API_KEY)"
    assert "channels" in plan["reddit"] and "channels" in plan["telegram"]
    chosen = dict(source_plan(event(sources=["bluesky", "youtube"])))
    assert chosen["youtube"] == "selected, but no_key: set YOUTUBE_API_KEY (reported as no_key at collect)"


def test_create_prints_sources_and_skip_reasons():
    out = CliRunner().invoke(cli.app, ["investigate", "create", "--kind", "event", "--name", "air", "--purpose", "p",
                                       "--terms", "Delhi smog"])
    assert out.exit_code == 0, out.output
    assert "created investigation #1 [event] air" in out.output
    assert "youtube" in out.output and "no_key: set YOUTUBE_API_KEY" in out.output


# ------------------------------------------------------------------------------------------- edit
def _audits() -> list[str]:
    with session_scope() as s:
        return list(s.scalars(select(AuditLog.target).where(AuditLog.action == "investigation_edit")).all())


def test_edit_changes_terms_sources_and_purpose_with_an_audited_before_and_after():
    iid = runner.create(event())
    change = runner.edit(iid, add_terms=["दिल्ली प्रदूषण"], lang="hi", add_sources=["youtube"],
                         remove_sources=["mastodon"], purpose="Track air-quality rumours", who="tester")
    cfg = runner.config_of(runner.get(iid))
    assert cfg.terms == {"en": ["Delhi smog"], "hi": ["दिल्ली प्रदूषण"]}
    assert "youtube" in cfg.sources and "mastodon" not in cfg.sources
    assert cfg.purpose == "Track air-quality rumours"
    assert change["before"]["purpose"] == "test" and change["after"]["purpose"] == "Track air-quality rumours"
    [row] = _audits()
    detail = json.loads(row)
    assert detail["id"] == iid
    assert detail["before"]["terms"] == {"en": ["Delhi smog"]}
    assert detail["after"]["terms"] == {"en": ["Delhi smog"], "hi": ["दिल्ली प्रदूषण"]}
    assert "mastodon" in detail["before"]["sources"] and "mastodon" not in detail["after"]["sources"]

    runner.edit(iid, remove_terms=["delhi SMOG"], who="tester")  # case-insensitive, any language
    assert runner.config_of(runner.get(iid)).terms == {"hi": ["दिल्ली प्रदूषण"]}
    assert len(_audits()) == 2


def test_edit_refuses_bad_changes_without_writing_anything():
    iid = runner.create(event())
    with pytest.raises(ValueError, match="at least one keyword"):
        runner.edit(iid, remove_terms=["Delhi smog"])
    with pytest.raises(ValueError, match="unknown source"):
        runner.edit(iid, add_sources=["twitter"])
    with pytest.raises(ValueError, match="at least one source"):
        runner.edit(iid, remove_sources=list(NO_KEY_SOURCES))
    with pytest.raises(ValueError, match="purpose is required"):
        runner.edit(iid, purpose="  ")
    with pytest.raises(ValueError, match="nothing to change"):
        runner.edit(iid, add_terms=["Delhi smog"])  # already there
    assert _audits() == []
    assert runner.config_of(runner.get(iid)).terms == {"en": ["Delhi smog"]}


def test_edit_cli():
    runner.create(event())
    out = CliRunner().invoke(cli.app, ["investigate", "edit", "air", "--add-terms", "धुके, प्रदूषण", "--lang", "mr",
                                       "--remove-source", "gdelt", "--add-source", "youtube"])
    assert out.exit_code == 0, out.output
    assert "terms" in out.output and "sources" in out.output
    assert "no_key: set YOUTUBE_API_KEY" in out.output  # added, but it will be skipped until the key is set
    cfg = runner.config_of(runner.get("air"))
    assert cfg.terms["mr"] == ["धुके", "प्रदूषण"] and "gdelt" not in cfg.sources
    bad = CliRunner().invoke(cli.app, ["investigate", "edit", "air", "--remove-terms", "Delhi smog,धुके,प्रदूषण"])
    assert bad.exit_code != 0 and "at least one keyword" in bad.output


# ------------------------------------------------------------------------------------------- showcase form
def test_showcase_source_options_grey_out_missing_keys(monkeypatch):
    from socmint.showcase import data

    opts = {o["source"]: o for o in data.source_options()}
    assert opts["bluesky"]["available"] and opts["bluesky"]["default"]
    yt = opts["youtube"]
    assert not yt["available"] and not yt["default"] and "YOUTUBE_API_KEY" in yt["label"]
    keys(monkeypatch, YOUTUBE_API_KEY="k1")
    yt = {o["source"]: o for o in data.source_options()}["youtube"]
    assert yt["available"] and yt["default"] and "YOUTUBE_API_KEY" not in yt["label"]


def test_showcase_hindi_and_marathi_lines_keep_their_language():
    from socmint.showcase import data

    cfg = data.build_config("Event", "pune", "test", "Pune flood, #PuneRains", ["bluesky"],
                            hi_terms="पुणे बाढ़", mr_terms="पुणे पूर, धरण फुटले")
    assert cfg.terms == {"en": ["Pune flood", "#PuneRains"], "hi": ["पुणे बाढ़"], "mr": ["पुणे पूर", "धरण फुटले"]}


def test_showcase_form_has_source_checkboxes_and_language_lines():
    from streamlit.testing.v1 import AppTest

    runner.create(event())
    at = AppTest.from_file(str(APP), default_timeout=120)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    boxes = {c.label: c for c in at.checkbox}
    yt = next(c for label, c in boxes.items() if label.startswith("youtube"))
    assert yt.disabled and "YOUTUBE_API_KEY" in yt.label and yt.value is False
    assert boxes["bluesky"].value is True and not boxes["bluesky"].disabled
    labels = [t.label for t in at.text_input]
    assert any(lb.startswith("Hindi terms") for lb in labels) and any(lb.startswith("Marathi terms") for lb in labels)
    assert any(e.label == "Edit this investigation" for e in at.expander)


def test_showcase_edit_form_saves_through_the_audited_path():
    from streamlit.testing.v1 import AppTest

    iid = runner.create(event())
    at = AppTest.from_file(str(APP), default_timeout=120)
    at.run()
    next(t for t in at.text_input if t.label == "Add terms (comma-separated)").input("Delhi AQI, #DelhiSmog")
    next(b for b in at.button if b.label == "Save changes").click()
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("Saved (audited)" in s.value for s in at.success)
    assert runner.config_of(runner.get(iid)).terms["en"] == ["Delhi smog", "Delhi AQI", "#DelhiSmog"]
    assert len(_audits()) == 1


def test_youtube_event_search_stays_inside_max_units_per_run(monkeypatch):
    from socmint.ingest.youtube import YouTubeSource

    keys(monkeypatch, YOUTUBE_API_KEY="k1")
    calls: list[str] = []

    class Client:
        user_agent = "test"

        def get_json(self, url, params=None, **kw):
            calls.append(url.rsplit("/", 1)[-1])
            return {"items": []}

    src = YouTubeSource({"name": "youtube_keywords", "type": "youtube", "max_units_per_run": 250}, client=Client())
    res = src.search_terms({"en": ["a", "b", "c", "d"]}, None, 50)
    assert calls.count("search") == 2  # 100 units each: a third would pass 250
    assert res.partial and any("quota budget" in n for n in res.notes)
