"""Host-local admission accounting primitives.

This module is deliberately stdlib-only.  Capacity facts retain both their
value and the reason they are unavailable; admission never fills in a missing
host fact with a guessed default.
"""

from __future__ import annotations

import math
import fcntl
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Generic, Iterable, Mapping, TypeVar, cast

T = TypeVar("T")

FOOTPRINTS = ("tiny", "small", "medium", "large")
PRIORITIES = ("merge-gate", "dev", "gate", "campaign")
MAX_JOB_ID_LENGTH = 128
JOB_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
GROUP_RE = JOB_ID_RE
PSI_MEMORY_FULL_REFUSE_AVG10_PCT = 3.0
QUEUE_RECHECK_S = 0.25


class ExitCode:
    """The fleetd runner's closed exit-code vocabulary."""

    PASS = 0
    LANE_FAILURE = 1
    REFUSAL = 2
    IDENTITY_OR_TRANSPORT = 3
    BUDGET_EXCEEDED = 4
    RUNNER_FAILURE = 70
    CHECKOUT_FAILURE = 71
    MISSING_BASE = 72
    QUEUED_WITHDRAWN = 75
    INCONCLUSIVE_OOM = 76
    STORE_COLLECTION_FAILED = 77
    STORE_SEED_FAILED = 78
    PREREQUISITE_MISSING = 79
    CANCELLED = 143


class AdmissionError(Exception):
    """A refusal with the exit code callers must report."""

    def __init__(self, exit_code: int, reason: str) -> None:
        super().__init__(reason)
        self.exit_code = exit_code
        self.reason = reason


@dataclass(frozen=True)
class Fact(Generic[T]):
    """A host fact which is either known or unavailable for an explicit reason."""

    value: T | None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.value is None and (not isinstance(self.reason, str) or not self.reason.strip()):
            raise ValueError("an unavailable fact requires a reason")
        if self.value is not None and self.reason is not None:
            raise ValueError("a known fact cannot also have an unavailable reason")

    @classmethod
    def known(cls, value: T) -> Fact[T]:
        return cls(value)

    @classmethod
    def unknown(cls, reason: str) -> Fact[T]:
        return cls(None, reason)

    def require(self, name: str) -> T:
        if self.value is None:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"{name} unavailable: {self.reason}")
        return cast(T, self.value)


@dataclass(frozen=True)
class CpuFacts:
    online: Fact[int]
    planning_cap: Fact[float]


@dataclass(frozen=True)
class MemoryFacts:
    total_bytes: Fact[int]
    available_bytes: Fact[int]
    host_reserve_bytes: Fact[int]
    swap_total_bytes: Fact[int]
    swap_free_bytes: Fact[int]


@dataclass(frozen=True)
class PressureFacts:
    some_avg10_pct: Fact[float]
    full_avg10_pct: Fact[float]


@dataclass(frozen=True)
class PsiFacts:
    cpu: PressureFacts
    memory: PressureFacts
    io: PressureFacts


@dataclass(frozen=True)
class GateSliceFacts:
    memory_current_bytes: Fact[int]
    memory_warm_bytes: Fact[int]
    memory_high_bytes: Fact[int]
    memory_max_bytes: Fact[int]
    cpu_max: Fact[str]


@dataclass(frozen=True)
class DiskFacts:
    name: Fact[str]
    mount: Fact[str]
    total_bytes: Fact[int]
    free_bytes: Fact[int]
    fs: Fact[str]
    floor_bytes: Fact[int]


@dataclass(frozen=True)
class PolicyFacts:
    planning_cap: Fact[float]
    max_footprint: Fact[str]
    max_planned_bytes: Fact[int]
    footprint_charges_bytes: Fact[Mapping[str, int]]
    priority_classes_by_principal: Fact[Mapping[str, tuple[str, ...]]]
    disk_floors: Fact[Mapping[str, int]]
    psi_refuse_some_avg10: Fact[float]
    admission: Fact[Mapping[str, object]]
    aging_s: Fact[float]
    legacy_charge_bytes: Fact[int]
    legacy_planned_cores: Fact[float]


@dataclass(frozen=True)
class GuardFacts:
    guard_present: Fact[bool]
    guard_state: Fact[str]
    config_digest: Fact[str]
    frozen_units: Fact[int]


@dataclass(frozen=True)
class HostCapacity:
    """The admission-relevant projection of the host-facts/2 field names."""

    cpu: CpuFacts
    load1: Fact[float]
    memory: MemoryFacts
    psi: PsiFacts
    gate_slice: GateSliceFacts
    disks: Fact[tuple[DiskFacts, ...]]
    cgroup_v2_mounted: Fact[bool]
    policy: PolicyFacts
    guard: GuardFacts
    warm_samples_bytes_by_id: Fact[Mapping[str, int]]


@dataclass(frozen=True)
class Charge:
    """One existing book entry's charge and observed warm use."""

    charged_bytes: int
    current_warm_bytes: int
    planned_cores: float


@dataclass(frozen=True)
class StaticEligibility:
    footprint: str
    charge_bytes: int
    max_planned_cores: float
    static_ram_bytes: int


def _nonnegative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AdmissionError(ExitCode.REFUSAL, f"{name} must be a non-negative integer")
    return value


def _positive_int(value: object, name: str) -> int:
    checked = _nonnegative_int(value, name)
    if checked == 0:
        raise AdmissionError(ExitCode.REFUSAL, f"{name} must be positive")
    return checked


def _nonnegative_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AdmissionError(ExitCode.REFUSAL, f"{name} must be a finite non-negative number")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise AdmissionError(ExitCode.REFUSAL, f"{name} must be a finite non-negative number") from exc
    if not math.isfinite(number) or number < 0:
        raise AdmissionError(ExitCode.REFUSAL, f"{name} must be a finite non-negative number")
    return number


def _known_charge(capacity: HostCapacity, footprint: str) -> int:
    if footprint not in FOOTPRINTS:
        raise AdmissionError(ExitCode.REFUSAL, f"unknown footprint class: {footprint!r}")
    max_footprint = capacity.policy.max_footprint.require("policy.max_footprint")
    if max_footprint not in FOOTPRINTS:
        raise AdmissionError(ExitCode.REFUSAL, "policy.max_footprint is invalid")
    if FOOTPRINTS.index(footprint) > FOOTPRINTS.index(max_footprint):
        raise AdmissionError(ExitCode.REFUSAL, f"footprint {footprint!r} exceeds host maximum {max_footprint!r}")
    charges = capacity.policy.footprint_charges_bytes.require("policy.footprint_charges_bytes")
    charge = charges.get(footprint)
    if isinstance(charge, bool) or not isinstance(charge, int) or charge <= 0:
        raise AdmissionError(ExitCode.REFUSAL, f"host has no valid charge for footprint {footprint!r}")
    return charge


def _static_ram_bytes(capacity: HostCapacity) -> int:
    total = _positive_int(capacity.memory.total_bytes.require("memory.total_bytes"), "memory.total_bytes")
    reserve = _nonnegative_int(
        capacity.memory.host_reserve_bytes.require("memory.host_reserve_bytes"),
        "memory.host_reserve_bytes",
    )
    high = _positive_int(
        capacity.gate_slice.memory_high_bytes.require("gate_slice.memory_high_bytes"),
        "gate_slice.memory_high_bytes",
    )
    maximum = _positive_int(
        capacity.gate_slice.memory_max_bytes.require("gate_slice.memory_max_bytes"),
        "gate_slice.memory_max_bytes",
    )
    planned_cap = _positive_int(
        capacity.policy.max_planned_bytes.require("policy.max_planned_bytes"),
        "policy.max_planned_bytes",
    )
    return min(total - reserve, min(high, maximum), planned_cap)


def _planning_core_cap(capacity: HostCapacity) -> float:
    online = _positive_int(capacity.cpu.online.require("cpu.online"), "cpu.online")
    slice_cap = _nonnegative_number(capacity.cpu.planning_cap.require("cpu.planning_cap"), "cpu.planning_cap")
    policy_cap = _nonnegative_number(
        capacity.policy.planning_cap.require("policy.planning_cap"), "policy.planning_cap"
    )
    return min(float(online + 1), slice_cap, policy_cap)


def static_eligibility(capacity: HostCapacity, footprint: str, planned_cores: float) -> StaticEligibility:
    """Validate request class and static RAM/CPU possibility without activity factors."""

    charge = _known_charge(capacity, footprint)
    static_ram = _static_ram_bytes(capacity)
    core_cap = _planning_core_cap(capacity)
    planned = _nonnegative_number(planned_cores, "resources.planned_cores")
    if planned == 0:
        raise AdmissionError(ExitCode.REFUSAL, "resources.planned_cores must be positive")
    if static_ram < charge:
        raise AdmissionError(
            ExitCode.REFUSAL,
            f"footprint {footprint!r} charge {charge} exceeds static host RAM capacity {static_ram}",
        )
    if planned > core_cap:
        raise AdmissionError(
            ExitCode.REFUSAL,
            f"planned cores {planned} exceed host planning cap {core_cap}",
        )
    return StaticEligibility(footprint, charge, core_cap, static_ram)


def fit_bytes(capacity: HostCapacity, charges: tuple[Charge, ...] = ()) -> int:
    """Return the §3.3 minimum of physical, slice, and planned-byte headroom."""

    total = _positive_int(capacity.memory.total_bytes.require("memory.total_bytes"), "memory.total_bytes")
    host_reserve = _nonnegative_int(
        capacity.memory.host_reserve_bytes.require("memory.host_reserve_bytes"),
        "memory.host_reserve_bytes",
    )
    slice_warm = _nonnegative_int(
        capacity.gate_slice.memory_warm_bytes.require("gate_slice.memory_warm_bytes"),
        "gate_slice.memory_warm_bytes",
    )
    slice_high = _positive_int(
        capacity.gate_slice.memory_high_bytes.require("gate_slice.memory_high_bytes"),
        "gate_slice.memory_high_bytes",
    )
    slice_max = _positive_int(
        capacity.gate_slice.memory_max_bytes.require("gate_slice.memory_max_bytes"),
        "gate_slice.memory_max_bytes",
    )
    max_planned = _positive_int(
        capacity.policy.max_planned_bytes.require("policy.max_planned_bytes"),
        "policy.max_planned_bytes",
    )
    charged = 0
    reserved = 0
    for index, entry in enumerate(charges):
        amount = _nonnegative_int(entry.charged_bytes, f"charges[{index}].charged_bytes")
        current = _nonnegative_int(entry.current_warm_bytes, f"charges[{index}].current_warm_bytes")
        _nonnegative_number(entry.planned_cores, f"charges[{index}].planned_cores")
        charged += amount
        reserved += max(0, amount - current)
    return min(
        total - host_reserve - charged,
        min(slice_high, slice_max) - slice_warm - reserved,
        max_planned - charged,
    )


def planned_core_headroom(capacity: HostCapacity, charges: tuple[Charge, ...] = ()) -> float:
    """Return host planned-core headroom, bounded by online cores and host policy."""

    used = 0.0
    for index, entry in enumerate(charges):
        _nonnegative_int(entry.charged_bytes, f"charges[{index}].charged_bytes")
        _nonnegative_int(entry.current_warm_bytes, f"charges[{index}].current_warm_bytes")
        used += _nonnegative_number(entry.planned_cores, f"charges[{index}].planned_cores")
    return _planning_core_cap(capacity) - used


def _validate_name(value: str, name: str) -> str:
    if not isinstance(value, str) or len(value) > MAX_JOB_ID_LENGTH or JOB_ID_RE.fullmatch(value) is None:
        raise AdmissionError(ExitCode.REFUSAL, f"{name} is not a valid admission identifier")
    return value


def _pressure_value(fact: Fact[float], name: str) -> float:
    value = fact.require(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"{name} is invalid")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"{name} is invalid") from exc
    if not math.isfinite(result) or result < 0 or result > 100:
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"{name} is invalid")
    return result


def _require_live_facts(capacity: HostCapacity) -> None:
    mounted = capacity.cgroup_v2_mounted.require("cgroup_v2.mounted")
    if not isinstance(mounted, bool):
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "cgroup_v2.mounted is invalid")
    if not mounted:
        raise AdmissionError(ExitCode.REFUSAL, "cgroup v2 is not mounted")
    online = _positive_int(capacity.cpu.online.require("cpu.online"), "cpu.online")
    load1 = capacity.load1.require("load1")
    if isinstance(load1, bool) or not isinstance(load1, (int, float)) or load1 < 0:
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "load1 is invalid")
    try:
        load_value = float(load1)
    except (OverflowError, ValueError) as exc:
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "load1 is invalid") from exc
    if not math.isfinite(load_value):
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "load1 is invalid")
    if load_value > online + 1:
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"load1 {load1} exceeds cores+1 ({online + 1})")
    memory_full = _pressure_value(capacity.psi.memory.full_avg10_pct, "psi.memory.full_avg10_pct")
    if memory_full >= PSI_MEMORY_FULL_REFUSE_AVG10_PCT:
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "memory full avg10 is at or above 3 percent")
    memory_some = _pressure_value(capacity.psi.memory.some_avg10_pct, "psi.memory.some_avg10_pct")
    some_limit = _nonnegative_number(
        capacity.policy.psi_refuse_some_avg10.require("policy.psi_refuse_some_avg10"),
        "policy.psi_refuse_some_avg10",
    )
    if some_limit > 100:
        raise AdmissionError(ExitCode.REFUSAL, "policy.psi_refuse_some_avg10 exceeds 100")
    if memory_some >= some_limit:
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "memory some avg10 reached its configured refusal threshold")
    _check_guard(capacity.guard)
    _check_disk_floors(capacity)


def _check_guard(guard: GuardFacts) -> None:
    present = guard.guard_present.require("guard.guard_present")
    if not isinstance(present, bool):
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "guard.guard_present is invalid")
    if not present:
        return
    state = guard.guard_state.require("guard.guard_state")
    if not isinstance(state, str):
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "guard.guard_state is invalid")
    if state in {"off", "report"}:
        return
    if state != "enforce":
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"guard state is unknown: {state!r}")
    frozen_units = _nonnegative_int(guard.frozen_units.require("guard.frozen_units"), "guard.frozen_units")
    if frozen_units > 0:
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"host has {frozen_units} frozen guard units")


def _check_disk_floors(capacity: HostCapacity) -> None:
    floors = capacity.policy.disk_floors.require("policy.disk_floors")
    if not isinstance(floors, Mapping) or not floors:
        raise AdmissionError(ExitCode.REFUSAL, "policy.disk_floors must name checkout and Docker roots")
    if not {"checkout", "docker"}.issubset(floors):
        raise AdmissionError(ExitCode.REFUSAL, "policy.disk_floors must name checkout and Docker roots")
    normalized: dict[str, int] = {}
    for name, floor in floors.items():
        if not isinstance(name, str) or not name:
            raise AdmissionError(ExitCode.REFUSAL, "policy.disk_floors contains an invalid disk name")
        normalized[name] = _nonnegative_int(floor, f"policy.disk_floors[{name!r}]")
    disks = capacity.disks.require("disks[]")
    if not isinstance(disks, tuple):
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "disks[] is invalid")
    by_name: dict[str, DiskFacts] = {}
    for index, disk in enumerate(disks):
        name = disk.name.require(f"disks[{index}].name")
        if not isinstance(name, str) or not name:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"disks[{index}].name is invalid")
        if name in by_name:
            raise AdmissionError(ExitCode.REFUSAL, f"duplicate disk fact for {name!r}")
        by_name[name] = disk
    for name, floor in normalized.items():
        disk = by_name.get(name)
        if disk is None:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"disk fact {name!r} is unavailable")
        reported_floor = _nonnegative_int(disk.floor_bytes.require(f"disks[{name!r}].floor_bytes"),
                                          f"disks[{name!r}].floor_bytes")
        if reported_floor != floor:
            raise AdmissionError(ExitCode.REFUSAL, f"disk floor facts disagree for {name!r}")
        free = _nonnegative_int(disk.free_bytes.require(f"disks[{name!r}].free_bytes"),
                                f"disks[{name!r}].free_bytes")
        if free < floor:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"disk {name!r} is below its free-space floor")


def _read_json_record(path: Path, *, expected_id: str) -> dict[str, object]:
    try:
        if path.is_symlink() or not path.is_file():
            raise OSError("record is not a regular file")
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, ValueError, RecursionError, OverflowError) as exc:
        raise AdmissionError(
            ExitCode.QUEUED_WITHDRAWN,
            f"admission record {path.name!r} is unreadable; its charge is retained: {exc}",
        ) from exc
    if not isinstance(value, dict):
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                             f"admission record {path.name!r} is not an object; its charge is retained")
    if value.get("job_id") != expected_id:
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                             f"admission record {path.name!r} has a mismatched id; its charge is retained")
    group = value.get("group")
    digest = value.get("digest")
    priority = value.get("priority")
    footprint = value.get("footprint")
    state = value.get("state")
    if not isinstance(group, str) or not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                             f"admission record {path.name!r} has invalid identity; its charge is retained")
    if (not isinstance(priority, str) or priority not in PRIORITIES
            or not isinstance(footprint, str) or footprint not in FOOTPRINTS
            or state != "admitted"):
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                             f"admission record {path.name!r} has invalid policy fields; its charge is retained")
    try:
        charged = _positive_int(value.get("charged_bytes"), "record.charged_bytes")
        current = _nonnegative_int(value.get("current_warm_bytes"), "record.current_warm_bytes")
        cores = _nonnegative_number(value.get("planned_cores"), "record.planned_cores")
        if cores == 0:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "record.planned_cores must be positive")
        created_at = _nonnegative_number(value.get("created_at"), "record.created_at")
    except AdmissionError as exc:
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                             f"admission record {path.name!r} is invalid; its charge is retained: {exc.reason}") from exc
    value["charged_bytes"] = charged
    value["current_warm_bytes"] = current
    value["planned_cores"] = cores
    value["created_at"] = created_at
    return value


def _read_waiter_record(path: Path, *, expected_id: str) -> dict[str, object]:
    try:
        if path.is_symlink() or not path.is_file():
            raise OSError("waiter is not a regular file")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, RecursionError, OverflowError) as exc:
        raise AdmissionError(
            ExitCode.QUEUED_WITHDRAWN,
            f"waiting record {path.name!r} is unreadable; it remains reserved: {exc}",
        ) from exc
    if not isinstance(value, dict) or value.get("job_id") != expected_id:
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                             f"waiting record {path.name!r} is malformed; it remains reserved")
    group = value.get("group")
    digest = value.get("digest")
    priority = value.get("priority")
    requested_priority = value.get("requested_priority")
    footprint = value.get("footprint")
    if not isinstance(group, str) or not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                             f"waiting record {path.name!r} has invalid identity; it remains reserved")
    if (not isinstance(priority, str) or priority not in PRIORITIES
            or not isinstance(requested_priority, str) or requested_priority not in PRIORITIES
            or not isinstance(footprint, str) or footprint not in FOOTPRINTS):
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                             f"waiting record {path.name!r} has invalid policy; it remains reserved")
    if value.get("state") != "waiting" or not isinstance(value.get("cancelled"), bool):
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                             f"waiting record {path.name!r} has invalid state; it remains reserved")
    record_kind = value.get("record_kind")
    if not isinstance(record_kind, str) or record_kind not in {"job", "lease"}:
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                             f"waiting record {path.name!r} has invalid record type; it remains reserved")
    try:
        cores = _nonnegative_number(value.get("planned_cores"), "waiting.planned_cores")
        submitted_at = _nonnegative_number(value.get("submitted_at"), "waiting.submitted_at")
        if cores == 0:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "waiting.planned_cores must be positive")
    except AdmissionError as exc:
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                             f"waiting record {path.name!r} is invalid; it remains reserved: {exc.reason}") from exc
    value["planned_cores"] = cores
    value["submitted_at"] = submitted_at
    value["record_kind"] = record_kind
    return value


def _read_lease_record(path: Path, *, expected_id: str) -> dict[str, object]:
    try:
        if path.is_symlink() or not path.is_file():
            raise OSError("lease is not a regular file")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, RecursionError, OverflowError) as exc:
        raise AdmissionError(
            ExitCode.QUEUED_WITHDRAWN,
            f"lease record {path.name!r} is unreadable; its charge is retained: {exc}",
        ) from exc
    if not isinstance(value, dict) or value.get("lease_id") != expected_id:
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                             f"lease record {path.name!r} is malformed; its charge is retained")
    group = value.get("group")
    digest = value.get("digest")
    name = value.get("container_name")
    priority = value.get("priority")
    footprint = value.get("footprint")
    state = value.get("state")
    if not isinstance(group, str) or not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                             f"lease record {path.name!r} has invalid identity; its charge is retained")
    if (not isinstance(name, str) or not name
            or not isinstance(priority, str) or priority not in PRIORITIES
            or not isinstance(footprint, str) or footprint not in FOOTPRINTS):
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                             f"lease record {path.name!r} has invalid policy; its charge is retained")
    if (not isinstance(state, str) or state not in {"pending", "bound", "tombstone"}
            or not isinstance(value.get("released"), bool)):
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                             f"lease record {path.name!r} has invalid state; its charge is retained")
    container_id = value.get("container_id")
    if container_id is not None and (not isinstance(container_id, str) or not container_id):
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                             f"lease record {path.name!r} has invalid container id; its charge is retained")
    if state == "bound" and container_id is None:
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                             f"lease record {path.name!r} is bound without an id; its charge is retained")
    if value["released"] and state != "tombstone":
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                             f"lease record {path.name!r} is released without a tombstone; its charge is retained")
    try:
        charged = _positive_int(value.get("charged_bytes"), "lease.charged_bytes")
        current = _nonnegative_int(value.get("current_warm_bytes"), "lease.current_warm_bytes")
        cores = _nonnegative_number(value.get("planned_cores"), "lease.planned_cores")
        created_at = _nonnegative_number(value.get("created_at"), "lease.created_at")
        if cores == 0:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "lease.planned_cores must be positive")
    except AdmissionError as exc:
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                             f"lease record {path.name!r} is invalid; its charge is retained: {exc.reason}") from exc
    value["charged_bytes"] = charged
    value["current_warm_bytes"] = current
    value["planned_cores"] = cores
    value["created_at"] = created_at
    return value


def _read_terminal_record(path: Path, *, expected_id: str) -> dict[str, object]:
    try:
        if path.is_symlink() or not path.is_file():
            raise OSError("terminal record is not a regular file")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, RecursionError, OverflowError) as exc:
        raise AdmissionError(
            ExitCode.QUEUED_WITHDRAWN,
            f"terminal record {path.name!r} is unreadable: {exc}",
        ) from exc
    if not isinstance(value, dict) or value.get("job_id") != expected_id:
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                             f"terminal record {path.name!r} is malformed")
    group = value.get("group")
    digest = value.get("digest")
    if (value.get("schema") != "fleet-admission-terminal/1" or value.get("state") != "completed"
            or not isinstance(group, str) or not group
            or not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None):
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                             f"terminal record {path.name!r} has invalid identity")
    try:
        completed_at = _nonnegative_number(value.get("completed_at"), "terminal.completed_at")
    except AdmissionError as exc:
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                             f"terminal record {path.name!r} is invalid: {exc.reason}") from exc
    return {
        "schema": "fleet-admission-terminal/1",
        "job_id": expected_id,
        "group": group,
        "digest": digest,
        "state": "completed",
        "completed_at": completed_at,
    }


def _open_lock(path: Path, *, blocking: bool) -> int | None:
    flags = os.O_CREAT | os.O_RDWR
    flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"cannot open admission lock {path.name!r}: {exc}") from exc
    operation = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
    try:
        fcntl.flock(fd, operation)
    except BlockingIOError:
        os.close(fd)
        return None
    except OSError as exc:
        os.close(fd)
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"cannot lock admission file {path.name!r}: {exc}") from exc
    return fd


def _unlock_close(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_json(path: Path, value: Mapping[str, object]) -> None:
    temporary: str | None = None
    try:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        fd, temporary = tempfile.mkstemp(prefix=".admission-", dir=path.parent)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_dir(path.parent)
    except (OSError, TypeError, ValueError) as exc:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"cannot atomically write admission record: {exc}") from exc


def _atomic_new_json(path: Path, value: Mapping[str, object]) -> None:
    """Write a new charged record, rolling it back if durability cannot be confirmed."""

    try:
        _atomic_json(path, value)
    except BaseException as original:
        try:
            path.unlink(missing_ok=True)
            _fsync_dir(path.parent)
        except OSError as cleanup_error:
            raise AdmissionError(
                ExitCode.QUEUED_WITHDRAWN,
                f"new admission record write failed and rollback could not be confirmed; its charge is retained: {cleanup_error}",
            ) from original
        raise


@dataclass(frozen=True)
class AdmissionResult:
    record: Mapping[str, object]
    handle: AdmissionHandle | LeaseHandle | None
    existing: bool


class AdmissionHandle:
    """An admitted job's executor-held flock; close alone does not erase its charge."""

    def __init__(self, book: AdmissionBook, record: Mapping[str, object], lock_fd: int) -> None:
        self._book = book
        self.record = dict(record)
        self._lock_fd: int | None = lock_fd

    @property
    def live(self) -> bool:
        return self._lock_fd is not None

    def fileno(self) -> int:
        if self._lock_fd is None:
            raise AdmissionError(ExitCode.REFUSAL, "job handle is closed")
        return self._lock_fd

    def handoff(self) -> None:
        """Close the parent fd after a child inherited it with pass_fds."""
        if self._lock_fd is not None:
            fd, self._lock_fd = self._lock_fd, None
            os.close(fd)

    def close(self) -> None:
        if self._lock_fd is not None:
            fd, self._lock_fd = self._lock_fd, None
            _unlock_close(fd)

    def complete(self) -> None:
        self._book.complete(self)

class LeaseHandle:
    """A pending/bound lease flock; close never releases its durable accounting record."""

    def __init__(self, book: AdmissionBook, record: Mapping[str, object], lock_fd: int) -> None:
        self._book = book
        self.record = dict(record)
        self._lock_fd: int | None = lock_fd

    @property
    def live(self) -> bool:
        return self._lock_fd is not None

    def bind(self, container_id: str) -> Mapping[str, object]:
        return self._book.bind_lease(str(self.record["lease_id"]), str(self.record["group"]), container_id, self)

    def close(self) -> None:
        if self._lock_fd is not None:
            fd, self._lock_fd = self._lock_fd, None
            _unlock_close(fd)

@dataclass(frozen=True)
class AdmissionResources:
    planned_cores: float
    footprint: str
    declared_peak_bytes: int | None = None
    cpu_set: tuple[int, ...] | None = None


@dataclass(frozen=True)
class AdmissionRequest:
    """The /1 submit record fields used by the admission core."""

    job_id: str
    repo: str
    sha: str
    base: str | None
    project: str
    lane: str
    shard: Mapping[str, int] | None
    resources: AdmissionResources
    priority: str
    requested_by_client: str
    requested_by_version: str
    requested_by_run: str
    stores: Mapping[str, object]
    extra_argv: tuple[str, ...]
    env: Mapping[str, str]
    dev: Mapping[str, object] | None
    kind: str

    def digest(self) -> str:
        """Return the §2.2 idempotence digest, excluding job_id and client version."""

        if self.resources.cpu_set is not None:
            raise AdmissionError(ExitCode.REFUSAL, "non-null resources.cpu_set is reserved in /1")
        if self.shard is not None:
            raise AdmissionError(ExitCode.REFUSAL, "non-null shard is reserved in /1")
        document: dict[str, object] = {
            "repo": self.repo,
            "sha": self.sha,
            "base": self.base,
            "project": self.project,
            "lane": self.lane,
            "shard": None,
            "kind": self.kind,
            "priority": self.priority,
            "resources": {
                "planned_cores": self.resources.planned_cores,
                "footprint": self.resources.footprint,
                "declared_peak_bytes": self.resources.declared_peak_bytes,
                "cpu_set": None,
            },
            "stores": self.stores,
            "extra_argv": self.extra_argv,
            "env": self.env,
            "dev": self.dev,
            "requested_by": {"client": self.requested_by_client, "run": self.requested_by_run},
        }
        try:
            canonical = json.dumps(_plain_json(document), sort_keys=True, separators=(",", ":"),
                                   ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise AdmissionError(ExitCode.REFUSAL, f"request is not canonical JSON: {exc}") from exc
        return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True)
class LeaseRequest:
    lease_id: str
    container_name: str
    resources: AdmissionResources
    priority: str

    @property
    def job_id(self) -> str:
        return self.lease_id

    def digest(self) -> str:
        if self.resources.cpu_set is not None:
            raise AdmissionError(ExitCode.REFUSAL, "non-null resources.cpu_set is reserved in /1")
        document = {
            "schema": "fleet-admission-lease-request/1",
            "container_name": self.container_name,
            "footprint": self.resources.footprint,
            "planned_cores": self.resources.planned_cores,
            "priority": self.priority,
        }
        try:
            canonical = json.dumps(_plain_json(document), sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise AdmissionError(ExitCode.REFUSAL, f"lease request is not canonical JSON: {exc}") from exc
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class InspectorAnswer(Enum):
    PRESENT = "present"
    NO_SUCH_OBJECT = "no-such-object"
    ERROR = "error"


def _plain_json(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite number")
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("object keys must be strings")
        return {key: _plain_json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain_json(item) for item in value]
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def _request_group(request: AdmissionRequest, authenticated_group: str) -> str:
    _validate_name(authenticated_group, "authenticated_group")
    _validate_group_prefix(request.job_id, authenticated_group)
    return authenticated_group


def _validate_group_prefix(job_id: str, authenticated_group: str) -> None:
    _validate_name(job_id, "job_id")
    _validate_name(authenticated_group, "authenticated_group")
    prefix = f"{authenticated_group}-"
    if not job_id.startswith(prefix) or len(job_id) == len(prefix):
        raise AdmissionError(ExitCode.REFUSAL, "job_id prefix does not match the authenticated group")


def _clamp_priority(capacity: HostCapacity, authenticated_group: str, requested: str) -> str:
    if requested not in PRIORITIES:
        raise AdmissionError(ExitCode.REFUSAL, f"unknown priority: {requested!r}")
    permissions = capacity.policy.priority_classes_by_principal.require(
        "policy.priority_classes_by_principal"
    )
    if not isinstance(permissions, Mapping):
        raise AdmissionError(ExitCode.REFUSAL, "priority principal policy is invalid")
    allowed = permissions.get(authenticated_group)
    if not isinstance(allowed, (tuple, list)) or not allowed:
        raise AdmissionError(ExitCode.REFUSAL, "priority policy is missing for authenticated group")
    if any(item not in PRIORITIES for item in allowed) or len(set(allowed)) != len(allowed):
        raise AdmissionError(ExitCode.REFUSAL, "priority policy contains an invalid class")
    if requested in allowed:
        return requested
    request_index = PRIORITIES.index(requested)
    not_more_urgent = [item for item in allowed if PRIORITIES.index(item) >= request_index]
    if not not_more_urgent:
        raise AdmissionError(ExitCode.REFUSAL, "priority policy has no allowed class at or below the request")
    return min(not_more_urgent, key=PRIORITIES.index)


class AdmissionBook:
    """Durable host admission records, serialized by one queue lock."""

    def __init__(
        self,
        state_dir: Path | str,
        *,
        clock: Callable[[], float],
        waiter: Callable[[float], None],
    ) -> None:
        self.state_dir = Path(state_dir)
        self.admission_dir = self.state_dir / "admission"
        self.admitted_dir = self.admission_dir / "admitted"
        self.completed_dir = self.admission_dir / "completed"
        self.waiting_dir = self.admission_dir / "waiting"
        self.leases_dir = self.admission_dir / "leases"
        self.locks_dir = self.admission_dir / "locks"
        self.queue_lock_path = self.admission_dir / "queue.lock"
        self.clock = clock
        self.waiter = waiter
        try:
            for directory in (self.admitted_dir, self.completed_dir, self.waiting_dir, self.leases_dir, self.locks_dir):
                directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"cannot initialize admission state: {exc}") from exc

    def _queue_lock(self) -> int:
        # Blocking flock acquisition cannot return None; only nonblocking locks do.
        return cast(int, _open_lock(self.queue_lock_path, blocking=True))

    def _load_records(self) -> dict[str, dict[str, object]]:
        try:
            job_paths = sorted(path for path in self.admitted_dir.iterdir() if path.name.endswith(".json"))
            terminal_names = {path.name for path in self.completed_dir.iterdir() if path.name.endswith(".json")}
            lease_paths = sorted(path for path in self.leases_dir.iterdir() if path.name.endswith(".json"))
        except OSError as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"cannot read admission records: {exc}") from exc
        records: dict[str, dict[str, object]] = {}
        for path in job_paths:
            # A terminal record decides something only while its job is still
            # active; finished history is not read and cannot block admission.
            if path.name in terminal_names:
                terminal = _read_terminal_record(self.completed_dir / path.name, expected_id=path.stem)
                try:
                    active = _read_json_record(path, expected_id=path.stem)
                except AdmissionError:
                    # A durable terminal written before a crash resolves a stale active file.
                    continue
                if active["group"] != terminal["group"] or active["digest"] != terminal["digest"]:
                    raise AdmissionError(
                        ExitCode.QUEUED_WITHDRAWN,
                        f"admission record {path.name!r} conflicts with its terminal record; its charge is retained",
                    )
                continue
            records[f"job:{path.stem}"] = _read_json_record(path, expected_id=path.stem)
        for path in lease_paths:
            record = _read_lease_record(path, expected_id=path.stem)
            if not record["released"]:
                records[f"lease:{path.stem}"] = record
        return records

    def _record_path(self, job_id: str) -> Path:
        _validate_name(job_id, "job_id")
        return self.admitted_dir / f"{job_id}.json"

    def _terminal_path(self, job_id: str) -> Path:
        _validate_name(job_id, "job_id")
        return self.completed_dir / f"{job_id}.json"

    def _lease_path(self, lease_id: str) -> Path:
        _validate_name(lease_id, "lease_id")
        return self.leases_dir / f"{lease_id}.json"

    def _job_lock_path(self, job_id: str) -> Path:
        _validate_name(job_id, "job_id")
        return self.locks_dir / f"{job_id}.lock"

    def _waiting_path(self, job_id: str) -> Path:
        _validate_name(job_id, "job_id")
        return self.waiting_dir / f"{job_id}.json"

    def _waiting_lock_path(self, job_id: str) -> Path:
        _validate_name(job_id, "job_id")
        return self.waiting_dir / f"{job_id}.lock"

    def _existing_admission_locked(
        self, job_id: str, authenticated_group: str, digest: str
    ) -> AdmissionResult | None:
        path = self._record_path(job_id)
        if not path.exists() and not path.is_symlink():
            return None
        record = _read_json_record(path, expected_id=job_id)
        if record["group"] != authenticated_group:
            raise AdmissionError(ExitCode.REFUSAL, "job id belongs to a different authenticated group")
        if record["digest"] != digest:
            raise AdmissionError(ExitCode.REFUSAL, "job id was already used with a different request digest")
        return AdmissionResult(record, None, True)

    def _existing_terminal_locked(
        self, job_id: str, authenticated_group: str, digest: str
    ) -> AdmissionResult | None:
        path = self._terminal_path(job_id)
        if not path.exists() and not path.is_symlink():
            return None
        record = _read_terminal_record(path, expected_id=job_id)
        if record["group"] != authenticated_group:
            raise AdmissionError(ExitCode.REFUSAL, "job id belongs to a different authenticated group")
        if record["digest"] != digest:
            raise AdmissionError(ExitCode.REFUSAL, "job id was already used with a different request digest")
        return AdmissionResult(record, None, True)

    def _existing_lease_locked(
        self, lease_id: str, authenticated_group: str, digest: str
    ) -> AdmissionResult | None:
        path = self._lease_path(lease_id)
        if not path.exists() and not path.is_symlink():
            return None
        record = _read_lease_record(path, expected_id=lease_id)
        if record["group"] != authenticated_group:
            raise AdmissionError(ExitCode.REFUSAL, "lease id belongs to a different authenticated group")
        if record["digest"] != digest:
            raise AdmissionError(ExitCode.REFUSAL, "lease id was already used with a different request digest")
        return AdmissionResult(record, None, True)

    def _existing_request_locked(
        self,
        job_id: str,
        authenticated_group: str,
        digest: str,
        *,
        is_lease: bool,
    ) -> AdmissionResult | None:
        if is_lease:
            if any(path.exists() or path.is_symlink() for path in
                   (self._record_path(job_id), self._terminal_path(job_id))):
                raise AdmissionError(ExitCode.REFUSAL, "lease id collides with an admitted or completed job id")
            return self._existing_lease_locked(job_id, authenticated_group, digest)
        if self._lease_path(job_id).exists() or self._lease_path(job_id).is_symlink():
            raise AdmissionError(ExitCode.REFUSAL, "job id collides with a lease id")
        terminal = self._existing_terminal_locked(job_id, authenticated_group, digest)
        if terminal is not None:
            return terminal
        return self._existing_admission_locked(job_id, authenticated_group, digest)

    def _lease_records_locked(self) -> dict[str, dict[str, object]]:
        try:
            paths = sorted(path for path in self.leases_dir.iterdir() if path.name.endswith(".json"))
        except OSError as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"cannot read lease records: {exc}") from exc
        return {path.stem: _read_lease_record(path, expected_id=path.stem) for path in paths}

    @staticmethod
    def _inspection(inspector: Callable[[str], InspectorAnswer], target: str) -> InspectorAnswer | object:
        try:
            return inspector(target)
        except Exception:
            return InspectorAnswer.ERROR

    def _lease_name_available_locked(
        self,
        lease_id: str,
        container_name: str,
        inspector: Callable[[str], InspectorAnswer],
    ) -> None:
        for existing_id, record in self._lease_records_locked().items():
            if existing_id != lease_id and record["container_name"] == container_name:
                raise AdmissionError(ExitCode.REFUSAL, f"container name {container_name!r} is already tombstoned or leased")
        answer = self._inspection(inspector, container_name)
        if answer is InspectorAnswer.PRESENT:
            raise AdmissionError(ExitCode.REFUSAL, f"container name {container_name!r} already exists")
        if answer is not InspectorAnswer.NO_SUCH_OBJECT:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                                 f"container name {container_name!r} could not be confirmed absent")

    def _reserve_lease_locked(
        self,
        *,
        request: LeaseRequest,
        authenticated_group: str,
        digest: str,
        priority: str,
        charged_bytes: int,
    ) -> AdmissionResult:
        created_at = _nonnegative_number(self.clock(), "clock")
        lock_fd = _open_lock(self._job_lock_path(request.lease_id), blocking=False)
        if lock_fd is None:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "lease flock is held without a readable lease record")
        record: dict[str, object] = {
            "schema": "fleet-admission-lease/1",
            "lease_id": request.lease_id,
            "group": authenticated_group,
            "digest": digest,
            "container_name": request.container_name,
            "container_id": None,
            "priority": priority,
            "footprint": request.resources.footprint,
            "planned_cores": float(request.resources.planned_cores),
            "charged_bytes": charged_bytes,
            "current_warm_bytes": 0,
            "state": "pending",
            "released": False,
            "created_at": created_at,
        }
        try:
            _atomic_new_json(self._lease_path(request.lease_id), record)
        except BaseException:
            _unlock_close(lock_fd)
            raise
        return AdmissionResult(record, LeaseHandle(self, record, lock_fd), False)

    def _admit_locked(
        self,
        *,
        job_id: str,
        authenticated_group: str,
        digest: str,
        priority: str,
        footprint: str,
        planned_cores: float,
        charged_bytes: int,
    ) -> AdmissionResult:
        created_at = _nonnegative_number(self.clock(), "clock")
        lock_fd = _open_lock(self._job_lock_path(job_id), blocking=False)
        if lock_fd is None:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                                 "job flock is held without a readable admission record")
        record: dict[str, object] = {
            "schema": "fleet-admission/1",
            "job_id": job_id,
            "group": authenticated_group,
            "digest": digest,
            "priority": priority,
            "footprint": footprint,
            "planned_cores": float(planned_cores),
            "charged_bytes": charged_bytes,
            "current_warm_bytes": 0,
            "state": "admitted",
            "created_at": created_at,
        }
        try:
            _atomic_new_json(self._record_path(job_id), record)
        except BaseException:
            _unlock_close(lock_fd)
            raise
        return AdmissionResult(record, AdmissionHandle(self, record, lock_fd), False)

    def _load_waiters_locked(self) -> list[dict[str, object]]:
        try:
            paths = sorted(path for path in self.waiting_dir.iterdir() if path.name.endswith(".json"))
        except OSError as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"cannot read waiting records: {exc}") from exc
        active: list[dict[str, object]] = []
        for path in paths:
            lock_fd = _open_lock(self._waiting_lock_path(path.stem), blocking=False)
            if lock_fd is None:
                active.append(_read_waiter_record(path, expected_id=path.stem))
                continue
            try:
                path.unlink()
                _fsync_dir(self.waiting_dir)
            except OSError as exc:
                raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                                     f"cannot withdraw dead waiter {path.stem!r}: {exc}") from exc
            finally:
                _unlock_close(lock_fd)
        return active

    def _existing_waiter_locked(
        self, job_id: str, authenticated_group: str, digest: str, record_kind: str
    ) -> AdmissionResult | None:
        path = self._waiting_path(job_id)
        if not path.exists() and not path.is_symlink():
            return None
        lock_fd = _open_lock(self._waiting_lock_path(job_id), blocking=False)
        if lock_fd is not None:
            try:
                path.unlink()
                _fsync_dir(self.waiting_dir)
            except OSError as exc:
                raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                                     f"cannot withdraw dead waiter {job_id!r}: {exc}") from exc
            finally:
                _unlock_close(lock_fd)
            return None
        record = _read_waiter_record(path, expected_id=job_id)
        if record["group"] != authenticated_group:
            raise AdmissionError(ExitCode.REFUSAL, "job id belongs to a different authenticated group")
        if record["digest"] != digest:
            raise AdmissionError(ExitCode.REFUSAL, "job id was already used with a different request digest")
        if record["record_kind"] != record_kind:
            raise AdmissionError(ExitCode.REFUSAL, "job id is already queued for a different record type")
        return AdmissionResult(record, None, True)

    def _register_waiter_locked(self, record: Mapping[str, object]) -> int:
        job_id = str(record["job_id"])
        path = self._waiting_path(job_id)
        lock_fd = _open_lock(self._waiting_lock_path(job_id), blocking=False)
        if lock_fd is None:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "waiter flock is held without a waiting record")
        try:
            _atomic_json(path, record)
        except BaseException:
            _unlock_close(lock_fd)
            raise
        return lock_fd

    def _remove_waiter_locked(self, job_id: str, digest: str, authenticated_group: str) -> None:
        path = self._waiting_path(job_id)
        if not path.exists() and not path.is_symlink():
            return
        record = _read_waiter_record(path, expected_id=job_id)
        if record["group"] != authenticated_group or record["digest"] != digest:
            raise AdmissionError(ExitCode.REFUSAL, "waiting record changed before withdrawal")
        try:
            path.unlink()
            _fsync_dir(self.waiting_dir)
        except OSError as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                                 f"cannot withdraw waiter {job_id!r}: {exc}") from exc

    @staticmethod
    def _queue_key(record: Mapping[str, object]) -> tuple[int, float, str]:
        return (PRIORITIES.index(str(record["priority"])), float(record["submitted_at"]), str(record["job_id"]))

    @staticmethod
    def _sampled_warm_bytes(capacity: HostCapacity, record: Mapping[str, object]) -> int:
        samples = capacity.warm_samples_bytes_by_id
        if samples.value is None:
            return 0
        if not isinstance(samples.value, Mapping):
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                                 "capacity.warm_samples_bytes_by_id is invalid")
        admission_id = record.get("job_id", record.get("lease_id"))
        if not isinstance(admission_id, str):
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                                 "admission record has no id for warm-set sampling")
        value = samples.value.get(admission_id, 0)
        try:
            return _nonnegative_int(value, f"warm_samples_bytes_by_id[{admission_id!r}]")
        except AdmissionError as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                                 f"capacity warm sample is invalid: {exc.reason}") from exc

    @classmethod
    def _book_charges(
        cls, capacity: HostCapacity, records: Mapping[str, Mapping[str, object]]
    ) -> tuple[Charge, ...]:
        return tuple(
            Charge(
                int(record["charged_bytes"]),
                cls._sampled_warm_bytes(capacity, record),
                float(record["planned_cores"]),
            )
            for record in records.values()
        )

    def _candidate_metrics(
        self,
        candidate: Mapping[str, object],
        capacity: HostCapacity,
        records: Mapping[str, Mapping[str, object]],
        legacy_ids: tuple[str, ...],
    ) -> tuple[StaticEligibility, int, float, tuple[Charge, ...]]:
        eligibility = static_eligibility(capacity, str(candidate["footprint"]), float(candidate["planned_cores"]))
        _require_live_facts(capacity)
        charges = self._book_charges(capacity, records)
        if legacy_ids:
            legacy_bytes = _positive_int(
                capacity.policy.legacy_charge_bytes.require("policy.legacy_charge_bytes"),
                "policy.legacy_charge_bytes",
            )
            legacy_cores = _nonnegative_number(
                capacity.policy.legacy_planned_cores.require("policy.legacy_planned_cores"),
                "policy.legacy_planned_cores",
            )
            charges += tuple(Charge(legacy_bytes, 0, legacy_cores) for _item in legacy_ids)
        remaining_bytes = fit_bytes(capacity, charges)
        remaining_cores = planned_core_headroom(capacity, charges)
        return eligibility, remaining_bytes, remaining_cores, charges

    def _require_candidate_fit(
        self,
        candidate: Mapping[str, object],
        capacity: HostCapacity,
        records: Mapping[str, Mapping[str, object]],
        legacy_ids: tuple[str, ...],
    ) -> tuple[StaticEligibility, int, float, tuple[Charge, ...]]:
        metrics = self._candidate_metrics(candidate, capacity, records, legacy_ids)
        eligibility, remaining_bytes, remaining_cores, _charges = metrics
        if remaining_bytes < eligibility.charge_bytes:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                                 f"RAM fit {remaining_bytes} is below charge {eligibility.charge_bytes}")
        requested_cores = float(candidate["planned_cores"])
        if remaining_cores < requested_cores:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                                 f"planned-core headroom {remaining_cores} is below request {requested_cores}")
        return metrics

    @staticmethod
    def _legacy_job_ids(provider: Callable[[], Iterable[str]]) -> tuple[str, ...]:
        try:
            items = tuple(provider())
        except Exception as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                                 f"legacy job list unavailable: {exc}") from exc
        if any(not isinstance(item, str) or not item for item in items):
            raise AdmissionError(ExitCode.REFUSAL, "legacy job ids must be non-empty strings")
        if len(set(items)) != len(items):
            raise AdmissionError(ExitCode.REFUSAL, "legacy job list contains duplicates")
        return items

    def _candidate_fits(
        self,
        candidate: Mapping[str, object],
        capacity: HostCapacity,
        records: Mapping[str, Mapping[str, object]],
        legacy_ids: tuple[str, ...],
    ) -> tuple[bool, tuple[StaticEligibility, int, float, tuple[Charge, ...]] | None]:
        try:
            metrics = self._candidate_metrics(candidate, capacity, records, legacy_ids)
        except AdmissionError as exc:
            if exc.exit_code == ExitCode.QUEUED_WITHDRAWN:
                return False, None
            raise
        eligibility, remaining_bytes, remaining_cores, _charges = metrics
        fits = remaining_bytes >= eligibility.charge_bytes and remaining_cores >= float(candidate["planned_cores"])
        return fits, metrics

    def _queue_allows(
        self,
        candidate: Mapping[str, object],
        waiters: list[dict[str, object]],
        capacity: HostCapacity,
        records: Mapping[str, Mapping[str, object]],
        legacy_ids: tuple[str, ...],
        now: float,
        aging_s: float,
    ) -> bool:
        active_waiters = [
            dict(candidate) if row["job_id"] == candidate["job_id"] else row
            for row in waiters if not row["cancelled"]
        ]
        if all(row["job_id"] != candidate["job_id"] for row in active_waiters):
            active_waiters.append(dict(candidate))
        ordered = sorted(active_waiters, key=self._queue_key)
        candidate_fits, candidate_metrics = self._candidate_fits(candidate, capacity, records, legacy_ids)
        if not candidate_fits or candidate_metrics is None:
            return False
        candidate_key = self._queue_key(candidate)
        # The reservation belongs to the first aged waiter in queue order, whether
        # it fits now or not. Continue the scan after finding it: a fitting waiter
        # ahead of the candidate must still block the candidate, even when the
        # reservation holder sits between them in queue order.
        reservation_holder: Mapping[str, object] | None = None
        earlier_fitting: list[Mapping[str, object]] = []
        for row in ordered:
            try:
                static_eligibility(capacity, str(row["footprint"]), float(row["planned_cores"]))
            except AdmissionError:
                # Candidate feasibility already established every shared host fact;
                # a waiter-specific footprint or core request can only be refused.
                continue
            if (
                reservation_holder is None
                and now - float(row["submitted_at"]) >= aging_s
            ):
                reservation_holder = row
            if row["job_id"] != candidate["job_id"] and self._queue_key(row) < candidate_key:
                row_fits, _row_metrics = self._candidate_fits(row, capacity, records, legacy_ids)
                if row_fits:
                    earlier_fitting.append(row)

        if reservation_holder is None:
            return not earlier_fitting
        if reservation_holder["job_id"] == candidate["job_id"]:
            return all(str(row["priority"]) != "merge-gate" for row in earlier_fitting)
        return (
            str(candidate["priority"]) == "merge-gate"
            and PRIORITIES.index(str(candidate["priority"]))
            < PRIORITIES.index(str(reservation_holder["priority"]))
            and not earlier_fitting
        )

    @staticmethod
    def _aging_seconds(capacity: HostCapacity) -> float:
        value = _nonnegative_number(capacity.policy.aging_s.require("policy.aging_s"), "policy.aging_s")
        if value == 0:
            raise AdmissionError(ExitCode.REFUSAL, "policy.aging_s must be positive")
        return value

    @staticmethod
    def _wait_limit(capacity: HostCapacity, supplied: float | None) -> float:
        if supplied is not None:
            return _nonnegative_number(supplied, "wait_s")
        policy = capacity.policy.admission.require("policy.admission")
        if not isinstance(policy, Mapping) or "wait_s" not in policy:
            raise AdmissionError(ExitCode.REFUSAL, "policy.admission.wait_s is required")
        return _nonnegative_number(policy["wait_s"], "policy.admission.wait_s")

    def _expire_waiter(
        self, job_id: str, authenticated_group: str, digest: str, last_refusal: str | None
    ) -> AdmissionResult:
        queue_fd = self._queue_lock()
        try:
            self._remove_waiter_locked(job_id, digest, authenticated_group)
        finally:
            _unlock_close(queue_fd)
        reason = "wait expired; waiter withdrawn and the book confirms the job never started"
        if last_refusal is not None:
            reason = f"{reason}; last refusal: {last_refusal}"
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, reason)

    @staticmethod
    def _host_capacity(provider: Callable[[], HostCapacity]) -> HostCapacity:
        try:
            return provider()
        except AdmissionError:
            raise
        except Exception as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                                 f"host capacity unavailable: {exc}") from exc

    def submit(
        self,
        request: AdmissionRequest | LeaseRequest,
        *,
        authenticated_group: str,
        capacity_provider: Callable[[], HostCapacity],
        legacy_jobs_provider: Callable[[], Iterable[str]],
        wait_s: float | None = None,
        inspector: Callable[[str], InspectorAnswer] | None = None,
        waiter: Callable[[float], None] | None = None,
    ) -> AdmissionResult:
        """Queue one /1 request, holding its waiter flock until admission or withdrawal."""

        group = _request_group(request, authenticated_group)
        is_lease = isinstance(request, LeaseRequest)
        if is_lease:
            _validate_name(request.container_name, "container_name")
            if inspector is None:
                raise AdmissionError(ExitCode.REFUSAL, "lease admission requires an injected object inspector")
        digest = request.digest()
        requested_cores = _nonnegative_number(request.resources.planned_cores, "resources.planned_cores")
        if requested_cores == 0:
            raise AdmissionError(ExitCode.REFUSAL, "resources.planned_cores must be positive")
        if request.priority not in PRIORITIES:
            raise AdmissionError(ExitCode.REFUSAL, f"unknown priority: {request.priority!r}")

        queue_fd = self._queue_lock()
        waiter_fd: int | None = None
        try:
            record_kind = "lease" if is_lease else "job"
            existing = self._existing_request_locked(request.job_id, group, digest, is_lease=is_lease)
            if existing is not None:
                return existing
            existing_waiter = self._existing_waiter_locked(request.job_id, group, digest, record_kind)
            if existing_waiter is not None:
                return existing_waiter
            capacity = self._host_capacity(capacity_provider)
            if is_lease:
                self._lease_name_available_locked(request.lease_id, request.container_name, inspector)
            eligibility = static_eligibility(capacity, request.resources.footprint, requested_cores)
            aging_s = self._aging_seconds(capacity)
            priority = _clamp_priority(capacity, group, request.priority)
            wait_limit = self._wait_limit(capacity, wait_s)
            submitted_at = _nonnegative_number(self.clock(), "clock")
            candidate: dict[str, object] = {
                "schema": "fleet-admission-waiting/1",
                "job_id": request.job_id,
                "group": group,
                "digest": digest,
                "requested_priority": request.priority,
                "priority": priority,
                "footprint": request.resources.footprint,
                "planned_cores": requested_cores,
                "submitted_at": submitted_at,
                "state": "waiting",
                "cancelled": False,
                "record_kind": record_kind,
            }
            if wait_limit == 0:
                legacy_ids = self._legacy_job_ids(legacy_jobs_provider)
                records = self._load_records()
                waiters = self._load_waiters_locked()
                now = _nonnegative_number(self.clock(), "clock")
                eligibility, _remaining_bytes, _remaining_cores, _charges = self._require_candidate_fit(
                    candidate, capacity, records, legacy_ids
                )
                if not self._queue_allows(candidate, waiters, capacity, records, legacy_ids, now, aging_s):
                    raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                                         "immediate admission refused by capacity or an earlier queue reservation")
                if is_lease:
                    return self._reserve_lease_locked(
                        request=request,
                        authenticated_group=group,
                        digest=digest,
                        priority=priority,
                        charged_bytes=eligibility.charge_bytes,
                    )
                return self._admit_locked(
                    job_id=request.job_id,
                    authenticated_group=group,
                    digest=digest,
                    priority=priority,
                    footprint=request.resources.footprint,
                    planned_cores=requested_cores,
                    charged_bytes=eligibility.charge_bytes,
                )
            # The WAITING record and its flock are both established inside queue.lock.
            waiter_fd = self._register_waiter_locked(candidate)
        finally:
            _unlock_close(queue_fd)

        deadline = submitted_at + wait_limit
        wait_for_next_check = self.waiter if waiter is None else waiter
        # Why the latest decision did not admit; reported when the wait expires.
        last_refusal: str | None = None
        try:
            while True:
                if _nonnegative_number(self.clock(), "clock") >= deadline:
                    return self._expire_waiter(request.job_id, group, digest, last_refusal)
                queue_fd = self._queue_lock()
                try:
                    waiter_path = self._waiting_path(request.job_id)
                    waiter_record = _read_waiter_record(waiter_path, expected_id=request.job_id)
                    if waiter_record["cancelled"]:
                        self._remove_waiter_locked(request.job_id, digest, group)
                        _unlock_close(waiter_fd)
                        waiter_fd = None
                        raise AdmissionError(ExitCode.CANCELLED, "queued request was cancelled and withdrawn")
                    capacity = self._host_capacity(capacity_provider)
                    current_priority = _clamp_priority(capacity, group, request.priority)
                    legacy_ids = self._legacy_job_ids(legacy_jobs_provider)
                    if is_lease:
                        self._lease_name_available_locked(request.lease_id, request.container_name, inspector)
                    aging_s = self._aging_seconds(capacity)
                    if waiter_record["priority"] != current_priority:
                        waiter_record["priority"] = current_priority
                        _atomic_json(waiter_path, waiter_record)
                    records = self._load_records()
                    waiters = self._load_waiters_locked()
                    now = _nonnegative_number(self.clock(), "clock")
                    eligibility, _remaining_bytes, _remaining_cores, _charges = self._require_candidate_fit(
                        waiter_record, capacity, records, legacy_ids
                    )
                    if self._queue_allows(waiter_record, waiters, capacity, records, legacy_ids, now, aging_s):
                        if is_lease:
                            result = self._reserve_lease_locked(
                                request=request,
                                authenticated_group=group,
                                digest=digest,
                                priority=current_priority,
                                charged_bytes=eligibility.charge_bytes,
                            )
                        else:
                            result = self._admit_locked(
                                job_id=request.job_id,
                                authenticated_group=group,
                                digest=digest,
                                priority=current_priority,
                                footprint=request.resources.footprint,
                                planned_cores=requested_cores,
                                charged_bytes=eligibility.charge_bytes,
                            )
                        try:
                            self._remove_waiter_locked(request.job_id, digest, group)
                        except AdmissionError:
                            # The admitted record and executor flock are already durable.
                            # Releasing the waiter flock makes any leftover WAITING file dead.
                            _unlock_close(waiter_fd)
                            waiter_fd = None
                            return result
                        _unlock_close(waiter_fd)
                        waiter_fd = None
                        return result
                    last_refusal = "capacity or an earlier queue reservation"
                except AdmissionError as exc:
                    last_refusal = exc.reason
                    if exc.exit_code != ExitCode.QUEUED_WITHDRAWN:
                        self._remove_waiter_locked(request.job_id, digest, group)
                        if waiter_fd is not None:
                            _unlock_close(waiter_fd)
                            waiter_fd = None
                        raise
                finally:
                    _unlock_close(queue_fd)

                now = _nonnegative_number(self.clock(), "clock")
                if now >= deadline:
                    return self._expire_waiter(request.job_id, group, digest, last_refusal)
                try:
                    wait_for_next_check(min(QUEUE_RECHECK_S, deadline - now))
                except AdmissionError:
                    cleanup_fd = self._queue_lock()
                    try:
                        self._remove_waiter_locked(request.job_id, digest, group)
                    finally:
                        _unlock_close(cleanup_fd)
                    raise
        finally:
            if waiter_fd is not None:
                _unlock_close(waiter_fd)

    def reserve_lease(
        self,
        request: LeaseRequest,
        *,
        authenticated_group: str,
        capacity_provider: Callable[[], HostCapacity],
        legacy_jobs_provider: Callable[[], Iterable[str]],
        inspector: Callable[[str], InspectorAnswer],
        wait_s: float | None = None,
        waiter: Callable[[float], None] | None = None,
    ) -> AdmissionResult:
        """Reserve a local-run-gate lease through the same priority and wait queue."""

        return self.submit(
            request,
            authenticated_group=authenticated_group,
            capacity_provider=capacity_provider,
            legacy_jobs_provider=legacy_jobs_provider,
            wait_s=wait_s,
            inspector=inspector,
            waiter=waiter,
        )

    def bind_lease(
        self,
        lease_id: str,
        authenticated_group: str,
        container_id: str,
        handle: LeaseHandle | None = None,
    ) -> Mapping[str, object]:
        _validate_name(lease_id, "lease_id")
        _validate_name(authenticated_group, "authenticated_group")
        if not isinstance(container_id, str) or not container_id:
            raise AdmissionError(ExitCode.REFUSAL, "container id must be non-empty")
        if handle is not None and (handle._book is not self or not handle.live):
            raise AdmissionError(ExitCode.REFUSAL, "bind requires this book's live lease handle")
        queue_fd = self._queue_lock()
        try:
            path = self._lease_path(lease_id)
            if not path.exists() and not path.is_symlink():
                raise AdmissionError(ExitCode.REFUSAL, "unknown lease id")
            record = _read_lease_record(path, expected_id=lease_id)
            if record["group"] != authenticated_group:
                raise AdmissionError(ExitCode.REFUSAL, "lease id belongs to a different authenticated group")
            if record["released"] or record["state"] == "tombstone":
                raise AdmissionError(ExitCode.REFUSAL, "a revoked lease cannot be bound")
            prior = record["container_id"]
            if prior is not None and prior != container_id:
                raise AdmissionError(ExitCode.REFUSAL, "lease is already bound to a different immutable container id")
            if prior is None:
                record["container_id"] = container_id
                record["state"] = "bound"
                _atomic_json(path, record)
            if handle is not None:
                handle.record = dict(record)
            return record
        finally:
            _unlock_close(queue_fd)

    def resume_lease(
        self,
        lease_id: str,
        authenticated_group: str,
        container_id: str | None = None,
    ) -> AdmissionResult:
        """Reload a lease and reacquire its flock before a reconnect can continue."""

        _validate_name(lease_id, "lease_id")
        _validate_name(authenticated_group, "authenticated_group")
        queue_fd = self._queue_lock()
        try:
            path = self._lease_path(lease_id)
            if not path.exists() and not path.is_symlink():
                raise AdmissionError(ExitCode.REFUSAL, "unknown lease id")
            record = _read_lease_record(path, expected_id=lease_id)
            if record["group"] != authenticated_group:
                raise AdmissionError(ExitCode.REFUSAL, "lease id belongs to a different authenticated group")
            if record["released"] or record["state"] == "tombstone":
                return AdmissionResult(record, None, True)
            prior = record["container_id"]
            if container_id is not None:
                if not isinstance(container_id, str) or not container_id:
                    raise AdmissionError(ExitCode.REFUSAL, "container id must be non-empty")
                if prior is not None and prior != container_id:
                    raise AdmissionError(ExitCode.REFUSAL,
                                         "reconnect container id does not match the immutable binding")
            lock_fd = _open_lock(self._job_lock_path(lease_id), blocking=False)
            if lock_fd is None:
                return AdmissionResult(record, None, True)
            try:
                if container_id is not None and prior is None:
                    record["container_id"] = container_id
                    record["state"] = "bound"
                    _atomic_json(path, record)
                return AdmissionResult(record, LeaseHandle(self, record, lock_fd), False)
            except BaseException:
                _unlock_close(lock_fd)
                raise
        finally:
            _unlock_close(queue_fd)

    def resume_job(self, job_id: str, authenticated_group: str) -> AdmissionResult:
        """Reload an admitted job and reacquire its executor flock before recovery."""

        _validate_name(job_id, "job_id")
        _validate_name(authenticated_group, "authenticated_group")
        queue_fd = self._queue_lock()
        try:
            path = self._record_path(job_id)
            terminal_path = self._terminal_path(job_id)
            if terminal_path.exists() or terminal_path.is_symlink():
                terminal = _read_terminal_record(terminal_path, expected_id=job_id)
                if terminal["group"] != authenticated_group:
                    raise AdmissionError(ExitCode.REFUSAL, "job id belongs to a different authenticated group")
                return AdmissionResult(terminal, None, True)
            if not path.exists() and not path.is_symlink():
                raise AdmissionError(ExitCode.REFUSAL, "unknown admitted job id")
            record = _read_json_record(path, expected_id=job_id)
            if record["group"] != authenticated_group:
                raise AdmissionError(ExitCode.REFUSAL, "job id belongs to a different authenticated group")
            lock_fd = _open_lock(self._job_lock_path(job_id), blocking=False)
            if lock_fd is None:
                return AdmissionResult(record, None, True)
            return AdmissionResult(record, AdmissionHandle(self, record, lock_fd), False)
        finally:
            _unlock_close(queue_fd)

    def observe_lease(self, lease_id: str, authenticated_group: str, current_warm_bytes: int) -> Mapping[str, object]:
        _validate_name(lease_id, "lease_id")
        _validate_name(authenticated_group, "authenticated_group")
        warm = _nonnegative_int(current_warm_bytes, "current_warm_bytes")
        queue_fd = self._queue_lock()
        try:
            path = self._lease_path(lease_id)
            record = _read_lease_record(path, expected_id=lease_id)
            if record["group"] != authenticated_group:
                raise AdmissionError(ExitCode.REFUSAL, "lease id belongs to a different authenticated group")
            if record["state"] != "bound":
                raise AdmissionError(ExitCode.REFUSAL, "an unbound lease has no cgroup warm-set observation")
            record["current_warm_bytes"] = warm
            _atomic_json(path, record)
            return record
        finally:
            _unlock_close(queue_fd)

    def reconcile_lease(
        self,
        lease_id: str,
        authenticated_group: str,
        inspector: Callable[[str], InspectorAnswer],
    ) -> Mapping[str, object] | None:
        """Observe a lease; only confirmed absence tombstones and releases its charge."""

        queue_fd = self._queue_lock()
        lease_fd: int | None = None
        try:
            path = self._lease_path(lease_id)
            if not path.exists() and not path.is_symlink():
                return None
            record = _read_lease_record(path, expected_id=lease_id)
            if record["group"] != authenticated_group:
                raise AdmissionError(ExitCode.REFUSAL, "lease id belongs to a different authenticated group")
            if record["released"]:
                return record
            if record["state"] == "pending" or (
                record["state"] == "tombstone" and record["container_id"] is None
            ):
                lease_fd = _open_lock(self._job_lock_path(lease_id), blocking=False)
                if lease_fd is None:
                    return record
            target = record["container_id"] or record["container_name"]
            answer = self._inspection(inspector, str(target))
            if answer is InspectorAnswer.NO_SUCH_OBJECT:
                record["state"] = "tombstone"
                record["released"] = True
                record["last_inspection"] = answer.value
                _atomic_json(path, record)
            return record
        finally:
            if lease_fd is not None:
                _unlock_close(lease_fd)
            _unlock_close(queue_fd)

    def revoke_lease(self, lease_id: str, authenticated_group: str) -> Mapping[str, object] | None:
        """Tombstone a lease before cleanup while retaining its accounting charge."""

        _validate_name(lease_id, "lease_id")
        _validate_name(authenticated_group, "authenticated_group")
        queue_fd = self._queue_lock()
        try:
            path = self._lease_path(lease_id)
            if not path.exists() and not path.is_symlink():
                return None
            record = _read_lease_record(path, expected_id=lease_id)
            if record["group"] != authenticated_group:
                raise AdmissionError(ExitCode.REFUSAL, "lease id belongs to a different authenticated group")
            if not record["released"]:
                record["state"] = "tombstone"
                _atomic_json(path, record)
            return record
        finally:
            _unlock_close(queue_fd)

    def cancel(self, job_id: str, authenticated_group: str) -> Mapping[str, object] | None:
        """Withdraw a queued request for its owning group; admitted charges remain held."""

        _validate_name(job_id, "job_id")
        _validate_name(authenticated_group, "authenticated_group")
        queue_fd = self._queue_lock()
        try:
            admitted_path = self._record_path(job_id)
            terminal_path = self._terminal_path(job_id)
            if terminal_path.exists() or terminal_path.is_symlink():
                terminal = _read_terminal_record(terminal_path, expected_id=job_id)
                if terminal["group"] != authenticated_group:
                    raise AdmissionError(ExitCode.REFUSAL, "job id belongs to a different authenticated group")
                return terminal
            if admitted_path.exists() or admitted_path.is_symlink():
                admitted = _read_json_record(admitted_path, expected_id=job_id)
                if admitted["group"] != authenticated_group:
                    raise AdmissionError(ExitCode.REFUSAL, "job id belongs to a different authenticated group")
                return admitted
            lease_path = self._lease_path(job_id)
            if lease_path.exists() or lease_path.is_symlink():
                lease = _read_lease_record(lease_path, expected_id=job_id)
                if lease["group"] != authenticated_group:
                    raise AdmissionError(ExitCode.REFUSAL, "lease id belongs to a different authenticated group")
                return lease
            path = self._waiting_path(job_id)
            if not path.exists() and not path.is_symlink():
                return None
            waiter = _read_waiter_record(path, expected_id=job_id)
            if waiter["group"] != authenticated_group:
                raise AdmissionError(ExitCode.REFUSAL, "job id belongs to a different authenticated group")
            if waiter["cancelled"]:
                return waiter
            waiter_fd = _open_lock(self._waiting_lock_path(job_id), blocking=False)
            if waiter_fd is not None:
                try:
                    path.unlink()
                    _fsync_dir(self.waiting_dir)
                except OSError as exc:
                    raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                                         f"cannot withdraw dead waiter {job_id!r}: {exc}") from exc
                finally:
                    _unlock_close(waiter_fd)
                return None
            waiter["cancelled"] = True
            _atomic_json(path, waiter)
            return waiter
        finally:
            _unlock_close(queue_fd)

    def status(self, job_id: str, authenticated_group: str) -> Mapping[str, object] | None:
        _validate_name(job_id, "job_id")
        _validate_name(authenticated_group, "authenticated_group")
        queue_fd = self._queue_lock()
        try:
            terminal_path = self._terminal_path(job_id)
            if terminal_path.exists() or terminal_path.is_symlink():
                record = _read_terminal_record(terminal_path, expected_id=job_id)
                if record["group"] != authenticated_group:
                    raise AdmissionError(ExitCode.REFUSAL, "job id belongs to a different authenticated group")
                return record
            path = self._record_path(job_id)
            if path.exists() or path.is_symlink():
                record = _read_json_record(path, expected_id=job_id)
                if record["group"] != authenticated_group:
                    raise AdmissionError(ExitCode.REFUSAL, "job id belongs to a different authenticated group")
                return record
            lease_path = self._lease_path(job_id)
            if lease_path.exists() or lease_path.is_symlink():
                record = _read_lease_record(lease_path, expected_id=job_id)
                if record["group"] != authenticated_group:
                    raise AdmissionError(ExitCode.REFUSAL, "lease id belongs to a different authenticated group")
                return record
            waiting_path = self._waiting_path(job_id)
            if not waiting_path.exists() and not waiting_path.is_symlink():
                return None
            waiter = _read_waiter_record(waiting_path, expected_id=job_id)
            if waiter["group"] != authenticated_group:
                raise AdmissionError(ExitCode.REFUSAL, "job id belongs to a different authenticated group")
            waiter_fd = _open_lock(self._waiting_lock_path(job_id), blocking=False)
            if waiter_fd is None:
                return waiter
            try:
                waiting_path.unlink()
                _fsync_dir(self.waiting_dir)
            except OSError as exc:
                raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                                     f"cannot withdraw dead waiter {job_id!r}: {exc}") from exc
            finally:
                _unlock_close(waiter_fd)
            return None
        finally:
            _unlock_close(queue_fd)

    def is_live(self, job_id: str) -> bool:
        """Report executor liveness from the per-job flock without deleting its charge."""

        _validate_name(job_id, "job_id")
        queue_fd = self._queue_lock()
        try:
            terminal_path = self._terminal_path(job_id)
            if terminal_path.exists() or terminal_path.is_symlink():
                _read_terminal_record(terminal_path, expected_id=job_id)
                return False
            path = self._record_path(job_id)
            lease_path = self._lease_path(job_id)
            if path.exists() or path.is_symlink():
                _read_json_record(path, expected_id=job_id)
            elif lease_path.exists() or lease_path.is_symlink():
                _read_lease_record(lease_path, expected_id=job_id)
            else:
                return False
            lock_fd = _open_lock(self._job_lock_path(job_id), blocking=False)
            if lock_fd is None:
                return True
            _unlock_close(lock_fd)
            return False
        finally:
            _unlock_close(queue_fd)

    def observe_job(self, job_id: str, authenticated_group: str, current_warm_bytes: int) -> Mapping[str, object]:
        warm = _nonnegative_int(current_warm_bytes, "current_warm_bytes")
        queue_fd = self._queue_lock()
        try:
            path = self._record_path(job_id)
            record = _read_json_record(path, expected_id=job_id)
            if record["group"] != authenticated_group:
                raise AdmissionError(ExitCode.REFUSAL, "job id belongs to a different authenticated group")
            record["current_warm_bytes"] = warm
            _atomic_json(path, record)
            return record
        finally:
            _unlock_close(queue_fd)

    def complete(self, handle: AdmissionHandle) -> None:
        """Persist completion before removing the charge-bearing admission record."""

        if handle._book is not self or not handle.live:
            raise AdmissionError(ExitCode.REFUSAL, "completion requires this book's live job handle")
        job_id = str(handle.record["job_id"])
        queue_fd = self._queue_lock()
        try:
            path = self._record_path(job_id)
            terminal_path = self._terminal_path(job_id)
            if terminal_path.exists() or terminal_path.is_symlink():
                terminal = _read_terminal_record(terminal_path, expected_id=job_id)
                if terminal["group"] != handle.record["group"] or terminal["digest"] != handle.record["digest"]:
                    raise AdmissionError(ExitCode.REFUSAL, "terminal record changed before completion")
            else:
                record = _read_json_record(path, expected_id=job_id)
                if record["group"] != handle.record["group"] or record["digest"] != handle.record["digest"]:
                    raise AdmissionError(ExitCode.REFUSAL, "admission record changed before completion")
                terminal = {
                    "schema": "fleet-admission-terminal/1",
                    "job_id": job_id,
                    "group": record["group"],
                    "digest": record["digest"],
                    "state": "completed",
                    "completed_at": _nonnegative_number(self.clock(), "clock"),
                }
                _atomic_new_json(terminal_path, terminal)
            try:
                path.unlink()
                _fsync_dir(self.admitted_dir)
            except OSError:
                # The durable terminal record takes precedence over a stale active file.
                pass
            handle.close()
        finally:
            _unlock_close(queue_fd)
