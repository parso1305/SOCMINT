"""Hugging Face model resolution: use the local cache when the model is there, download only when it is not.

Loading by repo id makes transformers / sentence-transformers ask the Hub for updates on every run (and warn
about unauthenticated requests). Loading from the cached snapshot path makes no network request at all.
"""

from __future__ import annotations

from typing import Any

from socmint.core.logging import get_logger

log = get_logger(__name__)


def _snapshot_download(repo_id: str, local_files_only: bool = False, **kw: Any) -> str:
    from huggingface_hub import snapshot_download

    return snapshot_download(repo_id, local_files_only=local_files_only, **kw)


def resolve(repo_id: str) -> str:
    """Local snapshot path if `repo_id` is fully cached, else `repo_id` itself (the loader then downloads it)."""
    try:
        path = _snapshot_download(repo_id, local_files_only=True)
    except Exception:  # noqa: BLE001  (not cached, partial cache, huggingface_hub missing)
        log.info("hf_model_download", extra={"model": repo_id})
        return repo_id
    log.info("hf_model_from_cache", extra={"model": repo_id})
    return path
