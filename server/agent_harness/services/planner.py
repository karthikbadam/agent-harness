"""Planner: turn a high-level ask into a list of draft tasks.

Implementation strategy: spawn a one-off job in the project using `JobManager`,
prompt claude to emit a strict JSON task list, then parse the assistant_text
events from the resulting log. Tasks are inserted with `source='planner'` and
`status='pending'` so the user can edit/confirm them before running.

We piggy-back on the regular job machinery so the planning conversation is
visible in the Jobs UI (and is captured under `~/.agent-harness/logs/jobs/<id>/`
just like any other job).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from .. import models
from ..db import session_scope
from ..jobs import JobManager

log = logging.getLogger(__name__)


PLANNER_INSTRUCTIONS = """\
You are a planning assistant. The user will describe a high-level ask; your
job is to decompose it into a short, ordered list of concrete tasks.

Return ONLY a JSON array of objects with these fields (no surrounding prose,
no Markdown fence — just the JSON):

  [
    {
      "title": "<short imperative title, <= 80 chars>",
      "prompt": "<the exact prompt that should be sent to claude when this task runs>",
      "depends_on_titles": ["<title of an earlier task>", ...]
    },
    ...
  ]

Rules:
- 1 to 8 tasks. Prefer fewer, more substantial tasks.
- Tasks should be small enough to checkpoint with a single git commit.
- `depends_on_titles` may be empty; if present, every entry MUST match an
  earlier task's exact title.
- Do not include any commentary outside the JSON array.

The user's ask follows.
"""


def _extract_json_array(text: str) -> list[dict] | None:
    """Best-effort JSON-array extractor. Returns None on failure."""
    if not text:
        return None
    # Direct parse first.
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
    except Exception:  # noqa: BLE001
        pass
    # Strip code fences.
    fence = re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", text)
    if fence:
        try:
            return json.loads(fence.group(1))
        except Exception:  # noqa: BLE001
            pass
    # Fall back to the first bracketed array.
    m = re.search(r"(\[[\s\S]*\])", text)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:  # noqa: BLE001
            pass
    return None


def _all_assistant_text(log_dir: Path) -> str:
    if not log_dir.is_dir():
        return ""
    pieces: list[str] = []
    for f in sorted(log_dir.glob("turn-*.jsonl")):
        try:
            with f.open("rb") as fh:
                for raw in fh:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line or '"assistant_text"' not in line:
                        continue
                    try:
                        ev = json.loads(line)
                    except Exception:  # noqa: BLE001
                        continue
                    if ev.get("type") != "assistant_text":
                        continue
                    t = ev.get("text")
                    if isinstance(t, str):
                        pieces.append(t)
        except Exception:  # noqa: BLE001
            continue
    return "\n".join(pieces)


async def plan(
    project_id: str,
    ask: str,
    job_manager: JobManager,
    log_root: Path,
) -> tuple[list[str], str | None, str | None]:
    """Run the planner and insert draft tasks.

    Returns (task_ids, raw_output_or_None, error_or_None).
    On JSON parse failure the raw output is returned so the caller can show
    it to the user; no tasks are inserted in that case.
    """
    prompt = PLANNER_INSTRUCTIONS + "\n\nAsk:\n" + ask
    title = f"[plan] {ask[:60]}"
    try:
        jid = job_manager.create_job(project_id, prompt, title=title)
    except ValueError as e:
        return [], None, f"could not create planner job: {e}"
    await job_manager.start(jid)
    await job_manager.wait(jid)

    log_dir = log_root / "jobs" / jid
    raw = _all_assistant_text(log_dir)
    parsed = _extract_json_array(raw)
    if parsed is None:
        return [], raw, "could not parse a JSON task array from planner output"

    return _insert_drafts(project_id, parsed), raw, None


def _insert_drafts(project_id: str, parsed: list[dict]) -> list[str]:
    """Insert planner-drafted tasks, resolving depends_on_titles to ids.

    Tasks land as `status='pending'`, `source='planner'`. Skips entries
    missing a title or prompt.
    """
    created_ids: list[str] = []
    with session_scope() as s:
        title_to_id: dict[str, str] = {}
        for idx, item in enumerate(parsed):
            if not isinstance(item, dict):
                continue
            title = item.get("title")
            prompt = item.get("prompt")
            if not isinstance(title, str) or not isinstance(prompt, str):
                continue
            t = models.Task(
                project_id=project_id,
                title=title[:256],
                prompt=prompt,
                status="pending",
                source="planner",
                order_idx=idx,
            )
            s.add(t)
            s.flush()
            title_to_id[title] = t.id
            created_ids.append(t.id)

        # Resolve dependencies. Titles that don't match anything are skipped
        # (no hard failure — the user can fix in the edit step).
        for idx, item in enumerate(parsed):
            if not isinstance(item, dict):
                continue
            title = item.get("title")
            if title not in title_to_id:
                continue
            deps = item.get("depends_on_titles") or []
            if not isinstance(deps, list):
                continue
            tid = title_to_id[title]
            for dep_title in deps:
                if not isinstance(dep_title, str):
                    continue
                dep_id = title_to_id.get(dep_title)
                if dep_id is None or dep_id == tid:
                    continue
                s.add(models.TaskDependency(task_id=tid, depends_on_id=dep_id))
    return created_ids
