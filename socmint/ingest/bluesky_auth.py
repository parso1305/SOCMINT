"""Bluesky logged-in session for `searchPosts` (optional: only with BLUESKY_HANDLE + BLUESKY_APP_PASSWORD).

This is Bluesky's documented route for a logged-in client (docs.bsky.app, checked 2026-11-07), not a way around
the public AppView's 403:

1. `POST https://bsky.social/xrpc/com.atproto.server.createSession` with the handle and an *app password*;
2. read the account's PDS from the returned DID document (`#atproto_pds` service; fall back to bsky.social);
3. call `GET {pds}/xrpc/app.bsky.feed.searchPosts` with `Authorization: Bearer <accessJwt>` and
   `atproto-proxy: did:web:api.bsky.app#bsky_appview`, so the PDS forwards the call to the AppView.

createSession is limited to 30 per 5 minutes and 300 per day per account, so the session
(`accessJwt`, `refreshJwt`, `did`, `pds`) is kept in `data/cache/bluesky_session.json` (git-ignored) and reused.
On HTTP 401 or an `ExpiredToken` / `InvalidToken` answer: refresh once, then log in again once, then fail.

Tokens and the password never reach logs, exception messages, `collection_runs.detail` or the HTTP disk cache:
every call made here (login, refresh, searchPosts) uses `cache_ttl=0`, so nothing is read from or written to the
disk cache (its key would include the login body; headers are never part of it), and errors raised from here only
name the endpoint and the HTTP status. Keep `cache_ttl=0` on any new call in this module.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from socmint.core.http import HttpError, PoliteClient
from socmint.core.logging import get_logger
from socmint.core.settings import get_settings

log = get_logger(__name__)

ENTRYWAY = "https://bsky.social"
APPVIEW_PROXY = "did:web:api.bsky.app#bsky_appview"
_AUTH_ERRORS = {"ExpiredToken", "InvalidToken", "AuthenticationRequired", "AuthMissing"}
_lock = threading.Lock()  # one login / refresh at a time in this process (sessions are shared through the file)


class BlueskyAuthError(RuntimeError):
    """Login, refresh or an authenticated call failed. The message never carries a token or the password."""


def configured() -> bool:
    """True when both BLUESKY_HANDLE and BLUESKY_APP_PASSWORD are set."""
    return get_settings().has("bluesky_handle", "bluesky_app_password")


def session_path() -> Path:
    return get_settings().cache_dir / "bluesky_session.json"


def _auth_failure(e: HttpError) -> bool:
    """401, or a 400 whose XRPC error says the token expired or is invalid."""
    if e.status == 401:
        return True
    if e.status != 400:
        return False
    try:
        return json.loads(e.body or b"{}").get("error") in _AUTH_ERRORS
    except (ValueError, AttributeError):
        return False


def _pds_of(data: dict[str, Any]) -> str:
    for svc in ((data.get("didDoc") or {}).get("service") or []):
        if str(svc.get("id", "")).endswith("#atproto_pds") and str(svc.get("serviceEndpoint", "")).startswith("https://"):
            return str(svc["serviceEndpoint"]).rstrip("/")
    return ENTRYWAY


class BlueskySession:
    """Holds one account's session and makes authenticated XRPC GETs through the polite client."""

    def __init__(self, client: PoliteClient):
        self.client = client
        self.handle = get_settings().bluesky_handle.strip().lstrip("@")
        self._s: dict[str, Any] | None = None

    # ------------------------------------------------------------ storage
    def _load(self) -> dict[str, Any] | None:
        p = session_path()
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if s.get("handle") != self.handle or not s.get("accessJwt") or not s.get("refreshJwt"):
            return None  # another account's session (the handle changed) or a damaged file: log in again
        return s

    def _save(self, s: dict[str, Any]) -> None:
        p = session_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(s), encoding="utf-8")
        tmp.replace(p)
        self._s = s

    # ------------------------------------------------------------ session calls
    def _post(self, url: str, **kwargs: Any) -> dict[str, Any]:
        return json.loads(self.client.get(url, method="POST", cache_ttl=0, retries=0, **kwargs))

    def login(self) -> dict[str, Any]:
        url = f"{ENTRYWAY}/xrpc/com.atproto.server.createSession"
        try:
            data = self._post(url, json_body={"identifier": self.handle,
                                              "password": get_settings().bluesky_app_password})
        except HttpError as e:
            if e.status in (400, 401):
                raise BlueskyAuthError(f"Bluesky login failed (HTTP {e.status}); check BLUESKY_HANDLE and "
                                       "BLUESKY_APP_PASSWORD in .env (use an app password)") from None
            raise HttpError(f"Bluesky login: HTTP {e.status} from {url}", e.status) from None
        s = {"handle": self.handle, "did": data.get("did"), "pds": _pds_of(data),
             "accessJwt": data.get("accessJwt"), "refreshJwt": data.get("refreshJwt")}
        if not s["accessJwt"] or not s["refreshJwt"]:
            raise BlueskyAuthError("Bluesky login answered without a session; check BLUESKY_HANDLE")
        self._save(s)
        log.info("bluesky_login", extra={"handle": self.handle, "pds": s["pds"]})
        return s

    def refresh(self, s: dict[str, Any]) -> dict[str, Any]:
        """refreshSession with the refresh token. Raises HttpError when the server refuses it."""
        data = self._post(f"{s['pds']}/xrpc/com.atproto.server.refreshSession",
                          headers={"Authorization": f"Bearer {s['refreshJwt']}"})
        s = {**s, "accessJwt": data.get("accessJwt"), "refreshJwt": data.get("refreshJwt") or s["refreshJwt"]}
        if not s["accessJwt"]:
            raise HttpError("Bluesky refreshSession answered without a token", 401)
        self._save(s)
        log.info("bluesky_session_refreshed", extra={"handle": self.handle})
        return s

    def current(self) -> dict[str, Any]:
        with _lock:
            if self._s is None:
                self._s = self._load() or self.login()
            return self._s

    def _call(self, s: dict[str, Any], nsid: str, params: dict[str, Any]) -> Any:
        try:
            return self.client.get_json(f"{s['pds']}/xrpc/{nsid}", params=params, cache_ttl=0,
                                        headers={"Authorization": f"Bearer {s['accessJwt']}",
                                                 "atproto-proxy": APPVIEW_PROXY})
        except HttpError as e:  # re-raise without the body (it is only kept to read the XRPC error name)
            raise HttpError(f"HTTP {e.status} for {s['pds']}/xrpc/{nsid}", e.status, e.body) from None

    def get_json(self, nsid: str, params: dict[str, Any]) -> Any:
        """Authenticated XRPC GET. On 401 / expired token: refresh once, then log in again once, then fail."""
        s = self.current()
        try:
            return self._call(s, nsid, params)
        except HttpError as e:
            if not _auth_failure(e):
                raise
        with _lock:
            try:
                s = self.refresh(s)
                refreshed = True
            except HttpError as e:
                if not _auth_failure(e):
                    raise HttpError(f"Bluesky refreshSession: HTTP {e.status}", e.status) from None
                refreshed = False
            if not refreshed:
                s = self.login()
        if refreshed:
            try:
                return self._call(s, nsid, params)
            except HttpError as e:
                if not _auth_failure(e):
                    raise
            with _lock:
                s = self.login()
        try:
            return self._call(s, nsid, params)
        except HttpError as e:
            if _auth_failure(e):
                raise BlueskyAuthError(f"Bluesky {nsid} still answered HTTP {e.status} after a session refresh and "
                                       "a new login; check the account and BLUESKY_APP_PASSWORD") from None
            raise
