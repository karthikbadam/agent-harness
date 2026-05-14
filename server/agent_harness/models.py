"""SQLAlchemy 2.0 ORM models.

Tables:
- projects: a repo/working-directory + per-project permission defaults.
- jobs: a conversation (1..N turns) tied to one project.
- turns: a single `claude -p` invocation; first turn captures session_id.
- schedules: cron entries that enqueue a job when fired.
- allowlist_rules: rule strings like `Bash(npm test:*)`; global or project-scoped.
- push_subscriptions: browser PushSubscription records (endpoint + keys).
- settings: small kv store for runtime-mutable settings.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
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


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    p256dh: Mapped[str] = mapped_column(Text, nullable=False)
    auth: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(String(64), default="iPhone")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
