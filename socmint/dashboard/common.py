"""Shared dashboard helpers: DB access, audit logging of views, evidence rendering."""

from __future__ import annotations

import os
from typing import Any

import pandas as pd
import streamlit as st

from socmint.core.db import audit, init_db, session_scope
from socmint.core.settings import get_settings


def setup(page: str) -> None:
    """Page config + header + audit entry. Call first on every page."""
    st.set_page_config(page_title=f"SOCMINT-India · {page}", layout="wide")
    init_db()
    url = get_settings().db_url
    demo = "demo.db" in url
    st.sidebar.markdown("**SOCMINT-India v0**  \nanalyst dashboard (local)")
    st.sidebar.caption(f"DB: `{url.rsplit('/', 1)[-1]}`" + ("  \n🧪 synthetic demo data" if demo else ""))
    st.sidebar.caption("Labels: indicators are *suspected/reported*; coordination is *possible coordination*. "
                       "Market alerts are risk indicators, never advice.")
    with session_scope() as s:
        audit(s, os.environ.get("USERNAME") or os.environ.get("USER") or "analyst", "dashboard_view", page)


def df(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def show_table(rows: list[dict[str, Any]], empty: str = "Nothing here yet.") -> None:
    if rows:
        st.dataframe(df(rows), use_container_width=True, hide_index=True)
    else:
        st.info(empty)


def evidence(title: str, payload: Any, expanded: bool = False) -> None:
    with st.expander(title, expanded=expanded):
        st.json(payload, expanded=True)


def alerts_section(module: str, limit: int = 200) -> None:
    from sqlalchemy import desc, select

    from socmint.core.models import Alert

    with session_scope() as s:
        rows = s.scalars(select(Alert).where(Alert.module == module).order_by(desc(Alert.created_at)).limit(limit)).all()
        data = [{"id": a.id, "severity": a.severity, "title": a.title, "created": a.created_at, "status": a.status,
                 "synthetic": a.synthetic, "investigation": (a.evidence or {}).get("investigation_id"),
                 "_evidence": a.evidence} for a in rows]
    st.subheader(f"Alerts ({len(data)})")
    if any(d["investigation"] for d in data):
        st.caption("Alerts with an `investigation` number come from that investigation's scoped analysis.")
    sev = st.multiselect("Severity", ["high", "medium", "low"], default=["high", "medium", "low"], key=f"sev_{module}")
    data = [d for d in data if d["severity"] in sev]
    show_table([{k: v for k, v in d.items() if not k.startswith("_")} for d in data], "No alerts.")
    for d in data[:30]:
        evidence(f"#{d['id']} [{d['severity']}] {d['title']}", d["_evidence"])
