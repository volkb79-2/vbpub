#!/usr/bin/env python3
"""Run CMRU's registered release lanes from an exact, secret-free snapshot."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
from xml.etree import ElementTree
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterator, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
SECRET_ENV_KEYS = ("GITHUB_PUSH_PAT", "GITHUB_TOKEN", "CMRU_GIT_AUTH_TOKEN")
FACTS_ENV_KEY = "CMRU_ASSAY_BASELINE_FACTS"
EXTRA_MOUNTS_ENV_KEY = "RUN_GATE_EXTRA_MOUNTS"
_GATE_CODE_ROOTS = (
    "cmru/src/cmru",
    "cmru/tools",
    "assay/src/assay",
    "assay/analysis/src",
    "assay/tools",
    "libraries/worktree/src/worktree",
)
_CODE_SUFFIXES = {".py", ".pyc", ".pyo", ".so", ".pyd"}


class GateInterrupted(SystemExit):
    def __init__(self, signum: int):
        self.signum = signum
        super().__init__(128 + signum)


@contextmanager
def _gate_signal_handlers():
    """Turn termination into normal unwinding so lane and lease cleanup runs."""
    previous = {}

    def interrupt(signum, _frame):
        for handled in previous:
            signal.signal(handled, signal.SIG_IGN)
        raise GateInterrupted(signum)

    try:
        for handled in (signal.SIGTERM, signal.SIGHUP):
            previous[handled] = signal.getsignal(handled)
            signal.signal(handled, interrupt)
        yield
    finally:
        for handled, handler in previous.items():
            signal.signal(handled, handler)


def _load_components():
    """Load the selected worktree's CMRU, Assay and baseline helper sources."""
    for source in (
        PROJECT_ROOT / "src",
        REPOSITORY_ROOT / "assay" / "src",
        # cli_extended is the INSTALLED released wheel (CX-D1), never a source root.
        REPOSITORY_ROOT / "libraries" / "worktree" / "src",
    ):
        sys.path.insert(0, str(source))
    from assay import git as assay_git
    import prepare_assay_baseline

    return assay_git, prepare_assay_baseline


def _preflight_assay_source(repo_root: Path) -> None:
    """Check the source tree before any registered lane can install it."""
    script = PROJECT_ROOT / "tools" / "check_assay_source.py"
    assay_source = repo_root / "assay"
    if script.is_symlink() or not script.is_file():
        raise RuntimeError(f"Assay source preflight is missing or unsafe: {script}")
    try:
        result = subprocess.run(
            [sys.executable, str(script), "--assay-source", str(assay_source)],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("could not run the Assay editable-install source preflight") from exc
    if result.returncode != 0 or "CMRU_ASSAY_SOURCE_PREFLIGHT=clean" not in result.stdout.splitlines():
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise RuntimeError(f"Assay editable-install source preflight refused: {detail}")


@contextmanager
def _release_gate_lock(temp_parent: Path) -> Iterator[None]:
    """Serialize snapshot allocation and nested release-gate lanes."""
    lock_path = temp_parent / ".cmru-release-gate.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RuntimeError("CMRU release-gate lock is not a regular file")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _lane_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for key in (*SECRET_ENV_KEYS, FACTS_ENV_KEY, EXTRA_MOUNTS_ENV_KEY):
        environment.pop(key, None)
    return environment


def _assert_no_untracked_gate_code(repo_root: Path) -> None:
    """Refuse Python helpers outside HEAD before importing or executing them."""
    environment = os.environ.copy()
    environment.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_COUNT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
    })
    for key in (
        "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    ):
        environment.pop(key, None)
    for key in tuple(environment):
        if key.startswith("GIT_CONFIG_KEY_") or key.startswith("GIT_CONFIG_VALUE_"):
            environment.pop(key, None)
    result = subprocess.run(
        [
            "git", "ls-files", "--others", "--ignored", "--exclude-standard",
            "-z", "--", *_GATE_CODE_ROOTS,
        ],
        cwd=repo_root, env=environment, capture_output=True, check=False,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).decode("utf-8", "replace").strip()
        raise RuntimeError(f"cannot inventory gate helper source files: {detail}")
    unsafe: list[str] = []
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        relative = PurePosixPath(os.fsdecode(raw_path))
        if "__pycache__" in relative.parts:
            continue  # private PYTHONPYCACHEPREFIX below makes these irrelevant
        path = repo_root.joinpath(*relative.parts)
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode) or relative.suffix in _CODE_SUFFIXES:
            unsafe.append(relative.as_posix())
    if unsafe:
        raise RuntimeError(
            "release gate refuses untracked or ignored executable source outside "
            "the selected HEAD: " + ", ".join(unsafe[:8])
        )


@contextmanager
def _private_gate_bytecode_cache():
    """Keep Python from importing or writing checkout-local bytecode caches."""
    previous_prefix = sys.pycache_prefix
    previous_environment = os.environ.get("PYTHONPYCACHEPREFIX")
    with tempfile.TemporaryDirectory(prefix="cmru-release-gate-pycache-") as cache:
        sys.pycache_prefix = cache
        os.environ["PYTHONPYCACHEPREFIX"] = cache
        try:
            yield
        finally:
            sys.pycache_prefix = previous_prefix
            if previous_environment is None:
                os.environ.pop("PYTHONPYCACHEPREFIX", None)
            else:
                os.environ["PYTHONPYCACHEPREFIX"] = previous_environment


def _is_failure_line(line: str) -> bool:
    return line.startswith(("FAILED ", "ERROR "))


def _report_lane_failure(project_root: Path, lane: str, since_ns: int) -> None:
    """Name the failing tests of a failed lane (BG-03).

    A lane's own console output can hide pytest's short-summary lines (assay
    keeps them only in the verdict's ``result_stdout_tail``). Print every
    ``FAILED``/``ERROR`` line from verdicts and junit files written by this
    lane run so the release log always names the test.
    """
    lines: list[str] = []
    for verdict in sorted((project_root / ".assay").glob("verdict-*.json")):
        try:
            if verdict.stat().st_mtime_ns < since_ns:
                continue
            tail = json.loads(verdict.read_text(encoding="utf-8")).get("result_stdout_tail")
        except (OSError, ValueError):
            continue
        if isinstance(tail, str):
            lines.extend(line for line in tail.splitlines() if _is_failure_line(line))
    junit = project_root / "junit-coverage.xml"
    try:
        if junit.stat().st_mtime_ns >= since_ns:
            for case in ElementTree.parse(junit).iter("testcase"):
                if case.find("failure") is not None or case.find("error") is not None:
                    lines.append(f"FAILED {case.get('classname')}::{case.get('name')} (junit)")
    except (OSError, ElementTree.ParseError):
        pass
    if lines:
        print(f"cmru-release-gate: lane {lane!r} failed; failing tests:", file=sys.stderr)
        for line in dict.fromkeys(lines):
            print(f"  {line}", file=sys.stderr)


POSTPONED_MARKER = Path(".assay") / "mutation-postponed-cmru.json"
POSTPONE_REASON = (
    "operator-approved provisional release: the R2 mutation lane was skipped and "
    "must be run afterwards against the released tag"
)


def _tracking_id(value: str) -> str:
    """Return a stripped, non-empty postponement tracking id or refuse."""
    tracking_id = value.strip()
    if not tracking_id:
        raise argparse.ArgumentTypeError("the postponement tracking id must be non-empty")
    return tracking_id


def _record_postponement(project_root: Path, tracking_id: str) -> Path:
    """Write the retained marker saying the mutation lane was skipped, and WARN.

    The marker lives under ``.assay`` so cmru's ``evidence_paths`` retain it
    with the rest of the gate evidence; it is never written by a full gate.
    """
    marker = project_root / POSTPONED_MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": 1,
        "lane": "mutation",
        "status": "postponed",
        "tracking_id": tracking_id,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reason": POSTPONE_REASON,
    }
    marker.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"cmru-release-gate: WARN: mutation lane POSTPONED ({tracking_id}); "
        f"this gate is PROVISIONAL; marker {marker}",
        file=sys.stderr,
    )
    return marker


def _invoke_lane(
    repo_root: Path,
    lane: str,
    environment: Mapping[str, str],
    worktree_root: Path | None = None,
) -> int:
    if worktree_root is None:
        worktree_root = repo_root
    started = time.time_ns()
    process = subprocess.Popen(
        ["./run-gate.py", "--worktree", str(worktree_root), lane],
        cwd=repo_root / "cmru",
        env=dict(environment),
    )
    try:
        returncode = process.wait()
    except (GateInterrupted, KeyboardInterrupt) as exc:
        signum = exc.signum if isinstance(exc, GateInterrupted) else signal.SIGTERM
        if process.poll() is None:
            try:
                process.send_signal(signum)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=35)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        raise
    if returncode:
        _report_lane_failure(worktree_root / "cmru", lane, started)
    return returncode


def _run_snapshot_lane(
        repo_root: Path, lane: str, environment: Mapping[str, str], snapshot,
        gate_snapshot) -> int:
    _assert_no_untracked_gate_code(repo_root)
    gate_snapshot.prepare_lane(snapshot, lane)
    try:
        result = _invoke_lane(
            repo_root, lane, environment, snapshot.worktree_path,
        )
        snapshot.lane_exit_codes[lane] = result
        return result
    except GateInterrupted as exc:
        snapshot.lane_exit_codes[lane] = 128 + exc.signum
        raise
    except BaseException:
        snapshot.lane_exit_codes[lane] = None
        raise
    finally:
        try:
            gate_snapshot.finish_lane(snapshot, lane)
        finally:
            gate_snapshot.write_snapshot_manifest(snapshot, "running")


def run_release_gate(
    repo_root: Path,
    *,
    temp_parent: Path = Path("/tmp"),
    postpone_mutation: str | None = None,
) -> int:
    with _private_gate_bytecode_cache():
        return _run_release_gate_with_private_cache(
            repo_root, temp_parent=temp_parent,
            postpone_mutation=postpone_mutation,
        )


def _run_release_gate_with_private_cache(
    repo_root: Path,
    *,
    temp_parent: Path = Path("/tmp"),
    postpone_mutation: str | None = None,
) -> int:
    if postpone_mutation is not None:
        postpone_mutation = _tracking_id(postpone_mutation)
    repo_root = repo_root.resolve(strict=True)
    project_root = repo_root / "cmru"
    if not project_root.is_dir() or project_root.is_symlink():
        raise RuntimeError(f"CMRU project directory is missing or unsafe: {project_root}")
    if PROJECT_ROOT.resolve() != project_root.resolve():
        raise RuntimeError(
            f"release gate helper is not running from the selected worktree: {PROJECT_ROOT}"
        )
    _assert_no_untracked_gate_code(repo_root)
    _preflight_assay_source(repo_root)
    assay_git, baseline = _load_components()
    if assay_git.repo_top(project_root).resolve() != repo_root:
        raise RuntimeError("selected CMRU project does not belong to the requested worktree")
    temp_parent = temp_parent.resolve(strict=True)
    if temp_parent == repo_root or temp_parent.is_relative_to(repo_root):
        raise RuntimeError("private CMRU gate lock directory is inside the judged worktree")
    with _release_gate_lock(temp_parent), _gate_signal_handlers():
        _assert_no_untracked_gate_code(repo_root)
        from cmru import gate_snapshot

        snapshot = gate_snapshot.open_gate_snapshot(repo_root)
        state = "incomplete"
        try:
            gate_snapshot.plant_source_canary(snapshot)
            gate_snapshot.write_snapshot_manifest(snapshot, "running")
            try:
                result = _run_locked_gate(
                    repo_root,
                    project_root,
                    baseline,
                    assay_git,
                    snapshot,
                    postpone_mutation,
                )
            except (GateInterrupted, KeyboardInterrupt):
                state = "interrupted"
                raise
            except BaseException:
                state = "failed"
                raise
            state = "passed" if result == 0 else "failed"
            return result
        finally:
            try:
                try:
                    gate_snapshot.remove_source_canary(snapshot)
                finally:
                    gate_snapshot.write_snapshot_manifest(snapshot, state)
            finally:
                gate_snapshot.close_gate_snapshot(snapshot)


def _run_locked_gate(
    repo_root: Path,
    project_root: Path,
    baseline,
    assay_git,
    snapshot,
    postpone_mutation: str | None = None,
) -> int:
    _assert_no_untracked_gate_code(repo_root)
    auth = baseline._repository_git_auth(repo_root)
    lane_environment = _lane_environment()
    from cmru import gate_snapshot

    for lane in ("installed-wheel", "assay", "coverage"):
        result = _run_snapshot_lane(
            repo_root, lane, lane_environment, snapshot, gate_snapshot,
        )
        if result:
            return result

    if postpone_mutation is not None:
        # Provisional gate: ONLY the mutation lane (and its remote-facts
        # preparation) is skipped; the postponement is recorded as evidence.
        _record_postponement(project_root, postpone_mutation)
    else:
        # A full gate never leaves an earlier provisional marker behind.
        (project_root / POSTPONED_MARKER).unlink(missing_ok=True)
        _assert_no_untracked_gate_code(repo_root)
        gate_snapshot.assert_source_head(snapshot)
        with gate_snapshot.host_git_hooks_disabled(repo_root):
            facts = baseline.build_facts(
                repo_root,
                project_root,
                git_auth=auth,
                assay_git=assay_git,
            )
        facts_json = json.dumps(facts, separators=(",", ":"), sort_keys=True)
        if auth.token and auth.token in facts_json:
            raise RuntimeError("remote baseline facts unexpectedly contain publisher credentials")
        mutation_environment = dict(lane_environment)
        mutation_environment[FACTS_ENV_KEY] = facts_json
        result = _run_snapshot_lane(
            repo_root, "mutation", mutation_environment, snapshot, gate_snapshot,
        )
        if result:
            return result

    result = _run_snapshot_lane(
        repo_root, "canary", lane_environment, snapshot, gate_snapshot,
    )
    if result:
        return result
    gate_snapshot.assert_source_head(snapshot)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument(
        "--postpone-mutation",
        metavar="TRACKING-ID",
        type=_tracking_id,
        default=None,
        help=(
            "skip ONLY the mutation lane (PROVISIONAL gate) and record the "
            "postponement under TRACKING-ID in .assay/mutation-postponed-cmru.json"
        ),
    )
    args = parser.parse_args(argv)
    try:
        return run_release_gate(args.worktree, postpone_mutation=args.postpone_mutation)
    except Exception as exc:
        print(f"cmru-release-gate: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
