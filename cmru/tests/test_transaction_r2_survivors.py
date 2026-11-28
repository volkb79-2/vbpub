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
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import transaction
from cmru.errors import UnsafeRecord

BASE = "a" * 40
BRANCH = "cmru-release-20261128_120000-demo-ab12cd"


# --- helpers -----------------------------------------------------------------


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    """An initialized repository with one commit, so worktrees can be added."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "test")
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
    """The lock lives under the common Git dir and must create missing parents."""
    root = _repo(tmp_path)
    nested = root / "deep" / "nested"
    monkeypatch.setattr(transaction, "_common_git_dir", lambda _root: nested)

    with transaction.release_lock(root):
        assert (nested / "cmru-release.lock").is_file()


# --- fetch_origin_main -------------------------------------------------------


def test_a_failed_origin_main_fetch_propagates_as_a_called_process_error(monkeypatch, tmp_path):
    """A transport failure is the CLI's own fetch-failure outcome, not a generic error."""
    root = _repo(tmp_path)
    seen = {}

    def failing_fetch(repo_root, *args, **kwargs):
        seen.update(repo_root=repo_root, args=args, kwargs=kwargs)
        if kwargs.get("check"):
            raise subprocess.CalledProcessError(128, ["git", *args])
        return SimpleNamespace(returncode=128)

    monkeypatch.setattr(transaction, "run_remote_git", failing_fetch)

    with pytest.raises(subprocess.CalledProcessError):
        transaction.fetch_origin_main(root)
    assert seen["args"] == ("fetch", "--prune", "origin", "main")
    assert seen["kwargs"]["check"] is True


# --- assert_build_source_publishable ----------------------------------------


def test_a_refused_build_source_ancestor_check_writes_no_git_diagnostic(capfd, tmp_path):
    """The ancestor probe's output stays captured; the caller sees only the refusal."""
    root = _repo(tmp_path)
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    _git(root, "remote", "add", "origin", str(origin))
    _git(root, "push", "-q", "origin", "main")

    with pytest.raises(UnsafeRecord, match="checked-out commit"):
        transaction.assert_build_source_publishable(
            root, "demo", {"source_mode": "head", "source_commit": "b" * 40},
        )
    captured = capfd.readouterr()
    assert "fatal" not in captured.err
    assert "fatal" not in captured.out


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


def test_a_ref_name_probe_keeps_gits_diagnostic_out_of_the_callers_output(
    capfd, tmp_path,
):
    """The check is a boolean answer, never a copy of Git's complaint on stderr."""
    root = _repo(tmp_path)
    for branch in ("bad..name", "bad name", "has?q"):
        assert transaction._valid_local_branch_name(branch, root) is False
    captured = capfd.readouterr()
    assert "is not a valid ref name" not in captured.err
    assert captured.err == ""
    assert captured.out == ""


# --- subprocess seams --------------------------------------------------------


def _record_run(monkeypatch, transaction_module, result, calls):
    def fake_run(argv, **kwargs):
        calls.append((list(argv), dict(kwargs)))
        return result

    monkeypatch.setattr(transaction_module, "subprocess", SimpleNamespace(
        run=fake_run, CalledProcessError=subprocess.CalledProcessError,
        CompletedProcess=subprocess.CompletedProcess, DEVNULL=subprocess.DEVNULL,
        SubprocessError=subprocess.SubprocessError,
    ))


def test_the_build_source_ancestor_probe_captures_gits_diagnostic(monkeypatch, tmp_path):
    """A refusal must never hand Git's own complaint to the caller's terminal."""
    monkeypatch.setattr(transaction, "fetch_origin_main", lambda *_a, **_k: BASE)
    calls = []
    _record_run(monkeypatch, transaction, SimpleNamespace(returncode=0, stdout="", stderr=""), calls)

    transaction.assert_build_source_publishable(
        tmp_path, "demo", {"source_mode": "head", "source_commit": "b" * 40},
    )
    argv, kwargs = calls[-1]
    assert argv == ["git", "-C", str(tmp_path), "merge-base", "--is-ancestor", "b" * 40, "origin/main"]
    assert kwargs == {"capture_output": True, "text": True, "check": False}


def test_the_local_branch_probe_asks_for_captured_text_without_checking(monkeypatch, tmp_path):
    """The probe is a boolean: no child output, no exception, just the exit code."""
    calls = []

    def fake_local_git(repo_root, *args, **kwargs):
        calls.append((list(args), dict(kwargs)))
        return SimpleNamespace(returncode=0 if args[-1].endswith("/feature/x") else 129)

    monkeypatch.setattr(transaction, "run_local_git", fake_local_git)

    assert transaction._valid_local_branch_name("feature/x", tmp_path) is True
    assert transaction._valid_local_branch_name("bad..name", tmp_path) is False
    assert calls == [
        (["check-ref-format", "refs/heads/feature/x"],
         {"capture_output": True, "text": True, "check": False}),
        (["check-ref-format", "refs/heads/bad..name"],
         {"capture_output": True, "text": True, "check": False}),
    ]
