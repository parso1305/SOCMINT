"""Narrative clusters: keywords, growth, matched fact-checks, sample posts."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import streamlit as st  # noqa: E402
from sqlalchemy import desc, select  # noqa: E402

from socmint.analyze.runs import latest_global_run  # noqa: E402
from socmint.core.db import session_scope  # noqa: E402
from socmint.core.models import FactCheck, Item, Narrative  # noqa: E402
from socmint.dashboard.common import alerts_section, setup, show_table  # noqa: E402

setup("Narratives")
st.title("Narratives")
with session_scope() as s:
    run = latest_global_run(s, Narrative)
    narrs = (s.scalars(select(Narrative).where(Narrative.run_id == run).order_by(desc(Narrative.growth_rate))).all()
             if run else [])
    fcs = {f.id: f for f in s.scalars(select(FactCheck)).all()}
    rows = [{"cluster": n.cluster_id, "keywords": ", ".join(n.keywords[:8]), "size": n.size, "last 24h": n.count_24h,
             "growth x": n.growth_rate, "first_seen": n.first_seen, "last_seen": n.last_seen,
             "debunked by": "; ".join(fcs[i].publisher for i in n.matched_factcheck_ids if i in fcs)} for n in narrs]
    detail = {n.cluster_id: {
        "factchecks": [{"title": fcs[i].title, "url": fcs[i].url} for i in n.matched_factcheck_ids if i in fcs],
        "samples": list(s.scalars(select(Item.text).where(Item.id.in_(n.sample_item_ids))).all())[:6]} for n in narrs}
st.caption(f"Latest global run: `{run}` (investigation runs are in the showcase). Growth = posts in the last 24h ÷ daily average of the previous 72h.")
show_table(rows, "No narratives yet. Run `socmint analyze --module narratives`.")
if rows:
    c = st.selectbox("Cluster", [r["cluster"] for r in rows])
    for f in detail[c]["factchecks"]:
        st.markdown(f"- 🔎 [{f['title']}]({f['url']})")
    st.markdown("**Sample posts (most central first)**")
    for t in detail[c]["samples"]:
        st.markdown(f"> {t[:300]}")
alerts_section("narratives")
