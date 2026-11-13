"""Strict versioned JSON contracts used by fleetd."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


class ContractError(ValueError):
    """Input does not match a versioned fleetd contract."""


@dataclass(frozen=True)
class HostFacts:
    """Validated ``host-facts/1`` document."""

    data: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.data)


@dataclass(frozen=True)
class WorkRequest:
    """Validated ``work-request/1`` document."""

    data: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.data)


@dataclass(frozen=True)
class AssayMergeManifest:
    """Validated ``assay-merge-manifest/1`` ownership table."""

    planned_ids: tuple[str, ...]
    owners: Mapping[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "assay-merge-manifest/1",
            "planned_ids": list(self.planned_ids),
            "owners": dict(self.owners),
        }


@dataclass(frozen=True)
class GuardConfig:
    """Validated host-owned guard rule table."""

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
    """Validate the public ``[lanes.<lane>.demand]`` table."""
    obj = _object(
        value, where,
        {"unit", "instance_memory", "unit_memory", "unit_seconds_ref", "efficient_upper_bound"},
        {"unit_memory_high"},
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


def validate_host_facts(value: object) -> HostFacts:
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
        {"slice", "present", "memory_min_bytes", "cpu_weight"},
        {"memory_min_bytes_reason", "cpu_weight_reason"},
    )
    _string(control["slice"], "host-facts/1.control_plane.slice")
    _boolean(control["present"], "host-facts/1.control_plane.present")
    _nullable_int(control, "memory_min_bytes", "host-facts/1.control_plane", minimum=0)
    _nullable_int(control, "cpu_weight", "host-facts/1.control_plane", minimum=1)
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
    planned = _strings(root["planned_ids"], "work-request/1.planned_ids")
    pending = _strings(root["pending_ids"], "work-request/1.pending_ids")
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


def validate_assay_merge_manifest(value: object) -> AssayMergeManifest:
    root = _object(value, "assay-merge-manifest/1", {"schema", "planned_ids", "owners"})
    if root["schema"] != "assay-merge-manifest/1":
        raise ContractError("assay-merge-manifest/1.schema: expected 'assay-merge-manifest/1'")
    planned = _strings(root["planned_ids"], "assay-merge-manifest/1.planned_ids")
    owners = root["owners"]
    if not isinstance(owners, dict):
        raise ContractError("assay-merge-manifest/1.owners: expected object")
    for candidate_id, store in owners.items():
        _string(candidate_id, "assay-merge-manifest/1.owners candidate ID")
        _string(store, f"assay-merge-manifest/1.owners.{candidate_id}")
    if set(owners) != set(planned):
        missing = sorted(set(planned) - set(owners))
        extra = sorted(set(owners) - set(planned))
        details = []
        if missing:
            details.append(f"missing owners: {', '.join(missing)}")
        if extra:
            details.append(f"owners outside planned_ids: {', '.join(extra)}")
        raise ContractError("assay-merge-manifest/1: " + "; ".join(details))
    return AssayMergeManifest(planned, owners)


def select_owner_records(
    manifest: AssayMergeManifest,
    stores: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Select each candidate's declared owner record; non-owner copies are ignored."""
    selected: dict[str, Any] = {}
    for candidate_id in manifest.planned_ids:
        store_name = manifest.owners[candidate_id]
        store = stores.get(store_name)
        if store is None or candidate_id not in store:
            raise ContractError(
                f"assay-merge-manifest/1: owner store {store_name!r} has no record for {candidate_id!r}"
            )
        selected[candidate_id] = store[candidate_id]
    return selected


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
