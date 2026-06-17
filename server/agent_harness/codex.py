"""Codex CLI subprocess runner + JSONL parser.

Parallel to ``claude.py`` — the load-bearing integration with the ``codex`` CLI
(``codex-cli`` 0.139+). Same defensive posture: capture the event shapes we
recognize, ignore everything else.

# JSONL line shapes we recognize

``codex [globals] exec --json [resume <id>] <prompt>`` emits one JSON object per
line. Captured from the installed CLI:

```
{"type":"thread.started","thread_id":"019ecd2b-..."}
{"type":"turn.started"}
{"type":"item.started","item":{"id":"item_0","type":"command_execution",
  "command":"...","aggregated_output":"","exit_code":null,"status":"in_progress"}}
{"type":"item.completed","item":{"id":"item_0","type":"command_execution",
  "command":"...","aggregated_output":"...","exit_code":0,"status":"completed"}}
{"type":"item.completed","item":{"id":"item_1","type":"file_change",
  "changes":[{"path":"...","kind":"add"}],"status":"completed"}}
{"type":"item.completed","item":{"id":"item_2","type":"agent_message","text":"...DONE"}}
{"type":"turn.completed","usage":{"input_tokens":...,"output_tokens":...}}
```

# Mapping to our StreamEvent union

- ``thread.started``                       → captures session_id (thread_id); no event
- ``item.started`` (command_execution/...)  → ToolUseEvent (pairs with the completed
                                              result so the idle watchdog's inflight
                                              accounting stays balanced)
- ``item.completed`` agent_message          → AssistantTextEvent
- ``item.completed`` command_execution/...   → ToolResultEvent (ok, output preview)
- ``item.completed`` reasoning               → dropped (like Claude's `thinking`)
- ``turn.completed``                         → TurnDoneEvent(exit_code 0); Codex reports
                                              no per-turn cost, so cost_usd is None
- ``turn.failed`` / top-level ``error``      → TurnDoneEvent(exit_code 1) (terminal)

Unknown ``type`` / item types and non-JSON warning lines are dropped at DEBUG.

Note: unlike Claude, Codex blocks reading from stdin when invoked with a piped
stdin, so the runner spawns it with ``stdin=DEVNULL``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator, Iterable

from .claude import AttachedFile
from .schemas import (
    AssistantTextEvent,
    StreamEvent,
    ToolResultEvent,
    ToolUseEvent,
    TurnDoneEvent,
)

log = logging.getLogger(__name__)

_PREVIEW_MAX = 4000

# Item types that represent an *action* (a tool call) — they arrive as a
# started/completed pair and map to ToolUse/ToolResult. Everything else
# (agent_message, reasoning, …) is handled explicitly or dropped.
_ACTION_ITEM_TYPES = frozenset(
    {"command_execution", "file_change", "patch_apply", "mcp_tool_call", "web_search"}
)

# Friendly tool labels for the transcript UI.
_TOOL_LABELS = {
    "command_execution": "shell",
    "file_change": "edit",
    "patch_apply": "edit",
    "mcp_tool_call": "mcp",
    "web_search": "web_search",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _preview(s: object) -> str:
    if not isinstance(s, str):
        s = json.dumps(s, default=str)
    return s if len(s) <= _PREVIEW_MAX else s[:_PREVIEW_MAX] + "…"


def _tool_label(item_type: str) -> str:
    return _TOOL_LABELS.get(item_type, item_type or "?")


def _tool_input(item: dict[str, object]) -> dict[str, object]:
    """Pull the interesting fields out of a Codex action item for display."""
    keys = ("command", "changes", "cwd", "tool", "server", "query", "url")
    out = {k: item[k] for k in keys if k in item}
    return out or {"item": item.get("id", "")}


def _result_ok(item: dict[str, object]) -> bool:
    code = item.get("exit_code")
    if isinstance(code, int):
        return code == 0
    return item.get("status") != "failed"


def _result_preview(item: dict[str, object]) -> str:
    itype = item.get("type")
    if itype == "command_execution":
        return _preview(item.get("aggregated_output", ""))
    if itype in ("file_change", "patch_apply"):
        changes = item.get("changes")
        if isinstance(changes, list):
            parts = []
            for c in changes:
                if isinstance(c, dict):
                    parts.append(f"{c.get('kind', '?')} {c.get('path', '?')}")
            return _preview("\n".join(parts) or json.dumps(changes, default=str))
    return _preview(item)


@dataclass
class CodexJsonParser:
    job_id: str
    turn: int
    session_id: str | None = None

    def feed(self, obj: dict[str, object]) -> list[StreamEvent]:
        """Map one Codex event to zero-or-more of our StreamEvents."""
        t = obj.get("type")
        if t == "thread.started":
            tid = obj.get("thread_id")
            if isinstance(tid, str):
                self.session_id = tid
            return []
        if t == "item.started":
            return list(self._handle_item_started(obj))
        if t == "item.completed":
            return list(self._handle_item_completed(obj))
        if t == "turn.completed":
            return [self._turn_done(0)]
        if t in ("turn.failed", "error"):
            return [self._turn_done(1)]
        # turn.started, item.updated, and anything unknown: nothing to surface.
        log.debug("unhandled codex event type %r", t)
        return []

    def feed_line(self, line: str) -> list[StreamEvent]:
        line = line.strip()
        if not line:
            return []
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            # Codex prints non-JSON warnings/progress to stdout sometimes;
            # ignore them rather than crashing the turn.
            log.debug("non-json codex line: %.200s", line)
            return []
        if not isinstance(obj, dict):
            return []
        return self.feed(obj)

    def parse_all(self, lines: Iterable[str]) -> list[StreamEvent]:
        out: list[StreamEvent] = []
        for line in lines:
            out.extend(self.feed_line(line))
        return out

    def _item(self, obj: dict[str, object]) -> dict[str, object] | None:
        item = obj.get("item")
        return item if isinstance(item, dict) else None

    def _handle_item_started(self, obj: dict[str, object]) -> Iterable[StreamEvent]:
        item = self._item(obj)
        if item is None:
            return
        itype = str(item.get("type") or "")
        if itype in _ACTION_ITEM_TYPES:
            yield ToolUseEvent(
                job_id=self.job_id,
                turn=self.turn,
                ts=_utcnow(),
                tool=_tool_label(itype),
                input=_tool_input(item),
            )

    def _handle_item_completed(self, obj: dict[str, object]) -> Iterable[StreamEvent]:
        item = self._item(obj)
        if item is None:
            return
        itype = str(item.get("type") or "")
        if itype == "agent_message":
            text = item.get("text")
            if isinstance(text, str) and text:
                yield AssistantTextEvent(
                    job_id=self.job_id, turn=self.turn, ts=_utcnow(), text=text
                )
        elif itype in _ACTION_ITEM_TYPES:
            yield ToolResultEvent(
                job_id=self.job_id,
                turn=self.turn,
                ts=_utcnow(),
                ok=_result_ok(item),
                output_preview=_result_preview(item),
            )
        # reasoning / todo_list / anything else: nothing to surface.

    def _turn_done(self, exit_code: int) -> StreamEvent:
        return TurnDoneEvent(
            job_id=self.job_id,
            turn=self.turn,
            ts=_utcnow(),
            exit_code=exit_code,
            cost_usd=None,  # Codex does not report per-turn cost
            duration_ms=None,
        )


# --------------------------------- Runner ---------------------------------- #


def resolve_codex_path(override: str | None = None) -> str:
    """Resolve the `codex` binary. Order: override → AH_CODEX_PATH env → PATH."""
    cand = override or os.environ.get("AH_CODEX_PATH") or shutil.which("codex")
    if not cand:
        raise FileNotFoundError(
            "codex CLI not found. Install it (https://github.com/openai/codex) or "
            "set codex_path in ~/.agent-harness/config.toml or AH_CODEX_PATH env."
        )
    return cand


@dataclass
class CodexRunner:
    """Async wrapper around ``codex [globals] exec --json``.

    Public surface mirrors :class:`agent_harness.claude.ClaudeRunner` so
    ``JobManager`` can drive either: ``run()``, ``stop()``, ``pid``,
    ``returncode``, ``session_id``, ``stop_requested``.

    ``sandbox`` is the Codex sandbox policy for this job's phase
    (``read-only`` for plan, ``workspace-write`` for execute/integrate/ad-hoc).
    ``extra_args`` are Codex-specific (never Claude flags). ``model`` maps to
    ``-m``. Image ``attachments`` map to ``-i``; non-image attachments are
    appended to the prompt like Claude does.
    """

    job_id: str
    turn: int
    prompt: str
    cwd: str
    resume_session_id: str | None = None
    sandbox: str = "workspace-write"
    dangerously_skip: bool = False
    extra_args: list[str] = field(default_factory=list)
    model: str | None = None
    codex_path: str | None = None
    env: dict[str, str] | None = None
    attachments: list[AttachedFile] = field(default_factory=list)

    _proc: asyncio.subprocess.Process | None = None
    _parser: CodexJsonParser | None = None
    _stop_requested: bool = False
    _result_delivered: bool = False

    def __post_init__(self) -> None:
        self._parser = CodexJsonParser(job_id=self.job_id, turn=self.turn)

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested

    @property
    def session_id(self) -> str | None:
        return self._parser.session_id if self._parser else None

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def returncode(self) -> int | None:
        return self._proc.returncode if self._proc else None

    def _image_paths(self) -> list[str]:
        return [a.path for a in self.attachments if a.mime_type.startswith("image/")]

    def _effective_prompt(self) -> str:
        """Prompt with any non-image attachment paths appended (images go to -i)."""
        non_images = [a for a in self.attachments if not a.mime_type.startswith("image/")]
        if not non_images:
            return self.prompt
        lines = [self.prompt, "", "[Attached files — view/read them as needed:]"]
        for att in non_images:
            lines.append(f"- {att.path}  ({att.filename})")
        return "\n".join(lines)

    def build_argv(self) -> list[str]:
        argv: list[str] = [resolve_codex_path(self.codex_path)]
        # Global flags must precede the `exec` subcommand.
        if self.dangerously_skip:
            argv += ["--dangerously-bypass-approvals-and-sandbox"]
        else:
            argv += ["-a", "never", "-s", self.sandbox]
        argv += ["--cd", self.cwd, "exec"]
        if self.resume_session_id:
            argv += ["resume", self.resume_session_id]
        argv += ["--json", "--skip-git-repo-check"]
        if self.model:
            argv += ["-m", self.model]
        for img in self._image_paths():
            argv += ["-i", img]
        if self.extra_args:
            argv += list(self.extra_args)
        # Prompt is the trailing positional argument.
        argv += [self._effective_prompt()]
        return argv

    async def run(self) -> AsyncIterator[StreamEvent]:
        argv = self.build_argv()
        log.info(
            "spawning codex job=%s turn=%d cwd=%s resume=%s sandbox=%s skip=%s",
            self.job_id,
            self.turn,
            self.cwd,
            self.resume_session_id or "-",
            self.sandbox,
            self.dangerously_skip,
        )
        merged_env = {**os.environ, **(self.env or {})}
        self._proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,  # Codex blocks on a piped stdin
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
            env=merged_env,
            limit=16 * 1024 * 1024,
        )
        assert self._proc.stdout is not None
        parser = self._parser
        assert parser is not None
        try:
            terminal = False
            while not terminal:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                events = parser.feed_line(line.decode("utf-8", "replace"))
                for ev in events:
                    yield ev
                    if isinstance(ev, TurnDoneEvent):
                        self._result_delivered = True
                        terminal = True
        finally:
            await self._drain_stderr_and_wait()

    async def _drain_stderr_and_wait(self) -> None:
        proc = self._proc
        if proc is None:
            return
        err_text = ""
        if proc.stderr is not None:
            try:
                err = await asyncio.wait_for(proc.stderr.read(), timeout=2.0)
                if err:
                    err_text = err.decode("utf-8", "replace")
            except Exception:
                pass
        if proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                    await proc.wait()
                except ProcessLookupError:
                    pass
        rc = proc.returncode
        if rc not in (0, None) and not self._stop_requested and not self._result_delivered:
            log.warning(
                "codex exited %d job=%s turn=%d%s",
                rc,
                self.job_id,
                self.turn,
                f" stderr: {err_text[:500]}" if err_text else " (no stderr)",
            )
        elif err_text:
            log.debug("codex stderr job=%s turn=%d: %.500s", self.job_id, self.turn, err_text)

    async def stop(self) -> None:
        self._stop_requested = True
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.send_signal(signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                return
            await proc.wait()


__all__ = [
    "CodexJsonParser",
    "CodexRunner",
    "resolve_codex_path",
]
