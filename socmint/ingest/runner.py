"""Run sources with per-source isolation: one failing source never stops the others."""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select

from socmint.core.db import session_scope
from socmint.core.http import OfflineError, RobotsDisallowed, Throttled
from socmint.core.logging import get_logger
from socmint.core.models import SourceRow, utcnow
from socmint.ingest.base import source_class
from socmint.ingest.registry import source_configs, sync_sources
from socmint.ingest.store import store_result

log = get_logger(__name__)


def run_sources(name: str = "all") -> list[dict[str, Any]]:
    """Run one source by name, or all configured sources. Returns a per-source summary."""
    sync_sources()
    configs = source_configs()
    if name != "all":
        configs = [c for c in configs if c["name"] == name]
        if not configs:
            raise KeyError(f"no source named {name!r} in sources.yaml")
    return [run_one(cfg, forced=(name != "all")) for cfg in configs]


def _update_row(name: str, **fields: Any) -> None:
    with session_scope() as s:
        row = s.scalar(select(SourceRow).where(SourceRow.name == name))
        if row:
            for k, v in fields.items():
                setattr(row, k, v)


def run_one(cfg: dict[str, Any], forced: bool = False) -> dict[str, Any]:
    name = cfg["name"]
    summary: dict[str, Any] = {"source": name, "type": cfg.get("type")}
    if not cfg.get("enabled", True):
        reason = cfg.get("disabled_reason", "disabled in sources.yaml")
        summary.update(status="disabled", detail=reason)
        _update_row(name, status="disabled", last_error=reason)
        if forced:
            log.warning("source_disabled", extra={"source": name, "reason": reason})
        return summary
    cls = source_class(cfg.get("type", ""))
    if cls is None:
        summary.update(status="error", detail=f"unknown source type {cfg.get('type')!r}")
        _update_row(name, status="error", last_error=summary["detail"])
        return summary
    try:
        src = cls(cfg)
        ok, why = src.availability()
    except Exception as e:  # noqa: BLE001
        ok, why = False, f"init failed: {e}"
    if not ok:
        log.warning("source_unavailable", extra={"source": name, "reason": why})
        summary.update(status="no_key", detail=why)
        _update_row(name, status="no_key", last_error=why)
        return summary

    with session_scope() as s:
        row = s.scalar(select(SourceRow).where(SourceRow.name == name))
        since = row.last_run if row and row.status == "ok" else None

    t0 = time.monotonic()
    try:
        res = src.fetch(since)
        with session_scope() as s:
            counts = store_result(s, res)
        summary.update(status="ok", fetched=res.count(), new=counts, seconds=round(time.monotonic() - t0, 1))
        _update_row(name, status="ok", last_run=utcnow(), last_error=None, last_count=res.count())
        log.info("source_ok", extra={"source": name, "fetched": res.count(), "new": counts})
    except RobotsDisallowed as e:
        summary.update(status="error", detail=str(e))
        _update_row(name, status="error", last_error=str(e), last_run=utcnow())
        log.warning("source_blocked_by_robots", extra={"source": name, "error": str(e)})
    except OfflineError as e:
        summary.update(status="offline", detail=str(e))
        _update_row(name, status="offline", last_error=str(e))
    except Throttled as e:
        summary.update(status="throttled", detail=str(e))
        _update_row(name, status="throttled", last_error=str(e))
        log.warning("source_throttled", extra={"source": name, "error": str(e)})
    except Exception as e:  # noqa: BLE001  (per-source isolation is the whole point)
        detail = f"{type(e).__name__}: {e}"[:1000]
        summary.update(status="error", detail=detail)
        _update_row(name, status="error", last_error=detail, last_run=utcnow())
        log.warning("source_failed", extra={"source": name, "error": detail})
    return summary
