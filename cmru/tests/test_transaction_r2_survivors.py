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

BASE = "a" * 40
BRANCH = "cmru-release-20261128_120000-demo-ab12cd"

# Everything this module's Git children may read must come from the fixture: no
# global or system configuration, no host template, no host hooks, no signing
# key, no inherited repository redirection. The fixture overrides these in the
# child's environment; none of them is a fallback for a missing fact.
_GIT_PATH_KEYS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_TEMPLATE_DIR",
    "GIT_TRACE",
    "GIT_TRACE_PACK_ACCESS",
    "GIT_TRACE_PACKET",
    "GIT_TRACE_PERFORMANCE",
    "GIT_TRACE_SETUP",
    "GIT_TRACE_CURL",
    "GIT_TRACE_CURL_NO_DATA",
    "GIT_TRACE_SHALLOW",
    "GIT_TRACE2",
    "GIT_TRACE2_EVENT",
    "GIT_TRACE2_PERF",
    "GIT_CONFIG",
)


def _isolate_git(monkeypatch, tmp_path: Path) -> Path:
    """Point every Git child of this test at one isolated, empty configuration.

    ``monkeypatch`` restores the process environment at teardown, and both the
    real-Git helper here and the product's own ``run_local_git``/
    ``run_remote_git`` copy ``os.environ`` for their children, so the isolation
    covers the product calls under test as well as the witnesses' own Git.
    """
    isolation = tmp_path / "git-isolation"
    isolation.mkdir(exist_ok=True)
    global_config = isolation / "gitconfig"
    global_config.write_text("", encoding="utf-8")
    hooks = isolation / "hooks"
    hooks.mkdir(exist_ok=True)
    template = isolation / "template"
    template.mkdir(exist_ok=True)
    for key in _GIT_PATH_KEYS:
        monkeypatch.delenv(key, raising=False)
    count = int(os.environ.get("GIT_CONFIG_COUNT", "0") or "0")
    for index in range(count):
        monkeypatch.delenv(f"GIT_CONFIG_KEY_{index}", raising=False)
        monkeypatch.delenv(f"GIT_CONFIG_VALUE_{index}", raising=False)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "0")
    monkeypatch.setenv("GIT_TEMPLATE_DIR", str(template))
    monkeypatch.setenv("GIT_AUTHOR_NAME", "cmru-test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "cmru-test@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "cmru-test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "cmru-test@example.invalid")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2000-01-01T00:00:00 +0000")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2000-01-01T00:00:00 +0000")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    monkeypatch.setenv("GIT_PROTOCOL_FROM_USER", "0")
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("LANGUAGE", "")
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("GIT_DEFAULT_HASH", "sha1")
    # Hooks and signing are off even if an earlier test (or the operator) left a
    # hostile setting behind in the environment.
    host_config = [
        ("core.hooksPath", str(hooks)),
        ("commit.gpgsign", "false"),
        ("tag.gpgsign", "false"),
        ("push.gpgSign", "false"),
        ("init.defaultBranch", "main"),
        ("maintenance.auto", "false"),
        ("maintenance.autoDetach", "false"),
        ("gc.auto", "0"),
        ("gc.autoDetach", "false"),
    ]
    monkeypatch.setenv("GIT_CONFIG_COUNT", str(len(host_config)))
    for index, (key, value) in enumerate(host_config):
        monkeypatch.setenv(f"GIT_CONFIG_KEY_{index}", key)
        monkeypatch.setenv(f"GIT_CONFIG_VALUE_{index}", value)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    return hooks


@pytest.fixture(autouse=True)
def _git_isolation(monkeypatch, tmp_path):
    """No test here may read the host's Git configuration or run its hooks."""
    return _isolate_git(monkeypatch, tmp_path)


def test_a_hostile_global_git_configuration_has_no_effect_on_this_module(monkeypatch, tmp_path):
    """A signing, hook-running host config must not reach this module's Git.

    The hostile configuration is installed BEFORE the module fixture's effect:
    it is written to a fresh file and exported as ``GIT_CONFIG_GLOBAL``, and a
    repository-local ``core.hooksPath`` points at a hook that writes a marker.
    Only the autouse isolation can neutralise it.
    """
    hostile = tmp_path / "hostile-gitconfig"
    marker = tmp_path / "hook-ran"
    hook = tmp_path / "hostile-hook"
    hook.write_text(f"#!/bin/sh\n: > '{marker}'\n", encoding="utf-8")
    hook.chmod(0o755)
    hostile.write_text(
        "[commit]\n\tgpgsign = true\n[tag]\n\tgpgsign = true\n"
        "[gpg]\n\tprogram = /nonexistent/definitely-not-a-signer\n",
        encoding="utf-8",
    )
    hostile_hooks = tmp_path / "hostile-hookdir"
    hostile_hooks.mkdir()
    for name in ("pre-commit", "commit-msg", "post-commit", "pre-push"):
        target = hostile_hooks / name
        target.write_text(hook.read_text(encoding="utf-8"), encoding="utf-8")
        target.chmod(0o755)
    hostile.write_text(
        hostile.read_text(encoding="utf-8")
        + f"[core]\n\thooksPath = {hostile_hooks}\n",
        encoding="utf-8",
    )

    # A real repository whose own commit would run the hostile hook and whose
    # commits would need a signing key, if either setting still applied.
    root = tmp_path / "hostile-repo"
    root.mkdir()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(hostile))
    monkeypatch.delenv("GIT_CONFIG_NOSYSTEM", raising=False)
    subprocess.run(["git", "init", "-q", str(root)], capture_output=True, text=True, check=False)
    hostile_repo_config = root / ".git" / "config"
    hostile_repo_config.write_text(
        hostile_repo_config.read_text(encoding="utf-8")
        + "[commit]\n\tgpgsign = true\n"
        + f"[core]\n\thooksPath = {hostile_hooks}\n",
        encoding="utf-8",
    )

    # The module fixture's isolation replaces that hostile global file, switches
    # hooks and signing off in the environment it hands to Git, and keeps the
    # repository's own ``hooksPath`` from being used.
    isolated = _isolate_git(monkeypatch, tmp_path)
    assert os.environ["GIT_CONFIG_GLOBAL"] != str(hostile)
    assert isolated != hostile_hooks

    # Without the isolation, the same repository really would fail here: the
    # hostile hook runs and ``commit.gpgsign`` demands a signer that is absent.
    unfixed = subprocess.run(
        ["git", "-C", str(root), "-c", f"core.hooksPath={hostile_hooks}",
         "-c", "commit.gpgsign=true", "-c", "gpg.program=/nonexistent/definitely-not-a-signer",
         "-c", "user.name=t", "-c", "user.email=t@t",
         "commit", "--allow-empty", "-q", "-m", "hostile"],
    )
    assert unfixed.returncode != 0
    assert marker.exists()
    marker.unlink()

    (root / "source.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "initial")
    head = _git(root, "rev-parse", "HEAD")
    _git(root, "tag", "hostile-proof")

    assert not marker.exists()
    assert head


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


def test_a_failed_origin_main_fetch_propagates_as_a_called_process_error(tmp_path):
    """A real fetch from an absent local remote surfaces as ``CalledProcessError``."""
    root = _repo(tmp_path)
    # A local ``file://`` remote whose path holds no repository: the child Git
    # fails the fetch itself (exit 128), with no stub rule deciding when to raise
    # and no helper process left behind for this test to reap.
    _git(root, "remote", "add", "origin", f"file://{tmp_path / 'no-such-remote.git'}")

    with pytest.raises(subprocess.CalledProcessError) as error:
        transaction.fetch_origin_main(root)

    assert error.value.returncode not in (0, 1)
    assert "fetch" in list(error.value.cmd)


# --- assert_build_source_publishable ----------------------------------------


def test_a_refused_build_source_ancestor_check_writes_no_git_diagnostic(capfd, tmp_path):
    """The ancestor probe's own diagnostic never reaches the caller.

    ``merge-base --is-ancestor`` with an absent object id really does print:
    the direct probe below records its ``fatal:`` line under this module's
    isolation. The refused product check must contribute nothing to either
    caller stream; the only text allowed through is the fetch notice of the
    preceding, deliberately uncaptured ``fetch_origin_main``, which proves the
    streams are observed and is subtracted here.
    """
    root = _repo(tmp_path)
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    _git(root, "remote", "add", "origin", str(origin))
    _git(root, "push", "-q", "origin", "main:refs/heads/main")
    _git(root, "fetch", "-q", "origin", "main")

    absent = "b" * 40
    direct = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", absent, "origin/main"],
        capture_output=True, text=True,
    )
    assert direct.returncode not in (0, 1)
    assert direct.stderr.startswith("fatal:")  # the probe has output to leak

    capfd.readouterr()
    with pytest.raises(UnsafeRecord, match="checked-out commit"):
        transaction.assert_build_source_publishable(
            root, "demo", {"source_mode": "head", "source_commit": absent},
        )
    captured = capfd.readouterr()
    fetch_notice = f"From {origin}\n * branch            main       -> FETCH_HEAD\n"
    assert captured.out == ""
    assert captured.err == fetch_notice


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
    """The check is a boolean answer, never a copy of Git's complaint on stderr.

    A non-captured child would hand the caller whatever Git writes; these names
    make ``git check-ref-format`` exit 1.
    """
    root = _repo(tmp_path)
    for branch in ("bad..name", "bad name", "has?q"):
        assert transaction._valid_local_branch_name(branch, root) is False
    captured = capfd.readouterr()
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
    argv, _kwargs = calls[-1]
    assert argv == ["git", "-C", str(tmp_path), "merge-base", "--is-ancestor", "b" * 40, "origin/main"]


def test_the_local_branch_probe_asks_for_no_child_output(tmp_path):
    """The probe is a boolean: no child output, no exception, just the exit code."""
    root = _repo(tmp_path)

    assert transaction._valid_local_branch_name("feature/x", root) is True
    assert transaction._valid_local_branch_name("bad..name", root) is False
