"""KI-78: pending notes enter the tagged release section before its gate."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import changelog, cli, release, release_notes, transaction
from cmru.errors import RefusedBeforeChange
from cmru.release_notes import PENDING_NOTES_BEGIN, PENDING_NOTES_END, ReleaseNotesError
from tests.test_release_notes import _git, _project, _release_repo

PENDING = (
    "### Added\n- a hand-written entry\n\n"
    "### Migration\n1. run `tool migrate` before upgrading\n\n"
    "```sh\ntool migrate --yes\n```"
)


@pytest.fixture(autouse=True)
def default_release_source_for_synthetic_transactions(monkeypatch):
    monkeypatch.setattr(transaction, "read_release_source", lambda *_args: None)

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


def test_unreleased_notes_are_read_without_comments_and_not_mixed_with_the_version_section():
    notes = release_notes.extract_release_section(DOCUMENT, "2.0.0", source="CHANGES.md")
    assert release_notes.unreleased_body(DOCUMENT) == PENDING
    assert "keep this comment" not in release_notes.unreleased_body(DOCUMENT)
    assert notes.text.startswith("## [2.0.0] - 2026-10-13")  # the section is unchanged


@pytest.mark.parametrize("spacing", [" ", "  ", "\t "])
def test_unreleased_and_version_headings_accept_markdown_whitespace(spacing):
    document = DOCUMENT.replace("## [Unreleased]", f"##{spacing}[Unreleased]")
    document = document.replace("## [2.0.0]", f"##{spacing}[2.0.0]")

    assert release_notes.unreleased_body(document) == PENDING
    notes = release_notes.extract_release_section(document, "2.0.0", source="CHANGES.md")
    folded = changelog.fold_unreleased_notes(document, "2.0.0")

    # The heading is matched whitespace-tolerantly, but the section is published
    # verbatim (README, DESIGN-GUIDE "Exact section, verbatim"), spacing included.
    assert notes.text.startswith(f"##{spacing}[2.0.0] - 2026-10-13")
    assert folded is not None
    assert PENDING in release_notes.extract_release_section(
        folded, "2.0.0", source="CHANGES.md",
    ).text


@pytest.mark.parametrize("document", [
    "## [1.0.0] - 2026-01-01\n- a\n",                                            # no section
    "## [Unreleased]\n\n## [1.0.0] - 2026-01-01\n- a\n",                         # empty
    "## [Unreleased]\n<!-- only a comment -->\n\n## [1.0.0] - 2026-01-01\n- a\n",  # comment only
])
def test_an_absent_or_empty_unreleased_section_has_no_notes(document):
    assert release_notes.unreleased_body(document) == ""
    assert release_notes.extract_release_section(document, "1.0.0", source="x").text


def test_the_unreleased_body_ends_at_the_history_marker_or_the_next_heading_or_the_end():
    marker = "## [Unreleased]\n- one\n<!-- cmru: release history -->\n- not mine\n"
    assert release_notes.unreleased_body(marker) == "- one"
    heading = "## [Unreleased]\n- one\n## [1.0.0] - 2026-01-01\n- two\n"
    assert release_notes.unreleased_body(heading) == "- one"
    assert release_notes.unreleased_body("## [Unreleased]\n- one\n") == "- one"


def test_headings_inside_html_comments_and_fenced_code_do_not_end_sections():
    document = (
        "<!--\n## [Unreleased]\n## [2.0.0] - 2026-10-13\n-->\n"
        "## [Unreleased]\n- real note\n```markdown\n## [1.0.0] - fake\n"
        "<!-- cmru: release history -->\n- not a marker\n"
        "<!-- literal code comment -->\n````\n"
        "<!-- cmru: release history -->\n\n"
        "## [2.0.0] - 2026-10-13\n- released\n"
    )
    assert release_notes.unreleased_body(document) == (
        "- real note\n```markdown\n## [1.0.0] - fake\n"
        "<!-- cmru: release history -->\n- not a marker\n"
        "<!-- literal code comment -->\n````"
    )
    notes = release_notes.extract_release_section(document, "2.0.0", source="x")
    assert notes.text == "## [2.0.0] - 2026-10-13\n- released"
    folded = changelog.fold_unreleased_notes(document, "2.0.0")
    assert folded is not None
    section = release_notes.extract_release_section(folded, "2.0.0", source="x").text
    assert "- real note" in section
    assert section.count("<!-- literal code comment -->") == 1


def test_a_backtick_inside_the_info_string_does_not_open_a_markdown_fence():
    headings = release_notes.markdown_h2_headings(
        "```invalid`info\n## [2.0.0] - 2026-10-13\n- visible heading\n",
    )
    assert [heading.text for heading in headings] == ["## [2.0.0] - 2026-10-13"]
    tilde_headings = release_notes.markdown_h2_headings(
        "~~~markdown\n## [1.0.0] - fake\n~~~\n## [2.0.0] - 2026-10-13\n",
    )
    assert [heading.text for heading in tilde_headings] == ["## [2.0.0] - 2026-10-13"]


@pytest.mark.parametrize("heading", ["## [unreleased]", "## Unreleased", "## [Unreleased] - 2026-01-01"])
def test_near_miss_unreleased_headings_refuse_instead_of_hiding_notes(heading):
    with pytest.raises(ReleaseNotesError, match=r"at most one exact `## \[Unreleased\]` heading"):
        release_notes.unreleased_body(f"{heading}\n- pending\n")


def test_duplicate_unreleased_headings_refuse():
    document = "## [Unreleased]\n- one\n## [Unreleased]\n- two\n"
    with pytest.raises(ReleaseNotesError, match=r"at most one exact `## \[Unreleased\]` heading"):
        release_notes.unreleased_body(document)


# --- ONE body builder ---------------------------------------------------------


def test_the_body_appends_the_tagged_section_that_contains_folded_pending_notes():
    folded = changelog.fold_unreleased_notes(DOCUMENT, "2.0.0")
    assert folded is not None
    notes = release_notes.extract_release_section(folded, "2.0.0", source="CHANGES.md")
    body = release.build_release_body(
        "demo", "2.0.0", artifact_name="demo-2.0.0.whl", sha256="ab" * 32, release_notes=notes,
    )
    assert body.endswith(notes.text)
    assert body.index("**Artifact:**") < body.index("## [2.0.0]") < body.index("### Migration")
    assert body.count("a hand-written entry") == 1
    assert PENDING_NOTES_BEGIN in body and PENDING_NOTES_END in body
    # an operator's --notes-env text still replaces only the opening line
    supplemented = release.build_release_body(
        "demo", "2.0.0", artifact_name="demo-2.0.0.whl", sha256="ab" * 32,
        notes="Hand-written intro", release_notes=notes,
    )
    assert supplemented.startswith("Hand-written intro\n\n**Artifact:**")
    assert supplemented.endswith(notes.text)


def test_without_pending_notes_the_body_is_unchanged():
    cleared = DOCUMENT.replace(PENDING, "")
    notes = release_notes.extract_release_section(cleared, "2.0.0", source="CHANGES.md")
    body = release.build_release_body(
        "demo", "2.0.0", artifact_name="a.whl", sha256="0" * 64, release_notes=notes,
    )
    assert body.endswith(notes.text)


# --- candidate preparation ----------------------------------------------------


def test_folding_moves_notes_into_the_candidate_version_and_clears_unreleased():
    updated = changelog.fold_unreleased_notes(DOCUMENT, "2.0.0")
    assert updated is not None
    # [Unreleased] is back to its empty state, keeping its own comment
    assert release_notes.unreleased_body(updated) == ""
    assert "## [Unreleased]\n\n<!-- keep this comment -->\n\n<!-- cmru: release history -->" in updated
    # the author's text, byte for byte, now ends the released section, marked
    section = release_notes.extract_release_section(updated, "2.0.0", source="x").text
    assert section.endswith(f"{PENDING_NOTES_BEGIN}\n{PENDING}\n{PENDING_NOTES_END}")
    assert "Repaired the ordering." in section
    # older history is untouched and still separated by a blank line
    assert updated.endswith("## [1.0.0] - 2026-09-01\n### Added\n- The first release.\n")
    assert f"{PENDING_NOTES_END}\n\n## [1.0.0]" in updated
    # idempotent: the candidate section already owns the notes
    assert changelog.fold_unreleased_notes(updated, "2.0.0") is None


def test_a_second_release_can_fold_notes_when_the_prior_version_has_fold_markers():
    first_release = changelog.fold_unreleased_notes(DOCUMENT, "2.0.0")
    assert first_release is not None
    second_source = first_release.replace(
        "<!-- cmru: release history -->\n\n## [2.0.0] - 2026-10-13",
        "<!-- cmru: release history -->\n\n"
        "## [2.1.0] - 2026-10-14\n### Fixed\n- The next repair.\n\n"
        "## [2.0.0] - 2026-10-13",
    ).replace(
        "## [Unreleased]\n\n<!-- keep this comment -->",
        "## [Unreleased]\n\n<!-- keep this comment -->\n\n- notes for 2.1.0",
    )

    second_release = changelog.fold_unreleased_notes(second_source, "2.1.0")

    assert second_release is not None
    assert "notes for 2.1.0" in release_notes.extract_release_section(
        second_release, "2.1.0", source="CHANGES.md",
    ).text
    assert "migrate --yes" in release_notes.extract_release_section(
        second_release, "2.0.0", source="CHANGES.md",
    ).text


@pytest.mark.real_release_notes_preflight
def test_regenerating_a_stale_section_preserves_its_folded_pending_notes(tmp_path):
    repo = _release_repo(tmp_path)
    project = _project()
    changes = repo / "demo" / "CHANGES.md"
    changes.write_text(
        "# Changelog\n\n## [Unreleased]\n- migrate before upgrading\n\n"
        "<!-- cmru: release history -->\n",
        encoding="utf-8",
    )
    _git(repo, "add", "demo/CHANGES.md")
    _git(repo, "commit", "-q", "-m", "docs: pending migration note")
    assert changelog.generate_release_changelog(repo, project) is True
    notes = changelog.pending_release_notes(repo, project)
    folded = changelog.fold_unreleased_notes(changes.read_text(encoding="utf-8"), notes.version)
    assert folded is not None
    changes.write_text(folded, encoding="utf-8")

    (repo / "demo" / "core.py").write_text("A = 3\n", encoding="utf-8")
    _git(repo, "add", "demo/core.py")
    _git(repo, "commit", "-q", "-m", "fix(demo): second repair")
    assert changelog.generate_release_changelog(repo, project) is True
    section = release_notes.extract_release_section(
        changes.read_text(encoding="utf-8"), notes.version, source="CHANGES.md",
    ).text
    assert section.count("migrate before upgrading") == 1
    assert PENDING_NOTES_BEGIN in section and PENDING_NOTES_END in section


@pytest.mark.real_release_notes_preflight
def test_regenerating_a_stale_section_with_malformed_folded_notes_refuses_before_change(tmp_path):
    repo = _release_repo(tmp_path)
    project = _project()
    changes = repo / "demo" / "CHANGES.md"
    changes.write_text(
        "# Changelog\n\n## [Unreleased]\n- migrate before upgrading\n\n"
        "<!-- cmru: release history -->\n",
        encoding="utf-8",
    )
    _git(repo, "add", "demo/CHANGES.md")
    _git(repo, "commit", "-q", "-m", "docs: pending migration note")
    assert changelog.generate_release_changelog(repo, project) is True
    notes = changelog.pending_release_notes(repo, project)
    folded = changelog.fold_unreleased_notes(changes.read_text(encoding="utf-8"), notes.version)
    assert folded is not None
    changes.write_text(folded, encoding="utf-8")
    _git(repo, "add", "demo/CHANGES.md")
    _git(repo, "commit", "-q", "-m", "chore(release): record pending notes")

    (repo / "demo" / "core.py").write_text("A = 3\n", encoding="utf-8")
    _git(repo, "add", "demo/core.py")
    _git(repo, "commit", "-q", "-m", "fix(demo): second repair")
    malformed = changes.read_text(encoding="utf-8").replace(
        f"{PENDING_NOTES_END}\n", "<!-- broken pending-note end -->\n",
    )
    changes.write_text(malformed, encoding="utf-8")
    before = changes.read_bytes()

    with pytest.raises(RefusedBeforeChange, match="invalid CMRU pending-note block"):
        changelog.generate_release_changelog(repo, project)

    assert changes.read_bytes() == before


@pytest.mark.real_release_notes_preflight
def test_release_prepare_folds_notes_before_the_candidate_commit(tmp_path):
    repo = _release_repo(tmp_path)
    project = _project()
    changes = repo / "demo" / "CHANGES.md"
    changes.write_text(
        "# Changelog\n\n## [Unreleased]\n- migrate before upgrading\n\n"
        "<!-- cmru: release history -->\n",
        encoding="utf-8",
    )
    _git(repo, "add", "demo/CHANGES.md")
    _git(repo, "commit", "-q", "-m", "docs: pending migration note")

    cli._prepare_release_projects(repo, {"demo": project}, ["demo"])

    assert release_notes.unreleased_body(changes.read_text(encoding="utf-8")) == ""
    notes = release_notes.extract_release_section(
        changes.read_text(encoding="utf-8"), "1.0.1", source="CHANGES.md",
    )
    assert "migrate before upgrading" in notes.text
    assert _git(repo, "show", "--format=%B", "-1").startswith(
        "chore(demo): prepare release inputs"
    )


@pytest.mark.real_release_notes_preflight
def test_non_body_publishers_still_fold_pending_notes_into_the_tagged_changelog(tmp_path):
    repo = _release_repo(tmp_path)
    project = _project()
    project.runner_steps = {}
    changes = repo / "demo" / "CHANGES.md"
    changes.write_text(
        "# Changelog\n\n## [Unreleased]\n- keep pending\n\n"
        "<!-- cmru: release history -->\n",
        encoding="utf-8",
    )
    _git(repo, "add", "demo/CHANGES.md")
    _git(repo, "commit", "-q", "-m", "docs: pending migration note")

    cli._prepare_release_projects(repo, {"demo": project}, ["demo"])

    updated = changes.read_text(encoding="utf-8")
    assert release_notes.unreleased_body(updated) == ""
    section = release_notes.extract_release_section(updated, "1.0.1", source="CHANGES.md")
    assert "keep pending" in section.text
    assert PENDING_NOTES_BEGIN in section.text


def test_folding_into_the_last_section_and_with_no_comment_to_keep():
    document = "## [Unreleased]\n- one\n<!-- cmru: release history -->\n\n## [1.0.0] - 2026-01-01\n- a\n"
    updated = changelog.fold_unreleased_notes(document, "1.0.0")
    assert updated == (
        "## [Unreleased]\n\n<!-- cmru: release history -->\n\n"
        f"## [1.0.0] - 2026-01-01\n- a\n\n{PENDING_NOTES_BEGIN}\n- one\n{PENDING_NOTES_END}\n"
    )


@pytest.mark.parametrize("block", [
    f"{PENDING_NOTES_BEGIN}\n- one\n{PENDING_NOTES_BEGIN}\n- two\n{PENDING_NOTES_END}\n",
    f"{PENDING_NOTES_BEGIN}{PENDING_NOTES_END}\n",
    f"{PENDING_NOTES_BEGIN}\n\n{PENDING_NOTES_END}\n",
])
def test_malformed_folded_note_blocks_refuse(block):
    with pytest.raises(ReleaseNotesError):
        changelog._folded_notes_block(block)


def test_folding_refuses_when_unreleased_notes_and_a_folded_block_both_exist():
    document = (
        f"## [Unreleased]\n- still pending\n\n"
        f"## [1.0.0] - 2026-01-01\n- released\n\n"
        f"{PENDING_NOTES_BEGIN}\n- already folded\n{PENDING_NOTES_END}\n"
    )
    with pytest.raises(ReleaseNotesError, match="target release section already has a folded-note block"):
        changelog.fold_unreleased_notes(document, "1.0.0")


@pytest.mark.real_release_notes_preflight
def test_folding_refusal_is_converted_to_a_release_preflight_refusal(tmp_path):
    repo = _release_repo(tmp_path)
    project = _project()
    changes = repo / "demo" / "CHANGES.md"
    changes.write_text("## [Unreleased]\n- note\n", encoding="utf-8")
    with pytest.raises(RefusedBeforeChange, match="release notes"):
        cli._fold_pending_release_notes(repo, project, "1.0.1")


@pytest.mark.real_release_notes_preflight
def test_duplicate_version_headings_in_a_generated_changelog_refuse(tmp_path):
    repo = _release_repo(tmp_path)
    project = _project()
    (repo / "demo" / "CHANGES.md").write_text(
        "# Changelog\n\n"
        "## [1.0.1] - 2026-10-13\n<!-- cmru: generated -->\n- one\n\n"
        "## [1.0.1] - 2026-10-14\n<!-- cmru: generated -->\n- two\n",
        encoding="utf-8",
    )
    with pytest.raises(RefusedBeforeChange, match="requires exactly one"):
        changelog.generate_release_changelog(repo, project)


@pytest.mark.parametrize("argv, expected", [
    (["cmru", "handler", "wheel-publish"], True),
    (["cmru", "handler", "tarball-publish"], True),
    (["cmru", "handler", "oci-image-release"], True),
    (["cmru", "handler", "oci-image-push"], False),
    (["cmru", "handler", "unsupported"], False),
    (["python", "-m", "cmru.handlers", "wheel-publish"], False),
    (["cmru"], False),
])
def test_only_declared_cmru_release_body_handlers_qualify(argv, expected):
    project = _project()
    project.runner_steps = {"push": SimpleNamespace(commands=[{"argv": argv}])}
    assert cli._has_cmru_github_release_body(project) is expected


def test_there_is_nothing_to_consume_without_an_unreleased_section_or_notes():
    assert changelog.fold_unreleased_notes("## [1.0.0] - 2026-01-01\n- a\n", "1.0.0") is None
    assert changelog.fold_unreleased_notes(
        "## [Unreleased]\n\n## [1.0.0] - 2026-01-01\n- a\n", "1.0.0",
    ) is None


def test_consuming_refuses_when_the_released_section_is_not_exactly_one():
    with pytest.raises(ReleaseNotesError, match=r"has 0 dated `## \[9\.9\.9\]` sections"):
        changelog.fold_unreleased_notes(DOCUMENT, "9.9.9")
    duplicated = DOCUMENT + "\n## [2.0.0] - 2026-10-14\n- again\n"
    with pytest.raises(ReleaseNotesError, match=r"has 2 dated `## \[2\.0\.0\]` sections"):
        changelog.fold_unreleased_notes(duplicated, "2.0.0")


def _project() -> SimpleNamespace:
    return SimpleNamespace(
        name="demo", cwd="demo", paths=["demo"], prefix="demo-v", git_tag=True,
        changelog="CHANGES.md", commit_generated=(), steps={},
        runner_steps={"push": SimpleNamespace(
            commands=[{"argv": ["cmru", "handler", "wheel-publish"]}],
        )},
        version=SimpleNamespace(strategy="scm", bump="conventional"),
        known_project_names=frozenset(),
    )


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
    assert shown.endswith(
        f"{PENDING_NOTES_BEGIN}\n- run `tool migrate` first\n{PENDING_NOTES_END}"
    )
    assert shown.index("**Artifact:**") < shown.index("## [1.0.1]") < shown.index("tool migrate")
    assert "pending notes, when present, are folded into this version section" in output
    assert changes.read_bytes() == before  # a dry run consumes nothing


@pytest.mark.real_release_notes_preflight
def test_dry_run_previews_tag_notes_without_claiming_a_github_body(tmp_path, capsys):
    repo = _release_repo(tmp_path)
    project = _project()
    changes = repo / "demo" / "CHANGES.md"
    changes.write_text(
        "# Changelog\n\n## [Unreleased]\n- keep migration notes\n\n"
        "<!-- cmru: release history -->\n", encoding="utf-8",
    )
    _git(repo, "add", "demo/CHANGES.md")
    _git(repo, "commit", "-q", "-m", "docs: pending notes")
    before = changes.read_bytes()
    project.runner_steps = {}
    cli._preview_release_bodies(repo, {"demo": project}, ["demo"])
    output = capsys.readouterr().out
    assert "no CMRU GitHub Release-body handler" in output
    assert "keep migration notes" in output
    assert "begin release body" not in output
    assert changes.read_bytes() == before

    cli._preview_release_bodies(repo, {"demo": _project()}, ["demo"], no_build=True)
    output = capsys.readouterr().out
    assert "--no-build skips publication" in output
    assert "keep migration notes" in output
    assert "begin release body" not in output
    assert changes.read_bytes() == before


def _sequence_project() -> SimpleNamespace:
    return SimpleNamespace(
        name="demo", cwd="demo", project_root=Path("demo"), paths=["demo"],
        runner_steps={"push": SimpleNamespace(commands=[{"argv": ["cmru", "handler", "wheel-publish"]}])},
        steps={}, env={}, github_token="tok", prefix="demo-v", scm_dist=None, git_tag=True,
        build_step="build", version=SimpleNamespace(strategy="scm"),
        changelog="CHANGES.md", commit_generated=(), known_project_names=frozenset(),
    )


def _drive(monkeypatch, tmp_path, *, no_build=False, publish=None, calls=None):
    calls = [] if calls is None else calls
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path, "cmru/release/x", "a" * 40)
    monkeypatch.setattr(cli.transaction, "write_release_progress", lambda *a: None)
    monkeypatch.setattr(cli.transaction, "write_release_result", lambda *a, **k: calls.append("result"))
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *a: None)
    monkeypatch.setattr(cli, "_prepare_release_projects", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_run_release_gates", lambda *a, **_kw: None)
    monkeypatch.setattr(cli.transaction, "promote_workspace", lambda *_, **__: calls.append("promote"))
    monkeypatch.setattr(cli.transaction, "push_backup_branch", lambda *_, **__: None)
    monkeypatch.setattr(cli, "_git", lambda *_a, **_k: "b" * 40)
    monkeypatch.setattr(cli, "_assert_release_gate_source_matches_commit", lambda *_a, **_k: None)
    monkeypatch.setattr(cli, "_tag_on_head", lambda *_, **__: "demo-v1.2.3")
    monkeypatch.setattr(cli, "_push_tags", lambda *_a, **_k: calls.append("tag"))
    monkeypatch.setattr("cmru.version.release_cmd", lambda *a, **k: None)
    monkeypatch.setattr(
        cli, "_run_tagged_build_and_publish",
        publish or (lambda *a, **k: calls.append("publish")),
    )
    cli._release_projects_sequentially(
        tmp_path, {"demo": _sequence_project()}, workspace, ["demo"],
        github_config=SimpleNamespace(), env_config=SimpleNamespace(), no_build=no_build,
    )
    return calls


@pytest.mark.real_release_notes_preflight
def test_release_has_no_post_publish_changelog_commit(monkeypatch, tmp_path):
    calls = _drive(monkeypatch, tmp_path)
    assert calls == ["tag", "publish", "result", "promote"]


@pytest.mark.real_release_notes_preflight
def test_a_failed_publish_keeps_the_notes_where_they_were(monkeypatch, tmp_path):
    calls: list[str] = []

    def failing(*_args, **_kwargs):
        calls.append("publish")
        raise RuntimeError("upload failed")

    with pytest.raises(RuntimeError, match="upload failed"):
        _drive(monkeypatch, tmp_path, publish=failing, calls=calls)
    assert calls == ["tag", "publish"]  # no result or promotion


@pytest.mark.real_release_notes_preflight
def test_a_no_build_release_publishes_nothing_and_promotes_without_a_note_move(monkeypatch, tmp_path):
    calls = _drive(monkeypatch, tmp_path, no_build=True)
    assert calls == ["tag", "result", "promote"]


# --- every consumer: custom changelog names and failed promotion ------------------


def _tagged_repo(tmp_path: Path, changelog: str) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t.invalid")
    _git(tmp_path, "config", "user.name", "t")
    for path, text in ((f"demo/{changelog}", "# Changelog\n"), ("demo/core.py", "A = 1\n")):
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text, encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "feat(demo): first")
    _git(tmp_path, "tag", "-a", "demo-v1.0.0", "-m", "demo-v1.0.0")
    return tmp_path


@pytest.mark.parametrize("name", ["CHANGES.md", "HISTORY.md", "docs/RELEASES.md"])
def test_a_changelog_only_commit_never_schedules_a_release_whatever_it_is_called(tmp_path, name):
    from cmru import version

    repo = _tagged_repo(tmp_path, name.split("/")[-1])
    project = _project()
    project.changelog = name
    project.version = SimpleNamespace(strategy="scm", bump="conventional")
    target = repo / "demo" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Changelog\n\nmoved [Unreleased] notes\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "chore(demo): move [Unreleased] notes into demo-v1.0.0")

    assert version.detect_changed_projects(repo, {"demo": project}) == []

    (repo / "demo" / "core.py").write_text("A = 2\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "fix(demo): real work")
    assert [item[0] for item in version.detect_changed_projects(repo, {"demo": project})] == ["demo"]


@pytest.mark.real_release_notes_preflight
def test_candidate_fold_works_for_a_custom_changelog_path(tmp_path):
    repo = _release_repo(tmp_path)
    project = _project()
    project.changelog = "docs/RELEASES.md"
    target = repo / "demo" / "docs" / "RELEASES.md"
    target.parent.mkdir(parents=True)
    target.write_text(DOCUMENT, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "docs: pending notes")

    updated = changelog.fold_unreleased_notes(target.read_text(encoding="utf-8"), "2.0.0")
    assert updated is not None
    target.write_text(updated, encoding="utf-8")

    assert "demo/docs/RELEASES.md" in _git(repo, "status", "--porcelain")
    assert release_notes.unreleased_body(target.read_text(encoding="utf-8")) == ""
    assert PENDING_NOTES_BEGIN in target.read_text(encoding="utf-8")


@pytest.mark.real_release_notes_preflight
def test_a_failed_promotion_has_no_post_publish_changelog_commit(
    monkeypatch, tmp_path,
):
    """main is changed only by the promotion push, so its [Unreleased] survives this failure."""
    calls: list[str] = []
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path, "cmru/release/x", "a" * 40)
    monkeypatch.setattr(cli.transaction, "write_release_progress", lambda *a: None)
    monkeypatch.setattr(cli.transaction, "write_release_result", lambda *a, **k: None)
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *a: None)
    monkeypatch.setattr(cli, "_prepare_release_projects", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_run_release_gates", lambda *a, **_kw: None)
    monkeypatch.setattr(cli.transaction, "push_backup_branch", lambda *_, **__: None)
    monkeypatch.setattr(cli, "_git", lambda *_a, **_k: "b" * 40)
    monkeypatch.setattr(cli, "_assert_release_gate_source_matches_commit", lambda *_a, **_k: None)
    monkeypatch.setattr(cli, "_tag_on_head", lambda *_, **__: "demo-v1.2.3")
    monkeypatch.setattr(cli, "_push_tags", lambda *_a, **_k: None)
    monkeypatch.setattr("cmru.version.release_cmd", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_run_tagged_build_and_publish", lambda *a, **k: None)

    def lost_race(*_args, **_kwargs):
        calls.append("promote")
        raise RuntimeError("release candidate was not promoted")

    monkeypatch.setattr(cli.transaction, "promote_workspace", lost_race)
    with pytest.raises(RuntimeError, match="not promoted"):
        cli._release_projects_sequentially(
            tmp_path, {"demo": _sequence_project()}, workspace, ["demo"],
            github_config=SimpleNamespace(), env_config=SimpleNamespace(),
        )
    assert calls == ["promote"]
