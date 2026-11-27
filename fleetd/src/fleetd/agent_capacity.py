"""Projection of validated host-facts/2 documents for the host admission book.

This host-side module uses only the standard library and fleetd's stdlib-only
admission and contracts modules.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, TypeVar

from .admission import (
    CpuFacts,
    DiskFacts,
    Fact,
    GateSliceFacts,
    GuardFacts,
    HostCapacity,
    MemoryFacts,
    PolicyFacts,
    PressureFacts,
    PsiFacts,
)
from .contracts import HostFacts

T = TypeVar("T")


def _field_fact(wrapper: Mapping[str, Any], where: str) -> Fact[Any]:
    value = wrapper["v"]
    if value is None:
        reason = wrapper.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"{where} is unavailable without a reason")
        return Fact.unknown(reason)
    return Fact.known(value)


def _mapped_fact(
    wrapper: Mapping[str, Any], where: str, convert: Callable[[Any], T],
) -> Fact[T]:
    fact = _field_fact(wrapper, where)
    if fact.value is None:
        return Fact.unknown(str(fact.reason))
    return Fact.known(convert(fact.value))


def host_capacity_from_facts(document: HostFacts) -> HostCapacity:
    """Build admission facts from a previously validated ``host-facts/2``.

    The collector does not yet emit fresh warm samples. They remain unknown so
    admission keeps each full charge reserved. Freeze events also have no
    hostobs reader; a reported value is therefore not trusted as a source.
    """
    if not isinstance(document, HostFacts):
        raise TypeError("document must be a validated HostFacts value")
    facts = document.to_dict()
    if facts.get("schema") != "host-facts/2":
        raise ValueError("admission capacity requires validated host-facts/2")

    cpu = facts["cpu"]
    memory = facts["memory"]
    psi = facts["psi"]
    gate_slice = facts["gate_slice"]
    policy = facts["policy"]
    guard = facts["guard"]

    def pressure(name: str) -> PressureFacts:
        table = psi[name]
        return PressureFacts(
            some_avg10_pct=_field_fact(table["some_avg10_pct"], f"psi.{name}.some_avg10_pct"),
            full_avg10_pct=_field_fact(table["full_avg10_pct"], f"psi.{name}.full_avg10_pct"),
        )

    disk_fact = _field_fact(facts["disks"], "disks[]")
    if disk_fact.value is None:
        disks: Fact[tuple[DiskFacts, ...]] = Fact.unknown(str(disk_fact.reason))
    else:
        disks = Fact.known(tuple(
            DiskFacts(
                name=_field_fact(row["name"], f"disks[{index}].name"),
                mount=_field_fact(row["mount"], f"disks[{index}].mount"),
                total_bytes=_field_fact(row["total_bytes"], f"disks[{index}].total_bytes"),
                free_bytes=_field_fact(row["free_bytes"], f"disks[{index}].free_bytes"),
                fs=_field_fact(row["fs"], f"disks[{index}].fs"),
                floor_bytes=_field_fact(row["floor_bytes"], f"disks[{index}].floor_bytes"),
            )
            for index, row in enumerate(disk_fact.value)
        ))

    charges = _mapped_fact(
        policy["footprint_charges_bytes"], "policy.footprint_charges_bytes",
        lambda value: dict(value),
    )
    priorities = _mapped_fact(
        policy["priority_classes_by_principal"], "policy.priority_classes_by_principal",
        lambda value: {principal: tuple(classes) for principal, classes in value.items()},
    )
    disk_floors = _mapped_fact(policy["disk_floors"], "policy.disk_floors", lambda value: dict(value))
    max_upload_store_bytes = _field_fact(
        policy["max_upload_store_bytes"], "policy.max_upload_store_bytes",
    )
    admission = _mapped_fact(policy["admission"], "policy.admission", lambda value: dict(value))

    cpu_max_fact = _field_fact(gate_slice["cpu_max"], "gate_slice.cpu_max")
    if cpu_max_fact.value is None:
        cpu_max = Fact.unknown(str(cpu_max_fact.reason))
    else:
        limits = cpu_max_fact.value
        cpu_max = Fact.known(f"{limits['quota_us']} {limits['period_us']}")

    reported_frozen = _field_fact(guard["frozen_units"], "guard.frozen_units")
    frozen_units = (
        Fact.unknown(str(reported_frozen.reason))
        if reported_frozen.value is None
        else Fact.unknown("not-collected: hostobs freeze events are not merged")
    )

    return HostCapacity(
        cpu=CpuFacts(
            online=_field_fact(cpu["online"], "cpu.online"),
            planning_cap=_field_fact(cpu["planning_cap"], "cpu.planning_cap"),
        ),
        load1=_field_fact(facts["load1"], "load1"),
        memory=MemoryFacts(
            total_bytes=_field_fact(memory["total_bytes"], "memory.total_bytes"),
            available_bytes=_field_fact(memory["available_bytes"], "memory.available_bytes"),
            host_reserve_bytes=_field_fact(memory["host_reserve_bytes"], "memory.host_reserve_bytes"),
            swap_total_bytes=_field_fact(memory["swap_total_bytes"], "memory.swap_total_bytes"),
            swap_free_bytes=_field_fact(memory["swap_free_bytes"], "memory.swap_free_bytes"),
        ),
        psi=PsiFacts(cpu=pressure("cpu"), memory=pressure("memory"), io=pressure("io")),
        gate_slice=GateSliceFacts(
            memory_current_bytes=_field_fact(gate_slice["memory_current_bytes"], "gate_slice.memory_current_bytes"),
            memory_warm_bytes=_field_fact(gate_slice["memory_warm_bytes"], "gate_slice.memory_warm_bytes"),
            memory_high_bytes=_field_fact(gate_slice["memory_high_bytes"], "gate_slice.memory_high_bytes"),
            memory_max_bytes=_field_fact(gate_slice["memory_max_bytes"], "gate_slice.memory_max_bytes"),
            cpu_max=cpu_max,
        ),
        disks=disks,
        cgroup_v2_mounted=_field_fact(facts["cgroup_v2"]["mounted"], "cgroup_v2.mounted"),
        policy=PolicyFacts(
            planning_cap=_field_fact(policy["planning_cap"], "policy.planning_cap"),
            max_footprint=_field_fact(policy["max_footprint"], "policy.max_footprint"),
            max_planned_bytes=_field_fact(policy["max_planned_bytes"], "policy.max_planned_bytes"),
            footprint_charges_bytes=charges,
            priority_classes_by_principal=priorities,
            disk_floors=disk_floors,
            max_upload_store_bytes=max_upload_store_bytes,
            psi_refuse_some_avg10=_field_fact(policy["psi_refuse_some_avg10"], "policy.psi_refuse_some_avg10"),
            admission=admission,
            aging_s=_field_fact(policy["aging_s"], "policy.aging_s"),
            legacy_charge_bytes=_field_fact(policy["legacy_charge_bytes"], "policy.legacy_charge_bytes"),
            legacy_planned_cores=_field_fact(policy["legacy_planned_cores"], "policy.legacy_planned_cores"),
        ),
        guard=GuardFacts(
            guard_present=_field_fact(guard["guard_present"], "guard.guard_present"),
            guard_state=_field_fact(guard["guard_state"], "guard.guard_state"),
            config_digest=_field_fact(guard["config_digest"], "guard.config_digest"),
            frozen_units=frozen_units,
        ),
        warm_samples_bytes_by_id=Fact.unknown(
            "not-collected: fresh admission warm samples are not in host-facts/2",
        ),
    )
