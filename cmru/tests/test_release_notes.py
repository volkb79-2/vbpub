"""KI-75: a release body is the generated install block plus the exact curated section."""
from __future__ import annotations

import dataclasses
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import changelog, cli, exit_codes, handlers, release, release_notes
from cmru.errors import RefusedBeforeChange, UsageRefusal
from cmru.release_notes import ReleaseNotes, ReleaseNotesError

CHANGES = """\
# Changelog

<!-- cmru: release history -->

## [Unreleased]
<!-- cleared: nothing pending -->

## [2.0.0] - 2026-10-13
<!-- cmru: generated -->
<!-- cmru: source-end=0123456789abcdef0123456789abcdef01234567 -->

### Added
- A new `--flag` with `code` and **emphasis**.

### Fixed
- Repaired the ordering.

### Migration
1. Replace `old` with `new`.

```toml
[section]
key = "value"
```

## [1.0.0] - 2026-09-01
### Added
- The first release.
"""


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


# --- the section extractor ---------------------------------------------------


def test_the_exact_version_section_is_returned_verbatim_without_neighbours():
    notes = release_notes.extract_release_section(CHANGES, "2.0.0", source="CHANGES.md")
    assert notes.version == "2.0.0" and notes.source == "CHANGES.md"
    assert notes.text.startswith("## [2.0.0] - 2026-10-13\n")
    assert notes.text.endswith('key = "value"\n```')
    assert "Repaired the ordering." in notes.text and "Migration" in notes.text
    assert "first release" not in notes.text
    # exactly the document's own bytes for that section
    start = CHANGES.index("## [2.0.0]")
    assert CHANGES[start:].startswith(notes.text)
    older = release_notes.extract_release_section(CHANGES, "1.0.0", source="CHANGES.md")
    assert older.text == "## [1.0.0] - 2026-09-01\n### Added\n- The first release."


@pytest.mark.parametrize("version, text, match", [
    ("3.0.0", CHANGES, r"no `## \[3\.0\.0\] - YYYY-MM-DD` section .*found: \[Unreleased\], \[2\.0\.0\]"),
    ("1.0", CHANGES, "no `## \\[1\\.0\\] - YYYY-MM-DD` section"),
    ("Unreleased", CHANGES, r"is not `## \[Unreleased\] - YYYY-MM-DD`"),
    ("4.0.0", "## [4.0.0] - UNRELEASED\n- soon\n", r"is not `## \[4\.0\.0\] - YYYY-MM-DD`"),
    ("5.0.0", "plain prose, no sections\n", "found: no `## ` sections"),
    ("6.0.0", "## [6.0.0] - 2026-01-01\n- a\n## [6.0.0] - 2026-01-02\n- b\n", "has 2 `## \\[6\\.0\\.0\\]` sections"),
    ("7.0.0", "## [7.0.0] - 2026-01-01\n\n", "has no release notes"),
    ("7.0.1", "## [7.0.1] - 2026-01-01\n<!-- cmru: generated -->\n\n## [7.0.0] - 2025-01-01\n- x\n", "has no release notes"),
])
def test_a_missing_malformed_duplicate_or_empty_section_refuses_precisely(version, text, match):
    with pytest.raises(ReleaseNotesError, match=match):
        release_notes.extract_release_section(text, version, source="CHANGES.md")


def test_the_last_section_runs_to_the_end_of_the_document():
    notes = release_notes.extract_release_section(
        "# Changelog\n## [1.0.0] - 2026-01-01\n- only\n\n\n", "1.0.0", source="x",
    )
    assert notes.text == "## [1.0.0] - 2026-01-01\n- only"


# --- reading the declared source ----------------------------------------------


def test_declarations_map_a_project_changelog_to_a_protected_source():
    assert release_notes.declaration_for("CHANGES.md") == "changelog:CHANGES.md"
    assert release_notes.declaration_for(None) == "none"


def test_a_project_declaring_no_changelog_publishes_the_generated_body_only(tmp_path):
    assert release_notes.load_release_notes(tmp_path, "none", "1.0.0") is None


def test_working_tree_read_and_its_failures(tmp_path):
    (tmp_path / "CHANGES.md").write_text(CHANGES, encoding="utf-8")
    notes = release_notes.load_release_notes(tmp_path, "changelog:CHANGES.md", "2.0.0")
    assert notes.source == str(tmp_path / "CHANGES.md")
    with pytest.raises(ReleaseNotesError, match="cannot read release notes source"):
        release_notes.load_release_notes(tmp_path, "changelog:ABSENT.md", "2.0.0")
    (tmp_path / "BINARY.md").write_bytes(b"\xff\xfe\x00")
    with pytest.raises(ReleaseNotesError, match="cannot read release notes source"):
        release_notes.load_release_notes(tmp_path, "changelog:BINARY.md", "2.0.0")


@pytest.mark.parametrize("declaration", [
    "changelog:", "changelog", "other:CHANGES.md", "", "rubbish",
])
def test_a_malformed_declaration_is_refused_not_guessed(tmp_path, declaration):
    with pytest.raises(ReleaseNotesError, match="is malformed"):
        release_notes.load_release_notes(tmp_path, declaration, "1.0.0")


@pytest.mark.parametrize("relative", ["/etc/passwd", "../CHANGES.md", "docs/..", "."])
def test_a_source_path_must_stay_inside_the_project(tmp_path, relative):
    with pytest.raises(ReleaseNotesError, match="must be a project-relative path"):
        release_notes.load_release_notes(tmp_path, f"changelog:{relative}", "1.0.0")


def test_notes_are_read_from_the_named_commit_not_the_working_tree(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t.invalid")
    _git(tmp_path, "config", "user.name", "t")
    project = tmp_path / "demo"
    project.mkdir()
    (project / "CHANGES.md").write_text(CHANGES, encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "tagged candidate")
    commit = _git(tmp_path, "rev-parse", "HEAD")
    (project / "CHANGES.md").write_text("# rewritten after the tag\n", encoding="utf-8")

    notes = release_notes.load_release_notes(
        project, "changelog:CHANGES.md", "2.0.0", commit=commit,
    )
    assert "Repaired the ordering." in notes.text
    assert notes.source == f"CHANGES.md at {commit[:12]}"
    with pytest.raises(ReleaseNotesError, match="cannot read release notes source"):
        release_notes.load_release_notes(
            project, "changelog:ABSENT.md", "2.0.0", commit=commit,
        )
    with pytest.raises(ReleaseNotesError, match="cannot read release notes source"):
        release_notes.load_release_notes(
            project, "changelog:CHANGES.md", "2.0.0", commit="0" * 40,
        )
    (project / "BINARY.md").write_bytes(b"\xff\xfe\x00")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "binary")
    with pytest.raises(ReleaseNotesError, match="is not UTF-8"):
        release_notes.load_release_notes(
            project, "changelog:BINARY.md", "2.0.0", commit=_git(tmp_path, "rev-parse", "HEAD"),
        )


def test_a_publisher_without_a_declared_source_is_refused_with_the_remedy(monkeypatch):
    monkeypatch.delenv("CMRU_RELEASE_NOTES_SOURCE")
    with pytest.raises(ReleaseNotesError, match="`changelog:<project-relative path>`"):
        release_notes.declared_source()
    monkeypatch.setenv("CMRU_RELEASE_NOTES_SOURCE", "none")
    assert release_notes.declared_source() == "none"


# --- ONE body builder ---------------------------------------------------------


def _notes() -> ReleaseNotes:
    return release_notes.extract_release_section(CHANGES, "2.0.0", source="CHANGES.md")


def test_the_body_is_the_generated_block_then_the_exact_curated_section():
    body = release.build_release_body(
        "demo", "2.0.0", artifact_name="demo-2.0.0.whl", sha256="ab" * 32, release_notes=_notes(),
    )
    generated = (
        "demo 2.0.0\n\n**Artifact:** `demo-2.0.0.whl`\n"
        f"**SHA256:** `{'ab' * 32}`\n\n"
        "Verify: `sha256sum -c demo-2.0.0.whl.sha256`\n\n"
        "Resolve latest programmatically by scanning `demo-v*` releases "
        "(highest semver); see cmru docs/SPEC.md S5."
    )
    assert body == generated + "\n\n" + _notes().text
    assert body.index("**Artifact:**") < body.index("## [2.0.0]")
    assert "first release" not in body


def test_operator_notes_only_replace_the_opening_line_and_cannot_drop_the_section():
    body = release.build_release_body(
        "demo", "2.0.0", artifact_name="a.whl", sha256="0" * 64,
        notes="Hand-written intro", release_notes=_notes(),
    )
    assert body.startswith("Hand-written intro\n\n**Artifact:**")
    assert body.endswith(_notes().text)


def test_without_curated_notes_the_body_is_the_generated_body_alone():
    body = release.build_release_body(
        "demo", "2.0.0", artifact_name="a.whl", sha256="0" * 64,
    )
    assert body.startswith("demo 2.0.0\n\n**Artifact:** `a.whl`")
    assert body.endswith("see cmru docs/SPEC.md S5.") and "## [" not in body


def test_the_variants_body_ends_with_the_same_curated_section():
    records = [{"asset": "demo-v2.0.0-a.tar.xz", "sha256": "1" * 64}]
    body = release.build_variants_release_body(
        "demo", "2.0.0", records=records, release_notes=_notes(),
    )
    assert "**Variants** (1)" in body and "- `demo-v2.0.0-a.tar.xz` — `" + "1" * 64 + "`" in body
    assert body.endswith(_notes().text)
    assert release.build_variants_release_body("demo", "2.0.0", records=records).endswith(
        "see cmru docs/SPEC.md S5.",
    )


class _RecordingGitHub:
    def __init__(self) -> None:
        self.bodies: list[tuple[str, str]] = []

    def asset_download_url(self, tag, name):
        return f"https://example.test/{tag}/{name}"

    def publish(self, tag, title, notes, assets, **_kwargs):
        self.bodies.append((tag, notes))
        return {"id": len(self.bodies)}


def test_both_publishers_send_the_body_the_shared_builder_made(tmp_path):
    artifact = tmp_path / "demo-2.0.0.whl"
    artifact.write_bytes(b"wheel bytes")
    gh = _RecordingGitHub()
    release.publish_versioned(
        gh, prefix="demo", version="2.0.0", asset_path=artifact, release_notes=_notes(),
    )
    digest = release.sha256_file(artifact)
    expected = release.build_release_body(
        "demo", "2.0.0", artifact_name=artifact.name, sha256=digest, release_notes=_notes(),
    )
    assert gh.bodies[0] == ("demo-v2.0.0", expected)

    variant = tmp_path / "a.tar.xz"
    variant.write_bytes(b"tarball")
    release_gh = _RecordingGitHub()
    release.publish_versioned_variants(
        release_gh, prefix="demo", version="2.0.0",
        variants=[release.VariantArtifact("a", variant)], asset_suffix=".tar.xz",
        release_notes=_notes(),
    )
    assert release_gh.bodies[0][1].endswith(_notes().text)


# --- the publish handlers -----------------------------------------------------


def _wheel(project: Path, version: str = "2.0.0") -> None:
    dist = project / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dist / f"demo-{version}-py3-none-any.whl", "w") as archive:
        archive.writestr(
            f"demo-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.1\nName: demo\nVersion: {version}\n\n",
        )


@pytest.fixture
def publisher(monkeypatch, tmp_path):
    gh = _RecordingGitHub()
    monkeypatch.setattr(handlers, "GitHubReleases", lambda *a, **k: gh)
    for key, value in (("GITHUB_PUSH_PAT", "t"), ("GITHUB_USERNAME", "o"), ("GITHUB_REPO", "r")):
        monkeypatch.setenv(key, value)
    project = tmp_path / "demo"
    project.mkdir()
    _wheel(project)
    (project / "CHANGES.md").write_text(CHANGES, encoding="utf-8")
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t.invalid")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "add", "demo/CHANGES.md")
    _git(tmp_path, "commit", "-q", "-m", "release candidate")
    _git(tmp_path, "tag", "demo-v2.0.0")
    return project, gh


def _wheel_publish(project: Path) -> int:
    return handlers.main(["wheel-publish", "--prefix", "demo", "--cwd", str(project)])


def test_wheel_publish_appends_the_exact_section_below_the_generated_block(publisher, monkeypatch):
    project, gh = publisher
    monkeypatch.setenv("CMRU_RELEASE_NOTES_SOURCE", "changelog:CHANGES.md")
    assert _wheel_publish(project) == 0
    tag, body = gh.bodies[0]
    assert tag == "demo-v2.0.0"
    assert body.index("**Artifact:**") < body.index("## [2.0.0] - 2026-10-13")
    assert body.endswith('key = "value"\n```')


def test_a_missing_or_wrong_version_section_refuses_before_anything_is_published(
    publisher, monkeypatch, capsys,
):
    project, gh = publisher
    (project / "CHANGES.md").write_text(CHANGES.replace("## [2.0.0]", "## [2.0.1]"), encoding="utf-8")
    _git(project.parent, "add", "demo/CHANGES.md")
    _git(project.parent, "commit", "-q", "-m", "wrong notes")
    _git(project.parent, "tag", "-f", "demo-v2.0.0", "HEAD")
    monkeypatch.setenv("CMRU_RELEASE_NOTES_SOURCE", "changelog:CHANGES.md")
    assert _wheel_publish(project) == exit_codes.REFUSED
    assert gh.bodies == []
    error = capsys.readouterr().err
    assert "[ERROR] release notes:" in error and "no `## [2.0.0] - YYYY-MM-DD` section" in error


def test_an_undeclared_notes_source_refuses_instead_of_falling_back(publisher, monkeypatch, capsys):
    project, gh = publisher
    monkeypatch.delenv("CMRU_RELEASE_NOTES_SOURCE")
    assert _wheel_publish(project) == exit_codes.REFUSED
    assert gh.bodies == []
    assert "CMRU_RELEASE_NOTES_SOURCE is not set" in capsys.readouterr().err


def test_a_project_without_a_changelog_publishes_the_generated_body(publisher, monkeypatch):
    project, gh = publisher
    monkeypatch.setenv("CMRU_RELEASE_NOTES_SOURCE", "none")
    assert _wheel_publish(project) == 0
    assert gh.bodies[0][1].startswith("demo 2.0.0\n\n**Artifact:**") and "## [" not in gh.bodies[0][1]


def test_a_dev_build_publishes_no_immutable_body_so_it_needs_no_section(publisher, monkeypatch):
    project, gh = publisher
    (project / "dist" / "demo-2.0.0-py3-none-any.whl").unlink()
    _wheel(project, "2.0.1.dev3")
    monkeypatch.setenv("CMRU_RELEASE_NOTES_SOURCE", "changelog:ABSENT.md")
    assert _wheel_publish(project) == 0
    assert all("## [" not in body for _tag, body in gh.bodies)


def test_the_tagged_candidates_commit_wins_over_a_later_working_tree(publisher, monkeypatch):
    project, gh = publisher
    repo = project.parent
    (project / "CHANGES.md").write_text("# edited after tagging\n", encoding="utf-8")
    monkeypatch.setenv("CMRU_RELEASE_NOTES_SOURCE", "changelog:CHANGES.md")
    assert _wheel_publish(project) == 0
    assert "Repaired the ordering." in gh.bodies[0][1]


def test_direct_publish_refuses_when_no_matching_tag_binds_the_notes(publisher, monkeypatch, capsys):
    project, gh = publisher
    _git(project.parent, "tag", "-d", "demo-v2.0.0")
    monkeypatch.setenv("CMRU_RELEASE_NOTES_SOURCE", "changelog:CHANGES.md")
    assert _wheel_publish(project) == exit_codes.REFUSED
    assert gh.bodies == []
    assert "cannot bind release notes to demo-v2.0.0" in capsys.readouterr().err


def test_a_retained_builds_source_commit_selects_the_notes(tmp_path, monkeypatch):
    project = tmp_path / "demo"
    project.mkdir()
    (project / "CHANGES.md").write_text(CHANGES, encoding="utf-8")
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t.invalid")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "built source")
    monkeypatch.setenv("CMRU_RELEASE_NOTES_SOURCE", "changelog:CHANGES.md")
    monkeypatch.delenv("CMRU_RELEASE_COMMIT", raising=False)
    (project / "CHANGES.md").write_text("# newer working tree\n", encoding="utf-8")
    record = {"manifest": {"source_commit": _git(tmp_path, "rev-parse", "HEAD")}}
    notes = handlers._curated_release_notes(project, "demo", "2.0.0", record)
    assert "Repaired the ordering." in notes.text


def test_tarball_publish_carries_the_section_too(publisher, monkeypatch):
    project, gh = publisher
    dist = project / "dist"
    (dist / "demo-2.0.0.tar.xz").write_bytes(b"tarball")
    monkeypatch.setenv("CMRU_RELEASE_NOTES_SOURCE", "changelog:CHANGES.md")
    monkeypatch.setenv("DEMO_VERSION", "2.0.0")
    status = handlers.main([
        "tarball-publish", "--prefix", "demo", "--cwd", str(project),
        "--glob", "demo-*.tar.xz", "--version-env", "DEMO_VERSION",
    ])
    assert status == 0
    assert gh.bodies[0][1].endswith('key = "value"\n```')


# --- the runner declares the source; projects cannot -----------------------------


def test_publisher_steps_receive_the_protected_declaration(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        cli, "execute_step",
        lambda step, root, logs, **kwargs: seen.update(kwargs["protected_env"]),
    )
    monkeypatch.setattr(cli, "_create_bound_cmru_launcher", lambda directory: directory / "cmru")
    project = cli.ProjectConfig(
        "demo", {}, {"push": []}, changelog="docs/NEWS.md", runtime_kind="none",
        build_metadata={}, runner_steps={"push": object()},
    )
    monkeypatch.setattr(cli, "_project_working_directory", lambda _project: ".")
    cli.run_project_step(project, "push", tmp_path, tmp_path / "logs")
    assert seen["CMRU_RELEASE_NOTES_SOURCE"] == "changelog:docs/NEWS.md"

    cli.run_project_step(
        dataclasses.replace(project, changelog=None), "push", tmp_path, tmp_path / "logs",
    )
    assert seen["CMRU_RELEASE_NOTES_SOURCE"] == "none"


# --- the real prepare-phase preflight and the dry-run preview ----------------------


def _project() -> SimpleNamespace:
    return SimpleNamespace(
        name="demo", cwd="demo", paths=["demo"], prefix="demo-v", git_tag=True,
        changelog="CHANGES.md", commit_generated=(), steps={},
        version=SimpleNamespace(strategy="scm", bump="conventional"),
    )


def _release_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t.invalid")
    _git(tmp_path, "config", "user.name", "t")
    for path, text, subject in (
        ("demo/CHANGES.md", "# Changelog\n\n<!-- cmru: release history -->\n", "chore: init"),
        ("demo/core.py", "A = 1\n", "feat(demo): first"),
    ):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        _git(tmp_path, "add", path)
        _git(tmp_path, "commit", "-q", "-m", subject)
    _git(tmp_path, "tag", "-a", "demo-v1.0.0", "-m", "demo-v1.0.0")
    (tmp_path / "demo" / "core.py").write_text("A = 2\n", encoding="utf-8")
    _git(tmp_path, "add", "demo/core.py")
    _git(tmp_path, "commit", "-q", "-m", "fix(demo): repair")
    return tmp_path


@pytest.mark.real_release_notes_preflight
def test_pending_release_notes_read_the_generated_section_for_the_pending_version(tmp_path):
    repo = _release_repo(tmp_path)
    project = _project()
    assert changelog.generate_release_changelog(repo, project) is True
    notes = changelog.pending_release_notes(repo, project)
    assert notes.version == "1.0.1"
    assert "### Fixed" in notes.text and "fix(demo): repair" in notes.text
    assert notes.source == str(repo / "demo" / "CHANGES.md")

    (repo / "demo" / "CHANGES.md").unlink()
    with pytest.raises(ReleaseNotesError, match="cannot read release notes source"):
        changelog.pending_release_notes(repo, project)


@pytest.mark.real_release_notes_preflight
def test_a_no_tag_project_has_no_release_body_to_preview(tmp_path):
    repo = _release_repo(tmp_path)
    project = _project()
    project.git_tag = False
    with pytest.raises(UsageRefusal, match="no GitHub Release body"):
        changelog.pending_release_notes(repo, project)


@pytest.mark.real_release_notes_preflight
def test_prepare_refuses_before_any_gate_when_the_pending_section_is_unusable(
    tmp_path, monkeypatch, capsys,
):
    repo = _release_repo(tmp_path)
    project = _project()
    generate = changelog.generate_release_changelog
    monkeypatch.setattr(changelog, "generate_release_changelog", lambda *a, **k: False)
    monkeypatch.setattr(cli, "_commit_prepared_generated", lambda *a: False)
    with pytest.raises(RefusedBeforeChange, match=r"demo: release notes: .*no `## \[1\.0\.1\]"):
        cli._prepare_release_projects(repo, {"demo": project}, ["demo"])

    assert generate(repo, project) is True
    cli._prepare_release_projects(repo, {"demo": project}, ["demo"])
    assert "the release body will end with the [1.0.1] section of CHANGES.md" in capsys.readouterr().out

    project.git_tag = False  # no GitHub Release body to build: no preflight
    project.changelog = "CHANGES.md"
    monkeypatch.setattr(cli, "_pending_release_notes_or_refuse", lambda *a, **k: 1 / 0)
    cli._prepare_release_projects(repo, {"demo": project}, ["demo"])


@pytest.mark.real_release_notes_preflight
def test_the_dry_run_preview_prints_the_publishers_body_and_leaves_the_tree_unchanged(
    tmp_path, capsys, monkeypatch,
):
    repo = _release_repo(tmp_path)
    project = _project()
    changes = repo / "demo" / "CHANGES.md"
    before = changes.read_bytes()
    calls = []
    real = release.build_release_body

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(release, "build_release_body", spy)
    cli._preview_release_bodies(repo, {"demo": project}, ["demo"])
    assert changes.read_bytes() == before  # preview generated and restored

    output = capsys.readouterr().out
    shown = output.split("----- begin release body -----\n", 1)[1].split(
        "\n----- end release body -----", 1,
    )[0]
    # The very builder the publisher uses produced it...
    assert calls and calls[0][0] == ("demo", "1.0.1")
    notes = calls[0][1]["release_notes"]
    assert "[DRY RUN] demo: GitHub Release body for demo-v1.0.1" in output
    # ...so with the build's two facts substituted it is byte-identical to the published body.
    published = real(
        "demo", "1.0.1", artifact_name="demo-1.0.1-py3-none-any.whl", sha256="ab" * 32,
        release_notes=notes,
    )
    assert shown.replace(
        release_notes.ARTIFACT_PLACEHOLDER, "demo-1.0.1-py3-none-any.whl",
    ).replace(release_notes.SHA256_PLACEHOLDER, "ab" * 32) == published
    assert published.endswith(notes.text) and "fix(demo): repair" in published


@pytest.mark.real_release_notes_preflight
def test_the_dry_run_preview_removes_a_changelog_it_created_and_skips_untagged_projects(
    tmp_path, capsys,
):
    repo = _release_repo(tmp_path)
    changes = repo / "demo" / "CHANGES.md"
    _git(repo, "rm", "-q", "-f", "demo/CHANGES.md")
    _git(repo, "commit", "-q", "-m", "drop the changelog")
    assert not changes.exists()
    skipped = _project()
    skipped.name = "other"
    skipped.git_tag = False
    bare = _project()
    bare.name = "bare"
    bare.changelog = None
    cli._preview_release_bodies(
        repo, {"demo": _project(), "other": skipped, "bare": bare}, ["demo", "other", "bare"],
    )
    assert not changes.exists()  # created for the preview, then removed again
    assert capsys.readouterr().out.count("begin release body") == 1


@pytest.mark.real_release_notes_preflight
def test_the_dry_run_preview_refuses_and_restores_when_the_section_cannot_be_built(
    tmp_path, monkeypatch,
):
    repo = _release_repo(tmp_path)
    changes = repo / "demo" / "CHANGES.md"
    before = changes.read_bytes()
    monkeypatch.setattr(changelog, "generate_release_changelog", lambda *a, **k: False)
    with pytest.raises(RefusedBeforeChange, match="release notes"):
        cli._preview_release_bodies(repo, {"demo": _project()}, ["demo"])
    assert changes.read_bytes() == before
