"""Overview page: counts and source health. Other pages live in ./pages."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # allow `streamlit run socmint/dashboard/app.py`

import streamlit as st  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from socmint.core.db import session_scope  # noqa: E402
from socmint.core.models import (  # noqa: E402
    Alert,
    FactCheck,
    Indicator,
    Item,
    LookalikeDomain,
    SourceRow,
    Submission,
)
from socmint.dashboard.common import setup, show_table  # noqa: E402

setup("Overview")
st.title("Overview")

with session_scope() as s:
    count = lambda m, *w: s.scalar(select(func.count()).select_from(m).where(*w))  # noqa: E731
    metrics = {
        "Items": count(Item), "Fact-checks": count(FactCheck), "Submissions": count(Submission),
        "Queued for review": count(Submission, Submission.status == "queued"),
        "Indicators": count(Indicator), "Lookalikes (registered)": count(LookalikeDomain, LookalikeDomain.registered),
        "Open alerts": count(Alert, Alert.status == "open"),
    }
    by_module = s.execute(select(Alert.module, Alert.severity, func.count()).group_by(Alert.module, Alert.severity)).all()
    verdicts = s.execute(select(Submission.verdict, func.count()).group_by(Submission.verdict)).all()
    sources = [{"source": r.name, "type": r.type, "enabled": r.enabled, "status": r.status, "last_run": r.last_run,
                "last_count": r.last_count} for r in s.scalars(select(SourceRow).order_by(SourceRow.name)).all()]

cols = st.columns(len(metrics))
for c, (k, v) in zip(cols, metrics.items(), strict=True):
    c.metric(k, v)

left, right = st.columns(2)
with left:
    st.subheader("Alerts by module / severity")
    show_table([{"module": m, "severity": sv, "count": n} for m, sv, n in by_module], "No alerts yet.")
with right:
    st.subheader("Tipline verdicts")
    show_table([{"verdict": v, "count": n} for v, n in verdicts], "No submissions yet.")

st.subheader("Source health")
ok = sum(1 for r in sources if r["status"] == "ok")
st.caption(f"{ok}/{len(sources)} sources OK - details on the Sources page.")
show_table(sources)
