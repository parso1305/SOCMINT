"""Data helpers for the showcase page: loading an investigation, key findings, the network graph, the create form.

Kept free of Streamlit so it can be tested directly. Wording stays neutral: "possible coordination", "suspected"
indicators, market "risk" alerts; nothing here produces buy/sell/hold language.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

import pandas as pd

from socmint.core.settings import get_settings
from socmint.investigate import report, runner
from socmint.investigate.config import NO_KEY_SOURCES, InvestigationConfig, parse_handles

KINDS = {"Event": "event", "Person / Account": "subject", "Group": "group"}
STATUS_COLOR = {"ok": "green", "offline": "gray", "missing_key": "orange", "unsupported": "gray",
                "blocked_by_robots": "orange", "error": "red"}
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def demo_mode() -> bool:
    return "demo.db" in get_settings().db_url


def load(inv_id: int) -> dict[str, Any]:
    """Summary + posts + accounts + edges for one investigation."""
    posts = report.posts(inv_id)
    return {"summary": report.summary(inv_id), "posts": posts, "accounts": report.accounts(inv_id, posts),
            "edges": report.edges(inv_id)}


# ------------------------------------------------------------------------------------------- headline
def headline(d: dict[str, Any]) -> dict[str, int]:
    s = d["summary"]
    flagged = sum(1 for p in d["posts"] if {lab["label"] for lab in p["labels"]} - {"other", "civic_safety"})
    return {"posts": s["counts"]["posts"], "accounts": s["counts"]["authors"], "flagged": flagged,
            "sources": len(s["collection"]["latest_by_source"]) or s["counts"]["sources"]}


def source_status(d: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"source": k, "status": r["status"], "fetched": r["fetched"], "new": r["new"], "detail": r["detail"] or "",
             "color": STATUS_COLOR.get(r["status"], "gray")}
            for k, r in sorted(d["summary"]["collection"]["latest_by_source"].items())]


def key_findings(d: dict[str, Any], n: int = 5) -> list[str]:
    """Up to `n` one-line findings, most actionable first."""
    s = d["summary"]
    out: list[str] = []
    for g in s["coordination_groups"][:1]:
        if g["score"] >= 0.6:
            out.append(f"Possible coordination: {g['accounts']} accounts posted near-identical text within "
                       f"{g['time_span_minutes']:.0f} minutes (score {g['score']:.2f}).")
    for f in s["factcheck_matches"][:1]:
        out.append(f"{f['posts']} posts match a published fact-check: “{f['title']}”"
                   + (f" (rating: {f['rating']})." if f.get("rating") else "."))
    if s["indicators"]:
        vals = ", ".join(f"{i['type']} {i['value']}" for i in s["indicators"][:3])
        out.append(f"Suspected scam indicators seen in posts: {vals}.")
    for a in s["market_alerts"][:1]:
        out.append(f"{a['title']} (risk score {a['risk_score']:.0f}). Risk indicator only.")
    grow = sorted((x for x in s["narratives"] if x["count_24h"]), key=lambda x: -(x["growth_rate"] or 0))
    for x in grow[:1]:
        out.append(f"Fastest-growing narrative: {', '.join(x['keywords'][:4])} "
                   f"({x['count_24h']} posts in the last 24 h, growth x{x['growth_rate']:.1f}).")
    cats = {k: v for k, v in s["by_category"].items() if k not in ("other",)}
    if cats:
        top = max(cats, key=cats.get)
        out.append(f"Most common category: {top.replace('_', ' ')} ({cats[top]} of {s['counts']['posts']} posts).")
    if s["by_hour"]:
        per: Counter = Counter()
        for r in s["by_hour"]:
            per[r["hour"]] += r["posts"]
        hour, cnt = per.most_common(1)[0]
        out.append(f"Peak activity: {cnt} posts in the hour from {hour:%d %b %H:00} UTC.")
    return out[:n]


# ------------------------------------------------------------------------------------------- frames
def posts_over_time(d: dict[str, Any]) -> pd.DataFrame:
    rows = d["summary"]["by_hour"]
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    span_h = (df["hour"].max() - df["hour"].min()).total_seconds() / 3600
    if span_h > 24 * 10:  # long windows read better per day
        df["hour"] = df["hour"].dt.floor("D")
    return df.pivot_table(index="hour", columns="platform", values="posts", aggfunc="sum", fill_value=0)


def counts_frame(counts: dict[str, int], label: str) -> pd.DataFrame:
    return pd.DataFrame({label: list(counts), "posts": list(counts.values())}).set_index(label)


def label_names(p: dict[str, Any]) -> list[str]:
    return [lab["label"] for lab in p["labels"]]


def posts_table(posts: list[dict[str, Any]], platforms: list[str] | None = None, categories: list[str] | None = None,
                langs: list[str] | None = None, query: str = "") -> pd.DataFrame:
    q = query.strip().lower()
    rows = []
    for p in posts:
        labs = label_names(p)
        if platforms and p["platform"] not in platforms:
            continue
        if categories and not set(labs) & set(categories):
            continue
        if langs and (p["lang"] or "unknown") not in langs:
            continue
        if q and q not in (p["text"] or "").lower() and q not in (p["author_handle"] or "").lower():
            continue
        rows.append({"id": p["id"], "time (UTC)": p["published_at"], "platform": p["platform"],
                     "author": p["author_handle"] or p["channel"] or "", "lang": p["lang"],
                     "categories": ", ".join(labs), "text": (p["text"] or "")[:160], "url": p["url"]})
    return pd.DataFrame(rows)


def subject_activity(d: dict[str, Any]) -> pd.DataFrame:
    """Posts per day per subject/member account (who posted when, for a Subject or Group)."""
    keys = {(a["platform"], a["platform_id"]): a["handle"] or a["platform_id"] for a in d["accounts"]
            if a["role"] in ("subject", "member")}
    rows = [{"day": p["published_at"].date(), "account": keys[(p["platform"], p["author_id"] or p["author_handle"])]}
            for p in d["posts"] if p["published_at"] and (p["platform"], p["author_id"] or p["author_handle"]) in keys]
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    return df.groupby(["day", "account"]).size().unstack(fill_value=0)


# ------------------------------------------------------------------------------------------- network
_SHAPES = {"account": "ellipse", "hashtag": "box", "url": "note"}
_EDGE_STYLE = {"forward": 'color="#1f77b4"', "reply": 'color="#2ca02c"', "mention": 'color="#9467bd", style=dashed',
               "shares_url": 'color="#8c564b"', "uses_hashtag": 'color="#bbbbbb"', "co_posts": 'color="#d62728", penwidth=2.5'}


def _node_kind(key: str) -> str:
    if key.startswith("#"):
        return "hashtag"
    if key.startswith(("http://", "https://")):
        return "url"
    return "account"


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def network_dot(d: dict[str, Any], top: int = 40, kinds: list[str] | None = None) -> tuple[str, int]:
    """Graphviz DOT of the `top` best-connected nodes (accounts, hashtags, URLs) and their edges."""
    names = {f"{p['platform']}:{p['author_id'] or p['author_handle']}": p["author_handle"] or p["author_id"]
             for p in d["posts"] if p["author_id"] or p["author_handle"]}
    pairs: Counter = Counter()
    for e in d["edges"]:
        if kinds and e["kind"] not in kinds:
            continue
        pairs[(e["kind"], e["src"], e["dst"])] += 1
    deg: Counter = Counter()
    for (_, a, b), n in pairs.items():
        deg[a] += n
        deg[b] += n
    keep = {k for k, _ in deg.most_common(top)}
    subjects = {f"{a['platform']}:{a['platform_id']}" for a in d["accounts"] if a["role"] in ("subject", "member")}
    lines = ["digraph G {", "graph [overlap=false, splines=true, layout=neato];",
             'node [fontsize=9, style=filled, fillcolor="#f2f2f2"];', "edge [arrowsize=0.5];"]
    for k in sorted(keep):
        kind = _node_kind(k)
        label = names.get(k) or (k.split(":", 1)[1] if kind == "account" and ":" in k else k)
        label = label if len(label) <= 32 else label[:30] + "…"
        fill = '"#ffd8a8"' if k in subjects else ('"#d0ebff"' if kind == "account" else '"#eeeeee"')
        lines.append(f"{_q(k)} [label={_q(label)}, shape={_SHAPES[kind]}, fillcolor={fill}];")
    for (kind, a, b), n in pairs.items():
        if a in keep and b in keep:
            arrow = ", dir=none" if kind == "co_posts" else ""
            lines.append(f"{_q(a)} -> {_q(b)} [{_EDGE_STYLE.get(kind, '')}{arrow}, tooltip={_q(f'{kind} x{n}')}];")
    lines.append("}")
    return "\n".join(lines), len(keep)


# ------------------------------------------------------------------------------------------- create form
def parse_terms(text: str) -> dict[str, list[str]]:
    """Comma/newline-separated keywords; Devanagari terms go under `hi`, the rest under `en`."""
    out: dict[str, list[str]] = {}
    for t in re.split(r"[,\n]", text or ""):
        t = t.strip()
        if t:
            out.setdefault("hi" if _DEVANAGARI.search(t) else "en", []).append(t)
    return out


def build_config(kind_label: str, name: str, purpose: str, targets: str, sources: list[str],
                 description: str = "") -> InvestigationConfig:
    """Validate the sidebar form (raises ValueError / pydantic ValidationError with a readable message)."""
    kind = KINDS[kind_label]
    if kind == "event":
        return InvestigationConfig(kind=kind, name=name.strip(), purpose=purpose, description=description,
                                   terms=parse_terms(targets), sources=sources)
    lines = [ln.strip() for ln in (targets or "").splitlines() if ln.strip()]
    handles = parse_handles(lines)
    return InvestigationConfig(kind=kind, name=name.strip(), purpose=purpose, description=description,
                               handles=handles, sources=[s for s in sources if s in handles] or [])


def default_sources() -> list[str]:
    return list(NO_KEY_SOURCES)


def create_and_collect(cfg: InvestigationConfig, progress=None) -> tuple[int, dict[str, Any]]:
    iid = runner.create(cfg)
    return iid, runner.collect(iid, progress=progress)
