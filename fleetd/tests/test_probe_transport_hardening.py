"""Probe size, finished-job filtering and pressure readings (FD-014, FD-022).

The real probe script runs ONLY under a temporary HOME on this machine, through
the same helpers the real-runner contract tests use; nothing here contacts a host.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import shlex
import subprocess

import pytest

from fleetd.contracts import ContractError
from fleetd import hostside
from fleetd.campaign import STALL_FLOOR_SECONDS, CampaignManager, _probe_settled
from fleetd.runner import JobSpec, RunnerJobs, probe_payload, probe_script
from fleetd.transport import FakeTransport, _read_command_metadata

from test_campaign import FakeClock, _plan, _real_runner_home, _spec_for_project, _state

MAX_ARG_STRLEN = 128 * 1024


def _probe_groups(script: str) -> list[dict[str, object]]:
    return hostside.decode_payload(shlex.split(script.splitlines()[-1])[-2])


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
    groups = _probe_groups(probe_script(jobs))
    assert {name for group in groups for name in group["names"]} == set(jobs)
    for group in groups:
        for name in group["names"]:
            assert group["ids_template"].replace("@NAME@", name) == jobs[name].candidate_file_home_path
            assert (group["commit"], group["project"], group["lane"]) == (
                jobs[name].commit, jobs[name].project, jobs[name].lane
            )
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
    return sorted(name for group in _probe_groups(probes[-1]) for name in group["names"])


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


def test_a_job_stopped_for_a_stall_after_the_launcher_closed_its_window_is_still_probed(tmp_path):
    """The launcher writes progress-end-offset before EXIT; a probe in that gap sees RUNNING
    with the offset set, and a stall stop in the same tick must not settle the job."""
    plan = _plan(1)
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", plan)
    job = next(job for job in _state(tmp_path)["jobs"].values() if job["status"] == "RUNNING")
    host = job["host"]
    transport.host(host).jobs[job["name"]].progress_end_offset = 0
    clock.advance(STALL_FLOOR_SECONDS + 1)
    manager.tick("campaign")
    stopped = _state(tmp_path)["jobs"][job["name"]]
    assert stopped["status"] in {"STALLED", "FAILED"}
    assert stopped["progress_end_offset"] == 0
    assert stopped["probe_settled"] is False
    manager.tick("campaign")
    assert job["name"] in _probed_names(transport, host)
    # The stop ended the job on the host; the EXIT row closes it for good.
    assert _state(tmp_path)["jobs"][job["name"]]["probe_settled"] is True
    manager.tick("campaign")
    assert job["name"] not in _probed_names(transport, host)


def test_a_job_from_an_older_state_without_the_flag_keeps_the_status_and_offset_rule(tmp_path):
    assert _probe_settled({"status": "COMPLETE", "progress_end_offset": 5}) is True
    assert _probe_settled({"status": "COMPLETE", "progress_end_offset": 5, "probe_settled": False}) is False
    assert _probe_settled({"status": "RUNNING", "progress_end_offset": 5, "probe_settled": True}) is True
    assert _probe_settled({"status": "COMPLETE", "progress_end_offset": None, "probe_settled": False}) is False


@pytest.mark.parametrize("field", ["campaign", "host", "project", "lane"])
def test_an_at_sign_in_a_path_template_field_is_refused_before_the_payload_is_built(field):
    """The path template stands the job name in with ``@NAME@``; no field may contain ``@``."""
    spec = _many_jobs(1)
    (name, good), = spec.items()
    values = {
        "campaign": good.campaign, "host": good.host, "project": good.project, "lane": good.lane,
    }
    values[field] = values[field] + "@NAME@"
    bad = dataclasses.replace(good, **values)
    with pytest.raises(ContractError):
        probe_payload({bad.name: bad})


def test_an_exit_row_without_a_closed_progress_window_is_not_settled(tmp_path):
    plan = _plan(1)
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", plan)
    job = next(job for job in _state(tmp_path)["jobs"].values() if job["status"] == "RUNNING")
    for candidate_id in job["candidate_ids"]:
        transport.add_progress(job["host"], job["name"], candidate_id, "killed")
    transport.finish(job["host"], job["name"])
    transport.host(job["host"]).jobs[job["name"]].progress_end_offset = None
    manager.tick("campaign")
    ended = _state(tmp_path)["jobs"][job["name"]]
    assert ended["probe_settled"] is False
    manager.tick("campaign")
    assert job["name"] in _probed_names(transport, job["host"])
