"""Behaviour witnesses for the release transaction's Git/ref guards (R2 batch bf1).

Each test pins one observable contract of a ``transaction.py`` helper whose
flipped constant or boolean operator a mutation run left alive: an unchecked
Git read returning instead of raising, the failure message falling back to
stdout, the frozen workspace record, the legacy-progress diagnostic quoting
Git's own stderr, the lock's directory creation, a failed remote fetch
propagating as ``CalledProcessError``, a refused ancestor check writing no
Git diagnostic to the caller's output, legacy adoption refusing a reported
Git top that is not the candidate path, and a leading-dash branch name never
passing as a valid local ref. No product code is changed.
"""
from __future__ import annotations

import dataclasses
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import transaction
from cmru.errors import UnsafeRecord
from tests._git_isolation import git_isolation, isolate_git  # noqa: F401

BASE = "a" * 40
BRANCH = "cmru-release-20261128_120000-demo-ab12cd"
pytestmark = pytest.mark.usefixtures("git_isolation")


def test_a_hostile_global_git_configuration_has_no_effect_on_this_module(monkeypatch, tmp_path):
    """The shared isolation blocks a hostile global hook setting."""
    hostile = tmp_path / "hostile-gitconfig"
    marker = tmp_path / "hook-ran"
    hostile_hooks = tmp_path / "hostile-hookdir"
    hostile_hooks.mkdir()
    hook = hostile_hooks / "pre-commit"
    hook.write_text(f"#!/bin/sh\n: > '{marker}'\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    hostile.write_text(f"[core]\n\thooksPath = {hostile_hooks}\n", encoding="utf-8")
    root = _repo(tmp_path)

    # Prove the executable hook is active with isolation disabled, then restore it.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(hostile))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "0")
    unisolated = transaction.run_local_git(
        root, "commit", "--allow-empty", "-m", "without-isolation",
        capture_output=True, text=True, check=False,
    )
    assert unisolated.returncode != 0
    assert marker.is_file()

    marker.unlink()
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", f"core.hooksPath={hostile_hooks}")
    monkeypatch.setenv("GIT_EXEC_PATH", str(hostile_hooks))
    isolated_hooks = isolate_git(monkeypatch, tmp_path)
    assert "GIT_CONFIG_PARAMETERS" not in os.environ
    assert "GIT_EXEC_PATH" not in os.environ
    assert os.environ["GIT_CONFIG_GLOBAL"] != str(hostile)
    assert isolated_hooks != hostile_hooks

    isolated_parent = tmp_path / "isolated"
    isolated_parent.mkdir()
    root = _repo(isolated_parent)
    isolated_commit = transaction.run_local_git(
        root, "commit", "--allow-empty", "-m", "with-isolation",
        capture_output=True, text=True, check=False,
    )
    assert isolated_commit.returncode == 0, isolated_commit.stderr
    assert not marker.exists()


# --- helpers -----------------------------------------------------------------


def _git(root: Path, *args: str) -> str:
    result = transaction.run_local_git(
        root, *args, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    """An initialized repository with one commit, so worktrees can be added."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    (root / "source.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "initial")
    return root


def _legacy_worktree(tmp_path: Path) -> tuple[Path, Path, str, str]:
    """A raw legacy ``cmru/release/<x>`` worktree with a valid progress record."""
    root = _repo(tmp_path)
    base = _git(root, "rev-parse", "HEAD")
    branch = "cmru/release/candidate"
    path = tmp_path / "retained"
    _git(root, "worktree", "add", "-q", "-b", branch, str(path), base)
    workspace = transaction.ReleaseWorkspace(root, path, branch, base)
    transaction.write_release_progress(root, workspace, base)
    return root, path, branch, base


def _remove_legacy_worktree(root: Path, path: Path, branch: str) -> None:
    _git(root, "worktree", "remove", "--force", str(path))
    _git(root, "branch", "-D", branch)


# --- _git --------------------------------------------------------------------


def test_an_unchecked_git_read_returns_instead_of_raising(tmp_path):
    """``check=False`` is the documented "report, never raise" form of _git."""
    root = _repo(tmp_path)

    assert transaction._git(root, "rev-parse", "--verify", "no-such-ref-here", check=False) == ""
    # The default stays strict: the same command raises with check on.
    with pytest.raises(RuntimeError, match="rev-parse --verify no-such-ref-here failed"):
        transaction._git(root, "rev-parse", "--verify", "no-such-ref-here")


@pytest.mark.parametrize(
    "stderr,stdout,expected,absent",
    [
        ("", "  only-stdout  ", "only-stdout", None),
        ("  only-stderr  ", "only-stdout", "only-stderr", "only-stdout"),
    ],
    ids=["empty-stderr", "empty-stdout-preferred-anyway"],
)
def test_git_failure_message_names_whichever_output_stream_exists(
    monkeypatch, tmp_path, stderr, stdout, expected, absent,
):
    """A failing Git call's diagnostic prefers stderr and falls back to stdout."""
    monkeypatch.setattr(
        transaction.subprocess, "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=3, stdout=stdout, stderr=stderr,
        ),
    )

    with pytest.raises(RuntimeError) as error:
        transaction._git(tmp_path, "rev-parse", "demo")
    message = str(error.value)
    assert "(3): " in message
    assert message.endswith(expected)
    if absent is not None:
        assert absent not in message.split(": ")[-1]


# --- ReleaseWorkspace --------------------------------------------------------


def test_the_release_workspace_record_is_frozen_and_hashable(tmp_path):
    """The snapshot must not be mutable in place after it was recorded."""
    first = transaction.ReleaseWorkspace(tmp_path, tmp_path / "a", BRANCH, BASE)
    second = transaction.ReleaseWorkspace(tmp_path, tmp_path / "a", BRANCH, BASE)

    with pytest.raises(dataclasses.FrozenInstanceError):
        first.branch = "cmru-release-inserted-into"
    assert first.branch == BRANCH
    assert hash(first) == hash(second)


# --- _validate_legacy_release_progress --------------------------------------


def test_an_unusable_progress_object_is_reported_with_gits_own_stderr(tmp_path):
    """A diagnostic failure must carry Git's text, captured as text."""
    root, path, branch, head = _legacy_worktree(tmp_path)
    progress = "b" * 40
    transaction.write_release_progress(
        root, transaction.ReleaseWorkspace(root, path, branch, head), progress,
    )
    probe = subprocess.run(
        ["git", "-C", str(path), "merge-base", "--is-ancestor", progress, head],
        capture_output=True, text=True,
    )
    assert probe.returncode not in (0, 1)
    assert probe.stderr.strip()
    try:
        with pytest.raises(RuntimeError) as error:
            transaction._validate_legacy_release_progress(root, path, branch, head)
        message = str(error.value)
    finally:
        _remove_legacy_worktree(root, path, branch)

    assert message == (
        f"cannot validate legacy release progress for {path} "
        f"({probe.returncode}): {probe.stderr.strip()}"
    )


# --- release_lock ------------------------------------------------------------


def test_the_release_lock_creates_the_scope_directory_it_needs(monkeypatch, tmp_path):
    """The lock requires an existing common directory and is written directly there."""
    root = _repo(tmp_path)
    missing_common = root / "deep" / "nested"
    monkeypatch.setattr(transaction, "_common_git_dir", lambda _root: missing_common)

    with pytest.raises(transaction.TransactionDirectoryRootError):
        with transaction.release_lock(root):
            pass
    assert not missing_common.exists()

    common = root / ".git"
    monkeypatch.setattr(transaction, "_common_git_dir", lambda _root: common)
    with transaction.release_lock(root):
        assert (common / "cmru-release.lock").is_file()


# --- fetch_origin_main -------------------------------------------------------


def test_a_failed_origin_main_fetch_propagates_as_a_called_process_error(
    monkeypatch, tmp_path,
):
    """A disallowed local transport fails before Git starts its helper."""
    root = _repo(tmp_path)
    _git(root, "remote", "add", "origin", f"file://{tmp_path / 'absent-remote.git'}")
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "https")

    with pytest.raises(subprocess.CalledProcessError) as error:
        transaction.fetch_origin_main(root)

    assert error.value.returncode not in (0, 1)
    assert "fetch" in list(error.value.cmd)


# --- assert_build_source_publishable ----------------------------------------


def test_a_refused_build_source_ancestor_check_writes_no_git_diagnostic(
    capfd, monkeypatch, tmp_path,
):
    """The ancestor probe's own diagnostic never reaches the caller.

    ``merge-base --is-ancestor`` with an absent object id really does print:
    the direct probe below records its stderr under this module's isolation.
    The refused product check must not write the probe's stderr to the caller.
    """
    root = _repo(tmp_path)
    head = _git(root, "rev-parse", "HEAD")
    _git(root, "update-ref", "refs/remotes/origin/main", head)
    monkeypatch.setattr(transaction, "fetch_origin_main", lambda *_a, **_k: head)

    absent = "b" * 40
    direct = transaction.run_local_git(
        root, "merge-base", "--is-ancestor", absent, "origin/main",
        capture_output=True, text=True, check=False,
    )
    assert direct.returncode not in (0, 1)
    assert direct.stderr

    capfd.readouterr()
    with pytest.raises(UnsafeRecord, match="checked-out commit"):
        transaction.assert_build_source_publishable(
            root, "demo", {"source_mode": "head", "source_commit": absent},
        )
    captured = capfd.readouterr()
    assert captured.out == ""
    assert captured.err == ""


# --- resume_workspace (legacy adoption) -------------------------------------


def test_legacy_adoption_refuses_a_git_top_that_is_not_the_candidate_path(
    monkeypatch, tmp_path,
):
    """The reported top of the candidate must be the candidate itself."""
    root, path, branch, _base = _legacy_worktree(tmp_path)
    real_shared = transaction._shared_worktree()
    original_discover = real_shared.discover_git_context

    class RelocatingShared:
        def __getattr__(self, name):
            return getattr(real_shared, name)

        def discover_git_context(self, candidate):
            top, common, branch_name, head = original_discover(candidate)
            if Path(candidate).resolve() == path.resolve():
                return f"{top}-reported-elsewhere", common, branch_name, head
            return top, common, branch_name, head

    monkeypatch.setattr(transaction, "_shared_worktree", lambda: RelocatingShared())
    try:
        with pytest.raises(RuntimeError, match="do not share the exact Git family"):
            transaction.resume_workspace(root, path)
    finally:
        _remove_legacy_worktree(root, path, branch)


# --- _valid_local_branch_name ------------------------------------------------


@pytest.mark.parametrize(
    "branch,expected",
    [
        ("feature/release", True),
        ("-dash", False),
        ("-", False),
        ("", False),
        ("bad name", False),
        ("bad..name", False),
    ],
    ids=["plain", "leading-dash", "lone-dash", "empty", "space", "double-dot"],
)
def test_only_a_git_valid_local_branch_name_is_accepted(tmp_path, branch, expected):
    root = _repo(tmp_path)

    assert transaction._valid_local_branch_name(branch, root) is expected


def test_local_branch_name_check_returns_boolean_results(tmp_path):
    root = _repo(tmp_path)
    for branch in ("bad..name", "bad name", "has?q"):
        assert transaction._valid_local_branch_name(branch, root) is False


def test_local_branch_probe_returns_boolean_results(tmp_path):
    root = _repo(tmp_path)

    assert transaction._valid_local_branch_name("feature/x", root) is True
    assert transaction._valid_local_branch_name("bad..name", root) is False


def test_local_branch_probe_keeps_git_trace_out_of_caller(capfd, monkeypatch, tmp_path):
    root = _repo(tmp_path)
    monkeypatch.setenv("GIT_TRACE", "1")

    assert transaction._valid_local_branch_name("feature/x", root) is True
    captured = capfd.readouterr()
    assert captured.out == ""
    assert captured.err == ""
