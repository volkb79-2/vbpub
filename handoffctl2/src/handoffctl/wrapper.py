"""Per-attempt wrapper: the supervision boundary. PACKAGE P04.

The wrapper is a DETACHED process (survives daemon restarts) that runs one
CLI leg and turns its lifecycle into artifacts the reconciler can read:
log file, receipt.json, and ATTEMPT_* events. Its kernel-guaranteed exit
path (including flock auto-release on death) IS the crash-safety story.

INTERFACE CONTRACT (frozen):

- WrapperSpec is serialized to <attempt_dir>/spec.json by the daemon before
  launch; `python -m handoffctl.wrapper <spec.json>` is the entry.
- launch_detached(spec) -> wrapper pid:
  double-fork + os.setsid so the wrapper leads its own session/pgroup and
  is reparented to init; the intermediate child writes the final pid to
  <attempt_dir>/wrapper.pid before exiting; parent waits for that file
  (timeout 10s) and returns the pid. stdout/stderr of the wrapper itself
  go to <attempt_dir>/wrapper.log.
- wrapper_main(spec_path) sequence:
    1. Load spec; load statefile; find attempt (by attempt_id) — it exists
       (daemon created it via ATTEMPT_CREATED) in state CREATED/PREFLIGHTING.
    2. Acquire every lease in spec.leases (leases.acquire, non-blocking).
       Any unavailable -> release the ones taken, write receipt
       {result: 'error', exit_code: 75, blocked_reason: 'lease-lost-race'},
       append ATTEMPT_FAILED (attempt.state=FAILED, receipt merged), exit 75.
       On success append LEASE_ACQUIRED per lease (task-scoped).
    3. Append ATTEMPT_STARTED: attempt.state=RUNNING, pid/pgid of the CLI
       child (see 4), log_path set.
    4. Spawn the CLI: subprocess.Popen(spec.argv, cwd=spec.cwd, env=merged
       env, stdout=log fd (append, line-buffered), stderr=STDOUT,
       start_new_session=True). Write child pid to <attempt_dir>/child.pid.
    5. After launch, try adapters.capture_session(route via spec.route_def
       raw dict -> RouteDef, log_path=spec.log_path) once after a 5s delay;
       on success append ATTEMPT_STARTED again with session_handle merged
       (upsert semantics make the re-emit safe) — the resume handle is
       captured EARLY (v2 §5.2), not at exit. (P17 2026-07-15: passing
       spec.log_path lets capture_session read a claude route's stream-json
       first line directly for the CURRENT run's actual log file — correct
       on both first dispatch and resume, where the log path differs.)
    6. Install SIGTERM/SIGINT handler: forward SIGTERM to the child's
       process group, wait up to spec.term_grace_seconds (default 30), then
       SIGKILL the group; classify as interrupted.
    7. Wait for exit. Classification precedence:
       interrupted-by-signal -> receipt {result 'error', exit_code, blocked_
       reason 'interrupted'} + ATTEMPT_INTERRUPTED (state INTERRUPTED);
       else adapters.classify_log_tail(last 200 log lines): 'blocked' ->
       receipt result 'blocked' with blocked_reason = first BLOCKED: line;
       'limit' -> result 'limit'; else exit 0 -> 'done', nonzero -> 'error'.
    8. Usage: adapters.extract_usage(route, attempt_dir, full log text),
       then config.Prices.load().price_tokens(route.model, usage).
    9. Write receipt.json ATOMICALLY (tmp+rename) to spec.receipt_path;
       append ATTEMPT_EXITED (state EXITED, receipt+usage+ended merged) —
       or ATTEMPT_INTERRUPTED/ATTEMPT_FAILED per classification (FAILED
       only for the lease-race and spawn-failure paths; a CLI that ran and
       exited nonzero is EXITED with receipt.result 'error': the RESULT
       carries the failure, the STATE says 'process completed').
   10. Append LEASE_RELEASED per lease, release flocks, exit with the
       child's exit code.
- Oracles: the wrapper does NOT run gates (the reviewer/gate adapter does);
  receipt.oracles stays [] at this layer — receipt.result reflects process
  outcome plus log classification only.
  (P14 2026-07-15: v2 §5.4 exempts an attempt from tier-2 stall-confirmation
  while it is inside a declared long gate run, approximated via a
  gate-running marker file the wrapper would touch around gate execution.
  Since gates are not wired into the wrapper at all yet (this bullet), that
  exemption is intentionally NOT implemented here — see daemon.py's
  _confirm_stall docstring for the equivalent decision on the reconciler
  side. Add the marker file once gate execution actually lands here.)
- Events: use storage.append_and_apply with actor Actor(WRAPPER,
  f'wrapper-{attempt_id}'); statefile loaded fresh via storage.load_state
  right before each event (the daemon may have written between steps).
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import adapters, leases, paths, storage
from .config import RouteDef
from .types import (
    Actor, ActorKind, AttemptState, EventType, Receipt, ReceiptResult,
    utc_now,
)

SESSION_CAPTURE_DELAY = 5.0


@dataclass
class WrapperSpec:
    project: str
    task_id: str
    attempt_id: str
    argv: list[str]
    cwd: str
    log_path: str
    receipt_path: str
    attempt_dir: str
    route_def: dict[str, Any]           # RouteDef fields, reconstructable
    leases: list[dict[str, Any]] = field(default_factory=list)  # {name, capacity}
    env_overrides: dict[str, str] = field(default_factory=dict)
    term_grace_seconds: int = 30

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "WrapperSpec":
        return cls(**d)


def launch_detached(spec: WrapperSpec) -> int:
    """Write spec.json, double-fork the wrapper, return its pid (see contract)."""
    spec_dir = Path(spec.attempt_dir)
    spec_dir.mkdir(parents=True, exist_ok=True)
    spec_path = spec_dir / "spec.json"
    spec_path.write_text(json.dumps(spec.to_dict()), encoding="utf-8")

    # Double-fork pattern for detachment
    pid = os.fork()
    if pid == 0:
        # First child
        os.setsid()  # Become session leader
        pid2 = os.fork()
        if pid2 == 0:
            # Second child (grandchild) - this becomes the wrapper.
            # The wrapper's OWN stdout/stderr go to <attempt_dir>/wrapper.log
            # (the CLI child's output goes to spec.log_path separately).
            wlog = Path(spec.attempt_dir) / "wrapper.log"
            wlog.parent.mkdir(parents=True, exist_ok=True)
            with wlog.open("ab") as log_fd:
                sys.stdout.flush()
                sys.stderr.flush()
                os.dup2(log_fd.fileno(), 1)
                os.dup2(log_fd.fileno(), 2)
                try:
                    rc = wrapper_main(str(spec_path))
                except BaseException:
                    import traceback
                    traceback.print_exc()
                    rc = 70
                os._exit(rc)
        else:
            # First child writes the grandchild pid and exits
            pid_file = Path(spec.attempt_dir) / "wrapper.pid"
            pid_file.write_text(str(pid2), encoding="utf-8")
            os._exit(0)
    else:
        # Parent waits for the pid file to appear
        pid_file = Path(spec.attempt_dir) / "wrapper.pid"
        start = time.monotonic()
        while time.monotonic() - start < 10:
            if pid_file.exists():
                wrapper_pid = int(pid_file.read_text(encoding="utf-8").strip())
                os.waitpid(pid, 0)  # Reap the intermediate child
                return wrapper_pid
            time.sleep(0.05)
        # Timeout
        os.waitpid(pid, 0)
        raise TimeoutError(f"wrapper.pid not created within 10s")


def wrapper_main(spec_path: str) -> int:
    """The wrapper process body (see contract). Returns process exit code."""
    spec_json = Path(spec_path).read_text(encoding="utf-8")
    spec = WrapperSpec.from_dict(json.loads(spec_json))

    # Step 1: Load spec, statefile, find attempt
    paths.ensure_layout(spec.project)
    state = storage.load_state(spec.project, spec.task_id)
    if state is None:
        return 1
    attempt = state.attempt_by_id(spec.attempt_id)
    if attempt is None:
        return 1

    held_leases: list[leases.Lease] = []

    try:
        # Step 2: Acquire leases
        for lease_spec in spec.leases:
            lease = leases.acquire(
                lease_spec["name"],
                owner=f"attempt-{spec.attempt_id}",
                purpose="wrapper",
                capacity=lease_spec.get("capacity", 1),
            )
            if lease is None:
                # Release acquired leases
                for held in held_leases:
                    held.release()
                # Write receipt
                receipt = Receipt(
                    result=ReceiptResult.ERROR,
                    exit_code=75,
                    blocked_reason="lease-lost-race",
                )
                Path(spec.receipt_path).parent.mkdir(parents=True, exist_ok=True)
                Path(spec.receipt_path).write_text(
                    json.dumps(receipt.to_dict()), encoding="utf-8"
                )
                # Append ATTEMPT_FAILED
                state = storage.load_state(spec.project, spec.task_id)
                attempt = state.attempt_by_id(spec.attempt_id)
                attempt.state = AttemptState.FAILED
                attempt.receipt = receipt
                storage.append_and_apply(
                    spec.project,
                    {spec.task_id: state},
                    actor=Actor(ActorKind.WRAPPER, f"wrapper-{spec.attempt_id}"),
                    type=EventType.ATTEMPT_FAILED,
                    payload={"attempt": attempt.to_dict()},
                    task_id=spec.task_id,
                    attempt_id=spec.attempt_id,
                )
                return 75
            held_leases.append(lease)

        # Append LEASE_ACQUIRED for each lease
        for lease_spec in spec.leases:
            state = storage.load_state(spec.project, spec.task_id)
            storage.append_and_apply(
                spec.project,
                {spec.task_id: state},
                actor=Actor(ActorKind.WRAPPER, f"wrapper-{spec.attempt_id}"),
                type=EventType.LEASE_ACQUIRED,
                payload={"lease": lease_spec["name"]},
                task_id=spec.task_id,
                attempt_id=spec.attempt_id,
            )

        # Step 3: Append ATTEMPT_STARTED
        state = storage.load_state(spec.project, spec.task_id)
        attempt = state.attempt_by_id(spec.attempt_id)

        # Step 6 (installed early, before the spawn and the session-capture
        # delay): a SIGTERM arriving at any point after the child exists must
        # be forwarded to the child's process group and classified as
        # interrupted. Installing after the 5s capture delay would leave a
        # window where SIGTERM kills the wrapper with the default action
        # (no receipt) — the detached signal tests exercise exactly that.
        child = None
        interrupted = False

        def sigterm_handler(signum, frame):
            nonlocal interrupted
            interrupted = True
            if child is not None:
                try:
                    os.killpg(os.getpgid(child.pid), signal.SIGTERM)
                except OSError:
                    pass

        old_sigterm = signal.signal(signal.SIGTERM, sigterm_handler)
        old_sigint = signal.signal(signal.SIGINT, sigterm_handler)

        try:
            # Step 4: Spawn CLI
            log_path = Path(spec.log_path)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            env = os.environ.copy()
            env.update(spec.env_overrides)

            with log_path.open("ab") as log_fd:
                child = subprocess.Popen(
                    spec.argv,
                    cwd=spec.cwd,
                    env=env,
                    stdout=log_fd,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )

                # Write child.pid
                Path(spec.attempt_dir, "child.pid").write_text(
                    str(child.pid), encoding="utf-8"
                )

                # Update attempt with pid/pgid and log_path
                attempt.pid = child.pid
                attempt.pgid = os.getpgid(child.pid)
                attempt.log_path = spec.log_path
                attempt.state = AttemptState.RUNNING
                state = storage.load_state(spec.project, spec.task_id)
                state.attempts = [
                    a if a.attempt_id != attempt.attempt_id else attempt
                    for a in state.attempts
                ]
                storage.append_and_apply(
                    spec.project,
                    {spec.task_id: state},
                    actor=Actor(ActorKind.WRAPPER, f"wrapper-{spec.attempt_id}"),
                    type=EventType.ATTEMPT_STARTED,
                    payload={"attempt": attempt.to_dict()},
                    task_id=spec.task_id,
                    attempt_id=spec.attempt_id,
                )

            # If the signal landed between spawn and now, forward it (the
            # handler saw child=None and could not).
            if interrupted:
                try:
                    os.killpg(os.getpgid(child.pid), signal.SIGTERM)
                except OSError:
                    pass

            # Step 5: Capture session after delay (interruptible)
            capture_deadline = time.monotonic() + SESSION_CAPTURE_DELAY
            while not interrupted and time.monotonic() < capture_deadline:
                time.sleep(0.05)
            if not interrupted:
                try:
                    route_def = RouteDef(**spec.route_def)
                    session_handle = adapters.capture_session(
                        route_def,
                        attempt_dir=Path(spec.attempt_dir),
                        worktree=spec.cwd,
                        launched_at=datetime.fromisoformat(attempt.started.isoformat()),
                        log_path=spec.log_path,
                    )
                    if session_handle:
                        state = storage.load_state(spec.project, spec.task_id)
                        attempt = state.attempt_by_id(spec.attempt_id)
                        attempt.session_handle = session_handle
                        state.attempts = [
                            a if a.attempt_id != attempt.attempt_id else attempt
                            for a in state.attempts
                        ]
                        storage.append_and_apply(
                            spec.project,
                            {spec.task_id: state},
                            actor=Actor(ActorKind.WRAPPER, f"wrapper-{spec.attempt_id}"),
                            type=EventType.ATTEMPT_STARTED,
                            payload={"attempt": attempt.to_dict()},
                            task_id=spec.task_id,
                            attempt_id=spec.attempt_id,
                        )
                except Exception:
                    pass  # Non-critical
            # Wait for child, handling interruption with grace period
            grace_end = None
            child_exit_code = -1

            while True:
                try:
                    _, status = os.waitpid(child.pid, os.WNOHANG)
                    if _ != 0:
                        # Child exited
                        child_exit_code = os.WEXITSTATUS(status) if os.WIFEXITED(status) else -1
                        break
                except OSError:
                    # Child already reaped or doesn't exist
                    child_exit_code = -1
                    break

                # If interrupted by signal and grace period set, check if we need to SIGKILL
                if interrupted and grace_end is None:
                    grace_end = time.monotonic() + spec.term_grace_seconds

                if interrupted and grace_end is not None and time.monotonic() > grace_end:
                    # Grace period expired, send SIGKILL
                    try:
                        os.killpg(os.getpgid(child.pid), signal.SIGKILL)
                    except OSError:
                        pass
                    # Continue waiting for child to be reaped
                    grace_end = time.monotonic() + 5  # Extended grace for SIGKILL

                time.sleep(0.05)
        finally:
            signal.signal(signal.SIGTERM, old_sigterm)
            signal.signal(signal.SIGINT, old_sigint)

        # Step 7: Classify result
        log_text = log_path.read_text(encoding="utf-8")
        log_tail = "\n".join(log_text.split("\n")[-200:])

        if interrupted:
            result = ReceiptResult.ERROR
            blocked_reason = "interrupted"
            event_type = EventType.ATTEMPT_INTERRUPTED
            attempt_state = AttemptState.INTERRUPTED
        else:
            classification = adapters.classify_log_tail(log_tail)
            if classification == "blocked":
                result = ReceiptResult.BLOCKED
                blocked_match = re.search(r"^BLOCKED: (.+)$", log_tail, re.MULTILINE)
                blocked_reason = blocked_match.group(1) if blocked_match else "unknown"
                event_type = EventType.ATTEMPT_EXITED
                attempt_state = AttemptState.EXITED
            elif classification == "limit":
                result = ReceiptResult.LIMIT
                blocked_reason = None
                event_type = EventType.ATTEMPT_EXITED
                attempt_state = AttemptState.EXITED
            elif child_exit_code == 0:
                result = ReceiptResult.DONE
                blocked_reason = None
                event_type = EventType.ATTEMPT_EXITED
                attempt_state = AttemptState.EXITED
            else:
                result = ReceiptResult.ERROR
                blocked_reason = None
                event_type = EventType.ATTEMPT_EXITED
                attempt_state = AttemptState.EXITED

        # Step 8: Extract usage
        route_def = RouteDef(**spec.route_def)
        usage = adapters.extract_usage(route_def, Path(spec.attempt_dir), log_text)
        from .config import Prices
        prices = Prices.load()
        usage = prices.price_tokens(route_def.model, usage)

        # Step 9: Write receipt and append event
        receipt = Receipt(
            result=result,
            exit_code=child_exit_code,
            blocked_reason=blocked_reason,
        )

        # Atomic write
        receipt_path = Path(spec.receipt_path)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = receipt_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(receipt.to_dict()), encoding="utf-8")
        os.replace(tmp_path, receipt_path)

        # Append event
        state = storage.load_state(spec.project, spec.task_id)
        attempt = state.attempt_by_id(spec.attempt_id)
        attempt.state = attempt_state
        attempt.ended = utc_now()
        attempt.receipt = receipt
        attempt.usage = usage
        storage.append_and_apply(
            spec.project,
            {spec.task_id: state},
            actor=Actor(ActorKind.WRAPPER, f"wrapper-{spec.attempt_id}"),
            type=event_type,
            payload={"attempt": attempt.to_dict()},
            task_id=spec.task_id,
            attempt_id=spec.attempt_id,
        )

        # Step 10: Release leases
        for lease_spec in spec.leases:
            state = storage.load_state(spec.project, spec.task_id)
            storage.append_and_apply(
                spec.project,
                {spec.task_id: state},
                actor=Actor(ActorKind.WRAPPER, f"wrapper-{spec.attempt_id}"),
                type=EventType.LEASE_RELEASED,
                payload={"lease": lease_spec["name"]},
                task_id=spec.task_id,
                attempt_id=spec.attempt_id,
            )

        for lease in held_leases:
            lease.release()

        return child_exit_code

    except Exception:
        # On crash, release leases
        for lease in held_leases:
            lease.release()
        raise


if __name__ == "__main__":  # pragma: no cover
    sys.exit(wrapper_main(sys.argv[1]))
