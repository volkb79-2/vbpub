"""KI-68 and KI-59 (ported from cmru-bugs62): a regenerated section never silently
drops hand-written lines, and only cmru's own exact bump commit is skipped on resume.

KI-68 matters doubly since KI-75: curated release notes live in the generated
section, and a body published from a silently regenerated section would lose them.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cmru import changelog
from cmru.changelog import generate_release_changelog
from cmru.errors import RefusedBeforeChange
from tests.test_changelog import _commit, _git, _init_demo, _project


def _stale_with_hand_edit(tmp_path, lines=("- HAND NOTE: keep me",)):
    """A generated [1.0.1] section, hand-edited, then made stale by a later commit."""
    _init_demo(tmp_path)
    _commit(tmp_path, "fix(demo): first fix", "demo/core.py", "A = 1\n")
    project = _project()
    assert generate_release_changelog(tmp_path, project) is True
    path = tmp_path / "demo" / "CHANGES.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace("### Fixed\n", "### Fixed\n" + "\n".join(lines) + "\n"), encoding="utf-8",
    )
    _git(tmp_path, "add", "demo/CHANGES.md")
    _git(tmp_path, "commit", "-m", "chore(release): changelog")
    _commit(tmp_path, "fix(demo): second fix after the section", "demo/core.py", "A = 2\n")
    return project, path


def test_ki68_regenerating_a_stale_section_with_hand_edits_is_refused_exit_4(tmp_path):
    project, path = _stale_with_hand_edit(tmp_path)
    before = path.read_bytes()

    with pytest.raises(RefusedBeforeChange) as raised:
        generate_release_changelog(tmp_path, project)

    assert raised.value.exit_code == 4
    message = str(raised.value)
    assert "[1.0.1]" in message and "- HAND NOTE: keep me" in message
    assert "first fix" not in message  # generator-produced lines are not "dropped"
    assert "source-end" in message
    assert path.read_bytes() == before


def test_ki68_refusal_lists_at_most_twenty_dropped_lines(tmp_path):
    project, _path = _stale_with_hand_edit(
        tmp_path, tuple(f"- hand line {i:02d}" for i in range(25)),
    )

    with pytest.raises(RefusedBeforeChange) as raised:
        generate_release_changelog(tmp_path, project)

    message = str(raised.value)
    assert message.count("- hand line") == 20
    assert "- hand line 19" in message and "- hand line 20" not in message
    assert "and 5 more" in message


def test_ki68_current_section_with_hand_edits_stays_byte_identical(tmp_path, capsys):
    _init_demo(tmp_path)
    _commit(tmp_path, "fix(demo): only fix", "demo/core.py", "A = 1\n")
    project = _project()
    generate_release_changelog(tmp_path, project)
    path = tmp_path / "demo" / "CHANGES.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace("### Fixed\n", "### Fixed\n- HAND NOTE\n"),
        encoding="utf-8",
    )
    _git(tmp_path, "add", "demo/CHANGES.md")
    _git(tmp_path, "commit", "-m", "chore(release): changelog")
    before = path.read_bytes()

    assert generate_release_changelog(tmp_path, project) is False
    assert generate_release_changelog(tmp_path, project) is False  # idempotent resume

    assert path.read_bytes() == before
    assert capsys.readouterr().err == ""


def test_ki68_stale_section_without_hand_edits_regenerates_silently(tmp_path, capsys):
    project, path = _stale_with_hand_edit(tmp_path, ())

    assert generate_release_changelog(tmp_path, project) is True
    assert "second fix after the section" in path.read_text(encoding="utf-8")
    assert capsys.readouterr().err == ""


def test_ki68_a_section_without_a_cursor_or_with_an_unlistable_range_yields_no_evidence(
    tmp_path, monkeypatch,
):
    path = tmp_path / "demo" / "CHANGES.md"
    no_cursor = "## [1.0.1] - 2026-01-01\n<!-- cmru: generated -->\n\n- hand written\n"
    assert changelog._dropped_hand_edits(
        tmp_path, _project(), path, no_cursor, "1.0.1", "demo-v1.0.0",
    ) == []

    def unknown_range(*_args, **_kwargs):
        raise RuntimeError("bad object")

    monkeypatch.setattr(changelog, "_subject_groups", unknown_range)
    with_cursor = no_cursor.replace(
        "generated -->\n", "generated -->\n<!-- cmru: source-end=" + "a" * 40 + " -->\n",
    )
    assert changelog._dropped_hand_edits(
        tmp_path, _project(), path, with_cursor, "1.0.1", "demo-v1.0.0",
    ) == []


def _file_project() -> SimpleNamespace:
    project = _project()
    project.version = SimpleNamespace(strategy="file:VERSION", bump="conventional")
    return project


def _generated_then_commit(tmp_path):
    _init_demo(tmp_path)
    _commit(tmp_path, "fix(demo): first fix", "demo/core.py", "A = 1\n")
    project = _file_project()
    assert generate_release_changelog(tmp_path, project) is True
    _git(tmp_path, "add", "demo/CHANGES.md")
    _git(tmp_path, "commit", "-m", "chore(release): changelog")
    return project


def test_ki59_cmrus_own_bump_commit_is_not_project_work(tmp_path):
    project = _generated_then_commit(tmp_path)
    _commit(tmp_path, "chore: bump demo-v to 1.0.1", "demo/VERSION", "1.0.1\n")

    assert generate_release_changelog(tmp_path, project) is False


@pytest.mark.parametrize(
    ("subject", "path"),
    [
        # The W0-REL reviewer's probe: a real code change with a look-alike subject.
        ("chore: bump demo-v to 1.0.1 and rewrite core", "demo/core.py"),
        # The exact subject, but the diff is real code, not just the version file.
        ("chore: bump demo-v to 1.0.1", "demo/core.py"),
        # Right file, wrong pending version.
        ("chore: bump demo-v to 9.9.9", "demo/VERSION"),
        # Right file and version, another project's prefix.
        ("chore: bump other-v to 1.0.1", "demo/VERSION"),
    ],
)
def test_ki59_look_alike_bump_commits_count_as_project_work(tmp_path, subject, path):
    project = _generated_then_commit(tmp_path)
    _commit(tmp_path, subject, path, "9.9.9\n")

    assert generate_release_changelog(tmp_path, project) is True
    assert subject.split(" and ")[0] in (tmp_path / "demo" / "CHANGES.md").read_text(encoding="utf-8")


def test_ki59_a_non_file_strategy_never_skips_a_bump_look_alike(tmp_path):
    project = _generated_then_commit(tmp_path)
    project.version = SimpleNamespace(strategy="scm", bump="conventional")
    _commit(tmp_path, "chore: bump demo-v to 1.0.1", "demo/VERSION", "1.0.1\n")

    assert generate_release_changelog(tmp_path, project) is True


def test_ki59_an_unlistable_bump_commit_is_not_skipped(tmp_path, monkeypatch):
    def broken_git(*_args, **_kwargs):
        raise RuntimeError("git show failed")

    monkeypatch.setattr(changelog, "_git", broken_git)
    assert changelog._is_own_bump_commit(
        tmp_path, _file_project(), tmp_path / "demo" / "CHANGES.md",
        "chore: bump demo-v to 1.0.1 (abc1234)", "1.0.1",
    ) is False
