"""Polite HTTP client: descriptive User-Agent, robots.txt, per-host rate limits, retries with backoff, disk cache.

Every network call in the project goes through `PoliteClient` so these rules are enforced in one place.
Set `SOCMINT_OFFLINE=1` to make any network call fail fast (used by the demo and tests).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import urllib.robotparser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from socmint.core.logging import get_logger
from socmint.core.settings import get_settings, threshold

log = get_logger(__name__)


class OfflineError(RuntimeError):
    """Raised when a network call is attempted in offline mode."""


class RobotsDisallowed(RuntimeError):
    """Raised when robots.txt disallows the URL for our User-Agent."""


class HttpError(RuntimeError):
    """Raised after retries are exhausted or on a non-retryable HTTP status."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def is_offline() -> bool:
    return os.environ.get("SOCMINT_OFFLINE", "") not in ("", "0", "false", "False")


_RETRYABLE = {429, 500, 502, 503, 504}
_warned_contact = False


class PoliteClient:
    """Shared HTTP client. Thread-safe for rate limiting; one instance per process is enough."""

    def __init__(self, rate_limit_s: float | None = None, timeout_s: float | None = None, retries: int | None = None):
        s = get_settings()
        self.user_agent = s.user_agent
        self.default_rate = rate_limit_s if rate_limit_s is not None else float(threshold("ingest.default_rate_limit_s", 2.0))
        self.timeout = timeout_s if timeout_s is not None else float(threshold("ingest.http_timeout_s", 20))
        self.retries = retries if retries is not None else int(threshold("ingest.http_retries", 3))
        self.cache_dir = s.cache_dir / "http"
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last_call: dict[str, float] = {}
        self._host_rate: dict[str, float] = {}
        self._lock = threading.Lock()
        self._client = httpx.Client(
            headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=self.timeout,
            follow_redirects=True,
        )
        global _warned_contact
        if not s.contact_email and not _warned_contact:
            log.warning("contact_email_missing", extra={"hint": "set CONTACT_EMAIL in .env; it is sent in the User-Agent"})
            _warned_contact = True

    def set_rate_limit(self, host: str, seconds: float) -> None:
        """Override the minimum interval between requests to `host`."""
        self._host_rate[host] = seconds

    # -- robots ---------------------------------------------------------------
    def allowed(self, url: str) -> bool:
        """Check robots.txt (cached per host). Missing/unreadable robots.txt means allowed."""
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in self._robots:
            rp: urllib.robotparser.RobotFileParser | None = urllib.robotparser.RobotFileParser()
            try:
                self._wait(p.netloc)  # robots.txt counts against the host's rate limit too
                r = self._client.get(base + "/robots.txt", timeout=10)
                ctype = r.headers.get("content-type", "")
                if r.status_code == 200 and "html" not in ctype.lower():
                    rp.parse(r.text.splitlines())
                else:
                    rp = None
            except httpx.HTTPError:
                rp = None
            self._robots[base] = rp
        rp = self._robots[base]
        return True if rp is None else rp.can_fetch(self.user_agent, url)

    # -- rate limit -----------------------------------------------------------
    def _wait(self, host: str) -> None:
        interval = self._host_rate.get(host, self.default_rate)
        with self._lock:
            last = self._last_call.get(host, 0.0)
            delay = last + interval - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._last_call[host] = time.monotonic()

    # -- cache ----------------------------------------------------------------
    def _cache_paths(self, key: str) -> tuple[Path, Path]:
        h = hashlib.sha1(key.encode()).hexdigest()
        return self.cache_dir / f"{h}.bin", self.cache_dir / f"{h}.json"

    def _cache_get(self, key: str, ttl: float) -> bytes | None:
        body, meta = self._cache_paths(key)
        if ttl <= 0 or not body.exists() or not meta.exists():
            return None
        info = json.loads(meta.read_text())
        if time.time() - info.get("fetched_at", 0) > ttl:
            return None
        return body.read_bytes()

    def _cache_put(self, key: str, content: bytes, url: str, status: int) -> None:
        body, meta = self._cache_paths(key)
        url = url.split("?")[0]  # never persist query strings (they may carry API keys)
        body.parent.mkdir(parents=True, exist_ok=True)
        body.write_bytes(content)
        meta.write_text(json.dumps({"url": url, "status": status, "fetched_at": time.time()}))

    # -- main entry -------------------------------------------------------------
    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        cache_ttl: float | None = None,
        check_robots: bool = True,
        auth: Any = None,
        method: str = "GET",
        data: dict[str, Any] | None = None,
        cache_key_extra: str = "",
        json_body: Any = None,
        retries: int | None = None,
    ) -> bytes:
        """Fetch `url` and return the body bytes.

        cache_ttl: seconds to reuse a cached body (None = config default, 0 = no cache; use 0 for images
        so image files are never written to disk).
        retries: override the client's retry count for this call (e.g. 0 for interactive collection).
        Raises OfflineError, RobotsDisallowed or HttpError.
        """
        if is_offline():
            raise OfflineError(f"offline mode: refusing to fetch {url}")
        ttl = float(threshold("ingest.cache_ttl_s", 3600)) if cache_ttl is None else cache_ttl
        safe_params = {k: v for k, v in (params or {}).items() if "key" not in k.lower()}
        safe_url = url.split("?key=")[0]
        body_key = json.dumps(json_body, sort_keys=True) if json_body is not None else ""
        key = f"{method} {safe_url} {json.dumps(safe_params, sort_keys=True)} {body_key} {cache_key_extra}"
        cached = self._cache_get(key, ttl)
        if cached is not None:
            return cached
        if check_robots and not self.allowed(url):
            raise RobotsDisallowed(f"robots.txt disallows {url} for our User-Agent")

        host = urlparse(url).netloc
        last_exc: Exception | None = None
        max_retries = self.retries if retries is None else retries
        for attempt in range(max_retries + 1):
            self._wait(host)
            try:
                r = self._client.request(method, url, params=params, headers=headers, auth=auth, data=data, json=json_body)
            except httpx.HTTPError as e:
                last_exc = e
                status = None
            else:
                status = r.status_code
                if status < 400:
                    if ttl > 0:
                        self._cache_put(key, r.content, str(r.url), status)
                    return r.content
                if status not in _RETRYABLE:
                    raise HttpError(f"HTTP {status} for {url}", status)
                last_exc = HttpError(f"HTTP {status} for {url}", status)
                retry_after = r.headers.get("retry-after")
                if retry_after and retry_after.isdigit():
                    time.sleep(min(int(retry_after), 60))
            if attempt < max_retries:
                backoff = min(2**attempt * 1.5, 30)
                if status == 429:  # throttled: back off well beyond the host's own interval
                    backoff = min(max(backoff, self._host_rate.get(host, self.default_rate)) * 2 ** (attempt + 1), 120)
                log.info("http_retry", extra={"url": url, "attempt": attempt + 1, "status": status, "sleep_s": backoff})
                time.sleep(backoff)
        raise HttpError(f"giving up on {url}: {last_exc}", getattr(last_exc, "status", None))

    def get_json(self, url: str, **kwargs: Any) -> Any:
        return json.loads(self.get(url, **kwargs).decode("utf-8", errors="replace"))

    def close(self) -> None:
        self._client.close()


_shared: PoliteClient | None = None


def get_client() -> PoliteClient:
    """Process-wide shared client (so per-host rate limits apply across sources)."""
    global _shared
    if _shared is None:
        _shared = PoliteClient()
    return _shared
