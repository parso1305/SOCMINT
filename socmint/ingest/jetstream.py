"""One long-lived Jetstream connection for `investigate watch` (Bluesky without an app password).

Instead of a fresh 60 s listen every cycle, the watch keeps **one** connection to Bluesky's official Jetstream
firehose open in a background thread. Posts are matched against the event's terms as they arrive and the matching
post URIs are queued (bounded, de-duplicated); each cycle drains the queue and hydrates the URIs through the public
AppView, where opt-outs are applied. A dropped connection is resumed with Jetstream's `cursor` set to the `time_us`
of the last event received, so nothing is replayed from before that event, and nothing at all at the start.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from socmint.core.logging import get_logger
from socmint.ingest.base import TermMatcher

log = get_logger(__name__)

POST_NSID = "app.bsky.feed.post"


class JetstreamListener:
    """Background Jetstream subscription. `drain()` hands over {post at-uri: matched terms} since the last drain."""

    def __init__(self, url: str, terms_by_lang: dict[str, list[str]], since: datetime | None = None,
                 connect: Callable[..., Any] | None = None, user_agent: str = "", max_queue: int = 5000,
                 reconnect_delay_s: float = 2.0, max_delay_s: float = 60.0):
        self.url = url
        self.matcher = TermMatcher(terms_by_lang)
        self.since = since
        self.user_agent = user_agent
        self.max_queue = max_queue
        self.reconnect_delay_s = reconnect_delay_s
        self.max_delay_s = max_delay_s
        self._connect = connect
        self._queue: dict[str, list[str]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.cursor: int | None = None  # time_us of the last event received
        self.scanned = self.matched = self.dropped = self.reconnects = 0
        self.last_error: str | None = None
        self.thread = threading.Thread(target=self._run, name="jetstream-watch", daemon=True)

    # ------------------------------------------------------------ control
    def start(self) -> JetstreamListener:
        self.thread.start()
        return self

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout)

    def set_terms(self, terms_by_lang: dict[str, list[str]]) -> None:
        """Match these terms from now on (the watch passes the investigation's current terms every cycle, so terms
        added while it runs are matched too). Posts already received are not matched again."""
        self.matcher = TermMatcher(terms_by_lang)

    def drain(self) -> dict[str, list[str]]:
        with self._lock:
            out, self._queue = self._queue, {}
        return out

    # ------------------------------------------------------------ worker
    def _put(self, uri: str, terms: list[str]) -> None:
        with self._lock:
            if uri in self._queue:
                return
            if len(self._queue) >= self.max_queue:
                self.dropped += 1
                return
            self._queue[uri] = terms
            self.matched += 1

    def _subscribe_url(self) -> str:
        url = f"{self.url}?wantedCollections={POST_NSID}"
        return url + (f"&cursor={self.cursor}" if self.cursor is not None else "")

    def _run(self) -> None:
        asyncio.run(self._loop())

    async def _loop(self) -> None:
        from socmint.ingest.bluesky import match_jetstream

        if self._connect is None:
            import websockets

            self._connect = websockets.connect
        delay = self.reconnect_delay_s
        while not self._stop.is_set():
            got = False
            try:
                async with self._connect(self._subscribe_url(), open_timeout=20, user_agent_header=self.user_agent,
                                         max_size=2**21) as ws:
                    while not self._stop.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except TimeoutError:
                            continue
                        msg = json.loads(raw)
                        if isinstance(msg.get("time_us"), int):
                            self.cursor = msg["time_us"]
                        self.scanned += 1
                        got = True
                        for uri, terms in match_jetstream([msg], self.matcher, self.since, 1).items():
                            self._put(uri, terms)
            except Exception as e:  # noqa: BLE001  (dropped connection, refused handshake, network error)
                self.last_error = type(e).__name__
            if self._stop.is_set():
                break
            self.reconnects += 1
            delay = self.reconnect_delay_s if got else min(self.max_delay_s, max(delay * 2, self.reconnect_delay_s))
            log.info("jetstream_reconnect", extra={"cursor": self.cursor, "reconnects": self.reconnects,
                                                   "error": self.last_error, "sleep_s": delay})
            end = time.monotonic() + delay
            while time.monotonic() < end and not self._stop.is_set():
                await asyncio.sleep(min(0.2, max(0.0, end - time.monotonic())))
