"""Sources: enabled/disabled, last run, status, last error; plus module status (hate module: not enabled)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import streamlit as st  # noqa: E402
from sqlalchemy import select  # noqa: E402

from socmint.analyze import hate  # noqa: E402
from socmint.core.db import session_scope  # noqa: E402
from socmint.core.models import SourceRow  # noqa: E402
from socmint.dashboard.common import setup, show_table  # noqa: E402
from socmint.ingest.registry import source_configs  # noqa: E402

setup("Sources")
st.title("Sources")
cfg = {c["name"]: c for c in source_configs()}
with session_scope() as s:
    rows = [{"source": r.name, "type": r.type, "enabled": r.enabled, "status": r.status, "last_run": r.last_run,
             "last_count": r.last_count, "last error / reason": (r.last_error or "")[:200],
             "notes": cfg.get(r.name, {}).get("notes", "")} for r in s.scalars(select(SourceRow).order_by(SourceRow.name))]
st.caption("Statuses: ok · partial · throttled (the source is limiting this IP) · no_key (add the key named in the "
           "reason to .env) · offline · error (including robots.txt disallows) · disabled (config). "
           "See SOURCES.md for access terms.")
show_table(rows, "No sources registered. Run `socmint init-db`.")

st.subheader("Hate / communal module")
if hate.is_enabled():
    st.error("Enabled flag is set, but v0 ships no model; nothing is classified.")
else:
    st.info("Not enabled. Interface only in v0; analyst-only and never shown to citizens.")
