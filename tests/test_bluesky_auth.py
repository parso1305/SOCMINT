"""M18.1: optional Bluesky logged-in search (only with BLUESKY_HANDLE + BLUESKY_APP_PASSWORD), Bluesky's documented
route: createSession on bsky.social, searchPosts on the account's PDS with the AppView proxy header. Sessions are
reused, refreshed once and re-created once on 401; tokens never reach logs, the HTTP cache or collection_runs."""

from __future__ import annotations

import json
import logging
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from socmint.core.db import session_scope
from socmint.core.http import PoliteClient
from socmint.core.models import CollectionRun, utcnow
from socmint.core.settings import get_settings, reset_settings
from socmint.ingest.bluesky import BlueskySource

PASSWORD = "abcd-efgh-ijkl-mnop"
ACCESS = ["access-TOKEN-one", "access-TOKEN-two", "access-TOKEN-three"]
REFRESH = ["refresh-TOKEN-one", "refresh-TOKEN-two", "refresh-TOKEN-three"]
TERMS = {"en": ["Delhi pollution"]}


def post(n: int, did: str = "did:plc:alice", labels: list | None = None) -> dict:
    return {"uri": f"at://{did}/app.bsky.feed.post/r{n}", "cid": f"c{n}",
            "author": {"did": did, "handle": did.split(":")[-1] + ".bsky.social", "labels": labels or []},
            "record": {"text": f"Delhi pollution update {n}", "createdAt": "2026-11-06T10:00:00Z"},
            "indexedAt": "2026-11-06T10:00:01Z", "likeCount": 1, "repostCount": 0, "replyCount": 0, "quoteCount": 0}


class Bsky:
    """Scripted Bluesky: `search` is a list of responses for successive searchPosts calls (status or 'page1'...)."""

    def __init__(self, search=("page1", "page2"), refresh_ok=True, login_ok=True):
        self.search = list(search)
        self.refresh_ok, self.login_ok = refresh_ok, login_ok
        self.calls: list[tuple[str, str, dict]] = []
        self.logins = self.refreshes = 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        self.calls.append((req.url.host, path, dict(req.headers)))
        if path == "/robots.txt":
            return httpx.Response(404)
        if path.endswith("createSession"):
            self.logins += 1
            body = json.loads(req.content)
            assert body == {"identifier": "project.bsky.social", "password": PASSWORD}
            if not self.login_ok:
                return httpx.Response(401, json={"error": "AuthenticationRequired", "message": "Invalid identifier"})
            i = self.logins - 1 + self.refreshes
            return httpx.Response(200, json={"accessJwt": ACCESS[i], "refreshJwt": REFRESH[i], "did": "did:plc:proj",
                                             "handle": "project.bsky.social", "didDoc": {"service": [
                                                 {"id": "#atproto_pds", "type": "AtprotoPersonalDataServer",
                                                  "serviceEndpoint": "https://pds.example"}]}})
        if path.endswith("refreshSession"):
            self.refreshes += 1
            if not self.refresh_ok:
                return httpx.Response(400, json={"error": "ExpiredToken", "message": "Token has expired"})
            i = self.logins - 1 + self.refreshes
            return httpx.Response(200, json={"accessJwt": ACCESS[i], "refreshJwt": REFRESH[i], "did": "did:plc:proj",
                                             "handle": "project.bsky.social"})
        if path.endswith("searchPosts") and req.url.host == "public.api.bsky.app":
            return httpx.Response(403, json={"error": "Forbidden"})  # the public AppView refuses search (live 2026-11-06)
        if path.endswith("searchPosts"):
            step = self.search.pop(0) if self.search else "empty"
            if step == 401:
                return httpx.Response(401, json={"error": "InvalidToken", "message": "Bad token"})
            if step == "expired":
                return httpx.Response(400, json={"error": "ExpiredToken", "message": "Token has expired"})
            if step == "page1":
                return httpx.Response(200, json={"posts": [post(1), post(2, "did:plc:optout", [
                    {"val": "!no-unauthenticated", "src": "did:plc:optout"}])], "cursor": "c1"})
            if step == "page2":
                return httpx.Response(200, json={"posts": [post(3)]})
            return httpx.Response(200, json={"posts": []})
        return httpx.Response(403, json={"error": "Forbidden"})


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("SOCMINT_OFFLINE", "0")
    monkeypatch.setenv("BLUESKY_HANDLE", "project.bsky.social")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", PASSWORD)
    reset_settings()


def source(t: Bsky) -> BlueskySource:
    c = PoliteClient(rate_limit_s=0)
    c._client = httpx.Client(transport=httpx.MockTransport(t))
    return BlueskySource({"name": "bluesky", "type": "bluesky", "search_mode": "auto", "rate_limit_s": 0}, client=c)


def test_logged_in_search_uses_the_pds_with_the_appview_proxy_and_follows_the_cursor(keys):
    t = Bsky()
    res = source(t).search_terms(TERMS, utcnow() - timedelta(days=1), 200)
    assert [it.external_id.rsplit("/", 1)[-1] for it in res.items] == ["r1", "r3"]  # the opted-out author dropped
    assert res.mode == "search (logged in)"
    search = [c for c in t.calls if c[1].endswith("searchPosts")]
    assert len(search) == 2 and {c[0] for c in search} == {"pds.example"}
    assert search[0][2]["authorization"] == f"Bearer {ACCESS[0]}"
    assert search[0][2]["atproto-proxy"] == "did:web:api.bsky.app#bsky_appview"
    assert not any(c[0] in ("api.bsky.app", "public.api.bsky.app") for c in t.calls)
    saved = json.loads((get_settings().cache_dir / "bluesky_session.json").read_text(encoding="utf-8"))
    assert saved["pds"] == "https://pds.example" and saved["did"] == "did:plc:proj"


def test_session_is_reused_between_runs(keys):
    t = Bsky(search=["page2", "page2"])
    source(t).search_terms(TERMS, None, 50)
    source(t).search_terms(TERMS, None, 50)
    assert t.logins == 1


@pytest.mark.parametrize("failure", [401, "expired"])
def test_expired_access_token_is_refreshed_once(keys, failure):
    t = Bsky(search=["page2", failure, "page2"])
    source(t).search_terms(TERMS, None, 50)
    res = source(t).search_terms(TERMS, None, 50)
    assert len(res.items) == 1 and t.refreshes == 1 and t.logins == 1
    last = [c for c in t.calls if c[1].endswith("searchPosts")][-1]
    assert last[2]["authorization"] == f"Bearer {ACCESS[1]}"


def test_failed_refresh_logs_in_again_once(keys):
    t = Bsky(search=["page2", 401, "page2"], refresh_ok=False)
    source(t).search_terms(TERMS, None, 50)
    res = source(t).search_terms(TERMS, None, 50)
    assert len(res.items) == 1 and t.refreshes == 1 and t.logins == 2


def test_still_unauthorised_after_refresh_and_login_fails_as_error_without_leaking(keys, caplog):
    from socmint.investigate import runner
    from socmint.investigate.config import InvestigationConfig

    caplog.set_level(logging.DEBUG)
    t = Bsky(search=[401, 401, 401], refresh_ok=False)
    iid = runner.create(InvestigationConfig(kind="event", name="bsky", purpose="test", terms=TERMS,
                                            sources=["bluesky"]))
    out = runner.collect(iid, factory=lambda lg, cfg: source(t),
                         resolver=lambda lg: [{"name": "bluesky", "type": "bluesky"}], enrich_after=False)
    assert out["runs"][0]["status"] == "error"
    assert t.refreshes == 1 and t.logins == 2
    with session_scope() as s:
        details = " ".join(r.detail or "" for r in s.scalars(select(CollectionRun)).all())
    cache = " ".join(p.read_text(encoding="utf-8", errors="replace")
                     for p in (get_settings().cache_dir / "http").glob("*")) \
        if (get_settings().cache_dir / "http").exists() else ""
    for secret in [PASSWORD, *ACCESS, *REFRESH]:
        assert secret not in caplog.text and secret not in details and secret not in cache


def test_tokens_never_reach_logs_or_the_http_cache_on_success(keys, caplog):
    caplog.set_level(logging.DEBUG)
    t = Bsky()
    source(t).search_terms(TERMS, None, 200)
    http_cache = get_settings().cache_dir / "http"
    blobs = " ".join(p.read_text(encoding="utf-8", errors="replace") for p in http_cache.glob("*")) \
        if http_cache.exists() else ""
    for secret in [PASSWORD, *ACCESS, *REFRESH]:
        assert secret not in caplog.text and secret not in blobs


def test_without_keys_behaviour_is_unchanged(monkeypatch):
    monkeypatch.setenv("SOCMINT_OFFLINE", "0")
    monkeypatch.setenv("BLUESKY_HANDLE", "")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "")
    reset_settings()
    t = Bsky()
    src = source(t)
    from socmint.core.schemas import FetchResult

    monkeypatch.setattr(src, "_jetstream", lambda *a, **k: FetchResult(notes=["listened"]))
    res = src.search_terms(TERMS, None, 50)
    assert res.notes[0].startswith("searchPosts on the public AppView answered HTTP 403")
    assert res.mode == "live listen 60 s"
    assert t.logins == 0 and not any(c[0] in ("bsky.social", "pds.example") for c in t.calls)
    assert any(c[0] == "public.api.bsky.app" and c[1].endswith("searchPosts") for c in t.calls)


def test_the_search_mode_is_stored_on_the_run_and_named_on_the_chip(keys):
    from socmint.investigate import runner
    from socmint.investigate.config import InvestigationConfig
    from socmint.showcase.data import load, source_status

    t = Bsky()
    iid = runner.create(InvestigationConfig(kind="event", name="bsky-mode", purpose="test", terms=TERMS,
                                            sources=["bluesky"]))
    out = runner.collect(iid, factory=lambda lg, cfg: source(t),
                         resolver=lambda lg: [{"name": "bluesky", "type": "bluesky"}], enrich_after=False)
    assert out["runs"][0]["status"] == "ok" and out["runs"][0]["mode"] == "search (logged in)"
    with session_scope() as s:
        assert s.scalars(select(CollectionRun)).one().mode == "search (logged in)"
    chip = source_status(load(iid))[0]
    assert chip["text"].startswith("Bluesky · search (logged in)")
