"""Host-local admission accounting primitives.

This module is deliberately stdlib-only.  Capacity facts retain both their
value and the reason they are unavailable; admission never fills in a missing
host fact with a guessed default.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Generic, Mapping, TypeVar, cast

T = TypeVar("T")

FOOTPRINTS = ("tiny", "small", "medium", "large")
PRIORITIES = ("merge-gate", "dev", "gate", "campaign")


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
    host_reserve_bytes: Fact[int]
    footprint_charges_bytes: Fact[Mapping[str, int]]
    priority_classes_by_principal: Fact[Mapping[str, tuple[str, ...]]]
    disk_floors: Fact[Mapping[str, int]]
    psi_refuse_some_avg10_pct: Fact[float]
    admission: Fact[Mapping[str, float]]
    aging_s: Fact[float]
    legacy_charge_bytes: Fact[int]


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
    number = float(value)
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
