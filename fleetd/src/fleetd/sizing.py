"""Sizing an R2 campaign from measured units instead of a fixed guess.

The old lane declarations carried one container's fixed caps (``cpus = "3"``,
``budget = "12h"``) and a budget that was right for exactly one shard size on
one worker count. The default R2 campaign is several instances on several
hosts, so every one of those numbers must DERIVE from the plan:

* **instances per host** from the host's own CPU and RAM budget, the fixed
  per-instance charge (judge + container) and the per-WORKER unit;
* **shards** = the instances (each instance is one ``--shard K/N`` run);
* **per-instance budget** from that instance's candidate count and the
  per-candidate wall seconds and its worker count, plus the lane baseline;
* **the outer bound** from the instance budget plus a launch/collection
  allowance, so a lane's declared budget and Run-Gate's bound are the same
  derivation instead of two hand-written numbers that drift (the
  11h-below-12h warning).

Every number carries its ``basis``: ``measured`` when a finished campaign's
own per-candidate records supplied it, ``declared`` when it is still the
lane's estimate. A sizing document therefore says out loud how much of itself
is a guess.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import ContractError

#: Closed set of provenance labels for a sizing input.
BASES = ("measured", "declared")

#: Which per-candidate statistic the seconds came from (``mean`` of a
#: measurement's per-candidate sum fields, the older ``median`` of a document
#: written before them, or a ``declared`` estimate).
STATISTICS = ("mean", "median", "declared")

SIZING_SCHEMA = "fleet-r2-sizing/1"

#: The budget margin when nothing measured says otherwise (``--margin``'s old
#: fixed default); with a measurement it is the FLOOR of the derived margin.
DEFAULT_MARGIN = 1.10

#: One-sided 95th-percentile factor of a sum of independent candidates: a
#: shard of ``n`` candidates is budgeted at ``n x mean + TAIL_Z x stdev x
#: sqrt(n)`` before the worker division, expressed as a margin over ``n x mean``
#: (FD-031). An ESTIMATE: it assumes the shard's candidates are drawn like the
#: measured ones, and a lane whose heavy candidates cluster in one shard can
#: still exceed it, which a later instance continues.
TAIL_Z = 1.645


def _positive(value: object, where: str, *, kind: type | tuple[type, ...] = (int, float)) -> float:
    if isinstance(value, bool) or not isinstance(value, kind):
        raise ContractError(f"{where}: expected a positive number")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ContractError(f"{where}: expected a positive finite number")
    return number


def _count(value: object, where: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"{where}: expected an integer")
    if value < minimum:
        raise ContractError(f"{where}: must be at least {minimum}")
    return value


@dataclass(frozen=True)
class HostSizingInput:
    """One host's capacity facts, as the planner already resolved them."""

    host_id: str
    cpu_slots: int
    memory_budget_bytes: int
    assigned_candidates: int


@dataclass(frozen=True)
class SizingInput:
    """The complete input of one sizing derivation."""

    candidate_count: int
    per_candidate_seconds: float
    per_candidate_memory_bytes: int
    basis: str
    worker_cpu: float
    worker_memory_bytes: int
    instance_memory_bytes: int
    instance_cpu: float
    max_workers_per_instance: int
    baseline_seconds: float
    hosts: tuple[HostSizingInput, ...]
    #: Coefficient of variation (stdev / mean) of the measured per-candidate
    #: seconds; 0.0 when nothing measured it (declared, or a pre-sum document).
    dispersion: float = 0.0
    seconds_statistic: str = "declared"


def _balanced_split(total: int, parts: int) -> list[int]:
    """Split *total* as evenly as possible across *parts* (remainder first)."""
    base, remainder = divmod(total, parts)
    return [base + (1 if index < remainder else 0) for index in range(parts)]


def _minutes(seconds: float) -> int:
    """Round a duration UP to whole minutes, the only grammar lanes accept."""
    return int(math.ceil(seconds / 60.0)) * 60


def derive_sizing(
    sizing: SizingInput,
    *,
    margin: float | None = None,
    minimum_budget_seconds: int = 900,
    startup_allowance_seconds: int = 900,
) -> dict[str, Any]:
    """Return the ``fleet-r2-sizing/1`` document for one campaign.

    *margin* is the EXPLICIT override of the budget margin: left ``None`` each
    shard's margin is derived, ``max(DEFAULT_MARGIN, 1 + TAIL_Z x dispersion /
    sqrt(candidates))``, so a measured heavy tail widens the budget of the
    shards it threatens most (the small ones) and a flat lane keeps the fixed
    default. The result is an estimate: a shard that still exceeds its budget
    stops there, and its remainder continues in a later instance.

    *minimum_budget_seconds* and *startup_allowance_seconds* are
    policy numbers with an explicit caller, never hidden constants: the
    defaults are what the CLI passes and what the consumer guide documents.
    """
    if margin is not None and (margin < 1.0 or not math.isfinite(margin)):
        raise ContractError("margin: expected a finite factor of at least 1.0")
    _count(minimum_budget_seconds, "minimum_budget_seconds", minimum=1)
    _count(startup_allowance_seconds, "startup_allowance_seconds")
    if sizing.basis not in BASES:
        raise ContractError(f"basis: expected one of {', '.join(BASES)}")
    if sizing.seconds_statistic not in STATISTICS:
        raise ContractError(f"seconds_statistic: expected one of {', '.join(STATISTICS)}")
    if (
        isinstance(sizing.dispersion, bool) or not isinstance(sizing.dispersion, (int, float))
        or not math.isfinite(float(sizing.dispersion)) or sizing.dispersion < 0
    ):
        raise ContractError("dispersion: expected a non-negative finite number")
    _count(sizing.candidate_count, "candidate_count")
    if sizing.candidate_count < 1:
        raise ContractError("candidate_count: an empty candidate set is nothing to judge")
    _positive(sizing.per_candidate_seconds, "per_candidate_seconds")
    _count(sizing.per_candidate_memory_bytes, "per_candidate_memory_bytes", minimum=1)
    _positive(sizing.worker_cpu, "worker_cpu")
    _count(sizing.worker_memory_bytes, "worker_memory_bytes", minimum=1)
    _count(sizing.instance_memory_bytes, "instance_memory_bytes", minimum=1)
    if isinstance(sizing.instance_cpu, bool) or not isinstance(sizing.instance_cpu, (int, float)):
        raise ContractError("instance_cpu: expected a non-negative number")
    if not math.isfinite(float(sizing.instance_cpu)) or sizing.instance_cpu < 0:
        raise ContractError("instance_cpu: expected a non-negative finite number")
    _count(sizing.max_workers_per_instance, "max_workers_per_instance", minimum=1)
    if isinstance(sizing.baseline_seconds, bool) or not isinstance(sizing.baseline_seconds, (int, float)):
        raise ContractError("baseline_seconds: expected a non-negative number")
    if not math.isfinite(float(sizing.baseline_seconds)) or sizing.baseline_seconds < 0:
        raise ContractError("baseline_seconds: expected a non-negative finite number")
    if not sizing.hosts:
        raise ContractError("hosts: at least one planned host is required")

    host_rows: list[dict[str, Any]] = []
    instances: list[tuple[dict[str, Any], int]] = []
    for host in sizing.hosts:
        where = f"hosts[{host.host_id}]"
        _count(host.cpu_slots, f"{where}.cpu_slots")
        _count(host.memory_budget_bytes, f"{where}.memory_budget_bytes")
        _count(host.assigned_candidates, f"{where}.assigned_candidates")
        row: dict[str, Any] = {
            "id": host.host_id,
            "cpu_slots": host.cpu_slots,
            "memory_budget_bytes": host.memory_budget_bytes,
            "assigned_candidates": host.assigned_candidates,
            "status": "available",
        }
        # A WORKER unit is charged per running worker; the fixed instance
        # charge covers the judge, the container and its own workspace, so the
        # first worker of an instance costs instance + one unit.
        memory_limited = math.floor(
            (host.memory_budget_bytes - sizing.instance_memory_bytes) / sizing.worker_memory_bytes
        )
        cpu_limited = math.floor(
            (host.cpu_slots - sizing.instance_cpu) / sizing.worker_cpu
        )
        workers = min(sizing.max_workers_per_instance, memory_limited, cpu_limited)
        if workers < 1:
            row["status"] = "refused"
            row["reason"] = (
                "one worker of this unit does not fit the host's memory budget and "
                "CPU slots beside the fixed instance charge"
            )
            row["instances"] = 0
            host_rows.append(row)
            continue
        instance_memory = sizing.instance_memory_bytes + workers * sizing.worker_memory_bytes
        instance_cpu = sizing.instance_cpu + workers * sizing.worker_cpu
        # The worker-fit test above already guarantees both budgets hold one
        # instance, so these quotients are at least 1 by construction.
        count = min(
            host.memory_budget_bytes // instance_memory,
            math.floor(host.cpu_slots / instance_cpu),
        )
        row.update({
            "instances": count,
            "workers_per_instance": workers,
            "workers_total": count * workers,
            "instance_memory_bytes": instance_memory,
            "instance_cpu": instance_cpu,
            "cpus_per_instance": round(instance_cpu, 3),
            "memory_per_instance": instance_memory,
        })
        host_rows.append(row)
        instances.extend((row, index) for index in range(count))

    if not instances:
        raise ContractError(
            "no planned host can hold one instance of the declared worker unit; "
            "lower worker_memory/worker_cpu or raise the host budget"
        )
    sizes = _balanced_split(sizing.candidate_count, len(instances))
    shard_rows: list[dict[str, Any]] = []
    for index, ((host, _instance_index), candidates) in enumerate(zip(instances, sizes, strict=True)):
        workers = host["workers_per_instance"]
        if candidates:
            seconds = (
                candidates * sizing.per_candidate_seconds / workers
                + float(sizing.baseline_seconds)
            )
        else:
            # A shard with no candidate still pays the lane baseline: sizing
            # must never pretend an idle instance is free.
            seconds = float(sizing.baseline_seconds)
        if margin is not None:
            shard_margin = float(margin)
        elif candidates:
            shard_margin = max(
                DEFAULT_MARGIN,
                1.0 + TAIL_Z * float(sizing.dispersion) / math.sqrt(candidates),
            )
        else:
            shard_margin = DEFAULT_MARGIN
        budget = max(minimum_budget_seconds, _minutes(seconds * shard_margin))
        shard_rows.append({
            "index": index,
            "host": host["id"],
            "candidate_count": candidates,
            "jobs": workers,
            "estimate_seconds": round(seconds, 3),
            "margin": round(shard_margin, 3),
            "budget_seconds": budget,
            "outer_budget_seconds": budget + _minutes(startup_allowance_seconds),
        })

    campaign_seconds = max(row["estimate_seconds"] for row in shard_rows)
    return {
        "schema": SIZING_SCHEMA,
        "basis": sizing.basis,
        "candidate_count": sizing.candidate_count,
        "per_candidate_seconds": round(float(sizing.per_candidate_seconds), 3),
        "per_candidate_memory_bytes": sizing.per_candidate_memory_bytes,
        "worker_unit": {
            "cpu": round(float(sizing.worker_cpu), 3),
            "memory_bytes": sizing.worker_memory_bytes,
        },
        "instance_unit": {
            "cpu": round(float(sizing.instance_cpu), 3),
            "memory_bytes": sizing.instance_memory_bytes,
        },
        "max_workers_per_instance": sizing.max_workers_per_instance,
        "baseline_seconds": round(float(sizing.baseline_seconds), 3),
        "margin": round(float(DEFAULT_MARGIN if margin is None else margin), 3),
        "margin_source": (
            "explicit" if margin is not None
            else "derived" if sizing.seconds_statistic != "declared" else "default"
        ),
        "seconds_statistic": sizing.seconds_statistic,
        "dispersion": round(float(sizing.dispersion), 6),
        "minimum_budget_seconds": minimum_budget_seconds,
        "startup_allowance_seconds": _minutes(startup_allowance_seconds),
        "shard_count": len(shard_rows),
        "instances": len(instances),
        "campaign_estimate_seconds": round(campaign_seconds, 3),
        "shards": shard_rows,
        "hosts": host_rows,
    }


def sizing_input_from_plan(
    plan: object,
    *,
    measurement: Mapping[str, Any] | None = None,
    per_candidate_seconds: float | None = None,
    per_candidate_memory_bytes: int | None = None,
) -> SizingInput:
    """Build a :class:`SizingInput` from a ``fleet-plan/1`` document.

    The plan already resolved every host's CPU slots, overcommitted sum budget
    and assignment, so sizing reads those facts instead of re-deriving them.
    The per-candidate unit comes from *measurement* (a finished campaign's own
    recorded numbers), else from the explicit arguments, else from the lane
    demand's declared estimates -- and the ``basis`` says WHICH.
    """
    if not isinstance(plan, Mapping) or plan.get("schema") != "fleet-plan/1":
        raise ContractError("plan: expected fleet-plan/1")
    demand = plan.get("demand")
    if not isinstance(demand, Mapping):
        raise ContractError("plan.demand: expected the lane demand object")
    foreach = (
        ("worker_cpu", "worker_cpu", "worker CPU share"),
        ("worker_memory_bytes", "worker_memory", "per-worker memory unit"),
        ("instance_memory_bytes", "instance_memory", "fixed per-instance memory charge"),
    )
    for key, name, label in foreach:
        if key not in demand:
            raise ContractError(
                f"demand.{name}: sizing requires the {label}; declare it in "
                "[lanes.<lane>.demand]"
            )
    if "instance_cpu" not in demand:
        raise ContractError(
            "demand.instance_cpu: sizing requires the fixed per-instance CPU charge "
            "(the judge and container overhead beside the workers)"
        )
    basis = "declared"
    statistic = "declared"
    dispersion = 0.0
    seconds = demand["unit_seconds_ref"]
    memory = demand["unit_memory_bytes"]
    if measurement is not None:
        summary = measurement.get("summary") if "summary" in measurement else measurement
        if not isinstance(summary, Mapping):
            raise ContractError("measurement.summary: expected an object")
        seconds = summary.get("elapsed_seconds_median")
        memory = summary.get("memory_peak_bytes_median")
        if seconds is None or memory is None:
            raise ContractError(
                "measurement.summary: needs elapsed_seconds_median and "
                "memory_peak_bytes_median from finished candidates"
            )
        basis = "measured"
        statistic = "median"
        # FD-031: a shard's wall time is the SUM of its candidates, so the
        # per-candidate MEAN over every candidate that occupied a worker is the
        # matching estimator; a document written before the sum fields keeps
        # its median.
        mean = summary.get("cost_seconds_mean")
        if mean is not None:
            seconds, statistic = mean, "mean"
            dispersion = float(summary.get("cost_seconds_stdev") or 0.0) / float(mean)
    if per_candidate_seconds is not None:
        seconds, basis = per_candidate_seconds, "declared"
        statistic, dispersion = "declared", 0.0
    if per_candidate_memory_bytes is not None:
        memory, basis = per_candidate_memory_bytes, "declared"
    _positive(seconds, "per_candidate_seconds")
    _count(memory, "per_candidate_memory_bytes", minimum=1)
    hosts = []
    for host in plan.get("hosts", []):
        if not isinstance(host, Mapping) or host.get("status") != "available":
            continue
        cpu_slots = host.get("cpu_worker_slots")
        if isinstance(cpu_slots, bool) or not isinstance(cpu_slots, int):
            raise ContractError(f"plan.hosts.{host.get('id')}.cpu_worker_slots: expected an integer")
        budget = host.get("budget_bytes")
        if isinstance(budget, bool) or not isinstance(budget, int):
            raise ContractError(f"plan.hosts.{host.get('id')}.budget_bytes: expected an integer")
        assigned = host.get("assigned", [])
        if not isinstance(assigned, list):
            raise ContractError(f"plan.hosts.{host.get('id')}.assigned: expected an array")
        hosts.append(HostSizingInput(
            host_id=str(host.get("id")),
            cpu_slots=cpu_slots,
            memory_budget_bytes=budget,
            assigned_candidates=len(assigned),
        ))
    if not hosts:
        raise ContractError("plan.hosts: sizing needs at least one available host")
    assigned_total = sum(host.assigned_candidates for host in hosts)
    unplaced = plan.get("unplaced", []) or []
    terminal = plan.get("terminal", []) or []
    if not isinstance(unplaced, list) or not isinstance(terminal, list):
        raise ContractError("plan unplaced/terminal: expected arrays")
    candidate_count = assigned_total + len(unplaced) + len(terminal)
    return SizingInput(
        candidate_count=candidate_count,
        per_candidate_seconds=float(seconds),
        per_candidate_memory_bytes=int(memory),
        basis=basis,
        worker_cpu=float(demand["worker_cpu"]),
        worker_memory_bytes=int(demand["worker_memory_bytes"]),
        instance_memory_bytes=int(demand["instance_memory_bytes"]),
        instance_cpu=float(demand["instance_cpu"]),
        max_workers_per_instance=int(demand["efficient_upper_bound"]),
        baseline_seconds=float(demand.get("baseline_seconds", 0.0)),
        hosts=tuple(hosts),
        dispersion=dispersion,
        seconds_statistic=statistic,
    )
