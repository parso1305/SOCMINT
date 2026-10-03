"""M7: every dashboard page loads with demo data (Streamlit AppTest, headless)."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

DASH = Path(__file__).resolve().parents[1] / "socmint" / "dashboard"
PAGES = [DASH / "app.py", *sorted((DASH / "pages").glob("*.py"))]


@pytest.fixture(scope="module")
def demo_loaded(tmp_path_factory):
    """Load all fixtures and run every module once into an isolated DB shared by the page tests."""
    import os

    from socmint import fixtures
    from socmint.analyze import claim_match, impersonation
    from socmint.analyze.runner import run_modules
    from socmint.core import db
    from socmint.core.settings import reset_settings
    from socmint.ingest.registry import sync_brands, sync_sources
    from socmint.tipline.pipeline import check
    from socmint.understand.entities import clear_caches

    path = tmp_path_factory.mktemp("dash") / "dash.db"
    os.environ.update({"SOCMINT_DB_URL": f"sqlite:///{path.as_posix()}", "SOCMINT_OFFLINE": "1",
                       "EMBEDDING_BACKEND": "tfidf", "PRIVACY_SALT": "t"})
    reset_settings()
    db.get_engine.cache_clear()
    db.init_db()
    sync_sources()
    sync_brands()
    impersonation.brands.cache_clear()
    fixtures.load_factchecks()
    fixtures.load_symbols()
    fixtures.load_items()
    fixtures.load_market()
    fixtures.load_lookalikes()
    clear_caches()
    claim_match.reset_index()
    for i, m in enumerate(fixtures.tipline_messages()[:12]):
        check(m["text"], sender_id=i, synthetic=True)
    check("Reminder: meeting at 5", sender_id=99)
    assert all(r["status"] == "ok" for r in run_modules("all"))
    return os.environ["SOCMINT_DB_URL"]


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.stem)
def test_page_loads(demo_loaded, page, monkeypatch):
    from socmint.core import db
    from socmint.core.settings import reset_settings

    monkeypatch.setenv("SOCMINT_DB_URL", demo_loaded)
    reset_settings()
    db.get_engine.cache_clear()
    at = AppTest.from_file(str(page), default_timeout=120)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.title, "page rendered no title"


def test_review_from_queue_page(demo_loaded, monkeypatch):
    from sqlalchemy import select

    from socmint.core import db
    from socmint.core.models import Submission
    from socmint.core.settings import reset_settings

    monkeypatch.setenv("SOCMINT_DB_URL", demo_loaded)
    reset_settings()
    db.get_engine.cache_clear()
    at = AppTest.from_file(str(DASH / "pages" / "1_Tipline_queue.py"), default_timeout=120)
    at.run()
    at.selectbox[1].set_value("benign")
    at.button[0].click()
    at.run()
    assert not at.exception
    with db.session_scope() as s:
        assert s.scalar(select(Submission).where(Submission.status == "reviewed")) is not None
