"""Additional observable CLI and release-contract boundaries."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import changelog, cli, release, tool_deps, transaction


def _install_backfill_facts(monkeypatch, repo_root: Path) -> None:
    monkeypatch.setattr(changelog, "_git", lambda _root, *args: (
        "a" * 40 if args[0] == "rev-parse" else "2026-09-27"
    ))
    monkeypatch.setattr(changelog, "_previous_project_tag", lambda *_args: None)
    monkeypatch.setattr(
        changelog, "_subject_groups", lambda *_args, **_kwargs: {"Fixed": ["fix: item"]},
    )
    monkeypatch.setattr(changelog, "_generated_exclusions", lambda *_args: [])


def test_backfill_dry_run_preserves_no_final_newline_in_existing_history(
    monkeypatch, tmp_path, capsys,
):
    project_root = tmp_path / "demo"
    project_root.mkdir()
    path = project_root / "CHANGES.md"
    original = (
        "# Changelog\n\n<!-- cmru: release history -->\n\n"
        "## [1.2.2] - 2026-01-01\n- old entry"
    )
    path.write_text(original, encoding="utf-8")
    project = SimpleNamespace(
        name="demo", cwd="demo", paths=["demo"], prefix="demo-v", changelog="CHANGES.md",
    )
    _install_backfill_facts(monkeypatch, tmp_path)

    assert changelog.backfill_release_changelog(
        tmp_path, project, "demo-v1.2.3", dry_run=True,
    )

    preview = capsys.readouterr().out
    assert "\n+## [1.2.3] - 2026-09-27\n" in preview
    assert "\n - old entry" in preview
    assert "\n ## [1.2.2] - 2026-01-01\n" in preview
    assert " --> +## [1.2.3]" not in preview
    assert path.read_text(encoding="utf-8") == original


def test_backfill_creates_missing_nested_changelog_directories(monkeypatch, tmp_path):
    (tmp_path / "demo").mkdir()
    project = SimpleNamespace(
        name="demo", cwd="demo", paths=["demo"], prefix="demo-v",
        changelog="docs/release/CHANGES.md",
    )
    _install_backfill_facts(monkeypatch, tmp_path)

    assert changelog.backfill_release_changelog(tmp_path, project, "demo-v1.2.3")

    path = tmp_path / "demo" / "docs" / "release" / "CHANGES.md"
    assert path.is_file()
    assert "backfilled-after-release tag=demo-v1.2.3" in path.read_text(encoding="utf-8")


def test_bound_cmru_launcher_surfaces_nonzero_identity_probe(monkeypatch, tmp_path):
    failing_python = tmp_path / "python"
    failing_python.write_text("#!/bin/sh\nexit 17\n", encoding="utf-8")
    failing_python.chmod(0o700)
    monkeypatch.setattr(cli.sys, "executable", str(failing_python))
    launcher_dir = tmp_path / "bound"
    launcher_dir.mkdir()

    with pytest.raises(RuntimeError, match=r"identity verification.*exit=17"):
        cli._create_bound_cmru_launcher(launcher_dir)


def test_tag_lookup_reports_http_400_as_a_fetch_failure(monkeypatch, capsys):
    client = release.GitHubReleases(owner="owner", repo="repo", token=None)
    monkeypatch.setattr(client, "_request", lambda *_args, **_kwargs: (400, "bad request"))

    with pytest.raises(SystemExit) as error:
        client.get_tag_commit("demo-v1.2.3")

    assert error.value.code == 1
    diagnostic = capsys.readouterr().err
    assert "[ERROR] fetch Git tag demo-v1.2.3" in diagnostic
    assert "[ERROR] HTTP 400: bad request" in diagnostic


def test_tool_deps_dry_run_requires_refresh_and_renders_usage(capsys):
    result = tool_deps.tool_deps_main(["--dry-run"])

    assert result == 2
    captured = capsys.readouterr()
    diagnostic = captured.out + captured.err
    assert "--dry-run requires --refresh" in diagnostic
    assert "usage:" in diagnostic.lower()


def test_publication_rejects_non_string_manifest_source_commit(tmp_path):
    output_id = "20260927T120000Z_" + "a" * 40
    root = tmp_path / output_id
    (root / "dist").mkdir(parents=True)
    (root / "dist" / "alpha.whl").write_bytes(b"wheel")
    manifest = {
        "schema_version": 1,
        "kind": "cmru-local-build",
        "publication": "eligible",
        "project": "alpha",
        "build_id": output_id,
        "source_commit": None,
        "source_commit_date": "2026-09-27T12:00:00+00:00",
        "source_tree_changes": [],
        "logs": [],
        "artifacts": [{"directory": "dist", "files": []}],
    }
    (root / "build.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(RuntimeError, match="build manifest source commit does not match"):
        transaction.validate_build_output_tree(root, "alpha", output_id)


def test_abandon_captures_remote_command_output_as_text(monkeypatch, tmp_path):
    branch = "cmru-release-20260927_120000-alpha-ab12cd"
    ref = f"refs/heads/{branch}"
    calls = []

    def run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        if argv[:3] == ["git", "ls-remote", "--heads"] and len(calls) == 1:
            return subprocess.CompletedProcess(argv, 0, "a" * 40 + "\t" + ref + "\n", "")
        if argv[:4] == ["git", "push", "origin", "--delete"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:3] == ["git", "ls-remote", "--heads"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(argv)

    monkeypatch.setattr(transaction.subprocess, "run", run)
    monkeypatch.setattr(transaction, "backup_was_pushed", lambda *_args: True)
    monkeypatch.setattr(transaction, "backup_was_removed", lambda *_args: False)
    monkeypatch.setattr(transaction, "mark_backup_removed", lambda *_args: None)
    monkeypatch.setattr(transaction, "remove_workspace", lambda *_args: None)
    monkeypatch.setattr(transaction, "forget_release_scope", lambda *_args: None)

    transaction.abandon_workspace(tmp_path, SimpleNamespace(branch=branch))

    assert len(calls) == 3
    for _argv, kwargs in calls:
        assert set(kwargs) == {"cwd", "capture_output", "text", "check", "env"}
        assert kwargs["cwd"] == tmp_path
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["check"] is False
        assert isinstance(kwargs["env"], dict)
        assert "GITHUB_PUSH_PAT" not in kwargs["env"]
        assert "GITHUB_TOKEN" not in kwargs["env"]
