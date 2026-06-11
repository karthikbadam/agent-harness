from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from agent_harness import models
from agent_harness.db import session_scope
from agent_harness.routes.tasks import merge_tasks, split_task
from agent_harness.schemas import MergeIn, SplitIn, SplitTaskItem


def _make_task(
    s: Session,
    project_id: str,
    title: str,
    status: str = "pending",
    order_idx: int = 0,
) -> str:
    t = models.Task(
        project_id=project_id,
        title=title,
        prompt=f"do {title}",
        status=status,
        order_idx=order_idx,
    )
    s.add(t)
    s.flush()
    return t.id


def _add_dep(s: Session, task_id: str, depends_on: str) -> None:
    s.add(models.TaskDependency(task_id=task_id, depends_on_id=depends_on))
    s.flush()


def _deps(s: Session, task_id: str) -> set[str]:
    rows = s.execute(
        models.TaskDependency.__table__.select().where(models.TaskDependency.task_id == task_id)
    ).all()
    return {r[1] for r in rows}  # (task_id, depends_on_id)


def test_split_inherits_incoming_chains_in_series_and_rewires_outgoing(
    initdb: Path,
) -> None:
    with session_scope() as s:
        proj = models.Project(name="r", path="/tmp")
        s.add(proj)
        s.flush()
        upstream = _make_task(s, proj.id, "u", status="done")
        target = _make_task(s, proj.id, "T", status="pending")
        downstream = _make_task(s, proj.id, "d", status="pending")
        _add_dep(s, target, upstream)
        _add_dep(s, downstream, target)

    with session_scope() as s:
        out = split_task(
            target,
            SplitIn(
                new_tasks=[
                    SplitTaskItem(title="T1a", prompt="p1"),
                    SplitTaskItem(title="T1b", prompt="p2"),
                ],
                inherit_deps_in=True,
                link_in_series=True,
            ),
            s,
        )
    new_ids = [t.id for t in out]
    assert len(new_ids) == 2

    with session_scope() as s:
        assert s.get(models.Task, target) is None
        a, b = new_ids
        assert _deps(s, a) == {upstream}
        assert _deps(s, b) == {a}
        assert _deps(s, downstream) == {b}
        assert s.get(models.Task, a).status == "ready"
        assert s.get(models.Task, b).status == "pending"


def test_split_rejects_non_pending_ready(initdb: Path) -> None:
    with session_scope() as s:
        proj = models.Project(name="r", path="/tmp")
        s.add(proj)
        s.flush()
        tid = _make_task(s, proj.id, "t", status="running")

    with session_scope() as s:
        with pytest.raises(HTTPException) as exc:
            split_task(
                tid,
                SplitIn(new_tasks=[SplitTaskItem(title="a", prompt="x")]),
                s,
            )
        assert exc.value.status_code == 409


def test_merge_unions_deps_and_rewires_downstream(initdb: Path) -> None:
    with session_scope() as s:
        proj = models.Project(name="r", path="/tmp")
        s.add(proj)
        s.flush()
        up_a = _make_task(s, proj.id, "ua", status="done")
        up_b = _make_task(s, proj.id, "ub", status="done")
        a = _make_task(s, proj.id, "A", status="pending")
        b = _make_task(s, proj.id, "B", status="pending")
        down = _make_task(s, proj.id, "down", status="pending")
        _add_dep(s, a, up_a)
        _add_dep(s, b, up_b)
        _add_dep(s, down, a)
        _add_dep(s, down, b)

    with session_scope() as s:
        merged = merge_tasks(MergeIn(task_ids=[a, b], title="AB", prompt="combined"), s)
    merged_id = merged.id

    with session_scope() as s:
        assert s.get(models.Task, a) is None
        assert s.get(models.Task, b) is None
        assert _deps(s, merged_id) == {up_a, up_b}
        assert _deps(s, down) == {merged_id}
        assert s.get(models.Task, merged_id).status == "ready"
        assert s.get(models.Task, down).status == "pending"


def test_merge_rejects_input_depending_on_other_input(initdb: Path) -> None:
    with session_scope() as s:
        proj = models.Project(name="r", path="/tmp")
        s.add(proj)
        s.flush()
        a = _make_task(s, proj.id, "A", status="pending")
        b = _make_task(s, proj.id, "B", status="pending")
        _add_dep(s, b, a)

    with session_scope() as s:
        with pytest.raises(HTTPException) as exc:
            merge_tasks(MergeIn(task_ids=[a, b], title="AB", prompt="x"), s)
        assert exc.value.status_code == 400


def test_merge_rejects_non_pending(initdb: Path) -> None:
    with session_scope() as s:
        proj = models.Project(name="r", path="/tmp")
        s.add(proj)
        s.flush()
        a = _make_task(s, proj.id, "A", status="ready")
        b = _make_task(s, proj.id, "B", status="pending")

    with session_scope() as s:
        with pytest.raises(HTTPException) as exc:
            merge_tasks(MergeIn(task_ids=[a, b], title="AB", prompt="x"), s)
        assert exc.value.status_code == 409
