from __future__ import annotations

import subprocess
import sys
import json
from dataclasses import replace

import pytest

from fleetd.admission import (
    AdmissionError,
    AdmissionRequest,
    AdmissionResources,
    Charge,
    CpuFacts,
    DiskFacts,
    Fact,
    GateSliceFacts,
    GuardFacts,
    HostCapacity,
    InspectorAnswer,
    LeaseRequest,
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


def _book(path, *, clock=lambda: 123.5, waiter=None):
    from fleetd.admission import AdmissionBook

    if waiter is None:
        waiter = getattr(clock, "wait", lambda _seconds: None)
    return AdmissionBook(path, clock=clock, waiter=waiter)


class _FakeClock:
    def __init__(self, start=0.0):
        self.now = start
        self.on_wait = None

    def __call__(self):
        return self.now

    def wait(self, seconds):
        if self.on_wait is not None:
            self.on_wait(seconds)
        else:
            self.now += seconds


def _request(
    *, job_id="ops-run-1", priority="gate", footprint="small", cores=1,
    cpu_set=None, shard=None, version="1.0.0", run="run-1", client="controller",
):
    return AdmissionRequest(
        job_id=job_id,
        repo="vbpub",
        sha="a" * 40,
        base=None,
        project="fleetd",
        lane="r0-r1",
        shard=shard,
        resources=AdmissionResources(cores, footprint, 1000, cpu_set),
        priority=priority,
        requested_by_client=client,
        requested_by_version=version,
        requested_by_run=run,
        stores={"collect": None, "seed": None},
        extra_argv=(),
        env={},
        dev=None,
        kind="lane",
    )


def _lease_request(*, lease_id="ops-lease-1", container_name="rg-lane-1", footprint="small",
                   cores=1, priority="gate"):
    return LeaseRequest(
        lease_id=lease_id,
        container_name=container_name,
        resources=AdmissionResources(cores, footprint),
        priority=priority,
    )


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
    with pytest.raises(AdmissionError, match="prefix does not match"):
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


def test_request_digest_excludes_job_id_and_client_version_but_binds_contract_fields():
    original = _request(job_id="ops-first", version="1.0.0")
    assert original.digest() == _request(job_id="ops-second", version="9.9.9").digest()
    assert original.digest() != _request(run="run-2").digest()
    assert original.digest() != _request(priority="campaign").digest()
    assert original.digest() != _request(footprint="medium").digest()


@pytest.mark.parametrize(
    "job_request, message",
    [
        (_request(cpu_set=(0,)), "cpu_set is reserved"),
        (_request(shard={"index": 0, "total": 1}), "shard is reserved"),
    ],
)
def test_reserved_cpu_set_and_shard_are_refused_in_v1(tmp_path, job_request, message):
    book = _book(tmp_path / "state")
    with pytest.raises(AdmissionError, match=message) as raised:
        book.submit(job_request, authenticated_group="ops", capacity_provider=lambda: _capacity(), wait_s=0)
    assert raised.value.exit_code == 2
    assert book.status(job_request.job_id, "ops") is None


def test_group_prefix_length_cap_and_job_grammar_are_enforced(tmp_path):
    from fleetd.admission import MAX_JOB_ID_LENGTH

    book = _book(tmp_path / "state")
    with pytest.raises(AdmissionError, match="prefix does not match"):
        book.submit(_request(job_id="other-run"), authenticated_group="ops",
                    capacity_provider=lambda: _capacity(), wait_s=0)
    with pytest.raises(AdmissionError, match="valid admission identifier"):
        book.submit(_request(job_id="ops-../escape"), authenticated_group="ops",
                    capacity_provider=lambda: _capacity(), wait_s=0)
    with pytest.raises(AdmissionError, match="valid admission identifier"):
        book.submit(_request(job_id="ops-" + "a" * MAX_JOB_ID_LENGTH), authenticated_group="ops",
                    capacity_provider=lambda: _capacity(), wait_s=0)


@pytest.mark.parametrize(
    ("requested", "allowed", "expected"),
    [
        ("gate", ("campaign", "dev"), "campaign"),
        ("dev", ("campaign", "gate"), "gate"),
        ("gate", ("merge-gate", "dev"), None),
    ],
)
def test_priority_clamps_only_to_the_most_urgent_allowed_class_not_more_urgent_than_request(
    tmp_path, requested, allowed, expected
):
    base = _capacity()
    capacity = replace(base, policy=replace(
        base.policy,
        priority_classes_by_principal=Fact.known({"ops": allowed}),
    ))
    request = _request(job_id="ops-priority", priority=requested)
    book = _book(tmp_path / "state")
    if expected is None:
        with pytest.raises(AdmissionError, match="no allowed class at or below") as raised:
            book.submit(request, authenticated_group="ops", capacity_provider=lambda: capacity, wait_s=0)
        assert raised.value.exit_code == 2
    else:
        result = book.submit(request, authenticated_group="ops", capacity_provider=lambda: capacity, wait_s=0)
        assert result.record["priority"] == expected
        result.handle.complete()


def test_unknown_or_missing_priority_policy_refuses_without_inventing_class(tmp_path):
    base = _capacity()
    unknown = replace(base, policy=replace(
        base.policy,
        priority_classes_by_principal=Fact.unknown("principal policy unreadable"),
    ))
    with pytest.raises(AdmissionError, match="priority_classes_by_principal unavailable") as raised:
        _book(tmp_path / "unknown").submit(_request(), authenticated_group="ops",
                                           capacity_provider=lambda: unknown, wait_s=0)
    assert raised.value.exit_code == 75

    missing = replace(base, policy=replace(
        base.policy,
        priority_classes_by_principal=Fact.known({}),
    ))
    with pytest.raises(AdmissionError, match="missing for authenticated group") as raised:
        _book(tmp_path / "missing").submit(_request(), authenticated_group="ops",
                                           capacity_provider=lambda: missing, wait_s=0)
    assert raised.value.exit_code == 2


def test_same_request_is_idempotent_through_the_queue_api(tmp_path):
    capacity = _capacity()
    book = _book(tmp_path / "state")
    request = _request()
    first = book.submit(request, authenticated_group="ops", capacity_provider=lambda: capacity, wait_s=0)
    same = book.submit(_request(version="2.0.0"), authenticated_group="ops",
                       capacity_provider=lambda: capacity, wait_s=0)
    assert same.existing is True
    assert same.handle is None
    assert same.record["digest"] == first.record["digest"]
    with pytest.raises(AdmissionError, match="different request digest") as raised:
        book.submit(_request(run="changed"), authenticated_group="ops",
                    capacity_provider=lambda: capacity, wait_s=0)
    assert raised.value.exit_code == 2
    first.handle.complete()


def test_wait_expiry_withdraws_waiter_and_confirms_never_started(tmp_path):
    clock = _FakeClock()
    blocked_capacity = _capacity(warm=7 * GIB)
    book = _book(tmp_path / "state", clock=clock, waiter=clock.wait)
    with pytest.raises(AdmissionError, match="waiter withdrawn and the book confirms the job never started") as raised:
        book.submit(_request(job_id="ops-expire"), authenticated_group="ops",
                    capacity_provider=lambda: blocked_capacity, wait_s=0.5)
    assert raised.value.exit_code == 75
    assert book.status("ops-expire", "ops") is None
    assert list(book.waiting_dir.glob("*.json")) == []


def test_aged_head_reserves_ram_and_cores_until_it_fits(tmp_path):
    clock = _FakeClock()
    capacity = _capacity(aging_s=60)
    book = _book(tmp_path / "state", clock=clock)
    blocker = _admit(book, capacity, job_id="ops-blocker", footprint="small", cores=1)
    head = _request(job_id="ops-a-head", priority="gate", footprint="large", cores=1)
    nested: dict[str, object] = {}

    def advance_then_try_backfill(seconds):
        clock.now += 60
        try:
            nested["result"] = book.submit(
                _request(job_id="ops-z-small", priority="gate", footprint="tiny"),
                authenticated_group="ops", capacity_provider=lambda: capacity, wait_s=0,
            )
        except AdmissionError as exc:
            nested["error"] = exc
        blocker.handle.complete()

    clock.on_wait = advance_then_try_backfill
    result = book.submit(head, authenticated_group="ops", capacity_provider=lambda: capacity, wait_s=120)
    assert isinstance(nested.get("error"), AdmissionError)
    assert nested["error"].exit_code == 75
    assert result.record["job_id"] == "ops-a-head"
    result.handle.complete()


def test_merge_gate_priority_passes_an_aged_lower_priority_head(tmp_path):
    clock = _FakeClock()
    capacity = _capacity(aging_s=60)
    book = _book(tmp_path / "state", clock=clock)
    blocker = _admit(book, capacity, job_id="ops-blocker", footprint="small", cores=1)
    head = _request(job_id="ops-a-head", priority="campaign", footprint="large", cores=1)
    nested: dict[str, object] = {}

    def advance_then_submit_merge(seconds):
        clock.now += 60
        result = book.submit(
            _request(job_id="ops-z-merge", priority="merge-gate", footprint="tiny"),
            authenticated_group="ops", capacity_provider=lambda: capacity, wait_s=0,
        )
        nested["result"] = result
        result.handle.complete()
        blocker.handle.complete()

    clock.on_wait = advance_then_submit_merge
    result = book.submit(head, authenticated_group="ops", capacity_provider=lambda: capacity, wait_s=120)
    assert nested["result"].record["priority"] == "merge-gate"
    assert result.record["job_id"] == "ops-a-head"
    result.handle.complete()


def test_unaged_head_allows_a_small_backfill_that_fits(tmp_path):
    clock = _FakeClock()
    capacity = _capacity(aging_s=60)
    book = _book(tmp_path / "state", clock=clock)
    blocker = _admit(book, capacity, job_id="ops-blocker", footprint="small", cores=1)
    head = _request(job_id="ops-a-head", priority="gate", footprint="large", cores=1)
    nested: dict[str, object] = {}

    def try_backfill(seconds):
        result = book.submit(
            _request(job_id="ops-z-small", priority="gate", footprint="tiny"),
            authenticated_group="ops", capacity_provider=lambda: capacity, wait_s=0,
        )
        nested["result"] = result
        result.handle.complete()
        blocker.handle.complete()

    clock.on_wait = try_backfill
    result = book.submit(head, authenticated_group="ops", capacity_provider=lambda: capacity, wait_s=120)
    assert nested["result"].record["job_id"] == "ops-z-small"
    assert result.record["job_id"] == "ops-a-head"
    result.handle.complete()


def test_cancel_and_status_match_stored_waiter_group(tmp_path):
    clock = _FakeClock()
    blocked_capacity = _capacity(warm=7 * GIB)
    book = _book(tmp_path / "state", clock=clock)
    request = _request(job_id="ops-cancel")
    outcomes: dict[str, object] = {}

    def inspect_then_cancel(seconds):
        outcomes["status"] = book.status("ops-cancel", "ops")
        with pytest.raises(AdmissionError, match="different authenticated group"):
            book.status("ops-cancel", "other")
        with pytest.raises(AdmissionError, match="different authenticated group"):
            book.cancel("ops-cancel", "other")
        outcomes["cancel"] = book.cancel("ops-cancel", "ops")
        clock.now += seconds

    clock.on_wait = inspect_then_cancel
    with pytest.raises(AdmissionError, match="cancelled and withdrawn") as raised:
        book.submit(request, authenticated_group="ops", capacity_provider=lambda: blocked_capacity, wait_s=10)
    assert raised.value.exit_code == 143
    assert outcomes["status"]["state"] == "waiting"
    assert outcomes["cancel"]["cancelled"] is True
    assert book.status("ops-cancel", "ops") is None


def test_dead_waiter_disappears_after_its_flock_owner_exits(tmp_path):
    clock = _FakeClock(start=100)
    capacity = _capacity()
    book = _book(tmp_path / "state", clock=clock)
    old_waiter_id = "ops-old-waiter"
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import fcntl, os, sys; fd=os.open(sys.argv[1], os.O_CREAT|os.O_RDWR, 0o600); "
            "fcntl.flock(fd, fcntl.LOCK_EX); print('READY', flush=True); sys.stdin.readline(); os.close(fd)",
            str(book._waiting_lock_path(old_waiter_id)),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert process.stdout.readline().strip() == "READY"
    waiting_record = {
        "schema": "fleet-admission-waiting/1",
        "job_id": old_waiter_id,
        "group": "ops",
        "digest": "d" * 64,
        "requested_priority": "gate",
        "priority": "gate",
        "footprint": "large",
        "planned_cores": 1.0,
        "submitted_at": 0.0,
        "state": "waiting",
        "cancelled": False,
    }
    book._waiting_path(old_waiter_id).write_text(json.dumps(waiting_record), encoding="utf-8")
    try:
        with pytest.raises(AdmissionError, match="earlier queue reservation") as raised:
            book.submit(_request(job_id="ops-new"), authenticated_group="ops",
                        capacity_provider=lambda: capacity, wait_s=0)
        assert raised.value.exit_code == 75
    finally:
        process.stdin.write("release\n")
        process.stdin.flush()
        process.stdin.close()
        assert process.wait() == 0
    admitted = book.submit(_request(job_id="ops-new"), authenticated_group="ops",
                           capacity_provider=lambda: capacity, wait_s=0)
    assert admitted.handle is not None
    assert book.status(old_waiter_id, "ops") is None
    admitted.handle.complete()


def test_pending_lease_is_durable_charged_and_bound_to_one_immutable_container_id(tmp_path):
    capacity = _capacity(max_planned=2 * GIB)
    inspector_calls = []

    def inspector(target):
        inspector_calls.append(target)
        return InspectorAnswer.NO_SUCH_OBJECT

    book = _book(tmp_path / "state")
    request = _lease_request()
    reserved = book.reserve_lease(
        request, authenticated_group="ops", capacity_provider=lambda: capacity,
        inspector=inspector, wait_s=0,
    )
    assert reserved.record["state"] == "pending"
    assert reserved.record["container_id"] is None
    assert reserved.record["released"] is False
    assert book.is_live("ops-lease-1") is True
    assert book.status("ops-lease-1", "ops")["container_name"] == "rg-lane-1"
    assert len(inspector_calls) >= 1

    with pytest.raises(AdmissionError, match="RAM fit") as raised:
        book.submit(_request(job_id="ops-too-large", footprint="small"), authenticated_group="ops",
                    capacity_provider=lambda: capacity, wait_s=0)
    assert raised.value.exit_code == 75

    bound = reserved.handle.bind("c" * 64)
    assert bound["state"] == "bound"
    assert bound["container_id"] == "c" * 64
    assert reserved.handle.bind("c" * 64)["container_id"] == "c" * 64
    with pytest.raises(AdmissionError, match="different immutable container id") as raised:
        reserved.handle.bind("d" * 64)
    assert raised.value.exit_code == 2
    observed = book.observe_lease("ops-lease-1", "ops", GIB)
    assert observed["current_warm_bytes"] == GIB
    with pytest.raises(AdmissionError, match="different authenticated group"):
        book.observe_lease("ops-lease-1", "other", 1)

    reserved.handle.close()
    reloaded = _book(tmp_path / "state")
    assert reloaded.status("ops-lease-1", "ops")["container_id"] == "c" * 64
    resumed = reloaded.resume_lease("ops-lease-1", "ops", "c" * 64)
    assert resumed.handle is not None
    assert resumed.record["state"] == "bound"
    with pytest.raises(AdmissionError, match="does not match the immutable binding") as raised:
        reloaded.resume_lease("ops-lease-1", "ops", "d" * 64)
    assert raised.value.exit_code == 2
    with pytest.raises(AdmissionError, match="different authenticated group"):
        reloaded.resume_lease("ops-lease-1", "other", "c" * 64)
    resumed.handle.close()


@pytest.mark.parametrize(
    "answer, released",
    [
        (InspectorAnswer.PRESENT, False),
        (InspectorAnswer.ERROR, False),
        ("unrecognized", False),
        (InspectorAnswer.NO_SUCH_OBJECT, True),
    ],
)
def test_lease_revoke_releases_only_on_explicit_no_such_object(tmp_path, answer, released):
    capacity = _capacity(max_planned=2 * GIB)
    book = _book(tmp_path / "state")
    request = _lease_request()
    reserved = book.reserve_lease(
        request, authenticated_group="ops", capacity_provider=lambda: capacity,
        inspector=lambda _name: InspectorAnswer.NO_SUCH_OBJECT, wait_s=0,
    )
    reserved.handle.bind("e" * 64)
    reserved.handle.close()
    result = book.reconcile_lease("ops-lease-1", "ops", lambda target: answer)
    assert result["state"] == "tombstone"
    assert result["released"] is released
    assert result["last_inspection"] == (answer.value if isinstance(answer, InspectorAnswer) else "unknown")

    if released:
        new = _admit(book, capacity, job_id="ops-after-release", footprint="small")
        new.handle.complete()
    else:
        with pytest.raises(AdmissionError, match="RAM fit") as raised:
            _admit(book, capacity, job_id="ops-before-release", footprint="small")
        assert raised.value.exit_code == 75


def test_lease_name_tombstone_blocks_reuse_even_after_release_and_inspector_errors_keep_charge(tmp_path):
    capacity = _capacity()
    book = _book(tmp_path / "state")
    reserved = book.reserve_lease(
        _lease_request(), authenticated_group="ops", capacity_provider=lambda: capacity,
        inspector=lambda _name: InspectorAnswer.NO_SUCH_OBJECT, wait_s=0,
    )
    reserved.handle.close()
    released = book.reconcile_lease(
        "ops-lease-1", "ops", lambda _target: InspectorAnswer.NO_SUCH_OBJECT
    )
    assert released["released"] is True
    with pytest.raises(AdmissionError, match="already tombstoned or leased") as raised:
        book.reserve_lease(
            _lease_request(lease_id="ops-lease-2"), authenticated_group="ops",
            capacity_provider=lambda: capacity, inspector=lambda _name: InspectorAnswer.NO_SUCH_OBJECT,
            wait_s=0,
        )
    assert raised.value.exit_code == 2

    reappeared = book.reconcile_lease(
        "ops-lease-1", "ops", lambda _target: InspectorAnswer.ERROR
    )
    assert reappeared["released"] is False
    with pytest.raises(AdmissionError, match="RAM fit") as raised:
        _admit(book, capacity, job_id="ops-still-charged", footprint="large")
    assert raised.value.exit_code == 75


def test_lease_name_collision_and_unknown_inspector_answer_fail_closed(tmp_path):
    capacity = _capacity()
    present = _book(tmp_path / "present")
    with pytest.raises(AdmissionError, match="already exists") as raised:
        present.reserve_lease(
            _lease_request(), authenticated_group="ops", capacity_provider=lambda: capacity,
            inspector=lambda _name: InspectorAnswer.PRESENT, wait_s=0,
        )
    assert raised.value.exit_code == 2

    unknown = _book(tmp_path / "unknown")
    with pytest.raises(AdmissionError, match="could not be confirmed absent") as raised:
        unknown.reserve_lease(
            _lease_request(), authenticated_group="ops", capacity_provider=lambda: capacity,
            inspector=lambda _name: InspectorAnswer.ERROR, wait_s=0,
        )
    assert raised.value.exit_code == 75


def test_lease_priority_and_waiters_share_the_host_book(tmp_path):
    clock = _FakeClock()
    capacity = _capacity(aging_s=60)
    book = _book(tmp_path / "state", clock=clock)
    blocker = _admit(book, capacity, job_id="ops-blocker", footprint="small")
    head = _request(job_id="ops-a-head", priority="campaign", footprint="large")
    nested = {}

    def let_merge_lease_pass(seconds):
        clock.now += 60
        lease = book.reserve_lease(
            _lease_request(lease_id="ops-z-lease", container_name="rg-merge", priority="merge-gate",
                           footprint="tiny"),
            authenticated_group="ops", capacity_provider=lambda: capacity,
            inspector=lambda _name: InspectorAnswer.NO_SUCH_OBJECT, wait_s=0,
        )
        nested["lease"] = lease
        lease.handle.close()
        book.reconcile_lease("ops-z-lease", "ops", lambda _target: InspectorAnswer.NO_SUCH_OBJECT)
        blocker.handle.complete()

    clock.on_wait = let_merge_lease_pass
    result = book.submit(head, authenticated_group="ops", capacity_provider=lambda: capacity, wait_s=120)
    assert nested["lease"].record["priority"] == "merge-gate"
    assert result.record["job_id"] == "ops-a-head"
    result.handle.complete()
