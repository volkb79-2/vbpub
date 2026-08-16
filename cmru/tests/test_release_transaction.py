"""Behavioural contract tests for isolated, source-first release transactions."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli, transaction
from cmru import tester_gate


# ---------------------------------------------------------------------------
# Real-repo harness for promotion_landed / revert_promotion / sync_local_main —
# these have real git ref/revert/fast-forward semantics not worth mocking.
# ---------------------------------------------------------------------------

def _git(*args, cwd, check=True):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {args} failed:\n{r.stderr}")
    return r.stdout.strip()


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", cwd=path)
    # Set the branch name HEAD will point to before the first commit, so the
    # test is independent of the local git's init.defaultBranch config.
    _git("symbolic-ref", "HEAD", "refs/heads/main", cwd=path)
    _git("config", "user.email", "test@cmru.test", cwd=path)
    _git("config", "user.name", "cmru test", cwd=path)


class _OriginAndClone:
    """A bare ``origin`` remote plus a ``repo_root`` clone with an initial commit
    on ``main``, pushed. :meth:`clone_workspace` mints additional clones standing
    in for release worktrees (they share the same origin, not the real worktree
    mechanism, but that is all these functions actually depend on)."""

    def __enter__(self) -> "_OriginAndClone":
        self.tmp = Path(tempfile.mkdtemp(prefix="cmru_txn_test_"))
        seed = self.tmp / "seed"
        _init_repo(seed)
        (seed / "README.md").write_text("init\n")
        _git("add", "README.md", cwd=seed)
        _git("commit", "-q", "-m", "chore: initial commit", cwd=seed)

        self.origin = self.tmp / "origin.git"
        _git("clone", "-q", "--bare", str(seed), str(self.origin), cwd=self.tmp)

        self.repo_root = self.tmp / "repo"
        _git("clone", "-q", str(self.origin), str(self.repo_root), cwd=self.tmp)
        _git("config", "user.email", "test@cmru.test", cwd=self.repo_root)
        _git("config", "user.name", "cmru test", cwd=self.repo_root)
        return self

    def clone_workspace(self, branch: str, *, path_name: str = "workspace") -> Path:
        path = self.tmp / path_name
        _git("clone", "-q", str(self.origin), str(path), cwd=self.tmp)
        _git("config", "user.email", "test@cmru.test", cwd=path)
        _git("config", "user.name", "cmru test", cwd=path)
        _git("checkout", "-q", "-b", branch, cwd=path)
        return path

    def add_worktree(self, branch: str, *, path_name: str = "workspace") -> Path:
        """A REAL ``git worktree`` of repo_root, matching what
        transaction.create_workspace() does in production — required for
        anything that calls ``git worktree ...``/``git branch ...`` against
        repo_root (list_retained_workspaces, remove_workspace). clone_workspace()
        makes an independent clone instead, which is fine for push/fetch-only
        tests but not for those."""
        path = self.tmp / path_name
        _git("worktree", "add", "-q", "-b", branch, str(path), "main", cwd=self.repo_root)
        return path

    def __exit__(self, *_exc) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)


def _project(name: str, *, paths: list[str] | None = None, steps=None):
    return SimpleNamespace(
        name=name,
        cwd=name,
        paths=paths or [name],
        steps=steps or {},
        project_root=Path(name),
        github_token="test-credential",
    )


def test_copy_secret_overlays_preserves_root_and_project_scoped_credentials(tmp_path):
    repo_root = tmp_path / "repo"
    workspace_path = tmp_path / "workspace"
    (repo_root / "alpha").mkdir(parents=True)
    workspace_path.mkdir()
    (repo_root / "cmru.secret.toml").write_text(
        '[github]\ntoken = "root-test-token"\n', encoding="utf-8",
    )
    project_config = repo_root / "alpha" / "cmru.toml"
    project_config.write_text("schema_version = 1\n", encoding="utf-8")
    (repo_root / "alpha" / "cmru.secret.toml").write_text(
        '[github]\ntoken = "project-test-token"\n', encoding="utf-8",
    )
    workspace = transaction.ReleaseWorkspace(
        repo_root, workspace_path, "cmru/release/test", "a" * 40,
    )

    transaction.copy_secret_overlays(repo_root, workspace, [project_config])

    assert (workspace_path / "cmru.secret.toml").read_text(encoding="utf-8") == (
        '[github]\ntoken = "root-test-token"\n'
    )
    assert (workspace_path / "alpha" / "cmru.secret.toml").read_text(encoding="utf-8") == (
        '[github]\ntoken = "project-test-token"\n'
    )
    assert (workspace_path / "cmru.secret.toml").stat().st_mode & 0o777 == 0o600
    assert (workspace_path / "alpha" / "cmru.secret.toml").stat().st_mode & 0o777 == 0o600


def test_copy_secret_overlays_rejects_a_non_file_secret_path(tmp_path):
    repo_root = tmp_path / "repo"
    workspace_path = tmp_path / "workspace"
    repo_root.mkdir()
    workspace_path.mkdir()
    (repo_root / "cmru.secret.toml").mkdir()
    workspace = transaction.ReleaseWorkspace(
        repo_root, workspace_path, "cmru/release/test", "a" * 40,
    )

    with pytest.raises(RuntimeError, match="not a regular file"):
        transaction.copy_secret_overlays(repo_root, workspace, [])


def test_child_args_replaces_absolute_config_with_snapshot_relative_path(tmp_path):
    config = tmp_path / "nested" / "cmru.toml"
    config.parent.mkdir()
    config.write_text("", encoding="utf-8")

    assert cli._child_release_args(
        ["--project", "alpha", "--config", str(config)], config, tmp_path,
    ) == ["--project", "alpha", "--config", "nested/cmru.toml"]


def test_child_args_removes_parent_only_resume_option(tmp_path):
    config = tmp_path / "cmru.toml"
    config.write_text("", encoding="utf-8")

    assert cli._child_release_args(
        ["--resume", "/tmp/retained", "--project", "alpha"], config, tmp_path,
    ) == ["--project", "alpha", "--config", "cmru.toml"]


def test_required_gate_rejects_project_without_run_tests(tmp_path):
    with pytest.raises(RuntimeError, match="no release gate"):
        cli._run_release_gates(tmp_path, {"alpha": _project("alpha")}, ["alpha"])


def test_build_step_config_defaults_every_project_subprocess_to_quiet():
    """The normal orchestration console is summary-only for every project step."""
    command = cli.Command(label="t", argv=["true"], cwd=".")

    assert cli._build_step_config("run-tests", [command]).quiet is True
    assert cli._build_step_config("prepare", [command]).quiet is True
    assert cli._build_step_config("build", [command]).quiet is True
    assert cli._build_step_config("push", [command]).quiet is True


def test_required_gate_runs_declared_command(tmp_path, monkeypatch):
    project = _project("alpha", steps={"run-tests": [object()]})
    calls: list[tuple[str, Path]] = []
    monkeypatch.setattr(
        cli, "run_project_step",
        lambda _project, step, root, _logs: calls.append((step, root)),
    )

    cli._run_release_gates(tmp_path, {"alpha": project}, ["alpha"])

    assert calls == [("run-tests", tmp_path)]


def test_prepare_commits_only_declared_generated_paths(tmp_path, monkeypatch):
    project = _project("alpha")
    project.commit_generated = ("generated",)
    monkeypatch.setattr(cli, "_worktree_changed_paths", lambda _root: ["alpha/generated/result.txt"])
    calls = []
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **kwargs: calls.append((argv, kwargs)))

    assert cli._commit_prepared_generated(tmp_path, project) is True
    assert calls[0][0] == ["git", "add", "-A", "--", "alpha/generated/result.txt"]
    assert calls[1][0][-1] == "chore(alpha): prepare release inputs"


def test_prepare_rejects_undeclared_write(tmp_path, monkeypatch):
    project = _project("alpha")
    project.commit_generated = ("generated",)
    monkeypatch.setattr(cli, "_worktree_changed_paths", lambda _root: ["alpha/not-allowed.txt"])

    with pytest.raises(RuntimeError, match="undeclared paths"):
        cli._commit_prepared_generated(tmp_path, project)


def test_prepare_generates_and_commits_declared_changelog(tmp_path, monkeypatch):
    """A changelog-only release has a source-first prepare commit before its gate."""
    project = _project("alpha")
    project.changelog = "CHANGES.md"
    generated: list[tuple] = []
    committed: list[object] = []

    import cmru.changelog
    monkeypatch.setattr(
        cmru.changelog, "generate_release_changelog",
        lambda *args, **kwargs: generated.append((args, kwargs)) or True,
    )
    monkeypatch.setattr(cli, "_commit_prepared_generated", lambda *_args: committed.append(True) or True)

    cli._prepare_release_projects(tmp_path, {"alpha": project}, ["alpha"], minor=True)

    assert generated[0][0][:2] == (tmp_path, project)
    assert generated[0][1]["minor"] is True
    assert committed == [True]


def test_worktree_changed_paths_accepts_clean_git_queries(tmp_path):
    """A clean diff has empty stdout, not an absent Git result."""
    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    assert cli._worktree_changed_paths(tmp_path) == []


def test_uncommitted_release_paths_flags_only_the_dirty_project():
    with _OriginAndClone() as h:
        (h.repo_root / "alpha").mkdir()
        (h.repo_root / "alpha" / "untracked.txt").write_text("oops\n")
        ordered = {"alpha": _project("alpha"), "beta": _project("beta")}

        dirty = cli._uncommitted_release_paths(h.repo_root, ordered, ["alpha", "beta"])

        assert list(dirty) == ["alpha"]
        assert dirty["alpha"] == ["alpha/untracked.txt"]


def test_uncommitted_release_paths_ignores_files_outside_declared_project_paths():
    with _OriginAndClone() as h:
        (h.repo_root / "unrelated.txt").write_text("scratch notes\n")
        ordered = {"alpha": _project("alpha")}

        assert cli._uncommitted_release_paths(h.repo_root, ordered, ["alpha"]) == {}


def test_release_aborts_before_creating_a_workspace_when_a_released_project_is_dirty(monkeypatch):
    with _OriginAndClone() as h:
        config = h.repo_root / "cmru.toml"
        config.write_text("", encoding="utf-8")
        (h.repo_root / "alpha").mkdir()
        (h.repo_root / "alpha" / "untracked.txt").write_text("oops\n")

        project = _project("alpha")
        loaded = (h.repo_root, {"alpha": project}, ["alpha"], ["alpha"], [], "project-first", {},
                  SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
        calls: list[object] = []

        monkeypatch.setattr(cli, "load_config", lambda _path: loaded)
        monkeypatch.setattr(cli, "apply_release_env", lambda *_args: None)
        monkeypatch.setattr(transaction, "release_lock", lambda _root: nullcontext())
        monkeypatch.setattr(transaction, "create_workspace", lambda _root, *, base: calls.append("created"))
        monkeypatch.setattr(transaction, "run_child", lambda _workspace, args: calls.append("ran-child") or 0)

        with pytest.raises(SystemExit) as exc:
            cli.main(["release", "--config", str(config), "--project", "alpha"])

        assert exc.value.code == 2
        # It never got as far as fetching origin or creating the isolated worktree.
        assert calls == []


def test_release_proceeds_when_uncommitted_changes_are_explicitly_allowed(monkeypatch):
    with _OriginAndClone() as h:
        config = h.repo_root / "cmru.toml"
        config.write_text("", encoding="utf-8")
        (h.repo_root / "alpha").mkdir()
        (h.repo_root / "alpha" / "untracked.txt").write_text("oops\n")

        project = _project("alpha")
        loaded = (h.repo_root, {"alpha": project}, ["alpha"], ["alpha"], [], "project-first", {},
                  SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
        workspace = transaction.ReleaseWorkspace(h.repo_root, h.repo_root / "release", "cmru/release/x", "a" * 40)
        calls: list[object] = []

        monkeypatch.setattr(cli, "load_config", lambda _path: loaded)
        monkeypatch.setattr(cli, "apply_release_env", lambda *_args: None)
        monkeypatch.setattr(transaction, "release_lock", lambda _root: nullcontext())
        monkeypatch.setattr(transaction, "fetch_origin_main", lambda _root: "a" * 40)
        monkeypatch.setattr(transaction, "assert_local_main_not_ahead", lambda _root: 0)
        monkeypatch.setattr(transaction, "create_workspace", lambda _root, *, base: workspace)
        monkeypatch.setattr(transaction, "copy_secret_overlays", lambda *_args: None)
        monkeypatch.setattr(transaction, "run_child", lambda _workspace, args: calls.append("ran-child") or 0)
        monkeypatch.setattr(transaction, "remove_workspace", lambda _w: None)
        monkeypatch.setattr(transaction, "remove_backup_branch", lambda _w: None)
        monkeypatch.setattr(transaction, "sync_local_main", lambda _root: True)

        with pytest.raises(SystemExit) as exc:
            cli.main(["release", "--config", str(config), "--project", "alpha", "--allow-uncommitted"])

        assert exc.value.code == 0
        assert "ran-child" in calls


def test_dry_run_is_not_blocked_by_uncommitted_release_path_changes(monkeypatch):
    """--dry-run has no publish step to protect against silently-omitted local
    edits, and "I have uncommitted work I haven't committed yet" is exactly the
    state you'd run a preview in — the preflight must not fire for it."""
    with _OriginAndClone() as h:
        config = h.repo_root / "cmru.toml"
        config.write_text("", encoding="utf-8")
        (h.repo_root / "alpha").mkdir()
        (h.repo_root / "alpha" / "untracked.txt").write_text("oops\n")

        project = _project("alpha")
        loaded = (h.repo_root, {"alpha": project}, ["alpha"], ["alpha"], [], "project-first", {},
                  SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
        workspace = transaction.ReleaseWorkspace(h.repo_root, h.repo_root / "release", "cmru/release/x", "a" * 40)
        calls: list[object] = []

        monkeypatch.setattr(cli, "load_config", lambda _path: loaded)
        monkeypatch.setattr(cli, "apply_release_env", lambda *_args: None)
        monkeypatch.setattr(transaction, "release_lock", lambda _root: nullcontext())
        monkeypatch.setattr(transaction, "fetch_origin_main", lambda _root: "a" * 40)
        monkeypatch.setattr(transaction, "assert_local_main_not_ahead", lambda _root: 0)
        monkeypatch.setattr(transaction, "create_workspace", lambda _root, *, base: workspace)
        monkeypatch.setattr(transaction, "copy_secret_overlays", lambda *_args: None)
        monkeypatch.setattr(transaction, "run_child", lambda _workspace, args: calls.append("ran-child") or 0)
        monkeypatch.setattr(transaction, "remove_workspace", lambda _w: None)
        monkeypatch.setattr(transaction, "remove_backup_branch", lambda _w: None)
        monkeypatch.setattr(transaction, "sync_local_main", lambda _root: True)

        with pytest.raises(SystemExit) as exc:
            cli.main(["release", "--config", str(config), "--project", "alpha", "--dry-run"])

        assert exc.value.code == 0
        assert "ran-child" in calls  # never hit the exit(2) uncommitted-changes gate


def test_parent_release_launches_isolated_child_and_never_runs_in_caller(
    tmp_path, monkeypatch
):
    config = tmp_path / "cmru.toml"
    config.write_text(
        """
[github]
owner = "octocat"
repo = "demo"
owner_type = "user"
[orchestration]
project_order = ["alpha"]
[project.alpha]
prefix = "alpha-v"
cwd = "alpha"
""",
        encoding="utf-8",
    )
    project = _project("alpha")
    loaded = (tmp_path, {"alpha": project}, ["alpha"], ["alpha"], [], "project-first", {},
              SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "release", "cmru/release/x", "a" * 40)
    calls: list[object] = []

    monkeypatch.setattr(cli, "load_config", lambda _path: loaded)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_args: None)
    monkeypatch.setattr(cli, "_uncommitted_release_paths", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(transaction, "release_lock", lambda _root: nullcontext())
    monkeypatch.setattr(transaction, "fetch_origin_main", lambda _root: "a" * 40)
    monkeypatch.setattr(transaction, "assert_local_main_not_ahead", lambda _root: 0)
    monkeypatch.setattr(transaction, "create_workspace", lambda _root, *, base: workspace)
    monkeypatch.setattr(transaction, "copy_secret_overlays", lambda *_args: calls.append("secret"))
    monkeypatch.setattr(transaction, "run_child", lambda _workspace, args: calls.append(list(args)) or 0)
    monkeypatch.setattr(transaction, "remove_workspace", lambda _workspace: calls.append("removed"))
    monkeypatch.setattr(transaction, "remove_backup_branch", lambda _workspace: calls.append("backup-removed"))
    monkeypatch.setattr(transaction, "forget_release_scope", lambda _root, _w: None)
    monkeypatch.setattr(transaction, "sync_local_main", lambda _root: calls.append("synced") or True)

    with pytest.raises(SystemExit) as exc:
        cli.main(["release", "--config", str(config), "--project", "alpha"])

    assert exc.value.code == 0
    assert not any(isinstance(call, tuple) for call in calls)
    assert ["--project", "alpha", "--config", "cmru.toml"] in calls
    assert "backup-removed" in calls
    assert "synced" in calls
    assert "removed" in calls


def test_parent_reverts_promotion_and_syncs_local_main_on_child_failure(tmp_path, monkeypatch):
    config = tmp_path / "cmru.toml"
    config.write_text(
        """
[github]
owner = "octocat"
repo = "demo"
owner_type = "user"
[orchestration]
project_order = ["alpha"]
[project.alpha]
prefix = "alpha-v"
cwd = "alpha"
""",
        encoding="utf-8",
    )
    project = _project("alpha")
    loaded = (tmp_path, {"alpha": project}, ["alpha"], ["alpha"], [], "project-first", {},
              SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "release", "cmru/release/x", "a" * 40)
    calls: list[object] = []

    monkeypatch.setattr(cli, "load_config", lambda _path: loaded)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_args: None)
    monkeypatch.setattr(cli, "_uncommitted_release_paths", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(transaction, "release_lock", lambda _root: nullcontext())
    monkeypatch.setattr(transaction, "fetch_origin_main", lambda _root: "a" * 40)
    monkeypatch.setattr(transaction, "assert_local_main_not_ahead", lambda _root: 0)
    monkeypatch.setattr(transaction, "create_workspace", lambda _root, *, base: workspace)
    monkeypatch.setattr(transaction, "copy_secret_overlays", lambda *_args: calls.append("secret"))
    # Child fails (e.g. build/publish) after it already promoted origin/main.
    monkeypatch.setattr(transaction, "run_child", lambda _workspace, args: calls.append(list(args)) or 1)
    monkeypatch.setattr(transaction, "promotion_landed", lambda _root, _w: calls.append("checked-promotion") or True)
    monkeypatch.setattr(transaction, "read_release_progress", lambda _root, _w: None)
    monkeypatch.setattr(
        transaction, "revert_promotion",
        lambda _w, *, from_sha=None: calls.append("reverted") or transaction.RevertResult(ok=True, reverted=True),
    )
    monkeypatch.setattr(transaction, "sync_local_main", lambda _root: calls.append("synced") or True)
    remove_calls: list[object] = []
    monkeypatch.setattr(transaction, "remove_workspace", lambda _w: remove_calls.append("removed"))
    monkeypatch.setattr(transaction, "remove_backup_branch", lambda _w: remove_calls.append("backup-removed"))

    with pytest.raises(SystemExit) as exc:
        cli.main(["release", "--config", str(config), "--project", "alpha"])

    assert exc.value.code == 1
    assert calls.index("checked-promotion") < calls.index("reverted") < calls.index("synced")
    # A failed release retains the worktree/branch for inspection — cleanup must not run.
    assert remove_calls == []


def test_parent_skips_revert_when_promotion_never_landed(tmp_path, monkeypatch):
    config = tmp_path / "cmru.toml"
    config.write_text(
        """
[github]
owner = "octocat"
repo = "demo"
owner_type = "user"
[orchestration]
project_order = ["alpha"]
[project.alpha]
prefix = "alpha-v"
cwd = "alpha"
""",
        encoding="utf-8",
    )
    project = _project("alpha")
    loaded = (tmp_path, {"alpha": project}, ["alpha"], ["alpha"], [], "project-first", {},
              SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "release", "cmru/release/x", "a" * 40)
    calls: list[object] = []

    monkeypatch.setattr(cli, "load_config", lambda _path: loaded)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_args: None)
    monkeypatch.setattr(cli, "_uncommitted_release_paths", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(transaction, "release_lock", lambda _root: nullcontext())
    monkeypatch.setattr(transaction, "fetch_origin_main", lambda _root: "a" * 40)
    monkeypatch.setattr(transaction, "assert_local_main_not_ahead", lambda _root: 0)
    monkeypatch.setattr(transaction, "create_workspace", lambda _root, *, base: workspace)
    monkeypatch.setattr(transaction, "copy_secret_overlays", lambda *_args: None)
    # Child fails before ever reaching promote_workspace (e.g. gates failed).
    monkeypatch.setattr(transaction, "run_child", lambda _workspace, args: 1)
    monkeypatch.setattr(transaction, "promotion_landed", lambda _root, _w: False)
    monkeypatch.setattr(
        transaction, "revert_promotion",
        lambda _w, **_kw: calls.append("reverted") or transaction.RevertResult(ok=True, reverted=True),
    )
    monkeypatch.setattr(transaction, "sync_local_main", lambda _root: calls.append("synced") or True)

    with pytest.raises(SystemExit) as exc:
        cli.main(["release", "--config", str(config), "--project", "alpha"])

    assert exc.value.code == 1
    assert "reverted" not in calls   # nothing to revert — promotion never landed
    assert "synced" in calls         # local main is still resynced regardless


def test_resume_and_abandon_are_mutually_exclusive(tmp_path):
    config = tmp_path / "cmru.toml"
    config.write_text("", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        cli.main([
            "release", "--config", str(config),
            "--resume", str(tmp_path / "retained"), "--abandon", "all-previous",
        ])

    assert exc.value.code == 2


def test_abandon_all_previous_scopes_to_the_requested_project_then_proceeds(tmp_path, monkeypatch):
    config = tmp_path / "cmru.toml"
    config.write_text(
        """
[github]
owner = "octocat"
repo = "demo"
owner_type = "user"
[orchestration]
project_order = ["alpha", "beta"]
default_projects = ["alpha", "beta"]
[project.alpha]
prefix = "alpha-v"
cwd = "alpha"
[project.beta]
prefix = "beta-v"
cwd = "beta"
""",
        encoding="utf-8",
    )
    projects = {"alpha": _project("alpha"), "beta": _project("beta")}
    loaded = (tmp_path, projects, ["alpha", "beta"], ["alpha", "beta"], [], "project-first", {},
              SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "release", "cmru/release/x", "a" * 40)
    calls: list[object] = []

    monkeypatch.setattr(cli, "load_config", lambda _path: loaded)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_args: None)
    monkeypatch.setattr(cli, "_uncommitted_release_paths", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(transaction, "release_lock", lambda _root: nullcontext())
    monkeypatch.setattr(
        transaction, "abandon_previous",
        lambda _root, scope: calls.append(("abandon_previous", list(scope))) or ["cmru/release/old"],
    )
    monkeypatch.setattr(transaction, "fetch_origin_main", lambda _root: "a" * 40)
    monkeypatch.setattr(transaction, "assert_local_main_not_ahead", lambda _root: 0)
    monkeypatch.setattr(transaction, "create_workspace", lambda _root, *, base: workspace)
    monkeypatch.setattr(transaction, "copy_secret_overlays", lambda *_args: None)
    monkeypatch.setattr(transaction, "run_child", lambda _workspace, args: calls.append("ran-child") or 0)
    monkeypatch.setattr(transaction, "remove_workspace", lambda _w: None)
    monkeypatch.setattr(transaction, "forget_release_scope", lambda _root, _w: None)
    monkeypatch.setattr(transaction, "remove_backup_branch", lambda _w: None)
    monkeypatch.setattr(transaction, "sync_local_main", lambda _root: True)

    with pytest.raises(SystemExit) as exc:
        cli.main([
            "release", "--config", str(config), "--project", "alpha", "--abandon", "all-previous",
        ])

    assert exc.value.code == 0
    # --project alpha narrows the abandon scope to alpha only, not the full default set.
    assert ("abandon_previous", ["alpha"]) in calls
    # Abandoning didn't stop the run — a fresh release still proceeded afterward.
    assert "ran-child" in calls
    assert calls.index(("abandon_previous", ["alpha"])) < calls.index("ran-child")


def test_abandon_all_previous_defaults_to_full_orchestrated_set_without_project_filter(
    tmp_path, monkeypatch,
):
    config = tmp_path / "cmru.toml"
    config.write_text(
        """
[github]
owner = "octocat"
repo = "demo"
owner_type = "user"
[orchestration]
project_order = ["alpha", "beta"]
default_projects = ["alpha", "beta"]
[project.alpha]
prefix = "alpha-v"
cwd = "alpha"
[project.beta]
prefix = "beta-v"
cwd = "beta"
""",
        encoding="utf-8",
    )
    projects = {"alpha": _project("alpha"), "beta": _project("beta")}
    loaded = (tmp_path, projects, ["alpha", "beta"], ["alpha", "beta"], [], "project-first", {},
              SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "release", "cmru/release/x", "a" * 40)
    calls: list[object] = []

    monkeypatch.setattr(cli, "load_config", lambda _path: loaded)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_args: None)
    monkeypatch.setattr(cli, "_uncommitted_release_paths", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(transaction, "release_lock", lambda _root: nullcontext())
    monkeypatch.setattr(
        transaction, "abandon_previous",
        lambda _root, scope: calls.append(("abandon_previous", list(scope))) or [],
    )
    monkeypatch.setattr(transaction, "fetch_origin_main", lambda _root: "a" * 40)
    monkeypatch.setattr(transaction, "assert_local_main_not_ahead", lambda _root: 0)
    monkeypatch.setattr(transaction, "create_workspace", lambda _root, *, base: workspace)
    monkeypatch.setattr(transaction, "copy_secret_overlays", lambda *_args: None)
    monkeypatch.setattr(transaction, "run_child", lambda _workspace, args: 0)
    monkeypatch.setattr(transaction, "remove_workspace", lambda _w: None)
    monkeypatch.setattr(transaction, "forget_release_scope", lambda _root, _w: None)
    monkeypatch.setattr(transaction, "remove_backup_branch", lambda _w: None)
    monkeypatch.setattr(transaction, "sync_local_main", lambda _root: True)

    with pytest.raises(SystemExit) as exc:
        cli.main(["release", "--config", str(config), "--abandon", "all-previous"])

    assert exc.value.code == 0
    assert ("abandon_previous", ["alpha", "beta"]) in calls


def test_abandon_specific_worktree_then_proceeds_with_fresh_release(tmp_path, monkeypatch):
    config = tmp_path / "cmru.toml"
    config.write_text(
        """
[github]
owner = "octocat"
repo = "demo"
owner_type = "user"
[orchestration]
project_order = ["alpha"]
[project.alpha]
prefix = "alpha-v"
cwd = "alpha"
""",
        encoding="utf-8",
    )
    project = _project("alpha")
    loaded = (tmp_path, {"alpha": project}, ["alpha"], ["alpha"], [], "project-first", {},
              SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
    stale_workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "stale", "cmru/release/stale", "b" * 40)
    fresh_workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "fresh", "cmru/release/fresh", "a" * 40)
    calls: list[object] = []

    monkeypatch.setattr(cli, "load_config", lambda _path: loaded)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_args: None)
    monkeypatch.setattr(cli, "_uncommitted_release_paths", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(transaction, "release_lock", lambda _root: nullcontext())
    monkeypatch.setattr(
        transaction, "resume_workspace",
        lambda _root, path: calls.append(("resume_workspace", path)) or stale_workspace,
    )
    monkeypatch.setattr(
        transaction, "abandon_workspace",
        lambda _root, w: calls.append(("abandon_workspace", w.branch)),
    )
    monkeypatch.setattr(transaction, "fetch_origin_main", lambda _root: "a" * 40)
    monkeypatch.setattr(transaction, "assert_local_main_not_ahead", lambda _root: 0)
    monkeypatch.setattr(transaction, "create_workspace", lambda _root, *, base: fresh_workspace)
    monkeypatch.setattr(transaction, "copy_secret_overlays", lambda *_args: None)
    monkeypatch.setattr(transaction, "run_child", lambda ws, args: calls.append(("ran", ws.branch)) or 0)
    monkeypatch.setattr(transaction, "remove_workspace", lambda _w: None)
    monkeypatch.setattr(transaction, "forget_release_scope", lambda _root, _w: None)
    monkeypatch.setattr(transaction, "remove_backup_branch", lambda _w: None)
    monkeypatch.setattr(transaction, "sync_local_main", lambda _root: True)

    with pytest.raises(SystemExit) as exc:
        cli.main([
            "release", "--config", str(config), "--abandon", str(tmp_path / "stale"),
        ])

    assert exc.value.code == 0
    assert ("resume_workspace", tmp_path / "stale") in calls
    assert ("abandon_workspace", "cmru/release/stale") in calls
    # A fresh workspace was created and run — abandoning didn't resume the stale one.
    assert ("ran", "cmru/release/fresh") in calls


def test_local_main_ahead_aborts_before_creating_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(transaction, "local_main_divergence", lambda _root: (2, 0))

    with pytest.raises(RuntimeError, match="Local main is 2 commit\\(s\\) ahead"):
        transaction.assert_local_main_not_ahead(tmp_path)


def test_local_main_behind_is_reported_but_allowed(tmp_path, monkeypatch):
    monkeypatch.setattr(transaction, "local_main_divergence", lambda _root: (0, 3))

    assert transaction.assert_local_main_not_ahead(tmp_path) == 3


def test_tester_gate_maps_the_deepest_cockpit_mount_to_docker_host_path(tmp_path):
    repo = Path("/workspaces/vbpub/.worktrees/release")
    mountinfo = (
        "1 0 0:1 / / rw - overlay overlay rw\n"
        "2 1 0:2 /home/vb/vbpub /workspaces/vbpub rw - ext4 /dev/vda rw\n"
    )

    assert tester_gate._physical_path(repo, mountinfo) == Path("/home/vb/vbpub/.worktrees/release")


def test_tester_gate_uses_explicit_container_workdir_and_no_shell(monkeypatch, tmp_path):
    monkeypatch.setattr(tester_gate, "_physical_path", lambda _path: Path("/host/repo"))

    argv = tester_gate.build_docker_command(
        tmp_path, "cmru", ["/opt/tester-venv/bin/python", "-m", "pytest", "tests", "-q"],
        image="tester-unified:test", memory="3g", memory_swap="16g", cpus="1.5",
    )

    assert argv == [
        "docker", "run", "--rm", "--mount", "type=bind,src=/host/repo,dst=/worktree",
        "--workdir", "/worktree/cmru", "--memory", "3g", "--memory-swap", "16g", "--cpus", "1.5",
        "tester-unified:test",
        "/opt/tester-venv/bin/python", "-m", "pytest", "tests", "-q",
    ]


def test_tester_gate_rejects_paths_outside_the_worktree(tmp_path):
    with pytest.raises(ValueError, match="relative path"):
        tester_gate.build_docker_command(
            tmp_path, "../ciu", ["true"], image="tester-unified:test", memory="3g", memory_swap="16g", cpus="1.5",
        )


def test_tester_gate_resolves_a_consumer_cwd_against_its_git_worktree(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    consumer = repo / "nyxloom"
    consumer.mkdir()

    root, relative = tester_gate._resolve_worktree_context(consumer, ".")

    assert root == repo.resolve()
    assert relative == "nyxloom"


def test_tester_gate_refuses_a_non_git_caller(tmp_path):
    with pytest.raises(SystemExit, match="not inside a Git worktree"):
        tester_gate._resolve_worktree_context(tmp_path, ".")


def test_tester_gate_no_sidecar_by_default_adds_no_docker_wiring(monkeypatch, tmp_path):
    monkeypatch.setattr(tester_gate, "_physical_path", lambda _path: Path("/host/repo"))

    argv = tester_gate.build_docker_command(
        tmp_path, "cmru", ["true"], image="tester-unified:test", memory="3g", memory_swap="16g", cpus="1.5",
    )

    assert "--network" not in argv
    assert not any("DOCKER_HOST" in part for part in argv)


def test_tester_gate_sidecar_name_attaches_network_and_docker_host(monkeypatch, tmp_path):
    monkeypatch.setattr(tester_gate, "_physical_path", lambda _path: Path("/host/repo"))

    argv = tester_gate.build_docker_command(
        tmp_path, "modern-debian-tools-python-debug", ["true"], sidecar_name="cmru-tester-dind-abc123",
        image="tester-unified:test", memory="3g", memory_swap="16g", cpus="1.5",
    )

    assert "--network" in argv
    assert "container:cmru-tester-dind-abc123" in argv
    assert "-e" in argv
    assert "DOCKER_HOST=tcp://localhost:2375" in argv


def test_dind_sidecar_starts_polls_readiness_yields_name_then_tears_down(monkeypatch):
    calls: list[list[str]] = []
    ready_after = 2
    probe_count = {"n": 0}

    def fake_run(argv, **_kw):
        calls.append(argv)
        if argv[:2] == ["docker", "run"]:
            return SimpleNamespace(returncode=0, stdout="containerid\n")
        if argv[:2] == ["docker", "exec"]:
            probe_count["n"] += 1
            ready = probe_count["n"] >= ready_after
            return SimpleNamespace(returncode=0 if ready else 1, stdout="29.6.2\n" if ready else "")
        if argv[:2] == ["docker", "stop"]:
            return SimpleNamespace(returncode=0, stdout="")
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(tester_gate.subprocess, "run", fake_run)
    monkeypatch.setattr(tester_gate.time, "sleep", lambda _s: None)

    with tester_gate.dind_sidecar("docker:dind-test") as name:
        assert name.startswith("cmru-tester-dind-")
        used_name = name

    start_call = next(c for c in calls if c[:2] == ["docker", "run"])
    assert "--privileged" in start_call
    assert used_name in start_call
    assert probe_count["n"] >= ready_after  # readiness was actually polled, not assumed
    stop_call = next(c for c in calls if c[:2] == ["docker", "stop"])
    assert used_name in stop_call


def test_dind_sidecar_tears_down_even_when_the_body_raises(monkeypatch):
    stopped = []

    def fake_run(argv, **_kw):
        if argv[:2] == ["docker", "run"]:
            return SimpleNamespace(returncode=0, stdout="containerid\n")
        if argv[:2] == ["docker", "exec"]:
            return SimpleNamespace(returncode=0, stdout="29.6.2\n")
        if argv[:2] == ["docker", "stop"]:
            stopped.append(argv)
            return SimpleNamespace(returncode=0, stdout="")
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(tester_gate.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="boom"):
        with tester_gate.dind_sidecar("docker:dind-test") as _name:
            raise RuntimeError("boom")

    assert len(stopped) == 1


def test_dind_sidecar_raises_if_never_ready_within_timeout(monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(tester_gate.time, "monotonic", lambda: clock["t"])

    def fake_sleep(seconds):
        clock["t"] += seconds

    monkeypatch.setattr(tester_gate.time, "sleep", fake_sleep)

    def fake_run(argv, **_kw):
        if argv[:2] == ["docker", "run"]:
            return SimpleNamespace(returncode=0, stdout="containerid\n")
        if argv[:2] == ["docker", "exec"]:
            return SimpleNamespace(returncode=1, stdout="")  # never ready
        if argv[:2] == ["docker", "stop"]:
            return SimpleNamespace(returncode=0, stdout="")
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(tester_gate.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="did not become ready"):
        with tester_gate.dind_sidecar("docker:dind-test", ready_timeout=2.0):
            pass  # pragma: no cover — never reached


def test_tester_gate_main_wires_enable_docker_through_a_dind_sidecar(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(
        tester_gate, "build_docker_command",
        lambda *args, **kwargs: captured.update(kwargs) or ["true"],
    )
    monkeypatch.setattr(tester_gate, "check_slice_unit", lambda _slice, _image: (True, "ok"))
    monkeypatch.setenv("CMRU_TESTER_UNIFIED_IMAGE", "tester-unified:test")
    monkeypatch.setenv("CMRU_TESTER_CGROUP_PROBE_IMAGE", "debian:test")
    monkeypatch.setenv("CMRU_TESTER_CPUS", "1.5")
    monkeypatch.setenv("CMRU_TESTER_DIND_IMAGE", "docker:dind-test")
    monkeypatch.setenv("CGROUP_PARENT_DEV_BACKGROUND", "dev-background.slice")
    monkeypatch.setenv("CMRU_TESTER_MEMORY", "3g")
    monkeypatch.setenv("CMRU_TESTER_MEMORY_SWAP", "16g")
    monkeypatch.setattr(tester_gate.subprocess, "run", lambda *_a, **_k: SimpleNamespace(returncode=0))
    monkeypatch.setattr(tester_gate.Path, "cwd", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(tester_gate, "_resolve_worktree_context", lambda _cwd, _relative: (tmp_path, _relative))

    @contextmanager
    def fake_sidecar(_image):
        yield "cmru-tester-dind-fixedname"

    monkeypatch.setattr(tester_gate, "dind_sidecar", fake_sidecar)

    with pytest.raises(SystemExit):
        tester_gate.main(["--cwd", "modern-debian-tools-python-debug", "--enable-docker", "--", "true"])

    assert captured["sidecar_name"] == "cmru-tester-dind-fixedname"
    assert captured["cgroup_parent"] == "dev-background.slice"
    assert captured["cgroup_parent_dev_background"] == "dev-background.slice"
    assert captured["memory"] == "3g"
    assert captured["memory_swap"] == "16g"
    assert captured["cpus"] == "1.5"


def test_tester_gate_main_skips_sidecar_when_docker_not_enabled(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(
        tester_gate, "build_docker_command",
        lambda *args, **kwargs: captured.update(kwargs) or ["true"],
    )
    monkeypatch.setattr(tester_gate, "check_slice_unit", lambda _slice, _image: (True, "ok"))
    monkeypatch.setenv("CMRU_TESTER_UNIFIED_IMAGE", "tester-unified:test")
    monkeypatch.setenv("CMRU_TESTER_CGROUP_PROBE_IMAGE", "debian:test")
    monkeypatch.setenv("CMRU_TESTER_CPUS", "1.5")
    monkeypatch.setenv("CGROUP_PARENT_DEV_BACKGROUND", "dev-background.slice")
    monkeypatch.setenv("CMRU_TESTER_MEMORY", "3g")
    monkeypatch.setenv("CMRU_TESTER_MEMORY_SWAP", "16g")
    monkeypatch.setattr(tester_gate.subprocess, "run", lambda *_a, **_k: SimpleNamespace(returncode=0))
    monkeypatch.setattr(tester_gate.Path, "cwd", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(tester_gate, "_resolve_worktree_context", lambda _cwd, _relative: (tmp_path, _relative))

    def fail_if_called(_image):
        raise AssertionError("dind_sidecar() must not run when --enable-docker is absent")

    monkeypatch.setattr(tester_gate, "dind_sidecar", fail_if_called)

    with pytest.raises(SystemExit):
        tester_gate.main(["--cwd", "cmru", "--", "true"])

    assert captured.get("sidecar_name") is None


def test_tester_gate_main_errors_when_no_cgroup_parent_resolvable(monkeypatch, tmp_path):
    monkeypatch.setenv("CMRU_TESTER_UNIFIED_IMAGE", "tester-unified:test")
    monkeypatch.delenv("CMRU_TESTER_CGROUP_PARENT", raising=False)
    monkeypatch.delenv("CGROUP_PARENT_DEV_BACKGROUND", raising=False)
    monkeypatch.setattr(tester_gate.Path, "cwd", staticmethod(lambda: tmp_path))

    def fail_if_called(*_a, **_k):
        raise AssertionError("must not launch docker when cgroup_parent is unresolvable")

    monkeypatch.setattr(tester_gate.subprocess, "run", fail_if_called)

    with pytest.raises(SystemExit, match="no cgroup_parent resolvable"):
        tester_gate.main(["--cwd", "cmru", "--", "true"])


def test_tester_gate_main_errors_when_no_memory_resolvable(monkeypatch, tmp_path):
    monkeypatch.setenv("CMRU_TESTER_UNIFIED_IMAGE", "tester-unified:test")
    monkeypatch.setenv("CGROUP_PARENT_DEV_BACKGROUND", "dev-background.slice")
    monkeypatch.delenv("CMRU_TESTER_MEMORY", raising=False)
    monkeypatch.setenv("CMRU_TESTER_MEMORY_SWAP", "16g")
    monkeypatch.setenv("CMRU_TESTER_CGROUP_PROBE_IMAGE", "debian:test")
    monkeypatch.setattr(tester_gate, "check_slice_unit", lambda _slice, _image: (True, "ok"))
    monkeypatch.setattr(tester_gate.Path, "cwd", staticmethod(lambda: tmp_path))

    def fail_if_called(*_a, **_k):
        raise AssertionError("must not launch docker when the memory limit is unresolvable")

    monkeypatch.setattr(tester_gate.subprocess, "run", fail_if_called)

    with pytest.raises(SystemExit, match="no memory limit resolvable"):
        tester_gate.main(["--cwd", "cmru", "--", "true"])


def test_tester_gate_main_errors_when_no_memory_swap_resolvable(monkeypatch, tmp_path):
    monkeypatch.setenv("CMRU_TESTER_UNIFIED_IMAGE", "tester-unified:test")
    monkeypatch.setenv("CGROUP_PARENT_DEV_BACKGROUND", "dev-background.slice")
    monkeypatch.setenv("CMRU_TESTER_MEMORY", "3g")
    monkeypatch.delenv("CMRU_TESTER_MEMORY_SWAP", raising=False)
    monkeypatch.setenv("CMRU_TESTER_CGROUP_PROBE_IMAGE", "debian:test")
    monkeypatch.setattr(tester_gate, "check_slice_unit", lambda _slice, _image: (True, "ok"))
    monkeypatch.setattr(tester_gate.Path, "cwd", staticmethod(lambda: tmp_path))

    def fail_if_called(*_a, **_k):
        raise AssertionError("must not launch docker when the memory-swap limit is unresolvable")

    monkeypatch.setattr(tester_gate.subprocess, "run", fail_if_called)

    with pytest.raises(SystemExit, match="no memory-swap limit resolvable"):
        tester_gate.main(["--cwd", "cmru", "--", "true"])


def test_tester_gate_main_errors_when_no_cpu_limit_resolvable(monkeypatch, tmp_path):
    monkeypatch.setenv("CMRU_TESTER_UNIFIED_IMAGE", "tester-unified:test")
    monkeypatch.setenv("CGROUP_PARENT_DEV_BACKGROUND", "dev-background.slice")
    monkeypatch.setenv("CMRU_TESTER_MEMORY", "3g")
    monkeypatch.setenv("CMRU_TESTER_MEMORY_SWAP", "16g")
    monkeypatch.setenv("CMRU_TESTER_CGROUP_PROBE_IMAGE", "debian:test")
    monkeypatch.delenv("CMRU_TESTER_CPUS", raising=False)
    monkeypatch.setattr(tester_gate, "check_slice_unit", lambda _slice, _image: (True, "ok"))
    monkeypatch.setattr(tester_gate.Path, "cwd", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(
        tester_gate.subprocess, "run",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not launch Docker")),
    )

    with pytest.raises(SystemExit, match="no CPU limit resolvable"):
        tester_gate.main(["--cwd", "cmru", "--", "true"])


def test_tester_gate_main_errors_when_no_probe_image_resolvable(monkeypatch, tmp_path):
    monkeypatch.setenv("CMRU_TESTER_UNIFIED_IMAGE", "tester-unified:test")
    monkeypatch.setenv("CGROUP_PARENT_DEV_BACKGROUND", "dev-background.slice")
    monkeypatch.delenv("CMRU_TESTER_CGROUP_PROBE_IMAGE", raising=False)
    monkeypatch.setattr(tester_gate.Path, "cwd", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(
        tester_gate, "check_slice_unit",
        lambda *_a: (_ for _ in ()).throw(AssertionError("must not probe without an image")),
    )

    with pytest.raises(SystemExit, match="no cgroup probe image resolvable"):
        tester_gate.main(["--cwd", "cmru", "--", "true"])


def test_tester_gate_main_errors_when_no_dind_image_resolvable(monkeypatch, tmp_path):
    monkeypatch.setenv("CMRU_TESTER_UNIFIED_IMAGE", "tester-unified:test")
    monkeypatch.setenv("CGROUP_PARENT_DEV_BACKGROUND", "dev-background.slice")
    monkeypatch.setenv("CMRU_TESTER_MEMORY", "3g")
    monkeypatch.setenv("CMRU_TESTER_MEMORY_SWAP", "16g")
    monkeypatch.setenv("CMRU_TESTER_CPUS", "1.5")
    monkeypatch.setenv("CMRU_TESTER_CGROUP_PROBE_IMAGE", "debian:test")
    monkeypatch.delenv("CMRU_TESTER_DIND_IMAGE", raising=False)
    monkeypatch.setattr(tester_gate, "check_slice_unit", lambda _slice, _image: (True, "ok"))
    monkeypatch.setattr(tester_gate.Path, "cwd", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(
        tester_gate, "dind_sidecar",
        lambda *_a: (_ for _ in ()).throw(AssertionError("must not start Docker-in-Docker")),
    )

    with pytest.raises(SystemExit, match="no nested Docker image resolvable"):
        tester_gate.main(["--cwd", "cmru", "--enable-docker", "--", "true"])


def test_tester_gate_forwards_cgroup_parent_dev_background_into_the_container(monkeypatch, tmp_path):
    monkeypatch.setattr(tester_gate, "_physical_path", lambda _path: Path("/host/repo"))

    argv = tester_gate.build_docker_command(
        tmp_path, "cmru", ["true"], memory="3g", memory_swap="16g",
        image="tester-unified:test", cpus="1.5",
        cgroup_parent_dev_background="dev-background.slice",
    )

    assert "-e" in argv
    assert "CGROUP_PARENT_DEV_BACKGROUND=dev-background.slice" in argv


def test_tester_gate_omits_cgroup_parent_dev_background_when_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(tester_gate, "_physical_path", lambda _path: Path("/host/repo"))

    argv = tester_gate.build_docker_command(
        tmp_path, "cmru", ["true"], image="tester-unified:test", memory="3g", memory_swap="16g", cpus="1.5",
    )

    assert not any("CGROUP_PARENT_DEV_BACKGROUND" in part for part in argv)


def test_git_common_dir_returns_none_for_an_ordinary_checkout(tmp_path):
    _init_repo(tmp_path)

    assert tester_gate._git_common_dir(tmp_path) is None


def test_git_common_dir_resolves_a_linked_worktree_to_the_shared_git_dir(tmp_path):
    main_repo = tmp_path / "main"
    _init_repo(main_repo)
    (main_repo / "f.txt").write_text("x")
    _git("add", "f.txt", cwd=main_repo)
    _git("commit", "-q", "-m", "init", cwd=main_repo)
    worktree = tmp_path / "wt"
    _git("worktree", "add", "-q", "-b", "release-test", str(worktree), cwd=main_repo)

    common = tester_gate._git_common_dir(worktree)

    assert common == (main_repo / ".git").resolve()


def test_build_docker_command_mounts_the_shared_git_dir_for_a_linked_worktree(monkeypatch, tmp_path):
    """The exact failure this closes: a release-transaction worktree's `.git`
    is a FILE pointing at an absolute host path outside the mounted subtree
    — without this extra mount, any git command needing history (not just
    the checked-out files) fails inside the gate container with `fatal: not
    a git repository`, even though the worktree itself mounted cleanly."""
    main_repo = tmp_path / "main"
    _init_repo(main_repo)
    (main_repo / "f.txt").write_text("x")
    _git("add", "f.txt", cwd=main_repo)
    _git("commit", "-q", "-m", "init", cwd=main_repo)
    worktree = tmp_path / "wt"
    _git("worktree", "add", "-q", "-b", "release-test2", str(worktree), cwd=main_repo)

    monkeypatch.setattr(tester_gate, "_physical_path", lambda p: p)

    argv = tester_gate.build_docker_command(
        worktree, "cmru", ["true"], image="tester-unified:test", memory="3g", memory_swap="16g", cpus="1.5",
    )

    common_dir = str((main_repo / ".git").resolve())
    assert f"type=bind,src={common_dir},dst={common_dir},readonly" in argv


def test_build_docker_command_adds_no_extra_mount_for_an_ordinary_checkout(monkeypatch, tmp_path):
    _init_repo(tmp_path)
    monkeypatch.setattr(tester_gate, "_physical_path", lambda p: p)

    argv = tester_gate.build_docker_command(
        tmp_path, "cmru", ["true"], image="tester-unified:test", memory="3g", memory_swap="16g", cpus="1.5",
    )

    assert argv.count("--mount") == 1


def test_tester_gate_main_refuses_to_launch_into_a_missing_slice(monkeypatch, tmp_path):
    monkeypatch.setenv("CMRU_TESTER_UNIFIED_IMAGE", "tester-unified:test")
    monkeypatch.setenv("CGROUP_PARENT_DEV_BACKGROUND", "dev-background.slice")
    monkeypatch.setenv("CMRU_TESTER_CGROUP_PROBE_IMAGE", "debian:test")
    monkeypatch.setattr(
        tester_gate, "check_slice_unit",
        lambda _slice, _image: (False, "dev-background.slice: LoadState=not-found — the unit is not installed on this host"),
    )
    monkeypatch.setattr(tester_gate.Path, "cwd", staticmethod(lambda: tmp_path))

    def fail_if_called(*_a, **_k):
        raise AssertionError("must not launch docker into a slice that fails the existence preflight")

    monkeypatch.setattr(tester_gate.subprocess, "run", fail_if_called)

    with pytest.raises(SystemExit, match="refusing to launch"):
        tester_gate.main(["--cwd", "cmru", "--", "true"])


def test_tester_gate_main_refuses_to_launch_without_an_explicit_image(monkeypatch, tmp_path):
    monkeypatch.delenv("CMRU_TESTER_UNIFIED_IMAGE", raising=False)
    monkeypatch.setattr(tester_gate.Path, "cwd", staticmethod(lambda: tmp_path))

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("must not launch docker when the image is unconfigured")

    monkeypatch.setattr(tester_gate.subprocess, "run", fail_if_called)

    with pytest.raises(SystemExit, match="no test image configured"):
        tester_gate.main(["--cwd", "cmru", "--", "true"])


def test_run_child_marks_process_as_transaction_child(tmp_path, monkeypatch):
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "release", "cmru/release/x", "b" * 40)
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["cwd"] = kwargs["cwd"]
        observed["env"] = kwargs["env"]
        return SimpleNamespace(returncode=17)

    monkeypatch.setattr(transaction.subprocess, "run", fake_run)
    assert transaction.run_child(workspace, ["--project", "alpha"]) == 17
    assert observed["command"][-4:] == ["release", "--_transaction-child", "--project", "alpha"]
    assert observed["cwd"] == workspace.path
    assert observed["env"][transaction.CHILD_ENV] == "1"
    assert observed["env"][transaction.BRANCH_ENV] == workspace.branch


def test_promote_workspace_fast_forwards_remote_main():
    with _OriginAndClone() as h:
        workspace_path = h.clone_workspace("cmru/release/abc123")
        base = _git("rev-parse", "HEAD", cwd=workspace_path)
        (workspace_path / "generated.txt").write_text("prepared\n")
        _git("add", "generated.txt", cwd=workspace_path)
        _git("commit", "-q", "-m", "chore: prepare release inputs", cwd=workspace_path)
        workspace = transaction.ReleaseWorkspace(h.repo_root, workspace_path, "cmru/release/abc123", base)

        transaction.promote_workspace(workspace)  # no concurrent push — succeeds on the first try

        _git("fetch", "-q", "origin", "main", cwd=h.repo_root)
        assert _git("rev-parse", "origin/main", cwd=h.repo_root) == _git("rev-parse", "HEAD", cwd=workspace_path)


def test_promote_workspace_rebases_and_retries_past_a_concurrent_unrelated_push():
    """This repo has other concurrent committers — a non-fast-forward rejection
    from a race, not a real conflict, must not fail the whole release."""
    with _OriginAndClone() as h:
        workspace_path = h.clone_workspace("cmru/release/race")
        base = _git("rev-parse", "HEAD", cwd=workspace_path)
        (workspace_path / "generated.txt").write_text("prepared\n")
        _git("add", "generated.txt", cwd=workspace_path)
        _git("commit", "-q", "-m", "chore: prepare release inputs", cwd=workspace_path)
        workspace = transaction.ReleaseWorkspace(h.repo_root, workspace_path, "cmru/release/race", base)

        other = h.clone_workspace("scratch", path_name="other")
        (other / "concurrent.txt").write_text("someone else\n")
        _git("add", "concurrent.txt", cwd=other)
        _git("commit", "-q", "-m", "concurrent change", cwd=other)
        _git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=other)

        transaction.promote_workspace(workspace)  # must rebase onto it and retry, not raise

        _git("fetch", "-q", "origin", "main", cwd=h.repo_root)
        assert _git("rev-parse", "origin/main", cwd=h.repo_root) == _git("rev-parse", "HEAD", cwd=workspace_path)
        remote_files = _git("ls-tree", "-r", "--name-only", "origin/main", cwd=h.repo_root)
        assert "generated.txt" in remote_files  # this release's own change
        assert "concurrent.txt" in remote_files  # the concurrent change, not clobbered


def test_promote_workspace_aborts_and_raises_on_a_real_rebase_conflict():
    with _OriginAndClone() as h:
        workspace_path = h.clone_workspace("cmru/release/conflict")
        base = _git("rev-parse", "HEAD", cwd=workspace_path)
        (workspace_path / "README.md").write_text("workspace change\n")
        _git("add", "README.md", cwd=workspace_path)
        _git("commit", "-q", "-m", "chore: workspace edits README", cwd=workspace_path)
        workspace = transaction.ReleaseWorkspace(h.repo_root, workspace_path, "cmru/release/conflict", base)

        other = h.clone_workspace("scratch2", path_name="other2")
        (other / "README.md").write_text("concurrent conflicting change\n")
        _git("add", "README.md", cwd=other)
        _git("commit", "-q", "-m", "concurrent conflicting edit", cwd=other)
        _git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=other)

        with pytest.raises(RuntimeError, match="real conflict"):
            transaction.promote_workspace(workspace)

        # Never left mid-rebase — a retained worktree must be immediately usable.
        assert not (workspace_path / ".git" / "rebase-merge").exists()
        assert not (workspace_path / ".git" / "rebase-apply").exists()
        assert _git("status", "--porcelain=v1", cwd=workspace_path) == ""


def test_promote_workspace_remaps_an_earlier_projects_checkpoint_after_rebase():
    """A multi-project run records write_release_progress() after each project's
    own promote. A LATER project's rebase must not strand an earlier project's
    already-recorded checkpoint on a commit its rewritten branch no longer has."""
    with _OriginAndClone() as h:
        workspace_path = h.clone_workspace("cmru/release/multi")
        base = _git("rev-parse", "HEAD", cwd=workspace_path)
        workspace = transaction.ReleaseWorkspace(h.repo_root, workspace_path, "cmru/release/multi", base)

        (workspace_path / "a.txt").write_text("project a\n")
        _git("add", "a.txt", cwd=workspace_path)
        _git("commit", "-q", "-m", "chore: project a", cwd=workspace_path)
        after_a = _git("rev-parse", "HEAD", cwd=workspace_path)
        transaction.write_release_progress(h.repo_root, workspace, after_a)

        (workspace_path / "b.txt").write_text("project b\n")
        _git("add", "b.txt", cwd=workspace_path)
        _git("commit", "-q", "-m", "chore: project b", cwd=workspace_path)

        other = h.clone_workspace("scratch3", path_name="other3")
        (other / "concurrent.txt").write_text("someone else\n")
        _git("add", "concurrent.txt", cwd=other)
        _git("commit", "-q", "-m", "concurrent change", cwd=other)
        _git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=other)

        transaction.promote_workspace(workspace)

        new_checkpoint = transaction.read_release_progress(h.repo_root, workspace)
        assert new_checkpoint is not None
        assert new_checkpoint != after_a  # the rebase gave it a brand new SHA
        # Still sits exactly one commit before HEAD (project a done, b pending) —
        # remapped by position, not left pointing at some other commit.
        assert _git("rev-list", "--count", f"{new_checkpoint}..HEAD", cwd=workspace_path) == "1"
        # And it is genuinely reachable from the rebased tip.
        assert _git("merge-base", new_checkpoint, "HEAD", cwd=workspace_path) == new_checkpoint


def test_promote_workspace_raises_immediately_for_a_non_race_push_failure(monkeypatch, tmp_path):
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "release", "cmru/release/x", "c" * 40)
    monkeypatch.setattr(transaction, "read_release_progress", lambda *_a, **_k: None)
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=1, stdout="", stderr="fatal: could not read Password\n")

    monkeypatch.setattr(transaction.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="could not read Password"):
        transaction.promote_workspace(workspace)

    assert len(calls) == 1  # not a race signature — no retry attempted


def test_promote_workspace_gives_up_after_exhausting_retries(monkeypatch, tmp_path):
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "release", "cmru/release/x", "c" * 40)
    monkeypatch.setattr(transaction, "read_release_progress", lambda *_a, **_k: None)
    rejected = SimpleNamespace(returncode=1, stdout="", stderr="! [rejected]  HEAD -> main (non-fast-forward)\n")
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        if argv[:2] == ["git", "push"]:
            return rejected
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(transaction.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="lost the race"):
        transaction.promote_workspace(workspace)

    push_attempts = [c for c in calls if c[:2] == ["git", "push"]]
    assert len(push_attempts) == transaction._PROMOTE_MAX_RETRIES + 1


def test_resume_rejects_worktree_from_another_repository(tmp_path, monkeypatch):
    retained = tmp_path / "retained"
    retained.mkdir()
    monkeypatch.setattr(transaction, "_common_git_dir", lambda path: Path("/a") if path == tmp_path else Path("/b"))

    with pytest.raises(RuntimeError, match="not a worktree"):
        transaction.resume_workspace(tmp_path, retained)


def test_push_backup_branch_force_pushes_under_its_own_name(tmp_path, monkeypatch):
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "release", "cmru/release/x", "c" * 40)
    calls = []
    monkeypatch.setattr(transaction.subprocess, "run", lambda argv, **kwargs: calls.append((argv, kwargs)))

    transaction.push_backup_branch(workspace)

    assert calls[0][0] == ["git", "push", "--force", "origin", "HEAD:refs/heads/cmru/release/x"]
    assert calls[0][1]["cwd"] == workspace.path
    assert calls[0][1]["check"] is True


def test_remove_backup_branch_deletes_remote_branch_best_effort(tmp_path, monkeypatch):
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "release", "cmru/release/x", "c" * 40)
    calls = []
    monkeypatch.setattr(
        transaction.subprocess, "run",
        lambda argv, **kwargs: calls.append((argv, kwargs)) or SimpleNamespace(returncode=1),
    )

    transaction.remove_backup_branch(workspace)  # must not raise even though the mock "fails"

    assert calls[0][0] == ["git", "push", "origin", "--delete", "cmru/release/x"]
    assert calls[0][1]["check"] is False


def test_promotion_landed_false_before_promote_true_after():
    with _OriginAndClone() as h:
        workspace_path = h.clone_workspace("cmru/release/abc123")
        base = _git("rev-parse", "HEAD", cwd=workspace_path)
        (workspace_path / "generated.txt").write_text("prepared\n")
        _git("add", "generated.txt", cwd=workspace_path)
        _git("commit", "-q", "-m", "chore: prepare release inputs", cwd=workspace_path)
        workspace = transaction.ReleaseWorkspace(
            h.repo_root, workspace_path, "cmru/release/abc123", base,
        )

        assert transaction.promotion_landed(h.repo_root, workspace) is False

        transaction.promote_workspace(workspace)

        assert transaction.promotion_landed(h.repo_root, workspace) is True


def test_promotion_landed_false_when_origin_advanced_past_release():
    with _OriginAndClone() as h:
        workspace_path = h.clone_workspace("cmru/release/moved")
        base = _git("rev-parse", "HEAD", cwd=workspace_path)
        workspace = transaction.ReleaseWorkspace(h.repo_root, workspace_path, "cmru/release/moved", base)
        transaction.promote_workspace(workspace)

        other = h.clone_workspace("scratch", path_name="other")
        (other / "z.txt").write_text("z\n")
        _git("add", "z.txt", cwd=other)
        _git("commit", "-q", "-m", "someone else's release", cwd=other)
        _git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=other)

        assert transaction.promotion_landed(h.repo_root, workspace) is False


def test_revert_promotion_restores_content_and_pushes_a_revert_commit():
    with _OriginAndClone() as h:
        workspace_path = h.clone_workspace("cmru/release/def456")
        base = _git("rev-parse", "HEAD", cwd=workspace_path)
        (workspace_path / "generated.txt").write_text("prepared\n")
        _git("add", "generated.txt", cwd=workspace_path)
        _git("commit", "-q", "-m", "chore: prepare release inputs", cwd=workspace_path)
        workspace = transaction.ReleaseWorkspace(
            h.repo_root, workspace_path, "cmru/release/def456", base,
        )
        transaction.promote_workspace(workspace)

        result = transaction.revert_promotion(workspace)
        assert result.ok is True
        assert result.reverted is True  # a revert commit was actually pushed

        origin_main = _git("rev-parse", "main", cwd=h.origin)
        show = subprocess.run(
            ["git", "show", f"{origin_main}:generated.txt"],
            cwd=h.origin, capture_output=True, text=True,
        )
        assert show.returncode != 0  # generated.txt no longer exists at origin/main's tip


def test_revert_promotion_is_a_noop_when_nothing_was_promoted():
    with _OriginAndClone() as h:
        workspace_path = h.clone_workspace("cmru/release/noop")
        base = _git("rev-parse", "HEAD", cwd=workspace_path)
        workspace = transaction.ReleaseWorkspace(h.repo_root, workspace_path, "cmru/release/noop", base)

        result = transaction.revert_promotion(workspace)
        assert result.ok is True
        assert result.reverted is False  # nothing needed undoing — distinct from "reverted"


def test_revert_promotion_fails_closed_when_origin_advanced_past_it():
    with _OriginAndClone() as h:
        workspace_path = h.clone_workspace("cmru/release/conflict")
        base = _git("rev-parse", "HEAD", cwd=workspace_path)
        (workspace_path / "shared.txt").write_text("prepared\n")
        _git("add", "shared.txt", cwd=workspace_path)
        _git("commit", "-q", "-m", "chore: prepare release inputs", cwd=workspace_path)
        workspace = transaction.ReleaseWorkspace(
            h.repo_root, workspace_path, "cmru/release/conflict", base,
        )
        transaction.promote_workspace(workspace)

        # Someone else pushes on top before the revert lands.
        other = h.clone_workspace("scratch2", path_name="other2")
        (other / "shared.txt").write_text("someone else's edit\n")
        _git("add", "shared.txt", cwd=other)
        _git("commit", "-q", "-m", "unrelated edit", cwd=other)
        _git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=other)

        assert transaction.revert_promotion(workspace).ok is False
        # Local revert (successful or aborted) must never leave a dirty working tree.
        assert _git("status", "--porcelain", cwd=workspace_path) == ""


def test_sync_local_main_fast_forwards_when_main_is_checked_out():
    with _OriginAndClone() as h:
        other = h.clone_workspace("scratch", path_name="other")
        (other / "new.txt").write_text("x\n")
        _git("add", "new.txt", cwd=other)
        _git("commit", "-q", "-m", "advance", cwd=other)
        _git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=other)

        assert _git("branch", "--show-current", cwd=h.repo_root) == "main"
        assert transaction.sync_local_main(h.repo_root) is True
        assert _git("rev-parse", "main", cwd=h.repo_root) == _git("rev-parse", "main", cwd=h.origin)


def test_sync_local_main_updates_ref_when_main_is_not_checked_out():
    with _OriginAndClone() as h:
        _git("checkout", "-q", "-b", "other-branch", cwd=h.repo_root)

        other = h.clone_workspace("scratch", path_name="other")
        (other / "new.txt").write_text("y\n")
        _git("add", "new.txt", cwd=other)
        _git("commit", "-q", "-m", "advance", cwd=other)
        _git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=other)

        assert transaction.sync_local_main(h.repo_root) is True
        assert _git("rev-parse", "main", cwd=h.repo_root) == _git("rev-parse", "main", cwd=h.origin)


def test_sync_local_main_rebases_local_only_commits_instead_of_failing():
    """A release only ever commits declared, mechanical generated paths (S-REL.4a),
    so local commits made while it built essentially never touch the same files —
    a rebase should replay them on top cleanly rather than refusing outright."""
    with _OriginAndClone() as h:
        (h.repo_root / "local-work.txt").write_text("local work in progress\n")
        _git("add", "local-work.txt", cwd=h.repo_root)
        _git("commit", "-q", "-m", "wip: local work while release built", cwd=h.repo_root)
        local_commit_before = _git("rev-parse", "HEAD", cwd=h.repo_root)

        other = h.clone_workspace("scratch", path_name="other")
        (other / "release-generated.txt").write_text("generated by the release\n")
        _git("add", "release-generated.txt", cwd=other)
        _git("commit", "-q", "-m", "chore(alpha): prepare release inputs", cwd=other)
        _git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=other)

        assert transaction.sync_local_main(h.repo_root) is True
        assert (h.repo_root / "local-work.txt").exists()
        assert (h.repo_root / "release-generated.txt").exists()
        # History stays linear — no merge commit — but the local commit WAS
        # rewritten (replayed on the new base): same message/content, new hash.
        log = _git("log", "--format=%H %s", cwd=h.repo_root).splitlines()
        assert local_commit_before not in [line.split(" ", 1)[0] for line in log]
        assert any(line.endswith("wip: local work while release built") for line in log)
        parents = _git("log", "-1", "--format=%P", cwd=h.repo_root).split()
        assert len(parents) == 1  # a rebase, not a merge commit
        # Local main is legitimately ahead of origin/main now (the developer's
        # own unpushed work) — exactly as assert_local_main_not_ahead would
        # expect until the developer pushes it themselves.
        ahead, _behind = transaction.local_main_divergence(h.repo_root)
        assert ahead >= 1


def test_sync_local_main_aborts_cleanly_on_real_conflict():
    with _OriginAndClone() as h:
        (h.repo_root / "README.md").write_text("local edit\n")
        _git("add", "README.md", cwd=h.repo_root)
        _git("commit", "-q", "-m", "local edit to README", cwd=h.repo_root)

        other = h.clone_workspace("scratch", path_name="other")
        (other / "README.md").write_text("conflicting release edit\n")
        _git("add", "README.md", cwd=other)
        _git("commit", "-q", "-m", "conflicting release edit", cwd=other)
        _git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=other)

        assert transaction.sync_local_main(h.repo_root) is False
        assert _git("status", "--porcelain", cwd=h.repo_root) == ""  # rebase --abort ran
        assert (h.repo_root / "README.md").read_text() == "local edit\n"  # untouched


def test_sync_local_main_does_not_force_move_a_diverged_non_current_main():
    with _OriginAndClone() as h:
        _git("branch", "extra-main-commit", cwd=h.repo_root)
        _git("checkout", "-q", "extra-main-commit", cwd=h.repo_root)
        (h.repo_root / "x.txt").write_text("x\n")
        _git("add", "x.txt", cwd=h.repo_root)
        _git("commit", "-q", "-m", "commit landed on main via another ref", cwd=h.repo_root)
        _git("branch", "-f", "main", "extra-main-commit", cwd=h.repo_root)
        _git("checkout", "-q", "-b", "other-branch", "extra-main-commit", cwd=h.repo_root)
        stale_main = _git("rev-parse", "main", cwd=h.repo_root)

        other = h.clone_workspace("scratch", path_name="other")
        (other / "y.txt").write_text("y\n")
        _git("add", "y.txt", cwd=other)
        _git("commit", "-q", "-m", "advance", cwd=other)
        _git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=other)

        assert transaction.sync_local_main(h.repo_root) is False
        assert _git("rev-parse", "main", cwd=h.repo_root) == stale_main


# ---------------------------------------------------------------------------
# --abandon: scope-marked cleanup of retained release worktrees
# ---------------------------------------------------------------------------

def test_write_and_read_release_scope_round_trips():
    with _OriginAndClone() as h:
        workspace_path = h.clone_workspace("cmru/release/scope1")
        base = _git("rev-parse", "HEAD", cwd=workspace_path)
        workspace = transaction.ReleaseWorkspace(h.repo_root, workspace_path, "cmru/release/scope1", base)

        assert transaction.read_release_scope(h.repo_root, workspace) is None

        transaction.write_release_scope(h.repo_root, workspace, ["ciu", "cmru"])

        assert transaction.read_release_scope(h.repo_root, workspace) == ["ciu", "cmru"]


def test_write_and_read_release_progress_round_trips():
    with _OriginAndClone() as h:
        workspace_path = h.clone_workspace("cmru/release/progress1")
        base = _git("rev-parse", "HEAD", cwd=workspace_path)
        workspace = transaction.ReleaseWorkspace(h.repo_root, workspace_path, "cmru/release/progress1", base)

        assert transaction.read_release_progress(h.repo_root, workspace) is None

        transaction.write_release_progress(h.repo_root, workspace, "deadbeef" * 5)

        assert transaction.read_release_progress(h.repo_root, workspace) == "deadbeef" * 5


def test_release_progress_written_from_a_real_worktree_is_visible_from_repo_root():
    """The production shape: the CHILD writes progress with repo_root == the
    worktree path (its own cwd); the PARENT later reads it with repo_root == the
    original checkout. Both must resolve to the same shared git-common-dir.
    clone_workspace() makes an independent clone (its own .git), which cannot
    exercise this — a real `git worktree add` (add_worktree) is required."""
    with _OriginAndClone() as h:
        workspace_path = h.add_worktree("cmru/release/shared-common-dir")
        base = _git("rev-parse", "HEAD", cwd=workspace_path)
        workspace = transaction.ReleaseWorkspace(h.repo_root, workspace_path, "cmru/release/shared-common-dir", base)

        # Child: writes using its own cwd (the worktree) as repo_root.
        transaction.write_release_progress(workspace_path, workspace, "cafef00d" * 5)

        # Parent: reads using the original checkout as repo_root.
        assert transaction.read_release_progress(h.repo_root, workspace) == "cafef00d" * 5


def test_abandon_workspace_forgets_release_progress_too():
    with _OriginAndClone() as h:
        workspace_path = h.add_worktree("cmru/release/progress2")
        base = _git("rev-parse", "HEAD", cwd=workspace_path)
        workspace = transaction.ReleaseWorkspace(h.repo_root, workspace_path, "cmru/release/progress2", base)
        transaction.write_release_progress(h.repo_root, workspace, "cafef00d" * 5)

        transaction.abandon_workspace(h.repo_root, workspace)

        assert transaction.read_release_progress(h.repo_root, workspace) is None


def test_revert_promotion_scoped_to_from_sha_leaves_earlier_commits_alone():
    """Per-project releases (S-REL, build-all-projects-after-another): project A's
    commit is already promoted and published when project B's commit is promoted
    and then B's build/publish fails. Reverting from the checkpoint after A (not
    workspace.base) must undo only B's content, leaving A's live."""
    with _OriginAndClone() as h:
        workspace_path = h.clone_workspace("cmru/release/scoped")
        base = _git("rev-parse", "HEAD", cwd=workspace_path)
        workspace = transaction.ReleaseWorkspace(h.repo_root, workspace_path, "cmru/release/scoped", base)

        # Project A: commit + promote (this succeeds and stays).
        (workspace_path / "project_a.txt").write_text("a released\n")
        _git("add", "project_a.txt", cwd=workspace_path)
        _git("commit", "-q", "-m", "chore(a): prepare release inputs", cwd=workspace_path)
        transaction.promote_workspace(workspace)
        checkpoint_after_a = _git("rev-parse", "HEAD", cwd=workspace_path)

        # Project B: commit + promote, then its build/publish fails.
        (workspace_path / "project_b.txt").write_text("b in flight\n")
        _git("add", "project_b.txt", cwd=workspace_path)
        _git("commit", "-q", "-m", "chore(b): prepare release inputs", cwd=workspace_path)
        transaction.promote_workspace(workspace)

        result = transaction.revert_promotion(workspace, from_sha=checkpoint_after_a)
        assert result.ok is True
        assert result.reverted is True

        origin_main = _git("rev-parse", "main", cwd=h.origin)
        a_present = subprocess.run(
            ["git", "show", f"{origin_main}:project_a.txt"], cwd=h.origin, capture_output=True, text=True,
        )
        b_present = subprocess.run(
            ["git", "show", f"{origin_main}:project_b.txt"], cwd=h.origin, capture_output=True, text=True,
        )
        assert a_present.returncode == 0  # project A's release is untouched
        assert b_present.returncode != 0  # project B's in-flight commit was reverted


@pytest.fixture(autouse=True)
def _explicit_publish_credential(monkeypatch):
    """Transaction tests exercise release mechanics, not secret resolution."""
    monkeypatch.setenv("GITHUB_PUSH_PAT", "test-credential")


def _seq_project(
    name, *, prefix=None, git_tag=True, strategy="scm", steps=None,
    commit_generated=(), build_step="build",
):
    declared_steps = steps or {}
    return SimpleNamespace(
        name=name,
        cwd=name,
        paths=[name],
        prefix=prefix,
        scm_dist=None,
        artifacts=[],
        git_tag=git_tag,
        version=SimpleNamespace(strategy=strategy),
        steps=declared_steps,
        runner_steps={
            step_name: cli._build_step_config(step_name, commands)
            for step_name, commands in declared_steps.items()
        },
        env={},
        build_metadata={},
        artifact_dirs=(),
        build_step=build_step,
        commit_generated=commit_generated,
        github_token="test-credential",
    )


def test_resolve_versions_from_git_skips_projects_not_exactly_tagged_on_head():
    """git describe --exact-match legitimately exits non-zero for any project whose
    tag isn't on the current commit. With projects releasing one after another
    (S-CLI.5a), that's the normal case for every scm_dist project except whichever
    one is currently being tagged/built — must not raise (regression: it did, the
    first time this ran for real against multiple scm_dist projects in one run)."""
    with _OriginAndClone() as h:
        workspace_path = h.clone_workspace("cmru/release/multi-scm")
        _git("tag", "-a", "alpha-v1.0.0", "-m", "alpha 1.0.0", cwd=workspace_path)

        alpha = SimpleNamespace(prefix="alpha-v", scm_dist="alpha")
        beta = SimpleNamespace(prefix="beta-v", scm_dist="beta")  # never tagged at all
        env_alpha, env_beta = (
            "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_ALPHA",
            "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_BETA",
        )
        os.environ.pop(env_alpha, None)
        os.environ.pop(env_beta, None)
        try:
            cli.resolve_versions_from_git(workspace_path, {"alpha": alpha, "beta": beta})
            assert os.environ.get(env_alpha) == "1.0.0"
            assert env_beta not in os.environ
        finally:
            os.environ.pop(env_alpha, None)
            os.environ.pop(env_beta, None)


def test_release_projects_sequentially_lets_a_later_project_see_an_earlier_ones_fresh_tag():
    """The actual bug this whole feature exists to fix: an OCI-image-style project
    (here 'beta', git_tag=False) that resolves a sibling wheel-style project's
    (here 'alpha') release must see alpha's tag as ALREADY published — not the
    previous run's — because alpha's entire cycle (prepare/gate/promote/tag/
    build/publish) finishes before beta's cycle starts."""
    with _OriginAndClone() as h:
        workspace_path = h.clone_workspace("cmru/release/seq")
        (workspace_path / "beta").mkdir()
        # release steps write their own step logs under repo_root/logs/ — ignore
        # it so that doesn't trip release_cmd's clean-working-tree guard.
        (workspace_path / ".gitignore").write_text("logs/\n")
        _git("add", ".gitignore", cwd=workspace_path)
        _git("commit", "-q", "-m", "chore: ignore step logs", cwd=workspace_path)
        _git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=workspace_path)
        base = _git("rev-parse", "HEAD", cwd=workspace_path)
        workspace = transaction.ReleaseWorkspace(h.repo_root, workspace_path, "cmru/release/seq", base)

        alpha = _seq_project(
            "alpha", prefix="alpha-v", git_tag=True, strategy="scm",
            steps={
                "run-tests": [cli.Command(label="t", argv=["true"], cwd=".")],
                "build": [cli.Command(label="build", argv=["true"], cwd=".")],
                "push": [cli.Command(label="push", argv=["true"], cwd=".")],
            },
        )
        beta = _seq_project(
            "beta", git_tag=False, build_step="prepare",
            steps={
                "run-tests": [cli.Command(label="t", argv=["true"], cwd=".")],
                # This is beta standing in for mdt: its own "prepare" (which is
                # where mdt's real image build lives) resolves alpha's release —
                # the assertion below checks it saw alpha's tag, not "(none)".
                    "prepare": [cli.Command(
                        label="resolve alpha",
                        argv=["bash", "-c", "git tag --list 'alpha-v*' > seen_alpha_tag.txt"],
                    cwd=".",
                )],
                "build": [cli.Command(label="build", argv=["true"], cwd=".")],
                "push": [cli.Command(label="push", argv=["true"], cwd=".")],
            },
            commit_generated=("seen_alpha_tag.txt",),
        )
        configs = {"alpha": alpha, "beta": beta}

        released = cli._release_projects_sequentially(
            workspace_path, configs, workspace, ["alpha", "beta"],
            github_config=cli.GitHubConfig("owner", "repo", "test-credential", "user"),
            env_config=cli.ReleaseEnvConfig({}, None),
        )

        assert released == ["alpha (alpha-v0.1.0)", "beta (no git tag)"]
        seen = (workspace_path / "beta" / "seen_alpha_tag.txt").read_text().strip()
        assert seen == "alpha-v0.1.0"  # beta's prepare saw alpha's brand-new tag, live

        # Both projects' work landed on origin/main.
        origin_main = _git("rev-parse", "main", cwd=h.origin)
        beta_file = subprocess.run(
            ["git", "show", f"{origin_main}:beta/seen_alpha_tag.txt"],
            cwd=h.origin, capture_output=True, text=True,
        )
        assert beta_file.returncode == 0
        assert "alpha-v0.1.0" in _git("tag", "--list", "alpha-v*", cwd=h.origin)


def test_release_projects_sequentially_checkpoints_only_up_to_the_last_success():
    """alpha finishes fully (it has no prepare step, so its cycle commits
    nothing — its checkpoint equals the run's starting base). beta's prepare
    commits, but beta's gate (which runs after prepare) fails, so beta advances
    local HEAD without ever promoting. read_release_progress must stop at the
    checkpoint written after alpha, not "wherever HEAD currently is" — proving
    the checkpoint deliberately excludes beta's unpromoted local commit rather
    than just tracking HEAD."""
    with _OriginAndClone() as h:
        workspace_path = h.clone_workspace("cmru/release/partial")
        (workspace_path / "beta").mkdir()
        (workspace_path / ".gitignore").write_text("logs/\n")
        _git("add", ".gitignore", cwd=workspace_path)
        _git("commit", "-q", "-m", "chore: ignore step logs", cwd=workspace_path)
        _git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=workspace_path)
        base = _git("rev-parse", "HEAD", cwd=workspace_path)
        workspace = transaction.ReleaseWorkspace(h.repo_root, workspace_path, "cmru/release/partial", base)

        alpha = _seq_project(
            "alpha", prefix="alpha-v", git_tag=True, strategy="scm",
            steps={
                "run-tests": [cli.Command(label="t", argv=["true"], cwd=".")],
                "build": [cli.Command(label="build", argv=["true"], cwd=".")],
                "push": [cli.Command(label="push", argv=["true"], cwd=".")],
            },
        )
        beta = _seq_project(
            "beta", git_tag=False, build_step="prepare",
            steps={
                "run-tests": [cli.Command(label="t", argv=["false"], cwd=".")],
                    "prepare": [cli.Command(
                        label="write", argv=["bash", "-c", "echo prepared > output.txt"], cwd=".",
                )],
                "build": [cli.Command(label="build", argv=["true"], cwd=".")],
                "push": [cli.Command(label="push", argv=["true"], cwd=".")],
            },
            commit_generated=("output.txt",),
        )
        configs = {"alpha": alpha, "beta": beta}

        with pytest.raises(subprocess.CalledProcessError):
            cli._release_projects_sequentially(
                workspace_path, configs, workspace, ["alpha", "beta"],
                github_config=cli.GitHubConfig("owner", "repo", "test-credential", "user"),
                env_config=cli.ReleaseEnvConfig({}, None),
            )

        # alpha committed nothing (no prepare step), so its checkpoint is `base`.
        checkpoint = transaction.read_release_progress(workspace_path, workspace)
        assert checkpoint == base

        # beta's prepare DID commit locally...
        assert (workspace_path / "beta" / "output.txt").exists()
        assert _git("rev-parse", "HEAD", cwd=workspace_path) != base  # local HEAD moved past checkpoint

        # ...but its gate failed before promote_workspace was ever called for it,
        # so that commit never reached origin/main — the checkpoint correctly
        # excludes it instead of trusting "wherever HEAD ended up".
        assert _git("rev-parse", "main", cwd=h.origin) == base
        assert "alpha-v0.1.0" in _git("tag", "--list", "alpha-v*", cwd=h.origin)


def test_release_projects_sequentially_seeds_checkpoint_from_this_runs_base_not_a_stale_one():
    """--resume reuses the SAME branch token as the failed attempt it's continuing,
    so a stale .progress file from that earlier attempt is still on disk under
    this run's token. Without seeding the checkpoint to *this* run's own base at
    the top of the loop, a later failure would read that stale (older) value and
    could revert past a commit the operator made while fixing things up for the
    resume — see the review finding this test locks in."""
    with _OriginAndClone() as h:
        workspace_path = h.clone_workspace("cmru/release/resume-token")
        (workspace_path / ".gitignore").write_text("logs/\n")
        _git("add", ".gitignore", cwd=workspace_path)
        _git("commit", "-q", "-m", "chore: ignore step logs", cwd=workspace_path)
        _git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=workspace_path)
        stale_base = _git("rev-parse", "HEAD", cwd=workspace_path)
        stale_workspace = transaction.ReleaseWorkspace(
            h.repo_root, workspace_path, "cmru/release/resume-token", stale_base,
        )
        # Simulate the earlier (failed) attempt on this same branch token having
        # already fully released some project, checkpointing at stale_base.
        transaction.write_release_progress(workspace_path, stale_workspace, stale_base)

        # The operator commits a fix while resuming — this run's real base.
        (workspace_path / "fix.txt").write_text("operator's fix\n")
        _git("add", "fix.txt", cwd=workspace_path)
        _git("commit", "-q", "-m", "fix: operator correction", cwd=workspace_path)
        _git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=workspace_path)
        new_base = _git("rev-parse", "HEAD", cwd=workspace_path)
        assert new_base != stale_base
        workspace = transaction.ReleaseWorkspace(h.repo_root, workspace_path, "cmru/release/resume-token", new_base)

        gamma = _seq_project(
            "gamma", git_tag=False,
            steps={
                "run-tests": [cli.Command(label="t", argv=["false"], cwd=".")],
                "build": [cli.Command(label="build", argv=["true"], cwd=".")],
                "push": [cli.Command(label="push", argv=["true"], cwd=".")],
            },
        )
        with pytest.raises(subprocess.CalledProcessError):
            cli._release_projects_sequentially(
                workspace_path, {"gamma": gamma}, workspace, ["gamma"],
                github_config=cli.GitHubConfig("owner", "repo", "test-credential", "user"),
                env_config=cli.ReleaseEnvConfig({}, None),
            )

        checkpoint = transaction.read_release_progress(workspace_path, workspace)
        assert checkpoint == new_base
        assert checkpoint != stale_base


def test_list_retained_workspaces_finds_release_worktrees_only():
    with _OriginAndClone() as h:
        h.add_worktree("cmru/release/abc", path_name="release-a")
        h.add_worktree("not-a-release-branch", path_name="unrelated")

        found = {w.branch for w in transaction.list_retained_workspaces(h.repo_root)}

        assert found == {"cmru/release/abc"}


def test_list_cmru_workspaces_discovers_retained_build_and_release_worktrees():
    with _OriginAndClone() as h:
        h.add_worktree("cmru/release/abc", path_name="release-a")
        h.add_worktree("cmru/build/def", path_name="build-a")
        h.add_worktree("human/topic", path_name="unrelated")

        found = {w.branch for w in transaction.list_cmru_workspaces(h.repo_root)}

        assert found == {"cmru/release/abc", "cmru/build/def"}


def test_retain_successful_build_outputs_copies_commit_addressed_record(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    main_project = repo_root / "alpha"
    child_root = tmp_path / "build-worktree"
    child_project = child_root / "alpha"
    (child_project / "logs" / "cmru").mkdir(parents=True)
    (child_project / "logs" / "cmru" / "run-tests.log").write_text("passed\n")
    (child_project / "dist").mkdir()
    (child_project / "dist" / "alpha.whl").write_bytes(b"wheel bytes")
    main_project.mkdir(parents=True)
    project = SimpleNamespace(project_root=main_project, artifact_dirs=("dist",))
    workspace = transaction.ReleaseWorkspace(
        repo_root=repo_root, path=child_root, branch="cmru/build/abc", base="a" * 40,
    )

    def fake_git(_path, *args, check=True):
        values = {
            ("rev-parse", "HEAD"): "a" * 40,
            ("show", "-s", "--format=%ct", "HEAD"): "0",
            ("show", "-s", "--format=%cI", "HEAD"): "1970-01-01T00:00:00+00:00",
            ("status", "--porcelain=v1", "--untracked-files=all"): " M alpha/generated.txt\n",
        }
        return values[args]

    monkeypatch.setattr(transaction, "_git", fake_git)

    retained = transaction.retain_successful_build_outputs(
        repo_root, workspace, {"alpha": project}, ["alpha"],
    )

    output_id = f"19700101T000000Z_{'a' * 40}"
    assert retained == [main_project / "logs" / output_id, main_project / "artifacts" / output_id]
    manifest = json.loads((main_project / "artifacts" / output_id / "build.json").read_text())
    assert manifest["publication"] == "forbidden"
    assert manifest["source_commit"] == "a" * 40
    assert manifest["source_tree_changes"] == [" M alpha/generated.txt"]
    assert (main_project / "logs" / output_id / "cmru" / "run-tests.log").read_text() == "passed\n"
    assert (main_project / "artifacts" / output_id / "dist" / "alpha.whl").read_bytes() == b"wheel bytes"
    # Retention copied before worktree removal; a caller-side retention failure
    # can therefore still be debugged from the original source tree.
    assert (child_project / "dist" / "alpha.whl").exists()

    dry_targets = transaction.delete_retained_build_output(
        repo_root, project, "alpha", output_id, dry_run=True,
    )
    assert dry_targets == [main_project / "logs" / output_id, main_project / "artifacts" / output_id]
    assert (main_project / "artifacts" / output_id).exists()
    transaction.delete_retained_build_output(repo_root, project, "alpha", output_id, dry_run=False)
    assert not (main_project / "logs" / output_id).exists()
    assert not (main_project / "artifacts" / output_id).exists()


def test_discard_build_workspace_requires_exact_managed_build_worktree():
    with _OriginAndClone() as h:
        parent = h.repo_root / ".worktrees"
        parent.mkdir()
        path = parent / "cmru-build-debug"
        _git("worktree", "add", "-q", "-b", "cmru/build/debug", str(path), "main", cwd=h.repo_root)

        preview = transaction.discard_build_workspace(h.repo_root, path, dry_run=True)
        assert preview.branch == "cmru/build/debug"
        assert path.is_dir()

        transaction.discard_build_workspace(h.repo_root, path, dry_run=False)
        assert not path.exists()


def test_parent_build_retains_successful_outputs_then_removes_worktree(tmp_path, monkeypatch):
    config = tmp_path / "cmru.toml"
    config.write_text("", encoding="utf-8")
    project = SimpleNamespace(project_root=tmp_path / "alpha")
    workspace = transaction.ReleaseWorkspace(
        tmp_path, tmp_path / ".worktrees" / "build", "cmru/build/abc", "a" * 40,
    )
    loaded = (
        tmp_path, {"alpha": project}, ["alpha"], ["alpha"], [], "project-first", {},
        SimpleNamespace(), SimpleNamespace(), SimpleNamespace(),
    )
    calls: list[str] = []

    monkeypatch.setattr(cli, "load_config", lambda _path: loaded)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_args: None)
    monkeypatch.setattr(cli, "_uncommitted_release_paths", lambda *_args: {})
    monkeypatch.setattr(transaction, "release_lock", lambda _root: nullcontext())
    monkeypatch.setattr(transaction, "fetch_origin_main", lambda _root: "a" * 40)
    monkeypatch.setattr(transaction, "assert_local_main_not_ahead", lambda _root: 0)
    monkeypatch.setattr(
        transaction, "create_workspace", lambda _root, *, base, purpose: workspace,
    )
    monkeypatch.setattr(transaction, "copy_secret_overlays", lambda *_args: None)
    monkeypatch.setattr(transaction, "run_child", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        transaction,
        "retain_successful_build_outputs",
        lambda *_args: calls.append("retained") or [tmp_path / "alpha" / "logs" / "record"],
    )
    monkeypatch.setattr(transaction, "remove_workspace", lambda _workspace: calls.append("removed"))

    with pytest.raises(SystemExit) as exc:
        cli.main(["build", "--config", str(config), "--project", "alpha"])

    assert exc.value.code == 0
    assert calls == ["retained", "removed"]


def test_parent_build_failure_keeps_worktree_and_does_not_retain_outputs(tmp_path, monkeypatch):
    config = tmp_path / "cmru.toml"
    config.write_text("", encoding="utf-8")
    project = SimpleNamespace(project_root=tmp_path / "alpha")
    workspace = transaction.ReleaseWorkspace(
        tmp_path, tmp_path / ".worktrees" / "build", "cmru/build/abc", "a" * 40,
    )
    loaded = (
        tmp_path, {"alpha": project}, ["alpha"], ["alpha"], [], "project-first", {},
        SimpleNamespace(), SimpleNamespace(), SimpleNamespace(),
    )
    calls: list[str] = []

    monkeypatch.setattr(cli, "load_config", lambda _path: loaded)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_args: None)
    monkeypatch.setattr(cli, "_uncommitted_release_paths", lambda *_args: {})
    monkeypatch.setattr(transaction, "release_lock", lambda _root: nullcontext())
    monkeypatch.setattr(transaction, "fetch_origin_main", lambda _root: "a" * 40)
    monkeypatch.setattr(transaction, "assert_local_main_not_ahead", lambda _root: 0)
    monkeypatch.setattr(
        transaction, "create_workspace", lambda _root, *, base, purpose: workspace,
    )
    monkeypatch.setattr(transaction, "copy_secret_overlays", lambda *_args: None)
    monkeypatch.setattr(transaction, "run_child", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(
        transaction, "retain_successful_build_outputs", lambda *_args: calls.append("retained"),
    )
    monkeypatch.setattr(transaction, "remove_workspace", lambda _workspace: calls.append("removed"))

    with pytest.raises(SystemExit) as exc:
        cli.main(["build", "--config", str(config), "--project", "alpha"])

    assert exc.value.code == 1
    assert calls == []


def test_abandon_workspace_removes_worktree_branch_backup_and_scope():
    with _OriginAndClone() as h:
        workspace_path = h.add_worktree("cmru/release/def")
        base = _git("rev-parse", "HEAD", cwd=workspace_path)
        workspace = transaction.ReleaseWorkspace(h.repo_root, workspace_path, "cmru/release/def", base)
        transaction.write_release_scope(h.repo_root, workspace, ["ciu"])
        transaction.push_backup_branch(workspace)  # origin now also has cmru/release/def

        transaction.abandon_workspace(h.repo_root, workspace)

        assert not workspace_path.exists()
        assert "cmru/release/def" not in _git("branch", "--list", cwd=h.repo_root)
        remote_branches = _git("ls-remote", "--heads", "origin", cwd=h.repo_root)
        assert "cmru/release/def" not in remote_branches
        assert transaction.read_release_scope(h.repo_root, workspace) is None


def test_abandon_previous_only_abandons_overlapping_scope():
    with _OriginAndClone() as h:
        ciu_path = h.add_worktree("cmru/release/ciu-attempt", path_name="ciu-attempt")
        ciu_ws = transaction.ReleaseWorkspace(
            h.repo_root, ciu_path, "cmru/release/ciu-attempt", _git("rev-parse", "HEAD", cwd=ciu_path),
        )
        transaction.write_release_scope(h.repo_root, ciu_ws, ["ciu"])

        pwmcp_path = h.add_worktree("cmru/release/pwmcp-attempt", path_name="pwmcp-attempt")
        pwmcp_ws = transaction.ReleaseWorkspace(
            h.repo_root, pwmcp_path, "cmru/release/pwmcp-attempt",
            _git("rev-parse", "HEAD", cwd=pwmcp_path),
        )
        transaction.write_release_scope(h.repo_root, pwmcp_ws, ["pwmcp"])

        unscoped_path = h.add_worktree("cmru/release/unscoped-attempt", path_name="unscoped-attempt")

        abandoned = transaction.abandon_previous(h.repo_root, ["ciu"])

        assert abandoned == ["cmru/release/ciu-attempt"]
        assert not ciu_path.exists()
        assert pwmcp_path.exists()       # different scope — left alone
        assert unscoped_path.exists()    # no recorded scope — left alone
