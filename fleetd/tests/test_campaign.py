"""Fake-host transport, named-job, scheduler, and collector contracts."""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import math
import os
import shutil
import subprocess
import time
from dataclasses import replace
from pathlib import Path

import pytest

from fleetd.campaign import CampaignManager, LOST_AFTER_SECONDS, STALL_FLOOR_SECONDS, _atomic_write, _event_key, _system_boot_id
from fleetd.contracts import ContractError
from fleetd.plan import plan_campaign
from fleetd.runner import (
    JobSpec,
    RunnerJobs,
    canonical_probe_json,
    parse_progress,
    probe_script,
    start_script,
    stop_script,
    validate_job_spec,
)
from fleetd.transport import (
    FakeTransport,
    HostUnavailable,
    HxTransport,
    TransportError,
    command_metadata,
    json_output,
    shell_command,
)
from fleetd.runner_layout import progress_home_path
from test_plan import DEMAND, _candidate, _candidate_table, _golden_8g_host_set


EXECUTION = {"schema": "fleet-plan-execution/1", "commit": "a" * 40, "project": "."}


class FakeClock:
    def __init__(self, value: float = 100.0):
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_system_boot_id_fails_closed_when_unavailable_or_invalid(monkeypatch):
    boot_path = Path("/proc/sys/kernel/random/boot_id")
    original_read_text = Path.read_text

    def unavailable(path, *args, **kwargs):
        if path == boot_path:
            raise OSError("missing boot id")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unavailable)
    with pytest.raises(ContractError, match="boot id is unavailable"):
        _system_boot_id()

    def malformed(path, *args, **kwargs):
        if path == boot_path:
            return "not-a-boot-id"
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", malformed)
    with pytest.raises(ContractError, match="boot id has an invalid format"):
        _system_boot_id()


def test_restart_rebase_leaves_a_monotonic_value_without_utc_partner(tmp_path):
    manager = CampaignManager(
        tmp_path,
        FakeTransport(),
        clock=FakeClock(50),
        utc_clock=FakeClock(1000),
        boot_id=lambda: "boot-after",
    )
    state = {"clock_boot_id": "boot-before", "updated_mono": 123.0}
    manager._rebase_after_restart(state)
    assert state["updated_mono"] == 123.0
    assert state["clock_boot_id"] == "boot-after"


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


def _real_runner_home(tmp_path: Path, spec: JobSpec) -> tuple[Path, dict[str, str]]:
    repo = Path(__file__).resolve().parents[2]
    runner_source = repo / "scripts/netcup/runner"
    home = tmp_path / "runner-home"
    runner_scripts = home / "runner-scripts"
    runner_scripts.mkdir(parents=True)
    for directory in ("jobs", "campaigns", "locks", "checkouts"):
        (home / directory).mkdir()
    shutil.copy2(runner_source / "run-job", runner_scripts / "run-job")
    shutil.copy2(runner_source / "run-r2-campaign.sh", runner_scripts / "run-r2-campaign.sh")
    (runner_scripts / "lib.sh").write_text(
        'RUNNER_HOME="${RUNNER_HOME:-$HOME}"\n'
        'RUNNER_UID="$(id -u)"\n'
        'GATES_SLICE=dev-gates.slice\n'
        'identity_check() { :; }\n',
        encoding="utf-8",
    )
    (runner_scripts / "run-lane.sh").write_text(
        """#!/usr/bin/env bash
set -euo pipefail
shopt -s nullglob
digest_files=("$HOME"/jobs/*/list.sha256)
(( ${#digest_files[@]} == 1 )) && [[ -s ${digest_files[0]} ]] || exit 79
commit=$1; project=$2; lane=$3; shift 3
ids=
while (( $# )); do
  if [[ $1 == --only-candidates ]]; then ids=$2; break; fi
  shift
done
project_dir="$RUNNER_HOME/checkouts/$commit/$project"
progress="$project_dir/.assay/progress-contract-assay.jsonl"
mkdir -p -- "$project_dir/.assay"
while IFS= read -r candidate; do
  printf '{"candidate_id":"%s","elapsed_seconds":1.0,"emitted_at":"2030-01-01T00:00:00Z","event":"candidate","outcome_bucket":"killed"}\\n' "$candidate" >> "$progress"
done < "$project_dir/$ids"
printf '{}\\n' > "$project_dir/.assay/verdict-contract-assay.json"
""",
        encoding="utf-8",
    )
    (runner_scripts / "run-lane.sh").chmod(0o755)

    checkout = home / "checkouts" / spec.commit
    project_dir = checkout if spec.project == "." else checkout / spec.project
    project_dir.mkdir(parents=True)
    (project_dir / ".gitignore").write_text(".assay/\n", encoding="utf-8")
    (project_dir / "run-gate.toml").write_text(
        '[lanes.contract-lane]\nkind = "assay"\nassay_lane = "contract-assay"\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(checkout), "init", "-q"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(checkout), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(checkout), "-c", "user.name=Fleetd Contract", "-c", "user.email=fleetd-contract@example.invalid", "commit", "-qm", "fixture"],
        check=True,
        capture_output=True,
    )
    ids_path = home / spec.candidate_file_home_path
    ids_path.parent.mkdir(parents=True, exist_ok=True)
    ids_path.write_text("".join(f"{candidate_id}\n" for candidate_id in spec.sorted_candidate_ids), encoding="ascii")
    environment = dict(os.environ)
    isolated_bin = tmp_path / "bin"
    isolated_bin.mkdir()
    for command in (
        "bash", "python3", "git", "sha256sum", "cut", "cat", "mkdir", "mv", "rm", "cp",
        "flock", "awk", "sleep", "nproc", "date", "tr", "sed", "grep", "ps", "nohup",
        "dirname", "basename", "id", "stat", "head", "tail", "uname", "env", "install",
    ):
        source = shutil.which(command, path=environment.get("PATH"))
        if source:
            (isolated_bin / command).symlink_to(source)
    environment.update({"HOME": str(home), "RUNNER_HOME": str(home), "PATH": str(isolated_bin)})
    return home, environment


def _real_start(home: Path, environment: dict[str, str], spec: JobSpec) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", start_script(spec)],
        cwd=home,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )


def _wait_real_job(home: Path, spec: JobSpec) -> Path:
    job_dir = home / "jobs" / spec.name
    deadline = time.monotonic() + 15
    while not (job_dir / "EXIT").is_file() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert (job_dir / "EXIT").is_file(), (job_dir / "job.log").read_text(encoding="utf-8")
    return job_dir


def _spec_for_project(project: str) -> JobSpec:
    return JobSpec(
        campaign="contract",
        host="host-a",
        slot=0,
        batch=0,
        candidate_ids=("a" * 64,),
        commit="b" * 40,
        project=project,
        lane="contract-lane",
        jobs=1,
    )


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


class ScriptedTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.put_calls = []

    def run(self, _host, _command, _timeout=None):
        return self.responses.pop(0)

    def put(self, host, path, content, _timeout=None):
        self.put_calls.append((host, path, content))

    def get(self, _host, _path, _timeout=None):
        return b""


def _probe_json(name, state="MISSING", digest=None):
    row = {"name": name, "state": state}
    if digest is not None:
        row["list_digest"] = digest
    return json.dumps({"jobs": [row]})


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

    def empty_timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("hx.sh", 1)

    monkeypatch.setattr("fleetd.transport.subprocess.run", empty_timeout)
    assert HxTransport(tmp_path / "hx.sh").run("worker-a", "true", 1) == (124, "", "transport timed out")

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
    transport.add_progress("host-a", spec.name, "a" * 64, "killed")
    transport.add_progress("host-a", spec.name, "a" * 64, "killed")
    row = runner.probe("host-a", {spec.name: spec})[spec.name]
    assert row["progress_count"] == 1 and len(row["records"]) == 2
    clock.advance(3)
    transport.set_slow("host-a")
    with pytest.raises(HostUnavailable):
        runner.probe("host-a", {spec.name: spec})
    transport.set_slow("host-a", False)
    transport.restart("host-a")
    row = runner.probe("host-a", {spec.name: spec})[spec.name]
    assert row["state"] == "EXIT" and row["exit_code"] == 137
    transport.set_unreachable("host-a")
    with pytest.raises(HostUnavailable):
        runner.probe("host-a", {spec.name: spec})


def test_job_names_digest_and_command_builders_are_stable_and_scoped():
    values = ("b" * 64, "a" * 64)
    spec = JobSpec("campaign", "host-a", 0, 4, values, "c" * 40, "fleetd", "r2", 2)
    validate_job_spec(spec)
    expected = hashlib.sha256(("a" * 64 + "\n" + "b" * 64 + "\n").encode()).hexdigest()
    assert spec.list_digest == expected
    assert spec.name == f"campaign.host-a.s0.b4.{expected[:8]}"
    probe = probe_script({spec.name: spec})
    start = start_script(spec)
    stop = stop_script(spec.name)
    assert "fleetd-campaign-command/1" in probe and "python3 -c" in probe
    assert spec.list_digest not in probe
    assert "run-job" in start and "--only-candidates" in start and "R2_MAX_ATTEMPTS" in start
    assert "sha256sum" in start and "EXISTING" in start
    assert 'run-r2-campaign.sh\" stop' in stop
    assert "ssh " not in start and "docker " not in start
    assert canonical_probe_json({"jobs": []}) == '{"jobs":[]}'
    with pytest.raises(ContractError, match="probe job key"):
        probe_script({"wrong-name": spec})
    with pytest.raises(ContractError, match="safe name"):
        stop_script("unsafe/name")
    with pytest.raises(ContractError, match="safe name"):
        validate_job_spec(JobSpec("../bad", "host-a", 0, 0, values, "c" * 40, ".", "r2", 1))
    with pytest.raises(ContractError, match="full lowercase Git commit"):
        validate_job_spec(JobSpec("campaign", "host-a", 0, 0, values, "C" * 40, ".", "r2", 1))


def test_runner_probe_start_stop_and_progress_errors_are_closed():
    transport = FakeTransport()
    runner = RunnerJobs(transport)
    spec = JobSpec("campaign", "host-a", 0, 0, ("a" * 64,), "b" * 40, ".", "r2", 1)
    assert runner.probe("host-a", {}) == {}
    assert transport.calls[-1][1] == "run"
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


def test_transport_paths_fake_metadata_and_fake_file_failures():
    with pytest.raises(TransportError, match="NUL-free"):
        shell_command(["echo", "bad\x00arg"])
    transport = FakeTransport()
    assert transport.run("host-a", "true") == (0, "", "")
    with pytest.raises(TransportError, match="NUL byte"):
        transport.put("host-a", "nested/bad\x00path", b"x")
    with pytest.raises(TransportError, match="non-empty path"):
        transport.get("host-a", "")

    assert transport.run("host-a", "# fleetd-campaign-command/1 %%%") == (0, "", "")
    assert transport.run("host-a", command_metadata("unknown", {}))[0] == 2
    rc, out, err = transport.run("host-a", command_metadata("probe", {"jobs": "not-an-array"}))
    assert (rc, json.loads(out), err) == (0, {"jobs": []}, "")

    transport.put("host-a", "nested/file", b"saved")
    assert transport.get("host-a", "nested/file") == b"saved"
    transport.set_slow("host-a")
    assert transport.run("host-a", "true") == (124, "", "transport timed out")
    with pytest.raises(HostUnavailable, match="timed out"):
        transport.put("host-a", "a", b"x")
    with pytest.raises(HostUnavailable, match="timed out"):
        transport.get("host-a", "a")
    transport.set_slow("host-a", False)
    with pytest.raises(TransportError, match="does not exist"):
        transport.get("host-a", "missing")
    transport.set_unreachable("host-a")
    with pytest.raises(HostUnavailable, match="unreachable"):
        transport.put("host-a", "a", b"x")
    with pytest.raises(HostUnavailable, match="unreachable"):
        transport.get("host-a", "a")
    assert json_output({"z": 2, "a": 1}) == '{"a":1,"z":2}'


def test_fake_probe_uses_shared_runner_files_and_rejects_invalid_start_inputs():
    transport = FakeTransport()
    runner = RunnerJobs(transport)
    spec = JobSpec("campaign", "host-a", 0, 0, ("a" * 64,), "b" * 40, ".", "r2", 1)
    runner.start(spec)
    host = transport.host("host-a")
    digest_path = f"jobs/{spec.name}/list.sha256"
    progress_path = progress_home_path(spec.commit, spec.project, spec.lane)
    host.files[progress_path] = b"not-json\n"
    del host.files[digest_path]

    row = runner.probe("host-a", {spec.name: spec})[spec.name]
    assert row["list_digest"] == spec.list_digest
    assert row["progress_path"] == progress_path
    assert row["records"] == []

    mismatch = command_metadata("start", {
        "name": "other-job",
        "list_digest": "bad",
        "ids_path": spec.candidate_file_home_path,
    })
    assert transport.run("host-a", mismatch) == (73, "", "candidate ids file digest mismatch")

    rc, out, err = transport.run("host-a", command_metadata("probe", {"jobs": [None]}))
    assert (rc, json.loads(out), err) == (0, {"jobs": []}, "")

    del host.files[spec.candidate_file_home_path]
    row = runner.probe("host-a", {spec.name: spec})[spec.name]
    assert row["list_digest"] is None
    assert row["progress_count"] == 0
    assert transport.run("host-a", start_script(spec)) == (73, "", "candidate ids file is missing or invalid")


def test_fake_probe_clamps_offsets_and_ignores_non_candidate_events():
    transport = FakeTransport()
    runner = RunnerJobs(transport)
    candidate_id = "a" * 64
    spec = JobSpec("campaign", "host-a", 0, 0, (candidate_id,), "b" * 40, ".", "r2", 1)
    runner.start(spec)
    host = transport.host("host-a")
    job = host.jobs[spec.name]
    raw_progress = json.dumps({"candidate_id": candidate_id, "event": "heartbeat"}).encode() + b"\n"
    host.files[job.progress_path] = raw_progress

    row = runner.probe("host-a", {spec.name: spec})[spec.name]
    assert row["records"] == []

    job.progress_offset = len(raw_progress) + 1
    row = runner.probe("host-a", {spec.name: spec})[spec.name]
    assert row["progress_end_offset"] is None
    assert row["records"] == []


def test_fake_probe_skips_malformed_verdict_mutation_shapes():
    verdicts = [
        {"claims": "not-an-array"},
        {"claims": [{"rigor": "R2", "mutation": {"killed": "not-an-array"}}]},
        {"claims": [{"rigor": "R2", "mutation": {"killed": [None, {"candidate_id": "f" * 64}]}}]},
    ]
    for index, verdict in enumerate(verdicts):
        transport = FakeTransport()
        runner = RunnerJobs(transport)
        spec = JobSpec(f"campaign-{index}", "host-a", 0, 0, ("a" * 64,), "b" * 40, ".", "r2", 1)
        runner.start(spec)
        transport.finish("host-a", spec.name, verdict=json.dumps(verdict).encode())
        row = runner.probe("host-a", {spec.name: spec})[spec.name]
        assert row["verdict_records"] == []


def test_fake_job_table_handles_direct_duplicate_start_and_repeated_restart():
    transport = FakeTransport()
    spec = JobSpec("campaign", "host-a", 0, 0, ("a" * 64,), "b" * 40, ".", "r2", 1)
    runner = RunnerJobs(transport)
    assert runner.start(spec)
    rc, out, err = transport.run("host-a", start_script(spec))
    assert (rc, out.strip(), err) == (0, "EXISTING", "")
    other_id = "c" * 64
    host = transport.host("host-a")
    host.files[spec.candidate_file_home_path] = f"{other_id}\n".encode("ascii")
    other_digest = hashlib.sha256(f"{other_id}\n".encode("ascii")).hexdigest()
    mismatch = command_metadata("start", {
        "name": spec.name,
        "list_digest": other_digest,
        "ids_path": spec.candidate_file_home_path,
    })
    assert transport.run("host-a", mismatch) == (73, "", "named job exists with another candidate-list digest")
    transport.restart("host-a")
    assert transport.host("host-a").jobs[spec.name].exit_code == 137
    transport.restart("host-a")
    assert transport.host("host-a").jobs[spec.name].exit_code == 137


def test_runner_validation_rejects_every_unsafe_job_field():
    base = JobSpec("campaign", "host-a", 0, 0, ("a" * 64,), "b" * 40, ".", "r2", 1)
    cases = [
        (replace(base, campaign="../bad"), "safe name"),
        (replace(base, host=""), "host id"),
        (replace(base, slot=True), "slot"),
        (replace(base, slot=-1), "slot"),
        (replace(base, batch=True), "batch"),
        (replace(base, batch=-1), "batch"),
        (replace(base, candidate_ids=()), "candidate list"),
        (replace(base, candidate_ids=("bad",)), "candidate list"),
        (replace(base, candidate_ids=("a" * 64, "a" * 64)), "duplicate"),
        (replace(base, project="../outside"), "project"),
        (replace(base, lane="../lane"), "lane"),
        (replace(base, jobs=True), "worker count"),
        (replace(base, jobs=0), "worker count"),
        (replace(base, campaign="c" * 120, host="h" * 120), "too long"),
    ]
    for spec, message in cases:
        with pytest.raises(ContractError, match=message):
            validate_job_spec(spec)


def test_runner_probe_start_and_stop_translate_malformed_responses_and_failures():
    spec = JobSpec("campaign", "host-a", 0, 0, ("a" * 64,), "b" * 40, ".", "r2", 1)
    name = spec.name
    for response in ((124, "", "timeout"), (255, "", "down")):
        with pytest.raises(HostUnavailable):
            RunnerJobs(ScriptedTransport([response])).probe("host-a", {name: spec})
    for status in (97, 98):
        with pytest.raises(TransportError, match=f"exit {status}"):
            RunnerJobs(ScriptedTransport([(status, "", "refused")])).probe("host-a", {name: spec})
    with pytest.raises(TransportError, match="exit 2"):
        RunnerJobs(ScriptedTransport([(2, "", "bad")])).probe("host-a", {name: spec})
    with pytest.raises(TransportError, match="invalid JSON"):
        RunnerJobs(ScriptedTransport([(0, "not json", "")])).probe("host-a", {name: spec})
    for output in (
        json.dumps({"jobs": "bad"}),
        json.dumps({"jobs": [None]}),
        json.dumps({"jobs": [{"state": "RUNNING"}]}),
        json.dumps({"jobs": []}),
    ):
        with pytest.raises(TransportError):
            RunnerJobs(ScriptedTransport([(0, output, "")])).probe("host-a", {name: spec})

    mismatch = _probe_json(name, "RUNNING", "bad")
    with pytest.raises(TransportError, match="different candidate-list digest"):
        RunnerJobs(ScriptedTransport([(0, mismatch, "")])).start(spec)
    existing = _probe_json(name, "RUNNING", spec.list8)
    with pytest.raises(TransportError, match="different candidate-list digest"):
        RunnerJobs(ScriptedTransport([(0, existing, "")])).start(spec)
    assert RunnerJobs._digest_matches(None, spec) is False
    assert RunnerJobs._digest_matches(spec.list8, spec) is False

    missing = _probe_json(name)
    started = ScriptedTransport([(0, missing, ""), (0, "STARTED\n", "")])
    assert RunnerJobs(started).start(spec) is True
    assert started.put_calls[0][2] == ("a" * 64 + "\n").encode()

    full_digest = _probe_json(name, "RUNNING", spec.list_digest)
    recovered = ScriptedTransport([
        (0, missing, ""), (1, "", "raced"), (0, full_digest, ""),
    ])
    assert RunnerJobs(recovered).start(spec) is False
    failed = ScriptedTransport([(0, missing, ""), (1, "", "start failed"), (0, missing, "")])
    with pytest.raises(TransportError, match="start-job failed"):
        RunnerJobs(failed).start(spec)
    unexpected = ScriptedTransport([(0, missing, ""), (0, "unexpected\n", "")])
    with pytest.raises(TransportError, match="unexpected response"):
        RunnerJobs(unexpected).start(spec)

    for response in ((124, "", "timeout"), (255, "", "down")):
        with pytest.raises(HostUnavailable):
            RunnerJobs(ScriptedTransport([response])).stop("host-a", name)
    with pytest.raises(TransportError, match="exit 2"):
        RunnerJobs(ScriptedTransport([(2, "", "bad")])).stop("host-a", name)


def test_progress_parser_filters_noise_and_normalizes_optional_fields():
    expected = "a" * 64
    events = [
        None,
        {"event": "heartbeat"},
        {"event": "candidate", "candidate_id": "b" * 64, "outcome_bucket": "killed"},
        {"event": "candidate", "candidate_id": expected, "outcome_bucket": "killed", "elapsed_seconds": True, "emitted_at": 7},
    ]
    with pytest.raises(ContractError, match="unsupported candidate outcome"):
        parse_progress((json.dumps({"event": "candidate", "candidate_id": expected, "outcome_bucket": 7}) + "\n").encode(), {expected})
    content = "not json\n" + "\n".join(json.dumps(event) for event in events) + "\n"
    records = parse_progress(content.encode(), {expected})
    assert records == [{
        "candidate_id": expected, "outcome_bucket": "killed",
        "elapsed_seconds": None, "emitted_at": None,
    }]
    prefix = b'{"event":"candidate","candidate_id":"' + ("b" * 64).encode() + b'","outcome_bucket":"killed"}\n'
    bounded = prefix + content.encode()
    assert parse_progress(
        bounded, {expected}, start_offset=len(prefix), end_offset=len(bounded)
    ) == records
    with pytest.raises(ContractError, match="start offset"):
        parse_progress(b"", set(), start_offset=True)
    with pytest.raises(ContractError, match="end offset"):
        parse_progress(b"", set(), start_offset=2, end_offset=1)


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
    failed = _state(tmp_path)["jobs"][job["name"]]
    assert failed["status"] == "FAILED"
    assert failed["exit_code"] == 143
    assert failed["stall_after_seconds"] == STALL_FLOOR_SECONDS
    assert not any(item.get("requeue_reason") == "stalled" for item in _state(tmp_path)["jobs"].values())


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


def test_first_stall_window_uses_batch_prediction_and_declared_baseline(tmp_path):
    plan = _plan(1)
    plan["time_reference"] = {"reason": "no comparable host scores"}
    plan["demand"]["baseline_seconds"] = 400
    assigned = next(candidate for host in plan["hosts"] for candidate in host["assigned"])
    assigned["time_ref_seconds"] = 560
    clock = FakeClock()
    manager = CampaignManager(tmp_path, FakeTransport(clock=clock), clock=clock)
    manager.run("slow", plan)
    state = _state(tmp_path, "slow")
    job = next(job for job in state["jobs"].values() if job["status"] == "RUNNING")
    assert CampaignManager._stall_threshold(state, job) == 3 * 560 + 400

    clock.advance(3 * 560 + 400 - 1)
    manager.tick("slow")
    assert _state(tmp_path, "slow")["jobs"][job["name"]]["status"] == "RUNNING"


def test_predicted_candidate_times_skip_invalid_measurements_and_nonfinite_results():
    plan = _plan(1)
    host = next(row for row in plan["hosts"] if row["assigned"])
    candidate_id = host["assigned"][0]["id"]
    job = {"host": host["id"], "candidate_ids": [candidate_id]}
    state = {
        "plan": plan,
        "candidate_metadata": {candidate_id: {"time_ref_seconds": True}},
    }
    assert CampaignManager._predicted_candidate_seconds(state, job) == []

    state["candidate_metadata"][candidate_id]["time_ref_seconds"] = 560
    plan["time_reference"] = {
        "baseline_score": 100, "geekbench_major": 6, "geekbench_version": "6.3.0",
    }
    host["speed"] = {
        "baseline_score": 100, "geekbench_major": 6, "geekbench_version": "6.3.0",
        "single_core": 100,
    }
    host["f_h"] = math.nan
    assert CampaignManager._predicted_candidate_seconds(state, job) == []


def test_stall_stop_that_flushes_final_progress_does_not_create_an_empty_replacement(tmp_path):
    plan = _plan(2)
    candidates = [candidate for host in plan["hosts"] for candidate in host["assigned"]]
    owner = next(host for host in plan["hosts"] if host["assigned"])
    owner["assigned"] = candidates
    owner["batches"] = [{
        "batch": 0, "kind": "regular", "candidate_ids": [candidate["id"] for candidate in candidates],
    }]
    for host in plan["hosts"]:
        if host is not owner:
            host["assigned"] = []
            host["batches"] = []
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", plan)
    job = next(iter(_state(tmp_path)["jobs"].values()))
    first_id, last_id = job["candidate_ids"]
    transport.add_progress(job["host"], job["name"], first_id, "killed")
    manager.status("campaign")
    state = _state(tmp_path)
    state_job = state["jobs"][job["name"]]

    def flush_final_event(_host, _name):
        state_job["records"].append({
            "candidate_id": last_id, "outcome_bucket": "survived", "elapsed_seconds": 1,
            "emitted_at": "2030-01-01T00:00:01+00:00",
        })

    manager.runner.stop = flush_final_event
    clock.advance(STALL_FLOOR_SECONDS + 1)
    manager._detect_stalls(state)

    assert state_job["status"] == "STALLED"
    assert len(state["jobs"]) == 1


def test_first_stall_window_uses_baseline_observed_on_the_same_host(tmp_path):
    plan = _plan(1)
    plan["time_reference"] = {"reason": "no comparable host scores"}
    assigned = next(candidate for host in plan["hosts"] for candidate in host["assigned"])
    assigned["time_ref_seconds"] = 560
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("observed", plan)
    job = next(iter(_state(tmp_path, "observed")["jobs"].values()))
    transport.add_progress(job["host"], job["name"], job["candidate_ids"][0], "killed")
    verdict = {
        "judgment": {
            "r2": {
                "r2_command": {"r2_baseline": {"wall_s": 400}},
                "mutation": {"killed": [{"candidate_id": job["candidate_ids"][0]}]},
            },
        },
    }
    transport.finish(job["host"], job["name"], verdict=json.dumps(verdict).encode())
    manager.status("observed")

    state = _state(tmp_path, "observed")
    observed_job = state["jobs"][job["name"]]
    assert observed_job["baseline_seconds"] == 400
    observed_job["finished_ids"] = []
    assert CampaignManager._stall_threshold(state, observed_job) == 3 * 560 + 400


def test_failed_zero_progress_batch_is_not_requeued_across_ten_ticks(tmp_path):
    plan = _plan(1)
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", plan)
    job = next(iter(_state(tmp_path)["jobs"].values()))
    transport.finish(job["host"], job["name"], exit_code=2)

    for _ in range(10):
        status = manager.tick("campaign")

    state = _state(tmp_path)
    assert len(state["jobs"]) == 1
    failed = state["jobs"][job["name"]]
    assert failed["status"] == "FAILED" and failed["exit_code"] == 2
    host = next(item for item in status["hosts"] if item["host"] == job["host"])
    batch = next(item for item in host["batches"] if item["name"] == job["name"])
    assert batch["state"] == "FAILED" and batch["exit_code"] == 2
    result, report, _, _ = manager.collect("campaign")
    row = next(row for row in result["candidates"] if row["candidate_id"] == job["candidate_ids"][0])
    assert row["outcome"] == "missing" and row["reason"] == "exit code 2"
    assert result["failed_jobs"][0]["exit_code"] == 2
    assert "exit code 2" in report


def test_requeue_exited_preserves_missing_reason_and_replans_pending_progress(tmp_path):
    manager = CampaignManager(tmp_path, FakeTransport())
    missing = {
        "name": "missing-job", "host": "host-a", "status": "EXIT", "requeued": False,
        "missing_job": True, "finished_ids": [], "candidate_ids": [], "started_mono": 1.0,
    }
    missing_state = {"jobs": {"missing-job": missing}, "deferred_requeues": []}
    manager._requeue_exited(missing_state)
    assert missing["status"] == "FAILED"
    assert missing["failure_reason"] == "missing_job (exit code unavailable)"

    first_id, pending_id = "a" * 64, "b" * 64
    progressed = {
        "name": "progressed-job", "host": "host-a", "status": "EXIT", "requeued": False,
        "missing_job": False, "finished_ids": [first_id], "candidate_ids": [first_id, pending_id],
        "records": [{"candidate_id": first_id}],
    }
    state = {
        "jobs": {"progressed-job": progressed}, "deferred_requeues": [],
        "hosts": {"host-a": {"status": "ONLINE"}},
    }
    requeued = []
    manager._enqueue_replanned = lambda _state, source, ids, target, reason: requeued.append(
        (source["name"], ids, target, reason)
    )
    manager._requeue_exited(state)
    assert requeued == [("progressed-job", [pending_id], "host-a", "job-exit")]


def test_one_host_probe_error_is_reported_without_stopping_other_hosts(tmp_path):
    plan, _first_id, _second_id = _two_host_one_candidate_plan()
    transport = FakeTransport()
    manager = CampaignManager(tmp_path, transport)
    manager.run("campaign", plan)
    probe = manager.runner.probe

    def fail_host_a(host, wanted):
        if host == "host-a":
            raise TransportError("host-a probe rejected")
        return probe(host, wanted)

    manager.runner.probe = fail_host_a
    status = manager.tick("campaign")
    hosts = {row["host"]: row for row in status["hosts"]}
    assert hosts["host-a"]["state"] == "ERROR"
    assert hosts["host-a"]["error"] == "host-a probe rejected"
    assert hosts["host-b"]["state"] == "ONLINE"


def test_stop_errors_are_recorded_for_the_affected_host(tmp_path):
    def fail_stop(*_args):
        raise TransportError("stop rejected")

    superseded_manager = CampaignManager(tmp_path / "superseded", FakeTransport())
    superseded_manager.run("campaign", _plan(1))
    superseded_state = _state(tmp_path / "superseded")
    superseded_job = next(iter(superseded_state["jobs"].values()))
    superseded_job["status"] = "SUPERSEDED_PENDING"
    superseded_job["stop_pending"] = True
    superseded_manager.runner.stop = fail_stop
    superseded_manager._stop_superseded_host(superseded_state, superseded_job["host"])
    assert superseded_state["hosts"][superseded_job["host"]]["status"] == "ERROR"

    duplicate_manager = CampaignManager(tmp_path / "duplicate", FakeTransport())
    duplicate_manager.run("campaign", _two_host_one_candidate_plan()[0])
    duplicate_state = _state(tmp_path / "duplicate")
    first = next(job for job in duplicate_state["jobs"].values() if job["host"] == "host-a")
    second = next(job for job in duplicate_state["jobs"].values() if job["host"] == "host-b")
    second["records"].append({"candidate_id": first["candidate_ids"][0]})
    duplicate_manager.runner.stop = fail_stop
    duplicate_manager._stop_returned_duplicates(duplicate_state, "host-a")
    assert duplicate_state["hosts"]["host-a"]["status"] == "ERROR"

    clock = FakeClock()
    stall_manager = CampaignManager(tmp_path / "stall", FakeTransport(clock=clock), clock=clock)
    stall_manager.run("campaign", _plan(1))
    stall_state = _state(tmp_path / "stall")
    stalled_job = next(iter(stall_state["jobs"].values()))
    clock.advance(STALL_FLOOR_SECONDS + 1)
    stall_manager.runner.stop = fail_stop
    stall_manager._detect_stalls(stall_state)
    assert stall_state["hosts"][stalled_job["host"]]["status"] == "ERROR"
    assert stalled_job["status"] == "RUNNING"


def test_collect_reports_assay_not_started_candidates_as_missing(tmp_path):
    plan = _plan(12)
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", plan)
    job = _first_running(_state(tmp_path), "host-a")
    assert len(job["candidate_ids"]) >= 2
    finished_id, not_started_id = job["candidate_ids"][:2]
    transport.add_progress(job["host"], job["name"], finished_id, "killed")
    verdict = {
        "judgment": {
            "r2": {
                "mutation": {
                    "killed": [{"candidate_id": finished_id}],
                    "budget_exceeded": [{
                        "candidate_id": not_started_id,
                        "process_tree_evidence": {"leader_status": "not-started"},
                    }],
                },
            },
        },
    }
    transport.finish(job["host"], job["name"], exit_code=4, verdict=json.dumps(verdict).encode())

    manager.status("campaign")
    result, _, _, _ = manager.collect("campaign")
    finished = next(row for row in result["candidates"] if row["candidate_id"] == finished_id)
    missing = next(row for row in result["candidates"] if row["candidate_id"] == not_started_id)
    assert finished["outcome"] == "killed"
    assert missing["outcome"] == "missing"
    assert missing["reason"] == "never started (Assay process tree status: not-started)"
    assert result["totals"]["timeout"] == 0
    assert result["totals"]["missing"] >= 1


@pytest.mark.parametrize(("status", "offset", "end_offset"), [
    ("RUNNING", True, None),
    ("RUNNING", 0, -1),
    ("FAILED", 0, None),
])
def test_collect_skips_invalid_or_open_progress_ranges(tmp_path, status, offset, end_offset):
    transport = FakeTransport()
    manager = CampaignManager(tmp_path, transport)
    manager.run("campaign", _plan(1))
    manager.status("campaign")
    state = _state(tmp_path)
    job = next(iter(state["jobs"].values()))
    job["status"] = status
    job["progress_offset"] = offset
    job["progress_end_offset"] = end_offset
    transport.host(job["host"]).files[job["progress_path"]] = b""

    result = manager._collect_result(state)

    assert result["totals"]["missing"] == 1


def test_prior_campaign_progress_does_not_count_for_a_new_batch_on_same_commit(tmp_path):
    plan = _plan(1)
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("earlier", plan)
    earlier = next(iter(_state(tmp_path, "earlier")["jobs"].values()))
    candidate_id = earlier["candidate_ids"][0]
    transport.add_progress(earlier["host"], earlier["name"], candidate_id, "killed")
    transport.finish(earlier["host"], earlier["name"])
    manager.status("earlier")

    manager.run("later", plan)
    manager.status("later")
    later = next(iter(_state(tmp_path, "later")["jobs"].values()))
    assert later["candidate_ids"] == [candidate_id]
    assert later["progress_offset"] > 0
    assert later["finished_ids"] == []
    transport.finish(later["host"], later["name"], exit_code=2)
    for _ in range(10):
        manager.tick("later")

    state = _state(tmp_path, "later")
    assert len(state["jobs"]) == 1
    assert state["jobs"][later["name"]]["status"] == "FAILED"
    assert state["jobs"][later["name"]]["finished_ids"] == []


def test_lost_host_requeues_only_unfinished_candidates_and_uses_largest_cap(tmp_path):
    plan = _plan(40)
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", plan)
    before = _state(tmp_path)
    lost_job = _first_running(before, "host-b")
    lost_candidates = {
        candidate
        for row in before["jobs"].values()
        if row["host"] == "host-b"
        for candidate in row["candidate_ids"]
    }
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
    assert set(candidate for job in requeued for candidate in job["candidate_ids"]) == lost_candidates - {completed}
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


def test_assay_verdict_parser_accepts_claim_and_judgment_shapes_and_outcome_buckets():
    first, second = "a" * 64, "b" * 64
    mutation = {
        "killed": [{"candidate_id": first}],
        "survived": [{"candidate_id": second}],
        "budget_exceeded": [{"candidate_id": first}],
        "hung": [{"candidate_id": second}],
        "crashed": [{"candidate_id": first}],
        "equivalent": [{"candidate_id": second}],
        "ignored": [{"candidate_id": first}],
    }
    document = {
        "claims": [None, {"rigor": "R1", "mutation": mutation},
                   {"rigor": "R2", "mutation": mutation},
                   {"rigor": "R2", "mutation": "bad"},
                   {"rigor": "R2", "mutation": {
                       "killed": "not-an-array", "survived": [None, {}, {"candidate_id": 4},
                       {"candidate_id": "f" * 64}],
                   }}],
        "judgment": {"r2": {"mutation": {"crashed": [{"candidate_id": second}]}}},
    }
    records = CampaignManager._verdict_records(json.dumps(document).encode(), {first, second})
    assert [row["outcome_bucket"] for row in records] == [
        "killed", "survived", "budget_exceeded", "hung", "crashed", "equivalent", "crashed"
    ]
    assert CampaignManager._verdict_records(b"[]", {first}) == []
    assert CampaignManager._verdict_records(json.dumps({"claims": "bad", "judgment": {"r2": "bad"}}).encode(), {first}) == []
    with pytest.raises(ContractError, match="not UTF-8 JSON"):
        CampaignManager._verdict_records(b"\xff", {first})
    with pytest.raises(ContractError, match="not UTF-8 JSON"):
        CampaignManager._verdict_records(b"not-json", {first})
    assert CampaignManager._outcome("budget_exceeded") == "timeout"
    assert CampaignManager._outcome("hung") == "timeout"
    assert CampaignManager._outcome("killed") == "killed"
    assert CampaignManager._outcome("survived") == "survived"
    assert CampaignManager._outcome("crashed") == "crashed"
    assert CampaignManager._outcome("equivalent") == "equivalent"
    with pytest.raises(ContractError, match="unsupported Assay outcome"):
        CampaignManager._outcome("unknown")
    assert CampaignManager._job_for_record({"jobs": {}}, "host-a", first) == "unknown"
    assert math.isinf(_event_key({"emitted_at": "invalid"}, "host-a", "job-a")[0])
    assert _event_key({"emitted_at": "2026-11-13T00:00:00Z"}, "host-a", "job-a")[0] < math.inf


def test_collect_uses_assay_verdict_when_progress_is_missing(tmp_path):
    plan = _plan(1)
    transport = FakeTransport()
    manager = CampaignManager(tmp_path, transport)
    manager.run("campaign", plan)
    job = next(iter(_state(tmp_path)["jobs"].values()))
    candidate_id = job["candidate_ids"][0]
    verdict = json.dumps({
        "claims": [{"rigor": "R2", "mutation": {"killed": [{"candidate_id": candidate_id}]}}]
    }).encode()
    transport.finish(job["host"], job["name"], verdict=verdict)
    result, report, _, _ = manager.collect("campaign")
    row = next(row for row in result["candidates"] if row["candidate_id"] == candidate_id)
    assert (row["outcome"], row["owner_host"], row["owner_job"]) == ("killed", job["host"], job["name"])
    assert result["totals"]["killed"] == 1
    assert any(item["artifact"] == "progress" for item in result["missing_artifacts"])
    assert "invalid-verdict" not in report


def test_status_reports_observed_eta_and_deterministic_rounding(tmp_path):
    plan = _plan(12)
    host_plan = next(host for host in plan["hosts"] if host["batches"])
    host_plan["eta_seconds"] = None
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", plan)
    job = _first_running(_state(tmp_path), host_plan["id"])
    transport.add_progress(host_plan["id"], job["name"], job["candidate_ids"][0], "survived")
    clock.advance(10)
    host = next(item for item in manager.status("campaign")["hosts"] if item["host"] == host_plan["id"])
    batch = next(item for item in host["batches"] if item["name"] == job["name"])
    assert batch["done"] == 1
    assert batch["rate_candidates_per_second"] == 0.1
    assert batch["eta_seconds"] is not None and batch["eta_seconds"] > 0
    assert CampaignManager._round(None) is None
    assert CampaignManager._round(math.inf) is None
    assert CampaignManager._round(1.23456) == 1.235
    assert CampaignManager._blend_eta(10, 20) == 15
    assert CampaignManager._blend_eta(10, None) == 10
    assert CampaignManager._blend_eta(None, 20) == 20
    assert CampaignManager._blend_eta(None, None) is None


def test_stall_stop_failure_and_already_finished_batches_do_not_requeue(tmp_path):
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", _plan(1))
    job = next(iter(_state(tmp_path)["jobs"].values()))
    candidate_id = job["candidate_ids"][0]
    transport.add_progress(job["host"], job["name"], candidate_id, "killed")
    manager.status("campaign")
    clock.advance(STALL_FLOOR_SECONDS + 1)
    manager.tick("campaign")
    assert _state(tmp_path)["jobs"][job["name"]]["status"] == "RUNNING"

    other_dir = tmp_path / "stop-failure"
    stop_clock = FakeClock()
    stop_manager = CampaignManager(other_dir, FakeTransport(clock=stop_clock), clock=stop_clock)
    stop_manager.run("campaign", _plan(1))
    stop_job = next(iter(_state(other_dir)["jobs"].values()))
    stop_clock.advance(STALL_FLOOR_SECONDS + 1)

    def unavailable(_host, _name):
        raise HostUnavailable("host disappeared before stop")

    stop_manager.runner.stop = unavailable
    stop_manager.tick("campaign")
    stopped_state = _state(other_dir)
    assert stopped_state["jobs"][stop_job["name"]]["status"] == "RUNNING"
    assert stopped_state["hosts"][stop_job["host"]]["status"] == "UNREACHABLE"


def test_deferred_and_exited_requeues_drop_completed_work_and_fail_zero_progress_exits(tmp_path):
    plan, first_id, second_id = _two_host_one_candidate_plan()
    manager = CampaignManager(tmp_path, FakeTransport())
    manager.run("campaign", plan)
    state = _state(tmp_path)
    job_a = _first_running(state, "host-a")
    job_b = _first_running(state, "host-b")

    state["jobs"][job_a["name"]]["records"] = [{
        "candidate_id": second_id, "outcome_bucket": "survived", "elapsed_seconds": 1,
        "emitted_at": "2026-11-13T00:00:00+00:00",
    }]
    state["deferred_requeues"] = [
        {"source_job": "missing-source", "candidate_ids": [first_id]},
        {"source_job": job_b["name"], "candidate_ids": [second_id]},
    ]
    manager._retry_deferred(state)
    assert state["deferred_requeues"] == []
    assert not any(job.get("requeue_reason") == "host-lost" for job in state["jobs"].values())

    state["jobs"][job_b["name"]]["status"] = "EXIT"
    state["jobs"][job_b["name"]]["requeued"] = False
    state["jobs"][job_b["name"]]["records"] = [{
        "candidate_id": second_id, "outcome_bucket": "survived", "elapsed_seconds": 1,
        "emitted_at": None,
    }]
    state["jobs"][job_b["name"]]["finished_ids"] = [second_id]
    manager._requeue_exited(state)
    assert state["jobs"][job_b["name"]]["requeued"] is True
    assert not any(job.get("requeue_reason") == "job-exit" for job in state["jobs"].values())

    state["jobs"][job_b["name"]]["status"] = "EXIT"
    state["jobs"][job_b["name"]]["requeued"] = False
    state["jobs"][job_b["name"]]["records"] = []
    state["jobs"][job_b["name"]]["finished_ids"] = []
    state["jobs"][job_a["name"]]["records"] = []
    state["hosts"]["host-b"]["status"] = "UNREACHABLE"
    manager._requeue_exited(state)
    assert state["jobs"][job_b["name"]]["status"] == "FAILED"
    assert state["deferred_requeues"] == []


def test_progress_making_exit_defers_only_pending_work_when_host_is_unreachable(tmp_path):
    plan = _plan(2)
    candidates = [candidate for host in plan["hosts"] for candidate in host["assigned"]]
    host_a = next(host for host in plan["hosts"] if host["id"] == "host-a")
    host_b = next(host for host in plan["hosts"] if host["id"] == "host-b")
    host_a["assigned"] = []
    host_a["batches"] = []
    host_b["assigned"] = candidates
    host_b["batches"] = [{
        "batch": 0, "kind": "regular", "candidate_ids": sorted(item["id"] for item in candidates),
    }]
    transport = FakeTransport()
    manager = CampaignManager(tmp_path, transport)
    manager.run("campaign", plan)
    job = _first_running(_state(tmp_path), "host-b")
    finished_id, pending_id = job["candidate_ids"]
    transport.add_progress("host-b", job["name"], finished_id, "killed")
    manager.status("campaign")

    state = _state(tmp_path)
    state_job = state["jobs"][job["name"]]
    state_job["status"] = "EXIT"
    state_job["exit_code"] = 2
    state_job["requeued"] = False
    state["hosts"]["host-b"]["status"] = "UNREACHABLE"
    manager._requeue_exited(state)

    assert state_job["status"] == "EXIT"
    assert state_job["requeued"] is True
    assert state["deferred_requeues"] == [{
        "source_job": job["name"], "candidate_ids": [pending_id],
        "reason": "job exited while its host is unreachable",
    }]


def test_lost_host_with_no_unfinished_candidates_does_not_create_requeue(tmp_path):
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", _plan(1))
    state = _state(tmp_path)
    job = next(iter(state["jobs"].values()))
    for candidate_id in job["candidate_ids"]:
        transport.add_progress(job["host"], job["name"], candidate_id, "survived")
    manager.status("campaign")
    transport.set_unreachable(job["host"])
    clock.advance(LOST_AFTER_SECONDS + 1)
    manager.tick("campaign")
    state = _state(tmp_path)
    assert state["hosts"][job["host"]]["status"] == "LOST"
    assert not any(candidate.get("requeue_reason") == "host-lost" for candidate in state["jobs"].values())


def test_returning_running_duplicate_is_stopped_after_other_host_finishes(tmp_path):
    plan, first_id, second_id = _two_host_one_candidate_plan()
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", plan)
    state = _state(tmp_path)
    first_job = _first_running(state, "host-a")
    lost = _first_running(state, "host-b")
    transport.add_progress("host-a", first_job["name"], first_id, "killed")
    transport.finish("host-a", first_job["name"])
    manager.status("campaign")
    transport.set_unreachable("host-b")
    clock.advance(LOST_AFTER_SECONDS + 1)
    manager.tick("campaign")
    state = _state(tmp_path)
    retry = next(job for job in state["jobs"].values() if job.get("requeue_reason") == "host-lost")
    transport.add_progress("host-a", retry["name"], second_id, "survived")
    transport.finish("host-a", retry["name"])
    manager.tick("campaign")
    stops_before = transport.host("host-b").stops
    transport.set_unreachable("host-b", False)
    manager.tick("campaign")
    final_state = _state(tmp_path)
    assert final_state["hosts"]["host-b"]["status"] == "ONLINE"
    assert final_state["jobs"][lost["name"]]["status"] == "SUPERSEDED"
    assert transport.host("host-b").stops == stops_before + 1


def test_superseded_and_returned_duplicate_stop_failures_preserve_retry_state(tmp_path):
    plan, _first_id, second_id = _two_host_one_candidate_plan()
    transport = FakeTransport()
    manager = CampaignManager(tmp_path, transport)
    manager.run("campaign", plan)
    state = _state(tmp_path)
    job_b = _first_running(state, "host-b")
    state["jobs"][job_b["name"]]["status"] = "SUPERSEDED_PENDING"
    state["jobs"][job_b["name"]]["stop_pending"] = True
    (tmp_path / "campaign.json").write_text(json.dumps(state), encoding="utf-8")
    transport.set_unreachable("host-b")
    manager.tick("campaign")
    state = _state(tmp_path)
    assert state["jobs"][job_b["name"]]["status"] == "SUPERSEDED_PENDING"
    assert state["jobs"][job_b["name"]]["stop_pending"] is True

    state["hosts"]["host-b"]["status"] = "LOST"
    state["jobs"][job_b["name"]]["status"] = "RUNNING"
    state["jobs"][job_b["name"]]["candidate_ids"] = [second_id]
    state["jobs"][next(job for job in state["jobs"] if state["jobs"][job]["host"] == "host-a")]["records"] = [{
        "candidate_id": second_id, "outcome_bucket": "survived", "elapsed_seconds": 1,
        "emitted_at": None,
    }]

    def unavailable(_host, _name):
        raise HostUnavailable("returning host vanished during stop")

    manager.runner.stop = unavailable
    manager._stop_returned_duplicates(state, "host-b")
    assert state["hosts"]["host-b"]["status"] == "UNREACHABLE"
    assert state["jobs"][job_b["name"]]["status"] == "RUNNING"


def test_requeue_planner_errors_are_recorded_and_start_errors_stay_queued(tmp_path, monkeypatch):
    plan = _plan(1)
    transport = FakeTransport()
    manager = CampaignManager(tmp_path, transport)
    manager.run("campaign", plan)
    state = _state(tmp_path)
    job = next(iter(state["jobs"].values()))
    monkeypatch.setattr("fleetd.campaign.replan_pending", lambda *_args: (_ for _ in ()).throw(ContractError("no route")))
    manager._enqueue_replanned(state, job, job["candidate_ids"], job["host"], "stalled")
    assert "no route" in state["warnings"][-1]

    def raise_host(_spec):
        raise HostUnavailable("host timed out")

    state["jobs"][job["name"]]["status"] = "QUEUED"
    state["jobs"][job["name"]]["started_mono"] = None
    state["hosts"][job["host"]]["status"] = "ONLINE"
    state["hosts"][job["host"]]["unreachable_since_mono"] = None
    transport.host(job["host"]).jobs.pop(job["name"], None)
    manager.runner.start = raise_host
    manager._start_available(state)
    assert state["jobs"][job["name"]]["status"] == "QUEUED"
    assert state["hosts"][job["host"]]["status"] == "UNREACHABLE"
    assert "host timed out" in state["jobs"][job["name"]]["start_error"]

    state["hosts"][job["host"]]["status"] = "ONLINE"
    state["hosts"][job["host"]]["unreachable_since_mono"] = None

    def raise_transport(_spec):
        raise TransportError("bad runner response")

    manager.runner.start = raise_transport
    manager._start_available(state)
    assert state["jobs"][job["name"]]["status"] == "QUEUED"
    assert "bad runner response" in state["warnings"][-1]


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


def test_campaign_plan_validator_rejects_inconsistent_tables_and_assignments(tmp_path):
    manager = CampaignManager(tmp_path, FakeTransport())
    base = _plan(2)
    cases = []

    bad = copy.deepcopy(base); bad["schema"] = "other"
    cases.append((bad, "fleet-plan/1"))
    bad = copy.deepcopy(base); bad["lane"] = ""
    cases.append((bad, "lane"))
    bad = copy.deepcopy(base); bad["hosts"][0] = "bad"
    cases.append((bad, "object with an id"))
    bad = copy.deepcopy(base); bad["hosts"][0]["id"] = "../outside"
    cases.append((bad, "unsafe host id"))
    bad = copy.deepcopy(base); bad["hosts"][1]["id"] = bad["hosts"][0]["id"]
    cases.append((bad, "duplicate host id"))
    bad = copy.deepcopy(base); bad["hosts"][0]["assigned"] = "bad"
    cases.append((bad, "assigned: expected an array"))
    bad = copy.deepcopy(base); bad["hosts"][0]["assigned"] = ["bad"]
    cases.append((bad, r"assigned\[0\]"))
    bad = copy.deepcopy(base); bad["hosts"][0]["batches"] = "bad"
    cases.append((bad, "batches: expected an array"))
    bad = copy.deepcopy(base); bad["hosts"][0]["batches"][0] = "bad"
    cases.append((bad, "candidate_ids array"))
    bad = copy.deepcopy(base); bad["hosts"][0]["batches"][0]["batch"] = True
    cases.append((bad, "non-negative integer"))
    bad = copy.deepcopy(base); bad["hosts"][0]["batches"][0]["batch"] = -1
    cases.append((bad, "non-negative integer"))
    bad = copy.deepcopy(base); bad["hosts"][0]["batches"][0]["candidate_ids"][0] = "bad"
    cases.append((bad, "invalid candidate id"))
    bad = copy.deepcopy(base); batch = bad["hosts"][0]["batches"][0]
    batch["candidate_ids"].append(batch["candidate_ids"][0])
    cases.append((bad, "assigned more than once"))
    bad = copy.deepcopy(base); bad["hosts"][0]["batches"][0]["candidate_ids"] = ["f" * 64]
    cases.append((bad, "absent from assigned"))
    bad = copy.deepcopy(base); bad["hosts"][0]["batches"] = []
    cases.append((bad, "missing from batches"))
    bad = copy.deepcopy(base); bad["hosts"][0]["worker_slots"] = True
    cases.append((bad, "worker_slots"))
    bad = copy.deepcopy(base); bad["hosts"][0]["worker_slots"] = 0
    cases.append((bad, "worker_slots"))
    bad = copy.deepcopy(base); bad["unplaced"] = "bad"
    cases.append((bad, "expected arrays"))
    bad = copy.deepcopy(base); bad["terminal"] = None
    cases.append((bad, "expected arrays"))
    bad = copy.deepcopy(base); bad["unplaced"] = ["bad"]
    cases.append((bad, "invalid candidate id"))
    bad = copy.deepcopy(base); bad["unplaced"] = [{"id": "f" * 64, "reason": "no fit"}]
    bad["hosts"][0]["assigned"].append(copy.deepcopy(bad["hosts"][1]["assigned"][0]))
    bad["hosts"][0]["batches"][0]["candidate_ids"].append(bad["hosts"][1]["assigned"][0]["id"])
    cases.append((bad, "assigned more than once"))
    bad = copy.deepcopy(base); bad["unplaced"] = [{"id": bad["hosts"][0]["assigned"][0]["id"], "reason": "duplicate"}]
    cases.append((bad, "both assigned and unplaced"))

    for bad_plan, message in cases:
        with pytest.raises(ContractError, match=message):
            manager.run("campaign", bad_plan)


def test_campaign_state_keeps_unplaced_terminal_and_empty_batches_as_missing(tmp_path):
    plan = _plan(1)
    available = next(host for host in plan["hosts"] if host["assigned"])
    available["batches"].append({"batch": len(available["batches"]), "kind": "regular", "candidate_ids": []})
    unplaced_id = "f" * 64
    terminal_id = "e" * 64
    plan["unplaced"] = [{"id": unplaced_id, "reason": "no available capacity"}]
    plan["terminal"] = [{"id": terminal_id, "reason": "memory crash retry exhausted"}]
    manager = CampaignManager(tmp_path, FakeTransport())
    manager.run("campaign", plan)
    state = _state(tmp_path)
    result, _, _, _ = manager.collect("campaign")
    rows = {row["candidate_id"]: row for row in result["candidates"]}
    assert rows[unplaced_id]["outcome"] == "missing"
    assert rows[unplaced_id]["reason"] == "no available capacity"
    assert rows[terminal_id]["outcome"] == "missing"
    assert rows[terminal_id]["reason"] == "memory crash retry exhausted"
    assert result["totals"]["missing"] == 3
    assert all(job["candidate_ids"] for job in state["jobs"].values())


def test_campaign_binds_plan_identity_and_reconciles_added_removed_hosts(tmp_path):
    plan = _plan(12)
    transport = FakeTransport()
    manager = CampaignManager(tmp_path, transport)
    manager.run("campaign", plan)
    changed_identity = copy.deepcopy(plan)
    changed_identity["execution"]["commit"] = "c" * 40
    with pytest.raises(ContractError, match="bound to its original"):
        manager.run("campaign", changed_identity)

    changed = copy.deepcopy(plan)
    removed_id = changed["hosts"][1]["id"]
    changed["hosts"] = changed["hosts"][:1]
    extra = copy.deepcopy(changed["hosts"][0])
    extra["id"] = "host-c"
    extra["assigned"] = []
    extra["batches"] = []
    changed["hosts"].append(extra)
    state = _state(tmp_path)
    manager._reconcile(state, changed)
    assert state["hosts"][removed_id]["status"] == "REMOVED_FROM_PLAN"
    assert state["hosts"]["host-c"]["status"] in {"ONLINE", "UNKNOWN"}
    manager._write_state(state)
    manager.run("campaign", changed)


def test_refresh_normalizes_probe_records_and_rejects_bad_probe_tables(tmp_path):
    plan = _plan(1)
    manager = CampaignManager(tmp_path, FakeTransport())
    manager.run("campaign", plan)
    job = next(iter(_state(tmp_path)["jobs"].values()))
    candidate_id = job["candidate_ids"][0]
    valid = {
        "candidate_id": candidate_id,
        "outcome_bucket": "killed",
        "elapsed_seconds": True,
        "emitted_at": 7,
    }
    row = {
        "name": job["name"], "state": "UNKNOWN", "list_digest": job["list_digest"],
        "progress_path": None, "verdict_path": None,
        "records": [None, {"candidate_id": 7}, {"candidate_id": "f" * 64},
                    {"candidate_id": candidate_id, "outcome_bucket": "not-an-outcome"}, valid],
    }
    manager.runner.probe = lambda _host, _wanted: {job["name"]: row}
    state = _state(tmp_path)
    state["jobs"][job["name"]]["started_mono"] = None
    state["jobs"][job["name"]]["last_progress_mono"] = None
    (tmp_path / "campaign.json").write_text(json.dumps(state), encoding="utf-8")
    manager.status("campaign")
    refreshed = _state(tmp_path)["jobs"][job["name"]]
    assert refreshed["status"] == "UNKNOWN"
    assert refreshed["started_mono"] is not None
    assert refreshed["last_progress_mono"] is not None
    assert refreshed["records"] == [{
        "candidate_id": candidate_id, "outcome_bucket": "killed",
        "elapsed_seconds": None, "emitted_at": None,
    }]
    assert refreshed["progress_path"] is None and refreshed["verdict_path"] == job["verdict_path"]
    manager.status("campaign")
    assert len(_state(tmp_path)["jobs"][job["name"]]["records"]) == 1

    row["records"] = "not-an-array"
    status = manager.status("campaign")
    host_status = next(item for item in status["hosts"] if item["host"] == job["host"])
    assert host_status["state"] == "ERROR"
    assert "invalid records field" in host_status["error"]


@pytest.mark.parametrize(("field", "value", "message"), [
    ("verdict_records", "not-an-array", "invalid verdict records field"),
    ("progress_offset", True, "invalid progress_offset"),
    ("baseline_seconds", True, "invalid baseline time"),
])
def test_refresh_rejects_invalid_verdict_and_progress_metadata(tmp_path, field, value, message):
    manager = CampaignManager(tmp_path, FakeTransport())
    manager.run("campaign", _plan(1))
    job = next(iter(_state(tmp_path)["jobs"].values()))
    row = {
        "name": job["name"], "state": "EXIT", "exit_code": 2,
        "list_digest": job["list_digest"], "records": [], "verdict_records": [],
    }
    row[field] = value
    manager.runner.probe = lambda _host, wanted: {name: row for name in wanted}

    status = manager.status("campaign")

    host_status = next(item for item in status["hosts"] if item["host"] == job["host"])
    assert host_status["state"] == "ERROR"
    assert message in host_status["error"]


def test_refresh_handles_unreachable_before_contact_and_superseded_missing_jobs(tmp_path):
    plan = _plan(1)
    host_id = next(host["id"] for host in plan["hosts"] if host["batches"])
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    transport.set_unreachable(host_id)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    status = manager.run("campaign", plan)
    state = _state(tmp_path)
    assert state["hosts"][host_id]["unreachable_since_mono"] == clock()
    assert next(host for host in status["hosts"] if host["host"] == host_id)["state"] == "UNREACHABLE"

    transport.set_unreachable(host_id, False)
    manager.tick("campaign")
    state = _state(tmp_path)
    job = next(job for job in state["jobs"].values() if job["host"] == host_id)
    state["jobs"][job["name"]]["status"] = "SUPERSEDED_PENDING"
    state["jobs"][job["name"]]["stop_pending"] = False
    (tmp_path / "campaign.json").write_text(json.dumps(state), encoding="utf-8")
    manager.tick("campaign")
    assert _state(tmp_path)["jobs"][job["name"]]["status"] == "SUPERSEDED"

    state = _state(tmp_path)
    state["jobs"][job["name"]]["status"] = "SUPERSEDED_PENDING"
    state["jobs"][job["name"]]["stop_pending"] = True
    (tmp_path / "campaign.json").write_text(json.dumps(state), encoding="utf-8")
    del transport.host(host_id).jobs[job["name"]]
    manager.tick("campaign")
    assert _state(tmp_path)["jobs"][job["name"]]["status"] == "SUPERSEDED"


def test_refresh_digest_mismatch_and_missing_running_jobs_are_detected(tmp_path):
    plan = _plan(1)
    transport = FakeTransport()
    manager = CampaignManager(tmp_path, transport)
    manager.run("campaign", plan)
    job = next(iter(_state(tmp_path)["jobs"].values()))
    host = transport.host(job["host"])
    host.files[f"jobs/{job['name']}/list.sha256"] = b"wrong\n"
    status = manager.status("campaign")
    host_status = next(item for item in status["hosts"] if item["host"] == job["host"])
    assert host_status["state"] == "ERROR"
    assert "different candidate-list digest" in host_status["error"]

    host.files[f"jobs/{job['name']}/list.sha256"] = f"{job['list_digest']}\n".encode()
    del host.jobs[job["name"]]
    manager.tick("campaign")
    state = _state(tmp_path)
    assert state["jobs"][job["name"]]["status"] == "FAILED"
    assert state["jobs"][job["name"]]["missing_job"] is True
    assert not any(item.get("requeue_reason") == "job-exit" for item in state["jobs"].values())


def test_missing_job_with_finished_progress_keeps_its_partial_result(tmp_path):
    transport = FakeTransport()
    manager = CampaignManager(tmp_path, transport)
    manager.run("campaign", _plan(1))
    job = next(iter(_state(tmp_path)["jobs"].values()))
    candidate_id = job["candidate_ids"][0]
    transport.add_progress(job["host"], job["name"], candidate_id, "killed")
    manager.status("campaign")

    del transport.host(job["host"]).jobs[job["name"]]
    manager.status("campaign")

    missing = _state(tmp_path)["jobs"][job["name"]]
    assert missing["missing_job"] is True
    assert missing["status"] == "EXIT"
    assert missing.get("failure_reason") is None


def test_campaign_accepts_blocked_plan_without_worker_facts_and_keeps_completed_jobs(tmp_path):
    blocked = _plan(1)
    moved = [candidate for host in blocked["hosts"] for candidate in host["assigned"]]
    blocked["unplaced"] = [*blocked.get("unplaced", []), *(
        {"id": candidate["id"], "reason": "all planned hosts blocked"} for candidate in moved
    )]
    for host in blocked["hosts"]:
        host["status"] = "blocked"
        host["assigned"] = []
        host["batches"] = []
        host.pop("worker_slots", None)
    manager = CampaignManager(tmp_path / "blocked", FakeTransport())
    status = manager.run("blocked", blocked)
    assert all(host["batches"] == [] for host in status["hosts"])
    result, _, _, _ = manager.collect("blocked")
    assert result["totals"]["missing"] == 1

    complete_plan = _plan(2)
    complete_manager = CampaignManager(tmp_path / "complete", FakeTransport())
    complete_manager.run("complete", complete_plan)
    state = _state(tmp_path / "complete", "complete")
    old_names = set(state["jobs"])
    for job in state["jobs"].values():
        job["status"] = "COMPLETE"
    expanded = _plan(3)
    complete_manager._reconcile(state, expanded)
    assert all(state["jobs"][name]["status"] == "COMPLETE" for name in old_names)


def test_refresh_unknown_initializes_progress_and_rejects_corrupt_candidate_ids(tmp_path):
    plan = _plan(1)
    manager = CampaignManager(tmp_path, FakeTransport())
    manager.run("campaign", plan)
    state = _state(tmp_path)
    job = next(iter(state["jobs"].values()))
    state["jobs"][job["name"]]["started_mono"] = None
    state["jobs"][job["name"]]["last_progress_mono"] = None
    (tmp_path / "campaign.json").write_text(json.dumps(state), encoding="utf-8")
    manager.runner.probe = lambda _host, _wanted: {
        job["name"]: {
            "name": job["name"], "state": "UNKNOWN", "list_digest": job["list_digest"],
            "progress_path": job["progress_path"], "verdict_path": job["verdict_path"], "records": [],
        }
    }
    manager.status("campaign")
    refreshed = _state(tmp_path)["jobs"][job["name"]]
    assert refreshed["status"] == "UNKNOWN"
    assert refreshed["started_mono"] == refreshed["last_progress_mono"]

    manager.runner.probe = lambda _host, _wanted: {
        job["name"]: {
            "name": job["name"], "state": "PAUSED", "list_digest": job["list_digest"], "records": [],
        }
    }
    unsupported = manager.status("campaign")
    unsupported_host = next(item for item in unsupported["hosts"] if item["host"] == job["host"])
    assert unsupported_host["state"] == "ERROR"
    assert "unsupported job state" in unsupported_host["error"]

    corrupted = _state(tmp_path)
    corrupted["jobs"][job["name"]]["candidate_ids"] = ["invalid"]
    (tmp_path / "campaign.json").write_text(json.dumps(corrupted), encoding="utf-8")
    manager.runner.probe = lambda _host, _wanted: {
        job["name"]: {
            "name": job["name"], "state": "RUNNING", "list_digest": job["list_digest"],
            "records": [{"candidate_id": "invalid", "outcome_bucket": "killed"}],
        }
    }
    invalid = manager.status("campaign")
    invalid_host = next(item for item in invalid["hosts"] if item["host"] == job["host"])
    assert invalid_host["state"] == "ERROR"
    assert "candidate list" in invalid_host["error"]


def test_refresh_rejects_a_candidate_id_that_fails_the_runtime_validator(tmp_path, monkeypatch):
    manager = CampaignManager(tmp_path, FakeTransport())
    manager.run("campaign", _plan(1))
    job = next(iter(_state(tmp_path)["jobs"].values()))
    candidate_id = job["candidate_ids"][0]
    manager.runner.probe = lambda _host, _wanted: {
        job["name"]: {
            "name": job["name"],
            "state": "RUNNING",
            "list_digest": job["list_digest"],
            "records": [{"candidate_id": candidate_id, "outcome_bucket": "killed"}],
        }
    }
    monkeypatch.setattr("fleetd.campaign.is_candidate_id", lambda _value: False)
    status = manager.status("campaign")
    host_status = next(item for item in status["hosts"] if item["host"] == job["host"])
    assert host_status["state"] == "ERROR"
    assert "invalid candidate id" in host_status["error"]


def test_campaign_superseded_stop_loss_and_deferred_retry_edges(tmp_path):
    plan, _first_id, _second_id = _two_host_one_candidate_plan()
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path / "stop", transport, clock=clock)
    manager.run("campaign", plan)
    state = _state(tmp_path / "stop")
    pending = _first_running(state, "host-a")
    state["jobs"][pending["name"]]["status"] = "SUPERSEDED_PENDING"
    state["jobs"][pending["name"]]["stop_pending"] = True
    manager.runner.stop = lambda *_args: (_ for _ in ()).throw(HostUnavailable("stop timed out"))
    manager._stop_superseded_host(state, "host-a")
    assert state["hosts"]["host-a"]["status"] == "UNREACHABLE"
    assert state["hosts"]["host-a"]["unreachable_since_mono"] == state["hosts"]["host-a"]["last_contact_mono"]

    state["jobs"][pending["name"]]["status"] = "SUPERSEDED_PENDING"
    state["jobs"][pending["name"]]["stop_pending"] = True
    manager.runner.stop = RunnerJobs(transport).stop
    def probe_superseded(_host, wanted):
        return {
            name: {
                "name": name,
                "state": "EXIT" if name == pending["name"] else "RUNNING",
                "exit_code": 143 if name == pending["name"] else None,
                "list_digest": state["jobs"][name]["list_digest"],
                "records": [],
            }
            for name in wanted
        }

    manager.runner.probe = probe_superseded
    manager._refresh(state)
    assert state["jobs"][pending["name"]]["status"] == "SUPERSEDED"
    assert state["jobs"][pending["name"]]["stop_pending"] is False

    lost = _first_running(state, "host-b")
    state["hosts"]["host-b"]["status"] = "LOST"
    state["hosts"]["host-b"]["unreachable_since_mono"] = None
    state["hosts"]["host-b"]["loss_requeued"] = False
    manager.runner.stop = RunnerJobs(transport).stop
    manager._handle_lost_hosts(state)
    assert state["hosts"]["host-b"]["status"] == "ONLINE"
    assert state["jobs"][lost["name"]]["status"] == "LOST"

    deferred_plan = _plan(2)
    deferred_transport = FakeTransport(clock=clock)
    deferred_manager = CampaignManager(tmp_path / "deferred", deferred_transport, clock=clock)
    deferred_manager.run("deferred", deferred_plan)
    deferred_state = _state(tmp_path / "deferred", "deferred")
    source = _first_running(deferred_state, "host-b")
    deferred_state["hosts"]["host-a"]["status"] = "UNREACHABLE"
    deferred_state["hosts"]["host-b"]["status"] = "LOST"
    deferred_state["hosts"]["host-b"]["unreachable_since_mono"] = clock() - LOST_AFTER_SECONDS - 1
    deferred_state["hosts"]["host-b"]["loss_requeued"] = False
    deferred_manager._handle_lost_hosts(deferred_state)
    assert deferred_state["deferred_requeues"]
    deferred_state["hosts"]["host-a"]["status"] = "ONLINE"
    deferred_manager._retry_deferred(deferred_state)
    assert not deferred_state["deferred_requeues"]
    assert any(job.get("requeue_reason") == "host-lost" for job in deferred_state["jobs"].values())


def test_deferred_requeue_is_dropped_when_candidate_finishes_before_target_returns(tmp_path):
    plan = _plan(2)
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", plan)
    state = _state(tmp_path)
    source = _first_running(state, "host-b")
    candidate_id = source["candidate_ids"][0]
    state["hosts"]["host-a"]["status"] = "UNREACHABLE"
    state["hosts"]["host-b"]["status"] = "LOST"
    state["hosts"]["host-b"]["unreachable_since_mono"] = clock() - LOST_AFTER_SECONDS - 1
    state["hosts"]["host-b"]["loss_requeued"] = False
    manager._handle_lost_hosts(state)
    assert state["deferred_requeues"]
    source["records"].append({
        "candidate_id": candidate_id, "outcome_bucket": "survived", "elapsed_seconds": 1,
        "emitted_at": "2030-01-01T00:00:00+00:00",
    })
    state["hosts"]["host-a"]["status"] = "ONLINE"
    manager._retry_deferred(state)
    assert state["deferred_requeues"] == []
    assert not any(job.get("requeue_reason") == "host-lost" for job in state["jobs"].values())


def test_deferred_retry_skips_work_already_running_on_a_third_host(tmp_path):
    candidate_id = "a" * 64
    manager = CampaignManager(tmp_path, FakeTransport())
    requeued = []
    manager._enqueue_replanned = lambda _state, source, ids, target, reason: requeued.append(
        (source["name"], ids, target, reason)
    )
    state = {
        "jobs": {
            "lost-job": {"name": "lost-job", "host": "host-a", "candidate_ids": [candidate_id]},
            "third-host-job": {
                "name": "third-host-job", "host": "host-c", "candidate_ids": [candidate_id], "status": "RUNNING",
            },
        },
        "deferred_requeues": [{
            "source_job": "lost-job", "candidate_ids": [candidate_id], "reason": "lost host has no target",
        }],
        "hosts": {
            "host-a": {"status": "LOST", "capacity_alone_bytes": 0},
            "host-b": {"status": "ONLINE", "capacity_alone_bytes": 2},
            "host-c": {"status": "ONLINE", "capacity_alone_bytes": 3},
        },
        "plan": {"hosts": [
            {"id": "host-b", "status": "available"},
            {"id": "host-c", "status": "available"},
        ]},
    }

    manager._retry_deferred(state)

    assert state["deferred_requeues"] == []
    assert requeued == []


def test_collection_uses_remote_progress_deduplicates_events_and_skips_owned_verdict(tmp_path):
    plan = _plan(30)
    transport = FakeTransport()
    manager = CampaignManager(tmp_path, transport)
    manager.run("campaign", plan)
    state = _state(tmp_path)
    job = _first_running(state, "host-a")
    first_id, second_id = job["candidate_ids"][:2]
    transport.add_progress("host-a", job["name"], first_id, "killed", emitted_at="2030-01-01T00:00:01+00:00")
    manager.status("campaign")
    state = _state(tmp_path)
    first_record = state["jobs"][job["name"]]["records"][0]
    state["jobs"][job["name"]]["records"].append(dict(first_record))
    remote_event = {
        "event": "candidate", "candidate_id": second_id, "outcome_bucket": "survived",
        "elapsed_seconds": 2.5, "emitted_at": "2030-01-01T00:00:02+00:00",
    }
    host = transport.host("host-a")
    progress_path = state["jobs"][job["name"]]["progress_path"]
    host.files[progress_path] += (json.dumps(remote_event, sort_keys=True, separators=(",", ":")) + "\n").encode()
    verdict = json.dumps({
        "claims": [{"rigor": "R2", "mutation": {"survived": [{"candidate_id": second_id}]}}]
    }).encode()
    transport.finish("host-a", job["name"], verdict=verdict)
    result = manager._collect_result(state)
    rows = {row["candidate_id"]: row for row in result["candidates"]}
    assert rows[first_id]["outcome"] == "killed"
    assert rows[second_id]["outcome"] == "survived"
    assert rows[second_id]["owner_host"] == "host-a"

    state = _state(tmp_path)
    state["jobs"][job["name"]]["records"].extend([
        None,
        {"candidate_id": "f" * 64, "outcome_bucket": "killed"},
        {"candidate_id": first_id, "outcome_bucket": "unknown"},
    ])
    state["jobs"][job["name"]]["progress_path"] = None
    result = manager._collect_result(state)
    assert any(item["artifact"] == "progress" and item["path"] == "" for item in result["missing_artifacts"])
    assert "None." in manager._render_report({
        "campaign": "empty", "lane": "r2", "plan_sha256": "0" * 64,
        "totals": {name: 0 for name in ("killed", "survived", "timeout", "crashed", "equivalent", "missing", "duplicates_ignored")},
        "candidates": [], "per_host": [], "missing_artifacts": [],
    })


def test_collection_output_is_independent_of_job_and_record_insertion_order(tmp_path):
    plan = _plan(12)
    transport = FakeTransport()
    clock = FakeClock()
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", plan)
    state = _state(tmp_path)
    for host_id in ("host-a", "host-b"):
        job = _first_running(state, host_id)
        candidate_id = job["candidate_ids"][0]
        transport.add_progress(host_id, job["name"], candidate_id, "survived")
    manager.status("campaign")
    state = _state(tmp_path)
    expected = manager._collect_result(state)
    reversed_jobs = dict(reversed(list(state["jobs"].items())))
    for job in reversed_jobs.values():
        job["records"] = list(reversed(job["records"]))
    state["jobs"] = reversed_jobs
    actual = manager._collect_result(state)
    assert actual == expected
    assert [row["candidate_id"] for row in actual["candidates"]] == sorted(state["planned_ids"])


def test_campaign_state_loading_and_atomic_write_reject_corruption(tmp_path):
    manager = CampaignManager(tmp_path, FakeTransport())
    with pytest.raises(ContractError, match="does not exist"):
        manager.status("missing")
    (tmp_path / "bad.json").write_text("not json", encoding="utf-8")
    with pytest.raises(ContractError, match="not valid UTF-8 JSON"):
        manager.status("bad")
    (tmp_path / "bad.json").write_bytes(b"\xff")
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
    malformed.write_text(json.dumps({"schema": "fleet-campaign-state/2", "campaign": "other", "hosts": {}, "jobs": {}}), encoding="utf-8")
    with pytest.raises(ContractError, match="identity or tables"):
        manager.status("wrong-identity")
    malformed.write_text(json.dumps({"schema": "fleet-campaign-state/2", "campaign": "wrong-identity", "hosts": [], "jobs": {}}), encoding="utf-8")
    with pytest.raises(ContractError, match="identity or tables"):
        manager.status("wrong-identity")


def test_lost_host_without_alternate_host_defers_and_resumes_if_it_returns(tmp_path):
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
    assert _state(tmp_path)["jobs"][job["name"]]["status"] == "RUNNING"
    assert _state(tmp_path)["deferred_requeues"]


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


def test_stall_and_loss_constants_are_the_operator_decisions():
    # The scenario tests advance by these module constants, so pin the literals here.
    assert STALL_FLOOR_SECONDS == 15 * 60
    assert LOST_AFTER_SECONDS == 30 * 60


def test_stall_floor_holds_when_observed_candidates_are_fast(tmp_path):
    plan = _plan(12)
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", plan)
    job = _first_running(_state(tmp_path), "host-a")
    transport.add_progress("host-a", job["name"], job["candidate_ids"][0], "killed", elapsed_seconds=1)
    manager.tick("campaign")
    clock.advance(899)
    manager.tick("campaign")
    assert _state(tmp_path)["jobs"][job["name"]]["status"] == "RUNNING"
    clock.advance(1)
    manager.tick("campaign")
    stalled = _state(tmp_path)["jobs"][job["name"]]
    assert stalled["status"] == "STALLED"
    assert stalled["stall_after_seconds"] == 900


def test_a_finished_candidate_restarts_the_stall_clock(tmp_path):
    plan = _plan(12)
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", plan)
    job = _first_running(_state(tmp_path), "host-a")
    clock.advance(600)
    transport.add_progress("host-a", job["name"], job["candidate_ids"][0], "killed", elapsed_seconds=1)
    manager.tick("campaign")
    clock.advance(600)  # 1200 s after the start, but only 600 s after the last finished candidate
    manager.tick("campaign")
    assert _state(tmp_path)["jobs"][job["name"]]["status"] == "RUNNING"
    clock.advance(300)
    manager.tick("campaign")
    assert _state(tmp_path)["jobs"][job["name"]]["status"] == "STALLED"


def test_stall_threshold_is_three_times_the_p95_not_the_median_or_the_maximum(tmp_path):
    plan = _plan(230)
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", plan)
    elapsed = [10.0] * 22 + [400.0, 1000.0]  # 24 observations: nearest-rank p95 is the 23rd = 400
    seen = 0
    for _ in range(3):
        job = _first_running(_state(tmp_path), "host-a")
        for candidate_id in job["candidate_ids"]:
            transport.add_progress("host-a", job["name"], candidate_id, "killed", elapsed_seconds=elapsed[seen])
            seen += 1
        transport.finish("host-a", job["name"])
        manager.tick("campaign")
    assert seen == 24
    running = _first_running(_state(tmp_path), "host-a")
    clock.advance(1199)
    manager.tick("campaign")
    assert _state(tmp_path)["jobs"][running["name"]]["status"] == "RUNNING"
    clock.advance(1)
    manager.tick("campaign")
    assert _state(tmp_path)["jobs"][running["name"]]["stall_after_seconds"] == 1200


@pytest.mark.parametrize("larger", ["host-a", "host-c"])
def test_lost_host_work_goes_to_the_largest_alone_capacity_of_three_hosts(tmp_path, larger):
    plan = _plan(40)
    host_a = next(host for host in plan["hosts"] if host["id"] == "host-a")
    host_c = copy.deepcopy(host_a)
    host_c["id"] = "host-c"
    host_c["assigned"] = host_a["assigned"][:8]
    host_c["batches"] = [dict(host_a["batches"][0], batch=0)]
    host_a["assigned"] = host_a["assigned"][8:]
    host_a["batches"] = [dict(host_a["batches"][1], batch=0)]
    plan["hosts"].append(host_c)
    host_a["cap_alone_bytes"] = 9 * 1024**3 if larger == "host-a" else 8 * 1024**3
    host_c["cap_alone_bytes"] = 9 * 1024**3 if larger == "host-c" else 8 * 1024**3
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", plan)
    transport.set_unreachable("host-b")
    clock.advance(LOST_AFTER_SECONDS + 1)
    manager.tick("campaign")
    requeued = [job for job in _state(tmp_path)["jobs"].values() if job.get("requeue_reason") == "host-lost"]
    assert requeued and {job["host"] for job in requeued} == {larger}


def test_state_write_failure_keeps_the_previous_state_and_leaves_no_temporary_file(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    _atomic_write(path, b"old")

    def refuse(*_args, **_kwargs):
        raise OSError("rename refused")

    monkeypatch.setattr("fleetd.campaign.os.replace", refuse)
    with pytest.raises(OSError, match="rename refused"):
        _atomic_write(path, b"new")
    assert path.read_bytes() == b"old"
    assert [item.name for item in tmp_path.iterdir()] == ["state.json"]


def test_real_run_job_starts_non_root_project_and_recovers_digest_from_ids_file(tmp_path):
    spec = _spec_for_project("ciu")
    home, environment = _real_runner_home(tmp_path, spec)
    progress_file = home / "checkouts" / spec.commit / "ciu" / ".assay" / "progress-contract-assay.jsonl"
    progress_file.parent.mkdir(parents=True, exist_ok=True)
    stale_progress = (
        json.dumps({
            "candidate_id": spec.sorted_candidate_ids[0],
            "elapsed_seconds": 99.0,
            "emitted_at": "2029-01-01T00:00:00Z",
            "event": "candidate",
            "outcome_bucket": "survived",
        }, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    progress_file.write_bytes(stale_progress)

    started = _real_start(home, environment, spec)
    assert started.returncode == 0, started.stderr
    assert started.stdout.strip() == "STARTED"
    job_dir = _wait_real_job(home, spec)
    assert (job_dir / "EXIT").read_text(encoding="ascii").strip() == "0"

    (job_dir / "list.sha256").unlink()
    adopted = _real_start(home, environment, spec)
    assert adopted.returncode == 0, adopted.stderr
    assert adopted.stdout.strip() == "EXISTING"

    probe = subprocess.run(
        ["bash", "-c", probe_script({spec.name: spec})],
        cwd=home,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert probe.returncode == 0, probe.stderr
    row = json.loads(probe.stdout)["jobs"][0]
    assert row["list_digest"] == spec.list_digest
    assert row["progress_offset"] == len(stale_progress), row
    assert row["progress_end_offset"] == progress_file.stat().st_size, row
    assert row["progress_count"] == 1
    assert len(row["records"]) == 1
    assert row["records"][0]["outcome_bucket"] == "killed"

    stopped = subprocess.run(
        ["bash", "-c", stop_script(spec.name)],
        cwd=home,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert stopped.returncode == 0, stopped.stderr
    assert stopped.stdout.strip() == "NOT_RUNNING"


def test_real_plain_run_probe_reads_progress_without_campaign_pointer(tmp_path):
    spec = _spec_for_project(".")
    home, environment = _real_runner_home(tmp_path, spec)
    started = _real_start(home, environment, spec)
    assert started.returncode == 0, started.stderr
    job_dir = _wait_real_job(home, spec)
    campaign_dir = home / "campaigns" / spec.runner_campaign
    assert not (campaign_dir / "progress-file").exists()
    assert (home / "checkouts" / spec.commit / ".assay" / "progress-contract-assay.jsonl").is_file()
    assert (job_dir / "EXIT").read_text(encoding="ascii").strip() == "0"

    probe = subprocess.run(
        ["bash", "-c", probe_script({spec.name: spec})],
        cwd=home,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert probe.returncode == 0, probe.stderr
    row = json.loads(probe.stdout)["jobs"][0]
    assert row["progress_path"] == f"checkouts/{spec.commit}/.assay/progress-contract-assay.jsonl"
    progress_file = home / "checkouts" / spec.commit / ".assay" / "progress-contract-assay.jsonl"
    assert row["progress_offset"] == 0, row
    assert row["progress_end_offset"] == progress_file.stat().st_size, row
    assert row["progress_count"] == 1
    assert [record["candidate_id"] for record in row["records"]] == list(spec.sorted_candidate_ids)


def test_probe_command_size_does_not_scale_with_candidate_count():
    candidates = tuple(f"{index:064x}" for index in range(8447))
    spec = replace(_spec_for_project("ciu"), candidate_ids=candidates)
    command = probe_script({spec.name: spec})
    assert len(command) < 8192
    assert candidates[-1] not in command


def test_stop_script_accepts_any_generated_job_name_length():
    name = "a" + "b" * 150
    assert f"{name}-s1" in stop_script(name)


def test_runner_project_path_matches_campaign_supervisor_contract():
    spec = replace(_spec_for_project("."), project="ciu/name..segment")
    with pytest.raises(ContractError, match="relative project path"):
        validate_job_spec(spec)


def test_idle_planned_host_is_probed_and_can_receive_lost_work(tmp_path):
    plan = _plan(1)
    source = next(host for host in plan["hosts"] if host["assigned"])
    idle = next(host for host in plan["hosts"] if not host["assigned"])
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", plan)

    transport.set_unreachable(source["id"])
    manager.tick("campaign")
    clock.advance(LOST_AFTER_SECONDS + 1)
    manager.tick("campaign")

    state = _state(tmp_path)
    assert state["hosts"][idle["id"]]["status"] == "ONLINE"
    assert not state["deferred_requeues"]
    assert any(
        job["host"] == idle["id"] and job.get("requeue_reason") == "host-lost"
        for job in state["jobs"].values()
    )


def test_stall_keeps_marker_and_creates_exactly_one_replacement(tmp_path):
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", _plan(40))
    original = _first_running(_state(tmp_path), "host-a")
    transport.add_progress("host-a", original["name"], original["candidate_ids"][0], "killed")
    manager.status("campaign")
    transport.set_unreachable("host-b")
    manager.tick("campaign")
    clock.advance(STALL_FLOOR_SECONDS + 1)
    manager.tick("campaign")

    after_detection = _state(tmp_path)
    assert after_detection["jobs"][original["name"]]["status"] == "STALLED"
    assert len([job for job in after_detection["jobs"].values() if job.get("requeue_reason") == "stalled"]) == 1

    manager.tick("campaign")
    after_probe_exit = _state(tmp_path)
    assert after_probe_exit["jobs"][original["name"]]["status"] == "STALLED"
    assert len([job for job in after_probe_exit["jobs"].values() if job.get("requeue_reason") == "stalled"]) == 1


def test_unreachable_host_becomes_lost_after_controller_restart(tmp_path):
    plan = _plan(12)
    monotonic_before = FakeClock(1_000_000)
    wall_before = FakeClock(1_000_000)
    boot = ["boot-before"]
    transport = FakeTransport(clock=monotonic_before)
    first = CampaignManager(
        tmp_path,
        transport,
        clock=monotonic_before,
        utc_clock=wall_before,
        boot_id=lambda: boot[0],
    )
    first.run("campaign", plan)
    transport.set_unreachable("host-a")
    first.tick("campaign")

    wall_before.advance(100)
    monotonic_before.advance(100)
    first.tick("campaign")
    saved = _state(tmp_path)
    assert saved["hosts"]["host-a"]["unreachable_since_utc"] is not None

    monotonic_after = FakeClock(50)
    wall_after = FakeClock(wall_before() + 2 * 60 * 60)
    boot[0] = "boot-after"
    restarted = CampaignManager(
        tmp_path,
        transport,
        clock=monotonic_after,
        utc_clock=wall_after,
        boot_id=lambda: boot[0],
    )
    restarted.tick("campaign")
    assert _state(tmp_path)["hosts"]["host-a"]["status"] == "LOST"


def test_backward_utc_step_after_controller_restart_delays_lost_detection(tmp_path):
    plan = _plan(1)
    monotonic_before = FakeClock(1_000_000)
    wall_before = FakeClock(1_000_000)
    boot = ["boot-before"]
    transport = FakeTransport(clock=monotonic_before)
    first = CampaignManager(
        tmp_path,
        transport,
        clock=monotonic_before,
        utc_clock=wall_before,
        boot_id=lambda: boot[0],
    )
    first.run("campaign", plan)
    transport.set_unreachable("host-a")
    first.tick("campaign")
    monotonic_before.advance(100)
    wall_before.advance(100)
    first.tick("campaign")

    monotonic_after = FakeClock(25)
    wall_after = FakeClock(wall_before() - LOST_AFTER_SECONDS * 4)
    boot[0] = "boot-after"
    restarted = CampaignManager(
        tmp_path,
        transport,
        clock=monotonic_after,
        utc_clock=wall_after,
        boot_id=lambda: boot[0],
    )
    restarted.tick("campaign")
    assert _state(tmp_path)["hosts"]["host-a"]["status"] == "UNREACHABLE"
    monotonic_after.advance(LOST_AFTER_SECONDS - 1)
    wall_after.advance(LOST_AFTER_SECONDS - 1)
    restarted.tick("campaign")
    assert _state(tmp_path)["hosts"]["host-a"]["status"] == "UNREACHABLE"
    monotonic_after.advance(2)
    wall_after.advance(2)
    restarted.tick("campaign")
    assert _state(tmp_path)["hosts"]["host-a"]["status"] == "LOST"


def test_large_golden_plan_requeues_all_work_after_one_stall_and_permanent_loss(tmp_path):
    plan = _plan(230)
    assert len(plan["hosts"]) == 2
    assert sum(len(host["assigned"]) for host in plan["hosts"]) == 230
    assert all(host["assigned"] for host in plan["hosts"])
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("large", plan)
    state = _state(tmp_path, "large")

    lost_host_job = _first_running(state, "host-b")
    stalled_job = _first_running(state, "host-a")
    already_finished = lost_host_job["candidate_ids"][0]
    transport.add_progress("host-b", lost_host_job["name"], already_finished, "survived")
    transport.add_progress("host-a", stalled_job["name"], stalled_job["candidate_ids"][0], "killed")
    manager.status("large")
    transport.set_unreachable("host-b")
    manager.tick("large")

    clock.advance(STALL_FLOOR_SECONDS + 1)
    manager.tick("large")
    assert _state(tmp_path, "large")["jobs"][stalled_job["name"]]["status"] == "STALLED"
    manager.tick("large")
    state = _state(tmp_path, "large")
    assert state["jobs"][stalled_job["name"]]["status"] == "STALLED"
    assert len([job for job in state["jobs"].values() if job.get("requeue_reason") == "stalled"]) == 1

    def finish_host_a_work() -> None:
        for _ in range(300):
            manager.tick("large")
            current = _state(tmp_path, "large")
            active = [
                job for job in current["jobs"].values()
                if job["host"] == "host-a" and job["status"] in {"QUEUED", "STARTING", "RUNNING", "UNKNOWN"}
            ]
            running = [job for job in active if job["status"] == "RUNNING"]
            for job in running:
                finished = CampaignManager._done_ids(current)
                for candidate_id in job["candidate_ids"]:
                    if candidate_id not in finished:
                        transport.add_progress("host-a", job["name"], candidate_id, "killed")
                transport.finish("host-a", job["name"])
            if not active:
                return
        pytest.fail("host-a retained campaign work after 300 scheduling steps")

    finish_host_a_work()
    clock.advance(LOST_AFTER_SECONDS - STALL_FLOOR_SECONDS)
    manager.tick("large")
    state = _state(tmp_path, "large")
    lost_jobs = [job for job in state["jobs"].values() if job["host"] == "host-b"]
    assert lost_jobs and all(job["status"] == "LOST" for job in lost_jobs)
    assert any(already_finished in job.get("finished_ids", []) for job in lost_jobs)

    finish_host_a_work()
    final_state = _state(tmp_path, "large")
    owners = CampaignManager._done_ids(final_state)
    assert owners == set(final_state["planned_ids"])
    assert len(owners) == 230
    assert all(already_finished not in job["candidate_ids"] for job in final_state["jobs"].values() if job.get("requeue_reason") == "host-lost")


def test_stall_threshold_never_pools_observations_across_hosts(tmp_path):
    # r1002 and v1001 are different hardware flavors; one host's candidate
    # times must never stretch another host's stall threshold.
    plan = _plan(12)
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", plan)
    state = _state(tmp_path)
    slow = _first_running(state, "host-a")
    quiet = _first_running(state, "host-b")
    transport.add_progress("host-a", slow["name"], slow["candidate_ids"][0], "killed", elapsed_seconds=600)
    manager.status("campaign")

    clock.advance(STALL_FLOOR_SECONDS)
    manager.tick("campaign")
    state = _state(tmp_path)
    assert state["jobs"][quiet["name"]]["status"] == "FAILED"
    assert state["jobs"][quiet["name"]]["exit_code"] == 143
    assert state["jobs"][quiet["name"]]["stall_after_seconds"] == STALL_FLOOR_SECONDS
    assert state["jobs"][slow["name"]]["status"] == "RUNNING"


def _lose(manager: CampaignManager, transport: FakeTransport, clock: FakeClock, host: str) -> None:
    transport.set_unreachable(host)
    manager.tick("campaign")
    clock.advance(LOST_AFTER_SECONDS + 1)
    manager.tick("campaign")


def test_repeated_loss_requeues_again_but_never_work_already_in_flight_elsewhere(tmp_path):
    plan = _plan(12)
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", plan)
    returning = _first_running(_state(tmp_path), "host-a")

    _lose(manager, transport, clock, "host-a")
    first_requeues = {
        name for name, job in _state(tmp_path)["jobs"].items()
        if job["host"] == "host-b" and job.get("requeue_reason") == "host-lost"
    }
    assert first_requeues
    transport.set_unreachable("host-a", False)
    manager.tick("campaign")
    state = _state(tmp_path)
    assert state["hosts"]["host-a"]["status"] == "ONLINE"
    assert state["jobs"][returning["name"]]["status"] == "RUNNING"

    _lose(manager, transport, clock, "host-a")
    state = _state(tmp_path)
    assert state["jobs"][returning["name"]]["status"] == "LOST"
    second_requeues = {
        name for name, job in state["jobs"].items()
        if job["host"] == "host-b" and job.get("requeue_reason") == "host-lost"
    }
    assert second_requeues == first_requeues


def test_exit_of_a_returned_job_does_not_requeue_work_in_flight_elsewhere(tmp_path):
    plan = _plan(12)
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", plan)
    returning = _first_running(_state(tmp_path), "host-a")

    _lose(manager, transport, clock, "host-a")
    transport.set_unreachable("host-a", False)
    transport.finish("host-a", returning["name"], exit_code=2)
    manager.tick("campaign")

    state = _state(tmp_path)
    assert state["jobs"][returning["name"]]["status"] == "FAILED"
    assert [job for job in state["jobs"].values() if job.get("requeue_reason") == "job-exit"] == []
    in_flight_elsewhere = {
        candidate_id for job in state["jobs"].values()
        if job["host"] == "host-b" and job["status"] in {"QUEUED", "RUNNING"}
        for candidate_id in job["candidate_ids"]
    }
    assert set(returning["candidate_ids"]) <= in_flight_elsewhere


def test_real_start_refuses_an_ids_file_whose_digest_differs_from_the_job_name(tmp_path):
    spec = _spec_for_project("ciu")
    home, environment = _real_runner_home(tmp_path, spec)
    (home / spec.candidate_file_home_path).write_text("b" * 64 + "\n", encoding="ascii")

    started = _real_start(home, environment, spec)
    assert started.returncode == 73
    assert "candidate file digest mismatch" in started.stderr
    assert not (home / "jobs" / spec.name).exists()


def _plan_with_refused_shared_prod_host() -> dict:
    hosts = _golden_8g_host_set()
    for index, host in enumerate(hosts["hosts"]):
        host["id"] = f"host-{chr(ord('a') + index)}"
    refused = copy.deepcopy(hosts["hosts"][0])
    refused["id"] = "host-shared-prod"
    refused["facts"]["policy"]["host_role"] = "shared-prod"
    hosts["hosts"].append(refused)
    candidates = [_candidate(f"refused-host-{index:03d}", 100 * 1024**2) for index in range(12)]
    plan = plan_campaign("r2", _candidate_table(candidates), hosts, DEMAND)
    plan["execution"] = dict(EXECUTION)
    assert next(host for host in plan["hosts"] if host["id"] == "host-shared-prod")["status"] == "refused"
    return plan


def test_refused_host_is_never_contacted_by_any_campaign_command(tmp_path):
    plan = _plan_with_refused_shared_prod_host()
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", plan)
    manager.status("campaign")
    transport.set_unreachable("host-a")
    manager.tick("campaign")
    clock.advance(LOST_AFTER_SECONDS + 1)
    manager.tick("campaign")
    manager.collect("campaign")

    assert [call for call in transport.calls if call[0] == "host-shared-prod"] == []
    state = _state(tmp_path)
    assert state["hosts"]["host-a"]["status"] == "LOST"
    assert all(job["host"] != "host-shared-prod" for job in state["jobs"].values())
    assert any(job["host"] == "host-b" and job.get("requeue_reason") == "host-lost" for job in state["jobs"].values())


def test_campaign_commands_refuse_to_overlap_on_one_state_file(tmp_path):
    plan = _plan(4)
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", plan)
    before = (tmp_path / "campaign.json").read_bytes()
    calls = len(transport.calls)

    holder = os.open(tmp_path / "campaign.lock", os.O_RDWR)
    try:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for command in (
            lambda: manager.run("campaign", plan),
            lambda: manager.status("campaign"),
            lambda: manager.tick("campaign"),
            lambda: manager.collect("campaign"),
        ):
            with pytest.raises(ContractError, match="campaign campaign is busy"):
                command()
    finally:
        os.close(holder)

    assert (tmp_path / "campaign.json").read_bytes() == before
    assert len(transport.calls) == calls
    assert manager.tick("campaign")["campaign"] == "campaign"


def test_start_refuses_an_unsynced_checkout_before_uploading(tmp_path):
    plan = _plan(4)
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    for host in ("host-a", "host-b"):
        transport.host(host).missing_checkouts.add(EXECUTION["commit"])
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", plan)

    assert [call for call in transport.calls if call[1] == "put"] == []
    state = _state(tmp_path)
    assert {job["status"] for job in state["jobs"].values()} == {"QUEUED"}
    assert any("is not synced" in job.get("start_error", "") for job in state["jobs"].values())

    for host in ("host-a", "host-b"):
        transport.host(host).missing_checkouts.clear()
    manager.tick("campaign")
    assert any(job["status"] == "RUNNING" for job in _state(tmp_path)["jobs"].values())


def test_real_probe_reports_checkout_readiness_and_never_creates_the_checkout(tmp_path):
    spec = _spec_for_project("ciu")
    home, environment = _real_runner_home(tmp_path, spec)
    unsynced = replace(spec, commit="c" * 40, batch=1)

    probe = subprocess.run(
        ["bash", "-c", probe_script({spec.name: spec, unsynced.name: unsynced})],
        cwd=home,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert probe.returncode == 0, probe.stderr
    rows = {row["name"]: row for row in json.loads(probe.stdout)["jobs"]}
    assert rows[spec.name] == {"name": spec.name, "state": "MISSING", "checkout_ready": True}
    assert rows[unsynced.name] == {"name": unsynced.name, "state": "MISSING", "checkout_ready": False}
    assert not (home / "checkouts" / unsynced.commit).exists()

    missing_project = replace(spec, project="absent-project", batch=2)
    probe = subprocess.run(
        ["bash", "-c", probe_script({missing_project.name: missing_project})],
        cwd=home,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert probe.returncode == 0, probe.stderr
    assert json.loads(probe.stdout)["jobs"][0]["checkout_ready"] is False


def test_real_probe_reads_progress_through_a_symlinked_checkouts_directory(tmp_path):
    spec = _spec_for_project(".")
    home, environment = _real_runner_home(tmp_path, spec)
    started = _real_start(home, environment, spec)
    assert started.returncode == 0, started.stderr
    _wait_real_job(home, spec)
    outside = tmp_path / "data-disk" / "checkouts"
    outside.parent.mkdir()
    (home / "checkouts").rename(outside)
    (home / "checkouts").symlink_to(outside)

    probe = subprocess.run(
        ["bash", "-c", probe_script({spec.name: spec})],
        cwd=home,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert probe.returncode == 0, probe.stderr
    row = json.loads(probe.stdout)["jobs"][0]
    assert row["progress_path"] == f"checkouts/{spec.commit}/.assay/progress-contract-assay.jsonl"
    progress_file = outside / spec.commit / ".assay" / "progress-contract-assay.jsonl"
    assert row["progress_offset"] == 0, row
    assert row["progress_end_offset"] == progress_file.stat().st_size, row
    assert [record["candidate_id"] for record in row["records"]] == list(spec.sorted_candidate_ids), row

    foreign_runner_home = dict(environment, RUNNER_HOME=str(tmp_path / "data-disk"))
    probe = subprocess.run(
        ["bash", "-c", probe_script({spec.name: spec})],
        cwd=home,
        env=foreign_runner_home,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert probe.returncode == 0, probe.stderr
    row = json.loads(probe.stdout)["jobs"][0]
    assert row["progress_path"] is None
    assert [record["candidate_id"] for record in row["records"]] == list(spec.sorted_candidate_ids)


def test_a_long_refused_start_does_not_leave_a_stale_stall_deadline_behind(tmp_path):
    plan = _plan(12)
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    for host in ("host-a", "host-b"):
        transport.host(host).missing_checkouts.add(EXECUTION["commit"])
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", plan)
    for _ in range(3):
        clock.advance(600)  # 30 minutes of refused starts, longer than the stall floor
        manager.tick("campaign")
    state = _state(tmp_path)
    assert all(job["started_mono"] is None and job["last_progress_mono"] is None for job in state["jobs"].values())
    assert len(state["warnings"]) == len(set(state["warnings"]))

    for host in ("host-a", "host-b"):
        transport.host(host).missing_checkouts.clear()
    manager.tick("campaign")
    started = {name for name, job in _state(tmp_path)["jobs"].items() if job["status"] == "RUNNING"}
    assert started
    manager.tick("campaign")
    state = _state(tmp_path)
    assert {name for name, job in state["jobs"].items() if job["status"] == "RUNNING"} == started
    assert sum(host.stops for host in transport.hosts.values()) == 0


class _CompetingTransport(FakeTransport):
    """A fake whose host calls try every campaign command from a second manager."""

    def __init__(self, rival: CampaignManager | None = None, plan: dict | None = None, **kwargs):
        super().__init__(**kwargs)
        self.rival = rival
        self.plan = plan
        self.refusals: list[str] = []

    def run(self, host_alias, command, timeout=None):
        if self.rival is not None:
            rival, self.rival = self.rival, None  # one attempt per host call, never re-entrant
            for name, attempt in (
                ("run", lambda: rival.run("campaign", self.plan)),
                ("status", lambda: rival.status("campaign")),
                ("tick", lambda: rival.tick("campaign")),
                ("collect", lambda: rival.collect("campaign")),
            ):
                try:
                    attempt()
                except ContractError as exc:
                    assert "is busy" in str(exc)
                    self.refusals.append(name)
            self.rival = rival
        return super().run(host_alias, command, timeout)


@pytest.mark.parametrize("command", ["run", "status", "tick", "collect"])
def test_a_second_manager_is_refused_for_the_whole_duration_of_every_command(tmp_path, command):
    plan = _plan(4)
    clock = FakeClock()
    seed = FakeTransport(clock=clock)
    CampaignManager(tmp_path, seed, clock=clock).run("campaign", plan)
    transport = _CompetingTransport(clock=clock)
    transport.hosts = seed.hosts
    rival = CampaignManager(tmp_path, transport, clock=clock)
    transport.rival, transport.plan = rival, plan
    holder = CampaignManager(tmp_path, transport, clock=clock)
    {
        "run": lambda: holder.run("campaign", plan),
        "status": lambda: holder.status("campaign"),
        "tick": lambda: holder.tick("campaign"),
        "collect": lambda: holder.collect("campaign"),
    }[command]()
    assert transport.refusals, "the holder never reached a host call"
    assert set(transport.refusals) == {"run", "status", "tick", "collect"}
    assert len(transport.refusals) % 4 == 0


def test_a_killed_lock_holder_never_wedges_the_campaign(tmp_path):
    plan = _plan(4)
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", plan)
    source = Path(__file__).resolve().parents[1] / "src"
    holder = subprocess.Popen(
        [
            "python3", "-c",
            "import fcntl, os, sys, time\n"
            "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)\n"
            "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
            "print('held', flush=True)\n"
            "time.sleep(60)\n",
            str(tmp_path / "campaign.lock"),
        ],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": str(source)},
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(ContractError, match="is busy"):
            manager.tick("campaign")
    finally:
        holder.kill()
        holder.wait(timeout=10)
    assert manager.tick("campaign")["campaign"] == "campaign"
    assert (tmp_path / "campaign.lock").exists()


def test_the_lock_is_released_when_a_command_fails(tmp_path):
    manager = CampaignManager(tmp_path, FakeTransport())
    with pytest.raises(ContractError, match="does not exist"):
        manager.tick("campaign")
    with pytest.raises(ContractError, match="does not exist"):
        manager.tick("campaign")


def test_real_probe_treats_a_symlinked_or_non_git_checkout_as_not_ready(tmp_path):
    spec = _spec_for_project("ciu")
    home, environment = _real_runner_home(tmp_path, spec)
    plain = replace(spec, commit="d" * 40, batch=1)
    (home / "checkouts" / plain.commit / "ciu").mkdir(parents=True)  # a directory, but not a git checkout
    linked = replace(spec, commit="e" * 40, batch=2)
    (home / "checkouts" / linked.commit).symlink_to(home / "checkouts" / spec.commit)  # the supervisor refuses links

    probe = subprocess.run(
        ["bash", "-c", probe_script({plain.name: plain, linked.name: linked, spec.name: spec})],
        cwd=home, env=environment, check=False, capture_output=True, text=True, timeout=10,
    )
    assert probe.returncode == 0, probe.stderr
    rows = {row["name"]: row["checkout_ready"] for row in json.loads(probe.stdout)["jobs"]}
    assert rows == {plain.name: False, linked.name: False, spec.name: True}


def test_a_lost_hosts_never_started_batch_runs_when_the_only_host_returns(tmp_path):
    plan = _plan(40)
    plan["hosts"] = [host for host in plan["hosts"] if host["id"] == "host-a"]
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", plan)
    assert sorted(job["status"] for job in _state(tmp_path)["jobs"].values()) == ["QUEUED", "RUNNING"]
    _lose(manager, transport, clock, "host-a")
    assert sorted(job["status"] for job in _state(tmp_path)["jobs"].values()) == ["LOST", "LOST"]
    transport.set_unreachable("host-a", False)
    manager.tick("campaign")
    state = _state(tmp_path)
    assert [job["name"] for job in state["jobs"].values() if job["status"] == "FAILED"] == []
    never_started = [job for job in state["jobs"].values() if job["started_mono"] is None and job["status"] == "EXIT"]
    assert never_started
    assert any(job.get("requeue_reason") == "job-exit" for job in state["jobs"].values())


def test_a_start_interrupted_before_the_job_existed_is_retried_not_failed(tmp_path):
    plan = _plan(4)
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", plan)
    path = tmp_path / "campaign.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    victim = next(job for job in state["jobs"].values() if job["status"] == "RUNNING")
    del transport.host(victim["host"]).jobs[victim["name"]]  # the controller died before the runner launched it
    victim["status"] = "STARTING"
    path.write_text(json.dumps(state), encoding="utf-8")
    manager.tick("campaign")
    after = _state(tmp_path)["jobs"][victim["name"]]
    assert after["status"] == "RUNNING"
    assert after["started_mono"] is not None and after["last_progress_mono"] is not None
    assert not any(job["status"] == "FAILED" for job in _state(tmp_path)["jobs"].values())


def _foreign_candidate_setup(tmp_path):
    plan = _plan(40)
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    manager = CampaignManager(tmp_path, transport, clock=clock)
    manager.run("campaign", plan)
    jobs = _state(tmp_path)["jobs"]
    running = next(job for job in jobs.values() if job["host"] == "host-a" and job["status"] == "RUNNING")
    other = next(job for job in jobs.values() if job["host"] == "host-a" and job["status"] == "QUEUED")
    return manager, transport, running, other["candidate_ids"][0]


def test_refresh_never_attributes_a_foreign_candidate_to_a_job(tmp_path):
    manager, transport, running, foreign = _foreign_candidate_setup(tmp_path)
    probe = manager.runner.probe

    def with_foreign_record(host, wanted):
        rows = probe(host, wanted)
        for row in rows.values():
            if row.get("state") != "MISSING":
                row["records"] = [*row.get("records", []), {
                    "candidate_id": foreign, "outcome_bucket": "killed",
                    "elapsed_seconds": 1.0, "emitted_at": "2030-01-01T00:00:00+00:00",
                }]
        return rows

    manager.runner.probe = with_foreign_record
    manager.tick("campaign")
    job = _state(tmp_path)["jobs"][running["name"]]
    assert foreign not in job["finished_ids"]
    assert all(record["candidate_id"] in running["candidate_ids"] for record in job["records"])


def test_collect_never_attributes_a_foreign_candidate_found_in_a_jobs_progress_window(tmp_path):
    manager, transport, running, foreign = _foreign_candidate_setup(tmp_path)
    transport.add_progress("host-a", running["name"], foreign, "killed", emitted_at="2030-01-01T00:00:01+00:00")
    manager.tick("campaign")
    result, _report, _json_path, _report_path = manager.collect("campaign")
    row = next(row for row in result["candidates"] if row["candidate_id"] == foreign)
    assert row["outcome"] == "missing"
    assert result["totals"]["killed"] == 0
