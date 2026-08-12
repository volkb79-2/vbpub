"""Behavioural tests for CMRU's source-first generated release history."""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from cmru.changelog import generate_release_changelog


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _commit(repo: Path, subject: str, path: str, content: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(repo, "add", path)
    _git(repo, "commit", "-m", subject)


def _project() -> SimpleNamespace:
    return SimpleNamespace(
        name="demo",
        cwd="demo",
        paths=["demo"],
        prefix="demo-v",
        mint_tag=True,
        changelog="CHANGES.md",
        version=SimpleNamespace(strategy="scm", bump="conventional"),
    )


def test_generates_pending_version_from_the_project_scoped_range(tmp_path):
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@cmru.test")
    _git(tmp_path, "config", "user.name", "CMRU test")
    _commit(
        tmp_path, "chore: initialise demo", "demo/CHANGES.md",
        "# Changelog\n\n<!-- cmru: release history -->\n",
    )
    _commit(tmp_path, "feat(demo): add a source-first history", "demo/core.py", "FEATURE = True\n")
    _git(tmp_path, "tag", "-a", "demo-v1.0.0", "-m", "Release demo-v1.0.0")
    _commit(tmp_path, "fix(demo): refuse an ambiguous path", "demo/core.py", "FEATURE = False\n")
    _commit(tmp_path, "docs: change another product", "other/README.md", "unrelated\n")

    project = _project()
    assert generate_release_changelog(tmp_path, project) is True

    history = (tmp_path / "demo" / "CHANGES.md").read_text(encoding="utf-8")
    assert "## [1.0.1]" in history
    assert "### Fixed" in history
    assert "fix(demo): refuse an ambiguous path" in history
    assert "change another product" not in history
    assert "<!-- cmru: generated -->" in history

    # A retained release worktree can resume without appending a duplicate entry.
    assert generate_release_changelog(tmp_path, project) is False


def test_feature_selects_a_minor_history_heading(tmp_path):
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@cmru.test")
    _git(tmp_path, "config", "user.name", "CMRU test")
    _commit(
        tmp_path, "chore: initialise demo", "demo/CHANGES.md",
        "# Changelog\n\n<!-- cmru: release history -->\n",
    )
    _git(tmp_path, "tag", "-a", "demo-v1.0.0", "-m", "Release demo-v1.0.0")
    _commit(tmp_path, "feat(demo): support release history", "demo/core.py", "FEATURE = True\n")

    assert generate_release_changelog(tmp_path, _project()) is True
    history = (tmp_path / "demo" / "CHANGES.md").read_text(encoding="utf-8")
    assert "## [1.1.0]" in history
    assert "### Added" in history
