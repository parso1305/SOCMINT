"""`socmint investigate watch NAME --every 15m [--for 8h] [--analyze]` (M19.2).

Repeats collect (and optionally analyze) every cycle, at least 5 minutes apart. Each cycle is audited (the
collect audit row, plus a start and a stop row for the watch) and prints one summary line. Sources in a cooldown
(GDELT after a 429) are skipped by the source itself without a request. Ctrl+C stops after the source(s) already
fetching; a second Ctrl+C quits at once.

Bluesky without an app password: one Jetstream connection stays open for the whole watch in a background thread
(`socmint.ingest.jetstream`), so each cycle stores every matching post made since the watch started instead of
only those made during a 60 s listen. This also builds the collection history narrative growth alerts need.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from socmint.core.db import audit, session_scope
from socmint.core.http import get_client, is_offline
from socmint.core.logging import get_logger
from socmint.core.models import utcnow
from socmint.investigate import runner
from socmint.investigate.config import InvestigationConfig

log = get_logger(__name__)

MIN_EVERY_S = 300


def parse_duration(text: str | None) -> int:
    """'15m', '8h', '1h30m', '90s' -> seconds; a bare number means minutes."""
    t = (text or "").strip().lower()
    if t.isdigit():
        return int(t) * 60
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", t)
    if not t or not m or not any(m.groups()):
        raise ValueError(f"cannot read the duration {text!r}; use for example 15m, 1h30m or 8h")
    h, mi, s = (int(g or 0) for g in m.groups())
    return h * 3600 + mi * 60 + s


def sliced_wait(stop: threading.Event, seconds: float) -> bool:
    """Wait up to `seconds`, waking every second so Ctrl+C (a signal handler setting `stop`) is seen on Windows
    too. True when stopped."""
    end = time.monotonic() + seconds
    while not stop.is_set():
        left = end - time.monotonic()
        if left <= 0:
            return False
        stop.wait(min(1.0, left))
    return True


def start_listener(cfg: InvestigationConfig, echo: Callable[[str], Any] = print) -> Any:
    """One Jetstream connection for the watch, when Bluesky is a source, no app password is set and the
    configured search mode allows Jetstream. None otherwise."""
    from socmint.ingest import bluesky_auth
    from socmint.ingest.bluesky import JETSTREAM
    from socmint.ingest.jetstream import JetstreamListener

    if "bluesky" not in cfg.sources or bluesky_auth.configured() or is_offline():
        return None
    scfg = next((c for c in runner.resolve_sources("bluesky") if c.get("search_mode", "auto") in ("auto", "jetstream")),
                None)
    if scfg is None:
        return None
    url = scfg.get("jetstream_url", JETSTREAM)
    client = get_client()
    if not client.allowed(url.replace("wss://", "https://", 1)):
        echo("Bluesky: robots.txt disallows the Jetstream host; no watch connection opened")
        return None
    echo("Bluesky: one Jetstream connection stays open for the whole watch (posts since now, matched as they arrive)")
    return JetstreamListener(url, cfg.terms, user_agent=client.user_agent).start()


def _line(n: int, out: dict[str, Any], analysis: dict[str, Any] | None, next_in: float | None) -> str:
    runs = ", ".join(f"{r['source']} {r['status']}" + (f" ({r['new']} new)" if r.get("new") else "")
                     for r in out["runs"])
    line = f"cycle {n} · {utcnow():%H:%M} UTC · {out['new_posts']} new posts · {runs or 'no sources'}"
    if analysis is not None:
        line += " · analyze: " + ", ".join(f"{m} {r['status']}" for m, r in analysis["modules"].items())
    if out.get("stopped"):
        line += " · stopped after the current source" + (f"; not started: {', '.join(out['skipped'])}"
                                                         if out.get("skipped") else "")
    elif next_in is not None:
        line += f" · next at {utcnow() + timedelta(seconds=next_in):%H:%M} UTC"
    return line


def watch(ref: int | str, every_s: int, for_s: int | None = None, analyze: bool = False,
          echo: Callable[[str], Any] = print, stop: threading.Event | None = None,
          clock: Callable[[], float] = time.monotonic, wait: Callable[[float], bool] | None = None,
          factory: runner.SourceFactory | None = None, resolver: Callable[[str], list[dict[str, Any]]] | None = None,
          enrich_after: bool = True, who: str | None = None,
          listener_factory: Callable[[InvestigationConfig], Any] | None = None) -> dict[str, Any]:
    """Collect every `every_s` seconds (>= 5 min) until `for_s` has passed or `stop` is set. Returns
    {"cycles", "reason": "finished" | "stopped", "new_posts"}."""
    if every_s < MIN_EVERY_S:
        raise ValueError(f"--every must be at least 5 minutes (got {every_s} s)")
    inv = runner.get(ref)
    cfg = runner.config_of(inv)
    stop = stop or threading.Event()
    wait = wait or (lambda s: sliced_wait(stop, s))
    who = who or runner.actor()
    with session_scope() as s:
        audit(s, who, "investigation_watch_start",
              f"id={inv.id} every={every_s}s for={f'{for_s}s' if for_s else 'until stopped'} analyze={analyze}")
    listener = (listener_factory or (lambda c: start_listener(c, echo)))(cfg)
    base = factory or runner.default_factory

    def fac(logical: str, scfg: dict[str, Any]) -> Any:
        src = base(logical, scfg)
        if listener is not None and logical == "bluesky":
            src.listener = listener
        return src

    cycles, total, reason = 0, 0, "finished"
    end = clock() + for_s if for_s else None
    try:
        while True:
            cycles += 1
            t0 = clock()
            if listener is not None:  # terms added while watching (suggested terms) reach the open connection too
                listener.set_terms(runner.config_of(runner.get(inv.id)).terms)
            out = runner.collect(inv.id, factory=fac, resolver=resolver, enrich_after=enrich_after, stop=stop,
                                 who=who)
            total += out["new_posts"]
            stopped = out["stopped"] or stop.is_set()
            analysis = runner.analyze(inv.id, who=who) if analyze and not stopped else None
            next_t = t0 + every_s
            last = stopped or (end is not None and next_t > end)
            echo(_line(cycles, out, analysis, None if last else max(0.0, next_t - clock())))
            if stopped:
                reason = "stopped"
                break
            if last:
                break
            if wait(max(0.0, next_t - clock())):
                reason = "stopped"
                break
    except KeyboardInterrupt:  # a second Ctrl+C, or no handler installed
        reason = "stopped"
    finally:
        if listener is not None:
            listener.stop()
        with session_scope() as s:
            audit(s, who, "investigation_watch_stop", f"id={inv.id} cycles={cycles} reason={reason} new_posts={total}")
    return {"cycles": cycles, "reason": reason, "new_posts": total}
