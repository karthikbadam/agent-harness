from __future__ import annotations

from pathlib import Path

from agent_harness import models
from agent_harness.db import session_scope


def test_roundtrip_project_job_turn(initdb: Path) -> None:
    with session_scope() as s:
        proj = models.Project(name="book", path="/tmp/book")
        s.add(proj)
        s.flush()
        job = models.Job(project_id=proj.id, title="chapter 0")
        s.add(job)
        s.flush()
        turn = models.Turn(job_id=job.id, idx=0, prompt="hello", status="queued")
        s.add(turn)
        s.flush()
        pid = proj.id
        jid = job.id

    with session_scope() as s:
        proj = s.get(models.Project, pid)
        assert proj is not None
        assert proj.name == "book"
        assert proj.permission_mode == "acceptEdits"
        assert proj.dangerously_skip is False
        job = s.get(models.Job, jid)
        assert job is not None
        assert job.status == "queued"
        assert len(job.turns) == 1
        assert job.turns[0].prompt == "hello"


def test_allowlist_rule_global_and_project(initdb: Path) -> None:
    with session_scope() as s:
        proj = models.Project(name="book", path="/tmp/book")
        s.add(proj)
        s.flush()
        s.add(models.AllowlistRule(rule="Bash(npm test:*)"))  # global
        s.add(models.AllowlistRule(project_id=proj.id, rule="Edit(**/*.py)"))
        s.flush()
        rules = s.query(models.AllowlistRule).all()
        assert {r.rule for r in rules} == {"Bash(npm test:*)", "Edit(**/*.py)"}


def test_schedule_and_push_sub(initdb: Path) -> None:
    with session_scope() as s:
        proj = models.Project(name="book", path="/tmp/book")
        s.add(proj)
        s.flush()
        sched = models.Schedule(
            project_id=proj.id, name="daily", cron="0 9 * * *", prompt="write"
        )
        s.add(sched)
        sub = models.PushSubscription(
            endpoint="https://example.com/p/1",
            p256dh="aaa",
            auth="bbb",
        )
        s.add(sub)
        s.flush()
        assert sched.enabled is True
        assert sub.label == "iPhone"
