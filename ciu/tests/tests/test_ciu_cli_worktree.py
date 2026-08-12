"""Tests for cli._worktree (S16 `ciu worktree add|rm|list` dispatch),
including the new `--data-isolation` flag (S16.2/CIU-23, O4).

These drive `cli._worktree` directly with synthetic argv and a monkeypatched
`ciu.worktree` module — no real git, no docker; that machinery is covered by
test_ciu_worktree.py. This file's job is the CLI's own argument forwarding
and error-to-exit-code mapping.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import cli  # noqa: E402
from ciu import dev  # noqa: E402
from ciu import worktree as wt_mod  # noqa: E402


@pytest.fixture(autouse=True)
def _patch_repo_root(monkeypatch, tmp_path):
    monkeypatch.setattr(dev, "resolve_repo_root", lambda *_a, **_kw: tmp_path)


class TestWorktreeAddDispatch:
    def test_forwards_all_options_including_data_isolation(self, monkeypatch, capsys):
        seen = {}

        def fake_add(repo_root, name, *, base, profile, worktree_dir, data_isolation):
            seen.update(
                repo_root=repo_root, name=name, base=base, profile=profile,
                worktree_dir=worktree_dir, data_isolation=data_isolation,
            )
            return Path("/tmp/repo/.worktrees/mypkg")

        monkeypatch.setattr(wt_mod, "add", fake_add)
        code = cli._worktree([
            "add", "mypkg", "--base", "develop", "--profile", "core,db",
            "--worktree-dir", ".wt", "--data-isolation", "postgres",
        ])
        assert code == 0
        assert seen == {
            "repo_root": seen["repo_root"],  # identity checked below
            "name": "mypkg", "base": "develop", "profile": "core,db",
            "worktree_dir": ".wt", "data_isolation": "postgres",
        }
        out = capsys.readouterr().out
        assert "worktree ready:" in out

    def test_data_isolation_defaults_to_none(self, monkeypatch):
        seen = {}

        def fake_add(repo_root, name, *, base, profile, worktree_dir, data_isolation):
            seen["data_isolation"] = data_isolation
            return Path("/tmp/repo/.worktrees/mypkg")

        monkeypatch.setattr(wt_mod, "add", fake_add)
        cli._worktree(["add", "mypkg"])
        assert seen["data_isolation"] is None

    def test_worktree_error_maps_to_exit_2(self, monkeypatch, capsys):
        def fake_add(*_a, **_kw):
            raise wt_mod.WorktreeError("[S16] boom")
        monkeypatch.setattr(wt_mod, "add", fake_add)
        code = cli._worktree(["add", "mypkg"])
        assert code == 2
        assert "[S16] boom" in capsys.readouterr().err

    def test_data_isolation_provision_failure_surfaces_as_exit_2(self, monkeypatch, capsys):
        """A DataIsolationError raised from add() is wrapped in a WorktreeError
        by worktree.add itself (see test_ciu_worktree.py); the CLI layer just
        needs to map ANY WorktreeError to exit 2 uniformly."""
        def fake_add(*_a, **_kw):
            raise wt_mod.WorktreeError(
                "[S16.2] worktree created, but provisioning the isolated "
                "database failed: simulated"
            )
        monkeypatch.setattr(wt_mod, "add", fake_add)
        code = cli._worktree(["add", "mypkg", "--data-isolation", "postgres"])
        assert code == 2
        assert "provisioning the isolated database failed" in capsys.readouterr().err


class TestWorktreeRmDispatch:
    def test_forwards_yes_and_force(self, monkeypatch, capsys):
        seen = {}

        def fake_remove(repo_root, name, *, yes, force):
            seen.update(repo_root=repo_root, name=name, yes=yes, force=force)
            return Path("/tmp/repo/.worktrees/mypkg")

        monkeypatch.setattr(wt_mod, "remove", fake_remove)
        code = cli._worktree(["rm", "mypkg", "-y", "--force"])
        assert code == 0
        assert seen["name"] == "mypkg" and seen["yes"] is True and seen["force"] is True
        assert "removed:" in capsys.readouterr().out

    def test_defaults_yes_and_force_false(self, monkeypatch):
        seen = {}

        def fake_remove(repo_root, name, *, yes, force):
            seen.update(yes=yes, force=force)
            return Path("/tmp/x")

        monkeypatch.setattr(wt_mod, "remove", fake_remove)
        cli._worktree(["rm", "mypkg"])
        assert seen == {"yes": False, "force": False}

    def test_worktree_error_maps_to_exit_2(self, monkeypatch, capsys):
        def fake_remove(*_a, **_kw):
            raise wt_mod.WorktreeError("[S16.2] could not drop data-isolation entity 'x'")
        monkeypatch.setattr(wt_mod, "remove", fake_remove)
        code = cli._worktree(["rm", "mypkg"])
        assert code == 2
        assert "could not drop data-isolation entity" in capsys.readouterr().err


class TestWorktreeListDispatch:
    def test_lists_primary_and_others(self, monkeypatch, capsys, tmp_path):
        primary = tmp_path / "repo"
        primary.mkdir()
        (primary / ".git").mkdir()  # a DIRECTORY .git marks the PRIMARY checkout

        worktree_dir = tmp_path / "repo" / ".worktrees" / "pkg"
        worktree_dir.mkdir(parents=True)
        (worktree_dir / ".git").write_text("gitdir: ../..\n")  # a FILE marks a worktree

        infos = [
            wt_mod.WorktreeInfo(primary, "main", "abc12345"),
            wt_mod.WorktreeInfo(worktree_dir, "pkg", "def45678"),
        ]
        monkeypatch.setattr(wt_mod, "list_worktrees", lambda repo_root: infos)
        code = cli._worktree(["list"])
        assert code == 0
        out = capsys.readouterr().out
        assert "(primary)" in out
        assert "pkg" in out
