"""M17.3: one set of status words (ok, partial, throttled, no_key, offline, opted_out, error) in collection_runs,
CLI lines, the collection log and the showcase chips; `no_key` names the .env variable that enables the source."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from socmint.core.db import session_scope
from socmint.core.http import HttpError, RobotsDisallowed
from socmint.core.models import CollectionRun
from socmint.core.schemas import AccountIn, FetchResult, ItemIn
from socmint.core.status import STATUSES, normalize
from socmint.ingest.base import OptedOut, Source
from socmint.investigate import runner
from socmint.investigate.config import InvestigationConfig


class Scripted(Source):
    type_name = "scripted"
    capabilities = {"terms", "accounts"}
    required_settings: tuple[str, ...] = ()

    def fetch(self, since):
        return FetchResult()

    def search_terms(self, terms_by_lang, since, limit):
        mode = self.cfg["mode"]
        if mode == "robots":
            raise RobotsDisallowed("robots.txt disallows https://x.example/ for our User-Agent")
        if mode == "429":
            raise HttpError("HTTP 429 for https://x.example/", 429)
        res = FetchResult(items=[ItemIn(source=self.name, source_type="news", external_id=self.name,
                                        text="Delhi pollution update")])
        res.partial = mode == "partial"
        return res

    def account_posts(self, handle, since, limit):
        if handle.startswith("optout"):
            raise OptedOut(f"{handle} carries the !no-unauthenticated label; not collected")
        return FetchResult(items=[ItemIn(source=self.name, source_type="bluesky", external_id=f"{handle}-1",
                                         author_id=f"did:plc:{handle}", author_handle=handle, text="hello")],
                           accounts=[AccountIn(platform="bluesky", platform_id=f"did:plc:{handle}", handle=handle)])


class Keyed(Scripted):
    required_settings = ("youtube_api_key",)


def run_event(modes: dict[str, str]) -> dict[str, dict[str, Any]]:
    iid = runner.create(InvestigationConfig(kind="event", name="words", purpose="test",
                                            terms={"en": ["Delhi pollution"]}, sources=["news_rss"]))
    cfgs = [{"name": n, "type": "scripted", "mode": m} for n, m in modes.items()]
    out = runner.collect(iid, factory=lambda lg, c: (Keyed if c["mode"] == "key" else Scripted)(c),
                         resolver=lambda lg: cfgs, enrich_after=False)
    return {r["source"]: r for r in out["runs"]}


def test_event_statuses_use_the_shared_words():
    runs = run_event({"a": "ok", "b": "partial", "c": "robots", "d": "429", "e": "key"})
    assert {k: r["status"] for k, r in runs.items()} == {"a": "ok", "b": "partial", "c": "error",
                                                         "d": "throttled", "e": "no_key"}
    assert "YOUTUBE_API_KEY" in runs["e"]["detail"] and ".env" in runs["e"]["detail"]
    assert "robots.txt" in runs["c"]["detail"]
    with session_scope() as s:
        assert set(s.scalars(select(CollectionRun.status)).all()) <= set(STATUSES)


def test_unknown_source_is_an_error_not_a_new_word():
    iid = runner.create(InvestigationConfig(kind="event", name="unknown", purpose="test",
                                            terms={"en": ["Delhi pollution"]}, sources=["news_rss"]))
    out = runner.collect(iid, only_source="myspace", enrich_after=False)
    assert out["runs"][0]["status"] == "error" and "unknown source" in out["runs"][0]["detail"]


@pytest.mark.parametrize("handles,status", [(["alice.example", "optout1"], "partial"),
                                            (["optout1", "optout2"], "opted_out")])
def test_subject_opt_outs(handles, status):
    iid = runner.create(InvestigationConfig(kind="subject", name=f"s-{status}", purpose="test",
                                            handles={"bluesky": handles}))
    out = runner.collect(iid, factory=lambda lg, c: Scripted({**c, "mode": "ok"}),
                         resolver=lambda lg: [{"name": "bluesky", "type": "scripted"}], enrich_after=False)
    run = out["runs"][0]
    assert run["status"] == status and "skipped optout1" in run["detail"]


def test_legacy_words_in_old_databases_are_shown_with_the_new_ones():
    assert [normalize(s) for s in ("missing_key", "unsupported", "blocked_by_robots", "ok", "throttled")] == \
        ["no_key", "error", "error", "ok", "throttled"]


def test_showcase_chip_colour_per_status():
    from socmint.showcase.data import STATUS_COLOR

    assert set(STATUS_COLOR) == set(STATUSES)
    assert STATUS_COLOR["ok"] == "green" and STATUS_COLOR["error"] == "red"
    assert len({STATUS_COLOR[s] for s in ("ok", "partial", "throttled", "error")}) == 4
