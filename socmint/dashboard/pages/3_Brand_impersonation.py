"""Lookalike domains and impersonation alerts."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import streamlit as st  # noqa: E402
from sqlalchemy import desc, select  # noqa: E402

from socmint.core.db import session_scope  # noqa: E402
from socmint.core.models import Brand, LookalikeDomain  # noqa: E402
from socmint.dashboard.common import alerts_section, setup, show_table  # noqa: E402

setup("Brand impersonation")
st.title("Brand impersonation")
with session_scope() as s:
    brands = {b.key: b.name for b in s.scalars(select(Brand)).all()}
sel = st.multiselect("Brands", list(brands), default=list(brands), format_func=lambda k: brands[k])
only_reg = st.checkbox("Registered only", value=True)
with session_scope() as s:
    q = select(LookalikeDomain).where(LookalikeDomain.brand.in_(sel))
    if only_reg:
        q = q.where(LookalikeDomain.registered)
    rows = [{"brand": d.brand, "domain": d.domain, "method": d.method, "registered": d.registered,
             "A records": ", ".join(d.dns_a or []), "first_seen": d.first_seen, "last_checked": d.last_checked,
             "synthetic": d.synthetic} for d in s.scalars(q.order_by(desc(LookalikeDomain.first_seen)).limit(3000)).all()]
st.caption("A registered lookalike is not proof of abuse (many are parked or defensive registrations).")
st.subheader(f"Lookalike domains ({len(rows)})")
show_table(rows, "No lookalikes recorded. Run `socmint analyze --module impersonation`.")
alerts_section("impersonation")
