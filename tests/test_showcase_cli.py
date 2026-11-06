"""P4: `socmint showcase --demo` reuses a current data/demo.db (instant start); `--rebuild` forces a rebuild."""

from __future__ import annotations

import sqlite3

import pytest
from typer.testing import CliRunner

from socmint import cli, demo, scenario


@pytest.fixture
def demo_path(tmp_path, monkeypatch):
    path = tmp_path / "demo.db"
    monkeypatch.setattr(demo, "demo_db_path", lambda: path)
    return path


def _seed_names(path, names):
    from socmint.core.db import get_engine, init_db
    from socmint.investigate import runner
    from socmint.investigate.config import InvestigationConfig

    url = f"sqlite:///{path.as_posix()}"
    init_db(url)
    get_engine.cache_clear()
    import os

    old = os.environ["SOCMINT_DB_URL"]
    os.environ["SOCMINT_DB_URL"] = url
    from socmint.core.settings import reset_settings

    reset_settings()
    try:
        for n in names:
            runner.create(InvestigationConfig(kind="event", name=n, purpose="p", terms={"en": ["x"]}, synthetic=True))
    finally:
        os.environ["SOCMINT_DB_URL"] = old
        reset_settings()
        get_engine.cache_clear()


def test_demo_db_ready_checks_file_schema_and_showcase_data(demo_path):
    assert demo.demo_db_ready() == (False, "data/demo.db does not exist")
    _seed_names(demo_path, [scenario.EVENT_NAME])
    ok, why = demo.demo_db_ready()
    assert not ok and "showcase investigations" in why
    _seed_names(demo_path, [scenario.SUBJECT_NAME, scenario.GROUP_NAME])
    assert demo.demo_db_ready() == (True, "")
    with sqlite3.connect(demo_path) as c:  # an older schema (missing table) is not current
        c.execute("DROP TABLE item_labels")
    ok, why = demo.demo_db_ready()
    assert not ok and "schema" in why


def test_showcase_reuses_or_rebuilds(demo_path, monkeypatch):
    builds, launched = [], []
    monkeypatch.setattr(demo, "run_showcase_demo",
                        lambda: builds.append(1) or {"db": "sqlite:///x", "investigations": {}})
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **kw: launched.append(kw["env"]["SOCMINT_DB_URL"]))
    ready = {"v": (False, "data/demo.db does not exist")}
    monkeypatch.setattr(demo, "demo_db_ready", lambda: ready["v"])

    r = CliRunner().invoke(cli.app, ["showcase", "--demo"])
    assert r.exit_code == 0 and len(builds) == 1 and "Built data/demo.db in" in r.output
    ready["v"] = (True, "")
    r = CliRunner().invoke(cli.app, ["showcase", "--demo"])
    assert r.exit_code == 0 and len(builds) == 1 and "Using existing data/demo.db" in r.output
    r = CliRunner().invoke(cli.app, ["showcase", "--rebuild"])
    assert r.exit_code == 0 and len(builds) == 2
    assert len(launched) == 3 and all("demo.db" in u or u == "sqlite:///x" for u in launched)
