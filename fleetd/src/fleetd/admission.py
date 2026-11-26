"""Host-local admission accounting primitives.

This module is deliberately stdlib-only.  Capacity facts retain both their
value and the reason they are unavailable; admission never fills in a missing
host fact with a guessed default.
"""

from __future__ import annotations

import math
import fcntl
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Generic, Iterable, Mapping, TypeVar, cast

T = TypeVar("T")

FOOTPRINTS = ("tiny", "small", "medium", "large")
PRIORITIES = ("merge-gate", "dev", "gate", "campaign")
MAX_JOB_ID_LENGTH = 128
JOB_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
GROUP_RE = JOB_ID_RE
PSI_MEMORY_FULL_REFUSE_AVG10_PCT = 3.0


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


def _validate_digest(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise AdmissionError(ExitCode.REFUSAL, "request digest must be a lowercase sha256")
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
    if not capacity.cgroup_v2_mounted.require("cgroup_v2.mounted"):
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
        capacity.policy.psi_refuse_some_avg10_pct.require("policy.psi_refuse_some_avg10_pct"),
        "policy.psi_refuse_some_avg10_pct",
    )
    if some_limit > 100:
        raise AdmissionError(ExitCode.REFUSAL, "policy.psi_refuse_some_avg10_pct exceeds 100")
    if memory_some >= some_limit:
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "memory some avg10 reached its configured refusal threshold")
    _check_guard(capacity.guard)
    _check_disk_floors(capacity)


def _check_guard(guard: GuardFacts) -> None:
    if not guard.guard_present.require("guard.guard_present"):
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
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
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
    if priority not in PRIORITIES or footprint not in FOOTPRINTS or state != "admitted":
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


def _open_lock(path: Path, *, blocking: bool) -> int | None:
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
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


@dataclass(frozen=True)
class AdmissionResult:
    record: Mapping[str, object]
    handle: AdmissionHandle | None
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

    def close(self) -> None:
        if self._lock_fd is not None:
            fd, self._lock_fd = self._lock_fd, None
            _unlock_close(fd)

    def complete(self) -> None:
        self._book.complete(self)

    def __enter__(self) -> AdmissionHandle:
        return self

    def __exit__(self, _kind: object, _value: object, _traceback: object) -> None:
        self.close()


class AdmissionBook:
    """Durable host admission records, serialized by one queue lock."""

    def __init__(self, state_dir: Path | str, *, clock: Callable[[], float]) -> None:
        self.state_dir = Path(state_dir)
        self.admission_dir = self.state_dir / "admission"
        self.admitted_dir = self.admission_dir / "admitted"
        self.waiting_dir = self.admission_dir / "waiting"
        self.leases_dir = self.admission_dir / "leases"
        self.locks_dir = self.admission_dir / "locks"
        self.queue_lock_path = self.admission_dir / "queue.lock"
        self.clock = clock
        try:
            for directory in (self.admitted_dir, self.waiting_dir, self.leases_dir, self.locks_dir):
                directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"cannot initialize admission state: {exc}") from exc

    def _queue_lock(self) -> int:
        fd = _open_lock(self.queue_lock_path, blocking=True)
        if fd is None:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "queue lock could not be acquired")
        return fd

    def _load_records(self) -> dict[str, dict[str, object]]:
        try:
            paths = sorted(path for path in self.admitted_dir.iterdir() if path.name.endswith(".json"))
        except OSError as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"cannot read admission records: {exc}") from exc
        return {path.stem: _read_json_record(path, expected_id=path.stem) for path in paths}

    def _record_path(self, job_id: str) -> Path:
        _validate_name(job_id, "job_id")
        return self.admitted_dir / f"{job_id}.json"

    def _job_lock_path(self, job_id: str) -> Path:
        _validate_name(job_id, "job_id")
        return self.locks_dir / f"{job_id}.lock"

    def admit_now(
        self,
        *,
        job_id: str,
        authenticated_group: str,
        digest: str,
        priority: str,
        footprint: str,
        planned_cores: float,
        capacity_provider: Callable[[], HostCapacity],
        legacy_jobs: Iterable[str] = (),
    ) -> AdmissionResult:
        """Make one immediate decision; timed waiting is layered on this in the queue step."""

        _validate_name(job_id, "job_id")
        _validate_name(authenticated_group, "authenticated_group")
        _validate_digest(digest)
        if priority not in PRIORITIES:
            raise AdmissionError(ExitCode.REFUSAL, f"unknown priority: {priority!r}")
        legacy_ids = tuple(legacy_jobs)
        if any(not isinstance(item, str) or not item for item in legacy_ids):
            raise AdmissionError(ExitCode.REFUSAL, "legacy job ids must be non-empty strings")
        if len(set(legacy_ids)) != len(legacy_ids):
            raise AdmissionError(ExitCode.REFUSAL, "legacy job list contains duplicates")

        queue_fd = self._queue_lock()
        try:
            record_path = self._record_path(job_id)
            if record_path.exists() or record_path.is_symlink():
                existing_record = _read_json_record(record_path, expected_id=job_id)
                if existing_record["group"] != authenticated_group:
                    raise AdmissionError(ExitCode.REFUSAL, "job id belongs to a different authenticated group")
                if existing_record["digest"] != digest:
                    raise AdmissionError(ExitCode.REFUSAL, "job id was already used with a different request digest")
                return AdmissionResult(existing_record, None, True)

            records = self._load_records()
            capacity = capacity_provider()
            eligibility = static_eligibility(capacity, footprint, planned_cores)
            _require_live_facts(capacity)
            active = tuple(
                Charge(
                    int(record["charged_bytes"]),
                    int(record["current_warm_bytes"]),
                    float(record["planned_cores"]),
                )
                for record in records.values()
            )
            legacy_charge: tuple[Charge, ...] = ()
            if legacy_ids:
                legacy_bytes = _positive_int(
                    capacity.policy.legacy_charge_bytes.require("policy.legacy_charge_bytes"),
                    "policy.legacy_charge_bytes",
                )
                legacy_charge = tuple(Charge(legacy_bytes, 0, 0) for _item in legacy_ids)
            all_charges = active + legacy_charge
            remaining_bytes = fit_bytes(capacity, all_charges)
            if remaining_bytes < eligibility.charge_bytes:
                raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                                     f"RAM fit {remaining_bytes} is below charge {eligibility.charge_bytes}")
            if legacy_ids:
                raise AdmissionError(
                    ExitCode.QUEUED_WITHDRAWN,
                    "legacy jobs have unknown planned-core use; new admission waits for their EXIT markers",
                )
            remaining_cores = planned_core_headroom(capacity, all_charges)
            if remaining_cores < float(planned_cores):
                raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                                     f"planned-core headroom {remaining_cores} is below request {planned_cores}")

            lock_fd = _open_lock(self._job_lock_path(job_id), blocking=False)
            if lock_fd is None:
                raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                                     "job flock is held without a readable admission record")
            created_at = _nonnegative_number(self.clock(), "clock")
            record: dict[str, object] = {
                "schema": "fleet-admission/1",
                "job_id": job_id,
                "group": authenticated_group,
                "digest": digest,
                "priority": priority,
                "footprint": footprint,
                "planned_cores": float(planned_cores),
                "charged_bytes": eligibility.charge_bytes,
                "current_warm_bytes": 0,
                "state": "admitted",
                "created_at": created_at,
            }
            try:
                _atomic_json(record_path, record)
            except BaseException:
                _unlock_close(lock_fd)
                raise
            return AdmissionResult(record, AdmissionHandle(self, record, lock_fd), False)
        finally:
            _unlock_close(queue_fd)

    def status(self, job_id: str, authenticated_group: str) -> Mapping[str, object] | None:
        _validate_name(job_id, "job_id")
        _validate_name(authenticated_group, "authenticated_group")
        queue_fd = self._queue_lock()
        try:
            path = self._record_path(job_id)
            if not path.exists() and not path.is_symlink():
                return None
            record = _read_json_record(path, expected_id=job_id)
            if record["group"] != authenticated_group:
                raise AdmissionError(ExitCode.REFUSAL, "job id belongs to a different authenticated group")
            return record
        finally:
            _unlock_close(queue_fd)

    def is_live(self, job_id: str) -> bool:
        """Report executor liveness from the per-job flock without deleting its charge."""

        _validate_name(job_id, "job_id")
        queue_fd = self._queue_lock()
        try:
            path = self._record_path(job_id)
            if not path.exists() and not path.is_symlink():
                return False
            _read_json_record(path, expected_id=job_id)
            lock_fd = _open_lock(self._job_lock_path(job_id), blocking=False)
            if lock_fd is None:
                return True
            _unlock_close(lock_fd)
            return False
        finally:
            _unlock_close(queue_fd)

    def complete(self, handle: AdmissionHandle) -> None:
        """Release a charge only after the executor has completed cleanup."""

        if handle._book is not self or not handle.live:
            raise AdmissionError(ExitCode.REFUSAL, "completion requires this book's live job handle")
        job_id = str(handle.record["job_id"])
        queue_fd = self._queue_lock()
        try:
            path = self._record_path(job_id)
            record = _read_json_record(path, expected_id=job_id)
            if record["group"] != handle.record["group"] or record["digest"] != handle.record["digest"]:
                raise AdmissionError(ExitCode.REFUSAL, "admission record changed before completion")
            try:
                path.unlink()
                _fsync_dir(self.admitted_dir)
            except OSError as exc:
                raise AdmissionError(ExitCode.QUEUED_WITHDRAWN,
                                     f"cannot remove completed admission record: {exc}") from exc
            handle.close()
        finally:
            _unlock_close(queue_fd)
