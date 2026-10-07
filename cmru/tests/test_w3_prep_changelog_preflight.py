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


def _next_major_above_newest_release(text: str) -> str:
    """The next major above the newest dated heading in ``text``.

    Derived from the tree so the test holds both before release-prep (newest is the last
    release) and inside the release transaction (newest is the freshly generated section).
    """
    newest = re.search(r"^## \[(\d+)\.\d+\.\d+\] - \d{4}-\d{2}-\d{2}$", text, re.MULTILINE)
    assert newest is not None
    return f"{int(newest.group(1)) + 1}.0.0"


def test_a_generated_section_is_found_despite_prose_mentioning_its_heading(tmp_path):
    """Regression: `## [X]` quoted mid-line in the [Unreleased] notes is not the section.

    A re-run for the same version must treat the real generated section as generated
    (idempotent / --resume safe), never report it as hand-authored.
    """
    marker = "<!-- cmru: release history -->"
    text = (
        "# Changelog\n\n## [Unreleased]\n<!-- generates the `## [6.0.0]` section -->\n\n"
        f"{marker}\n"
    )
    repo = _repo_with_changes(tmp_path, text)

    assert generate_release_changelog(repo, _project(), set_version="6.0.0") is True
    again = generate_release_changelog(repo, _project(), set_version="6.0.0")
    assert again is False
    history = (repo / "cmru" / "CHANGES.md").read_text(encoding="utf-8")
    assert len(re.findall(r"^## \[6\.0\.0\]", history, re.MULTILINE)) == 1


def test_shipped_changes_md_has_no_hand_authored_unreleased_heading():
    text = REAL_CHANGES.read_text(encoding="utf-8")
    assert re.findall(r"^## \[[^\]]+\] - UNRELEASED$", text, re.MULTILINE) == []


def test_release_time_changelog_preflight_passes_on_this_trees_changes_md(tmp_path):
    text = REAL_CHANGES.read_text(encoding="utf-8")
    version = _next_major_above_newest_release(text)
    repo = _repo_with_changes(tmp_path, text)

    assert generate_release_changelog(repo, _project(), set_version=version) is True

    history = (repo / "cmru" / "CHANGES.md").read_text(encoding="utf-8")
    escaped = re.escape(version)
    assert re.search(rf"^## \[{escaped}\] - \d{{4}}-\d{{2}}-\d{{2}}$", history, re.MULTILINE)
    assert "feat(cmru)!: redesign the command line" in history
    assert len(re.findall(rf"^## \[{escaped}\]", history, re.MULTILINE)) == 1
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
