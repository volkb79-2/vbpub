from __future__ import annotations

import subprocess
import sys
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
        disks=_fact((
            DiskFacts(_fact("checkout"), _fact("/checkouts"), _fact(100 * GIB), _fact(50 * GIB),
                      _fact("ext4"), _fact(0)),
            DiskFacts(_fact("docker"), _fact("/var/lib/docker"), _fact(100 * GIB), _fact(50 * GIB),
                      _fact("ext4"), _fact(0)),
        )),
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


def _book(path, *, clock=lambda: 123.5):
    from fleetd.admission import AdmissionBook

    return AdmissionBook(path, clock=clock)


def _admit(book, capacity, *, job_id="ops-run-1", digest="a" * 64, group="ops", priority="gate",
           footprint="small", cores=1, legacy_jobs=(), provider=None):
    capacity_provider = provider if provider is not None else lambda: capacity
    return book.admit_now(
        job_id=job_id,
        authenticated_group=group,
        digest=digest,
        priority=priority,
        footprint=footprint,
        planned_cores=cores,
        capacity_provider=capacity_provider,
        legacy_jobs=legacy_jobs,
    )


def test_admission_record_is_durable_idempotent_group_bound_and_flocked(tmp_path):
    capacity = _capacity()
    book = _book(tmp_path / "state")
    result = _admit(book, capacity)
    assert result.existing is False
    assert result.handle is not None and result.handle.live is True
    assert result.record["charged_bytes"] == 3 * GIB // 2
    assert result.record["created_at"] == 123.5
    assert book.status("ops-run-1", "ops")["digest"] == "a" * 64
    assert book.is_live("ops-run-1") is True

    child = subprocess.run(
        [
            sys.executable,
            "-c",
            "import fcntl, os, sys; fd=os.open(sys.argv[1], os.O_CREAT|os.O_RDWR, 0o600); "
            "\ntry:\n fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB); print('free')"
            "\nexcept BlockingIOError:\n print('held')\nfinally:\n os.close(fd)",
            str(book._job_lock_path("ops-run-1")),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert child.stdout.strip() == "held"

    reopened = _book(tmp_path / "state")
    assert reopened.status("ops-run-1", "ops")["charged_bytes"] == 3 * GIB // 2
    assert _admit(reopened, capacity).existing is True
    with pytest.raises(AdmissionError, match="different authenticated group"):
        book.status("ops-run-1", "other")
    with pytest.raises(AdmissionError, match="different authenticated group"):
        _admit(book, capacity, group="other")
    with pytest.raises(AdmissionError, match="different request digest"):
        _admit(book, capacity, digest="b" * 64)

    result.handle.complete()
    assert result.handle.live is False
    assert book.status("ops-run-1", "ops") is None
    assert book.is_live("ops-run-1") is False


@pytest.mark.parametrize(
    "capacity, reason",
    [
        (replace(_capacity(), load1=Fact.known(6.0)), "load1"),
        (replace(_capacity(), psi=replace(_capacity().psi, memory=PressureFacts(Fact.known(0.0), Fact.known(3.0)))),
         "memory full avg10"),
        (replace(_capacity(), psi=replace(_capacity().psi, memory=PressureFacts(Fact.known(3.0), Fact.known(0.0)))),
         "memory some avg10"),
        (replace(_capacity(), load1=Fact.unknown("loadavg unreadable")), "load1 unavailable"),
        (replace(_capacity(), cgroup_v2_mounted=Fact.known(False)), "cgroup v2"),
        (replace(_capacity(), guard=GuardFacts(Fact.known(True), Fact.known("enforce"), Fact.unknown("digest"),
                                               Fact.known(1))), "frozen guard units"),
        (replace(_capacity(), guard=GuardFacts(Fact.known(True), Fact.known("enforce"), Fact.unknown("digest"),
                                               Fact.unknown("hostobs window incomplete"))), "frozen_units unavailable"),
        (replace(_capacity(), guard=GuardFacts(Fact.known(True), Fact.known("unknown"), Fact.unknown("digest"),
                                               Fact.unknown("hostobs window incomplete"))), "guard state is unknown"),
    ],
)
def test_live_load_psi_disk_cgroup_and_freeze_rules_return_closed_refusals(tmp_path, capacity, reason):
    book = _book(tmp_path / "state")
    with pytest.raises(AdmissionError, match=reason) as raised:
        _admit(book, capacity)
    assert raised.value.exit_code in {2, 75}
    assert book.status("ops-run-1", "ops") is None


@pytest.mark.parametrize("guard_state", ["off", "report"])
def test_off_and_report_guard_states_do_not_need_frozen_units(tmp_path, guard_state):
    base = _capacity()
    capacity = replace(
        base,
        guard=GuardFacts(Fact.known(True), Fact.known(guard_state), Fact.unknown("digest"),
                         Fact.unknown("hostobs not required")),
    )
    result = _admit(_book(tmp_path / "state"), capacity)
    assert result.handle is not None
    result.handle.complete()


def test_absent_guard_does_not_require_state_or_freeze_count(tmp_path):
    base = _capacity()
    capacity = replace(
        base,
        guard=GuardFacts(Fact.known(False), Fact.unknown("guard absent"), Fact.unknown("guard absent"),
                         Fact.unknown("guard absent")),
    )
    result = _admit(_book(tmp_path / "state"), capacity)
    assert result.handle is not None
    result.handle.complete()


def test_enforce_guard_with_zero_frozen_units_allows_admission(tmp_path):
    base = _capacity()
    capacity = replace(
        base,
        guard=GuardFacts(Fact.known(True), Fact.known("enforce"), Fact.known("f" * 64), Fact.known(0)),
    )
    result = _admit(_book(tmp_path / "state"), capacity)
    assert result.handle is not None
    result.handle.complete()


def test_disk_floor_below_policy_refuses_and_disagreement_is_invalid(tmp_path):
    base = _capacity()
    disk = base.disks.value[0]
    low_disk = replace(disk, free_bytes=Fact.known(99), floor_bytes=Fact.known(100))
    low = replace(base, disks=Fact.known((low_disk, base.disks.value[1])),
                  policy=replace(base.policy, disk_floors=Fact.known({"checkout": 100, "docker": 0})))
    with pytest.raises(AdmissionError, match="below its free-space floor") as raised:
        _admit(_book(tmp_path / "low"), low)
    assert raised.value.exit_code == 75

    mismatch = replace(base, policy=replace(base.policy, disk_floors=Fact.known({"checkout": 1, "docker": 0})))
    with pytest.raises(AdmissionError, match="disk floor facts disagree") as raised:
        _admit(_book(tmp_path / "mismatch"), mismatch)
    assert raised.value.exit_code == 2


def test_missing_disk_facts_and_empty_floor_policy_do_not_admit(tmp_path):
    base = _capacity()
    missing = replace(base, disks=Fact.unknown("statvfs failed"))
    with pytest.raises(AdmissionError, match=r"disks\[\] unavailable") as raised:
        _admit(_book(tmp_path / "missing"), missing)
    assert raised.value.exit_code == 75

    empty = replace(base, policy=replace(base.policy, disk_floors=Fact.known({})))
    with pytest.raises(AdmissionError, match="must name checkout and Docker roots") as raised:
        _admit(_book(tmp_path / "empty"), empty)
    assert raised.value.exit_code == 2


def test_existing_charges_bound_ram_and_planned_cores(tmp_path):
    capacity = _capacity()
    book = _book(tmp_path / "state")
    first = _admit(book, capacity, job_id="ops-first", cores=2)
    with pytest.raises(AdmissionError, match="planned-core headroom") as raised:
        _admit(book, capacity, job_id="ops-second", digest="b" * 64, cores=4)
    assert raised.value.exit_code == 75
    with pytest.raises(AdmissionError, match="RAM fit") as raised:
        _admit(book, capacity, job_id="ops-third", digest="c" * 64, footprint="large")
    assert raised.value.exit_code == 75
    first.handle.complete()


def test_legacy_jobs_use_the_required_memory_charge_and_never_guess_cores(tmp_path):
    capacity = _capacity(legacy_charge=2 * GIB)
    book = _book(tmp_path / "state")
    with pytest.raises(AdmissionError, match="unknown planned-core use") as raised:
        _admit(book, capacity, legacy_jobs=("legacy-without-EXIT",))
    assert raised.value.exit_code == 75

    unknown_charge = replace(
        capacity,
        policy=replace(capacity.policy, legacy_charge_bytes=Fact.unknown("legacy policy missing")),
    )
    with pytest.raises(AdmissionError, match="legacy_charge_bytes unavailable") as raised:
        _admit(_book(tmp_path / "unknown"), unknown_charge, legacy_jobs=("legacy-without-EXIT",))
    assert raised.value.exit_code == 75

    too_large = _capacity(legacy_charge=5 * GIB)
    with pytest.raises(AdmissionError, match="RAM fit") as raised:
        _admit(_book(tmp_path / "charged"), too_large, footprint="large", legacy_jobs=("legacy",))
    assert raised.value.exit_code == 75


def test_unreadable_admitted_record_keeps_its_charge_and_refuses_new_work(tmp_path):
    capacity = _capacity()
    book = _book(tmp_path / "state")
    first = _admit(book, capacity)
    path = book._record_path("ops-run-1")
    path.write_text("{torn", encoding="utf-8")
    with pytest.raises(AdmissionError, match="unreadable; its charge is retained") as raised:
        _admit(book, capacity, job_id="ops-next", digest="b" * 64)
    assert raised.value.exit_code == 75
    assert path.read_text(encoding="utf-8") == "{torn"
    first.handle.close()
