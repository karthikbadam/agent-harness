from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_harness.broadcaster import Broadcaster, BroadcasterRegistry, make_status_event
from agent_harness.schemas import AssistantTextEvent, ToolUseEvent


def _text(job_id: str, turn: int, body: str) -> AssistantTextEvent:
    return AssistantTextEvent(
        job_id=job_id, turn=turn, ts=datetime.now(timezone.utc), text=body
    )


async def _collect(it, n: int) -> list:
    out = []
    async for ev in it:
        out.append(ev)
        if len(out) >= n:
            break
    return out


async def test_publish_assigns_monotonic_seq_and_persists(tmp_path: Path) -> None:
    b = Broadcaster("j1", tmp_path)
    b.start_turn(0)
    await b.publish(_text("j1", 0, "a"))
    await b.publish(_text("j1", 0, "b"))
    await b.publish(_text("j1", 0, "c"))
    assert b.seq == 3
    lines = (tmp_path / "turn-0.jsonl").read_text().strip().splitlines()
    assert len(lines) == 3


async def test_subscribe_replays_then_goes_live(tmp_path: Path) -> None:
    b = Broadcaster("j1", tmp_path)
    b.start_turn(0)
    await b.publish(_text("j1", 0, "one"))
    await b.publish(_text("j1", 0, "two"))

    got: list = []

    async def consumer() -> None:
        async for ev in b.subscribe():
            got.append(ev)
            if len(got) >= 4:
                break

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0.05)  # let replay run
    await b.publish(_text("j1", 0, "three"))
    await b.publish(_text("j1", 0, "four"))
    await asyncio.wait_for(task, timeout=2)
    assert [e.text for e in got] == ["one", "two", "three", "four"]
    assert [e.seq for e in got] == [1, 2, 3, 4]


async def test_subscribe_honors_last_event_id(tmp_path: Path) -> None:
    b = Broadcaster("j1", tmp_path)
    b.start_turn(0)
    for i in range(5):
        await b.publish(_text("j1", 0, f"m{i}"))

    got: list = []

    async def consumer() -> None:
        async for ev in b.subscribe(last_event_id=3):
            got.append(ev)
            if len(got) >= 2:
                break

    await asyncio.wait_for(consumer(), timeout=2)
    assert [e.seq for e in got] == [4, 5]


async def test_two_subscribers_both_get_live_events(tmp_path: Path) -> None:
    b = Broadcaster("j1", tmp_path)
    b.start_turn(0)
    got_a: list = []
    got_b: list = []

    async def consume(target: list, n: int) -> None:
        async for ev in b.subscribe():
            target.append(ev)
            if len(target) >= n:
                break

    t1 = asyncio.create_task(consume(got_a, 2))
    t2 = asyncio.create_task(consume(got_b, 2))
    await asyncio.sleep(0.05)
    await b.publish(_text("j1", 0, "x"))
    await b.publish(_text("j1", 0, "y"))
    await asyncio.gather(t1, t2)
    assert [e.text for e in got_a] == ["x", "y"]
    assert [e.text for e in got_b] == ["x", "y"]


async def test_seq_continues_across_turns(tmp_path: Path) -> None:
    b = Broadcaster("j1", tmp_path)
    b.start_turn(0)
    await b.publish(_text("j1", 0, "a"))
    await b.publish(_text("j1", 0, "b"))
    b.start_turn(1)
    await b.publish(_text("j1", 1, "c"))
    assert b.seq == 3
    assert (tmp_path / "turn-0.jsonl").read_text().count("\n") == 2
    assert (tmp_path / "turn-1.jsonl").read_text().count("\n") == 1


async def test_new_broadcaster_restores_seq_from_disk(tmp_path: Path) -> None:
    b = Broadcaster("j1", tmp_path)
    b.start_turn(0)
    for _ in range(7):
        await b.publish(_text("j1", 0, "x"))
    # Simulate restart.
    b2 = Broadcaster("j1", tmp_path)
    assert b2.seq == 7


def test_registry_caches_by_job_id(tmp_path: Path) -> None:
    reg = BroadcasterRegistry(tmp_path)
    b1 = reg.get("j1")
    b2 = reg.get("j1")
    assert b1 is b2
    b3 = reg.get("j2")
    assert b3 is not b1
    assert (tmp_path / "jobs" / "j1").is_dir()
    assert (tmp_path / "jobs" / "j2").is_dir()


def test_status_event_factory() -> None:
    ev = make_status_event("j1", 0, "running")
    assert ev.type == "job_status"
    assert ev.status == "running"
