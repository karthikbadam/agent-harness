from __future__ import annotations

from pathlib import Path

from agent_harness import models
from agent_harness.db import session_scope
from agent_harness.services import claude_md


def _mkproject(tmp_path: Path, **kw: object) -> models.Project:
    pdir = tmp_path / kw.get("name", "proj")
    pdir.mkdir()
    with session_scope() as s:
        p = models.Project(
            name=str(kw.get("name", "proj")),
            path=str(pdir),
            instructions=kw.get("instructions"),
            skills=list(kw.get("skills") or []),
            context_paths=list(kw.get("context_paths") or []),
            agent_provider=str(kw.get("agent_provider", "claude")),
        )
        s.add(p)
        s.flush()
        s.refresh(p)
        s.expunge(p)
        return p


def test_sync_creates_claude_md_when_missing(initdb: Path, tmp_path: Path) -> None:
    p = _mkproject(
        tmp_path,
        name="ctx",
        instructions="Use snake_case.",
        skills=["init", "review"],
        context_paths=["/tmp/notes"],
    )
    target = claude_md.sync_project(p)
    assert target is not None
    text = target.read_text(encoding="utf-8")
    assert claude_md.BEGIN_MARKER in text
    assert claude_md.END_MARKER in text
    assert "Use snake_case." in text
    assert "`Skill(init)`" in text
    assert "/tmp/notes" in text


def test_sync_preserves_user_content_outside_fence(initdb: Path, tmp_path: Path) -> None:
    p = _mkproject(tmp_path, name="ctx2", instructions="v1")
    target = claude_md.sync_project(p)
    assert target is not None
    # Append user content after the managed block.
    target.write_text(
        target.read_text(encoding="utf-8") + "\n## My notes\n\nhand-edited.\n",
        encoding="utf-8",
    )
    # Now re-sync with updated instructions.
    with session_scope() as s:
        live = s.get(models.Project, p.id)
        assert live is not None
        live.instructions = "v2"
        s.flush()
        s.refresh(live)
        s.expunge(live)
        p = live
    claude_md.sync_project(p)
    after = target.read_text(encoding="utf-8")
    assert "v2" in after
    assert "v1" not in after
    assert "## My notes" in after
    assert "hand-edited." in after


def test_sync_idempotent(initdb: Path, tmp_path: Path) -> None:
    p = _mkproject(tmp_path, name="ctx3", instructions="stable", skills=["init"])
    target = claude_md.sync_project(p)
    assert target is not None
    first = target.read_text(encoding="utf-8")
    target_again = claude_md.sync_project(p)
    assert target_again == target
    assert target.read_text(encoding="utf-8") == first


def test_sync_skips_missing_path(initdb: Path, tmp_path: Path) -> None:
    with session_scope() as s:
        p = models.Project(name="nope", path=str(tmp_path / "does-not-exist"))
        s.add(p)
        s.flush()
        s.refresh(p)
        s.expunge(p)
    assert claude_md.sync_project(p) is None


def test_codex_project_also_writes_agents_md(initdb: Path, tmp_path: Path) -> None:
    p = _mkproject(tmp_path, name="cdx", instructions="Verify cheaply.", agent_provider="codex")
    target = claude_md.sync_project(p)
    assert target is not None
    pdir = Path(p.path)
    agents = pdir / "AGENTS.md"
    assert agents.exists(), "codex project should get an AGENTS.md"
    text = agents.read_text(encoding="utf-8")
    assert claude_md.BEGIN_MARKER in text
    assert "Verify cheaply." in text


def test_auto_project_writes_agents_md(initdb: Path, tmp_path: Path) -> None:
    p = _mkproject(tmp_path, name="au", instructions="x", agent_provider="auto")
    claude_md.sync_project(p)
    assert (Path(p.path) / "AGENTS.md").exists()


def test_claude_project_also_mirrors_agents_md(initdb: Path, tmp_path: Path) -> None:
    # AGENTS.md is mirrored for every project (symmetric with CLAUDE.md) so a
    # per-task codex override on a Claude-default project still gets guidance.
    p = _mkproject(tmp_path, name="cl", instructions="x", agent_provider="claude")
    claude_md.sync_project(p)
    assert (Path(p.path) / "AGENTS.md").exists()
    assert (Path(p.path) / "CLAUDE.md").exists()


def test_existing_agents_md_stays_synced_for_claude_project(initdb: Path, tmp_path: Path) -> None:
    # A claude project that already has an AGENTS.md (e.g. after switching back
    # from codex) keeps it in sync rather than going stale.
    p = _mkproject(tmp_path, name="cl2", instructions="v1", agent_provider="claude")
    agents = Path(p.path) / "AGENTS.md"
    agents.write_text("## hand\n\nnotes\n", encoding="utf-8")
    claude_md.sync_project(p)
    text = agents.read_text(encoding="utf-8")
    assert claude_md.BEGIN_MARKER in text
    assert "v1" in text
    assert "## hand" in text  # user content preserved


def test_sync_prepends_when_markers_missing(initdb: Path, tmp_path: Path) -> None:
    pdir = tmp_path / "pre"
    pdir.mkdir()
    (pdir / "CLAUDE.md").write_text("## Existing\n\nhello\n", encoding="utf-8")
    with session_scope() as s:
        p = models.Project(name="pre", path=str(pdir), instructions="new")
        s.add(p)
        s.flush()
        s.refresh(p)
        s.expunge(p)
    claude_md.sync_project(p)
    text = (pdir / "CLAUDE.md").read_text(encoding="utf-8")
    assert text.startswith(claude_md.BEGIN_MARKER)
    assert "## Existing" in text
    assert "hello" in text
