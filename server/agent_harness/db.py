"""SQLAlchemy 2.0 engine + session factory. SQLite with WAL."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _make_engine(db_path: Path) -> Engine:
    url = f"sqlite:///{db_path}"
    eng = create_engine(url, echo=False, future=True, connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _set_sqlite_pragma(dbapi_connection, _):  # type: ignore[no-untyped-def]
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    return eng


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        settings = get_settings()
        assert settings.db_path is not None
        _engine = _make_engine(settings.db_path)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def init_db() -> None:
    """Create tables if missing. Called on startup and by `agent-harness init`."""
    from . import models  # noqa: F401  ensure tables registered

    engine = get_engine()
    models.Base.metadata.create_all(engine)
    _apply_column_migrations(engine)


def _apply_column_migrations(engine: Engine) -> None:
    """Idempotent ALTER TABLE for columns added after first release.

    SQLAlchemy's `create_all` only creates missing tables — it won't add
    columns to existing ones. We keep a tiny per-column add list here.
    """
    additions: list[tuple[str, str, str]] = [
        ("projects", "is_default", "BOOLEAN NOT NULL DEFAULT 0"),
    ]
    from sqlalchemy import text

    with engine.begin() as conn:
        for table, column, ddl in additions:
            existing = {
                row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
            }
            if column not in existing:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def reset_engine() -> None:
    """For tests: drop the cached engine so a new AH_HOME picks up a fresh DB."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


@contextmanager
def session_scope() -> Iterator[Session]:
    get_engine()
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    get_engine()
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.close()
