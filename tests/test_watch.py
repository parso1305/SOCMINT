"""M19.2: `investigate watch` repeats collect (and analyze) every cycle (>= 5 min), audited, one summary line per
cycle, stopping cleanly on Ctrl+C after the current source. Without a Bluesky app password one Jetstream connection
stays open for the whole watch: matches are queued (bounded) and hydrated each cycle, and a reconnect resumes from
the `time_us` of the last event received, never further back."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from websockets.exceptions import ConnectionClosedError

from socmint.core.db import session_scope
from socmint.core.http import PoliteClient
from socmint.core.models import AuditLog
from socmint.core.schemas import FetchResult, ItemIn
from socmint.ingest.base import Source
from socmint.ingest.bluesky import BlueskySource
from socmint.ingest.jetstream import JetstreamListener
from socmint.investigate import runner, watch
from socmint.investigate.config import InvestigationConfig

TERMS = {"en": ["Delhi smog"]}


# ------------------------------------------------------------------------------------------- durations
@pytest.mark.parametrize("text, seconds", [("15m", 900), ("8h", 28800), ("1h30m", 5400), ("90s", 90), ("20", 1200)])
def test_parse_duration(text, seconds):
    assert watch.parse_duration(text) == seconds


@pytest.mark.parametrize("bad", ["", "soon", "5x", "-5m"])
def test_parse_duration_rejects_nonsense(bad):
    with pytest.raises(ValueError):
        watch.parse_duration(bad)


# ------------------------------------------------------------------------------------------- the loop
class Posts(Source):
    type_name = "watch_posts"
    capabilities = {"terms"}
    calls = 0
    on_fetch: Any = None

    def fetch(self, since):
        return FetchResult()

    def search_terms(self, terms_by_lang, since, limit):
        Posts.calls += 1
        if Posts.on_fetch:
            Posts.on_fetch(self)
        n = Posts.calls
        return FetchResult(items=[ItemIn(source=self.name, source_type="news", external_id=f"{self.name}-{n}",
                                         text=f"Delhi smog report {n}")])


def event(sources=("news_rss",)) -> int:
    return runner.create(InvestigationConfig(kind="event", name="watched", purpose="test", terms=TERMS,
                                             sources=list(sources)))


class Clock:
    """Fake time: `wait` advances the clock instead of sleeping."""

    def __init__(self):
        self.t = 1000.0
        self.waits: list[float] = []

    def now(self) -> float:
        return self.t

    def wait(self, seconds: float) -> bool:
        self.waits.append(seconds)
        self.t += seconds
        return False


def actions(name: str) -> list[str]:
    with session_scope() as s:
        return list(s.scalars(select(AuditLog.target).where(AuditLog.action == name)).all())


def test_every_must_be_at_least_five_minutes():
    iid = event()
    with pytest.raises(ValueError, match="at least 5 minutes"):
        watch.watch(iid, every_s=240)


def test_cycles_until_the_end_with_one_line_each_and_audited():
    Posts.calls, Posts.on_fetch = 0, None
    iid = event()
    clock, lines = Clock(), []
    out = watch.watch(iid, every_s=300, for_s=600, echo=lines.append, clock=clock.now, wait=clock.wait,
                      factory=lambda lg, cfg: Posts(cfg), resolver=lambda lg: [{"name": "feed", "type": "watch_posts"}],
                      enrich_after=False)
    assert out["cycles"] == 3 and out["reason"] == "finished"
    assert clock.waits == [300, 300]
    cycle_lines = [ln for ln in lines if ln.startswith("cycle ")]
    assert len(cycle_lines) == 3 and "1 new" in cycle_lines[0] and "feed ok" in cycle_lines[0]
    assert len(actions("investigation_collect")) == 3
    [start] = actions("investigation_watch_start")
    [stop] = actions("investigation_watch_stop")
    assert "every=300s" in start and "cycles=3" in stop and "reason=finished" in stop


def test_ctrl_c_stops_after_the_current_source(monkeypatch):
    """Sources run one at a time here; Ctrl+C during the first leaves the second unstarted and ends the watch."""
    real = runner.threshold
    monkeypatch.setattr(runner, "threshold", lambda k, d=None: 1 if k == "investigate.max_parallel_sources"
                        else real(k, d))
    Posts.calls = 0
    stop = threading.Event()
    Posts.on_fetch = lambda src: stop.set()  # the analyst presses Ctrl+C while the first source is fetching
    iid = event()
    clock, lines = Clock(), []
    out = watch.watch(iid, every_s=300, echo=lines.append, clock=clock.now, wait=clock.wait, stop=stop,
                      factory=lambda lg, cfg: Posts(cfg),
                      resolver=lambda lg: [{"name": "first", "type": "watch_posts"},
                                           {"name": "second", "type": "watch_posts"}],
                      enrich_after=False)
    Posts.on_fetch = None
    assert out["cycles"] == 1 and out["reason"] == "stopped" and Posts.calls == 1
    assert clock.waits == []
    assert any("stopped" in ln and "second" in ln for ln in lines)
    assert "reason=stopped" in actions("investigation_watch_stop")[0]


# ------------------------------------------------------------------------------------------- Jetstream listener
def jet(t: int, rkey: str, text: str, did: str = "did:plc:a") -> dict:
    return {"did": did, "time_us": t, "kind": "commit",
            "commit": {"operation": "create", "collection": "app.bsky.feed.post", "rkey": rkey,
                       "record": {"text": text, "createdAt": "2026-11-07T10:00:00Z"}}}


class FakeConnect:
    """Scripted Jetstream: each connection serves one batch, then drops; after the last batch it idles."""

    def __init__(self, batches: list[list[dict]]):
        self.batches = [list(b) for b in batches]
        self.urls: list[str] = []

    def __call__(self, url: str, **kw):
        self.urls.append(url)
        batch = self.batches.pop(0) if self.batches else None
        return FakeWS(batch)


class FakeWS:
    def __init__(self, batch):
        self.batch = batch

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def recv(self):
        if self.batch is None:  # idle connection: nothing arrives
            await asyncio.sleep(0.05)
            raise TimeoutError
        if self.batch:
            return json.dumps(self.batch.pop(0))
        raise ConnectionClosedError(None, None)


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_listener_reconnects_from_the_last_event_received_and_never_further_back():
    fake = FakeConnect([[jet(1000, "r1", "Delhi smog at dawn"), jet(1001, "r2", "cricket score")],
                        [jet(1002, "r3", "delhi SMOG again")]])
    lst = JetstreamListener("wss://jet.example/subscribe", TERMS, connect=fake, reconnect_delay_s=0)
    lst.start()
    try:
        assert wait_for(lambda: lst.scanned >= 3)
    finally:
        lst.stop()
    assert "cursor" not in fake.urls[0]  # live from now: no replay at start
    assert fake.urls[1].endswith("cursor=1001")  # time_us of the last event received
    assert lst.reconnects >= 1
    assert set(lst.drain()) == {"at://did:plc:a/app.bsky.feed.post/r1", "at://did:plc:a/app.bsky.feed.post/r3"}
    assert lst.drain() == {}


def test_listener_queue_is_bounded():
    fake = FakeConnect([[jet(1000 + i, f"r{i}", "Delhi smog") for i in range(5)]])
    lst = JetstreamListener("wss://jet.example/subscribe", TERMS, connect=fake, reconnect_delay_s=0, max_queue=2)
    lst.start()
    try:
        assert wait_for(lambda: lst.scanned >= 5)
    finally:
        lst.stop()
    assert len(lst.drain()) == 2 and lst.dropped == 3


def test_listener_stops_cleanly():
    lst = JetstreamListener("wss://jet.example/subscribe", TERMS, connect=FakeConnect([]), reconnect_delay_s=0)
    lst.start()
    lst.stop()
    assert not lst.thread.is_alive()


def test_bluesky_source_hydrates_what_the_watch_listener_queued(monkeypatch):
    monkeypatch.setenv("SOCMINT_OFFLINE", "0")

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if req.url.path.endswith("searchPosts"):
            return httpx.Response(403, json={"error": "Forbidden"})
        if req.url.path.endswith("getPosts"):
            uris = req.url.params.get_list("uris")
            return httpx.Response(200, json={"posts": [
                {"uri": u, "cid": "c", "author": {"did": "did:plc:a", "handle": "a.bsky.social"},
                 "record": {"text": "Delhi smog at dawn", "createdAt": "2026-11-07T10:00:00Z"}} for u in uris]})
        return httpx.Response(404)

    c = PoliteClient(rate_limit_s=0)
    c._client = httpx.Client(transport=httpx.MockTransport(handler))

    class Queued:
        scanned, dropped, reconnects = 120, 0, 1

        def drain(self):
            return {"at://did:plc:a/app.bsky.feed.post/r1": ["Delhi smog"]}

    src = BlueskySource({"name": "bluesky", "type": "bluesky", "search_mode": "auto", "rate_limit_s": 0}, client=c)
    src.listener = Queued()
    monkeypatch.setattr(src, "_jetstream", lambda *a, **k: pytest.fail("no 60 s listen during a watch"))
    res = src.search_terms(TERMS, None, 50)
    assert [i.external_id for i in res.items] == ["at://did:plc:a/app.bsky.feed.post/r1"]
    assert res.mode == "live listen (watch)"
    assert any("120 new posts scanned since the last cycle" in n for n in res.notes)


def test_watch_cli_refuses_less_than_five_minutes_and_bad_durations():
    from typer.testing import CliRunner

    from socmint import cli

    event()
    out = CliRunner().invoke(cli.app, ["investigate", "watch", "watched", "--every", "4m"])
    assert out.exit_code == 1 and "at least 5 minutes" in out.output
    out = CliRunner().invoke(cli.app, ["investigate", "watch", "watched", "--every", "soon"])
    assert out.exit_code == 1 and "cannot read the duration" in out.output


# ------------------------------------------------------------------------------------------- terms added mid-watch
def test_listener_matches_terms_set_after_it_was_created():
    fake = FakeConnect([[jet(1000, "r1", "cricket score"), jet(1001, "r2", "Delhi smog")]])
    lst = JetstreamListener("wss://jet.example/subscribe", TERMS, connect=fake, reconnect_delay_s=0)
    lst.set_terms({"en": ["cricket"]})
    lst.start()
    try:
        assert wait_for(lambda: lst.scanned >= 2)
    finally:
        lst.stop()
    assert set(lst.drain()) == {"at://did:plc:a/app.bsky.feed.post/r1"}


def test_terms_added_during_a_watch_reach_the_jetstream_listener():
    """DEMO.md recipe step 4: suggested terms added while a watch runs are matched by its open Jetstream connection
    from the next cycle on (the other sources read the config every cycle anyway)."""
    Posts.calls, Posts.on_fetch = 0, None
    iid = event()
    given: list[dict] = []

    class Listener:
        def set_terms(self, terms):
            given.append(terms)

        def stop(self):
            pass

    clock = Clock()

    def analyst_adds_a_term_then_waits(seconds):
        runner.edit(iid, add_terms=["#DelhiAirQuality"])
        return clock.wait(seconds)

    out = watch.watch(iid, every_s=300, for_s=300, echo=lambda s: None, clock=clock.now,
                      wait=analyst_adds_a_term_then_waits, factory=lambda lg, cfg: Posts(cfg),
                      resolver=lambda lg: [{"name": "feed", "type": "watch_posts"}], enrich_after=False,
                      listener_factory=lambda cfg: Listener())
    assert out["cycles"] == 2
    assert "#DelhiAirQuality" not in given[0]["en"] and "#DelhiAirQuality" in given[-1]["en"]
