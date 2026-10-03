"""Indicators (suspected / reported / confirmed / benign) and scam-verdict submissions."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import streamlit as st  # noqa: E402
from sqlalchemy import desc, select  # noqa: E402

from socmint.core.db import session_scope  # noqa: E402
from socmint.core.models import Indicator, Submission  # noqa: E402
from socmint.dashboard.common import setup, show_table  # noqa: E402

setup("Scams & indicators")
st.title("Scams & indicators")
st.caption("Indicators are *reported* (third-party feed) or *suspected* (tipline / public posts) until an analyst "
           "confirms them. They carry no link to any sender.")
c1, c2, c3 = st.columns(3)
all_types = ["url", "domain", "phone", "upi", "telegram_handle"]
types = c1.multiselect("Type", all_types, default=all_types)
statuses = c2.multiselect("Status", ["suspected", "reported", "confirmed", "benign"], default=["suspected", "confirmed"])
needle = c3.text_input("Search value")
with session_scope() as s:
    q = select(Indicator).where(Indicator.type.in_(types), Indicator.status.in_(statuses))
    if needle:
        q = q.where(Indicator.value.contains(needle))
    rows = [{"type": i.type, "value": i.value, "status": i.status, "seen": i.seen_count, "first_seen": i.first_seen,
             "last_seen": i.last_seen, "brand": i.linked_brand, "sources": ", ".join(i.sources or []),
             "synthetic": i.synthetic} for i in s.scalars(q.order_by(desc(Indicator.last_seen)).limit(2000)).all()]
    scams = s.scalars(select(Submission).where(Submission.verdict.in_(["likely_scam", "suspicious"]))
                      .order_by(desc(Submission.received_at)).limit(300)).all()
    srows = [{"id": x.id, "received": x.received_at, "verdict": x.verdict, "score": x.score, "lang": x.lang,
              "rules fired": ", ".join(r["code"].split(":")[-1] for r in (x.reasons or {}).get("reasons", [])
                                       if r.get("weight")),
              "text (redacted)": (x.extracted_text or "")[:100]} for x in scams]
st.subheader(f"Indicators ({len(rows)})")
show_table(rows, "No indicators match the filters.")
st.subheader(f"Scam / suspicious tipline submissions ({len(srows)})")
show_table(srows, "None yet.")
