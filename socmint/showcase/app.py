"""SOCMINT-India showcase: one page, three kinds of investigation (event, person/account, group).

Run with `socmint showcase [--demo]` (binds to 127.0.0.1). Every view of an investigation is written to audit_log.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from socmint.core.db import init_db
from socmint.investigate import report, runner
from socmint.investigate.config import LOGICAL_SOURCES
from socmint.showcase import data

st.set_page_config(page_title="SOCMINT-India showcase", layout="wide")
init_db()
DEMO = data.demo_mode()

# ------------------------------------------------------------------------------------------- sidebar
st.sidebar.title("SOCMINT-India")
st.sidebar.caption("Open-source monitoring of public posts. Analyst-directed; official APIs and public feeds only.")

with st.sidebar.expander("New investigation", expanded=False):
    with st.form("new_inv", clear_on_submit=False):
        kind_label = st.radio("Kind", list(data.KINDS), horizontal=True)
        name = st.text_input("Name")
        purpose = st.text_area("Purpose (required)", help="Why is this being monitored? Stored and audited.")
        targets = st.text_area("Keywords (event) or platform:handle lines (person/account, group)",
                               placeholder="flood, बाढ़, #PuneRains\n— or —\nbluesky:example.bsky.social\n"
                                           "mastodon:@user@mastodon.social")
        sources = st.multiselect("Sources", sorted(LOGICAL_SOURCES), default=data.default_sources(),
                                 help="Default: sources that need no API key. Key-gated sources fail cleanly "
                                      "without keys.")
        submitted = st.form_submit_button("Create and collect now")
    if submitted:
        try:
            cfg = data.build_config(kind_label, name, purpose, targets, sources)
        except Exception as e:  # noqa: BLE001  (validation message shown to the analyst)
            st.error(str(e).split("\n[")[0])
        else:
            with st.status(f"Collecting for {cfg.name}…", expanded=True) as status:
                def progress(src: str, run: dict) -> None:
                    status.write(f"{src}: **{run['status']}** — {run['fetched']} fetched, {run['new']} new")
                try:
                    iid, out = data.create_and_collect(cfg, progress=progress)
                    status.update(label=f"Collected {out['new_posts']} new posts", state="complete")
                    st.session_state["inv_id"] = iid
                except ValueError as e:
                    status.update(label=str(e), state="error")

invs = runner.list_all()
if not invs:
    st.title("SOCMINT-India showcase")
    st.info("No investigations yet. Create one in the sidebar, or run `socmint showcase --demo` for synthetic "
            "demo data.")
    st.stop()

labels = {i["id"]: f"#{i['id']} · {i['kind']} · {i['name']}" for i in invs}
ids = list(labels)
current = st.session_state.get("inv_id")
inv_id = st.sidebar.selectbox("Investigation", ids, index=ids.index(current) if current in ids else 0,
                              format_func=labels.get)
st.session_state["inv_id"] = inv_id
if st.session_state.get("_viewed") != inv_id:  # audit each investigation once per selection
    runner.record_view(inv_id, "showcase")
    st.session_state["_viewed"] = inv_id

d = data.load(inv_id)
s = d["summary"]
inv = s["investigation"]

# ------------------------------------------------------------------------------------------- header
if DEMO or inv["synthetic"]:
    st.warning("DEMO DATA (synthetic): fictional people, channels, domains and numbers. Nothing here is real.")
st.title(inv["name"])
st.caption(f"{inv['kind'].upper()} · purpose: {inv['purpose']}"
           + (f" · last collected {inv['last_collected_at']:%d %b %Y %H:%M} UTC" if inv["last_collected_at"] else ""))
h = data.headline(d)
c1, c2, c3, c4 = st.columns(4)
c1.metric("Posts", h["posts"])
c2.metric("Accounts", h["accounts"])
c3.metric("Flagged posts", h["flagged"], help="Posts with a category other than civic safety / other.")
c4.metric("Sources", h["sources"])
chips = data.source_status(d)
if chips:
    st.markdown(" ".join(f":{c['color']}-background[{c['source']}: {c['status']} ({c['new']} new)]" for c in chips))

ov, posts_tab, people, network, evidence = st.tabs(
    ["Overview", "Posts", "People & accounts", "Network", "Evidence & export"])

# ------------------------------------------------------------------------------------------- overview
with ov:
    left, right = st.columns([3, 2])
    with left:
        ts, bucket = data.posts_over_time(d)
        st.subheader(f"Posts per {bucket}, by platform")
        if ts.empty:
            st.info("No posts yet.")
        else:
            st.bar_chart(ts, x_label=f"time (UTC), one bar per {bucket}", y_label="posts")
    with right:
        st.subheader("Key findings")
        findings = data.key_findings(d)
        for f in findings:
            st.markdown(f"- {f}")
        if not findings:
            st.info("Nothing notable yet.")
    a, b = st.columns(2)
    with a:
        st.subheader("Categories")
        if s["by_category"]:
            st.bar_chart(data.counts_frame(s["by_category"], "category"), horizontal=True)
    with b:
        st.subheader("Languages")
        if s["by_language"]:
            st.bar_chart(data.counts_frame(s["by_language"], "language"), horizontal=True)

# ------------------------------------------------------------------------------------------- posts
with posts_tab:
    f1, f2, f3, f4 = st.columns(4)
    plats = f1.multiselect("Platform", sorted(s["by_platform"]))
    cats = f2.multiselect("Category", sorted(s["by_category"]))
    langs = f3.multiselect("Language", sorted(s["by_language"]))
    q = f4.text_input("Search text / author")
    table = data.posts_table(d["posts"], plats, cats, langs, q)
    st.caption(f"{len(table)} of {len(d['posts'])} posts")
    if table.empty:
        st.info("No posts match.")
    else:
        widths = data.POSTS_COLUMN_WIDTHS
        st.dataframe(table, hide_index=True, width="stretch", column_order=list(widths),
                     column_config={**{c: st.column_config.Column(c, width=w) for c, w in widths.items()},
                                    "time (UTC)": st.column_config.DatetimeColumn("time (UTC)", format="D MMM HH:mm",
                                                                                  width=widths["time (UTC)"]),
                                    "text": st.column_config.TextColumn("text", width=widths["text"],
                                                                        help="First 140 characters; full text below"),
                                    "link": st.column_config.LinkColumn("link", display_text="open",
                                                                        width=widths["link"])})
        pid = st.selectbox("Post detail", table["id"].tolist(),
                           format_func=lambda i: f"#{i} " + next(p["text"][:80] for p in d["posts"] if p["id"] == i))
        p = next(x for x in d["posts"] if x["id"] == pid)
        with st.container(border=True):
            st.markdown(f"**{p['author_display_name'] or p['author_handle'] or p['channel'] or p['source']}** · "
                        f"{p['platform']} · {p['published_at']:%d %b %Y %H:%M} UTC · lang `{p['lang']}`")
            st.write(p["text"])
            if p["url"]:
                st.markdown(f"[Original post]({p['url']})")
            for lab in p["labels"]:
                st.markdown(f"- **{lab['label']}**" + (f" / {lab['sublabel']}" if lab.get("sublabel") else "")
                            + f" · {lab['method']} · confidence {lab['confidence']:.2f}  \n"
                            + f"  Evidence: {data.label_evidence(lab)}")
            st.markdown("**Why it is in this investigation**")
            st.json(p["matched_by"], expanded=True)
            with st.expander("Entities, signals and engagement"):
                st.json({"entities": p["entities"], "signals": p["signals"], "engagement": p["engagement"],
                         "hashtags": p["hashtags"], "mentions": p["mentions"], "urls": p["urls"]}, expanded=False)

# ------------------------------------------------------------------------------------------- people
with people:
    focus = [a for a in d["accounts"] if a["role"] in ("subject", "member")]
    if focus:
        st.subheader("Subjects" if inv["kind"] == "subject" else "Group members")
        cols = st.columns(min(len(focus), 4))
        for i, a in enumerate(focus[:12]):
            with cols[i % len(cols)].container(border=True):
                st.markdown(f"**{a['display_name'] or a['handle']}**  \n`{a['platform']}` · {a['handle'] or ''}")
                if a["description"]:
                    st.caption(a["description"][:160])
                m1, m2 = st.columns(2)
                m1.metric("Followers", f"{a['followers']:,}" if a["followers"] is not None else "—")
                m2.metric("Posts here", a["posts_in_investigation"])
                st.caption(f"added by {a['added_by']}"
                           + (f" · account created {a['created_at']:%b %Y}" if a["created_at"] else ""))
        act = data.subject_activity(d)
        if not act.empty:
            st.subheader("Activity per day")
            st.bar_chart(act)
    st.subheader("Top authors in this investigation")
    tops = [{"handle": a["handle"], "platform": a["platform"], "role": a["role"], "posts here": a["posts_in_investigation"],
             "followers": a["followers"], "first seen here": a["first_seen_here"], "last seen here": a["last_seen_here"]}
            for a in d["accounts"]]
    if tops:
        st.dataframe(pd.DataFrame(tops), hide_index=True, width="stretch")
    else:
        st.info("No accounts yet.")
    st.caption("Only public profile fields returned by each platform's official API are shown. No identity "
               "resolution across platforms.")

# ------------------------------------------------------------------------------------------- network
with network:
    kinds = st.multiselect("Edge kinds", ["co_posts", "forward", "reply", "mention", "shares_url", "uses_hashtag"],
                           default=["co_posts", "forward", "reply", "mention", "shares_url"])
    dot, n_nodes = data.network_dot(d, top=40, kinds=kinds)
    if n_nodes:
        st.graphviz_chart(dot, width="stretch")
        st.caption(f"Top {n_nodes} best-connected nodes. Orange: subjects / group members. Red lines: possible "
                   "coordination (near-identical posts).")
    else:
        st.info("No edges of these kinds yet.")
    st.subheader("Possible coordination groups")
    groups = s["coordination_groups"]
    if not groups:
        st.info("None detected (run analysis after collecting).")
    else:
        st.dataframe(pd.DataFrame([{"group": g["id"], "score": round(g["score"], 2), "accounts": g["accounts"],
                                    "posts": g["posts"], "minutes": g["time_span_minutes"],
                                    "text": (g["sample_text"] or "")[:120]} for g in groups]),
                     hide_index=True, width="stretch")
        if inv["kind"] == "event":
            with st.form("promote"):
                gid = st.selectbox("Group", [g["id"] for g in groups],
                                   format_func=lambda i: f"#{i} (score {next(g['score'] for g in groups if g['id'] == i):.2f})")
                gname = st.text_input("New group investigation name", value=f"Group from {inv['name']}")
                gpurpose = st.text_input("Purpose (required)")
                if st.form_submit_button("Promote to Group"):
                    try:
                        st.session_state["inv_id"] = runner.promote_group(gid, gname, gpurpose)
                        st.rerun()
                    except Exception as e:  # noqa: BLE001
                        st.error(str(e).split("\n[")[0])

# ------------------------------------------------------------------------------------------- evidence
with evidence:
    st.subheader("Suspected indicators")
    if s["indicators"]:
        st.dataframe(pd.DataFrame([{k: v for k, v in i.items() if k != "sources"} for i in s["indicators"]]),
                     hide_index=True, width="stretch")
    else:
        st.info("No indicators in these posts.")
    st.subheader("Fact-check matches")
    if s["factcheck_matches"]:
        st.dataframe(pd.DataFrame([{"fact-check": f["title"], "publisher": f.get("publisher"), "rating": f.get("rating"),
                                    "posts": f["posts"], "best score": round(f["score"], 2), "url": f.get("url")}
                                   for f in s["factcheck_matches"]]),
                     hide_index=True, width="stretch",
                     column_config={"url": st.column_config.LinkColumn("url", display_text="open")})
    else:
        st.info("No fact-check matches.")
    if s["narratives"]:
        st.subheader("Narratives")
        st.dataframe(pd.DataFrame([{"keywords": ", ".join(n["keywords"][:6]), "posts": n["size"],
                                    "last 24 h": n["count_24h"], "growth": n["growth_rate"],
                                    "fact-checked": bool(n["matched_factcheck_ids"])} for n in s["narratives"]]),
                     hide_index=True, width="stretch")
    for a in s["market_alerts"]:
        st.subheader("Market-integrity risk")
        st.error(f"{a['title']} — risk score {a['risk_score']:.0f}")
        st.write(a["summary"])
        st.caption(a["disclaimer"])
        st.json(a["risk_factors"], expanded=False)
    st.subheader("Collection log")
    runs = s["collection"]["runs"]
    if runs:
        st.dataframe(pd.DataFrame(runs), hide_index=True, width="stretch")
    st.subheader("Export")
    st.caption("Writes posts, accounts, edges, indicators, summary and a SHA-256 manifest. Every export is audited.")
    e1, e2 = st.columns(2)
    for col, fmt in ((e1, "json"), (e2, "csv")):
        key = f"export_{inv_id}_{fmt}"
        if col.button(f"Prepare {fmt.upper()} export", key=f"btn_{key}"):
            folder = report.export(inv_id, fmt)
            st.session_state[key] = (folder.name + ".zip", report.zip_bundle(folder))
        if key in st.session_state:
            fname, blob = st.session_state[key]
            col.download_button(f"Download {fname}", blob, file_name=fname, mime="application/zip", key=f"dl_{key}")
    with st.expander("Investigation config"):
        st.json(inv["config"], expanded=False)
