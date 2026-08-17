"""Behavioral tests for the S16 Git-worktree lifecycle substrate.

These tests use real Git worktrees in a temporary repository while replacing
CIU environment generation and cleanup. They exercise checkout creation,
lookup, clean-before-remove ordering, and exact target-environment handling
without Docker, a network, or the wall clock.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import worktree  # noqa: E402


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    assert _git(["init", "-b", "main"], repo).returncode == 0
    assert _git(["config", "user.email", "t@example.com"], repo).returncode == 0
    assert _git(["config", "user.name", "Test"], repo).returncode == 0
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    (repo / ".gitignore").write_text("ciu.env\n", encoding="utf-8")
    assert _git(["add", "README.md", ".gitignore"], repo).returncode == 0
    assert _git(["commit", "-m", "init"], repo).returncode == 0
    return repo


def _instance_id_for(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:6]


@pytest.fixture
def fake_generate_env(monkeypatch):
    def fake(path: Path) -> int:
        instance_id = _instance_id_for(path)
        (path / "ciu.env").write_text(
            f'export INSTANCE_ID="{instance_id}"\n', encoding="utf-8"
        )
        return 0

    monkeypatch.setattr(worktree, "_generate_env_in", fake)
    return fake


class TestAddRemoveList:
    def test_add_creates_worktree_on_new_branch(self, tmp_repo, fake_generate_env):
        target = worktree.add(tmp_repo, "feature-x", base="main")
        assert target == tmp_repo / ".worktrees" / "feature-x"
        assert target.is_dir()
        assert (target / "ciu.env").is_file()

    def test_add_rejects_invalid_names(self, tmp_repo):
        for name in ("a/b", ".hidden", ""):
            with pytest.raises(worktree.WorktreeError):
                worktree.add(tmp_repo, name)

    def test_add_rejects_existing_target(self, tmp_repo, fake_generate_env):
        worktree.add(tmp_repo, "dup", base="main")
        with pytest.raises(worktree.WorktreeError, match="already exists"):
            worktree.add(tmp_repo, "dup", base="main")

    def test_add_profile_writes_services_profile(self, tmp_repo, fake_generate_env):
        target = worktree.add(tmp_repo, "wt", base="main", profile="core,db")
        env_text = (target / "ciu.env").read_text(encoding="utf-8")
        assert 'CIU_SERVICES_PROFILE="core,db"' in env_text

    def test_add_generate_env_failure_raises(self, tmp_repo, monkeypatch):
        monkeypatch.setattr(worktree, "_generate_env_in", lambda path: 1)
        with pytest.raises(worktree.WorktreeError, match="ciu env generate"):
            worktree.add(tmp_repo, "wt", base="main")

    def test_add_surfaces_git_worktree_creation_failure(self, tmp_repo, monkeypatch):
        monkeypatch.setattr(
            worktree,
            "_git",
            lambda *_args: subprocess.CompletedProcess(
                ["git"], 1, "", "branch exists"
            ),
        )
        with pytest.raises(worktree.WorktreeError, match="branch exists"):
            worktree.add(tmp_repo, "wt", base="main")

    def test_list_worktrees_shows_primary_and_added(self, tmp_repo, fake_generate_env):
        worktree.add(tmp_repo, "wt1", base="main")
        infos = worktree.list_worktrees(tmp_repo)
        assert any(info.is_primary for info in infos)
        assert any(not info.is_primary and info.path.name == "wt1" for info in infos)

    def test_list_worktrees_preserves_git_detached_state(self, tmp_path, monkeypatch):
        output = (
            f"worktree {tmp_path / 'primary'}\nHEAD 11111111\nbranch refs/heads/main\n\n"
            f"worktree {tmp_path / 'detached'}\nHEAD 22222222\ndetached\n\n"
        )
        monkeypatch.setattr(
            worktree,
            "_git",
            lambda *_args: subprocess.CompletedProcess(["git"], 0, output, ""),
        )
        assert [item.branch for item in worktree.list_worktrees(tmp_path)] == [
            "main",
            "(detached)",
        ]

    def test_list_worktrees_surfaces_git_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            worktree,
            "_git",
            lambda *_args: subprocess.CompletedProcess(
                ["git"], 1, "", "no repository"
            ),
        )
        with pytest.raises(worktree.WorktreeError, match="no repository"):
            worktree.list_worktrees(tmp_path)

    def test_find_worktree_by_name(self, tmp_repo, fake_generate_env):
        worktree.add(tmp_repo, "wt1", base="main")
        found = worktree.find_worktree(tmp_repo, "wt1")
        assert found is not None and found.path.name == "wt1"
        assert worktree.find_worktree(tmp_repo, "nope") is None

    def test_remove_unknown_name_raises(self, tmp_repo):
        with pytest.raises(worktree.WorktreeError, match="no worktree named"):
            worktree.remove(tmp_repo, "nope")

    def test_remove_primary_refused(self, tmp_repo):
        with pytest.raises(worktree.WorktreeError, match="PRIMARY"):
            worktree.remove(tmp_repo, tmp_repo.name)

    def test_remove_cleans_before_git_remove(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        target = worktree.add(tmp_repo, "wt1", base="main")
        calls: list[str] = []
        monkeypatch.setattr(
            worktree, "_clean_in", lambda wt, *, yes: calls.append("clean") or 0
        )
        removed = worktree.remove(tmp_repo, "wt1", force=True)
        assert removed == target
        assert calls == ["clean"]
        assert not target.exists()

    def test_remove_failed_clean_aborts_without_force(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        target = worktree.add(tmp_repo, "wt1", base="main")
        monkeypatch.setattr(worktree, "_clean_in", lambda wt, *, yes: 1)
        with pytest.raises(worktree.WorktreeError, match="ciu clean.*failed"):
            worktree.remove(tmp_repo, "wt1", force=False)
        assert target.exists()

    def test_remove_failed_clean_force_proceeds_silently(
        self, tmp_repo, fake_generate_env, monkeypatch, capsys
    ):
        worktree.add(tmp_repo, "wt1", base="main")
        monkeypatch.setattr(worktree, "_clean_in", lambda wt, *, yes: 1)
        worktree.remove(tmp_repo, "wt1", force=True)
        assert capsys.readouterr().out == ""

    def test_remove_surfaces_git_remove_failure_after_clean(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        worktree.add(tmp_repo, "wt1", base="main")
        real_git = worktree._git
        monkeypatch.setattr(worktree, "_clean_in", lambda *_args, **_kw: 0)

        def fail_remove(args, cwd):
            if args[:2] == ["worktree", "remove"]:
                return subprocess.CompletedProcess(
                    ["git"], 1, "", "checkout locked"
                )
            return real_git(args, cwd)

        monkeypatch.setattr(worktree, "_git", fail_remove)
        with pytest.raises(worktree.WorktreeError, match="checkout locked"):
            worktree.remove(tmp_repo, "wt1")


class TestWorktreeSubprocessEnvironment:
    _IDENTITY_KEYS = (
        "REPO_ROOT",
        "PHYSICAL_REPO_ROOT",
        "DOCKER_NETWORK_INTERNAL",
        "INSTANCE_ID",
        "REPO_NAME",
        "CIU_SERVICES_PROFILE",
    )

    def test_generate_env_strips_primary_instance_identity(self, tmp_path, monkeypatch):
        for key in self._IDENTITY_KEYS:
            monkeypatch.setenv(key, "primary-value")
        seen: dict[str, object] = {}

        def fake_run(argv, **kwargs):
            seen.update(argv=argv, **kwargs)
            return subprocess.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr(worktree.subprocess, "run", fake_run)
        assert worktree._generate_env_in(tmp_path) == 0
        env = seen["env"]
        assert isinstance(env, dict)
        for key in self._IDENTITY_KEYS:
            assert key not in env
        assert seen["cwd"] == str(tmp_path)
        assert seen["check"] is False

    def test_clean_uses_target_worktree_env_not_primary(self, tmp_path, monkeypatch):
        (tmp_path / "ciu.env").write_text(
            'export REPO_ROOT="/target"\nexport INSTANCE_ID="target-id"\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("REPO_ROOT", "/primary")
        seen: dict[str, object] = {}

        def fake_run(argv, **kwargs):
            seen.update(argv=argv, **kwargs)
            return subprocess.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr(worktree.subprocess, "run", fake_run)
        assert worktree._clean_in(tmp_path, yes=True) == 0
        env = seen["env"]
        assert isinstance(env, dict)
        assert env["REPO_ROOT"] == "/target"
        assert seen["argv"][-1] == "-y"

    def test_clean_refuses_to_guess_when_target_env_is_missing(self, tmp_path):
        with pytest.raises(worktree.WorktreeError, match="does not exist"):
            worktree._clean_in(tmp_path, yes=False)

    def test_clean_surfaces_unreadable_target_env(self, tmp_path, monkeypatch):
        env_file = tmp_path / "ciu.env"
        env_file.write_text('export REPO_ROOT="/target"\n', encoding="utf-8")
        from ciu import workspace_env

        monkeypatch.setattr(
            workspace_env,
            "parse_workspace_env",
            lambda _path: (_ for _ in ()).throw(PermissionError("denied")),
        )
        with pytest.raises(worktree.WorktreeError, match="could not read.*denied"):
            worktree._clean_in(tmp_path, yes=False)
