"""Memory and IO pressure defer new batch starts but never stop a running batch (FD-022)."""

from __future__ import annotations

import pytest

from fleetd.campaign import CampaignManager
from fleetd.runner import RunnerJobs, _parse_pressure
from fleetd.transport import FakeTransport, HostUnavailable, TransportError

from fleetd.campaign import STALL_FLOOR_SECONDS
from test_campaign import FakeClock, ScriptedTransport, _jobs, _plan, _state


def _manager(tmp_path, plan_count=12):
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    return CampaignManager(tmp_path, transport, clock=clock), transport, _plan(plan_count), clock


def _host_row(document, host):
    return next(row for row in document["hosts"] if row["host"] == host)


def test_every_planned_host_row_records_the_refusal_threshold():
    assert {row["psi_refuse_some_avg10"] for row in _plan(4)["hosts"]} == {25}


def test_a_host_above_the_threshold_starts_nothing_names_the_reason_and_starts_again_when_it_falls(tmp_path):
    manager, transport, plan, _clock = _manager(tmp_path)
    transport.set_pressure("host-a", memory=26.5)
    document = manager.run("campaign", plan)
    assert transport.host("host-a").starts == 0
    assert transport.host("host-b").starts >= 1
    row = _host_row(document, "host-a")
    assert row["state"] == "ONLINE"
    assert row["start_deferred"] == "memory PSI some avg10 26.5% exceeds 25%; no new batch starts until it falls"
    assert _host_row(document, "host-b")["start_deferred"] is None
    assert {job["status"] for job in _jobs(_state(tmp_path), "host-a")} == {"QUEUED"}
    # The reason is also what a plain status reads, from the same probe.
    assert _host_row(manager.status("campaign"), "host-a")["start_deferred"].startswith("memory PSI")

    transport.set_pressure("host-a", memory=3.0, io=3.0)
    document = manager.tick("campaign")
    assert transport.host("host-a").starts == 1
    assert _host_row(document, "host-a")["start_deferred"] is None


def test_io_pressure_defers_too_and_the_memory_reading_is_checked_first(tmp_path):
    manager, transport, plan, _clock = _manager(tmp_path)
    transport.set_pressure("host-a", memory=1.0, io=40.0)
    document = manager.run("campaign", plan)
    assert transport.host("host-a").starts == 0
    assert _host_row(document, "host-a")["start_deferred"].startswith("io PSI some avg10 40%")
    transport.set_pressure("host-a", memory=30.0, io=40.0)
    assert _host_row(manager.status("campaign"), "host-a")["start_deferred"].startswith("memory PSI")


@pytest.mark.parametrize(("memory", "io", "starts"), [(25.0, 25.0, 1), (25.01, 0.0, 0), (0.0, 25.01, 0)])
def test_the_threshold_comparison_is_strictly_greater_than(tmp_path, memory, io, starts):
    manager, transport, plan, _clock = _manager(tmp_path)
    transport.set_pressure("host-a", memory=memory, io=io)
    manager.run("campaign", plan)
    assert transport.host("host-a").starts == starts


@pytest.mark.parametrize(("memory", "io", "resource"), [(None, 0.0, "memory"), (0.0, None, "io")])
def test_an_unreadable_pressure_file_refuses_to_start_and_says_so(tmp_path, memory, io, resource):
    manager, transport, plan, _clock = _manager(tmp_path)
    transport.set_pressure("host-a", memory=memory, io=io)
    document = manager.run("campaign", plan)
    assert transport.host("host-a").starts == 0
    assert _host_row(document, "host-a")["start_deferred"] == (
        f"{resource} PSI some avg10 is unknown on the host; no new batch starts"
    )


def test_a_probe_without_a_pressure_object_refuses_to_start(tmp_path):
    manager, transport, plan, _clock = _manager(tmp_path)
    manager.runner.pressure = lambda _host: None
    document = manager.run("campaign", plan)
    assert transport.host("host-a").starts == 0
    assert _host_row(document, "host-a")["start_deferred"].startswith("memory PSI some avg10 is unknown")


def test_a_plan_without_the_threshold_refuses_to_start_and_says_to_replan(tmp_path):
    manager, transport, plan, _clock = _manager(tmp_path)
    for host in plan["hosts"]:
        host.pop("psi_refuse_some_avg10")
    plan["hosts"][1]["psi_refuse_some_avg10"] = True
    document = manager.run("campaign", plan)
    assert sum(host.starts for host in transport.hosts.values()) == 0
    assert all("re-plan" in row["start_deferred"] for row in document["hosts"])


@pytest.mark.parametrize("threshold", [float("nan"), float("inf"), "25"])
def test_a_threshold_that_is_not_a_finite_number_defers_every_start(tmp_path, threshold):
    manager, transport, plan, _clock = _manager(tmp_path)
    for host in plan["hosts"]:
        host["psi_refuse_some_avg10"] = threshold
    document = manager.run("campaign", plan)
    assert sum(host.starts for host in transport.hosts.values()) == 0
    assert all("re-plan" in row["start_deferred"] for row in document["hosts"])


def test_a_failed_probe_forgets_the_previous_pressure_reading():
    runner = RunnerJobs(FakeTransport())
    runner.probe("host-a", {})
    assert runner.pressure("host-a") == {"memory_some_avg10": 0.0, "io_some_avg10": 0.0}
    runner.transport.set_unreachable("host-a")
    with pytest.raises(HostUnavailable):
        runner.probe("host-a", {})
    assert runner.pressure("host-a") is None
    # A reachable host whose answer is not a probe document is a failed probe too.
    runner.transport.set_unreachable("host-a", False)
    runner.probe("host-a", {})
    assert runner.pressure("host-a") is not None
    runner.transport = ScriptedTransport([(0, "[]", "")])
    with pytest.raises(TransportError, match="job table"):
        runner.probe("host-a", {})
    assert runner.pressure("host-a") is None
    malformed = '{"jobs":[],"pressure":{"memory_some_avg10":true,"io_some_avg10":120}}'
    runner.transport = ScriptedTransport([(0, malformed, "")])
    runner.probe("host-a", {})
    assert runner.pressure("host-a") == {"memory_some_avg10": None, "io_some_avg10": None}


def test_rising_pressure_never_stops_a_running_batch(tmp_path):
    manager, transport, plan, clock = _manager(tmp_path)
    manager.run("campaign", plan)
    running = [job["name"] for job in _jobs(_state(tmp_path), "host-a", "RUNNING")]
    assert running
    transport.set_pressure("host-a", memory=90.0, io=90.0)
    clock.advance(10)
    manager.tick("campaign")
    assert transport.host("host-a").stops == 0
    assert [job["name"] for job in _jobs(_state(tmp_path), "host-a", "RUNNING")] == running


def test_status_shows_no_reason_for_a_host_with_nothing_queued_or_not_online(tmp_path):
    manager, transport, plan, _clock = _manager(tmp_path, plan_count=1)
    manager.run("campaign", plan)
    state = manager._load_state("campaign")
    (name, job), = state["jobs"].items()
    host = job["host"]
    transport.set_pressure(host, memory=90.0)
    job["status"] = "COMPLETE"
    manager._write_state(state)
    document = manager.status_document(manager._load_state("campaign"))
    assert _host_row(document, host)["start_deferred"] is None
    # The one job is queued again on a host that is now unreachable.
    transport.set_unreachable(host)
    state = manager._load_state("campaign")
    state["jobs"][name]["status"] = "QUEUED"
    manager._write_state(state)
    document = manager.tick("campaign")
    row = _host_row(document, host)
    assert row["state"] == "UNREACHABLE"
    assert row["start_deferred"] is None
    assert manager._load_state("campaign")["hosts"][host]["pressure"] is None
    assert transport.host(host).starts == 1



def test_the_pressure_parser_accepts_zero_through_one_hundred_and_nothing_else():
    keys = ("memory_some_avg10", "io_some_avg10")
    assert _parse_pressure(None) is None and _parse_pressure([1, 2]) is None
    assert _parse_pressure({"memory_some_avg10": 0, "io_some_avg10": 100}) == {keys[0]: 0.0, keys[1]: 100.0}
    for bad in (100.5, -0.5, float("nan"), float("inf"), True, "5", None):
        assert _parse_pressure({"memory_some_avg10": bad, "io_some_avg10": 1}) == {keys[0]: None, keys[1]: 1.0}
    assert _parse_pressure({}) == {keys[0]: None, keys[1]: None}


def _running(tmp_path):
    manager, transport, plan, clock = _manager(tmp_path)
    manager.run("campaign", plan)
    state = manager._load_state("campaign")
    assert state["hosts"]["host-a"]["pressure"] == {"memory_some_avg10": 0.0, "io_some_avg10": 0.0}
    return manager, transport, clock


def _pressure_of(manager, host="host-a"):
    return manager._load_state("campaign")["hosts"][host]["pressure"]


def test_a_host_in_error_forgets_its_pressure_reading(tmp_path):
    manager, _transport, _clock = _running(tmp_path)

    def refuse(_host, _wanted):
        raise TransportError("probe rejected")

    manager.runner.probe = refuse
    manager.tick("campaign")
    assert manager._load_state("campaign")["hosts"]["host-a"]["status"] == "ERROR"
    assert _pressure_of(manager) is None


def test_a_stop_that_cannot_reach_the_host_forgets_its_pressure_reading(tmp_path):
    manager, _transport, clock = _running(tmp_path)

    def unreachable(_host, _name):
        raise HostUnavailable("host timed out")

    manager.runner.stop = unreachable
    clock.advance(STALL_FLOOR_SECONDS + 1)
    manager.tick("campaign")
    assert manager._load_state("campaign")["hosts"]["host-a"]["status"] == "UNREACHABLE"
    assert _pressure_of(manager) is None


def test_a_start_that_cannot_reach_the_host_forgets_its_pressure_reading(tmp_path):
    manager = _manager(tmp_path)[0]

    def unreachable(_spec):
        raise HostUnavailable("host timed out")

    manager.runner.start = unreachable
    manager.run("campaign", _plan(12))
    assert manager._load_state("campaign")["hosts"]["host-a"]["status"] == "UNREACHABLE"
    assert _pressure_of(manager) is None
