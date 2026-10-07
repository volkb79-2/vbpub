"""The shipped ``cmru/CHANGES.md`` must let ``cmru release cmru --set-version 6.0.0`` start.

The release-time changelog step (``generate_release_changelog``) refuses a hand-authored
``## [X] - UNRELEASED`` heading for the target version (KI-23) and a non-empty plain
``## [Unreleased]`` body (KI-30).  These tests run that real function, with no release,
over a copy of this tree's own ``CHANGES.md`` in a throwaway git repository.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru.changelog import generate_release_changelog
from cmru.errors import RefusedBeforeChange

REAL_CHANGES = Path(__file__).resolve().parents[1] / "CHANGES.md"


def _git(repo: Path, *args: str) -> None:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def _project() -> SimpleNamespace:
    return SimpleNamespace(
        name="cmru",
        cwd="cmru",
        paths=["cmru"],
        prefix="cmru-v",
        git_tag=True,
        changelog="CHANGES.md",
        commit_generated=(),
        version=SimpleNamespace(strategy="scm", bump="conventional"),
    )


def _repo_with_changes(tmp_path: Path, text: str) -> Path:
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@cmru.test")
    _git(tmp_path, "config", "user.name", "CMRU test")
    target = tmp_path / "cmru" / "CHANGES.md"
    target.parent.mkdir(parents=True)
    target.write_text(text, encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "chore: initialise cmru")
    _git(tmp_path, "tag", "-a", "cmru-v5.5.0", "-m", "Release cmru-v5.5.0")
    (tmp_path / "cmru" / "core.py").write_text("X = 1\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "feat(cmru)!: redesign the command line")
    return tmp_path


def test_shipped_changes_md_has_no_hand_authored_unreleased_heading():
    text = REAL_CHANGES.read_text(encoding="utf-8")
    assert re.findall(r"^## \[[^\]]+\] - UNRELEASED$", text, re.MULTILINE) == []


def test_release_time_changelog_preflight_passes_on_this_trees_changes_md(tmp_path):
    repo = _repo_with_changes(tmp_path, REAL_CHANGES.read_text(encoding="utf-8"))

    assert generate_release_changelog(repo, _project(), set_version="6.0.0") is True

    history = (repo / "cmru" / "CHANGES.md").read_text(encoding="utf-8")
    assert re.search(r"^## \[6\.0\.0\] - \d{4}-\d{2}-\d{2}$", history, re.MULTILINE)
    assert "feat(cmru)!: redesign the command line" in history
    assert len(re.findall(r"^## \[6\.0\.0\]", history, re.MULTILINE)) == 1
    assert re.findall(r"^## \[[^\]]+\] - UNRELEASED$", history, re.MULTILINE) == []


@pytest.mark.parametrize("draft", ["## [6.0.0] - UNRELEASED\n\ndraft text\n"])
def test_the_preflight_refuses_when_the_unreleased_draft_heading_is_re_added(tmp_path, draft):
    """Plant guard: the removed draft heading would block the release again."""
    text = REAL_CHANGES.read_text(encoding="utf-8")
    marker = "<!-- cmru: release history -->"
    assert marker in text
    repo = _repo_with_changes(tmp_path, text.replace(marker, draft + "\n" + marker, 1))

    with pytest.raises(RefusedBeforeChange, match="KI-23"):
        generate_release_changelog(repo, _project(), set_version="6.0.0")
