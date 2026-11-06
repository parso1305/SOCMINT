"""P6 (D6): a model already in the local Hugging Face cache is loaded from disk, with no Hub request."""

from __future__ import annotations

import logging
import sys
import types

import pytest

from socmint.core import hf


class _NotCached(Exception):
    pass


@pytest.fixture
def fake_hub(monkeypatch, tmp_path):
    """snapshot_download that knows one cached repo; records whether it was asked to stay local."""
    calls = []

    def snapshot_download(repo_id, local_files_only=False, **kw):
        calls.append((repo_id, local_files_only))
        if repo_id == "org/cached":
            return str(tmp_path / "snap")
        raise _NotCached(repo_id)

    monkeypatch.setattr(hf, "_snapshot_download", snapshot_download)
    return calls


def test_cached_model_resolves_to_local_snapshot(fake_hub, tmp_path, caplog):
    caplog.set_level(logging.INFO)
    assert hf.resolve("org/cached") == str(tmp_path / "snap")
    assert fake_hub == [("org/cached", True)]  # only ever asked the local cache
    assert any(r.getMessage() == "hf_model_from_cache" for r in caplog.records)


def test_missing_model_falls_back_to_download(fake_hub, caplog):
    caplog.set_level(logging.INFO)
    assert hf.resolve("org/missing") == "org/missing"
    assert any(r.getMessage() == "hf_model_download" for r in caplog.records)


def test_e5_embedder_loads_from_cache_path(fake_hub, monkeypatch, tmp_path):
    seen = {}

    class FakeST:
        def __init__(self, name, device="cpu", **kw):
            seen["name"] = name

    monkeypatch.setitem(sys.modules, "sentence_transformers", types.SimpleNamespace(SentenceTransformer=FakeST))
    from socmint.analyze.embeddings import E5Embedder

    emb = E5Embedder("org/cached")
    assert seen["name"] == str(tmp_path / "snap") and emb.name == "e5:org/cached"
