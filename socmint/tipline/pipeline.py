"""Tipline pipeline shared by the Telegram bot, API and CLI."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select

from socmint.core.db import session_scope
from socmint.core.logging import get_logger
from socmint.core.models import Submission, utcnow
from socmint.core.privacy import purge_media
from socmint.core.settings import get_settings

log = get_logger(__name__)


def apply_retention() -> dict[str, Any]:
    """Delete raw media older than RETENTION_DAYS and null out the stored paths."""
    s = get_settings()
    deleted = purge_media(s.media_dir, s.retention_days)
    cutoff = utcnow() - timedelta(days=s.retention_days)
    cleared = 0
    with session_scope() as db:
        rows = db.scalars(
            select(Submission).where(Submission.media_path.is_not(None), Submission.received_at < cutoff)
        ).all()
        for r in rows:
            r.media_path = None
            cleared += 1
    log.info("retention_applied", extra={"files_deleted": len(deleted), "paths_cleared": cleared})
    return {"files_deleted": len(deleted), "paths_cleared": cleared, "retention_days": s.retention_days}
