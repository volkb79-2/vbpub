"""The plan/apply action model: every mutating feature renders the exact RPC calls first, then applies behind gates.

Gates, in order, for ``apply_plan``: action validity -> live safety state and refusal list (never overridable) ->
write session -> typed confirmation for destructive actions -> state drift (the fingerprint binds the calls, the
confirmation target and the probe results) -> execute through the guarded client, which only lets exactly these
calls run (default-deny, see ``transport.GuardedRpc``).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable, Mapping

from .errors import PlanExecutionError, Refused
from .policy import (ALWAYS_REFUSED, BOOT_POOL, Caller, SafetyState, compute_state,
                     is_destructive_method, is_read_method, needs_membership, normalise_method, refusal_for)  # noqa: F401
from .profile import Secret
from .transport import GuardedRpc, _APPLY_AUTHORITY


class Kind(StrEnum):
    READ = "read"
    MUTATE = "mutate"
    DESTRUCTIVE = "destructive"


@dataclass(frozen=True)
class RpcCall:
    method: str
    params: tuple[Any, ...] = ()
    job: bool = False

    def render(self) -> str:
        args = ", ".join(json.dumps(_redact(p), sort_keys=True) for p in self.params)
        return f"{self.method}({args})" + ("  [job]" if self.job else "")

    def __repr__(self) -> str:
        return f"RpcCall(method={self.method!r}, params={_redact(self.params)!r}, job={self.job!r})"

    def key(self) -> tuple[str, tuple[Any, ...], bool]:
        return (self.method, self.params, self.job)


_SENSITIVE_KEYS = frozenset({"password", "old_password", "new_password", "api_key", "sshpubkey", "private_key",
                             "credentials", "credential", "token", "secret", "secret_access_key", "access_key",
                             "client_secret", "encryption_password", "encryption_salt", "key", "client_key", "salt",
                             "stored_key", "server_key"})


def _redact(value: Any, key: Any = None) -> Any:
    """Return a display-safe copy of JSON-shaped call data."""
    if isinstance(value, Secret) or (isinstance(key, str) and key.casefold() in _SENSITIVE_KEYS):
        return "***"
    if isinstance(value, dict):
        return {k: _redact(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


def _fingerprint_value(value: Any, nonce: str, key: Any = None) -> Any:
    """Bind secret values into a plan without placing them in its display or stored digest material."""
    sensitive_key = isinstance(key, str) and key.casefold() in _SENSITIVE_KEYS
    if isinstance(value, Secret):
        raw = value.reveal()
        return {"secret_sha256": hashlib.sha256(nonce.encode() + b"\0" + raw.encode()).hexdigest()}
    if sensitive_key:
        raw = value if isinstance(value, str) else repr(value)
        return {"secret_sha256": hashlib.sha256(nonce.encode() + b"\0" + raw.encode()).hexdigest()}
    if isinstance(value, dict):
        return {k: _fingerprint_value(v, nonce, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_fingerprint_value(item, nonce) for item in value]
    return value


@dataclass(frozen=True)
class Action:
    id: str  # an ACTION-MATRIX row id (A-09, B-01, ...) or a TNT-xx id for actions with no matrix row
    kind: Kind
    title: str
    calls: tuple[RpcCall, ...]
    probe: tuple[RpcCall, ...] = ()  # read-only calls whose result must not change between plan and apply
    confirm_target: str | None = None  # the name the operator types for a destructive action
    force_with_jobs: bool = False
    system_preflight: tuple[Any, ...] = ()
    confirm_self: bool = False  # explicit confirmation when an account action can change the current login


@dataclass(frozen=True)
class Plan:
    """A plan is bound to the session that made it and is single-use (see ``GuardedRpc.spend``)."""

    action: Action
    fingerprint: str
    session_id: str = ""
    nonce: str = ""

    def render(self) -> str:
        lines = [f"{self.action.id} [{self.action.kind}] {self.action.title}", "Exact calls, in order:"]
        lines += [f"  {i}. {c.render()}" for i, c in enumerate(self.action.calls, 1)]
        if self.action.confirm_target:
            lines.append(f"Destructive: type {self.action.confirm_target!r} to confirm.")
        if self.action.confirm_self:
            lines.append("Explicit current-account confirmation: supplied.")
        if self.action.system_preflight:
            host, jobs, vms, containers, sessions, full_admin = self.action.system_preflight
            lines.append(f"System preflight for {host}:")
            if full_admin:
                lines.append("  FULL_ADMIN verified; the job list covers all sessions.")
            lines.append(f"  {len(jobs)} running job(s): " + (", ".join(f"{ident} {method}" for ident, method in jobs) or "none"))
            lines.append(f"  {len(vms)} running VM(s): " + (", ".join(vms) or "none"))
            lines.append(f"  {len(containers)} running container(s): " + (", ".join(containers) or "none"))
            lines.append(f"  {sessions} active SMB sessions")
            if self.action.force_with_jobs:
                lines.append("  Force with running jobs: requested; typed confirmation is still required.")
        lines.append(f"State fingerprint: {self.fingerprint[:12]}")
        return "\n".join(lines)


@dataclass(frozen=True)
class CallExecution:
    index: int
    method: str
    status: str
    job_id: str | None = None
    state: str | None = None
    generation: int | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"index": self.index, "method": self.method, "status": self.status,
                "job_id": self.job_id, "state": self.state, "generation": self.generation,
                "error": self.error}


class ApplyResult(list):
    """List-compatible call results plus one outcome record per planned call."""

    def __init__(self, results: list[Any], calls: list[CallExecution]):
        super().__init__(results)
        self.calls = tuple(calls)


def validate_action(action: Action) -> None:
    """Structural rules that hold for every action, checked at plan time and again at apply time."""
    if not action.calls:
        raise Refused("empty-action", f"{action.id} has no calls")
    for call in action.probe:
        if not is_read_method(call.method):
            raise Refused("probe-not-read", f"{action.id}: probe call {call.method} is not a read method")
    if action.kind is Kind.READ:
        for call in action.calls:
            if not is_read_method(call.method):
                raise Refused("read-action", f"{action.id} is a READ action but calls {call.method}, which is not a read method")
    if action.kind is not Kind.READ and not action.probe:
        raise Refused("empty-probe", f"{action.id} changes host state but has no target probe: the drift check would be vacuous")
    if action.kind is Kind.DESTRUCTIVE and not (action.confirm_target and action.confirm_target.strip()):
        raise Refused("empty-confirm", f"{action.id} is destructive but names no confirmation target to type")
    if (not isinstance(action.force_with_jobs, bool) or not isinstance(action.system_preflight, tuple)
            or not isinstance(action.confirm_self, bool)):
        raise Refused("action-policy", "action safety metadata is malformed")
    if action.force_with_jobs and not action.system_preflight:
        raise Refused("action-policy", "force_with_jobs is available only for a preflighted system action")
    if action.kind is not Kind.DESTRUCTIVE:
        for call in action.calls:
            if is_destructive_method(call.method):
                raise Refused("kind-mismatch", f"{action.id}: {call.method} destroys data, so the action must be DESTRUCTIVE (probe and typed confirmation)")
    if any(call.method == "pool.dataset.delete" for call in action.calls):
        expected = ("pool.dataset.details", "container.query", "vm.query")
        if tuple(call.method for call in action.probe) != expected:
            raise Refused("dataset-delete-probe", "pool.dataset.delete requires dataset, container, and VM usage probes")
    from .adapters import GENERATIONS
    manifested_writes = {
        normalise_method(method)
        for backend_type in GENERATIONS.values()
        for methods in backend_type.manifest.values()
        for method, spec in methods.items()
        if spec.supported and spec.classification != "read"
    }
    for call in action.calls:
        method = normalise_method(call.method)
        if not is_read_method(method) and method not in manifested_writes:
            raise Refused("unknown-write", f"{action.id}: {method} is not in a supported adapter manifest for v27")


def _check_cancelled(cancelled: Callable[[], bool] | None) -> None:
    if cancelled is not None and cancelled():
        raise Refused("cancelled", "the selected host session changed; this operation was cancelled")


def fingerprint_of(rpc: GuardedRpc, action: Action, nonce: str,
                   cancelled: Callable[[], bool] | None = None) -> str:
    probe_results = []
    for call in action.probe:
        _check_cancelled(cancelled)
        probe_results.append(rpc.call(call.method, *call.params))
    from .workflows import validate_plan_preconditions
    validate_plan_preconditions(action, probe_results)
    material = {
        "id": action.id, "kind": str(action.kind), "title": action.title, "confirm": action.confirm_target,
        "force_with_jobs": action.force_with_jobs, "system_preflight": action.system_preflight,
        "confirm_self": action.confirm_self,
        "session": rpc.session_id, "nonce": nonce,
        "calls": [[c.method, _fingerprint_value(c.params, nonce), c.job] for c in action.calls],
        "probe": [[c.method, _fingerprint_value(c.params, nonce), _fingerprint_value(result, nonce)]
                  for c, result in zip(action.probe, probe_results)],
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode()).hexdigest()


def make_plan(action: Action, rpc: GuardedRpc, *, cancelled: Callable[[], bool] | None = None) -> Plan:
    """Planning only ever reads; the probe calls are checked to be read methods before any of them runs."""
    if not isinstance(rpc, GuardedRpc):
        raise Refused("unguarded", "plans are made only through a GuardedRpc session")
    validate_action(action)
    nonce = uuid.uuid4().hex
    _check_cancelled(cancelled)
    fingerprint = fingerprint_of(rpc, action, nonce, cancelled)
    _check_cancelled(cancelled)
    rpc._issue_plan(nonce, fingerprint)
    return Plan(action, fingerprint, rpc.session_id, nonce)


def apply_plan(plan: Plan, rpc: GuardedRpc, *, allow_write: bool, typed_confirmation: str | None = None,
               job_progress: Callable[[Mapping[str, Any]], None] | None = None,
               cancelled: Callable[[], bool] | None = None) -> ApplyResult:
    if not isinstance(rpc, GuardedRpc):
        raise Refused("unguarded", "plans run only through a GuardedRpc session")
    action = plan.action
    _check_cancelled(cancelled)
    validate_action(action)
    if plan.session_id != rpc.session_id:
        raise Refused("foreign-session", "this plan was made on another session; plan again on this one")
    rpc.check_plan(plan.nonce, plan.fingerprint)
    state: SafetyState | None = None
    if any(needs_membership(c.method, c.params) for c in action.calls):  # lazy: unrelated actions never depend on pool membership
        state = compute_state(rpc)
    for call in action.calls:
        reason = refusal_for(call.method, call.params, state)
        if reason:
            raise Refused("refusal-list", f"{call.method}: {reason}")
    if action.kind is not Kind.READ and (not allow_write or rpc.read_only):
        raise Refused("read-only", f"{action.id} changes the host; rerun with --allow-write")
    if action.kind is Kind.DESTRUCTIVE and (action.confirm_target is None or typed_confirmation != action.confirm_target):
        raise Refused("typed-confirmation", f"{action.id} is destructive: type {action.confirm_target!r} exactly to confirm")
    if fingerprint_of(rpc, action, plan.nonce, cancelled) != plan.fingerprint:
        raise Refused("state-drift", f"the host or the action changed since {action.id} was planned; plan again")
    _check_cancelled(cancelled)
    grant = rpc.spend(plan.nonce, plan.fingerprint, [c.key() for c in action.calls], state,
                      authority=_APPLY_AUTHORITY, require_grant=action.kind is not Kind.READ)
    results: list[Any] = []
    records = [CallExecution(index + 1, call.method, "not-started") for index, call in enumerate(action.calls)]

    def partial_message(error: BaseException, index: int) -> str:
        completed = sum(record.status == "completed" for record in records)
        current = records[index]
        detail = f"call {current.index} ({current.method}) is {current.status}"
        if current.job_id is not None:
            detail += f" with job ID {current.job_id}"
        if current.state:
            detail += f" in state {current.state}"
        reason = str(error).strip() or type(error).__name__
        return (f"plan stopped after {completed} completed call(s); {detail}. review Jobs and current host "
                f"state before planning recovery. Error: {reason}")

    def execute(index: int, call: RpcCall) -> Any:
        _check_cancelled(cancelled)
        before = len(rpc.calls)
        handle = None
        try:
            if call.job:
                handle = rpc.call(call.method, *call.params, job=True, job_callback=job_progress)
                job_id = getattr(handle, "job_id", None)
                generation = getattr(getattr(handle, "registry", None), "get", lambda _key: None)(
                    getattr(handle, "local_id", ""))
                generation = generation.get("generation") if isinstance(generation, Mapping) else None
                records[index] = CallExecution(index + 1, call.method, "submitted",
                                               None if job_id is None else str(job_id),
                                               getattr(handle, "state", "WAITING"), generation)
                value = handle.wait(cancelled=cancelled)
                records[index] = CallExecution(index + 1, call.method, "completed", str(job_id),
                                               getattr(handle, "state", "SUCCESS"), generation)
            else:
                value = rpc.call(call.method, *call.params)
                records[index] = CallExecution(index + 1, call.method, "completed")
            results.append(value)
            return value
        except Exception as exc:
            attempted = len(rpc.calls) > before
            if handle is not None:
                state = getattr(handle, "state", "UNKNOWN")
                if state in {"FAILED", "ABORTED"}:
                    status = "failed"
                elif state == "UNKNOWN":
                    status = "unknown"
                else:
                    status = "submitted"
                job_id = str(getattr(handle, "job_id", "")) or None
                record = getattr(getattr(handle, "registry", None), "get", lambda _key: None)(
                    getattr(handle, "local_id", ""))
                generation = record.get("generation") if isinstance(record, Mapping) else None
                records[index] = CallExecution(index + 1, call.method, status, job_id, state, generation,
                                               str(exc))
                attempted = True
            elif attempted:
                records[index] = CallExecution(index + 1, call.method, "unknown", error=str(exc))
            if action.kind is not Kind.READ and attempted:
                raise PlanExecutionError(partial_message(exc, index), ApplyResult(results, records)) from exc
            if action.kind is not Kind.READ and any(record.status == "completed" for record in records):
                raise PlanExecutionError(partial_message(exc, index), ApplyResult(results, records)) from exc
            raise

    def execute_all() -> ApplyResult:
        for index, call in enumerate(action.calls):
            try:
                execute(index, call)
            except Exception as exc:
                if (not isinstance(exc, PlanExecutionError) and action.kind is not Kind.READ
                        and any(record.status == "completed" for record in records)):
                    raise PlanExecutionError(partial_message(exc, index), ApplyResult(results, records)) from exc
                raise
        return ApplyResult(results, records)

    if action.kind is Kind.READ:
        return execute_all()
    with rpc._authorized([c.key() for c in action.calls], grant):
        return execute_all()
