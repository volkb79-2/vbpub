"""Test helpers for host-facts/2 equivalents of retained host-facts/1 samples."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_NOT_COLLECTED = "not-collected: source is absent from the host-facts/1 fixture"
_UNITS = (
    "ssh.service",
    "docker.service",
    "containerd.service",
    "systemd-journald.service",
    "cgprofiled.service",
    "mdt-psi-guard.service",
)


def _wrap(value: Any, reason: str | None = None) -> dict[str, Any]:
    return {"v": None, "reason": reason or _NOT_COLLECTED} if value is None else {"v": value}


def _legacy_value(table: dict[str, Any], key: str, *, reason_key: str | None = None) -> dict[str, Any]:
    reason_key = reason_key or f"{key}_reason"
    return _wrap(table.get(key), table.get(reason_key))


def host_facts_v2(legacy: dict[str, Any]) -> dict[str, Any]:
    """Convert the checked-in /1 planner sample to equivalent /2 facts.

    The values used by the old planner map directly: online CPUs, MemTotal,
    overcommit, PSI avg10, the control-plane service rows and Geekbench data.
    New admission-only facts remain explicit nulls with fixture-source reasons.
    """
    cpu = legacy["cpu"]
    memory = legacy["memory"]
    psi = legacy["psi"]
    control = legacy["control_plane"]
    old_policy = legacy["policy"]
    speed = legacy.get("speed")
    if speed is None:
        speed_v2 = _wrap(None, legacy.get("speed_reason"))
    else:
        speed_v2 = _wrap({key: _wrap(value) for key, value in speed.items()})

    services = control.get("service_memory_min")
    service_v2: dict[str, Any] = {}
    for unit in _UNITS:
        old = services.get(unit, {}) if isinstance(services, dict) else {}
        service_v2[unit] = {
            "memory_min_bytes": _legacy_value(old, "memory_min_bytes"),
            "load_state": _legacy_value(old, "load_state"),
        }

    old_jobs = legacy.get("jobs")
    jobs_v2 = None
    if isinstance(old_jobs, list):
        jobs_v2 = []
        for job in old_jobs:
            jobs_v2.append({
                "id": _wrap(job.get("name")),
                "state": _wrap("running" if job.get("running") else "complete"),
                "requested_by": _wrap(None),
                "resources": _wrap(None),
                "started_utc": _wrap(None),
                "progress_done": _wrap(job.get("candidates_done", 0)),
                "progress_total": _wrap(job.get("candidates_total", 0)),
                "progress_unit": _wrap("candidate"),
                "last_progress_utc": _wrap(None),
            })

    psi_v2: dict[str, Any] = {}
    for resource in ("cpu", "memory", "io"):
        psi_v2[resource] = {}
        for pressure in ("some", "full"):
            for window in (10, 60, 300):
                key = f"{pressure}_avg{window}_pct"
                psi_v2[resource][key] = (
                    _legacy_value(psi[resource], key) if window == 10 else _wrap(None)
                )

    result = {
        "schema": "host-facts/2",
        "agent_version": "1.2.3",
        "collected_utc": "2026-01-01T00:00:00Z",
        "boot_id": _legacy_value(legacy, "boot_id"),
        "cpu": {
            "online": _legacy_value(cpu, "online"),
            "planning_cap": _wrap(None),
            "oversubscribe_max": _wrap(cpu["oversubscribe_max"]),
        },
        "load1": _wrap(None),
        "load5": _wrap(None),
        "load15": _wrap(None),
        "speed": speed_v2,
        "memory": {
            "total_bytes": _legacy_value(memory, "total_bytes"),
            "available_bytes": _legacy_value(memory, "available_bytes"),
            "host_reserve_bytes": _wrap(None),
            "swap_total_bytes": _legacy_value(memory, "swap_total_bytes"),
            "swap_free_bytes": _legacy_value(memory, "swap_free_bytes"),
            "overcommit": _wrap(memory["overcommit"]),
        },
        "psi": psi_v2,
        "gate_slice": {
            "memory_current_bytes": _wrap(None),
            "memory_warm_bytes": _wrap(None),
            "memory_high_bytes": _wrap(None),
            "memory_max_bytes": _wrap(None),
            "cpu_max": _wrap(None),
        },
        "disks": _wrap(None),
        "cgroup_v2": {
            "mounted": _wrap(legacy["cgroup_v2"]["mounted"]),
            "controllers": _legacy_value(legacy["cgroup_v2"], "controllers"),
        },
        "kernel_interfaces": {
            key: _legacy_value(legacy["kernel_interfaces"], key)
            for key in ("cgroup_freeze", "memory_reclaim", "memory_reclaim_swappiness_arg")
        },
        "ksm": {key: _wrap(value) for key, value in legacy["ksm"].items()},
        "control_plane": {
            "slice": _wrap(control["slice"]),
            "present": _wrap(control["present"]),
            "cpu_weight": _legacy_value(control, "cpu_weight"),
            "memory_min_bytes": _legacy_value(control, "memory_min_bytes"),
            "profile_reserve_bytes": _wrap(control.get("profile_reserve_bytes", 768 * 1024**2)),
            "service_memory_min": service_v2,
        },
        "guard": {
            "guard_present": _legacy_value(legacy["guard"], "enabled"),
            "guard_state": _wrap(
                legacy["guard"]["state"] if legacy["guard"]["state"] in {"off", "report", "enforce", "unknown"} else None,
                f"legacy guard state {legacy['guard']['state']!r} is outside the host-facts/2 vocabulary",
            ),
            "config_digest": _legacy_value(legacy["guard"], "config_digest"),
            "frozen_units": _wrap(None, "thrashguard-absent" if not legacy["guard"].get("enabled") else "not-collected"),
        },
        "freeze": {
            "since_utc": _wrap(None),
            "by": _wrap(None),
            "freeze_reason": _wrap(None),
            "queued": _wrap(None),
        },
        "policy": {
            "host_role": _legacy_value(old_policy, "host_role"),
            "psi_refuse_some_avg10": _wrap(old_policy["psi_refuse_some_avg10"]),
            "max_lane_containers": _legacy_value(old_policy, "max_lane_containers"),
            "planning_cap": _wrap(None),
            "max_footprint": _wrap(None),
            "max_planned_bytes": _wrap(None),
            "footprint_charges_bytes": _wrap(None),
            "priority_classes_by_principal": _wrap(None),
            "disk_floors": _wrap(None),
            "max_upload_bytes": _wrap(None),
            "admission": _wrap(None),
        "aging_s": _wrap(None, "not-configured: facts.toml.policy.aging_s"),
        "legacy_charge_bytes": _wrap(
            None, "not-configured: facts.toml.policy.legacy_charge_bytes",
        ),
        "legacy_planned_cores": _wrap(
            None, "not-configured: facts.toml.policy.legacy_planned_cores",
        ),
        },
        "queue": {
            "planned_cores": _wrap(None, "not-collected: admission book arrives with the host agent"),
            "charged_bytes": _wrap(None, "not-collected: admission book arrives with the host agent"),
            "waiting": _wrap(None, "not-collected: admission book arrives with the host agent"),
        },
        "jobs": _wrap(jobs_v2, legacy.get("jobs_reason", _NOT_COLLECTED)),
        "tools": _wrap(legacy.get("tools"), legacy.get("tools_reason", _NOT_COLLECTED)),
    }
    return result


def host_set_v2(legacy: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(legacy))
    for row in result["hosts"]:
        row["facts"] = host_facts_v2(row["facts"])
    return result


def write_host_set_v2(legacy_path: Path, target_path: Path) -> Path:
    legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
    target_path.write_text(json.dumps(host_set_v2(legacy)), encoding="utf-8")
    return target_path


def write_host_facts_v2(legacy_path: Path, target_path: Path) -> Path:
    legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
    target_path.write_text(json.dumps(host_facts_v2(legacy)), encoding="utf-8")
    return target_path
