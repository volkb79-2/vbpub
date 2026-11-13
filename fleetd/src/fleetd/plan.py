"""Deterministic RAM-first campaign planning for the ``fleet plan`` CLI."""

from __future__ import annotations

import math
import re
import shlex
import statistics
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .contracts import ContractError, is_candidate_id, validate_host_facts, validate_lane_demand


_HOST_ID = re.compile(r"(?:\.{1,2}|[A-Za-z0-9][A-Za-z0-9_.-]{0,63})\Z")

# Design §3.6, control-plane MemoryMin default. The clamp is policy-owned and
# deliberately not an operator override in this first-week planner.
_MIB = 1024**2
_CONTROL_PLANE_MIN_BASE_BYTES = 256 * _MIB
_CONTROL_PLANE_MIN_FRACTION_NUMERATOR = 6
_CONTROL_PLANE_MIN_FRACTION_DENOMINATOR = 100
_CONTROL_PLANE_MIN_FLOOR_BYTES = 768 * _MIB
_CONTROL_PLANE_MIN_CAP_BYTES = 2 * 1024**3


@dataclass(frozen=True)
class CandidateInput:
    candidate_id: str
    source_file: str
    measured_memory_bytes: int | None
    estimated_memory_class: str
    elapsed_seconds: float | None
    measured_on_host: str | None
    memory_crash_evidence: bool
    memory_crash_retries: int


@dataclass(frozen=True)
class HostInput:
    host_id: str
    facts: dict[str, Any]
    f_h: float
    other_running: tuple[dict[str, Any], ...]


def _table(value: object, where: str, required: set[str], optional: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{where}: expected object")
    optional = optional or set()
    extra = sorted(set(value) - required - optional)
    missing = sorted(required - set(value))
    if extra:
        raise ContractError(f"{where}: unknown field(s): {', '.join(extra)}")
    if missing:
        raise ContractError(f"{where}: missing field(s): {', '.join(missing)}")
    return value


def _name(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ContractError(f"{where}: expected non-empty text without control characters")
    return value


def _host_id(value: object, where: str) -> str:
    if not isinstance(value, str) or _HOST_ID.fullmatch(value) is None:
        raise ContractError(f"{where}: expected a safe host id using letters, digits, '.', '_' or '-' ")
    if value in {".", ".."}:
        raise ContractError(f"{where}: dot path segments are not host ids")
    return value


def _control_plane_reserve(facts: Mapping[str, Any]) -> tuple[int | None, str]:
    """Return the reported or design-default control-plane memory reserve."""
    total_bytes = facts["memory"]["total_bytes"]
    if total_bytes is None:
        return None, "unknown"
    # Round 6% upward to a whole byte so the default never falls below policy.
    percentage_bytes = (
        total_bytes * _CONTROL_PLANE_MIN_FRACTION_NUMERATOR
        + _CONTROL_PLANE_MIN_FRACTION_DENOMINATOR - 1
    ) // _CONTROL_PLANE_MIN_FRACTION_DENOMINATOR
    design_default = min(
        _CONTROL_PLANE_MIN_CAP_BYTES,
        max(_CONTROL_PLANE_MIN_FLOOR_BYTES, _CONTROL_PLANE_MIN_BASE_BYTES + percentage_bytes),
    )
    control_plane = facts["control_plane"]
    reported = control_plane["memory_min_bytes"]
    if (
        control_plane["present"] is not False
        and reported is not None
        and reported >= design_default
    ):
        return reported, "reported"
    return design_default, "design-default"


def _integer(value: object, where: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"{where}: expected integer")
    if value < minimum:
        raise ContractError(f"{where}: must be at least {minimum}")
    return value


def _number(value: object, where: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
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


def parse_candidate_table(value: object) -> tuple[CandidateInput, ...]:
    """Validate ``candidate-table/1`` and return records in input order."""
    root = _table(value, "candidate-table/1", {"schema", "candidates"})
    if root["schema"] != "candidate-table/1":
        raise ContractError("candidate-table/1.schema: expected 'candidate-table/1'")
    rows = root["candidates"]
    if not isinstance(rows, list):
        raise ContractError("candidate-table/1.candidates: expected array")
    candidates: list[CandidateInput] = []
    seen: set[str] = set()
    for index, value in enumerate(rows):
        where = f"candidate-table/1.candidates[{index}]"
        row = _table(
            value, where, {"id", "file"},
            {"measured_memory_bytes", "estimated_memory_class", "elapsed_seconds", "measured_on_host", "memory_crash_evidence", "memory_crash_retries"},
        )
        candidate_id = row["id"]
        if not is_candidate_id(candidate_id):
            raise ContractError(
                f"{where}.id: expected a 64-character lowercase SHA-256 candidate id"
            )
        if candidate_id in seen:
            raise ContractError(f"candidate-table/1.candidates: duplicate id {candidate_id!r}")
        seen.add(candidate_id)
        source_file = _name(row["file"], f"{where}.file")
        memory = None if "measured_memory_bytes" not in row else _integer(row["measured_memory_bytes"], f"{where}.measured_memory_bytes", minimum=1)
        memory_class = row.get("estimated_memory_class", "low")
        if not isinstance(memory_class, str) or memory_class not in {"low", "high"}:
            raise ContractError(f"{where}.estimated_memory_class: expected low or high")
        elapsed = None if "elapsed_seconds" not in row else _number(row["elapsed_seconds"], f"{where}.elapsed_seconds", minimum=0.000001)
        measured_on = None if "measured_on_host" not in row else _host_id(row["measured_on_host"], f"{where}.measured_on_host")
        if elapsed is not None and measured_on is None:
            raise ContractError(f"{where}.elapsed_seconds: requires measured_on_host")
        if elapsed is None and measured_on is not None:
            raise ContractError(f"{where}.measured_on_host: requires elapsed_seconds")
        memory_crash = row.get("memory_crash_evidence", False)
        if not isinstance(memory_crash, bool):
            raise ContractError(f"{where}.memory_crash_evidence: expected boolean")
        retries = _integer(row.get("memory_crash_retries", 0), f"{where}.memory_crash_retries", minimum=0)
        if retries > 1:
            raise ContractError(f"{where}.memory_crash_retries: retry count cannot exceed 1")
        if retries and not memory_crash:
            raise ContractError(f"{where}.memory_crash_retries: requires memory_crash_evidence")
        candidates.append(CandidateInput(candidate_id, source_file, memory, memory_class, elapsed, measured_on, memory_crash, retries))
    return tuple(candidates)


def _parse_other_running(value: object, where: str) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        raise ContractError(f"{where}: expected array; use [] when no other jobs are running")
    jobs: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, value in enumerate(value):
        path = f"{where}[{index}]"
        job = _table(value, path, {"id", "expected_memory_bytes", "workers", "containers"})
        name = _name(job["id"], f"{path}.id")
        if name in names:
            raise ContractError(f"{where}: duplicate running job id {name!r}")
        names.add(name)
        jobs.append({
            "id": name,
            "expected_memory_bytes": _integer(job["expected_memory_bytes"], f"{path}.expected_memory_bytes", minimum=1),
            "workers": _integer(job["workers"], f"{path}.workers"),
            "containers": _integer(job["containers"], f"{path}.containers"),
        })
    return tuple(jobs)


def parse_host_set(value: object) -> tuple[HostInput, ...]:
    """Validate ``fleet-host-set/1`` including the explicit other-job budget."""
    root = _table(value, "fleet-host-set/1", {"schema", "hosts"})
    if root["schema"] != "fleet-host-set/1":
        raise ContractError("fleet-host-set/1.schema: expected 'fleet-host-set/1'")
    rows = root["hosts"]
    if not isinstance(rows, list):
        raise ContractError("fleet-host-set/1.hosts: expected array")
    hosts: list[HostInput] = []
    seen: set[str] = set()
    for index, value in enumerate(rows):
        where = f"fleet-host-set/1.hosts[{index}]"
        row = _table(value, where, {"id", "facts", "other_running"}, {"f_h"})
        host_id = _host_id(row["id"], f"{where}.id")
        if host_id in seen:
            raise ContractError(f"fleet-host-set/1.hosts: duplicate host id {host_id!r}")
        seen.add(host_id)
        facts = validate_host_facts(row["facts"]).to_dict()
        factor = _number(row.get("f_h", 1.0), f"{where}.f_h", minimum=0.5, maximum=2.0)
        other_running = _parse_other_running(row["other_running"], f"{where}.other_running")
        active_fact_jobs = facts.get("jobs")
        if active_fact_jobs is not None:
            known_running = {job["name"] for job in active_fact_jobs if job["running"]}
            supplied_running = {job["id"] for job in other_running}
            missing_jobs = sorted(known_running - supplied_running)
            if missing_jobs:
                raise ContractError(f"{where}.other_running: missing running host job(s): {', '.join(missing_jobs)}")
        hosts.append(HostInput(host_id, facts, factor, other_running))
    if not hosts:
        raise ContractError("fleet-host-set/1.hosts: at least one host is required")
    return tuple(sorted(hosts, key=lambda host: host.host_id))


def read_lane_demand_table(path: str | Path, lane: str) -> dict[str, Any]:
    """Read the raw lane demand table from the project's Run-Gate TOML."""
    try:
        document = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except OSError:
        raise
    except (tomllib.TOMLDecodeError, UnicodeError) as exc:
        raise ContractError(f"{path}: invalid Run-Gate TOML: {exc}") from exc
    lanes = document.get("lanes") if isinstance(document, dict) else None
    if not isinstance(lanes, dict) or lane not in lanes or not isinstance(lanes[lane], dict):
        raise ContractError(f"{path}: lane {lane!r} has no [lanes.{lane}.demand] table")
    if "demand" not in lanes[lane]:
        raise ContractError(f"{path}: lane {lane!r} has no [lanes.{lane}.demand] table")
    demand = lanes[lane]["demand"]
    if not isinstance(demand, dict):
        raise ContractError(f"{path}: lane {lane!r} has no [lanes.{lane}.demand] table")
    return demand


def load_lane_demand(path: str | Path, lane: str) -> dict[str, Any]:
    """Read and validate a lane's demand table from Run-Gate TOML."""
    return validate_lane_demand(read_lane_demand_table(path, lane), f"lanes.{lane}.demand")


def _median(values: list[float | int]) -> float:
    return float(statistics.median(sorted(values)))


def _resolve_memory(candidates: tuple[CandidateInput, ...], demand: dict[str, Any]) -> list[dict[str, Any]]:
    measured = [candidate.measured_memory_bytes for candidate in candidates if candidate.measured_memory_bytes is not None]
    lane_median = _median(measured) if measured else None
    file_samples: dict[str, list[float | int]] = {}
    for candidate in candidates:
        if candidate.measured_memory_bytes is not None:
            file_samples.setdefault(candidate.source_file, []).append(candidate.measured_memory_bytes)
    file_medians = {source_file: _median(samples) for source_file, samples in file_samples.items()}
    resolved: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate.measured_memory_bytes is not None:
            memory, source = float(candidate.measured_memory_bytes), "candidate_record"
        elif candidate.source_file in file_medians:
            memory, source = file_medians[candidate.source_file], "file_median"
        elif lane_median is not None:
            memory, source = lane_median, "lane_median"
        elif candidate.estimated_memory_class == "high":
            high = demand.get("unit_memory_high_bytes")
            if high is None:
                raise ContractError(
                    f"candidate {candidate.candidate_id!r}: high memory class requires demand.unit_memory_high"
                )
            memory, source = float(high), "declared_high"
        else:
            memory, source = float(demand["unit_memory_bytes"]), "declared_median"
        resolved.append({"input": candidate, "memory_bytes": memory, "memory_source": source})
    return resolved


def _speed(host: HostInput) -> dict[str, Any] | None:
    speed = host.facts["speed"]
    return speed if isinstance(speed, dict) else None


def _time_reference(hosts: tuple[HostInput, ...]) -> tuple[int | None, str | None, int | None, str | None]:
    if not hosts:
        return None, None, None, "no eligible host is available for a Geekbench reference"
    speeds = [_speed(host) for host in hosts]
    known = [speed for speed in speeds if speed is not None]
    if not known:
        return None, None, None, "no host has a Geekbench record"
    majors = {speed["geekbench_major"] for speed in known}
    if len(majors) != 1:
        return None, None, None, "host Geekbench majors differ; scores are never mixed"
    versions = {speed["geekbench_version"] for speed in known}
    if len(versions) != 1:
        return None, None, None, "host Geekbench versions differ; scores are never mixed"
    baselines = {speed["baseline_score"] for speed in known}
    if len(baselines) != 1:
        return None, None, None, "host Geekbench baseline scores differ; ETA is not comparable"
    return next(iter(majors)), next(iter(versions)), next(iter(baselines)), None


def _resolve_time(
    candidates: tuple[CandidateInput, ...],
    hosts: tuple[HostInput, ...],
    demand: dict[str, Any],
    reference_major: int | None,
    reference_version: str | None,
    reference_baseline: int | None,
) -> list[dict[str, Any]]:
    hosts_by_id = {host.host_id: host for host in hosts}
    samples: list[tuple[CandidateInput, float]] = []
    ignored_sample_reasons: dict[str, str] = {}
    for candidate in candidates:
        if candidate.elapsed_seconds is None:
            continue
        if reference_major is None or reference_version is None or reference_baseline is None:
            ignored_sample_reasons[candidate.candidate_id] = "common Geekbench reference is unavailable"
            continue
        source_host = hosts_by_id.get(candidate.measured_on_host or "")
        if source_host is None:
            ignored_sample_reasons[candidate.candidate_id] = (
                f"measurement host {candidate.measured_on_host!r} is absent from the current host set"
            )
            continue
        source_speed = _speed(source_host)
        if (
            source_speed is None
            or source_speed["geekbench_major"] != reference_major
            or source_speed["geekbench_version"] != reference_version
            or source_speed["baseline_score"] != reference_baseline
        ):
            ignored_sample_reasons[candidate.candidate_id] = (
                f"measurement host {candidate.measured_on_host!r} has no matching Geekbench reference"
            )
            continue
        normalized = candidate.elapsed_seconds * source_speed["single_core"] / source_speed["baseline_score"]
        samples.append((candidate, normalized))
    lane_median = _median([sample for _, sample in samples]) if samples else None
    file_samples: dict[str, list[float]] = {}
    own_samples = {candidate.candidate_id: sample for candidate, sample in samples}
    for candidate, sample in samples:
        file_samples.setdefault(candidate.source_file, []).append(sample)
    file_medians = {source_file: _median(values) for source_file, values in file_samples.items()}
    resolved: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate.candidate_id in own_samples:
            seconds, source = own_samples[candidate.candidate_id], "candidate_record"
        elif candidate.source_file in file_medians:
            seconds, source = file_medians[candidate.source_file], "file_median"
        elif lane_median is not None:
            seconds, source = lane_median, "lane_median"
        else:
            seconds, source = float(demand["unit_seconds_ref"]), "declared_reference"
        time_reason = ignored_sample_reasons.get(candidate.candidate_id)
        if time_reason is not None:
            time_reason = f"candidate timing sample ignored: {time_reason}; used {source}"
        resolved.append({
            "input": candidate,
            "time_ref_seconds": seconds,
            "time_source": source,
            "time_reason": time_reason,
        })
    return resolved


def _refusal(host: HostInput, other_memory: int, other_workers: int, other_containers: int) -> str | None:
    facts = host.facts
    policy = facts["policy"]
    role = policy["host_role"]
    if role is None:
        return "host role is unknown"
    if role == "shared-prod":
        return "shared-prod hosts are not eligible for campaign work"
    threshold = policy["psi_refuse_some_avg10"]
    for resource in ("memory", "io"):
        value = facts["psi"][resource]["some_avg10_pct"]
        if value is None:
            return f"{resource} PSI some avg10 is unknown"
        if value > threshold:
            return f"{resource} PSI some avg10 {value:g}% exceeds host refusal threshold {threshold:g}%"
    if facts["cgroup_v2"]["mounted"] is not True:
        return "cgroup v2 is not mounted"
    if facts["cgroup_v2"]["controllers"] is None:
        return "cgroup v2 controller list is unknown"
    if facts["memory"]["for_lanes_bytes"] is None:
        return "physical lane memory is unknown"
    reserve_bytes, _reserve_source = _control_plane_reserve(facts)
    if reserve_bytes is None:
        return "MemTotal is unknown for the control-plane memory reserve"
    physical = facts["memory"]["for_lanes_bytes"] - reserve_bytes
    if physical <= 0:
        return "control-plane memory reserve leaves no physical RAM for lanes"
    cpus = facts["cpu"]["for_lanes"]
    if cpus is None:
        return "CPU capacity for lanes is unknown"
    cpu_slots = math.floor(cpus * facts["cpu"]["oversubscribe_max"]) - other_workers
    if cpu_slots <= 0:
        return "no worker slots remain after other running jobs"
    max_containers = policy["max_lane_containers"]
    if max_containers is None:
        return "maximum lane container count is unknown"
    if max_containers - other_containers <= 0:
        return "no lane container slots remain after other running jobs"
    if facts["memory"]["overcommit"] <= 0:
        return "host memory overcommit factor is invalid"
    if facts["memory"]["overcommit"] * physical - other_memory <= 0:
        return "other running work consumes the host's overcommitted RAM budget"
    return None


def _capacity(host: HostInput, demand: dict[str, Any]) -> dict[str, Any]:
    facts = host.facts
    reserve_bytes, reserve_source = _control_plane_reserve(facts)
    other_memory = sum(job["expected_memory_bytes"] for job in host.other_running)
    other_workers = sum(job["workers"] for job in host.other_running)
    other_containers = sum(job["containers"] for job in host.other_running)
    refusal = _refusal(host, other_memory, other_workers, other_containers)
    result: dict[str, Any] = {
        "id": host.host_id,
        "status": "refused" if refusal else "available",
        "f_h": host.f_h,
        "other_running_memory_bytes": other_memory,
        "other_running_workers": other_workers,
        "other_running_containers": other_containers,
        "reserve_bytes": reserve_bytes,
        "reserve_source": reserve_source,
    }
    if refusal is not None:
        result["reason"] = refusal
        return result
    cpu_slots = math.floor(facts["cpu"]["for_lanes"] * facts["cpu"]["oversubscribe_max"]) - other_workers
    # Manual WP2 commands run one selected batch at a time per host. Parallel
    # campaign slots would overlap the same Run-Gate lane instance and Assay
    # store; WP5's scheduler owns that lifecycle.
    workers = min(cpu_slots, demand["efficient_upper_bound"])
    if workers <= 0:
        result["status"] = "refused"
        result["reason"] = "no usable worker slots remain after host policy limits"
        return result
    slots = 1
    physical = facts["memory"]["for_lanes_bytes"] - reserve_bytes
    overhead = other_memory + slots * demand["instance_memory_bytes"]
    budget = physical * facts["memory"]["overcommit"] - overhead
    if budget <= 0:
        result["status"] = "refused"
        result["reason"] = "instance and other-job charges exhaust the overcommitted RAM budget"
        return result
    if physical - overhead <= 0:
        result["status"] = "refused"
        result["reason"] = "instance and other-job charges exhaust physical lane RAM"
        return result
    result.update({
        "cpu_worker_slots": cpu_slots,
        "worker_slots": workers,
        "campaign_slots": slots,
        "speed": facts["speed"],
        "physical_bytes": physical,
        "overhead_bytes": overhead,
        "budget_bytes": budget,
        "average_worker_budget_bytes": budget / workers,
        "cap_alone_bytes": physical - overhead,
    })
    return result


def _assign_candidates(
    resolved: list[dict[str, Any]],
    host_caps: list[dict[str, Any]],
    reference_major: int | None,
    reference_version: str | None,
    reference_baseline: int | None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]], list[dict[str, Any]], list[dict[str, Any]]]:
    capacities = {host["id"]: host for host in host_caps if host["status"] == "available"}
    assignments: dict[str, list[dict[str, Any]]] = {host_id: [] for host_id in capacities}
    special_alone: dict[str, list[dict[str, Any]]] = {host_id: [] for host_id in capacities}
    retries: dict[str, list[dict[str, Any]]] = {host_id: [] for host_id in capacities}
    unplaced: list[dict[str, Any]] = []
    terminal: list[dict[str, Any]] = []
    hosts_by_id = {host["id"]: host for host in host_caps}

    for candidate in resolved:
        item = candidate["input"]
        if item.memory_crash_retries >= 1:
            terminal.append({"id": item.candidate_id, "reason": "memory-crash retry was already used; candidate is final"})
            continue
        if item.memory_crash_evidence:
            if not capacities:
                unplaced.append({"id": item.candidate_id, "reason": "no host is available for an isolated memory-crash retry"})
                continue
            retry_host = max(capacities.values(), key=lambda host: (host["cap_alone_bytes"], _reverse_id(host["id"])))
            if candidate["memory_bytes"] > retry_host["cap_alone_bytes"]:
                unplaced.append({
                    "id": item.candidate_id,
                    "reason": f"memory-crash retry needs {candidate['memory_bytes']:g} bytes; largest alone cap is {retry_host['cap_alone_bytes']:g} bytes",
                })
            elif candidate["memory_bytes"] > retry_host["budget_bytes"]:
                unplaced.append({
                    "id": item.candidate_id,
                    "reason": f"memory-crash retry needs {candidate['memory_bytes']:g} bytes; largest-cap host sum budget is {retry_host['budget_bytes']:g} bytes",
                })
            else:
                retries[retry_host["id"]].append(candidate)

    normal = [candidate for candidate in resolved if not candidate["input"].memory_crash_evidence and candidate["input"].memory_crash_retries == 0]
    if capacities:
        min_budget = min(host["average_worker_budget_bytes"] for host in capacities.values())
        for candidate in resolved:
            candidate["ram_class"] = "low" if candidate["memory_bytes"] <= min_budget else "high"
        ordered: list[dict[str, Any]] = []
        for candidate in normal:
            item = candidate["input"]
            candidate["eligible_hosts"] = sorted(
                host_id for host_id, host in capacities.items() if candidate["memory_bytes"] <= host["cap_bytes"]
            )
            ordered.append(candidate)
        ordered.sort(key=lambda candidate: (len(candidate["eligible_hosts"]), -candidate["time_ref_seconds"], candidate["input"].candidate_id))
        for candidate in ordered:
            memory = candidate["memory_bytes"]
            eligible = [capacities[host_id] for host_id in candidate["eligible_hosts"]]
            if not eligible:
                selected = max(
                    capacities.values(),
                    key=lambda host: (host["cap_alone_bytes"], _reverse_id(host["id"])),
                )
                if memory <= selected["cap_alone_bytes"] and memory <= selected["budget_bytes"]:
                    candidate["assignment_kind"] = "alone"
                    candidate["mix_flag_reason"] = "candidate exceeds every shared cap and is assigned alone"
                    special_alone[selected["id"]].append(candidate)
                else:
                    max_alone = max(host["cap_alone_bytes"] for host in capacities.values())
                    max_budget = max(host["budget_bytes"] for host in capacities.values())
                    unplaced.append({
                        "id": candidate["input"].candidate_id,
                        "reason": (
                            f"requires {memory:g} bytes; largest physical alone cap is {max_alone:g} bytes "
                            f"and largest host sum budget is {max_budget:g} bytes"
                        ),
                    })
                continue
            mixes = [host for host in eligible if _mean([item["memory_bytes"] for item in assignments[host["id"]]] + [memory]) <= host["average_worker_budget_bytes"]]
            mix_flag_reason: str | None = None
            choices = eligible
            if mixes:
                choices = mixes
            else:
                mix_flag_reason = "no eligible host keeps the assigned-list mean within its average RAM budget"
                choices = [max(eligible, key=lambda host: (host["average_worker_budget_bytes"], _reverse_id(host["id"]))) ]
            finishes = {
                host["id"]: _projected_finish(
                    host, assignments[host["id"]], candidate, reference_major, reference_version, reference_baseline
                )
                for host in choices
            }
            assignment_reason: str | None = None
            if all(value is not None for value in finishes.values()):
                selected = min(choices, key=lambda host: (finishes[host["id"]], host["id"]))
            else:
                # With no comparable Geekbench scores, distribute reference
                # work by worker capacity. This is only a placement tie-break;
                # it is not reported as an ETA or a host speed comparison.
                selected = min(
                    choices,
                    key=lambda host: (
                        (
                            sum(item["time_ref_seconds"] for item in assignments[host["id"]])
                            + candidate["time_ref_seconds"]
                        ) / host["worker_slots"],
                        host["id"],
                    ),
                )
                assignment_reason = (
                    "ETA unavailable; balanced normalized reference work per worker"
                )
            candidate["assignment_kind"] = "shared"
            candidate["mix_flag_reason"] = mix_flag_reason
            candidate["assignment_reason"] = assignment_reason
            assignments[selected["id"]].append(candidate)
    else:
        for candidate in normal:
            unplaced.append({"id": candidate["input"].candidate_id, "reason": "no host is available for placement"})

    combined_specials = {
        host_id: [*special_alone[host_id], *retries[host_id]]
        for host_id in capacities
    }
    return assignments, combined_specials, unplaced, terminal


def _reverse_id(value: str) -> tuple[int, ...]:
    """Reverse code points for max; a prefix tie prefers the longer id."""
    return tuple(-ord(char) for char in value)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _host_time(
    host: Mapping[str, Any], seconds_ref: float, reference_major: int | None,
    reference_version: str | None, reference_baseline: int | None,
) -> float | None:
    speed = host.get("speed")
    if not isinstance(speed, dict) or reference_major is None or reference_version is None or reference_baseline is None:
        return None
    if (
        speed["geekbench_major"] != reference_major
        or speed["geekbench_version"] != reference_version
        or speed["baseline_score"] != reference_baseline
    ):
        return None
    return seconds_ref * reference_baseline / speed["single_core"] * host["f_h"]


def _projected_finish(
    host: Mapping[str, Any], existing: list[dict[str, Any]], candidate: dict[str, Any],
    reference_major: int | None, reference_version: str | None, reference_baseline: int | None,
) -> float | None:
    times = [
        _host_time(host, item["time_ref_seconds"], reference_major, reference_version, reference_baseline)
        for item in [*existing, candidate]
    ]
    if any(value is None for value in times):
        return None
    return sum(value for value in times if value is not None) / host["worker_slots"]


def _ordered_mix(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    high = sorted(
        (candidate for candidate in candidates if candidate["ram_class"] == "high"),
        key=lambda candidate: (-candidate["memory_bytes"], -candidate["time_ref_seconds"], candidate["input"].candidate_id),
    )
    low = sorted(
        (candidate for candidate in candidates if candidate["ram_class"] == "low"),
        key=lambda candidate: (candidate["time_ref_seconds"], candidate["memory_bytes"], candidate["input"].candidate_id),
    )
    result: list[dict[str, Any]] = []
    while high or low:
        if high:
            result.append(high.pop(0))
        if low:
            result.append(low.pop(0))
    return result


def _waves(candidates: list[dict[str, Any]], host: dict[str, Any]) -> list[dict[str, Any]]:
    remaining = list(candidates)
    result: list[dict[str, Any]] = []
    worker_slots = host["worker_slots"]
    budget = host["budget_bytes"]
    average_budget = host["average_worker_budget_bytes"]
    capacities = host["slot_worker_capacities"]
    while remaining:
        wave: list[dict[str, Any]] = []
        charged = 0.0
        deferred: list[dict[str, Any]] = []
        for candidate in remaining:
            memory = candidate["memory_bytes"]
            if len(wave) < worker_slots and charged + memory <= budget:
                wave.append(candidate)
                charged += memory
            else:
                deferred.append(candidate)
        if not wave:
            raise ContractError(f"planner invariant: candidate {remaining[0]['input'].candidate_id!r} cannot fit a worker wave")
        mix_holds = _mean([candidate["memory_bytes"] for candidate in wave]) <= average_budget
        slot_batches: list[dict[str, Any]] = []
        offset = 0
        for slot, worker_capacity in enumerate(capacities):
            ids = [candidate["input"].candidate_id for candidate in wave[offset:offset + worker_capacity]]
            offset += worker_capacity
            if ids:
                slot_batches.append({"slot": slot, "worker_capacity": worker_capacity, "candidate_ids": ids})
        wave_result = {
            "candidate_ids": [candidate["input"].candidate_id for candidate in wave],
            "expected_charge_bytes": charged,
            "budget_bytes": budget,
            "mix_holds": mix_holds,
            "slot_batches": slot_batches,
        }
        if not mix_holds:
            wave_result["mix_flag_reason"] = "wave mean exceeds average RAM budget"
        result.append(wave_result)
        remaining = deferred
    return result


def _batch_groups(candidates: list[dict[str, Any]], host: dict[str, Any], multiplier: float) -> list[dict[str, Any]]:
    ordered = _ordered_mix(candidates)
    target = max(1, math.ceil(multiplier * host["worker_slots"]))
    groups: list[dict[str, Any]] = []
    for start in range(0, len(ordered), target):
        selected = ordered[start:start + target]
        groups.append({
            "batch": len(groups),
            "candidate_ids": [item["input"].candidate_id for item in selected],
            "target_candidate_count": target,
            "waves": _waves(selected, host),
        })
    return groups


def _single_group(candidate: dict[str, Any], host: dict[str, Any], *, retry: bool) -> dict[str, Any]:
    item = candidate["input"]
    charge = candidate["memory_bytes"]
    physical_cap = host["cap_alone_bytes"]
    slot_batch = {"slot": 0, "worker_capacity": 1 if retry else host["slot_worker_capacities"][0], "candidate_ids": [item.candidate_id]}
    wave = {
        "candidate_ids": [item.candidate_id],
        "expected_charge_bytes": charge,
        "budget_bytes": host["budget_bytes"],
        "physical_alone_cap_bytes": physical_cap,
        "mix_holds": charge <= host["average_worker_budget_bytes"],
        "retry": retry,
        "slot_batches": [slot_batch],
    }
    if charge > host["average_worker_budget_bytes"]:
        wave["mix_flag_reason"] = (
            "isolated retry exceeds the average RAM budget; its physical alone cap still holds"
            if retry else candidate.get("mix_flag_reason", "isolated candidate exceeds the average RAM budget")
        )
    if retry:
        wave["retry_reason"] = "memory-crash retry must run alone with --jobs 1"
    return {
        "batch": 0,
        "kind": "retry" if retry else "alone",
        "candidate_ids": [item.candidate_id],
        "target_candidate_count": 1,
        "waves": [wave],
    }


def _host_eta(
    host: dict[str, Any],
    candidates: list[dict[str, Any]],
    reference_major: int | None,
    reference_version: str | None,
    reference_baseline: int | None,
) -> tuple[float | None, str | None]:
    if not candidates:
        return 0.0, None
    if reference_major is None or reference_version is None or reference_baseline is None:
        return None, "common Geekbench reference is unavailable"
    candidate_times = [
        _host_time(host, candidate["time_ref_seconds"], reference_major, reference_version, reference_baseline)
        for candidate in candidates
    ]
    if any(value is None for value in candidate_times):
        return None, "host Geekbench score is unavailable or uses a different major/version"
    parallel_times: list[float] = []
    isolated_times: list[float] = []
    for candidate, value in zip(candidates, candidate_times, strict=True):
        actual_time = value
        isolated = candidate["input"].memory_crash_evidence or candidate.get("assignment_kind") == "alone"
        (isolated_times if isolated else parallel_times).append(actual_time)
    eta = sum(parallel_times) / host["worker_slots"] + max(parallel_times, default=0.0) + sum(isolated_times)
    return eta, None


def plan_campaign(
    lane: str,
    candidate_table: object,
    host_set: object,
    demand_table: object,
    *,
    batch_multiplier: float = 4.0,
) -> dict[str, Any]:
    """Return a deterministic RAM-first plan from three validated inputs."""
    if not isinstance(lane, str) or not lane.strip():
        raise ContractError("lane: expected non-empty string")
    multiplier = _number(batch_multiplier, "batch_multiplier", minimum=0.1)
    candidates = parse_candidate_table(candidate_table)
    hosts = parse_host_set(host_set)
    demand = validate_lane_demand(demand_table)
    if demand["unit"] != "candidate":
        raise ContractError("demand.unit: fleet plan currently requires 'candidate'")
    capacity_rows = [_capacity(host, demand) for host in hosts]
    available_ids = {row["id"] for row in capacity_rows if row["status"] == "available"}
    eligible_hosts = tuple(host for host in hosts if host.host_id in available_ids)
    reference_major, reference_version, reference_baseline, eta_basis_reason = _time_reference(eligible_hosts)
    memory = _resolve_memory(candidates, demand)
    time = _resolve_time(candidates, hosts, demand, reference_major, reference_version, reference_baseline)
    time_by_id = {item["input"].candidate_id: item for item in time}
    resolved = []
    for item in memory:
        candidate_id = item["input"].candidate_id
        resolved.append(item | {
            "time_ref_seconds": time_by_id[candidate_id]["time_ref_seconds"],
            "time_source": time_by_id[candidate_id]["time_source"],
            "time_reason": time_by_id[candidate_id]["time_reason"],
        })

    available = [row for row in capacity_rows if row["status"] == "available"]
    if available:
        min_average = min(row["average_worker_budget_bytes"] for row in available)
        low_candidates = [item["memory_bytes"] for item in resolved if item["memory_bytes"] <= min_average]
        ram_low = _median(low_candidates) if low_candidates else float(demand["unit_memory_bytes"])
    else:
        ram_low = float(demand["unit_memory_bytes"])
    for host in available:
        workers = host["worker_slots"]
        host["ram_low_bytes"] = ram_low
        host["cap_bytes"] = min(
            host["budget_bytes"] - (workers - 1) * ram_low,
            host["physical_bytes"] - host["overhead_bytes"] - (workers - 1) * ram_low,
        )
        host["slot_worker_capacities"] = [
            min(demand["efficient_upper_bound"], max(0, workers - index * demand["efficient_upper_bound"]))
            for index in range(host["campaign_slots"])
        ]

    assignments, specials, unplaced, terminal = _assign_candidates(
        resolved, capacity_rows, reference_major, reference_version, reference_baseline
    )
    host_plans: list[dict[str, Any]] = []
    for capacity in capacity_rows:
        host_id = capacity["id"]
        host_plan = dict(capacity)
        if capacity["status"] != "available":
            host_plan.update({"assigned": [], "batches": [], "eta_seconds": None, "eta_reason": capacity["reason"]})
            host_plans.append(host_plan)
            continue
        normal = assignments[host_id]
        alone_and_retry = specials[host_id]
        retries = [item for item in alone_and_retry if item["input"].memory_crash_evidence]
        alone = [item for item in alone_and_retry if not item["input"].memory_crash_evidence]
        regular_batches = _batch_groups(normal, capacity, multiplier)
        alone_batches = [_single_group(item, capacity, retry=False) for item in sorted(alone, key=lambda candidate: candidate["input"].candidate_id)]
        retry_batches = [_single_group(item, capacity, retry=True) for item in sorted(retries, key=lambda candidate: candidate["input"].candidate_id)]
        batches = regular_batches + alone_batches + retry_batches
        for index, batch in enumerate(batches):
            batch["batch"] = index
        all_assigned = normal + alone + retries
        eta, eta_reason = _host_eta(capacity, all_assigned, reference_major, reference_version, reference_baseline)
        assigned_output = []
        for item in sorted(all_assigned, key=lambda candidate: candidate["input"].candidate_id):
            source = item["input"]
            assigned_item = {
                "id": source.candidate_id,
                "file": source.source_file,
                "memory_bytes": item["memory_bytes"],
                "memory_source": item["memory_source"],
                "time_ref_seconds": item["time_ref_seconds"],
                "time_source": item["time_source"],
                "ram_class": item["ram_class"],
                "assignment": item.get("assignment_kind", "retry" if source.memory_crash_evidence else "alone"),
            }
            for optional_reason in ("time_reason", "mix_flag_reason", "assignment_reason"):
                if item.get(optional_reason) is not None:
                    assigned_item[optional_reason] = item[optional_reason]
            assigned_output.append(assigned_item)
        regular_mean = _mean([item["memory_bytes"] for item in normal])
        host_result = {
            "ram_low_bytes": ram_low,
            "cap_bytes": capacity["cap_bytes"],
            "assigned": assigned_output,
            "assigned_mean_memory_bytes": regular_mean,
            "mix_holds": regular_mean <= capacity["average_worker_budget_bytes"] if normal else True,
            "mix_flagged": any(item.get("mix_flag_reason") is not None for item in normal),
            "batches": batches,
            "eta_seconds": eta,
        }
        if eta_reason is not None:
            host_result["eta_reason"] = eta_reason
        host_plan.update(host_result)
        host_plans.append(host_plan)

    valid_etas = [host["eta_seconds"] for host in host_plans if host["status"] == "available" and host["assigned"] and host["eta_seconds"] is not None]
    assigned_eta_missing = any(host["status"] == "available" and host["assigned"] and host["eta_seconds"] is None for host in host_plans)
    if assigned_eta_missing:
        lane_eta, lane_eta_reason = None, "at least one assigned host has no comparable Geekbench ETA"
    elif valid_etas:
        lane_eta, lane_eta_reason = max(valid_etas), None
    else:
        lane_eta, lane_eta_reason = 0.0, None
    time_reference: dict[str, Any] = {
        "normalization": "elapsed_seconds * source.single_core / source.baseline_score",
        "host_eta": "time_ref_seconds * baseline_score / host.single_core * f_h",
    }
    if eta_basis_reason is not None:
        time_reference["reason"] = eta_basis_reason
    else:
        time_reference.update({
            "geekbench_major": reference_major,
            "geekbench_version": reference_version,
            "baseline_score": reference_baseline,
        })
    result = {
        "schema": "fleet-plan/1",
        "lane": lane,
        "demand": demand,
        "batch_multiplier": multiplier,
        "time_reference": time_reference,
        "ram_low_bytes": ram_low,
        "hosts": host_plans,
        "unplaced": sorted(unplaced, key=lambda item: item["id"]),
        "terminal": sorted(terminal, key=lambda item: item["id"]),
        "eta_seconds": lane_eta,
    }
    if lane_eta_reason is not None:
        result["eta_reason"] = lane_eta_reason
    return result


def replan_pending(
    plan: Mapping[str, Any],
    candidate_ids: list[str] | tuple[str, ...],
    host_id: str,
) -> list[dict[str, Any]]:
    """Rebuild batches for unfinished planned candidates on one selected host.

    The host choice remains the campaign scheduler's policy; all RAM, worker,
    mix, retry, and batching decisions are delegated to the same placement
    functions used by the original planner.
    """
    candidates_by_id = {
        candidate["id"]: candidate
        for host in plan.get("hosts", [])
        for candidate in host.get("assigned", [])
    }
    selected_host = next(
        (host for host in plan.get("hosts", []) if host.get("id") == host_id and host.get("status") == "available"),
        None,
    )
    if selected_host is None:
        raise ContractError(f"replan host {host_id!r} is not available in the original plan")
    if not candidate_ids:
        return []

    resolved: list[dict[str, Any]] = []
    for index, candidate_id in enumerate(candidate_ids):
        if not is_candidate_id(candidate_id):
            raise ContractError(f"replan candidate_ids[{index}]: invalid candidate id")
        source = candidates_by_id.get(candidate_id)
        if source is None:
            raise ContractError(f"replan candidate {candidate_id!r} is not assigned in the original plan")
        retry = source.get("assignment") == "retry"
        item = CandidateInput(
            candidate_id=candidate_id,
            source_file=source["file"],
            measured_memory_bytes=source["memory_bytes"],
            estimated_memory_class=source["ram_class"],
            elapsed_seconds=None,
            measured_on_host=None,
            memory_crash_evidence=retry,
            memory_crash_retries=0,
        )
        resolved.append({
            "input": item,
            "memory_bytes": source["memory_bytes"],
            "time_ref_seconds": source["time_ref_seconds"],
            "ram_class": source["ram_class"],
        })

    capacity = {
        key: selected_host[key]
        for key in (
            "id", "status", "cap_bytes", "budget_bytes", "average_worker_budget_bytes",
            "cap_alone_bytes", "worker_slots", "speed", "f_h", "slot_worker_capacities",
        )
        if key in selected_host
    }
    if "cap_bytes" not in capacity or "budget_bytes" not in capacity:
        raise ContractError(f"replan host {host_id!r} is missing planner capacity facts")
    time_reference = plan.get("time_reference", {})
    assignments, specials, unplaced, terminal = _assign_candidates(
        resolved,
        [capacity],
        time_reference.get("geekbench_major"),
        time_reference.get("geekbench_version"),
        time_reference.get("baseline_score"),
    )
    if unplaced or terminal:
        details = unplaced + terminal
        raise ContractError("planner could not place re-queued candidates: " + ", ".join(row["id"] for row in details))
    normal = assignments[host_id]
    alone_and_retry = specials[host_id]
    retry_rows = [item for item in alone_and_retry if item["input"].memory_crash_evidence]
    alone_rows = [item for item in alone_and_retry if not item["input"].memory_crash_evidence]
    multiplier = plan.get("batch_multiplier", 4.0)
    batches = _batch_groups(normal, capacity, multiplier)
    batches.extend(_single_group(item, capacity, retry=False) for item in sorted(alone_rows, key=lambda row: row["input"].candidate_id))
    batches.extend(_single_group(item, capacity, retry=True) for item in sorted(retry_rows, key=lambda row: row["input"].candidate_id))
    for index, batch in enumerate(batches):
        batch["batch"] = index
    return batches


def extract_demand(path: str | Path, lane: str) -> dict[str, Any]:
    return load_lane_demand(path, lane)


def render_commands(plan: Mapping[str, Any]) -> str:
    """Render serial per-host batch commands for the existing runner."""
    lane = shlex.quote(str(plan["lane"]))
    lines: list[str] = ["set -euo pipefail"]
    for host in plan["hosts"]:
        if host["status"] != "available":
            continue
        lines.append(f"# host {host['id']}")
        lines.append("(")
        lines.append("set -euo pipefail")
        for batch in host["batches"]:
            ids = batch["candidate_ids"]
            if not ids:
                continue
            relative = f".assay/fleetd-plan/{host['id']}/batch-{batch['batch']:03d}.ids"
            file_arg = shlex.quote(relative)
            directory = shlex.quote(str(Path(relative).parent))
            values = " ".join(shlex.quote(candidate_id) for candidate_id in ids)
            jobs = 1 if batch.get("kind") == "retry" else min(host["worker_slots"], len(ids))
            lines.append(f"mkdir -p {directory}")
            lines.append(f"printf '%s\\n' {values} > {file_arg}")
            lines.append(
                "# Exit 4 is BUDGET_EXCEEDED; selected candidates may still be pending."
            )
            lines.append(f"if run-gate {lane} --jobs {jobs} --only-candidates {file_arg}; then")
            lines.append("  status=0")
            lines.append("else")
            lines.append("  status=$?")
            lines.append("fi")
            lines.append('if [ "$status" -eq 4 ]; then')
            lines.append('  echo "run-gate reported BUDGET_EXCEEDED (exit 4); check closeout for pending candidates" >&2')
            lines.append('elif [ "$status" -ne 0 ]; then')
            lines.append('  echo "run-gate failed with exit code $status" >&2')
            lines.append('  exit "$status"')
            lines.append("fi")
        lines.append(")")
    return "\n".join(lines) + "\n"
