"""P1 (C2, C3, E4): the global analyst views show the global analysis run, never an investigation's scoped run."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from streamlit.testing.v1 import AppTest

PAGES = Path(__file__).resolve().parents[1] / "socmint" / "dashboard" / "pages"


@pytest.fixture(scope="module")
def global_then_scoped(tmp_path_factory):
    """A global run of every module, then the showcase investigations (scoped runs written afterwards)."""
    import os

    from socmint import fixtures
    from socmint.analyze import claim_match, impersonation
    from socmint.analyze.runner import run_modules
    from socmint.core import db
    from socmint.core.settings import reset_settings
    from socmint.demo import seed_investigations
    from socmint.ingest.registry import sync_brands, sync_sources
    from socmint.understand.entities import clear_caches

    path = tmp_path_factory.mktemp("global") / "global.db"
    os.environ.update({"SOCMINT_DB_URL": f"sqlite:///{path.as_posix()}", "SOCMINT_OFFLINE": "1",
                       "EMBEDDING_BACKEND": "tfidf", "PRIVACY_SALT": "t", "SOCMINT_API_KEY": "k"})
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
    clear_caches()
    claim_match.reset_index()
    mods = {r["module"]: r["result"] for r in run_modules("all")}
    seeded = seed_investigations()
    return os.environ["SOCMINT_DB_URL"], mods["narratives"]["run_id"], mods["coordination"]["run_id"], seeded


def _use(url, monkeypatch):
    from socmint.core import db
    from socmint.core.settings import reset_settings

    monkeypatch.setenv("SOCMINT_DB_URL", url)
    monkeypatch.setenv("SOCMINT_API_KEY", "k")
    reset_settings()
    db.get_engine.cache_clear()


def test_selectors_return_the_global_run(global_then_scoped, monkeypatch):
    from socmint.analyze.runs import latest_global_run
    from socmint.core.db import session_scope
    from socmint.core.models import CoordinationGroup, Narrative

    url, narr_run, coord_run, seeded = global_then_scoped
    _use(url, monkeypatch)
    with session_scope() as s:
        # the scoped runs really are newer, so "latest by created_at" alone would pick one of them
        assert s.scalar(select(func.count(Narrative.id)).where(Narrative.investigation_id.is_not(None))) > 0
        newest = s.scalar(select(CoordinationGroup).order_by(CoordinationGroup.created_at.desc()).limit(1))
        assert newest.investigation_id is not None
        assert latest_global_run(s, Narrative) == narr_run
        assert latest_global_run(s, CoordinationGroup) == coord_run


def test_dashboard_pages_show_the_global_run(global_then_scoped, monkeypatch):
    url, narr_run, coord_run, _ = global_then_scoped
    _use(url, monkeypatch)
    at = AppTest.from_file(str(PAGES / "4_Narratives.py"), default_timeout=120).run()
    assert not at.exception
    assert any(narr_run in c.value for c in at.caption)
    at = AppTest.from_file(str(PAGES / "5_Coordination.py"), default_timeout=120).run()
    assert not at.exception
    assert any(coord_run in c.value for c in at.caption)
    # scoped alerts are labelled with their investigation, global ones are not
    table = at.dataframe[-1].value
    assert "investigation" in table.columns
    assert table["investigation"].notna().any() and table["investigation"].isna().any()


def test_api_narratives_global_and_alerts_labelled(global_then_scoped, monkeypatch):
    from socmint.api.app import app
    from socmint.core.db import session_scope
    from socmint.core.models import Narrative

    url, narr_run, _, seeded = global_then_scoped
    _use(url, monkeypatch)
    with session_scope() as s:
        n_global = s.scalar(select(func.count(Narrative.id)).where(Narrative.run_id == narr_run))
    client = TestClient(app)
    rows = client.get("/narratives", headers={"X-API-Key": "k"}).json()
    assert len(rows) == n_global
    alerts = client.get("/alerts", headers={"X-API-Key": "k"}).json()
    assert {a["investigation_id"] for a in alerts} >= {None, seeded["event"]["id"]}
