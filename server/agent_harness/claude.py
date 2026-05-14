"""Claude Code subprocess runner + stream-json parser.

This module is the load-bearing integration with the `claude` CLI. Treat
Claude's stream-json shape *defensively* — capture what's there, ignore unknown
event types.

# Stream-json line shapes we recognize

`claude -p <prompt> --output-format stream-json --verbose` emits one JSON
object per line. Known top-level `type` values:

```
{"type":"system","subtype":"init","session_id":"...","model":"...",
 "cwd":"...","tools":[...], ...}

{"type":"assistant","session_id":"...","message":{
  "id":"...","role":"assistant","content":[
    {"type":"text","text":"..."},
    {"type":"tool_use","id":"toolu_...","name":"Bash","input":{...}}
  ]}}

{"type":"user","session_id":"...","message":{
  "role":"user","content":[
    {"type":"tool_result","tool_use_id":"toolu_...",
     "content":"...","is_error":false}
  ]}}

{"type":"result","subtype":"success","is_error":false,
 "duration_ms":1234,"total_cost_usd":0.0123,"session_id":"...",
 "num_turns":1,"result":"..."}
```

# Mapping to our StreamEvent union

- `system/init`                   → captures session_id; no event emitted
- `assistant` text block          → AssistantTextEvent
- `assistant` tool_use block      → ToolUseEvent (tool, input); remember name by tool_use_id
- `user` tool_result block        → ToolResultEvent (ok = not is_error, preview)
                                    If is_error AND output matches a permission
                                    pattern → also emits ToolBlockedEvent with
                                    a suggested rule inferred from the tool.
- `result`                        → TurnDoneEvent (exit_code 0 if success else 1,
                                                   cost_usd, duration_ms)

Unknown `type` values are dropped at DEBUG. Specifically dropped on purpose:
  - `rate_limit_event`, `system/hook_started`, `system/hook_response`,
    `system/notification` — chatty meta from the host environment.
  - `thinking` content blocks inside an assistant message — not part of the
    v1 transcript UX. (Adding later means: emit a new event type + UI card.)

The parser is stateful: it tracks tool_use_id → tool_name so that a later
tool_result event can know which tool was blocked even though the result event
doesn't repeat the name.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import signal
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator, Iterable

from .schemas import (
    AssistantTextEvent,
    JobStatusEvent,
    StreamEvent,
    ToolBlockedEvent,
    ToolResultEvent,
    ToolUseEvent,
    TurnDoneEvent,
)

log = logging.getLogger(__name__)

_PREVIEW_MAX = 4000
_PERMISSION_PATTERNS = (
    re.compile(r"permission", re.I),
    re.compile(r"not allowed", re.I),
    re.compile(r"requires approval", re.I),
    re.compile(r"blocked by", re.I),
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _preview(s: object) -> str:
    if not isinstance(s, str):
        s = json.dumps(s, default=str)
    return s if len(s) <= _PREVIEW_MAX else s[:_PREVIEW_MAX] + "…"


def _looks_like_permission_error(text: str) -> bool:
    return any(p.search(text) for p in _PERMISSION_PATTERNS)


def _suggest_rule_for(tool: str, tool_input: dict[str, object]) -> str:
    """Best-effort rule suggestion. We deliberately keep this dumb."""
    if tool == "Bash":
        cmd = str(tool_input.get("command", "")).strip()
        head = cmd.split()[0] if cmd else ""
        return f"Bash({head}:*)" if head else "Bash(*)"
    if tool in ("Edit", "Write", "MultiEdit"):
        path = str(tool_input.get("file_path", ""))
        if path:
            ext = os.path.splitext(path)[1]
            if ext:
                return f"{tool}(**/*{ext})"
        return f"{tool}(*)"
    return f"{tool}(*)"


@dataclass
class StreamJsonParser:
    job_id: str
    turn: int
    session_id: str | None = None
    _tool_names: dict[str, str] = field(default_factory=dict)
    _tool_inputs: dict[str, dict[str, object]] = field(default_factory=dict)

    def feed(self, obj: dict[str, object]) -> list[StreamEvent]:
        """Map one upstream event to zero-or-more of our StreamEvents."""
        t = obj.get("type")
        if t == "system":
            sid = obj.get("session_id")
            if isinstance(sid, str):
                self.session_id = sid
            return []
        if t == "assistant":
            return list(self._handle_assistant(obj))
        if t == "user":
            return list(self._handle_user(obj))
        if t == "result":
            return [self._handle_result(obj)]
        log.debug("unknown stream-json type %r", t)
        return []

    def feed_line(self, line: str) -> list[StreamEvent]:
        line = line.strip()
        if not line:
            return []
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            log.warning("bad json on stream: %s | %.200s", e, line)
            return []
        if not isinstance(obj, dict):
            return []
        return self.feed(obj)

    def parse_all(self, lines: Iterable[str]) -> list[StreamEvent]:
        out: list[StreamEvent] = []
        for line in lines:
            out.extend(self.feed_line(line))
        return out

    def _handle_assistant(self, obj: dict[str, object]) -> Iterable[StreamEvent]:
        sid = obj.get("session_id")
        if isinstance(sid, str):
            self.session_id = sid
        msg = obj.get("message")
        if not isinstance(msg, dict):
            return
        content = msg.get("content")
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text = block.get("text") or ""
                if isinstance(text, str) and text:
                    yield AssistantTextEvent(
                        job_id=self.job_id, turn=self.turn, ts=_utcnow(), text=text
                    )
            elif btype == "tool_use":
                tname = str(block.get("name") or "")
                tid = str(block.get("id") or "")
                tinput_raw = block.get("input")
                tinput: dict[str, object] = tinput_raw if isinstance(tinput_raw, dict) else {}
                if tid and tname:
                    self._tool_names[tid] = tname
                    self._tool_inputs[tid] = tinput
                yield ToolUseEvent(
                    job_id=self.job_id,
                    turn=self.turn,
                    ts=_utcnow(),
                    tool=tname or "?",
                    input=tinput,
                )

    def _handle_user(self, obj: dict[str, object]) -> Iterable[StreamEvent]:
        msg = obj.get("message")
        if not isinstance(msg, dict):
            return
        content = msg.get("content")
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_result":
                continue
            tid = str(block.get("tool_use_id") or "")
            is_error = bool(block.get("is_error", False))
            raw = block.get("content", "")
            if isinstance(raw, list):  # claude can send rich content
                pieces: list[str] = []
                for el in raw:
                    if isinstance(el, dict) and "text" in el:
                        pieces.append(str(el["text"]))
                    else:
                        pieces.append(json.dumps(el, default=str))
                text = "\n".join(pieces)
            else:
                text = str(raw)
            yield ToolResultEvent(
                job_id=self.job_id,
                turn=self.turn,
                ts=_utcnow(),
                ok=not is_error,
                output_preview=_preview(text),
            )
            if is_error and _looks_like_permission_error(text):
                tname = self._tool_names.get(tid, "?")
                tinput = self._tool_inputs.get(tid, {})
                yield ToolBlockedEvent(
                    job_id=self.job_id,
                    turn=self.turn,
                    ts=_utcnow(),
                    tool=tname,
                    reason=_preview(text),
                    suggested_rule=_suggest_rule_for(tname, tinput) if tname != "?" else None,
                )

    def _handle_result(self, obj: dict[str, object]) -> StreamEvent:
        sid = obj.get("session_id")
        if isinstance(sid, str):
            self.session_id = sid
        is_error = bool(obj.get("is_error", False))
        subtype = obj.get("subtype")
        exit_code = 0 if (subtype == "success" and not is_error) else 1
        cost = obj.get("total_cost_usd")
        dur = obj.get("duration_ms")
        return TurnDoneEvent(
            job_id=self.job_id,
            turn=self.turn,
            ts=_utcnow(),
            exit_code=exit_code,
            cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
            duration_ms=int(dur) if isinstance(dur, (int, float)) else None,
        )


# --------------------------------- Runner ---------------------------------- #


def resolve_claude_path(override: str | None = None) -> str:
    """Resolve the `claude` binary. Order: override → AH_CLAUDE_PATH env → PATH."""
    cand = override or os.environ.get("AH_CLAUDE_PATH") or shutil.which("claude")
    if not cand:
        raise FileNotFoundError(
            "claude CLI not found. Install it (https://docs.claude.com/) or set "
            "claude_path in ~/.agent-harness/config.toml or AH_CLAUDE_PATH env."
        )
    return cand


@dataclass
class ClaudeRunner:
    job_id: str
    turn: int
    prompt: str
    cwd: str
    resume_session_id: str | None = None
    permission_mode: str | None = "acceptEdits"
    allowed_tools: list[str] = field(default_factory=list)
    dangerously_skip: bool = False
    claude_path: str | None = None
    env: dict[str, str] | None = None

    _proc: asyncio.subprocess.Process | None = None
    _parser: StreamJsonParser | None = None

    def __post_init__(self) -> None:
        self._parser = StreamJsonParser(job_id=self.job_id, turn=self.turn)

    @property
    def session_id(self) -> str | None:
        return self._parser.session_id if self._parser else None

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    def build_argv(self) -> list[str]:
        argv: list[str] = [
            resolve_claude_path(self.claude_path),
            "-p",
            self.prompt,
            "--output-format",
            "stream-json",
            "--verbose",
        ]
        if self.resume_session_id:
            argv += ["--resume", self.resume_session_id]
        if self.dangerously_skip:
            argv += ["--dangerously-skip-permissions"]
        else:
            if self.permission_mode:
                argv += ["--permission-mode", self.permission_mode]
            if self.allowed_tools:
                argv += ["--allowed-tools", ",".join(self.allowed_tools)]
        return argv

    async def run(self) -> AsyncIterator[StreamEvent]:
        argv = self.build_argv()
        log.info("spawning claude: %s (cwd=%s)", argv[:2] + ["…"], self.cwd)
        merged_env = {**os.environ, **(self.env or {})}
        self._proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
            env=merged_env,
        )
        assert self._proc.stdout is not None
        parser = self._parser
        assert parser is not None
        try:
            while True:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                events = parser.feed_line(line.decode("utf-8", "replace"))
                for ev in events:
                    yield ev
        finally:
            await self._drain_stderr_and_wait()

    async def _drain_stderr_and_wait(self) -> None:
        proc = self._proc
        if proc is None:
            return
        if proc.stderr is not None:
            try:
                err = await proc.stderr.read()
                if err:
                    log.debug("claude stderr: %.500s", err.decode("utf-8", "replace"))
            except Exception:
                pass
        await proc.wait()

    @property
    def returncode(self) -> int | None:
        return self._proc.returncode if self._proc else None

    async def stop(self) -> None:
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
    "AssistantTextEvent",
    "ClaudeRunner",
    "JobStatusEvent",
    "StreamEvent",
    "StreamJsonParser",
    "ToolBlockedEvent",
    "ToolResultEvent",
    "ToolUseEvent",
    "TurnDoneEvent",
    "resolve_claude_path",
]
