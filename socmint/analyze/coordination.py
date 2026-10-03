"""Chain Coherence Score v0 - "possible coordination" among public-channel posts (never "bots").

1. Near-duplicates: MinHash over word 3-gram shingles, LSH at Jaccard >= threshold, grouped (union-find).
2. Per group: distinct accounts, time span, synchrony (share within +/- window of the median time),
   channel diversity, burstiness.
3. Score = weighted combination (weights in thresholds.yaml), in [0, 1].
4. networkx co-posting graph of accounts; groups above `alert_score` become alerts with full evidence.
Accounts appear only as nodes in groups - this module never profiles individuals.
"""

from __future__ import annotations

import re
import uuid
from datetime import timedelta
from typing import Any

import networkx as nx
from datasketch import MinHash, MinHashLSH
from sqlalchemy import select

from socmint.core.db import session_scope
from socmint.core.logging import get_logger
from socmint.core.models import Alert, CoordinationGroup, Item, utcnow
from socmint.core.settings import threshold
from socmint.understand.normalize import match_text

log = get_logger(__name__)
PUBLIC_TYPES = ("telegram", "reddit", "youtube")
_TOK = re.compile(r"[a-z0-9ऀ-ॿ]+")


def shingles(text: str, k: int) -> set[str]:
    toks = _TOK.findall(match_text(text))
    if len(toks) < k:
        return set(toks)
    return {" ".join(toks[i : i + k]) for i in range(len(toks) - k + 1)}


def minhash(sh: set[str], num_perm: int) -> MinHash:
    m = MinHash(num_perm=num_perm, seed=1)
    for s in sh:
        m.update(s.encode("utf-8"))
    return m


def group_metrics(times: list, accounts: list[str], channels: list[str]) -> dict[str, float]:
    w = timedelta(minutes=float(threshold("coordination.sync_window_minutes", 10)))
    ts = sorted(times)
    med = ts[len(ts) // 2] if len(ts) % 2 else ts[len(ts) // 2 - 1] + (ts[len(ts) // 2] - ts[len(ts) // 2 - 1]) / 2
    sync = sum(abs(t - med) <= w for t in ts) / len(ts)
    span_min = (ts[-1] - ts[0]).total_seconds() / 60
    weights = threshold("coordination.weights", {}) or {}
    sat = float(threshold("coordination.accounts_saturation", 10))
    ref = float(threshold("coordination.burst_reference_minutes", 360))
    n_acc, n_ch = len(set(accounts)), len(set(channels))
    parts = {
        "accounts": min(n_acc / sat, 1.0),
        "synchrony": sync,
        "channels": min((n_ch - 1) / 2, 1.0) if n_ch > 1 else 0.0,
        "burst": max(0.0, 1 - span_min / ref),
    }
    score = sum(float(weights.get(k, 0)) * v for k, v in parts.items())
    return {"score": round(score, 3), "n_accounts": n_acc, "n_channels": n_ch, "time_span_minutes": round(span_min, 1),
            "synchrony": round(sync, 3), "components": {k: round(v, 3) for k, v in parts.items()},
            "median_time": med}


def run(window_days: int = 7) -> dict[str, Any]:
    jac = float(threshold("coordination.jaccard", 0.8))
    num_perm = int(threshold("coordination.num_perm", 128))
    k = int(threshold("coordination.shingle_words", 3))
    min_size = int(threshold("coordination.min_group_size", 3))
    min_acc = int(threshold("coordination.min_accounts", 3))
    alert_score = float(threshold("coordination.alert_score", 0.6))
    since = utcnow() - timedelta(days=window_days)
    with session_scope() as s:
        rows = s.execute(select(Item.id, Item.text, Item.published_at, Item.author_handle, Item.channel, Item.url,
                                Item.synthetic, Item.source_type)
                         .where(Item.source_type.in_(PUBLIC_TYPES), Item.published_at >= since,
                                Item.author_handle.is_not(None))).all()
    lsh = MinHashLSH(threshold=jac, num_perm=num_perm)
    hashes: dict[int, MinHash] = {}
    for r in rows:
        sh = shingles(r.text or "", k)
        if len(sh) < 3:  # too short to call a duplicate meaningfully
            continue
        m = minhash(sh, num_perm)
        hashes[r.id] = m
        lsh.insert(str(r.id), m)

    parent: dict[int, int] = {i: i for i in hashes}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, m in hashes.items():
        for j in map(int, lsh.query(m)):
            if j != i and m.jaccard(hashes[j]) >= jac:
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in hashes:
        groups.setdefault(find(i), []).append(i)

    by_id = {r.id: r for r in rows}
    run_id = utcnow().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    summary = {"items": len(rows), "groups": 0, "alerts": 0, "run_id": run_id}
    with session_scope() as s:
        for members in groups.values():
            items = [by_id[i] for i in members]
            accounts = [it.author_handle for it in items]
            if len(items) < min_size or len(set(accounts)) < min_acc:
                continue
            met = group_metrics([it.published_at for it in items], accounts, [it.channel or "" for it in items])
            g = nx.Graph()
            for it in items:
                g.add_node(it.author_handle, channel=it.channel)
            accs = sorted(set(accounts))
            for a_i, a in enumerate(accs):
                for b in accs[a_i + 1 :]:
                    g.add_edge(a, b, weight=1)
            edges = [[a, b] for a, b in g.edges()]
            items_sorted = sorted(items, key=lambda it: it.published_at)
            evidence = {
                "components": met["components"], "weights": threshold("coordination.weights", {}),
                "jaccard_threshold": jac, "median_time": str(met["median_time"]),
                "posts": [{"item_id": it.id, "account": it.author_handle, "channel": it.channel,
                           "time": str(it.published_at), "url": it.url} for it in items_sorted[:50]],
                "graph": {"nodes": g.number_of_nodes(), "edges": g.number_of_edges(), "density": round(nx.density(g), 3)},
            }
            s.add(CoordinationGroup(run_id=run_id, score=met["score"], n_items=len(items), n_accounts=met["n_accounts"],
                                    n_channels=met["n_channels"], time_span_minutes=met["time_span_minutes"],
                                    synchrony=met["synchrony"], sample_text=items_sorted[0].text[:300],
                                    item_ids=[it.id for it in items_sorted], edges=edges, evidence=evidence))
            summary["groups"] += 1
            if met["score"] >= alert_score:
                s.add(Alert(module="coordination", severity="high" if met["score"] >= 0.8 else "medium",
                            title=(f"Possible coordination: {len(items)} near-identical posts by {met['n_accounts']} "
                                   f"accounts in {met['time_span_minutes']:.0f} min"),
                            dedup_key=f"coord:{run_id}:{min(members)}", synthetic=all(it.synthetic for it in items),
                            evidence={**evidence, "score": met["score"], "sample_text": items_sorted[0].text[:300],
                                      "label": "possible coordination (not proof of automation or intent)"}))
                summary["alerts"] += 1
    log.info("coordination_done", extra=summary)
    return summary
