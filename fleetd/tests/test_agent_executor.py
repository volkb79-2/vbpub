import hashlib
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
