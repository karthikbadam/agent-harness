from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_harness import schemas
from agent_harness.claude import StreamJsonParser, _suggest_rule_for


FIXTURES = Path(__file__).parent / "fixtures" / "stream"


def _read(name: str) -> list[str]:
    return (FIXTURES / name).read_text().splitlines()


def _types(events: list[schemas.StreamEvent]) -> list[str]:
    return [e.type for e in events]


def test_text_only_parses_assistant_text_and_turn_done() -> None:
    p = StreamJsonParser(job_id="j", turn=0)
    events = p.parse_all(_read("text_only.jsonl"))
    assert _types(events) == ["assistant_text", "assistant_text", "turn_done"]
    assert p.session_id == "sess_abc"
    done = events[-1]
    assert isinstance(done, schemas.TurnDoneEvent)
    assert done.exit_code == 0
    assert done.cost_usd == 0.0123
    assert done.duration_ms == 1234


def test_tool_use_then_result_pair() -> None:
    p = StreamJsonParser(job_id="j", turn=0)
    events = p.parse_all(_read("tool_use_ok.jsonl"))
    assert _types(events) == [
        "assistant_text",
        "tool_use",
        "tool_result",
        "assistant_text",
        "turn_done",
    ]
    tu = events[1]
    assert isinstance(tu, schemas.ToolUseEvent)
    assert tu.tool == "Bash"
    assert tu.input == {"command": "ls -la"}
    tr = events[2]
    assert isinstance(tr, schemas.ToolResultEvent)
    assert tr.ok is True
    assert "total 0" in tr.output_preview


def test_permission_blocked_emits_tool_blocked() -> None:
    p = StreamJsonParser(job_id="j", turn=0)
    events = p.parse_all(_read("tool_blocked.jsonl"))
    assert "tool_blocked" in _types(events)
    blocked = next(e for e in events if e.type == "tool_blocked")
    assert isinstance(blocked, schemas.ToolBlockedEvent)
    assert blocked.tool == "Bash"
    assert blocked.suggested_rule == "Bash(pytest:*)"
    done = next(e for e in events if e.type == "turn_done")
    assert isinstance(done, schemas.TurnDoneEvent)
    assert done.exit_code == 1


def test_mixed_content_blocks_and_unknown_types_ignored() -> None:
    p = StreamJsonParser(job_id="j", turn=0)
    events = p.parse_all(_read("mixed.jsonl"))
    # Two text blocks in one assistant message, then a tool_use, then a
    # rich-content tool_result, then turn_done. Unknown event dropped.
    assert _types(events) == [
        "assistant_text",
        "assistant_text",
        "tool_use",
        "tool_result",
        "turn_done",
    ]
    tr = events[3]
    assert isinstance(tr, schemas.ToolResultEvent)
    assert tr.output_preview == "edit applied"


def test_bad_json_line_skipped() -> None:
    p = StreamJsonParser(job_id="j", turn=0)
    assert p.feed_line("not json") == []
    assert p.feed_line("") == []
    assert p.feed_line("   ") == []


@pytest.mark.parametrize(
    "tool, tool_input, expected",
    [
        ("Bash", {"command": "pytest -q"}, "Bash(pytest:*)"),
        ("Bash", {"command": ""}, "Bash(*)"),
        ("Edit", {"file_path": "/tmp/a.py"}, "Edit(**/*.py)"),
        ("Write", {"file_path": "x.tsx"}, "Write(**/*.tsx)"),
        ("Edit", {"file_path": "no-ext"}, "Edit(*)"),
        ("WeirdTool", {}, "WeirdTool(*)"),
    ],
)
def test_suggest_rule(tool: str, tool_input: dict, expected: str) -> None:
    assert _suggest_rule_for(tool, tool_input) == expected


def test_seq_default_zero_until_broadcaster_assigns() -> None:
    p = StreamJsonParser(job_id="j", turn=0)
    events = p.parse_all(_read("text_only.jsonl"))
    assert all(e.seq == 0 for e in events)


def test_real_shape_thinking_and_rate_limit_dropped() -> None:
    """Verified against actual `claude` CLI output: thinking blocks, rate_limit_event,
    and hook_started/hook_response system events are all silently dropped."""
    p = StreamJsonParser(job_id="j", turn=0)
    events = p.parse_all(_read("real_shape.jsonl"))
    assert _types(events) == ["assistant_text", "tool_use", "tool_result", "turn_done"]
    assert p.session_id == "uuid-real"
    done = events[-1]
    assert isinstance(done, schemas.TurnDoneEvent)
    assert done.cost_usd == 0.065
    assert done.duration_ms == 5891


def test_replay_jsonl_round_trips_through_pydantic() -> None:
    """Persisted jsonl (one parsed event per line) round-trips via discriminated union."""
    p = StreamJsonParser(job_id="j", turn=0)
    events = p.parse_all(_read("tool_use_ok.jsonl"))
    serialized = [e.model_dump_json() for e in events]
    from pydantic import TypeAdapter

    adapter: TypeAdapter[schemas.StreamEvent] = TypeAdapter(schemas.StreamEvent)
    restored = [adapter.validate_json(s) for s in serialized]
    assert [e.type for e in restored] == [e.type for e in events]
