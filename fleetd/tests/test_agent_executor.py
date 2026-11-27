import json
import subprocess

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


def _executor(tmp_path, *, scope=None, user_resolver=None):
    policy, _ = _policy(tmp_path)
    state = tmp_path / "state"
    jobs = state / "jobs"
    jobs.mkdir(parents=True)
    jobs.chmod(0o700)
    return HostJobExecutor(
        state, policy, policy_path=tmp_path / "agent.toml",
        scope_launcher=scope or _Scope(),
        user_resolver=user_resolver or (lambda name: RunnerIdentity(name, 1001, 1001, (1001, 44))),
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
    assert argv[:4] == [executor.python_executable, "-m", "fleetd.executor", "worker"]
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
