"""Probe size, finished-job filtering and pressure readings (FD-014, FD-022).

The real probe script runs ONLY under a temporary HOME on this machine, through
the same helpers the real-runner contract tests use; nothing here contacts a host.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import subprocess

import pytest

from fleetd.campaign import CampaignManager, _probe_settled
from fleetd.runner import JobSpec, RunnerJobs, probe_payload, probe_script
from fleetd.transport import FakeTransport, _probe_targets, _read_command_metadata

from test_campaign import FakeClock, _plan, _real_runner_home, _spec_for_project, _state

MAX_ARG_STRLEN = 128 * 1024


def _many_jobs(count: int, *, commit: str = "a" * 40, project: str = "fleetd", first: int = 0) -> dict[str, JobSpec]:
    jobs = {}
    for batch in range(first, first + count):
        candidate = hashlib.sha256(f"candidate-{batch}".encode()).hexdigest()
        spec = JobSpec("r2-week46", "host-small", 0, batch, (candidate,), commit, project, "r2", 2)
        jobs[spec.name] = spec
    return jobs


def test_probe_command_stays_small_for_a_hundred_fifty_batches():
    script = probe_script(_many_jobs(150))
    assert len(script.encode()) < 100 * 1024
    # Not just under the cap: a thousand batches still fit in one argument.
    assert len(probe_script(_many_jobs(1000)).encode()) < MAX_ARG_STRLEN
    # The leading metadata comment names the operation only; the targets exist once.
    assert _read_command_metadata(script) == {"operation": "probe"}
    assert script.count(next(iter(_many_jobs(1)))) == 0


def test_probe_returns_a_row_for_every_requested_batch_of_a_150_batch_host():
    transport = FakeTransport()
    runner = RunnerJobs(transport)
    jobs = _many_jobs(150)
    for spec in list(jobs.values())[:3]:
        runner.start(spec)
    rows = runner.probe("host-small", jobs)
    assert set(rows) == set(jobs)
    assert sorted(row["state"] for row in rows.values()).count("RUNNING") == 3
    assert [row["state"] for name, row in rows.items() if name in list(jobs)[3:]] == ["MISSING"] * 147
    assert len(probe_script(jobs).encode()) < 100 * 1024


def test_probe_payload_groups_by_checkout_and_derives_each_ids_path_from_the_name():
    first = _many_jobs(2, commit="a" * 40, project="fleetd")
    second = _many_jobs(2, commit="b" * 40, project=".", first=2)
    jobs = {**first, **second}
    targets = _probe_targets(probe_script(jobs))
    by_name = {target["name"]: target for target in targets}
    assert set(by_name) == set(jobs)
    for name, spec in jobs.items():
        assert by_name[name] == {
            "name": name,
            "ids_path": spec.candidate_file_home_path,
            "commit": spec.commit,
            "project": spec.project,
            "lane": spec.lane,
        }
    # One table entry per (campaign, host, commit, project, lane), not per job.
    assert probe_payload(jobs) != probe_payload(first)
    transport = FakeTransport()
    transport.host("host-small").missing_checkouts.add("b" * 40)
    rows = RunnerJobs(transport).probe("host-small", jobs)
    assert {row["checkout_ready"] for name, row in rows.items() if name in first} == {True}
    assert {row["checkout_ready"] for name, row in rows.items() if name in second} == {False}


def _probed_names(transport: FakeTransport, host: str) -> list[str]:
    probes = [
        script for alias, kind, script in transport.calls
        if alias == host and kind == "run"
        and (_read_command_metadata(script) or {}).get("operation") == "probe"
    ]
    return sorted(target["name"] for target in _probe_targets(probes[-1]))


def test_a_finished_job_is_not_probed_again(tmp_path):
    plan = _plan(1)
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", plan)
    job = next(job for job in _state(tmp_path)["jobs"].values() if job["status"] == "RUNNING")
    host = job["host"]
    assert _probed_names(transport, host) == [job["name"]]
    for candidate_id in job["candidate_ids"]:
        transport.add_progress(host, job["name"], candidate_id, "killed")
    transport.finish(host, job["name"])
    manager.tick("campaign")
    settled = _state(tmp_path)["jobs"][job["name"]]
    assert settled["status"] == "COMPLETE"
    assert settled["progress_end_offset"] is not None
    manager.tick("campaign")
    assert _probed_names(transport, host) == []
    # The host is still contacted (an empty probe is its liveness and pressure reading).
    assert _state(tmp_path)["hosts"][host]["status"] == "ONLINE"


@pytest.mark.parametrize(
    ("status", "end_offset", "settled"),
    [
        ("COMPLETE", 10, True),
        ("EXIT", 10, True),
        ("FAILED", 10, True),
        ("STALLED", 10, True),
        ("COMPLETE", 0, True),
        ("COMPLETE", None, False),
        ("FAILED", None, False),
        ("STALLED", None, False),
        ("RUNNING", 10, False),
        ("UNKNOWN", 10, False),
        ("LOST", 10, False),
        ("QUEUED", None, False),
        ("STARTING", None, False),
        ("SUPERSEDED_PENDING", 10, False),
    ],
)
def test_only_ended_jobs_with_a_closed_progress_window_are_settled(status, end_offset, settled):
    assert _probe_settled({"status": status, "progress_end_offset": end_offset}) is settled
    assert _probe_settled({"status": status}) is False


def test_a_stalled_job_is_probed_until_its_progress_window_closes(tmp_path):
    plan = _plan(1)
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", plan)
    job = next(job for job in _state(tmp_path)["jobs"].values() if job["status"] == "RUNNING")
    host = job["host"]
    state = manager._load_state("campaign")
    state["jobs"][job["name"]]["status"] = "STALLED"
    manager._write_state(state)
    manager.tick("campaign")
    assert job["name"] in _probed_names(transport, host)


def _fixture_proc(root, memory, io):
    (root / "pressure").mkdir(parents=True)
    for name, content in (("memory", memory), ("io", io)):
        if content is not None:
            (root / "pressure" / name).write_text(content, encoding="ascii")
    return root


@pytest.mark.parametrize(
    ("memory", "io", "expected"),
    [
        ("some avg10=12.50 avg60=1.00 avg300=0.50 total=1\nfull avg10=3.00 avg60=0 avg300=0 total=0\n",
         "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n", (12.5, 0.0)),
        ("full avg10=9.00 avg60=0 avg300=0 total=0\nsome avg60=1.00 avg10=26.25 avg300=0 total=1\n",
         "some avg10=100.00 avg60=0 avg300=0 total=0\n", (26.25, 100.0)),
        ("some avg10=101.00 avg60=0 avg300=0 total=0\n", "some avg10=-1.00 avg60=0\n", (None, None)),
        ("some avg10=oops avg60=0\n", "some avg60=0 avg300=0\n", (None, None)),
        ("", "full avg10=1.00\n", (None, None)),
        ("some avg10=nan avg60=0\n", None, (None, None)),
    ],
)
def test_real_probe_reads_some_avg10_from_a_fixture_proc_tree(tmp_path, memory, io, expected):
    spec = _spec_for_project(".")
    home, environment = _real_runner_home(tmp_path, spec)
    proc = _fixture_proc(tmp_path / "proc", memory, io)
    probe = subprocess.run(
        ["bash", "-c", probe_script({}, proc_root=str(proc))],
        cwd=home, env=environment, check=False, capture_output=True, text=True, timeout=10,
    )
    assert probe.returncode == 0, probe.stderr
    assert json.loads(probe.stdout) == {
        "jobs": [], "pressure": {"memory_some_avg10": expected[0], "io_some_avg10": expected[1]},
    }


def test_real_probe_without_a_proc_pressure_tree_reports_unknown(tmp_path):
    spec = _spec_for_project(".")
    home, environment = _real_runner_home(tmp_path, spec)
    probe = subprocess.run(
        ["bash", "-c", probe_script({}, proc_root=str(tmp_path / "absent"))],
        cwd=home, env=environment, check=False, capture_output=True, text=True, timeout=10,
    )
    assert json.loads(probe.stdout)["pressure"] == {"memory_some_avg10": None, "io_some_avg10": None}


def test_real_probe_of_two_checkout_groups_returns_each_job_row(tmp_path):
    first = _spec_for_project(".")
    second = dataclasses.replace(_spec_for_project("ciu"), batch=1)
    home, environment = _real_runner_home(tmp_path, first)
    jobs = {first.name: first, second.name: second}
    probe = subprocess.run(
        ["bash", "-c", probe_script(jobs)],
        cwd=home, env=environment, check=False, capture_output=True, text=True, timeout=10,
    )
    assert probe.returncode == 0, probe.stderr
    rows = {row["name"]: row for row in json.loads(probe.stdout)["jobs"]}
    assert set(rows) == set(jobs)
    assert {row["state"] for row in rows.values()} == {"MISSING"}
