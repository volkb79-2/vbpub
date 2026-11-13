"""Fake-host transport, named-job, scheduler, and collector contracts."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from fleetd.campaign import CampaignManager, LOST_AFTER_SECONDS, STALL_FLOOR_SECONDS, _atomic_write
from fleetd.contracts import ContractError
from fleetd.plan import plan_campaign
from fleetd.runner import JobSpec, RunnerJobs, parse_progress, probe_script, start_script, stop_script, validate_job_spec
from fleetd.transport import FakeTransport, HostUnavailable, HxTransport, TransportError, shell_command
from test_plan import DEMAND, _candidate, _candidate_table, _golden_8g_host_set


EXECUTION = {"schema": "fleet-plan-execution/1", "commit": "a" * 40, "project": "."}


class FakeClock:
    def __init__(self, value: float = 100.0):
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _plan(count: int = 12) -> dict:
    hosts = _golden_8g_host_set()
    for index, host in enumerate(hosts["hosts"]):
        host["id"] = f"host-{chr(ord('a') + index)}"
    candidates = [
        _candidate(f"campaign-candidate-{index:03d}", 100 * 1024**2, source=f"src/group-{index % 5}.py")
        for index in range(count)
    ]
    plan = plan_campaign("r2", _candidate_table(candidates), hosts, DEMAND)
    plan["execution"] = dict(EXECUTION)
    return plan


def _two_host_one_candidate_plan() -> tuple[dict, str, str]:
    plan = _plan(2)
    candidates = [item for host in plan["hosts"] for item in host["assigned"]]
    assert len(candidates) == 2
    for index, host in enumerate(plan["hosts"]):
        candidate = candidates[index]
        host["assigned"] = [candidate]
        host["batches"] = [{"batch": 0, "kind": "regular", "candidate_ids": [candidate["id"]]}]
        host["eta_seconds"] = 95.0
    return plan, candidates[0]["id"], candidates[1]["id"]


def _state(directory: Path, campaign: str = "campaign") -> dict:
    return json.loads((directory / f"{campaign}.json").read_text(encoding="utf-8"))


def _jobs(state: dict, host: str, status: str | None = None) -> list[dict]:
    return [
        job for job in state["jobs"].values()
        if job["host"] == host and (status is None or job["status"] == status)
    ]


def _first_running(state: dict, host: str) -> dict:
    return next(job for job in _jobs(state, host) if job["status"] == "RUNNING")


def test_shell_commands_and_hx_transport_build_argv_without_running_a_host(monkeypatch, tmp_path):
    wrapper = tmp_path / "hx.sh"
    calls = []

    def success(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    monkeypatch.setattr("fleetd.transport.subprocess.run", success)
    assert HxTransport(wrapper).run("worker-a", ["printf", "a b"], 7) == (0, "ok", "")
    assert calls[0][0] == [str(wrapper), "worker-a", "printf 'a b'"]
    assert calls[0][1]["timeout"] == 7
    assert shell_command("printf 'literal script'") == "printf 'literal script'"
    assert shell_command(["echo", "two words"]) == "echo 'two words'"
    with pytest.raises(TransportError, match="must not be empty"):
        shell_command(" \n")
    with pytest.raises(TransportError, match="NUL-free"):
        shell_command(["echo", "bad\x00arg"])
    with pytest.raises(TransportError, match="non-empty executable"):
        shell_command([""])
    with pytest.raises(TransportError, match="host alias"):
        HxTransport(wrapper).run("bad alias", "true")


def test_hx_transport_timeout_and_launch_errors_are_reported(monkeypatch, tmp_path):
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("hx.sh", 1, output=b"part", stderr=b"late")

    monkeypatch.setattr("fleetd.transport.subprocess.run", timeout)
    assert HxTransport(tmp_path / "hx.sh").run("worker-a", "true", 1) == (124, "part", "late")

    def missing(*_args, **_kwargs):
        raise OSError("missing wrapper")

    monkeypatch.setattr("fleetd.transport.subprocess.run", missing)
    with pytest.raises(TransportError, match="could not launch"):
        HxTransport(tmp_path / "hx.sh").run("worker-a", "true")


def test_hx_file_operations_quote_paths_and_translate_errors(monkeypatch, tmp_path):
    transport = HxTransport(tmp_path / "hx.sh")
    scripts = []

    def success(host, command, timeout=None):
        scripts.append((host, command, timeout))
        if "base64 --wrap=0" in command:
            import base64
            return 0, base64.b64encode(b"saved").decode(), ""
        return 0, "", ""

    monkeypatch.setattr(transport, "run", success)
    transport.put("worker-a", "checkouts/rev/a b", b"bytes", 2)
    assert "base64 --decode" in scripts[0][1]
    assert transport.get("worker-a", "checkouts/rev/a b", 3) == b"saved"
    assert "'checkouts/rev/a b'" in scripts[0][1]
    with pytest.raises(TransportError, match="beneath the remote home"):
        transport.put("worker-a", "../outside", b"x")
    with pytest.raises(TransportError, match="beneath the remote home"):
        transport.get("worker-a", "/outside")

    monkeypatch.setattr(transport, "run", lambda *_args, **_kwargs: (255, "", "disconnected"))
    with pytest.raises(HostUnavailable, match="unavailable"):
        transport.put("worker-a", "a", b"x")
    with pytest.raises(HostUnavailable, match="unavailable"):
        transport.get("worker-a", "a")
    monkeypatch.setattr(transport, "run", lambda *_args, **_kwargs: (2, "", "bad remote command"))
    with pytest.raises(TransportError, match="could not upload"):
        transport.put("worker-a", "a", b"x")
    with pytest.raises(TransportError, match="could not fetch"):
        transport.get("worker-a", "a")
    monkeypatch.setattr(transport, "run", lambda *_args, **_kwargs: (0, "!", ""))
    with pytest.raises(TransportError, match="invalid base64"):
        transport.get("worker-a", "a")


def test_fake_transport_models_unreachable_slow_restart_and_duplicate_starts():
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    spec = JobSpec("campaign", "host-a", 0, 0, ("a" * 64,), "b" * 40, ".", "r2", 1)
    runner = RunnerJobs(transport)
    assert runner.start(spec) is True
    assert runner.start(spec) is False
    assert transport.host("host-a").starts == 1
    clock.advance(3)
    transport.set_slow("host-a")
    with pytest.raises(HostUnavailable):
        runner.probe("host-a", {spec.name: spec.candidate_ids})
    transport.set_slow("host-a", False)
    transport.restart("host-a")
    row = runner.probe("host-a", {spec.name: spec.candidate_ids})[spec.name]
    assert row["state"] == "EXIT" and row["exit_code"] == 137
    transport.set_unreachable("host-a")
    with pytest.raises(HostUnavailable):
        runner.probe("host-a", {spec.name: spec.candidate_ids})


def test_job_names_digest_and_command_builders_are_stable_and_scoped():
    values = ("b" * 64, "a" * 64)
    spec = JobSpec("campaign", "host-a", 0, 4, values, "c" * 40, "fleetd", "r2", 2)
    validate_job_spec(spec)
    expected = hashlib.sha256(("a" * 64 + "\n" + "b" * 64 + "\n").encode()).hexdigest()
    assert spec.list_digest == expected
    assert spec.name == f"campaign.host-a.s0.b4.{expected[:8]}"
    probe = probe_script({spec.name: values})
    start = start_script(spec)
    stop = stop_script(spec.name)
    assert "fleetd-campaign-command/1" in probe and "list.sha256" in probe
    assert "run-job" in start and "--only-candidates" in start and "R2_MAX_ATTEMPTS" in start
    assert "sha256sum" in start and "EXISTING" in start
    assert 'run-r2-campaign.sh\" stop' in stop
    assert "ssh " not in start and "docker " not in start
    with pytest.raises(ContractError, match="safe name"):
        validate_job_spec(JobSpec("../bad", "host-a", 0, 0, values, "c" * 40, ".", "r2", 1))
    with pytest.raises(ContractError, match="full lowercase Git commit"):
        validate_job_spec(JobSpec("campaign", "host-a", 0, 0, values, "C" * 40, ".", "r2", 1))


def test_runner_probe_start_stop_and_progress_errors_are_closed():
    transport = FakeTransport()
    runner = RunnerJobs(transport)
    spec = JobSpec("campaign", "host-a", 0, 0, ("a" * 64,), "b" * 40, ".", "r2", 1)
    assert runner.probe("host-a", {}) == {}
    assert runner.start(spec) is True
    assert runner.stop("host-a", spec.name) is True
    assert runner.stop("host-a", spec.name) is False
    assert parse_progress(b"not-json\n{}\n", {"a" * 64}) == []
    valid = {
        "event": "candidate", "candidate_id": "a" * 64, "outcome_bucket": "killed",
        "elapsed_seconds": float("nan"), "emitted_at": "now",
    }
    parsed = parse_progress((json.dumps(valid) + "\n").encode(), {"a" * 64})
    assert parsed[0]["elapsed_seconds"] is None
    assert parse_progress((json.dumps({**valid, "candidate_id": "c" * 64}) + "\n").encode(), {"a" * 64}) == []
    with pytest.raises(ContractError, match="not UTF-8"):
        parse_progress(b"\xff", set())
    with pytest.raises(ContractError, match="invalid candidate"):
        parse_progress((json.dumps({**valid, "candidate_id": "bad"}) + "\n").encode(), set())
    with pytest.raises(ContractError, match="unsupported candidate outcome"):
        parse_progress((json.dumps({**valid, "outcome_bucket": "unknown"}) + "\n").encode(), {"a" * 64})


def test_campaign_run_reinvocation_and_state_loss_re_adopt_named_jobs(tmp_path):
    plan = _plan(30)
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", plan)
    first_state = _state(tmp_path)
    assert {job["host"] for job in first_state["jobs"].values() if job["status"] == "RUNNING"} == {"host-a", "host-b"}
    starts = sum(host.starts for host in transport.hosts.values())
    manager.run("campaign", plan)
    assert sum(host.starts for host in transport.hosts.values()) == starts

    (tmp_path / "campaign.json").unlink()
    recovered = CampaignManager(tmp_path, transport, clock=clock).run("campaign", plan)
    recovered_state = _state(tmp_path)
    assert sum(host.starts for host in transport.hosts.values()) == starts
    assert all(job["adopted"] for job in recovered_state["jobs"].values() if job["status"] == "RUNNING")
    assert all(host["state"] == "ONLINE" for host in recovered["hosts"] if host["host"] in {"host-a", "host-b"})


def test_replanning_changes_name_and_stops_obsolete_running_job(tmp_path):
    plan = _plan(12)
    transport = FakeTransport()
    manager = CampaignManager(tmp_path, transport)
    manager.run("campaign", plan)
    old_state = _state(tmp_path)
    old_names = {job["name"] for job in old_state["jobs"].values() if job["status"] == "RUNNING"}
    changed = _plan(13)
    manager.run("campaign", changed)
    state = _state(tmp_path)
    assert old_names.isdisjoint({job["name"] for job in state["jobs"].values() if job["status"] == "RUNNING"})
    assert all(state["jobs"][name]["status"] == "SUPERSEDED" for name in old_names)
    assert sum(host.stops for host in transport.hosts.values()) == len(old_names)


def test_stall_floor_and_unreachable_pause_are_progress_based(tmp_path):
    plan = _plan(1)
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", plan)
    state = _state(tmp_path)
    job = next(job for job in state["jobs"].values() if job["status"] == "RUNNING")
    host_id = job["host"]
    transport.set_unreachable(host_id)
    clock.advance(1200)
    manager.tick("campaign")
    assert any(host["state"] == "UNREACHABLE" for host in manager.status("campaign")["hosts"] if host["host"] == host_id)
    transport.set_unreachable(host_id, False)
    manager.tick("campaign")
    state = _state(tmp_path)
    observed = state["jobs"][job["name"]]
    assert observed["paused_seconds"] == 1200
    clock.advance(STALL_FLOOR_SECONDS - 1)
    manager.tick("campaign")
    assert _state(tmp_path)["jobs"][job["name"]]["status"] == "RUNNING"
    clock.advance(1)
    manager.tick("campaign")
    assert _state(tmp_path)["jobs"][job["name"]]["status"] == "STALLED"
    assert _state(tmp_path)["jobs"][job["name"]]["stall_after_seconds"] == STALL_FLOOR_SECONDS


def test_stall_threshold_uses_three_times_observed_p95(tmp_path):
    plan = _plan(12)
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", plan)
    state = _state(tmp_path)
    job = _first_running(state, "host-a")
    transport.add_progress("host-a", job["name"], job["candidate_ids"][0], "killed", elapsed_seconds=1000)
    manager.status("campaign")
    clock.advance(2999)
    manager.tick("campaign")
    assert _state(tmp_path)["jobs"][job["name"]]["status"] == "RUNNING"
    clock.advance(1)
    manager.tick("campaign")
    assert _state(tmp_path)["jobs"][job["name"]]["stall_after_seconds"] == 3000


def test_lost_host_requeues_only_unfinished_candidates_and_uses_largest_cap(tmp_path):
    plan = _plan(40)
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", plan)
    before = _state(tmp_path)
    lost_job = _first_running(before, "host-b")
    completed = lost_job["candidate_ids"][0]
    transport.add_progress("host-b", lost_job["name"], completed, "survived", emitted_at="2026-11-13T00:00:00+00:00")
    manager.status("campaign")
    transport.set_unreachable("host-b")
    clock.advance(LOST_AFTER_SECONDS + 1)
    manager.tick("campaign")
    after = _state(tmp_path)
    requeued = [
        job for job in after["jobs"].values()
        if job.get("requeue_reason") == "host-lost" and job["host"] != "host-b"
    ]
    assert after["hosts"]["host-b"]["status"] == "LOST"
    assert requeued
    assert all(completed not in job["candidate_ids"] for job in requeued)
    assert set(candidate for job in requeued for candidate in job["candidate_ids"]) == set(lost_job["candidate_ids"][1:])
    expected_target = max(
        (host["cap_alone_bytes"], host["id"])
        for host in plan["hosts"] if host["id"] != "host-b" and host["status"] == "available"
    )[1]
    assert all(job["host"] == expected_target for job in requeued)


def test_returning_after_loss_can_finish_a_duplicate_without_double_counting(tmp_path):
    plan, first_id, second_id = _two_host_one_candidate_plan()
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", plan)
    state = _state(tmp_path)
    job_a = _first_running(state, "host-a")
    job_b = _first_running(state, "host-b")
    assert first_id in job_a["candidate_ids"] and second_id in job_b["candidate_ids"]
    transport.add_progress("host-a", job_a["name"], first_id, "killed", emitted_at="2026-11-13T00:00:02+00:00")
    transport.finish("host-a", job_a["name"])
    transport.set_unreachable("host-b")
    clock.advance(LOST_AFTER_SECONDS + 1)
    manager.tick("campaign")
    state = _state(tmp_path)
    retry = next(job for job in state["jobs"].values() if job.get("requeue_reason") == "host-lost")
    assert second_id in retry["candidate_ids"]
    transport.add_progress("host-a", retry["name"], second_id, "survived", emitted_at="2026-11-13T00:00:03+00:00")
    transport.finish("host-a", retry["name"])
    manager.tick("campaign")
    transport.set_unreachable("host-b", False)
    transport.add_progress("host-b", job_b["name"], second_id, "survived", emitted_at="2026-11-13T00:00:01+00:00")
    transport.finish("host-b", job_b["name"], 137)
    manager.tick("campaign")
    result, report, json_path, report_path = manager.collect("campaign")
    second = next(row for row in result["candidates"] if row["candidate_id"] == second_id)
    assert second["owner_host"] == "host-b"
    assert second["duplicates_ignored"] == 1
    assert result["totals"] == {
        "killed": 1, "survived": 1, "timeout": 0, "crashed": 0,
        "equivalent": 0, "missing": 0, "duplicates_ignored": 1,
    }
    raw_sum = sum(
        len(job.progress_records)
        for host in transport.hosts.values()
        for job in host.jobs.values()
    )
    assert raw_sum - result["totals"]["duplicates_ignored"] == 2
    assert "| Killed | Survived | Timeout |" in report
    assert json_path.is_file() and report_path.is_file()
    repeated, report_again, _, _ = manager.collect("campaign")
    assert repeated == result
    assert report_again == report


def test_collection_handles_timeout_verdicts_missing_artifacts_and_invalid_json(tmp_path):
    plan, first_id, second_id = _two_host_one_candidate_plan()
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", plan)
    state = _state(tmp_path)
    jobs = [_first_running(state, host_id) for host_id in ("host-a", "host-b")]
    first_job = next(job for job in jobs if first_id in job["candidate_ids"])
    second_job = next(job for job in jobs if second_id in job["candidate_ids"])
    transport.add_progress(first_job["host"], first_job["name"], first_id, "budget_exceeded")
    transport.finish(first_job["host"], first_job["name"], verdict=b"not-json")
    transport.add_progress(second_job["host"], second_job["name"], second_id, "survived")
    transport.finish(second_job["host"], second_job["name"])
    manager.tick("campaign")
    transport.set_unreachable(second_job["host"])
    manager.status("campaign")
    result, _, _, _ = manager.collect("campaign")
    assert result["totals"]["timeout"] == 1
    assert any(item["artifact"] == "invalid-verdict" and item["host"] == first_job["host"] for item in result["missing_artifacts"])
    assert any(item["host"] == second_job["host"] for item in result["missing_artifacts"])


def test_campaign_refuses_invalid_plan_execution_state_and_campaign_name(tmp_path):
    manager = CampaignManager(tmp_path, FakeTransport())
    with pytest.raises(ContractError, match="safe name"):
        manager.run("../bad", _plan(1))
    with pytest.raises(ContractError, match="execution context"):
        manager.run("campaign", {"schema": "fleet-plan/1", "lane": "r2", "hosts": []})
    plan = _plan(1)
    plan["execution"]["project"] = "../outside"
    with pytest.raises(ContractError, match="relative project path"):
        manager.run("campaign", plan)
    bad_commit = _plan(1)
    bad_commit["execution"]["commit"] = "short"
    with pytest.raises(ContractError, match="full lowercase Git commit"):
        manager.run("campaign", bad_commit)
    bad_hosts = _plan(1)
    bad_hosts["hosts"] = "wrong"
    with pytest.raises(ContractError, match="hosts: expected"):
        manager.run("campaign", bad_hosts)


def test_campaign_state_loading_and_atomic_write_reject_corruption(tmp_path):
    manager = CampaignManager(tmp_path, FakeTransport())
    with pytest.raises(ContractError, match="does not exist"):
        manager.status("missing")
    (tmp_path / "bad.json").write_text("not json", encoding="utf-8")
    with pytest.raises(ContractError, match="not valid UTF-8 JSON"):
        manager.status("bad")
    (tmp_path / "bad.json").write_text('{"schema":"other"}', encoding="utf-8")
    with pytest.raises(ContractError, match="unsupported schema"):
        manager.status("bad")
    path = tmp_path / "atomic.json"
    _atomic_write(path, b"first")
    _atomic_write(path, b"second")
    assert path.read_bytes() == b"second"
    malformed = tmp_path / "wrong-identity.json"
    malformed.write_text(json.dumps({"schema": "fleet-campaign-state/1", "campaign": "other", "hosts": {}, "jobs": {}}), encoding="utf-8")
    with pytest.raises(ContractError, match="identity or tables"):
        manager.status("wrong-identity")


def test_lost_host_without_alternate_host_defers_and_retries_when_available(tmp_path):
    plan = _plan(1)
    # Keep a one-host campaign so the first loss cannot choose a replacement.
    chosen_host = next(host for host in plan["hosts"] if host["assigned"])
    plan["hosts"] = [chosen_host]
    host_id = chosen_host["id"]
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", plan)
    job = _first_running(_state(tmp_path), host_id)
    transport.set_unreachable(host_id)
    clock.advance(LOST_AFTER_SECONDS + 1)
    manager.tick("campaign")
    state = _state(tmp_path)
    assert state["deferred_requeues"]
    transport.set_unreachable(host_id, False)
    manager.tick("campaign")
    assert _state(tmp_path)["hosts"][host_id]["status"] == "ONLINE"
    assert _state(tmp_path)["jobs"][job["name"]]["status"] == "LOST"


def test_large_golden_plan_runs_stall_and_loss_requeue_scenario(tmp_path):
    plan = _plan(230)
    assert len(plan["hosts"]) == 2
    assert sum(len(host["assigned"]) for host in plan["hosts"]) == 230
    assert all(host["assigned"] for host in plan["hosts"])
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("large", plan)
    state = _state(tmp_path, "large")
    stalled_job = _first_running(state, "host-a")
    transport.add_progress("host-a", stalled_job["name"], stalled_job["candidate_ids"][0], "killed", elapsed_seconds=500)
    manager.status("large")
    clock.advance(1500)
    manager.tick("large")
    after_stall = _state(tmp_path, "large")
    assert after_stall["jobs"][stalled_job["name"]]["status"] == "STALLED"
    assert any(job.get("requeue_reason") == "stalled" for job in after_stall["jobs"].values())

    lost_job = _first_running(after_stall, "host-b")
    finished = lost_job["candidate_ids"][0]
    transport.add_progress("host-b", lost_job["name"], finished, "survived")
    manager.status("large")
    transport.set_unreachable("host-b")
    clock.advance(LOST_AFTER_SECONDS + 1)
    manager.tick("large")
    final_state = _state(tmp_path, "large")
    lost_requeues = [job for job in final_state["jobs"].values() if job.get("requeue_reason") == "host-lost"]
    assert lost_requeues and all(finished not in job["candidate_ids"] for job in lost_requeues)
    assert final_state["hosts"]["host-b"]["status"] == "LOST"

