"""Behaviour of ``cmru gate-snapshots`` (C-6): listing and safe, repeatable removal."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from cmru import cli, exit_codes, gate_snapshot


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def repository(tmp_path, monkeypatch):
    """A linked source worktree holding one closed (resumable) gate snapshot."""
    primary = tmp_path / "primary"
    primary.mkdir()
    _git(primary, "init", "--quiet")
    _git(primary, "config", "user.name", "CMRU Gate Test")
    _git(primary, "config", "user.email", "cmru-gate@example.invalid")
    (primary / "README.md").write_text("tracked fixture\n", encoding="utf-8")
    _git(primary, "add", "README.md")
    _git(primary, "commit", "--quiet", "-m", "fixture")
    source = tmp_path / "source"
    _git(primary, "worktree", "add", "--quiet", "-b", "gate-source", str(source), "HEAD")
    snapshot = gate_snapshot.open_gate_snapshot(source)
    gate_snapshot.close_gate_snapshot(snapshot)
    monkeypatch.chdir(source)
    return source, snapshot


def test_listing_names_each_snapshot_status_and_reason(repository, capsys):
    source, snapshot = repository
    assert cli.main(["gate-snapshots"]) == 0
    output = capsys.readouterr().out
    assert f"retained: {snapshot.branch}" in output
    assert str(snapshot.worktree_path) in output and str(source) in output
    assert "a retry can resume it" in output
    assert snapshot.worktree_path.is_dir()


def test_json_listing_is_machine_readable(repository, capsys):
    _source, snapshot = repository
    assert cli.main(["gate-snapshots", "--json", "--include-current"]) == 0
    (record,) = json.loads(capsys.readouterr().out)
    assert record["branch"] == snapshot.branch
    assert record["status"] == gate_snapshot.REMOVABLE


def test_remove_keeps_a_resumable_snapshot_unless_asked(repository, capsys):
    _source, snapshot = repository
    assert cli.main(["gate-snapshots", "--remove"]) == 0
    assert snapshot.worktree_path.is_dir()
    assert "Removed" not in capsys.readouterr().out


def test_dry_run_names_the_removal_and_changes_nothing(repository, capsys):
    _source, snapshot = repository
    assert cli.main(["gate-snapshots", "--remove", "--include-current", "--dry-run"]) == 0
    assert f"[DRY RUN] Would remove gate snapshot {snapshot.worktree_path}" in capsys.readouterr().out
    assert snapshot.worktree_path.is_dir()


def test_remove_is_repeatable(repository, capsys):
    source, snapshot = repository
    assert cli.main(["gate-snapshots", "--remove", "--include-current"]) == 0
    assert f"Removed gate snapshot {snapshot.worktree_path}" in capsys.readouterr().out
    assert not snapshot.worktree_path.exists()
    assert _git(source, "branch", "--list", snapshot.branch) == ""
    assert cli.main(["gate-snapshots", "--remove", "--include-current"]) == 0
    assert "No retained CMRU gate snapshots." in capsys.readouterr().out


def test_a_snapshot_that_cannot_be_removed_fails_the_verb_and_names_why(
    repository, monkeypatch, capsys,
):
    _source, snapshot = repository

    def refuse(*_args, **_kwargs):
        raise RuntimeError("lease went live")

    monkeypatch.setattr(gate_snapshot, "remove_gate_snapshot", refuse)
    assert cli.main(["gate-snapshots", "--remove", "--include-current"]) == exit_codes.FAILURE
    error = capsys.readouterr().err
    assert f"could not remove gate snapshot {snapshot.worktree_path}: lease went live" in error
    assert snapshot.worktree_path.is_dir()


def test_inspection_failure_is_reported_not_raised(repository, monkeypatch, capsys):
    def broken(*_args, **_kwargs):
        raise RuntimeError("records unreadable")

    monkeypatch.setattr(gate_snapshot, "inspect_gate_snapshots", broken)
    assert cli.main(["gate-snapshots"]) == exit_codes.FAILURE
    assert "cannot inspect gate snapshots: records unreadable" in capsys.readouterr().err


def test_outside_a_repository_is_a_usage_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["gate-snapshots"]) == exit_codes.CONFIG_ERROR
    assert "must run inside the repository" in capsys.readouterr().err
