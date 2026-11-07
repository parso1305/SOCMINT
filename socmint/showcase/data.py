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
from socmint.core.status import normalize
from socmint.investigate import report, runner
from socmint.investigate.config import (
    CHANNEL_SOURCES,
    EVENT_KEY_SOURCES,
    LOGICAL_SOURCES,
    NO_KEY_SOURCES,
    InvestigationConfig,
    default_event_sources,
    missing_keys,
    parse_handles,
)

KINDS = {"Event": "event", "Person / Account": "subject", "Group": "group"}
STATUS_COLOR = {"ok": "green", "partial": "orange", "throttled": "violet", "no_key": "gray", "offline": "gray",
                "opted_out": "blue", "error": "red"}  # one colour per socmint.core.status word
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
# Posts table: pixel widths that fit the main area at 1366 px with the sidebar open (no horizontal scrolling).
POSTS_COLUMN_WIDTHS = {"time (UTC)": 95, "platform": 75, "author": 135, "text": 330, "categories": 150, "lang": 45,
                       "link": 45}


def demo_mode() -> bool:
    return "demo.db" in get_settings().db_url


def synthetic_built_at() -> Any:
    """When this DB's synthetic (demo) data was built: the creation time of its first synthetic investigation."""
    from sqlalchemy import func, select

    from socmint.core.db import session_scope
    from socmint.core.models import Investigation

    with session_scope() as s:
        return s.scalar(select(func.min(Investigation.created_at)).where(Investigation.synthetic.is_(True)))


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


_PLATFORM_NAMES = {"bluesky": "Bluesky", "mastodon": "Mastodon", "telegram": "Telegram", "telegram_channels": "Telegram",
                   "reddit": "Reddit", "youtube": "YouTube", "news_rss": "News", "news": "News", "rss": "News",
                   "gdelt": "GDELT", "google_factcheck": "Google Fact Check"}


def source_label(name: str) -> str:
    """Friendly name for a source: "demo_bluesky" -> "Bluesky (demo)", a feed -> its `publisher` in sources.yaml."""
    from socmint.ingest.registry import source_configs

    if name.startswith("demo_"):
        rest = name.removeprefix("demo_")
        return f"{_PLATFORM_NAMES.get(rest, rest.replace('_', ' ').title())} (demo)"
    cfg = next((c for c in source_configs() if c.get("name") == name), None)
    if cfg and cfg.get("publisher"):
        return str(cfg["publisher"])
    if cfg and cfg.get("type") in _PLATFORM_NAMES:
        return _PLATFORM_NAMES[cfg["type"]]
    return _PLATFORM_NAMES.get(name) or name.replace("_", " ").title()


def source_status(d: dict[str, Any]) -> list[dict[str, Any]]:
    """One chip per source: this investigation's total posts from it ("Bluesky (demo) · 5 posts"), coloured by the
    last run's status; a last run that was not `ok` is named too. The last run's new count is in `last` and in the
    collection log (a later run that finds nothing new must not make a source look empty)."""
    out = []
    totals = d["summary"].get("by_source", {})
    for k, r in d["summary"]["collection"]["latest_by_source"].items():
        label = source_label(k)
        status = normalize(r["status"])
        posts = int(totals.get(k, 0))
        how = f"{r['mode']} · " if r.get("mode") else ""  # e.g. "search (logged in)", "live listen 60 s"
        failed = "" if status == "ok" else f" · {status.replace('_', ' ')}"
        out.append({"source": k, "label": label, "text": f"{label} · {how}{posts} posts{failed}", "status": status,
                    "posts": posts, "fetched": r["fetched"], "new": r["new"], "detail": r["detail"] or "",
                    "last": f"last run: {status.replace('_', ' ')}, {r['new']} new of {r['fetched']} fetched",
                    "color": STATUS_COLOR[status]})
    return sorted(out, key=lambda c: c["label"].lower())


def _hours(h: float) -> str:
    return "under 1 h" if h < 1 else f"{h:.0f} h"


def growth_rated(s: dict[str, Any]) -> bool:
    """Narrative growth compares the last 24 h with a baseline, so it means something only once the investigation
    has collected for `narratives.min_baseline_hours` (a first collection makes every narrative look new)."""
    return s.get("history_hours", 0) >= s.get("baseline_hours", 72)


def growth_note(s: dict[str, Any]) -> str | None:
    """Why the narratives table shows no growth yet (None once growth is rated)."""
    if growth_rated(s):
        return None
    return (f"Growth not rated yet: {_hours(s.get('history_hours', 0))} of collection history, a baseline needs "
            f"{_hours(s.get('baseline_hours', 72))} (narratives.min_baseline_hours).")


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
    active = [x for x in s["narratives"] if x["count_24h"]]
    if active and not growth_rated(s):
        x = max(active, key=lambda x: x["count_24h"])
        out.append(f"Most active narrative: {', '.join(x['keywords'][:4])} ({x['count_24h']} posts in the last 24 h; "
                   f"growth not rated yet: {_hours(s['history_hours'])} of history, "
                   f"needs {_hours(s['baseline_hours'])}).")
    elif active:
        x = max(active, key=lambda x: x["growth_rate"] or 0)
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
_BUCKETS = ((48, "h", "hour"), (24 * 7, "6h", "6 hours"))  # (span up to, pandas frequency, label); else daily


def posts_over_time(d: dict[str, Any]) -> tuple[pd.DataFrame, str]:
    """Posts per time bucket and platform, with the bucket's name. The bucket follows the span of the posts:
    hourly under 48 h, 6-hourly up to 7 days, daily beyond (so a few days of posts are not 1-4 per bar)."""
    rows = d["summary"]["by_hour"]
    if not rows:
        return pd.DataFrame(), "hour"
    df = pd.DataFrame(rows)
    df["hour"] = pd.to_datetime(df["hour"])
    span_h = (df["hour"].max() - df["hour"].min()).total_seconds() / 3600
    freq, label = next(((f, lab) for limit, f, lab in _BUCKETS if span_h < limit), ("D", "day"))
    df["bucket"] = df["hour"].dt.floor(freq)
    out = df.pivot_table(index="bucket", columns="platform", values="posts", aggfunc="sum", fill_value=0)
    out = out.reindex(pd.date_range(out.index.min(), out.index.max(), freq=freq), fill_value=0)
    out.index.name = "time (UTC)"
    return out, label


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
        text = " ".join((p["text"] or "").split())
        rows.append({"time (UTC)": p["published_at"], "platform": p["platform"],
                     "author": p["author_handle"] or p["channel"] or "",
                     "text": text if len(text) <= 140 else text[:139].rstrip() + "…",
                     "categories": ", ".join(labs), "lang": p["lang"], "link": p["url"], "id": p["id"]})
    return pd.DataFrame(rows)


def label_evidence(lab: dict[str, Any]) -> str:
    """One readable line for a label's evidence (keywords, rule, fact-check, indicators...)."""
    ev = lab.get("evidence") or {}
    parts = [f"rule `{ev['rule']}`"] if ev.get("rule") else []
    kws = list(ev.get("keywords") or []) + [k for ks in (ev.get("sublabel_keywords") or {}).values() for k in ks]
    if kws:
        parts.append("keywords: " + ", ".join(dict.fromkeys(kws)))
    if ev.get("reasons"):
        parts.append("scam signals: " + ", ".join(ev["reasons"][:5]))
    if ev.get("factchecks"):
        parts.append("fact-check: " + ev["factchecks"][0].get("title", ""))
    if ev.get("findings"):
        parts.append("brand findings: " + ", ".join(str(f.get("value") or f.get("host") or f.get("brand"))
                                                    for f in ev["findings"][:3]))
    if ev.get("tickers"):
        parts.append("tickers: " + ", ".join(ev["tickers"]))
    if ev.get("matched_investigation_term"):
        mit = ev["matched_investigation_term"]
        terms = mit if isinstance(mit, list) else [t for ts in mit.values() for t in ts]
        parts.append("not counted (investigation's own terms): " + ", ".join(dict.fromkeys(terms)))
    if ev.get("note") and not parts[1:]:
        parts.append(ev["note"])
    return "; ".join(parts) or "—"


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


def split_terms(text: str) -> list[str]:
    return [t.strip() for t in re.split(r"[,\n]", text or "") if t.strip()]


def build_config(kind_label: str, name: str, purpose: str, targets: str, sources: list[str],
                 description: str = "", hi_terms: str = "", mr_terms: str = "") -> InvestigationConfig:
    """Validate the sidebar form (raises ValueError / pydantic ValidationError with a readable message).
    `hi_terms` / `mr_terms` (their own input lines) are stored under `hi` / `mr`."""
    kind = KINDS[kind_label]
    if kind == "event":
        terms = parse_terms(targets)
        for lang, line in (("hi", hi_terms), ("mr", mr_terms)):
            for t in split_terms(line):
                if t not in terms.get(lang, []):
                    terms.setdefault(lang, []).append(t)
        return InvestigationConfig(kind=kind, name=name.strip(), purpose=purpose, description=description,
                                   terms=terms, sources=sources)
    lines = [ln.strip() for ln in (targets or "").splitlines() if ln.strip()]
    handles = parse_handles(lines)
    return InvestigationConfig(kind=kind, name=name.strip(), purpose=purpose, description=description,
                               handles=handles, sources=[s for s in sources if s in handles] or [])


def default_sources() -> list[str]:
    return default_event_sources()


def source_options() -> list[dict[str, Any]]:
    """Every logical source for the form: available (keys set), the .env variables it still needs, and whether a
    new event uses it by default. A source with missing keys is shown greyed out with the variable name."""
    defaults = default_event_sources()
    order = [*NO_KEY_SOURCES, *EVENT_KEY_SOURCES, *CHANNEL_SOURCES]
    out = []
    for lg in [*order, *sorted(set(LOGICAL_SOURCES) - set(order))]:
        miss = missing_keys(lg)
        label = lg + (f" — needs {' and '.join(miss)} in .env" if miss else "")
        if not miss and lg in CHANNEL_SOURCES:
            label += f" (only inside listed {CHANNEL_SOURCES[lg]})"
        out.append({"source": lg, "label": label, "available": not miss, "missing": miss, "default": lg in defaults})
    return out


def create_and_collect(cfg: InvestigationConfig, progress=None) -> tuple[int, dict[str, Any]]:
    iid = runner.create(cfg)
    return iid, runner.collect(iid, progress=progress)


def add_suggested(inv_id: int, terms: list[str]) -> dict[str, Any]:
    """Overview "Add selected": the same validated, audited path as `investigate edit`."""
    from socmint.investigate.suggest import add_suggested as add

    return add(inv_id, terms)
