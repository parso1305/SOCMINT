"""Selecting analysis runs. Global views (dashboard, API) use the global run; scoped runs belong to investigations."""

from __future__ import annotations

from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session


def latest_global_run(s: Session, model: Any) -> str | None:
    """`run_id` of the newest global (not investigation-scoped) run of `model` (Narrative or CoordinationGroup)."""
    return s.scalar(select(model.run_id).where(model.investigation_id.is_(None))
                    .order_by(desc(model.created_at)).limit(1))
