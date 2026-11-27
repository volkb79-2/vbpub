import hashlib
import fcntl
import io
import json
import os
import subprocess
import tarfile

import pytest

from fleetd.admission import AdmissionError
from fleetd.agent_policy import load_agent_policy
from fleetd.contracts import ContractError
from fleetd.executor import (
    HostJobExecutor,
    RunnerIdentity,
    ScopeState,
    SystemdScopeLauncher,
    validate_job_request,
)
from fleetd.executor_worker import (
    CommandResult,
    HostJobWorker,
    _extract_seed,
    _version_from_output,
    _version_key,
    _write_stdin_file,
)
from test_agent_protocol import SHA, _policy, _submit


class _Handle:
    def __init__(self, fd=37):
        self.fd = fd
        self.handed_off = False
        self.live = True

    def fileno(self):
        return self.fd

    def handoff(self):
        self.handed_off = True
        self.live = False


class _Scope:
    def __init__(self, state=None):
        self.state = state
        self.calls = []

    def launch(self, unit, argv, lock_fd):
        self.calls.append((unit, list(argv), lock_fd))
        return self.state or ScopeState(unit, True, 412)


def _executor(tmp_path, *, scope=None, user_resolver=None, guard_presence=None, process_signals=None):
    policy, _ = _policy(tmp_path)
    state = tmp_path / "state"
    jobs = state / "jobs"
    jobs.mkdir(parents=True)
    jobs.chmod(0o700)
    return HostJobExecutor(
        state, policy, policy_path=tmp_path / "agent.toml",
        scope_launcher=scope or _Scope(),
        user_resolver=user_resolver or (lambda name: RunnerIdentity(name, 1001, 1001, (1001, 44))),
        guard_presence=guard_presence,
        process_signals=process_signals,
        clock=lambda: "2026-11-27T00:00:00Z",
    ), policy, state


def _durable_job(state, request):
    job_dir = state / "jobs" / request["job_id"]
    job_dir.mkdir(mode=0o700)
    (job_dir / "request.json").write_text("{}", encoding="utf-8")
    return job_dir


def test_policy_requires_runner_user_and_retains_the_configured_value(tmp_path):
    policy, _ = _policy(tmp_path)
    assert policy.runner_user == "vscode"

    path = tmp_path / "agent.toml"
    path.write_text(path.read_text(encoding="utf-8").replace('runner_user = "vscode"\n', ""), encoding="utf-8")
    with pytest.raises(ContractError, match="missing key.*runner_user"):
        load_agent_policy(path)


def test_executor_start_hands_off_flock_only_after_scope_confirmation(tmp_path):
    scope = _Scope()
    executor, policy, state = _executor(tmp_path, scope=scope)
    request = _submit()
    request["sha"] = SHA
    job_dir = _durable_job(state, request)
    handle = _Handle()

    result = executor.start(request, handle)

    unit, argv, lock_fd = scope.calls[0]
    assert unit == f"fleetd-job-{request['job_id']}.scope"
    assert argv[:4] == [executor.python_executable, "-m", "fleetd.executor_worker", "worker"]
    assert "--state-dir" in argv and str(state) in argv
    assert "--policy" in argv and str(executor.policy_path) in argv
    assert "--job-id" in argv and request["job_id"] in argv
    assert "--lock-fd" in argv and str(handle.fd) in argv
    assert lock_fd == handle.fd
    assert handle.handed_off and not handle.live
    assert result["state"] == "running"
    assert result["executor_pid"] == 412
    assert result["started_utc"] == "2026-11-27T00:00:00Z"
    assert json.loads((job_dir / "state.json").read_text(encoding="utf-8"))["scope"] == unit


def test_executor_holds_worker_start_gate_until_scope_confirmation(tmp_path):
    observed = []
    executor, _policy_value, state = _executor(tmp_path)
    request = _submit()
    job_dir = _durable_job(state, request)

    class _CheckingScope:
        def launch(self, unit, argv, lock_fd):
            gate_fd = os.open(job_dir / "start.lock", os.O_RDWR)
            try:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(gate_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                observed.append((unit, list(argv), lock_fd))
            finally:
                os.close(gate_fd)
            return ScopeState(unit, True, 413)

    executor.scope_launcher = _CheckingScope()
    handle = _Handle()

    result = executor.start(request, handle)

    assert handle.handed_off is True
    assert result["start_confirmed"] is True
    assert result["executor_pid"] == 413
    gate_fd = os.open(job_dir / "start.lock", os.O_RDWR)
    try:
        fcntl.flock(gate_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert observed[0][2] == handle.fd
    finally:
        fcntl.flock(gate_fd, fcntl.LOCK_UN)
        os.close(gate_fd)


def test_cancel_during_scope_start_survives_confirmation_state_write(tmp_path):
    executor, _policy_value, state = _executor(tmp_path, guard_presence=lambda: False)
    request = _submit()
    job_dir = _durable_job(state, request)

    class _CancelDuringLaunch:
        def launch(self, unit, _argv, _lock_fd):
            stopped = executor.request_stop(request["job_id"], "cancel during start")
            assert stopped["stop_signal"] == "pending-lane-start"
            return ScopeState(unit, True, 414)

    executor.scope_launcher = _CancelDuringLaunch()
    result = executor.start(request, _Handle())
    durable = json.loads((job_dir / "state.json").read_text(encoding="utf-8"))

    assert result["start_confirmed"] is True
    assert durable["state"] == "stopping"
    assert durable["cancel_requested"] is True
    assert durable["stop_signal"] == "pending-lane-start"


def test_executor_keeps_the_admission_handle_when_scope_start_is_unconfirmed(tmp_path):
    executor, _policy_value, state = _executor(
        tmp_path, scope=_Scope(ScopeState("fleetd-job-controller-run-1.scope", False, 0)),
    )
    request = _submit()
    _durable_job(state, request)
    handle = _Handle()

    with pytest.raises(AdmissionError, match="not confirmed"):
        executor.start(request, handle)

    assert handle.live
    assert not handle.handed_off
    assert json.loads((state / "jobs" / request["job_id"] / "state.json").read_text())["state"] == "starting"


def test_worker_does_not_consume_or_complete_an_unconfirmed_start(tmp_path):
    worker = _worker(tmp_path, _VersionRunner({}))
    worker.job_dir.mkdir(parents=True)
    (worker.job_dir / "start.lock").touch(mode=0o600)
    (worker.job_dir / "state.json").write_text(json.dumps({
        "schema": "fleet-host-job-state/1", "job_id": "controller-run-1",
        "state": "starting", "start_confirmed": False,
    }), encoding="utf-8")

    try:
        assert worker.run() == 70
        assert not (worker.job_dir / "result.json").exists()
        assert json.loads((worker.job_dir / "state.json").read_text(encoding="utf-8"))["state"] == "starting"
    finally:
        os.close(worker.repo_root_fd)


def test_worker_accepts_a_confirmed_scope_start(tmp_path):
    worker = _worker(tmp_path, _VersionRunner({}))
    worker.job_dir.mkdir(parents=True)
    (worker.job_dir / "start.lock").touch(mode=0o600)
    (worker.job_dir / "state.json").write_text(json.dumps({
        "schema": "fleet-host-job-state/1", "job_id": "controller-run-1",
        "state": "running", "start_confirmed": True,
    }), encoding="utf-8")

    try:
        assert worker._await_scope_confirmation() is True
    finally:
        os.close(worker.repo_root_fd)


def test_executor_refuses_unknown_and_root_runner_users_before_launch(tmp_path):
    for resolver, message in (
        (lambda _name: (_ for _ in ()).throw(LookupError("no such user")), "unknown"),
        (lambda name: RunnerIdentity(name, 0, 0, (0,)), "non-root uid"),
    ):
        scope = _Scope()
        scenario = tmp_path / message.replace(" ", "-")
        scenario.mkdir()
        executor, _policy_value, state = _executor(scenario, scope=scope, user_resolver=resolver)
        request = _submit()
        _durable_job(state, request)
        with pytest.raises(AdmissionError, match=message):
            executor.start(request, _Handle())
        assert scope.calls == []


def test_executor_rechecks_job_commit_project_and_repository_paths(tmp_path):
    policy, _ = _policy(tmp_path)
    request = _submit()
    assert validate_job_request(request, policy) == (request["job_id"], policy.repo_roots["vbpub"])

    for update, message in (
        ({"job_id": "../outside"}, "job id"),
        ({"repo": "missing"}, "unknown repository"),
        ({"sha": "deadbeef"}, "full commit id"),
        ({"project": "../outside"}, "unsafe component"),
        ({"project": "/outside"}, "relative"),
    ):
        invalid = dict(request)
        invalid.update(update)
        with pytest.raises(AdmissionError, match=message):
            validate_job_request(invalid, policy)


def test_read_state_returns_none_for_missing_and_rejects_symlink_or_invalid_state(tmp_path):
    executor, _policy_value, state = _executor(tmp_path)
    request = _submit()
    job_dir = _durable_job(state, request)
    assert executor.read_state(request["job_id"]) is None

    target = job_dir / "target.json"
    target.write_text("{}", encoding="utf-8")
    (job_dir / "state.json").symlink_to(target)
    with pytest.raises(AdmissionError, match="symlink"):
        executor.read_state(request["job_id"])

    (job_dir / "state.json").unlink()
    (job_dir / "state.json").write_text('{"schema":"wrong","job_id":"controller-run-1"}', encoding="utf-8")
    with pytest.raises(AdmissionError, match="invalid identity"):
        executor.read_state(request["job_id"])


def test_systemd_scope_launcher_uses_argv_and_inherits_only_the_job_lock():
    popen_calls = []
    command_calls = []

    def popen(argv, **kwargs):
        popen_calls.append((argv, kwargs))
        return object()

    def run(argv, **kwargs):
        command_calls.append((argv, kwargs))
        return subprocess.CompletedProcess(
            argv, 0, "ActiveState=active\nMainPID=515\n", "",
        )

    launcher = SystemdScopeLauncher(3, popen=popen, run=run)
    state = launcher.launch("fleetd-job-controller-run-1.scope", ["/usr/bin/python3", "-m", "worker"], 37)

    argv, options = popen_calls[0]
    assert argv == [
        "systemd-run", "--scope", "--quiet", "--collect", "--unit",
        "fleetd-job-controller-run-1.scope", "--", "/usr/bin/python3", "-m", "worker",
    ]
    assert options["pass_fds"] == (37,)
    assert options["start_new_session"] is True
    query, query_options = command_calls[0]
    assert query[:2] == ["systemctl", "show"]
    assert query[-1] == "fleetd-job-controller-run-1.scope"
    assert query_options["timeout"] == 3
    assert state == ScopeState("fleetd-job-controller-run-1.scope", True, 515)


def test_systemd_scope_launcher_refuses_failed_or_unreadable_confirmation():
    launcher = SystemdScopeLauncher(
        1,
        popen=lambda *_args, **_kwargs: object(),
        run=lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 1, "", "no unit"),
    )
    with pytest.raises(AdmissionError, match="cannot confirm job scope"):
        launcher.launch("fleetd-job-x.scope", ["python3", "worker"], 8)

    inactive = SystemdScopeLauncher(
        1,
        popen=lambda *_args, **_kwargs: object(),
        run=lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, "ActiveState=activating\nMainPID=0\n", ""),
    )
    with pytest.raises(AdmissionError, match="did not confirm an active worker"):
        inactive.launch("fleetd-job-x.scope", ["python3", "worker"], 8)


def test_systemd_scope_launcher_refuses_when_scope_command_cannot_start():
    launcher = SystemdScopeLauncher(
        1,
        popen=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("systemd-run missing")),
        run=lambda *_args, **_kwargs: pytest.fail("systemctl must not run after spawn failure"),
    )
    with pytest.raises(AdmissionError, match="cannot start job scope"):
        launcher.launch("fleetd-job-x.scope", ["python3", "worker"], 8)


def test_executor_stop_records_absent_guard_and_terms_the_lane_group(tmp_path):
    class _Signals:
        def __init__(self):
            self.pids = []

        def terminate_group(self, pid):
            self.pids.append(pid)

    signals = _Signals()
    executor, _policy_value, state_dir = _executor(
        tmp_path, guard_presence=lambda: False, process_signals=signals,
    )
    request = _submit()
    job_dir = _durable_job(state_dir, request)
    (job_dir / "state.json").write_text(json.dumps({
        "schema": "fleet-host-job-state/1", "job_id": request["job_id"],
        "state": "running", "lane_pid": 412, "guard": {"present": None, "reason": "not-collected"},
    }), encoding="utf-8")

    stopped = executor.request_stop(request["job_id"], "operator request")

    assert stopped["state"] == "stopping"
    assert stopped["cancel_requested"] is True
    assert stopped["stop_signal"] == "sent"
    assert stopped["guard"] == {"present": False, "reason": "thrashguard-absent"}
    assert signals.pids == [412]


def test_executor_defers_cancel_when_guard_is_present_or_unknown(tmp_path):
    class _Signals:
        def terminate_group(self, _pid):
            pytest.fail("guard refusal must not send TERM")

    for name, presence, expected in (
        ("present", lambda: True, "deregister/thaw client is unavailable"),
        ("unknown", lambda: None, "presence is unknown"),
    ):
        scenario = tmp_path / name
        scenario.mkdir()
        executor, _policy_value, state_dir = _executor(
            scenario, guard_presence=presence, process_signals=_Signals(),
        )
        request = _submit()
        job_dir = _durable_job(state_dir, request)
        (job_dir / "state.json").write_text(json.dumps({
            "schema": "fleet-host-job-state/1", "job_id": request["job_id"],
            "state": "running", "lane_pid": 412,
        }), encoding="utf-8")
        with pytest.raises(AdmissionError, match=expected):
            executor.request_stop(request["job_id"], "operator request")
        state = json.loads((job_dir / "state.json").read_text(encoding="utf-8"))
        assert state["state"] == "stopping"
        assert "cancel_requested" not in state


def test_executor_stop_keeps_stopping_state_when_term_fails_and_handles_pending_lane(tmp_path):
    class _Signals:
        def terminate_group(self, _pid):
            raise PermissionError("TERM denied")

    executor, _policy_value, state_dir = _executor(
        tmp_path, guard_presence=lambda: False, process_signals=_Signals(),
    )
    request = _submit()
    job_dir = _durable_job(state_dir, request)
    (job_dir / "state.json").write_text(json.dumps({
        "schema": "fleet-host-job-state/1", "job_id": request["job_id"],
        "state": "running", "lane_pid": 412,
    }), encoding="utf-8")
    stopped = executor.request_stop(request["job_id"], "operator request")
    assert stopped["state"] == "stopping"
    assert stopped["stop_signal"] == "failed"
    assert stopped["reason"] == "TERM failed: TERM denied"

    other = tmp_path / "pending"
    other.mkdir()
    executor, _policy_value, state_dir = _executor(other, guard_presence=lambda: False)
    job_dir = _durable_job(state_dir, request)
    (job_dir / "state.json").write_text(json.dumps({
        "schema": "fleet-host-job-state/1", "job_id": request["job_id"], "state": "running",
    }), encoding="utf-8")
    stopped = executor.request_stop(request["job_id"], "operator request")
    assert stopped["stop_signal"] == "pending-lane-start"


def _worker(tmp_path, runner, *, finder=lambda _name: "/usr/bin/run-gate"):
    policy, _ = _policy(tmp_path)
    worker = HostJobWorker(
        tmp_path / "state", policy, "controller-run-1", 9,
        user_resolver=lambda name: RunnerIdentity(name, 1001, 1001, (1001,)),
        runner_factory=lambda _identity: runner,
        run_gate_finder=finder,
    )
    worker.runner_identity = RunnerIdentity("vscode", 1001, 1001, (1001,), "/home/vscode")
    worker.runner = runner
    worker.repo_root_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    return worker


class _VersionRunner:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def run(self, argv, **_kwargs):
        self.calls.append(list(argv))
        response = self.responses[argv[0]]
        if isinstance(response, BaseException):
            raise response
        return response


def test_run_gate_version_parser_handles_host_header_and_floor_order():
    assert _version_from_output(b"run-gate (python 3.14) 25.0.2\n") == "25.0.2"
    assert _version_key("25.0.1") == _version_key("25.0.1+local")
    assert _version_key("25.0.1rc1") < _version_key("25.0.1")
    assert _version_key("25.0.2.dev1") < _version_key("25.0.2")
    assert _version_from_output(b"") is None
    with pytest.raises(ValueError, match="unsupported run-gate version"):
        _version_key("not-a-version")


def test_run_gate_selection_uses_supported_fallback_and_refuses_old_tools(tmp_path):
    from fleetd.executor_worker import _RG_FALLBACK

    runner = _VersionRunner({
        "/usr/bin/run-gate": CommandResult(0, b"run-gate 24.3.0\n"),
        _RG_FALLBACK: CommandResult(0, b"run-gate 25.0.2\n"),
    })
    worker = _worker(tmp_path, runner)
    try:
        assert worker._select_run_gate() == (_RG_FALLBACK, "25.0.2")
        assert [call[0] for call in runner.calls] == ["/usr/bin/run-gate", _RG_FALLBACK]
    finally:
        os.close(worker.repo_root_fd)

    no_tools_dir = tmp_path / "none"
    no_tools_dir.mkdir()
    runner = _VersionRunner({
        _RG_FALLBACK: CommandResult(0, b"run-gate 25.0.0\n"),
    })
    worker = _worker(no_tools_dir, runner, finder=lambda _name: None)
    try:
        with pytest.raises(AdmissionError, match="no usable run-gate >= 25.0.1"):
            worker._select_run_gate()
    finally:
        os.close(worker.repo_root_fd)


def test_collection_streams_and_verifies_artifacts_and_one_store_archive(tmp_path):
    from fleetd.executor_worker import _archive_artifacts, _archive_store

    class _ArchiveRunner:
        def run_to_fd(self, argv, *, cwd_fd, output_fd, **_kwargs):
            if argv[3] == "archive-artifacts":
                document = _archive_artifacts(cwd_fd, output_fd)
            else:
                relative = argv[argv.index("--relative") + 1]
                document = _archive_store(cwd_fd, output_fd, relative)
            return CommandResult(0, json.dumps(document).encode("utf-8"), b"")

    checkout = tmp_path / "checkout"
    (checkout / ".assay").mkdir(parents=True)
    (checkout / ".run-gate").mkdir()
    (checkout / "store" / "nested").mkdir(parents=True)
    (checkout / ".assay" / "progress-x.jsonl").write_text('{"done":1}\n', encoding="utf-8")
    (checkout / ".run-gate" / "verdict-x.json").write_text('{"verdict":"PASS"}\n', encoding="utf-8")
    (checkout / "store" / "nested" / "payload.bin").write_bytes(b"payload")
    worker = _worker(tmp_path, _ArchiveRunner())
    worker.job_dir.mkdir(parents=True)
    (worker.job_dir / "state.json").write_text(json.dumps({
        "schema": "fleet-host-job-state/1", "job_id": "controller-run-1", "state": "collecting",
    }), encoding="utf-8")
    worker.request = {"project": ".", "stores": {"collect": "store"}}
    worker.checkout_fd = os.open(checkout, os.O_RDONLY | os.O_DIRECTORY)
    try:
        first = worker._collect()
        second = worker._collect()
        assert first == second
        assert first["artifact_files"] == 2
        assert first["store"]["status"] == "collected"
        checksum = (worker.job_dir / "assay-store.tgz.sha256").read_text(encoding="ascii")
        assert checksum == f"{first['store']['sha256']}  assay-store.tgz\n"
        with tarfile.open(worker.job_dir / "artifacts.tgz", "r:gz") as archive:
            assert set(archive.getnames()) >= {".assay/progress-x.jsonl", ".run-gate/verdict-x.json"}
        with tarfile.open(worker.job_dir / "assay-store.tgz", "r:gz") as archive:
            assert "store/nested/payload.bin" in archive.getnames()
    finally:
        os.close(worker.checkout_fd)
        os.close(worker.repo_root_fd)


def test_store_collection_rejects_symlinks_without_losing_artifacts(tmp_path):
    from fleetd.executor_worker import _archive_artifacts, _archive_store

    class _ArchiveRunner:
        def run_to_fd(self, argv, *, cwd_fd, output_fd, **_kwargs):
            if argv[3] == "archive-artifacts":
                document = _archive_artifacts(cwd_fd, output_fd)
            else:
                document = _archive_store(cwd_fd, output_fd, argv[argv.index("--relative") + 1])
            return CommandResult(0, json.dumps(document).encode("utf-8"), b"")

    checkout = tmp_path / "checkout"
    (checkout / ".assay").mkdir(parents=True)
    (checkout / ".assay" / "coverage.json").write_text("{}", encoding="utf-8")
    (checkout / "store").mkdir()
    (checkout / "store" / "escape").symlink_to(tmp_path / "outside")
    worker = _worker(tmp_path, _ArchiveRunner())
    worker.job_dir.mkdir(parents=True)
    (worker.job_dir / "state.json").write_text(json.dumps({
        "schema": "fleet-host-job-state/1", "job_id": "controller-run-1", "state": "collecting",
    }), encoding="utf-8")
    worker.request = {"project": ".", "stores": {"collect": "store"}}
    worker.checkout_fd = os.open(checkout, os.O_RDONLY | os.O_DIRECTORY)
    try:
        result = worker._collect()
        assert result["store"]["status"] == "failed"
        assert (worker.job_dir / "artifacts.tgz").is_file()
        assert not (worker.job_dir / "assay-store.tgz").exists()
        assert any("STORE-COLLECT-FAILED store symlink" in line for line in (worker.job_dir / "job.log").read_text().splitlines())
    finally:
        os.close(worker.checkout_fd)
        os.close(worker.repo_root_fd)


def test_worker_waits_for_each_inspected_lane_container_without_a_deadline(tmp_path):
    container_id = "a" * 64

    class _Docker:
        def __init__(self):
            self.calls = []

        def inspect(self, target):
            self.calls.append(target)
            return {
                "Id": container_id,
                "State": {"Running": len(self.calls) == 1},
            }

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "run-gate.toml").write_text(
        "[lanes.r0-r1]\nenvironment = 'tester'\n\n[environments.tester]\nmode = 'ephemeral'\nimage = 'tester:local'\n",
        encoding="utf-8",
    )
    event_dir = tmp_path / ".fleetd-events" / "vbpub"
    event_dir.mkdir(parents=True)
    event = event_dir / "controller-run-1.jsonl"
    event.write_text(json.dumps({
        "api": "fleet-host-agent/1", "job_id": "controller-run-1", "event": "lane-started",
        "lane": "r0-r1", "container_id": container_id, "image_id": None, "adopted": False,
    }) + "\n", encoding="utf-8")
    event.chmod(0o600)
    docker = _Docker()
    worker = _worker(tmp_path, _VersionRunner({}))
    worker.runner_identity = RunnerIdentity("test-runner", os.getuid(), os.getgid(), (os.getgid(),))
    worker.request = {"project": "."}
    worker.checkout_fd = os.open(repo, os.O_RDONLY | os.O_DIRECTORY)
    worker.job_dir.mkdir(parents=True)
    (worker.job_dir / "state.json").write_text(json.dumps({
        "schema": "fleet-host-job-state/1", "job_id": "controller-run-1", "state": "collecting",
    }), encoding="utf-8")
    worker.repo_root = repo
    worker.event_rel = ".fleetd-events/vbpub/controller-run-1.jsonl"
    worker.docker = docker
    waits = []
    worker.waiter = waits.append
    try:
        events = worker._wait_for_lane_containers()
        assert len(events) == 1
        assert docker.calls == [container_id, container_id]
        assert waits == [1]
    finally:
        os.close(worker.checkout_fd)
        os.close(worker.repo_root_fd)


def test_result_pass_requires_checkout_event_summary_and_process_agreement(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "run-gate.toml").write_text(
        "[lanes.r0-r1]\nenvironment = 'host'\n\n[environments.host]\nmode = 'host'\n",
        encoding="utf-8",
    )
    worker = _worker(tmp_path, _VersionRunner({}))
    worker.job_dir.mkdir(parents=True)
    worker.checkout_fd = os.open(checkout, os.O_RDONLY | os.O_DIRECTORY)
    worker.request = {"repo": "vbpub", "sha": SHA, "lane": "r0-r1", "project": "."}
    worker.repo_root = tmp_path / "repo"
    worker.checkout_rel = ".fleetd-worktrees/controller-run-1"
    (worker.job_dir / "state.json").write_text(json.dumps({
        "schema": "fleet-host-job-state/1", "job_id": "controller-run-1", "state": "collecting",
        "checkout": {"ready": True, "sha": SHA},
        "collection": {"collected_at": "2026-11-27T00:00:00Z"},
    }), encoding="utf-8")
    (worker.job_dir / "job.log").write_text(
        f"2026-11-27T00:00:00Z checkout {worker.repo_root / worker.checkout_rel} at {SHA}\n",
        encoding="utf-8",
    )
    summary = "run-gate: lane 'r0-r1' verdict PASS; exit_code 0; log /tmp/lane.log\n"
    (worker.job_dir / "lane.log").write_text(summary, encoding="utf-8")
    event = {
        "api": "fleet-host-agent/1", "job_id": "controller-run-1", "event": "lane-verdict",
        "lane": "r0-r1", "verdict": "PASS", "exit_code": 0, "lane_exit_code": 0,
    }
    try:
        bound = worker._bind_verdict([event], 0, [])
        assert bound["verdict"] == "PASS"
        assert bound["exit_code"] == 0
        result = json.loads((worker.job_dir / "result.json").read_text(encoding="utf-8"))
        assert result["checkout_sha"] == SHA
        assert result["binding"] == {"status": "verified", "reasons": []}

        forged = dict(event, exit_code=1)
        refused = worker._bind_verdict([forged], 0, [])
        assert refused["verdict"] == "ERROR"
        assert refused["exit_code"] == 3
        result = json.loads((worker.job_dir / "result.json").read_text(encoding="utf-8"))
        assert result["binding"]["status"] == "refused"
    finally:
        os.close(worker.checkout_fd)
        os.close(worker.repo_root_fd)


@pytest.mark.parametrize("event_text", [
    json.dumps({
        "api": "fleet-host-agent/1", "job_id": "controller-run-1", "event": "lane-verdict",
        "lane": "r0-r1", "verdict": "PASS", "exit_code": 0, "lane_exit_code": 0,
        "unexpected": "extra",
    }) + "\n",
    '{"api":"fleet-host-agent/1","api":"fleet-host-agent/1",'
    '"job_id":"controller-run-1","event":"lane-verdict","lane":"r0-r1",'
    '"verdict":"PASS","exit_code":0,"lane_exit_code":0}\n',
    json.dumps({
        "api": "fleet-host-agent/1", "job_id": "controller-run-1", "event": "lane-verdict",
        "lane": "r0-r1", "verdict": "PASS", "exit_code": 0,
    }) + "\n",
    json.dumps({
        "api": "fleet-host-agent/1", "job_id": "controller-run-1", "event": [],
        "lane": "r0-r1", "verdict": "PASS", "exit_code": 0, "lane_exit_code": 0,
    }) + "\n",
])
def test_event_reader_refuses_malformed_missing_extra_and_duplicate_fields(tmp_path, event_text):
    worker = _worker(tmp_path, _VersionRunner({}))
    worker.runner_identity = RunnerIdentity("test-runner", os.getuid(), os.getgid(), (os.getgid(),))
    repo = tmp_path / "repo"
    repo.mkdir()
    event_dir = tmp_path / ".fleetd-events" / "vbpub"
    event_dir.mkdir(parents=True)
    event_path = event_dir / "controller-run-1.jsonl"
    event_path.write_text(event_text, encoding="utf-8")
    event_path.chmod(0o600)
    worker.repo_root = repo
    worker.event_rel = ".fleetd-events/vbpub/controller-run-1.jsonl"
    try:
        with pytest.raises(AdmissionError, match="Run-Gate event line 1"):
            worker._read_events()
    finally:
        os.close(worker.repo_root_fd)


def test_container_binding_requires_full_id_checkout_mount_and_run_gate_labels(tmp_path):
    container_id = "b" * 64
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "run-gate.toml").write_text(
        "[lanes.r0-r1]\nenvironment = 'tester'\n\n[environments.tester]\nmode = 'ephemeral'\nimage = 'tester:local'\n",
        encoding="utf-8",
    )
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    expected_source = str(repo_root / ".fleetd-worktrees/controller-run-1")
    answer = {
        "Id": container_id,
        "State": {"Running": False},
        "Mounts": [{"Type": "bind", "Source": expected_source, "Destination": "/workspace"}],
        "Config": {"Labels": {
            "run-gate.repo": "vbpub", "run-gate.project": "vbpub", "run-gate.lane": "r0-r1",
            "run-gate.commit": SHA, "run-gate.name-schema": "2",
            "run-gate.started": "2026-11-27T00:00:00Z", "run-gate.job": "controller-run-1",
        }},
    }

    class _Docker:
        def inspect(self, _target):
            return answer

    worker = _worker(tmp_path, _VersionRunner({}))
    worker.request = {"project": ".", "sha": SHA, "lane": "r0-r1"}
    worker.repo_root = repo_root
    worker.checkout_rel = ".fleetd-worktrees/controller-run-1"
    worker.checkout_fd = os.open(checkout, os.O_RDONLY | os.O_DIRECTORY)
    worker.docker = _Docker()
    try:
        event = {"event": "lane-started", "lane": "r0-r1", "container_id": container_id}
        bound = worker._inspect_container_binding(event, expected_sha=SHA)
        assert bound["checkout_mount"] == expected_source
        assert bound["labels"]["run-gate.job"] == "controller-run-1"

        answer["Mounts"] = [{"Type": "bind", "Source": str(tmp_path / "other"), "Destination": "/workspace"}]
        with pytest.raises(AdmissionError, match="does not mount this job's checkout"):
            worker._inspect_container_binding(event, expected_sha=SHA)
    finally:
        os.close(worker.checkout_fd)
        os.close(worker.repo_root_fd)

def _pipe_with(data):
    read_fd, write_fd = os.pipe()
    os.write(write_fd, data)
    os.close(write_fd)
    return read_fd


def _seed_archive(member_name="store/value.txt", content=b"seed", member_type=tarfile.REGTYPE):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        member = tarfile.TarInfo(member_name)
        member.type = member_type
        if member_type == tarfile.REGTYPE:
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
        else:
            archive.addfile(member)
    return output.getvalue()


def test_verified_bundle_copy_checks_length_digest_and_removes_partial(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "checkout").mkdir()
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    source = b"verified bundle"
    source_fd = _pipe_with(source)
    try:
        _write_stdin_file(root_fd, "checkout/in.bundle", source_fd, hashlib.sha256(source).hexdigest(), len(source))
    finally:
        os.close(source_fd)
    assert (root / "checkout" / "in.bundle").read_bytes() == source

    source_fd = _pipe_with(b"changed")
    try:
        with pytest.raises(ValueError, match="digest or size changed"):
            _write_stdin_file(root_fd, "checkout/partial", source_fd, hashlib.sha256(source).hexdigest(), len(source))
    finally:
        os.close(source_fd)
        os.close(root_fd)
    assert not (root / "checkout" / "partial").exists()


def test_seed_unpack_verifies_archive_and_replaces_only_target_tree(tmp_path):
    root = tmp_path / "checkout"
    target = root / "store"
    target.mkdir(parents=True)
    (target / "stale").write_text("old", encoding="utf-8")
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    archive = _seed_archive()
    input_fd = _pipe_with(archive)
    try:
        _extract_seed(root_fd, input_fd, "store", hashlib.sha256(archive).hexdigest(), len(archive), 100)
    finally:
        os.close(input_fd)
    assert (target / "value.txt").read_bytes() == b"seed"
    assert not (target / "stale").exists()

    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "other").symlink_to(outside, target_is_directory=True)
    escaped = _seed_archive("store/../outside/value.txt")
    input_fd = _pipe_with(escaped)
    try:
        with pytest.raises(ValueError, match="member-outside-path"):
            _extract_seed(root_fd, input_fd, "store", hashlib.sha256(escaped).hexdigest(), len(escaped), 100)
    finally:
        os.close(input_fd)
        os.close(root_fd)
    assert list(outside.iterdir()) == []


def test_seed_unpack_refuses_special_members_bad_digest_and_unsafe_target(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    symlink_archive = _seed_archive("store/link", member_type=tarfile.SYMTYPE)
    input_fd = _pipe_with(symlink_archive)
    try:
        with pytest.raises(ValueError, match="special-member"):
            _extract_seed(
                root_fd, input_fd, "store", hashlib.sha256(symlink_archive).hexdigest(),
                len(symlink_archive), 100,
            )
    finally:
        os.close(input_fd)

    input_fd = _pipe_with(b"not the archive")
    try:
        with pytest.raises(ValueError, match="checksum"):
            _extract_seed(root_fd, input_fd, "store", "0" * 64, 15, 100)
    finally:
        os.close(input_fd)

    input_fd = _pipe_with(symlink_archive)
    try:
        with pytest.raises(ValueError, match="unsafe-target"):
            _extract_seed(
                root_fd, input_fd, ".git/store", hashlib.sha256(symlink_archive).hexdigest(),
                len(symlink_archive), 100,
            )
    finally:
        os.close(input_fd)
        os.close(root_fd)


def test_seed_upload_requires_matching_content_object_and_sha256_sidecar(tmp_path):
    from fleetd.executor_worker import HostJobWorker

    worker = _worker(tmp_path, _VersionRunner({}))
    worker.state_dir.mkdir()
    worker.group = "controller"
    upload_root = worker.state_dir / "uploads" / worker.group
    upload_root.mkdir(parents=True)
    archive = b"seed archive bytes"
    archive_digest = hashlib.sha256(archive).hexdigest()

    def publish(name, content):
        digest = hashlib.sha256(content).hexdigest()
        object_dir = upload_root / name
        object_dir.mkdir()
        (object_dir / digest).write_bytes(content)
        return object_dir / digest

    publish("assay-seed-0.tgz", archive)
    sidecar = publish("assay-seed-0.tgz.sha256", f"{archive_digest}  assay-seed-0.tgz\n".encode())
    try:
        assert worker._find_seed_upload(archive_digest, len(archive)) == "assay-seed-0.tgz"
        sidecar.unlink()
        assert worker._find_seed_upload(archive_digest, len(archive)) is None
    finally:
        os.close(worker.repo_root_fd)
