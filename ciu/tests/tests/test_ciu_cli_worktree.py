"""CLI dispatch tests for the S16 worktree lifecycle."""
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
    def test_forwards_all_options(self, monkeypatch, capsys):
        seen = {}

        def fake_add(
            repo_root,
            name,
            *,
            base,
            profile,
            worktree_dir,
            shared_infra,
            shared_infra_services,
            shared_infra_ref_projects,
        ):
            seen.update(
                repo_root=repo_root,
                name=name,
                base=base,
                profile=profile,
                worktree_dir=worktree_dir,
                shared_infra=shared_infra,
                shared_infra_services=shared_infra_services,
                shared_infra_ref_projects=shared_infra_ref_projects,
            )
            return Path("/tmp/repo/.worktrees/mypkg")

        monkeypatch.setattr(wt_mod, "add", fake_add)
        code = cli._worktree(
            [
                "add",
                "mypkg",
                "--base",
                "develop",
                "--profile",
                "core,db",
                "--worktree-dir",
                ".wt",
            ]
        )
        assert code == 0
        assert seen == {
            "repo_root": seen["repo_root"],
            "name": "mypkg",
            "base": "develop",
            "profile": "core,db",
            "worktree_dir": ".wt",
            "shared_infra": None,
            "shared_infra_services": None,
            "shared_infra_ref_projects": None,
        }
        assert "worktree ready:" in capsys.readouterr().out

    def test_worktree_error_maps_to_exit_2(self, monkeypatch, capsys):
        def fake_add(*_a, **_kw):
            raise wt_mod.WorktreeError("[S16] boom")

        monkeypatch.setattr(wt_mod, "add", fake_add)
        assert cli._worktree(["add", "mypkg"]) == 2
        assert "[S16] boom" in capsys.readouterr().err


class TestWorktreeAddSharedInfraDispatch:
    def test_forwards_shared_infra_flags(self, monkeypatch, capsys):
        seen = {}

        def fake_add(
            repo_root,
            name,
            *,
            base,
            profile,
            worktree_dir,
            shared_infra,
            shared_infra_services,
            shared_infra_ref_projects,
        ):
            seen.update(
                shared_infra=shared_infra,
                shared_infra_services=shared_infra_services,
                shared_infra_ref_projects=shared_infra_ref_projects,
            )
            return Path("/tmp/repo/.worktrees/mypkg")

        monkeypatch.setattr(wt_mod, "add", fake_add)
        code = cli._worktree(
            [
                "add",
                "mypkg",
                "--profile",
                "core,db",
                "--shared-infra",
                "primary",
                "--shared-infra-services",
                "api,worker",
                "--shared-infra-ref-projects",
                "idp-dev-idp,vault-dev-vault",
            ]
        )
        assert code == 0
        assert seen == {
            "shared_infra": "primary",
            "shared_infra_services": "api,worker",
            "shared_infra_ref_projects": "idp-dev-idp,vault-dev-vault",
        }
        assert "worktree ready:" in capsys.readouterr().out

    def test_shared_infra_flags_default_to_none(self, monkeypatch):
        seen = {}

        def fake_add(
            repo_root,
            name,
            *,
            base,
            profile,
            worktree_dir,
            shared_infra,
            shared_infra_services,
            shared_infra_ref_projects,
        ):
            seen.update(
                shared_infra=shared_infra,
                shared_infra_services=shared_infra_services,
                shared_infra_ref_projects=shared_infra_ref_projects,
            )
            return Path("/tmp/repo/.worktrees/mypkg")

        monkeypatch.setattr(wt_mod, "add", fake_add)
        cli._worktree(["add", "mypkg"])
        assert seen == {
            "shared_infra": None,
            "shared_infra_services": None,
            "shared_infra_ref_projects": None,
        }

    def test_partial_shared_infra_error_surfaces_as_exit_2(
        self, monkeypatch, capsys
    ):
        def fake_add(*_a, **_kw):
            raise wt_mod.WorktreeError("[S16.1] partial group")

        monkeypatch.setattr(wt_mod, "add", fake_add)
        assert cli._worktree(["add", "mypkg", "--shared-infra", "primary"]) == 2
        assert "partial group" in capsys.readouterr().err


class TestWorktreeRmDispatch:
    def test_forwards_yes_and_force(self, monkeypatch, capsys):
        seen = {}

        def fake_remove(repo_root, name, *, yes, force):
            seen.update(repo_root=repo_root, name=name, yes=yes, force=force)
            return Path("/tmp/repo/.worktrees/mypkg")

        monkeypatch.setattr(wt_mod, "remove", fake_remove)
        assert cli._worktree(["rm", "mypkg", "-y", "--force"]) == 0
        assert seen["name"] == "mypkg"
        assert seen["yes"] is True and seen["force"] is True
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
            raise wt_mod.WorktreeError("[S16] clean failed")

        monkeypatch.setattr(wt_mod, "remove", fake_remove)
        assert cli._worktree(["rm", "mypkg"]) == 2
        assert "clean failed" in capsys.readouterr().err


class TestWorktreeListDispatch:
    def test_lists_primary_and_others(self, monkeypatch, capsys, tmp_path):
        primary = tmp_path / "repo"
        primary.mkdir()
        (primary / ".git").mkdir()
        linked = primary / ".worktrees" / "pkg"
        linked.mkdir(parents=True)
        (linked / ".git").write_text("gitdir: ../..\n", encoding="utf-8")

        monkeypatch.setattr(
            wt_mod,
            "list_worktrees",
            lambda repo_root: [
                wt_mod.WorktreeInfo(primary, "main", "abc12345"),
                wt_mod.WorktreeInfo(linked, "pkg", "def45678"),
            ],
        )
        assert cli._worktree(["list"]) == 0
        out = capsys.readouterr().out
        assert "(primary)" in out
        assert "pkg" in out
