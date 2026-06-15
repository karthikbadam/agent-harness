from __future__ import annotations

from pathlib import Path

from agent_harness.codex import CodexJsonParser

FIXTURES = Path(__file__).parent / "fixtures" / "codex"


def _parse(name: str) -> tuple[CodexJsonParser, list]:
    p = CodexJsonParser(job_id="j1", turn=0)
    events = p.parse_all((FIXTURES / name).read_text().splitlines())
    return p, events


def test_fresh_transcript_maps_to_events() -> None:
    p, events = _parse("fresh.jsonl")
    assert [e.type for e in events] == [
        "tool_use",
        "tool_result",
        "assistant_text",
        "turn_done",
    ]
    assert p.session_id == "019ecd2b-adc5-7fe2-8e7f-526fc9c89879"
    assert events[0].tool == "shell"
    assert events[1].ok is True
    assert "DONE" in events[2].text
    assert events[3].exit_code == 0
    assert events[3].cost_usd is None  # Codex reports no per-turn cost


def test_file_change_and_command_pair_into_tool_events() -> None:
    _, events = _parse("file_change.jsonl")
    types = [e.type for e in events]
    # agent_message, edit (file_change), shell, two results, final message, done
    assert types == [
        "assistant_text",
        "tool_use",
        "tool_result",
        "tool_use",
        "tool_result",
        "assistant_text",
        "turn_done",
    ]
    assert events[1].tool == "edit"
    assert events[3].tool == "shell"
    assert events[-1].exit_code == 0


def test_failure_transcript_is_terminal_and_ignores_noise() -> None:
    p, events = _parse("failure.jsonl")
    # The leading non-JSON "Reading additional input from stdin..." line is
    # dropped; the failed command becomes a not-ok tool_result; reasoning is
    # dropped; turn.failed becomes a non-zero turn_done.
    types = [e.type for e in events]
    assert types == ["tool_use", "tool_result", "turn_done"]
    assert events[1].ok is False  # exit_code 2
    assert events[2].exit_code == 1
    assert p.session_id == "019ecd2b-dead-7fe2-8e7f-000000000001"


def test_command_output_reaches_preview() -> None:
    _, events = _parse("failure.jsonl")
    result = events[1]
    assert "boom" in result.output_preview


def test_non_json_and_unknown_lines_are_ignored() -> None:
    p = CodexJsonParser(job_id="j", turn=0)
    assert p.feed_line("not json at all") == []
    assert p.feed_line('{"type":"turn.started"}') == []
    assert p.feed_line('{"type":"item.updated","item":{"type":"reasoning"}}') == []
    assert p.feed_line("") == []
