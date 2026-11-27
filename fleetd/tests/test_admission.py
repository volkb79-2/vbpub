from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import json
from dataclasses import replace

import pytest
import fleetd.admission as admission_module

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
    ExitCode,
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
    legacy_planned_cores=1.0,
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
            _fact(charges),
            _fact({"ops": ("merge-gate", "dev", "gate", "campaign")}),
            _fact({"checkout": 0, "docker": 0}),
            Fact.unknown("not-configured: facts.toml.policy.max_upload_store_bytes"),
            _fact(3.0),
            _fact({"wait_s": 0.0, "settle_s": 0.0}),
            _fact(aging_s),
            _fact(legacy_charge),
            _fact(legacy_planned_cores),
        ),
        guard=GuardFacts(_fact(False), Fact.unknown("guard absent"), Fact.unknown("guard absent"), Fact.unknown("guard absent")),
        warm_samples_bytes_by_id=Fact.known({}),
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


@pytest.mark.parametrize(
    ("validator", "value"),
    [
        ("nonnegative_int", True),
        ("nonnegative_int", -1),
        ("nonnegative_int", 1.5),
        ("positive_int", 0),
        ("nonnegative_number", True),
        ("nonnegative_number", -0.1),
        ("nonnegative_number", float("nan")),
        ("nonnegative_number", "1"),
        pytest.param("nonnegative_number", 10**10000, id="number-overflows-float"),
    ],
)
def test_numeric_validators_refuse_invalid_values_with_two(validator, value):
    from fleetd.admission import _nonnegative_int, _nonnegative_number, _positive_int

    selected = {
        "nonnegative_int": _nonnegative_int,
        "positive_int": _positive_int,
        "nonnegative_number": _nonnegative_number,
    }[validator]
    with pytest.raises(AdmissionError) as raised:
        selected(value, "test.value")
    assert raised.value.exit_code == 2


@pytest.mark.parametrize(
    ("value", "reason"),
    [(None, None), (1, "unexpected reason")],
)
def test_fact_requires_a_reason_only_when_unknown(value, reason):
    with pytest.raises(ValueError):
        Fact(value, reason)


def test_unknown_fact_requires_with_its_original_reason_and_uses_75():
    fact = Fact.unknown("device read returned EIO")
    with pytest.raises(AdmissionError, match="memory.total_bytes unavailable: device read returned EIO") as raised:
        fact.require("memory.total_bytes")
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
    return book.submit(
        _request(job_id=job_id, run=digest, priority=priority, footprint=footprint, cores=cores),
        authenticated_group=group,
        capacity_provider=capacity_provider,
        legacy_jobs_provider=lambda: tuple(legacy_jobs),
        wait_s=0,
    )


def test_admission_record_is_durable_idempotent_group_bound_and_flocked(tmp_path):
    capacity = _capacity()
    book = _book(tmp_path / "state")
    result = _admit(book, capacity)
    assert result.existing is False
    assert result.handle is not None and result.handle.live is True
    assert result.record["charged_bytes"] == 3 * GIB // 2
    assert result.record["created_at"] == 123.5
    assert book.status("ops-run-1", "ops")["digest"] == _request(run="a" * 64).digest()
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
    assert book.cancel("ops-run-1", "ops")["state"] == "admitted"
    with pytest.raises(AdmissionError, match="different authenticated group") as raised:
        book.cancel("ops-run-1", "other")
    assert raised.value.exit_code == 2
    with pytest.raises(AdmissionError, match="prefix does not match"):
        _admit(book, capacity, group="other")
    with pytest.raises(AdmissionError, match="different request digest"):
        _admit(book, capacity, digest="b" * 64)

    result.handle.complete()
    assert result.handle.live is False
    terminal = book.status("ops-run-1", "ops")
    assert terminal["state"] == "completed"
    assert terminal["group"] == "ops"
    assert terminal["digest"] == _request(run="a" * 64).digest()
    assert "charged_bytes" not in terminal
    replay = _admit(book, capacity)
    assert replay.existing is True
    assert replay.handle is None
    assert book.is_live("ops-run-1") is False


def test_executor_terminal_details_survive_completion_and_restart(tmp_path):
    book = _book(tmp_path / "state")
    admitted = _admit(book, _capacity(), job_id="ops-result-details")
    details = {
        "verdict": "PASS",
        "exit_code": 0,
        "lane": "r0-r1",
        "api": "fleet-host-agent/1",
        "collected_at": "2026-11-27T00:00:00Z",
        "result_sha256": "a" * 64,
        "result_size": 1234,
    }

    admitted.handle.complete(terminal_details=details)

    terminal = _book(tmp_path / "state").status("ops-result-details", "ops")
    assert terminal is not None
    assert {field: terminal[field] for field in details} == details
    assert terminal["state"] == "completed"


def test_record_and_executor_flock_exist_before_queue_lock_is_released(tmp_path, monkeypatch):
    capacity = _capacity()
    book = _book(tmp_path / "state")
    original_unlock_close = admission_module._unlock_close
    observed = []

    def inspect_before_unlock(fd):
        queue_stat = book.queue_lock_path.stat()
        fd_stat = os.fstat(fd)
        if (fd_stat.st_dev, fd_stat.st_ino) == (queue_stat.st_dev, queue_stat.st_ino):
            assert book._record_path("ops-before-unlock").is_file()
            child = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import fcntl, os, sys; fd=os.open(sys.argv[1], os.O_CREAT|os.O_RDWR, 0o600); "
                    "\ntry:\n fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB); print('free')"
                    "\nexcept BlockingIOError:\n print('held')\nfinally:\n os.close(fd)",
                    str(book._job_lock_path("ops-before-unlock")),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            assert child.stdout.strip() == "held"
            observed.append("record-and-flock-ready")
        original_unlock_close(fd)

    monkeypatch.setattr(admission_module, "_unlock_close", inspect_before_unlock)
    admitted = _admit(book, capacity, job_id="ops-before-unlock")
    assert observed == ["record-and-flock-ready"]
    monkeypatch.undo()
    admitted.handle.complete()


def test_two_process_admission_decisions_serialize_one_host_capacity_book(tmp_path):
    state = tmp_path / "shared-state"
    _book(state)
    child_script = """
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "tests"))
from test_admission import _book, _capacity, _request
from fleetd.admission import AdmissionError
book = _book(sys.argv[1])
job_id = sys.argv[2]
print("READY", flush=True)
if sys.stdin.readline().strip() != "decide":
    sys.exit(0)
try:
    result = book.submit(
        _request(job_id=job_id, footprint="large"), authenticated_group="ops",
        capacity_provider=lambda: _capacity(), legacy_jobs_provider=lambda: (), wait_s=0,
    )
    print("ADMITTED:" + job_id, flush=True)
    result.handle.close()
except AdmissionError as exc:
    print("REFUSED:" + str(exc.exit_code), flush=True)
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", child_script, str(state), f"ops-process-{index}"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        for index in (1, 2)
    ]
    try:
        assert [process.stdout.readline().strip() for process in processes] == ["READY", "READY"]
        for process in processes:
            process.stdin.write("decide\n")
            process.stdin.flush()
        outcomes = [process.stdout.readline().strip() for process in processes]
        assert all(process.wait() == 0 for process in processes)
    finally:
        for process in processes:
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
            process.wait()
    assert sum(outcome.startswith("ADMITTED:") for outcome in outcomes) == 1
    assert outcomes.count("REFUSED:75") == 1
    admitted_id = next(outcome.split(":", 1)[1] for outcome in outcomes if outcome.startswith("ADMITTED:"))
    assert _book(state).status(admitted_id, "ops")["state"] == "admitted"


def test_dead_executor_charge_can_be_reacquired_for_recovery(tmp_path):
    capacity = _capacity()
    book = _book(tmp_path / "state")
    original = _admit(book, capacity)
    original.handle.close()
    assert book.is_live("ops-run-1") is False
    with pytest.raises(AdmissionError, match="RAM fit") as raised:
        _admit(book, capacity, job_id="ops-dead-holder-still-charged", footprint="large")
    assert raised.value.exit_code == 75

    resumed = book.resume_job("ops-run-1", "ops")
    assert resumed.handle is not None
    assert resumed.existing is False
    assert book.is_live("ops-run-1") is True
    assert book.resume_job("ops-run-1", "ops").handle is None
    with pytest.raises(AdmissionError, match="different authenticated group") as raised:
        book.resume_job("ops-run-1", "other")
    assert raised.value.exit_code == 2
    resumed.handle.complete()
    assert book.status("ops-run-1", "ops")["state"] == "completed"


def test_job_handle_handoff_keeps_flock_until_child_closes_inherited_fd(tmp_path):
    capacity = _capacity()
    book = _book(tmp_path / "state")
    admitted = _admit(book, capacity)
    fd = admitted.handle.fileno()
    process = subprocess.Popen(
        [sys.executable, "-c", "import os, sys; fd=int(sys.argv[1]); print('READY', flush=True); "
         "sys.stdin.readline(); os.close(fd)", str(fd)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1,
        pass_fds=(fd,),
    )
    assert process.stdout.readline().strip() == "READY"
    admitted.handle.handoff()
    assert admitted.handle.live is False
    assert book.is_live("ops-run-1") is True
    process.stdin.write("release\n")
    process.stdin.flush()
    process.stdin.close()
    assert process.wait() == 0
    assert book.is_live("ops-run-1") is False
    resumed = book.resume_job("ops-run-1", "ops")
    assert resumed.handle is not None
    resumed.handle.complete()


def test_terminal_replay_binds_group_and_digest_and_unreadable_terminal_refuses(tmp_path):
    capacity = _capacity()
    book = _book(tmp_path / "state")
    job_id = "ops-ci-completed"
    first = _admit(book, capacity, job_id=job_id, group="ops")
    first.handle.complete()

    with pytest.raises(AdmissionError, match="different authenticated group") as raised:
        _admit(book, capacity, job_id=job_id, group="ops-ci")
    assert raised.value.exit_code == 2
    with pytest.raises(AdmissionError, match="different request digest") as raised:
        _admit(book, capacity, job_id=job_id, group="ops", digest="b" * 64)
    assert raised.value.exit_code == 2

    path = book._terminal_path(job_id)
    path.write_text("{torn", encoding="utf-8")
    with pytest.raises(AdmissionError, match="terminal record .* is unreadable") as raised:
        _admit(book, capacity, job_id=job_id, group="ops")
    assert raised.value.exit_code == 75
    assert path.read_text(encoding="utf-8") == "{torn"


def test_durable_terminal_wins_if_admitted_file_removal_fails(tmp_path, monkeypatch):
    capacity = _capacity()
    book = _book(tmp_path / "state")
    admitted = _admit(book, capacity)
    active_path = book._record_path("ops-run-1")
    original_unlink = type(active_path).unlink

    def fail_admitted_unlink(path, *args, **kwargs):
        if path == active_path:
            raise OSError("injected crash after terminal write")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(type(active_path), "unlink", fail_admitted_unlink)
    admitted.handle.complete()
    assert active_path.exists()
    assert book._terminal_path("ops-run-1").exists()
    assert book.status("ops-run-1", "ops")["state"] == "completed"
    assert not book.is_live("ops-run-1")
    monkeypatch.setattr(type(active_path), "unlink", original_unlink)

    next_job = _admit(book, capacity, job_id="ops-next", footprint="large")
    next_job.handle.complete()


@pytest.mark.parametrize(
    "capacity, reason, exit_code",
    [
        (replace(_capacity(), load1=Fact.known(6.0)), "load1", 75),
        (replace(_capacity(), psi=replace(_capacity().psi, memory=PressureFacts(Fact.known(0.0), Fact.known(3.0)))),
         "memory full avg10", 75),
        (replace(_capacity(), psi=replace(_capacity().psi, memory=PressureFacts(Fact.known(3.0), Fact.known(0.0)))),
         "memory some avg10", 75),
        (replace(_capacity(), load1=Fact.unknown("loadavg unreadable")), "load1 unavailable", 75),
        (replace(_capacity(), cgroup_v2_mounted=Fact.known(False)), "cgroup v2", 2),
        (replace(_capacity(), guard=GuardFacts(Fact.known(True), Fact.known("enforce"), Fact.unknown("digest"),
                                               Fact.known(1))), "frozen guard units", 75),
        (replace(_capacity(), guard=GuardFacts(Fact.known(True), Fact.known("enforce"), Fact.unknown("digest"),
                                               Fact.unknown("hostobs window incomplete"))), "frozen_units unavailable", 75),
        (replace(_capacity(), guard=GuardFacts(Fact.known(True), Fact.known("unknown"), Fact.unknown("digest"),
                                               Fact.unknown("hostobs window incomplete"))), "guard state is unknown", 75),
    ],
)
def test_live_load_psi_disk_cgroup_and_freeze_rules_return_closed_refusals(
    tmp_path, capacity, reason, exit_code
):
    book = _book(tmp_path / "state")
    with pytest.raises(AdmissionError, match=reason) as raised:
        _admit(book, capacity)
    assert raised.value.exit_code == exit_code
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


def test_enforce_freeze_rule_also_refuses_lease_reservation(tmp_path):
    base = _capacity()
    frozen = replace(
        base,
        guard=GuardFacts(Fact.known(True), Fact.known("enforce"), Fact.known("f" * 64), Fact.known(1)),
    )
    book = _book(tmp_path / "state")
    with pytest.raises(AdmissionError, match="host has 1 frozen guard units") as raised:
        book.reserve_lease(
            _lease_request(), authenticated_group="ops", capacity_provider=lambda: frozen,
            legacy_jobs_provider=lambda: (), inspector=lambda _name: InspectorAnswer.NO_SUCH_OBJECT,
            wait_s=0,
        )
    assert raised.value.exit_code == 75
    assert book.status("ops-lease-1", "ops") is None


def test_prerequisite_missing_exit_code_79_is_unique():
    values = [value for name, value in vars(ExitCode).items() if name.isupper() and isinstance(value, int)]
    assert ExitCode.PREREQUISITE_MISSING == 79
    assert values.count(ExitCode.PREREQUISITE_MISSING) == 1


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

    partial = replace(base, policy=replace(base.policy, disk_floors=Fact.known({"checkout": 0})))
    with pytest.raises(AdmissionError, match="must name checkout and Docker roots") as raised:
        _admit(_book(tmp_path / "partial"), partial)
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


@pytest.mark.parametrize(
    ("capacity", "reason"),
    [
        (replace(_capacity(), cgroup_v2_mounted=Fact.known(0)), "cgroup_v2.mounted is invalid"),
        (replace(_capacity(), guard=replace(_capacity().guard, guard_present=Fact.known(0))),
         "guard.guard_present is invalid"),
    ],
)
def test_non_boolean_guard_and_cgroup_presence_facts_refuse(tmp_path, capacity, reason):
    with pytest.raises(AdmissionError, match=reason) as raised:
        _admit(_book(tmp_path / "state"), capacity)
    assert raised.value.exit_code == 75


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


def test_warm_samples_are_fresh_provider_facts_and_missing_samples_reserve_full_charge(tmp_path):
    initial = _capacity(total=16 * GIB, reserve=GIB, high=8 * GIB, maximum=8 * GIB,
                        max_planned=16 * GIB)
    book = _book(tmp_path / "state")
    live = _admit(book, initial, job_id="ops-warm", footprint="large")
    book.observe_job("ops-warm", "ops", 5 * GIB)
    with pytest.raises(AdmissionError, match="different authenticated group") as raised:
        book.observe_job("ops-warm", "other", 5 * GIB)
    assert raised.value.exit_code == 2
    with pytest.raises(AdmissionError, match="unreadable; its charge is retained") as raised:
        book.observe_job("ops-missing", "ops", 1)
    assert raised.value.exit_code == 75

    constrained = _capacity(total=16 * GIB, reserve=GIB, high=4 * GIB, maximum=8 * GIB,
                            max_planned=16 * GIB)
    constrained = replace(
        constrained,
        warm_samples_bytes_by_id=Fact.unknown("hostobs sample window incomplete"),
    )
    with pytest.raises(AdmissionError, match="RAM fit -2") as raised:
        _admit(book, constrained, job_id="ops-no-sample", footprint="small")
    assert raised.value.exit_code == 75

    sampled = replace(
        constrained,
        warm_samples_bytes_by_id=Fact.known({"ops-warm": 5 * GIB}),
    )
    next_job = _admit(book, sampled, job_id="ops-fresh-sample", footprint="small")
    assert next_job.handle is not None
    next_job.handle.complete()
    live.handle.complete()


def test_legacy_jobs_are_each_charged_policy_memory_and_cores(tmp_path):
    capacity = _capacity(legacy_charge=2 * GIB, legacy_planned_cores=5)
    book = _book(tmp_path / "state")
    with pytest.raises(AdmissionError, match="planned-core headroom 0.0 is below request 1") as raised:
        _admit(book, capacity, legacy_jobs=("legacy-without-EXIT",))
    assert raised.value.exit_code == 75

    unknown_cores = replace(
        capacity,
        policy=replace(capacity.policy, legacy_planned_cores=Fact.unknown("legacy core policy missing")),
    )
    with pytest.raises(AdmissionError, match="policy.legacy_planned_cores unavailable: legacy core policy missing") as raised:
        _admit(_book(tmp_path / "unknown-cores"), unknown_cores,
               legacy_jobs=("legacy-without-EXIT",))
    assert raised.value.exit_code == 75

    unknown_charge = replace(
        capacity,
        policy=replace(capacity.policy, legacy_charge_bytes=Fact.unknown("legacy policy missing")),
    )
    with pytest.raises(AdmissionError, match="policy.legacy_charge_bytes unavailable") as raised:
        _admit(_book(tmp_path / "unknown"), unknown_charge, legacy_jobs=("legacy-without-EXIT",))
    assert raised.value.exit_code == 75

    too_large = _capacity(legacy_charge=5 * GIB)
    with pytest.raises(AdmissionError, match="RAM fit") as raised:
        _admit(_book(tmp_path / "charged"), too_large, footprint="large", legacy_jobs=("legacy",))
    assert raised.value.exit_code == 75

    fits = _capacity(legacy_charge=GIB, legacy_planned_cores=0.5)
    admitted = _admit(_book(tmp_path / "fits"), fits, legacy_jobs=("legacy-without-EXIT",))
    assert admitted.handle is not None
    admitted.handle.complete()


def test_legacy_jobs_provider_runs_under_queue_lock_for_each_wait_decision(tmp_path):
    clock = _FakeClock()
    capacity = _capacity()
    book = _book(tmp_path / "state", clock=clock)
    blocker = _admit(book, capacity, job_id="ops-blocker", footprint="small")
    lock_observations = []

    def legacy_jobs_provider():
        fd = os.open(book.queue_lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                lock_observations.append("held")
            else:
                lock_observations.append("free")
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
        return ()

    def release_blocker(seconds):
        blocker.handle.complete()
        clock.now += seconds

    book.waiter = release_blocker
    result = book.submit(
        _request(job_id="ops-waited", footprint="large"), authenticated_group="ops",
        capacity_provider=lambda: capacity, legacy_jobs_provider=legacy_jobs_provider, wait_s=2,
    )
    assert len(lock_observations) >= 2
    assert set(lock_observations) == {"held"}
    result.handle.complete()


def test_legacy_provider_is_refreshed_for_waiting_candidate_and_charges_new_legacy_job(tmp_path):
    clock = _FakeClock()
    capacity = _capacity(legacy_charge=6 * GIB, legacy_planned_cores=0)
    book = _book(tmp_path / "state", clock=clock)
    blocker = _admit(book, capacity, job_id="ops-blocker", footprint="large")
    provider_results = []
    blocker_released = False

    def legacy_jobs_provider():
        result = () if not provider_results else ("legacy-without-EXIT",)
        provider_results.append(result)
        return result

    def release_blocker(seconds):
        nonlocal blocker_released
        if not blocker_released:
            blocker.handle.complete()
            blocker_released = True
        clock.now += seconds

    book.waiter = release_blocker
    with pytest.raises(AdmissionError, match="waiter withdrawn and the book confirms the job never started") as raised:
        book.submit(
            _request(job_id="ops-legacy-waiter"), authenticated_group="ops",
            capacity_provider=lambda: capacity, legacy_jobs_provider=legacy_jobs_provider, wait_s=0.5,
        )
    assert raised.value.exit_code == 75
    assert provider_results == [(), ("legacy-without-EXIT",)]
    assert book.status("ops-legacy-waiter", "ops") is None


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


@pytest.mark.parametrize("record_kind", ["job", "lease", "waiter", "terminal"])
@pytest.mark.parametrize("shape", ["corrupt", "symlink", "nonfile"])
def test_unreadable_job_lease_and_waiter_records_fail_closed_without_mutation(
    tmp_path, record_kind, shape
):
    capacity = _capacity()
    book = _book(tmp_path / f"{record_kind}-{shape}")
    waiter_process = None
    if record_kind == "job":
        existing = _admit(book, capacity, job_id="ops-existing-job")
        existing.handle.close()
        path = book._record_path("ops-existing-job")
    elif record_kind == "lease":
        existing = book.reserve_lease(
            _lease_request(lease_id="ops-existing-lease"), authenticated_group="ops",
            capacity_provider=lambda: capacity, legacy_jobs_provider=lambda: (),
            inspector=lambda _name: InspectorAnswer.NO_SUCH_OBJECT, wait_s=0,
        )
        existing.handle.close()
        path = book._lease_path("ops-existing-lease")
    elif record_kind == "waiter":
        waiter_id = "ops-existing-waiter"
        waiter_process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import fcntl, os, sys; fd=os.open(sys.argv[1], os.O_CREAT|os.O_RDWR, 0o600); "
                "fcntl.flock(fd, fcntl.LOCK_EX); print('READY', flush=True); sys.stdin.readline(); os.close(fd)",
                str(book._waiting_lock_path(waiter_id)),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        assert waiter_process.stdout.readline().strip() == "READY"
        path = book._waiting_path(waiter_id)
    else:
        terminal = {
            "schema": "fleet-admission-terminal/1", "job_id": "ops-existing-terminal", "group": "ops",
            "digest": "a" * 64, "state": "completed", "completed_at": 123.5,
        }
        path = book._terminal_path("ops-existing-terminal")
        admission_module._atomic_new_json(path, terminal)

    target = tmp_path / f"{record_kind}-{shape}-target"
    if path.exists() or path.is_symlink():
        if path.is_dir() and not path.is_symlink():
            path.rmdir()
        else:
            path.unlink()
    if shape == "corrupt":
        path.write_text("{torn", encoding="utf-8")
    elif shape == "symlink":
        target.write_text("{}", encoding="utf-8")
        path.symlink_to(target)
    else:
        path.mkdir()

    try:
        with pytest.raises(AdmissionError) as raised:
            if record_kind == "terminal":
                book.status("ops-existing-terminal", "ops")
            else:
                _admit(book, capacity, job_id="ops-new-after-record-error")
        assert raised.value.exit_code == 75
        assert path.exists() or path.is_symlink()
        if shape == "corrupt":
            assert path.read_text(encoding="utf-8") == "{torn"
        elif shape == "symlink":
            assert path.is_symlink() and target.read_text(encoding="utf-8") == "{}"
        else:
            assert path.is_dir()
    finally:
        if waiter_process is not None:
            waiter_process.stdin.write("release\n")
            waiter_process.stdin.flush()
            waiter_process.stdin.close()
            assert waiter_process.wait() == 0


def _parser_record(record_kind):
    common = {"group": "ops", "digest": "a" * 64, "priority": "gate", "footprint": "small",
              "planned_cores": 1, "charged_bytes": 1, "current_warm_bytes": 0, "created_at": 1}
    if record_kind == "job":
        return {**common, "schema": "fleet-admission/1", "job_id": "ops-record", "state": "admitted"}
    if record_kind == "waiter":
        return {"schema": "fleet-admission-waiting/1", "job_id": "ops-record", **common,
                "requested_priority": "gate", "submitted_at": 1, "state": "waiting",
                "cancelled": False, "record_kind": "job"}
    if record_kind == "lease":
        return {"schema": "fleet-admission-lease/1", "lease_id": "ops-record", **common,
                "container_name": "runner", "container_id": None, "state": "pending", "released": False}
    return {"schema": "fleet-admission-terminal/1", "job_id": "ops-record", "group": "ops",
            "digest": "a" * 64, "state": "completed", "completed_at": 1}


def _parser_path(tmp_path, record_kind):
    return tmp_path / f"{record_kind}.json"


@pytest.mark.parametrize(
    ("record_kind", "field", "value"),
    [
        ("job", "root", []), ("job", "job_id", "wrong"), ("job", "group", None),
        ("job", "digest", None), ("job", "digest", "x"), ("job", "priority", []),
        ("job", "priority", "other"), ("job", "footprint", []), ("job", "footprint", "other"),
        ("job", "state", ["admitted"]), ("job", "charged_bytes", 0),
        ("job", "current_warm_bytes", -1), ("job", "planned_cores", 0),
        ("job", "created_at", -1),
        ("waiter", "root", []), ("waiter", "job_id", "wrong"), ("waiter", "group", None),
        ("waiter", "digest", None), ("waiter", "digest", "x"), ("waiter", "priority", []),
        ("waiter", "priority", "other"), ("waiter", "requested_priority", []),
        ("waiter", "requested_priority", "other"), ("waiter", "footprint", []),
        ("waiter", "footprint", "other"), ("waiter", "state", "admitted"),
        ("waiter", "cancelled", 1), ("waiter", "record_kind", []),
        ("waiter", "record_kind", "other"), ("waiter", "planned_cores", 0),
        ("waiter", "submitted_at", -1),
        ("lease", "root", []), ("lease", "lease_id", "wrong"), ("lease", "group", None),
        ("lease", "digest", None), ("lease", "digest", "x"), ("lease", "container_name", None),
        ("lease", "container_name", ""), ("lease", "priority", []), ("lease", "priority", "other"),
        ("lease", "footprint", []), ("lease", "footprint", "other"), ("lease", "state", []),
        ("lease", "state", "other"), ("lease", "released", 1), ("lease", "container_id", []),
        ("lease", "container_id", ""), ("lease", "state", "bound"),
        ("lease", "released_state", "bound"), ("lease", "charged_bytes", 0),
        ("lease", "current_warm_bytes", -1), ("lease", "planned_cores", 0),
        ("lease", "created_at", -1),
        ("terminal", "root", []), ("terminal", "job_id", "wrong"),
        ("terminal", "schema", "other"), ("terminal", "state", []),
        ("terminal", "group", ""), ("terminal", "digest", "x"), ("terminal", "completed_at", -1),
    ],
)
def test_record_field_tampering_is_a_75_refusal_and_never_rewrites_input(
    tmp_path, record_kind, field, value
):
    parser = {
        "job": admission_module._read_json_record,
        "waiter": admission_module._read_waiter_record,
        "lease": admission_module._read_lease_record,
        "terminal": admission_module._read_terminal_record,
    }[record_kind]
    record = _parser_record(record_kind)
    if field == "root":
        record = value
    elif field == "released_state":
        record["state"] = value
        record["released"] = True
        record["container_id"] = "container-immutable-id"
    elif field == "state" and record_kind == "lease" and value == "bound":
        record["state"] = value
        record["container_id"] = None
    else:
        record[field] = value
    path = _parser_path(tmp_path, record_kind)
    raw = json.dumps(record).encode("utf-8")
    path.write_bytes(raw)
    with pytest.raises(AdmissionError) as raised:
        parser(path, expected_id="ops-record")
    assert raised.value.exit_code == 75
    assert path.read_bytes() == raw


@pytest.mark.parametrize("record_kind", ["job", "waiter", "lease", "terminal"])
@pytest.mark.parametrize("payload_kind", ["invalid_utf8", "deep_json", "oversized_integer"])
def test_record_decoding_failures_are_closed_75_and_leave_files_untouched(
    tmp_path, record_kind, payload_kind
):
    parser = {
        "job": admission_module._read_json_record,
        "waiter": admission_module._read_waiter_record,
        "lease": admission_module._read_lease_record,
        "terminal": admission_module._read_terminal_record,
    }[record_kind]
    if payload_kind == "invalid_utf8":
        raw = b"\xff"
    elif payload_kind == "deep_json":
        raw = (b"[" * 1100) + (b"]" * 1100)
    else:
        raw = b'{"job_id":"ops-record","planned_cores":' + b"9" * 5000 + b"}"
    path = _parser_path(tmp_path, record_kind)
    path.write_bytes(raw)
    with pytest.raises(AdmissionError) as raised:
        parser(path, expected_id="ops-record")
    assert raised.value.exit_code == 75
    assert path.read_bytes() == raw


@pytest.mark.parametrize("record_kind", ["job", "waiter", "lease", "terminal"])
def test_valid_record_parsers_return_normalized_records(tmp_path, record_kind):
    parser = {
        "job": admission_module._read_json_record,
        "waiter": admission_module._read_waiter_record,
        "lease": admission_module._read_lease_record,
        "terminal": admission_module._read_terminal_record,
    }[record_kind]
    record = _parser_record(record_kind)
    path = _parser_path(tmp_path, record_kind)
    path.write_text(json.dumps(record), encoding="utf-8")
    parsed = parser(path, expected_id="ops-record")
    assert parsed["group"] == "ops"
    assert parsed["digest"] == "a" * 64
    if record_kind == "terminal":
        assert parsed["state"] == "completed"
    elif record_kind == "waiter":
        assert parsed["record_kind"] == "job"
        assert parsed["submitted_at"] == 1.0
    else:
        assert parsed["planned_cores"] == 1.0


def test_lock_open_flock_and_unlock_faults_fail_closed_and_close_descriptors(tmp_path, monkeypatch):
    lock_path = tmp_path / "book.lock"
    real_open = admission_module.os.open
    with monkeypatch.context() as patcher:
        patcher.setattr(admission_module.os, "open", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("open fault")))
        with pytest.raises(AdmissionError, match="cannot open admission lock") as raised:
            admission_module._open_lock(lock_path, blocking=False)
        assert raised.value.exit_code == 75

    with monkeypatch.context() as patcher:
        patcher.setattr(admission_module.fcntl, "flock", lambda *_args: (_ for _ in ()).throw(BlockingIOError()))
        assert admission_module._open_lock(lock_path, blocking=False) is None
    with monkeypatch.context() as patcher:
        patcher.setattr(admission_module.fcntl, "flock", lambda *_args: (_ for _ in ()).throw(OSError("flock fault")))
        with pytest.raises(AdmissionError, match="cannot lock admission file") as raised:
            admission_module._open_lock(lock_path, blocking=False)
        assert raised.value.exit_code == 75

    symlink = tmp_path / "symlink.lock"
    target = tmp_path / "target.lock"
    target.write_bytes(b"")
    symlink.symlink_to(target)
    with pytest.raises(AdmissionError, match="cannot open admission lock") as raised:
        admission_module._open_lock(symlink, blocking=False)
    assert raised.value.exit_code == 75

    fd = real_open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    with monkeypatch.context() as patcher:
        patcher.setattr(admission_module.fcntl, "flock", lambda *_args: (_ for _ in ()).throw(OSError("unlock fault")))
        with pytest.raises(OSError, match="unlock fault"):
            admission_module._unlock_close(fd)
    with pytest.raises(OSError):
        os.fstat(fd)


def test_atomic_serialization_and_rollback_faults_leave_no_new_charge(tmp_path, monkeypatch):
    path = tmp_path / "serialization.json"
    with pytest.raises(AdmissionError, match="cannot atomically write admission record") as raised:
        admission_module._atomic_json(path, {"unsupported": object()})
    assert raised.value.exit_code == 75
    assert not path.exists()

    book = _book(tmp_path / "state")
    real_atomic = admission_module._atomic_json
    real_fsync_dir = admission_module._fsync_dir
    monkeypatch.setattr(
        admission_module, "_atomic_json",
        lambda *_args: (_ for _ in ()).throw(AdmissionError(75, "injected first write failure")),
    )
    monkeypatch.setattr(
        admission_module, "_fsync_dir",
        lambda *_args: (_ for _ in ()).throw(OSError("injected rollback fsync failure")),
    )
    with pytest.raises(AdmissionError, match="rollback could not be confirmed") as raised:
        _admit(book, _capacity(), job_id="ops-rollback-failure")
    assert raised.value.exit_code == 75
    assert not book._record_path("ops-rollback-failure").exists()
    monkeypatch.setattr(admission_module, "_atomic_json", real_atomic)
    monkeypatch.setattr(admission_module, "_fsync_dir", real_fsync_dir)
    lock_fd = admission_module._open_lock(book._job_lock_path("ops-rollback-failure"), blocking=False)
    assert lock_fd is not None
    admission_module._unlock_close(lock_fd)


def test_job_handle_closed_and_handoff_noops_are_safe_and_keep_record(tmp_path):
    capacity = _capacity()
    book = _book(tmp_path / "state")
    admitted = _admit(book, capacity, job_id="ops-handoff-noop")
    assert admitted.handle.fileno() >= 0
    admitted.handle.handoff()
    assert admitted.handle.live is False
    admitted.handle.handoff()
    admitted.handle.close()
    with pytest.raises(AdmissionError, match="job handle is closed") as raised:
        admitted.handle.fileno()
    assert raised.value.exit_code == 2
    assert book._record_path("ops-handoff-noop").exists()
    recovered = book.resume_job("ops-handoff-noop", "ops")
    assert recovered.handle is not None
    recovered.handle.complete()

    lease = book.reserve_lease(
        _lease_request(lease_id="ops-close-noop"), authenticated_group="ops",
        capacity_provider=lambda: capacity, legacy_jobs_provider=lambda: (),
        inspector=lambda _name: InspectorAnswer.NO_SUCH_OBJECT, wait_s=0,
    )
    lease.handle.close()
    lease.handle.close()
    assert lease.handle.live is False
    assert book.status("ops-close-noop", "ops")["state"] == "pending"


def test_private_replay_group_and_priority_validators_fail_closed(tmp_path):
    book = _book(tmp_path / "state")
    capacity = _capacity()
    admitted = _admit(book, capacity, job_id="ops-private-replay")
    with pytest.raises(AdmissionError, match="different authenticated group") as raised:
        book._existing_admission_locked("ops-private-replay", "other", admitted.record["digest"])
    assert raised.value.exit_code == 2
    with pytest.raises(AdmissionError, match="unknown priority") as raised:
        admission_module._clamp_priority(capacity, "ops", "unknown")
    assert raised.value.exit_code == 2
    admitted.handle.complete()


def test_state_initialization_and_record_directory_read_errors_are_75(tmp_path, monkeypatch):
    occupied = tmp_path / "occupied"
    occupied.write_text("file", encoding="utf-8")
    with pytest.raises(AdmissionError, match="cannot initialize admission state") as raised:
        _book(occupied / "state")
    assert raised.value.exit_code == 75

    book = _book(tmp_path / "read-errors")
    original_iterdir = type(book.admitted_dir).iterdir

    def fail_selected_directory(path):
        if path == book.admitted_dir or path == book.leases_dir:
            raise OSError("directory read fault")
        return original_iterdir(path)

    monkeypatch.setattr(type(book.admitted_dir), "iterdir", fail_selected_directory)
    with pytest.raises(AdmissionError, match="cannot read admission records") as raised:
        book._load_records()
    assert raised.value.exit_code == 75
    with pytest.raises(AdmissionError, match="cannot read lease records") as raised:
        book._lease_records_locked()
    assert raised.value.exit_code == 75


@pytest.mark.parametrize(
    ("scenario", "exit_code"),
    [
        ("unknown_footprint", 2), ("invalid_max_footprint", 2), ("missing_charge", 2),
        ("boolean_charge", 2), ("charge_exceeds_static_ram", 2), ("planned_cores_exceed_cap", 2),
        ("zero_planned_cores", 2), ("unknown_policy_fact", 75),
        ("load_boolean", 75), ("load_text", 75), ("load_negative", 75),
        ("load_huge", 75), ("load_infinite", 75), ("load_over_limit", 75),
        ("full_psi_unknown", 75), ("full_psi_boolean", 75), ("full_psi_huge", 75),
        ("full_psi_out_of_range", 75), ("some_psi_threshold", 75),
        ("some_psi_policy_over_100", 2), ("some_psi_unknown", 75),
        ("guard_state_unknown_fact", 75), ("guard_state_non_string", 75),
        ("guard_state_unknown_value", 75), ("frozen_units_unknown", 75),
        ("frozen_units_boolean", 2), ("disk_policy_non_mapping", 2),
        ("disk_policy_empty", 2), ("disk_policy_missing_checkout", 2),
        ("disk_policy_missing_docker", 2), ("disk_policy_bad_name", 2),
        ("disk_policy_bad_floor", 2), ("disks_non_tuple", 75),
        ("disk_name_unknown", 75), ("disk_name_bad", 75), ("disk_duplicate", 2),
        ("disk_floor_unknown", 75), ("disk_floor_disagrees", 2),
    ],
)
def test_capacity_and_live_fact_failure_paths_return_exact_closed_code(tmp_path, scenario, exit_code):
    capacity = _capacity()
    if scenario == "unknown_footprint":
        with pytest.raises(AdmissionError) as raised:
            static_eligibility(capacity, "unknown", 1)
    elif scenario == "invalid_max_footprint":
        capacity = replace(capacity, policy=replace(capacity.policy, max_footprint=_fact("invalid")))
        with pytest.raises(AdmissionError) as raised:
            static_eligibility(capacity, "small", 1)
    elif scenario in {"missing_charge", "boolean_charge"}:
        charges = {"small": True} if scenario == "boolean_charge" else {}
        capacity = replace(capacity, policy=replace(capacity.policy, footprint_charges_bytes=_fact(charges)))
        with pytest.raises(AdmissionError) as raised:
            static_eligibility(capacity, "small", 1)
    elif scenario == "charge_exceeds_static_ram":
        capacity = _capacity(charges={"small": 8 * GIB})
        with pytest.raises(AdmissionError) as raised:
            static_eligibility(capacity, "small", 1)
    elif scenario == "planned_cores_exceed_cap":
        with pytest.raises(AdmissionError) as raised:
            static_eligibility(capacity, "small", 6)
    elif scenario == "zero_planned_cores":
        with pytest.raises(AdmissionError) as raised:
            static_eligibility(capacity, "small", 0)
    else:
        if scenario == "unknown_policy_fact":
            capacity = replace(capacity, memory=replace(capacity.memory, total_bytes=Fact.unknown("RAM unavailable")))
        elif scenario.startswith("load_"):
            load = {
                "load_boolean": True, "load_text": "bad", "load_negative": -1,
                "load_huge": 10**10000, "load_infinite": float("inf"), "load_over_limit": 6,
            }[scenario]
            capacity = replace(capacity, load1=_fact(load))
        elif scenario.startswith("full_psi_"):
            full = {
                "full_psi_unknown": Fact.unknown("PSI file missing"),
                "full_psi_boolean": _fact(True), "full_psi_huge": _fact(10**10000),
                "full_psi_out_of_range": _fact(101),
            }[scenario]
            pressure = replace(capacity.psi.memory, full_avg10_pct=full)
            capacity = replace(capacity, psi=replace(capacity.psi, memory=pressure))
        elif scenario in {"some_psi_threshold", "some_psi_policy_over_100", "some_psi_unknown"}:
            some = Fact.unknown("memory PSI missing") if scenario == "some_psi_unknown" else _fact(1)
            pressure = replace(capacity.psi.memory, some_avg10_pct=some)
            policy_limit = _fact(101) if scenario == "some_psi_policy_over_100" else _fact(1)
            capacity = replace(capacity, psi=replace(capacity.psi, memory=pressure),
                               policy=replace(capacity.policy, psi_refuse_some_avg10=policy_limit))
        elif scenario.startswith("guard_") or scenario.startswith("frozen_"):
            if scenario == "guard_state_unknown_fact":
                guard = replace(capacity.guard, guard_present=_fact(True), guard_state=Fact.unknown("state missing"))
            elif scenario == "guard_state_non_string":
                guard = replace(capacity.guard, guard_present=_fact(True), guard_state=_fact([]))
            elif scenario == "guard_state_unknown_value":
                guard = replace(capacity.guard, guard_present=_fact(True), guard_state=_fact("arming"))
            elif scenario == "frozen_units_unknown":
                guard = replace(capacity.guard, guard_present=_fact(True), guard_state=_fact("enforce"),
                                frozen_units=Fact.unknown("freeze count missing"))
            else:
                guard = replace(capacity.guard, guard_present=_fact(True), guard_state=_fact("enforce"),
                                frozen_units=_fact(True))
            capacity = replace(capacity, guard=guard)
        else:
            floors = {"checkout": 0, "docker": 0}
            disks = capacity.disks.value
            if scenario == "disk_policy_non_mapping": floors = []
            elif scenario == "disk_policy_empty": floors = {}
            elif scenario == "disk_policy_missing_checkout": floors = {"docker": 0}
            elif scenario == "disk_policy_missing_docker": floors = {"checkout": 0}
            elif scenario == "disk_policy_bad_name": floors = {"": 0, "checkout": 0, "docker": 0}
            elif scenario == "disk_policy_bad_floor": floors = {"checkout": -1, "docker": 0}
            elif scenario == "disks_non_tuple": disks = []
            elif scenario == "disk_name_unknown": floors = {"checkout": 0, "docker": 0, "missing": 0}
            elif scenario == "disk_name_bad":
                disks = (replace(disks[0], name=_fact("")), disks[1])
            elif scenario == "disk_duplicate": disks = (disks[0], disks[0])
            elif scenario == "disk_floor_unknown":
                disks = (replace(disks[0], floor_bytes=Fact.unknown("floor file missing")), disks[1])
            elif scenario == "disk_floor_disagrees": floors = {"checkout": 1, "docker": 0}
            capacity = replace(capacity, disks=_fact(disks), policy=replace(capacity.policy, disk_floors=_fact(floors)))
        with pytest.raises(AdmissionError) as raised:
            _admit(_book(tmp_path / scenario), capacity, job_id=f"ops-{scenario}")
    assert raised.value.exit_code == exit_code


@pytest.mark.parametrize("record_kind", ["job", "lease"])
@pytest.mark.parametrize("failure_point", ["write", "rename", "file_fsync", "directory_fsync"])
def test_new_record_write_fault_rolls_back_record_and_releases_flock(
    tmp_path, monkeypatch, record_kind, failure_point
):
    book = _book(tmp_path / f"{record_kind}-{failure_point}")
    if failure_point == "write":
        real_fdopen = admission_module.os.fdopen

        class FailedWrite:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.stream.close()

            def write(self, _value):
                raise OSError("injected write failure")

        monkeypatch.setattr(
            admission_module.os, "fdopen", lambda fd, mode: FailedWrite(real_fdopen(fd, mode))
        )
    elif failure_point == "rename":
        monkeypatch.setattr(
            admission_module.os, "replace",
            lambda *_args: (_ for _ in ()).throw(OSError("injected rename failure")),
        )
    elif failure_point == "file_fsync":
        original_fsync = admission_module.os.fsync
        calls = 0

        def fail_file_fsync(fd):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("injected file fsync failure")
            return original_fsync(fd)

        monkeypatch.setattr(admission_module.os, "fsync", fail_file_fsync)
    else:
        original_fsync_dir = admission_module._fsync_dir
        calls = 0

        def fail_directory_fsync(path):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("injected directory fsync failure")
            return original_fsync_dir(path)

        monkeypatch.setattr(admission_module, "_fsync_dir", fail_directory_fsync)

    if record_kind == "job":
        job_id = "ops-fault-job"
        path = book._record_path(job_id)
        lock_path = book._job_lock_path(job_id)
        submit = lambda: _admit(book, _capacity(), job_id=job_id)
    else:
        job_id = "ops-fault-lease"
        path = book._lease_path(job_id)
        lock_path = book._job_lock_path(job_id)
        submit = lambda: book.reserve_lease(
            _lease_request(lease_id=job_id), authenticated_group="ops",
            capacity_provider=lambda: _capacity(), legacy_jobs_provider=lambda: (),
            inspector=lambda _name: InspectorAnswer.NO_SUCH_OBJECT, wait_s=0,
        )

    with pytest.raises(AdmissionError, match="cannot atomically write admission record") as raised:
        submit()
    assert raised.value.exit_code == 75
    assert not path.exists()
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def test_waiter_removal_failure_returns_durable_admission_and_releases_waiter_flock(tmp_path, monkeypatch):
    capacity = _capacity()
    book = _book(tmp_path / "state", clock=_FakeClock())

    def fail_waiter_removal(*_args):
        raise AdmissionError(75, "injected waiter unlink failure")

    monkeypatch.setattr(book, "_remove_waiter_locked", fail_waiter_removal)
    admitted = book.submit(
        _request(job_id="ops-waiter-cleanup"), authenticated_group="ops",
        capacity_provider=lambda: capacity, legacy_jobs_provider=lambda: (), wait_s=10,
    )
    assert admitted.handle is not None
    assert book.status("ops-waiter-cleanup", "ops")["state"] == "admitted"

    monkeypatch.undo()
    next_job = _admit(book, capacity, job_id="ops-after-waiter-cleanup")
    assert next_job.handle is not None
    admitted.handle.complete()
    next_job.handle.complete()


def test_request_digest_excludes_job_id_and_client_version_but_binds_contract_fields():
    original = _request(job_id="ops-first", version="1.0.0")
    assert original.digest() == _request(job_id="ops-second", version="9.9.9").digest()
    assert original.digest() != _request(run="run-2").digest()
    assert original.digest() != _request(priority="campaign").digest()
    assert original.digest() != _request(footprint="medium").digest()


def test_canonical_json_projection_and_digest_failures_are_closed():
    assert admission_module._plain_json({"items": (None, True, 1, 1.5, ["x"])}) == {
        "items": [None, True, 1, 1.5, ["x"]]
    }
    with pytest.raises(TypeError, match="object keys must be strings"):
        admission_module._plain_json({1: "bad"})
    with pytest.raises(ValueError, match="non-finite"):
        admission_module._plain_json(float("nan"))
    with pytest.raises(TypeError, match="unsupported JSON value"):
        admission_module._plain_json({"bad"})

    invalid_job = replace(_request(), stores={"collect": {"bad"}})
    with pytest.raises(AdmissionError, match="not canonical JSON") as raised:
        invalid_job.digest()
    assert raised.value.exit_code == 2
    nonfinite_job = replace(_request(), env={"BAD": float("nan")})
    with pytest.raises(AdmissionError, match="not canonical JSON") as raised:
        nonfinite_job.digest()
    assert raised.value.exit_code == 2
    invalid_lease = LeaseRequest("ops-lease-json", "runner", AdmissionResources(1, {"bad"}), "gate")
    with pytest.raises(AdmissionError, match="not canonical JSON") as raised:
        invalid_lease.digest()
    assert raised.value.exit_code == 2


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
        book.submit(job_request, authenticated_group="ops", capacity_provider=lambda: _capacity(),
                    legacy_jobs_provider=lambda: (), wait_s=0)
    assert raised.value.exit_code == 2
    assert book.status(job_request.job_id, "ops") is None


def test_lease_cpu_set_is_refused_in_v1(tmp_path):
    book = _book(tmp_path / "state")
    request = _lease_request()
    request = replace(request, resources=replace(request.resources, cpu_set=(0,)))
    with pytest.raises(AdmissionError, match="cpu_set is reserved") as raised:
        book.reserve_lease(
            request, authenticated_group="ops", capacity_provider=lambda: _capacity(),
            legacy_jobs_provider=lambda: (), inspector=lambda _name: InspectorAnswer.NO_SUCH_OBJECT,
            wait_s=0,
        )
    assert raised.value.exit_code == 2
    assert book.status(request.lease_id, "ops") is None


def test_group_prefix_length_cap_and_job_grammar_are_enforced(tmp_path):
    from fleetd.admission import MAX_JOB_ID_LENGTH

    book = _book(tmp_path / "state")
    with pytest.raises(AdmissionError, match="prefix does not match"):
        book.submit(_request(job_id="other-run"), authenticated_group="ops",
                    capacity_provider=lambda: _capacity(), legacy_jobs_provider=lambda: (), wait_s=0)
    with pytest.raises(AdmissionError, match="valid admission identifier"):
        book.submit(_request(job_id="ops-../escape"), authenticated_group="ops",
                    capacity_provider=lambda: _capacity(), legacy_jobs_provider=lambda: (), wait_s=0)
    with pytest.raises(AdmissionError, match="valid admission identifier"):
        book.submit(_request(job_id="ops-" + "a" * MAX_JOB_ID_LENGTH), authenticated_group="ops",
                    capacity_provider=lambda: _capacity(), legacy_jobs_provider=lambda: (), wait_s=0)


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
            book.submit(request, authenticated_group="ops", capacity_provider=lambda: capacity,
                        legacy_jobs_provider=lambda: (), wait_s=0)
        assert raised.value.exit_code == 2
    else:
        result = book.submit(request, authenticated_group="ops", capacity_provider=lambda: capacity,
                             legacy_jobs_provider=lambda: (), wait_s=0)
        assert result.record["priority"] == expected
        result.handle.complete()


def test_reclamped_waiter_priority_is_persisted_for_other_book_readers(tmp_path):
    clock = _FakeClock()
    first_policy = {"ops": ("campaign",)}
    next_policy = {"ops": ("gate",)}
    base = _capacity(max_planned=2 * GIB)
    first_capacity = replace(base, policy=replace(
        base.policy,
        priority_classes_by_principal=Fact.known(first_policy),
    ))
    next_capacity = replace(base, policy=replace(
        base.policy,
        priority_classes_by_principal=Fact.known(next_policy),
    ))
    capacities = [first_capacity]
    book = _book(tmp_path / "state", clock=clock)
    blocker = _admit(book, first_capacity, job_id="ops-blocker")
    observations = []

    def wait_for_admission(seconds):
        if not observations:
            capacities[0] = next_capacity
            observations.append("policy changed")
        elif len(observations) == 1:
            observations.append(book.status("ops-waiter", "ops")["priority"])
            blocker.handle.complete()
        clock.now += seconds

    book.waiter = wait_for_admission
    result = book.submit(
        _request(job_id="ops-waiter", priority="dev"),
        authenticated_group="ops",
        capacity_provider=lambda: capacities[0],
        legacy_jobs_provider=lambda: (),
        wait_s=2,
    )
    assert observations == ["policy changed", "gate"]
    assert result.record["priority"] == "gate"
    result.handle.complete()


def test_unknown_or_missing_priority_policy_refuses_without_inventing_class(tmp_path):
    base = _capacity()
    unknown = replace(base, policy=replace(
        base.policy,
        priority_classes_by_principal=Fact.unknown("principal policy unreadable"),
    ))
    with pytest.raises(AdmissionError, match="priority_classes_by_principal unavailable") as raised:
        _book(tmp_path / "unknown").submit(_request(), authenticated_group="ops",
                                           capacity_provider=lambda: unknown, legacy_jobs_provider=lambda: (), wait_s=0)
    assert raised.value.exit_code == 75

    missing = replace(base, policy=replace(
        base.policy,
        priority_classes_by_principal=Fact.known({}),
    ))
    with pytest.raises(AdmissionError, match="missing for authenticated group") as raised:
        _book(tmp_path / "missing").submit(_request(), authenticated_group="ops",
                                           capacity_provider=lambda: missing, legacy_jobs_provider=lambda: (), wait_s=0)
    assert raised.value.exit_code == 2


@pytest.mark.parametrize(
    ("policy", "requested", "reason"),
    [
        ([], "gate", "principal policy is invalid"),
        ({"ops": []}, "gate", "missing for authenticated group"),
        ({"ops": ("unknown",)}, "gate", "invalid class"),
        ({"ops": ("gate", "gate")}, "gate", "invalid class"),
        ({"ops": ("merge-gate",)}, "campaign", "no allowed class at or below"),
    ],
)
def test_priority_policy_validation_refuses_exactly_with_two(tmp_path, policy, requested, reason):
    capacity = _capacity()
    capacity = replace(capacity, policy=replace(
        capacity.policy, priority_classes_by_principal=_fact(policy)
    ))
    with pytest.raises(AdmissionError, match=reason) as raised:
        _admit(_book(tmp_path / reason.replace(" ", "-")), capacity,
               job_id="ops-policy-invalid", priority=requested)
    assert raised.value.exit_code == 2


def test_policy_wait_limit_is_required_when_omitted_and_aging_must_be_positive(tmp_path):
    base = _capacity()
    admitted = _book(tmp_path / "policy-wait").submit(
        _request(job_id="ops-policy-wait"), authenticated_group="ops",
        capacity_provider=lambda: base, legacy_jobs_provider=lambda: (),
    )
    assert admitted.handle is not None
    admitted.handle.complete()

    missing_wait = replace(
        base,
        policy=replace(base.policy, admission=Fact.known({"settle_s": 0.0})),
    )
    with pytest.raises(AdmissionError, match="policy.admission.wait_s is required") as raised:
        _book(tmp_path / "missing-wait").submit(
            _request(job_id="ops-missing-wait"), authenticated_group="ops",
            capacity_provider=lambda: missing_wait, legacy_jobs_provider=lambda: (),
        )
    assert raised.value.exit_code == 2

    zero_aging = _capacity(aging_s=0)
    with pytest.raises(AdmissionError, match="policy.aging_s must be positive") as raised:
        _book(tmp_path / "zero-aging").submit(
            _request(job_id="ops-zero-aging"), authenticated_group="ops",
            capacity_provider=lambda: zero_aging, legacy_jobs_provider=lambda: (), wait_s=0,
        )
    assert raised.value.exit_code == 2

    unknown_aging = replace(
        base,
        policy=replace(base.policy, aging_s=Fact.unknown("policy file absent")),
    )
    with pytest.raises(AdmissionError, match="policy.aging_s unavailable: policy file absent") as raised:
        _book(tmp_path / "unknown-aging").submit(
            _request(job_id="ops-unknown-aging"), authenticated_group="ops",
            capacity_provider=lambda: unknown_aging, legacy_jobs_provider=lambda: (), wait_s=0,
        )
    assert raised.value.exit_code == 75


@pytest.mark.parametrize(
    ("request_options", "wait_s", "priority_policy", "exit_code", "reason"),
    [
        ({"cores": 0}, 0, None, 2, "planned_cores must be positive"),
        ({"cores": True}, 0, None, 2, "finite non-negative number"),
        ({}, -1, None, 2, "wait_s must be a finite non-negative number"),
        ({}, True, None, 2, "wait_s must be a finite non-negative number"),
        ({"priority": "unknown"}, 0, None, 2, "unknown priority"),
        ({"job_id": "wrong-prefix"}, 0, None, 2, "prefix does not match"),
        ({}, 0, [], 2, "principal policy is invalid"),
        ({}, 0, {"ops": ("campaign", "campaign")}, 2, "invalid class"),
    ],
)
def test_submit_input_and_priority_validation_paths_return_two(
    tmp_path, request_options, wait_s, priority_policy, exit_code, reason
):
    capacity = _capacity()
    if priority_policy is not None:
        capacity = replace(capacity, policy=replace(
            capacity.policy, priority_classes_by_principal=_fact(priority_policy)
        ))
    with pytest.raises(AdmissionError, match=reason) as raised:
        options = dict(request_options)
        _book(tmp_path / "state").submit(
            _request(job_id=options.pop("job_id", "ops-invalid-input"), **options),
            authenticated_group="ops", capacity_provider=lambda: capacity,
            legacy_jobs_provider=lambda: (), wait_s=wait_s,
        )
    assert raised.value.exit_code == exit_code


def test_missing_admission_policy_fact_and_invalid_policy_shape_refuse(tmp_path):
    base = _capacity()
    unknown = replace(base, policy=replace(base.policy, admission=Fact.unknown("admission policy missing")))
    with pytest.raises(AdmissionError, match="policy.admission unavailable: admission policy missing") as raised:
        _book(tmp_path / "unknown").submit(
            _request(job_id="ops-unknown-admission-policy"), authenticated_group="ops",
            capacity_provider=lambda: unknown, legacy_jobs_provider=lambda: (),
        )
    assert raised.value.exit_code == 75
    invalid = replace(base, policy=replace(base.policy, admission=_fact([])))
    with pytest.raises(AdmissionError, match="policy.admission.wait_s is required") as raised:
        _book(tmp_path / "invalid").submit(
            _request(job_id="ops-invalid-admission-policy"), authenticated_group="ops",
            capacity_provider=lambda: invalid, legacy_jobs_provider=lambda: (),
        )
    assert raised.value.exit_code == 2


def test_same_request_is_idempotent_through_the_queue_api(tmp_path):
    capacity = _capacity()
    book = _book(tmp_path / "state")
    request = _request()
    first = book.submit(request, authenticated_group="ops", capacity_provider=lambda: capacity,
                        legacy_jobs_provider=lambda: (), wait_s=0)
    same = book.submit(_request(version="2.0.0"), authenticated_group="ops",
                       capacity_provider=lambda: capacity, legacy_jobs_provider=lambda: (), wait_s=0)
    assert same.existing is True
    assert same.handle is None
    assert same.record["digest"] == first.record["digest"]
    with pytest.raises(AdmissionError, match="different request digest") as raised:
        book.submit(_request(run="changed"), authenticated_group="ops",
                    capacity_provider=lambda: capacity, legacy_jobs_provider=lambda: (), wait_s=0)
    assert raised.value.exit_code == 2
    first.handle.complete()


def test_wait_expiry_withdraws_waiter_and_confirms_never_started(tmp_path):
    clock = _FakeClock()
    blocked_capacity = _capacity(warm=7 * GIB)
    book = _book(tmp_path / "state", clock=clock, waiter=clock.wait)
    observations = []

    def inspect_duplicate(seconds):
        if not observations:
            same = book.submit(
                _request(job_id="ops-expire"), authenticated_group="ops",
                capacity_provider=lambda: blocked_capacity, legacy_jobs_provider=lambda: (), wait_s=0,
            )
            assert same.existing is True and same.handle is None
            with pytest.raises(AdmissionError, match="different request digest") as changed:
                book.submit(
                    _request(job_id="ops-expire", run="changed"), authenticated_group="ops",
                    capacity_provider=lambda: blocked_capacity, legacy_jobs_provider=lambda: (), wait_s=0,
                )
            assert changed.value.exit_code == 2
            observations.append("replayed while waiting")
        clock.now += seconds

    book.waiter = inspect_duplicate
    with pytest.raises(AdmissionError, match="waiter withdrawn and the book confirms the job never started") as raised:
        book.submit(_request(job_id="ops-expire"), authenticated_group="ops",
                    capacity_provider=lambda: blocked_capacity, legacy_jobs_provider=lambda: (), wait_s=0.5)
    assert raised.value.exit_code == 75
    assert observations == ["replayed while waiting"]
    assert book.status("ops-expire", "ops") is None
    assert list(book.waiting_dir.glob("*.json")) == []


def test_wait_expiry_reached_during_capacity_decision_withdraws_before_return(tmp_path):
    clock = _FakeClock()
    blocked = _capacity(warm=7 * GIB)
    book = _book(tmp_path / "state", clock=clock)
    calls = []

    def capacity_provider():
        calls.append("decision")
        if len(calls) == 2:
            clock.now = 0.5
        return blocked

    with pytest.raises(AdmissionError, match="waiter withdrawn and the book confirms the job never started") as raised:
        book.submit(
            _request(job_id="ops-expiry-during-decision"), authenticated_group="ops",
            capacity_provider=capacity_provider, legacy_jobs_provider=lambda: (), wait_s=0.5,
        )
    assert raised.value.exit_code == 75
    assert calls == ["decision", "decision"]
    assert book.status("ops-expiry-during-decision", "ops") is None


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
                authenticated_group="ops", capacity_provider=lambda: capacity,
                legacy_jobs_provider=lambda: (), wait_s=0,
            )
        except AdmissionError as exc:
            nested["error"] = exc
        blocker.handle.complete()

    clock.on_wait = advance_then_try_backfill
    result = book.submit(head, authenticated_group="ops", capacity_provider=lambda: capacity,
                         legacy_jobs_provider=lambda: (), wait_s=120)
    assert isinstance(nested.get("error"), AdmissionError)
    assert nested["error"].exit_code == 75
    assert result.record["job_id"] == "ops-a-head"
    result.handle.complete()


def test_static_refusal_of_old_waiter_does_not_block_later_fitting_candidate(tmp_path):
    clock = _FakeClock()
    initial = _capacity()
    changed = _capacity(max_footprint="tiny", charges={"tiny": GIB // 2, "small": 3 * GIB // 2,
                                                       "medium": 4 * GIB, "large": 6 * GIB})
    capacities = [initial]
    book = _book(tmp_path / "state", clock=clock)
    blocker = _admit(book, initial, job_id="ops-blocker", footprint="large")
    outcomes = {}

    def change_facts_and_submit(seconds):
        capacities[0] = changed
        blocker.handle.complete()
        try:
            outcomes["result"] = book.submit(
                _request(job_id="ops-z-fitting", priority="gate", footprint="tiny"),
                authenticated_group="ops", capacity_provider=lambda: capacities[0],
                legacy_jobs_provider=lambda: (), wait_s=0,
            )
        except AdmissionError as exc:
            outcomes["error"] = exc
        clock.now += seconds

    clock.on_wait = change_facts_and_submit
    with pytest.raises(AdmissionError, match="exceeds host maximum") as raised:
        book.submit(
            _request(job_id="ops-a-now-impossible", priority="merge-gate", footprint="large"),
            authenticated_group="ops", capacity_provider=lambda: capacities[0],
            legacy_jobs_provider=lambda: (), wait_s=5,
        )
    assert raised.value.exit_code == 2
    assert outcomes.get("error") is None
    assert outcomes["result"].record["job_id"] == "ops-z-fitting"
    outcomes["result"].handle.complete()


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
            authenticated_group="ops", capacity_provider=lambda: capacity,
            legacy_jobs_provider=lambda: (), wait_s=0,
        )
        nested["result"] = result
        result.handle.complete()
        blocker.handle.complete()

    clock.on_wait = advance_then_submit_merge
    result = book.submit(head, authenticated_group="ops", capacity_provider=lambda: capacity,
                         legacy_jobs_provider=lambda: (), wait_s=120)
    assert nested["result"].record["priority"] == "merge-gate"
    assert result.record["job_id"] == "ops-a-head"
    result.handle.complete()


@pytest.mark.parametrize(
    ("holder_priority", "candidate_priority"),
    [("gate", "dev"), ("campaign", "dev")],
)
def test_aged_nonfitting_reservation_blocks_every_candidate_except_higher_priority_merge_gate(
    tmp_path, holder_priority, candidate_priority
):
    clock = _FakeClock()
    capacity = _capacity(aging_s=60)
    book = _book(tmp_path / "state", clock=clock)
    blocker = _admit(book, capacity, job_id="ops-blocker", footprint="small", cores=1)
    holder = _request(job_id="ops-a-holder", priority=holder_priority, footprint="large", cores=1)
    nested = {}

    def age_holder_then_submit(seconds):
        clock.now += 60
        try:
            nested["result"] = book.submit(
                _request(job_id="ops-z-candidate", priority=candidate_priority, footprint="tiny"),
                authenticated_group="ops", capacity_provider=lambda: capacity,
                legacy_jobs_provider=lambda: (), wait_s=0,
            )
        except AdmissionError as exc:
            nested["error"] = exc
        blocker.handle.complete()

    clock.on_wait = age_holder_then_submit
    result = book.submit(holder, authenticated_group="ops", capacity_provider=lambda: capacity,
                         legacy_jobs_provider=lambda: (), wait_s=120)
    assert isinstance(nested.get("error"), AdmissionError)
    assert nested["error"].exit_code == 75
    assert "reservation" in nested["error"].reason
    assert "result" not in nested
    assert result.record["job_id"] == "ops-a-holder"
    result.handle.complete()


def test_queue_order_blocks_older_fitting_waiter_and_skips_later_fitting_waiter(tmp_path):
    capacity = _capacity()
    book = _book(tmp_path / "state")
    candidate = {
        "job_id": "ops-z-candidate", "priority": "dev", "footprint": "tiny",
        "planned_cores": 1.0, "submitted_at": 10.0, "cancelled": False,
    }
    older_fit = {
        "job_id": "ops-a-older", "priority": "merge-gate", "footprint": "tiny",
        "planned_cores": 1.0, "submitted_at": 1.0, "cancelled": False,
    }
    assert book._queue_allows(candidate, [older_fit], capacity, {}, (), 10.0, 60.0) is False

    higher_candidate = {**candidate, "priority": "merge-gate", "job_id": "ops-a-candidate"}
    later_fit = {**older_fit, "priority": "dev", "job_id": "ops-z-later", "submitted_at": 11.0}
    assert book._queue_allows(higher_candidate, [later_fit], capacity, {}, (), 10.0, 60.0) is True


def test_wait_loop_holds_for_earlier_fitting_waiter_then_admits_after_withdrawal(tmp_path):
    clock = _FakeClock()
    capacity = _capacity()
    book = _book(tmp_path / "state", clock=clock)
    earlier_id = "ops-a-earlier-fit"
    earlier_record = {
        "schema": "fleet-admission-waiting/1", "job_id": earlier_id, "group": "ops",
        "digest": "a" * 64, "requested_priority": "merge-gate", "priority": "merge-gate",
        "footprint": "tiny", "planned_cores": 1.0, "submitted_at": 0.0,
        "state": "waiting", "cancelled": False, "record_kind": "job",
    }
    book._waiting_path(earlier_id).write_text(json.dumps(earlier_record), encoding="utf-8")
    earlier_fd = admission_module._open_lock(book._waiting_lock_path(earlier_id), blocking=False)
    assert earlier_fd is not None
    observations = []

    def withdraw_earlier(seconds):
        observations.append("candidate waited")
        book._waiting_path(earlier_id).unlink()
        admission_module._unlock_close(earlier_fd)
        clock.now += seconds

    book.waiter = withdraw_earlier
    admitted = book.submit(
        _request(job_id="ops-z-current", priority="dev", footprint="tiny"),
        authenticated_group="ops", capacity_provider=lambda: capacity,
        legacy_jobs_provider=lambda: (), wait_s=1,
    )
    assert observations == ["candidate waited"]
    assert admitted.record["job_id"] == "ops-z-current"
    admitted.handle.complete()


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
            authenticated_group="ops", capacity_provider=lambda: capacity,
            legacy_jobs_provider=lambda: (), wait_s=0,
        )
        nested["result"] = result
        result.handle.complete()
        blocker.handle.complete()

    clock.on_wait = try_backfill
    result = book.submit(head, authenticated_group="ops", capacity_provider=lambda: capacity,
                         legacy_jobs_provider=lambda: (), wait_s=120)
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
        outcomes["cancel_replay"] = book.cancel("ops-cancel", "ops")
        clock.now += seconds

    clock.on_wait = inspect_then_cancel
    with pytest.raises(AdmissionError, match="cancelled and withdrawn") as raised:
        book.submit(request, authenticated_group="ops", capacity_provider=lambda: blocked_capacity,
                    legacy_jobs_provider=lambda: (), wait_s=10)
    assert raised.value.exit_code == 143
    assert outcomes["status"]["state"] == "waiting"
    assert outcomes["cancel"]["cancelled"] is True
    assert outcomes["cancel_replay"]["cancelled"] is True
    assert book.status("ops-cancel", "ops") is None
    assert book.cancel("ops-never-existed", "ops") is None


def test_admitted_and_terminal_status_cancel_are_group_bound_and_keep_expected_state(tmp_path):
    capacity = _capacity()
    book = _book(tmp_path / "state")
    admitted = _admit(book, capacity, job_id="ops-group-bound")
    with pytest.raises(AdmissionError, match="different authenticated group") as raised:
        book.status("ops-group-bound", "other")
    assert raised.value.exit_code == 2
    with pytest.raises(AdmissionError, match="different authenticated group") as raised:
        book.cancel("ops-group-bound", "other")
    assert raised.value.exit_code == 2
    assert book.cancel("ops-group-bound", "ops")["state"] == "admitted"
    assert book._record_path("ops-group-bound").exists()
    admitted.handle.complete()
    terminal = book.status("ops-group-bound", "ops")
    assert terminal["state"] == "completed"
    assert book.cancel("ops-group-bound", "ops") == terminal
    with pytest.raises(AdmissionError, match="different authenticated group") as raised:
        book.status("ops-group-bound", "other")
    assert raised.value.exit_code == 2
    with pytest.raises(AdmissionError, match="different authenticated group") as raised:
        book.cancel("ops-group-bound", "other")
    assert raised.value.exit_code == 2


@pytest.mark.parametrize("operation", ["status", "cancel"])
def test_status_and_cancel_remove_a_dead_waiter_before_return(tmp_path, operation):
    book = _book(tmp_path / operation)
    record = {
        "schema": "fleet-admission-waiting/1", "job_id": "ops-dead-status", "group": "ops",
        "digest": "d" * 64, "requested_priority": "gate", "priority": "gate",
        "footprint": "small", "planned_cores": 1.0, "submitted_at": 1.0,
        "state": "waiting", "cancelled": False, "record_kind": "job",
    }
    path = book._waiting_path("ops-dead-status")
    path.write_text(json.dumps(record), encoding="utf-8")
    result = getattr(book, operation)("ops-dead-status", "ops")
    assert result is None
    assert not path.exists()


@pytest.mark.parametrize("operation", ["status", "cancel"])
def test_status_and_cancel_dead_waiter_unlink_failures_return_75(tmp_path, monkeypatch, operation):
    book = _book(tmp_path / operation)
    waiter_id = "ops-dead-status-fault"
    record = {
        "schema": "fleet-admission-waiting/1", "job_id": waiter_id, "group": "ops",
        "digest": "d" * 64, "requested_priority": "gate", "priority": "gate",
        "footprint": "small", "planned_cores": 1.0, "submitted_at": 1.0,
        "state": "waiting", "cancelled": False, "record_kind": "job",
    }
    path = book._waiting_path(waiter_id)
    path.write_text(json.dumps(record), encoding="utf-8")
    original_unlink = type(path).unlink
    monkeypatch.setattr(
        type(path), "unlink",
        lambda item, *args, **kwargs: (_ for _ in ()).throw(OSError("dead waiter unlink fault"))
        if item == path else original_unlink(item, *args, **kwargs),
    )
    with pytest.raises(AdmissionError, match="cannot withdraw dead waiter") as raised:
        getattr(book, operation)(waiter_id, "ops")
    assert raised.value.exit_code == 75
    assert path.exists()


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
        "record_kind": "job",
        "cancelled": False,
    }
    book._waiting_path(old_waiter_id).write_text(json.dumps(waiting_record), encoding="utf-8")
    try:
        with pytest.raises(AdmissionError, match="earlier queue reservation") as raised:
            book.submit(_request(job_id="ops-new"), authenticated_group="ops",
                        capacity_provider=lambda: capacity, legacy_jobs_provider=lambda: (), wait_s=0)
        assert raised.value.exit_code == 75
    finally:
        process.stdin.write("release\n")
        process.stdin.flush()
        process.stdin.close()
        assert process.wait() == 0
    admitted = book.submit(_request(job_id="ops-new"), authenticated_group="ops",
                           capacity_provider=lambda: capacity, legacy_jobs_provider=lambda: (), wait_s=0)
    assert admitted.handle is not None
    assert book.status(old_waiter_id, "ops") is None
    admitted.handle.complete()


def test_dead_corrupt_waiter_is_removed_before_its_record_is_parsed(tmp_path):
    capacity = _capacity()
    book = _book(tmp_path / "state")
    corrupt_path = book._waiting_path("ops-dead-corrupt")
    corrupt_path.write_text("{torn", encoding="utf-8")

    admitted = book.submit(
        _request(job_id="ops-after-corrupt"), authenticated_group="ops",
        capacity_provider=lambda: capacity, legacy_jobs_provider=lambda: (), wait_s=0,
    )
    assert admitted.handle is not None
    assert not corrupt_path.exists()
    admitted.handle.complete()


def test_same_id_dead_waiter_is_removed_before_a_fresh_submit(tmp_path):
    capacity = _capacity()
    book = _book(tmp_path / "state")
    request = _request(job_id="ops-dead-waiter-replay")
    stale = {
        "schema": "fleet-admission-waiting/1", "job_id": request.job_id, "group": "ops",
        "digest": "f" * 64, "requested_priority": "gate", "priority": "gate",
        "footprint": "large", "planned_cores": 1.0, "submitted_at": 1.0,
        "state": "waiting", "cancelled": False, "record_kind": "job",
    }
    stale_path = book._waiting_path(request.job_id)
    stale_path.write_text(json.dumps(stale), encoding="utf-8")
    admitted = book.submit(
        request, authenticated_group="ops", capacity_provider=lambda: capacity,
        legacy_jobs_provider=lambda: (), wait_s=0,
    )
    assert admitted.handle is not None
    assert not stale_path.exists()
    assert admitted.record["digest"] == request.digest()
    admitted.handle.complete()


def test_dead_waiter_withdrawal_failure_preserves_file_and_returns_75(tmp_path, monkeypatch):
    book = _book(tmp_path / "state")
    waiter_id = "ops-remove-failure"
    record = {
        "schema": "fleet-admission-waiting/1", "job_id": waiter_id, "group": "ops",
        "digest": "e" * 64, "requested_priority": "gate", "priority": "gate",
        "footprint": "small", "planned_cores": 1.0, "submitted_at": 1.0,
        "state": "waiting", "cancelled": False, "record_kind": "job",
    }
    path = book._waiting_path(waiter_id)
    path.write_text(json.dumps(record), encoding="utf-8")
    original_unlink = type(path).unlink
    monkeypatch.setattr(
        type(path), "unlink",
        lambda item, *args, **kwargs: (_ for _ in ()).throw(OSError("withdraw fault"))
        if item == path else original_unlink(item, *args, **kwargs),
    )
    with pytest.raises(AdmissionError, match="cannot withdraw waiter") as raised:
        book._remove_waiter_locked(waiter_id, "e" * 64, "ops")
    assert raised.value.exit_code == 75
    assert path.exists()


def test_submit_same_id_dead_waiter_unlink_failure_refuses_and_preserves_file(tmp_path, monkeypatch):
    book = _book(tmp_path / "state")
    request = _request(job_id="ops-same-id-withdraw-failure")
    record = {
        "schema": "fleet-admission-waiting/1", "job_id": request.job_id, "group": "ops",
        "digest": "e" * 64, "requested_priority": "gate", "priority": "gate",
        "footprint": "small", "planned_cores": 1.0, "submitted_at": 1.0,
        "state": "waiting", "cancelled": False, "record_kind": "job",
    }
    path = book._waiting_path(request.job_id)
    path.write_text(json.dumps(record), encoding="utf-8")
    original_unlink = type(path).unlink
    monkeypatch.setattr(
        type(path), "unlink",
        lambda item, *args, **kwargs: (_ for _ in ()).throw(OSError("existing waiter unlink fault"))
        if item == path else original_unlink(item, *args, **kwargs),
    )
    with pytest.raises(AdmissionError, match="cannot withdraw dead waiter") as raised:
        book.submit(request, authenticated_group="ops", capacity_provider=lambda: _capacity(),
                    legacy_jobs_provider=lambda: (), wait_s=0)
    assert raised.value.exit_code == 75
    assert path.exists()


def test_waiter_record_register_remove_collision_and_write_failure_paths(tmp_path, monkeypatch):
    book = _book(tmp_path / "state")
    record = {
        "schema": "fleet-admission-waiting/1", "job_id": "ops-storage-waiter", "group": "ops",
        "digest": "c" * 64, "requested_priority": "gate", "priority": "gate",
        "footprint": "small", "planned_cores": 1.0, "submitted_at": 1.0,
        "state": "waiting", "cancelled": False, "record_kind": "job",
    }
    fd = book._register_waiter_locked(record)
    assert book._waiting_path("ops-storage-waiter").exists()
    assert book._load_waiters_locked()[0]["job_id"] == "ops-storage-waiter"
    book._remove_waiter_locked("ops-storage-waiter", "c" * 64, "ops")
    assert not book._waiting_path("ops-storage-waiter").exists()
    admission_module._unlock_close(fd)
    assert book._remove_waiter_locked("ops-storage-waiter", "c" * 64, "ops") is None

    path = book._waiting_path("ops-storage-waiter")
    admission_module._atomic_json(path, record)
    with pytest.raises(AdmissionError, match="changed before withdrawal") as raised:
        book._remove_waiter_locked("ops-storage-waiter", "d" * 64, "ops")
    assert raised.value.exit_code == 2
    assert path.exists()

    original_write = admission_module._atomic_json
    monkeypatch.setattr(
        admission_module, "_atomic_json",
        lambda *_args: (_ for _ in ()).throw(AdmissionError(75, "injected waiter write failure")),
    )
    with pytest.raises(AdmissionError, match="injected waiter write failure") as raised:
        book._register_waiter_locked({**record, "job_id": "ops-failed-waiter"})
    assert raised.value.exit_code == 75
    assert not book._waiting_path("ops-failed-waiter").exists()
    monkeypatch.setattr(admission_module, "_atomic_json", original_write)
    lock_fd = admission_module._open_lock(book._waiting_lock_path("ops-failed-waiter"), blocking=False)
    assert lock_fd is not None
    admission_module._unlock_close(lock_fd)

    held_id = "ops-held-waiter-lock"
    held_fd = admission_module._open_lock(book._waiting_lock_path(held_id), blocking=False)
    assert held_fd is not None
    try:
        with pytest.raises(AdmissionError, match="waiter flock is held without a waiting record") as raised:
            book._register_waiter_locked({**record, "job_id": held_id})
        assert raised.value.exit_code == 75
        assert not book._waiting_path(held_id).exists()
    finally:
        admission_module._unlock_close(held_fd)


def test_warm_sample_provider_unknown_invalid_and_missing_id_outcomes(tmp_path):
    book = _book(tmp_path / "state")
    record = {"job_id": "ops-warm-job", "charged_bytes": GIB, "planned_cores": 1}
    unknown = replace(_capacity(), warm_samples_bytes_by_id=Fact.unknown("sampling unavailable"))
    assert book._sampled_warm_bytes(unknown, record) == 0
    known_missing = replace(_capacity(), warm_samples_bytes_by_id=_fact({}))
    assert book._sampled_warm_bytes(known_missing, record) == 0
    with pytest.raises(AdmissionError, match="no id for warm-set sampling") as raised:
        book._sampled_warm_bytes(known_missing, {"charged_bytes": GIB})
    assert raised.value.exit_code == 75
    invalid_shape = replace(_capacity(), warm_samples_bytes_by_id=_fact([]))
    with pytest.raises(AdmissionError, match="warm_samples_bytes_by_id is invalid") as raised:
        book._sampled_warm_bytes(invalid_shape, record)
    assert raised.value.exit_code == 75
    with pytest.raises(AdmissionError, match="capacity warm sample is invalid") as raised:
        book._sampled_warm_bytes(replace(_capacity(), warm_samples_bytes_by_id=_fact({"ops-warm-job": True})), record)
    assert raised.value.exit_code == 75
    with pytest.raises(AdmissionError, match="capacity warm sample is invalid") as raised:
        book._sampled_warm_bytes(replace(_capacity(), warm_samples_bytes_by_id=_fact({"ops-warm-job": -1})), record)
    assert raised.value.exit_code == 75


def test_candidate_fits_converts_unknown_fact_to_not_fitting_but_propagates_refusal(tmp_path):
    book = _book(tmp_path / "state")
    candidate = {"job_id": "ops-candidate", "footprint": "small", "planned_cores": 1,
                 "priority": "gate", "submitted_at": 0.0}
    unknown = replace(_capacity(), load1=Fact.unknown("load file unreadable"))
    assert book._candidate_fits(candidate, unknown, {}, ()) == (False, None)
    assert book._queue_allows(candidate, [], unknown, {}, (), 0.0, 60.0) is False
    invalid = _capacity(max_footprint="tiny")
    with pytest.raises(AdmissionError) as raised:
        book._candidate_fits(candidate, invalid, {}, ())
    assert raised.value.exit_code == 2


def test_waiter_loader_directory_and_dead_record_withdrawal_faults_are_75(tmp_path, monkeypatch):
    book = _book(tmp_path / "state")
    record = {
        "schema": "fleet-admission-waiting/1", "job_id": "ops-withdraw-fault", "group": "ops",
        "digest": "e" * 64, "requested_priority": "gate", "priority": "gate",
        "footprint": "small", "planned_cores": 1.0, "submitted_at": 1.0,
        "state": "waiting", "cancelled": False, "record_kind": "job",
    }
    path = book._waiting_path("ops-withdraw-fault")
    path.write_text(json.dumps(record), encoding="utf-8")
    original_iterdir = type(book.waiting_dir).iterdir
    monkeypatch.setattr(
        type(book.waiting_dir), "iterdir",
        lambda directory: (_ for _ in ()).throw(OSError("waiting directory unavailable"))
        if directory == book.waiting_dir else original_iterdir(directory),
    )
    with pytest.raises(AdmissionError, match="cannot read waiting records") as raised:
        book._load_waiters_locked()
    assert raised.value.exit_code == 75
    monkeypatch.setattr(type(book.waiting_dir), "iterdir", original_iterdir)

    original_unlink = type(path).unlink
    monkeypatch.setattr(
        type(path), "unlink",
        lambda item, *args, **kwargs: (_ for _ in ()).throw(OSError("waiter unlink unavailable"))
        if item == path else original_unlink(item, *args, **kwargs),
    )
    with pytest.raises(AdmissionError, match="cannot withdraw dead waiter") as raised:
        book._load_waiters_locked()
    assert raised.value.exit_code == 75
    assert path.exists()


@pytest.mark.parametrize(
    ("authenticated_group", "digest", "record_kind", "reason"),
    [
        ("other", "f" * 64, "job", "different authenticated group"),
        ("ops", "f" * 64, "job", "different request digest"),
        ("ops", "e" * 64, "lease", "different record type"),
    ],
)
def test_live_waiter_replay_must_match_group_digest_and_record_type(
    tmp_path, authenticated_group, digest, record_kind, reason
):
    book = _book(tmp_path / "state")
    waiter_id = "ops-live-replay"
    record = {
        "schema": "fleet-admission-waiting/1", "job_id": waiter_id, "group": "ops",
        "digest": "e" * 64, "requested_priority": "gate", "priority": "gate",
        "footprint": "small", "planned_cores": 1.0, "submitted_at": 1.0,
        "state": "waiting", "cancelled": False, "record_kind": "job",
    }
    book._waiting_path(waiter_id).write_text(json.dumps(record), encoding="utf-8")
    lock_fd = admission_module._open_lock(book._waiting_lock_path(waiter_id), blocking=False)
    assert lock_fd is not None
    try:
        with pytest.raises(AdmissionError, match=reason) as raised:
            book._existing_waiter_locked(waiter_id, authenticated_group, digest, record_kind)
        assert raised.value.exit_code == 2
    finally:
        admission_module._unlock_close(lock_fd)


def test_lease_and_job_admission_flocks_without_records_refuse_75(tmp_path):
    capacity = _capacity(max_planned=3 * GIB)
    book = _book(tmp_path / "state")
    job_id = "ops-flock-without-job"
    job_fd = admission_module._open_lock(book._job_lock_path(job_id), blocking=False)
    assert job_fd is not None
    try:
        with pytest.raises(AdmissionError, match="job flock is held without a readable admission record") as raised:
            _admit(book, capacity, job_id=job_id)
        assert raised.value.exit_code == 75
        assert not book._record_path(job_id).exists()
    finally:
        admission_module._unlock_close(job_fd)

    lease_id = "ops-flock-without-lease"
    lease_fd = admission_module._open_lock(book._job_lock_path(lease_id), blocking=False)
    assert lease_fd is not None
    try:
        with pytest.raises(AdmissionError, match="lease flock is held without a readable lease record") as raised:
            book.reserve_lease(
                _lease_request(lease_id=lease_id), authenticated_group="ops",
                capacity_provider=lambda: capacity, legacy_jobs_provider=lambda: (),
                inspector=lambda _name: InspectorAnswer.NO_SUCH_OBJECT, wait_s=0,
            )
        assert raised.value.exit_code == 75
        assert not book._lease_path(lease_id).exists()
    finally:
        admission_module._unlock_close(lease_fd)


@pytest.mark.parametrize(
    ("legacy_result", "exit_code", "reason"),
    [
        ("raise", 75, "legacy job list unavailable"),
        (("",), 2, "non-empty strings"),
        (("legacy", "legacy"), 2, "contains duplicates"),
    ],
)
def test_legacy_provider_faults_and_invalid_lists_fail_closed(tmp_path, legacy_result, exit_code, reason):
    def provider():
        if legacy_result == "raise":
            raise OSError("legacy directory unavailable")
        return legacy_result

    with pytest.raises(AdmissionError, match=reason) as raised:
        _book(tmp_path / "state").submit(
            _request(job_id="ops-legacy-invalid"), authenticated_group="ops",
            capacity_provider=lambda: _capacity(), legacy_jobs_provider=provider, wait_s=0,
        )
    assert raised.value.exit_code == exit_code


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
        legacy_jobs_provider=lambda: (),
        inspector=inspector, wait_s=0,
    )
    assert reserved.record["state"] == "pending"
    assert reserved.record["container_id"] is None
    assert reserved.record["released"] is False
    assert book.is_live("ops-lease-1") is True
    assert book.status("ops-lease-1", "ops")["container_name"] == "rg-lane-1"
    assert len(inspector_calls) >= 1
    same = book.reserve_lease(
        request, authenticated_group="ops", capacity_provider=lambda: capacity,
        legacy_jobs_provider=lambda: (), inspector=inspector, wait_s=0,
    )
    assert same.existing is True and same.handle is None
    with pytest.raises(AdmissionError, match="different request digest") as raised:
        book.reserve_lease(
            _lease_request(container_name="rg-other"), authenticated_group="ops",
            capacity_provider=lambda: capacity, legacy_jobs_provider=lambda: (),
            inspector=inspector, wait_s=0,
        )
    assert raised.value.exit_code == 2
    with pytest.raises(AdmissionError, match="different authenticated group") as raised:
        book.status("ops-lease-1", "other")
    assert raised.value.exit_code == 2
    assert book.cancel("ops-lease-1", "ops")["state"] == "pending"
    with pytest.raises(AdmissionError, match="different authenticated group") as raised:
        book.cancel("ops-lease-1", "other")
    assert raised.value.exit_code == 2

    with pytest.raises(AdmissionError, match="RAM fit") as raised:
        book.submit(_request(job_id="ops-too-large", footprint="small"), authenticated_group="ops",
                    capacity_provider=lambda: capacity, legacy_jobs_provider=lambda: (), wait_s=0)
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


def test_lease_api_refusals_and_released_tombstone_replays_are_exact(tmp_path):
    capacity = _capacity(max_planned=3 * GIB)
    book = _book(tmp_path / "state")
    assert book.reconcile_lease("ops-missing", "ops", lambda _name: InspectorAnswer.NO_SUCH_OBJECT) is None
    assert book.revoke_lease("ops-missing", "ops") is None
    with pytest.raises(AdmissionError, match="unknown lease id") as raised:
        book.bind_lease("ops-missing", "ops", "id")
    assert raised.value.exit_code == 2
    missing_resume = pytest.raises(AdmissionError, match="unknown lease id")
    with missing_resume as raised:
        book.resume_lease("ops-missing", "ops")
    assert raised.value.exit_code == 2
    with pytest.raises(AdmissionError) as raised:
        book.reserve_lease(
            _lease_request(lease_id="ops-no-inspector"), authenticated_group="ops",
            capacity_provider=lambda: capacity, legacy_jobs_provider=lambda: (), inspector=None, wait_s=0,
        )
    assert raised.value.exit_code == 2

    reserved = book.reserve_lease(
        _lease_request(lease_id="ops-released-lease"), authenticated_group="ops",
        capacity_provider=lambda: capacity, legacy_jobs_provider=lambda: (),
        inspector=lambda _name: InspectorAnswer.NO_SUCH_OBJECT, wait_s=0,
    )
    with pytest.raises(AdmissionError, match="different authenticated group") as raised:
        book._existing_lease_locked("ops-released-lease", "other", reserved.record["digest"])
    assert raised.value.exit_code == 2
    with pytest.raises(AdmissionError, match="different authenticated group") as raised:
        book.bind_lease("ops-released-lease", "other", "container-id")
    assert raised.value.exit_code == 2
    with pytest.raises(AdmissionError, match="container id must be non-empty") as raised:
        book.bind_lease("ops-released-lease", "ops", "")
    assert raised.value.exit_code == 2
    foreign = book.reserve_lease(
        _lease_request(lease_id="ops-foreign-handle", container_name="runner-foreign"),
        authenticated_group="ops", capacity_provider=lambda: capacity,
        legacy_jobs_provider=lambda: (), inspector=lambda _name: InspectorAnswer.NO_SUCH_OBJECT,
        wait_s=0,
    )
    bound_without_handle = book.bind_lease(
        "ops-foreign-handle", "ops", "container-bound-without-handle"
    )
    assert bound_without_handle["state"] == "bound"
    other_book = _book(tmp_path / "other")
    with pytest.raises(AdmissionError, match="this book's live lease handle") as raised:
        other_book.bind_lease("ops-released-lease", "ops", "id", reserved.handle)
    assert raised.value.exit_code == 2
    with pytest.raises(AdmissionError, match="this book's live lease handle") as raised:
        other_book.bind_lease("ops-released-lease", "ops", "id", foreign.handle)
    assert raised.value.exit_code == 2
    with pytest.raises(AdmissionError, match="different authenticated group") as raised:
        book.resume_lease("ops-released-lease", "other")
    assert raised.value.exit_code == 2
    with pytest.raises(AdmissionError, match="container id must be non-empty") as raised:
        book.resume_lease("ops-released-lease", "ops", "")
    assert raised.value.exit_code == 2
    with pytest.raises(AdmissionError, match="unbound lease") as raised:
        book.observe_lease("ops-released-lease", "ops", 1)
    assert raised.value.exit_code == 2
    with pytest.raises(AdmissionError, match="different authenticated group") as raised:
        book.revoke_lease("ops-released-lease", "other")
    assert raised.value.exit_code == 2
    with pytest.raises(AdmissionError, match="different authenticated group") as raised:
        book.reconcile_lease("ops-released-lease", "other", lambda _name: InspectorAnswer.NO_SUCH_OBJECT)
    assert raised.value.exit_code == 2

    revoked = book.revoke_lease("ops-released-lease", "ops")
    assert revoked["state"] == "tombstone" and revoked["released"] is False
    with pytest.raises(AdmissionError, match="revoked lease") as raised:
        book.bind_lease("ops-released-lease", "ops", "container-id")
    assert raised.value.exit_code == 2
    assert book.resume_lease("ops-released-lease", "ops").handle is None
    error_observation = book.reconcile_lease("ops-released-lease", "ops", lambda _name: InspectorAnswer.ERROR)
    assert error_observation == revoked
    assert book._load_records()["lease:ops-released-lease"]["released"] is False
    still_locked = book.reconcile_lease(
        "ops-released-lease", "ops", lambda _name: InspectorAnswer.NO_SUCH_OBJECT
    )
    assert still_locked == revoked
    assert still_locked["released"] is False
    reserved.handle.close()
    released = book.reconcile_lease(
        "ops-released-lease", "ops", lambda _name: InspectorAnswer.NO_SUCH_OBJECT
    )
    assert released["released"] is True
    assert book.revoke_lease("ops-released-lease", "ops") == released
    assert book.resume_lease("ops-released-lease", "ops").handle is None
    assert book.status("ops-released-lease", "ops")["released"] is True
    assert book.cancel("ops-released-lease", "ops")["released"] is True

    foreign.handle.close()


def test_resume_pending_lease_does_not_bind_until_its_flock_is_reacquired(tmp_path):
    capacity = _capacity()
    book = _book(tmp_path / "state")
    reserved = book.reserve_lease(
        _lease_request(), authenticated_group="ops", capacity_provider=lambda: capacity,
        legacy_jobs_provider=lambda: (), inspector=lambda _name: InspectorAnswer.NO_SUCH_OBJECT,
        wait_s=0,
    )

    held = book.resume_lease("ops-lease-1", "ops", "a" * 64)
    assert held.handle is None
    assert held.record["state"] == "pending"
    assert held.record["container_id"] is None
    assert book.status("ops-lease-1", "ops")["state"] == "pending"

    reserved.handle.close()
    resumed_unbound = book.resume_lease("ops-lease-1", "ops")
    assert resumed_unbound.handle is not None
    assert resumed_unbound.record["state"] == "pending"
    assert resumed_unbound.record["container_id"] is None
    resumed_unbound.handle.close()
    resumed = book.resume_lease("ops-lease-1", "ops", "a" * 64)
    assert resumed.handle is not None
    assert resumed.record["state"] == "bound"
    assert resumed.record["container_id"] == "a" * 64
    resumed.handle.close()


def test_resume_pending_lease_binding_write_failure_releases_lock_and_keeps_pending_record(
    tmp_path, monkeypatch
):
    capacity = _capacity()
    book = _book(tmp_path / "state")
    reserved = book.reserve_lease(
        _lease_request(lease_id="ops-resume-write-fault"), authenticated_group="ops",
        capacity_provider=lambda: capacity, legacy_jobs_provider=lambda: (),
        inspector=lambda _name: InspectorAnswer.NO_SUCH_OBJECT, wait_s=0,
    )
    reserved.handle.close()
    real_write = admission_module._atomic_json
    monkeypatch.setattr(
        admission_module, "_atomic_json",
        lambda *_args: (_ for _ in ()).throw(AdmissionError(75, "injected lease binding write failure")),
    )
    with pytest.raises(AdmissionError, match="injected lease binding write failure") as raised:
        book.resume_lease("ops-resume-write-fault", "ops", "immutable-container")
    assert raised.value.exit_code == 75
    assert book.status("ops-resume-write-fault", "ops")["state"] == "pending"
    assert book.status("ops-resume-write-fault", "ops")["container_id"] is None
    monkeypatch.setattr(admission_module, "_atomic_json", real_write)
    resumed = book.resume_lease("ops-resume-write-fault", "ops", "immutable-container")
    assert resumed.handle is not None
    assert resumed.record["state"] == "bound"
    resumed.handle.close()


@pytest.mark.parametrize(
    "answer, state, released",
    [
        (InspectorAnswer.PRESENT, "bound", False),
        (InspectorAnswer.ERROR, "bound", False),
        ("unrecognized", "bound", False),
        (InspectorAnswer.NO_SUCH_OBJECT, "tombstone", True),
    ],
)
def test_lease_reconcile_changes_only_on_explicit_no_such_object(tmp_path, answer, state, released):
    capacity = _capacity(max_planned=2 * GIB)
    book = _book(tmp_path / "state")
    request = _lease_request()
    reserved = book.reserve_lease(
        request, authenticated_group="ops", capacity_provider=lambda: capacity,
        legacy_jobs_provider=lambda: (),
        inspector=lambda _name: InspectorAnswer.NO_SUCH_OBJECT, wait_s=0,
    )
    reserved.handle.bind("e" * 64)
    reserved.handle.close()
    before = book.status("ops-lease-1", "ops")
    result = book.reconcile_lease("ops-lease-1", "ops", lambda target: answer)
    assert result["state"] == state
    assert result["released"] is released
    if released:
        assert result["last_inspection"] == answer.value
    else:
        assert result == before

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
        legacy_jobs_provider=lambda: (),
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
            capacity_provider=lambda: capacity, legacy_jobs_provider=lambda: (),
            inspector=lambda _name: InspectorAnswer.NO_SUCH_OBJECT,
            wait_s=0,
        )
    assert raised.value.exit_code == 2

    reappeared = book.reconcile_lease(
        "ops-lease-1", "ops", lambda _target: InspectorAnswer.ERROR
    )
    assert reappeared == released
    after_release = _admit(book, capacity, job_id="ops-after-reconcile", footprint="large")
    after_release.handle.complete()


def test_pending_live_lease_and_raising_inspector_leave_record_unchanged(tmp_path):
    capacity = _capacity()
    book = _book(tmp_path / "pending")
    pending = book.reserve_lease(
        _lease_request(), authenticated_group="ops", capacity_provider=lambda: capacity,
        legacy_jobs_provider=lambda: (), inspector=lambda _name: InspectorAnswer.NO_SUCH_OBJECT,
        wait_s=0,
    )
    before = dict(pending.record)
    live_result = book.reconcile_lease("ops-lease-1", "ops", lambda _name: InspectorAnswer.NO_SUCH_OBJECT)
    assert live_result == before
    assert book.is_live("ops-lease-1") is True
    with pytest.raises(AdmissionError, match="RAM fit") as raised:
        _admit(book, capacity, job_id="ops-still-charged", footprint="large")
    assert raised.value.exit_code == 75
    pending.handle.close()

    def raising_inspector(_name):
        raise OSError("injected inspector failure")

    unchanged = book.reconcile_lease("ops-lease-1", "ops", raising_inspector)
    assert unchanged == before


def test_explicit_revoke_tombstones_but_keeps_charge_until_reconcile_confirms_absence(tmp_path):
    capacity = _capacity()
    book = _book(tmp_path / "state")
    reserved = book.reserve_lease(
        _lease_request(), authenticated_group="ops", capacity_provider=lambda: capacity,
        legacy_jobs_provider=lambda: (), inspector=lambda _name: InspectorAnswer.NO_SUCH_OBJECT,
        wait_s=0,
    )
    reserved.handle.bind("f" * 64)
    reserved.handle.close()
    revoked = book.revoke_lease("ops-lease-1", "ops")
    assert revoked["state"] == "tombstone"
    assert revoked["released"] is False
    with pytest.raises(AdmissionError, match="RAM fit") as raised:
        _admit(book, capacity, job_id="ops-before-confirmation", footprint="large")
    assert raised.value.exit_code == 75
    released = book.reconcile_lease("ops-lease-1", "ops", lambda _name: InspectorAnswer.NO_SUCH_OBJECT)
    assert released["released"] is True


def test_lease_name_collision_and_unknown_inspector_answer_fail_closed(tmp_path):
    capacity = _capacity()
    present = _book(tmp_path / "present")
    with pytest.raises(AdmissionError, match="already exists") as raised:
        present.reserve_lease(
            _lease_request(), authenticated_group="ops", capacity_provider=lambda: capacity,
            legacy_jobs_provider=lambda: (),
            inspector=lambda _name: InspectorAnswer.PRESENT, wait_s=0,
        )
    assert raised.value.exit_code == 2

    unknown = _book(tmp_path / "unknown")
    with pytest.raises(AdmissionError, match="could not be confirmed absent") as raised:
        unknown.reserve_lease(
            _lease_request(), authenticated_group="ops", capacity_provider=lambda: capacity,
            legacy_jobs_provider=lambda: (),
            inspector=lambda _name: InspectorAnswer.ERROR, wait_s=0,
        )
    assert raised.value.exit_code == 75


def test_job_and_lease_ids_collide_in_both_directions(tmp_path):
    capacity = _capacity()
    lease_book = _book(tmp_path / "lease-first")
    lease = lease_book.reserve_lease(
        _lease_request(lease_id="ops-shared-id"), authenticated_group="ops",
        capacity_provider=lambda: capacity, legacy_jobs_provider=lambda: (),
        inspector=lambda _name: InspectorAnswer.NO_SUCH_OBJECT, wait_s=0,
    )
    lease.handle.close()
    with pytest.raises(AdmissionError, match="job id collides with a lease id") as raised:
        lease_book.submit(
            _request(job_id="ops-shared-id"), authenticated_group="ops",
            capacity_provider=lambda: capacity, legacy_jobs_provider=lambda: (), wait_s=0,
        )
    assert raised.value.exit_code == 2

    job_book = _book(tmp_path / "job-first")
    job = _admit(job_book, capacity, job_id="ops-shared-id")
    job.handle.close()
    with pytest.raises(AdmissionError, match="lease id collides with an admitted or completed job id") as raised:
        job_book.reserve_lease(
            _lease_request(lease_id="ops-shared-id"), authenticated_group="ops",
            capacity_provider=lambda: capacity, legacy_jobs_provider=lambda: (),
            inspector=lambda _name: InspectorAnswer.NO_SUCH_OBJECT, wait_s=0,
        )
    assert raised.value.exit_code == 2


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
            authenticated_group="ops", capacity_provider=lambda: capacity, legacy_jobs_provider=lambda: (),
            inspector=lambda _name: InspectorAnswer.NO_SUCH_OBJECT, wait_s=0,
        )
        nested["lease"] = lease
        lease.handle.close()
        book.reconcile_lease("ops-z-lease", "ops", lambda _target: InspectorAnswer.NO_SUCH_OBJECT)
        blocker.handle.complete()

    clock.on_wait = let_merge_lease_pass
    result = book.submit(head, authenticated_group="ops", capacity_provider=lambda: capacity,
                         legacy_jobs_provider=lambda: (), wait_s=120)
    assert nested["lease"].record["priority"] == "merge-gate"
    assert result.record["job_id"] == "ops-a-head"
    result.handle.complete()


def test_waiting_lease_is_granted_after_capacity_opens(tmp_path):
    clock = _FakeClock()
    capacity = _capacity()
    book = _book(tmp_path / "state", clock=clock)
    blocker = _admit(book, capacity, job_id="ops-blocker", footprint="large")

    def release_blocker(seconds):
        blocker.handle.complete()
        clock.now += seconds

    book.waiter = release_blocker
    lease = book.reserve_lease(
        _lease_request(lease_id="ops-waited-lease"), authenticated_group="ops",
        capacity_provider=lambda: capacity, legacy_jobs_provider=lambda: (),
        inspector=lambda _name: InspectorAnswer.NO_SUCH_OBJECT, wait_s=2,
    )
    assert lease.record["state"] == "pending"
    assert lease.handle is not None
    lease.handle.close()
    released = book.reconcile_lease(
        "ops-waited-lease", "ops", lambda _name: InspectorAnswer.NO_SUCH_OBJECT
    )
    assert released["released"] is True


def test_observe_job_group_and_missing_checks_preserve_conservative_warm_reservation(tmp_path):
    capacity = _capacity(total=32 * GIB, reserve=GIB, high=7 * GIB,
                         maximum=32 * GIB, max_planned=32 * GIB)
    book = _book(tmp_path / "state")
    admitted = _admit(book, capacity, job_id="ops-observed", footprint="large")
    with pytest.raises(AdmissionError, match="different authenticated group") as raised:
        book.observe_job("ops-observed", "other", 5 * GIB)
    assert raised.value.exit_code == 2
    with pytest.raises(AdmissionError) as raised:
        book.observe_job("ops-missing", "ops", 1)
    assert raised.value.exit_code == 75
    observed = book.observe_job("ops-observed", "ops", 5 * GIB)
    assert observed["current_warm_bytes"] == 5 * GIB

    no_sample = replace(capacity, warm_samples_bytes_by_id=_fact({}))
    with pytest.raises(AdmissionError, match="RAM fit") as raised:
        _admit(book, no_sample, job_id="ops-no-sample", footprint="small")
    assert raised.value.exit_code == 75
    sampled = replace(capacity, warm_samples_bytes_by_id=_fact({"ops-observed": 5 * GIB}))
    next_job = _admit(book, sampled, job_id="ops-fresh-sample", footprint="small")
    assert next_job.record["state"] == "admitted"
    next_job.handle.complete()
    admitted.handle.complete()


def test_dead_job_holder_keeps_charge_and_is_reported_not_live(tmp_path):
    capacity = _capacity(max_planned=2 * GIB)
    book = _book(tmp_path / "state")
    admitted = _admit(book, capacity, job_id="ops-dead-holder", footprint="small")
    admitted.handle.close()
    assert book.is_live("ops-dead-holder") is False
    assert book._record_path("ops-dead-holder").exists()
    with pytest.raises(AdmissionError, match="RAM fit") as raised:
        _admit(book, capacity, job_id="ops-after-dead-holder", footprint="small")
    assert raised.value.exit_code == 75


def test_job_and_lease_liveness_paths_cover_missing_live_dead_and_terminal_records(tmp_path):
    capacity = _capacity()
    book = _book(tmp_path / "state")
    assert book.is_live("ops-absent") is False
    job = _admit(book, capacity, job_id="ops-live-state")
    assert book.is_live("ops-live-state") is True
    job.handle.close()
    assert book.is_live("ops-live-state") is False
    resumed = book.resume_job("ops-live-state", "ops")
    assert resumed.handle is not None
    resumed.handle.complete()
    assert book.is_live("ops-live-state") is False
    lease = book.reserve_lease(
        _lease_request(lease_id="ops-live-lease"), authenticated_group="ops",
        capacity_provider=lambda: capacity, legacy_jobs_provider=lambda: (),
        inspector=lambda _name: InspectorAnswer.NO_SUCH_OBJECT, wait_s=0,
    )
    assert book.is_live("ops-live-lease") is True
    lease.handle.close()
    assert book.is_live("ops-live-lease") is False
    assert "lease:ops-live-lease" in book._load_records()


def test_resume_job_group_unknown_and_terminal_paths_return_exact_results(tmp_path):
    book = _book(tmp_path / "state")
    with pytest.raises(AdmissionError, match="unknown admitted job id") as raised:
        book.resume_job("ops-missing-job", "ops")
    assert raised.value.exit_code == 2
    admitted = _admit(book, _capacity(), job_id="ops-resume-group")
    with pytest.raises(AdmissionError, match="different authenticated group") as raised:
        book.resume_job("ops-resume-group", "other")
    assert raised.value.exit_code == 2
    admitted.handle.complete()
    terminal = book.resume_job("ops-resume-group", "ops")
    assert terminal.existing is True and terminal.handle is None
    assert terminal.record["state"] == "completed"
    with pytest.raises(AdmissionError, match="different authenticated group") as raised:
        book.resume_job("ops-resume-group", "other")
    assert raised.value.exit_code == 2


def test_complete_mismatch_and_terminal_write_failure_keep_charge_and_handle(tmp_path, monkeypatch):
    capacity = _capacity()
    book = _book(tmp_path / "state")
    admitted = _admit(book, capacity, job_id="ops-complete-failure")
    foreign_book = _book(tmp_path / "foreign")
    with pytest.raises(AdmissionError, match="completion requires this book's live job handle") as raised:
        foreign_book.complete(admitted.handle)
    assert raised.value.exit_code == 2
    assert admitted.handle.live is True
    original_digest = admitted.handle.record["digest"]
    admitted.handle.record["digest"] = "b" * 64
    with pytest.raises(AdmissionError, match="admission record changed") as raised:
        admitted.handle.complete()
    assert raised.value.exit_code == 2
    assert admitted.handle.live is True
    assert book._record_path("ops-complete-failure").exists()
    admitted.handle.record["digest"] = original_digest

    real_atomic_new = admission_module._atomic_new_json

    def fail_terminal(path, value):
        if path == book._terminal_path("ops-complete-failure"):
            raise AdmissionError(75, "injected terminal persistence failure")
        return real_atomic_new(path, value)

    monkeypatch.setattr(admission_module, "_atomic_new_json", fail_terminal)
    with pytest.raises(AdmissionError, match="injected terminal persistence failure") as raised:
        admitted.handle.complete()
    assert raised.value.exit_code == 75
    assert admitted.handle.live is True
    assert book._record_path("ops-complete-failure").exists()
    assert not book._terminal_path("ops-complete-failure").exists()
    monkeypatch.setattr(admission_module, "_atomic_new_json", real_atomic_new)
    admitted.handle.complete()
    assert not book._record_path("ops-complete-failure").exists()
    assert book.status("ops-complete-failure", "ops")["state"] == "completed"


def test_existing_terminal_record_is_idempotent_when_complete_replays(tmp_path):
    capacity = _capacity()
    book = _book(tmp_path / "state")
    admitted = _admit(book, capacity, job_id="ops-terminal-replay")
    terminal = {
        "schema": "fleet-admission-terminal/1", "job_id": "ops-terminal-replay", "group": "ops",
        "digest": admitted.record["digest"], "state": "completed", "completed_at": 124.0,
    }
    admission_module._atomic_new_json(book._terminal_path("ops-terminal-replay"), terminal)
    assert "job:ops-terminal-replay" not in book._load_records()
    with pytest.raises(AdmissionError, match="different authenticated group") as raised:
        book._existing_terminal_locked("ops-terminal-replay", "other", admitted.record["digest"])
    assert raised.value.exit_code == 2
    admitted.handle.complete()
    assert not book._record_path("ops-terminal-replay").exists()
    replay = _admit(book, capacity, job_id="ops-terminal-replay")
    assert replay.existing is True and replay.handle is None
    assert replay.record["completed_at"] == 124.0


def test_durable_terminal_resolves_a_torn_stale_active_file(tmp_path):
    capacity = _capacity()
    book = _book(tmp_path / "state")
    request = _request(job_id="ops-terminal-before-unlink")
    terminal = {
        "schema": "fleet-admission-terminal/1", "job_id": request.job_id, "group": "ops",
        "digest": request.digest(), "state": "completed", "completed_at": 124.0,
    }
    admission_module._atomic_new_json(book._terminal_path(request.job_id), terminal)
    book._record_path(request.job_id).write_text("{torn", encoding="utf-8")
    assert book._load_records() == {}
    replay = book.submit(request, authenticated_group="ops", capacity_provider=lambda: capacity,
                         legacy_jobs_provider=lambda: (), wait_s=0)
    assert replay.existing is True and replay.handle is None
    assert replay.record["state"] == "completed"
    assert book._record_path(request.job_id).read_text(encoding="utf-8") == "{torn"


def test_completion_with_conflicting_terminal_keeps_active_record_and_handle(tmp_path):
    book = _book(tmp_path / "state")
    admitted = _admit(book, _capacity(), job_id="ops-terminal-conflict")
    terminal = {
        "schema": "fleet-admission-terminal/1", "job_id": "ops-terminal-conflict", "group": "ops",
        "digest": "f" * 64, "state": "completed", "completed_at": 123.5,
    }
    admission_module._atomic_new_json(book._terminal_path("ops-terminal-conflict"), terminal)
    with pytest.raises(AdmissionError, match="terminal record changed before completion") as raised:
        admitted.handle.complete()
    assert raised.value.exit_code == 2
    assert admitted.handle.live is True
    assert book._record_path("ops-terminal-conflict").exists()
    with pytest.raises(AdmissionError, match="conflicts with its terminal record; its charge is retained") as raised:
        book._load_records()
    assert raised.value.exit_code == 75
    admitted.handle.close()


def test_container_name_appearing_during_lease_wait_refuses_with_two(tmp_path):
    clock = _FakeClock()
    capacity = _capacity()
    book = _book(tmp_path / "state", clock=clock)
    blocker = _admit(book, capacity, job_id="ops-blocker", footprint="large")
    inspections = []

    def inspector(_name):
        inspections.append("inspect")
        if len(inspections) == 1:
            return InspectorAnswer.NO_SUCH_OBJECT
        return InspectorAnswer.PRESENT

    with pytest.raises(AdmissionError, match="already exists") as raised:
        book.reserve_lease(
            _lease_request(lease_id="ops-name-appeared"), authenticated_group="ops",
            capacity_provider=lambda: capacity, legacy_jobs_provider=lambda: (),
            inspector=inspector, wait_s=2,
        )
    assert raised.value.exit_code == 2
    assert len(inspections) == 2
    assert book.status("ops-name-appeared", "ops") is None
    blocker.handle.complete()


@pytest.mark.parametrize("holder_priority", ["gate", "campaign"])
@pytest.mark.parametrize(("candidate_priority", "passes"), [("dev", False), ("merge-gate", True)])
def test_aged_holder_that_now_fits_keeps_its_reservation_against_a_later_arrival(
    tmp_path, holder_priority, candidate_priority, passes
):
    clock = _FakeClock()
    capacity = _capacity(aging_s=60)
    book = _book(tmp_path / "state", clock=clock)
    blocker = _admit(book, capacity, job_id="ops-blocker", footprint="small", cores=1)
    holder = _request(job_id="ops-a-holder", priority=holder_priority, footprint="large", cores=1)
    nested: dict[str, object] = {}

    def free_capacity_then_submit_before_the_holder_polls(seconds):
        if nested:
            clock.now += seconds
            return
        nested["ran"] = True
        clock.now += 60
        blocker.handle.complete()
        try:
            result = book.submit(
                _request(job_id="ops-z-arrival", priority=candidate_priority, footprint="small"),
                authenticated_group="ops", capacity_provider=lambda: capacity,
                legacy_jobs_provider=lambda: (), wait_s=0,
            )
        except AdmissionError as exc:
            nested["error"] = exc
        else:
            nested["result"] = result
            result.handle.complete()

    clock.on_wait = free_capacity_then_submit_before_the_holder_polls
    result = book.submit(holder, authenticated_group="ops", capacity_provider=lambda: capacity,
                         legacy_jobs_provider=lambda: (), wait_s=120)
    assert ("result" in nested) is passes
    if not passes:
        assert nested["error"].exit_code == 75
        assert "immediate admission refused" in nested["error"].reason
    assert result.record["job_id"] == "ops-a-holder"
    result.handle.complete()


def _waiting_row(job_id, priority, submitted_at):
    return {
        "job_id": job_id, "priority": priority, "footprint": "tiny",
        "planned_cores": 1.0, "submitted_at": submitted_at, "cancelled": False,
    }


@pytest.mark.parametrize(
    ("first", "second", "admitted"),
    [
        # an aged holder passes an earlier-sorting fresh waiter, which waits
        (("ops-fresh", "dev", 99.0), ("ops-aged", "campaign", 0.0), "ops-aged"),
        # two aged waiters: the first in queue order holds the reservation
        (("ops-older", "campaign", 0.0), ("ops-younger", "campaign", 1.0), "ops-older"),
        (("ops-aged-dev", "dev", 1.0), ("ops-aged-campaign", "campaign", 0.0), "ops-aged-dev"),
        # a fresh merge gate passes an aged holder, which waits for it
        (("ops-merge", "merge-gate", 99.0), ("ops-aged", "campaign", 0.0), "ops-merge"),
    ],
)
def test_exactly_one_of_two_fitting_waiters_is_admissible_so_neither_blocks_the_other_for_ever(
    tmp_path, first, second, admitted
):
    book = _book(tmp_path / "state")
    capacity = _capacity()
    rows = [_waiting_row(*first), _waiting_row(*second)]
    allowed = [
        row["job_id"] for row in rows
        if book._queue_allows(row, rows, capacity, {}, (), 100.0, 60.0)
    ]
    assert allowed == [admitted]


def test_aged_nonfitting_reservation_does_not_allow_merge_gates_to_pass_out_of_queue_order(tmp_path):
    book = _book(tmp_path / "state")
    capacity = _capacity()
    existing_charge = {
        "job_id": "ops-existing", "charged_bytes": 2 * GIB,
        "current_warm_bytes": 0, "planned_cores": 1.0,
    }
    holder = _waiting_row("ops-aged-holder", "campaign", 0.0)
    holder["footprint"] = "large"
    first = _waiting_row("ops-first-merge", "merge-gate", 50.0)
    second = _waiting_row("ops-second-merge", "merge-gate", 51.0)
    rows = [holder, first, second]

    assert book._queue_allows(second, rows, capacity, {"job:ops-existing": existing_charge}, (), 100.0, 60.0) is False
    assert book._queue_allows(first, rows, capacity, {"job:ops-existing": existing_charge}, (), 100.0, 60.0) is True

    admitted_first = {
        "job_id": first["job_id"], "charged_bytes": GIB // 2,
        "current_warm_bytes": 0, "planned_cores": 1.0,
    }
    after_first_admission = [holder, second]
    assert book._queue_allows(
        second, after_first_admission,
        capacity, {"job:ops-existing": existing_charge, "job:ops-first-merge": admitted_first}, (),
        100.0, 60.0,
    ) is True


def test_expired_wait_names_the_last_refusal(tmp_path):
    clock = _FakeClock()
    capacity = replace(
        _capacity(),
        policy=replace(_capacity().policy, legacy_planned_cores=Fact.unknown("legacy core policy missing")),
    )
    book = _book(tmp_path / "state", clock=clock)
    with pytest.raises(AdmissionError, match="wait expired") as raised:
        book.submit(
            _request(job_id="ops-waits-for-policy"), authenticated_group="ops",
            capacity_provider=lambda: capacity,
            legacy_jobs_provider=lambda: ("legacy-without-EXIT",), wait_s=1,
        )
    assert raised.value.exit_code == 75
    assert "policy.legacy_planned_cores unavailable: legacy core policy missing" in raised.value.reason
    assert book.status("ops-waits-for-policy", "ops") is None


def test_custom_waiter_disconnect_withdraws_its_record_before_returning(tmp_path):
    book = _book(tmp_path / "state")
    with pytest.raises(AdmissionError, match="client disconnected") as raised:
        book.submit(
            _request(job_id="ops-disconnected-waiter", footprint="small"),
            authenticated_group="ops",
            capacity_provider=lambda: _capacity(),
            legacy_jobs_provider=lambda: ("legacy-running",),
            wait_s=10,
            waiter=lambda _seconds: (_ for _ in ()).throw(
                AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "client disconnected")
            ),
        )

    assert raised.value.exit_code == ExitCode.IDENTITY_OR_TRANSPORT
    assert book.status("ops-disconnected-waiter", "ops") is None


def test_unreadable_terminal_of_an_unrelated_finished_job_does_not_block_admission(tmp_path):
    capacity = _capacity()
    book = _book(tmp_path / "state")
    path = book._terminal_path("ops-long-finished")
    path.write_text("{torn", encoding="utf-8")
    admitted = _admit(book, capacity, job_id="ops-new-job")
    assert admitted.handle is not None
    assert path.read_text(encoding="utf-8") == "{torn"
    admitted.handle.complete()


def test_raising_capacity_provider_refuses_with_75_and_records_nothing(tmp_path):
    book = _book(tmp_path / "state")

    def capacity_provider():
        raise RuntimeError("hostobs unreachable")

    with pytest.raises(AdmissionError, match="host capacity unavailable: hostobs unreachable") as raised:
        book.submit(
            _request(job_id="ops-no-facts"), authenticated_group="ops",
            capacity_provider=capacity_provider, legacy_jobs_provider=lambda: (), wait_s=0,
        )
    assert raised.value.exit_code == 75
    assert book.status("ops-no-facts", "ops") is None


def test_capacity_provider_admission_error_keeps_its_own_code_and_reason(tmp_path):
    book = _book(tmp_path / "state")

    def capacity_provider():
        raise AdmissionError(ExitCode.REFUSAL, "host policy refused")

    with pytest.raises(AdmissionError) as raised:
        book.submit(
            _request(job_id="ops-refused-facts"), authenticated_group="ops",
            capacity_provider=capacity_provider, legacy_jobs_provider=lambda: (), wait_s=0,
        )
    assert raised.value.exit_code == 2
    assert raised.value.reason == "host policy refused"


def test_wait_that_expires_before_any_decision_names_no_refusal(tmp_path):
    readings: list[None] = []

    def clock():
        readings.append(None)
        return 0.0 if len(readings) == 1 else 10.0

    book = _book(tmp_path / "state", clock=clock)
    with pytest.raises(AdmissionError) as raised:
        book.submit(
            _request(job_id="ops-late-start"), authenticated_group="ops",
            capacity_provider=_capacity, legacy_jobs_provider=lambda: (), wait_s=1,
        )
    assert raised.value.exit_code == 75
    assert raised.value.reason == "wait expired; waiter withdrawn and the book confirms the job never started"
    assert book.status("ops-late-start", "ops") is None
