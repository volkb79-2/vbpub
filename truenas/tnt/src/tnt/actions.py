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
from typing import Any

from .errors import Refused
from .policy import (ALWAYS_REFUSED, BOOT_POOL, EXPLICIT_WRITE_ALLOWLIST, Caller, SafetyState, compute_state,
                     is_destructive_method, is_read_method, needs_membership, normalise_method, refusal_for)  # noqa: F401
from .profile import Secret
from .transport import GuardedRpc


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


_SENSITIVE_KEYS = frozenset({"password", "old_password", "new_password", "api_key", "sshpubkey", "private_key"})


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
        lines.append(f"State fingerprint: {self.fingerprint[:12]}")
        return "\n".join(lines)


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
    if action.kind is Kind.DESTRUCTIVE and not action.probe:
        raise Refused("empty-probe", f"{action.id} is destructive but has no probe: the drift check would be vacuous")
    if action.kind is Kind.DESTRUCTIVE and not (action.confirm_target and action.confirm_target.strip()):
        raise Refused("empty-confirm", f"{action.id} is destructive but names no confirmation target to type")
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
    known_writes = manifested_writes | EXPLICIT_WRITE_ALLOWLIST
    for call in action.calls:
        method = normalise_method(call.method)
        if not is_read_method(method) and method not in known_writes:
            raise Refused("unknown-write", f"{action.id}: {method} is not in a supported adapter manifest or explicit write allow-list")


def fingerprint_of(rpc: GuardedRpc, action: Action, nonce: str) -> str:
    probe_results = [rpc.call(c.method, *c.params) for c in action.probe]
    from .workflows import validate_plan_preconditions
    validate_plan_preconditions(action, probe_results)
    material = {
        "id": action.id, "kind": str(action.kind), "confirm": action.confirm_target,
        "session": rpc.session_id, "nonce": nonce,
        "calls": [[c.method, _fingerprint_value(c.params, nonce), c.job] for c in action.calls],
        "probe": [[c.method, _fingerprint_value(c.params, nonce), _fingerprint_value(result, nonce)]
                  for c, result in zip(action.probe, probe_results)],
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode()).hexdigest()


def make_plan(action: Action, rpc: GuardedRpc) -> Plan:
    """Planning only ever reads; the probe calls are checked to be read methods before any of them runs."""
    if not isinstance(rpc, GuardedRpc):
        raise Refused("unguarded", "plans are made only through a GuardedRpc session")
    validate_action(action)
    nonce = uuid.uuid4().hex
    fingerprint = fingerprint_of(rpc, action, nonce)
    rpc._issue_plan(nonce, fingerprint)
    return Plan(action, fingerprint, rpc.session_id, nonce)


def apply_plan(plan: Plan, rpc: GuardedRpc, *, allow_write: bool, typed_confirmation: str | None = None) -> list[Any]:
    if not isinstance(rpc, GuardedRpc):
        raise Refused("unguarded", "plans run only through a GuardedRpc session")
    action = plan.action
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
    if fingerprint_of(rpc, action, plan.nonce) != plan.fingerprint:
        raise Refused("state-drift", f"the host or the action changed since {action.id} was planned; plan again")
    rpc.spend(plan.nonce, plan.fingerprint)
    if action.kind is Kind.READ:
        return [rpc.call(c.method, *c.params, job=True) if c.job else rpc.call(c.method, *c.params) for c in action.calls]
    with rpc.authorized([c.key() for c in action.calls], state):
        return [rpc.call(c.method, *c.params, job=True) if c.job else rpc.call(c.method, *c.params) for c in action.calls]
