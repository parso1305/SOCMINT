"""Pluggable text embeddings.

- `E5Embedder`: sentence-transformers multilingual-e5 (optional `[ml]` extra), cross-lingual.
- `TfidfEmbedder`: character n-gram TF-IDF fallback (always available, same-language only).

`get_embedder()` picks according to EMBEDDING_BACKEND (auto | e5 | tfidf) and falls back to TF-IDF
with a warning if the model cannot be loaded (not installed, or offline without a cached model).
"""

from __future__ import annotations

import os
import threading
from typing import Protocol

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from socmint.core.logging import get_logger
from socmint.core.settings import get_settings
from socmint.understand.normalize import match_text

log = get_logger(__name__)


class Embedder(Protocol):
    name: str
    needs_fit: bool

    def fit(self, corpus: list[str]) -> None: ...

    def encode(self, texts: list[str]) -> np.ndarray:
        """Return L2-normalised float32 vectors, one row per text."""
        ...


def _l2(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (m / norms).astype(np.float32)


class E5Embedder:
    """intfloat/multilingual-e5-*: symmetric similarity uses the 'query: ' prefix on both sides."""

    needs_fit = False

    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer

        from socmint.core.hf import resolve

        self.name = f"e5:{model_name}"
        self._model = SentenceTransformer(resolve(model_name), device="cpu")  # cached copy: no Hub request
        self._lock = threading.Lock()

    def fit(self, corpus: list[str]) -> None:  # noqa: D401 - nothing to fit
        return None

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 384), dtype=np.float32)
        with self._lock:
            vecs = self._model.encode([f"query: {t}" for t in texts], batch_size=32, normalize_embeddings=True,
                                      show_progress_bar=False, convert_to_numpy=True)
        return vecs.astype(np.float32)


class TfidfEmbedder:
    """Character n-gram TF-IDF (2-5, word-bounded). Must be fit on the corpus being searched."""

    name = "tfidf:char_wb(2,5)"
    needs_fit = True

    def __init__(self) -> None:
        self._vec: TfidfVectorizer | None = None

    def fit(self, corpus: list[str]) -> None:
        docs = [match_text(t) for t in corpus] or ["empty"]
        self._vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), sublinear_tf=True, min_df=1)
        self._vec.fit(docs)

    def encode(self, texts: list[str]) -> np.ndarray:
        if self._vec is None:
            self.fit(texts)
        assert self._vec is not None
        m = self._vec.transform([match_text(t) for t in texts]).toarray()
        return _l2(m)


_lock = threading.Lock()
_cached: dict[str, object] = {}


def get_embedder(backend: str | None = None) -> Embedder:
    """Return a (cached) embedder. 'auto' tries e5 and falls back to TF-IDF."""
    s = get_settings()
    backend = (backend or s.embedding_backend or "auto").lower()
    with _lock:
        if backend in _cached:
            return _cached[backend]  # type: ignore[return-value]
        emb: Embedder
        if backend in ("auto", "e5"):
            try:
                if os.environ.get("SOCMINT_OFFLINE"):
                    os.environ.setdefault("HF_HUB_OFFLINE", "1")
                emb = E5Embedder(s.embedding_model)
                log.info("embedder_loaded", extra={"backend": emb.name})
            except Exception as e:  # noqa: BLE001  (ImportError, no cached model offline, ...)
                if backend == "e5":
                    log.warning("e5_unavailable_falling_back", extra={"error": str(e)[:300]})
                else:
                    log.warning("embeddings_degraded_to_tfidf", extra={"reason": str(e)[:300]})
                emb = TfidfEmbedder()
        else:
            emb = TfidfEmbedder()
        _cached[backend] = emb
        return emb


def is_tfidf(emb: Embedder) -> bool:
    return emb.name.startswith("tfidf")


def to_bytes(v: np.ndarray) -> bytes:
    return np.asarray(v, dtype=np.float32).tobytes()


def from_bytes(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float32)
