"""M17.1: sources are fetched in parallel (stores stay sequential in the calling thread); the HTTP client spaces
requests per host, so two hosts never wait on each other while one host stays spaced by its rate limit."""

from __future__ import annotations

import threading
import time
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from socmint.core.db import session_scope
from socmint.core.http import PoliteClient
from socmint.core.models import AuditLog, CollectionRun
from socmint.core.schemas import FetchResult, ItemIn
from socmint.ingest.base import Source
from socmint.investigate import runner
from socmint.investigate.config import InvestigationConfig


def slow_client(seconds: float, counter: dict[str, int] | None = None) -> PoliteClient:
    """A real PoliteClient whose transport answers every request after `seconds` (a slow network)."""
    def handler(request: httpx.Request) -> httpx.Response:
        if counter is not None:
            counter[request.url.path] = counter.get(request.url.path, 0) + 1
        time.sleep(seconds)
        return httpx.Response(200, text="User-agent: *\nAllow: /\n" if request.url.path == "/robots.txt" else "{}")

    c = PoliteClient()
    c._client = httpx.Client(transport=httpx.MockTransport(handler))
    return c


class HostSource(Source):
    """One request to cfg['url'] per search, then one post."""

    type_name = "fake_host"
    capabilities = {"terms"}
    threads: list[int] = []

    def hosts(self) -> list[str]:
        return [self.cfg["url"]]

    def fetch(self, since):
        return FetchResult()

    def search_terms(self, terms_by_lang, since, limit):
        HostSource.threads.append(threading.get_ident())
        if self.cfg.get("fail"):
            raise RuntimeError("upstream exploded")
        self.client.get(self.cfg["url"], cache_ttl=0, check_robots=False)
        return FetchResult(items=[ItemIn(source=self.name, source_type="news", external_id=self.cfg["url"],
                                         url=self.cfg["url"], text="Pune flood update from the city")])


def collect_with(urls: dict[str, dict[str, Any]], client: PoliteClient, **kw: Any) -> tuple[dict[str, Any], float]:
    iid = runner.create(InvestigationConfig(kind="event", name="parallel", purpose="test",
                                            terms={"en": ["Pune flood"]}, sources=["news_rss"]))
    cfgs = [{"name": name, "type": "fake_host", **c} for name, c in urls.items()]
    t0 = time.monotonic()
    out = runner.collect(iid, factory=lambda logical, cfg: HostSource(cfg, client=client),
                         resolver=lambda logical: cfgs, enrich_after=False, **kw)
    return out, time.monotonic() - t0


@pytest.fixture
def online(monkeypatch):
    monkeypatch.setenv("SOCMINT_OFFLINE", "0")
    HostSource.threads = []


def test_two_hosts_fetch_in_parallel(online):
    out, took = collect_with({"a": {"url": "https://a.example/feed"}, "b": {"url": "https://b.example/feed"}},
                             slow_client(1.0))
    assert {r["source"]: r["status"] for r in out["runs"]} == {"a": "ok", "b": "ok"}
    assert out["new_posts"] == 2
    assert took < 1.6, f"two 1 s sources on different hosts took {took:.2f} s"


def test_same_host_stays_spaced_by_its_rate_limit(online):
    out, took = collect_with({"a": {"url": "https://same.example/a", "rate_limit_s": 1.0},
                              "b": {"url": "https://same.example/b", "rate_limit_s": 1.0}}, slow_client(1.0))
    assert out["new_posts"] == 2
    assert took >= 1.9, f"two requests to one host with a 1 s interval took only {took:.2f} s"


def test_failing_source_does_not_stop_the_others_and_runs_are_recorded(online):
    progress_threads: list[int] = []
    out, _ = collect_with({"a": {"url": "https://a.example/feed"}, "bad": {"url": "https://c.example/x", "fail": True},
                           "b": {"url": "https://b.example/feed"}}, slow_client(0.2),
                          progress=lambda name, run: progress_threads.append(threading.get_ident()))
    status = {r["source"]: r["status"] for r in out["runs"]}
    assert status == {"a": "ok", "b": "ok", "bad": "error"}
    assert out["new_posts"] == 2
    main = threading.get_ident()
    assert progress_threads == [main, main, main]          # progress only from the calling thread
    assert main not in HostSource.threads                  # fetches ran in worker threads
    with session_scope() as s:
        runs = {r.source: r for r in s.scalars(select(CollectionRun)).all()}
        assert set(runs) == {"a", "b", "bad"} and runs["bad"].detail.startswith("RuntimeError: upstream exploded")
        assert runs["a"].fetched == 1 and runs["a"].new == 1 and runs["a"].finished_at >= runs["a"].started_at
        assert s.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "investigation_collect")) == 1


def test_waiting_on_one_host_never_blocks_another():
    c = PoliteClient(rate_limit_s=1.0)
    c._wait("a.example")                       # a.example's next slot is 1 s away
    t = threading.Thread(target=c._wait, args=("a.example",))
    t.start()
    time.sleep(0.05)                           # the thread is now sleeping for a.example
    t0 = time.monotonic()
    c._wait("b.example")
    assert time.monotonic() - t0 < 0.3
    t.join()


def test_robots_txt_is_fetched_once_per_host_under_concurrency(online):
    counter: dict[str, int] = {}
    c = slow_client(0.3, counter)
    threads = [threading.Thread(target=c.allowed, args=(f"https://r.example/p{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert counter.get("/robots.txt") == 1


# ------------------------------------------------------------------------------------------- progressive enrich
class Gate(Source):
    """`fast` returns a post at once; `slow` waits (up to 5 s) for the fast post to be enriched, then returns its own.
    `fc` returns a fact-check instead of a post."""

    type_name = "fake_gate"
    capabilities = {"terms"}
    enriched = threading.Event()
    overlapped: list[bool] = []

    def fetch(self, since):
        return FetchResult()

    def search_terms(self, terms_by_lang, since, limit):
        from datetime import datetime

        from socmint.core.schemas import FactCheckIn

        if self.name == "slow":
            Gate.overlapped.append(Gate.enriched.wait(5))
        if self.name == "fc":
            Gate.overlapped.append(Gate.enriched.wait(5))
            return FetchResult(factchecks=[FactCheckIn(publisher="Demo Check", title="Pune flood claim is false",
                                                       url="https://check.example/1", lang="en",
                                                       published_at=datetime(2026, 11, 1))])
        return FetchResult(items=[ItemIn(source=self.name, source_type="news", external_id=f"{self.name}-1",
                                         url=f"https://{self.name}.example/1", text=f"Pune flood update {self.name}")])


def gate_collect(names: list[str], monkeypatch) -> tuple[dict[str, Any], list[list[int]]]:
    Gate.enriched, Gate.overlapped = threading.Event(), []
    calls: list[list[int]] = []
    real = runner.enrich

    def spy(ids, **kw):
        calls.append(list(ids))
        out = real(ids, **kw)
        Gate.enriched.set()
        return out

    monkeypatch.setattr(runner, "enrich", spy)
    iid = runner.create(InvestigationConfig(kind="event", name="progressive", purpose="test",
                                            terms={"en": ["Pune flood"]}, sources=["news_rss"]))
    out = runner.collect(iid, factory=lambda logical, cfg: Gate(cfg),
                         resolver=lambda logical: [{"name": n, "type": "fake_gate"} for n in names])
    return out, calls


def test_posts_are_enriched_while_slower_sources_still_fetch(monkeypatch):
    """The first source's posts (and the model load) are enriched while a slower source is still fetching."""
    out, calls = gate_collect(["fast", "slow"], monkeypatch)
    assert Gate.overlapped == [True]
    assert len(calls) == 2 and sorted(sum(calls, [])) == sorted(set(sum(calls, [])))  # each post enriched once
    assert out["new_posts"] == 2 and out["enrich"]["posts"] == 2
    assert out["enrich"]["labels"]


def test_posts_enriched_before_a_late_fact_check_are_matched_again(monkeypatch):
    """Claim matching must see fact-checks stored later in the same collect: earlier posts are enriched again."""
    out, calls = gate_collect(["fast", "fc"], monkeypatch)
    assert Gate.overlapped == [True]
    assert len(calls) == 2 and calls[0] == calls[1]  # the fast post, then again after the fact-check arrived
    assert out["enrich"]["posts"] == 1
