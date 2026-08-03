"""Per-attempt wrapper: the supervision boundary. PACKAGE P04.

The wrapper is a DETACHED process (survives daemon restarts) that runs one
CLI leg and turns its lifecycle into artifacts the reconciler can read:
log file, receipt.json, and ATTEMPT_* events. Its kernel-guaranteed exit
path (including flock auto-release on death) IS the crash-safety story.

INTERFACE CONTRACT (frozen):

- WrapperSpec is serialized to <attempt_dir>/spec.json by the daemon before
  launch; `python -m nyxloom.wrapper <spec.json>` is the entry.
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
    4. Establish containment (CR-13a, D-R7), then spawn. If the pinned route
       requires containment (containment.requires_containment -- true unless
       the route declares trust="operator", and unconditionally true for a
       free endpoint), build a ContainmentPlan from spec.repo_root/spec.cwd
       and wrap spec.argv in `docker run`. ANY failure to establish it --
       no image configured, no repository declared, an unmapped host path, a
       docker daemon that does not answer, an image that was never built --
       writes receipt {result:'error', exit_code:76, blocked_reason:
       'containment-unavailable:<why>'}, appends ATTEMPT_FAILED, logs ERROR
       and exits 76 WITHOUT spawning anything. There is no uncontained
       fallback; a downgrade is the one outcome containment cannot have.
       Then subprocess.Popen(argv, cwd=spec.cwd,
       env=containment.child_env(route, overrides) -- an ALLOWLIST of
       BASE_ENV_ALLOW plus the route's DECLARED secrets plus this attempt's
       overrides, never the daemon's environment,
       stdout=log fd (append, line-buffered), stderr=STDOUT,
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
       SIGKILL the group; classify as interrupted. For a CONTAINED leg the
       child is the docker CLIENT, which forwards SIGTERM to the container
       but whose death does NOT stop it -- so the SIGKILL path also issues
       `docker rm -f` on the container name, or the kill would leave a live
       unsupervised agent behind.
    7. Wait for exit. Classification precedence:
       interrupted-by-signal -> receipt {result 'error', exit_code, blocked_
       reason 'interrupted'} + ATTEMPT_INTERRUPTED (state INTERRUPTED);
       else adapters.classify_log_tail(last 200 log lines): 'blocked' ->
       receipt result 'blocked' with blocked_reason = first BLOCKED: line;
       'scope_amendment' (B21 2026-07-23, D-R16 §3) -> receipt result
       'scope_amendment' with amendment_request = the {"file", "reason"} dict
       parsed from the first SCOPE_AMENDMENT_REQUEST: line (None if the line
       failed to parse as JSON) -- daemon.py's EmitAttemptExit routes this to
       a bounded approve-and-requeue, never a dead end;
       'transient' (B24 2026-07-23, D-R17) -> receipt result 'transient' but
       -- UNLIKE every other classification -- event_type/attempt_state are
       the SAME pair the interrupted-by-signal branch above uses
       (ATTEMPT_INTERRUPTED / INTERRUPTED), NOT ATTEMPT_EXITED/EXITED: a
       provider throttle (502/429/ResourceExhausted/idle-timeout/worker
       request-limit) means the LEG was cut off, not that the attempt
       reached a genuine dead end, and EXITED can never be revived
       (TERMINAL_ATTEMPT_STATES). daemon.py's EXISTING ResumeAttempt path
       (built for INTERRUPTED attempts) then retries the SAME session,
       gated by an exponential backoff and bounded by MAX_TRANSIENT_RESUMES;
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

from . import adapters, containment, leases, paths, results, storage
from .config import RouteDef
from .log import get_logger
from .types import (
    Actor, ActorKind, AttemptState, EventType, Receipt, ReceiptResult,
    utc_now,
)

log = get_logger("wrapper")  # P05a (docs/plan-logging.md §5)

SESSION_CAPTURE_DELAY = 5.0

#: Exit code for a leg that never started because containment could not be
#: established. Distinct from 75 (lease-lost-race) so an operator reading a
#: receipt can tell "someone else held the lease" -- normal, self-healing --
#: from "this deployment cannot contain an agent" -- which needs a human and
#: will not heal on its own.
CONTAINMENT_EXIT = 76

# 2026-08-03 (CR-13a, D-R7): the RISK-006 daemon-only-secret DENYLIST that
# stood here is GONE, replaced by containment.child_env's allowlist. (Its
# constant name is deliberately not repeated in this module: product_truth's
# `containment` fact reads this file for it, and a mention in prose is
# indistinguishable from the thing itself to a reader who greps -- the same
# rule actual_state_backend applies to the store selector.)
# The denylist named three secrets an agent must not receive; it could not
# name the fourth,
# because a denylist only ever excludes what someone already thought of, and
# the daemon's environment grows with the deployment. What a route receives is
# now exactly what it DECLARES (RouteDef.secrets) plus the non-authority
# operational names in containment.BASE_ENV_ALLOW plus this attempt's
# overrides -- and for a contained leg, only the declared names cross the
# container boundary at all.


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
    #: CR-13a (D-R7): the repository whose worktree this leg runs in -- the
    #: ONLY host path a contained leg is given. Empty is not "the default";
    #: it is "no launch site declared one", and a route that requires
    #: containment refuses to launch on it (containment.plan_for). The
    #: requirement itself is NOT a field: it is derived from the pinned
    #: `route_def`, so no launch site can omit containment by forgetting it.
    repo_root: str = ""
    leases: list[dict[str, Any]] = field(default_factory=list)  # {name, capacity}
    env_overrides: dict[str, str] = field(default_factory=dict)
    term_grace_seconds: int = 30
    # CR-03 (DR-13): which typed result this leg produces, if any. Empty means
    # "this leg has no agent judgement to collect" (a carve or a diagnosis
    # today), and the wrapper writes no result document -- rather than writing
    # an empty one, which a reader could not tell from a leg that answered
    # nothing.
    result_kind: str = ""
    # The tasks this leg judges. Empty means "just task_id". A wave review
    # judges several tasks in ONE attempt, so the wrapper has to be told which
    # -- it cannot derive the membership, and guessing from the worktree would
    # be the same "find the files that look like reviews" scan CR-03 removes.
    result_tasks: list[str] = field(default_factory=list)

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


def _head_commit(cwd: str) -> str:
    """The worktree's current HEAD, or "" when it cannot be read.

    Mechanically knowable, so the wrapper stamps it rather than asking the
    agent -- an agent that reported the wrong commit would bind a judgement to
    a tree it did not review. Empty on failure: an absent expectation is not
    checked (results._require_identity), so a repository the wrapper cannot
    read degrades to "no revision claim" rather than to a false one.
    """
    try:
        res = subprocess.run(["git", "-C", cwd, "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=15)
    except Exception:  # census: process-boundary translation (CR-03)
        return ""
    return res.stdout.strip() if res.returncode == 0 else ""


def _write_typed_results(spec: WrapperSpec, log_text: str) -> None:
    """Assemble one typed result per judged task, atomically.

    The wrapper is the only party that knows BOTH the agent's conclusion and
    the facts it must be bound to, which is why the assembly lives here rather
    than in the daemon (which would have to trust the agent's copy of the
    facts) or in the agent (which cannot hash its own log).

    A leg that produced no readable judgement still gets a result: a FAILED
    one naming why. Recording the absence as a typed record rather than as a
    missing file is what lets the daemon tell "the reviewer never answered"
    from "the reviewer said no" -- those route differently, and an absence
    cannot distinguish them.
    """
    if not spec.result_kind:
        return
    try:
        kind = results.ResultKind(spec.result_kind)
    except ValueError:
        log.error("result-kind-unknown", attempt=spec.attempt_id, kind=spec.result_kind)
        return

    evidence_bytes = log_text.encode("utf-8", errors="replace")
    evidence_name = Path(spec.log_path).name
    head = _head_commit(spec.cwd)
    attempt_dir = Path(spec.attempt_dir)

    for task_id in (spec.result_tasks or [spec.task_id]):
        judgement_path = Path(spec.cwd) / results.judgement_filename(task_id)
        try:
            raw = judgement_path.read_bytes()
            judgement = results.load_judgement(
                raw, task_id=task_id, attempt_id=spec.attempt_id, kind=kind)
            record = results.bind_evidence(
                judgement, evidence_path=evidence_name,
                evidence_bytes=evidence_bytes, head_commit=head)
        except OSError:
            record = results.failed_result(
                kind, task_id=task_id, attempt_id=spec.attempt_id,
                evidence_path=evidence_name, evidence_bytes=evidence_bytes,
                detail=f"no judgement at {results.judgement_filename(task_id)}",
                head_commit=head)
        except results.ResultRejected as exc:
            record = results.failed_result(
                kind, task_id=task_id, attempt_id=spec.attempt_id,
                evidence_path=evidence_name, evidence_bytes=evidence_bytes,
                detail=f"judgement refused: {exc.code.value}: {exc.detail}",
                head_commit=head)
            log.warning("judgement-refused", attempt=spec.attempt_id, task=task_id,
                        code=exc.code.value, detail=exc.detail[:200])

        out = attempt_dir / results.result_filename(task_id)
        tmp = out.with_suffix(".tmp")
        tmp.write_text(record.to_json(), encoding="utf-8")
        os.replace(tmp, out)


def _refuse_launch(spec: WrapperSpec, *, blocked_reason: str,
                   exit_code: int) -> int:
    """Record a leg that never started, and return its exit code.

    ONE shape for both pre-launch refusals -- the lease race and a
    containment failure -- because a reader must be able to tell them apart by
    ``blocked_reason`` rather than by which of two near-identical code paths
    happened to write the receipt. Both are attempt state FAILED (nothing ran)
    rather than EXITED (a process completed), which is the same distinction
    step 9 of the contract draws.
    """
    receipt = Receipt(result=ReceiptResult.ERROR, exit_code=exit_code,
                      blocked_reason=blocked_reason)
    Path(spec.receipt_path).parent.mkdir(parents=True, exist_ok=True)
    Path(spec.receipt_path).write_text(
        json.dumps(receipt.to_dict()), encoding="utf-8")
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
    return exit_code


def contained_launch(spec: WrapperSpec, route: RouteDef,
                     env_overrides: dict[str, str]) -> tuple[list[str], str]:
    """``(argv, container_name)`` for this leg. Raises rather than downgrades.

    Returns ``spec.argv`` unchanged and an empty name for a route that runs
    uncontained (``trust = "operator"``, never a free endpoint). Otherwise
    builds the plan, verifies the runtime can actually start it NOW, and wraps
    the argv -- raising :class:`containment.ContainmentUnavailable` if any of
    that fails.

    The probe is here and not only at the effect boundary because the two are
    separated by a process launch and by however long the daemon's pass took:
    an image pruned in between must stop THIS launch, not the previous
    admission check.
    """
    if not containment.requires_containment(route):
        return list(spec.argv), ""
    plan = containment.plan_for(route, worktree=spec.cwd,
                                repo_root=spec.repo_root,
                                overrides=env_overrides)
    reason = containment.probe(plan.image)
    if reason:
        raise containment.ContainmentUnavailable(reason)
    name = containment.container_name(spec.attempt_id)
    return plan.docker_argv(spec.argv, name), name


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

    # CR-03 (DR-13): the child is TOLD its identity rather than asked to
    # transcribe it out of the prompt. An agent that mistypes an attempt id
    # produces a judgement the reader must refuse, which reads to an operator
    # as a broken reviewer rather than as a typo -- and a cheap model copying
    # a hex string is exactly the kind of mechanical burden that produces
    # those.
    env_overrides = {
        **spec.env_overrides,
        "NYXLOOM_TASK_ID": spec.task_id,
        "NYXLOOM_ATTEMPT_ID": spec.attempt_id,
    }

    # Step 4a (CR-13a, D-R7): establish containment BEFORE acquiring any
    # lease. A leg that cannot be contained will not run, so it must not
    # first take a mutex other work is waiting on -- and refusing here means
    # there is nothing to unwind.
    route_def = RouteDef(**spec.route_def)
    try:
        argv, container = contained_launch(spec, route_def, env_overrides)
    except containment.ContainmentUnavailable as exc:
        # P05a (§5): ERROR -- this attempt failed, and unlike a lease race it
        # will fail identically next pass until an operator changes the
        # deployment. The reason travels in the receipt, not only in this log.
        log.error("containment-unavailable", project=spec.project,
                  task=spec.task_id, attempt=spec.attempt_id,
                  route=route_def.route_id, reason=exc.reason)
        return _refuse_launch(
            spec, blocked_reason=f"containment-unavailable:{exc.reason}",
            exit_code=CONTAINMENT_EXIT)

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
                # P05a (§5): "attempt errored" is a named ERROR example --
                # a lease-race loss fails this whole attempt (state FAILED).
                log.error("lease-lost-race", project=spec.project, task=spec.task_id,
                          attempt=spec.attempt_id, lease=lease_spec["name"])
                return _refuse_launch(spec, blocked_reason="lease-lost-race",
                                      exit_code=75)
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
            # Step 4: Spawn (the CLI, or the docker client running it)
            log_path = Path(spec.log_path)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            # CR-13a: an ALLOWLIST built from the route's own declaration.
            # For a contained leg this is the DOCKER CLIENT's environment;
            # only the names in the plan's `-e` list cross into the container,
            # and their values are read by docker from here rather than
            # written into an argv.
            env = containment.child_env(route_def, env_overrides)

            with log_path.open("ab") as log_fd:
                child = subprocess.Popen(
                    argv,
                    cwd=spec.cwd,
                    env=env,
                    stdout=log_fd,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                # P05a (§5): subprocess spawn -> DEBUG.
                log.debug("spawn", project=spec.project, task=spec.task_id,
                          attempt=spec.attempt_id, pid=child.pid, cwd=spec.cwd)

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
                    # P05a (§5): a provider call -> DEBUG.
                    log.debug("session-capture-attempt", project=spec.project, task=spec.task_id,
                              attempt=spec.attempt_id, route=route_def.route_id)
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
                except Exception:  # census: advisory-degradation (CR-13a)
                    # A resume handle that could not be captured only ever
                    # SUBTRACTS: the leg still runs, and the daemon cold-starts
                    # instead of resuming. It can never make a launch happen.
                    pass
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
                    # CR-13a: for a contained leg the process just killed was
                    # the docker CLIENT. The container it started outlives it,
                    # so without this the SIGKILL leaves an agent running with
                    # nothing supervising it -- strictly worse than the
                    # uncontained leg this replaced.
                    if container:
                        containment.force_remove(container)
                    # Continue waiting for child to be reaped
                    grace_end = time.monotonic() + 5  # Extended grace for SIGKILL

                time.sleep(0.05)
        finally:
            signal.signal(signal.SIGTERM, old_sigterm)
            signal.signal(signal.SIGINT, old_sigint)

        # Step 7: Classify result
        log_text = log_path.read_text(encoding="utf-8")
        log_tail = "\n".join(log_text.split("\n")[-200:])

        amendment_request: dict | None = None
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
            elif classification == "scope_amendment":
                # B21 2026-07-23 (D-R16 §3): translate the marker into the
                # receipt's amendment_request carrier. A malformed/missing
                # JSON payload degrades to None -- daemon.py's EmitAttemptExit
                # still routes on receipt.result (SCOPE_AMENDMENT), it just
                # has no file/reason to report; it does not crash or
                # misclassify as BLOCKED.
                result = ReceiptResult.SCOPE_AMENDMENT
                blocked_reason = None
                amendment_match = re.search(
                    r"^SCOPE_AMENDMENT_REQUEST:\s*(\{.*\})\s*$", log_tail, re.MULTILINE)
                if amendment_match:
                    try:
                        amendment_request = json.loads(amendment_match.group(1))
                    except json.JSONDecodeError:
                        amendment_request = None
                event_type = EventType.ATTEMPT_EXITED
                attempt_state = AttemptState.EXITED
            elif classification == "transient":
                # B24 2026-07-23 (D-R17, D-B24-1 -- THE critical seam): a
                # provider throttle/outage, NOT a genuine dead end. Pair
                # with ATTEMPT_INTERRUPTED/INTERRUPTED (exactly the
                # interrupted-by-signal branch's pair above), never
                # ATTEMPT_EXITED/EXITED -- an EXITED attempt can never be
                # revived (TERMINAL_ATTEMPT_STATES, ATTEMPT_TRANSITIONS
                # [EXITED] == frozenset()), which would silently strand a
                # merely-throttled leg. RUNNING->INTERRUPTED is a legal edge
                # and INTERRUPTED->RUNNING (the daemon's ResumeAttempt path)
                # is explicitly whitelisted as not-a-regression, so the
                # EXISTING resume machinery picks this up unmodified.
                result = ReceiptResult.TRANSIENT
                blocked_reason = None
                event_type = EventType.ATTEMPT_INTERRUPTED
                attempt_state = AttemptState.INTERRUPTED
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

        # P05a (§5): a failure -> WARNING/ERROR; a clean exit -> DEBUG (the
        # daemon's own ATTEMPT_EXITED/-INTERRUPTED events already carry this
        # fact into the event log -- this is the wrapper's own diagnostic
        # narrative, not a duplicate record of domain state). INTERRUPTED is
        # a handled, operator/supervisor-triggered stop (degraded, not a
        # crash) -> WARNING; a genuine process error -> ERROR (§5's "attempt
        # errored"); LIMIT/BLOCKED/SCOPE_AMENDMENT (B21 2026-07-23, D-R16 §3
        # -- a bounded, never-a-dead-end escalation, same soft-outcome shape
        # as LIMIT/BLOCKED) are soft, handled outcomes -> WARNING.
        if attempt_state is AttemptState.INTERRUPTED:
            log.warning("attempt-exit", project=spec.project, task=spec.task_id,
                        attempt=spec.attempt_id, result=result.value, exit_code=child_exit_code)
        elif result is ReceiptResult.DONE:
            log.debug("attempt-exit", project=spec.project, task=spec.task_id,
                      attempt=spec.attempt_id, result=result.value, exit_code=child_exit_code)
        elif result in (ReceiptResult.LIMIT, ReceiptResult.BLOCKED, ReceiptResult.SCOPE_AMENDMENT):
            log.warning("attempt-exit", project=spec.project, task=spec.task_id,
                        attempt=spec.attempt_id, result=result.value, exit_code=child_exit_code,
                        blocked_reason=blocked_reason)
        else:
            log.error("attempt-exit", project=spec.project, task=spec.task_id,
                      attempt=spec.attempt_id, result=result.value, exit_code=child_exit_code)

        # Step 8: Extract usage
        usage = adapters.extract_usage(route_def, Path(spec.attempt_dir), log_text)
        from .config import Prices
        prices = Prices.load()
        usage = prices.price_tokens(route_def.model, usage)

        # Step 9: Write receipt and append event
        receipt = Receipt(
            result=result,
            exit_code=child_exit_code,
            blocked_reason=blocked_reason,
            amendment_request=amendment_request,
        )

        # Atomic write
        receipt_path = Path(spec.receipt_path)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = receipt_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(receipt.to_dict()), encoding="utf-8")
        os.replace(tmp_path, receipt_path)

        # CR-03 (DR-13): collect the agent's judgement and bind it to the
        # facts. AFTER the receipt, because the receipt records that the
        # PROCESS ended and this records what the AGENT concluded -- two
        # different facts, and the process one must survive even if the agent
        # wrote nothing readable.
        _write_typed_results(spec, log_text)

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

    except Exception:  # census: cleanup/containment (CR-13a)
        # Releases the flocks and RE-RAISES: it substitutes no value and
        # decides nothing. Without it a wrapper crash would hold every mutex
        # this leg took until the kernel released them at process death --
        # which it does, but not before the next pass has already seen them
        # held. Nothing here can turn a refusal into a permission.
        for lease in held_leases:
            lease.release()
        raise


if __name__ == "__main__":  # pragma: no cover
    sys.exit(wrapper_main(sys.argv[1]))
