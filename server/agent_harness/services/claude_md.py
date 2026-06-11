"""Sync project shared context into a managed CLAUDE.md block.

We materialize a project's `instructions`, `skills`, and `context_paths` into
a fenced section of `<project.path>/CLAUDE.md`. Claude Code reads CLAUDE.md
from the working directory natively, so this avoids per-job CLI flags.

User content outside the fence is preserved on re-sync. If no CLAUDE.md
exists, one is created containing only the managed block.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .. import models

log = logging.getLogger(__name__)

BEGIN_MARKER = "<!-- BEGIN agent-harness managed: do not edit -->"
END_MARKER = "<!-- END agent-harness managed -->"


def render_block(project: models.Project) -> str:
    """Render the managed CLAUDE.md block for a project. Always fenced."""
    lines: list[str] = [BEGIN_MARKER, "", f"# Project: {project.name}", ""]
    instructions = (project.instructions or "").strip()
    if instructions:
        lines.append("## Instructions")
        lines.append("")
        lines.append(instructions)
        lines.append("")
    skills = list(project.skills or [])
    if skills:
        lines.append("## Skills")
        lines.append("")
        lines.append("Auto-allowed for this project (no permission prompt for these):")
        lines.append("")
        for s in skills:
            lines.append(f"- `Skill({s})`")
        lines.append("")
    paths = list(project.context_paths or [])
    if paths:
        lines.append("## Reference paths")
        lines.append("")
        for p in paths:
            lines.append(f"- `{p}`")
        lines.append("")
    lines.append(END_MARKER)
    return "\n".join(lines) + "\n"


def _splice(existing: str, new_block: str) -> str:
    """Return `existing` with the managed block replaced by `new_block`.

    If the markers aren't present, prepend the new block (followed by a blank
    line then the original content). If both markers are present, splice in
    place. If only one marker is present, treat the file as malformed and
    prepend (don't drop user content).
    """
    if not existing.strip():
        return new_block
    begin = existing.find(BEGIN_MARKER)
    end = existing.find(END_MARKER)
    if begin == -1 or end == -1 or end < begin:
        # Markers missing or malformed: prepend the managed block, preserve rest.
        sep = "" if existing.startswith("\n") else "\n"
        return new_block + sep + existing
    # Splice: replace [begin .. end+len(END_MARKER)] with new_block (sans trailing \n).
    end_after = end + len(END_MARKER)
    # Consume one trailing newline that immediately follows the end marker.
    if end_after < len(existing) and existing[end_after] == "\n":
        end_after += 1
    return existing[:begin] + new_block + existing[end_after:]


def sync_project(project: models.Project) -> Path | None:
    """Write the managed block into `<project.path>/CLAUDE.md`.

    Returns the CLAUDE.md path on success, or None if the project has no
    path on disk (e.g. a directory that doesn't exist).
    """
    if not project.path:
        return None
    pdir = Path(project.path)
    if not pdir.is_dir():
        log.info("claude_md.sync: project path %s missing; skipping", project.path)
        return None
    target = pdir / "CLAUDE.md"
    block = render_block(project)
    if target.exists():
        existing = target.read_text(encoding="utf-8")
        new = _splice(existing, block)
    else:
        new = block
    if not target.exists() or target.read_text(encoding="utf-8") != new:
        target.write_text(new, encoding="utf-8")
    return target


def sync_all() -> int:
    """Sync every project's CLAUDE.md. Returns the count synced."""
    from ..db import session_scope

    count = 0
    with session_scope() as s:
        for proj in s.query(models.Project).all():
            try:
                if sync_project(proj) is not None:
                    count += 1
            except Exception as e:  # noqa: BLE001
                log.warning("claude_md.sync_all: %s failed: %s", proj.id, e)
    return count
