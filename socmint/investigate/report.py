"""Investigation summary (shared by the showcase dashboard and the export) and the JSON / CSV export bundle."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import select

from socmint.core.db import audit, session_scope
from socmint.core.models import (
    Account,
    Alert,
    CollectionRun,
    CoordinationGroup,
    Edge,
    FactCheck,
    Indicator,
    InvestigationAccount,
    InvestigationItem,
    Item,
    ItemLabel,
    Narrative,
    utcnow,
)
from socmint.core.schemas import account_key
from socmint.core.settings import get_settings
from socmint.investigate.runner import actor, config_of, get
from socmint.understand.urls import host_of

_IND_TYPES = {"urls": "url", "domains": "domain", "phones": "phone", "upi_ids": "upi"}


def _effective(rows: list[ItemLabel]) -> list[dict[str, Any]]:
    from socmint.analyze.categorize import effective_labels

    return effective_labels(rows)


def posts(inv_id: int) -> list[dict[str, Any]]:
    """Every post linked to the investigation with its effective labels and match explanation."""
    with session_scope() as s:
        links = {r.item_id: r for r in s.scalars(select(InvestigationItem).where(
            InvestigationItem.investigation_id == inv_id)).all()}
        ids = list(links)
        items: list[Item] = []
        labels: dict[int, list[ItemLabel]] = defaultdict(list)
        for i in range(0, len(ids), 500):
            items += s.scalars(select(Item).where(Item.id.in_(ids[i : i + 500]))).all()
            for lab in s.scalars(select(ItemLabel).where(ItemLabel.item_id.in_(ids[i : i + 500]))).all():
                labels[lab.item_id].append(lab)
        out = []
        for it in sorted(items, key=lambda x: x.published_at or x.ingested_at, reverse=True):
            meta = it.meta or {}
            out.append({
                "id": it.id, "source": it.source, "platform": it.source_type, "external_id": it.external_id,
                "url": it.url, "published_at": it.published_at, "author_handle": it.author_handle,
                "author_id": it.author_id, "author_display_name": it.author_display_name, "channel": it.channel,
                "lang": it.lang, "text": it.text, "hashtags": it.hashtags or [], "mentions": it.mentions or [],
                "urls": it.urls or [], "engagement": it.engagement or {}, "reply_to": it.reply_to_external_id,
                "forwarded_from": it.forwarded_from, "media": it.media or [],
                "labels": _effective(labels.get(it.id, [])), "matched_by": links[it.id].matched_by,
                "entities": meta.get("entities", {}), "signals": meta.get("signals", {}), "synthetic": it.synthetic})
        return out


def accounts(inv_id: int, post_rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    post_rows = post_rows if post_rows is not None else posts(inv_id)
    per: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in post_rows:
        ident = p["author_id"] or p["author_handle"]
        if ident:
            per[account_key(p["platform"], ident)].append(p)
    with session_scope() as s:
        links = {r.account_id: r for r in s.scalars(select(InvestigationAccount).where(
            InvestigationAccount.investigation_id == inv_id)).all()}
        rows = s.scalars(select(Account).where(Account.id.in_(links or {-1}))).all()
        out = []
        for a in rows:
            mine = per.get(account_key(a.platform, a.platform_id), [])
            times = [p["published_at"] for p in mine if p["published_at"]]
            out.append({"account_id": a.id, "platform": a.platform, "platform_id": a.platform_id, "handle": a.handle,
                        "display_name": a.display_name, "url": a.url, "created_at": a.created_at,
                        "followers": a.followers, "following": a.following, "posts_count": a.posts_count,
                        "description": a.description, "role": links[a.id].role, "added_by": links[a.id].added_by,
                        "posts_in_investigation": len(mine), "first_seen_here": min(times) if times else None,
                        "last_seen_here": max(times) if times else None, "meta": a.meta or {},
                        "synthetic": a.synthetic})
    rank = {"subject": 0, "member": 1, "top_author": 2}
    return sorted(out, key=lambda r: (rank.get(r["role"], 3), -r["posts_in_investigation"]))


def edges(inv_id: int) -> list[dict[str, Any]]:
    with session_scope() as s:
        ids = list(s.scalars(select(InvestigationItem.item_id).where(InvestigationItem.investigation_id == inv_id)).all())
        rows = list(s.scalars(select(Edge).where(Edge.investigation_id == inv_id)).all())
        seen = {r.id for r in rows}
        for i in range(0, len(ids), 500):
            rows += [e for e in s.scalars(select(Edge).where(Edge.item_id.in_(ids[i : i + 500]))).all() if e.id not in seen]
        return [{"kind": e.kind, "src": e.src, "dst": e.dst, "item_id": e.item_id, "created_at": e.created_at}
                for e in rows]


def indicators(post_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Indicators whose values appear in this investigation's posts (looked up, never linked)."""
    seen: Counter = Counter()
    for p in post_rows:
        for k, t in _IND_TYPES.items():
            for v in (p["entities"] or {}).get(k, []):
                seen[(t, v)] += 1
    if not seen:
        return []
    with session_scope() as s:
        out = []
        keys = list(seen)
        for i in range(0, len(keys), 300):
            vals = {v for _, v in keys[i : i + 300]}
            for r in s.scalars(select(Indicator).where(Indicator.value.in_(vals))).all():
                if (r.type, r.value) in seen:
                    out.append({"type": r.type, "value": r.value, "status": r.status, "linked_brand": r.linked_brand,
                                "seen_count": r.seen_count, "posts_here": seen[(r.type, r.value)],
                                "first_seen": r.first_seen, "sources": r.sources})
    return sorted(out, key=lambda r: (-r["posts_here"], r["type"]))


def summary(ref: int | str) -> dict[str, Any]:
    """Counts by source, language, category, day and hour; top accounts, hashtags, URLs, domains; indicators;
    fact-check matches; coordination groups; narratives; market alerts; first/last seen; collection log."""
    inv = get(ref)
    cfg = config_of(inv)
    prows = posts(inv.id)
    arows = accounts(inv.id, prows)
    times = [p["published_at"] for p in prows if p["published_at"]]
    by_cat: Counter = Counter()
    for p in prows:
        by_cat.update({lab["label"] for lab in p["labels"]})
    by_hour: Counter = Counter()
    by_day: Counter = Counter()
    for p in prows:
        if p["published_at"]:
            by_hour[(p["published_at"].replace(minute=0, second=0, microsecond=0), p["platform"])] += 1
            by_day[(p["published_at"].date().isoformat(), p["platform"])] += 1
    tags = Counter("#" + h.lstrip("#").lower() for p in prows for h in p["hashtags"])
    urls = Counter(u for p in prows for u in p["urls"])
    domains = Counter(h for h in (host_of(u) for u in urls.elements()) if h)
    fc_votes: dict[int, dict[str, Any]] = {}
    for p in prows:
        for m in (p["signals"] or {}).get("factchecks", []):
            row = fc_votes.setdefault(m["id"], {**m, "posts": 0, "item_ids": []})
            row["posts"] += 1
            row["score"] = max(row["score"], m["score"])
            row["item_ids"].append(p["id"])
    with session_scope() as s:
        for fid, row in fc_votes.items():
            fc = s.get(FactCheck, fid)
            if fc is not None:
                row.update(rating=fc.rating, synthetic=fc.synthetic)
        last = (inv.last_analysis or {}).get("coordination", {}).get("run_id")
        groups = [{"id": g.id, "score": g.score, "posts": g.n_items, "accounts": g.n_accounts, "channels": g.n_channels,
                   "time_span_minutes": g.time_span_minutes, "synchrony": g.synchrony, "sample_text": g.sample_text,
                   "item_ids": g.item_ids, "edges": g.edges,
                   "evidence": {k: v for k, v in (g.evidence or {}).items() if k != "posts"}}
                  for g in s.scalars(select(CoordinationGroup).where(CoordinationGroup.investigation_id == inv.id,
                                                                     *([CoordinationGroup.run_id == last] if last else []))
                                     .order_by(CoordinationGroup.score.desc())).all()]
        nlast = (inv.last_analysis or {}).get("narratives", {}).get("run_id")
        narrs = [{"id": n.id, "keywords": n.keywords, "size": n.size, "growth_rate": n.growth_rate,
                  "count_24h": n.count_24h, "matched_factcheck_ids": n.matched_factcheck_ids,
                  "first_seen": n.first_seen, "last_seen": n.last_seen}
                 for n in s.scalars(select(Narrative).where(Narrative.investigation_id == inv.id,
                                                            *([Narrative.run_id == nlast] if nlast else []))
                                    .order_by(Narrative.size.desc())).all()]
        market = [{"id": a.id, "title": a.title, "severity": a.severity, "created_at": a.created_at,
                   "risk_score": a.evidence.get("risk_score"), "summary": a.evidence.get("summary"),
                   "disclaimer": a.evidence.get("disclaimer"), "risk_factors": a.evidence.get("risk_factors")}
                  for a in s.scalars(select(Alert).where(Alert.module == "market")).all()
                  if (a.evidence or {}).get("investigation_id") == inv.id]
        runs = [{"source": r.source, "status": r.status, "fetched": r.fetched, "new": r.new, "detail": r.detail,
                 "started_at": r.started_at, "finished_at": r.finished_at}
                for r in s.scalars(select(CollectionRun).where(CollectionRun.investigation_id == inv.id)
                                   .order_by(CollectionRun.id.desc())).all()]
    latest: dict[str, dict[str, Any]] = {}
    for r in runs:  # newest first: the latest status per source
        latest.setdefault(r["source"], r)
    return {
        "investigation": {"id": inv.id, "kind": inv.kind, "name": inv.name, "purpose": inv.purpose,
                          "description": inv.description, "status": inv.status, "created_at": inv.created_at,
                          "last_collected_at": inv.last_collected_at, "synthetic": inv.synthetic,
                          "config": cfg.model_dump(mode="json")},
        "counts": {"posts": len(prows), "accounts": len(arows), "sources": len({p["source"] for p in prows}),
                   "platforms": len({p["platform"] for p in prows}), "authors": len({(p["platform"], p["author_handle"])
                                                                                 for p in prows if p["author_handle"]})},
        "by_source": dict(Counter(p["source"] for p in prows).most_common()),
        "by_platform": dict(Counter(p["platform"] for p in prows).most_common()),
        "by_language": dict(Counter(p["lang"] or "unknown" for p in prows).most_common()),
        "by_category": dict(by_cat.most_common()),
        "by_hour": [{"hour": h, "platform": pl, "posts": n} for (h, pl), n in sorted(by_hour.items())],
        "by_day": [{"day": d, "platform": pl, "posts": n} for (d, pl), n in sorted(by_day.items())],
        "top_accounts": arows[:15],
        "top_hashtags": tags.most_common(15), "top_urls": urls.most_common(15), "top_domains": domains.most_common(15),
        "indicators": indicators(prows),
        "factcheck_matches": sorted(fc_votes.values(), key=lambda r: -r["posts"]),
        "coordination_groups": groups, "narratives": narrs, "market_alerts": market,
        "first_seen": min(times) if times else None, "last_seen": max(times) if times else None,
        "collection": {"runs": runs[:200], "latest_by_source": latest},
    }


# ------------------------------------------------------------------------------------------- export
def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:50] or "investigation"


def _cell(v: Any) -> Any:
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False, default=str)
    return "" if v is None else v


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    cols = list(dict.fromkeys(k for r in rows for k in r)) or ["empty"]
    with path.open("w", encoding="utf-8-sig", newline="") as f:  # BOM so Excel shows Devanagari correctly
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: _cell(r.get(k)) for k in cols})


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


def export(ref: int | str, fmt: str = "json", out_dir: Path | None = None, who: str | None = None) -> Path:
    """Write the bundle (posts with labels, accounts, edges, indicators, summary, config, manifest with SHA-256)."""
    if fmt not in ("json", "csv"):
        raise ValueError("format must be json or csv")
    inv = get(ref)
    summ = summary(inv.id)
    prows = posts(inv.id)
    tables = {"posts": prows, "accounts": accounts(inv.id, prows), "edges": edges(inv.id), "indicators": summ["indicators"]}
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    base = (out_dir or get_settings().data_dir / "exports") / f"{_slug(inv.name)}-{inv.id}-{stamp}-{fmt}"
    base.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    for name, rows in tables.items():
        p = base / f"{name}.{fmt}"
        if fmt == "json":
            p.write_text(_dump(rows), encoding="utf-8")
        else:
            _write_csv(p, rows)
        files.append(p)
    for name, obj in (("summary", summ), ("investigation", summ["investigation"])):
        p = base / f"{name}.json"
        p.write_text(_dump(obj), encoding="utf-8")
        files.append(p)
    manifest = {"investigation_id": inv.id, "name": inv.name, "kind": inv.kind, "purpose": inv.purpose,
                "format": fmt, "exported_at": utcnow().isoformat() + "Z", "exported_by": who or actor(),
                "synthetic": inv.synthetic, "counts": {k: len(v) for k, v in tables.items()},
                "files": [{"name": p.name, "bytes": p.stat().st_size,
                           "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in files],
                "note": "Public posts and public profile fields only. Indicators are suspected/reported, not verdicts."}
    (base / "manifest.json").write_text(_dump(manifest), encoding="utf-8")
    with session_scope() as s:
        audit(s, who or actor(), "investigation_export", f"id={inv.id} format={fmt} path={base}")
    return base


def zip_bundle(folder: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(folder.iterdir()):
            z.write(p, arcname=f"{folder.name}/{p.name}")
    return buf.getvalue()
