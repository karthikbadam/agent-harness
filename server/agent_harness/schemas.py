"""Pydantic v2 schemas — single source of truth for both Python and TS.

The frontend's `types/api.ts` is generated from FastAPI's OpenAPI dump of these
models. Add a field here → regen → it appears in TS. Don't define DTOs in
routes; keep them all here so the generator picks them up.

`StreamEvent` is a Pydantic discriminated union over `type`. Adding a new event
type here is the only place to edit; the parser + UI follow.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


# ----------------------------- Stream events ------------------------------- #


class _EventBase(BaseModel):
    model_config = ConfigDict(extra="ignore")

    job_id: str
    turn: int
    ts: datetime
    seq: int = 0  # monotonic per-job event id; assigned by broadcaster


class ToolUseEvent(_EventBase):
    type: Literal["tool_use"] = "tool_use"
    tool: str
    input: dict[str, object] = Field(default_factory=dict)


class ToolResultEvent(_EventBase):
    type: Literal["tool_result"] = "tool_result"
    ok: bool
    output_preview: str = ""


class AssistantTextEvent(_EventBase):
    type: Literal["assistant_text"] = "assistant_text"
    text: str


class TurnDoneEvent(_EventBase):
    type: Literal["turn_done"] = "turn_done"
    exit_code: int
    cost_usd: float | None = None
    duration_ms: int | None = None


class JobStatusEvent(_EventBase):
    type: Literal["job_status"] = "job_status"
    status: Literal["queued", "running", "done", "failed", "stopped"]


StreamEvent = Annotated[
    Union[
        ToolUseEvent,
        ToolResultEvent,
        AssistantTextEvent,
        TurnDoneEvent,
        JobStatusEvent,
    ],
    Field(discriminator="type"),
]


# --------------------------------- DTOs ------------------------------------ #


class ProjectCreate(BaseModel):
    name: str
    path: str
    permission_mode: Literal["acceptEdits", "plan", "default"] = "acceptEdits"
    dangerously_skip: bool = False
    extra_claude_args: list[str] = Field(default_factory=list)
    idle_timeout_seconds: int | None = None
    is_default: bool = False
    instructions: str | None = None
    skills: list[str] = Field(default_factory=list)
    context_paths: list[str] = Field(default_factory=list)


class ProjectUpdate(BaseModel):
    name: str | None = None
    path: str | None = None
    permission_mode: Literal["acceptEdits", "plan", "default"] | None = None
    dangerously_skip: bool | None = None
    extra_claude_args: list[str] | None = None
    idle_timeout_seconds: int | None = None
    is_default: bool | None = None
    instructions: str | None = None
    skills: list[str] | None = None
    context_paths: list[str] | None = None


class ProjectOut(BaseModel):
    id: str
    name: str
    path: str
    permission_mode: str
    dangerously_skip: bool
    extra_claude_args: list[str] = Field(default_factory=list)
    idle_timeout_seconds: int | None = None
    is_default: bool = False
    instructions: str | None = None
    skills: list[str] = Field(default_factory=list)
    context_paths: list[str] = Field(default_factory=list)
    created_at: datetime


class TurnOut(BaseModel):
    id: str
    idx: int
    prompt: str
    status: str
    exit_code: int | None = None
    cost_usd: float | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None


class JobOut(BaseModel):
    id: str
    project_id: str
    title: str
    status: str
    session_id: str | None = None
    schedule_id: str | None = None
    task_id: str | None = None
    phase: str | None = None
    created_at: datetime
    ended_at: datetime | None = None
    turns: list[TurnOut] = Field(default_factory=list)


class JobCreate(BaseModel):
    prompt: str
    project_id: str | None = None
    title: str | None = None


class FollowupCreate(BaseModel):
    prompt: str


class ScheduleCreate(BaseModel):
    project_id: str
    name: str
    cron: str
    prompt: str
    enabled: bool = True


class ScheduleUpdate(BaseModel):
    name: str | None = None
    cron: str | None = None
    prompt: str | None = None
    enabled: bool | None = None


class ScheduleOut(BaseModel):
    id: str
    project_id: str
    name: str
    cron: str
    prompt: str
    enabled: bool
    created_at: datetime


class AllowlistRuleCreate(BaseModel):
    rule: str
    project_id: str | None = None


class AllowlistRuleOut(BaseModel):
    id: str
    rule: str
    project_id: str | None
    created_at: datetime


class TaskCreate(BaseModel):
    title: str
    prompt: str
    depends_on: list[str] = Field(default_factory=list)
    order_idx: int = 0


class TaskUpdate(BaseModel):
    title: str | None = None
    prompt: str | None = None
    depends_on: list[str] | None = None
    order_idx: int | None = None


class TaskOut(BaseModel):
    id: str
    project_id: str
    title: str
    prompt: str
    status: str
    source: str
    order_idx: int
    mode: str = "plan_then_execute"
    worktree_path: str | None = None
    worktree_branch: str | None = None
    integration_status: str | None = None
    synthetic: bool = False
    depends_on: list[str] = Field(default_factory=list)
    latest_outcome_id: str | None = None
    created_at: datetime
    updated_at: datetime


class OutcomeOut(BaseModel):
    id: str
    task_id: str
    job_id: str
    commit_sha: str | None
    branch: str | None
    summary: str | None
    status: str
    kind: str = "execute"
    created_at: datetime


class PlanCreate(BaseModel):
    ask: str


class PlanOut(BaseModel):
    task_ids: list[str]
    raw: str | None = None  # raw model output when parsing failed
    error: str | None = None


class AuthInfo(BaseModel):
    """Returned to authenticated clients on /api/me."""

    ok: bool = True


class ErrorOut(BaseModel):
    detail: str
