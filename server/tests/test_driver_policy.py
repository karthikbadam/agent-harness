from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_harness import models
from agent_harness.db import session_scope
from agent_harness.services import driver_policy


def _proj(s, mode: str = "on") -> str:
    p = models.Project(name="p", path="/tmp", autopilot_mode=mode)
    s.add(p)
    s.flush()
    return p.id


def _task(
    s,
    project_id: str,
    title: str,
    status: str = "pending",
    integration_status: str | None = None,
    synthetic: bool = False,
    retries: int = 0,
    last_failed_at: datetime | None = None,
    mode: str = "plan_then_execute",
) -> str:
    t = models.Task(
        project_id=project_id,
        title=title,
        prompt=title,
        status=status,
        integration_status=integration_status,
        synthetic=synthetic,
        retries=retries,
        last_failed_at=last_failed_at,
        mode=mode,
    )
    s.add(t)
    s.flush()
    return t.id


def _dep(s, task_id: str, depends_on: str) -> None:
    s.add(models.TaskDependency(task_id=task_id, depends_on_id=depends_on))
    s.flush()


def _job(s, project_id: str, task_id: str, phase: str | None = None) -> str:
    j = models.Job(project_id=project_id, task_id=task_id, phase=phase)
    s.add(j)
    s.flush()
    return j.id


def test_ack_prioritized_over_run(initdb: Path) -> None:
    with session_scope() as s:
        pid = _proj(s)
        t_run = _task(s, pid, "t_run", status="ready")
        t_ack = _task(s, pid, "t_ack", status="running")
        jid = _job(s, pid, t_ack, phase="awaiting_ack")
        actions = driver_policy.next_actions(s, pid)
    assert [a.kind for a in actions] == ["ack", "run"]
    assert actions[0].job_id == jid


def test_retry_respects_backoff(initdb: Path) -> None:
    with session_scope() as s:
        pid = _proj(s)
        # Recently failed → still in backoff, should NOT retry.
        recent = _task(
            s, pid, "recent", status="failed",
            retries=0, last_failed_at=datetime.now(timezone.utc),
        )
        # Long-ago failed → past backoff, should retry.
        long_ago = _task(
            s, pid, "long", status="failed",
            retries=0,
            last_failed_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        actions = driver_policy.next_actions(s, pid)
    retried = [a.task_id for a in actions if a.kind == "retry"]
    assert long_ago in retried
    assert recent not in retried


def test_retry_stops_at_max(initdb: Path) -> None:
    with session_scope() as s:
        pid = _proj(s)
        _task(
            s, pid, "exhausted", status="failed",
            retries=driver_policy.MAX_RETRIES,
            last_failed_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        actions = driver_policy.next_actions(s, pid)
    assert not [a for a in actions if a.kind == "retry"]


def test_wave_picks_mergeable_tasks(initdb: Path) -> None:
    with session_scope() as s:
        pid = _proj(s)
        # T1 done+pending, no deps → in wave
        t1 = _task(s, pid, "t1", status="done", integration_status="pending")
        # T2 done+pending, no deps → in wave
        t2 = _task(s, pid, "t2", status="done", integration_status="pending")
        # T3 done+pending but depends on T1 which is not yet integrated → NOT in wave
        t3 = _task(s, pid, "t3", status="done", integration_status="pending")
        _dep(s, t3, t1)
        actions = driver_policy.next_actions(s, pid)
    integrates = [a for a in actions if a.kind == "integrate"]
    assert len(integrates) == 1
    ids = set(integrates[0].payload["task_ids"])
    assert ids == {t1, t2}


def test_wave_advances_after_integration(initdb: Path) -> None:
    with session_scope() as s:
        pid = _proj(s)
        t1 = _task(s, pid, "t1", status="done", integration_status="integrated")
        t3 = _task(s, pid, "t3", status="done", integration_status="pending")
        _dep(s, t3, t1)
        actions = driver_policy.next_actions(s, pid)
    integrates = [a for a in actions if a.kind == "integrate"]
    assert len(integrates) == 1
    assert integrates[0].payload["task_ids"] == [t3]


def test_no_integrate_while_one_is_running(initdb: Path) -> None:
    with session_scope() as s:
        pid = _proj(s)
        # An existing synthetic integration task that is currently running.
        _task(s, pid, "int", status="running", synthetic=True, mode="one_shot")
        _task(s, pid, "t1", status="done", integration_status="pending")
        actions = driver_policy.next_actions(s, pid)
    assert not [a for a in actions if a.kind == "integrate"]


def test_run_bounded_by_parallel_cap(initdb: Path) -> None:
    with session_scope() as s:
        pid = _proj(s)
        # 1 running, cap=2 → 1 slot left
        _task(s, pid, "active", status="running")
        _task(s, pid, "r1", status="ready")
        _task(s, pid, "r2", status="ready")
        actions = driver_policy.next_actions(s, pid, parallel_cap=2)
    runs = [a for a in actions if a.kind == "run"]
    assert len(runs) == 1


def test_max_actions_caps_output(initdb: Path) -> None:
    with session_scope() as s:
        pid = _proj(s)
        for i in range(5):
            _task(s, pid, f"r{i}", status="ready")
        actions = driver_policy.next_actions(s, pid, max_actions=3, parallel_cap=10)
    assert len(actions) == 3
