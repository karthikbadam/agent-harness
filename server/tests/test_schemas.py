from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from agent_harness import schemas


_StreamAdapter: TypeAdapter[schemas.StreamEvent] = TypeAdapter(schemas.StreamEvent)


def _base() -> dict[str, object]:
    return {"job_id": "j1", "turn": 0, "ts": datetime.now(timezone.utc).isoformat()}


def test_discriminated_union_routes_by_type() -> None:
    e = _StreamAdapter.validate_python({**_base(), "type": "tool_use", "tool": "Bash", "input": {"cmd": "ls"}})
    assert isinstance(e, schemas.ToolUseEvent)
    assert e.tool == "Bash"

    e = _StreamAdapter.validate_python({**_base(), "type": "assistant_text", "text": "hi"})
    assert isinstance(e, schemas.AssistantTextEvent)

    e = _StreamAdapter.validate_python({**_base(), "type": "turn_done", "exit_code": 0, "cost_usd": 0.01})
    assert isinstance(e, schemas.TurnDoneEvent)
    assert e.cost_usd == 0.01

    e = _StreamAdapter.validate_python(
        {**_base(), "type": "tool_blocked", "tool": "Bash", "reason": "no perm"}
    )
    assert isinstance(e, schemas.ToolBlockedEvent)


def test_unknown_event_type_rejected() -> None:
    with pytest.raises(ValidationError):
        _StreamAdapter.validate_python({**_base(), "type": "wat"})


def test_project_create_default_permission_mode() -> None:
    p = schemas.ProjectCreate(name="book", path="/tmp/book")
    assert p.permission_mode == "acceptEdits"
    assert p.dangerously_skip is False


def test_schedule_create_required_fields() -> None:
    s = schemas.ScheduleCreate(project_id="p1", name="daily", cron="0 9 * * *", prompt="hi")
    assert s.enabled is True
