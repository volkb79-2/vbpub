import hashlib
import contextlib
import fcntl
import io
import json
import os
import shutil
import subprocess
import tarfile
import time
from pathlib import Path

import pytest
import fleetd.executor_worker as executor_worker_module
import fleetd.executor as executor_module

from fleetd.admission import AdmissionError, ExitCode
from fleetd.agent_admission import AgentAdmission
from fleetd.agent_protocol import validate_request
from fleetd.agent_policy import load_agent_policy
from fleetd.contracts import ContractError
from fleetd.executor import (
    HostJobExecutor,
    RunnerIdentity,
    ScopeState,
    SystemdScopeLauncher,
    _atomic_json,
    _job_state_lock,
    _read_pid_cgroup,
    _start_reaper,
    validate_job_request,
)
from fleetd.executor_worker import (
    CommandResult,
    DockerClient,
    HostJobWorker,
    SubprocessRunnerCommands,
    _extract_seed,
    _version_from_output,
    _version_key,
    _write_stdin_file,
    main as worker_main,
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

    def launch(self, unit, argv, lock_fd, handshake_read_fd, handshake_write_fd):
        self.calls.append((unit, list(argv), lock_fd, handshake_read_fd, handshake_write_fd))
        return self.state or ScopeState(unit, True, 412)


def _executor(tmp_path, *, scope=None, user_resolver=None, guard_presence=None):
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


def test_store_collection_accepts_multiple_nonoverlapping_repository_relative_paths(tmp_path):
    policy, _checkout = _policy(tmp_path)
    identity = policy.resolve_peer(1000, 100)
    request = _submit()
    request["stores"] = {"collect": ["scripts/netcup/.run-gate/assay-shards", "fleetd/.assay"] , "seed": None}
    assert validate_request(request, identity, policy) is request

    request["stores"] = {"collect": ["fleetd", "fleetd/tests"], "seed": None}
    with pytest.raises(AdmissionError, match="may not overlap"):
        validate_request(request, identity, policy)


def test_executor_start_hands_off_flock_only_after_scope_confirmation(tmp_path):
    scope = _Scope()
    executor, policy, state = _executor(tmp_path, scope=scope)
    request = _submit()
    request["sha"] = SHA
    job_dir = _durable_job(state, request)
    handle = _Handle()

    result = executor.start(request, handle)

    unit, argv, lock_fd, _read_fd, _write_fd = scope.calls[0]
    assert unit == f"fleetd-job-{request['job_id']}.scope"
    assert argv[:4] == [executor.python_executable, "-m", "fleetd.executor_worker", "worker"]
    assert "--state-dir" in argv and str(state) in argv
    assert "--policy" in argv and str(executor.policy_path) in argv
    assert "--job-id" in argv and request["job_id"] in argv
    assert "--lock-fd" in argv and str(handle.fd) in argv
    assert "--handshake-fd" in argv
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
        def launch(self, unit, argv, lock_fd, _read_fd, _write_fd):
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
        def launch(self, unit, _argv, _lock_fd, _read_fd, _write_fd):
            stopped = executor.request_stop(request["job_id"], "cancel during start")
            assert stopped["cancel_requested"] is True
            return ScopeState(unit, True, 414)

    executor.scope_launcher = _CancelDuringLaunch()
    result = executor.start(request, _Handle())
    durable = json.loads((job_dir / "state.json").read_text(encoding="utf-8"))

    assert result["start_confirmed"] is True
    assert durable["state"] == "stopping"
    assert durable["cancel_requested"] is True


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
        os.write(kwargs["pass_fds"][1], b"515\n")
        return type("WorkerProcess", (), {"pid": 515, "wait": lambda _self: 0})()

    def run(argv, **kwargs):
        command_calls.append((argv, kwargs))
        return subprocess.CompletedProcess(
            argv, 0, "ActiveState=active\nMainPID=999\n", "",
        )

    read_fd, write_fd = os.pipe()
    reaped = []
    launcher = SystemdScopeLauncher(
        3, popen=popen, run=run,
        pid_cgroup=lambda pid: f"/user.slice/fleetd-job-controller-run-1.scope" if pid == 515 else "/wrong",
        reaper_start=lambda wait: reaped.append(wait),
    )
    state = launcher.launch(
        "fleetd-job-controller-run-1.scope", ["/usr/bin/python3", "-m", "worker"],
        37, read_fd, write_fd,
    )

    argv, options = popen_calls[0]
    assert argv == [
        "systemd-run", "--scope", "--quiet", "--collect", "--unit",
        "fleetd-job-controller-run-1.scope", "--", "/usr/bin/python3", "-m", "worker",
    ]
    assert options["pass_fds"] == (37, write_fd)
    assert options["start_new_session"] is True
    query, query_options = command_calls[0]
    assert query[:2] == ["systemctl", "show"]
    assert query[-1] == "fleetd-job-controller-run-1.scope"
    assert 0 < query_options["timeout"] <= 3
    assert state == ScopeState("fleetd-job-controller-run-1.scope", True, 515)
    assert len(reaped) == 1
    os.close(read_fd)


def test_systemd_scope_launcher_refuses_failed_or_unreadable_confirmation():
    def launch_with(run):
        read_fd, write_fd = os.pipe()
        def popen(_argv, **kwargs):
            os.write(kwargs["pass_fds"][1], b"515\n")
            return type("WorkerProcess", (), {"pid": 515, "wait": lambda _self: 0})()
        now = [0.0]
        launcher = SystemdScopeLauncher(
            1, popen=popen, run=run, pid_cgroup=lambda _pid: "/x/fleetd-job-x.scope",
            waiter=lambda amount: now.__setitem__(0, now[0] + amount),
            monotonic=lambda: now[0], reaper_start=lambda _wait: None,
        )
        try:
            launcher.launch("fleetd-job-x.scope", ["python3", "worker"], 8, read_fd, write_fd)
        finally:
            os.close(read_fd)

    with pytest.raises(AdmissionError, match="timed out confirming active job scope"):
        launch_with(lambda _argv, **_kwargs: (_ for _ in ()).throw(OSError("systemctl unavailable")))

    with pytest.raises(AdmissionError, match="timed out confirming active job scope"):
        launch_with(lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, "ActiveState=activating\n", ""))


def test_systemd_scope_launcher_refuses_when_scope_command_cannot_start():
    launcher = SystemdScopeLauncher(
        1,
        popen=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("systemd-run missing")),
        run=lambda *_args, **_kwargs: pytest.fail("systemctl must not run after spawn failure"),
    )
    read_fd, write_fd = os.pipe()
    try:
        with pytest.raises(AdmissionError, match="cannot start job scope"):
            launcher.launch("fleetd-job-x.scope", ["python3", "worker"], 8, read_fd, write_fd)
    finally:
        os.close(read_fd)


def test_systemd_scope_launcher_requires_handshake_pid_and_matching_cgroup():
    def launch_with(*, handshake, cgroup):
        read_fd, write_fd = os.pipe()

        def popen(_argv, **kwargs):
            if handshake is not None:
                os.write(kwargs["pass_fds"][1], handshake)
            return type("WorkerProcess", (), {"pid": 515, "wait": lambda _self: 0})()

        launcher = SystemdScopeLauncher(
            1,
            popen=popen,
            run=lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, "ActiveState=active\nMainPID=999\n", ""),
            pid_cgroup=lambda _pid: cgroup,
            reaper_start=lambda _wait: None,
        )
        try:
            return launcher.launch("fleetd-job-x.scope", ["python3", "worker"], 8, read_fd, write_fd)
        finally:
            os.close(read_fd)

    with pytest.raises(AdmissionError, match="handshake pid 516 differs"):
        launch_with(handshake=b"516\n", cgroup="/x/fleetd-job-x.scope")
    with pytest.raises(AdmissionError, match="not scope fleetd-job-x.scope"):
        launch_with(handshake=b"515\n", cgroup="/x/other.scope")


def test_scope_reaper_starts_a_daemon_waiter_thread_without_waiting_in_the_agent():
    calls = []

    class _Thread:
        def __init__(self, *, target, daemon):
            calls.append((target, daemon))

        def start(self):
            calls.append("start")

    def wait():
        return 0

    _start_reaper(wait, thread_factory=_Thread)
    assert calls == [(wait, True), "start"]


def test_scope_launcher_refuses_unreapable_worker_and_unreadable_worker_cgroup():
    read_fd, write_fd = os.pipe()

    def popen(_argv, **kwargs):
        os.write(kwargs["pass_fds"][1], b"515\n")
        return type("WorkerProcess", (), {"pid": 515, "wait": lambda _self: 0})()

    active = lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, "ActiveState=active\n", "")
    try:
        unreapable = SystemdScopeLauncher(
            1, popen=popen, run=lambda *_args, **_kwargs: pytest.fail("scope query ran before reaper startup"),
            reaper_start=lambda _wait: (_ for _ in ()).throw(RuntimeError("thread unavailable")),
        )
        with pytest.raises(AdmissionError, match="cannot reap job scope command"):
            unreapable.launch("fleetd-job-x.scope", ["worker"], 8, read_fd, write_fd)
    finally:
        os.close(read_fd)

    read_fd, write_fd = os.pipe()
    try:
        unreadable = SystemdScopeLauncher(
            1, popen=popen, run=active,
            pid_cgroup=lambda _pid: (_ for _ in ()).throw(OSError("procfs unavailable")),
            reaper_start=lambda _wait: None,
        )
        with pytest.raises(AdmissionError, match="cannot read worker 515 cgroup"):
            unreadable.launch("fleetd-job-x.scope", ["worker"], 8, read_fd, write_fd)
    finally:
        os.close(read_fd)


def test_worker_main_verifies_flock_before_handshake_and_execution(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    lock_path = state_dir / "admission.lock"
    lock_path.touch()
    lock_fd = os.open(lock_path, os.O_RDWR)
    read_fd, write_fd = os.pipe()
    order = []
    real_flock = fcntl.flock
    real_write = os.write

    class _Worker:
        def __init__(self, _state_dir, _policy, _job_id, _lock_fd):
            pass

        def run(self):
            order.append("run")
            return 0

    def flock(fd, operation):
        order.append("flock")
        return real_flock(fd, operation)

    def write(fd, body):
        order.append("write")
        return real_write(fd, body)

    monkeypatch.setattr(executor_worker_module, "load_agent_policy", lambda _path: object())
    monkeypatch.setattr(executor_worker_module, "HostJobWorker", _Worker)
    monkeypatch.setattr(executor_worker_module.fcntl, "flock", flock)
    monkeypatch.setattr(executor_worker_module.os, "write", write)
    try:
        status = worker_main([
            "worker", "--state-dir", str(state_dir), "--policy", str(tmp_path / "agent.toml"),
            "--job-id", "controller-run-1", "--lock-fd", str(lock_fd), "--handshake-fd", str(write_fd),
        ])
        handshake = os.read(read_fd, 64)
        assert status == 0
        assert handshake == f"{os.getpid()}\n".encode()
        assert order == ["flock", "write", "run"]
    finally:
        os.close(lock_fd)
        os.close(read_fd)
        try:
            os.close(write_fd)
        except OSError:
            pass


def test_executor_state_read_accepts_records_larger_than_legacy_64k_limit(tmp_path):
    executor, _policy_value, state_dir = _executor(tmp_path)
    request = _submit()
    job_dir = _durable_job(state_dir, request)
    state = {
        "schema": "fleet-host-job-state/1", "job_id": request["job_id"], "state": "running",
        "large_log_excerpt": "x" * 70_000,
    }
    (job_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    assert executor.read_state(request["job_id"])["large_log_excerpt"] == "x" * 70_000


def test_docker_client_queries_exact_job_label_and_accepts_only_explicit_missing_objects():
    calls = []
    container_id = "c" * 64

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, f"{container_id}\n".encode(), b"")

    docker = DockerClient(3, run=run)
    assert docker.job_containers("controller-run-1") == (container_id,)
    assert calls[0][0] == [
        "/usr/bin/docker", "--host", "unix:///var/run/docker.sock", "ps", "-a", "--no-trunc",
        "--quiet", "--filter", "label=run-gate.job=controller-run-1",
    ]
    assert calls[0][1]["timeout"] == 3

    for detail in (
        f"Error: No such object: {container_id}",
        f"Error response from daemon: No such object: {container_id}",
        f"error: no such object: {container_id}",
        f"  ERROR response from daemon:   NO SUCH OBJECT: {container_id}  ",
    ):
        absent = DockerClient(
            3,
            run=lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 1, b"", f"{detail}\n".encode()),
        )
        assert absent.inspect(container_id) is None

    unrelated = DockerClient(
        3,
        run=lambda argv, **_kwargs: subprocess.CompletedProcess(
            argv, 1, b"", f"Error: No such object: {'d' * 64}\n".encode(),
        ),
    )
    with pytest.raises(AdmissionError, match="Docker inspect.*failed"):
        unrelated.inspect(container_id)

    for detail in (
        f"Error: No such container: {container_id}",
        f"Error response from daemon: No such container: {container_id}",
    ):
        absent = DockerClient(
            3,
            run=lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 1, b"", f"{detail}\n".encode()),
        )
        absent.remove_stopped(container_id)


def test_executor_docker_client_uses_fixed_host_closed_environment_and_absolute_binary(monkeypatch):
    calls = []

    for name, value in {
        "DOCKER_HOST": "tcp://attacker.example:2375",
        "DOCKER_CONTEXT": "attacker-context",
        "DOCKER_CONFIG": "/tmp/attacker-docker-config",
        "DOCKER_TLS_VERIFY": "1",
        "DOCKER_CERT_PATH": "/tmp/attacker-certs",
    }.items():
        monkeypatch.setenv(name, value)

    def run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        return subprocess.CompletedProcess(argv, 0, b"[]", b"")

    DockerClient(2, run=run).command("version")

    argv, kwargs = calls[0]
    assert argv == ["/usr/bin/docker", "--host", "unix:///var/run/docker.sock", "version"]
    assert kwargs["env"] == {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LC_ALL": "C"}
    assert kwargs["cwd"] == "/"
    assert kwargs["shell"] is False and kwargs["start_new_session"] is True
    assert kwargs["timeout"] == 2
    assert kwargs["check"] is False and kwargs["capture_output"] is True and kwargs["text"] is False


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("DOCKER_HOST", "tcp://attacker.example:2375"),
        ("DOCKER_CONTEXT", "attacker-context"),
        ("DOCKER_CONFIG", "/tmp/attacker-docker-config"),
        ("DOCKER_TLS_VERIFY", "1"),
        ("DOCKER_CERT_PATH", "/tmp/attacker-certs"),
    ],
)
def test_lane_environment_uses_fixed_docker_endpoint_and_rejects_request_selectors(
    tmp_path, name, value,
):
    request = _submit()
    request["env"] = {}
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path, request=request)

    environment = worker._environment()
    assert environment["DOCKER_HOST"] == "unix:///var/run/docker.sock"
    assert not ({"DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"} & environment.keys())

    worker.request["env"] = {name: value}
    with pytest.raises(AdmissionError, match=name):
        worker._environment()


def test_lane_environment_refuses_host_allowed_docker_selector_by_name(tmp_path):
    request = _submit()
    request["env"] = {}
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path, request=request)
    worker.policy.env_values["DOCKER_HOST"] = frozenset({"tcp://attacker.example:2375"})

    with pytest.raises(AdmissionError, match="agent.toml.env_values.DOCKER_HOST"):
        worker._environment()


def test_executor_stop_records_cancel_intent_without_signaling_the_lane_group(tmp_path):
    executor, _policy_value, state_dir = _executor(tmp_path, guard_presence=lambda: False)
    request = _submit()
    job_dir = _durable_job(state_dir, request)
    (job_dir / "state.json").write_text(json.dumps({
        "schema": "fleet-host-job-state/1", "job_id": request["job_id"],
        "state": "running", "lane_pid": 412, "guard": {"present": None, "reason": "not-collected"},
    }), encoding="utf-8")

    stopped = executor.request_stop(request["job_id"], "operator request")

    assert stopped["state"] == "stopping"
    assert stopped["cancel_requested"] is True
    assert stopped["cancel_reason"] == "operator request"
    assert "stop_signal" not in stopped


def test_executor_persists_cancel_intent_when_guard_is_present_or_unknown(tmp_path):
    for name, presence in (
        ("present", lambda: True),
        ("unknown", lambda: None),
    ):
        scenario = tmp_path / name
        scenario.mkdir()
        executor, _policy_value, state_dir = _executor(
            scenario, guard_presence=presence,
        )
        request = _submit()
        job_dir = _durable_job(state_dir, request)
        (job_dir / "state.json").write_text(json.dumps({
            "schema": "fleet-host-job-state/1", "job_id": request["job_id"],
            "state": "running", "lane_pid": 412,
        }), encoding="utf-8")
        stopped = executor.request_stop(request["job_id"], "operator request")
        state = json.loads((job_dir / "state.json").read_text(encoding="utf-8"))
        assert state["state"] == "stopping"
        assert state["cancel_requested"] is True
        assert stopped["cancel_requested"] is True


def test_executor_stop_records_intent_for_running_and_pending_lanes(tmp_path):
    executor, _policy_value, state_dir = _executor(tmp_path, guard_presence=lambda: False)
    request = _submit()
    job_dir = _durable_job(state_dir, request)
    (job_dir / "state.json").write_text(json.dumps({
        "schema": "fleet-host-job-state/1", "job_id": request["job_id"],
        "state": "running", "lane_pid": 412,
    }), encoding="utf-8")
    stopped = executor.request_stop(request["job_id"], "operator request")
    assert stopped["state"] == "stopping"
    assert stopped["cancel_requested"] is True

    other = tmp_path / "pending"
    other.mkdir()
    executor, _policy_value, state_dir = _executor(other, guard_presence=lambda: False)
    job_dir = _durable_job(state_dir, request)
    (job_dir / "state.json").write_text(json.dumps({
        "schema": "fleet-host-job-state/1", "job_id": request["job_id"], "state": "running",
    }), encoding="utf-8")
    stopped = executor.request_stop(request["job_id"], "operator request")
    assert stopped["cancel_requested"] is True
    assert "stop_signal" not in stopped


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


def test_subprocess_runner_drops_groups_gid_uid_before_fchdir(monkeypatch):
    calls = []
    monkeypatch.setattr(os, "setgroups", lambda groups: calls.append(("groups", tuple(groups))))
    monkeypatch.setattr(os, "setgid", lambda gid: calls.append(("gid", gid)))
    monkeypatch.setattr(os, "setuid", lambda uid: calls.append(("uid", uid)))
    monkeypatch.setattr(os, "fchdir", lambda fd: calls.append(("chdir", fd)))
    runner = SubprocessRunnerCommands(RunnerIdentity("runner", 1001, 1002, (1002, 44)))
    runner._preexec(27)()
    assert calls == [
        ("groups", (1002, 44)), ("gid", 1002), ("uid", 1001), ("chdir", 27),
    ]


def test_ephemeral_prerequisites_use_lane_environment_image_override_and_admission_show(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "run-gate.toml").write_text(
        "[admission]\nenabled = true\n\n"
        "[lanes.r0-r1]\nenvironment = 'tester'\n\n"
        "[environments.tester]\nmode = 'ephemeral'\nimage = 'declared:local'\n"
        "image_env = 'FLEET_TEST_IMAGE'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FLEET_TEST_IMAGE", "root-agent:wrong")

    class _Runner:
        def __init__(self):
            self.calls = []

        def run(self, argv, *, cwd_fd, env, timeout=None):
            self.calls.append((list(argv), cwd_fd, dict(env), timeout))
            if argv[1:] == ["--version"]:
                return CommandResult(0, b"run-gate 25.0.2\n")
            if argv[1:] == ["admission", "show"]:
                return CommandResult(0, b"admission: enabled=true max_concurrent=2\n")
            return CommandResult(1, b"", b"unexpected command")

    class _Docker:
        def __init__(self):
            self.image_refs = []

        def image(self, image):
            self.image_refs.append(image)
            return {"Config": {"User": "root"}}

    runner = _Runner()
    docker = _Docker()
    worker = _worker(tmp_path, runner)
    worker.runner_identity = RunnerIdentity("runner", 1001, 1002, (1002,))
    worker.request = {
        "project": ".", "lane": "r0-r1", "env": {"FLEET_TEST_IMAGE": "request:chosen"},
    }
    worker.checkout_fd = os.open(checkout, os.O_RDONLY | os.O_DIRECTORY)
    worker.docker = docker
    try:
        _binary, mode, facts = worker._prerequisites()
        assert mode == "ephemeral"
        assert facts["image"] == "request:chosen"
        assert docker.image_refs == ["request:chosen"]
        admission_call = next(call for call in runner.calls if call[0][1:3] == ["admission", "show"])
        assert admission_call[2]["FLEET_TEST_IMAGE"] == "request:chosen"
    finally:
        os.close(worker.checkout_fd)
        os.close(worker.repo_root_fd)


def test_invoker_lane_user_checks_runner_uid_and_gid_in_image(tmp_path):
    class _Docker:
        def __init__(self):
            self.checked = []

        def check_passwd(self, image, username_or_uid, job_id, *, expected_gid=None):
            self.checked.append((image, username_or_uid, job_id, expected_gid))

    worker = _worker(tmp_path, _VersionRunner({}))
    worker.runner_identity = RunnerIdentity("runner", 1001, 1002, (1002, 44))
    docker = _Docker()
    worker.docker = docker
    try:
        worker._check_lane_user("tester:local", {}, {"user": "invoker"})
        assert docker.checked == [("tester:local", "1001", "controller-run-1", 1002)]
    finally:
        os.close(worker.repo_root_fd)


def test_cancel_after_lane_starting_record_but_before_start_clears_live_marker(tmp_path):
    class _Runner:
        def start(self, *_args, **_kwargs):
            pytest.fail("cancel checkpoint must prevent lane start")

    worker = _worker(tmp_path, _Runner())
    worker.runner_identity = RunnerIdentity("runner", 1001, 1002, (1002,))
    repo = tmp_path / "repo"
    repo.mkdir()
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    worker.repo_root = repo
    worker.checkout_rel = ".fleetd-worktrees/controller-run-1"
    worker.checkout_fd = os.open(checkout, os.O_RDONLY | os.O_DIRECTORY)
    worker.request = {
        "repo": "vbpub", "project": ".", "lane": "r0-r1", "extra_argv": [], "base": None,
    }
    worker.job_dir.mkdir(parents=True)
    (worker.job_dir / "state.json").write_text(json.dumps({
        "schema": "fleet-host-job-state/1", "job_id": "controller-run-1", "state": "stopping",
        "cancel_requested": True, "cancel_reason": "operator request",
        "guard": {"present": False},
    }), encoding="utf-8")

    def helper(mode, *args, cwd_fd=None):
        assert mode == "prepare-event"
        executor_worker_module._prepare_event(cwd_fd, args[1])
        return CommandResult(0)

    worker._helper = helper
    try:
        with pytest.raises(AdmissionError, match="operator request"):
            worker._run_lane("run-gate", "host", {"runner_name": None}, None)
        state = json.loads((worker.job_dir / "state.json").read_text(encoding="utf-8"))
        assert state["lane_may_be_live"] is False
        assert state["lane_start_attempted"] is False
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)
def test_worker_handles_cancel_after_wait_without_signaling_an_exited_child(tmp_path):
    process_state = {"exit_status": None, "polls": 0}
    signal_pids = []

    class _Process:
        pid = 733

        def poll(self):
            process_state["polls"] += 1
            return process_state["exit_status"]

    class _Runner:
        def start(self, *_args, **_kwargs):
            return _Process()

    class _Signals:
        def terminate_group(self, pid):
            signal_pids.append(pid)

    worker = _worker(tmp_path, _Runner())
    worker.runner_identity = RunnerIdentity("runner", 1001, 1002, (1002,))
    worker.request = {
        "repo": "vbpub", "project": ".", "lane": "r0-r1", "extra_argv": [], "base": None,
    }
    repo = tmp_path / "repo"
    repo.mkdir()
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    worker.repo_root = repo
    worker.checkout_rel = ".fleetd-worktrees/controller-run-1"
    worker.checkout_fd = os.open(checkout, os.O_RDONLY | os.O_DIRECTORY)
    worker.job_dir.mkdir(parents=True)
    (worker.job_dir / "state.json").write_text(json.dumps({
        "schema": "fleet-host-job-state/1", "job_id": "controller-run-1", "state": "running",
        "guard": {"present": False}, "lane_mode": "host",
    }), encoding="utf-8")

    def helper(mode, *args, cwd_fd=None):
        assert mode == "prepare-event"
        executor_worker_module._prepare_event(cwd_fd, args[1])
        return CommandResult(0)

    worker._helper = helper
    worker.process_signals = _Signals()
    waits = []

    def waiter(duration):
        waits.append(duration)
        if len(waits) == 1:
            process_state["exit_status"] = 143
            worker._save_state(cancel_requested=True, cancel_reason="test cancel")

    worker.waiter = waiter
    try:
        assert worker._run_lane("run-gate", "host", {"runner_name": None}, None) == 143
        assert signal_pids == []
        assert process_state["polls"] == 2
        state = json.loads((worker.job_dir / "state.json").read_text(encoding="utf-8"))
        assert state["lane_process_status"] == 143
        assert state["lane_status_owner"] == "worker"
        assert "stop_signal" not in state
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)
@pytest.mark.parametrize("cancel", [False, True])
def test_worker_run_checks_out_runs_and_publishes_only_after_terminal_record(tmp_path, cancel):
    policy, empty_seed = _policy(tmp_path)
    seed_source = tmp_path / "seed-source"
    seed_source.mkdir()
    subprocess.run(["git", "init", "--quiet", "--initial-branch=main", str(seed_source)], check=True)
    (seed_source / "run-gate.toml").write_text(
        "[lanes.r0-r1]\nenvironment = 'host'\n\n[environments.host]\nmode = 'host'\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(seed_source), "add", "run-gate.toml"], check=True)
    subprocess.run([
        "git", "-C", str(seed_source), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "--quiet", "-m", "seed",
    ], check=True)
    commit = subprocess.run(
        ["git", "-C", str(seed_source), "rev-parse", "HEAD"], check=True, capture_output=True, text=True,
    ).stdout.strip()
    host_seed = policy.repo_roots["vbpub"] / commit
    shutil.copytree(seed_source, host_seed)

    request = _submit()
    request["sha"] = commit
    state_dir = tmp_path / "state"
    jobs_dir = state_dir / "jobs"
    jobs_dir.mkdir(parents=True)
    jobs_dir.chmod(0o700)
    job_dir = jobs_dir / request["job_id"]
    job_dir.mkdir(mode=0o700)
    (job_dir / "start.lock").touch(mode=0o600)
    digest = AgentAdmission.request_model(request).digest()
    (job_dir / "request.json").write_text(json.dumps({
        "schema": "fleet-host-job-request/1", "group": "controller",
        "digest": digest, "request": request,
    }), encoding="utf-8")
    (job_dir / "state.json").write_text(json.dumps({
        "schema": "fleet-host-job-state/1", "job_id": request["job_id"],
        "state": "running", "start_confirmed": True,
        "scope": f"fleetd-job-{request['job_id']}.scope",
        "guard": {"present": False, "reason": "thrashguard-absent"},
    }), encoding="utf-8")
    lock_fd = os.open(state_dir / "admission.lock", os.O_CREAT | os.O_RDWR, 0o600)
    lifecycle = []
    processes = []

    class _Process:
        pid = 734

        def __init__(self):
            self.terminated = False

        def poll(self):
            if cancel and not self.terminated:
                return None
            lifecycle.append("lane-exit")
            return 143 if cancel else 0

        def wait(self):
            return 0

    class _Runner:
        def __init__(self):
            self.artifact_attempts = 0

        def run(self, argv, *, cwd_fd, env, timeout=None):
            if argv[0] == "/usr/bin/run-gate":
                return CommandResult(0, b"run-gate 25.0.1\n", b"")
            if argv[0] == "git":
                answer = subprocess.run(argv, capture_output=True, check=False)
                return CommandResult(answer.returncode, answer.stdout, answer.stderr)
            if len(argv) > 6 and argv[1:4] == ["-I", "-P", "-c"]:
                return self._helper(argv, cwd_fd)
            return CommandResult(127, b"", b"unhandled fake runner command")

        def run_with_input(self, argv, *, cwd_fd, env, source_fd, timeout=None):
            return CommandResult(127, b"", b"unexpected input stream")

        def run_to_fd(self, argv, *, cwd_fd, env, output_fd):
            mode = next(value for value in argv if value in {"archive-artifacts", "archive-store"})
            if mode == "archive-artifacts":
                self.artifact_attempts += 1
                if self.artifact_attempts == 1:
                    return CommandResult(1, b"", b"transient archive helper failure")
            answer = self._helper(argv, cwd_fd)
            if mode == "archive-artifacts" and answer.returncode == 0:
                lifecycle.append("collect")
            return answer

        def _helper(self, argv, cwd_fd):
            helper_args = argv[6:]
            if (
                helper_args and helper_args[0] == "remove-tree" and len(helper_args) > 2
                and helper_args[2] == f".fleetd-worktrees/{request['job_id']}"
                and "lane-start" in lifecycle
            ):
                lifecycle.append("checkout-remove")
            saved = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
            stdout, stderr = io.StringIO(), io.StringIO()
            try:
                os.fchdir(cwd_fd)
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    status = worker_main(helper_args)
            finally:
                os.fchdir(saved)
                os.close(saved)
            return CommandResult(status, stdout.getvalue().encode(), stderr.getvalue().encode())

        def start(self, argv, *, cwd_fd, env, output_fd):
            lifecycle.append("lane-start")
            event_path = Path(argv[argv.index("--event-file") + 1])
            event_path.write_text(json.dumps({
                "api": "fleet-host-agent/1", "job_id": request["job_id"],
                "event": "lane-verdict", "lane": request["lane"],
                "verdict": "PASS", "exit_code": 0, "lane_exit_code": 0,
            }) + "\n", encoding="utf-8")
            os.write(output_fd, b"run-gate: lane 'r0-r1' verdict PASS; exit_code 0; log lane.log\n")
            process = _Process()
            processes.append(process)
            return process

    class _Docker:
        def __init__(self):
            self.queries = 0

        def job_containers(self, _job_id):
            self.queries += 1
            lifecycle.append("docker-query")
            if self.queries == 1:
                raise AdmissionError(75, "transient Docker query failure")
            return ()

        def inspect(self, _target):
            return None

        def remove_stopped(self, _container_id):
            return None

    runner = _Runner()
    docker = _Docker()
    waits = []

    class _Signals:
        def terminate_group(self, pid):
            assert pid == 734
            lifecycle.append("term")
            processes[0].terminated = True

    worker = None

    def waiter(duration):
        waits.append(duration)
        if cancel and not any(item == "cancel-request" for item in lifecycle):
            lifecycle.append("cancel-request")
            worker._save_state(cancel_requested=True, cancel_reason="operator request")

    worker = HostJobWorker(
        state_dir, policy, request["job_id"], lock_fd,
        user_resolver=lambda name: RunnerIdentity(name, os.getuid(), os.getgid(), (os.getgid(),), str(tmp_path)),
        runner_factory=lambda _identity: runner, docker=docker,
        pressure_reader=lambda _path: "some avg10=0.00 avg60=0.00 total=0\nfull avg10=0.00 avg60=0.00 total=0\n",
        waiter=waiter,
        run_gate_finder=lambda _name: "/usr/bin/run-gate",
        scope_process_reader=lambda _unit: (os.getpid(),),
        process_signals=_Signals(),
    )
    terminal_records = []

    def complete(details):
        assert not (job_dir / "result.json").exists()
        assert (job_dir / "result-pending.json").is_file()
        lifecycle.append("terminal-write")
        terminal_records.append(dict(details))
        fcntl.flock(worker.lock_fd, fcntl.LOCK_UN)
        os.close(worker.lock_fd)
        worker.lock_fd = -1

    worker._complete_admission = complete
    result_code = worker.run()
    expected_exit = 143 if cancel else 0
    assert result_code == expected_exit, (
        result_code,
        (job_dir / "state.json").read_text(encoding="utf-8"),
        (job_dir / "job.log").read_text(encoding="utf-8"),
    )
    state = json.loads((job_dir / "state.json").read_text(encoding="utf-8"))
    result = json.loads((job_dir / "result.json").read_text(encoding="utf-8"))
    expected_verdict = "CANCELLED" if cancel else "PASS"
    assert state["state"] == ("cancelled" if cancel else "done")
    assert state["verdict"] == result["verdict"] == expected_verdict
    assert terminal_records[0]["verdict"] == expected_verdict
    assert terminal_records[0]["result_sha256"] == hashlib.sha256((job_dir / "result.json").read_bytes()).hexdigest()
    assert runner.artifact_attempts == 2
    assert docker.queries >= 2
    assert waits.count(1) >= 2
    assert state["lane_mode"] == "host"
    assert state["lane_status_owner"] == "worker"
    assert lifecycle.index("lane-exit") < lifecycle.index("collect")
    assert lifecycle.index("collect") < lifecycle.index("checkout-remove")
    assert lifecycle.index("checkout-remove") < lifecycle.index("terminal-write")
    if cancel:
        assert lifecycle.index("cancel-request") < lifecycle.index("term")
        assert lifecycle.index("term") < lifecycle.index("lane-exit")
    assert not (policy.repo_roots["vbpub"] / ".fleetd-worktrees" / request["job_id"]).exists()


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
    class _ArchiveRunner:
        def run_to_fd(self, argv, *, cwd_fd, output_fd, **_kwargs):
            mode = next(value for value in argv if value in {"archive-artifacts", "archive-store"})
            helper_args = list(argv[argv.index(mode):])
            cwd_save = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
            stdout = io.StringIO()
            try:
                os.fchdir(cwd_fd)
                with contextlib.redirect_stdout(stdout):
                    status = worker_main(helper_args)
            finally:
                os.fchdir(cwd_save)
                os.close(cwd_save)
            return CommandResult(status, stdout.getvalue().encode("utf-8"), b"")

    checkout = tmp_path / "checkout"
    (checkout / ".assay").mkdir(parents=True)
    (checkout / ".run-gate").mkdir()
    (checkout / "store" / "nested").mkdir(parents=True)
    (checkout / ".assay" / "progress-x.jsonl").write_text('{"done":1}\n', encoding="utf-8")
    (checkout / ".run-gate" / "verdict-x.json").write_text('{"verdict":"PASS"}\n', encoding="utf-8")
    (checkout / "store" / "nested" / "payload.bin").write_bytes(b"payload")
    (checkout / "carry" / "subdir").mkdir(parents=True)
    (checkout / "carry" / "subdir" / "second.bin").write_bytes(b"second")
    worker = _worker(tmp_path, _ArchiveRunner())
    worker.job_dir.mkdir(parents=True)
    (worker.job_dir / "state.json").write_text(json.dumps({
        "schema": "fleet-host-job-state/1", "job_id": "controller-run-1", "state": "collecting",
    }), encoding="utf-8")
    worker.request = {"project": ".", "stores": {"collect": ["store", "carry"]}}
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
            assert set(archive.getnames()) >= {
                "store/nested/payload.bin", "carry/subdir/second.bin",
            }
    finally:
        os.close(worker.checkout_fd)
        os.close(worker.repo_root_fd)


def test_store_collection_rejects_symlinks_without_losing_artifacts(tmp_path):
    class _ArchiveRunner:
        def run_to_fd(self, argv, *, cwd_fd, output_fd, **_kwargs):
            mode = next(value for value in argv if value in {"archive-artifacts", "archive-store"})
            helper_args = list(argv[argv.index(mode):])
            cwd_save = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
            stdout = io.StringIO()
            try:
                os.fchdir(cwd_fd)
                with contextlib.redirect_stdout(stdout):
                    status = worker_main(helper_args)
            finally:
                os.fchdir(cwd_save)
                os.close(cwd_save)
            return CommandResult(status, stdout.getvalue().encode("utf-8"), b"")

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
        assert result["store"]["status"] == "none"
        assert result["store"]["stores"] == [{"path": "store", "status": "failed", "reason": "symlink"}]
        assert (worker.job_dir / "artifacts.tgz").is_file()
        assert not (worker.job_dir / "assay-store.tgz").exists()
        assert any("STORE-COLLECT-FAILED store symlink" in line for line in (worker.job_dir / "job.log").read_text().splitlines())
    finally:
        os.close(worker.checkout_fd)
        os.close(worker.repo_root_fd)


def test_worker_persists_start_observation_then_waits_for_lane_container_without_a_deadline(tmp_path):
    container_id = "a" * 64
    repo = tmp_path / "repo"
    repo.mkdir()
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "run-gate.toml").write_text(
        "[lanes.r0-r1]\nenvironment = 'tester'\n\n[environments.tester]\nmode = 'ephemeral'\nimage = 'tester:local'\n",
        encoding="utf-8",
    )
    expected_source = str(repo / ".fleetd-worktrees/controller-run-1")

    class _Docker:
        def __init__(self):
            self.calls = []

        def inspect(self, target):
            self.calls.append(target)
            return {
                "Id": container_id,
                "State": {"Running": len(self.calls) < 3},
                "Mounts": [{"Type": "bind", "Source": expected_source, "Destination": "/workspace"}],
                "Config": {"Labels": {
                    "run-gate.repo": "vbpub", "run-gate.project": ".", "run-gate.lane": "r0-r1",
                    "run-gate.commit": SHA, "run-gate.name-schema": "2",
                    "run-gate.started": "2026-11-27T00:00:00Z", "run-gate.job": "controller-run-1",
                }},
            }

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
    worker.request = {"repo": "vbpub", "project": ".", "sha": SHA, "lane": "r0-r1"}
    worker.checkout_fd = os.open(checkout, os.O_RDONLY | os.O_DIRECTORY)
    worker.job_dir.mkdir(parents=True)
    (worker.job_dir / "state.json").write_text(json.dumps({
        "schema": "fleet-host-job-state/1", "job_id": "controller-run-1", "state": "collecting",
        "lane_mode": "ephemeral", "runner_name": None,
    }), encoding="utf-8")
    worker.repo_root = repo
    worker.event_rel = ".fleetd-events/vbpub/controller-run-1.jsonl"
    worker.checkout_rel = ".fleetd-worktrees/controller-run-1"
    worker.docker = docker
    waits = []
    worker.waiter = waits.append
    try:
        events = worker._wait_for_lane_containers()
        assert len(events) == 1
        assert docker.calls == [container_id, container_id, container_id]
        assert waits == [1]
        state = json.loads((worker.job_dir / "state.json").read_text(encoding="utf-8"))
        observation = state["lane_event_observations"][0]
        assert observation["binding"]["id"] == container_id
        assert observation["binding"]["checkout_mount"] == expected_source
    finally:
        os.close(worker.checkout_fd)
        os.close(worker.repo_root_fd)


def test_worker_records_gone_container_at_lane_started_observation_as_error(tmp_path):
    container_id = "d" * 64

    class _Docker:
        def inspect(self, target):
            assert target == container_id
            return None

    worker = _worker(tmp_path, _VersionRunner({}))
    worker.runner_identity = RunnerIdentity("test-runner", os.getuid(), os.getgid(), (os.getgid(),))
    worker.request = {"repo": "vbpub", "project": ".", "sha": SHA, "lane": "r0-r1"}
    worker.job_dir.mkdir(parents=True)
    (worker.job_dir / "state.json").write_text(json.dumps({
        "schema": "fleet-host-job-state/1", "job_id": "controller-run-1", "state": "running",
        "lane_mode": "ephemeral", "runner_name": None,
    }), encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "run-gate.toml").write_text(
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
    worker.checkout_fd = os.open(checkout, os.O_RDONLY | os.O_DIRECTORY)
    worker.checkout_rel = ".fleetd-worktrees/controller-run-1"
    worker.repo_root = repo
    worker.event_rel = ".fleetd-events/vbpub/controller-run-1.jsonl"
    worker.docker = _Docker()
    try:
        worker._follow_lane_events(final=False)
        state = json.loads((worker.job_dir / "state.json").read_text(encoding="utf-8"))
        assert "gone at observation" in worker.lane_error
        assert state["lane_event_observations"][0]["error"] == worker.lane_error
        assert worker.event_cursor == 1
    finally:
        os.close(worker.checkout_fd)
        os.close(worker.repo_root_fd)


@pytest.mark.parametrize("mode, expected_status", [
    ("host", "no-container"),
    ("ephemeral", "inspection-error"),
])
def test_event_follower_records_host_without_container_and_refuses_container_without_id(
    tmp_path, mode, expected_status,
):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path, _VersionRunner({}))
    worker.request.update(sha=SHA, lane="r0-r1")
    worker._save_state(lane_mode=mode)
    event = {
        "api": "fleet-host-agent/1", "job_id": worker.job_id,
        "event": "lane-started", "lane": "r0-r1", "container_id": None,
        "image_id": None, "adopted": False,
    }
    worker._read_events = lambda **_kwargs: [event]
    try:
        worker._follow_lane_events(final=False)
        state = worker._state()
        observation = state["lane_event_observations"][0]
        binding = worker._bind_container_events([event], expected_sha=SHA)[0]
        assert binding["status"] == expected_status
        if mode == "host":
            assert observation["binding"]["status"] == expected_status
            assert worker.lane_error is None
        else:
            assert "no full lane-started id" in observation["error"]
            assert worker.lane_error == observation["error"]
        assert worker.event_cursor == 1
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize("exit_code, expected_error", [
    (ExitCode.QUEUED_WITHDRAWN, None),
    (ExitCode.IDENTITY_OR_TRANSPORT, "event transport damaged"),
])
def test_event_follower_retries_transport_failures_and_records_permanent_errors(
    tmp_path, exit_code, expected_error,
):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path, _VersionRunner({}))
    worker._read_events = lambda **_kwargs: (_ for _ in ()).throw(
        AdmissionError(exit_code, "event transport damaged")
    )
    try:
        worker._follow_lane_events(final=False)
        assert worker.lane_error == expected_error
        assert worker.event_cursor == 0
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize("cancel", [True, False])
def test_exec_cancel_or_missing_verdict_cannot_release_the_job(tmp_path, cancel):
    class StopRetry(RuntimeError):
        pass

    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path)
    worker._save_state(lane_mode="exec", runner_name="persistent-runner", cancel_requested=cancel)
    worker._follow_lane_events = lambda **_kwargs: None
    worker._read_events = lambda **_kwargs: []
    worker._ensure_scope_is_only_worker = lambda: pytest.fail("exec proof failure must stop before teardown")
    worker.waiter = lambda _seconds: (_ for _ in ()).throw(StopRetry("stop retry loop in test"))
    with pytest.raises(StopRetry):
        worker._finalize_lane(0)
    state = worker._state()
    expected = (
        "exec-cancel-cannot-prove-runner-process-stopped" if cancel
        else "exec-end-without-run-gate-verdict-cannot-prove-runner-process-stopped"
    )
    assert state["state"] == "stopping" and expected in state["reason"]
    assert not (job_dir / "terminal-pending.json").exists()
    assert not (job_dir / "result.json").exists()
    for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
        if fd >= 0:
            os.close(fd)


def test_worker_requires_exact_job_label_and_stopped_full_ids_before_release(tmp_path):
    container_id = "e" * 64

    class _Docker:
        def __init__(self):
            self.removed = []
            self.enumerations = 0

        def job_containers(self, job_id):
            assert job_id == "controller-run-1"
            self.enumerations += 1
            return (container_id,) if self.enumerations == 1 else ()

        def inspect(self, target):
            assert target == container_id
            return {
                "Id": container_id,
                "Config": {"Labels": {"run-gate.job": "controller-run-1"}},
                "State": {"Running": False},
            }

        def remove_stopped(self, target):
            self.removed.append(target)

    worker = _worker(tmp_path, _VersionRunner({}))
    worker.job_dir.mkdir(parents=True)
    (worker.job_dir / "state.json").write_text(json.dumps({
        "schema": "fleet-host-job-state/1", "job_id": "controller-run-1", "state": "collecting",
    }), encoding="utf-8")
    docker = _Docker()
    worker.docker = docker
    waits = []
    worker.waiter = waits.append
    try:
        worker._ensure_job_containers_stopped(remove=True)
        assert docker.removed == [container_id]
        assert docker.enumerations == 2
        assert waits == [1]
    finally:
        os.close(worker.repo_root_fd)


def test_teardown_reinspects_exact_container_until_stopped_then_removes_it(tmp_path):
    container_id = "c" * 64
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path, _VersionRunner({}))
    expected_source = str(worker.repo_root / worker.checkout_rel)
    labels = {
        "run-gate.repo": "vbpub", "run-gate.project": ".", "run-gate.lane": "r0-r1",
        "run-gate.commit": SHA, "run-gate.name-schema": "2",
        "run-gate.started": "2026-11-27T00:00:00Z", "run-gate.job": worker.job_id,
    }

    class _Docker:
        def __init__(self):
            self.states = [True, False]
            self.inspected = []
            self.removed = []

        def inspect(self, target):
            assert target == container_id
            self.inspected.append(target)
            return {
                "Id": container_id,
                "Mounts": [{"Type": "bind", "Source": expected_source, "Destination": "/workspace"}],
                "Config": {"Labels": labels},
                "State": {"Running": self.states.pop(0)},
            }

        def remove_stopped(self, target):
            self.removed.append(target)

    docker = _Docker()
    worker.docker = docker
    waits = []
    worker.waiter = waits.append
    try:
        worker._teardown_containers([{
            "id": container_id, "lane": "r0-r1", "mode": "ephemeral", "name": None,
        }], expected_sha=SHA)
        assert docker.inspected == [container_id, container_id]
        assert docker.removed == [container_id]
        assert waits == [1]
        assert worker._state()["live_containers"] == []
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


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
        result = json.loads((worker.job_dir / "result-pending.json").read_text(encoding="utf-8"))
        assert not (worker.job_dir / "result.json").exists()
        assert result["checkout_sha"] == SHA
        assert result["binding"] == {"status": "verified", "reasons": []}

        forged = dict(event, exit_code=1)
        refused = worker._bind_verdict([forged], 0, [])
        assert refused["verdict"] == "ERROR"
        assert refused["exit_code"] == 3
        result = json.loads((worker.job_dir / "result-pending.json").read_text(encoding="utf-8"))
        assert not (worker.job_dir / "result.json").exists()
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


def test_event_reader_ignores_an_incomplete_tail_while_running_but_refuses_it_at_end(tmp_path):
    worker = _worker(tmp_path, _VersionRunner({}))
    worker.runner_identity = RunnerIdentity("test-runner", os.getuid(), os.getgid(), (os.getgid(),))
    repo = tmp_path / "repo"
    repo.mkdir()
    event_dir = tmp_path / ".fleetd-events" / "vbpub"
    event_dir.mkdir(parents=True)
    event_path = event_dir / "controller-run-1.jsonl"
    complete = json.dumps({
        "api": "fleet-host-agent/1", "job_id": "controller-run-1", "event": "lane-verdict",
        "lane": "r0-r1", "verdict": "PASS", "exit_code": 0, "lane_exit_code": 0,
    }) + "\n"
    event_path.write_text(complete + '{"api":"fleet-host-agent/1","event":', encoding="utf-8")
    event_path.chmod(0o600)
    worker.repo_root = repo
    worker.event_rel = ".fleetd-events/vbpub/controller-run-1.jsonl"
    try:
        assert len(worker._read_events(final=False)) == 1
        with pytest.raises(AdmissionError, match="event line 2 is incomplete"):
            worker._read_events(final=True)
    finally:
        os.close(worker.repo_root_fd)


@pytest.mark.parametrize("damage, expected", [
    ("broken-config", "run-gate-config-invalid-after-lane"),
    ("assay-symlink", "assay-directory-symlink-after-lane"),
])
def test_lane_config_and_assay_paths_changed_by_lane_fail_closed(tmp_path, damage, expected):
    worker, _runner, _job_dir, checkout = _ready_worker(tmp_path, _VersionRunner({}))
    worker.request.update(project=".", lane="r0-r1")
    worker._save_state(lane_mode="host")
    config = checkout / "run-gate.toml"
    config.write_text(
        "[lanes.r0-r1]\nenvironment = 'host'\n\n[environments.host]\nmode = 'host'\n",
        encoding="utf-8",
    )
    if damage == "broken-config":
        config.write_text("[lanes.r0-r1\n", encoding="utf-8")
    else:
        (checkout / ".assay-link-target").mkdir()
        (checkout / ".assay").symlink_to(checkout / ".assay-link-target", target_is_directory=True)
    worker._validate_run_gate_after_run()
    assert worker.lane_error is not None and expected in worker.lane_error
    for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
        if fd >= 0:
            os.close(fd)


def test_final_event_parse_error_is_terminal_error_after_stop_proofs(tmp_path):
    worker, _runner, job_dir, checkout = _ready_worker(tmp_path, _VersionRunner({}))
    worker.request.update(sha=SHA, project=".", lane="r0-r1")
    worker._save_state(
        lane_mode="host", lane_process_status=0, lane_may_be_live=False,
        checkout={"ready": True, "sha": SHA},
    )
    (checkout / "run-gate.toml").write_text(
        "[lanes.r0-r1]\nenvironment = 'host'\n\n[environments.host]\nmode = 'host'\n",
        encoding="utf-8",
    )
    (job_dir / "job.log").write_text(
        f"2026-11-27T00:00:00Z checkout {worker.repo_root / worker.checkout_rel} at {SHA}\n",
        encoding="utf-8",
    )
    (job_dir / "lane.log").write_text(
        "run-gate: lane 'r0-r1' verdict PASS; exit_code 0; log lane.log\n",
        encoding="utf-8",
    )
    worker._follow_lane_events = lambda **_kwargs: None
    worker._read_events = lambda **_kwargs: (_ for _ in ()).throw(
        AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "Run-Gate event line 2 is incomplete or too large")
    )
    worker._ensure_scope_is_only_worker = lambda: None
    worker._ensure_job_containers_stopped = lambda **_kwargs: None
    worker._collect = lambda: worker._save_state(collection={"collected_at": "2026-11-27T00:00:00Z"})
    worker._teardown_containers = lambda *_args, **_kwargs: None
    worker._remove_checkout = lambda: None
    terminal_writes = []

    def publish(terminal, *, final_state, reason):
        assert (job_dir / "terminal-pending.json").is_file()
        assert not (job_dir / "result.json").exists()
        terminal_writes.append((dict(terminal), final_state, reason))

    worker._publish_terminal = publish
    try:
        assert worker._finalize_lane(0) == int(ExitCode.IDENTITY_OR_TRANSPORT)
        assert terminal_writes[0][0]["verdict"] == "ERROR"
        assert terminal_writes[0][1] == "failed"
        assert any("event line 2 is incomplete" in item for item in terminal_writes[0][0]["binding_reasons"])
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


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
    worker.request = {"repo": "vbpub", "project": "vbpub", "sha": SHA, "lane": "r0-r1"}
    worker.job_dir.mkdir(parents=True)
    (worker.job_dir / "state.json").write_text(json.dumps({
        "schema": "fleet-host-job-state/1", "job_id": "controller-run-1", "state": "running",
        "lane_mode": "ephemeral", "runner_name": None,
    }), encoding="utf-8")
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


def test_exec_binding_checks_persistent_runner_identity_and_checkout_mount(tmp_path):
    container_id = "9" * 64
    worker, _runner, _job_dir, checkout = _ready_worker(tmp_path, _VersionRunner({}))
    (checkout / "run-gate.toml").write_text(
        "[lanes.r0-r1]\nenvironment = 'runner'\n\n"
        "[environments.runner]\nmode = 'exec'\ncontainer_name = 'persistent-runner'\n",
        encoding="utf-8",
    )
    worker._save_state(lane_mode="exec", runner_name="persistent-runner")
    expected_source = str(worker.repo_root / worker.checkout_rel)
    answer = {
        "Id": container_id,
        "Name": "/persistent-runner",
        "State": {"Running": True},
        "Mounts": [{"Type": "bind", "Source": expected_source, "Destination": "/workspace"}],
    }

    class _Docker:
        def __init__(self):
            self.calls = []

        def inspect(self, target):
            self.calls.append(target)
            return answer

    docker = _Docker()
    worker.docker = docker
    event = {"event": "lane-started", "lane": "r0-r1", "container_id": container_id}
    try:
        binding = worker._inspect_container_binding(event, expected_sha=SHA)
        assert binding["mode"] == "exec" and binding["name"] == "persistent-runner"
        assert docker.calls == [container_id, "persistent-runner"]
        assert worker._container_mode("r0-r1.shard1of2") == ("exec", "persistent-runner")

        answer["Name"] = "/different-runner"
        with pytest.raises(AdmissionError, match="does not match the persistent runner name"):
            worker._inspect_container_binding(event, expected_sha=SHA)
        answer["Name"] = "/persistent-runner"
        answer["Mounts"] = [{"Type": "bind", "Source": str(tmp_path / "other"), "Destination": "/workspace"}]
        with pytest.raises(AdmissionError, match="does not mount this job's checkout"):
            worker._inspect_container_binding(event, expected_sha=SHA)
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)

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


@pytest.mark.parametrize("mode", [
    "prepare", "prepare-event", "write-file", "extract-seed", "remove-tree",
    "archive-artifacts", "archive-store",
])
def test_worker_main_dispatches_every_helper_mode(mode, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    output_fd = -1
    input_fd = -1
    saved_stdin = -1
    try:
        if mode == "prepare":
            args = [mode, "--relative", "made/nested"]
            expected = tmp_path / "made" / "nested"
        elif mode == "prepare-event":
            args = [mode, "--relative", ".fleetd-events/vbpub/job.jsonl"]
            expected = tmp_path / ".fleetd-events" / "vbpub" / "job.jsonl"
        elif mode == "write-file":
            body = b"helper input"
            input_fd = _pipe_with(body)
            saved_stdin = os.dup(0)
            os.dup2(input_fd, 0)
            args = [mode, "--relative", "stage.bin", "--sha256", hashlib.sha256(body).hexdigest(),
                    "--bytes", str(len(body))]
            expected = tmp_path / "stage.bin"
        elif mode == "extract-seed":
            body = _seed_archive("store/value.txt", b"seeded")
            input_fd = _pipe_with(body)
            saved_stdin = os.dup(0)
            os.dup2(input_fd, 0)
            args = [mode, "--relative", "store", "--sha256", hashlib.sha256(body).hexdigest(),
                    "--bytes", str(len(body)), "--max-bytes", "1024"]
            expected = tmp_path / "store" / "value.txt"
        elif mode == "remove-tree":
            expected = tmp_path / "remove-me"
            expected.mkdir()
            (expected / "file").write_text("remove", encoding="utf-8")
            args = [mode, "--relative", "remove-me"]
        elif mode == "archive-artifacts":
            (tmp_path / ".assay").mkdir()
            (tmp_path / ".assay" / "verdict.json").write_text("{}", encoding="utf-8")
            output_fd = os.open(tmp_path / "out.tgz", os.O_CREAT | os.O_RDWR, 0o600)
            args = [mode, "--output-fd", str(output_fd)]
            expected = tmp_path / "out.tgz"
        else:
            (tmp_path / "store").mkdir()
            (tmp_path / "store" / "value").write_text("payload", encoding="utf-8")
            output_fd = os.open(tmp_path / "out.tgz", os.O_CREAT | os.O_RDWR, 0o600)
            args = [mode, "--relative", "store", "--output-fd", str(output_fd)]
            expected = tmp_path / "out.tgz"

        assert worker_main(args) == 0
        if mode == "prepare":
            assert expected.is_dir()
            assert expected.stat().st_mode & 0o777 == 0o755
        elif mode == "prepare-event":
            assert expected.is_file()
            assert expected.stat().st_mode & 0o777 == 0o600
        elif mode == "write-file":
            assert expected.read_bytes() == b"helper input"
        elif mode == "extract-seed":
            assert expected.read_bytes() == b"seeded"
        elif mode == "remove-tree":
            assert not expected.exists()
        else:
            assert os.fstat(output_fd).st_size > 0
            assert capsys.readouterr().out
    finally:
        if saved_stdin >= 0:
            os.dup2(saved_stdin, 0)
            os.close(saved_stdin)
        if input_fd >= 0:
            os.close(input_fd)
        if output_fd >= 0:
            os.close(output_fd)


def test_worker_main_closes_unknown_commands_and_helper_path_errors(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert worker_main(["not-a-mode"]) == int(ExitCode.REFUSAL)
    assert "expected worker or one of" in capsys.readouterr().err
    assert worker_main(["remove-tree", "--relative", "../outside"]) == int(ExitCode.RUNNER_FAILURE)
    assert "fleetd-executor-worker: target file path is unsafe" in capsys.readouterr().err


def test_worker_main_refuses_a_missing_inherited_lock_before_handshake(tmp_path, monkeypatch, capsys):
    policy, _checkout = _policy(tmp_path)
    monkeypatch.setattr(executor_worker_module, "load_agent_policy", lambda _path: policy)
    read_fd, write_fd = os.pipe()
    try:
        result = worker_main([
            "worker", "--state-dir", str(tmp_path / "state"), "--policy", str(tmp_path / "agent.toml"),
            "--job-id", "controller-run-1", "--lock-fd", "-1", "--handshake-fd", str(write_fd),
        ])
        assert result == int(ExitCode.RUNNER_FAILURE)
        os.close(write_fd)
        write_fd = -1
        assert os.read(read_fd, 64) == b""
        assert "Bad file descriptor" in capsys.readouterr().err
    finally:
        if write_fd >= 0:
            os.close(write_fd)
        os.close(read_fd)


def test_helper_argv_uses_isolated_import_path_with_hostile_cwd_modules(tmp_path):
    hostile = tmp_path / "hostile"
    hostile.mkdir()
    (hostile / "json.py").write_text("raise RuntimeError('hostile json imported')\n", encoding="utf-8")
    package = hostile / "fleetd"
    package.mkdir()
    (package / "__init__.py").write_text("raise RuntimeError('hostile fleetd imported')\n", encoding="utf-8")
    (package / "executor_worker.py").write_text("raise RuntimeError('hostile helper imported')\n", encoding="utf-8")

    worker = _worker(tmp_path, _VersionRunner({}))
    argv = worker._helper_argv("prepare", "--relative", "isolated")
    try:
        answer = subprocess.run(argv, cwd=hostile, capture_output=True, check=False)
    finally:
        os.close(worker.repo_root_fd)

    assert answer.returncode == 0, answer.stderr.decode("utf-8", "replace")
    assert (hostile / "isolated").is_dir()
    assert "-I" in argv and "-P" in argv


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


def test_seed_unpack_refuses_size_duplicate_file_target_and_symlink_target(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)

    def reject(body, fragment, *, target="store", expected_size=None, max_bytes=1024):
        input_fd = _pipe_with(body)
        try:
            with pytest.raises(ValueError, match=fragment):
                _extract_seed(
                    root_fd, input_fd, target, hashlib.sha256(body).hexdigest(),
                    len(body) if expected_size is None else expected_size, max_bytes,
                )
        finally:
            os.close(input_fd)

    try:
        body = _seed_archive("store/value", b"value")
        reject(body, "size", expected_size=len(body) + 1)
        reject(body, "too-large", max_bytes=4)

        duplicate = io.BytesIO()
        with tarfile.open(fileobj=duplicate, mode="w:gz") as archive:
            for contents in (b"first", b"second"):
                member = tarfile.TarInfo("store/value")
                member.size = len(contents)
                archive.addfile(member, io.BytesIO(contents))
        reject(duplicate.getvalue(), "duplicate-member")

        file_at_target = _seed_archive("store", b"not a directory")
        reject(file_at_target, "target-is-not-directory")

        outside = tmp_path / "outside"
        outside.mkdir()
        (root / "store").symlink_to(outside, target_is_directory=True)
        reject(body, "unsafe-existing-target")
        assert list(outside.iterdir()) == []
    finally:
        os.close(root_fd)


def test_beneath_directory_helpers_refuse_unsafe_paths_and_remove_without_following_links(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for relative in ("/absolute", "a/../b", "a\\b"):
            with pytest.raises(ValueError):
                executor_worker_module._mkdir_beneath(root_fd, relative)
        for relative in ("/absolute", "../outside", "a\\b"):
            with pytest.raises(ValueError):
                executor_worker_module._open_parent(root_fd, relative)

        for _ in range(2):
            nested_fd = executor_worker_module._mkdir_beneath(root_fd, "made/nested")
            os.close(nested_fd)
        parent_fd, leaf = executor_worker_module._open_parent(root_fd, "leaf")
        assert leaf == "leaf"
        os.close(parent_fd)

        (root / "tree" / "nested").mkdir(parents=True)
        (root / "tree" / "nested" / "file").write_text("payload", encoding="utf-8")
        outside = tmp_path / "outside-file"
        outside.write_text("keep", encoding="utf-8")
        (root / "link").symlink_to(outside)
        executor_worker_module._remove_tree_beneath(root_fd, "missing")
        executor_worker_module._remove_tree_beneath(root_fd, "tree")
        executor_worker_module._remove_tree_beneath(root_fd, "link")
        assert not (root / "tree").exists()
        assert not (root / "link").exists()
        assert outside.read_text(encoding="utf-8") == "keep"
    finally:
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


def test_subprocess_runner_adapters_cover_success_timeout_and_broken_pipe(monkeypatch, tmp_path):
    class _Stdin:
        def __init__(self, *, broken=False):
            self.broken = broken
            self.data = bytearray()
            self.closed = False

        def write(self, value):
            if self.broken:
                raise BrokenPipeError("closed fake input")
            self.data.extend(value)

        def close(self):
            self.closed = True

    class _Process:
        def __init__(self, responses, *, broken=False):
            self.responses = list(responses)
            self.returncode = 7
            self.stdin = _Stdin(broken=broken)
            self.kills = 0
            self.communicate_calls = []

        def communicate(self, timeout=None):
            self.communicate_calls.append(timeout)
            answer = self.responses.pop(0)
            if isinstance(answer, BaseException):
                raise answer
            return answer

        def kill(self):
            self.kills += 1

    calls = []
    current = []

    def popen(argv, **kwargs):
        calls.append((list(argv), kwargs))
        return current.pop(0)

    runner = SubprocessRunnerCommands(RunnerIdentity("runner", 1001, 1002, (1002, 44)), popen=popen)
    cwd_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    output_fd = os.open(tmp_path / "output", os.O_CREAT | os.O_RDWR, 0o600)
    source_fd = _pipe_with(b"streamed input")
    try:
        ok = _Process([(b"out", b"err")])
        current.append(ok)
        assert runner.run(["cmd"], cwd_fd=cwd_fd, env={"A": "B"}, timeout=3) == CommandResult(7, b"out", b"err")
        assert calls[-1][1]["pass_fds"] == (cwd_fd,)
        assert calls[-1][1]["close_fds"] is True

        timed_out = _Process([subprocess.TimeoutExpired(["cmd"], 1), (b"after", b"term")])
        current.append(timed_out)
        with pytest.raises(subprocess.TimeoutExpired):
            runner.run(["cmd"], cwd_fd=cwd_fd, env={})
        assert timed_out.kills == 1
        assert timed_out.communicate_calls == [None, None]

        streamed = _Process([(b"ignored", b"stream-error")])
        current.append(streamed)
        streamed_stdin = streamed.stdin
        assert runner.run_with_input(
            ["cmd"], cwd_fd=cwd_fd, env={}, source_fd=source_fd,
        ) == CommandResult(7, b"", b"stream-error")
        assert bytes(streamed_stdin.data) == b"streamed input"
        assert streamed_stdin.closed
        os.close(source_fd)
        source_fd = -1

        broken_source = _pipe_with(b"ignored after broken pipe")
        broken = _Process([(b"", b"broken-pipe-stderr")], broken=True)
        broken_stdin = broken.stdin
        current.append(broken)
        assert runner.run_with_input(
            ["cmd"], cwd_fd=cwd_fd, env={}, source_fd=broken_source,
        ) == CommandResult(7, b"", b"broken-pipe-stderr")
        assert broken_stdin.closed
        os.close(broken_source)

        streamed_timeout = _Process([subprocess.TimeoutExpired(["cmd"], 2), (b"", b"")])
        current.append(streamed_timeout)
        source_again = _pipe_with(b"one chunk")
        with pytest.raises(subprocess.TimeoutExpired):
            runner.run_with_input(["cmd"], cwd_fd=cwd_fd, env={}, source_fd=source_again)
        os.close(source_again)
        assert streamed_timeout.kills == 1

        fd_process = _Process([(b"fd-output", b"fd-error")])
        current.append(fd_process)
        assert runner.run_to_fd(
            ["archive"], cwd_fd=cwd_fd, env={}, output_fd=output_fd,
        ) == CommandResult(7, b"fd-output", b"fd-error")
        assert calls[-1][1]["pass_fds"] == (cwd_fd, output_fd)

        started = _Process([])
        current.append(started)
        assert runner.start(["lane"], cwd_fd=cwd_fd, env={}, output_fd=output_fd) is started
        assert calls[-1][1]["start_new_session"] is True
    finally:
        if source_fd >= 0:
            os.close(source_fd)
        os.close(output_fd)
        os.close(cwd_fd)


def test_docker_client_covers_inspection_passwd_query_and_remove_refusals():
    container_id = "d" * 64

    def client_with(result):
        return DockerClient(2, run=lambda _argv, **_kwargs: result)

    with pytest.raises(AdmissionError, match="Docker query failed"):
        DockerClient(2, run=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("offline"))).image("img")
    with pytest.raises(AdmissionError, match="not present"):
        client_with(subprocess.CompletedProcess([], 1, b"", b"missing")).image("img")
    for output in (b"not-json", b"[]", b"[1]"):
        with pytest.raises(AdmissionError, match="image inspection"):
            client_with(subprocess.CompletedProcess([], 0, output, b"")).image("img")
    with pytest.raises(AdmissionError, match="unreadable image"):
        client_with(subprocess.CompletedProcess([], 0, b"\xff", b"")).image("img")
    assert client_with(subprocess.CompletedProcess([], 0, b'[{"Id":"image"}]', b"")).image("img")["Id"] == "image"

    with pytest.raises(AdmissionError, match="no passwd entry"):
        client_with(subprocess.CompletedProcess([], 1, b"", b"")).check_passwd("img", "1001", "job")
    for passwd in (b"bad", b"x:x:uid:gid:x:x:x\n", b"x:x:1002:1001:x:x:x\n"):
        with pytest.raises(AdmissionError, match="passwd"):
            client_with(subprocess.CompletedProcess([], 0, passwd, b"")).check_passwd(
                "img", "1001", "job", expected_gid=1001,
            )
    client_with(subprocess.CompletedProcess([], 0, b"x:x:1001:1001:x:x:x\n", b"")).check_passwd(
        "img", "1001", "a job", expected_gid=1001,
    )

    with pytest.raises(AdmissionError, match="inspect.*failed"):
        client_with(subprocess.CompletedProcess([], 1, b"", b"permission denied")).inspect(container_id)
    for output in (b"broken", b"[]", b"[null]"):
        with pytest.raises(AdmissionError, match="inspect"):
            client_with(subprocess.CompletedProcess([], 0, output, b"")).inspect(container_id)
    assert client_with(subprocess.CompletedProcess([], 0, b'[{"Id":"' + container_id.encode() + b'"}]', b"")).inspect(container_id)
    with pytest.raises(AdmissionError, match="full Docker id"):
        client_with(subprocess.CompletedProcess([], 0, b"", b"")).remove_stopped("short")
    with pytest.raises(AdmissionError, match="could not remove"):
        client_with(subprocess.CompletedProcess([], 1, b"", b"still running")).remove_stopped(container_id)
    client_with(subprocess.CompletedProcess([], 0, b"", b"")).remove_stopped(container_id)

    docker = client_with(subprocess.CompletedProcess([], 0, f"{container_id}\n".encode(), b""))
    with pytest.raises(AdmissionError, match="safe job id"):
        docker.job_containers("../unsafe")
    with pytest.raises(AdmissionError, match="query failed"):
        client_with(subprocess.CompletedProcess([], 1, b"", b"denied")).job_containers("safe-job")
    for output, reason in ((b"\xff", "non-ASCII"), (b"short\n", "non-full"),
                           (f"{container_id}\n{container_id}\n".encode(), "duplicate")):
        with pytest.raises(AdmissionError, match=reason):
            client_with(subprocess.CompletedProcess([], 0, output, b"")).job_containers("safe-job")


def test_archive_filesystem_and_config_helpers_cover_safe_and_refusal_paths(tmp_path, monkeypatch):
    root = tmp_path / "tree"
    root.mkdir()
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert executor_worker_module._tree_entries(root_fd, "absent", reject_special=True) == ([], "absent")
        (root / ".assay").mkdir()
        (root / ".assay" / "verdict.json").write_text("{}", encoding="utf-8")
        (root / ".assay" / "other.txt").write_text("skip", encoding="utf-8")
        (root / ".assay" / "big.log").write_bytes(b"z" * executor_worker_module._ARTIFACT_MAX_BYTES)
        (root / ".assay" / "nested").mkdir()
        (root / ".assay" / "nested" / "progress-one.jsonl").write_text("{}\n", encoding="utf-8")
        assert executor_worker_module._artifact_filename("verdict.json")
        assert executor_worker_module._artifact_filename("x-progress.jsonl")
        assert executor_worker_module._artifact_filename("coverage-data.json")
        assert executor_worker_module._artifact_filename("lane.log")
        assert executor_worker_module._artifact_filename("junit.xml")
        assert not executor_worker_module._artifact_filename("other.txt")

        entries, reason = executor_worker_module._tree_entries(root_fd, ".assay", reject_special=True)
        assert reason is None and any(path.endswith("verdict.json") for path, _directory, _size in entries)
        os.symlink("verdict.json", root / ".assay" / "link")
        assert executor_worker_module._tree_entries(root_fd, ".assay", reject_special=True) == ([], "symlink")
        tolerant, tolerant_reason = executor_worker_module._tree_entries(root_fd, ".assay", reject_special=False)
        assert tolerant_reason is None and not any(path.endswith("/link") for path, _d, _s in tolerant)

        artifact_fd = os.open(root / "artifacts.tgz", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            details = executor_worker_module._archive_artifacts(root_fd, artifact_fd)
            assert details["files"] == 2
            assert executor_worker_module._verify_archive_fd(artifact_fd, kind="artifacts") > 0
        finally:
            os.close(artifact_fd)

        (root / "store").mkdir()
        (root / "store" / "value").write_bytes(b"payload")
        (root / "store-with-link").mkdir()
        (root / "store-with-link" / "link").symlink_to(root / "store" / "value")
        monkeypatch.setattr(executor_worker_module, "_STORE_MAX_BYTES", 5)
        store_fd = os.open(root / "store.tgz", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            collected = executor_worker_module._archive_store(
                root_fd, store_fd, ["../bad", "missing", "store-with-link", "store"],
            )
            assert collected["status"] == "none"
            assert [item["reason"] for item in collected["stores"]] == ["invalid-path", None, "symlink", "7"]
            assert executor_worker_module._verify_archive_fd(store_fd, kind="store", store_path=["store"]) > 0
        finally:
            os.close(store_fd)

        monkeypatch.setattr(executor_worker_module, "_write_archive", lambda *_args: (_ for _ in ()).throw(tarfile.TarError("bad pack")))
        monkeypatch.setattr(executor_worker_module, "_STORE_MAX_BYTES", 100)
        failed_fd = os.open(root / "failed.tgz", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            failed = executor_worker_module._archive_store(root_fd, failed_fd, ["store"])
            assert failed["status"] == "failed" and failed["stores"][0]["reason"] == "pack-failed"
        finally:
            os.close(failed_fd)
    finally:
        os.close(root_fd)

    with pytest.raises(AdmissionError, match="not a regular file"):
        directory_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            executor_worker_module._verify_archive_fd(directory_fd, kind="artifacts")
        finally:
            os.close(directory_fd)
    with pytest.raises(AdmissionError, match="unreadable"):
        corrupt = os.open(tmp_path / "corrupt.tgz", os.O_CREAT | os.O_RDWR, 0o600)
        os.write(corrupt, b"not tar")
        try:
            executor_worker_module._verify_archive_fd(corrupt, kind="store", store_path="store")
        finally:
            os.close(corrupt)

    config_root = tmp_path / "config"
    config_root.mkdir()
    config_fd = os.open(config_root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert executor_worker_module._parse_toml_fd(config_fd, "missing.toml") is None
        (config_root / "run-gate.root.toml").write_text(
            "[lanes.shared]\nenvironment='base'\n[environments.base]\nmode='host'\nvalue=1\n",
            encoding="utf-8",
        )
        (config_root / "project").mkdir()
        (config_root / "project" / "run-gate.toml").write_text(
            "[lanes.shared]\nenvironment='project'\n[environments.project]\nmode='ephemeral'\nvalue=2\n",
            encoding="utf-8",
        )
        lane, env = executor_worker_module._lane_environment(config_fd, "project", "shared")
        assert lane["environment"] == "project" and env == {"mode": "ephemeral", "value": 2}
        lane, env = executor_worker_module._lane_environment(config_fd, ".", "shared")
        assert env["mode"] == "host"
        with pytest.raises(AdmissionError, match="not declared"):
            executor_worker_module._lane_environment(config_fd, ".", "missing")
        (config_root / "run-gate.toml").write_text("[lanes.bad]\nenvironment=''\n", encoding="utf-8")
        with pytest.raises(AdmissionError, match="no environment"):
            executor_worker_module._lane_environment(config_fd, ".", "bad")
        (config_root / "run-gate.toml").write_text(
            "[lanes.bad]\nenvironment='bad'\n[environments.bad]\nmode='none'\n", encoding="utf-8",
        )
        with pytest.raises(AdmissionError, match="no valid mode"):
            executor_worker_module._lane_environment(config_fd, ".", "bad")
        assert executor_worker_module._exec_container_name(config_fd, "host", {"container_name": "runner"}) == "runner"
        (config_root / "ciu.global.toml").write_text(
            "[deploy]\nproject_name='project'\nenvironment_tag='dev'\n", encoding="utf-8",
        )
        assert executor_worker_module._exec_container_name(config_fd, "gate", {}) == "project-dev-gate"
        (config_root / "ciu.global.toml").write_text("[deploy]\nnetwork_name='net-network'\n", encoding="utf-8")
        assert executor_worker_module._exec_container_name(config_fd, "gate", {}) == "net-gate"
        (config_root / "ciu.global.toml").write_text("[deploy]\n", encoding="utf-8")
        with pytest.raises(AdmissionError, match="no persistent runner"):
            executor_worker_module._exec_container_name(config_fd, "gate", {})
        (config_root / "bad.toml").write_text("not = [valid", encoding="utf-8")
        with pytest.raises(AdmissionError, match="unreadable"):
            executor_worker_module._parse_toml_fd(config_fd, "bad.toml")
        (config_root / "symlink.toml").symlink_to(config_root / "bad.toml")
        with pytest.raises(AdmissionError, match="unsafe"):
            executor_worker_module._parse_toml_fd(config_fd, "symlink.toml")
    finally:
        os.close(config_fd)


def test_worker_psi_memory_admission_and_host_parsers_use_injected_facts(tmp_path, monkeypatch):
    policy, _checkout = _policy(tmp_path)
    state_dir = tmp_path / "state"
    jobs = state_dir / "jobs"
    job_dir = jobs / "controller-run-1"
    job_dir.mkdir(parents=True)
    state = {
        "schema": "fleet-host-job-state/1", "job_id": "controller-run-1", "state": "running",
        "cancel_requested": False,
    }
    (job_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    waits = []
    psi_answers = iter((
        "some avg10=30.0 avg60=0 total=0\n",
        "full avg10=0 total=0\n",
        "some avg10=0 avg60=30 total=0\n",
        "full avg10=0 total=0\n",
    ))
    worker = HostJobWorker(
        state_dir, policy, "controller-run-1", 9,
        user_resolver=lambda name: RunnerIdentity(name, 1001, 1001, (1001,)),
        pressure_reader=lambda _path: next(psi_answers),
        meminfo_reader=lambda: "MemTotal: 16384000 kB\nMemAvailable: 8192000 kB\n",
        waiter=waits.append,
    )
    worker.request = {"resources": {"declared_peak_bytes": 2 * 1024 * 1024}}
    worker._wait_for_psi()
    assert waits == [30]
    ticket = worker._memory_admission()
    assert ticket >= 0
    os.close(ticket)

    worker.request = {"resources": {"declared_peak_bytes": 0}}
    assert worker._memory_admission() is None
    worker.request = {"resources": {"declared_peak_bytes": -1}}
    with pytest.raises(AdmissionError, match="declared_peak_bytes"):
        worker._memory_admission()

    worker.request = {"resources": {"declared_peak_bytes": 2 * 1024 * 1024}}
    worker.meminfo_reader = lambda: "MemTotal: 16384000 kB\nMemAvailable: 0 kB\n"
    worker.waiter = lambda _seconds: None
    with pytest.raises(AdmissionError, match="wait expired"):
        worker._memory_admission()

    worker.request = {"resources": {"declared_peak_bytes": 2 * 1024 * 1024}}
    worker.meminfo_reader = lambda: "missing facts"
    with pytest.raises(AdmissionError, match="memory facts are unreadable"):
        worker._memory_admission()

    worker.pressure_reader = lambda _path: "unreadable pressure"
    with pytest.raises(AdmissionError, match="PSI facts are unreadable"):
        worker._wait_for_psi()

    assert executor_worker_module._pressure_value("some avg10=2.5 total=4", "some") == 2.5
    for pressure in ("", "some avg60=1", "some avg10=nan", "some avg10=-1"):
        with pytest.raises(ValueError):
            executor_worker_module._pressure_value(pressure, "some")
    assert executor_worker_module._parse_meminfo("MemTotal: 8192000 kB\nMemAvailable: 1024000 kB\n") == (8000, 1000)
    for info in ("", "MemTotal: 0 kB\nMemAvailable: 1 kB\n", "MemTotal: 1 kB\n"):
        with pytest.raises(ValueError):
            executor_worker_module._parse_meminfo(info)

    import builtins

    real_open = builtins.open
    reads = iter(("0::/system.slice/fleetd-job-test.scope\n", "123\n456\n"))

    def fake_open(path, *args, **kwargs):
        if str(path) == "/proc/self/cgroup":
            return io.StringIO(next(reads))
        if str(path).endswith("/cgroup.procs"):
            return io.StringIO(next(reads))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fake_open)
    assert executor_worker_module._scope_processes("fleetd-job-test.scope") == (123, 456)
    reads = iter(("1:cpu:/somewhere\n",))
    with pytest.raises(OSError, match="does not match"):
        executor_worker_module._scope_processes("fleetd-job-test.scope")
    reads = iter(("0::/system.slice/fleetd-job-test.scope\n", "0\n"))
    with pytest.raises(OSError, match="invalid pid"):
        executor_worker_module._scope_processes("fleetd-job-test.scope")


def test_worker_checks_out_a_thin_bundle_against_a_host_seed_and_cleans_staging(tmp_path):
    policy, _checkout = _policy(tmp_path)
    repo_root = policy.repo_roots["vbpub"]
    repo_root.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "bundle-source"
    source.mkdir()
    subprocess.run(["git", "init", "--quiet", "--initial-branch=main", str(source)], check=True)
    (source / "value.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "value.txt"], check=True)
    subprocess.run([
        "git", "-C", str(source), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "--quiet", "-m", "seed",
    ], check=True)
    seed_sha = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"], check=True, capture_output=True, text=True,
    ).stdout.strip()
    shutil.copytree(source, repo_root / seed_sha)
    (source / "value.txt").write_text("target\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "value.txt"], check=True)
    subprocess.run([
        "git", "-C", str(source), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "--quiet", "-m", "target",
    ], check=True)
    target_sha = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"], check=True, capture_output=True, text=True,
    ).stdout.strip()
    tag_name = "run-gate-v25.1.1"
    subprocess.run([
        "git", "-C", str(source), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "tag", "-a", tag_name, "-m", "release", target_sha,
    ], check=True)
    bundle = tmp_path / "thin.bundle"
    subprocess.run([
        "git", "-C", str(source), "bundle", "create", str(bundle), "HEAD", f"refs/tags/{tag_name}",
        f"^{seed_sha}",
    ], check=True)

    state_dir = tmp_path / "state"
    jobs = state_dir / "jobs"
    job_dir = jobs / "controller-run-1"
    job_dir.mkdir(parents=True)
    worker_holder = {}

    class _GitAndHelperRunner:
        def _helper(self, argv, cwd_fd, input_fd=None):
            saved_cwd = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
            saved_stdin = os.dup(0)
            pipe_read = pipe_write = -1
            stdout, stderr = io.StringIO(), io.StringIO()
            try:
                os.fchdir(cwd_fd)
                if input_fd is not None:
                    pipe_read, pipe_write = os.pipe()
                    body = bytearray()
                    while True:
                        chunk = os.read(input_fd, 4096)
                        if not chunk:
                            break
                        body.extend(chunk)
                    os.write(pipe_write, body)
                    os.close(pipe_write)
                    pipe_write = -1
                    os.dup2(pipe_read, 0)
                helper_argv = argv[6:]
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    status = worker_main(helper_argv)
            finally:
                os.dup2(saved_stdin, 0)
                os.fchdir(saved_cwd)
                os.close(saved_stdin)
                os.close(saved_cwd)
                if pipe_read >= 0:
                    os.close(pipe_read)
                if pipe_write >= 0:
                    os.close(pipe_write)
            return CommandResult(status, stdout.getvalue().encode(), stderr.getvalue().encode())

        def run(self, argv, *, cwd_fd, env, timeout=None):
            if len(argv) > 6 and argv[1:4] == ["-I", "-P", "-c"]:
                return self._helper(argv, cwd_fd)
            if argv and argv[0] == "git":
                cwd = os.readlink(f"/proc/self/fd/{cwd_fd}")
                answer = subprocess.run(argv, cwd=cwd, capture_output=True, check=False)
                return CommandResult(answer.returncode, answer.stdout, answer.stderr)
            return CommandResult(127, b"", b"unsupported command")

        def run_with_input(self, argv, *, cwd_fd, env, source_fd, timeout=None):
            return self._helper(argv, cwd_fd, source_fd)

    runner = _GitAndHelperRunner()
    worker = HostJobWorker(
        state_dir, policy, "controller-run-1", 9,
        user_resolver=lambda name: RunnerIdentity(name, 1001, 1001, (1001,), str(tmp_path)),
        runner_factory=lambda _identity: runner,
    )
    worker_holder["worker"] = worker
    worker.runner_identity = RunnerIdentity("runner", 1001, 1001, (1001,), str(tmp_path))
    worker.runner = runner
    worker.group = "controller"
    worker.request = _submit()
    worker.request["sha"] = target_sha
    worker.request_digest = "digest"
    _atomic = {
        "schema": "fleet-host-job-state/1", "job_id": worker.job_id, "state": "running",
    }
    (job_dir / "state.json").write_text(json.dumps(_atomic), encoding="utf-8")
    upload_root = state_dir / "uploads" / "controller" / "in.bundle"
    upload_root.mkdir(parents=True)
    bundle_data = bundle.read_bytes()
    digest = hashlib.sha256(bundle_data).hexdigest()
    (upload_root / digest).write_bytes(bundle_data)
    invalid_bundle = b"not a git bundle"
    while hashlib.sha256(invalid_bundle).hexdigest() >= digest:
        invalid_bundle += b"!"
    invalid_digest = hashlib.sha256(invalid_bundle).hexdigest()
    (upload_root / invalid_digest).write_bytes(invalid_bundle)
    assert invalid_digest < digest

    try:
        worker._create_checkout()
        actual = subprocess.run([
            "git", "-C", str(repo_root / worker.checkout_rel), "rev-parse", "HEAD",
        ], check=True, capture_output=True, text=True).stdout.strip()
        assert actual == target_sha
        assert (repo_root / worker.checkout_rel / "value.txt").read_text(encoding="utf-8") == "target\n"
        assert subprocess.run([
            "git", "-C", str(repo_root / worker.checkout_rel), "cat-file", "-t", f"refs/tags/{tag_name}",
        ], check=True, capture_output=True, text=True).stdout.strip() == "tag"
        assert subprocess.run([
            "git", "-C", str(repo_root / worker.checkout_rel), "rev-parse", f"refs/tags/{tag_name}^{{}}",
        ], check=True, capture_output=True, text=True).stdout.strip() == target_sha
        assert all("bundle-" not in child.name for child in (repo_root / ".fleetd-worktrees").iterdir())
    finally:
        os.close(worker.repo_root_fd)


def test_executor_filesystem_locks_state_limits_and_cgroup_reader(tmp_path, monkeypatch):
    from fleetd.executor import _EXECUTOR_JSON_MAX

    record = tmp_path / "state.json"
    _atomic_json(record, {"value": "ok"})
    assert json.loads(record.read_text(encoding="utf-8")) == {"value": "ok"}
    with pytest.raises(AdmissionError, match="cannot persist executor state"):
        _atomic_json(record, {"value": object()})
    with pytest.raises(AdmissionError, match="exceeds"):
        _atomic_json(record, {"value": "x" * (_EXECUTOR_JSON_MAX + 1)})

    lock_dir = tmp_path / "job"
    lock_dir.mkdir()
    with _job_state_lock(lock_dir):
        assert (lock_dir / "state.lock").is_file()
    (lock_dir / "state.lock").chmod(0o644)
    with pytest.raises(AdmissionError, match="private regular file"):
        with _job_state_lock(lock_dir):
            pass
    (lock_dir / "state.lock").unlink()
    (lock_dir / "state.lock").symlink_to(record)
    with pytest.raises(AdmissionError, match="cannot lock executor state"):
        with _job_state_lock(lock_dir):
            pass

    import builtins

    real_open = builtins.open
    monkeypatch.setattr(
        builtins, "open",
        lambda path, *args, **kwargs: io.StringIO("2:cpu:/other\n0::/system.slice/job.scope\n")
        if path == "/proc/42/cgroup" else real_open(path, *args, **kwargs),
    )
    assert _read_pid_cgroup(42) == "/system.slice/job.scope"
    monkeypatch.setattr(
        builtins, "open",
        lambda path, *args, **kwargs: io.StringIO("2:cpu:/other\n")
        if path == "/proc/42/cgroup" else real_open(path, *args, **kwargs),
    )
    with pytest.raises(OSError, match="no unified cgroup"):
        _read_pid_cgroup(42)


def test_executor_atomic_lock_signal_and_scope_failure_paths(tmp_path, monkeypatch):
    record = tmp_path / "state.json"
    replace = executor_module.os.replace

    def fail_replace(_source, _destination):
        raise OSError("rename failed")

    monkeypatch.setattr(executor_module.os, "replace", fail_replace)
    with pytest.raises(AdmissionError, match="cannot persist executor state"):
        _atomic_json(record, {"value": "ok"})
    assert not record.exists()
    assert list(tmp_path.iterdir()) == []

    monkeypatch.setattr(executor_module.os, "replace", replace)
    monkeypatch.setattr(
        executor_module, "_fsync_directory",
        lambda _path: (_ for _ in ()).throw(OSError("directory sync failed")),
    )
    with pytest.raises(AdmissionError, match="directory sync failed"):
        _atomic_json(record, {"value": "durable file, failed directory sync"})
    assert record.is_file()

    lock_dir = tmp_path / "lock"
    lock_dir.mkdir()
    real_flock = fcntl.flock
    monkeypatch.setattr(
        executor_module.fcntl, "flock",
        lambda *_args: (_ for _ in ()).throw(OSError("lock unavailable")),
    )
    with pytest.raises(AdmissionError, match="cannot lock executor state"):
        with _job_state_lock(lock_dir):
            pytest.fail("lock body must not run")
    monkeypatch.setattr(executor_module.fcntl, "flock", real_flock)

    signal_calls = []
    monkeypatch.setattr(executor_module.os, "killpg", lambda pid, sig: signal_calls.append((pid, sig)))
    executor_module.SystemProcessSignals().terminate_group(44)
    assert signal_calls == [(44, executor_module.signal.SIGTERM)]

    def launcher_case(*, popen, reaper=lambda _wait: None, run=lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, "ActiveState=active\n", ""), pid_cgroup=lambda _pid: "/x/unit.scope", monotonic=time.monotonic):
        read_fd, write_fd = os.pipe()
        launcher = SystemdScopeLauncher(
            1, popen=popen, reaper_start=reaper, run=run, pid_cgroup=pid_cgroup, monotonic=monotonic,
        )
        try:
            return launcher.launch("unit.scope", ["worker"], 8, read_fd, write_fd)
        finally:
            os.close(read_fd)

    with pytest.raises(AdmissionError, match="cannot start job scope"):
        launcher_case(popen=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("systemd-run missing")))

    def write_handshake(_argv, **kwargs):
        os.write(kwargs["pass_fds"][1], b"515\n")
        return type("Process", (), {"pid": 515, "wait": lambda _self: 0})()

    with pytest.raises(AdmissionError, match="cannot reap job scope"):
        launcher_case(
            popen=write_handshake,
            reaper=lambda _wait: (_ for _ in ()).throw(RuntimeError("thread unavailable")),
        )
    with pytest.raises(AdmissionError, match="cannot read worker 515 cgroup"):
        launcher_case(popen=write_handshake, pid_cgroup=lambda _pid: (_ for _ in ()).throw(OSError("proc unavailable")))
    with pytest.raises(AdmissionError, match="timed out confirming active job scope"):
        launcher_case(popen=write_handshake, monotonic=iter((0.0, 0.0, 2.0)).__next__)

    read_fd, write_fd = os.pipe()
    monkeypatch.setattr(executor_module.select, "select", lambda *_args: ([], [], []))
    with pytest.raises(AdmissionError, match="worker handshake timed out"):
        SystemdScopeLauncher(1)._read_worker_handshake(read_fd, time.monotonic() + 1, "unit.scope")
    os.close(read_fd)
    os.close(write_fd)


def test_executor_user_resolution_uses_host_nss_facts_without_defaults(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    accounts = {"runner": SimpleNamespace(pw_uid=1002, pw_gid=44, pw_dir="/home/runner")}
    groups = []
    monkeypatch.setitem(sys.modules, "pwd", SimpleNamespace(getpwnam=lambda name: accounts[name]))
    monkeypatch.setitem(sys.modules, "grp", SimpleNamespace(getgrgid=lambda gid: groups.append(gid)))
    monkeypatch.setattr(os, "getgrouplist", lambda _name, _gid: [55, 44, 55])
    identity = executor_module._resolve_system_user("runner")
    assert identity == RunnerIdentity("runner", 1002, 44, (44, 55), "/home/runner")
    assert groups == [44, 55]

    accounts["runner"] = SimpleNamespace(pw_uid=1002, pw_gid=44, pw_dir="/home/runner")
    monkeypatch.setattr(os, "getgrouplist", lambda _name, _gid: [55])
    assert executor_module._resolve_system_user("runner").groups == (44, 55)
    accounts["root"] = SimpleNamespace(pw_uid=0, pw_gid=0, pw_dir="/root")
    with pytest.raises(ValueError, match="uid 0"):
        executor_module._resolve_system_user("root")
    accounts["stale"] = SimpleNamespace(pw_uid=1003, pw_gid=44, pw_dir="/home/stale")
    monkeypatch.setattr(os, "getgrouplist", lambda _name, _gid: [77])
    monkeypatch.setitem(sys.modules, "grp", SimpleNamespace(getgrgid=lambda _gid: (_ for _ in ()).throw(KeyError("missing gid"))))
    with pytest.raises(KeyError, match="missing gid"):
        executor_module._resolve_system_user("stale")


def test_executor_refuses_bad_job_directories_and_state_records(tmp_path):
    executor, _policy_value, state_dir = _executor(tmp_path)
    request = _submit()
    job_dir = _durable_job(state_dir, request)
    (job_dir / "state.json").write_bytes(b"\xff")
    with pytest.raises(AdmissionError, match="invalid"):
        executor.read_state(request["job_id"])
    (job_dir / "state.json").write_bytes(b"x" * (1024 * 1024 + 1))
    with pytest.raises(AdmissionError, match="exceeds 1 MiB"):
        executor.read_state(request["job_id"])
    (job_dir / "state.json").unlink()
    (job_dir / "state.json").mkdir()
    with pytest.raises(AdmissionError, match="not a regular file"):
        executor.read_state(request["job_id"])
    (job_dir / "state.json").rmdir()
    (job_dir / "state.json").write_text(json.dumps({
        "schema": "fleet-host-job-state/1", "job_id": request["job_id"], "state": "done",
    }), encoding="utf-8")
    with pytest.raises(AdmissionError, match="finished job"):
        executor.request_stop(request["job_id"], "stop")
    (job_dir / "state.json").write_text(json.dumps({
        "schema": "fleet-host-job-state/1", "job_id": request["job_id"], "state": "stopping",
        "lane_process_status": 0,
    }), encoding="utf-8")
    with pytest.raises(AdmissionError, match="lane.*has exited"):
        executor.request_stop(request["job_id"], "stop")

    bad_jobs_dir = tmp_path / "bad-jobs"
    bad_jobs_dir.mkdir(mode=0o755)
    bad = HostJobExecutor(
        bad_jobs_dir.parent, executor.policy,
        scope_launcher=_Scope(), user_resolver=lambda name: RunnerIdentity(name, 1001, 1001, (1001,)),
    )
    bad.jobs_dir = bad_jobs_dir
    with pytest.raises(AdmissionError, match="accessible"):
        bad._job_dir("job", create=False)

    private_jobs = tmp_path / "private-jobs"
    private_jobs.mkdir(mode=0o700)
    bad.jobs_dir = private_jobs
    (private_jobs / "job").symlink_to(job_dir, target_is_directory=True)
    with pytest.raises(AdmissionError, match="is a symlink"):
        bad._job_dir("job", create=False)


def test_systemd_scope_launcher_retries_queries_and_validates_handshake_errors():
    read_fd, write_fd = os.pipe()
    now = [0.0]
    queries = []

    def popen(_argv, **kwargs):
        os.write(kwargs["pass_fds"][1], b"515\n")
        return type("WorkerProcess", (), {"pid": 515, "wait": lambda _self: 0})()

    def query(argv, **_kwargs):
        queries.append(list(argv))
        if len(queries) == 1:
            raise OSError("transient systemctl error")
        return subprocess.CompletedProcess(argv, 0, "ActiveState=active\n", "")

    launcher = SystemdScopeLauncher(
        2, popen=popen, run=query, pid_cgroup=lambda _pid: "/user.slice/fleetd-job-x.scope",
        waiter=lambda amount: now.__setitem__(0, now[0] + amount), monotonic=lambda: now[0],
        reaper_start=lambda _wait: None,
    )
    try:
        assert launcher.launch("fleetd-job-x.scope", ["worker"], 8, read_fd, write_fd) == ScopeState(
            "fleetd-job-x.scope", True, 515,
        )
        assert len(queries) == 2
    finally:
        os.close(read_fd)

    for payload, message in ((b"x\n", "handshake is invalid"), (b"0\n", "pid is invalid"),
                             (b"1" * 65, "too large"), (None, "before its handshake")):
        child_read, child_write = os.pipe()
        if payload is not None:
            os.write(child_write, payload)
            os.close(child_write)
        else:
            os.close(child_write)
        try:
            with pytest.raises(AdmissionError, match=message):
                SystemdScopeLauncher(1)._read_worker_handshake(child_read, time.monotonic() + 1, "unit.scope")
        finally:
            os.close(child_read)

    child_read, child_write = os.pipe()
    os.close(child_write)
    try:
        with pytest.raises(AdmissionError, match="timed out"):
            SystemdScopeLauncher(1, monotonic=lambda: 0)._read_worker_handshake(
                child_read, -1, "unit.scope",
            )
    finally:
        os.close(child_read)


def test_executor_start_refuses_missing_request_and_retains_charge_on_confirmation_write_error(tmp_path, monkeypatch):
    executor, _policy_value, state_dir = _executor(tmp_path)
    request = _submit()
    request["sha"] = SHA
    handle = _Handle()
    job_dir = executor.jobs_dir / request["job_id"]
    job_dir.mkdir(mode=0o700)
    with pytest.raises(AdmissionError, match="no durable request"):
        executor.start(request, handle)
    assert handle.live

    request = dict(request)
    request["job_id"] = "controller-run-2"
    job_dir = _durable_job(state_dir, request)
    real_atomic = executor_module._atomic_json
    failed = {"value": False}

    def fail_confirmation(path, document):
        if document.get("start_confirmed") is True and not failed["value"]:
            failed["value"] = True
            raise AdmissionError(70, "simulated durable confirmation write failure")
        return real_atomic(path, document)

    monkeypatch.setattr(executor_module, "_atomic_json", fail_confirmation)
    with pytest.raises(AdmissionError, match="confirmation write failure"):
        executor.start(request, handle)
    durable = json.loads((job_dir / "state.json").read_text(encoding="utf-8"))
    assert durable["start_confirmed"] is False
    assert "start_error" in durable
    assert handle.live and not handle.handed_off


def test_executor_start_requires_a_live_admission_handle_and_valid_request_record(tmp_path):
    executor, _policy_value, state_dir = _executor(tmp_path)
    request = _submit()
    request["sha"] = SHA
    _durable_job(state_dir, request)
    handle = _Handle()
    handle.live = False
    with pytest.raises(AdmissionError, match="live admission handle"):
        executor.start(request, handle)

    job_dir = state_dir / "jobs" / request["job_id"]
    (job_dir / "request.json").unlink()
    (job_dir / "request.json").symlink_to(job_dir / "state.json")
    handle.live = True
    with pytest.raises(AdmissionError, match="cannot open executor request"):
        executor.start(request, handle)

    (job_dir / "request.json").unlink()
    (job_dir / "request.json").mkdir()
    with pytest.raises(AdmissionError, match="not a regular file"):
        executor.start(request, handle)


def _ready_worker(tmp_path, runner=None, *, request=None, docker=None, waiter=lambda _seconds: None):
    policy, _checkout = _policy(tmp_path)
    request = dict(_submit() if request is None else request)
    state_dir = tmp_path / "worker-state"
    job_dir = state_dir / "jobs" / request["job_id"]
    job_dir.mkdir(parents=True)
    (job_dir / "start.lock").touch(mode=0o600)
    (job_dir / "state.json").write_text(json.dumps({
        "schema": "fleet-host-job-state/1", "job_id": request["job_id"],
        "state": "running", "start_confirmed": True, "cancel_requested": False,
        "scope": f"fleetd-job-{request['job_id']}.scope",
        "guard": {"present": False, "reason": "thrashguard-absent"},
    }), encoding="utf-8")
    runner = _VersionRunner({}) if runner is None else runner
    worker = HostJobWorker(
        state_dir, policy, request["job_id"], 9,
        user_resolver=lambda name: RunnerIdentity(name, 1001, 1001, (1001,), str(tmp_path)),
        runner_factory=lambda _identity: runner, docker=docker, waiter=waiter,
        run_gate_finder=lambda _name: "/usr/bin/run-gate",
    )
    worker.request = request
    worker.group = "controller"
    worker.runner_identity = RunnerIdentity("runner", 1001, 1001, (1001,), str(tmp_path))
    worker.runner = runner
    worker.repo_root = policy.repo_roots[request["repo"]]
    worker.repo_root.mkdir(parents=True, exist_ok=True)
    worker.repo_root_fd = os.open(worker.repo_root, os.O_RDONLY | os.O_DIRECTORY)
    worker.checkout_rel = f".fleetd-worktrees/{request['job_id']}"
    checkout = worker.repo_root / worker.checkout_rel
    checkout.mkdir(parents=True)
    worker.checkout_fd = os.open(checkout, os.O_RDONLY | os.O_DIRECTORY)
    project = request.get("project", ".")
    worker.project_fd = os.dup(worker.checkout_fd) if project == "." else -1
    return worker, runner, job_dir, checkout


def test_worker_request_environment_cancel_and_command_wrappers(tmp_path):
    class _Commands:
        def __init__(self):
            self.results = []
            self.calls = []

        def run(self, argv, *, cwd_fd, env, timeout=None):
            self.calls.append((list(argv), cwd_fd, dict(env), timeout))
            result = self.results.pop(0)
            return result

    commands = _Commands()
    request = _submit()
    request["env"] = {"CUSTOM": "value", "RUN_GATE_PROFILE": "chosen"}
    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path, commands, request=request)
    digest = AgentAdmission.request_model(worker.request).digest()
    (job_dir / "request.json").write_text(json.dumps({
        "schema": "fleet-host-job-request/1", "group": "controller", "digest": digest,
        "request": worker.request,
    }), encoding="utf-8")
    worker._read_request()
    assert worker.request_digest == digest and worker.group == "controller"
    env = worker._environment()
    assert env["CUSTOM"] == "value" and env["RUN_GATE_PROFILE"] == "chosen"
    assert env["HOME"] == str(tmp_path) and env["RUN_GATE_JOB"] == worker.job_id

    commands.results.append(CommandResult(0, b"ok", b""))
    assert worker._run(["argv"] ) == CommandResult(0, b"ok", b"")
    commands.results.append(CommandResult(5, b"", b"specific failure"))
    with pytest.raises(AdmissionError, match="expected failure: specific failure") as caught:
        worker._run_checked(["argv"], exit_code=79, detail="expected failure")
    assert caught.value.exit_code == 79
    commands.results.append(CommandResult(1, b"", b"helper refused"))
    with pytest.raises(AdmissionError, match="helper 'prepare' failed: helper refused"):
        worker._helper("prepare", "--relative", "safe")

    worker._cancel_checkpoint()
    worker._save_state(cancel_requested=True, cancel_reason="operator")
    with pytest.raises(AdmissionError, match="operator"):
        worker._cancel_checkpoint()
    assert worker._await_scope_confirmation() is True
    (job_dir / "start.lock").unlink(missing_ok=True)
    (job_dir / "start.lock").touch(mode=0o600)
    (job_dir / "state.json").write_text("bad json", encoding="utf-8")
    assert worker._await_scope_confirmation() is False
    for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
        os.close(fd)


@pytest.mark.parametrize("failure, message", [
    ("bad-json", "durable request is unreadable"),
    ("bad-schema", "durable request schema is invalid"),
    ("bad-identity", "durable request identity is invalid"),
    ("bad-payload", "durable request payload is invalid"),
    ("bad-digest", "durable request digest does not match"),
])
def test_worker_refuses_corrupt_durable_request_records(tmp_path, failure, message):
    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path, _VersionRunner({}))
    if failure == "bad-json":
        payload = "not-json"
    else:
        request = dict(worker.request)
        group = "controller"
        schema = "fleet-host-job-request/1"
        digest = AgentAdmission.request_model(request).digest()
        if failure == "bad-schema":
            schema = "fleet-host-job-request/2"
        elif failure == "bad-identity":
            request["job_id"] = "another-job"
        elif failure == "bad-payload":
            request = {"job_id": worker.job_id}
            digest = "placeholder"
        elif failure == "bad-digest":
            digest = "0" * 64
        payload = json.dumps({"schema": schema, "group": group, "digest": digest, "request": request})
    (job_dir / "request.json").write_text(payload, encoding="utf-8")
    try:
        with pytest.raises(AdmissionError, match=message):
            worker._read_request()
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_worker_command_wrappers_refuse_missing_runner_and_use_closed_exit_details(tmp_path):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path, _VersionRunner({}))
    try:
        worker.runner = None
        with pytest.raises(AdmissionError, match="interface is unavailable"):
            worker._run(["git", "status"])

        class _Commands:
            def run(self, _argv, **_kwargs):
                return CommandResult(7, b"", b"")

        worker.runner = _Commands()
        with pytest.raises(AdmissionError, match=r"failed \(exit 7\)") as caught:
            worker._run_checked(["git", "status"], detail="command failed")
        assert caught.value.exit_code == int(ExitCode.RUNNER_FAILURE)
        worker._save_state(state="stopping", cancel_requested=True, cancel_reason="")
        with pytest.raises(AdmissionError, match="cancelled before lane start"):
            worker._cancel_checkpoint()
        worker._save_state(state="running", retry_required=True)
        assert worker._state()["state"] == "stopping"
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_worker_memory_ticket_wait_cancel_and_psi_timeout_paths(tmp_path):
    policy, _checkout = _policy(tmp_path)
    state_dir = tmp_path / "state"
    job_dir = state_dir / "jobs" / "controller-run-1"
    job_dir.mkdir(parents=True)
    (job_dir / "state.json").write_text(json.dumps({
        "schema": "fleet-host-job-state/1", "job_id": "controller-run-1", "state": "running",
        "cancel_requested": False,
    }), encoding="utf-8")
    waits = []
    worker = HostJobWorker(
        state_dir, policy, "controller-run-1", 9,
        user_resolver=lambda name: RunnerIdentity(name, 1001, 1001, (1001,)),
        meminfo_reader=lambda: "MemTotal: 16384000 kB\nMemAvailable: 8192000 kB\n",
        pressure_reader=lambda _path: "some avg10=26 total=0\nfull avg10=0 total=0\n",
        waiter=waits.append,
    )
    worker.request = {"resources": {"declared_peak_bytes": 2 * 1024 * 1024}}
    memory_lock = os.open(state_dir / "memory-admit.lock", os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(memory_lock, fcntl.LOCK_EX)

    def release_after_wait(seconds):
        waits.append(seconds)
        fcntl.flock(memory_lock, fcntl.LOCK_UN)

    worker.waiter = release_after_wait
    ticket = worker._memory_admission()
    assert ticket >= 0 and waits == [10]
    os.close(ticket)
    os.close(memory_lock)

    waits.clear()
    worker.waiter = waits.append
    worker._save_state(cancel_requested=False)
    worker._memory_admission()
    assert waits == []

    waits.clear()
    worker.pressure_reader = lambda _path: "some avg10=26 total=0\nfull avg10=0 total=0\n"
    with pytest.raises(AdmissionError, match="PSI above"):
        worker._wait_for_psi()
    assert len(waits) == 30

    waits.clear()
    def cancel_wait(seconds):
        waits.append(seconds)
        worker._save_state(cancel_requested=True, cancel_reason="cancelled in PSI wait")
    worker.waiter = cancel_wait
    with pytest.raises(AdmissionError, match="cancelled in PSI wait"):
        worker._wait_for_psi()

    worker.request = {"resources": {"declared_peak_bytes": 1}}
    assert worker._memory_admission() is None

    memory_path = state_dir / "memory-admit.lock"
    memory_path.unlink()
    memory_path.symlink_to(job_dir / "state.json")
    worker.request = {"resources": {"declared_peak_bytes": 2 * 1024 * 1024}}
    with pytest.raises(AdmissionError, match="memory admission ticket"):
        worker._memory_admission()


def test_seed_store_preflight_reports_refusals_and_unpacks_only_untracked_data(tmp_path):
    archive = _seed_archive("store/value.txt", b"seed value")
    digest = hashlib.sha256(archive).hexdigest()
    sidecar = f"{digest}  assay-seed-0.tgz\n".encode("ascii")

    class _SeedRunner:
        def __init__(self, *, tracked=b"", helper_status=0):
            self.tracked = tracked
            self.helper_status = helper_status

        def run(self, argv, **_kwargs):
            if argv[:4] == ["git", "-C", str(worker.repo_root / worker.checkout_rel), "ls-files"]:
                return CommandResult(0, self.tracked, b"")
            return CommandResult(127, b"", b"unexpected command")

        def run_with_input(self, argv, *, cwd_fd, source_fd, **_kwargs):
            if self.helper_status:
                return CommandResult(self.helper_status, b"", b"helper seed failure")
            saved_cwd = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
            saved_stdin = os.dup(0)
            read_fd = write_fd = -1
            stdout, stderr = io.StringIO(), io.StringIO()
            try:
                os.fchdir(cwd_fd)
                read_fd, write_fd = os.pipe()
                content = bytearray()
                while True:
                    chunk = os.read(source_fd, 4096)
                    if not chunk:
                        break
                    content.extend(chunk)
                os.write(write_fd, content)
                os.close(write_fd)
                write_fd = -1
                os.dup2(read_fd, 0)
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    status = worker_main(argv[6:])
            finally:
                os.dup2(saved_stdin, 0)
                os.fchdir(saved_cwd)
                os.close(saved_stdin)
                os.close(saved_cwd)
                if read_fd >= 0:
                    os.close(read_fd)
                if write_fd >= 0:
                    os.close(write_fd)
            return CommandResult(status, stdout.getvalue().encode(), stderr.getvalue().encode())

    scenarios = tmp_path / "seed-scenarios"
    scenarios.mkdir()
    none_directory = scenarios / "none"
    none_directory.mkdir()
    worker, _runner, _job_dir, _checkout = _ready_worker(none_directory)
    worker.request["stores"] = {"seed": None}
    worker._seed_stores()
    for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
        os.close(fd)

    def prepared(name, runner=None):
        directory = scenarios / name
        directory.mkdir()
        runner_value = _SeedRunner() if runner is None else runner
        made, _unused, _job, checkout = _ready_worker(directory, runner_value)
        return made, checkout

    worker, _checkout = prepared("invalid")
    worker.request["stores"] = {"seed": {"path": "../unsafe"}}
    with pytest.raises(AdmissionError, match="invalid-descriptor"):
        worker._seed_stores()
    for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
        os.close(fd)

    worker, _checkout = prepared("missing")
    worker.request["stores"] = {"seed": {"path": "store", "sha256": digest, "bytes": len(archive)}}
    with pytest.raises(AdmissionError, match="checksum"):
        worker._seed_stores()
    for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
        os.close(fd)

    def upload(worker_value):
        upload_root = worker_value.state_dir / "uploads" / "controller"
        archive_dir = upload_root / "assay-seed-0.tgz"
        checksum_dir = upload_root / "assay-seed-0.tgz.sha256"
        archive_dir.mkdir(parents=True)
        checksum_dir.mkdir()
        (archive_dir / digest).write_bytes(archive)
        checksum_hash = hashlib.sha256(sidecar).hexdigest()
        (checksum_dir / checksum_hash).write_bytes(sidecar)

    worker, _checkout = prepared("tracked", _SeedRunner(tracked=b"store/file\n"))
    upload(worker)
    worker.request["stores"] = {"seed": {"path": "store", "sha256": digest, "bytes": len(archive)}}
    with pytest.raises(AdmissionError, match="tracked-files"):
        worker._seed_stores()
    for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
        os.close(fd)

    worker, _checkout = prepared("helper-fails", _SeedRunner(helper_status=1))
    upload(worker)
    worker.request["stores"] = {"seed": {"path": "store", "sha256": digest, "bytes": len(archive)}}
    with pytest.raises(AdmissionError, match="helper seed failure"):
        worker._seed_stores()
    for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
        os.close(fd)

    worker, checkout = prepared("success")
    upload(worker)
    worker.request["stores"] = {"seed": {"path": "store", "sha256": digest, "bytes": len(archive)}}
    worker._seed_stores()
    assert (checkout / "store" / "value.txt").read_bytes() == b"seed value"
    for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
        os.close(fd)


@pytest.mark.parametrize("mode", ["host", "ephemeral", "exec"])
def test_run_lane_builds_argv_environment_and_records_child_status(tmp_path, mode):
    lifecycle = []
    captured = {}
    request = _submit()
    request.update(extra_argv=["--verbose"], base="refs/remote-gate/base")

    class _LaneProcess:
        pid = 861

        def __init__(self):
            self.statuses = [None, 0]

        def poll(self):
            status = self.statuses.pop(0)
            lifecycle.append(("poll", status))
            return status

        def wait(self):
            return 0

    class _LaneRunner:
        def run(self, argv, *, cwd_fd, env, timeout=None):
            if len(argv) > 6 and argv[1:4] == ["-I", "-P", "-c"]:
                saved = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
                out, err = io.StringIO(), io.StringIO()
                try:
                    os.fchdir(cwd_fd)
                    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                        status = worker_main(argv[6:])
                finally:
                    os.fchdir(saved)
                    os.close(saved)
                return CommandResult(status, out.getvalue().encode(), err.getvalue().encode())
            return CommandResult(0, b"", b"")

        def start(self, argv, *, cwd_fd, env, output_fd):
            captured.update(argv=list(argv), env=dict(env), cwd_fd=cwd_fd)
            event_path = Path(argv[argv.index("--event-file") + 1])
            event_path.write_text(json.dumps({
                "api": "fleet-host-agent/1", "job_id": request["job_id"],
                "event": "lane-verdict", "lane": request["lane"],
                "verdict": "PASS", "exit_code": 0, "lane_exit_code": 0,
            }) + "\n", encoding="utf-8")
            os.write(output_fd, b"run-gate: lane 'r0-r1' verdict PASS; exit_code 0; log lane.log\n")
            lifecycle.append("start")
            return _LaneProcess()

    worker, _runner, job_dir, checkout = _ready_worker(
        tmp_path, _LaneRunner(), request=request, waiter=lifecycle.append,
    )
    os.close(worker.project_fd)
    worker.project_fd = -1
    worker.runner_identity = RunnerIdentity("runner", os.getuid(), os.getgid(), (os.getgid(),), str(tmp_path))
    worker._save_state(lane_mode=mode, runner_name="persistent-runner" if mode == "exec" else None)
    status = worker._run_lane("/usr/bin/run-gate", mode, {"image": "tester:local", "runner_name": "persistent-runner"}, None)
    assert status == 0
    assert captured["argv"] == [
        "/usr/bin/run-gate", request["lane"], "--verbose", "--base", "refs/remote-gate/base",
        "--event-file", str(worker.event_path), "--job-id", request["job_id"],
    ]
    assert captured["env"]["RUN_GATE_JOB"] == request["job_id"]
    assert "RUN_GATE_EXPECTED_RUNNER" not in captured["env"]
    if mode == "ephemeral":
        assert captured["env"]["RUN_GATE_TESTER_UNIFIED_IMAGE"] == "tester:local"
    else:
        assert "RUN_GATE_TESTER_UNIFIED_IMAGE" not in captured["env"]
    state = worker._state()
    assert state["lane_process_status"] == 0 and state["lane_may_be_live"] is False
    assert state["lane_status_owner"] == "worker"
    assert any(item == ("poll", None) for item in lifecycle)
    assert (checkout / "run-gate.toml").is_dir() is False
    for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
        os.close(fd)


def test_lane_event_follower_persists_container_observation_before_child_exit(tmp_path):
    container_id = "f" * 64
    request = _submit()
    request.update(sha=SHA, project=".", lane="r0-r1", extra_argv=[], base=None)
    lifecycle = []

    class _Process:
        pid = 862

        def __init__(self):
            self.polls = 0

        def poll(self):
            self.polls += 1
            return 0 if self.polls == 3 else None

    class _Runner:
        def start(self, _argv, *, cwd_fd, env, output_fd):
            return _Process()

    worker, _runner, _job_dir, checkout = _ready_worker(tmp_path, _Runner(), request=request)
    os.close(worker.project_fd)
    worker.project_fd = -1
    worker.runner_identity = RunnerIdentity("runner", os.getuid(), os.getgid(), (os.getgid(),), str(tmp_path))
    (checkout / "run-gate.toml").write_text(
        "[lanes.r0-r1]\nenvironment = 'tester'\n\n"
        "[environments.tester]\nmode = 'ephemeral'\nimage = 'tester:local'\n",
        encoding="utf-8",
    )
    expected_source = str(worker.repo_root / worker.checkout_rel)

    class _Docker:
        def inspect(self, target):
            lifecycle.append(("inspect", target))
            return {
                "Id": container_id,
                "State": {"Running": True},
                "Mounts": [{"Type": "bind", "Source": expected_source, "Destination": "/workspace"}],
                "Config": {"Labels": {
                    "run-gate.repo": request["repo"], "run-gate.project": ".",
                    "run-gate.lane": "r0-r1", "run-gate.commit": SHA,
                    "run-gate.name-schema": "2", "run-gate.started": "2026-11-27T00:00:00Z",
                    "run-gate.job": request["job_id"],
                }},
            }

    worker.docker = _Docker()

    def helper(mode, *args, cwd_fd=None):
        assert mode == "prepare-event"
        executor_worker_module._prepare_event(cwd_fd, args[1])
        return CommandResult(0)

    worker._helper = helper
    save = worker._retry_state_save

    def record(**updates):
        if "lane_event_observations" in updates:
            lifecycle.append("observation-saved")
        if "lane_process_status" in updates:
            lifecycle.append("exit-status-saved")
        return save(**updates)

    worker._retry_state_save = record
    waits = []

    def waiter(duration):
        waits.append(duration)
        if len(waits) == 1:
            worker.event_path.write_text(json.dumps({
                "api": "fleet-host-agent/1", "job_id": request["job_id"],
                "event": "lane-started", "lane": "r0-r1", "container_id": container_id,
                "image_id": None, "adopted": False,
            }) + "\n", encoding="utf-8")

    worker.waiter = waiter
    worker._save_state(lane_mode="ephemeral", runner_name=None)
    try:
        assert worker._run_lane("/usr/bin/run-gate", "ephemeral", {
            "image": "tester:local", "runner_name": None,
        }, None) == 0
        state = worker._state()
        assert state["lane_event_observations"][0]["binding"]["id"] == container_id
        assert lifecycle.index("observation-saved") < lifecycle.index("exit-status-saved")
        assert lifecycle[0] == ("inspect", container_id)
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize(
    "mode,guard,client,signal_error,expected",
    [
        ("host", False, None, None, "sent"),
        ("host", True, None, None, "refused"),
        ("host", None, None, None, "refused"),
        ("exec", False, None, None, "refused"),
        ("host", False, None, ProcessLookupError(), "process-already-exited"),
        ("host", False, None, OSError("no permission"), "failed"),
    ],
)
def test_worker_owned_cancel_signals_only_after_guard_handshake(
    tmp_path, mode, guard, client, signal_error, expected,
):
    request = _submit()
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path, request=request)
    worker._save_state(lane_mode=mode, guard={"present": guard}, cancel_requested=True)
    calls = []

    class _Signals:
        def terminate_group(self, pid):
            calls.append(("term", pid))
            if signal_error is not None:
                raise signal_error

    if client is not None:
        worker.thrashguard_client = client
    worker.process_signals = _Signals()
    worker.runner_process = type("Process", (), {"pid": 88})()
    worker._request_worker_stop(worker.runner_process)
    state = worker._state()
    if expected == "sent":
        assert state["stop_signal"] == "sent" and calls == [("term", 88)]
    elif expected == "process-already-exited":
        assert state["stop_signal"] == expected
    elif expected == "failed":
        assert state["stop_signal"] == "failed" and "TERM failed" in state["reason"]
    else:
        assert state["cancel_refused"] is True
        assert calls == []
    for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
        os.close(fd)


def test_worker_cancel_deregisters_guard_before_term_and_handles_deregister_errors(tmp_path):
    request = _submit()
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path, request=request)
    worker._save_state(lane_mode="host", guard={"present": True}, cancel_requested=True)
    calls = []

    class _Guard:
        def deregister(self, job_id):
            calls.append(("deregister", job_id))

    class _Signals:
        def terminate_group(self, pid):
            calls.append(("term", pid))

    worker.thrashguard_client = _Guard()
    worker.process_signals = _Signals()
    worker.runner_process = type("Process", (), {"pid": 89})()
    worker._request_worker_stop(worker.runner_process)
    assert calls == [("deregister", worker.job_id), ("term", 89)]
    assert worker._state()["guard_deregistered"] is True
    for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
        os.close(fd)

    scenario = tmp_path / "deregister-error"
    scenario.mkdir()
    worker, _runner, _job_dir, _checkout = _ready_worker(scenario, request=request)
    worker._save_state(lane_mode="host", guard={"present": True}, cancel_requested=True)

    class _BadGuard:
        def deregister(self, _job_id):
            raise OSError("guard unavailable")

    class _NoSignals:
        def terminate_group(self, _pid):
            pytest.fail("TERM must wait for successful guard deregistration")

    worker.thrashguard_client = _BadGuard()
    worker.process_signals = _NoSignals()
    worker.runner_process = type("Process", (), {"pid": 90})()
    worker._request_worker_stop(worker.runner_process)
    assert worker._state()["cancel_refused"] is True
    assert "deregister-failed" in worker._state()["reason"]
    for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
        os.close(fd)


def test_worker_keeps_charge_if_lane_start_outcome_is_uncertain(tmp_path):
    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path)
    request = worker.request
    request["sha"] = SHA
    worker._read_request = lambda: None
    worker._await_scope_confirmation = lambda: True
    worker._create_checkout = lambda: None
    worker._prerequisites = lambda: ("/usr/bin/run-gate", "25.0.1", {
        "lane": {}, "environment": {"mode": "host"}, "runner_name": None,
    })
    worker._wait_for_psi = lambda: None
    worker._seed_stores = lambda: None
    worker._memory_admission = lambda: None
    finalized = []

    def start_attempt(_binary, _mode, _facts, _memory_lock_fd):
        worker._save_state(state="lane-starting", lane_may_be_live=True, lane_start_attempted=True)
        raise OSError("start outcome not confirmed")

    worker._run_lane = start_attempt
    worker._finish_unstarted = lambda *_args: finalized.append("unstarted")
    code = worker.run()
    state = worker._state()
    assert code == int(75)
    assert state["state"] == "stopping" and state["lane_may_be_live"] is True
    assert "start outcome not confirmed" in state["reason"]
    assert finalized == []
    assert not (job_dir / "result.json").exists()


def test_worker_run_routes_pre_lane_refusal_to_terminal_completion(tmp_path):
    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path)
    worker._await_scope_confirmation = lambda: True
    worker._read_request = lambda: None
    worker.request["kind"] = "dev"
    worker._create_checkout = lambda: None
    finalized = []
    worker._finish_unstarted = lambda code, reason: finalized.append((code, reason)) or int(code)
    assert worker.run() == int(executor_module.ExitCode.REFUSAL)
    assert finalized == [(executor_module.ExitCode.REFUSAL, "kind=dev is outside the C3b-1 lane executor")]


def test_worker_finish_unstarted_publishes_only_after_terminal_write_and_retries(tmp_path):
    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path)
    worker.request_digest = "d" * 64
    worker.lock_fd = 77
    worker.checkout_rel = ""
    worker._ensure_scope_is_only_worker = lambda: None
    worker._ensure_job_containers_stopped = lambda **_kwargs: None
    worker._collect = lambda: None
    waits = []
    worker.waiter = waits.append
    calls = []

    def complete(details):
        assert not (job_dir / "result.json").exists()
        assert (job_dir / "result-pending.json").is_file()
        calls.append(dict(details))
        if len(calls) == 1:
            raise AdmissionError(75, "injected durable terminal write failure")

    worker._complete_admission = complete
    assert worker._finish_unstarted(79, "Run-Gate prerequisite missing") == 79
    assert len(calls) == 2
    assert waits == [1]
    assert json.loads((job_dir / "result.json").read_text(encoding="utf-8"))["verdict"] == "ERROR"
    state = worker._state()
    assert state["state"] == "failed" and state["exit_code"] == 79
    assert state["result_sha256"] == hashlib.sha256((job_dir / "result.json").read_bytes()).hexdigest()
    for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
        if fd >= 0:
            os.close(fd)


def test_synchronize_job_refs_removes_stale_refs_and_requires_a_present_base(tmp_path):
    class _GitRunner:
        def run(self, argv, *, cwd_fd, env, timeout=None):
            cwd = os.readlink(f"/proc/self/fd/{cwd_fd}")
            answer = subprocess.run(argv, cwd=cwd, capture_output=True, check=False)
            return CommandResult(answer.returncode, answer.stdout, answer.stderr)

    worker, _runner, _job_dir, checkout = _ready_worker(tmp_path, _GitRunner())
    subprocess.run(["git", "init", "--quiet", "--initial-branch=main", str(checkout)], check=True)
    (checkout / "file").write_text("commit\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(checkout), "add", "file"], check=True)
    subprocess.run([
        "git", "-C", str(checkout), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "--quiet", "-m", "seed",
    ], check=True)
    sha = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], check=True, capture_output=True, text=True,
    ).stdout.strip()
    blob = subprocess.run(
        ["git", "-C", str(checkout), "hash-object", "-w", str(checkout / "file")],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    subprocess.run(["git", "-C", str(checkout), "update-ref", "refs/remotes/origin/main", blob], check=True)
    subprocess.run(["git", "-C", str(checkout), "update-ref", "refs/heads/main", sha], check=True)
    subprocess.run(["git", "-C", str(checkout), "update-ref", "refs/remote-gate/base", sha], check=True)
    subprocess.run(["git", "-C", str(checkout), "update-ref", "refs/remotes/origin/main", sha], check=True)
    worker.request.pop("base", None)
    worker._synchronize_job_refs()
    for ref in ("refs/remotes/origin/main", "refs/heads/main", "refs/remote-gate/base"):
        answer = subprocess.run(["git", "-C", str(checkout), "show-ref", "--verify", ref], check=False)
        assert answer.returncode != 0

    subprocess.run(["git", "-C", str(checkout), "update-ref", "refs/remotes/origin/main", sha], check=True)
    worker.request["base"] = sha
    worker._synchronize_job_refs()
    answer = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "--verify", "refs/remote-gate/base^{commit}"],
        check=True, capture_output=True, text=True,
    )
    assert answer.stdout.strip() == sha
    for ref in ("refs/remotes/origin/main", "refs/heads/main"):
        answer = subprocess.run(["git", "-C", str(checkout), "show-ref", "--verify", ref], check=False)
        assert answer.returncode != 0

    worker.request["base"] = "missing-base"
    with pytest.raises(AdmissionError, match="cannot install requested base"):
        worker._synchronize_job_refs()
    for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
        os.close(fd)


@pytest.mark.parametrize("request_data, message", [
    ({"repo": "unknown", "sha": SHA}, "unknown repo"),
    ({"repo": "vbpub", "sha": "short"}, "not a full commit id"),
])
def test_worker_checkout_rechecks_repo_and_full_commit_before_path_operations(tmp_path, request_data, message):
    worker = _worker(tmp_path, _VersionRunner({}))
    worker.request = request_data
    try:
        with pytest.raises(AdmissionError, match=message):
            worker._create_checkout()
        assert worker.repo_root_fd >= 0
    finally:
        os.close(worker.repo_root_fd)


def test_host_checkout_candidates_skip_symlinks_and_reset_failures_are_closed(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / SHA).mkdir()
    (repo / ("b" * 40)).symlink_to(repo / SHA, target_is_directory=True)

    class _Runner:
        def __init__(self):
            self.calls = []

        def run(self, argv, **_kwargs):
            self.calls.append(list(argv))
            if argv[0] == "git":
                return CommandResult(1, b"", b"git refused")
            return CommandResult(0)

    runner = _Runner()
    worker = _worker(tmp_path, runner)
    os.close(worker.repo_root_fd)
    worker.repo_root = repo
    worker.repo_root_fd = os.open(repo, os.O_RDONLY | os.O_DIRECTORY)
    worker.checkout_rel = ".fleetd-worktrees/controller-run-1"
    worker.job_dir.mkdir(parents=True)
    try:
        assert worker._host_checkout_candidates() == [SHA]
        assert worker._reset_checkout("missing-seed") is False
        assert worker._reset_checkout(None) is False
        assert any(call[:2] == ["git", "init"] for call in runner.calls)
        assert sum(call[0] != "git" for call in runner.calls) == 3
    finally:
        os.close(worker.repo_root_fd)


def test_executor_remaining_directory_identity_and_state_open_refusals(tmp_path, monkeypatch):
    executor, policy, state_dir = _executor(tmp_path)
    request = _submit()
    job_dir = _durable_job(state_dir, request)
    assert executor.read_state(request["job_id"]) is None

    with pytest.raises(AdmissionError, match="has no state"):
        executor.request_stop(request["job_id"], "stop")

    jobs_dir = executor.jobs_dir
    executor.jobs_dir = tmp_path / "missing-jobs"
    with pytest.raises(AdmissionError, match="not a real directory"):
        executor._job_dir(request["job_id"], create=False)
    executor.jobs_dir = tmp_path / "jobs-link"
    executor.jobs_dir.symlink_to(jobs_dir, target_is_directory=True)
    with pytest.raises(AdmissionError, match="not a real directory"):
        executor._job_dir(request["job_id"], create=False)
    executor.jobs_dir = jobs_dir

    missing_job = "new-job"
    original_mkdir = Path.mkdir

    def fail_job_mkdir(path, *args, **kwargs):
        if path == jobs_dir / missing_job:
            raise OSError("directory unavailable")
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_job_mkdir)
    with pytest.raises(AdmissionError, match="cannot create executor job directory"):
        executor._job_dir(missing_job, create=True)
    monkeypatch.setattr(Path, "mkdir", original_mkdir)
    (jobs_dir / "private-check").mkdir(mode=0o700)
    (jobs_dir / "private-check").chmod(0o755)
    with pytest.raises(AdmissionError, match="is not private"):
        executor._job_dir("private-check", create=False)

    state_path = job_dir / "state.json"
    real_open = os.open

    def deny_state_open(path, *args, **kwargs):
        if Path(path) == state_path:
            raise PermissionError("state read denied")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(executor_module.os, "open", deny_state_open)
    with pytest.raises(AdmissionError, match="cannot open executor state"):
        executor.read_state(request["job_id"])


def test_executor_uses_default_user_resolution_and_handles_guard_probe_failure(tmp_path, monkeypatch):
    policy, _checkout = _policy(tmp_path)
    resolved = RunnerIdentity(policy.runner_user, 1001, 1002, (1002,))
    monkeypatch.setattr(executor_module, "_resolve_system_user", lambda username: resolved)
    executor = HostJobExecutor(
        tmp_path / "state", policy,
        scope_launcher=_Scope(),
        guard_presence=lambda: (_ for _ in ()).throw(OSError("guard facts unavailable")),
    )
    assert executor._resolve_runner() == resolved

    state_dir = tmp_path / "state"
    jobs_dir = state_dir / "jobs"
    jobs_dir.mkdir(parents=True)
    jobs_dir.chmod(0o700)
    request = _submit()
    request["sha"] = SHA
    job_dir = _durable_job(state_dir, request)
    handle = _Handle()
    state = executor.start(request, handle)
    assert state["guard"] == {"present": None, "reason": "not-collected"}
    assert handle.handed_off


def test_systemd_scope_launcher_handles_closed_pipe_and_query_deadline(monkeypatch):
    read_fd, write_fd = os.pipe()
    real_close = os.close
    failed_close = {write_fd: False}

    def close_once(fd):
        if fd == write_fd and not failed_close[fd]:
            failed_close[fd] = True
            raise OSError("injected close race")
        real_close(fd)

    def popen(_argv, **kwargs):
        os.write(kwargs["pass_fds"][1], b"515\n")
        return type("WorkerProcess", (), {"pid": 515, "wait": lambda _self: 0})()

    monkeypatch.setattr(executor_module.os, "close", close_once)
    launcher = SystemdScopeLauncher(
        1, popen=popen, reaper_start=lambda _wait: None,
        run=lambda argv, **_kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired(argv, 1)),
        pid_cgroup=lambda _pid: "/unit.scope",
        monotonic=iter((0.0, 0.0, 0.0, 2.0)).__next__,
    )
    try:
        with pytest.raises(AdmissionError, match="cannot confirm job scope"):
            launcher.launch("unit.scope", ["worker"], 8, read_fd, write_fd)
    finally:
        real_close(read_fd)
        real_close(write_fd)


def test_executor_atomic_state_cleanup_ignores_missing_temporary(tmp_path, monkeypatch):
    def remove_then_fail(source, _destination):
        Path(source).unlink()
        raise OSError("rename source raced away")

    monkeypatch.setattr(executor_module.os, "replace", remove_then_fail)
    with pytest.raises(AdmissionError, match="cannot persist executor state"):
        _atomic_json(tmp_path / "state.json", {"state": "running"})
    assert list(tmp_path.iterdir()) == []


def test_executor_lock_open_failure_and_post_confirmation_handoff_failure(tmp_path, monkeypatch):
    lock_dir = tmp_path / "lock-open"
    lock_dir.mkdir()
    real_open = os.open

    def fail_lock_open(path, *args, **kwargs):
        if Path(path).name == "state.lock":
            raise PermissionError("lock file denied")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(executor_module.os, "open", fail_lock_open)
    with pytest.raises(AdmissionError, match="cannot lock executor state"):
        with _job_state_lock(lock_dir):
            pytest.fail("body must not run without a lock descriptor")
    monkeypatch.setattr(executor_module.os, "open", real_open)

    handoff_root = tmp_path / "handoff"
    handoff_root.mkdir()
    executor, _policy_value, state_dir = _executor(handoff_root)
    request = _submit()
    request["sha"] = SHA
    job_dir = _durable_job(state_dir, request)

    class _FailingHandoff(_Handle):
        def handoff(self):
            raise RuntimeError("handoff was not acknowledged")

    created_pipes = []
    real_pipe = os.pipe
    real_close = os.close

    def capture_pipe():
        pair = real_pipe()
        created_pipes.extend(pair)
        return pair

    monkeypatch.setattr(executor_module.os, "pipe", capture_pipe)
    failures = set()

    def close_once(fd):
        if fd in created_pipes and fd not in failures:
            failures.add(fd)
            raise OSError("descriptor already closed")
        real_close(fd)

    monkeypatch.setattr(executor_module.os, "close", close_once)
    with pytest.raises(RuntimeError, match="handoff was not acknowledged"):
        executor.start(request, _FailingHandoff())
    durable = json.loads((job_dir / "state.json").read_text(encoding="utf-8"))
    assert durable["start_confirmed"] is True
    assert "start_error" not in durable
    assert failures == set(created_pipes)
    monkeypatch.setattr(executor_module.os, "close", real_close)
    for fd in created_pipes:
        real_close(fd)


def test_request_validation_rejects_root_repository_and_bad_components(tmp_path):
    policy, _checkout = _policy(tmp_path)
    request = _submit()
    for project in ("/absolute", "one//two", "one/../two", "one\\two"):
        candidate = dict(request, project=project)
        with pytest.raises(AdmissionError, match="project"):
            validate_job_request(candidate, policy)
    original_roots = policy.repo_roots
    object.__setattr__(policy, "repo_roots", {request["repo"]: Path("/")})
    try:
        with pytest.raises(AdmissionError, match="repo root"):
            validate_job_request(request, policy)
    finally:
        object.__setattr__(policy, "repo_roots", original_roots)


def test_executor_confirmation_state_disappearing_keeps_the_charge(tmp_path):
    executor, _policy_value, state_dir = _executor(tmp_path)
    request = _submit()
    request["sha"] = SHA
    job_dir = _durable_job(state_dir, request)
    handle = _Handle()
    executor.read_state = lambda _job_id: None
    with pytest.raises(AdmissionError, match="state disappeared"):
        executor.start(request, handle)
    saved = json.loads((job_dir / "state.json").read_text(encoding="utf-8"))
    assert saved["start_confirmed"] is False
    assert handle.live and not handle.handed_off


@pytest.mark.parametrize("failure", ["cleanup-lock", "pipe-create"])
def test_executor_start_refusal_preserves_durable_request_when_cleanup_cannot_lock(tmp_path, monkeypatch, failure):
    executor, _policy_value, state_dir = _executor(tmp_path)
    request = _submit()
    request["sha"] = SHA
    job_dir = _durable_job(state_dir, request)
    handle = _Handle()
    original_lock = executor_module._job_state_lock
    lock_calls = []

    @contextlib.contextmanager
    def fail_cleanup_lock(path):
        lock_calls.append(path)
        if len(lock_calls) > 1:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "cleanup lock unavailable")
        with original_lock(path):
            yield

    monkeypatch.setattr(executor_module, "_job_state_lock", fail_cleanup_lock)
    if failure == "cleanup-lock":
        executor.scope_launcher = type("FailedScope", (), {
            "launch": lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("scope failed")),
        })()
    else:
        monkeypatch.setattr(
            executor_module.os, "pipe",
            lambda: (_ for _ in ()).throw(OSError("pipe unavailable")),
        )
    with pytest.raises((AdmissionError, OSError, RuntimeError)):
        executor.start(request, handle)
    assert len(lock_calls) == 2
    assert json.loads((job_dir / "state.json").read_text(encoding="utf-8"))["start_confirmed"] is False
    assert handle.live and not handle.handed_off


def test_executor_scope_launch_error_ignores_handshake_close_race(tmp_path, monkeypatch):
    read_fd, write_fd = os.pipe()
    real_close = os.close

    def close_write_with_error(fd):
        if fd == write_fd:
            raise OSError("already closed")
        real_close(fd)

    monkeypatch.setattr(executor_module.os, "close", close_write_with_error)
    try:
        with pytest.raises(AdmissionError, match="cannot start job scope"):
            SystemdScopeLauncher(
                1, popen=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("not installed")),
            ).launch("unit.scope", ["worker"], 8, read_fd, write_fd)
    finally:
        monkeypatch.setattr(executor_module.os, "close", real_close)
        real_close(read_fd)
        real_close(write_fd)


def test_worker_safe_file_archive_and_tar_refusal_matrix(tmp_path, monkeypatch):
    root = tmp_path / "safe-root"
    root.mkdir()
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        ordinary = root / "ordinary"
        ordinary.write_bytes(b"abc")
        assert executor_worker_module._read_regular(ordinary) == b"abc"
        with pytest.raises(AdmissionError, match="exceeds"):
            executor_worker_module._read_regular(ordinary, limit=2)
        with pytest.raises(AdmissionError, match="cannot open"):
            executor_worker_module._read_regular(root / "missing")
        with pytest.raises(AdmissionError, match="not a regular file"):
            executor_worker_module._read_regular(root)
        (root / "link").symlink_to(ordinary)
        with pytest.raises(AdmissionError, match="cannot open"):
            executor_worker_module._read_regular(root / "link")

        with pytest.raises(AdmissionError, match="absolute non-escaping"):
            executor_worker_module._open_absolute_directory(Path("relative"))
        with pytest.raises(AdmissionError, match="absolute non-escaping"):
            executor_worker_module._open_absolute_directory(Path("/tmp/../etc"))
        outside = tmp_path / "outside"
        outside.mkdir()
        (root / "directory-link").symlink_to(outside, target_is_directory=True)
        with pytest.raises(AdmissionError, match="contains a symlink"):
            executor_worker_module._open_absolute_directory(root / "directory-link")

        nested = root / "nested"
        nested.mkdir()
        os.mkfifo(nested / "fifo")
        entries, reason = executor_worker_module._tree_entries(root_fd, "nested", reject_special=True)
        assert reason == "special-file" and entries == []

        output_fd = os.open(root / "changed.tgz", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            with pytest.raises(OSError, match="source changed"):
                executor_worker_module._write_archive(root_fd, output_fd, [("ordinary", False, 4)])
        finally:
            os.close(output_fd)

        (root / "store-dir").mkdir()
        (root / "store-dir" / "value").write_bytes(b"data")
        (root / "not-a-dir").write_text("file", encoding="utf-8")
        monkeypatch.setattr(executor_worker_module, "_STORE_MAX_BYTES", 8)
        store_fd = os.open(root / "store-ok.tgz", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            outcome = executor_worker_module._archive_store(
                root_fd, store_fd, ["not-a-dir/child", "store-dir"],
            )
            assert outcome["status"] == "collected"
            assert outcome["stores"][0]["reason"] == "not-a-directory"
            assert outcome["stores"][1]["status"] == "collected"
            assert executor_worker_module._verify_archive_fd(
                store_fd, kind="store", store_path="store-dir",
            ) > 0
        finally:
            os.close(store_fd)
    finally:
        os.close(root_fd)

    def make_archive(path, members):
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_TRUNC, 0o600)
        with os.fdopen(os.dup(fd), "wb") as stream:
            with tarfile.open(fileobj=stream, mode="w:gz") as archive:
                for name, kind, content in members:
                    item = tarfile.TarInfo(name)
                    if kind == "dir":
                        item.type = tarfile.DIRTYPE
                        item.size = 0
                        archive.addfile(item)
                    elif kind == "symlink":
                        item.type = tarfile.SYMTYPE
                        item.linkname = "target"
                        archive.addfile(item)
                    else:
                        payload = content
                        item.size = len(payload)
                        archive.addfile(item, io.BytesIO(payload))
        os.lseek(fd, 0, os.SEEK_SET)
        return fd

    cases = [
        (("../escape", "file", b"x"), "unsafe member"),
        (("unexpected/file", "file", b"x"), "unexpected path"),
        ((".assay/oversized.log", "file", b"x" * executor_worker_module._ARTIFACT_MAX_BYTES), "oversized file"),
        ((".assay/link", "symlink", b""), "unsafe member"),
    ]
    for index, (member, message) in enumerate(cases):
        fd = make_archive(tmp_path / f"artifact-{index}.tgz", [member])
        try:
            with pytest.raises(AdmissionError, match=message):
                executor_worker_module._verify_archive_fd(fd, kind="artifacts")
        finally:
            os.close(fd)

    for index, members in enumerate((
        [("elsewhere/file", "file", b"x")],
        [("store/file", "file", b"12345")],
        [("store/file", "file", b"x"), ("store/file", "file", b"y")],
    )):
        fd = make_archive(tmp_path / f"store-{index}.tgz", members)
        try:
            if index == 1:
                monkeypatch.setattr(executor_worker_module, "_STORE_MAX_BYTES", 4)
                message = "uncompressed limit"
            else:
                message = "unexpected path" if index == 0 else "unsafe member"
            with pytest.raises(AdmissionError, match=message):
                executor_worker_module._verify_archive_fd(fd, kind="store", store_path="store")
        finally:
            os.close(fd)


def test_worker_config_version_and_event_preparation_refusals(tmp_path):
    root = tmp_path / "config-root"
    root.mkdir()
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        (root / "invalid-utf8.toml").write_bytes(b"\xff")
        with pytest.raises(AdmissionError, match="unreadable"):
            executor_worker_module._parse_toml_fd(root_fd, "invalid-utf8.toml")
        (root / "large.toml").write_bytes(b"x" * (1024 * 1024 + 1))
        with pytest.raises(AdmissionError, match="exceeds 1 MiB"):
            executor_worker_module._parse_toml_fd(root_fd, "large.toml")
        (root / "run-gate.root.toml").write_text(
            "[lanes.root]\nenvironment='host-env'\n[lanes.noenv]\nenvironment=''\n"
            "[environments.host-env]\nmode='host'\n",
            encoding="utf-8",
        )
        lane, environment = executor_worker_module._lane_environment(root_fd, ".", "root")
        assert lane["environment"] == "host-env" and environment["mode"] == "host"
        with pytest.raises(AdmissionError, match="no environment"):
            executor_worker_module._lane_environment(root_fd, ".", "noenv")
        (root / "ciu.global.toml").write_text("[deploy]\nnetwork_name='plain'\n", encoding="utf-8")
        with pytest.raises(AdmissionError, match="no persistent runner"):
            executor_worker_module._exec_container_name(root_fd, "gate", {})
    finally:
        os.close(root_fd)

    assert executor_worker_module._version_from_output(b"") is None
    assert executor_worker_module._version_from_output(b"\xff") is None
    assert executor_worker_module._version_from_output(b"run-gate version unavailable\n") is None
    assert _version_key("1.2.0a1")[1] < _version_key("1.2.0b1")[1]
    assert _version_key("1.2.0.post1")[1] == 4
    assert _version_key("1.2.0.dev1")[1] == 0

    event_parent = tmp_path / "events"
    event_parent.mkdir()
    event_root_fd = os.open(event_parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        executor_worker_module._prepare_event(event_root_fd, "repo/job.jsonl")
        event = event_parent / "repo" / "job.jsonl"
        assert event.stat().st_mode & 0o777 == 0o600
        assert event.read_bytes() == b""
    finally:
        os.close(event_root_fd)


@pytest.mark.parametrize("mode", ["host", "exec"])
def test_worker_host_and_exec_prerequisites_bind_the_persistent_runner(tmp_path, mode):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "run-gate.toml").write_text(
        "[lanes.r0-r1]\nenvironment='runner'\n"
        "[environments.runner]\nmode='" + mode + "'\ncontainer_name='persistent-runner'\n",
        encoding="utf-8",
    )

    class _Runner:
        def run(self, argv, **_kwargs):
            if argv[1:] == ["--version"]:
                return CommandResult(0, b"run-gate 25.0.2\n")
            return CommandResult(0)

    class _Docker:
        def inspect(self, name):
            assert name == "persistent-runner"
            return {"Id": "a" * 64, "State": {"Running": True}}

    worker = _worker(tmp_path, _Runner())
    worker.request = {"repo": "vbpub", "project": ".", "lane": "r0-r1"}
    worker.checkout_fd = os.open(checkout, os.O_RDONLY | os.O_DIRECTORY)
    worker.docker = _Docker()
    try:
        _binary, found_mode, facts = worker._prerequisites()
        assert found_mode == mode
        if mode == "exec":
            assert facts["runner_name"] == "persistent-runner"
            assert facts["container_name"] == "persistent-runner"
        else:
            assert facts["runner_name"] is None
    finally:
        os.close(worker.checkout_fd)
        os.close(worker.repo_root_fd)


@pytest.mark.parametrize("result, message", [
    ("raise", "admission show failed"),
    (CommandResult(1, b"no"), r"failed \(rc 1\)"),
    (CommandResult(0, b"unrelated output"), "printed no enabled"),
    ("missing-cap", "publishes no gates cap"),
])
def test_worker_admission_show_failures_refuse_prerequisites(tmp_path, result, message):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "run-gate.toml").write_text(
        "[admission]\nenabled=true\n[lanes.r0-r1]\nenvironment='host'\n"
        "[environments.host]\nmode='host'\n",
        encoding="utf-8",
    )

    class _Runner:
        def run(self, argv, **_kwargs):
            if argv[1:] == ["--version"]:
                return CommandResult(0, b"run-gate 25.0.2\n")
            if result == "raise":
                raise OSError("admission provider offline")
            if result == "missing-cap":
                return CommandResult(0, b"admission: enabled=true\n")
            return result

    worker = _worker(tmp_path, _Runner())
    worker.request = {"repo": "vbpub", "project": ".", "lane": "r0-r1"}
    worker.checkout_fd = os.open(checkout, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(AdmissionError, match=message):
            worker._prerequisites()
    finally:
        os.close(worker.checkout_fd)
        os.close(worker.repo_root_fd)


def test_worker_lane_user_variants_and_declared_base_refusals(tmp_path):
    class _Docker:
        def __init__(self):
            self.checked = []

        def check_passwd(self, image, user, job_id, *, expected_gid=None):
            self.checked.append((image, user, job_id, expected_gid))

    worker = _worker(tmp_path, _VersionRunner({}))
    docker = _Docker()
    worker.docker = docker
    worker.runner_identity = RunnerIdentity("runner", 1001, 1002, (1002,))
    try:
        worker._check_lane_user("image", {"Config": {"User": "runner"}}, {})
        worker._check_lane_user("image", {}, {"user": "1001:1002"})
        worker._check_lane_user("image", {}, {"user": "0:0"})
        assert docker.checked == [
            ("image", "runner", worker.job_id, None),
            ("image", "1001", worker.job_id, 1002),
        ]
        with pytest.raises(AdmissionError, match="gid is invalid"):
            worker._check_lane_user("image", {}, {"user": "1001:bad"})
        worker.runner_identity = RunnerIdentity("root", 0, 0, (0,))
        with pytest.raises(AdmissionError, match="invoker resolves to uid 0"):
            worker._check_lane_user("image", {}, {"user": "invoker"})

        for lane in ({"base": "-bad"}, {"base": ""}):
            with pytest.raises(AdmissionError, match="invalid comparison base"):
                worker._check_declared_base(lane)
        worker._run = lambda *_args, **_kwargs: CommandResult(1)
        with pytest.raises(AdmissionError, match="missing from the host checkout"):
            worker._check_declared_base({"base": "missing"})
        worker._check_declared_base({})
    finally:
        os.close(worker.repo_root_fd)


def test_worker_select_run_gate_retries_bad_discovery_and_rejects_unusable_tools(tmp_path):
    from fleetd.executor_worker import _RG_FALLBACK

    class _Runner:
        def __init__(self, responses):
            self.responses = list(responses)
            self.calls = []

        def run(self, argv, **_kwargs):
            self.calls.append(list(argv))
            response = self.responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response

    runner = _Runner([
        OSError("discovered executable disappeared"),
        CommandResult(0, b"run-gate 25.0.2\n"),
    ])
    worker = _worker(tmp_path, runner, finder=lambda _name: "/discovered/run-gate")
    try:
        assert worker._select_run_gate() == (_RG_FALLBACK, "25.0.2")
        assert [call[0] for call in runner.calls] == [
            "/discovered/run-gate", "/home/vscode/.venvs/gate-tools2502/bin/run-gate",
        ]
    finally:
        os.close(worker.repo_root_fd)

    second = tmp_path / "second"
    second.mkdir()
    runner = _Runner([
        CommandResult(0, b"unrecognized tool output\n"),
        subprocess.TimeoutExpired(["run-gate", "--version"], 1),
    ])
    worker = _worker(second, runner, finder=lambda _name: "/discovered/run-gate")
    try:
        with pytest.raises(AdmissionError, match="no usable run-gate"):
            worker._select_run_gate()
    finally:
        os.close(worker.repo_root_fd)


def test_worker_fetch_uploaded_bundle_skips_bad_candidate_and_imports_refs_and_tags(tmp_path):
    worker = _worker(tmp_path, None)
    state_uploads = worker.state_dir / "uploads" / "controller" / "in.bundle"
    state_uploads.mkdir(parents=True)
    objects = {b"first candidate": None, b"second candidate": None}
    digests = []
    for content in objects:
        digest = hashlib.sha256(content).hexdigest()
        digests.append(digest)
        (state_uploads / digest).write_bytes(content)
    digests.sort()
    request_sha = SHA
    worker.request = {"repo": "vbpub", "sha": request_sha}
    worker.group = "controller"
    worker.checkout_rel = ".fleetd-worktrees/controller-run-1"
    worker._reset_checkout = lambda seed: True
    removed = []
    worker._helper = lambda mode, *args, **_kwargs: removed.append((mode, args)) or CommandResult(0)

    class _Runner:
        def __init__(self):
            self.calls = []
            self.verified = []

        def run_with_input(self, _argv, **_kwargs):
            return CommandResult(0)

        def run(self, argv, **_kwargs):
            call = list(argv)
            self.calls.append(call)
            if "verify" in call:
                digest = call[-1].split("bundle-", 1)[1]
                self.verified.append(digest)
                return CommandResult(0 if digest == digests[1] else 1)
            if call[1:3] == ["ls-remote", call[-2]]:
                return CommandResult(
                    0,
                    b"abc\trefs/tags/existing-v25.1.0\nabc\trefs/tags/new-v7.2.0\n"
                    b"abc\trefs/tags/notes\nabc\trefs/tags/existing-v25.1.0^{}\n",
                )
            if "show-ref" in call:
                return CommandResult(0 if call[-1].endswith("existing-v25.1.0") else 1)
            if "cat-file" in call:
                return CommandResult(0 if call[-1] == f"{request_sha}^{{commit}}" else 1)
            return CommandResult(0)

    runner = _Runner()
    worker.runner = runner
    worker._fetch_uploaded_bundle(seeds=("seed",))
    assert runner.verified == [digests[0], digests[0], digests[1]]
    assert any(call[-1] == f"+refs/remote-gate/base:refs/remote-gate/base" for call in runner.calls)
    assert any(call[-1] == "+refs/tags/new-v7.2.0:refs/tags/new-v7.2.0" for call in runner.calls)
    assert not any("refs/remotes/origin/main" in call for call in runner.calls)
    assert not any("refs/tags/notes" in call for call in runner.calls)
    assert len(removed) == 2 and all(mode == "remove-tree" for mode, _args in removed)
    os.close(worker.repo_root_fd)


def test_worker_uploaded_bundle_missing_objects_and_invalid_sha_refuse(tmp_path):
    worker = _worker(tmp_path, _VersionRunner({}))
    worker.state_dir.mkdir()
    worker.request = {"sha": SHA}
    worker.group = "controller"
    with pytest.raises(AdmissionError, match="no verified in.bundle"):
        worker._fetch_uploaded_bundle()

    uploads = worker.state_dir / "uploads" / "controller" / "in.bundle"
    uploads.mkdir(parents=True)
    payload = b"bundle"
    (uploads / hashlib.sha256(payload).hexdigest()).write_bytes(payload)
    worker.request["sha"] = "bad"
    with pytest.raises(AdmissionError, match="sha is invalid"):
        worker._fetch_uploaded_bundle()
    os.close(worker.repo_root_fd)


def test_worker_checkout_refuses_failed_seed_missing_base_and_commit_mismatch(tmp_path):
    worker, runner, _job_dir, _checkout = _ready_worker(tmp_path, _VersionRunner({}))
    worker.request = {"repo": "vbpub", "sha": SHA}
    worker._host_checkout_candidates = lambda: [SHA]
    worker._reset_checkout = lambda _seed: False
    worker._helper = lambda *_args, **_kwargs: CommandResult(0)
    try:
        with pytest.raises(AdmissionError, match="cannot clone host checkout seed"):
            worker._create_checkout()
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)

    missing_base = tmp_path / "missing-base"
    missing_base.mkdir()

    class _GitRunner:
        def __init__(self, *, base_missing=False, resolved=SHA):
            self.base_missing = base_missing
            self.resolved = resolved

        def run(self, argv, **_kwargs):
            if argv[0] == "git" and argv[3] == "cat-file" and self.base_missing:
                return CommandResult(1, b"", b"missing")
            if argv[0] == "git" and argv[3] == "checkout":
                return CommandResult(0)
            if argv[0] == "git" and argv[3] == "rev-parse":
                return CommandResult(0, (self.resolved + "\n").encode("ascii"))
            return CommandResult(0)

    worker, _runner, _job_dir, _checkout = _ready_worker(
        missing_base, _GitRunner(base_missing=True),
    )
    worker.request = {"repo": "vbpub", "sha": SHA, "base": "refs/heads/missing"}
    worker._host_checkout_candidates = lambda: []
    worker._fetch_uploaded_bundle = lambda _seeds: None
    worker._synchronize_job_refs = lambda: None
    worker._helper = lambda *_args, **_kwargs: CommandResult(0)
    try:
        with pytest.raises(AdmissionError, match="base .* missing on the host"):
            worker._create_checkout()
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)

    mismatch = tmp_path / "mismatch"
    mismatch.mkdir()
    worker, _runner, _job_dir, _checkout = _ready_worker(
        mismatch, _GitRunner(resolved="b" * 40),
    )
    worker.request = {"repo": "vbpub", "sha": SHA}
    worker._host_checkout_candidates = lambda: []
    worker._fetch_uploaded_bundle = lambda _seeds: None
    worker._synchronize_job_refs = lambda: None
    worker._helper = lambda *_args, **_kwargs: CommandResult(0)
    try:
        with pytest.raises(AdmissionError, match="does not match request sha"):
            worker._create_checkout()
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize("event, message", [
    ({"api": "wrong", "job_id": "controller-run-1", "event": "lane-verdict",
      "lane": "r0-r1", "verdict": "PASS", "exit_code": 0, "lane_exit_code": 0}, "invalid identity"),
    ({"api": "fleet-host-agent/1", "job_id": "other", "event": "lane-verdict",
      "lane": "r0-r1", "verdict": "PASS", "exit_code": 0, "lane_exit_code": 0}, "invalid identity"),
    ({"api": "fleet-host-agent/1", "job_id": "controller-run-1", "event": "unknown",
      "lane": "r0-r1", "verdict": "PASS", "exit_code": 0, "lane_exit_code": 0}, "invalid identity"),
    ({"api": "fleet-host-agent/1", "job_id": "controller-run-1", "event": "lane-started",
      "lane": "r0-r1", "container_id": 1, "image_id": None, "adopted": False}, "invalid lane-started fields"),
    ({"api": "fleet-host-agent/1", "job_id": "controller-run-1", "event": "lane-started",
      "lane": "r0-r1", "container_id": None, "image_id": None, "adopted": 1}, "invalid lane-started fields"),
    ({"api": "fleet-host-agent/1", "job_id": "controller-run-1", "event": "lane-started",
      "lane": "r0-r1", "container_id": None, "image_id": None, "adopted": False, "reason": 1},
     "invalid lane-started fields"),
    ({"api": "fleet-host-agent/1", "job_id": "controller-run-1", "event": "lane-started",
      "lane": "r0-r1", "container_id": None, "image_id": 1, "adopted": False}, "invalid lane-started fields"),
    ({"api": "fleet-host-agent/1", "job_id": "controller-run-1", "event": "lane-verdict",
      "lane": "r0-r1", "verdict": "UNKNOWN", "exit_code": 0, "lane_exit_code": 0}, "invalid lane-verdict fields"),
    ({"api": "fleet-host-agent/1", "job_id": "controller-run-1", "event": "lane-verdict",
      "lane": "r0-r1", "verdict": "PASS", "exit_code": True, "lane_exit_code": 0}, "invalid lane-verdict fields"),
    ({"api": "fleet-host-agent/1", "job_id": "controller-run-1", "event": "lane-verdict",
      "lane": "r0-r1", "verdict": "PASS", "exit_code": 0, "lane_exit_code": False}, "invalid lane-verdict fields"),
    (["not", "an", "object"], "invalid identity"),
])
def test_event_reader_refuses_closed_identity_and_field_mismatches(tmp_path, event, message):
    worker = _worker(tmp_path, _VersionRunner({}))
    worker.runner_identity = RunnerIdentity("runner", os.getuid(), os.getgid(), (os.getgid(),))
    repo = tmp_path / "repo"
    repo.mkdir()
    event_dir = tmp_path / ".fleetd-events" / "vbpub"
    event_dir.mkdir(parents=True)
    path = event_dir / "controller-run-1.jsonl"
    path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    path.chmod(0o600)
    worker.repo_root = repo
    worker.event_rel = ".fleetd-events/vbpub/controller-run-1.jsonl"
    worker.request = {"repo": "vbpub", "project": ".", "lane": "r0-r1"}
    try:
        with pytest.raises(AdmissionError, match=message):
            worker._read_events()
    finally:
        os.close(worker.repo_root_fd)


@pytest.mark.parametrize("contents, message", [
    (b"\xff\n", "unreadable"),
    (b"not-json\n", "unreadable"),
    (b"x" * 65537 + b"\n", "incomplete or too large"),
])
def test_event_reader_refuses_unreadable_or_oversized_lines(tmp_path, contents, message):
    worker = _worker(tmp_path, _VersionRunner({}))
    worker.runner_identity = RunnerIdentity("runner", os.getuid(), os.getgid(), (os.getgid(),))
    repo = tmp_path / "repo"
    repo.mkdir()
    event_dir = tmp_path / ".fleetd-events" / "vbpub"
    event_dir.mkdir(parents=True)
    path = event_dir / "controller-run-1.jsonl"
    path.write_bytes(contents)
    path.chmod(0o600)
    worker.repo_root = repo
    worker.event_rel = ".fleetd-events/vbpub/controller-run-1.jsonl"
    try:
        with pytest.raises(AdmissionError, match=message):
            worker._read_events()
    finally:
        os.close(worker.repo_root_fd)


@pytest.mark.parametrize("summary, message", [
    ("", "summary is missing"),
    ("run-gate: lane 'other' verdict PASS; exit_code 0\n", "summary is missing"),
    ("run-gate: lane 'r0-r1' verdict MAYBE; exit_code 0\n", "unknown verdict"),
    ("run-gate: lane 'r0-r1' verdict PASS; exit_code 0; exit_code 1\n", "repeats exit_code"),
    ("run-gate: lane 'r0-r1' verdict PASS; exit_code nope\n", "invalid exit_code"),
    ("run-gate: lane 'r0-r1' verdict PASS; exit_code ", "incomplete"),
    ("run-gate: lane 'r0-r1' verdict " + "x" * 65537 + "\n", "too large"),
])
def test_last_lane_summary_requires_one_exact_complete_anchored_record(tmp_path, summary, message):
    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path)
    worker.request["lane"] = "r0-r1"
    (job_dir / "lane.log").write_text(summary, encoding="utf-8")
    try:
        with pytest.raises(AdmissionError, match=message):
            worker._last_lane_summary()
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_worker_archive_streaming_failure_success_and_store_noop(tmp_path, monkeypatch):
    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path)

    class _Runner:
        def __init__(self, status=0, stdout=b'{"files":1}', content=b"archive"):
            self.status = status
            self.stdout = stdout
            self.content = content

        def run_to_fd(self, _argv, *, output_fd, **_kwargs):
            if self.content:
                os.write(output_fd, self.content)
            return CommandResult(self.status, self.stdout, b"fake archive stderr")

    try:
        worker.runner = object()
        with pytest.raises(AdmissionError, match="cannot stream"):
            worker._write_archive_from_runner(
                "archive-artifacts", "artifacts.tgz", (), root_fd=worker.checkout_fd, kind="artifacts",
            )

        worker.runner = _Runner(status=1)
        with pytest.raises(AdmissionError, match="fake archive stderr"):
            worker._write_archive_from_runner(
                "archive-artifacts", "artifacts.tgz", (), root_fd=worker.checkout_fd, kind="artifacts",
            )
        assert not (job_dir / ".artifacts.tgz.part").exists()

        worker.runner = _Runner(stdout=b"not-json")
        with pytest.raises(AdmissionError, match="invalid artifacts collection details"):
            worker._write_archive_from_runner(
                "archive-artifacts", "artifacts.tgz", (), root_fd=worker.checkout_fd, kind="artifacts",
            )
        worker.runner = _Runner(stdout=b"[]")
        with pytest.raises(AdmissionError, match="invalid artifacts collection details"):
            worker._write_archive_from_runner(
                "archive-artifacts", "artifacts.tgz", (), root_fd=worker.checkout_fd, kind="artifacts",
            )

        worker.runner = _Runner(stdout=b'{"status":"none"}')
        assert worker._write_archive_from_runner(
            "archive-store", "assay-store.tgz", (), root_fd=worker.checkout_fd,
            kind="store", store_path="store",
        ) == ("", 0, {"status": "none"})

        monkeypatch.setattr(executor_worker_module, "_verify_archive_fd", lambda fd, **_kwargs: os.fstat(fd).st_size)
        worker.runner = _Runner()
        digest, size, details = worker._write_archive_from_runner(
            "archive-artifacts", "artifacts.tgz", (), root_fd=worker.checkout_fd, kind="artifacts",
        )
        assert digest == hashlib.sha256(b"archive").hexdigest()
        assert size == len(b"archive") and details == {"files": 1}
        assert (job_dir / ".artifacts.tgz.part").is_file()
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_worker_saved_collection_requires_archive_and_checksum_agreement(tmp_path, monkeypatch):
    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path)
    store_rel = ["store", "project/.assay"]
    store_digest = "a" * 64
    artifact_digest = "b" * 64
    collection = {
        "store_rel": store_rel,
        "artifacts_sha256": artifact_digest,
        "artifacts_size": 12,
        "store": {"status": "collected", "sha256": store_digest, "size": 9},
    }
    worker._save_state(collection=collection)
    (job_dir / "assay-store.tgz.sha256").write_text(
        f"{store_digest}  assay-store.tgz\n", encoding="ascii",
    )
    worker._archive_saved = lambda name, **_kwargs: (
        (artifact_digest, 12) if name == "artifacts.tgz" else (store_digest, 9)
    )
    try:
        assert worker._saved_collection(store_rel) == collection
        assert worker._saved_collection("store") is None
        worker._save_state(collection={**collection, "artifacts_size": 11})
        assert worker._saved_collection(store_rel) is None
        worker._save_state(collection=collection)
        (job_dir / "assay-store.tgz.sha256").write_text("wrong\n", encoding="ascii")
        assert worker._saved_collection(store_rel) is None
        (job_dir / "assay-store.tgz.sha256").write_text(
            f"{store_digest}  assay-store.tgz\n", encoding="ascii",
        )
        worker._archive_saved = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AdmissionError(ExitCode.STORE_COLLECTION_FAILED, "archive changed"),
        )
        assert worker._saved_collection(store_rel) is None
        worker._save_state(collection={
            **collection, "store_rel": None, "store": {"status": "collected"},
        })
        assert worker._saved_collection(None) is None
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_worker_pending_terminal_closed_shape_and_publication_order(tmp_path):
    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path)
    terminal = worker._simple_result("ERROR", 79, "test refusal")
    worker._persist_pending_terminal(terminal)
    assert worker._read_pending_terminal() == terminal
    (job_dir / "terminal-pending.json").write_text("{}", encoding="utf-8")
    with pytest.raises(AdmissionError, match="invalid closed shape"):
        worker._read_pending_terminal()
    worker._persist_pending_terminal(terminal)
    waits = []
    worker.waiter = waits.append
    completions = []

    def complete(details):
        assert not (job_dir / "result.json").exists()
        assert (job_dir / "result-pending.json").is_file()
        completions.append(dict(details))
        if len(completions) == 1:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "record fsync failed")

    worker._complete_admission = complete
    worker._publish_terminal(terminal, final_state="failed", reason="test refusal")
    assert len(completions) == 2 and waits == [1]
    assert (job_dir / "result.json").is_file()
    assert not (job_dir / "result-pending.json").exists()
    assert not (job_dir / "terminal-pending.json").exists()
    state = worker._state()
    assert state["state"] == "failed" and state["verdict"] == "ERROR"
    for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
        if fd >= 0:
            os.close(fd)


def test_worker_collect_validates_paths_promotes_one_store_and_reuses_collection(tmp_path):
    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path)
    try:
        for stores in (
            {"collect": 1}, {"collect": []}, {"collect": ["../escape"]},
            {"collect": ["store", "store"]}, {"collect": [".git/config"]},
        ):
            worker.request["stores"] = stores
            with pytest.raises(AdmissionError, match="stores.collect"):
                worker._collect()

        worker.request["stores"] = {"collect": ["store"]}
        worker._saved_collection = lambda _store_rel: None
        calls = []

        def write_archive(_helper, destination, _arguments, *, kind, **_kwargs):
            calls.append((destination, kind))
            content = b"artifacts archive" if kind == "artifacts" else b"store archive"
            part = job_dir / f".{destination}.part"
            part.write_bytes(content)
            if kind == "artifacts":
                return hashlib.sha256(content).hexdigest(), len(content), {"files": 2}
            return hashlib.sha256(content).hexdigest(), len(content), {
                "status": "collected", "stores": [{"path": "store", "status": "collected"}],
                "logs": ["STORE-COLLECTED store", 7],
            }

        worker._write_archive_from_runner = write_archive
        collection = worker._collect()
        assert collection["store_rel"] == ["store"]
        assert collection["store"]["status"] == "collected"
        assert (job_dir / "artifacts.tgz").is_file()
        assert (job_dir / "assay-store.tgz").is_file()
        store_digest = hashlib.sha256((job_dir / "assay-store.tgz").read_bytes()).hexdigest()
        assert (job_dir / "assay-store.tgz.sha256").read_text(encoding="ascii") == (
            f"{store_digest}  assay-store.tgz\n"
        )
        assert calls == [("artifacts.tgz", "artifacts"), ("assay-store.tgz", "store")]
        log = (job_dir / "job.log").read_text(encoding="utf-8")
        assert "STORE-COLLECTED store" in log

        worker._saved_collection = lambda _store_rel: collection
        assert worker._collect() == collection
        assert calls == [("artifacts.tgz", "artifacts"), ("assay-store.tgz", "store")]
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_worker_collect_refuses_unverified_checkout_and_bad_project(tmp_path):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    checkout_fd = worker.checkout_fd
    project_fd = worker.project_fd
    worker.checkout_fd = -1
    try:
        with pytest.raises(AdmissionError, match="without a verified checkout"):
            worker._collect()
        worker.checkout_fd = checkout_fd
        worker.project_fd = -1
        worker.request["project"] = 1
        with pytest.raises(AdmissionError, match="request project is invalid"):
            worker._collect()
    finally:
        worker.project_fd = project_fd
        worker.checkout_fd = checkout_fd
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize("damage, expected", [
    ("no-event", "exactly one lane-verdict"),
    ("duplicate-event", "exactly one lane-verdict"),
    ("wrong-lane", "different lane"),
    ("process-status", "Run-Gate process status"),
    ("summary-verdict", "anchored summary"),
    ("summary-lane-status", "lane_exit_code"),
    ("missing-checkout-line", "host checkout line"),
    ("missing-summary", "summary is missing"),
    ("missing-container", "disappeared before binding"),
    ("inspection-error", "inspect denied"),
    ("lane-error", "damaged event transport"),
])
def test_verdict_binding_refuses_each_unproved_host_fact(tmp_path, damage, expected):
    worker, _runner, job_dir, checkout = _ready_worker(tmp_path)
    worker.request.update(repo="vbpub", project=".", sha=SHA, lane="r0-r1")
    worker._save_state(
        lane_mode="host", runner_name=None,
        checkout={"ready": True, "sha": SHA},
        collection={"collected_at": "2026-11-27T00:00:00Z"},
    )
    (checkout / "run-gate.toml").write_text(
        "[lanes.r0-r1]\nenvironment='host'\n[environments.host]\nmode='host'\n",
        encoding="utf-8",
    )
    (job_dir / "job.log").write_text(
        f"2026-11-27T00:00:00Z checkout {worker.repo_root / worker.checkout_rel} at {SHA}\n",
        encoding="utf-8",
    )
    (job_dir / "lane.log").write_text(
        "run-gate: lane 'r0-r1' verdict PASS; exit_code 0; log lane.log\n",
        encoding="utf-8",
    )
    event = {
        "api": "fleet-host-agent/1", "job_id": worker.job_id, "event": "lane-verdict",
        "lane": "r0-r1", "verdict": "PASS", "exit_code": 0, "lane_exit_code": 0,
    }
    events = [event]
    containers = []
    process_status = 0
    if damage == "no-event":
        events = []
    elif damage == "duplicate-event":
        events = [event, event]
    elif damage == "wrong-lane":
        events = [dict(event, lane="other")]
    elif damage == "process-status":
        events = [dict(event, exit_code=1)]
    elif damage == "summary-verdict":
        (job_dir / "lane.log").write_text(
            "run-gate: lane 'r0-r1' verdict FAIL; exit_code 1; log lane.log\n",
            encoding="utf-8",
        )
    elif damage == "summary-lane-status":
        events = [dict(event, lane_exit_code=7)]
    elif damage == "missing-checkout-line":
        (job_dir / "job.log").write_text("no checkout fact\n", encoding="utf-8")
    elif damage == "missing-summary":
        (job_dir / "lane.log").write_text("ordinary output\n", encoding="utf-8")
    elif damage == "missing-container":
        containers = [{"id": "a" * 64, "status": "missing-object"}]
    elif damage == "inspection-error":
        containers = [{"id": "a" * 64, "status": "inspection-error", "reason": "inspect denied"}]
    elif damage == "lane-error":
        worker.lane_error = "damaged event transport"

    try:
        terminal = worker._bind_verdict(events, process_status, containers)
        result = json.loads((job_dir / "result-pending.json").read_text(encoding="utf-8"))
        assert terminal["verdict"] == "ERROR"
        assert result["binding"]["status"] == "refused"
        assert any(expected in reason for reason in result["binding"]["reasons"])
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_container_event_binding_refuses_missing_or_error_observations(tmp_path):
    container_id = "c" * 64
    worker, _runner, _job_dir, checkout = _ready_worker(tmp_path)
    worker.request.update(repo="vbpub", project=".", sha=SHA, lane="r0-r1")
    worker._save_state(lane_mode="ephemeral", runner_name=None)
    (checkout / "run-gate.toml").write_text(
        "[lanes.r0-r1]\nenvironment='test'\n[environments.test]\nmode='ephemeral'\nimage='test:local'\n",
        encoding="utf-8",
    )
    event = {"event": "lane-started", "lane": "r0-r1", "container_id": container_id}
    try:
        missing = worker._bind_container_events([event], expected_sha=SHA)
        assert missing[0]["status"] == "inspection-error"
        assert "not durably observed" in missing[0]["reason"]
        worker._save_state(lane_event_observations=[{
            "index": 0, "event": event, "error": "container disappeared before observation",
        }])
        errored = worker._bind_container_events([event], expected_sha=SHA)
        assert errored[0]["status"] == "inspection-error"
        assert errored[0]["reason"] == "container disappeared before observation"
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize("failure, message", [
    ("provider", "provider is unavailable"),
    ("query", "query is offline"),
    ("invalid-query", "invalid result"),
    ("short-id", "non-full id"),
    ("inspect", "inspect failed"),
    ("mismatched-id", "did not confirm exact id"),
    ("wrong-label", "does not carry run-gate.job"),
    ("bad-state", "state.*unreadable"),
    ("remove", "remove.*failed"),
])
def test_worker_container_stop_proofs_refuse_every_unconfirmed_boundary(tmp_path, failure, message):
    container_id = "f" * 64

    class _Docker:
        def job_containers(self, _job_id):
            if failure == "query":
                raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "query is offline")
            if failure == "invalid-query":
                return "not-a-sequence"
            if failure == "short-id":
                return ("short",)
            return (container_id,)

        def inspect(self, _target):
            if failure == "inspect":
                raise OSError("inspect failed")
            answer = {
                "Id": "0" * 64 if failure == "mismatched-id" else container_id,
                "Config": {"Labels": {"run-gate.job": "wrong-job" if failure == "wrong-label" else "controller-run-1"}},
                "State": "unreadable" if failure == "bad-state" else {"Running": False},
            }
            return answer

        def remove_stopped(self, _container_id):
            if failure == "remove":
                raise OSError("remove failed")

    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.docker = object() if failure == "provider" else _Docker()
    worker.waiter = lambda _seconds: None
    try:
        with pytest.raises(AdmissionError, match=message):
            worker._ensure_job_containers_stopped(remove=True)
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_worker_container_stop_proof_retries_running_and_accepts_explicitly_missing_id(tmp_path):
    container_id = "f" * 64

    class _Docker:
        def __init__(self, missing=False):
            self.missing = missing
            self.query_count = 0
            self.inspect_count = 0
            self.removed = []

        def job_containers(self, _job_id):
            self.query_count += 1
            return (container_id,) if self.query_count < 3 else ()

        def inspect(self, _target):
            self.inspect_count += 1
            if self.missing:
                return None
            return {
                "Id": container_id,
                "Config": {"Labels": {"run-gate.job": "controller-run-1"}},
                "State": {"Running": self.inspect_count == 1},
            }

        def remove_stopped(self, target):
            self.removed.append(target)

    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    docker = _Docker()
    worker.docker = docker
    waits = []
    worker.waiter = waits.append
    try:
        worker._ensure_job_containers_stopped(remove=True)
        assert waits == [1, 1]
        assert docker.query_count == 3 and docker.inspect_count == 2
        assert docker.removed == [container_id]
        assert worker._state()["live_containers"] == []
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)

    missing_root = tmp_path / "missing"
    missing_root.mkdir()
    worker, _runner, _job_dir, _checkout = _ready_worker(missing_root)
    worker.docker = _Docker(missing=True)
    worker.waiter = lambda _seconds: None
    try:
        worker._ensure_job_containers_stopped(remove=True)
        assert worker._state()["live_containers"] == []
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_worker_scope_proof_requires_this_worker_alone(tmp_path):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    try:
        worker._save_state(scope=None)
        with pytest.raises(AdmissionError, match="scope identity is unavailable"):
            worker._ensure_scope_is_only_worker()
        worker._save_state(scope=f"fleetd-job-{worker.job_id}.scope")
        worker.scope_process_reader = lambda _unit: (os.getpid(),)
        worker._ensure_scope_is_only_worker()
        worker.scope_process_reader = lambda _unit: (os.getpid(), os.getpid() + 1)
        with pytest.raises(AdmissionError, match="expected only worker"):
            worker._ensure_scope_is_only_worker()
        worker.scope_process_reader = lambda _unit: (_ for _ in ()).throw(OSError("cgroup unavailable"))
        with pytest.raises(AdmissionError, match="cannot inspect executor scope"):
            worker._ensure_scope_is_only_worker()
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_worker_retry_state_save_recovers_and_observes_cancel_during_retry(tmp_path):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker._save_state(cancel_requested=True, cancel_reason="cancel while saving")
    real_save = worker._save_state
    attempts = []

    def fail_once(**updates):
        attempts.append(dict(updates))
        if len(attempts) == 1:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "transient state fsync")
        return real_save(**updates)

    signals = []
    worker._save_state = fail_once
    worker.runner_process = type("Process", (), {"pid": 777, "poll": lambda _self: None})()
    worker.process_signals = type("Signals", (), {
        "terminate_group": lambda _self, pid: signals.append(pid),
    })()
    waits = []
    worker.waiter = waits.append
    try:
        state = worker._retry_state_save(retry_marker="persisted")
        assert state["retry_marker"] == "persisted"
        assert waits == [1]
        assert signals == [777]
        assert worker._state()["stop_signal"] == "sent"
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_worker_stops_live_lane_when_cancel_arrives_before_pid_state_is_durable(tmp_path):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.request.update(repo="vbpub", project=".", lane="r0-r1", extra_argv=[], base=None)
    worker._save_state(cancel_requested=False, guard={"present": False})
    worker._helper = lambda *_args, **_kwargs: CommandResult(0)
    worker._follow_lane_events = lambda **_kwargs: None
    retry_save = worker._retry_state_save

    def omit_pid_until_exit(**updates):
        updates.pop("lane_pid", None)
        return retry_save(**updates)

    worker._retry_state_save = omit_pid_until_exit
    lifecycle = []

    class _Process:
        pid = 779
        status = None

        def poll(self):
            return self.status

    process = _Process()

    class _Runner:
        def start(self, _argv, **_kwargs):
            lifecycle.append("start")
            return process

    worker.runner = _Runner()

    class _Signals:
        def terminate_group(self, pid):
            assert pid == process.pid
            lifecycle.append("term")
            process.status = 143

    worker.process_signals = _Signals()

    def cancel_during_settle(_seconds):
        lifecycle.append("wait")
        worker._save_state(cancel_requested=True, cancel_reason="operator")

    worker.waiter = cancel_during_settle
    memory_lock = os.open(tmp_path / "memory.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        assert worker._run_lane("run-gate", "host", {"runner_name": None}, memory_lock) == 143
        state = worker._state()
        assert state.get("lane_pid") is None
        assert state["lane_process_status"] == 143
        assert state["stop_signal"] == "sent"
        assert lifecycle == ["start", "wait", "term"]
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize("case, expected_signal, expected_state", [
    ("exec", False, "exec-cancel-cannot-prove-runner-process-stopped"),
    ("guard-missing", False, "thrashguard-deregister-unavailable"),
    ("guard-error", False, "thrashguard-deregister-failed"),
    ("guard-unknown", False, "thrashguard-presence-unknown"),
    ("term-lookup", True, "process-already-exited"),
    ("term-error", True, "failed"),
    ("term-success", True, "sent"),
    ("already-signaled", False, "sent"),
    ("no-poll", True, "sent"),
    ("poll-error", True, "sent"),
    ("poll-exited", False, "lane-exited"),
])
def test_worker_stop_intent_covers_guard_and_term_outcomes(tmp_path, case, expected_signal, expected_state):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    guard_calls = []
    signal_calls = []

    class _Guard:
        def deregister(self, job_id):
            guard_calls.append(job_id)
            if case == "guard-error":
                raise RuntimeError("deregister offline")

    class _Signals:
        def terminate_group(self, pid):
            signal_calls.append(pid)
            if case == "term-lookup":
                raise ProcessLookupError("already stopped")
            if case == "term-error":
                raise OSError("TERM denied")

    class _Process:
        pid = 778

        def poll(self):
            if case == "poll-error":
                raise RuntimeError("poll failed")
            if case == "poll-exited":
                return 5
            return None

    present = True if case in {"guard-missing", "guard-error"} else None if case == "guard-unknown" else False
    worker._save_state(
        lane_mode="exec" if case == "exec" else "host",
        stop_signal="sent" if case == "already-signaled" else None,
        guard={"present": present},
    )
    worker.thrashguard_client = _Guard() if case not in {"guard-missing", "guard-unknown"} else None
    worker.process_signals = _Signals()
    try:
        process = type("ProcessWithoutPoll", (), {"pid": 778})() if case == "no-poll" else _Process()
        worker._request_worker_stop(process)
        state = worker._state()
        if case == "exec":
            assert expected_state in state["reason"]
        elif expected_state == "lane-exited":
            assert state["lane_process_status"] == 5
        elif expected_state in {"process-already-exited", "failed", "sent"}:
            assert state.get("stop_signal") == expected_state
        else:
            assert expected_state in state["reason"]
        assert bool(signal_calls) is expected_signal
        if case == "guard-error":
            assert guard_calls == [worker.job_id]
        elif case == "guard-missing":
            assert guard_calls == []
        elif case == "term-success":
            assert guard_calls == []
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_worker_main_refuses_nonregular_lock_and_incomplete_handshake(tmp_path, monkeypatch, capsys):
    policy, _checkout = _policy(tmp_path)
    monkeypatch.setattr(executor_worker_module, "load_agent_policy", lambda _path: policy)
    lock_dir = tmp_path / "lock-directory"
    lock_dir.mkdir()
    lock_fd = os.open(lock_dir, os.O_RDONLY | os.O_DIRECTORY)
    read_fd, write_fd = os.pipe()
    try:
        result = worker_main([
            "worker", "--state-dir", str(tmp_path / "state"), "--policy", str(tmp_path / "agent.toml"),
            "--job-id", "controller-run-1", "--lock-fd", str(lock_fd), "--handshake-fd", str(write_fd),
        ])
        assert result == int(ExitCode.RUNNER_FAILURE)
        assert "not a regular file" in capsys.readouterr().err
        os.close(write_fd)
        write_fd = -1
        assert os.read(read_fd, 1) == b""
    finally:
        os.close(lock_fd)
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)

    lock_fd = os.open(tmp_path / "lock-file", os.O_CREAT | os.O_RDWR, 0o600)
    read_fd, write_fd = os.pipe()
    real_write = os.write
    monkeypatch.setattr(
        executor_worker_module.os, "write",
        lambda fd, body: len(body) - 1 if fd == write_fd else real_write(fd, body),
    )
    try:
        result = worker_main([
            "worker", "--state-dir", str(tmp_path / "state"), "--policy", str(tmp_path / "agent.toml"),
            "--job-id", "controller-run-1", "--lock-fd", str(lock_fd), "--handshake-fd", str(write_fd),
        ])
        assert result == int(ExitCode.RUNNER_FAILURE)
        assert "handshake was incomplete" in capsys.readouterr().err
        os.close(write_fd)
        write_fd = -1
        assert os.read(read_fd, 64) == b""
    finally:
        monkeypatch.setattr(executor_worker_module.os, "write", real_write)
        os.close(lock_fd)
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)


def test_stdin_file_rejects_invalid_size_and_preserves_existing_target(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        invalid_source = _pipe_with(b"x")
        with pytest.raises(ValueError, match="invalid expected digest or size"):
            _write_stdin_file(root_fd, "file", invalid_source, "bad", 1)
        os.close(invalid_source)

        too_long = _pipe_with(b"three")
        try:
            with pytest.raises(ValueError, match="size changed"):
                _write_stdin_file(root_fd, "too-long", too_long, hashlib.sha256(b"three").hexdigest(), 2)
        finally:
            os.close(too_long)
        assert not (root / "too-long").exists()

        existing = root / "existing"
        existing.write_bytes(b"original")
        source = _pipe_with(b"replacement")
        try:
            with pytest.raises(FileExistsError):
                _write_stdin_file(
                    root_fd, "existing", source, hashlib.sha256(b"replacement").hexdigest(), len(b"replacement"),
                )
        finally:
            os.close(source)
        assert existing.read_bytes() == b"original"

        source = _pipe_with(b"write error")
        real_write = os.write
        monkeypatch.setattr(
            executor_worker_module.os, "write",
            lambda _fd, _body: 0,
        )
        try:
            with pytest.raises(OSError, match="no progress"):
                _write_stdin_file(
                    root_fd, "no-progress", source, hashlib.sha256(b"write error").hexdigest(), len(b"write error"),
                )
        finally:
            monkeypatch.setattr(executor_worker_module.os, "write", real_write)
            os.close(source)
        assert not (root / "no-progress").exists()
    finally:
        os.close(root_fd)


def test_extract_seed_supports_nested_directory_members_and_toml_table_guard(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    root.mkdir()
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    body_stream = io.BytesIO()
    with tarfile.open(fileobj=body_stream, mode="w:gz") as archive:
        directory = tarfile.TarInfo("cache/store/nested/")
        directory.type = tarfile.DIRTYPE
        archive.addfile(directory)
        member = tarfile.TarInfo("cache/store/nested/value")
        member.size = 3
        archive.addfile(member, io.BytesIO(b"val"))
    body = body_stream.getvalue()
    input_fd = _pipe_with(body)
    try:
        _extract_seed(root_fd, input_fd, "cache/store", hashlib.sha256(body).hexdigest(), len(body), 100)
    finally:
        os.close(input_fd)
        os.close(root_fd)
    assert (root / "cache" / "store" / "nested" / "value").read_bytes() == b"val"

    config = tmp_path / "table.toml"
    config.write_text("key='value'\n", encoding="utf-8")
    config_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    monkeypatch.setattr(executor_worker_module.tomllib, "loads", lambda _text: [])
    try:
        with pytest.raises(AdmissionError, match="is not a table"):
            executor_worker_module._parse_toml_fd(config_fd, "table.toml")
    finally:
        os.close(config_fd)


def test_tree_walker_closes_pending_descriptors_and_artifact_tree_errors(tmp_path, monkeypatch):
    root = tmp_path / "walk"
    root.mkdir()
    (root / "parent" / "first").mkdir(parents=True)
    (root / "parent" / "second").mkdir()
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    real_listdir = os.listdir

    def fail_child(fd):
        target = os.readlink(f"/proc/self/fd/{fd}")
        if target.endswith("/second"):
            raise RuntimeError("injected directory enumeration failure")
        return real_listdir(fd)

    monkeypatch.setattr(executor_worker_module.os, "listdir", fail_child)
    try:
        with pytest.raises(RuntimeError, match="enumeration failure"):
            executor_worker_module._tree_entries(root_fd, "parent", reject_special=True)
        monkeypatch.setattr(executor_worker_module.os, "listdir", real_listdir)
        monkeypatch.setattr(
            executor_worker_module, "_tree_entries",
            lambda _fd, relative, **_kwargs: ([], "special") if relative == ".assay" else ([], "absent"),
        )
        output_fd = os.open(root / "artifacts.tgz", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            with pytest.raises(OSError, match="cannot inspect artifact tree"):
                executor_worker_module._archive_artifacts(root_fd, output_fd)
        finally:
            os.close(output_fd)
    finally:
        os.close(root_fd)


def test_worker_module_main_guard_runs_closed_refusal(tmp_path, monkeypatch):
    import runpy
    import sys

    monkeypatch.setattr("sys.argv", ["fleetd.executor_worker"])
    monkeypatch.delitem(sys.modules, "fleetd.executor_worker")
    with pytest.raises(SystemExit) as caught:
        runpy.run_module("fleetd.executor_worker", run_name="__main__")
    assert caught.value.code == int(ExitCode.REFUSAL)


def test_runner_input_recovery_closes_stdin_when_source_read_fails(tmp_path, monkeypatch):
    class _Stdin:
        def __init__(self):
            self.close_calls = 0

        def write(self, _value):
            raise AssertionError("source read fails before stdin write")

        def close(self):
            self.close_calls += 1
            raise OSError("close failed")

    class _Process:
        def __init__(self):
            self.stdin = _Stdin()
            self.returncode = 0
            self.communicated = 0

        def communicate(self, timeout=None):
            self.communicated += 1
            return b"", b"recovered"

    process = _Process()
    runner = SubprocessRunnerCommands(
        RunnerIdentity("runner", 1001, 1001, (1001,)), popen=lambda *_args, **_kwargs: process,
    )
    stream = process.stdin
    source_fd = _pipe_with(b"unused")
    cwd_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    monkeypatch.setattr(executor_worker_module.os, "read", lambda *_args: (_ for _ in ()).throw(OSError("read failed")))
    try:
        assert runner.run_with_input(
            ["helper"], cwd_fd=cwd_fd, env={}, source_fd=source_fd,
        ) == CommandResult(0, b"", b"recovered")
        assert stream.close_calls == 1
        assert process.stdin is None
        assert process.communicated == 1
    finally:
        os.close(source_fd)
        os.close(cwd_fd)


def test_docker_passwd_accepts_named_user_without_numeric_identity_check():
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, b"build:x:17:18::/home/build:/bin/sh\n", b"")

    DockerClient(2, run=run).check_passwd("image", "build", "job with unsafe chars")
    assert calls[0][-2:] == ["passwd", "build"]
    assert "fleetd-uid-job-with-unsafe-chars" in calls[0]


def test_executor_request_validation_accepts_nested_project(tmp_path):
    policy, _checkout = _policy(tmp_path)
    request = _submit()
    request.update(project="fleetd/src")
    assert validate_job_request(request, policy) == (request["job_id"], policy.repo_roots["vbpub"])


def test_executor_state_lock_open_failure_closes_no_unopened_descriptor(tmp_path, monkeypatch):
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    real_open = executor_module.os.open
    attempted = []

    def fail_lock(path, flags, *args, **kwargs):
        attempted.append(Path(path).name)
        if Path(path).name == "state.lock":
            raise OSError("lock path denied")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(executor_module.os, "open", fail_lock)
    with pytest.raises(AdmissionError, match="cannot lock executor state"):
        with _job_state_lock(job_dir):
            pytest.fail("lock open must fail")
    assert attempted == ["state.lock"]


def test_executor_worker_reads_only_string_lane_environment_overrides(tmp_path, monkeypatch):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.request["env"] = {"KEPT": "yes", "DROPPED": 4, 7: "not-a-key"}
    monkeypatch.setenv("PATH", "/usr/local/bin")
    try:
        environment = worker._environment()
        assert environment["PATH"] == "/usr/local/bin"
        assert environment["KEPT"] == "yes"
        assert "DROPPED" not in environment and 7 not in environment
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_exec_name_resolution_refuses_missing_deploy_identity(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    config_fd = os.open(checkout, os.O_RDONLY | os.O_DIRECTORY)
    try:
        (checkout / "ciu.global.toml").write_text("[other]\nname='x'\n", encoding="utf-8")
        with pytest.raises(AdmissionError, match="no persistent runner name"):
            executor_worker_module._exec_container_name(config_fd, "tester", {})
    finally:
        os.close(config_fd)


def test_worker_state_rejects_a_wrong_durable_identity(tmp_path):
    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path)
    state = worker._state()
    state["job_id"] = "other-job"
    (job_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    try:
        with pytest.raises(AdmissionError, match="state identity is invalid"):
            worker._state()
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_artifact_tree_closes_pending_directories_after_special_member(tmp_path):
    root = tmp_path / "artifacts"
    (root / ".assay" / "nested").mkdir(parents=True)
    (root / ".assay" / "z-link").symlink_to(root / ".assay" / "nested", target_is_directory=True)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        entries, reason = executor_worker_module._tree_entries(root_fd, ".assay", reject_special=True)
        assert entries == [] and reason == "symlink"
        assert executor_worker_module._tree_entries(root_fd, ".assay", reject_special=False)[1] is None
    finally:
        os.close(root_fd)


def test_store_archive_pack_failure_marks_collected_entries_failed(tmp_path, monkeypatch):
    root = tmp_path / "store-root"
    (root / "store").mkdir(parents=True)
    (root / "store" / "value").write_text("value", encoding="utf-8")
    output = root / "archive.tgz"
    output.touch()
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    output_fd = os.open(output, os.O_RDWR)
    monkeypatch.setattr(
        executor_worker_module, "_write_archive",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    try:
        result = executor_worker_module._archive_store(root_fd, output_fd, ["store"])
        assert result["status"] == "failed" and result["reason"] == "pack-failed"
        assert result["stores"] == [{"path": "store", "status": "failed", "reason": "pack-failed"}]
        assert result["logs"] == ["STORE-COLLECT-FAILED store pack-failed"]
    finally:
        os.close(output_fd)
        os.close(root_fd)


def test_store_archive_maps_unopenable_targets_and_preserves_noncollected_statuses(tmp_path, monkeypatch):
    root = tmp_path / "archive-root"
    root.mkdir()
    (root / "collected").mkdir()
    (root / "collected" / "value").write_text("value", encoding="utf-8")
    (root / "not-dir").write_text("file", encoding="utf-8")
    output = root / "archive.tgz"
    output.touch()
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    output_fd = os.open(output, os.O_RDWR)
    real_tree = executor_worker_module._tree_entries

    def tree(root_descriptor, relative, *, reject_special):
        if relative == "loop":
            raise OSError(executor_worker_module.errno.ELOOP, "symlink loop")
        if relative == "denied":
            raise PermissionError("denied")
        return real_tree(root_descriptor, relative, reject_special=reject_special)

    monkeypatch.setattr(executor_worker_module, "_tree_entries", tree)
    monkeypatch.setattr(
        executor_worker_module, "_write_archive",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    try:
        result = executor_worker_module._archive_store(
            root_fd, output_fd, ["collected", "absent", "not-dir", "loop", "denied"],
        )
        assert result["status"] == "failed"
        assert result["stores"] == [
            {"path": "collected", "status": "failed", "reason": "pack-failed"},
            {"path": "absent", "status": "absent", "reason": None},
            {"path": "not-dir", "status": "failed", "reason": "not-a-directory"},
            {"path": "loop", "status": "failed", "reason": "symlink"},
            {"path": "denied", "status": "failed", "reason": "unreadable"},
        ]
        assert result["logs"][0] == "STORE-COLLECT-FAILED collected pack-failed"
        assert result["logs"][1] == "STORE-ABSENT absent"
    finally:
        os.close(output_fd)
        os.close(root_fd)


def test_upload_object_scan_closes_a_nonregular_descriptor(tmp_path, monkeypatch):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    upload_dir = worker.state_dir / "uploads" / worker.group / "objects"
    upload_dir.mkdir(parents=True)
    digest = "b" * 64
    (upload_dir / digest).mkdir()
    real_open_beneath = executor_worker_module.open_beneath

    def allow_directory(root_fd, relative, *, directory=False):
        if relative == digest and not directory:
            return os.open(upload_dir / digest, os.O_RDONLY | os.O_DIRECTORY)
        return real_open_beneath(root_fd, relative, directory=directory)

    monkeypatch.setattr(executor_worker_module, "open_beneath", allow_directory)
    try:
        assert worker._uploaded_objects("objects") == []
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_checkout_with_host_seed_still_attempts_other_uploaded_bundles(tmp_path):
    worker, runner, _job_dir, _checkout = _ready_worker(tmp_path)
    seed = worker.repo_root / SHA
    seed.mkdir(exist_ok=True)
    worker.request.update(repo="vbpub", sha=SHA)
    worker._host_checkout_candidates = lambda: [SHA]
    worker._reset_checkout = lambda _seed: True
    copied_refs = []
    worker._copy_seed_refs = lambda _seed: copied_refs.append(_seed)
    fetched = []
    worker._uploaded_objects = lambda _name: [("a" * 64, 1)]
    worker._fetch_uploaded_bundle = lambda seeds: fetched.append(tuple(seeds))
    worker._synchronize_job_refs = lambda: None
    command_calls = []
    runner.run = lambda argv, **_kwargs: command_calls.append(list(argv)) or CommandResult(
        0, f"{SHA}\n".encode() if argv[-1] == "HEAD" else b"", b"",
    )
    worker._helper = lambda *_args, **_kwargs: CommandResult(0)
    try:
        worker._create_checkout()
        assert copied_refs == [SHA]
        assert fetched == [(SHA,)]
        assert any("checkout" in call for call in command_calls)
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_checkout_reset_refuses_a_checkout_path_that_becomes_unsafe(tmp_path, monkeypatch):
    class _Commands:
        def run(self, argv, **_kwargs):
            if argv[0] == "git" and argv[1] == "init":
                (worker.repo_root / worker.checkout_rel).mkdir(parents=True, exist_ok=True)
            return CommandResult(0)

    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.runner = _Commands()
    worker._helper = lambda *_args, **_kwargs: CommandResult(0)
    real_open_beneath = executor_worker_module.open_beneath

    def fail_checkout(root_fd, relative, *, directory=False):
        if relative == worker.checkout_rel:
            raise OSError("checkout path replaced")
        return real_open_beneath(root_fd, relative, directory=directory)

    monkeypatch.setattr(executor_worker_module, "open_beneath", fail_checkout)
    try:
        with pytest.raises(AdmissionError, match="host checkout path is unsafe"):
            worker._reset_checkout(None)
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_bundle_transfer_refuses_staging_failures_and_unreadable_uploads(tmp_path, monkeypatch):
    content = b"uploaded bundle"
    digest = hashlib.sha256(content).hexdigest()
    worker, runner, _job_dir, _checkout = _ready_worker(tmp_path)
    upload_dir = worker.state_dir / "uploads" / worker.group / "in.bundle"
    upload_dir.mkdir(parents=True)
    (upload_dir / digest).write_bytes(content)
    worker.request.update(sha=SHA)
    worker._uploaded_objects = lambda _name: [(digest, len(content))]
    worker.runner = runner
    runner.run_with_input = lambda *_args, **_kwargs: CommandResult(1, b"", b"stage failed")
    worker._helper = lambda *_args, **_kwargs: CommandResult(0)
    try:
        with pytest.raises(AdmissionError, match="could not stage the verified bundle"):
            worker._fetch_uploaded_bundle()
        real_open_beneath = executor_worker_module.open_beneath

        def fail_object(root_fd, relative, *, directory=False):
            if relative == digest and not directory:
                raise PermissionError("upload denied")
            return real_open_beneath(root_fd, relative, directory=directory)

        worker._uploaded_objects = lambda _name: [(digest, len(content))]
        monkeypatch.setattr(executor_worker_module, "open_beneath", fail_object)
        with pytest.raises(AdmissionError, match="cannot open verified bundle upload"):
            worker._fetch_uploaded_bundle()
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_bundle_retry_uses_other_candidate_and_records_failed_tag_listing(tmp_path):
    content = b"valid object"
    digest = hashlib.sha256(content).hexdigest()
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    upload_dir = worker.state_dir / "uploads" / worker.group / "in.bundle"
    upload_dir.mkdir(parents=True)
    (upload_dir / digest).write_bytes(content)
    worker.request.update(sha=SHA)
    worker._uploaded_objects = lambda _name: [(digest, len(content))]
    worker._reset_checkout = lambda _seed: True
    removed = []
    worker._helper = lambda mode, *args, **_kwargs: removed.append((mode, args)) or CommandResult(0)

    class _Runner:
        def __init__(self):
            self.head_fetches = 0
            self.calls = []

        def run_with_input(self, *_args, **_kwargs):
            return CommandResult(0)

        def run(self, argv, **_kwargs):
            call = list(argv)
            self.calls.append(call)
            if "verify" in call:
                return CommandResult(0)
            if "fetch" in call and call[-1] == "HEAD":
                self.head_fetches += 1
                return CommandResult(1 if self.head_fetches == 1 else 0)
            if len(call) > 1 and call[1] == "ls-remote":
                return CommandResult(1, b"", b"bundle has no tag listing")
            if "cat-file" in call:
                return CommandResult(0)
            return CommandResult(0)

    runner = _Runner()
    worker.runner = runner
    try:
        worker._fetch_uploaded_bundle(seeds=("host-seed",))
        assert runner.head_fetches == 2
        assert any("release tag listing from bundle failed" in line for line in (worker.job_dir / "job.log").read_text().splitlines())
        assert len(removed) == 1
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_bundle_refusal_after_verified_fetch_without_requested_commit(tmp_path):
    content = b"verified but unrelated"
    digest = hashlib.sha256(content).hexdigest()
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    upload_dir = worker.state_dir / "uploads" / worker.group / "in.bundle"
    upload_dir.mkdir(parents=True)
    (upload_dir / digest).write_bytes(content)
    worker.request.update(sha=SHA)
    worker._uploaded_objects = lambda _name: [(digest, len(content))]
    worker._reset_checkout = lambda _seed: True
    worker._helper = lambda *_args, **_kwargs: CommandResult(0)

    class _Runner:
        def run_with_input(self, *_args, **_kwargs):
            return CommandResult(0)

        def run(self, argv, **_kwargs):
            if "cat-file" in argv:
                return CommandResult(1, b"", b"missing commit")
            return CommandResult(0)

    worker.runner = _Runner()
    try:
        with pytest.raises(AdmissionError, match="do not contain the requested commit"):
            worker._fetch_uploaded_bundle()
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_worker_complete_admission_checks_and_transfers_the_inherited_lock(tmp_path, monkeypatch):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.group = "controller"
    worker.request_digest = "digest-value"
    descriptor = os.open(tmp_path / "admission.lock", os.O_CREAT | os.O_RDWR, 0o600)
    worker.lock_fd = descriptor
    captured = {}

    class _Book:
        def __init__(self, state_dir):
            captured["state_dir"] = state_dir

        def complete(self, handle, *, terminal_details):
            captured["handle"] = handle
            captured["terminal"] = terminal_details

    monkeypatch.setattr(executor_worker_module, "AdmissionBook", _Book)
    monkeypatch.setattr(executor_worker_module, "AdmissionHandle", lambda *args: args)
    try:
        worker.group = ""
        with pytest.raises(AdmissionError, match="no inherited live admission lock"):
            worker._complete_admission({})
        worker.group = "controller"
        worker.request_digest = "digest-value"
        worker._complete_admission({"verdict": "PASS"})
        assert captured["state_dir"] == worker.state_dir
        assert captured["handle"][1] == {
            "job_id": worker.job_id, "group": "controller", "digest": "digest-value",
        }
        assert captured["terminal"] == {"verdict": "PASS"}
        assert worker.lock_fd == -1
        os.fstat(descriptor)
    finally:
        os.close(descriptor)
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize("damage, expected", [
    ("identity", "run-gate-request-identity-invalid"),
    ("mode", "environment-changed-during-lane"),
    ("assay-unreadable", "assay-directory-unreadable-after-lane"),
])
def test_run_gate_after_run_rechecks_identity_mode_and_assay_access(tmp_path, monkeypatch, damage, expected):
    worker, _runner, _job_dir, checkout = _ready_worker(tmp_path)
    worker.request.update(project=".", lane="r0-r1")
    (checkout / "run-gate.toml").write_text(
        "[lanes.r0-r1]\nenvironment='host'\n[environments.host]\nmode='host'\n",
        encoding="utf-8",
    )
    worker._save_state(lane_mode="exec" if damage == "mode" else "host")
    if damage == "identity":
        worker.request["project"] = None
    elif damage == "assay-unreadable":
        real_stat = executor_worker_module.os.stat

        def fail_assay(path, *args, **kwargs):
            if path == ".assay":
                raise PermissionError("assay denied")
            return real_stat(path, *args, **kwargs)

        monkeypatch.setattr(executor_worker_module.os, "stat", fail_assay)
    try:
        worker._validate_run_gate_after_run()
        assert worker.lane_error is not None and expected in worker.lane_error
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize("verdict, expected_state", [
    ("PASS", "done"),
    ("CANCELLED", "cancelled"),
])
def test_finalize_lane_resumes_pending_terminal_after_cleanup_proofs(tmp_path, verdict, expected_state):
    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path)
    worker.request.update(sha=SHA, project=".", lane="r0-r1")
    terminal = {
        "verdict": verdict, "exit_code": 0 if verdict == "PASS" else int(ExitCode.CANCELLED),
        "lane": "r0-r1", "api": "fleet-host-agent/1", "collected_at": "2026-11-27T00:00:00Z",
        "result_sha256": "a" * 64, "result_size": 321,
    }
    worker._persist_pending_terminal(terminal)
    (job_dir / "result-pending.json").write_text(json.dumps({
        "containers": [
            {"id": "b" * 64, "lane": "r0-r1", "mode": "ephemeral", "status": "verified"},
            {"id": "c" * 64, "lane": "r0-r1", "mode": "host", "status": "no-container"},
            "malformed-entry",
        ],
        "binding": {"status": "verified", "reasons": []},
    }), encoding="utf-8")
    calls = []
    worker._teardown_containers = lambda containers, **_kwargs: calls.append(("teardown", list(containers)))
    worker._ensure_job_containers_stopped = lambda **kwargs: calls.append(("containers", kwargs["remove"]))
    worker._ensure_scope_is_only_worker = lambda: calls.append(("scope", None))
    worker._remove_checkout = lambda: calls.append(("checkout", None))
    published = []
    worker._publish_terminal = lambda value, **kwargs: published.append((dict(value), kwargs))
    if verdict == "CANCELLED":
        worker._save_state(cancel_reason="operator requested cancellation")
    try:
        assert worker._finalize_lane(terminal["exit_code"]) == terminal["exit_code"]
        assert calls[0][0] == "teardown" and len(calls[0][1]) == 1
        assert [call for call in calls if call[0] == "containers"] == [
            ("containers", True), ("containers", False),
        ]
        assert calls.index(("checkout", None)) < len(calls) - 1
        assert published[0][1]["final_state"] == expected_state
        if verdict == "CANCELLED":
            assert published[0][1]["reason"] == "operator requested cancellation"
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize("failure, reason", [
    ("term", "TERM failed; cancellation finalization is refused"),
    ("missing-client", "thrashguard-deregister-unavailable"),
    ("deregister", "thrashguard-deregister-failed"),
    ("unknown-guard", "thrashguard-presence-unknown"),
])
def test_finalize_lane_keeps_charge_on_unproved_cancel_guard_state(tmp_path, failure, reason):
    class StopRetry(RuntimeError):
        pass

    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    guard_present = True if failure in {"missing-client", "deregister"} else None if failure == "unknown-guard" else False
    worker._save_state(cancel_requested=True, lane_mode="host", guard={"present": guard_present})
    if failure == "term":
        worker._save_state(stop_signal="failed", reason="TERM failed; cancellation finalization is refused")
    elif failure == "deregister":
        class _Guard:
            def deregister(self, _job_id):
                raise OSError("guard offline")
        worker.thrashguard_client = _Guard()
    worker._follow_lane_events = lambda **_kwargs: None
    worker._read_events = lambda **_kwargs: []
    worker.waiter = lambda _seconds: (_ for _ in ()).throw(StopRetry("end retry loop"))
    with pytest.raises(StopRetry):
        worker._finalize_lane(0)
    state = worker._state()
    assert state["state"] == "stopping" and reason in state["reason"]
    assert not (_job_dir / "terminal-pending.json").exists()
    assert not (_job_dir / "result.json").exists()
    for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
        if fd >= 0:
            os.close(fd)


def test_uploaded_object_scan_filters_bad_entries_and_wraps_directory_errors(tmp_path, monkeypatch):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    upload_dir = worker.state_dir / "uploads" / worker.group / "in.bundle"
    upload_dir.mkdir(parents=True)
    content = b"verified bundle bytes"
    digest = hashlib.sha256(content).hexdigest()
    (upload_dir / digest).write_bytes(content)
    (upload_dir / "not-a-digest").write_bytes(content)
    (upload_dir / ("a" * 64)).write_bytes(b"wrong content")
    (upload_dir / ("b" * 64)).mkdir()
    (upload_dir / ("c" * 64)).symlink_to(upload_dir / digest)
    try:
        assert worker._uploaded_objects("in.bundle") == [(digest, len(content))]
        monkeypatch.setattr(
            executor_worker_module, "open_beneath",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("denied")),
        )
        with pytest.raises(AdmissionError, match="cannot inspect host upload objects"):
            worker._uploaded_objects("in.bundle")
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_checkout_ref_synchronization_always_removes_untrusted_main_refs(tmp_path):
    class _Commands:
        def __init__(self, main_result):
            self.main_result = main_result
            self.calls = []

        def run(self, argv, **_kwargs):
            self.calls.append(list(argv))
            if "rev-parse" in argv:
                return self.main_result
            return CommandResult(0)

    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.request["base"] = None
    commands = _Commands(CommandResult(1, b"", b"missing"))
    worker.runner = commands
    try:
        worker._synchronize_job_refs()
        deleted = [call for call in commands.calls if "-d" in call]
        assert any("refs/remote-gate/base" in call for call in deleted)
        assert any("refs/remotes/origin/main" in call for call in deleted)
        assert any("refs/heads/main" in call for call in deleted)

        commands.calls.clear()
        worker.request["base"] = SHA
        commands.main_result = CommandResult(0, f"{SHA}\n".encode(), b"")
        worker._synchronize_job_refs()
        assert any("refs/remote-gate/base" in call and SHA in call for call in commands.calls)
        assert any("refs/remotes/origin/main" in call and "-d" in call for call in commands.calls)
        assert any("refs/heads/main" in call and "-d" in call for call in commands.calls)
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_declared_main_base_requires_an_explicit_request_base(tmp_path):
    class _BaseRunner:
        def __init__(self):
            self.calls = []

        def run(self, argv, **_kwargs):
            self.calls.append(list(argv))
            return CommandResult(0)

    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    commands = _BaseRunner()
    worker.runner = commands
    worker.request.pop("base", None)
    try:
        with pytest.raises(AdmissionError, match="lane base 'main' requires an explicit request base"):
            worker._check_declared_base({"base": "main"})
        with pytest.raises(AdmissionError, match="lane base 'origin/main' requires an explicit request base"):
            worker._check_declared_base({"base": "origin/main"})
        assert commands.calls == []

        worker.request["base"] = SHA
        worker._check_declared_base({"base": "main"})
        assert commands.calls[-1][-1] == f"{SHA}^{{commit}}"
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_copy_seed_refs_uses_only_release_tags_and_never_seed_main(tmp_path):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)

    class _RefRunner:
        def __init__(self):
            self.calls = []

        def run(self, argv, **_kwargs):
            self.calls.append(list(argv))
            if "for-each-ref" in argv:
                return CommandResult(
                    0,
                    b"refs/tags/run-gate-v25.1.0\nrefs/tags/notes\nrefs/tags/assay-v7.2.0\n",
                )
            return CommandResult(0)

    commands = _RefRunner()
    worker.runner = commands
    try:
        worker._copy_seed_refs(SHA)
        fetches = [call for call in commands.calls if "fetch" in call]
        assert all("refs/remotes/origin/main" not in call for call in fetches)
        assert [call[-1] for call in fetches] == [
            "+refs/tags/run-gate-v25.1.0:refs/tags/run-gate-v25.1.0",
            "+refs/tags/assay-v7.2.0:refs/tags/assay-v7.2.0",
        ]
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize("seed", [None, SHA])
def test_checkout_reset_opens_successful_new_or_seeded_clone(tmp_path, seed):
    class _Commands:
        def __init__(self, repo, checkout_rel):
            self.repo = repo
            self.checkout_rel = checkout_rel
            self.calls = []

        def run(self, argv, **_kwargs):
            self.calls.append(list(argv))
            if argv[0] == "git" and argv[1] in {"init", "clone"}:
                (self.repo / self.checkout_rel).mkdir(parents=True, exist_ok=True)
            return CommandResult(0)

    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    if seed is not None:
        (worker.repo_root / seed).mkdir(exist_ok=True)
    commands = _Commands(worker.repo_root, worker.checkout_rel)
    worker.runner = commands
    try:
        assert worker._reset_checkout(seed) is True
        assert worker.checkout_fd >= 0
        git_call = next(call for call in commands.calls if call[0] == "git")
        assert ("clone" if seed else "init") in git_call
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_create_checkout_verifies_base_checkout_head_and_records_host_sha(tmp_path):
    class _Commands:
        def __init__(self, repo, checkout_rel):
            self.repo = repo
            self.checkout_rel = checkout_rel
            self.calls = []

        def run(self, argv, **_kwargs):
            call = list(argv)
            self.calls.append(call)
            if call[:2] == ["git", "clone"]:
                (self.repo / self.checkout_rel).mkdir(parents=True, exist_ok=True)
            if "rev-parse" in call and call[-1] == "HEAD":
                return CommandResult(0, f"{SHA}\n".encode(), b"")
            return CommandResult(0)

    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    (worker.repo_root / SHA).mkdir(exist_ok=True)
    commands = _Commands(worker.repo_root, worker.checkout_rel)
    worker.runner = commands
    worker.request.update(repo="vbpub", sha=SHA, base=SHA)
    helper_calls = []

    def helper(mode, *args, **kwargs):
        helper_calls.append((mode, args, kwargs))
        if mode == "prepare":
            (worker.repo_root / ".fleetd-worktrees").mkdir(exist_ok=True)
        return CommandResult(0)

    worker._helper = helper
    try:
        worker._create_checkout()
        assert any("cat-file" in call and f"{SHA}^{{commit}}" in call for call in commands.calls)
        assert any("checkout" in call and SHA in call for call in commands.calls)
        assert any("rev-parse" in call and call[-1] == "HEAD" for call in commands.calls)
        assert worker._state()["checkout"] == {"ready": True, "sha": SHA}
        assert helper_calls[0][0:2] == ("prepare", ("--relative", ".fleetd-worktrees"))
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize("damage, message", [
    ("missing", "event file is missing"),
    ("symlink", "event file is unreadable"),
    ("mode", "unsafe ownership or links"),
    ("hardlink", "unsafe ownership or links"),
])
def test_event_reader_refuses_untrusted_file_boundaries(tmp_path, damage, message):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.runner_identity = RunnerIdentity("runner", os.getuid(), os.getgid(), (os.getgid(),))
    event_dir = worker.repo_root.parent / ".fleetd-events" / "vbpub"
    event_dir.mkdir(parents=True)
    event_path = event_dir / f"{worker.job_id}.jsonl"
    if damage != "missing":
        event_path.write_text("", encoding="utf-8")
        event_path.chmod(0o600)
    if damage == "symlink":
        event_path.unlink()
        event_path.symlink_to(event_dir / "target")
    elif damage == "mode":
        event_path.chmod(0o644)
    elif damage == "hardlink":
        (event_dir / "second-link").hardlink_to(event_path)
    worker.event_rel = f".fleetd-events/vbpub/{worker.job_id}.jsonl"
    try:
        with pytest.raises(AdmissionError, match=message):
            worker._read_events()
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_event_reader_accepts_reason_field_and_null_lane_exit_code(tmp_path):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.runner_identity = RunnerIdentity("runner", os.getuid(), os.getgid(), (os.getgid(),))
    event_dir = worker.repo_root.parent / ".fleetd-events" / "vbpub"
    event_dir.mkdir(parents=True)
    event_path = event_dir / f"{worker.job_id}.jsonl"
    events = [
        {
            "api": "fleet-host-agent/1", "job_id": worker.job_id, "event": "lane-started",
            "lane": "r0-r1", "container_id": None, "image_id": None, "adopted": False,
            "reason": "host lane",
        },
        {
            "api": "fleet-host-agent/1", "job_id": worker.job_id, "event": "lane-verdict",
            "lane": "r0-r1", "verdict": "ERROR", "exit_code": 79, "lane_exit_code": None,
        },
    ]
    event_path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    event_path.chmod(0o600)
    worker.event_rel = f".fleetd-events/vbpub/{worker.job_id}.jsonl"
    try:
        assert worker._read_events() == events
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_run_lane_constructs_bound_argv_environment_and_persists_child_status(tmp_path):
    class _Process:
        pid = 4242

        def __init__(self):
            self.statuses = [None, 0]

        def poll(self):
            return self.statuses.pop(0)

    class _Commands:
        def __init__(self):
            self.started = None

        def start(self, argv, *, cwd_fd, env, output_fd):
            self.started = (list(argv), cwd_fd, dict(env), output_fd)
            return process

    process = _Process()
    commands = _Commands()
    request = _submit()
    request.update(project=".", lane="r0-r1", repo="vbpub", base=SHA, extra_argv=["--strict"])
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path, commands, request=request)
    worker._helper = lambda *_args, **_kwargs: CommandResult(0)
    worker._follow_lane_events = lambda **_kwargs: None
    worker._environment = lambda: {"PATH": "/usr/bin", "RUN_GATE_PROFILE": "off"}
    os.close(worker.project_fd)
    worker.project_fd = -1
    try:
        status = worker._run_lane("/usr/bin/run-gate", "ephemeral", {"image": "tester:local"}, None)
        assert status == 0
        argv, cwd_fd, env, output_fd = commands.started
        assert argv[:5] == ["/usr/bin/run-gate", "r0-r1", "--strict", "--base", SHA]
        assert argv[-2:] == ["--job-id", worker.job_id]
        assert argv[-4] == "--event-file"
        assert argv[-3] == str(worker.event_path)
        assert cwd_fd == worker.project_fd and output_fd >= 0
        assert env["RUN_GATE_TESTER_UNIFIED_IMAGE"] == "tester:local"
        assert worker._state()["lane_process_status"] == 0
        assert worker._state()["lane_status_owner"] == "worker"
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize("poll_case", ["exited-during-settle", "poll-error-then-exit"])
def test_run_lane_releases_memory_ticket_when_child_exits_or_poll_errors(tmp_path, poll_case):
    class _Process:
        pid = 4243

        def __init__(self):
            self.statuses = [0] if poll_case == "exited-during-settle" else [RuntimeError("poll failed"), 0]

        def poll(self):
            result = self.statuses.pop(0)
            if isinstance(result, BaseException):
                raise result
            return result

    process = _Process()

    class _Commands:
        def start(self, *_args, **_kwargs):
            return process

    request = _submit()
    request.update(project=".", lane="r0-r1", repo="vbpub")
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path, _Commands(), request=request)
    worker._helper = lambda *_args, **_kwargs: CommandResult(0)
    worker._follow_lane_events = lambda **_kwargs: None
    worker._environment = lambda: {"PATH": "/usr/bin"}
    worker.waiter = lambda _seconds: None
    lock_fd = os.open(worker.state_dir / "memory-lock", os.O_CREAT | os.O_RDWR, 0o600)
    os.close(worker.project_fd)
    worker.project_fd = -1
    try:
        assert worker._run_lane("run-gate", "host", {"runner_name": None}, lock_fd) == 0
        assert worker.memory_lock_fd == -1
        assert worker._state()["lane_process_status"] == 0
        with pytest.raises(OSError):
            os.fstat(lock_fd)
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_run_lane_refuses_non_string_extra_arguments_before_child_start(tmp_path):
    class _Commands:
        def start(self, *_args, **_kwargs):
            pytest.fail("invalid argv must be rejected before start")

    request = _submit()
    request.update(project=".", lane="r0-r1", repo="vbpub", extra_argv=["--valid", 4])
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path, _Commands(), request=request)
    worker._helper = lambda *_args, **_kwargs: CommandResult(0)
    worker._environment = lambda: {"PATH": "/usr/bin"}
    os.close(worker.project_fd)
    worker.project_fd = -1
    try:
        with pytest.raises(AdmissionError, match="extra_argv is invalid"):
            worker._run_lane("run-gate", "host", {"runner_name": None}, None)
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_run_gate_selection_deduplicates_the_fallback_and_handles_bad_version_parser(tmp_path, monkeypatch):
    from fleetd.executor_worker import _RG_FALLBACK

    runner = _VersionRunner({_RG_FALLBACK: CommandResult(0, b"run-gate 25.0.2\n", b"")})
    worker = _worker(tmp_path, runner, finder=lambda _name: _RG_FALLBACK)
    try:
        assert worker._select_run_gate() == (_RG_FALLBACK, "25.0.2")
        assert len(runner.calls) == 1
    finally:
        os.close(worker.repo_root_fd)

    bad_version_dir = tmp_path / "bad-version"
    bad_version_dir.mkdir()
    runner = _VersionRunner({_RG_FALLBACK: CommandResult(0, b"run-gate 25.0.2\n", b"")})
    worker = _worker(bad_version_dir, runner, finder=lambda _name: _RG_FALLBACK)
    monkeypatch.setattr(executor_worker_module, "_version_from_output", lambda _output: "unsupported-version")
    try:
        with pytest.raises(AdmissionError, match="no usable run-gate"):
            worker._select_run_gate()
    finally:
        os.close(worker.repo_root_fd)


def test_prerequisite_refusals_cover_invalid_project_image_and_persistent_runner(tmp_path):
    worker, runner, _job_dir, checkout = _ready_worker(tmp_path)
    worker._select_run_gate = lambda: ("run-gate", "25.0.2")
    worker.request.update(project=None, lane="r0-r1")
    try:
        with pytest.raises(AdmissionError, match="project or lane is invalid"):
            worker._prerequisites()
        worker.request["project"] = "."
        (checkout / "run-gate.toml").write_text(
            "[lanes.r0-r1]\nenvironment='container'\n"
            "[environments.container]\nmode='ephemeral'\nimage_env=7\n",
            encoding="utf-8",
        )
        worker._environment = lambda: {}
        with pytest.raises(AdmissionError, match="no resolved image"):
            worker._prerequisites()
        (checkout / "run-gate.toml").write_text(
            "[lanes.r0-r1]\nenvironment='exec'\n"
            "[environments.exec]\nmode='exec'\ncontainer_name='invalid/name'\n",
            encoding="utf-8",
        )
        with pytest.raises(AdmissionError, match="runner name is invalid"):
            worker._prerequisites()
        (checkout / "run-gate.toml").write_text(
            "[lanes.r0-r1]\nenvironment='exec'\n"
            "[environments.exec]\nmode='exec'\ncontainer_name='persistent-runner'\n",
            encoding="utf-8",
        )
        worker.docker = type("_Docker", (), {"inspect": lambda _self, _name: None})()
        with pytest.raises(AdmissionError, match="is not running"):
            worker._prerequisites()
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_lane_user_zero_gid_and_base_refusal_paths(tmp_path):
    class _Docker:
        def __init__(self):
            self.calls = []

        def check_passwd(self, *args, **kwargs):
            self.calls.append((args, kwargs))

    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    docker = _Docker()
    worker.docker = docker
    worker.runner_identity = RunnerIdentity("runner", 1001, 1002, (1002,))
    try:
        worker._check_lane_user("image", {}, {"user": "0"})
        worker._check_lane_user("image", {}, {"user": "00"})
        worker._check_lane_user("image", {}, {"user": "23"})
        assert docker.calls[-1][1] == {"expected_gid": None}
        worker._check_lane_user("image", {}, {"user": "23:44"})
        assert docker.calls[-1][1] == {"expected_gid": 44}
        with pytest.raises(AdmissionError, match="gid is invalid"):
            worker._check_lane_user("image", {}, {"user": "23:not-a-gid"})
        worker._check_lane_user("image", {"Config": {"User": "named"}}, {})
        assert docker.calls[-1][0][1] == "named"

        worker._check_declared_base({})
        for base in ("", "-bad", "bad\0ref"):
            with pytest.raises(AdmissionError, match="invalid comparison base"):
                worker._check_declared_base({"base": base})
        worker.runner = _VersionRunner({"git": CommandResult(1, b"", b"missing")})
        with pytest.raises(AdmissionError, match="is missing from the host checkout"):
            worker._check_declared_base({"base": SHA})
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_seed_store_invalid_descriptor_and_tracked_check_refusal(tmp_path):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.request["stores"] = {"seed": "invalid"}
    try:
        with pytest.raises(AdmissionError, match="invalid-descriptor"):
            worker._seed_stores()
        assert worker._find_seed_upload("bad", 5) is None
        payload = b"seed archive"
        digest = hashlib.sha256(payload).hexdigest()
        worker.request["stores"] = {"seed": {"path": "cache/store", "sha256": digest, "bytes": len(payload)}}
        worker._find_seed_upload = lambda _digest, _size: "assay-seed-1.tgz"
        worker.runner = _VersionRunner({"git": CommandResult(1, b"", b"git error")})
        with pytest.raises(AdmissionError, match="tracked-check"):
            worker._seed_stores()
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_seed_upload_scanner_rejects_checksum_candidates_and_wraps_io_errors(tmp_path, monkeypatch):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    upload_root = worker.state_dir / "uploads" / worker.group
    (upload_root / "assay-seed-1.tgz").mkdir(parents=True)
    calls = []

    def objects(name):
        calls.append(name)
        if name == "assay-seed-1.tgz":
            return [("a" * 64, 20)]
        if name == "assay-seed-1.tgz.sha256":
            return [("b" * 64, 513)]
        return []

    worker._uploaded_objects = objects
    try:
        assert worker._find_seed_upload("a" * 64, 20) is None
        assert calls == ["assay-seed-1.tgz", "assay-seed-1.tgz.sha256"]
        monkeypatch.setattr(
            executor_worker_module, "open_beneath",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("denied")),
        )
        with pytest.raises(AdmissionError, match="cannot inspect seed upload directory"):
            worker._find_seed_upload("a" * 64, 20)
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_open_upload_object_refuses_non_regular_and_unreadable_objects(tmp_path, monkeypatch):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    upload_dir = worker.state_dir / "uploads" / worker.group / "in.bundle"
    upload_dir.mkdir(parents=True)
    digest = "d" * 64
    (upload_dir / digest).mkdir()
    real_open_beneath = executor_worker_module.open_beneath

    def open_directory_for_final_file(root_fd, relative, *, directory=False):
        if relative == digest and not directory:
            return os.open(upload_dir / digest, os.O_RDONLY | os.O_DIRECTORY)
        return real_open_beneath(root_fd, relative, directory=directory)

    monkeypatch.setattr(executor_worker_module, "open_beneath", open_directory_for_final_file)
    try:
        with pytest.raises(AdmissionError, match="not a regular file"):
            worker._open_upload_object("in.bundle", digest)
        monkeypatch.setattr(
            executor_worker_module, "open_beneath",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("denied")),
        )
        with pytest.raises(AdmissionError, match="cannot open uploaded object"):
            worker._open_upload_object("in.bundle", digest)
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_run_lane_cancel_checkpoint_prevents_start_and_clears_live_marker(tmp_path):
    class _Commands:
        def start(self, *_args, **_kwargs):
            pytest.fail("cancel checkpoint must run before child start")

    request = _submit()
    request.update(project=".", lane="r0-r1", repo="vbpub")
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path, _Commands(), request=request)
    worker._helper = lambda *_args, **_kwargs: CommandResult(0)
    worker._environment = lambda: {"PATH": "/usr/bin"}
    worker._save_state(cancel_requested=True, cancel_reason="operator requested stop")
    try:
        with pytest.raises(AdmissionError, match="operator requested stop"):
            worker._run_lane("run-gate", "host", {"runner_name": None}, None)
        state = worker._state()
        assert state["lane_may_be_live"] is False
        assert state["lane_start_attempted"] is False
        assert state["lane_status_owner"] == "worker"
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_run_lane_refuses_invalid_repo_and_retains_live_marker_on_other_checkpoint_error(tmp_path):
    class _Commands:
        def start(self, *_args, **_kwargs):
            pytest.fail("checkpoint or repo validation must refuse before child start")

    request = _submit()
    request.update(project=".", lane="r0-r1", repo="vbpub")
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path, _Commands(), request=request)
    worker.request["repo"] = None
    worker._helper = lambda *_args, **_kwargs: CommandResult(0)
    worker._environment = lambda: {"PATH": "/usr/bin"}
    os.close(worker.project_fd)
    worker.project_fd = -1
    try:
        with pytest.raises(AdmissionError, match="request repo is invalid"):
            worker._run_lane("run-gate", "host", {"runner_name": None}, None)
        os.close(worker.project_fd)
        worker.project_fd = -1
        worker.request["repo"] = "vbpub"
        checkpoint_calls = {"count": 0}

        def fail_checkpoint():
            checkpoint_calls["count"] += 1
            if checkpoint_calls["count"] == 1:
                raise AdmissionError(ExitCode.RUNNER_FAILURE, "state is unreadable")

        worker._cancel_checkpoint = fail_checkpoint
        with pytest.raises(AdmissionError, match="state is unreadable"):
            worker._run_lane("run-gate", "host", {"runner_name": None}, None)
        state = worker._state()
        assert state["lane_may_be_live"] is True
        assert state["lane_status_owner"] == "worker"
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_run_lane_memory_settle_does_not_swallow_non_cancel_checkpoint_failures(tmp_path):
    class _Process:
        pid = 4250

        def poll(self):
            return None

    process = _Process()

    class _Commands:
        def start(self, *_args, **_kwargs):
            return process

    request = _submit()
    request.update(project=".", lane="r0-r1", repo="vbpub")
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path, _Commands(), request=request)
    worker._helper = lambda *_args, **_kwargs: CommandResult(0)
    worker._environment = lambda: {"PATH": "/usr/bin"}
    checkpoint_calls = {"count": 0}

    def fail_after_start():
        checkpoint_calls["count"] += 1
        if checkpoint_calls["count"] == 2:
            raise AdmissionError(ExitCode.RUNNER_FAILURE, "state read failed after start")

    worker._cancel_checkpoint = fail_after_start
    lock_fd = os.open(worker.state_dir / "memory-lock", os.O_CREAT | os.O_RDWR, 0o600)
    os.close(worker.project_fd)
    worker.project_fd = -1
    try:
        with pytest.raises(AdmissionError, match="state read failed after start"):
            worker._run_lane("run-gate", "host", {"runner_name": None}, lock_fd)
        state = worker._state()
        assert state["lane_may_be_live"] is True
        assert state["lane_pid"] == process.pid
        assert worker.memory_lock_fd == -1
        with pytest.raises(OSError):
            os.fstat(lock_fd)
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_run_lane_poll_errors_and_cancel_checks_stay_in_the_worker(tmp_path):
    class _Process:
        pid = 4251

        def __init__(self):
            self.statuses = [RuntimeError("poll one"), None, 0]

        def poll(self):
            result = self.statuses.pop(0)
            if isinstance(result, BaseException):
                state_control["fail_next_state"] = True
                raise result
            return result

    state_control = {"fail_next_state": False}
    process = _Process()

    class _Commands:
        def start(self, *_args, **_kwargs):
            worker._save_state(cancel_requested=True, cancel_reason="test cancel")
            return process

    request = _submit()
    request.update(project=".", lane="r0-r1", repo="vbpub")
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path, _Commands(), request=request)
    worker._helper = lambda *_args, **_kwargs: CommandResult(0)
    worker._environment = lambda: {"PATH": "/usr/bin"}
    original_state = worker._state

    def state_reader():
        if state_control["fail_next_state"]:
            state_control["fail_next_state"] = False
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "transient state read")
        return original_state()

    worker._state = state_reader
    stops = []

    def refuse_signal(_process):
        stops.append("worker-stop")
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "stop retry")

    worker._request_worker_stop = refuse_signal
    final_calls = []

    def follow(*, final):
        final_calls.append(final)
        if final:
            raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "final event unreadable")

    worker._follow_lane_events = follow
    worker.waiter = lambda _seconds: None
    os.close(worker.project_fd)
    worker.project_fd = -1
    try:
        assert worker._run_lane("run-gate", "host", {"runner_name": None}, None) == 0
        assert stops == ["worker-stop"]
        assert worker.lane_error == "final event unreadable"
        assert final_calls[-1] is True
        assert worker._state()["lane_process_status"] == 0
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_run_lane_checks_cancel_before_and_after_each_wait(tmp_path):
    class _Process:
        pid = 4252

        def __init__(self):
            self.statuses = [None, None, 0]

        def poll(self):
            return self.statuses.pop(0)

    process = _Process()

    class _Commands:
        def start(self, *_args, **_kwargs):
            worker._save_state(cancel_requested=True, cancel_reason="test cancel")
            return process

    request = _submit()
    request.update(project=".", lane="r0-r1", repo="vbpub")
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path, _Commands(), request=request)
    worker._helper = lambda *_args, **_kwargs: CommandResult(0)
    worker._environment = lambda: {"PATH": "/usr/bin"}
    stop_calls = []
    worker._request_worker_stop = lambda _process: stop_calls.append("stop")
    worker._follow_lane_events = lambda **_kwargs: None
    worker.waiter = lambda _seconds: None
    os.close(worker.project_fd)
    worker.project_fd = -1
    try:
        assert worker._run_lane("run-gate", "host", {"runner_name": None}, None) == 0
        assert stop_calls == ["stop", "stop"]
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_runner_input_recovers_when_communicate_breaks_after_stdin_closed(tmp_path):
    class _Stdin:
        closed = False

        def close(self):
            self.closed = True

    class _Process:
        returncode = 0

        def __init__(self):
            self.stdin = _Stdin()
            self.communications = 0

        def communicate(self, timeout=None):
            self.communications += 1
            if self.communications == 1:
                assert timeout == 7
                raise BrokenPipeError("reader closed after stdin close")
            return b"", b"recovered"

    process = _Process()
    calls = []
    runner = SubprocessRunnerCommands(
        RunnerIdentity("runner", 1001, 1001, (1001,)),
        popen=lambda *args, **kwargs: calls.append((args, kwargs)) or process,
    )
    source_fd, write_fd = os.pipe()
    os.close(write_fd)
    try:
        result = runner.run_with_input(
            ["helper"], cwd_fd=9, env={"PATH": "/usr/bin"}, source_fd=source_fd, timeout=7,
        )
        assert result == CommandResult(0, b"", b"recovered")
        assert process.stdin is None and process.communications == 2
        assert calls[0][1]["close_fds"] is True and calls[0][1]["pass_fds"] == (9,)
    finally:
        os.close(source_fd)


def test_memory_admission_refuses_when_available_memory_never_recovers(tmp_path, monkeypatch):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.request["resources"] = {"declared_peak_bytes": 64 * 1024 * 1024}
    worker.meminfo_reader = lambda: "MemTotal: 1048576 kB\nMemAvailable: 0 kB\n"
    waits = []
    worker.waiter = waits.append
    monkeypatch.setattr(executor_worker_module, "_MEMORY_WAIT_S", 0)
    try:
        with pytest.raises(AdmissionError, match="memory admission wait expired"):
            worker._memory_admission()
        assert waits == []
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_seed_upload_rejects_a_validly_hashed_but_wrong_checksum_sidecar(tmp_path):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    payload = b"candidate seed archive"
    payload_digest = hashlib.sha256(payload).hexdigest()
    sidecar = f"{'0' * 64}  assay-seed-1.tgz\n".encode("ascii")
    sidecar_digest = hashlib.sha256(sidecar).hexdigest()
    upload_root = worker.state_dir / "uploads" / worker.group
    archive_dir = upload_root / "assay-seed-1.tgz"
    checksum_dir = upload_root / "assay-seed-1.tgz.sha256"
    archive_dir.mkdir(parents=True)
    checksum_dir.mkdir()
    (archive_dir / payload_digest).write_bytes(payload)
    (checksum_dir / sidecar_digest).write_bytes(sidecar)
    try:
        assert worker._find_seed_upload(payload_digest, len(payload)) is None
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_job_container_stop_proof_can_confirm_without_removal(tmp_path):
    container_id = "d" * 64

    class _Docker:
        removed = False

        def job_containers(self, job_id):
            assert job_id == "controller-run-1"
            return (container_id,)

        def inspect(self, target):
            assert target == container_id
            return {
                "Id": container_id,
                "Config": {"Labels": {"run-gate.job": "controller-run-1"}},
                "State": {"Running": False},
            }

        def remove_stopped(self, _target):
            self.removed = True

    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    docker = _Docker()
    worker.docker = docker
    try:
        worker._ensure_job_containers_stopped(remove=False)
        assert docker.removed is False
        assert worker._state()["live_containers"] == []
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_worker_complete_admission_refuses_a_closed_inherited_lock(tmp_path):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.group = "controller"
    worker.request_digest = "a" * 64
    worker.lock_fd = 987654
    try:
        with pytest.raises(AdmissionError, match="inherited admission lock is unavailable"):
            worker._complete_admission({"verdict": "PASS"})
        assert worker.lock_fd == 987654
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_memory_admission_refuses_an_unavailable_ticket_without_waiting(tmp_path, monkeypatch):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.request["resources"] = {"declared_peak_bytes": 2 * 1024 * 1024}
    lock_path = worker.state_dir / "memory-admit.lock"
    held_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(held_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    monkeypatch.setattr(executor_worker_module, "_MEMORY_WAIT_S", 0)
    waits = []
    worker.waiter = waits.append
    try:
        with pytest.raises(AdmissionError, match="memory admission ticket wait expired"):
            worker._memory_admission()
        assert waits == []
    finally:
        fcntl.flock(held_fd, fcntl.LOCK_UN)
        os.close(held_fd)
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_declared_base_accepts_a_commit_present_in_the_host_checkout(tmp_path):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    runner = _VersionRunner({"git": CommandResult(0, b"", b"")})
    worker.runner = runner
    try:
        worker._check_declared_base({"base": "refs/remotes/origin/main"})
        assert runner.calls[0][3:] == ["cat-file", "-e", "refs/remotes/origin/main^{commit}"]
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize("project,lane", [(None, "r0-r1"), (".", None)])
def test_run_lane_refuses_non_string_project_or_lane_before_side_effects(tmp_path, project, lane):
    class _Runner:
        def start(self, *_args, **_kwargs):
            pytest.fail("invalid request identity must be rejected before child start")

    request = _submit()
    request.update(project=project, lane=lane, repo="vbpub")
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path, _Runner(), request=request)
    try:
        with pytest.raises(AdmissionError, match="request project or lane is invalid"):
            worker._run_lane("run-gate", "host", {"runner_name": None}, None)
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize("updates,cancel", [
    ({"stop_signal": "sent"}, True),
    ({"cancel_refused": True}, True),
    ({"guard_deregistered": True}, True),
    ({"ordinary": True}, False),
])
def test_retry_state_save_handles_cancel_probe_skip_and_false_cases(tmp_path, updates, cancel):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker._save_state(cancel_requested=cancel)
    real_save = worker._save_state
    attempts = []

    def fail_once(**values):
        attempts.append(dict(values))
        if len(attempts) == 1:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "transient state error")
        return real_save(**values)

    worker._save_state = fail_once
    worker.runner_process = type("Process", (), {"pid": 778, "poll": lambda _self: None})()
    worker.process_signals = type("Signals", (), {
        "terminate_group": lambda *_args: pytest.fail("cancel probe must not run for terminal updates"),
    })()
    waits = []
    worker.waiter = waits.append
    try:
        state = worker._retry_state_save(**updates)
        assert all(state.get(key) == value for key, value in updates.items())
        assert waits == [1]
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize("cancel", [False, True])
def test_finalize_lane_completes_verified_host_result_and_cancel_guard(tmp_path, cancel):
    class _Guard:
        def __init__(self):
            self.calls = []

        def deregister(self, job_id):
            self.calls.append(job_id)

    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path)
    worker.request.update(sha=SHA, project=".", lane="r0-r1")
    worker._save_state(
        lane_mode="host", runner_name=None, cancel_requested=cancel,
        cancel_reason="operator cancellation" if cancel else None,
        guard={"present": True if cancel else False},
    )
    events = []
    worker._follow_lane_events = lambda **_kwargs: None
    worker._read_events = lambda **_kwargs: events
    worker._validate_run_gate_after_run = lambda: None
    worker._ensure_scope_is_only_worker = lambda: None
    worker._ensure_job_containers_stopped = lambda **_kwargs: None
    worker._bind_container_events = lambda *_args, **_kwargs: []
    worker._collect = lambda: {"status": "none"}
    published = []
    worker._publish_terminal = lambda terminal, **kwargs: published.append((dict(terminal), kwargs))
    worker._teardown_containers = lambda *_args, **_kwargs: None
    worker._remove_checkout = lambda: None
    terminal = {
        "verdict": "CANCELLED" if cancel else "PASS",
        "exit_code": int(ExitCode.CANCELLED) if cancel else 0,
        "lane": "r0-r1", "api": "fleet-host-agent/1",
        "collected_at": "2026-11-27T00:00:00Z", "result_sha256": "a" * 64,
        "result_size": 17, "binding_reasons": [],
    }

    def bind(_events, _status, _containers, *, cancelled=False):
        assert cancelled is cancel
        (job_dir / "result-pending.json").write_text(json.dumps({
            "containers": [], "binding": {"status": "verified", "reasons": []},
        }), encoding="utf-8")
        return terminal

    worker._bind_verdict = bind
    guard = _Guard()
    worker.thrashguard_client = guard
    try:
        assert worker._finalize_lane(terminal["exit_code"]) == terminal["exit_code"]
        assert len(published) == 1
        assert published[0][1]["final_state"] == ("cancelled" if cancel else "done")
        assert guard.calls == ([worker.job_id] if cancel else [])
        if cancel:
            assert published[0][1]["reason"] == "operator cancellation"
        assert (job_dir / "terminal-pending.json").is_file()
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize("mode,cancel,events,reason", [
    ("exec", True, [], "exec-cancel-cannot-prove-runner-process-stopped"),
    ("exec", False, [], "exec-end-without-run-gate-verdict"),
])
def test_finalize_lane_refuses_exec_end_without_stop_proof(tmp_path, mode, cancel, events, reason):
    class StopRetry(RuntimeError):
        pass

    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.request.update(sha=SHA, project=".", lane="r0-r1")
    worker._save_state(lane_mode=mode, cancel_requested=cancel, guard={"present": False})
    worker._follow_lane_events = lambda **_kwargs: None
    worker._read_events = lambda **_kwargs: events
    worker.waiter = lambda _seconds: (_ for _ in ()).throw(StopRetry("stop retrying"))
    try:
        with pytest.raises(StopRetry):
            worker._finalize_lane(0)
        assert reason in worker._state()["reason"]
        assert worker._state()["retry_required"] is True
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_finalize_lane_retries_unobserved_events_and_transient_event_reader_failure(tmp_path):
    class StopRetry(RuntimeError):
        pass

    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.request.update(sha=SHA, project=".", lane="r0-r1")
    worker._save_state(lane_mode="host")
    worker._follow_lane_events = lambda **_kwargs: None
    worker._read_events = lambda **_kwargs: [{"event": "lane-started", "lane": "r0-r1"}]
    waits = []

    def stop_retry(_seconds):
        waits.append(1)
        raise StopRetry(f"wait {len(waits)}")

    worker.waiter = stop_retry
    try:
        with pytest.raises(StopRetry, match="wait 1"):
            worker._finalize_lane(0)
        worker.event_cursor = 0
        worker._read_events = lambda **_kwargs: (_ for _ in ()).throw(
            AdmissionError(ExitCode.QUEUED_WITHDRAWN, "event transport unavailable"),
        )
        with pytest.raises(StopRetry, match="wait 2"):
            worker._finalize_lane(0)
        assert "event transport unavailable" in worker._state()["reason"]
        assert worker._state()["retry_required"] is True
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize("exit_code,reason,verdict", [
    (int(ExitCode.REFUSAL), "invalid request", "ERROR"),
    (int(ExitCode.PREREQUISITE_MISSING), "Run-Gate missing", "ERROR"),
    (int(ExitCode.PREREQUISITE_MISSING), "image missing", "NOT_RUN"),
    (int(ExitCode.RUNNER_FAILURE), "worker did not start", "NOT_RUN"),
    (int(ExitCode.CANCELLED), "operator cancelled", "CANCELLED"),
])
def test_finish_unstarted_maps_pre_lane_outcomes_and_publishes_terminal_record(
    tmp_path, exit_code, reason, verdict,
):
    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path)
    worker.group = "controller"
    worker.request_digest = "a" * 64
    worker.checkout_rel = ""
    worker.runner = None
    worker._ensure_scope_is_only_worker = lambda: None
    worker._ensure_job_containers_stopped = lambda **_kwargs: None
    published = []
    worker._publish_terminal = lambda terminal, **kwargs: published.append((dict(terminal), kwargs))
    try:
        assert worker._finish_unstarted(exit_code, reason) == exit_code
        assert len(published) == 1
        assert published[0][0]["verdict"] == verdict
        assert published[0][1]["final_state"] == ("cancelled" if verdict == "CANCELLED" else "failed")
        assert (job_dir / "result-pending.json").is_file()
        assert (job_dir / "terminal-pending.json").is_file()
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_finish_unstarted_keeps_charge_for_live_lane_or_missing_identity(tmp_path):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker._save_state(lane_may_be_live=True)
    try:
        assert worker._finish_unstarted(79, "uncertain start") == int(ExitCode.QUEUED_WITHDRAWN)
        assert "could not be confirmed stopped" in worker._state()["reason"]
        worker._save_state(lane_may_be_live=False)
        worker.group = ""
        assert worker._finish_unstarted(79, "missing durable identity") == int(ExitCode.QUEUED_WITHDRAWN)
        assert "inherited admission lock" in worker._state()["reason"]
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_collect_artifacts_only_and_absent_store_preserves_explicit_status(tmp_path):
    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path)
    worker._saved_collection = lambda _store_rel: None
    calls = []

    def write_archive(helper, destination, _arguments, *, kind, **_kwargs):
        calls.append((helper, destination, kind))
        part = job_dir / f".{destination}.part"
        if kind == "artifacts":
            content = b"verified artifacts"
            part.write_bytes(content)
            return hashlib.sha256(content).hexdigest(), len(content), {"files": ["log.txt"]}
        part.write_bytes(b"")
        return "", 0, {"status": "absent", "reason": None, "log": "STORE-ABSENT store"}

    worker._write_archive_from_runner = write_archive
    try:
        worker.request["stores"] = None
        artifacts_only = worker._collect()
        assert artifacts_only["store_rel"] is None
        assert artifacts_only["store"] == {"status": "none"}
        assert (job_dir / "artifacts.tgz").is_file()

        worker._save_state(collection=None)
        worker.request["stores"] = {"collect": "store"}
        for stale in (
            ".assay-store.tgz.part", "assay-store.tgz", "assay-store.tgz.sha256",
            ".assay-store.tgz.sha256.part",
        ):
            (job_dir / stale).write_bytes(b"stale")
        absent = worker._collect()
        assert absent["store_rel"] == "store"
        assert absent["store"]["status"] == "absent"
        assert not (job_dir / ".assay-store.tgz.part").exists()
        assert not (job_dir / "assay-store.tgz").exists()
        assert not (job_dir / "assay-store.tgz.sha256").exists()
        log = (job_dir / "job.log").read_text(encoding="utf-8")
        assert "STORE-ABSENT store" in log
        assert calls == [
            ("archive-artifacts", "artifacts.tgz", "artifacts"),
            ("archive-artifacts", "artifacts.tgz", "artifacts"),
            ("archive-store", "assay-store.tgz", "store"),
        ]
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_collect_store_failure_removes_part_and_keeps_artifact_archive(tmp_path):
    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path)
    worker.request["stores"] = {"collect": ["store"]}
    worker._saved_collection = lambda _store_rel: None
    calls = []

    def write_archive(_helper, destination, _arguments, *, kind, **_kwargs):
        part = job_dir / f".{destination}.part"
        part.write_bytes(b"artifact" if kind == "artifacts" else b"partial store")
        calls.append(kind)
        if kind == "store":
            raise AdmissionError(ExitCode.STORE_COLLECTION_FAILED, "store stream was unreadable")
        content = b"artifact"
        return hashlib.sha256(content).hexdigest(), len(content), {"files": []}

    worker._write_archive_from_runner = write_archive
    try:
        with pytest.raises(AdmissionError, match="store stream was unreadable"):
            worker._collect()
        assert calls == ["artifacts", "store"]
        assert (job_dir / "artifacts.tgz").is_file()
        assert not (job_dir / ".assay-store.tgz.part").exists()
        assert "collection" not in worker._state()
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize("mode,container_id", [
    ("host", None),
    ("ephemeral", None),
])
def test_wait_for_lane_containers_accepts_host_event_and_refuses_missing_container_id(
    tmp_path, mode, container_id,
):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.request["lane"] = "r0-r1"
    worker._save_state(lane_mode=mode, runner_name=None)
    event = {"event": "lane-started", "lane": "r0-r1", "container_id": container_id}
    worker._read_events = lambda **_kwargs: [event]
    worker.docker = type("Docker", (), {"inspect": lambda *_args: None})()
    try:
        if mode == "host":
            assert worker._wait_for_lane_containers() == [event]
            assert worker._state()["live_containers"] == []
        else:
            with pytest.raises(AdmissionError, match="has no full container id"):
                worker._wait_for_lane_containers()
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_wait_for_lane_containers_requires_inspect_for_container_events(tmp_path):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.request["lane"] = "r0-r1"
    worker._save_state(lane_mode="ephemeral", runner_name=None)
    worker.docker = object()
    worker._follow_lane_events = lambda **_kwargs: None
    worker._read_events = lambda **_kwargs: [{
        "event": "lane-started", "lane": "r0-r1", "container_id": "e" * 64,
    }]
    try:
        with pytest.raises(AdmissionError, match="inspect provider is unavailable"):
            worker._wait_for_lane_containers()
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_event_follower_marks_invalid_lane_and_ignores_retryable_event_read(tmp_path):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.request["sha"] = SHA
    worker._read_events = lambda **_kwargs: [{"event": "lane-started", "lane": None}]
    try:
        worker._follow_lane_events(final=False)
        assert worker.lane_error == "lane-started event has no lane"
        assert worker.event_cursor == 0
        worker.lane_error = None
        worker.event_cursor = 0
        worker._read_events = lambda **_kwargs: (_ for _ in ()).throw(
            AdmissionError(ExitCode.QUEUED_WITHDRAWN, "event is not readable yet"),
        )
        worker._follow_lane_events(final=False)
        assert worker.lane_error is None and worker.event_cursor == 0
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_job_container_query_and_inspect_wrap_non_admission_errors(tmp_path):
    container_id = "e" * 64

    class _QueryFailure:
        def job_containers(self, _job_id):
            raise RuntimeError("daemon unavailable")

        def inspect(self, _target):
            return None

        def remove_stopped(self, _target):
            pytest.fail("query failure must not remove containers")

    class _InspectFailure:
        def job_containers(self, _job_id):
            return (container_id,)

        def inspect(self, _target):
            raise RuntimeError("inspect transport failed")

        def remove_stopped(self, _target):
            pytest.fail("inspect failure must not remove containers")

    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    try:
        worker.docker = _QueryFailure()
        with pytest.raises(AdmissionError, match="query failed: daemon unavailable"):
            worker._ensure_job_containers_stopped(remove=True)
        worker.docker = _InspectFailure()
        with pytest.raises(AdmissionError, match="inspect .* failed: inspect transport failed"):
            worker._ensure_job_containers_stopped(remove=True)
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_container_mode_resolves_captured_and_sharded_lane_facts(tmp_path):
    worker, _runner, _job_dir, checkout = _ready_worker(tmp_path)
    worker.request.update(project=".", lane="r0-r1")
    (checkout / "run-gate.toml").write_text(
        "[lanes.r0-r1]\nenvironment='host'\n[environments.host]\nmode='host'\n",
        encoding="utf-8",
    )
    try:
        worker._save_state(lane_mode="host", runner_name=9)
        assert worker._container_mode("r0-r1") == ("host", None)
        worker._save_state(lane_mode="unrecognized", runner_name=None)
        assert worker._container_mode("r0-r1.shard1of2") == ("host", None)
        worker.request["project"] = None
        with pytest.raises(AdmissionError, match="request project is invalid"):
            worker._container_mode("r0-r1.shard1of2")
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_container_observation_requires_exact_mount_identity_labels_and_state(tmp_path):
    container_id = "f" * 64
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.request.update(repo="vbpub", project=".", sha=SHA, lane="r0-r1")
    source = str(worker.repo_root / worker.checkout_rel)
    labels = {
        "run-gate.repo": "vbpub", "run-gate.project": ".", "run-gate.lane": "r0-r1",
        "run-gate.commit": SHA, "run-gate.name-schema": "2", "run-gate.started": "now",
        "run-gate.job": worker.job_id,
    }
    answer = {
        "Id": container_id,
        "State": {"Running": False},
        "Mounts": [{"Type": "bind", "Source": source, "Destination": "/workspace"}],
        "Config": {"Labels": labels},
    }
    try:
        binding = worker._verify_container_observation(
            answer, container_id=container_id, event_lane="r0-r1", mode="ephemeral",
            persistent_name=None, expected_sha=SHA,
        )
        assert binding["labels"]["run-gate.job"] == worker.job_id
        assert worker._verify_container_observation(
            {**answer, "State": {"Running": True}}, container_id=container_id,
            event_lane="r0-r1", mode="ephemeral", persistent_name=None,
            expected_sha=SHA, require_stopped=False,
        )["id"] == container_id
        for bad_answer, message in (
            ({**answer, "Id": "short"}, "exact container id"),
            ({**answer, "Mounts": "not-a-mount-list"}, "does not mount this job's checkout"),
            ({**answer, "Config": {"Labels": {}}}, "incomplete run-gate labels"),
            ({**answer, "Config": {"Labels": {**labels, "run-gate.commit": "0" * 40}}},
             "do not bind repo, project, lane, commit, and job"),
            ({**answer, "State": {"Running": True}}, "not confirmed stopped"),
        ):
            with pytest.raises(AdmissionError, match=message):
                worker._verify_container_observation(
                    bad_answer, container_id=container_id, event_lane="r0-r1", mode="ephemeral",
                    persistent_name=None, expected_sha=SHA,
                )
        with pytest.raises(AdmissionError, match="host-mode lane"):
            worker._verify_container_observation(
                answer, container_id=container_id, event_lane="r0-r1", mode="host",
                persistent_name=None, expected_sha=SHA,
            )
        for persistent_name, name, state, message in (
            (None, "/persistent", {"Running": True}, "no persistent runner name"),
            ("persistent", "/wrong", {"Running": True}, "does not match the persistent runner name"),
            ("persistent", "/persistent", {"Running": False}, "persistent runner is not running"),
        ):
            with pytest.raises(AdmissionError, match=message):
                worker._verify_container_observation(
                    {**answer, "Name": name, "State": state}, container_id=container_id,
                    event_lane="r0-r1", mode="exec", persistent_name=persistent_name,
                    expected_sha=SHA,
                )
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_inspect_container_binding_handles_host_null_and_unavailable_answers(tmp_path):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.request.update(project=".", lane="r0-r1")
    worker._save_state(lane_mode="host", runner_name=None)
    event = {"event": "lane-started", "lane": "r0-r1", "container_id": None}
    try:
        assert worker._inspect_container_binding(event, expected_sha=SHA) is None
        worker.docker = object()
        with pytest.raises(AdmissionError, match="inspect provider is unavailable"):
            worker._inspect_container_binding(
                {**event, "container_id": "a" * 64}, expected_sha=SHA,
            )
        worker.docker = type("Docker", (), {"inspect": lambda *_args: None})()
        assert worker._inspect_container_binding(
            {**event, "container_id": "a" * 64}, expected_sha=SHA,
        ) is None
        with pytest.raises(AdmissionError, match="invalid container id"):
            worker._inspect_container_binding({**event, "container_id": "short"}, expected_sha=SHA)
        with pytest.raises(AdmissionError, match="no lane"):
            worker._inspect_container_binding(
                {**event, "lane": None, "container_id": "a" * 64}, expected_sha=SHA,
            )
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_teardown_skips_exec_runner_and_accepts_already_missing_container(tmp_path):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    calls = []
    worker.docker = type("Docker", (), {
        "inspect": lambda *_args: calls.append("inspect"),
        "remove_stopped": lambda *_args: calls.append("remove"),
    })()
    try:
        worker._teardown_containers([{"id": "a" * 64, "lane": "r0-r1", "mode": "exec"}], expected_sha=SHA)
        assert calls == []
        worker.docker = type("Docker", (), {
            "inspect": lambda *_args: None,
            "remove_stopped": lambda *_args: calls.append("remove"),
        })()
        worker._teardown_containers([{"id": "b" * 64, "lane": "r0-r1", "mode": "ephemeral"}], expected_sha=SHA)
        assert calls == []
        with pytest.raises(AdmissionError, match="record is incomplete"):
            worker._teardown_containers([{"mode": "ephemeral"}], expected_sha=SHA)
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_worker_archive_output_and_checksum_failures_clean_transient_files(tmp_path, monkeypatch):
    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path)
    try:
        for name in ("", ".", "..", "nested/file"):
            with pytest.raises(AdmissionError, match="invalid executor output name"):
                worker._open_job_output(name)
        directory = job_dir / "output-directory"
        directory.mkdir()
        with pytest.raises(AdmissionError, match="cannot create output-directory"):
            worker._open_job_output("output-directory")

        class _RaisingRunner:
            def run_to_fd(self, *_args, **_kwargs):
                raise OSError("runner stream disconnected")

        worker.runner = _RaisingRunner()
        with pytest.raises(OSError, match="stream disconnected"):
            worker._write_archive_from_runner(
                "archive-artifacts", "artifacts.tgz", (), root_fd=worker.project_fd, kind="artifacts",
            )
        assert not (job_dir / ".artifacts.tgz.part").exists()

        class _InvalidJsonRunner:
            def run_to_fd(self, _argv, *, output_fd, **_kwargs):
                os.write(output_fd, b"partial")
                return CommandResult(0, b"\xff", b"")

        worker.runner = _InvalidJsonRunner()
        with pytest.raises(AdmissionError, match="invalid artifacts collection details"):
            worker._write_archive_from_runner(
                "archive-artifacts", "artifacts.tgz", (), root_fd=worker.project_fd, kind="artifacts",
            )
        assert not (job_dir / ".artifacts.tgz.part").exists()

        class _GoodJsonRunner:
            def run_to_fd(self, _argv, *, output_fd, **_kwargs):
                os.write(output_fd, b"archive")
                return CommandResult(0, b'{"files": []}', b"")

        worker.runner = _GoodJsonRunner()
        monkeypatch.setattr(executor_worker_module, "_verify_archive_fd", lambda *_args, **_kwargs: 9)
        with pytest.raises(AdmissionError, match="size changed during verification"):
            worker._write_archive_from_runner(
                "archive-artifacts", "artifacts.tgz", (), root_fd=worker.project_fd, kind="artifacts",
            )
        assert not (job_dir / ".artifacts.tgz.part").exists()

        checksum = job_dir / ".assay-store.tgz.sha256.part"
        monkeypatch.setattr(
            executor_worker_module, "_fsync_dir",
            lambda _path: (_ for _ in ()).throw(OSError("directory sync failed")),
        )
        with pytest.raises(AdmissionError, match="cannot persist assay-store.tgz checksum"):
            worker._write_checksum("a" * 64, "assay-store.tgz")
        assert not checksum.exists()
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_worker_hashes_empty_archive_and_refuses_unavailable_saved_file(tmp_path):
    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path)
    empty = job_dir / "empty"
    empty.touch()
    fd = os.open(empty, os.O_RDWR)
    try:
        assert HostJobWorker._hash_fd(fd) == (hashlib.sha256(b"").hexdigest(), 0)
        with pytest.raises(AdmissionError, match="cannot reopen missing.tgz"):
            worker._archive_saved("missing.tgz", kind="artifacts")
    finally:
        os.close(fd)
        for owned in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if owned >= 0:
                os.close(owned)


def test_run_lane_tolerates_transient_poll_and_state_read_errors_until_child_exit(tmp_path):
    class _Process:
        pid = 987

        def __init__(self):
            self.outcomes = [RuntimeError("poll before wait"), RuntimeError("poll after wait"), 0]

        def poll(self):
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

    class _Runner:
        def start(self, *_args, **_kwargs):
            return process

    request = _submit()
    request.update(project=".", repo="vbpub", lane="r0-r1", extra_argv=[], base=None)
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path, _Runner(), request=request)
    process = _Process()
    worker._helper = lambda *_args, **_kwargs: CommandResult(0)
    worker._environment = lambda: {"PATH": "/usr/bin"}
    worker._follow_lane_events = lambda **_kwargs: None
    real_state = worker._state
    state_calls = []

    def state_with_transient_errors():
        state_calls.append(len(state_calls) + 1)
        if state_calls[-1] in {4, 5}:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "temporary state read failure")
        return real_state()

    worker._state = state_with_transient_errors
    waits = []
    worker.waiter = waits.append
    try:
        assert worker._run_lane("run-gate", "host", {"runner_name": None}, None) == 0
        assert waits == [1]
        assert worker._state()["lane_process_status"] == 0
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_host_checkout_line_requires_one_utf8_record_matching_state_and_request(tmp_path):
    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path)
    worker.request["sha"] = SHA
    worker._save_state(checkout={"sha": SHA})
    expected = str(worker.repo_root / worker.checkout_rel)
    line = f"2026-11-27T00:00:00Z checkout {expected} at {SHA}\n"
    path = job_dir / "job.log"
    try:
        path.write_text(line, encoding="utf-8")
        assert worker._host_checkout_line() == (line.rstrip("\n"), SHA)
        path.write_text(line + line, encoding="utf-8")
        with pytest.raises(AdmissionError, match="missing, duplicated, or differs"):
            worker._host_checkout_line()
        path.write_bytes(b"\xff\n")
        with pytest.raises(AdmissionError, match="checkout log is unreadable"):
            worker._host_checkout_line()
        path.write_text(line, encoding="utf-8")
        worker._save_state(checkout={"sha": "0" * 40})
        with pytest.raises(AdmissionError, match="do not agree"):
            worker._host_checkout_line()
        worker._save_state(checkout={"sha": SHA})
        worker.request["sha"] = "not-a-commit"
        with pytest.raises(AdmissionError, match="do not agree"):
            worker._host_checkout_line()
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_lane_summary_parser_handles_ignored_lines_null_exit_and_bad_open(tmp_path):
    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path)
    worker.request["lane"] = "r0-r1"
    path = job_dir / "lane.log"
    try:
        with pytest.raises(AdmissionError, match="lane log is unavailable"):
            worker._last_lane_summary()
        path.mkdir()
        with pytest.raises(AdmissionError, match="not a regular file"):
            worker._last_lane_summary()
        path.rmdir()
        path.write_bytes(
            b"ordinary output " + b"x" * 70000 + b"\n"
            b"ordinary incomplete tail"
            b"\nrun-gate: lane 'r0-r1' verdict PASS; exit_code null; log lane.log\r\n"
        )
        line, verdict, lane_exit = worker._last_lane_summary()
        assert line.endswith("exit_code null; log lane.log")
        assert verdict == "PASS" and lane_exit is None
        path.write_bytes(b"run-gate: lane 'r0-r1' verdict PASS\xff; exit_code 0\n")
        with pytest.raises(AdmissionError, match="summary is unreadable"):
            worker._last_lane_summary()
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize("event,process_status,cancelled,expected", [
    ({"lane": "r0-r1", "verdict": "MAYBE", "exit_code": 0, "lane_exit_code": 0}, 0, False,
     "unknown verdict"),
    ({"lane": "r0-r1", "verdict": "PASS", "exit_code": True, "lane_exit_code": 0}, 0, False,
     "integer process status"),
    ({"lane": "r0-r1", "verdict": "PASS", "exit_code": 0}, 0, False,
     "valid lane_exit_code"),
    ({"lane": "other", "verdict": "PASS", "exit_code": 0, "lane_exit_code": 0}, 0, False,
     "different lane"),
    ({"lane": "r0-r1", "verdict": "PASS", "exit_code": 8, "lane_exit_code": 0}, 8, False,
     "outside its closed verdict table"),
])
def test_verdict_binding_rejects_malformed_or_out_of_table_event_facts(
    tmp_path, event, process_status, cancelled, expected,
):
    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path)
    worker.request.update(repo="vbpub", project=".", sha=SHA, lane="r0-r1")
    worker._save_state(
        lane_mode="host", runner_name=None, checkout={"sha": SHA},
        collection={"collected_at": "2026-11-27T00:00:00Z"},
    )
    path = str(worker.repo_root / worker.checkout_rel)
    (job_dir / "job.log").write_text(f"now checkout {path} at {SHA}\n", encoding="utf-8")
    (job_dir / "lane.log").write_text(
        "run-gate: lane 'r0-r1' verdict PASS; exit_code 0; log lane.log\n", encoding="utf-8",
    )
    event = {
        "api": "fleet-host-agent/1", "job_id": worker.job_id, "event": "lane-verdict",
        **event,
    }
    try:
        terminal = worker._bind_verdict([event], process_status, [], cancelled=cancelled)
        result = json.loads((job_dir / "result-pending.json").read_text(encoding="utf-8"))
        assert any(expected in reason for reason in result["binding"]["reasons"])
        assert terminal["verdict"] == ("CANCELLED" if cancelled else "ERROR")
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_verdict_binding_requires_started_event_for_ephemeral_lane(tmp_path):
    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path)
    worker.request.update(repo="vbpub", project=".", sha=SHA, lane="r0-r1")
    worker._save_state(
        lane_mode="ephemeral", runner_name=None, checkout={"sha": SHA},
        collection={"collected_at": "2026-11-27T00:00:00Z"},
    )
    path = str(worker.repo_root / worker.checkout_rel)
    (job_dir / "job.log").write_text(f"now checkout {path} at {SHA}\n", encoding="utf-8")
    (job_dir / "lane.log").write_text(
        "run-gate: lane 'r0-r1' verdict PASS; exit_code 0; log lane.log\n", encoding="utf-8",
    )
    event = {
        "api": "fleet-host-agent/1", "job_id": worker.job_id, "event": "lane-verdict",
        "lane": "r0-r1", "verdict": "PASS", "exit_code": 0, "lane_exit_code": 0,
    }
    try:
        terminal = worker._bind_verdict([event], 0, [])
        assert terminal["verdict"] == "ERROR"
        result = json.loads((job_dir / "result-pending.json").read_text(encoding="utf-8"))
        assert any("no lane-started event" in reason for reason in result["binding"]["reasons"])
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_output_open_rejects_nonregular_leaf_and_saved_archive_size_change(tmp_path, monkeypatch):
    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path)
    fifo = job_dir / "fifo-output"
    os.mkfifo(fifo)
    real_open = executor_worker_module.os.open

    def open_fifo_nonblocking(path, flags, *args, **kwargs):
        if Path(path) == fifo:
            return real_open(path, os.O_RDONLY | os.O_NONBLOCK, *args, **kwargs)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(executor_worker_module.os, "open", open_fifo_nonblocking)
    try:
        with pytest.raises(AdmissionError, match="fifo-output is not a regular file"):
            worker._open_job_output("fifo-output")
    finally:
        monkeypatch.setattr(executor_worker_module.os, "open", real_open)
    archive = job_dir / "artifacts.tgz"
    archive.write_bytes(b"archive bytes")
    monkeypatch.setattr(executor_worker_module, "_verify_archive_fd", lambda *_args, **_kwargs: 1)
    try:
        with pytest.raises(AdmissionError, match="changed during verification"):
            worker._archive_saved("artifacts.tgz", kind="artifacts")
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_event_reader_requires_host_owned_event_path_and_container_events_bind_by_observation(tmp_path):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    try:
        with pytest.raises(AdmissionError, match="event file was not established"):
            worker._read_events()
        worker.request.update(project=".", lane="r0-r1")
        worker._save_state(lane_mode="host", runner_name=None, lane_event_observations=[{
            "index": 0, "event": {"event": "lane-started", "lane": "r0-r1", "container_id": None},
            "binding": {"id": None, "lane": "r0-r1", "mode": "host", "status": "no-container"},
        }])
        result = worker._bind_container_events([{
            "event": "lane-started", "lane": "r0-r1", "container_id": None,
        }], expected_sha=SHA)
        assert result == [{
            "id": None, "lane": "r0-r1", "mode": "host", "name": None, "status": "no-container",
        }]
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_simple_result_and_terminal_read_refuse_missing_identity_or_corrupt_json(tmp_path):
    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path)
    try:
        worker.request["lane"] = None
        with pytest.raises(AdmissionError, match="without the request lane"):
            worker._simple_result("ERROR", int(ExitCode.REFUSAL), "invalid lane")
        (job_dir / "result-pending.json").unlink()
        (job_dir / "terminal-pending.json").write_bytes(b"\xff")
        with pytest.raises(AdmissionError, match="pending terminal result is unreadable"):
            worker._read_pending_terminal()
        worker.terminal_completed = True
        with pytest.raises(AdmissionError, match="no result artifact"):
            worker._publish_terminal({}, final_state="failed", reason="no artifact")
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_remove_checkout_noop_does_not_call_helper(tmp_path):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.checkout_rel = ""
    worker._helper = lambda *_args, **_kwargs: pytest.fail("empty checkout path must be a no-op")
    try:
        worker._remove_checkout()
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize("failure,expected_code", [
    ("unknown-user", int(ExitCode.REFUSAL)),
    ("root-user", int(ExitCode.REFUSAL)),
    ("runner-factory", int(ExitCode.RUNNER_FAILURE)),
    ("prerequisite", int(ExitCode.PREREQUISITE_MISSING)),
])
def test_worker_run_routes_setup_failures_to_pre_lane_terminal_path(tmp_path, failure, expected_code):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker._await_scope_confirmation = lambda: True
    worker._read_request = lambda: None
    worker._create_checkout = lambda: None
    worker.request["kind"] = "lane"
    worker._finish_unstarted = lambda code, _reason: int(code)
    if failure == "unknown-user":
        worker.user_resolver = lambda _name: (_ for _ in ()).throw(KeyError("missing account"))
    else:
        worker.user_resolver = lambda name: RunnerIdentity(
            name, 0 if failure == "root-user" else 1001, 1001, (1001,), "/runner",
        )
    if failure == "runner-factory":
        worker.runner_factory = lambda _identity: (_ for _ in ()).throw(RuntimeError("factory failed"))
    else:
        worker.runner_factory = lambda _identity: object()
    if failure == "prerequisite":
        worker._prerequisites = lambda: (_ for _ in ()).throw(
            AdmissionError(ExitCode.PREREQUISITE_MISSING, "Run-Gate unavailable"),
        )
    assert worker.run() == expected_code


@pytest.mark.parametrize("failure,exit_code", [
    ("live-admission", int(ExitCode.CANCELLED)),
    ("recorded-exit", int(ExitCode.RUNNER_FAILURE)),
    ("live-exception", int(ExitCode.REFUSAL)),
])
def test_worker_run_keeps_charge_after_lane_start_or_recorded_exit(tmp_path, failure, exit_code):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker._await_scope_confirmation = lambda: True
    worker._read_request = lambda: None
    worker._create_checkout = lambda: None
    worker.request.update(kind="lane", sha=SHA)
    worker.runner_factory = lambda _identity: object()
    worker._prerequisites = lambda: ("run-gate", "host", {"runner_name": None})
    worker._wait_for_psi = lambda: None
    worker._seed_stores = lambda: None
    worker._memory_admission = lambda: None
    if failure == "live-admission":
        def start_then_fail(*_args):
            worker._save_state(lane_may_be_live=True)
            raise AdmissionError(exit_code, "cancel observed during lane start")
    elif failure == "recorded-exit":
        def start_then_fail(*_args):
            worker._save_state(lane_process_status=0, lane_may_be_live=False)
            raise AdmissionError(exit_code, "finalizer transport failed")
    else:
        def start_then_fail(*_args):
            worker._save_state(lane_may_be_live=True)
            raise RuntimeError("worker crashed after start")
    worker._run_lane = start_then_fail
    assert worker.run() == int(ExitCode.QUEUED_WITHDRAWN)
    state = worker._state()
    assert state["state"] == "stopping" and state["retry_required"] is True


def test_finish_unstarted_returns_when_durable_state_cannot_be_read(tmp_path):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker._state = lambda: (_ for _ in ()).throw(
        AdmissionError(ExitCode.QUEUED_WITHDRAWN, "state unavailable"),
    )
    try:
        assert worker._finish_unstarted(79, "state unavailable") == 79
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_post_run_gate_validation_skips_project_stat_after_fd_was_closed(tmp_path):
    worker, _runner, _job_dir, checkout = _ready_worker(tmp_path)
    worker.request.update(project=".", lane="r0-r1")
    (checkout / "run-gate.toml").write_text(
        "[lanes.r0-r1]\nenvironment='host'\n[environments.host]\nmode='host'\n",
        encoding="utf-8",
    )
    worker._save_state(lane_mode="host")
    os.close(worker.project_fd)
    worker.project_fd = -1
    try:
        worker._validate_run_gate_after_run()
        assert worker.lane_error is None
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_inspect_container_binding_wraps_generic_and_invalid_docker_answers(tmp_path):
    container_id = "a" * 64
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.request.update(project=".", lane="r0-r1")
    worker._save_state(lane_mode="host", runner_name=None)

    class _ErrorDocker:
        def inspect(self, _target):
            raise RuntimeError("inspect transport broke")

    class _InvalidDocker:
        def inspect(self, _target):
            return ["not", "an", "object"]

    event = {"event": "lane-started", "lane": "r0-r1", "container_id": container_id}
    try:
        worker.docker = _ErrorDocker()
        with pytest.raises(AdmissionError, match="inspect .* failed: inspect transport broke"):
            worker._inspect_container_binding(event, expected_sha=SHA)
        worker.docker = _InvalidDocker()
        with pytest.raises(AdmissionError, match="returned an invalid object"):
            worker._inspect_container_binding(event, expected_sha=SHA)
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_exec_binding_wraps_persistent_runner_inspect_errors(tmp_path):
    container_id = "b" * 64
    worker, _runner, _job_dir, checkout = _ready_worker(tmp_path)
    worker.request.update(project=".", lane="r0-r1")
    (checkout / "run-gate.toml").write_text(
        "[lanes.r0-r1]\nenvironment='runner'\n"
        "[environments.runner]\nmode='exec'\ncontainer_name='persistent-runner'\n",
        encoding="utf-8",
    )
    worker._save_state(lane_mode="exec", runner_name="persistent-runner")
    source = str(worker.repo_root / worker.checkout_rel)
    answer = {
        "Id": container_id, "Name": "/persistent-runner", "State": {"Running": True},
        "Mounts": [{"Type": "bind", "Source": source, "Destination": "/workspace"}],
    }

    class _Docker:
        def inspect(self, target):
            if target == container_id:
                return answer
            raise RuntimeError("runner inspect failed")

    worker.docker = _Docker()
    try:
        with pytest.raises(AdmissionError, match="inspect runner .* failed: runner inspect failed"):
            worker._inspect_container_binding(
                {"event": "lane-started", "lane": "r0-r1", "container_id": container_id},
                expected_sha=SHA,
            )
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_publish_terminal_uses_existing_result_without_pending_artifact(tmp_path):
    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path)
    worker.terminal_completed = True
    (job_dir / "result.json").write_text("{}", encoding="utf-8")
    terminal = {
        "verdict": "ERROR", "exit_code": int(ExitCode.REFUSAL),
        "result_sha256": "a" * 64, "result_size": 2, "lane": "r0-r1",
        "api": "fleet-host-agent/1", "collected_at": "2026-11-27T00:00:00Z",
    }
    try:
        worker._publish_terminal(terminal, final_state="failed", reason="refused")
        state = worker._state()
        assert state["state"] == "failed" and state["verdict"] == "ERROR"
        assert state["retry_required"] is False
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_event_follower_advances_over_verdict_events_without_inspection(tmp_path):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker._read_events = lambda **_kwargs: [{"event": "lane-verdict", "lane": "r0-r1"}]
    try:
        worker._follow_lane_events(final=False)
        assert worker.event_cursor == 1
        assert worker.event_observations == []
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_saved_collection_reuses_archive_when_store_was_absent(tmp_path):
    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path)
    collection = {
        "store_rel": None, "artifacts_sha256": "a" * 64, "artifacts_size": 12,
        "store": {"status": "absent"},
    }
    worker._save_state(collection=collection)
    worker._archive_saved = lambda _name, **_kwargs: ("a" * 64, 12)
    try:
        assert worker._saved_collection(None) == collection
        worker._save_state(collection={**collection, "store_rel": "store"})
        assert worker._saved_collection(None) is None
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_wait_for_lane_containers_handles_empty_events_null_inspect_and_bad_state(tmp_path):
    container_id = "d" * 64
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.request["lane"] = "r0-r1"
    worker._follow_lane_events = lambda **_kwargs: None
    worker._read_events = lambda **_kwargs: []
    worker.docker = object()
    try:
        assert worker._wait_for_lane_containers() == []
        assert worker._state()["live_containers"] == []

        worker._save_state(lane_mode="ephemeral", runner_name=None)
        event = {"event": "lane-started", "lane": "r0-r1", "container_id": container_id}
        worker._read_events = lambda **_kwargs: [event]
        worker.docker = type("MissingDocker", (), {"inspect": lambda *_args: None})()
        assert worker._wait_for_lane_containers() == [event]

        worker.docker = type("BadStateDocker", (), {
            "inspect": lambda *_args: {"Id": container_id, "State": {"Running": "false"}},
        })()
        with pytest.raises(AdmissionError, match="state .* is unreadable"):
            worker._wait_for_lane_containers()
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize("lane,environment,message", [
    ({"environment": "x"}, {"mode": "mystery"}, "no resolved execution mode"),
    ({}, {"mode": "exec"}, "no environment name"),
])
def test_container_mode_refuses_unresolved_fallback_facts(tmp_path, monkeypatch, lane, environment, message):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.request.update(project=".", lane="top")
    worker._save_state(lane_mode=None, runner_name=None)
    monkeypatch.setattr(
        executor_worker_module, "_lane_environment", lambda *_args: (lane, environment),
    )
    try:
        with pytest.raises(AdmissionError, match=message):
            worker._container_mode("different-lane")
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_bind_container_events_preserves_each_observation_status(tmp_path):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.request.update(project=".", lane="r0-r1")
    worker._save_state(lane_mode="host", runner_name=None, lane_event_observations=[
        {"index": 0, "binding": {"status": "no-container"}},
        {"index": 1, "binding": {"status": "no-container"}},
        {"index": 2, "binding": {"id": "c" * 64, "mode": "ephemeral"}},
        {"index": 3, "error": "docker transport refused"},
    ])
    events = [
        {"event": "lane-started", "lane": "r0-r1", "container_id": None},
        {"event": "lane-started", "lane": "r0-r1", "container_id": "a" * 64},
        {"event": "lane-started", "lane": "r0-r1", "container_id": "c" * 64},
        {"event": "lane-started", "lane": "r0-r1", "container_id": "d" * 64},
        {"event": "lane-started", "lane": "r0-r1", "container_id": "e" * 64},
    ]
    try:
        bindings = worker._bind_container_events(events, expected_sha=SHA)
        assert [item["status"] for item in bindings] == [
            "no-container", "missing-object", "verified", "inspection-error", "inspection-error",
        ]
        assert "not durably observed" in bindings[-1]["reason"]
        assert worker.lane_error is not None
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_retry_state_save_retries_after_transient_state_read_error(tmp_path):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    real_save = worker._save_state
    real_state = worker._state
    attempts = []
    state_attempts = []

    def fail_save_once(**updates):
        attempts.append(dict(updates))
        if len(attempts) == 1:
            raise OSError("state write unavailable")
        return real_save(**updates)

    def fail_state_once():
        state_attempts.append(1)
        if len(state_attempts) == 1:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "state read unavailable")
        return real_state()

    worker._save_state = fail_save_once
    worker._state = fail_state_once
    worker.runner_process = type("Process", (), {"pid": 321, "poll": lambda _self: None})()
    worker.waiter = lambda _seconds: None
    try:
        result = worker._retry_state_save(saved=True)
        assert result["saved"] is True
        assert len(attempts) == 2 and len(state_attempts) == 2
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_scope_process_reader_and_host_fact_parsers_cover_injected_sources(monkeypatch):
    import builtins

    real_open = builtins.open
    documents = {
        "/proc/self/cgroup": "2:cpu:/unified\n0::/system.slice/job.scope\n",
        "/sys/fs/cgroup/system.slice/job.scope/cgroup.procs": "123\n456\n",
        "/proc/meminfo": "MemTotal: 1048576 kB\nMemAvailable: 524288 kB\n",
    }

    def fake_open(path, *args, **kwargs):
        if str(path) in documents:
            return io.StringIO(documents[str(path)])
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fake_open)
    assert executor_worker_module._scope_processes("job.scope") == (123, 456)
    assert executor_worker_module._read_meminfo().startswith("MemTotal:")
    assert executor_worker_module._parse_meminfo("junk\nMemTotal: 1024 kB\nMemAvailable: 512 kB\n") == (1, 0)
    for text, row in (
        ("some avg10=-1 total=0\n", "some"),
        ("some avg10=nan total=0\n", "some"),
        ("full avg60=1 total=0\n", "full"),
    ):
        with pytest.raises(ValueError, match="avg10 is unavailable"):
            executor_worker_module._pressure_value(text, row)
    for meminfo in (
        "MemAvailable: 1 kB\n", "MemTotal: 0 kB\nMemAvailable: 1 kB\n",
        "MemTotal: 1 kB\nMemAvailable: -1 kB\n",
    ):
        with pytest.raises(ValueError, match="MemTotal/MemAvailable"):
            executor_worker_module._parse_meminfo(meminfo)

    documents["/proc/self/cgroup"] = "2:cpu:/other\n"
    with pytest.raises(OSError, match="does not match scope"):
        executor_worker_module._scope_processes("job.scope")
    documents["/proc/self/cgroup"] = "0::/system.slice/../job.scope\n"
    with pytest.raises(OSError, match="does not match scope"):
        executor_worker_module._scope_processes("job.scope")
    documents["/proc/self/cgroup"] = "0::/system.slice/job.scope\n"
    documents["/sys/fs/cgroup/system.slice/job.scope/cgroup.procs"] = "0\n"
    with pytest.raises(OSError, match="invalid pid"):
        executor_worker_module._scope_processes("job.scope")


def test_extract_seed_rejects_unreadable_and_empty_archives_with_closed_descriptors(tmp_path):
    root = tmp_path / "seed-root"
    root.mkdir()
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        content = b"not a tar archive"
        input_fd = _pipe_with(content)
        try:
            with pytest.raises(ValueError, match="unreadable-archive"):
                _extract_seed(root_fd, input_fd, "store", hashlib.sha256(content).hexdigest(), len(content), 128)
        finally:
            os.close(input_fd)

        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz"):
            pass
        empty_archive = stream.getvalue()
        input_fd = _pipe_with(empty_archive)
        try:
            _extract_seed(
                root_fd, input_fd, "empty-store", hashlib.sha256(empty_archive).hexdigest(),
                len(empty_archive), 128,
            )
        finally:
            os.close(input_fd)
        assert (root / "empty-store").is_dir()
        assert not list(root.glob(".fleetd-seed-input-*"))

        for target, expected_sha, expected_size, max_bytes, fragment in (
            ("store", "a" * 64, 0, 128, "invalid-descriptor"),
            ("store", "a" * 64, 1, 0, "invalid-descriptor"),
        ):
            input_fd = _pipe_with(b"x")
            try:
                with pytest.raises(ValueError, match=fragment):
                    _extract_seed(root_fd, input_fd, target, expected_sha, expected_size, max_bytes)
            finally:
                os.close(input_fd)
    finally:
        os.close(root_fd)


@pytest.mark.parametrize("failure", ["policy", "nonregular-lock", "lock", "handshake"])
def test_worker_main_refuses_each_pre_handshake_failure_without_running_worker(
    tmp_path, monkeypatch, capsys, failure,
):
    policy, _checkout = _policy(tmp_path)
    if failure == "policy":
        monkeypatch.setattr(
            executor_worker_module, "load_agent_policy",
            lambda _path: (_ for _ in ()).throw(ValueError("bad policy")),
        )
    else:
        monkeypatch.setattr(executor_worker_module, "load_agent_policy", lambda _path: policy)
    if failure == "nonregular-lock":
        lock_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    else:
        lock_path = tmp_path / "worker-lock"
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    read_fd, write_fd = os.pipe()
    if failure == "lock":
        monkeypatch.setattr(
            executor_worker_module.fcntl, "flock",
            lambda *_args: (_ for _ in ()).throw(BlockingIOError("lock held")),
        )
    if failure == "handshake":
        real_write = executor_worker_module.os.write
        monkeypatch.setattr(
            executor_worker_module.os, "write",
            lambda fd, data: 0 if fd == write_fd else real_write(fd, data),
        )
    try:
        status = worker_main([
            "worker", "--state-dir", str(tmp_path / "state"), "--policy", str(tmp_path / "agent.toml"),
            "--job-id", "controller-run-1", "--lock-fd", str(lock_fd), "--handshake-fd", str(write_fd),
        ])
        assert status == int(ExitCode.RUNNER_FAILURE)
        assert "fleetd-executor-worker:" in capsys.readouterr().err
    finally:
        os.close(lock_fd)
        os.close(write_fd)
        assert os.read(read_fd, 64) == b""
        os.close(read_fd)


def test_helper_main_catches_invalid_event_path_and_closes_working_directory(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    status = worker_main(["prepare-event", "--relative", "no-parent-separator"])
    assert status == int(ExitCode.RUNNER_FAILURE)
    assert "fleetd-executor-worker:" in capsys.readouterr().err


def test_seed_upload_scanner_skips_candidate_with_wrong_uploaded_object_metadata(tmp_path):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    upload_dir = worker.state_dir / "uploads" / worker.group
    (upload_dir / "assay-seed-2.tgz").mkdir(parents=True)
    calls = []

    def wrong_objects(name):
        calls.append(name)
        if name == "assay-seed-2.tgz":
            return [("b" * 64, 11)]
        return []

    worker._uploaded_objects = wrong_objects
    try:
        assert worker._find_seed_upload("a" * 64, 10) is None
        assert calls == ["assay-seed-2.tgz"]
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_wait_for_lane_containers_inspects_and_accepts_stopped_full_id(tmp_path):
    container_id = "c" * 64

    class _Docker:
        def __init__(self):
            self.calls = []

        def inspect(self, target):
            self.calls.append(target)
            return {"Id": container_id, "State": {"Running": False}}

    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.request["lane"] = "r0-r1"
    worker._save_state(lane_mode="ephemeral", runner_name=None)
    worker._follow_lane_events = lambda **_kwargs: None
    event = {"event": "lane-started", "lane": "r0-r1", "container_id": container_id}
    worker._read_events = lambda **_kwargs: [event]
    docker = _Docker()
    worker.docker = docker
    try:
        assert worker._wait_for_lane_containers() == [event]
        assert docker.calls == [container_id]
        assert worker._state()["live_containers"] == []
        assert worker._state()["retry_required"] is False
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_job_state_lock_closes_without_unlocking_when_acquire_fails(tmp_path, monkeypatch):
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    observed_fd = []

    def fail_lock(fd, operation):
        observed_fd.append(fd)
        raise OSError("lock unavailable")

    monkeypatch.setattr(executor_module.fcntl, "flock", fail_lock)
    with pytest.raises(AdmissionError, match="cannot lock executor state"):
        with _job_state_lock(job_dir):
            pytest.fail("lock acquisition unexpectedly succeeded")
    assert len(observed_fd) == 1
    with pytest.raises(OSError):
        os.fstat(observed_fd[0])


def test_event_follower_retries_an_inspect_failure_without_advancing_cursor(tmp_path):
    container_id = "a" * 64
    event = {"event": "lane-started", "lane": "r0-r1", "container_id": container_id}
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.request.update(lane="r0-r1", sha=SHA)
    worker._save_state(lane_mode="ephemeral", runner_name=None)
    worker._read_events = lambda **_kwargs: [event]
    attempts = []

    def inspect(_event, **_kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "temporary Docker inspection error")
        return {"id": container_id, "mode": "ephemeral", "lane": "r0-r1"}

    worker._inspect_container_binding = inspect
    try:
        worker._follow_lane_events(final=False)
        assert worker.event_cursor == 0
        assert worker.event_observations == []
        assert worker.lane_error is None
        worker._follow_lane_events(final=False)
        assert worker.event_cursor == 1
        assert worker.event_observations[0]["binding"]["id"] == container_id
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_archive_and_checksum_cleanup_ignore_parts_removed_by_failed_writers(tmp_path, monkeypatch):
    class _Runner:
        def run_to_fd(self, _argv, **_kwargs):
            (job_dir / ".artifacts.tgz.part").unlink()
            return CommandResult(1, b"", b"collection failed")

    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path, _Runner())
    worker._environment = lambda: {"PATH": "/usr/bin"}
    try:
        with pytest.raises(AdmissionError, match="could not collect artifacts archive"):
            worker._write_archive_from_runner(
                "archive-artifacts", "artifacts.tgz", (), root_fd=worker.project_fd, kind="artifacts",
            )

        temporary = job_dir / ".assay-store.tgz.sha256.part"
        real_replace = executor_worker_module.os.replace

        def remove_then_fail(source, destination):
            if Path(source) == temporary:
                temporary.unlink()
                raise OSError("checksum rename failed")
            return real_replace(source, destination)

        monkeypatch.setattr(executor_worker_module.os, "replace", remove_then_fail)
        with pytest.raises(AdmissionError, match="cannot persist assay-store.tgz checksum"):
            worker._write_checksum("a" * 64, "assay-store.tgz")
        assert not temporary.exists()
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize("store_rel,store,checksum,expected", [
    (None, {"status": "collected"}, b"unused", False),
    ([".assay"], {"status": "collected", "sha256": "b" * 64, "size": 7}, b"wrong", False),
    ([".assay"], {"status": "collected", "sha256": "c" * 64, "size": 7}, b"b" * 64 + b"  assay-store.tgz\n", False),
    ([".assay"], {"status": "collected", "sha256": "b" * 64, "size": 8}, b"b" * 64 + b"  assay-store.tgz\n", False),
    ([".assay"], {"status": "failed"}, b"unused", True),
])
def test_saved_collection_revalidates_store_archive_and_checksum(
    tmp_path, monkeypatch, store_rel, store, checksum, expected,
):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    collection = {
        "store_rel": store_rel,
        "artifacts_sha256": "a" * 64,
        "artifacts_size": 3,
        "store": store,
    }
    worker._state = lambda: {"collection": collection}
    calls = []

    def archive(name, *, kind, store_path=None):
        calls.append((name, kind, store_path))
        return ("a" * 64, 3) if kind == "artifacts" else ("b" * 64, 7)

    worker._archive_saved = archive
    monkeypatch.setattr(executor_worker_module, "_read_regular", lambda *_args, **_kwargs: checksum)
    try:
        result = worker._saved_collection(store_rel)
        assert (result is not None) is expected
        if store_rel is None:
            assert calls == [("artifacts.tgz", "artifacts", None)]
        elif store.get("status") == "collected":
            assert calls[-1] == ("assay-store.tgz", "store", store_rel)
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_collect_records_noncollected_store_log_and_absent_part(tmp_path):
    request = _submit()
    request.update(project=".", stores={"collect": [".assay"], "seed": None})
    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path, request=request)
    calls = []

    def archive(_command, destination, _arguments, *, kind, **_kwargs):
        calls.append((destination, kind))
        if kind == "artifacts":
            path = job_dir / ".artifacts.tgz.part"
            path.write_bytes(b"artifact")
            return "a" * 64, 8, {"files": 1}
        return "", 0, {"status": "failed", "reason": "store unavailable", "log": "store log"}

    worker._write_archive_from_runner = archive
    messages = []
    worker._log = messages.append
    try:
        collection = worker._collect()
        assert collection["store"] == {
            "status": "failed", "reason": "store unavailable", "stores": None,
        }
        assert "store log" in messages
        assert not (job_dir / ".assay-store.tgz.part").exists()
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_collect_store_error_cleans_missing_partial_and_preserves_artifact(tmp_path):
    request = _submit()
    request.update(project=".", stores={"collect": [".assay"], "seed": None})
    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path, request=request)

    def archive(_command, destination, _arguments, *, kind, **_kwargs):
        if kind == "artifacts":
            (job_dir / ".artifacts.tgz.part").write_bytes(b"artifact")
            return "a" * 64, 8, {"files": 1}
        raise AdmissionError(ExitCode.STORE_COLLECTION_FAILED, "store transfer failed")

    worker._write_archive_from_runner = archive
    try:
        with pytest.raises(AdmissionError, match="store transfer failed"):
            worker._collect()
        assert (job_dir / "artifacts.tgz").exists()
        assert not (job_dir / ".assay-store.tgz.part").exists()
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_collect_reads_repository_relative_store_from_checkout_root(tmp_path):
    request = _submit()
    request.update(
        project="fleetd", stores={"collect": ["fleetd/.assay"], "seed": None},
    )
    worker, _runner, job_dir, checkout = _ready_worker(tmp_path, request=request)
    project = checkout / "fleetd"
    project.mkdir()
    worker.project_fd = os.open(project, os.O_RDONLY | os.O_DIRECTORY)
    observations = []

    def archive(_command, destination, arguments, *, root_fd, kind, **_kwargs):
        observations.append((destination, tuple(arguments), root_fd, kind))
        if kind == "artifacts":
            (job_dir / ".artifacts.tgz.part").write_bytes(b"artifacts")
            return "a" * 64, 9, {"files": []}
        return "", 0, {"status": "absent", "reason": "no carry"}

    worker._write_archive_from_runner = archive
    try:
        collection = worker._collect()
        assert observations[0][2] == worker.project_fd
        assert observations[1][2] == worker.checkout_fd
        assert observations[1][1] == ("--relative", "fleetd/.assay")
        assert collection["store"]["status"] == "absent"
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_run_lane_waits_through_a_second_nonexiting_poll(tmp_path):
    request = _submit()
    request.update(project=".", lane="r0-r1", extra_argv=[], base=None)

    class _Process:
        pid = 991

        def __init__(self):
            self.values = [None, None, None, 0]

        def poll(self):
            return self.values.pop(0)

    process = _Process()

    class _Runner:
        def start(self, *_args, **_kwargs):
            return process

    waits = []
    worker, _runner, _job_dir, _checkout = _ready_worker(
        tmp_path, _Runner(), request=request, waiter=waits.append,
    )
    worker._helper = lambda *_args, **_kwargs: CommandResult(0)
    worker._environment = lambda: {"PATH": "/usr/bin"}
    worker._follow_lane_events = lambda **_kwargs: None
    try:
        assert worker._run_lane("run-gate", "host", {"runner_name": None}, None) == 0
        assert waits == [1, 1]
        assert worker._state()["lane_process_status"] == 0
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize("state_sequence,expected_waits", [
    ([{"Running": False}], 0),
    ([{"Running": True}, {"Running": False}], 1),
])
def test_lane_container_wait_handles_host_events_missing_ids_and_running_transitions(
    tmp_path, state_sequence, expected_waits,
):
    container_id = "d" * 64

    class _Docker:
        def __init__(self):
            self.answers = iter(state_sequence)
            self.calls = []

        def inspect(self, target):
            self.calls.append(target)
            state = next(self.answers)
            return {"Id": container_id, "State": state}

    request = _submit()
    request.update(project=".", lane="r0-r1")
    waits = []
    worker, _runner, _job_dir, _checkout = _ready_worker(
        tmp_path, request=request, waiter=waits.append,
    )
    worker._save_state(lane_mode="ephemeral", runner_name=None)
    worker._follow_lane_events = lambda **_kwargs: None
    event = {"event": "lane-started", "lane": "r0-r1", "container_id": container_id}
    worker._read_events = lambda **_kwargs: [event]
    docker = _Docker()
    worker.docker = docker
    try:
        assert worker._wait_for_lane_containers() == [event]
        assert docker.calls == [container_id] * (expected_waits + 1)
        assert waits == [1] * expected_waits
        assert worker._state()["live_containers"] == []
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_lane_container_wait_accepts_host_lane_without_container_id(tmp_path):
    request = _submit()
    request.update(project=".", lane="r0-r1")

    class _Docker:
        def inspect(self, _target):
            pytest.fail("host lane without a container must not be inspected")

    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path, request=request, docker=_Docker())
    worker._save_state(lane_mode="host", runner_name=None)
    worker._follow_lane_events = lambda **_kwargs: None
    event = {"event": "lane-started", "lane": "r0-r1", "container_id": None}
    worker._read_events = lambda **_kwargs: [event]
    try:
        assert worker._wait_for_lane_containers() == [event]
        assert worker._state()["live_containers"] == []
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize("failure, expected", [
    ("inspect", "Docker inspect"),
    ("remove", "Docker remove"),
])
def test_job_container_teardown_keeps_errors_retryable(tmp_path, failure, expected):
    container_id = "e" * 64

    class _Docker:
        def __init__(self):
            self.queries = 0

        def job_containers(self, _job_id):
            self.queries += 1
            return [container_id] if self.queries == 1 else []

        def inspect(self, _target):
            if failure == "inspect":
                raise RuntimeError("inspect transport failed")
            return {
                "Id": container_id,
                "Config": {"Labels": {"run-gate.job": "controller-run-1"}},
                "State": {"Running": False},
            }

        def remove_stopped(self, _container_id):
            raise RuntimeError("remove transport failed")

    docker = _Docker()
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path, docker=docker)
    with pytest.raises(AdmissionError, match=expected):
        worker._ensure_job_containers_stopped(remove=True)
    for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
        if fd >= 0:
            os.close(fd)


@pytest.mark.parametrize("summary,fragment", [
    (b"run-gate: lane 'r0-r1' verdict PASS" + b"x" * 65540 + b"\n", "too large"),
    (b"run-gate: lane 'r0-r1' verdict PASS", "incomplete"),
    (b"run-gate: lane 'r0-r1' verdict UNKNOWN; exit_code 0\n", "unknown verdict"),
    (b"run-gate: lane 'r0-r1' verdict PASS; exit_code 0; exit_code 0\n", "repeats exit_code"),
    (b"run-gate: lane 'r0-r1' verdict PASS; exit_code nope\n", "invalid exit_code"),
])
def test_lane_summary_refuses_oversized_incomplete_and_invalid_exact_lines(tmp_path, summary, fragment):
    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path)
    worker.request["lane"] = "r0-r1"
    path = job_dir / "lane.log"
    path.write_bytes(summary)
    try:
        with pytest.raises(AdmissionError, match=fragment):
            worker._last_lane_summary()
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_lane_summary_refuses_non_string_request_lane(tmp_path):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.request["lane"] = None
    with pytest.raises(AdmissionError, match="request lane is invalid"):
        worker._last_lane_summary()
    for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
        if fd >= 0:
            os.close(fd)


def test_lane_summary_accepts_a_verdict_without_lane_exit_code(tmp_path):
    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path)
    worker.request["lane"] = "r0-r1"
    (job_dir / "lane.log").write_text(
        "run-gate: lane 'r0-r1' verdict PASS; duration 2s\n", encoding="utf-8",
    )
    try:
        _line, verdict, lane_exit = worker._last_lane_summary()
        assert verdict == "PASS"
        assert lane_exit is None
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_bind_container_events_skips_lane_verdict_event_records(tmp_path):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.request.update(project=".", lane="r0-r1")
    worker._save_state(lane_mode="host", runner_name=None)
    try:
        assert worker._bind_container_events(
            [{"event": "lane-verdict", "lane": "r0-r1"}], expected_sha=SHA,
        ) == []
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize("updates", [
    {"container_id": 7},
    {"image_id": 7},
    {"adopted": 1},
    {"reason": 7},
])
def test_event_reader_rejects_invalid_lane_started_fields(tmp_path, updates):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.runner_identity = RunnerIdentity("runner", os.getuid(), os.getgid(), (os.getgid(),))
    event_dir = worker.repo_root.parent / ".fleetd-events" / "vbpub"
    event_dir.mkdir(parents=True)
    event_path = event_dir / f"{worker.job_id}.jsonl"
    event = {
        "api": "fleet-host-agent/1", "job_id": worker.job_id, "event": "lane-started",
        "lane": "r0-r1", "container_id": None, "image_id": None, "adopted": False,
    }
    event.update(updates)
    event_path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    event_path.chmod(0o600)
    worker.event_rel = f".fleetd-events/vbpub/{worker.job_id}.jsonl"
    try:
        with pytest.raises(AdmissionError, match="invalid lane-started fields"):
            worker._read_events()
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_checksum_writer_refuses_a_zero_progress_write_and_removes_partial(tmp_path, monkeypatch):
    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path)
    monkeypatch.setattr(executor_worker_module.os, "write", lambda *_args: 0)
    try:
        with pytest.raises(AdmissionError, match="checksum write made no progress"):
            worker._write_checksum("a" * 64, "assay-store.tgz")
        assert not (job_dir / ".assay-store.tgz.sha256.part").exists()
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_seed_extract_handles_nested_directories_missing_stream_and_cleanup_races(tmp_path, monkeypatch):
    root = tmp_path / "seed-root"
    root.mkdir()
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as archive:
            directory = tarfile.TarInfo("group/store/nested/")
            directory.type = tarfile.DIRTYPE
            archive.addfile(directory)
            content = b"nested seed"
            member = tarfile.TarInfo("group/store/nested/value")
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
        body = stream.getvalue()
        input_fd = _pipe_with(body)
        try:
            _extract_seed(root_fd, input_fd, "group/store", hashlib.sha256(body).hexdigest(), len(body), 128)
        finally:
            os.close(input_fd)
        assert (root / "group" / "store" / "nested" / "value").read_bytes() == content

        missing_source = b"x"
        current_target = ["missing"]

        class _Archive:
            def getmembers(self):
                member = tarfile.TarInfo(f"{current_target[0]}/value")
                member.size = 1
                return [member]

            def extractfile(self, _member):
                return None

            def close(self):
                return None

        monkeypatch.setattr(executor_worker_module.tarfile, "open", lambda **_kwargs: _Archive())
        input_fd = _pipe_with(missing_source)
        try:
            with pytest.raises(ValueError, match="unreadable-archive"):
                _extract_seed(
                    root_fd, input_fd, "missing", hashlib.sha256(missing_source).hexdigest(),
                    len(missing_source), 128,
                )
        finally:
            os.close(input_fd)

        current_target[0] = "short"

        class _ShortArchive(_Archive):
            def extractfile(self, _member):
                return io.BytesIO(b"")

        monkeypatch.setattr(executor_worker_module.tarfile, "open", lambda **_kwargs: _ShortArchive())
        input_fd = _pipe_with(missing_source)
        try:
            with pytest.raises(ValueError, match="unreadable-archive"):
                _extract_seed(
                    root_fd, input_fd, "short", hashlib.sha256(missing_source).hexdigest(),
                    len(missing_source), 128,
                )
        finally:
            os.close(input_fd)

        current_target[0] = "missing-again"
        def missing_cleanup(_name, *, dir_fd=None):
            raise FileNotFoundError("temporary already removed")

        monkeypatch.setattr(executor_worker_module.os, "unlink", missing_cleanup)
        input_fd = _pipe_with(missing_source)
        try:
            with pytest.raises(ValueError, match="unreadable-archive"):
                _extract_seed(
                    root_fd, input_fd, "missing-again", hashlib.sha256(missing_source).hexdigest(),
                    len(missing_source), 128,
                )
        finally:
            os.close(input_fd)
    finally:
        os.close(root_fd)


def test_seed_extract_rejects_input_overrun_and_zero_member_write(tmp_path, monkeypatch):
    root = tmp_path / "seed-root"
    root.mkdir()
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        content = b"oversized"
        input_fd = _pipe_with(content)
        try:
            with pytest.raises(ValueError, match="size"):
                _extract_seed(
                    root_fd, input_fd, "store", hashlib.sha256(content).hexdigest(), 1, 128,
                )
        finally:
            os.close(input_fd)

        body = _seed_archive("store/value", b"content")
        real_write = executor_worker_module.os.write
        calls = []
        input_fd = _pipe_with(body)

        def zero_member_write(fd, data):
            calls.append(fd)
            if len(calls) == 2:
                return 0
            return real_write(fd, data)

        monkeypatch.setattr(executor_worker_module.os, "write", zero_member_write)
        try:
            with pytest.raises(ValueError, match="unpack-failed"):
                _extract_seed(
                    root_fd, input_fd, "store", hashlib.sha256(body).hexdigest(), len(body), 128,
                )
        finally:
            os.close(input_fd)
    finally:
        os.close(root_fd)


def test_seed_extract_rejects_zero_progress_while_staging_archive(tmp_path, monkeypatch):
    root = tmp_path / "seed-root"
    root.mkdir()
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    body = _seed_archive("store/value", b"content")
    input_fd = _pipe_with(body)

    def zero_staging_write(_fd, _data):
        return 0

    monkeypatch.setattr(executor_worker_module.os, "write", zero_staging_write)
    try:
        with pytest.raises(ValueError, match="unpack-failed"):
            _extract_seed(
                root_fd, input_fd, "store", hashlib.sha256(body).hexdigest(), len(body), 128,
            )
        assert list(root.iterdir()) == []
    finally:
        os.close(input_fd)
        os.close(root_fd)


def test_seed_extract_handles_temp_open_failure_without_cleanup(tmp_path, monkeypatch):
    root = tmp_path / "seed-root"
    root.mkdir()
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    content = b"x"
    input_fd = _pipe_with(content)
    real_open = executor_worker_module.os.open
    attempted = []

    def fail_temp_open(path, flags, *args, **kwargs):
        attempted.append(str(path))
        if str(path).startswith(".fleetd-seed-input-"):
            raise OSError("seed staging unavailable")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(executor_worker_module.os, "open", fail_temp_open)
    try:
        with pytest.raises(ValueError, match="unpack-failed"):
            _extract_seed(
                root_fd, input_fd, "store", hashlib.sha256(content).hexdigest(), len(content), 128,
            )
        assert len(attempted) == 1
        assert attempted[0].startswith(".fleetd-seed-input-")
        assert list(root.iterdir()) == []
    finally:
        os.close(input_fd)
        os.close(root_fd)


def test_checkout_seed_directory_and_file_modes_match_runner_access_policy(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        nested_fd = executor_worker_module._mkdir_beneath(root_fd, "checkout/.assay")
        os.close(nested_fd)
        assert os.stat(root / "checkout").st_mode & 0o777 == 0o755
        assert os.stat(root / "checkout" / ".assay").st_mode & 0o777 == 0o755

        body = _seed_archive("store/value", b"seed")
        input_fd = _pipe_with(body)
        prior_umask = os.umask(0o077)
        try:
            _extract_seed(
                root_fd, input_fd, "store", hashlib.sha256(body).hexdigest(), len(body), 128,
            )
        finally:
            os.umask(prior_umask)
            os.close(input_fd)
        assert os.stat(root / "store" / "value").st_mode & 0o777 == 0o644
    finally:
        os.close(root_fd)


def test_uploaded_file_cleanup_ignores_a_leaf_removed_during_failure(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "nested").mkdir(parents=True)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    content = b"payload"
    input_fd = _pipe_with(content)
    real_unlink = executor_worker_module.os.unlink
    cleanup_calls = []

    def remove_then_report_missing(name, *, dir_fd=None):
        cleanup_calls.append(name)
        real_unlink(name, dir_fd=dir_fd)
        raise FileNotFoundError("already removed")

    monkeypatch.setattr(executor_worker_module.os, "unlink", remove_then_report_missing)
    try:
        with pytest.raises(ValueError, match="digest or size changed"):
            _write_stdin_file(
                root_fd, "nested/partial", input_fd,
                hashlib.sha256(content).hexdigest(), len(content) + 1,
            )
        assert not (root / "nested" / "partial").exists()
        assert cleanup_calls == ["partial"]
    finally:
        os.close(input_fd)
        os.close(root_fd)


def test_publish_terminal_renames_pending_result_after_durable_completion(tmp_path):
    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path)
    pending = job_dir / "result-pending.json"
    pending.write_text('{"verdict":"ERROR"}', encoding="utf-8")
    completions = []
    state_updates = []
    worker._complete_admission = lambda details: completions.append(dict(details))
    worker._retry_state_save = lambda **updates: state_updates.append(dict(updates)) or updates
    terminal = {
        "verdict": "ERROR", "exit_code": 3, "lane": "r0-r1", "api": "fleet-host-agent/1",
        "collected_at": "2026-11-27T00:00:00Z", "result_sha256": "a" * 64, "result_size": 21,
    }
    try:
        worker._publish_terminal(terminal, final_state="failed", reason="binding failed")
        assert completions == [terminal]
        assert not pending.exists()
        assert (job_dir / "result.json").read_text(encoding="utf-8") == '{"verdict":"ERROR"}'
        assert state_updates[-1]["verdict"] == "ERROR"
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_run_gate_post_validation_accepts_an_absent_assay_directory(tmp_path, monkeypatch):
    worker, _runner, _job_dir, checkout = _ready_worker(tmp_path)
    (checkout / "run-gate.toml").write_text("[lanes.r0-r1]\n", encoding="utf-8")
    worker.request.update(project=".", lane="r0-r1")
    worker._save_state(lane_mode="host")
    monkeypatch.setattr(
        executor_worker_module, "_lane_environment",
        lambda *_args, **_kwargs: ({"environment": "host"}, {"mode": "host"}),
    )
    try:
        worker._validate_run_gate_after_run()
        assert worker.lane_error is None
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_job_container_teardown_removes_only_stopped_exact_ids_and_rechecks(tmp_path):
    container_id = "9" * 64

    class _Docker:
        def __init__(self):
            self.queries = 0
            self.removed = []

        def job_containers(self, _job_id):
            self.queries += 1
            return [container_id] if self.queries == 1 else []

        def inspect(self, target):
            return {
                "Id": target,
                "Config": {"Labels": {"run-gate.job": "controller-run-1"}},
                "State": {"Running": False},
            }

        def remove_stopped(self, target):
            self.removed.append(target)

    waits = []
    docker = _Docker()
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path, docker=docker, waiter=waits.append)
    try:
        worker._ensure_job_containers_stopped(remove=True)
        assert docker.removed == [container_id]
        assert docker.queries == 2
        assert waits == [1]
        assert worker._state()["live_containers"] == []
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_job_container_teardown_treats_an_explicit_missing_inspect_as_gone(tmp_path):
    container_id = "8" * 64

    class _Docker:
        def __init__(self):
            self.queries = 0

        def job_containers(self, _job_id):
            self.queries += 1
            return [container_id] if self.queries == 1 else []

        def inspect(self, _target):
            return None

        def remove_stopped(self, _target):
            pytest.fail("already missing object should not be removed")

    docker = _Docker()
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path, docker=docker)
    try:
        worker._ensure_job_containers_stopped(remove=True)
        assert worker._state()["live_containers"] == []
        assert docker.queries == 2
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize("failure,expected", [
    ("query", "query refused"),
    ("inspect", "inspect refused"),
    ("remove", "remove refused"),
])
def test_job_container_teardown_propagates_admission_refusals(tmp_path, failure, expected):
    container_id = "7" * 64

    class _Docker:
        def job_containers(self, _job_id):
            if failure == "query":
                raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "query refused")
            return [container_id]

        def inspect(self, _target):
            if failure == "inspect":
                raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "inspect refused")
            return {
                "Id": container_id,
                "Config": {"Labels": {"run-gate.job": "controller-run-1"}},
                "State": {"Running": False},
            }

        def remove_stopped(self, _target):
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "remove refused")

    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path, docker=_Docker())
    with pytest.raises(AdmissionError, match=expected):
        worker._ensure_job_containers_stopped(remove=True)
    for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
        if fd >= 0:
            os.close(fd)


def test_worker_environment_does_not_invent_run_gate_expected_runner(tmp_path, monkeypatch):
    monkeypatch.setenv("RUN_GATE_EXPECTED_RUNNER", "host-selected-name")
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    try:
        environment = worker._environment()
        assert "RUN_GATE_EXPECTED_RUNNER" not in environment
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_simple_result_uses_clock_when_collection_timestamp_is_unavailable(tmp_path):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.request["lane"] = "r0-r1"
    worker.clock = lambda: "2026-11-27T00:00:00Z"
    worker._save_state(collection={"status": "missing-timestamp"})
    try:
        terminal = worker._simple_result("ERROR", int(ExitCode.REFUSAL), "missing collection timestamp")
        assert terminal["collected_at"] == "2026-11-27T00:00:00Z"
        result = json.loads((worker.job_dir / "result-pending.json").read_text(encoding="utf-8"))
        assert result["collection"] == {"status": "missing-timestamp"}
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_container_mount_validation_rejects_each_nonmatching_mount_shape(tmp_path):
    container_id = "6" * 64
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.request.update(repo="vbpub", project=".", lane="r0-r1", sha=SHA)
    source = str(worker.repo_root / worker.checkout_rel)
    base = {"Id": container_id, "State": {"Running": False}}
    bad_mount_sets = (
        None,
        [],
        ["not-a-mount"],
        [{"Type": "volume", "Source": source, "Destination": "/workspace"}],
        [{"Type": "bind", "Source": "/other", "Destination": "/workspace"}],
        [{"Type": "bind", "Source": source, "Destination": "relative"}],
        [{"Type": "bind", "Source": source, "Destination": 7}],
    )
    try:
        for mounts in bad_mount_sets:
            with pytest.raises(AdmissionError, match="does not mount this job's checkout"):
                worker._verify_container_observation(
                    {**base, "Mounts": mounts}, container_id=container_id, event_lane="r0-r1",
                    mode="ephemeral", persistent_name=None, expected_sha=SHA,
                )
        with pytest.raises(AdmissionError, match="host-mode lane.*emitted a container id"):
            worker._verify_container_observation(
                {**base, "Mounts": [{"Type": "bind", "Source": source, "Destination": "/workspace"}]},
                container_id=container_id, event_lane="r0-r1", mode="host",
                persistent_name=None, expected_sha=SHA,
            )
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_exec_binding_preserves_admission_inspect_failure_and_refuses_wrong_runner_id(tmp_path):
    container_id = "5" * 64
    worker, _runner, _job_dir, checkout = _ready_worker(tmp_path)
    worker.request.update(project=".", lane="r0-r1")
    (checkout / "run-gate.toml").write_text(
        "[lanes.r0-r1]\nenvironment='runner'\n"
        "[environments.runner]\nmode='exec'\ncontainer_name='persistent-runner'\n",
        encoding="utf-8",
    )
    worker._save_state(lane_mode="exec", runner_name="persistent-runner")
    event = {"event": "lane-started", "lane": "r0-r1", "container_id": container_id}

    class _InspectRefusal:
        def inspect(self, _target):
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "inspect refused")

    worker.docker = _InspectRefusal()
    try:
        with pytest.raises(AdmissionError, match="inspect refused"):
            worker._inspect_container_binding(event, expected_sha=SHA)

        source = str(worker.repo_root / worker.checkout_rel)
        answer = {
            "Id": container_id, "Name": "/persistent-runner", "State": {"Running": True},
            "Mounts": [{"Type": "bind", "Source": source, "Destination": "/workspace"}],
        }

        class _WrongNameTarget:
            def inspect(self, target):
                return answer if target == container_id else {**answer, "Id": "4" * 64}

        worker.docker = _WrongNameTarget()
        with pytest.raises(AdmissionError, match="does not match event container"):
            worker._inspect_container_binding(event, expected_sha=SHA)
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_container_teardown_rejects_incomplete_records_and_missing_providers(tmp_path):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    try:
        with pytest.raises(AdmissionError, match="record is incomplete"):
            worker._teardown_containers([{"mode": "ephemeral", "lane": "r0-r1"}], expected_sha=SHA)
        worker.docker = object()
        with pytest.raises(AdmissionError, match="teardown provider is unavailable"):
            worker._teardown_containers(
                [{"mode": "ephemeral", "id": "3" * 64, "lane": "r0-r1"}], expected_sha=SHA,
            )
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_worker_run_unlocks_memory_ticket_when_unlock_itself_fails(tmp_path, monkeypatch):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    memory_lock = os.open(tmp_path / "memory.ticket", os.O_CREAT | os.O_RDWR, 0o600)
    worker.memory_lock_fd = memory_lock
    worker._await_scope_confirmation = lambda: True

    def fail_request():
        raise AdmissionError(ExitCode.REFUSAL, "invalid durable request")

    worker._read_request = fail_request
    worker._finish_unstarted = lambda exit_code, _reason: int(exit_code)
    real_flock = executor_worker_module.fcntl.flock

    def fail_memory_unlock(fd, operation):
        if fd == memory_lock and operation == fcntl.LOCK_UN:
            raise OSError("memory lock unlock failed")
        return real_flock(fd, operation)

    monkeypatch.setattr(executor_worker_module.fcntl, "flock", fail_memory_unlock)
    assert worker.run() == int(ExitCode.REFUSAL)
    with pytest.raises(OSError):
        os.fstat(memory_lock)


def test_finish_unstarted_keeps_charge_when_request_identity_is_missing(tmp_path):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.group = None
    try:
        result = worker._finish_unstarted(int(ExitCode.REFUSAL), "missing identity")
        assert result == int(ExitCode.QUEUED_WITHDRAWN)
        assert worker._state()["retry_required"] is True
        assert "inherited admission lock" in worker._state()["reason"]
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_finalize_lane_retries_oserror_during_collection_then_publishes(tmp_path):
    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path)
    worker.request.update(sha=SHA, project=".", lane="r0-r1")
    worker._save_state(lane_mode="host", runner_name=None)
    worker._follow_lane_events = lambda **_kwargs: None
    worker._read_events = lambda **_kwargs: []
    worker._validate_run_gate_after_run = lambda: None
    worker._ensure_scope_is_only_worker = lambda: None
    worker._ensure_job_containers_stopped = lambda **_kwargs: None
    worker._bind_container_events = lambda *_args, **_kwargs: []
    collects = []

    def collect():
        collects.append(1)
        if len(collects) == 1:
            raise OSError("temporary store read failure")
        worker._save_state(collection={"collected_at": "2026-11-27T00:00:00Z"})
        return worker._state()["collection"]

    worker._collect = collect
    terminal = {
        "verdict": "ERROR", "exit_code": 3, "lane": "r0-r1", "api": "fleet-host-agent/1",
        "collected_at": "2026-11-27T00:00:00Z", "result_sha256": "a" * 64,
        "result_size": 12, "binding_reasons": ["test binding failure"],
    }

    def bind(*_args, **_kwargs):
        (job_dir / "result-pending.json").write_text(json.dumps({
            "containers": [], "binding": {"reasons": ["test binding failure"]},
        }), encoding="utf-8")
        return terminal

    worker._bind_verdict = bind
    worker._teardown_containers = lambda *_args, **_kwargs: None
    worker._remove_checkout = lambda: None
    published = []
    worker._publish_terminal = lambda value, **kwargs: published.append((value, kwargs))
    waits = []
    worker.waiter = waits.append
    try:
        assert worker._finalize_lane(0) == 3
        assert len(collects) == 2
        assert waits == [1]
        assert len(published) == 1
        assert worker._state()["retry_required"] is True
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_finish_unstarted_skips_collection_without_checkout(tmp_path):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.request["lane"] = "r0-r1"
    worker.request_digest = "d" * 64
    worker.group = "controller"
    os.close(worker.checkout_fd)
    worker.checkout_fd = -1
    worker.runner = None
    worker.checkout_rel = ""
    worker._ensure_scope_is_only_worker = lambda: None
    worker._ensure_job_containers_stopped = lambda **_kwargs: None
    calls = []
    worker._publish_terminal = lambda *_args, **_kwargs: calls.append("published")
    try:
        assert worker._finish_unstarted(int(ExitCode.PREREQUISITE_MISSING), "missing optional image") == int(ExitCode.PREREQUISITE_MISSING)
        assert calls == ["published"]
        assert json.loads((worker.job_dir / "result-pending.json").read_text())["verdict"] == "NOT_RUN"
    finally:
        for fd in (worker.project_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize("damage,expected", [
    ("broken-config", "run-gate-config-invalid-after-lane"),
    ("assay-symlink", "assay-directory-symlink-after-lane"),
])
def test_finalize_lane_turns_permanent_repository_damage_into_terminal_error(tmp_path, damage, expected):
    worker, _runner, job_dir, checkout = _ready_worker(tmp_path)
    worker.request.update(sha=SHA, project=".", lane="r0-r1")
    config = checkout / "run-gate.toml"
    config.write_text(
        "[lanes.r0-r1]\nenvironment='host'\n[environments.host]\nmode='host'\n",
        encoding="utf-8",
    )
    if damage == "broken-config":
        config.write_text("[lanes.r0-r1\n", encoding="utf-8")
    else:
        (checkout / ".assay").symlink_to(checkout / "missing-assay")
    worker._save_state(
        lane_mode="host", runner_name=None, checkout={"ready": True, "sha": SHA},
    )
    (job_dir / "job.log").write_text(
        f"2026-11-27T00:00:00Z checkout {worker.repo_root / worker.checkout_rel} at {SHA}\n",
        encoding="utf-8",
    )
    (job_dir / "lane.log").write_text(
        "run-gate: lane 'r0-r1' verdict PASS; exit_code 0\n", encoding="utf-8",
    )
    event = {
        "api": "fleet-host-agent/1", "job_id": worker.job_id, "event": "lane-verdict",
        "lane": "r0-r1", "verdict": "PASS", "exit_code": 0, "lane_exit_code": 0,
    }
    worker._read_events = lambda **_kwargs: [event]
    worker._ensure_scope_is_only_worker = lambda: None
    worker._ensure_job_containers_stopped = lambda **_kwargs: None
    worker._bind_container_events = lambda *_args, **_kwargs: []

    def collect():
        worker._save_state(collection={"collected_at": "2026-11-27T00:00:00Z"})
        return worker._state()["collection"]

    worker._collect = collect
    worker._teardown_containers = lambda *_args, **_kwargs: None
    worker._remove_checkout = lambda: None
    published = []
    worker._publish_terminal = lambda value, **kwargs: published.append((dict(value), kwargs))
    try:
        assert worker._finalize_lane(0) == int(ExitCode.IDENTITY_OR_TRANSPORT)
        result = json.loads((job_dir / "result-pending.json").read_text(encoding="utf-8"))
        assert result["verdict"] == "ERROR"
        assert any(expected in reason for reason in result["binding"]["reasons"])
        assert published[0][1]["final_state"] == "failed"
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_finalize_lane_turns_null_container_id_into_terminal_error(tmp_path):
    worker, _runner, job_dir, checkout = _ready_worker(tmp_path)
    worker.request.update(sha=SHA, project=".", lane="r0-r1")
    (checkout / "run-gate.toml").write_text(
        "[lanes.r0-r1]\nenvironment='tester'\n"
        "[environments.tester]\nmode='ephemeral'\nimage='tester:local'\n",
        encoding="utf-8",
    )
    worker._save_state(
        lane_mode="ephemeral", runner_name=None, checkout={"ready": True, "sha": SHA},
    )
    (job_dir / "job.log").write_text(
        f"2026-11-27T00:00:00Z checkout {worker.repo_root / worker.checkout_rel} at {SHA}\n",
        encoding="utf-8",
    )
    (job_dir / "lane.log").write_text(
        "run-gate: lane 'r0-r1' verdict PASS; exit_code 0\n", encoding="utf-8",
    )
    events = [
        {
            "api": "fleet-host-agent/1", "job_id": worker.job_id, "event": "lane-started",
            "lane": "r0-r1", "container_id": None, "image_id": None, "adopted": False,
        },
        {
            "api": "fleet-host-agent/1", "job_id": worker.job_id, "event": "lane-verdict",
            "lane": "r0-r1", "verdict": "PASS", "exit_code": 0, "lane_exit_code": 0,
        },
    ]
    worker._read_events = lambda **_kwargs: events
    worker.docker = object()
    worker._ensure_scope_is_only_worker = lambda: None
    worker._ensure_job_containers_stopped = lambda **_kwargs: None

    def collect():
        worker._save_state(collection={"collected_at": "2026-11-27T00:00:00Z"})
        return worker._state()["collection"]

    worker._collect = collect
    worker._teardown_containers = lambda *_args, **_kwargs: None
    worker._remove_checkout = lambda: None
    published = []
    worker._publish_terminal = lambda value, **kwargs: published.append((dict(value), kwargs))
    try:
        assert worker._finalize_lane(0) == int(ExitCode.IDENTITY_OR_TRANSPORT)
        result = json.loads((job_dir / "result-pending.json").read_text(encoding="utf-8"))
        assert result["verdict"] == "ERROR"
        assert any("has no full lane-started id" in reason for reason in result["binding"]["reasons"])
        assert published[0][1]["final_state"] == "failed"
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_saved_collection_discards_a_store_archive_that_cannot_be_reopened(tmp_path):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    digest = "a" * 64
    worker._save_state(collection={
        "store_rel": "stores/cache",
        "artifacts_sha256": digest,
        "artifacts_size": 4,
        "store": {"status": "collected", "sha256": digest, "size": 4},
    })

    def archive_saved(name, **_kwargs):
        if name == "artifacts.tgz":
            return digest, 4
        raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "saved store archive disappeared")

    worker._archive_saved = archive_saved
    try:
        assert worker._saved_collection("stores/cache") is None
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_event_reader_rejects_an_extra_lane_started_field(tmp_path):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.runner_identity = RunnerIdentity("runner", os.getuid(), os.getgid(), (os.getgid(),))
    event_dir = worker.repo_root.parent / ".fleetd-events" / "vbpub"
    event_dir.mkdir(parents=True)
    event_path = event_dir / f"{worker.job_id}.jsonl"
    event_path.write_text(json.dumps({
        "api": "fleet-host-agent/1", "job_id": worker.job_id, "event": "lane-started",
        "lane": "r0-r1", "container_id": None, "image_id": None, "adopted": False,
        "unreviewed": True,
    }) + "\n", encoding="utf-8")
    event_path.chmod(0o600)
    worker.event_rel = f".fleetd-events/vbpub/{worker.job_id}.jsonl"
    try:
        with pytest.raises(AdmissionError, match="unexpected lane-started shape"):
            worker._read_events()
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize("mode,event,answer,message", [
    ("host", {"event": "lane-started", "container_id": "a" * 64}, None, "has no lane"),
    ("ephemeral", {"event": "lane-started", "lane": "r0-r1", "container_id": "short"}, None, "non-full container id"),
    ("host", {"event": "lane-started", "lane": "r0-r1", "container_id": "a" * 64}, None, "host-mode lane"),
    ("ephemeral", {"event": "lane-started", "lane": "r0-r1", "container_id": "a" * 64},
     {"Id": "b" * 64, "State": {"Running": False}}, "did not confirm full id"),
    ("exec", {"event": "lane-started", "lane": "r0-r1", "container_id": "a" * 64},
     {"Id": "a" * 64, "State": {"Running": True}}, None),
])
def test_lane_container_wait_covers_bad_host_exec_and_full_id_answers(tmp_path, mode, event, answer, message):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.request["lane"] = "r0-r1"
    worker._save_state(lane_mode=mode, runner_name="persistent-runner" if mode == "exec" else None)
    worker._follow_lane_events = lambda **_kwargs: None
    worker._read_events = lambda **_kwargs: [event]

    class _Docker:
        def inspect(self, _target):
            return answer

    worker.docker = _Docker()
    try:
        if message is not None:
            with pytest.raises(AdmissionError, match=message):
                worker._wait_for_lane_containers()
        else:
            assert worker._wait_for_lane_containers() == [event]
            assert worker._state()["live_containers"] == []
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize("failure,message", [
    ("admission", "wrapped inspect refusal"),
    ("generic", "Docker inspect .* failed"),
    ("invalid-answer", "returned an invalid object"),
    ("invalid-state", "state for .* is unreadable"),
])
def test_container_teardown_retries_only_after_confirmable_inspection(tmp_path, failure, message):
    container_id = "c" * 64
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.request.update(project=".", lane="r0-r1", sha=SHA)
    source = str(worker.repo_root / worker.checkout_rel)
    labels = {
        "run-gate.repo": "vbpub", "run-gate.project": ".", "run-gate.lane": "r0-r1",
        "run-gate.commit": SHA, "run-gate.name-schema": "2", "run-gate.started": "now",
        "run-gate.job": worker.job_id,
    }
    answer = {
        "Id": container_id, "State": {"Running": False},
        "Mounts": [{"Type": "bind", "Source": source, "Destination": "/workspace"}],
        "Config": {"Labels": labels},
    }

    class _Docker:
        def inspect(self, _target):
            if failure == "admission":
                raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "wrapped inspect refusal")
            if failure == "generic":
                raise RuntimeError("socket unavailable")
            if failure == "invalid-answer":
                return []
            if failure == "invalid-state":
                return {**answer, "State": {"Running": "false"}}
            return answer

        def remove_stopped(self, _target):
            pytest.fail("unconfirmed container must not be removed")

    worker.docker = _Docker()
    record = {"id": container_id, "lane": "r0-r1", "mode": "ephemeral", "name": None}
    try:
        with pytest.raises(AdmissionError, match=message):
            worker._teardown_containers([record], expected_sha=SHA)
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_exec_binding_preserves_admission_failure_from_persistent_name_inspect(tmp_path):
    container_id = "d" * 64
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.request.update(project=".", lane="r0-r1", sha=SHA)
    worker._save_state(lane_mode="exec", runner_name="persistent-runner")
    source = str(worker.repo_root / worker.checkout_rel)

    class _Docker:
        def inspect(self, target):
            if target == "persistent-runner":
                raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "named runner inspect refused")
            return {
                "Id": container_id, "Name": "/persistent-runner", "State": {"Running": True},
                "Mounts": [{"Type": "bind", "Source": source, "Destination": "/workspace"}],
            }

    worker.docker = _Docker()
    try:
        with pytest.raises(AdmissionError, match="named runner inspect refused"):
            worker._inspect_container_binding(
                {"event": "lane-started", "lane": "r0-r1", "container_id": container_id},
                expected_sha=SHA,
            )
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_lane_summary_skips_long_and_partial_unanchored_lines(tmp_path):
    worker, _runner, job_dir, _checkout = _ready_worker(tmp_path)
    worker.request["lane"] = "r0-r1"
    (job_dir / "lane.log").write_bytes(
        b"x" * 70000 + b"\n"
        + b"run-gate: lane 'r0-r1' verdict PASS; exit_code 0\n"
        + b"partial diagnostic"
    )
    try:
        summary, verdict, exit_code = worker._last_lane_summary()
        assert summary == "run-gate: lane 'r0-r1' verdict PASS; exit_code 0"
        assert verdict == "PASS" and exit_code == 0
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_verdict_binding_records_inspection_errors_and_rejects_invalid_lane(tmp_path):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.request.update(repo="vbpub", lane="r0-r1", sha=SHA)
    worker._host_checkout_line = lambda: ("checkout line", SHA)
    worker._container_mode = lambda _lane: ("host", None)
    worker._last_lane_summary = lambda: (
        "run-gate: lane 'r0-r1' verdict PASS; exit_code 0", "PASS", 0,
    )
    verdict_event = {
        "event": "lane-verdict", "lane": "r0-r1", "verdict": "PASS",
        "exit_code": 0, "lane_exit_code": 0,
    }
    try:
        terminal = worker._bind_verdict(
            [verdict_event], 0,
            [{"id": "e" * 64, "status": "inspection-error", "reason": "inspection was unavailable"}],
        )
        assert terminal["verdict"] == "ERROR"
        saved = json.loads((worker.job_dir / "result-pending.json").read_text(encoding="utf-8"))
        assert "inspection was unavailable" in saved["binding"]["reasons"]

        verified = worker._bind_verdict(
            [verdict_event], 0, [{"id": "e" * 64, "status": "verified"}],
        )
        assert verified["verdict"] == "PASS"

        worker.request["lane"] = None
        with pytest.raises(AdmissionError, match="request lane is invalid"):
            worker._bind_verdict([verdict_event], 0, [])
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_container_event_binding_rejects_a_missing_lane(tmp_path):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    try:
        with pytest.raises(AdmissionError, match="lane-started event has no lane"):
            worker._bind_container_events(
                [{"event": "lane-started", "lane": None, "container_id": None}],
                expected_sha=SHA,
            )
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_simple_result_preserves_a_string_collection_timestamp(tmp_path):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker._save_state(collection={"collected_at": "2026-11-27T00:00:00Z"})
    worker.clock = lambda: pytest.fail("a durable collection timestamp must be retained")
    try:
        result = worker._simple_result("ERROR", 3, "test error")
        assert result["collected_at"] == "2026-11-27T00:00:00Z"
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_run_gate_post_validation_accepts_a_regular_assay_directory(tmp_path):
    worker, _runner, _job_dir, checkout = _ready_worker(tmp_path)
    (checkout / "run-gate.toml").write_text(
        "[lanes.r0-r1]\nenvironment='host'\n[environments.host]\nmode='host'\n",
        encoding="utf-8",
    )
    (checkout / ".assay").mkdir()
    worker.request.update(project=".", lane="r0-r1")
    worker._save_state(lane_mode="host")
    try:
        worker._validate_run_gate_after_run()
        assert worker.lane_error is None
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize("case", [
    "unobserved-event", "exec-cancel", "term-failed", "guard-unavailable",
    "guard-deregister-error", "guard-unknown", "exec-without-verdict",
])
def test_finalize_lane_keeps_retryable_stop_proofs_pending(tmp_path, case):
    class StopRetry(RuntimeError):
        pass

    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.request.update(sha=SHA, lane="r0-r1", project=".")
    mode = "exec" if case in {"exec-cancel", "exec-without-verdict"} else "host"
    state_updates = {"lane_mode": mode, "runner_name": "persistent-runner" if mode == "exec" else None}
    if case != "unobserved-event":
        state_updates["cancel_requested"] = case != "exec-without-verdict"
    if case in {"term-failed", "guard-unavailable", "guard-deregister-error", "guard-unknown"}:
        state_updates.update(cancel_requested=True, stop_signal="sent")
    if case == "term-failed":
        state_updates["stop_signal"] = "failed"
        state_updates["reason"] = "TERM failed: denied"
    if case in {"guard-unavailable", "guard-deregister-error", "guard-unknown"}:
        guard_present = None if case == "guard-unknown" else True
        state_updates["guard"] = {"present": guard_present}
    worker._save_state(**state_updates)
    event = {"event": "lane-verdict", "lane": "r0-r1"}
    worker._follow_lane_events = lambda **_kwargs: None
    worker._read_events = lambda **_kwargs: [event] if case == "unobserved-event" else []
    worker.event_cursor = 0
    if case == "guard-deregister-error":
        worker.thrashguard_client = type(
            "Guard", (), {"deregister": lambda *_args: (_ for _ in ()).throw(RuntimeError("guard unavailable"))},
        )()
    waits = []

    def wait_twice(_seconds):
        waits.append(1)
        if len(waits) == 2:
            raise StopRetry("retry requested")

    worker.waiter = wait_twice
    worker._ensure_scope_is_only_worker = lambda: pytest.fail("stop proof must defer before teardown")
    try:
        with pytest.raises(StopRetry, match="retry requested"):
            worker._finalize_lane(0)
        assert waits == [1, 1]
        if case == "unobserved-event":
            assert worker._state()["state"] == "running"
        else:
            assert worker._state()["state"] == "stopping"
            assert worker._state()["retry_required"] is True
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_finalize_lane_records_invalid_request_sha_and_retries(tmp_path):
    class StopRetry(RuntimeError):
        pass

    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.request.update(sha=None, lane="r0-r1", project=".")
    worker._save_state(lane_mode="host", runner_name=None)
    worker._follow_lane_events = lambda **_kwargs: None
    worker._read_events = lambda **_kwargs: []
    worker._validate_run_gate_after_run = lambda: None
    worker.waiter = lambda _seconds: (_ for _ in ()).throw(StopRetry("invalid sha deferred"))
    try:
        with pytest.raises(StopRetry, match="invalid sha deferred"):
            worker._finalize_lane(0)
        assert "request sha is invalid" in worker._state()["reason"]
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize("failure", ["admission", "oserror"])
def test_finalize_lane_retries_after_terminal_proof_or_collection_failure(tmp_path, failure):
    class StopRetry(RuntimeError):
        pass

    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.request.update(sha=SHA, lane="r0-r1", project=".")
    worker._save_state(lane_mode="host", runner_name=None)
    worker._follow_lane_events = lambda **_kwargs: None
    worker._read_events = lambda **_kwargs: []
    worker._validate_run_gate_after_run = lambda: None
    worker._ensure_job_containers_stopped = lambda **_kwargs: None
    worker._bind_container_events = lambda *_args, **_kwargs: []
    if failure == "admission":
        worker._ensure_scope_is_only_worker = lambda: (_ for _ in ()).throw(
            AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "scope proof lost"),
        )
        worker._save_state = lambda **_updates: (_ for _ in ()).throw(OSError("state persistence unavailable"))
    else:
        worker._ensure_scope_is_only_worker = lambda: None
        worker.lane_error = "preexisting binding error"

        def fail_collect():
            worker._save_state = lambda **_updates: (_ for _ in ()).throw(
                OSError("state persistence unavailable"),
            )
            raise OSError("store read failed")

        worker._collect = fail_collect

    worker.waiter = lambda _seconds: (_ for _ in ()).throw(StopRetry("retry after failed state"))
    try:
        with pytest.raises(StopRetry, match="retry after failed state"):
            worker._finalize_lane(0)
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_publish_terminal_retries_when_state_save_fails_after_admission_refusal(tmp_path):
    class StopRetry(RuntimeError):
        pass

    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker._complete_admission = lambda _terminal: (_ for _ in ()).throw(
        AdmissionError(ExitCode.QUEUED_WITHDRAWN, "terminal write refused"),
    )
    worker._save_state = lambda **_updates: (_ for _ in ()).throw(OSError("state write failed"))
    worker.waiter = lambda _seconds: (_ for _ in ()).throw(StopRetry("terminal retry"))
    terminal = {
        "verdict": "ERROR", "exit_code": 3, "lane": "r0-r1", "api": "fleet-host-agent/1",
        "collected_at": "2026-11-27T00:00:00Z", "result_sha256": "a" * 64, "result_size": 12,
    }
    try:
        with pytest.raises(StopRetry, match="terminal retry"):
            worker._publish_terminal(terminal, final_state="failed", reason="test")
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


def test_finish_unstarted_resumes_pending_record_and_removes_checkout(tmp_path):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.request_digest = "d" * 64
    terminal = {
        "verdict": "NOT_RUN", "exit_code": int(ExitCode.PREREQUISITE_MISSING),
        "lane": "r0-r1", "api": "fleet-host-agent/1", "collected_at": "2026-11-27T00:00:00Z",
        "result_sha256": "a" * 64, "result_size": 12,
    }
    worker._persist_pending_terminal(terminal)
    worker._ensure_scope_is_only_worker = lambda: None
    worker._ensure_job_containers_stopped = lambda **_kwargs: None
    removed = []
    worker._remove_checkout = lambda: removed.append(worker.checkout_rel)
    published = []
    worker._publish_terminal = lambda value, **kwargs: published.append((dict(value), kwargs))
    try:
        assert worker._finish_unstarted(
            int(ExitCode.PREREQUISITE_MISSING), "missing optional image",
        ) == int(ExitCode.PREREQUISITE_MISSING)
        assert removed == [worker.checkout_rel]
        assert published[0][0] == terminal
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize("failure", ["admission", "oserror"])
def test_finish_unstarted_keeps_charge_when_teardown_needs_retry(tmp_path, failure):
    class StopRetry(RuntimeError):
        pass

    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker.request_digest = "d" * 64
    worker._ensure_scope_is_only_worker = lambda: None
    exc = (
        AdmissionError(ExitCode.QUEUED_WITHDRAWN, "container query unavailable")
        if failure == "admission" else OSError("checkout removal unavailable")
    )
    def fail_teardown(**_kwargs):
        worker._save_state = lambda **_updates: (_ for _ in ()).throw(OSError("state write unavailable"))
        raise exc

    worker._ensure_job_containers_stopped = fail_teardown
    published = []
    worker._publish_terminal = lambda *_args, **_kwargs: published.append("released")
    worker.waiter = lambda _seconds: (_ for _ in ()).throw(StopRetry("pre-lane retry"))
    try:
        with pytest.raises(StopRetry, match="pre-lane retry"):
            worker._finish_unstarted(int(ExitCode.REFUSAL), "pre-lane failure")
        assert published == []
        assert not (worker.job_dir / "result.json").exists()
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize("failure,expected", [
    (AdmissionError(ExitCode.REFUSAL, "request rejected"), int(ExitCode.REFUSAL)),
    (RuntimeError("unexpected worker error"), int(ExitCode.RUNNER_FAILURE)),
])
def test_worker_run_uses_closed_refusal_when_durable_state_cannot_be_read(tmp_path, failure, expected):
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path)
    worker._await_scope_confirmation = lambda: True
    worker._read_request = lambda: (_ for _ in ()).throw(failure)
    worker._state = lambda: (_ for _ in ()).throw(AdmissionError(ExitCode.QUEUED_WITHDRAWN, "state unreadable"))
    worker._finish_unstarted = lambda exit_code, _reason: int(exit_code)
    assert worker.run() == expected


def test_seed_extract_rejects_a_negative_member_size(tmp_path, monkeypatch):
    root = tmp_path / "seed-root"
    root.mkdir()
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    body = b"archive descriptor"
    input_fd = _pipe_with(body)

    class _Member:
        name = "store/value"
        size = -1

        def isdir(self):
            return False

        def isfile(self):
            return True

    class _Archive:
        def getmembers(self):
            return [_Member()]

        def close(self):
            pass

    monkeypatch.setattr(executor_worker_module.tarfile, "open", lambda **_kwargs: _Archive())
    try:
        with pytest.raises(ValueError, match="invalid-size"):
            _extract_seed(
                root_fd, input_fd, "store", hashlib.sha256(body).hexdigest(), len(body), 128,
            )
    finally:
        os.close(input_fd)
        os.close(root_fd)


def test_run_lane_continues_waiting_after_cancel_stop_refusal(tmp_path):
    class _Process:
        pid = 5202

        def __init__(self):
            self.statuses = [None, 0]

        def poll(self):
            return self.statuses.pop(0)

    process = _Process()

    class _Commands:
        def start(self, *_args, **_kwargs):
            worker._save_state(cancel_requested=True, cancel_reason="cancel requested")
            return process

    request = _submit()
    request.update(project=".", lane="r0-r1", repo="vbpub")
    worker, _runner, _job_dir, _checkout = _ready_worker(tmp_path, _Commands(), request=request)
    worker._helper = lambda *_args, **_kwargs: CommandResult(0)
    worker._environment = lambda: {"PATH": "/usr/bin"}
    worker._follow_lane_events = lambda **_kwargs: None
    stop_attempts = []

    def refuse_stop(_process):
        stop_attempts.append(1)
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "guard changed during cancel")

    worker._request_worker_stop = refuse_stop
    os.close(worker.project_fd)
    worker.project_fd = -1
    try:
        assert worker._run_lane("run-gate", "host", {"runner_name": None}, None) == 0
        assert stop_attempts == [1]
        assert worker._state()["lane_process_status"] == 0
    finally:
        for fd in (worker.project_fd, worker.checkout_fd, worker.repo_root_fd):
            if fd >= 0:
                os.close(fd)
