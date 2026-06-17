from __future__ import annotations

from pathlib import Path

import pytest

from agent_harness import models
from agent_harness.broadcaster import BroadcasterRegistry
from agent_harness.db import session_scope
from agent_harness.jobs import JobManager, resolve_provider


# ------------------------------ resolve_provider --------------------------- #


@pytest.mark.parametrize(
    "setting,kind,expected",
    [
        ("claude", "plan", "claude"),
        ("claude", "execute", "claude"),
        ("codex", "plan", "codex"),
        ("codex", "execute", "codex"),
        ("auto", "plan", "claude"),
        ("auto", "execute", "codex"),
        ("auto", "integrate", "codex"),
        ("auto", "ad_hoc", "codex"),
        (None, "execute", "claude"),
        ("bogus", "execute", "claude"),
    ],
)
def test_resolve_provider(setting: str | None, kind: str, expected: str) -> None:
    assert resolve_provider(setting, kind) == expected


# ------------------------- create_job stores provider ---------------------- #


def _project(agent_provider: str = "claude") -> str:
    with session_scope() as s:
        p = models.Project(name="p", path="/tmp", agent_provider=agent_provider)
        s.add(p)
        s.flush()
        return p.id


def _job_provider(job_id: str) -> str:
    with session_scope() as s:
        j = s.get(models.Job, job_id)
        assert j is not None
        return j.agent_provider


def _mgr(ah_home: Path) -> JobManager:
    return JobManager(BroadcasterRegistry(ah_home / "logs"))


def test_create_job_inherits_project_codex(initdb: Path) -> None:
    pid = _project("codex")
    jid = _mgr(initdb).create_job(pid, "hi")
    assert _job_provider(jid) == "codex"


def test_create_job_auto_adhoc_resolves_codex(initdb: Path) -> None:
    pid = _project("auto")
    jid = _mgr(initdb).create_job(pid, "hi")  # ad_hoc
    assert _job_provider(jid) == "codex"


def test_create_job_default_project_is_claude(initdb: Path) -> None:
    pid = _project()  # claude
    jid = _mgr(initdb).create_job(pid, "hi")
    assert _job_provider(jid) == "claude"


def test_create_job_arg_override_wins(initdb: Path) -> None:
    pid = _project("claude")
    jid = _mgr(initdb).create_job(pid, "hi", agent_provider="codex")
    assert _job_provider(jid) == "codex"


def test_create_job_task_override_beats_project(initdb: Path) -> None:
    pid = _project("claude")
    with session_scope() as s:
        t = models.Task(
            project_id=pid, title="t", prompt="p", mode="one_shot", agent_provider="codex"
        )
        s.add(t)
        s.flush()
        tid = t.id
    # one_shot task → kind=execute; task override 'codex' beats project 'claude'.
    jid = _mgr(initdb).create_job(pid, "hi", task_id=tid)
    assert _job_provider(jid) == "codex"


def test_planner_propagates_provider_to_children(initdb: Path) -> None:
    from agent_harness.services.planner import _propagate_provider

    pid = _project("claude")
    with session_scope() as s:
        plan = models.Task(
            project_id=pid, title="Plan", prompt="x", mode="plan", agent_provider="codex"
        )
        s.add(plan)
        s.flush()
        plan_id = plan.id
        kids = []
        for i in range(2):
            t = models.Task(
                project_id=pid, title=f"c{i}", prompt="p", mode="execute_only", source="planner"
            )
            s.add(t)
            s.flush()
            kids.append(t.id)
    _propagate_provider(plan_id, kids)
    with session_scope() as s:
        for kid in kids:
            assert s.get(models.Task, kid).agent_provider == "codex"


def test_planner_propagate_noop_when_plan_has_no_override(initdb: Path) -> None:
    from agent_harness.services.planner import _propagate_provider

    pid = _project("claude")
    with session_scope() as s:
        plan = models.Task(project_id=pid, title="Plan", prompt="x", mode="plan")  # no override
        s.add(plan)
        s.flush()
        plan_id = plan.id
        t = models.Task(project_id=pid, title="c", prompt="p", source="planner")
        s.add(t)
        s.flush()
        kid = t.id
    _propagate_provider(plan_id, [kid])
    with session_scope() as s:
        assert s.get(models.Task, kid).agent_provider is None  # inherit project default


def test_create_job_auto_plan_phase_resolves_claude(initdb: Path) -> None:
    pid = _project("auto")
    with session_scope() as s:
        t = models.Task(project_id=pid, title="t", prompt="p", mode="plan_then_execute")
        s.add(t)
        s.flush()
        tid = t.id
    # plan_then_execute task → first job kind=plan → auto resolves to claude.
    jid = _mgr(initdb).create_job(pid, "hi", task_id=tid)
    assert _job_provider(jid) == "claude"
