"""Claim matching: embed a message and find the closest fact-checks by cosine similarity."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sqlalchemy import func, select

from socmint.analyze.embeddings import Embedder, from_bytes, get_embedder, is_tfidf, to_bytes
from socmint.core.db import session_scope
from socmint.core.logging import get_logger
from socmint.core.models import FactCheck, Item
from socmint.core.schemas import FactCheckMatch
from socmint.core.settings import PROJECT_ROOT, threshold

log = get_logger(__name__)


def factcheck_text(fc: FactCheck) -> str:
    claim = fc.claim_text or ""
    return fc.title if claim.strip() == fc.title.strip() else f"{fc.title}. {claim}"


@dataclass
class _Index:
    key: tuple
    ids: list[int]
    matrix: np.ndarray
    embedder: Embedder
    mu: np.ndarray | None = None   # background mean (dense backends); None = raw cosine


_index: _Index | None = None
_index_lock = threading.Lock()


def ensure_embeddings(emb: Embedder | None = None) -> int:
    """Compute and store embeddings for fact-checks missing them (dense backends only). Returns count."""
    emb = emb or get_embedder()
    if is_tfidf(emb):
        return 0
    n = 0
    with session_scope() as s:
        rows = s.scalars(select(FactCheck).where(
            (FactCheck.embedding.is_(None)) | (FactCheck.embedding_model != emb.name))).all()
        for i in range(0, len(rows), 64):
            batch = rows[i : i + 64]
            vecs = emb.encode([factcheck_text(r) for r in batch])
            for r, v in zip(batch, vecs, strict=True):
                r.embedding = to_bytes(v)
                r.embedding_model = emb.name
                n += 1
    if n:
        log.info("factcheck_embeddings_updated", extra={"count": n, "model": emb.name})
    return n


def _get_index(emb: Embedder) -> _Index:
    global _index
    with session_scope() as s:
        key = (emb.name, s.scalar(select(func.count(FactCheck.id))), s.scalar(select(func.max(FactCheck.id))),
               s.scalar(select(func.max(Item.id))))
    with _index_lock:
        if _index is not None and _index.key == key:
            return _index
        ensure_embeddings(emb)
        with session_scope() as s:
            rows = s.scalars(select(FactCheck).order_by(FactCheck.id)).all()
            ids = [r.id for r in rows]
            mu = None
            if is_tfidf(emb):
                texts = [factcheck_text(r) for r in rows]
                emb.fit(texts or ["empty"])
                matrix = emb.encode(texts) if texts else np.zeros((0, 1), dtype=np.float32)
            else:
                matrix = np.vstack([from_bytes(r.embedding) for r in rows]) if rows else np.zeros((0, 384), np.float32)
                mu = background_mean(emb, s, matrix)
                if mu is not None:
                    matrix = _center(matrix, mu)
        _index = _Index(key, ids, matrix, emb, mu)
        return _index


def background_mean_path() -> Path:
    return PROJECT_ROOT / str(threshold("claim_match.background_mean_file", "data/e5_background_mean.npy"))


def background_mean(emb: Embedder, s, matrix: np.ndarray) -> np.ndarray | None:
    """Mean embedding of a broad background corpus, used to centre e5 vectors.

    Prefers the shipped file (computed from diverse real news headlines with `socmint analyze --module
    background`), so results do not depend on whatever topics happen to dominate the local DB.
    Falls back to recent local items + fact-checks when no file exists.
    """
    p = background_mean_path()
    if p.exists():
        mu = np.load(p)
        if mu.shape[0] == matrix.shape[1]:
            return mu.astype(np.float32)
        log.warning("background_mean_dim_mismatch", extra={"file": str(p)})
    bg_texts = s.scalars(select(Item.text).where(Item.text != "").order_by(Item.id.desc())
                         .limit(int(threshold("claim_match.background_items", 1000)))).all()
    if len(bg_texts) + len(matrix) < int(threshold("claim_match.center_min_background", 30)):
        return None
    bg = emb.encode(list(bg_texts)) if bg_texts else np.zeros((0, matrix.shape[1]), np.float32)
    return np.vstack([bg, matrix]).mean(axis=0).astype(np.float32)


def compute_background_mean(limit: int = 2000) -> dict:
    """Recompute and save the background mean from real (non-synthetic) ingested items."""
    emb = get_embedder()
    if is_tfidf(emb):
        return {"saved": False, "reason": "TF-IDF backend does not use a background mean"}
    with session_scope() as s:
        texts = s.scalars(select(Item.text).where(Item.text != "", Item.synthetic.is_(False))
                          .order_by(Item.id.desc()).limit(limit)).all()
    if len(texts) < 100:
        return {"saved": False, "reason": f"need >=100 real items, have {len(texts)}"}
    mu = emb.encode(list(texts)).mean(axis=0).astype(np.float32)
    p = background_mean_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    np.save(p, mu)
    reset_index()
    return {"saved": True, "path": str(p), "items": len(texts), "model": emb.name}


def _center(m: np.ndarray, mu: np.ndarray) -> np.ndarray:
    c = m - mu
    n = np.linalg.norm(c, axis=-1, keepdims=True)
    n[n == 0] = 1.0
    return (c / n).astype(np.float32)


def reset_index() -> None:
    global _index
    with _index_lock:
        _index = None


def match_threshold(emb: Embedder, centered: bool = False) -> float:
    if is_tfidf(emb):
        return float(threshold("claim_match.tfidf", 0.45))
    return float(threshold("claim_match.e5_centered" if centered else "claim_match.e5", 0.9))


def match_claims(text: str, top_k: int | None = None, min_score: float | None = None) -> list[FactCheckMatch]:
    """Top-k fact-checks above threshold for `text`, best first."""
    if not text or not text.strip():
        return []
    emb = get_embedder()
    idx = _get_index(emb)
    if not idx.ids:
        return []
    top_k = top_k or int(threshold("claim_match.top_k", 3))
    min_score = match_threshold(emb, idx.mu is not None) if min_score is None else min_score
    q = idx.embedder.encode([text])
    q = (_center(q, idx.mu) if idx.mu is not None else q)[0]
    sims = idx.matrix @ q
    order = np.argsort(-sims)[:top_k]
    picked = [(idx.ids[i], float(sims[i])) for i in order if sims[i] >= min_score]
    if not picked:
        return []
    with session_scope() as s:
        rows = {r.id: r for r in s.scalars(select(FactCheck).where(FactCheck.id.in_([p[0] for p in picked]))).all()}
        return [FactCheckMatch(factcheck_id=i, publisher=rows[i].publisher, title=rows[i].title, url=rows[i].url,
                               rating=rows[i].rating, published_at=rows[i].published_at, lang=rows[i].lang,
                               score=round(sc, 4)) for i, sc in picked if i in rows]


def similarity_to_factchecks(vectors: np.ndarray, emb: Embedder) -> tuple[list[int], np.ndarray]:
    """For narrative linking: returns (factcheck ids, similarity matrix [n_vectors x n_factchecks])."""
    idx = _get_index(emb)
    if not idx.ids:
        return [], np.zeros((len(vectors), 0))
    if idx.mu is not None:
        vectors = _center(vectors, idx.mu)
    return idx.ids, vectors @ idx.matrix.T


def index_is_centered() -> bool:
    return _index is not None and _index.mu is not None
