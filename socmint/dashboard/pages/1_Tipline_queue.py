"""Tipline queue: de-identified submissions; mark reviewed with an outcome (updates indicator status)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import streamlit as st  # noqa: E402
from sqlalchemy import desc, select  # noqa: E402

from socmint.core.db import session_scope  # noqa: E402
from socmint.core.models import Submission  # noqa: E402
from socmint.dashboard.common import evidence, setup, show_table  # noqa: E402
from socmint.tipline.pipeline import REVIEW_OUTCOMES, review_submission  # noqa: E402

setup("Tipline queue")
st.title("Tipline queue")
st.caption("Submissions are de-identified: pseudonymous sender code only, PII redacted from text. "
           "Never contact or identify senders.")
status = st.radio("Show", ["queued", "auto", "reviewed", "all"], horizontal=True)
with session_scope() as s:
    q = select(Submission).order_by(desc(Submission.received_at)).limit(500)
    if status != "all":
        q = q.where(Submission.status == status)
    subs = s.scalars(q).all()
    rows = [{"id": x.id, "received": x.received_at, "channel": x.channel, "type": x.type, "lang": x.lang,
             "verdict": x.verdict, "score": x.score, "status": x.status, "outcome": x.review_outcome,
             "text (redacted)": (x.extracted_text or "")[:120], "synthetic": x.synthetic} for x in subs]
    details = {x.id: {"reasons": x.reasons, "pending_indicators": x.pending_indicators, "media_hash": x.media_hash,
                      "sender (pseudonym)": (x.sender_hash or "")[:10] + "…"} for x in subs}
show_table(rows, "No submissions with this status.")

if rows:
    st.subheader("Review")
    sid = st.selectbox("Submission", [r["id"] for r in rows])
    evidence(f"Evidence for #{sid}", details[sid], expanded=True)
    with st.form("review"):
        outcome = st.selectbox("Outcome", list(REVIEW_OUTCOMES),
                               help="confirmed_scam -> its indicators become 'confirmed'; benign -> 'benign'.")
        if st.form_submit_button("Mark reviewed"):
            res = review_submission(int(sid), outcome, actor="dashboard")
            st.success(f"Reviewed #{sid}: {outcome}; indicators updated: {res['indicators_updated']}")
