"""Possible-coordination groups (Chain Coherence Score v0) with a small co-posting graph."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import streamlit as st  # noqa: E402
from sqlalchemy import desc, select  # noqa: E402

from socmint.core.db import session_scope  # noqa: E402
from socmint.core.models import CoordinationGroup  # noqa: E402
from socmint.dashboard.common import alerts_section, evidence, setup, show_table  # noqa: E402

setup("Coordination")
st.title("Possible coordination")
st.caption("Groups of near-identical public posts. 'Possible coordination' is a pattern, not proof of automation or "
           "intent. Accounts appear only as nodes in groups, never as individuals to watch.")
with session_scope() as s:
    run = s.scalar(select(CoordinationGroup.run_id).order_by(desc(CoordinationGroup.created_at)).limit(1))
    groups = (s.scalars(select(CoordinationGroup).where(CoordinationGroup.run_id == run)
                        .order_by(desc(CoordinationGroup.score))).all() if run else [])
    rows = [{"group": g.id, "score": g.score, "posts": g.n_items, "accounts": g.n_accounts, "channels": g.n_channels,
             "span (min)": g.time_span_minutes, "synchrony": g.synchrony, "text": g.sample_text[:90]} for g in groups]
    detail = {g.id: (g.edges, g.evidence) for g in groups}
min_score = st.slider("Minimum score", 0.0, 1.0, 0.3, 0.05)
rows = [r for r in rows if r["score"] >= min_score]
show_table(rows, "No groups at this score. Run `socmint analyze --module coordination`.")
if rows:
    gid = st.selectbox("Group", [r["group"] for r in rows])
    edges, ev = detail[gid]
    lines = [f'"{a}" -- "{b}";' for a, b in edges]
    st.graphviz_chart("graph G { layout=circo; node [shape=ellipse, fontsize=10];\n" + "\n".join(lines) + "\n}")
    evidence("Evidence (score components, weights, posts)", ev)
alerts_section("coordination")
