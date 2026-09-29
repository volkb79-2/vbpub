"""Behavior checks added after reviewing the remaining mutation survivors."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cmru import cli, standards, transaction


def test_short_time_prefix_remains_opt_in_in_the_registered_cli():
    args = cli._build_cli().parser.parse_args(["status"])

    assert args.log_prefix_time_short is False


def test_standards_dry_run_diff_keeps_old_and_new_revision_lines_separate(
    tmp_path, capsys,
):
    config = tmp_path / "cmru.toml"
    original = "[project]\ntemplate_revision = 0\n"
    config.write_text(original, encoding="utf-8")

    assert standards._update_project_revision(config, dry_run=True)

    preview = capsys.readouterr().out
    assert (
        f"-template_revision = 0\n+template_revision = "
        f"{standards.PROJECT_TEMPLATE_REVISION}\n"
    ) in preview
    assert config.read_text(encoding="utf-8") == original


def test_abandon_reports_a_malformed_nonempty_duplicate_scope(
    monkeypatch, tmp_path, capsys,
):
    branch = "cmru-release-20260927_120000-alpha-ab12cd"
    workspace = SimpleNamespace(
        branch=branch,
        path=tmp_path / ".worktrees" / branch,
        is_prunable=False,
        context=SimpleNamespace(base_commit="a" * 40),
    )
    monkeypatch.setattr(cli, "_current_git_root", lambda: tmp_path)
    monkeypatch.setattr(
        transaction, "list_cmru_workspaces", lambda _root: [workspace],
    )
    monkeypatch.setattr(transaction, "read_release_scope", lambda *_: ["alpha", "alpha"])
    monkeypatch.setattr(transaction, "read_release_results", lambda *_: {})
    monkeypatch.setattr(transaction, "read_release_progress", lambda *_: "a" * 40)
    monkeypatch.setattr(transaction, "backup_was_pushed", lambda *_: False)
    monkeypatch.setattr(transaction, "backup_was_removed", lambda *_: False)
    monkeypatch.setattr(
        cli.subprocess, "run",
        lambda *_args, **_kwargs: pytest.fail("malformed scope reached remote inspection"),
    )

    result = cli._abandon(
        SimpleNamespace(branch=None, dry_run=True, yes=False),
        SimpleNamespace(confirm=lambda _prompt: pytest.fail("dry-run prompted")),
    )

    assert result == 2
    assert "reason: release scope metadata is missing, malformed, or ambiguous" in capsys.readouterr().out
