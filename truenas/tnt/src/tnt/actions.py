"""The plan/apply action model: every mutating feature renders the exact RPC calls first, then applies behind gates.

Gates, in order, for ``apply_plan``: refusal list (never overridable) -> write session -> typed confirmation for
destructive actions -> state drift check (the probe fingerprint taken at plan time must still hold) -> execute.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable, Protocol

from .errors import Refused


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
        args = ", ".join(json.dumps(p, sort_keys=True) for p in self.params)
        return f"{self.method}({args})" + ("  [job]" if self.job else "")


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
    action: Action
    fingerprint: str

    def render(self) -> str:
        lines = [f"{self.action.id} [{self.action.kind}] {self.action.title}", "Exact calls, in order:"]
        lines += [f"  {i}. {c.render()}" for i, c in enumerate(self.action.calls, 1)]
        if self.action.confirm_target:
            lines.append(f"Destructive: type {self.action.confirm_target!r} to confirm.")
        lines.append(f"State fingerprint: {self.fingerprint[:12]}")
        return "\n".join(lines)


class Caller(Protocol):
    def call(self, method: str, *params: Any, job: bool = False) -> Any: ...


# -- refusal list (docs/DESIGN.md section 8). These are never executed, with or without confirmation. -------
ALWAYS_REFUSED = {
    "system.reboot": "tnt never reboots or shuts down a host",
    "system.shutdown": "tnt never reboots or shuts down a host",
    "boot.attach": "boot-pool topology is out of scope (ACTION-MATRIX B-07: attach to a partition raises an internal TypeError)",
    "boot.detach": "boot-pool topology is out of scope (ACTION-MATRIX B-07)",
    "boot.replace": "boot-pool topology is out of scope (ACTION-MATRIX B-07)",
}
BOOT_POOL_REFUSED = {"pool.export", "pool.update", "pool.dataset.delete", "pool.snapshot.delete"}  # ACTION-MATRIX A-08


def refusal_for(call: RpcCall, protected_disks: Iterable[str] = ()) -> str | None:
    if call.method in ALWAYS_REFUSED:
        return ALWAYS_REFUSED[call.method]
    if call.method.startswith("auth.") and call.method not in ("auth.me", "auth.mechanism_choices"):
        return "tnt does not change credentials or sessions"
    if call.method in BOOT_POOL_REFUSED and "boot-pool" in json.dumps(call.params, default=str):
        return "boot-pool is never exported, updated or deleted from here (ACTION-MATRIX A-08)"
    if call.method == "disk.wipe" and call.params and call.params[0] in set(protected_disks):
        return f"disk {call.params[0]} belongs to a pool; wipe refuses disks that are in use"
    return None


def fingerprint_of(rpc: Caller, probe: Iterable[RpcCall]) -> str:
    material = [[c.method, list(c.params), rpc.call(c.method, *c.params)] for c in probe]
    return hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode()).hexdigest()


def make_plan(action: Action, rpc: Caller) -> Plan:
    """Planning only ever reads: the probe calls must be read methods (the guarded client enforces that)."""
    return Plan(action, fingerprint_of(rpc, action.probe))


def apply_plan(plan: Plan, rpc: Caller, *, allow_write: bool, typed_confirmation: str | None = None,
               protected_disks: Iterable[str] = ()) -> list[Any]:
    action = plan.action
    protected = tuple(protected_disks)
    for call in action.calls:
        reason = refusal_for(call, protected)
        if reason:
            raise Refused("refusal-list", f"{call.method}: {reason}")
    if action.kind is not Kind.READ and not allow_write:
        raise Refused("read-only", f"{action.id} changes the host; rerun with --allow-write")
    if action.kind is Kind.DESTRUCTIVE and (action.confirm_target is None or typed_confirmation != action.confirm_target):
        raise Refused("typed-confirmation", f"{action.id} is destructive: type {action.confirm_target!r} exactly to confirm")
    if fingerprint_of(rpc, action.probe) != plan.fingerprint:
        raise Refused("state-drift", f"the host changed since {action.id} was planned; plan again")
    return [rpc.call(c.method, *c.params, job=True) if c.job else rpc.call(c.method, *c.params) for c in action.calls]
