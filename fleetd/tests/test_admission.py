from __future__ import annotations

from dataclasses import replace

import pytest

from fleetd.admission import (
    AdmissionError,
    Charge,
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
    fit_bytes,
    planned_core_headroom,
    static_eligibility,
)

GIB = 1024**3


def _fact(value):
    return Fact.known(value)


def _capacity(
    *,
    total=8 * GIB,
    reserve=1 * GIB,
    warm=0,
    high=7 * GIB,
    maximum=8 * GIB,
    max_planned=7 * GIB,
    online=4,
    cpu_cap=5.0,
    policy_cpu_cap=5.0,
    max_footprint="large",
    charges=None,
    aging_s=60.0,
    legacy_charge=6 * GIB,
):
    if charges is None:
        charges = {
            "tiny": GIB // 2,
            "small": 3 * GIB // 2,
            "medium": 4 * GIB,
            "large": 6 * GIB,
        }
    pressure = PressureFacts(_fact(0.0), _fact(0.0))
    return HostCapacity(
        cpu=CpuFacts(_fact(online), _fact(cpu_cap)),
        load1=_fact(0.0),
        memory=MemoryFacts(
            _fact(total), _fact(total - warm), _fact(reserve), _fact(0), _fact(0)
        ),
        psi=PsiFacts(pressure, pressure, pressure),
        gate_slice=GateSliceFacts(
            _fact(warm), _fact(warm), _fact(high), _fact(maximum), _fact("max 100000")
        ),
        disks=_fact(()),
        cgroup_v2_mounted=_fact(True),
        policy=PolicyFacts(
            _fact(policy_cpu_cap),
            _fact(max_footprint),
            _fact(max_planned),
            _fact(reserve),
            _fact(charges),
            _fact({"ops": ("merge-gate", "dev", "gate", "campaign")}),
            _fact({"checkout": 0, "docker": 0}),
            _fact(3.0),
            _fact({"wait_s": 0.0, "settle_s": 0.0}),
            _fact(aging_s),
            _fact(legacy_charge),
        ),
        guard=GuardFacts(_fact(False), Fact.unknown("guard absent"), Fact.unknown("guard absent"), Fact.unknown("guard absent")),
    )


@pytest.mark.parametrize(
    ("footprint", "expected_charge"),
    [
        ("tiny", GIB // 2),
        ("small", 3 * GIB // 2),
        ("medium", 4 * GIB),
        ("large", 6 * GIB),
    ],
)
def test_static_eligibility_uses_per_host_eight_gib_charges(footprint, expected_charge):
    capacity = _capacity()
    result = static_eligibility(capacity, footprint, 1)
    assert result.charge_bytes == expected_charge
    assert result.static_ram_bytes == 7 * GIB
    assert result.max_planned_cores == 5


@pytest.mark.parametrize(
    ("capacity_options", "entry", "expected"),
    [
        ({}, Charge(5 * GIB, 5 * GIB, 1), 2 * GIB),
        ({"total": 16 * GIB, "reserve": GIB, "high": 6 * GIB, "maximum": 8 * GIB,
          "max_planned": 16 * GIB, "warm": GIB}, Charge(2 * GIB, GIB // 2, 1), 7 * GIB // 2),
        ({"total": 16 * GIB, "reserve": GIB, "high": 16 * GIB, "maximum": 16 * GIB,
          "max_planned": 3 * GIB}, Charge(2 * GIB, 0, 1), GIB),
    ],
)
def test_fit_bytes_uses_the_tightest_physical_slice_or_policy_limit(capacity_options, entry, expected):
    assert fit_bytes(_capacity(**capacity_options), (entry,)) == expected


def test_fit_bytes_matches_the_design_formula_with_reserved_warm_set():
    capacity = _capacity(
        reserve=GIB,
        warm=GIB,
        high=7 * GIB,
        maximum=8 * GIB,
        max_planned=6 * GIB,
    )
    existing = (Charge(2 * GIB, GIB // 2, 1),)
    assert fit_bytes(capacity, existing) == 4 * GIB


def test_controller_host_small_class_and_six_core_cap():
    capacity = _capacity(
        total=64 * GIB,
        reserve=4 * GIB,
        high=12 * GIB,
        maximum=12 * GIB,
        max_planned=3 * GIB,
        online=8,
        cpu_cap=6,
        policy_cpu_cap=6,
        max_footprint="small",
    )
    eligible = static_eligibility(capacity, "small", 6)
    assert eligible.charge_bytes == 3 * GIB // 2
    assert eligible.max_planned_cores == 6
    with pytest.raises(AdmissionError) as raised:
        static_eligibility(capacity, "medium", 1)
    assert raised.value.exit_code == 2
    with pytest.raises(AdmissionError, match="planning cap"):
        static_eligibility(capacity, "small", 6.5)


def test_unknown_required_capacity_fact_preserves_reason_and_returns_75():
    capacity = _capacity()
    capacity = replace(
        capacity,
        gate_slice=replace(capacity.gate_slice, memory_high_bytes=Fact.unknown("cgroup file unreadable")),
    )
    with pytest.raises(AdmissionError, match="memory_high_bytes unavailable: cgroup file unreadable") as raised:
        static_eligibility(capacity, "small", 1)
    assert raised.value.exit_code == 75


def test_core_headroom_uses_the_smaller_host_cap_and_active_plans():
    capacity = _capacity(online=4, cpu_cap=4, policy_cpu_cap=3.5)
    assert planned_core_headroom(capacity, (Charge(GIB, 0, 1.25),)) == 2.25
