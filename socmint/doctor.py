"""`socmint doctor`: a read-only check to run before a demo.

Prints PASS / WARN / FAIL with a one-line fix per line. It never prints a secret (keys are reported as `set` or
`missing`), never opens the main database (so it cannot migrate it) and sends at most one polite request per
source host: the robots.txt fetch the collectors would make anyway (cached for the rest of the process).
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import socket
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

from socmint.core import cooldown
from socmint.core.http import PoliteClient, get_client, is_offline
from socmint.core.models import utcnow
from socmint.core.settings import PROJECT_ROOT, get_settings, threshold

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
OK_FIX = "—"


@dataclass
class Check:
    name: str
    level: str
    detail: str
    fix: str = OK_FIX


# (.env variables, logical source or None, how events use it, what it enables, where to get it)
KEYS: list[tuple[tuple[str, ...], str | None, str, str]] = [
    (("YOUTUBE_API_KEY",), "youtube", "event",
     "youtube joins every new event automatically (search.list, quota-capped)"),
    (("GOOGLE_FACTCHECK_API_KEY",), "google_factcheck", "event",
     "google_factcheck joins every new event automatically (claims matched to posts)"),
    (("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"), "reddit", "channels",
     "reddit, only for events that list subreddits (`channels: reddit:`)"),
    (("TELEGRAM_API_ID", "TELEGRAM_API_HASH"), "telegram", "channels",
     "telegram, only for events that list public channels (`channels: telegram:`)"),
    (("BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD"), "bluesky", "event",
     "bluesky searches posts (logged in, a dedicated account + app password) instead of a 60 s live listen"),
    (("TELEGRAM_BOT_TOKEN",), None, "other", "the Telegram tipline bot (not used by events)"),
    (("GOOGLE_SAFE_BROWSING_KEY",), None, "other", "Safe Browsing URL checks in the tipline (not used by events)"),
    (("URLHAUS_AUTH_KEY",), None, "other", "the URLhaus threat feed in `socmint ingest` (not used by events)"),
]
PORTS = [(8000, "API (socmint api)", "start the API with `socmint api --port 8010`"),
         (8501, "analyst dashboard (socmint dashboard)", "stop that process or run `socmint dashboard --port 8511`"),
         (8502, "showcase (socmint showcase)", "stop that process or run `socmint showcase --port 8512`")]


def _is_set(var: str) -> bool:
    return bool(getattr(get_settings(), var.lower(), ""))


# ------------------------------------------------------------------------------------------- checks
def env_checks() -> list[Check]:
    out = []
    env = PROJECT_ROOT / ".env"
    out.append(Check(".env file", PASS, "found") if env.exists() else
               Check(".env file", FAIL, "not found", "run `socmint init-db` (creates .env from .env.example)"))
    out.append(Check("CONTACT_EMAIL", PASS, "set (sent in the User-Agent to every source)") if _is_set("CONTACT_EMAIL")
               else Check("CONTACT_EMAIL", WARN, "missing: requests go out as 'contact-not-configured'",
                          "add CONTACT_EMAIL=<your address> to .env"))
    for names, _logical, use, what in KEYS:
        label = " + ".join(names)
        have = [n for n in names if _is_set(n)]
        if len(have) == len(names):
            out.append(Check(label, PASS, f"set; {what}"))
        elif use == "other":
            out.append(Check(label, PASS, f"missing (optional; enables {what})"))
        else:
            out.append(Check(label, WARN, f"missing; with it, {what}",
                             f"add {' and '.join(n + '=' for n in names if n not in have)} to .env (see README)"))
    return out


def event_sources_check() -> Check:
    from socmint.investigate.config import KEY_GATED, default_event_sources
    from socmint.investigate.runner import resolve_sources

    used = default_event_sources()
    parts = []
    for logical in used:
        n = len(resolve_sources(logical))
        if logical == "bluesky":
            from socmint.ingest import bluesky_auth

            parts.append("bluesky (search, logged in)" if bluesky_auth.configured()
                         else "bluesky (live listen 60 s; public search is closed)")
        elif logical == "news_rss":
            parts.append(f"news_rss ({n} feeds)")
        else:
            parts.append(logical)
    unused = [f"{lg} (no_key: set {' and '.join(k.upper() for k in keys)})" for lg, keys in KEY_GATED.items()
              if lg not in used and lg not in ("reddit", "telegram")]
    unused += ["reddit / telegram (only when an event lists channels)"]
    return Check("event sources", PASS, "a new event uses " + ", ".join(parts) + "; not used: " + "; ".join(unused))


def _event_urls() -> list[str]:
    """One URL per host that a new event's sources would contact."""
    from socmint.ingest.base import source_class
    from socmint.investigate.config import default_event_sources
    from socmint.investigate.runner import resolve_sources

    urls: list[str] = []
    for logical in default_event_sources():
        for cfg in resolve_sources(logical):
            cls = source_class(cfg.get("type", ""))
            if cls is None:
                continue
            try:
                urls += cls(cfg).hosts()
            except Exception:  # noqa: BLE001  (a misconfigured source is reported by collect, not here)
                continue
            if cfg.get("jetstream_url"):
                urls.append(str(cfg["jetstream_url"]).replace("wss://", "https://", 1))
    return urls


def reachability(urls: list[str], client: PoliteClient | None = None) -> list[Check]:
    """One robots.txt request per host (in parallel; per-host rate limits hold). Hosts in a cooldown get none."""
    client = client or get_client()
    hosts: dict[str, str] = {}
    for u in urls:
        p = urlparse(u)
        hosts.setdefault(p.netloc, f"{p.scheme}://{p.netloc}/")

    def one(host: str, url: str) -> Check:
        end = cooldown.until(host)
        if end:
            return Check(f"reach {host}", WARN, f"cooldown after HTTP 429: next try after {end:%H:%M} UTC "
                                                "(no request sent)", "wait for the cooldown; other sources still run")
        st = client.probe(url)
        if st == "HTTP 429":
            return Check(f"reach {host}", WARN, "HTTP 429: the host is limiting this IP",
                         "try again later; collection will report it as throttled")
        if st.startswith("HTTP ") and int(st[5:]) < 500:
            return Check(f"reach {host}", PASS, f"reachable (robots.txt {st})")
        return Check(f"reach {host}", WARN, f"{st}", "check the network / proxy; collection reports this source "
                                                    "as error and the others still run")

    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda kv: one(*kv), hosts.items()))
    tried = [r for r in rows if "no request sent" not in r.detail]
    if tried and not any(r.level == PASS for r in tried):
        rows.append(Check("network", FAIL, "no source host answered", "check the internet connection or proxy"))
    return rows


def model_check() -> Check:
    s = get_settings()
    if s.embedding_backend.lower() == "tfidf":
        return Check("e5 model", PASS, "not needed (EMBEDDING_BACKEND=tfidf)")
    if importlib.util.find_spec("sentence_transformers") is None:  # find, not import (torch is slow to load)
        return Check("e5 model", WARN, "sentence-transformers not installed: TF-IDF fallback (same-language only)",
                     "python -m uv sync --extra ml")
    from socmint.core.hf import _snapshot_download

    try:
        _snapshot_download(s.embedding_model, local_files_only=True)  # local only: no network request
    except Exception:  # noqa: BLE001
        return Check("e5 model", WARN, f"{s.embedding_model} not cached: the first run downloads ~470 MB",
                     "run `socmint demo` once on a good connection before the meeting")
    return Check("e5 model", PASS, f"{s.embedding_model} cached")


def tesseract_check() -> Check:
    from socmint.understand.ocr import ocr_status

    ok, langs, why = ocr_status()
    if ok:
        return Check("Tesseract (OCR)", PASS, f"found ({langs})" + (f"; {why}" if why else ""))
    return Check("Tesseract (OCR)", WARN, f"OCR disabled: {why}", "install Tesseract with hin + mar (see README); "
                                                                 "image text is skipped until then")


def _process_on(port: int) -> str:
    """Name of the process listening on `port` (Windows: netstat + tasklist), or a neutral fallback."""
    try:
        if os.name == "nt":
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=10).stdout
            pid = next((ln.split()[-1] for ln in out.splitlines()
                        if re.search(rf"[:.]{port}\s", ln) and "LISTENING" in ln), None)
            if pid:
                row = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                                     text=True, timeout=10).stdout.strip()
                name = row.split('","')[0].strip('"') if row.startswith('"') else ""
                return f"{name} (PID {pid})" if name else f"PID {pid}"
    except (OSError, subprocess.SubprocessError, StopIteration):
        pass
    return "another process"


def port_check(port: int, purpose: str, fix: str | None = None) -> Check:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            pass
    except OSError:
        return Check(f"port {port}", PASS, f"free for the {purpose}")
    return Check(f"port {port}", WARN, f"in use by {_process_on(port)}",
                 fix or f"stop that process or choose another --port for the {purpose}")


def demo_check(now: datetime | None = None) -> Check:
    from socmint import demo, scenario

    path = demo.demo_db_path()
    if not path.exists():
        return Check("demo data", WARN, "data/demo.db does not exist", "build it: `socmint showcase --rebuild`")
    built = demo_built_at(path)
    if built is None:
        return Check("demo data", WARN, "data/demo.db has no showcase investigations",
                     "rebuild it: `socmint showcase --rebuild`")
    age_h = ((now or utcnow()) - built).total_seconds() / 3600
    max_h = float(threshold("showcase.demo_max_age_hours", 12))
    detail = f"built {built:%d %b %Y %H:%M} UTC ({age_h:.0f} h ago; '{scenario.EVENT_NAME}' and 2 more)"
    if age_h > max_h:
        return Check("demo data", WARN, detail + "; its 'last 24 h' numbers are stale: rebuild before a meeting",
                     "`socmint showcase --rebuild` (about a minute)")
    return Check("demo data", PASS, detail)


def demo_built_at(path) -> datetime | None:  # noqa: ANN001
    """When data/demo.db was built: the creation time of its showcase investigations (read-only open)."""
    from socmint import scenario

    try:
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT min(created_at) FROM investigations WHERE name IN (?, ?, ?)",
                              (scenario.EVENT_NAME, scenario.SUBJECT_NAME, scenario.GROUP_NAME)).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    if not row or not row[0]:
        return None
    try:
        return datetime.fromisoformat(str(row[0]))
    except ValueError:
        return None


def uv_check() -> Check:
    if shutil.which("uv"):
        return Check("uv", PASS, "on PATH")
    return Check("uv", WARN, "not on PATH, so `uv run socmint …` fails",
                 "use `python -m uv run socmint …` or `.\\.venv\\Scripts\\socmint.exe …`")


def run_checks(network: bool = True) -> list[Check]:
    checks = env_checks()
    checks.append(event_sources_check())
    if network and not is_offline():
        checks += reachability(_event_urls())
    else:
        checks.append(Check("reachability", WARN, "skipped (offline)", "run `socmint doctor` without --offline "
                                                                         "and with SOCMINT_OFFLINE unset"))
    for host, c in cooldown.active().items():
        if not any(x.name == f"reach {host}" for x in checks):
            checks.append(Check(f"cooldown {host}", WARN, f"next try after {c['until']:%H:%M} UTC ({c['reason']})",
                                "wait; the source is skipped as throttled until then"))
    checks.append(model_check())
    checks.append(tesseract_check())
    checks += [port_check(p, purpose, fix) for p, purpose, fix in PORTS]
    checks.append(demo_check())
    checks.append(uv_check())
    return checks


def render(checks: list[Check]) -> str:
    width = max(len(c.name) for c in checks) + 2
    lines = []
    for c in checks:
        lines.append(f"{c.level:<5} {c.name:<{width}} {c.detail}")
        if c.fix != OK_FIX:
            lines.append(f"{'':<5} {'':<{width}} fix: {c.fix}")
    n = {lv: sum(c.level == lv for c in checks) for lv in (PASS, WARN, FAIL)}
    lines.append(f"\n{n[PASS]} PASS · {n[WARN]} WARN · {n[FAIL]} FAIL")
    return "\n".join(lines)
