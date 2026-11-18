"""Memory and IO pressure defer new batch starts but never stop a running batch (FD-022)."""

from __future__ import annotations

import pytest

from fleetd.campaign import CampaignManager
from fleetd.runner import RunnerJobs, _parse_pressure
from fleetd.transport import FakeTransport, HostUnavailable

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
    for malformed in ("[]", '{"jobs":[],"pressure":{"memory_some_avg10":true,"io_some_avg10":120}}'):
        runner.transport = ScriptedTransport([(0, malformed, "")] if malformed != "[]" else [])
        if malformed == "[]":
            continue
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
    transport.set_pressure("host-a", memory=90.0)
    transport.set_pressure("host-b", memory=90.0)
    manager.run("campaign", plan)
    state = manager._load_state("campaign")
    for job in state["jobs"].values():
        job["status"] = "COMPLETE"
    manager._write_state(state)
    document = manager.status_document(manager._load_state("campaign"))
    assert all(row["start_deferred"] is None for row in document["hosts"])
    transport.set_unreachable("host-a")
    state = manager._load_state("campaign")
    state["jobs"][next(iter(state["jobs"]))]["status"] = "QUEUED"
    manager._write_state(state)
    document = manager.tick("campaign")
    assert all(row["start_deferred"] is None for row in document["hosts"] if row["state"] != "ONLINE")


def test_the_pressure_parser_accepts_zero_through_one_hundred_and_nothing_else():
    keys = ("memory_some_avg10", "io_some_avg10")
    assert _parse_pressure(None) is None and _parse_pressure([1, 2]) is None
    assert _parse_pressure({"memory_some_avg10": 0, "io_some_avg10": 100}) == {keys[0]: 0.0, keys[1]: 100.0}
    for bad in (100.5, -0.5, float("nan"), float("inf"), True, "5", None):
        assert _parse_pressure({"memory_some_avg10": bad, "io_some_avg10": 1}) == {keys[0]: None, keys[1]: 1.0}
    assert _parse_pressure({}) == {keys[0]: None, keys[1]: None}
