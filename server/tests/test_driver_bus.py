from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent_harness import models
from agent_harness.db import session_scope
from agent_harness.services import driver_bus


@pytest.fixture(autouse=True)
def _fresh_bus() -> None:
    driver_bus.reset_bus()
    yield
    driver_bus.reset_bus()


async def test_no_subscribers_is_noop(initdb: Path) -> None:
    bus = driver_bus.get_bus()
    bus.emit("task_ready", "proj-x")  # would normally check mode; just returns
    assert bus.subscriber_count() == 0


async def test_mode_off_emit_is_suppressed_at_source(initdb: Path) -> None:
    with session_scope() as s:
        s.add(models.Project(id="proj-off", name="p", path="/tmp", autopilot_mode="off"))
    bus = driver_bus.get_bus()
    q = bus.subscribe()
    bus.emit("task_ready", "proj-off")
    await asyncio.sleep(0.01)
    assert q.qsize() == 0


async def test_mode_on_emit_delivers(initdb: Path) -> None:
    with session_scope() as s:
        s.add(models.Project(id="proj-on", name="p", path="/tmp", autopilot_mode="on"))
    bus = driver_bus.get_bus()
    q = bus.subscribe()
    bus.emit("task_ready", "proj-on", task_id="t1")
    evt = await asyncio.wait_for(q.get(), timeout=1.0)
    assert evt.event == "task_ready"
    assert evt.project_id == "proj-on"
    assert evt.task_id == "t1"


async def test_force_bypasses_mode_gate(initdb: Path) -> None:
    with session_scope() as s:
        s.add(models.Project(id="proj-off2", name="p", path="/tmp", autopilot_mode="off"))
    bus = driver_bus.get_bus()
    q = bus.subscribe()
    bus.emit("mode_off", "proj-off2", force=True)
    evt = await asyncio.wait_for(q.get(), timeout=1.0)
    assert evt.event == "mode_off"


async def test_second_subscriber_rejected(initdb: Path) -> None:
    bus = driver_bus.get_bus()
    bus.subscribe()
    with pytest.raises(RuntimeError, match="already connected"):
        bus.subscribe()


async def test_unsubscribe_allows_new_subscriber(initdb: Path) -> None:
    bus = driver_bus.get_bus()
    q1 = bus.subscribe()
    bus.unsubscribe(q1)
    q2 = bus.subscribe()  # no longer raises
    assert bus.subscriber_count() == 1
    bus.unsubscribe(q2)
