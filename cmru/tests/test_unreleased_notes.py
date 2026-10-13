"""KI-78: a project's [Unreleased] entries ride the release body and are consumed after the publish."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import changelog, cli, release, release_notes, transaction
from cmru.release_notes import CONSUMED_MARKER, ReleaseNotesError
from tests.test_release_notes import _git, _project, _release_repo

PENDING = (
    "### Added\n- a hand-written entry\n\n"
    "### Migration\n1. run `tool migrate` before upgrading\n\n"
    "```sh\ntool migrate --yes\n```"
)

DOCUMENT = f"""\
# Changelog

## [Unreleased]
<!-- keep this comment -->

{PENDING}

<!-- cmru: release history -->

## [2.0.0] - 2026-10-13
<!-- cmru: generated -->

### Fixed
- Repaired the ordering.

## [1.0.0] - 2026-09-01
### Added
- The first release.
"""


# --- the [Unreleased] reader --------------------------------------------------


def test_unreleased_notes_are_read_verbatim_without_comments():
    notes = release_notes.extract_release_section(DOCUMENT, "2.0.0", source="CHANGES.md")
    assert notes.unreleased == PENDING
    assert "keep this comment" not in notes.unreleased
    assert notes.text.startswith("## [2.0.0] - 2026-10-13")  # the section is unchanged


@pytest.mark.parametrize("document", [
    "## [1.0.0] - 2026-01-01\n- a\n",                                            # no section
    "## [Unreleased]\n\n## [1.0.0] - 2026-01-01\n- a\n",                         # empty
    "## [Unreleased]\n<!-- only a comment -->\n\n## [1.0.0] - 2026-01-01\n- a\n",  # comment only
])
def test_an_absent_or_empty_unreleased_section_has_no_notes(document):
    assert release_notes.unreleased_body(document) == ""
    assert release_notes.extract_release_section(
        document, "1.0.0", source="x",
    ).unreleased == ""


def test_the_unreleased_body_ends_at_the_history_marker_or_the_next_heading_or_the_end():
    marker = "## [Unreleased]\n- one\n<!-- cmru: release history -->\n- not mine\n"
    assert release_notes.unreleased_body(marker) == "- one"
    heading = "## [Unreleased]\n- one\n## [1.0.0] - 2026-01-01\n- two\n"
    assert release_notes.unreleased_body(heading) == "- one"
    assert release_notes.unreleased_body("## [Unreleased]\n- one\n") == "- one"


# --- ONE body builder ---------------------------------------------------------


def test_the_body_appends_the_unreleased_notes_below_the_generated_install_text():
    notes = release_notes.extract_release_section(DOCUMENT, "2.0.0", source="CHANGES.md")
    body = release.build_release_body(
        "demo", "2.0.0", artifact_name="demo-2.0.0.whl", sha256="ab" * 32, release_notes=notes,
    )
    assert body.endswith(notes.text + "\n\n" + PENDING)
    assert body.index("**Artifact:**") < body.index("## [2.0.0]") < body.index("### Migration")
    # an operator's --notes-env text still replaces only the opening line
    supplemented = release.build_release_body(
        "demo", "2.0.0", artifact_name="demo-2.0.0.whl", sha256="ab" * 32,
        notes="Hand-written intro", release_notes=notes,
    )
    assert supplemented.startswith("Hand-written intro\n\n**Artifact:**")
    assert supplemented.endswith(PENDING)


def test_without_pending_notes_the_body_is_unchanged():
    cleared = DOCUMENT.replace(PENDING, "")
    notes = release_notes.extract_release_section(cleared, "2.0.0", source="CHANGES.md")
    body = release.build_release_body(
        "demo", "2.0.0", artifact_name="a.whl", sha256="0" * 64, release_notes=notes,
    )
    assert body.endswith(notes.text)


# --- consuming ----------------------------------------------------------------


def test_consuming_moves_the_notes_into_the_released_version_and_clears_unreleased():
    updated = changelog.consume_unreleased_notes(DOCUMENT, "2.0.0")
    assert updated is not None
    # [Unreleased] is back to its empty state, keeping its own comment
    assert release_notes.unreleased_body(updated) == ""
    assert "## [Unreleased]\n\n<!-- keep this comment -->\n\n<!-- cmru: release history -->" in updated
    # the author's text, byte for byte, now ends the released section, marked
    section = release_notes.extract_release_section(updated, "2.0.0", source="x").text
    assert section.endswith(f"{CONSUMED_MARKER}\n{PENDING}")
    assert "Repaired the ordering." in section
    # older history is untouched and still separated by a blank line
    assert updated.endswith("## [1.0.0] - 2026-09-01\n### Added\n- The first release.\n")
    assert f"{PENDING}\n\n## [1.0.0]" in updated
    # idempotent: nothing left to consume
    assert changelog.consume_unreleased_notes(updated, "2.0.0") is None


def test_consuming_into_the_last_section_and_with_no_comment_to_keep():
    document = "## [Unreleased]\n- one\n<!-- cmru: release history -->\n\n## [1.0.0] - 2026-01-01\n- a\n"
    updated = changelog.consume_unreleased_notes(document, "1.0.0")
    assert updated == (
        "## [Unreleased]\n\n<!-- cmru: release history -->\n\n"
        f"## [1.0.0] - 2026-01-01\n- a\n\n{CONSUMED_MARKER}\n- one\n"
    )


def test_there_is_nothing_to_consume_without_an_unreleased_section_or_notes():
    assert changelog.consume_unreleased_notes("## [1.0.0] - 2026-01-01\n- a\n", "1.0.0") is None
    assert changelog.consume_unreleased_notes(
        "## [Unreleased]\n\n## [1.0.0] - 2026-01-01\n- a\n", "1.0.0",
    ) is None


def test_consuming_refuses_when_the_released_section_is_not_exactly_one():
    with pytest.raises(ReleaseNotesError, match=r"has 0 dated `## \[9\.9\.9\]` sections"):
        changelog.consume_unreleased_notes(DOCUMENT, "9.9.9")
    duplicated = DOCUMENT + "\n## [2.0.0] - 2026-10-14\n- again\n"
    with pytest.raises(ReleaseNotesError, match=r"has 2 dated `## \[2\.0\.0\]` sections"):
        changelog.consume_unreleased_notes(duplicated, "2.0.0")


def test_consume_published_notes_rewrites_the_file_only_when_there_is_something(tmp_path):
    project = _project()
    changes = tmp_path / "demo" / "CHANGES.md"
    changes.parent.mkdir()
    changes.write_text(DOCUMENT, encoding="utf-8")
    assert changelog.consume_published_notes(tmp_path, project, "demo-v2.0.0") is True
    assert release_notes.unreleased_body(changes.read_text(encoding="utf-8")) == ""
    after = changes.read_bytes()
    assert changelog.consume_published_notes(tmp_path, project, "demo-v2.0.0") is False
    assert changes.read_bytes() == after


# --- the transaction hook -----------------------------------------------------


@pytest.mark.real_release_notes_preflight
def test_the_hook_commits_the_move_with_the_candidate_trailer(tmp_path):
    repo = _release_repo(tmp_path)
    project = _project()
    changes = repo / "demo" / "CHANGES.md"
    changes.write_text(DOCUMENT, encoding="utf-8")
    _git(repo, "add", "demo/CHANGES.md")
    _git(repo, "commit", "-q", "-m", "docs: pending notes")
    published_head = _git(repo, "rev-parse", "HEAD")

    assert cli._consume_published_unreleased_notes(repo, project, "demo-v2.0.0") is True

    assert _git(repo, "rev-parse", "HEAD~1") == published_head  # exactly one commit on top
    message = _git(repo, "log", "-1", "--format=%B")
    assert message.startswith("chore(demo): move [Unreleased] notes into demo-v2.0.0")
    assert "Cmru-Release-Candidate: demo-v2.0.0" in message
    assert _git(repo, "show", "--stat", "--format=", "HEAD").count("|") == 1  # only CHANGES.md
    assert _git(repo, "status", "--porcelain") == ""
    assert release_notes.unreleased_body(changes.read_text(encoding="utf-8")) == ""
    # nothing left: no second commit
    commits = _git(repo, "rev-list", "--count", "HEAD")
    assert cli._consume_published_unreleased_notes(repo, project, "demo-v2.0.0") is False
    assert _git(repo, "rev-list", "--count", "HEAD") == commits


@pytest.mark.real_release_notes_preflight
def test_the_hook_does_nothing_for_a_project_without_a_changelog(tmp_path):
    project = _project()
    project.changelog = None
    assert cli._consume_published_unreleased_notes(tmp_path, project, "demo-v1.0.0") is False


@pytest.mark.real_release_notes_preflight
def test_the_dry_run_shows_the_unreleased_notes_and_keeps_them_in_the_file(tmp_path, capsys):
    repo = _release_repo(tmp_path)
    changes = repo / "demo" / "CHANGES.md"
    changes.write_text(
        "# Changelog\n\n## [Unreleased]\n- run `tool migrate` first\n\n"
        "<!-- cmru: release history -->\n", encoding="utf-8",
    )
    _git(repo, "add", "demo/CHANGES.md")
    _git(repo, "commit", "-q", "-m", "docs: pending notes")
    before = changes.read_bytes()

    cli._preview_release_bodies(repo, {"demo": _project()}, ["demo"])

    output = capsys.readouterr().out
    shown = output.split("----- begin release body -----\n", 1)[1].split(
        "\n----- end release body -----", 1,
    )[0]
    assert shown.endswith("\n\n- run `tool migrate` first")
    assert shown.index("**Artifact:**") < shown.index("## [1.0.1]") < shown.index("tool migrate")
    assert "its pending [Unreleased] notes are appended and stay in the file" in output
    assert changes.read_bytes() == before  # a dry run consumes nothing


def _sequence_project() -> SimpleNamespace:
    return SimpleNamespace(
        name="demo", cwd="demo", project_root=Path("demo"), paths=["demo"], runner_steps={},
        steps={}, env={}, github_token="tok", prefix="demo-v", scm_dist=None, git_tag=True,
        build_step="build", version=SimpleNamespace(strategy="scm"),
        changelog="CHANGES.md", commit_generated=(),
    )


def _drive(monkeypatch, tmp_path, *, no_build=False, publish=None, calls=None):
    calls = [] if calls is None else calls
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path, "cmru/release/x", "a" * 40)
    monkeypatch.setattr(cli.transaction, "write_release_progress", lambda *a: None)
    monkeypatch.setattr(cli.transaction, "write_release_result", lambda *a, **k: calls.append("result"))
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *a: None)
    monkeypatch.setattr(cli, "_prepare_release_projects", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_run_release_gates", lambda *a: None)
    monkeypatch.setattr(cli.transaction, "promote_workspace", lambda *_, **__: calls.append("promote"))
    monkeypatch.setattr(cli.transaction, "push_backup_branch", lambda *_, **__: None)
    monkeypatch.setattr(cli, "_git", lambda *_a, **_k: "b" * 40)
    monkeypatch.setattr(cli, "_tag_on_head", lambda *_, **__: "demo-v1.2.3")
    monkeypatch.setattr(cli, "_push_tags", lambda *_a, **_k: calls.append("tag"))
    monkeypatch.setattr("cmru.version.release_cmd", lambda *a, **k: None)
    monkeypatch.setattr(
        cli, "_run_tagged_build_and_publish",
        publish or (lambda *a, **k: calls.append("publish")),
    )
    monkeypatch.setattr(
        cli, "_consume_published_unreleased_notes",
        lambda _root, _project, tag: calls.append(f"consume:{tag}") or True,
    )
    cli._release_projects_sequentially(
        tmp_path, {"demo": _sequence_project()}, workspace, ["demo"],
        github_config=SimpleNamespace(), env_config=SimpleNamespace(), no_build=no_build,
    )
    return calls


@pytest.mark.real_release_notes_preflight
def test_notes_are_consumed_after_the_publish_and_before_promotion(monkeypatch, tmp_path):
    calls = _drive(monkeypatch, tmp_path)
    assert calls == ["tag", "publish", "result", "consume:demo-v1.2.3", "promote"]


@pytest.mark.real_release_notes_preflight
def test_a_failed_publish_keeps_the_notes_where_they_were(monkeypatch, tmp_path):
    calls: list[str] = []

    def failing(*_args, **_kwargs):
        calls.append("publish")
        raise RuntimeError("upload failed")

    with pytest.raises(RuntimeError, match="upload failed"):
        _drive(monkeypatch, tmp_path, publish=failing, calls=calls)
    assert calls == ["tag", "publish"]  # no result, no consume, no promotion


@pytest.mark.real_release_notes_preflight
def test_a_no_build_release_publishes_nothing_so_consumes_nothing(monkeypatch, tmp_path):
    calls = _drive(monkeypatch, tmp_path, no_build=True)
    assert calls == ["tag", "result", "promote"]
