"""Database engine/session helpers. SQLite by default; any SQLAlchemy URL works."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from socmint.core.models import AuditLog, Base
from socmint.core.settings import get_settings


@lru_cache(maxsize=8)
def get_engine(url: str | None = None) -> Engine:
    """Create (and cache) an engine for `url` or the configured database."""
    url = url or get_settings().db_url
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        db_path = url.split("///", 1)[-1]
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


def init_db(url: str | None = None) -> Engine:
    """Create all tables (idempotent)."""
    engine = get_engine(url)
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)
    return engine


def _add_missing_columns(engine: Engine) -> None:
    """Tiny additive migration: ALTER TABLE ADD COLUMN for model columns missing from existing tables."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in have:
                    ddl = col.type.compile(engine.dialect)
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" {ddl}'))


@contextmanager
def session_scope(url: str | None = None) -> Iterator[Session]:
    """Transactional session: commits on success, rolls back on error."""
    factory = sessionmaker(bind=get_engine(url), expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def audit(session: Session, actor: str, action: str, target: str = "") -> None:
    """Append an audit-log entry (dashboard views and API queries)."""
    session.add(AuditLog(actor=actor, action=action, target=target[:2000]))
