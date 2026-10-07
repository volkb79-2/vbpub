"""Behavioural tests for CMRU's source-first generated release history."""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from cmru.changelog import backfill_release_changelog, generate_release_changelog


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
        git_tag=True,
        changelog="CHANGES.md",
        version=SimpleNamespace(strategy="scm", bump="conventional"),
    )


def _image_project() -> SimpleNamespace:
    return SimpleNamespace(
        name="image",
        cwd="image",
        paths=["image"],
        prefix="image-v",
        git_tag=False,
        changelog="CHANGES.md",
        commit_generated=(),
        version=SimpleNamespace(strategy="none", bump="conventional"),
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


def test_no_tag_image_release_uses_source_revision_and_resumes_without_duplication(tmp_path):
    """A registry-only release still receives history without inventing a semver tag."""
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@cmru.test")
    _git(tmp_path, "config", "user.name", "CMRU test")
    _commit(tmp_path, "feat(image): create the first image", "image/Dockerfile", "FROM scratch\n")
    source_tip = _git(tmp_path, "rev-parse", "HEAD")

    project = _image_project()
    assert generate_release_changelog(tmp_path, project) is True
    history_path = tmp_path / "image" / "CHANGES.md"
    history = history_path.read_text(encoding="utf-8")
    assert f"## [source-{source_tip[:12]}]" in history
    assert f"<!-- cmru: source-end={source_tip} -->" in history
    assert "feat(image): create the first image" in history

    _git(tmp_path, "add", "image/CHANGES.md")
    _git(tmp_path, "commit", "-m", "chore(image): prepare release inputs")
    # The generated history commit itself is not product source.  A retained
    # transaction must not append a second empty history entry on resume.
    assert generate_release_changelog(tmp_path, project) is False


def test_no_tag_release_records_changed_prepare_provenance_without_source_commits(tmp_path):
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@cmru.test")
    _git(tmp_path, "config", "user.name", "CMRU test")
    _commit(tmp_path, "feat(image): create the first image", "image/Dockerfile", "FROM scratch\n")
    project = _image_project()
    project.commit_generated = ("provenance.json",)
    assert generate_release_changelog(tmp_path, project) is True
    _git(tmp_path, "add", "image/CHANGES.md")
    _git(tmp_path, "commit", "-m", "chore(image): prepare release inputs")
    previous_head = _git(tmp_path, "rev-parse", "HEAD")

    # A private build can make a new publishable image by regenerating its declared
    # provenance without a new hand-authored commit in this product's source range.
    (tmp_path / "image" / "provenance.json").write_text('{"base":"new"}\n', encoding="utf-8")
    assert generate_release_changelog(tmp_path, project) is True
    history = (tmp_path / "image" / "CHANGES.md").read_text(encoding="utf-8")
    assert f"## [source-{previous_head[:12]}]" in history
    assert "Release metadata prepared by CMRU." in history


def test_backfill_catalogues_an_already_published_tag_without_moving_it(tmp_path):
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@cmru.test")
    _git(tmp_path, "config", "user.name", "CMRU test")
    _commit(tmp_path, "feat(demo): first published feature", "demo/core.py", "FEATURE = True\n")
    tag_commit = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "tag", "-a", "demo-v0.1.0", "-m", "Release demo-v0.1.0")

    assert backfill_release_changelog(tmp_path, _project(), "demo-v0.1.0") is True
    history = (tmp_path / "demo" / "CHANGES.md").read_text(encoding="utf-8")
    assert "## [0.1.0]" in history
    assert f"<!-- cmru: source-end={tag_commit} -->" in history
    assert "<!-- cmru: backfilled-after-release tag=demo-v0.1.0 -->" in history
    assert "feat(demo): first published feature" in history
    assert backfill_release_changelog(tmp_path, _project(), "demo-v0.1.0") is False


def _init_demo(repo: Path, changelog: str = "# Changelog\n\n<!-- cmru: release history -->\n") -> None:
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@cmru.test")
    _git(repo, "config", "user.name", "CMRU test")
    _commit(repo, "chore: initialise demo", "demo/CHANGES.md", changelog)
    _git(repo, "tag", "-a", "demo-v1.0.0", "-m", "Release demo-v1.0.0")


def test_rel02_stale_generated_section_is_regenerated_in_place(tmp_path):
    _init_demo(tmp_path)
    _commit(tmp_path, "fix(demo): first fix", "demo/core.py", "A = 1\n")
    project = _project()
    assert generate_release_changelog(tmp_path, project) is True
    _git(tmp_path, "add", "demo/CHANGES.md")
    _git(tmp_path, "commit", "-m", "chore(release): changelog")
    # Project commits land AFTER the generated section's source cursor.
    _commit(tmp_path, "fix(demo): second fix after the section", "demo/core.py", "A = 2\n")

    assert generate_release_changelog(tmp_path, project) is True

    history = (tmp_path / "demo" / "CHANGES.md").read_text(encoding="utf-8")
    assert history.count("## [1.0.1]") == 1
    assert "first fix" in history
    assert "second fix after the section" in history
    head = _git(tmp_path, "rev-parse", "HEAD")
    assert f"<!-- cmru: source-end={head} -->" in history
    assert history.count("<!-- cmru: release history -->") == 1


def test_rel02_current_generated_section_stays_byte_identical_on_resume(tmp_path):
    _init_demo(tmp_path)
    _commit(tmp_path, "fix(demo): only fix", "demo/core.py", "A = 1\n")
    project = _project()
    assert generate_release_changelog(tmp_path, project) is True
    _git(tmp_path, "add", "demo/CHANGES.md")
    _git(tmp_path, "commit", "-m", "chore(release): changelog")
    before = (tmp_path / "demo" / "CHANGES.md").read_bytes()

    assert generate_release_changelog(tmp_path, project) is False

    assert (tmp_path / "demo" / "CHANGES.md").read_bytes() == before


def test_rel02_stale_section_keeps_older_sections_intact(tmp_path):
    _init_demo(
        tmp_path,
        "# Changelog\n\n<!-- cmru: release history -->\n\n## [0.9.0] - 2020-01-01\n"
        "<!-- cmru: generated -->\n<!-- cmru: source-end=" + "0" * 40 + " -->\n\n- old\n",
    )
    _commit(tmp_path, "fix(demo): one", "demo/core.py", "A = 1\n")
    project = _project()
    generate_release_changelog(tmp_path, project)
    _git(tmp_path, "add", "demo/CHANGES.md")
    _git(tmp_path, "commit", "-m", "chore(release): changelog")
    _commit(tmp_path, "fix(demo): two", "demo/core.py", "A = 2\n")
    generate_release_changelog(tmp_path, project)

    history = (tmp_path / "demo" / "CHANGES.md").read_text(encoding="utf-8")
    assert "## [0.9.0] - 2020-01-01" in history
    assert "- old\n" in history
    assert history.index("## [1.0.1]") < history.index("## [0.9.0]")


def test_rel10_tagged_release_refuses_a_nonempty_plain_unreleased_section(tmp_path):
    _init_demo(
        tmp_path,
        "# Changelog\n\n## [Unreleased]\n\n### Added\n- hand-written entry\n\n"
        "<!-- cmru: release history -->\n",
    )
    _commit(tmp_path, "fix(demo): a fix", "demo/core.py", "A = 1\n")
    before = (tmp_path / "demo" / "CHANGES.md").read_bytes()

    try:
        generate_release_changelog(tmp_path, _project())
    except RuntimeError as exc:
        assert "KI-30" in str(exc)
        assert "[Unreleased]" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a non-empty [Unreleased] must block a tagged release")
    assert (tmp_path / "demo" / "CHANGES.md").read_bytes() == before


def test_rel10_cleared_unreleased_section_with_a_comment_is_accepted(tmp_path):
    _init_demo(
        tmp_path,
        "# Changelog\n\n## [Unreleased]\n\n<!-- cleared for 6.0.0; content regenerated -->\n\n"
        "<!-- cmru: release history -->\n",
    )
    _commit(tmp_path, "fix(demo): a fix", "demo/core.py", "A = 1\n")

    assert generate_release_changelog(tmp_path, _project()) is True
    assert "## [1.0.1]" in (tmp_path / "demo" / "CHANGES.md").read_text(encoding="utf-8")
