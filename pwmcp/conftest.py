"""Project-wide test isolation checks for the PWMCP suite.

Repository-cleanliness guard contract (PWMCP-12..16):

* Scope: Git-visible paths under ``pwmcp/`` only (``git status`` limited to this directory), measured
  before the first test and after the last one. Changes elsewhere in the monorepo are not blamed on PWMCP.
* Out of scope: ignored paths (``.pytest_cache/``, ``__pycache__/``, ``.assay/``, ``coverage.json``) and
  files interpreter-exit writers such as coverage create after the session ends. The guard certifies
  "no new or changed Git-visible entries", not whole-tree cleanliness.
* Prerequisite: a Git checkout this user may read. Missing ``git``, a non-worktree snapshot and a
  ``safe.directory`` refusal each stop the run before the suite with one actionable message;
  ``PWMCP_ALLOW_NO_GIT_GUARD=1`` is the explicit, warned opt-out for snapshot lanes without Git.
* Runs once, in the pytest-xdist controller (or the only process), never in workers, so one worker's
  temporary write can never be attributed to another worker's snapshot window.
* The status query uses ``--no-optional-locks`` (no index refresh contention), has a timeout, and the
  failure report lists a bounded number of added/removed entries plus a count of omitted ones.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

_REPOSITORY = Path(__file__).resolve().parent
_GIT_EXECUTABLE_ENV = "PWMCP_TEST_GIT_EXECUTABLE"
_ALLOW_NO_GUARD_ENV = "PWMCP_ALLOW_NO_GIT_GUARD"
_GIT_TIMEOUT_SECONDS = 60
_REAP_TIMEOUT_SECONDS = 2
_PR_SET_CHILD_SUBREAPER = 36
_PR_GET_CHILD_SUBREAPER = 37
_REPORT_LIMIT = 20
_SNAPSHOT_KEY = pytest.StashKey[tuple[str, ...]]()


class GitGuardUnavailable(RuntimeError):
    """The Git prerequisite of the cleanliness guard is not met; the message says how to fix it."""


def classify_git_failure(stderr: str) -> str:
    """Turn a failed ``git status`` into one actionable diagnostic."""

    text = stderr.strip()
    if "dubious ownership" in text or "safe.directory" in text:
        return (
            "git refuses this checkout (dubious ownership). Run "
            f"'git config --global --add safe.directory {_REPOSITORY}' (or set it in the gate's "
            "environment) so the PWMCP cleanliness guard can read it."
        )
    if "not a git repository" in text:
        return (
            f"{_REPOSITORY} is not inside a Git worktree (a source snapshot?). Run the suite from a "
            f"checkout, or set {_ALLOW_NO_GUARD_ENV}=1 to run without the repository-cleanliness guard."
        )
    return f"git status failed: {text[-500:] or '<no stderr>'}"


def snapshot(
    repository: Path = _REPOSITORY,
    git_executable: str | None = None,
    timeout: float = _GIT_TIMEOUT_SECONDS,
) -> tuple[str, ...]:
    """Git-visible status entries under ``repository`` (sorted lines); raises ``GitGuardUnavailable``."""

    git = git_executable or os.environ.get(_GIT_EXECUTABLE_ENV) or shutil.which("git")
    if git is None:
        raise GitGuardUnavailable(
            f"git is not on PATH. Install it, set {_GIT_EXECUTABLE_ENV}, or set "
            f"{_ALLOW_NO_GUARD_ENV}=1 to run without the repository-cleanliness guard."
        )
    # Own session/process group: on a timeout the whole group is killed. subprocess.run() would kill only
    # the direct child and orphan its descendants (git helpers, a wrapper script's children), which then
    # outlive the test session and are reported by assay as a leaked process tree (PWMCP-33).
    # Child subreaper for the whole query: a killed wrapper's children are re-parented to this process
    # (not to the gate's process-tree window, which counts every adopted zombie as a leak: PWMCP-40), so
    # they can be reaped here.
    with _child_subreaper():
        try:
            process = subprocess.Popen(
                [git, "--no-optional-locks", "status", "--porcelain", "--untracked-files=all", "--", "."],
                cwd=repository,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise GitGuardUnavailable(f"git executable {git!r} cannot be run: {exc}") from exc
        try:
            try:
                stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                _kill_group(process)
                raise GitGuardUnavailable(
                    f"git status did not finish within {timeout:g}s in {repository}"
                ) from exc
            except BaseException:
                _kill_group(process)
                raise
        finally:
            reap_process_group(process.pid)
    if process.returncode != 0:
        raise GitGuardUnavailable(classify_git_failure(stderr))
    return tuple(sorted(stdout.splitlines()))


@contextlib.contextmanager
def _child_subreaper():
    """Make this process a child subreaper for the block (Linux; a no-op elsewhere or when already one)."""

    try:
        libc = ctypes.CDLL(None, use_errno=True)
        prior = ctypes.c_int(0)
        usable = libc.prctl(_PR_GET_CHILD_SUBREAPER, ctypes.byref(prior), 0, 0, 0) == 0 and not prior.value
        usable = usable and libc.prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) == 0
    except (OSError, AttributeError):
        usable = False
    try:
        yield
    finally:
        if usable:
            libc.prctl(_PR_SET_CHILD_SUBREAPER, 0, 0, 0, 0)


def reap_process_group(pgid: int, timeout: float = _REAP_TIMEOUT_SECONDS) -> None:
    """Reap every child of this process in process group ``pgid`` (dead ones; bounded wait for dying ones)."""

    deadline = time.monotonic() + timeout
    while True:
        try:
            pid, _status = os.waitpid(-pgid, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            if time.monotonic() >= deadline:
                return  # a live member that ignores the kill: not ours to wait for
            time.sleep(0.005)


def _kill_group(process: subprocess.Popen[str]) -> None:
    """SIGKILL the child's whole process group and reap it (idempotent)."""

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.communicate(timeout=_REAP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        # A descendant that left the group (setsid) may still hold the pipes open: stop reading them.
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        process.wait()


def describe_change(before: tuple[str, ...], after: tuple[str, ...], limit: int = _REPORT_LIMIT) -> str:
    """Bounded report of entries that appeared or disappeared between two snapshots."""

    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    lines = ["PWMCP tests changed Git-visible repository state under pwmcp/ during the session."]
    for label, entries in (("Added/changed", added), ("No longer listed", removed)):
        if not entries:
            continue
        lines.append(f"{label} ({len(entries)}):")
        lines.extend(f"  {entry}" for entry in entries[:limit])
        if len(entries) > limit:
            lines.append(f"  ... and {len(entries) - limit} more")
    return "\n".join(lines)


def _is_xdist_worker(config: pytest.Config) -> bool:
    return hasattr(config, "workerinput")


def pytest_sessionstart(session: pytest.Session) -> None:
    if _is_xdist_worker(session.config):
        return
    try:
        session.config.stash[_SNAPSHOT_KEY] = snapshot()
    except GitGuardUnavailable as exc:
        if os.environ.get(_ALLOW_NO_GUARD_ENV) == "1":
            sys.stderr.write(f"WARNING: PWMCP repository-cleanliness guard disabled: {exc}\n")
            return
        raise pytest.UsageError(f"PWMCP repository-cleanliness guard cannot run: {exc}") from exc


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    before = session.config.stash.get(_SNAPSHOT_KEY, None)
    if before is None or _is_xdist_worker(session.config):
        return
    try:
        after = snapshot()
    except GitGuardUnavailable as exc:
        sys.stderr.write(f"PWMCP repository-cleanliness guard could not re-read the tree: {exc}\n")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
        return
    if after != before:
        sys.stderr.write(describe_change(before, after) + "\n")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
