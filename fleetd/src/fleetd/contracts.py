"""Strict versioned JSON contracts used by fleetd."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping


_MIB = 1024**2
DEFAULT_CONTROL_PLANE_PROFILE_RESERVE_BYTES = 768 * _MIB

# Keep the control-plane MemoryMin probe bounded and shared by facts collection,
# validation, and planning. Unit names are verified against the Debian installer
# and host-setup sources, cgprofile's native unit, and mdt-psi-guard's protected
# unit list. ssh.service is the unit used by host-setup's SSH drop-in.
CONTROL_PLANE_SERVICE_UNITS = (
    "ssh.service",
    "docker.service",
    "containerd.service",
    "systemd-journald.service",
    "cgprofiled.service",
    "mdt-psi-guard.service",
)

# Debian-install-v2 declares two of those units optional: it writes their
# drop-in and verification allows them to be absent (`LoadState=not-found` or
# `masked`), while every other listed unit must load. This set is the same
# optional set as `CONTROL_PLANE_MEMORY_MIN_OPTIONAL_UNITS` in
# scripts/debian-install-v2; a test parses that installer table and fails on
# drift, so this is a checked copy rather than a second hand-written truth.
CONTROL_PLANE_SERVICE_OPTIONAL_UNITS = frozenset({
    "cgprofiled.service",
    "mdt-psi-guard.service",
})

# Load states that mean the unit is not on this host at all. Every other state
# (including a state this collector has never seen) is NOT treated as absent: an
# unknown state must not silently contribute zero to the reserve.
CONTROL_PLANE_ABSENT_LOAD_STATES = frozenset({"not-found", "masked"})

# Documented per-service reserve defaults, in bytes. They are the measured
# floors debian-install-v2 writes as each unit's `MemoryMin` (the 2026-11-15
# hot-set measurement, 1.25 x the measured hot working set, larger value of the
# two hardware flavors) and are used only for one service whose own fact is
# unavailable. A test parses the installer's table and the documented table and
# fails when either drifts from this one.
CONTROL_PLANE_SERVICE_MEMORY_MIN_DEFAULTS = {
    "ssh.service": 10 * _MIB,
    "docker.service": 210 * _MIB,
    "containerd.service": 92 * _MIB,
    "systemd-journald.service": 24 * _MIB,
    "cgprofiled.service": 4 * _MIB,
    "mdt-psi-guard.service": 5 * _MIB,
}

# DERIVED sanity bound for one service, not a second hand-written table: a
# reported floor above this factor of that service's documented default is
# refused instead of summed. The defaults are 1.25 x a measured hot set, so 4 x
# allows that service's hot set to triple (docker/containerd are documented to
# grow with the running container count) before the value is treated as a
# misconfigured or misread unit rather than a promise. A refusal is evidence
# (the host is refused with the reason), never a clamp.
CONTROL_PLANE_SERVICE_MEMORY_MIN_CEILING_FACTOR = 4
CONTROL_PLANE_SERVICE_MEMORY_MIN_CEILINGS = {
    unit: value * CONTROL_PLANE_SERVICE_MEMORY_MIN_CEILING_FACTOR
    for unit, value in CONTROL_PLANE_SERVICE_MEMORY_MIN_DEFAULTS.items()
}

# The total's sanity bound is DERIVED from the same table: twice the documented
# floors (345 MiB x 2 = 690 MiB). Docker's and containerd's hot sets are
# documented to grow with the running container count, so a host above the
# installer's floors is legitimate; twice the floors is the point where the
# per-service drop-ins have plainly drifted away from the measured table, which
# needs a re-measurement (FD-007b), not a bigger sum. It is deliberately NOT
# the installer's 460 MiB `system.slice` ceiling: that bounds one derived
# ancestor slice, while this reserve is the sum of what each service protects,
# including `cgprofiled.service`, which lives outside `system.slice`.
# An over-bound total is refused with the reason; it is never clamped.
CONTROL_PLANE_SERVICE_MEMORY_MIN_TOTAL_CEILING_FACTOR = 2
CONTROL_PLANE_SERVICE_MEMORY_MIN_TOTAL_CEILING_BYTES = (
    sum(CONTROL_PLANE_SERVICE_MEMORY_MIN_DEFAULTS.values())
    * CONTROL_PLANE_SERVICE_MEMORY_MIN_TOTAL_CEILING_FACTOR
)


class ContractError(ValueError):
    """Input does not match a versioned fleetd contract."""


_CANDIDATE_ID = re.compile(r"[0-9a-f]{64}\Z")
_SEMVER = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?\Z"
)
_UTC_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z\Z")


def is_candidate_id(value: object) -> bool:
    """Return whether value is a native Assay lowercase SHA-256 id."""
    return isinstance(value, str) and _CANDIDATE_ID.fullmatch(value) is not None


@dataclass(frozen=True)
class HostFacts:
    """Validated versioned host-facts document."""

    data: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.data)


@dataclass(frozen=True)
class WorkRequest:
    """Validated ``work-request/1`` document."""

    data: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.data)


def _object(
    value: object,
    where: str,
    required: set[str],
    optional: set[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{where}: expected object")
    optional = optional or set()
    extra = sorted(set(value) - required - optional)
    if extra:
        raise ContractError(f"{where}: unknown field(s): {', '.join(extra)}")
    missing = sorted(required - set(value))
    if missing:
        raise ContractError(f"{where}: missing field(s): {', '.join(missing)}")
    return value


def _string(value: object, where: str, *, nonempty: bool = True) -> str:
    if not isinstance(value, str):
        raise ContractError(f"{where}: expected string")
    if nonempty and not value.strip():
        raise ContractError(f"{where}: expected non-empty string")
    return value


def _boolean(value: object, where: str) -> bool:
    if not isinstance(value, bool):
        raise ContractError(f"{where}: expected boolean")
    return value


def _integer(value: object, where: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"{where}: expected integer")
    if value < minimum:
        raise ContractError(f"{where}: must be at least {minimum}")
    return value


def _number(
    value: object,
    where: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{where}: expected number")
    try:
        number = float(value)
    except OverflowError:
        raise ContractError(f"{where}: expected finite number") from None
    if not math.isfinite(number):
        raise ContractError(f"{where}: expected finite number")
    if minimum is not None and number < minimum:
        raise ContractError(f"{where}: must be at least {minimum}")
    if maximum is not None and number > maximum:
        raise ContractError(f"{where}: must be at most {maximum}")
    return number


def _nullable(
    obj: Mapping[str, Any],
    key: str,
    reason_key: str,
    where: str,
    check: Callable[[object, str], object],
) -> object:
    value = obj[key]
    reason_present = reason_key in obj
    if value is None:
        if not reason_present:
            raise ContractError(f"{where}.{key}: null requires {reason_key}")
        reason = _string(obj[reason_key], f"{where}.{reason_key}")
        return (None, reason)
    if reason_present:
        raise ContractError(f"{where}.{reason_key}: only allowed when {key} is null")
    return (check(value, f"{where}.{key}"), None)


def _nullable_int(obj: Mapping[str, Any], key: str, where: str, *, minimum: int = 0) -> object:
    return _nullable(obj, key, f"{key}_reason", where, lambda value, path: _integer(value, path, minimum=minimum))


def _nullable_num(
    obj: Mapping[str, Any], key: str, where: str, *, minimum: float | None = None, maximum: float | None = None,
) -> object:
    return _nullable(
        obj, key, f"{key}_reason", where,
        lambda value, path: _number(value, path, minimum=minimum, maximum=maximum),
    )


def _nullable_str(obj: Mapping[str, Any], key: str, where: str) -> object:
    return _nullable(obj, key, f"{key}_reason", where, _string)


def _nullable_bool(obj: Mapping[str, Any], key: str, where: str) -> object:
    return _nullable(obj, key, f"{key}_reason", where, _boolean)


def _strings(value: object, where: str, *, unique: bool = True) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ContractError(f"{where}: expected array")
    result = tuple(_string(item, f"{where}[{index}]") for index, item in enumerate(value))
    if unique and len(set(result)) != len(result):
        raise ContractError(f"{where}: duplicate value")
    return result


def _candidate_id(value: object, where: str) -> str:
    candidate_id = _string(value, where)
    if not is_candidate_id(candidate_id):
        raise ContractError(
            f"{where}: expected a 64-character lowercase SHA-256 candidate id"
        )
    return candidate_id


def _candidate_ids(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ContractError(f"{where}: expected array")
    result = tuple(
        _candidate_id(item, f"{where}[{index}]")
        for index, item in enumerate(value)
    )
    if len(set(result)) != len(result):
        raise ContractError(f"{where}: duplicate value")
    return result


def parse_memory_bytes(value: object, where: str) -> int:
    """Parse positive byte counts and binary k/m/g suffixes used by lane TOML."""
    if isinstance(value, bool):
        raise ContractError(f"{where}: expected positive bytes or a memory string")
    if isinstance(value, int):
        return _integer(value, where, minimum=1)
    if not isinstance(value, str):
        raise ContractError(f"{where}: expected positive bytes or a memory string")
    match = re.fullmatch(r"([1-9][0-9]*)\s*(b|k|kb|kib|m|mb|mib|g|gb|gib)?", value.strip(), re.IGNORECASE)
    if match is None:
        raise ContractError(f"{where}: invalid memory value {value!r}")
    factor = {
        None: 1,
        "b": 1,
        "k": 1024,
        "kb": 1024,
        "kib": 1024,
        "m": 1024**2,
        "mb": 1024**2,
        "mib": 1024**2,
        "g": 1024**3,
        "gb": 1024**3,
        "gib": 1024**3,
    }[match.group(2).lower() if match.group(2) else None]
    return int(match.group(1)) * factor


def validate_lane_demand(value: object, where: str = "demand") -> dict[str, Any]:
    """Validate the public ``[lanes.<lane>.demand]`` table.

    ``worker_memory``/``worker_cpu`` declare the per-WORKER unit (one pytest
    run per worker) and ``instance_cpu`` the fixed per-instance charge beside
    the workers (judge, container, workspace). They are optional here so
    every pre-existing demand table keeps validating unchanged; sizing
    refuses a plan whose demand does not declare them, by name.
    """
    obj = _object(
        value, where,
        {"unit", "instance_memory", "unit_memory", "unit_seconds_ref", "efficient_upper_bound"},
        {"unit_memory_high", "baseline_seconds", "worker_memory", "worker_cpu", "instance_cpu"},
    )
    unit = _string(obj["unit"], f"{where}.unit")
    if unit not in {"candidate", "worker", "run"}:
        raise ContractError(f"{where}.unit: expected candidate, worker, or run")
    result: dict[str, Any] = {
        "unit": unit,
        "instance_memory_bytes": parse_memory_bytes(obj["instance_memory"], f"{where}.instance_memory"),
        "unit_memory_bytes": parse_memory_bytes(obj["unit_memory"], f"{where}.unit_memory"),
        "unit_seconds_ref": _number(obj["unit_seconds_ref"], f"{where}.unit_seconds_ref", minimum=0.000001),
        "efficient_upper_bound": _integer(obj["efficient_upper_bound"], f"{where}.efficient_upper_bound", minimum=1),
    }
    if "unit_memory_high" in obj:
        result["unit_memory_high_bytes"] = parse_memory_bytes(obj["unit_memory_high"], f"{where}.unit_memory_high")
        if result["unit_memory_high_bytes"] < result["unit_memory_bytes"]:
            raise ContractError(f"{where}.unit_memory_high: must be at least unit_memory")
    if "baseline_seconds" in obj:
        result["baseline_seconds"] = _number(obj["baseline_seconds"], f"{where}.baseline_seconds", minimum=0)
    if "worker_memory" in obj:
        result["worker_memory_bytes"] = parse_memory_bytes(obj["worker_memory"], f"{where}.worker_memory")
    if "worker_cpu" in obj:
        result["worker_cpu"] = _number(obj["worker_cpu"], f"{where}.worker_cpu", minimum=0.000001)
    if "instance_cpu" in obj:
        result["instance_cpu"] = _number(obj["instance_cpu"], f"{where}.instance_cpu", minimum=0)
    if "worker_memory_bytes" in result and result["worker_memory_bytes"] > result["unit_memory_bytes"]:
        # One worker's own charge cannot exceed the lane's median unit charge:
        # that would make the per-worker derivation always refuse its own host.
        raise ContractError(f"{where}.worker_memory: must not exceed unit_memory")
    return result


def _validate_psi(value: object, where: str) -> None:
    keys = {"some_avg10_pct", "full_avg10_pct"}
    obj = _object(value, where, keys, {"some_avg10_pct_reason", "full_avg10_pct_reason"})
    _nullable_num(obj, "some_avg10_pct", where, minimum=0, maximum=100)
    _nullable_num(obj, "full_avg10_pct", where, minimum=0, maximum=100)


def _validate_jobs(value: object, where: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ContractError(f"{where}: expected array")
    result = []
    names = set()
    for index, item in enumerate(value):
        path = f"{where}[{index}]"
        job = _object(
            item, path,
            {"name", "running", "exit", "progress_mtime_s", "candidates_done", "candidates_total"},
            {"exit_reason", "progress_mtime_s_reason"},
        )
        name = _string(job["name"], f"{path}.name")
        if name in names:
            raise ContractError(f"{where}: duplicate job name {name!r}")
        names.add(name)
        _boolean(job["running"], f"{path}.running")
        _nullable_int(job, "exit", path, minimum=0)
        _nullable_num(job, "progress_mtime_s", path, minimum=0)
        done = _integer(job["candidates_done"], f"{path}.candidates_done", minimum=0)
        total = _integer(job["candidates_total"], f"{path}.candidates_total", minimum=0)
        if done > total:
            raise ContractError(f"{path}.candidates_done: cannot exceed candidates_total")
        result.append(job)
    return result


def _validate_tools(value: object, where: str) -> dict[str, Any]:
    tools = _object(value, where, {"run_gate", "images"})
    _strings(tools["run_gate"], f"{where}.run_gate", unique=True)
    images = tools["images"]
    if not isinstance(images, dict):
        raise ContractError(f"{where}.images: expected object")
    for name, digest in images.items():
        _string(name, f"{where}.images name")
        _string(digest, f"{where}.images.{name}")
    return tools


def _validate_group(
    value: object,
    where: str,
    fields: Mapping[str, tuple[str, float | int | None, float | int | None]],
) -> None:
    required = set(fields)
    optional = {f"{key}_reason" for key in fields}
    obj = _object(value, where, required, optional)
    for key, (kind, minimum, maximum) in fields.items():
        if kind == "int":
            _nullable_int(obj, key, where, minimum=int(minimum or 0))
        elif kind == "number":
            _nullable_num(obj, key, where, minimum=minimum, maximum=maximum)
        elif kind == "str":
            _nullable_str(obj, key, where)
        elif kind == "bool":
            _nullable_bool(obj, key, where)
        else:
            raise ContractError(f"{where}.{key}: unsupported contract type {kind}")


def _validate_host_facts_1(value: object) -> HostFacts:
    root = _object(
        value, "host-facts/1",
        {"schema", "boot_id", "host_now_boottime_s", "cpu", "speed", "memory", "psi", "cgroup_v2", "kernel_interfaces", "ksm", "control_plane", "guard", "policy", "jobs", "tools"},
        {"boot_id_reason", "host_now_boottime_s_reason", "speed_reason", "jobs_reason", "tools_reason"},
    )
    if root["schema"] != "host-facts/1":
        raise ContractError("host-facts/1.schema: expected 'host-facts/1'")
    _nullable_str(root, "boot_id", "host-facts/1")
    _nullable_num(root, "host_now_boottime_s", "host-facts/1", minimum=0)

    cpu = _object(
        root["cpu"], "host-facts/1.cpu", {"model", "online", "for_lanes", "oversubscribe_max"},
        {"model_reason", "online_reason", "for_lanes_reason"},
    )
    _nullable_str(cpu, "model", "host-facts/1.cpu")
    _nullable_int(cpu, "online", "host-facts/1.cpu", minimum=1)
    _nullable_num(cpu, "for_lanes", "host-facts/1.cpu", minimum=0.000001)
    _number(cpu["oversubscribe_max"], "host-facts/1.cpu.oversubscribe_max", minimum=0.000001)

    speed, _speed_reason = _nullable(root, "speed", "speed_reason", "host-facts/1", lambda item, path: item)
    if speed is not None:
        speed_obj = _object(
            speed, "host-facts/1.speed",
            {"geekbench_major", "geekbench_version", "baseline_score", "single_core", "multi_core", "basis"},
        )
        major = _integer(speed_obj["geekbench_major"], "host-facts/1.speed.geekbench_major", minimum=1)
        _string(speed_obj["geekbench_version"], "host-facts/1.speed.geekbench_version")
        for name in ("baseline_score", "single_core", "multi_core"):
            _integer(speed_obj[name], f"host-facts/1.speed.{name}", minimum=1)
        basis = _string(speed_obj["basis"], "host-facts/1.speed.basis")
        if basis not in {f"geekbench{major}-measured", f"geekbench{major}-table"}:
            raise ContractError("host-facts/1.speed.basis: must name the record's Geekbench major and basis")

    memory_fields = {
        "total_bytes": ("int", 0, None), "available_bytes": ("int", 0, None),
        "for_lanes_bytes": ("int", 0, None), "swap_total_bytes": ("int", 0, None),
        "swap_free_bytes": ("int", 0, None), "swap_for_parking_bytes": ("int", 0, None),
    }
    memory = _object(
        root["memory"], "host-facts/1.memory", set(memory_fields) | {"overcommit"},
        {f"{field}_reason" for field in memory_fields},
    )
    _validate_group({key: memory[key] for key in memory_fields} | {f"{key}_reason": memory[f"{key}_reason"] for key in memory_fields if f"{key}_reason" in memory}, "host-facts/1.memory", memory_fields)
    _number(memory["overcommit"], "host-facts/1.memory.overcommit", minimum=0.000001)

    psi = _object(root["psi"], "host-facts/1.psi", {"cpu", "memory", "io"})
    for name in ("cpu", "memory", "io"):
        _validate_psi(psi[name], f"host-facts/1.psi.{name}")

    cgroup = _object(
        root["cgroup_v2"], "host-facts/1.cgroup_v2", {"mounted", "controllers"},
        {"controllers_reason"},
    )
    _boolean(cgroup["mounted"], "host-facts/1.cgroup_v2.mounted")
    controllers = cgroup["controllers"]
    if controllers is None:
        _nullable(cgroup, "controllers", "controllers_reason", "host-facts/1.cgroup_v2", lambda item, path: item)
    else:
        if "controllers_reason" in cgroup:
            raise ContractError("host-facts/1.cgroup_v2.controllers_reason: only allowed when controllers is null")
        _strings(controllers, "host-facts/1.cgroup_v2.controllers", unique=True)

    _validate_group(root["kernel_interfaces"], "host-facts/1.kernel_interfaces", {
        "cgroup_freeze": ("bool", None, None), "memory_reclaim": ("bool", None, None),
        "memory_reclaim_swappiness_arg": ("bool", None, None),
    })

    _validate_group(root["ksm"], "host-facts/1.ksm", {
        "run": ("int", 0, None), "advisor_mode": ("str", None, None), "advisor_max_cpu": ("number", 0, 100),
        "pages_sharing": ("int", 0, None), "lane_optin_shim": ("bool", None, None),
    })
    control = _object(
        root["control_plane"], "host-facts/1.control_plane",
        {"slice", "present", "cpu_weight"},
        {
            "memory_min_bytes", "memory_min_bytes_reason", "cpu_weight_reason",
            "profile_reserve_bytes", "service_memory_min", "service_memory_min_reason",
        },
    )
    _string(control["slice"], "host-facts/1.control_plane.slice")
    _boolean(control["present"], "host-facts/1.control_plane.present")
    if "memory_min_bytes" in control:
        _nullable_int(control, "memory_min_bytes", "host-facts/1.control_plane", minimum=0)
    elif "memory_min_bytes_reason" in control:
        raise ContractError(
            "host-facts/1.control_plane.memory_min_bytes_reason: only allowed when memory_min_bytes is null"
        )
    _nullable_int(control, "cpu_weight", "host-facts/1.control_plane", minimum=1)
    if "profile_reserve_bytes" in control:
        _integer(
            control["profile_reserve_bytes"],
            "host-facts/1.control_plane.profile_reserve_bytes",
            minimum=1,
        )
    if "service_memory_min" in control:
        if "service_memory_min_reason" in control:
            raise ContractError(
                "host-facts/1.control_plane.service_memory_min_reason: only allowed when service_memory_min is absent"
            )
        service_facts = _object(
            control["service_memory_min"],
            "host-facts/1.control_plane.service_memory_min",
            set(CONTROL_PLANE_SERVICE_UNITS),
        )
        for unit in CONTROL_PLANE_SERVICE_UNITS:
            path = f"host-facts/1.control_plane.service_memory_min.{unit}"
            service = _object(
                service_facts[unit], path,
                {"memory_min_bytes"}, {"memory_min_bytes_reason", "load_state", "load_state_reason"},
            )
            _nullable_int(service, "memory_min_bytes", path, minimum=0)
            if "load_state" in service:
                _nullable_str(service, "load_state", path)
    elif "service_memory_min_reason" in control:
        _string(control["service_memory_min_reason"], "host-facts/1.control_plane.service_memory_min_reason")
    _validate_group(root["guard"], "host-facts/1.guard", {
        "enabled": ("bool", None, None), "config_digest": ("str", None, None), "state": ("str", None, None),
    })
    policy = _object(
        root["policy"], "host-facts/1.policy",
        {"host_role", "psi_refuse_some_avg10", "max_lane_containers"},
        {"host_role_reason", "max_lane_containers_reason"},
    )
    host_role, _host_role_reason = _nullable_str(policy, "host_role", "host-facts/1.policy")
    if host_role is not None and host_role not in {"dedicated-dev", "shared-prod"}:
        raise ContractError(
            "host-facts/1.policy.host_role: expected 'dedicated-dev' or 'shared-prod'"
        )
    _number(policy["psi_refuse_some_avg10"], "host-facts/1.policy.psi_refuse_some_avg10", minimum=0, maximum=100)
    _nullable_int(policy, "max_lane_containers", "host-facts/1.policy", minimum=1)
    jobs, _jobs_reason = _nullable(root, "jobs", "jobs_reason", "host-facts/1", _validate_jobs)
    tools, _tools_reason = _nullable(root, "tools", "tools_reason", "host-facts/1", _validate_tools)
    return HostFacts(root)


def _wrapped(
    value: object,
    where: str,
    check: Callable[[object, str], object],
) -> object:
    """Validate a /2 value wrapper, including the reason/value exclusivity rule."""
    obj = _object(value, where, {"v"}, {"reason"})
    if obj["v"] is None:
        if "reason" not in obj:
            raise ContractError(f"{where}: null requires reason")
        _string(obj["reason"], f"{where}.reason")
        return None
    if "reason" in obj:
        raise ContractError(f"{where}.reason: only allowed when v is null")
    return check(obj["v"], f"{where}.v")


def _wrapped_configured(
    value: object,
    where: str,
    check: Callable[[object, str], object],
    *,
    unit: str,
) -> object:
    """Validate a configured fact with explicit source and unit metadata."""
    if isinstance(value, dict) and set(value) == {"v", "reason"} and value["v"] is None:
        _string(value["reason"], f"{where}.reason")
        return None
    obj = _object(value, where, {"v", "unit", "source"}, {"reason"})
    actual_unit = _string(obj["unit"], f"{where}.unit")
    if actual_unit != unit:
        raise ContractError(f"{where}.unit: expected {unit!r}")
    _string(obj["source"], f"{where}.source")
    if obj["v"] is None:
        if "reason" not in obj:
            raise ContractError(f"{where}: null requires reason")
        _string(obj["reason"], f"{where}.reason")
        return None
    if "reason" in obj:
        raise ContractError(f"{where}.reason: only allowed when v is null")
    return check(obj["v"], f"{where}.v")


def _wrapped_int(value: object, where: str, *, minimum: int = 0) -> object:
    return _wrapped(value, where, lambda item, path: _integer(item, path, minimum=minimum))


def _wrapped_num(
    value: object,
    where: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> object:
    return _wrapped(
        value, where,
        lambda item, path: _number(item, path, minimum=minimum, maximum=maximum),
    )


def _wrapped_str(value: object, where: str) -> object:
    return _wrapped(value, where, _string)


def _wrapped_bool(value: object, where: str) -> object:
    return _wrapped(value, where, _boolean)


def _wrapped_group(
    value: object,
    where: str,
    fields: Mapping[str, Callable[[object, str], object]],
) -> dict[str, Any]:
    obj = _object(value, where, set(fields))
    for key, check in fields.items():
        check(obj[key], f"{where}.{key}")
    return obj


def _plain_strings(value: object, where: str) -> object:
    return list(_strings(value, where))


def _v2_timestamp(value: object, where: str) -> str:
    text = _string(value, where)
    if _UTC_TIMESTAMP.fullmatch(text) is None:
        raise ContractError(f"{where}: expected an RFC 3339 UTC timestamp ending in Z")
    try:
        datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError:
        raise ContractError(f"{where}: expected a valid RFC 3339 UTC timestamp") from None
    return text


def _wrapped_timestamp(value: object, where: str) -> object:
    return _wrapped(value, where, _v2_timestamp)


def _v2_semver(value: object, where: str) -> str:
    text = _string(value, where)
    if _SEMVER.fullmatch(text) is None:
        raise ContractError(f"{where}: expected SemVer")
    return text


def _number_map(value: object, where: str, *, integer: bool = False) -> dict[str, int | float]:
    if not isinstance(value, dict):
        raise ContractError(f"{where}: expected object")
    result: dict[str, int | float] = {}
    for key, item in value.items():
        name = _string(key, f"{where} key")
        result[name] = (
            _integer(item, f"{where}.{name}", minimum=0)
            if integer else _number(item, f"{where}.{name}", minimum=0)
        )
    return result


def _v2_admission(value: object, where: str) -> dict[str, Any]:
    obj = _object(value, where, {"queue_path", "wait_s", "settle_s"})
    _string(obj["queue_path"], f"{where}.queue_path")
    _number(obj["wait_s"], f"{where}.wait_s", minimum=0)
    _number(obj["settle_s"], f"{where}.settle_s", minimum=0)
    return obj


def _v2_policy_object(kind: str) -> Callable[[object, str], object]:
    if kind == "footprint_charges_bytes":
        def check_charges(value: object, where: str) -> object:
            obj = _number_map(value, where, integer=True)
            if any(key not in {"tiny", "small", "medium", "large"} for key in obj):
                raise ContractError(f"{where}: expected footprint class keys")
            return obj
        return check_charges
    if kind == "priority_classes_by_principal":
        def check_priorities(value: object, where: str) -> object:
            if not isinstance(value, dict):
                raise ContractError(f"{where}: expected object")
            allowed = {"merge-gate", "dev", "gate", "campaign"}
            for principal, classes in value.items():
                _string(principal, f"{where} principal")
                values = _strings(classes, f"{where}.{principal}")
                if any(item not in allowed for item in values):
                    raise ContractError(f"{where}.{principal}: contains an unknown priority class")
            return value
        return check_priorities
    if kind == "disk_floors":
        return lambda value, where: _number_map(value, where, integer=True)
    if kind == "admission":
        return _v2_admission
    raise ContractError(f"unsupported host-facts/2 policy field {kind!r}")


def _validate_v2_speed(value: object, where: str) -> None:
    fields = {
        "geekbench_major": lambda item, path: _wrapped_int(item, path, minimum=1),
        "geekbench_version": _wrapped_str,
        "baseline_score": lambda item, path: _wrapped_int(item, path, minimum=1),
        "single_core": lambda item, path: _wrapped_int(item, path, minimum=1),
        "multi_core": lambda item, path: _wrapped_int(item, path, minimum=1),
        "basis": _wrapped_str,
    }
    speed = _wrapped(value, where, lambda item, path: _validate_wrapped_object(item, path, fields))
    if speed is not None:
        major_obj = speed["geekbench_major"]
        basis_obj = speed["basis"]
        major = major_obj["v"]
        basis = basis_obj["v"]
        if major is not None and basis is not None and basis not in {f"geekbench{major}-measured", f"geekbench{major}-table"}:
            raise ContractError(f"{where}.basis.v: must name the record's Geekbench major and basis")


def _validate_wrapped_object(
    value: object,
    where: str,
    fields: Mapping[str, Callable[[object, str], object]],
) -> dict[str, Any]:
    return _wrapped_group(value, where, fields)


def _validate_v2_psi(value: object, where: str) -> None:
    fields = {
        f"{pressure}_avg{window}_pct": lambda item, path: _wrapped_num(item, path, minimum=0, maximum=100)
        for pressure in ("some", "full")
        for window in (10, 60, 300)
    }
    _wrapped_group(value, where, fields)


def _validate_v2_cpu_max(value: object, where: str) -> object:
    def check(raw: object, path: str) -> object:
        obj = _object(raw, path, {"quota_us", "period_us"})
        quota = obj["quota_us"]
        if quota != "max":
            _integer(quota, f"{path}.quota_us", minimum=1)
        _integer(obj["period_us"], f"{path}.period_us", minimum=1)
        return obj
    return _wrapped(value, where, check)


def _validate_v2_memory_limit(value: object, where: str) -> object:
    def check(raw: object, path: str) -> object:
        if raw == "max":
            return raw
        return _integer(raw, path, minimum=0)
    return _wrapped(value, where, check)


def _validate_v2_disks(value: object, where: str) -> None:
    def check(raw: object, path: str) -> object:
        if not isinstance(raw, list):
            raise ContractError(f"{path}: expected array")
        seen: set[str] = set()
        for index, value in enumerate(raw):
            row_path = f"{path}[{index}]"
            row = _object(value, row_path, {"name", "mount", "total_bytes", "free_bytes", "fs", "floor_bytes"})
            name = _wrapped(row["name"], f"{row_path}.name", _string)
            if isinstance(name, str):
                if name in seen:
                    raise ContractError(f"{path}: duplicate disk name {name!r}")
                seen.add(name)
            _wrapped(row["mount"], f"{row_path}.mount", _string)
            _wrapped_int(row["total_bytes"], f"{row_path}.total_bytes", minimum=0)
            _wrapped_int(row["free_bytes"], f"{row_path}.free_bytes", minimum=0)
            _wrapped(row["fs"], f"{row_path}.fs", _string)
            _wrapped_int(row["floor_bytes"], f"{row_path}.floor_bytes", minimum=0)
        return raw
    _wrapped(value, where, check)


def _validate_v2_service_memory_min(value: object, where: str) -> None:
    table = _object(value, where, set(CONTROL_PLANE_SERVICE_UNITS))
    for unit in CONTROL_PLANE_SERVICE_UNITS:
        path = f"{where}.{unit}"
        row = _object(table[unit], path, {"memory_min_bytes", "load_state"})
        _wrapped_int(row["memory_min_bytes"], f"{path}.memory_min_bytes", minimum=0)
        _wrapped(row["load_state"], f"{path}.load_state", _string)


def _validate_v2_jobs(value: object, where: str) -> object:
    def check(raw: object, path: str) -> object:
        if not isinstance(raw, list):
            raise ContractError(f"{path}: expected array")
        seen: set[str] = set()
        for index, item in enumerate(raw):
            job_path = f"{path}[{index}]"
            job = _object(item, job_path, {
                "id", "state", "requested_by", "resources", "started_utc",
                "progress_done", "progress_total", "progress_unit", "last_progress_utc",
            })
            job_id = _wrapped(job["id"], f"{job_path}.id", _string)
            if isinstance(job_id, str):
                if job_id in seen:
                    raise ContractError(f"{path}: duplicate job id {job_id!r}")
                seen.add(job_id)
            _wrapped(job["state"], f"{job_path}.state", _string)
            _wrapped(job["requested_by"], f"{job_path}.requested_by", _mapping_object)
            _wrapped(job["resources"], f"{job_path}.resources", _mapping_object)
            _wrapped(job["started_utc"], f"{job_path}.started_utc", _v2_timestamp)
            _wrapped_int(job["progress_done"], f"{job_path}.progress_done", minimum=0)
            _wrapped_int(job["progress_total"], f"{job_path}.progress_total", minimum=0)
            _wrapped(job["progress_unit"], f"{job_path}.progress_unit", _string)
            _wrapped(job["last_progress_utc"], f"{job_path}.last_progress_utc", _v2_timestamp)
        return raw
    return _wrapped(value, where, check)


def _mapping_object(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{where}: expected object")
    return value


def _validate_v2_tools(value: object, where: str) -> None:
    def check(raw: object, path: str) -> object:
        tools = _object(raw, path, {"run_gate", "images"})
        _strings(tools["run_gate"], f"{path}.run_gate")
        images = tools["images"]
        if not isinstance(images, dict):
            raise ContractError(f"{path}.images: expected object")
        for name, digest in images.items():
            _string(name, f"{path}.images name")
            _string(digest, f"{path}.images.{name}")
        return tools
    _wrapped(value, where, check)


def _validate_host_facts_2(value: object) -> HostFacts:
    root = _object(
        value, "host-facts/2",
        {
            "schema", "agent_version", "collected_utc", "boot_id", "cpu", "load1", "load5", "load15",
            "speed", "memory", "psi", "gate_slice", "disks", "cgroup_v2", "kernel_interfaces", "ksm",
            "control_plane", "guard", "freeze", "policy", "queue", "jobs", "tools",
        },
    )
    if root["schema"] != "host-facts/2":
        raise ContractError("host-facts/2.schema: expected 'host-facts/2'")
    _v2_semver(root["agent_version"], "host-facts/2.agent_version")
    _v2_timestamp(root["collected_utc"], "host-facts/2.collected_utc")
    _wrapped(root["boot_id"], "host-facts/2.boot_id", _string)

    _wrapped_group(root["cpu"], "host-facts/2.cpu", {
        "online": lambda item, path: _wrapped_int(item, path, minimum=1),
        "planning_cap": lambda item, path: _wrapped_num(item, path, minimum=0),
        # Kept for this package's equivalent-plan guarantee; admission uses planning_cap later.
        "oversubscribe_max": lambda item, path: _wrapped_num(item, path, minimum=0.000001),
    })
    for name in ("load1", "load5", "load15"):
        _wrapped_num(root[name], f"host-facts/2.{name}", minimum=0)
    _validate_v2_speed(root["speed"], "host-facts/2.speed")

    _wrapped_group(root["memory"], "host-facts/2.memory", {
        "total_bytes": lambda item, path: _wrapped_int(item, path, minimum=0),
        "available_bytes": lambda item, path: _wrapped_int(item, path, minimum=0),
        "host_reserve_bytes": lambda item, path: _wrapped_int(item, path, minimum=0),
        "swap_total_bytes": lambda item, path: _wrapped_int(item, path, minimum=0),
        "swap_free_bytes": lambda item, path: _wrapped_int(item, path, minimum=0),
        # Preserves the planner's existing expected-sum policy without making it
        # depend on any of the admission-only policy keys.
        "overcommit": lambda item, path: _wrapped_num(item, path, minimum=0.000001),
    })
    psi = _object(root["psi"], "host-facts/2.psi", {"cpu", "memory", "io"})
    for name in ("cpu", "memory", "io"):
        _validate_v2_psi(psi[name], f"host-facts/2.psi.{name}")

    _wrapped_group(root["gate_slice"], "host-facts/2.gate_slice", {
        "memory_current_bytes": lambda item, path: _wrapped_int(item, path, minimum=0),
        "memory_warm_bytes": lambda item, path: _wrapped_int(item, path, minimum=0),
        "memory_high_bytes": _validate_v2_memory_limit,
        "memory_max_bytes": _validate_v2_memory_limit,
        "cpu_max": _validate_v2_cpu_max,
    })
    _validate_v2_disks(root["disks"], "host-facts/2.disks")

    _wrapped_group(root["cgroup_v2"], "host-facts/2.cgroup_v2", {
        "mounted": _wrapped_bool,
        "controllers": lambda item, path: _wrapped(item, path, _plain_strings),
    })
    _validate_wrapped_object(root["kernel_interfaces"], "host-facts/2.kernel_interfaces", {
        "cgroup_freeze": _wrapped_bool,
        "memory_reclaim": _wrapped_bool,
        "memory_reclaim_swappiness_arg": _wrapped_bool,
    })
    _validate_wrapped_object(root["ksm"], "host-facts/2.ksm", {
        "run": lambda item, path: _wrapped_int(item, path, minimum=0),
        "advisor_mode": _wrapped_str,
        "advisor_max_cpu": lambda item, path: _wrapped_num(item, path, minimum=0, maximum=100),
        "pages_sharing": lambda item, path: _wrapped_int(item, path, minimum=0),
        "lane_optin_shim": _wrapped_bool,
    })

    control = _object(root["control_plane"], "host-facts/2.control_plane", {
        "slice", "present", "cpu_weight", "memory_min_bytes", "profile_reserve_bytes", "service_memory_min",
    })
    _wrapped(control["slice"], "host-facts/2.control_plane.slice", _string)
    _wrapped(control["present"], "host-facts/2.control_plane.present", _boolean)
    _wrapped_int(control["cpu_weight"], "host-facts/2.control_plane.cpu_weight", minimum=1)
    _wrapped_int(control["memory_min_bytes"], "host-facts/2.control_plane.memory_min_bytes", minimum=0)
    _wrapped_int(control["profile_reserve_bytes"], "host-facts/2.control_plane.profile_reserve_bytes", minimum=1)
    _validate_v2_service_memory_min(control["service_memory_min"], "host-facts/2.control_plane.service_memory_min")

    guard = _object(root["guard"], "host-facts/2.guard", {"guard_present", "guard_state", "config_digest", "frozen_units"})
    _wrapped(guard["guard_present"], "host-facts/2.guard.guard_present", _boolean)
    def guard_state(value: object, where: str) -> object:
        state = _string(value, where)
        if state not in {"off", "report", "enforce", "unknown"}:
            raise ContractError(f"{where}: expected off, report, enforce, or unknown")
        return state
    _wrapped(guard["guard_state"], "host-facts/2.guard.guard_state", guard_state)
    _wrapped(guard["config_digest"], "host-facts/2.guard.config_digest", _string)
    _wrapped_int(guard["frozen_units"], "host-facts/2.guard.frozen_units", minimum=0)

    freeze = _object(root["freeze"], "host-facts/2.freeze", {"since_utc", "by", "freeze_reason", "queued"})
    _wrapped_timestamp(freeze["since_utc"], "host-facts/2.freeze.since_utc")
    _wrapped_str(freeze["by"], "host-facts/2.freeze.by")
    _wrapped_str(freeze["freeze_reason"], "host-facts/2.freeze.freeze_reason")
    _wrapped_int(freeze["queued"], "host-facts/2.freeze.queued", minimum=0)

    policy = _object(root["policy"], "host-facts/2.policy", {
        "host_role", "psi_refuse_some_avg10", "max_lane_containers", "planning_cap", "max_footprint",
        "max_planned_bytes", "footprint_charges_bytes", "priority_classes_by_principal", "disk_floors",
        "max_upload_bytes", "admission", "aging_s", "legacy_charge_bytes", "legacy_planned_cores",
    })
    def host_role(value: object, where: str) -> object:
        role = _string(value, where)
        if role not in {"dedicated-dev", "shared-prod"}:
            raise ContractError(f"{where}: expected 'dedicated-dev' or 'shared-prod'")
        return role
    _wrapped(policy["host_role"], "host-facts/2.policy.host_role", host_role)
    _wrapped_num(policy["psi_refuse_some_avg10"], "host-facts/2.policy.psi_refuse_some_avg10", minimum=0, maximum=100)
    _wrapped_int(policy["max_lane_containers"], "host-facts/2.policy.max_lane_containers", minimum=1)
    _wrapped_num(policy["planning_cap"], "host-facts/2.policy.planning_cap", minimum=0)
    def footprint(value: object, where: str) -> object:
        name = _string(value, where)
        if name not in {"tiny", "small", "medium", "large"}:
            raise ContractError(f"{where}: expected tiny, small, medium, or large")
        return name
    _wrapped(policy["max_footprint"], "host-facts/2.policy.max_footprint", footprint)
    _wrapped_int(policy["max_planned_bytes"], "host-facts/2.policy.max_planned_bytes", minimum=1)
    _wrapped(policy["footprint_charges_bytes"], "host-facts/2.policy.footprint_charges_bytes", _v2_policy_object("footprint_charges_bytes"))
    _wrapped(policy["priority_classes_by_principal"], "host-facts/2.policy.priority_classes_by_principal", _v2_policy_object("priority_classes_by_principal"))
    _wrapped(policy["disk_floors"], "host-facts/2.policy.disk_floors", _v2_policy_object("disk_floors"))
    _wrapped_int(policy["max_upload_bytes"], "host-facts/2.policy.max_upload_bytes", minimum=1)
    _wrapped(policy["admission"], "host-facts/2.policy.admission", _v2_policy_object("admission"))
    def positive_number(value: object, where: str) -> object:
        result = _number(value, where, minimum=0)
        if result <= 0:
            raise ContractError(f"{where}: must be greater than zero")
        return result
    _wrapped_configured(
        policy["aging_s"], "host-facts/2.policy.aging_s", positive_number, unit="seconds",
    )
    _wrapped_configured(
        policy["legacy_charge_bytes"], "host-facts/2.policy.legacy_charge_bytes",
        lambda item, path: _integer(item, path, minimum=1), unit="bytes",
    )
    _wrapped_configured(
        policy["legacy_planned_cores"], "host-facts/2.policy.legacy_planned_cores",
        positive_number, unit="cores",
    )

    _wrapped_group(root["queue"], "host-facts/2.queue", {
        "planned_cores": lambda item, path: _wrapped_num(item, path, minimum=0),
        "charged_bytes": lambda item, path: _wrapped_int(item, path, minimum=0),
        "waiting": lambda item, path: _wrapped_int(item, path, minimum=0),
    })
    _validate_v2_jobs(root["jobs"], "host-facts/2.jobs")
    _validate_v2_tools(root["tools"], "host-facts/2.tools")
    return HostFacts(root)


def validate_host_facts(value: object) -> HostFacts:
    """Validate ``host-facts/1`` for migration or the current ``/2`` contract."""
    if not isinstance(value, dict):
        raise ContractError("host-facts: expected object")
    schema = value.get("schema")
    if schema == "host-facts/1":
        return _validate_host_facts_1(value)
    if schema == "host-facts/2":
        return _validate_host_facts_2(value)
    raise ContractError("host-facts.schema: expected 'host-facts/1' or 'host-facts/2'")


def validate_work_request(value: object) -> WorkRequest:
    root = _object(
        value, "work-request/1",
        {"schema", "campaign_id", "lane", "commit", "image_digest", "environment_mode", "floors", "demand", "planned_ids", "pending_ids", "carry_over"},
        {"carry_over_reason"},
    )
    if root["schema"] != "work-request/1":
        raise ContractError("work-request/1.schema: expected 'work-request/1'")
    for key in ("campaign_id", "lane", "environment_mode"):
        _string(root[key], f"work-request/1.{key}")
    commit = _string(root["commit"], "work-request/1.commit")
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit) is None:
        raise ContractError("work-request/1.commit: expected a full lowercase Git object ID")
    digest = _string(root["image_digest"], "work-request/1.image_digest")
    if re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
        raise ContractError("work-request/1.image_digest: expected sha256 followed by 64 lowercase hex digits")
    floors = root["floors"]
    if not isinstance(floors, dict):
        raise ContractError("work-request/1.floors: expected object")
    for key, item in floors.items():
        _string(key, "work-request/1.floors key")
        _string(item, f"work-request/1.floors.{key}")
    validate_lane_demand(root["demand"], "work-request/1.demand")
    planned = _candidate_ids(root["planned_ids"], "work-request/1.planned_ids")
    pending = _candidate_ids(root["pending_ids"], "work-request/1.pending_ids")
    if not set(pending).issubset(planned):
        raise ContractError("work-request/1.pending_ids: every pending ID must appear in planned_ids")
    carry = root["carry_over"]
    if carry is None:
        _nullable(root, "carry_over", "carry_over_reason", "work-request/1", lambda item, path: item)
    else:
        if "carry_over_reason" in root:
            raise ContractError("work-request/1.carry_over_reason: only allowed when carry_over is null")
        carry_obj = _object(carry, "work-request/1.carry_over", {"source", "reason"})
        _string(carry_obj["source"], "work-request/1.carry_over.source")
        _string(carry_obj["reason"], "work-request/1.carry_over.reason")
    return WorkRequest(root)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def load_json(path: str | Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except UnicodeError as exc:
        raise ContractError(f"{path}: JSON is not UTF-8: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ContractError(f"{path}: invalid JSON at line {exc.lineno} column {exc.colno}: {exc.msg}") from exc


def dump_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n"
