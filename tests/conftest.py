"""Shared fixtures: every test gets an isolated SQLite DB and data dir; no network, no keys."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Point settings at a temp DB/data dir, set a test salt, and force the offline TF-IDF backend."""
    from socmint.core import db
    from socmint.core.settings import reset_settings

    monkeypatch.setenv("SOCMINT_DB_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    monkeypatch.setenv("SOCMINT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PRIVACY_SALT", "test-salt")
    monkeypatch.setenv("SOCMINT_API_KEY", "test-key")
    monkeypatch.setenv("EMBEDDING_BACKEND", monkeypatch_backend())
    monkeypatch.setenv("SOCMINT_OFFLINE", "1")
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "YOUTUBE_API_KEY",
                "GOOGLE_FACTCHECK_API_KEY", "REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "GOOGLE_SAFE_BROWSING_KEY",
                "URLHAUS_AUTH_KEY", "BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD"):
        monkeypatch.setenv(key, "")
    reset_settings()
    db.get_engine.cache_clear()
    db.init_db()
    yield tmp_path
    db.get_engine.cache_clear()
    reset_settings()


def monkeypatch_backend() -> str:
    """Tests use TF-IDF by default (fast, deterministic). Set SOCMINT_TEST_BACKEND=e5 to test e5."""
    import os

    return os.environ.get("SOCMINT_TEST_BACKEND", "tfidf")
