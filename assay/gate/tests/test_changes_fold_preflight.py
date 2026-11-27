from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT_PATH = "gate/tests/test_changes_fold_preflight.py"
DOCS_TEST_PATH = "tests/core/test_docs_examples_and_vocabulary.py"
CURRENT_ONLY_NOTE = "run-local assay-injected `PYTHONPATH`"
FIRST_VERSION = "999.0.0"


def test_changes_fold_preflight_is_registered_in_tooling_tests():
    config = tomllib.loads((PROJECT_ROOT / "run-gate.toml").read_text(encoding="utf-8"))
    argv = config["lanes"]["tooling-tests"]["argv"]

    assert PREFLIGHT_PATH in "\n".join(argv)


@pytest.fixture
def current_sections_docs_check():
    """A fixture for the old docs-check shape that loses entries as history ages."""

    def check(changes: str) -> None:
        headings = list(re.finditer(
            r"^## \[([^\]]+)\](?: - \d{4}-\d{2}-\d{2})?$",
            changes,
            re.MULTILINE,
        ))
        sections = [
            (
                heading.group(1),
                changes[
                    heading.end():
                    headings[index + 1].start()
                    if index + 1 < len(headings)
                    else len(changes)
                ],
            )
            for index, heading in enumerate(headings)
        ]
        unreleased = next(
            (body for name, body in sections if name == "Unreleased"), ""
        )
        latest = next(
            (body for name, body in sections if name != "Unreleased"), ""
        )
        assert CURRENT_ONLY_NOTE in unreleased + latest, (
            f"{CURRENT_ONLY_NOTE!r} is not present in [Unreleased] or the newest release"
        )

    return check


def _cmru_release_functions():
    cmru_source = PROJECT_ROOT.parent / "cmru" / "src"
    sys.path.insert(0, str(cmru_source))
    try:
        from cmru.changelog import generate_release_changelog, pending_release_notes
        from cmru.cli import _fold_pending_release_notes
    except ImportError as exc:
        pytest.fail(
            "CMRU source cannot be imported in this test interpreter without "
            f"installing packages: {exc}"
        )
    return generate_release_changelog, pending_release_notes, _fold_pending_release_notes


def _copy_docs_test_tree(repo_root: Path, destination: Path) -> Path:
    destination.mkdir()
    copied_assay = destination / "assay"
    shutil.copytree(
        repo_root / "assay",
        copied_assay,
        ignore=shutil.ignore_patterns(
            ".git",
            ".assay",
            ".pytest_cache",
            "__pycache__",
            "*.pyc",
            ".venv",
            "artifacts",
            "build",
            "dist",
            "evidence",
            "htmlcov",
            "logs",
            "vm-lab",
            "*.egg-info",
        ),
    )
    shutil.copytree(repo_root / "cmru" / "src", destination / "cmru" / "src")
    hostobs_changes = destination / "hostobs" / "CHANGES.md"
    hostobs_changes.parent.mkdir(parents=True)
    shutil.copy2(repo_root / "hostobs" / "CHANGES.md", hostobs_changes)
    return copied_assay


def _run_git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        },
    )
    assert result.returncode == 0, (
        f"git {args!r} failed ({result.returncode}):\n"
        f"{result.stdout}{result.stderr}"
    )
    return result.stdout.strip()


def _initialize_fixture_git(repo_root: Path, assay_root: Path, seed_name: str) -> None:
    _run_git(repo_root, "init", "--quiet")
    _run_git(repo_root, "config", "user.name", "Assay changelog preflight")
    _run_git(repo_root, "config", "user.email", "assay-preflight@example.invalid")
    _run_git(repo_root, "config", "commit.gpgsign", "false")
    seed = assay_root / seed_name
    seed.write_text("source history for the synthetic release\n", encoding="utf-8")
    _run_git(repo_root, "add", f"assay/{seed_name}")
    _run_git(repo_root, "commit", "--quiet", "-m", "test: seed changelog preflight")


def _run_docs_tests(copied_assay: Path, state: str) -> str:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            DOCS_TEST_PATH,
        ],
        cwd=copied_assay,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    output = f"{result.stdout}{result.stderr}"
    assert result.returncode == 0, (
        f"docs tests failed against the {state} CMRU-folded changelog; "
        f"inner pytest output follows:\n{output}"
    )
    return output


def _release_project():
    return SimpleNamespace(
        name="assay",
        cwd="assay",
        changelog="CHANGES.md",
        prefix="assay-v",
        git_tag=True,
        paths=["assay"],
        version=SimpleNamespace(strategy="scm", bump="conventional"),
    )


def _release_candidate(
    repo_root: Path,
    project,
    *,
    generate_release_changelog,
    pending_release_notes,
    fold_pending_release_notes,
    set_version: str | None = None,
) -> bool:
    generate_release_changelog(
        repo_root, project, **({"set_version": set_version} if set_version else {})
    )
    plan = {"set_version": set_version} if set_version else {}
    notes = pending_release_notes(repo_root, project, **plan)
    folded = fold_pending_release_notes(repo_root, project, notes.version)
    pending_release_notes(repo_root, project, **plan)
    return folded


def _add_unreleased_note(changes_path: Path) -> None:
    changes = changes_path.read_text(encoding="utf-8")
    heading = re.search(r"^## \[Unreleased\]\s*$", changes, re.MULTILINE)
    assert heading is not None, "CHANGES.md has no [Unreleased] section"
    insert_at = heading.end()
    changes_path.write_text(
        changes[:insert_at]
        + "\n\n### Testing\n- synthetic B596 following-release note\n"
        + changes[insert_at:],
        encoding="utf-8",
    )


def _prove_current_sections_control_fails(
    tmp_path: Path,
    current_sections_docs_check,
    *,
    generate_release_changelog,
    pending_release_notes,
    fold_pending_release_notes,
) -> None:
    repo_copy = tmp_path / "current-sections-control"
    assay_copy = repo_copy / "assay"
    assay_copy.mkdir(parents=True)
    changes_path = assay_copy / "CHANGES.md"
    changes_path.write_text(
        "# Changelog\n\n"
        "## [Unreleased]\n\n"
        f"### Testing\n- {CURRENT_ONLY_NOTE}\n\n"
        "<!-- cmru: release history -->\n",
        encoding="utf-8",
    )
    project = _release_project()
    _initialize_fixture_git(repo_copy, assay_copy, ".current-sections-seed")

    first_folded = _release_candidate(
        repo_copy,
        project,
        generate_release_changelog=generate_release_changelog,
        pending_release_notes=pending_release_notes,
        fold_pending_release_notes=fold_pending_release_notes,
        set_version=FIRST_VERSION,
    )
    assert first_folded, "the current-sections fixture must fold its initial note"
    first_changes = changes_path.read_text(encoding="utf-8")
    current_sections_docs_check(first_changes)

    _run_git(repo_copy, "add", "assay/CHANGES.md")
    _run_git(repo_copy, "commit", "--quiet", "-m", "chore: prepare fixture release")
    _run_git(repo_copy, "tag", f"assay-v{FIRST_VERSION}")
    _add_unreleased_note(changes_path)
    next_source = assay_copy / ".current-sections-following-source"
    next_source.write_text("following fixture source\n", encoding="utf-8")
    _run_git(
        repo_copy,
        "add",
        "assay/CHANGES.md",
        "assay/.current-sections-following-source",
    )
    _run_git(
        repo_copy,
        "commit",
        "--quiet",
        "-m",
        "fix: add synthetic following-release source",
    )
    second_folded = _release_candidate(
        repo_copy,
        project,
        generate_release_changelog=generate_release_changelog,
        pending_release_notes=pending_release_notes,
        fold_pending_release_notes=fold_pending_release_notes,
    )
    assert second_folded, "the second current-sections fixture fold must happen"
    with pytest.raises(AssertionError, match=r"not present in \[Unreleased\]"):
        current_sections_docs_check(changes_path.read_text(encoding="utf-8"))


def test_docs_tests_pass_before_and_after_two_real_cmru_folds(
    tmp_path: Path,
    current_sections_docs_check,
):
    (
        generate_release_changelog,
        pending_release_notes,
        fold_pending_release_notes,
    ) = _cmru_release_functions()
    source_changes = PROJECT_ROOT / "CHANGES.md"
    source_changes_before = source_changes.read_bytes()
    copied_assay = _copy_docs_test_tree(PROJECT_ROOT.parent, tmp_path / "repo")
    repo_copy = copied_assay.parent
    changes_path = copied_assay / "CHANGES.md"
    project = _release_project()

    _initialize_fixture_git(repo_copy, copied_assay, ".b596-preflight-seed")

    _release_candidate(
        repo_copy,
        project,
        generate_release_changelog=generate_release_changelog,
        pending_release_notes=pending_release_notes,
        fold_pending_release_notes=fold_pending_release_notes,
        set_version=FIRST_VERSION,
    )
    # A no-note first state is legal; the following release below must fold.
    _run_docs_tests(copied_assay, "first-release")
    first_changes = changes_path.read_text(encoding="utf-8")
    current_sections_docs_check(first_changes)

    _run_git(repo_copy, "add", "assay/CHANGES.md")
    _run_git(repo_copy, "commit", "--quiet", "-m", "chore: prepare first release")
    _run_git(repo_copy, "tag", f"assay-v{FIRST_VERSION}")
    _add_unreleased_note(changes_path)
    next_source = copied_assay / ".b596-following-release-source"
    next_source.write_text("source history for the following release\n", encoding="utf-8")
    _run_git(repo_copy, "add", "assay/CHANGES.md", "assay/.b596-following-release-source")
    _run_git(
        repo_copy,
        "commit",
        "--quiet",
        "-m",
        "fix: add synthetic following-release source",
    )

    second_folded = _release_candidate(
        repo_copy,
        project,
        generate_release_changelog=generate_release_changelog,
        pending_release_notes=pending_release_notes,
        fold_pending_release_notes=fold_pending_release_notes,
    )
    assert second_folded, "the synthetic following release must fold its [Unreleased] note"
    second_changes = changes_path.read_text(encoding="utf-8")
    _prove_current_sections_control_fails(
        tmp_path,
        current_sections_docs_check,
        generate_release_changelog=generate_release_changelog,
        pending_release_notes=pending_release_notes,
        fold_pending_release_notes=fold_pending_release_notes,
    )
    _run_docs_tests(copied_assay, "following-release")

    assert source_changes.read_bytes() == source_changes_before, (
        "the preflight changed the package worktree's CHANGES.md"
    )
    assert changes_path.read_text(encoding="utf-8") == second_changes
