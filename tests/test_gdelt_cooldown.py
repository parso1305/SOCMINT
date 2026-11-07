"""M17.2: GDELT fails cleanly and recovers: 10 s connect / 30 s read timeouts, one retry on a network error,
a saved cooldown after HTTP 429 (no request until it expires), and a stable cache key per investigation."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

import httpx
import pytest

from socmint.core import cooldown
from socmint.core.http import PoliteClient, Throttled
from socmint.core.models import utcnow
from socmint.ingest.gdelt import GdeltSource

BODY = {"articles": [{"url": "https://n.example/a", "title": "Delhi pollution: AQI severe again",
                      "seendate": "20261106T081500Z", "domain": "n.example", "language": "English"}]}
TERMS = {"en": ["Delhi pollution", "#DelhiPollution"]}


class Transport:
    """Scripted GDELT: each call pops the next action (an httpx exception class, a status code or 'ok')."""

    def __init__(self, *script):
        self.script = list(script)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self.script.pop(0) if self.script else "ok"
        if isinstance(step, type) and issubclass(step, Exception):
            raise step("simulated", request=request)
        if step == "ok":
            return httpx.Response(200, json=BODY)
        if step == "plaintext":
            return httpx.Response(200, text="Please limit requests to one every 5 seconds.")
        return httpx.Response(step, headers={"Retry-After": "30"}, text="Too Many Requests")


@pytest.fixture
def gdelt(monkeypatch):
    monkeypatch.setenv("SOCMINT_OFFLINE", "0")

    def make(*script, **cfg):
        t = Transport(*script)
        c = PoliteClient()
        c._client = httpx.Client(transport=httpx.MockTransport(t))
        src = GdeltSource({"name": "gdelt_india", "type": "gdelt", "rate_limit_s": 0.05,
                           "window_start": "2026-11-01T06:30:00", **cfg}, client=c)
        c._robots["https://api.gdeltproject.org"] = None  # robots.txt already "fetched"
        return src, t
    return make


def test_timeouts_are_10s_connect_and_30s_read(gdelt):
    src, t = gdelt("ok")
    src.search_terms(TERMS, utcnow() - timedelta(days=1), 50)
    to = t.requests[0].extensions["timeout"]
    assert to["connect"] == 10 and to["read"] == 30


def test_one_retry_after_a_timeout_then_success(gdelt):
    src, t = gdelt(httpx.ReadTimeout, "ok")
    res = src.search_terms(TERMS, utcnow() - timedelta(days=1), 50)
    assert len(t.requests) == 2 and len(res.items) == 1


def test_two_network_errors_fail_without_a_third_try(gdelt):
    src, t = gdelt(httpx.ConnectError, httpx.ConnectError, "ok")
    with pytest.raises(Exception, match="giving up"):
        src.search_terms(TERMS, utcnow() - timedelta(days=1), 50)
    assert len(t.requests) == 2


def test_429_sets_a_cooldown_and_later_runs_send_nothing(gdelt):
    src, t = gdelt(429, "ok")
    t0 = time.monotonic()
    with pytest.raises(Throttled, match="GDELT is limiting this IP; next try after"):
        src.search_terms(TERMS, utcnow() - timedelta(days=1), 50)
    assert time.monotonic() - t0 < 3, "no retry and no Retry-After sleep in an interactive run"
    assert len(t.requests) == 1
    until = cooldown.until("api.gdeltproject.org")
    assert until is not None and timedelta(minutes=14) < until - utcnow() <= timedelta(minutes=15)
    from socmint.core.settings import get_settings
    saved = json.loads((get_settings().cache_dir / "cooldowns.json").read_text(encoding="utf-8"))
    assert "api.gdeltproject.org" in saved
    with pytest.raises(Throttled):
        src.search_terms(TERMS, utcnow() - timedelta(days=1), 50)
    assert len(t.requests) == 1, "no request may be sent during the cooldown"


def test_plaintext_rate_limit_answer_is_a_throttle_too(gdelt):
    src, t = gdelt("plaintext", "ok")
    with pytest.raises(Throttled):
        src.search_terms(TERMS, utcnow() - timedelta(days=1), 50)
    assert cooldown.until("api.gdeltproject.org") is not None
    # once the cooldown is over, the plain-text answer (HTTP 200) must not be served from the cache
    cooldown.start("api.gdeltproject.org", minutes=15, reason="test", now=utcnow() - timedelta(minutes=16))
    assert len(src.search_terms(TERMS, utcnow() - timedelta(days=1), 50).items) == 1
    assert len(t.requests) == 2


def test_expired_cooldown_lets_the_request_through(gdelt):
    cooldown.start("api.gdeltproject.org", minutes=15, reason="HTTP 429", now=utcnow() - timedelta(minutes=16))
    assert cooldown.until("api.gdeltproject.org") is None
    src, t = gdelt("ok")
    assert len(src.search_terms(TERMS, utcnow() - timedelta(days=1), 50).items) == 1
    assert len(t.requests) == 1


def test_collect_twice_uses_the_cache_even_though_since_moved(gdelt):
    src, t = gdelt("ok", "ok")
    src.search_terms(TERMS, datetime(2026, 10, 31, 10, 0), 50)
    res = src.search_terms(TERMS, utcnow() - timedelta(minutes=5), 50)
    assert len(t.requests) == 1 and len(res.items) == 1
    assert t.requests[0].url.params["startdatetime"] == "20261101060000"  # the investigation window, to the hour


def test_runner_reports_throttled_with_the_next_try_time(monkeypatch):
    from socmint.investigate import runner
    from socmint.investigate.config import InvestigationConfig

    monkeypatch.setenv("SOCMINT_OFFLINE", "0")
    cooldown.start("api.gdeltproject.org", minutes=15, reason="HTTP 429")
    iid = runner.create(InvestigationConfig(kind="event", name="g", purpose="test", terms=TERMS, sources=["gdelt"]))
    out = runner.collect(iid, resolver=lambda logical: [{"name": "gdelt_india", "type": "gdelt"}], enrich_after=False)
    run = out["runs"][0]
    assert run["status"] == "throttled" and "next try after" in run["detail"]


def test_retry_after_is_not_slept_when_no_retry_follows(monkeypatch):
    monkeypatch.setenv("SOCMINT_OFFLINE", "0")
    t = Transport(429)
    c = PoliteClient()
    c._client = httpx.Client(transport=httpx.MockTransport(t))
    t0 = time.monotonic()
    with pytest.raises(Exception, match="429"):
        c.get("https://slow.example/x", check_robots=False, cache_ttl=0, retries=0)
    assert time.monotonic() - t0 < 2
