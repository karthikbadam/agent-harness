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
        ("projects", "instructions", "TEXT"),
        ("projects", "skills", "JSON"),
        ("projects", "context_paths", "JSON"),
        ("jobs", "task_id", "VARCHAR(12)"),
        # v2: plan-then-execute + worktrees + integration
        ("jobs", "phase", "VARCHAR(16)"),
        ("jobs", "cwd_override", "TEXT"),
        ("tasks", "mode", "VARCHAR(24) NOT NULL DEFAULT 'plan_then_execute'"),
        ("tasks", "worktree_path", "TEXT"),
        ("tasks", "worktree_branch", "VARCHAR(255)"),
        ("tasks", "integration_status", "VARCHAR(16)"),
        ("tasks", "synthetic", "BOOLEAN NOT NULL DEFAULT 0"),
        ("outcomes", "kind", "VARCHAR(16) NOT NULL DEFAULT 'execute'"),
        # driver
        ("projects", "autopilot_mode", "VARCHAR(8) NOT NULL DEFAULT 'off'"),
        ("tasks", "retries", "INTEGER NOT NULL DEFAULT 0"),
        ("tasks", "last_failed_at", "DATETIME"),
        # v3: phase moves to Task; Jobs carry kind + cwd. The old jobs.phase and
        # jobs.cwd_override columns stay on disk for the lifetime of the install
        # (sqlite ALTER doesn't drop columns) but the ORM no longer maps them.
        ("tasks", "phase", "VARCHAR(16)"),
        ("jobs", "kind", "VARCHAR(12) NOT NULL DEFAULT 'ad_hoc'"),
        ("jobs", "cwd", "TEXT NOT NULL DEFAULT ''"),
        # agent-loop: per-task idle timeout override (null = inherit project/global)
        ("tasks", "idle_timeout_seconds", "INTEGER"),
        # agent-loop Tier 2: native loop task (F4). parent_task_id groups loop
        # iteration children; loop_spec/loop_state live on the mode='loop'
        # parent; outcomes.meta carries each iteration's parsed result.
        ("tasks", "parent_task_id", "VARCHAR(12)"),
        ("tasks", "loop_spec", "JSON"),
        ("tasks", "loop_state", "JSON"),
        ("outcomes", "meta", "JSON"),
        # Tier 3: loop stuck-detection runs a "rethink" iteration, optionally
        # on a stronger model, via a per-task model override.
        ("tasks", "model_override", "VARCHAR(64)"),
        # attachments: user-uploaded files; cover image for project cards.
        ("projects", "cover_image_id", "VARCHAR(12)"),
        ("turns", "attachment_ids", "JSON"),
        # codex provider: project default (claude|codex|auto), job resolved
        # provider (claude|codex), per-task override (null = inherit). Column
        # defaults backfill existing rows to 'claude' — no behavior change.
        ("projects", "agent_provider", "VARCHAR(8) NOT NULL DEFAULT 'claude'"),
        ("jobs", "agent_provider", "VARCHAR(8) NOT NULL DEFAULT 'claude'"),
        ("tasks", "agent_provider", "VARCHAR(8)"),
    ]

    with engine.begin() as conn:
        for table, column, ddl in additions:
            existing = {
                row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
            }
            if column in existing:
                continue
            conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            # Behavioral backfill for Task.mode: tasks authored before v2 should
            # keep one-shot behavior. ADD COLUMN fills existing rows with the
            # column-level default ('plan_then_execute'); flip them to
            # 'one_shot' so only post-upgrade tasks hit the new planning gate.
            if table == "tasks" and column == "mode":
                conn.exec_driver_sql(
                    "UPDATE tasks SET mode='one_shot' WHERE mode='plan_then_execute'"
                )
            # v3 backfill: copy the old jobs.phase into the new tasks.phase, and
            # populate jobs.kind/cwd from the old fields so existing rows survive
            # the cutover. The old columns stay readable via raw SQL but the ORM
            # ignores them from here on.
            if table == "tasks" and column == "phase":
                conn.exec_driver_sql(
                    """
                    UPDATE tasks SET phase = (
                      SELECT j.phase FROM jobs j
                      WHERE j.task_id = tasks.id AND j.phase IS NOT NULL
                      ORDER BY j.created_at DESC LIMIT 1
                    )
                    """
                )
            if table == "jobs" and column == "kind":
                conn.exec_driver_sql(
                    """
                    UPDATE jobs SET kind = CASE
                      WHEN task_id IS NULL THEN 'ad_hoc'
                      WHEN phase = 'planning' OR phase = 'awaiting_ack' THEN 'plan'
                      WHEN phase = 'executing' THEN 'execute'
                      WHEN phase = 'integrating' THEN 'integrate'
                      ELSE 'ad_hoc'
                    END
                    """
                )
            if table == "jobs" and column == "cwd":
                conn.exec_driver_sql(
                    """
                    UPDATE jobs SET cwd = COALESCE(
                      cwd_override,
                      (SELECT p.path FROM projects p WHERE p.id = jobs.project_id),
                      ''
                    )
                    """
                )


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
