"""SQLAlchemy 2.0 ORM models.

Tables:
- projects: a repo/working-directory + per-project permission defaults +
  shared context (instructions/skills/context_paths).
- jobs: a conversation (1..N turns) tied to one project, optionally a task.
- turns: a single `claude -p` invocation; first turn captures session_id.
- schedules: cron entries that enqueue a job when fired.
- allowlist_rules: rule strings like `Bash(npm test:*)`; global or project-scoped.
- tasks: a planned unit of work inside a project; depends on other tasks via
  task_dependencies; produces an outcome when its bound job finishes.
- task_dependencies: (task_id, depends_on_id) join table for the DAG.
- outcomes: checkpoint tied to a git commit, recorded when a task-bound job
  finishes.
- settings: small kv store for runtime-mutable settings.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    permission_mode: Mapped[str] = mapped_column(String(32), default="acceptEdits")
    dangerously_skip: Mapped[bool] = mapped_column(default=False)
    extra_claude_args: Mapped[list[str]] = mapped_column(JSON, default=list)
    idle_timeout_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_default: Mapped[bool] = mapped_column(default=False)
    instructions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    context_paths: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    jobs: Mapped[list["Job"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    rules: Mapped[list["AllowlistRule"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(256), default="")
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|done|failed|stopped
    session_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    schedule_id: Mapped[Optional[str]] = mapped_column(ForeignKey("schedules.id"), nullable=True)
    task_id: Mapped[Optional[str]] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    phase: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    cwd_override: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    ended_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    project: Mapped[Project] = relationship(back_populates="jobs")
    turns: Mapped[list["Turn"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="Turn.idx"
    )


class Turn(Base):
    __tablename__ = "turns"
    __table_args__ = (UniqueConstraint("job_id", "idx", name="uq_turn_job_idx"),)

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    idx: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    pid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    exit_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Optional[float]] = mapped_column(nullable=True)
    log_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    job: Mapped[Job] = relationship(back_populates="turns")


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    cron: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class AllowlistRule(Base):
    __tablename__ = "allowlist_rules"
    __table_args__ = (UniqueConstraint("project_id", "rule", name="uq_rule_project"),)

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    project_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("projects.id"), nullable=True
    )  # null = global
    rule: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    project: Mapped[Optional[Project]] = relationship(back_populates="rules")


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="pending"
    )  # pending|ready|running|done|failed|canceled
    source: Mapped[str] = mapped_column(String(16), default="manual")  # manual|planner
    order_idx: Mapped[int] = mapped_column(Integer, default=0)
    mode: Mapped[str] = mapped_column(
        String(24), default="plan_then_execute"
    )  # plan_then_execute|one_shot
    worktree_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    worktree_branch: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    integration_status: Mapped[Optional[str]] = mapped_column(
        String(16), nullable=True
    )  # pending|integrated|conflict; null for one_shot and synthetic tasks
    synthetic: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class TaskDependency(Base):
    __tablename__ = "task_dependencies"

    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    depends_on_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )


class Outcome(Base):
    __tablename__ = "outcomes"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    commit_sha: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    branch: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="success")  # success|failed
    kind: Mapped[str] = mapped_column(String(16), default="execute")  # plan|execute|integrate
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
