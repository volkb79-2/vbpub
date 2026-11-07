"""Direct-mode ``cgprofile run``: profile one command in the caller's own cgroup subtree.

Standard library only. The collector child (``cgprofile.cli.legacy _collect``) and
this driver meet in the run directory through sentinel files (a file survives the
driver being suspended mid-run, and the two may be in different PID namespaces).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import List, Optional

from . import phases, store
from .cli import legacy
from .ctlwire import WireFailure

START_TIMEOUT = 90.0
STOP_TIMEOUT = 30.0
SELF_TARGET = "self@follow"


@dataclass
class Outcome:
    run_dir: str
    exit_status: int
    duration_seconds: float


def _note(message: str) -> None:
    print(f"[cgprofile] {message}", file=sys.stderr, flush=True)


def collector_command(run_path: str) -> List[str]:
    return [sys.executable, "-m", "cgprofile.cli.legacy", "_collect",
            "--run-dir", run_path, "--target", SELF_TARGET]


def wait_for(path: str, timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(path):
            return
        time.sleep(0.05)
    raise WireFailure(f"timed out after {timeout:.0f}s waiting for {what}", exit_code=1, code="collector-timeout")


def _terminate(child: subprocess.Popen) -> None:
    child.terminate()
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        child.kill()


def start_collector(run_path: str, timeout: float = START_TIMEOUT) -> subprocess.Popen:
    child = subprocess.Popen(collector_command(run_path), stdout=sys.stderr, stderr=sys.stderr)
    try:
        wait_for(os.path.join(run_path, legacy.READY_FILE), timeout, "the collector to start")
    except WireFailure:
        _terminate(child)
        raise
    return child


def stop_collector(run_path: str, child: subprocess.Popen, timeout: float = STOP_TIMEOUT) -> None:
    open(os.path.join(run_path, legacy.STOP_FILE), "w").close()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(os.path.join(run_path, legacy.DONE_FILE)) or child.poll() is not None:
            break
        time.sleep(0.1)
    else:
        _note("collector did not stop cleanly: terminating")
        child.terminate()
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        child.kill()


def run_command(command: List[str], *, out_dir: str, phase: Optional[str], client_dir: str,
                run_id: Optional[str] = None) -> Outcome:
    """Run ``command`` under the collector; returns where the data is and how the command ended."""
    os.makedirs(out_dir, exist_ok=True)
    run = store.RunDir(out_dir, run_id or store.new_run_id(), create=True)
    child = start_collector(run.path)
    label = phase or os.path.basename(command[0])
    phases.emit_mark(run.path, label, kind="phase", meta={"argv": command}, source="wrapper")
    env = dict(os.environ)
    env["CGPROFILE_RUN_DIR"] = run.path
    env["PATH"] = client_dir + os.pathsep + env.get("PATH", "")
    started = time.time()
    try:
        result = subprocess.run(command, env=env, check=False)
    except OSError as exc:
        stop_collector(run.path, child)
        raise WireFailure(f"failed to run {command[0]!r}: {exc}", exit_code=1, code="run-failed") from exc
    elapsed = time.time() - started
    phases.emit_mark(run.path, f"{label}:done", kind="event",
                     meta={"exit_code": result.returncode, "elapsed_s": round(elapsed, 3)}, source="wrapper")
    stop_collector(run.path, child)
    return Outcome(run.path, result.returncode, round(elapsed, 3))
