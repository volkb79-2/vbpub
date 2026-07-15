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
       raw dict -> RouteDef) once after a 5s delay; on success append
       ATTEMPT_STARTED again with session_handle merged (upsert semantics
       make the re-emit safe) — the resume handle is captured EARLY (v2
       §5.2), not at exit.
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
- Events: use storage.append_and_apply with actor Actor(WRAPPER,
  f'wrapper-{attempt_id}'); statefile loaded fresh via storage.load_state
  right before each event (the daemon may have written between steps).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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
        from dataclasses import asdict
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "WrapperSpec":
        return cls(**d)


def launch_detached(spec: WrapperSpec) -> int:
    """Write spec.json, double-fork the wrapper, return its pid (see contract)."""
    raise NotImplementedError


def wrapper_main(spec_path: str) -> int:
    """The wrapper process body (see contract). Returns process exit code."""
    raise NotImplementedError


if __name__ == "__main__":  # pragma: no cover
    sys.exit(wrapper_main(sys.argv[1]))
