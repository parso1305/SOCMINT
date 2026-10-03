"""Market-manipulation RISK view: mentions vs volume. Evidence only - never investment advice."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
from sqlalchemy import desc, func, select  # noqa: E402

from socmint.core.db import session_scope  # noqa: E402
from socmint.core.models import MarketDaily  # noqa: E402
from socmint.dashboard.common import alerts_section, setup, show_table  # noqa: E402

setup("Market risk")
st.title("Market manipulation risk")
st.warning("Risk indicators for analysts only. Not investment advice: no buy/sell/hold views and no price predictions.")
with session_scope() as s:
    latest = s.scalar(select(func.max(MarketDaily.date)))
    rows = (s.scalars(select(MarketDaily).where(MarketDaily.date == latest, MarketDaily.mentions > 0)
                      .order_by(desc(MarketDaily.mention_z))).all() if latest else [])
    table = [{"symbol": r.symbol, "date": r.date.date(), "mentions": r.mentions, "mention_z": r.mention_z,
              "volume": r.volume, "volume_z": r.volume_z, "price change %": r.price_change, "synthetic": r.synthetic}
             for r in rows]
    symbols = [r["symbol"] for r in table] or [x for (x,) in s.execute(select(MarketDaily.symbol).distinct().limit(50))]
st.subheader(f"Symbols mentioned on {latest.date() if latest else 'n/a'}")
show_table(table, "No symbol mentions on the latest market day.")
if symbols:
    sym = st.selectbox("Symbol", symbols)
    with session_scope() as s:
        hist = s.scalars(select(MarketDaily).where(MarketDaily.symbol == sym).order_by(MarketDaily.date)).all()
        frame = pd.DataFrame([{"date": h.date, "mentions": h.mentions, "volume": h.volume} for h in hist])
    if not frame.empty:
        frame = frame.set_index("date")
        c1, c2 = st.columns(2)
        c1.markdown("**Mentions per day**")
        c1.bar_chart(frame["mentions"])
        c2.markdown("**Traded volume per day**")
        c2.line_chart(frame["volume"])
alerts_section("market")
