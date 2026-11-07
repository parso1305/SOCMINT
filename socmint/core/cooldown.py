"""Per-host cooldowns after a source throttles us (HTTP 429), saved in data/cache/cooldowns.json.

While a host is cooling down, sources skip it with status `throttled` and send no request at all, so a
throttled IP is not hit again and again by repeated "Collect now" clicks or watch cycles.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from socmint.core.models import utcnow
from socmint.core.settings import get_settings

_lock = threading.Lock()


def _path() -> Path:
    return get_settings().cache_dir / "cooldowns.json"


def _load() -> dict[str, Any]:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def start(host: str, minutes: float, reason: str, now: datetime | None = None) -> datetime:
    """Start (or extend) a cooldown for `host`; returns when it ends (naive UTC)."""
    until = (now or utcnow()) + timedelta(minutes=minutes)
    with _lock:
        data = _load()
        data[host] = {"until": until.isoformat(timespec="seconds"), "reason": reason[:200]}
        _path().parent.mkdir(parents=True, exist_ok=True)
        _path().write_text(json.dumps(data, indent=1), encoding="utf-8")
    return until


def until(host: str, now: datetime | None = None) -> datetime | None:
    """End of `host`'s cooldown if one is active, else None."""
    entry = _load().get(host)
    if not entry:
        return None
    try:
        end = datetime.fromisoformat(entry["until"])
    except (KeyError, ValueError):
        return None
    return end if end > (now or utcnow()) else None


def active(now: datetime | None = None) -> dict[str, dict[str, Any]]:
    """Every active cooldown: {host: {"until": datetime, "reason": str}} (for `socmint doctor`)."""
    out = {}
    for host, entry in _load().items():
        end = until(host, now)
        if end:
            out[host] = {"until": end, "reason": entry.get("reason", "")}
    return out
