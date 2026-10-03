"""Narrative mapping (batch): embed recent items -> HDBSCAN clusters -> class-based TF-IDF keywords
-> link to fact-checks (member voting) -> growth (last 24h vs previous 72h daily average) -> alerts."""

from __future__ import annotations

import math
import re
import uuid
from collections import Counter
from datetime import timedelta
from typing import Any

import numpy as np
from sklearn.cluster import HDBSCAN
from sqlalchemy import select

from socmint.analyze.claim_match import match_claims
from socmint.analyze.embeddings import TfidfEmbedder, get_embedder, is_tfidf
from socmint.core.db import session_scope
from socmint.core.logging import get_logger
from socmint.core.models import Alert, FactCheck, Item, Narrative, utcnow
from socmint.core.settings import load_config, threshold
from socmint.understand.normalize import match_text

log = get_logger(__name__)
_TOKEN = re.compile(r"[a-zऀ-ॿ][a-z0-9ऀ-ॿ]+")  # keeps Devanagari matras inside words


def _stopwords() -> set[str]:
    cfg = load_config("stopwords") or {}
    return {match_text(str(w)) for ws in cfg.values() for w in (ws or [])}


def ctfidf_keywords(docs_by_cluster: dict[int, list[str]], top_n: int) -> dict[int, list[str]]:
    """Class-based TF-IDF: tf(t,c)/|c| * log(1 + A / f(t)), A = mean words per class."""
    stop = _stopwords()
    counts = {c: Counter(t for d in docs for t in _TOKEN.findall(match_text(d)) if t not in stop and not t.isdigit())
              for c, docs in docs_by_cluster.items()}
    total_f: Counter = Counter()
    for cnt in counts.values():
        total_f.update(cnt)
    avg_words = np.mean([sum(c.values()) for c in counts.values()]) if counts else 1.0
    out = {}
    for c, cnt in counts.items():
        size = sum(cnt.values()) or 1
        scored = {t: (n / size) * math.log(1 + avg_words / total_f[t]) for t, n in cnt.items()}
        out[c] = [t for t, _ in sorted(scored.items(), key=lambda kv: -kv[1])[:top_n]]
    return out


def _link_factchecks(texts: list[str], keywords: list[str], fc_texts: dict[int, str], tfidf_mode: bool,
                     sample: int = 20) -> tuple[list[int], dict[int, int]]:
    """Fact-checks matched by >= max(2, 20%) of sampled member posts. Returns (ids, votes).

    TF-IDF mode also requires >= `factcheck_link_min_shared_keywords` cluster keywords in the fact-check
    text: character n-grams alone confuse topics that share a place name (see DECISIONS.md).
    """
    votes: Counter = Counter()
    picked = texts[:sample]
    for t in picked:
        for m in match_claims(t, top_k=1):
            votes[m.factcheck_id] += 1
    need = max(2, math.ceil(0.2 * len(picked)))
    linked = [fid for fid, v in votes.most_common() if v >= need]
    if tfidf_mode:
        k = int(threshold("narratives.factcheck_link_min_shared_keywords", 2))
        linked = [fid for fid in linked
                  if sum(1 for w in keywords if w in _TOKEN.findall(match_text(fc_texts.get(fid, "")))) >= k]
    return linked, dict(votes)


def run(window_days: int | None = None) -> dict[str, Any]:
    window_days = window_days or int(threshold("narratives.window_days", 14))
    now = utcnow()
    since = now - timedelta(days=window_days)
    with session_scope() as s:
        rows = s.execute(select(Item.id, Item.text, Item.published_at, Item.ingested_at, Item.synthetic)
                         .where(Item.text != "")).all()
    rows = [r for r in rows if (r.published_at or r.ingested_at) >= since]
    min_size = int(threshold("narratives.min_cluster_size", 5))
    if len(rows) < min_size * 2:
        return {"items": len(rows), "clusters": 0, "note": "not enough items in window"}

    texts = [r.text for r in rows]
    emb = get_embedder()
    if is_tfidf(emb):
        local = TfidfEmbedder()
        local.fit(texts)
        X = local.encode(texts)
    else:
        X = emb.encode(texts)
    labels = HDBSCAN(min_cluster_size=min_size, min_samples=int(threshold("narratives.min_samples", 2)),
                     metric="euclidean", copy=True).fit_predict(X)

    clusters: dict[int, list[int]] = {}
    for i, lab in enumerate(labels):
        if lab >= 0:
            clusters.setdefault(int(lab), []).append(i)
    kw = ctfidf_keywords({c: [texts[i] for i in idx] for c, idx in clusters.items()},
                         int(threshold("narratives.keywords_per_cluster", 8)))
    run_id = now.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    growth_alert = float(threshold("narratives.growth_alert", 2.0))
    min24 = int(threshold("narratives.min_count_24h", 4))
    floor = float(threshold("narratives.growth_floor_per_day", 0.5))
    summary = {"items": len(rows), "clusters": len(clusters), "noise": int((labels < 0).sum()), "alerts": 0,
               "run_id": run_id}

    with session_scope() as s:
        fc_rows = s.execute(select(FactCheck.id, FactCheck.title, FactCheck.claim_text)).all()
        fc_titles = {r.id: r.title for r in fc_rows}
        fc_texts = {r.id: f"{r.title} {r.claim_text or ''}" for r in fc_rows}
        for c, idx in sorted(clusters.items()):
            times = [rows[i].published_at or rows[i].ingested_at for i in idx]
            c24 = sum(t >= now - timedelta(hours=24) for t in times)
            prev72 = sum(now - timedelta(hours=96) <= t < now - timedelta(hours=24) for t in times)
            prev_daily = prev72 / 3.0
            growth = round(c24 / max(prev_daily, floor), 2)
            # most central members first (closest to centroid) for fact-check voting and samples
            centroid = X[idx].mean(axis=0)
            order = [idx[j] for j in np.argsort(-(X[idx] @ centroid))]
            linked, votes = _link_factchecks([texts[i] for i in order], kw.get(c, []), fc_texts, is_tfidf(emb))
            n = Narrative(run_id=run_id, cluster_id=c, keywords=kw.get(c, []), size=len(idx), first_seen=min(times),
                          last_seen=max(times), growth_rate=growth, count_24h=c24, matched_factcheck_ids=linked,
                          sample_item_ids=[rows[i].id for i in order[:10]])
            s.add(n)
            if growth >= growth_alert and c24 >= min24:
                debunked = bool(linked)
                synthetic = all(rows[i].synthetic for i in idx)
                title = (f"Debunked narrative resurfacing fast: {', '.join(kw.get(c, [])[:4])}" if debunked
                         else f"Fast-growing narrative: {', '.join(kw.get(c, [])[:4])}")
                s.add(Alert(module="narratives", severity="high" if debunked else "medium", title=title,
                            dedup_key=f"narr:{run_id}:{c}", synthetic=synthetic,
                            evidence={"run_id": run_id, "cluster_id": c, "size": len(idx), "count_24h": c24,
                                      "prev_72h": prev72, "growth_rate": growth, "keywords": kw.get(c, []),
                                      "matched_factchecks": [{"id": f, "title": fc_titles.get(f), "votes": votes.get(f)}
                                                             for f in linked],
                                      "sample_item_ids": [rows[i].id for i in order[:5]],
                                      "sample_texts": [texts[i][:160] for i in order[:3]], "embedder": emb.name}))
                summary["alerts"] += 1
    log.info("narratives_done", extra=summary)
    return summary
