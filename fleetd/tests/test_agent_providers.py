import hashlib
import json
import math
import os
import select
import selectors
import signal
import subprocess
import sys
import threading

import pytest

from fleetd import agent_entry, agent_providers
from fleetd.admission import AdmissionError, ExitCode, InspectorAnswer
from fleetd.agent_policy import load_agent_policy
from fleetd.agent_providers import (
    MAX_DOCKER_OUTPUT_BYTES,
    docker_container_identity_inspector,
    docker_object_inspector,
    legacy_job_ids,
)
from fleetd.contracts import ContractError
from test_agent_protocol import _policy

_CONTAINER_ID = "a" * 64
_DOCKER_ENV = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LC_ALL": "C"}
_DOCKER_HOST = "unix:///var/run/docker.sock"


class _BoundedRunnerInterruption(BaseException):
    pass


def _docker_record(container_id=_CONTAINER_ID, name="legacy-runner", labels=None):
    return {
        "Id": container_id,
        "Name": f"/{name}",
        "Config": {"Labels": labels},
        "State": {"Status": "running"},
    }


def _completed(argv, payload, *, returncode=0, stderr=""):
    stdout = payload if isinstance(payload, bytes) else (
        payload.encode("utf-8") if isinstance(payload, str) else json.dumps(payload).encode("utf-8")
    )
    error = stderr if isinstance(stderr, bytes) else stderr.encode("utf-8")
    return subprocess.CompletedProcess(argv, returncode, stdout, error)


def _legacy_id(name):
    return "legacy-" + hashlib.sha256(os.fsencode(name)).hexdigest()[:40]


def test_legacy_job_ids_charge_every_entry_except_a_regular_exit_marker(tmp_path, monkeypatch):
    root = tmp_path / "jobs"
    root.mkdir()
    (root / "active odd name").mkdir()
    finished = root / "finished"
    finished.mkdir()
    (finished / "EXIT").write_text("0\n", encoding="utf-8")
    marker_directory = root / "directory-marker"
    marker_directory.mkdir()
    (marker_directory / "EXIT").mkdir()
    marker_link = root / "symlink-marker"
    marker_link.mkdir()
    outside_marker = tmp_path / "outside-marker"
    outside_marker.write_text("0\n", encoding="utf-8")
    (marker_link / "EXIT").symlink_to(outside_marker)
    fifo_marker = root / "fifo-marker"
    fifo_marker.mkdir()
    os.mkfifo(fifo_marker / "EXIT")
    linked_entry = root / "entry-link"
    linked_entry.symlink_to(finished, target_is_directory=True)
    (root / "plain-file").write_text("unexpected", encoding="utf-8")
    (root / "unreadable").mkdir()

    original_open_beneath = agent_providers.open_beneath

    def open_beneath(root_fd, relative, *, directory=False):
        if relative == "unreadable":
            raise PermissionError("simulated unreadable entry")
        return original_open_beneath(root_fd, relative, directory=directory)

    monkeypatch.setattr(agent_providers, "open_beneath", open_beneath)
    names = sorted(path.name for path in root.iterdir())
    expected = tuple(_legacy_id(name) for name in names if name != "finished")

    assert legacy_job_ids(root) == expected
    assert legacy_job_ids(root) == expected
    assert len(set(expected)) == len(expected)
    assert all(name.startswith("legacy-") and len(name) == 47 for name in expected)


def test_exit_marker_classifies_device_like_descriptor_without_reading_it(tmp_path):
    root = tmp_path / "jobs"
    entry = root / "device-marker"
    entry.mkdir(parents=True)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    flags_seen = []

    def marker_opener(path, flags, *, dir_fd):
        flags_seen.append((path, flags, dir_fd))
        return os.open("/dev/null", os.O_PATH | os.O_CLOEXEC)

    try:
        assert agent_providers._has_regular_exit_marker(
            root_fd, "device-marker", marker_opener=marker_opener,
        ) is False
    finally:
        os.close(root_fd)
    assert flags_seen[0][0] == "EXIT"
    assert flags_seen[0][1] & os.O_PATH
    assert flags_seen[0][1] & os.O_NOFOLLOW


def test_legacy_job_ids_reject_relative_unresolved_and_parent_link_paths(tmp_path):
    with pytest.raises(ValueError, match="must be absolute"):
        legacy_job_ids("relative/jobs")

    root = tmp_path / "jobs"
    root.mkdir()
    with pytest.raises(ValueError, match="fully resolved"):
        legacy_job_ids(str(tmp_path / "unused" / ".." / "jobs"))

    linked_parent = tmp_path / "parent-link"
    linked_parent.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(OSError):
        legacy_job_ids(linked_parent / "jobs")

    with pytest.raises(OSError):
        legacy_job_ids(tmp_path / "missing" / "jobs")

    with pytest.raises(ValueError, match="fully resolved"):
        legacy_job_ids("/")


def test_legacy_job_ids_refuse_listing_errors_and_non_text_names(tmp_path, monkeypatch):
    root = tmp_path / "jobs"
    root.mkdir()
    monkeypatch.setattr(
        agent_providers.os, "scandir",
        lambda _fd: (_ for _ in ()).throw(PermissionError("simulated listing failure")),
    )
    with pytest.raises(PermissionError, match="listing failure"):
        legacy_job_ids(root)

    with pytest.raises(OSError, match="non-text entry"):
        legacy_job_ids(root, directory_scanner=lambda _fd: [b"not-text"])

    class BytesEntry:
        name = b"not-text"

    class Entries:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def __iter__(self):
            return iter([BytesEntry()])

    monkeypatch.setattr(agent_providers.os, "scandir", lambda _fd: Entries())
    with pytest.raises(OSError, match="non-text entry"):
        legacy_job_ids(root)

    scans = 0

    def non_text_second_pass(_fd):
        nonlocal scans
        scans += 1
        return ["first"] if scans == 1 else [b"second"]

    with pytest.raises(OSError, match="non-text entry"):
        legacy_job_ids(
            root,
            directory_scanner=non_text_second_pass,
            marker_checker=lambda _fd, _name: False,
        )


def test_legacy_job_ids_charge_unexpected_names_and_refuse_duplicate_ids(tmp_path, monkeypatch):
    root = tmp_path / "jobs"
    root.mkdir()
    odd_names = ["../odd-name", "pending"]
    ids = legacy_job_ids(
        root,
        directory_scanner=lambda _fd: odd_names,
        marker_checker=lambda _fd, _name: False,
    )
    assert ids == tuple(_legacy_id(name) for name in odd_names)

    monkeypatch.setattr(agent_providers, "_legacy_job_id", lambda _name: "legacy-collision")
    with pytest.raises(OSError, match="duplicate admission id"):
        legacy_job_ids(
            root,
            directory_scanner=lambda _fd: ["one", "two"],
            marker_checker=lambda _fd, _name: False,
        )


def test_legacy_job_ids_refuse_more_than_the_named_entry_limit(tmp_path, monkeypatch):
    root = tmp_path / "jobs"
    root.mkdir()
    (root / "one").touch()
    (root / "two").touch()
    monkeypatch.setattr(agent_providers, "MAX_LEGACY_JOB_ENTRIES", 1)
    with pytest.raises(OSError, match=r"MAX_LEGACY_JOB_ENTRIES \(1\)"):
        legacy_job_ids(root)


def test_legacy_job_ids_second_pass_keeps_vanished_live_entry_and_charges_new_entry(tmp_path):
    root = tmp_path / "jobs"
    root.mkdir()
    (root / "moving").mkdir()
    renamed = False

    def marker(root_fd, name):
        nonlocal renamed
        if name == "moving" and not renamed:
            (root / "moving").rename(root / "renamed")
            finished = root / "finished-late"
            finished.mkdir()
            (finished / "EXIT").write_text("0\n", encoding="ascii")
            renamed = True
            return False
        return agent_providers._has_regular_exit_marker(root_fd, name)

    assert legacy_job_ids(root, marker_checker=marker) == (
        _legacy_id("moving"),
        _legacy_id("renamed"),
    )


def test_legacy_job_ids_refuse_a_replaced_root_through_the_reopen_seam(tmp_path):
    first = tmp_path / "jobs-first"
    second = tmp_path / "jobs-second"
    first.mkdir()
    second.mkdir()
    opened = 0

    def root_opener(_path):
        nonlocal opened
        opened += 1
        path = first if opened == 1 else second
        return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)

    with pytest.raises(OSError, match="changed during admission scan"):
        legacy_job_ids(
            "/configured/jobs",
            root_opener=root_opener,
            directory_scanner=lambda _fd: [],
        )
    assert opened == 2


def test_legacy_job_ids_are_stable_when_other_entries_are_inserted_or_removed(tmp_path):
    root = tmp_path / "jobs"
    root.mkdir()
    (root / "stable").mkdir()
    original = legacy_job_ids(root)
    (root / "inserted").mkdir()
    after_insert = legacy_job_ids(root)
    (root / "inserted").rmdir()
    after_remove = legacy_job_ids(root)
    assert original == (_legacy_id("stable"),)
    assert _legacy_id("stable") in after_insert
    assert after_remove == original


def test_docker_object_inspector_uses_fixed_argv_environment_and_exact_targets():
    record = _docker_record()
    calls = []

    def runner(argv, **options):
        calls.append((argv, options))
        return _completed(argv, [record])

    inspect = docker_object_inspector(2.5, runner=runner)
    assert inspect(_CONTAINER_ID) is InspectorAnswer.PRESENT
    assert inspect("legacy-runner") is InspectorAnswer.PRESENT
    assert inspect(_CONTAINER_ID[:12]) is InspectorAnswer.ERROR
    assert inspect("a" * 63) is InspectorAnswer.ERROR
    assert inspect("--help") is InspectorAnswer.ERROR
    assert inspect(None) is InspectorAnswer.ERROR
    assert len(calls) == 2
    assert [call[0] for call in calls] == [
        ["/usr/bin/docker", "--host", _DOCKER_HOST, "inspect", "--type=container", _CONTAINER_ID],
        ["/usr/bin/docker", "--host", _DOCKER_HOST, "inspect", "--type=container", "legacy-runner"],
    ]
    for _argv, options in calls:
        assert options == {
            "check": False,
            "capture_output": True,
            "text": False,
            "timeout": 2.5,
            "shell": False,
            "env": _DOCKER_ENV,
            "cwd": "/",
            "start_new_session": True,
        }


def test_docker_object_inspector_returns_no_such_only_for_explicit_empty_errors():
    def missing_container(argv, **_options):
        return _completed(argv, b"", returncode=1, stderr="error response from daemon: no such container: absent")

    def missing_object(argv, **_options):
        return _completed(argv, b"", returncode=1, stderr="NO SUCH OBJECT: absent")

    def other_failure(argv, **_options):
        return _completed(argv, b"", returncode=1, stderr="permission denied")

    def wrong_target(argv, **_options):
        return _completed(argv, b"", returncode=1, stderr="No such object: another")

    def partial_output(argv, **_options):
        return _completed(argv, b"{partial", returncode=1, stderr="No such object: absent")

    assert docker_object_inspector(1, runner=missing_container)("absent") is InspectorAnswer.NO_SUCH_OBJECT
    assert docker_object_inspector(1, runner=missing_object)("absent") is InspectorAnswer.NO_SUCH_OBJECT
    assert docker_object_inspector(1, runner=other_failure)("absent") is InspectorAnswer.ERROR
    assert docker_object_inspector(1, runner=wrong_target)("absent") is InspectorAnswer.ERROR
    assert docker_object_inspector(1, runner=partial_output)("absent") is InspectorAnswer.ERROR


@pytest.mark.parametrize(
    "runner",
    [
        lambda _argv, **_options: (_ for _ in ()).throw(FileNotFoundError("docker missing")),
        lambda _argv, **_options: (_ for _ in ()).throw(subprocess.TimeoutExpired("docker", 1)),
        lambda _argv, **_options: (_ for _ in ()).throw(RuntimeError("runner failed")),
        lambda _argv, **_options: object(),
        lambda argv, **_options: subprocess.CompletedProcess(argv, "0", b"[]", b""),
        lambda argv, **_options: subprocess.CompletedProcess(argv, 0, "[]", b""),
        lambda argv, **_options: subprocess.CompletedProcess(argv, 0, b"[]", ""),
        lambda argv, **_options: subprocess.CompletedProcess(argv, 0, b"\xff", b""),
        lambda argv, **_options: subprocess.CompletedProcess(argv, 0, b"[]", b"\xff"),
        lambda argv, **_options: _completed(argv, "{}", stderr="unexpected warning"),
        lambda argv, **_options: _completed(argv, "{partial"),
        lambda argv, **_options: _completed(argv, []),
        lambda argv, **_options: _completed(argv, [_docker_record(), _docker_record()]),
        lambda argv, **_options: _completed(argv, True),
        lambda argv, **_options: _completed(argv, [None]),
        lambda argv, **_options: _completed(argv, [_docker_record(name="different")]),
        lambda argv, **_options: _completed(argv, [_docker_record(container_id="short")]),
        lambda argv, **_options: _completed(argv, [_docker_record(container_id=17)]),
        lambda argv, **_options: _completed(argv, [{**_docker_record(), "Name": None}]),
        lambda argv, **_options: _completed(argv, [_docker_record(name="bad/name")]),
        lambda argv, **_options: _completed(
            argv, [{key: value for key, value in _docker_record().items() if key != "Config"}],
        ),
        lambda argv, **_options: _completed(argv, [{**_docker_record(), "Config": {"Labels": []}}]),
        lambda argv, **_options: _completed(
            argv, [{**_docker_record(), "Config": {"Labels": {"bad": 1}}}],
        ),
    ],
    ids=[
        "missing-binary", "timeout", "runner-exception", "invalid-runner-result", "bad-return-code",
        "text-stdout", "text-stderr", "invalid-utf8-stdout", "invalid-utf8-stderr", "success-stderr",
        "partial-json", "empty-array", "multiple-objects", "non-object-json", "non-object-array-entry",
        "mismatched-name", "invalid-id", "non-text-id", "missing-name", "invalid-name", "missing-config",
        "invalid-label-shape", "invalid-label-value",
    ],
)
def test_docker_object_inspector_fails_closed_for_incomplete_or_invalid_results(runner):
    assert docker_object_inspector(1, runner=runner)("legacy-runner") is InspectorAnswer.ERROR


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_docker_object_inspector_refuses_output_above_the_named_cap(monkeypatch, stream):
    monkeypatch.setattr(agent_providers, "MAX_DOCKER_OUTPUT_BYTES", 8)
    inspect = docker_object_inspector(
        1,
        runner=lambda argv, **_options: _completed(
            argv,
            b"123456789" if stream == "stdout" else b"",
            stderr=b"123456789" if stream == "stderr" else b"",
        ),
    )
    assert inspect("legacy-runner") is InspectorAnswer.ERROR


@pytest.mark.parametrize("timeout", [True, "1", 0, -1, math.inf, math.nan, 10**400])
def test_docker_inspector_requires_a_finite_positive_timeout(timeout):
    with pytest.raises(ValueError, match="finite positive number"):
        docker_object_inspector(timeout)


def test_host_docker_adapter_uses_bounded_collector_without_runner(monkeypatch):
    observed = {}

    def bounded(argv, timeout):
        observed.update(argv=list(argv), timeout=timeout)
        return subprocess.CompletedProcess(argv, 0, b"ok", b"")

    monkeypatch.setattr(agent_providers, "_run_bounded", bounded)
    result = agent_providers.run_docker_command(["info"], 2.5)
    assert result.stdout == b"ok"
    assert observed == {
        "argv": ["/usr/bin/docker", "--host", _DOCKER_HOST, "info"],
        "timeout": 2.5,
    }


def test_host_docker_adapter_refuses_invalid_arguments():
    for arguments in ([""], ["inspect", "bad\x00target"], [1]):
        with pytest.raises(agent_providers.DockerInspectionError, match="arguments are invalid"):
            agent_providers.run_docker_command(arguments, 1, runner=lambda *_args, **_kwargs: None)


def test_bounded_command_wrapper_forwards_the_injected_collector(monkeypatch):
    observed = {}
    result = subprocess.CompletedProcess(["query"], 0, b"answer", b"")

    def collector(argv, timeout, **kwargs):
        observed.update(argv=list(argv), timeout=timeout, **kwargs)
        return result

    monkeypatch.setattr(agent_providers, "_run_bounded", collector)
    assert agent_providers.run_bounded_command(
        ["query"], 3, environment={"PATH": "/usr/bin"}, cwd="/tmp", stdin=None,
        pass_fds=(17,), output_limit=123,
    ) is result
    assert observed == {
        "argv": ["query"], "timeout": 3, "environment": {"PATH": "/usr/bin"},
        "cwd": "/tmp", "stdin": None, "pass_fds": (17,), "output_limit": 123,
        "popen_factory": None, "preexec_fn": None,
    }


@pytest.mark.parametrize("result", [
    subprocess.CompletedProcess(["docker"], "0", b"[]", b""),
    subprocess.CompletedProcess(["docker"], 0, "[]", b""),
    subprocess.CompletedProcess(["docker"], 0, b"[]", ""),
])
def test_docker_inspector_checks_the_completed_result_boundary(monkeypatch, result):
    monkeypatch.setattr(agent_providers, "run_docker_command", lambda *_args, **_kwargs: result)
    inspect = docker_object_inspector(1)
    assert inspect("legacy-runner") is InspectorAnswer.ERROR


def test_docker_inspector_handles_completed_result_property_failure(monkeypatch):
    class BrokenResult:
        @property
        def returncode(self):
            raise RuntimeError("result property unavailable")

    monkeypatch.setattr(agent_providers, "run_docker_command", lambda *_args, **_kwargs: BrokenResult())
    inspect = docker_object_inspector(1)
    assert inspect("legacy-runner") is InspectorAnswer.ERROR


def test_docker_inspector_rejects_an_invalid_executable_without_running_it():
    for executable in ("-docker", "", None, "docker"):
        calls = []
        inspect = docker_object_inspector(
            1, runner=lambda *args, **kwargs: calls.append((args, kwargs)), docker_binary=executable,
        )
        assert inspect("legacy-runner") is InspectorAnswer.ERROR
        assert calls == []


def test_docker_object_inspector_accepts_a_valid_unprefixed_docker_name():
    record = _docker_record()
    record["Name"] = "legacy-runner"
    inspect = docker_object_inspector(
        1, runner=lambda argv, **_options: _completed(argv, record),
    )
    assert inspect("legacy-runner") is InspectorAnswer.PRESENT


def test_container_identity_inspector_requires_exact_full_id_and_returns_bind_metadata():
    record = _docker_record(labels={"fleetd.lease": "controller-lease"})
    calls = []

    def runner(argv, **options):
        calls.append((argv, options))
        return _completed(argv, record)

    inspect = docker_container_identity_inspector(3, runner=runner)
    assert inspect(_CONTAINER_ID) == {
        "id": _CONTAINER_ID,
        "name": "/legacy-runner",
        "labels": {"fleetd.lease": "controller-lease"},
    }
    assert calls[0][0] == ["/usr/bin/docker", "--host", _DOCKER_HOST, "inspect", "--type=container", _CONTAINER_ID]
    assert calls[0][1]["timeout"] == 3

    missing = docker_container_identity_inspector(
        3,
        runner=lambda argv, **_options: _completed(
            argv, b"", returncode=1, stderr=f"Error: No such object: {_CONTAINER_ID}"
        ),
    )
    assert missing(_CONTAINER_ID) is None

    invalid_calls = []
    invalid = docker_container_identity_inspector(
        3,
        runner=lambda *args, **kwargs: invalid_calls.append((args, kwargs)),
    )
    with pytest.raises(agent_providers.DockerInspectionError, match="full lowercase Docker ID"):
        invalid(_CONTAINER_ID[:12])
    with pytest.raises(agent_providers.DockerInspectionError, match="target must be text"):
        invalid(None)
    assert invalid_calls == []


def test_container_identity_inspector_errors_on_mismatched_id_bad_labels_and_runner_failure():
    wrong = _docker_record(container_id="b" * 64)
    inspect_wrong = docker_container_identity_inspector(
        1, runner=lambda argv, **_options: _completed(argv, [wrong]),
    )
    with pytest.raises(agent_providers.DockerInspectionError, match="different container ID"):
        inspect_wrong(_CONTAINER_ID)

    no_labels = docker_container_identity_inspector(
        1, runner=lambda argv, **_options: _completed(argv, _docker_record(labels=None)),
    )
    assert no_labels(_CONTAINER_ID)["labels"] == {}

    failed = docker_container_identity_inspector(
        1, runner=lambda _argv, **_options: (_ for _ in ()).throw(FileNotFoundError("docker missing")),
    )
    with pytest.raises(agent_providers.DockerInspectionError, match="identity inspection failed"):
        failed(_CONTAINER_ID)


class _FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class _FakeStream:
    def __init__(self, fd):
        self.fd = fd
        self.closed = False

    def fileno(self):
        return self.fd

    def close(self):
        self.closed = True


class _FakeSelectorKey:
    def __init__(self, stream, data):
        self.fileobj = stream
        self.data = data


class _FakeProcess:
    pid = 99123

    def __init__(self, *, wait_error=None, wait_action=None):
        self.stdout = _FakeStream(101)
        self.stderr = _FakeStream(102)
        self.wait_error = wait_error
        self.wait_action = wait_action
        self.wait_timeouts = []
        self.events = []

    def wait(self, *, timeout):
        assert self.events == ["signal"], "reap began before one group signal"
        self.events.append("reap")
        self.wait_timeouts.append(timeout)
        if self.wait_action is not None:
            self.wait_action()
        if self.wait_error is not None:
            raise self.wait_error
        return 0


class _FakeSelector:
    def __init__(self, *, clock=None, elapse=False):
        self.entries = {}
        self.clock = clock
        self.elapse = elapse
        self.closed = False

    def register(self, stream, _events, data):
        fd = stream if isinstance(stream, int) else stream.fileno()
        self.entries[fd] = (stream, data)

    def get_map(self):
        return dict(self.entries)

    def select(self, timeout):
        if self.elapse:
            self.clock.now += timeout
            return []
        return [
            (_FakeSelectorKey(stream, data), agent_providers.selectors.EVENT_READ)
            for stream, data in self.entries.values()
        ]

    def unregister(self, stream):
        fd = stream if isinstance(stream, int) else stream.fileno()
        del self.entries[fd]

    def close(self):
        self.closed = True


def _bounded_seams(monkeypatch, *, read_map, clock=None, selector=None, process=None, spawn=None, kill=None):
    active_clock = _FakeClock() if clock is None else clock
    active_selector = _FakeSelector() if selector is None else selector
    active_process = _FakeProcess() if process is None else process
    killed = [] if kill is None else kill

    def read(fd, _amount):
        values = read_map.get(fd, [])
        return values.pop(0) if values else b""

    monkeypatch.setattr(agent_providers.os, "read", read)
    monkeypatch.setattr(agent_providers.os, "set_blocking", lambda _fd, _value: None)
    monkeypatch.setattr(
        agent_providers.os,
        "pidfd_open",
        lambda _pid: os.open(os.devnull, os.O_RDONLY | os.O_CLOEXEC),
    )
    def process_factory(*_argv, **_options):
        if spawn is not None:
            spawn()
        return active_process

    def kill_group(pid, sig):
        active_process.events.append("signal")
        killed.append((pid, sig))

    return active_clock, active_selector, active_process, killed, process_factory, kill_group


def _track_successful_bounded_closes(monkeypatch, selector, process, *, failures=None):
    pidfd = 303
    close_order = []
    active_failures = {} if failures is None else failures

    def close_resource(name):
        close_order.append(name)
        if name in active_failures:
            raise active_failures[name]

    monkeypatch.setattr(agent_providers.os, "pidfd_open", lambda _pid: pidfd)
    monkeypatch.setattr(agent_providers.os, "close", lambda _fd: close_resource("pidfd"))
    selector.close = lambda: close_resource("selector")
    process.stdout.close = lambda: close_resource("stdout")
    process.stderr.close = lambda: close_resource("stderr")
    return close_order


def test_bounded_docker_runner_drains_both_streams_and_reaps(monkeypatch):
    streams = {101: [b"out", b""], 102: [b"err", b""]}
    clock, selector, process, killed, spawn, kill_group = _bounded_seams(monkeypatch, read_map=streams)
    result = agent_providers._run_bounded(
        ["/usr/bin/docker"], 5,
        popen_factory=spawn,
        selector_factory=lambda: selector,
        monotonic=clock,
        kill_group=kill_group,
    )
    assert result.stdout == b"out"
    assert result.stderr == b"err"
    assert result.returncode == 0
    assert process.wait_timeouts == [5]
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.events == ["signal", "reap"]


@pytest.mark.parametrize("overflow_fd", [101, 102], ids=["stdout", "stderr"])
def test_bounded_docker_runner_kills_on_output_cap_and_reaps_with_deadline(monkeypatch, overflow_fd):
    streams = {101: [], 102: []}
    streams[overflow_fd] = [b"four"]
    clock, selector, process, killed, spawn, kill_group = _bounded_seams(monkeypatch, read_map=streams)
    with pytest.raises(agent_providers.DockerInspectionError, match="output exceeded"):
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 5,
            popen_factory=spawn,
            selector_factory=lambda: selector,
            monotonic=clock,
            kill_group=kill_group,
            output_limit=3,
        )
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.wait_timeouts == [5]
    assert process.events == ["signal", "reap"]


def test_bounded_docker_runner_times_out_while_output_pipes_remain_open(monkeypatch):
    clock = _FakeClock()
    selector = _FakeSelector(clock=clock, elapse=True)
    process = _FakeProcess()
    clock, selector, process, killed, spawn, kill_group = _bounded_seams(
        monkeypatch, read_map={}, clock=clock, selector=selector, process=process,
    )
    with pytest.raises(agent_providers.DockerInspectionError, match="query_timeout_s"):
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 2,
            popen_factory=spawn,
            selector_factory=lambda: selector,
            monotonic=clock,
            kill_group=kill_group,
        )
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.wait_timeouts == [0]
    assert process.events == ["signal", "reap"]


def test_bounded_docker_runner_covers_spawn_timeout_reap_timeout_and_kill_errors(monkeypatch):
    clock = _FakeClock()
    process = _FakeProcess()

    def slow_spawn():
        clock.now = 2

    clock, _selector, process, killed, spawn, kill_group = _bounded_seams(
        monkeypatch, read_map={}, clock=clock, process=process, spawn=slow_spawn,
    )
    with pytest.raises(agent_providers.DockerInspectionError, match="during spawn"):
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            monotonic=clock,
            kill_group=kill_group,
        )
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.wait_timeouts == [0]
    assert process.events == ["signal", "reap"]

    clock = _FakeClock()
    selector = _FakeSelector()
    process = _FakeProcess(wait_error=subprocess.TimeoutExpired("docker", 1))
    streams = {101: [b""], 102: [b""]}
    clock, selector, process, killed, spawn, kill_group = _bounded_seams(
        monkeypatch, read_map=streams, clock=clock, selector=selector, process=process,
    )
    clock.now = 0
    with pytest.raises(agent_providers.DockerInspectionError, match="query_timeout_s"):
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            selector_factory=lambda: selector,
            monotonic=clock,
            kill_group=kill_group,
        )
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.wait_timeouts == [1]
    assert process.events == ["signal", "reap"]


def test_bounded_docker_runner_kills_child_immediately_after_late_creation(monkeypatch):
    clock = _FakeClock()
    process = _FakeProcess()
    spawn_entered = threading.Event()
    release_spawn = threading.Event()
    events = []

    def delayed_creation():
        events.append("creation-started")
        spawn_entered.set()
        release_spawn.wait()
        events.append("creation-returned")

    clock, _selector, process, killed, spawn, _kill = _bounded_seams(
        monkeypatch, read_map={}, clock=clock, process=process, spawn=delayed_creation,
    )

    def kill_group(pid, sig):
        events.append("group-killed")
        process.events.append("signal")
        killed.append((pid, sig))

    outcome = []

    def inspect():
        try:
            agent_providers._run_bounded(
                ["/usr/bin/docker"], 1,
                popen_factory=spawn,
                monotonic=clock,
                kill_group=kill_group,
            )
        except Exception as exc:
            outcome.append(exc)

    worker = threading.Thread(target=inspect)
    worker.start()
    spawn_entered.wait()
    clock.now = 2
    events.append("admission-refused")
    release_spawn.set()
    worker.join()

    assert len(outcome) == 1
    assert isinstance(outcome[0], agent_providers.DockerInspectionError)
    assert events == ["creation-started", "admission-refused", "creation-returned", "group-killed"]
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.wait_timeouts == [0]
    assert process.events == ["signal", "reap"]


def test_bounded_docker_runner_signals_before_a_late_successful_reap(monkeypatch):
    clock = _FakeClock()
    selector = _FakeSelector()
    process = _FakeProcess(wait_action=lambda: setattr(clock, "now", 1))
    streams = {101: [b""], 102: [b""]}
    clock, selector, process, killed, spawn, kill_group = _bounded_seams(
        monkeypatch, read_map=streams, clock=clock, selector=selector, process=process,
    )
    with pytest.raises(agent_providers.DockerInspectionError, match="while reaping"):
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            selector_factory=lambda: selector,
            monotonic=clock,
            kill_group=kill_group,
        )
    assert process.wait_timeouts == [1]
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.events == ["signal", "reap"]


def test_bounded_docker_runner_signals_late_same_group_descendant_before_reap(monkeypatch):
    clock = _FakeClock()
    selector = _FakeSelector()
    process = _FakeProcess()
    process.descendant_alive = True
    events = []
    streams = {101: [b""], 102: [b""]}
    _, selector, process, killed, spawn, _kill = _bounded_seams(
        monkeypatch, read_map=streams, clock=clock, selector=selector, process=process,
    )
    pidfd, pidfd_writer = os.pipe()
    monkeypatch.setattr(agent_providers.os, "pidfd_open", lambda _pid: pidfd)
    real_close = os.close
    pidfd_closes = []

    def close_and_record(fd):
        if fd == pidfd:
            pidfd_closes.append(fd)
        return real_close(fd)

    monkeypatch.setattr(agent_providers.os, "close", close_and_record)

    original_select = selector.select

    def observe_pidfd(timeout):
        if any(isinstance(stream, int) for stream, _data in selector.entries.values()):
            assert timeout == 1
            events.append("exit-observed")
            clock.now = 1
            return [(_FakeSelectorKey(pidfd, ("pidfd", None)), agent_providers.selectors.EVENT_READ)]
        return original_select(timeout)

    selector.select = observe_pidfd

    def kill_group(pid, sig):
        events.append("group-signal")
        process.events.append("signal")
        killed.append((pid, sig))
        process.descendant_alive = False

    def reap(*, timeout):
        events.append("reap")
        process.events.append("reap")
        process.wait_timeouts.append(timeout)
        clock.now = 1
        assert not process.descendant_alive
        return 0

    process.wait = reap
    try:
        with pytest.raises(agent_providers.DockerInspectionError, match="query_timeout_s"):
            agent_providers._run_bounded(
                ["/usr/bin/docker"], 1,
                popen_factory=spawn,
                selector_factory=lambda: selector,
                monotonic=clock,
                kill_group=kill_group,
            )
    finally:
        for fd in (pidfd, pidfd_writer):
            try:
                real_close(fd)
            except OSError:
                pass

    assert events == ["exit-observed", "group-signal", "reap"]
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.wait_timeouts == [0]
    assert process.events == ["signal", "reap"]
    assert pidfd_closes == [pidfd]


def test_bounded_docker_runner_kills_at_deadline_before_reap(monkeypatch):
    clock = _FakeClock()
    selector = _FakeSelector()
    process = _FakeProcess()
    streams = {101: [b""], 102: [b""]}
    _, selector, process, killed, spawn, kill_group = _bounded_seams(
        monkeypatch, read_map=streams, clock=clock, selector=selector, process=process,
    )
    original_read = agent_providers.os.read

    def deadline_read(fd, amount):
        result = original_read(fd, amount)
        if fd == 102:
            clock.now = 1
        return result

    monkeypatch.setattr(agent_providers.os, "read", deadline_read)
    with pytest.raises(agent_providers.DockerInspectionError, match="query_timeout_s"):
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            selector_factory=lambda: selector,
            monotonic=clock,
            kill_group=kill_group,
        )
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.wait_timeouts == [0]
    assert process.events == ["signal", "reap"]


def test_bounded_docker_runner_handles_reader_and_group_kill_failures(monkeypatch):
    process = _FakeProcess()
    selector = _FakeSelector()
    clock = _FakeClock()
    _, _, process, _killed, spawn, _kill = _bounded_seams(
        monkeypatch, read_map={101: [b"data"], 102: []}, clock=clock, selector=selector, process=process,
    )
    monkeypatch.setattr(
        agent_providers.os, "read",
        lambda _fd, _amount: (_ for _ in ()).throw(OSError("pipe read failed")),
    )
    kills = []

    def record_kill(pid, sig):
        process.events.append("signal")
        kills.append((pid, sig))

    with pytest.raises(agent_providers.DockerInspectionError, match="output pipe failed"):
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            selector_factory=lambda: selector,
            monotonic=clock,
            kill_group=record_kill,
        )
    assert kills == [(process.pid, signal.SIGKILL)]
    assert process.events == ["signal", "reap"]

    process.events.clear()

    def denied_kill(_pid, _sig):
        process.events.append("signal")
        raise PermissionError("denied")

    with pytest.raises(agent_providers.DockerInspectionError, match="could not kill"):
        agent_providers._kill_process_group_and_reap(
            process,
            1,
            lambda: 0,
            denied_kill,
        )
    assert process.events == ["signal", "reap"]


def test_bounded_docker_runner_does_not_retry_a_raising_termination_helper(monkeypatch):
    process = _FakeProcess()
    selector = _FakeSelector()
    clock = _FakeClock()
    _, _, process, _killed, spawn, _kill = _bounded_seams(
        monkeypatch, read_map={101: [b"data"], 102: []}, clock=clock,
        selector=selector, process=process,
    )
    monkeypatch.setattr(
        agent_providers.os, "read",
        lambda _fd, _amount: (_ for _ in ()).throw(OSError("pipe read failed")),
    )
    calls = []

    def failed_kill(pid, sig):
        process.events.append("signal")
        calls.append((pid, sig))
        raise PermissionError("group denied")

    with pytest.raises(agent_providers.DockerInspectionError, match="could not kill"):
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            selector_factory=lambda: selector,
            monotonic=clock,
            kill_group=failed_kill,
        )
    assert calls == [(process.pid, signal.SIGKILL)]
    assert process.wait_timeouts == [1]
    assert process.events == ["signal", "reap"]


def test_bounded_docker_runner_wait_interruption_overrides_earlier_signal_error(monkeypatch):
    wait_interruption = _BoundedRunnerInterruption("wait interrupted after signal error")
    process = _FakeProcess(wait_error=wait_interruption)
    clock = _FakeClock()
    selector = _FakeSelector()
    streams = {101: [b""], 102: [b""]}
    _, selector, process, killed, spawn, _kill = _bounded_seams(
        monkeypatch, read_map=streams, clock=clock, selector=selector, process=process,
    )
    close_order = _track_successful_bounded_closes(monkeypatch, selector, process)

    def signal_error(pid, sig):
        process.events.append("signal")
        killed.append((pid, sig))
        raise PermissionError("ordinary signal failure")

    with pytest.raises(_BoundedRunnerInterruption, match="wait interrupted after signal error"):
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            selector_factory=lambda: selector,
            monotonic=clock,
            kill_group=signal_error,
        )
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.events == ["signal", "reap"]
    assert close_order == ["pidfd", "selector", "stdout", "stderr"]


def test_bounded_docker_runner_close_interruption_overrides_earlier_body_error(monkeypatch):
    process = _FakeProcess()
    clock = _FakeClock()
    _, _selector, process, killed, spawn, kill_group = _bounded_seams(
        monkeypatch, read_map={}, clock=clock, process=process,
    )
    closed = []

    def close_stdout():
        closed.append("stdout")

    def close_stderr():
        closed.append("stderr")
        raise _BoundedRunnerInterruption("stderr close interrupted after body error")

    process.stdout.close = close_stdout
    process.stderr.close = close_stderr

    def broken_selector():
        raise RuntimeError("ordinary selector setup failure")

    with pytest.raises(_BoundedRunnerInterruption, match="stderr close interrupted after body error"):
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            selector_factory=broken_selector,
            monotonic=clock,
            kill_group=kill_group,
        )
    assert closed == ["stdout", "stderr"]
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.events == ["signal", "reap"]


def test_bounded_docker_runner_group_signal_interruption_overrides_body_error(monkeypatch):
    process = _FakeProcess()
    clock = _FakeClock()
    _, _selector, process, killed, spawn, _kill = _bounded_seams(
        monkeypatch, read_map={}, clock=clock, process=process,
    )
    closed = []
    process.stdout.close = lambda: closed.append("stdout")
    process.stderr.close = lambda: closed.append("stderr")

    def body_error():
        raise RuntimeError("ordinary selector setup failure")

    signal_interruption = _BoundedRunnerInterruption("group signal interrupted")

    def group_signal(pid, sig):
        process.events.append("signal")
        killed.append((pid, sig))
        raise signal_interruption

    with pytest.raises(_BoundedRunnerInterruption, match="group signal interrupted") as caught:
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            selector_factory=body_error,
            monotonic=clock,
            kill_group=group_signal,
        )
    assert caught.value is signal_interruption
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.events == ["signal", "reap"]
    assert closed == ["stdout", "stderr"]


def test_bounded_docker_runner_records_interruption_from_termination_helper(monkeypatch):
    process = _FakeProcess()
    clock = _FakeClock()
    _, _selector, process, killed, spawn, kill_group = _bounded_seams(
        monkeypatch, read_map={}, clock=clock, process=process,
    )
    closed = []
    process.stdout.close = lambda: closed.append("stdout")
    process.stderr.close = lambda: closed.append("stderr")
    cleanup_interruption = _BoundedRunnerInterruption("termination helper interrupted")

    def ordinary_body_error():
        raise RuntimeError("ordinary selector setup failure")

    def cleanup_then_interrupt(child, deadline, active_clock, signal_group, *, failure_records):
        signal_group(child.pid, signal.SIGKILL)
        child.wait(timeout=max(0.0, deadline - active_clock()))
        raise cleanup_interruption

    monkeypatch.setattr(
        agent_providers,
        "_kill_process_group_and_reap",
        cleanup_then_interrupt,
    )
    with pytest.raises(_BoundedRunnerInterruption, match="termination helper interrupted") as caught:
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            selector_factory=ordinary_body_error,
            monotonic=clock,
            kill_group=kill_group,
        )
    assert caught.value is cleanup_interruption
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.events == ["signal", "reap"]
    assert closed == ["stdout", "stderr"]


def test_bounded_docker_runner_first_body_interruption_wins_over_later_interruptions(monkeypatch):
    process = _FakeProcess()
    clock = _FakeClock()
    _, _selector, process, killed, spawn, _kill = _bounded_seams(
        monkeypatch, read_map={}, clock=clock, process=process,
    )
    closed = []
    body_interruption = _BoundedRunnerInterruption("body interruption first")
    signal_interruption = _BoundedRunnerInterruption("signal interruption second")
    close_interruption = _BoundedRunnerInterruption("close interruption third")

    def body_interrupts():
        raise body_interruption

    def group_signal(pid, sig):
        process.events.append("signal")
        killed.append((pid, sig))
        raise signal_interruption

    def close_stdout():
        closed.append("stdout")
        raise close_interruption

    process.stdout.close = close_stdout
    process.stderr.close = lambda: closed.append("stderr")

    with pytest.raises(_BoundedRunnerInterruption, match="body interruption first") as caught:
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            selector_factory=body_interrupts,
            monotonic=clock,
            kill_group=group_signal,
        )
    assert caught.value is body_interruption
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.events == ["signal", "reap"]
    assert closed == ["stdout", "stderr"]


def test_bounded_docker_runner_body_interruption_overrides_later_ordinary_signal_error(monkeypatch):
    process = _FakeProcess()
    clock = _FakeClock()
    _, _selector, process, killed, spawn, _kill = _bounded_seams(
        monkeypatch, read_map={}, clock=clock, process=process,
    )
    closed = []
    body_interruption = _BoundedRunnerInterruption("body interruption before ordinary signal error")
    process.stdout.close = lambda: closed.append("stdout")
    process.stderr.close = lambda: closed.append("stderr")

    def interrupt_body():
        raise body_interruption

    def ordinary_signal_error(pid, sig):
        process.events.append("signal")
        killed.append((pid, sig))
        raise PermissionError("ordinary signal failure after body interruption")

    with pytest.raises(_BoundedRunnerInterruption, match="body interruption before ordinary signal error") as caught:
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            selector_factory=interrupt_body,
            monotonic=clock,
            kill_group=ordinary_signal_error,
        )
    assert caught.value is body_interruption
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.events == ["signal", "reap"]
    assert closed == ["stdout", "stderr"]


def test_bounded_docker_runner_first_cleanup_interruption_wins_over_wait_interruption(monkeypatch):
    wait_interruption = _BoundedRunnerInterruption("wait interruption second")
    signal_interruption = _BoundedRunnerInterruption("signal interruption first")
    process = _FakeProcess(wait_error=wait_interruption)
    clock = _FakeClock()
    selector = _FakeSelector()
    streams = {101: [b""], 102: [b""]}
    _, selector, process, killed, spawn, _kill = _bounded_seams(
        monkeypatch, read_map=streams, clock=clock, selector=selector, process=process,
    )
    close_order = _track_successful_bounded_closes(monkeypatch, selector, process)

    def group_signal(pid, sig):
        process.events.append("signal")
        killed.append((pid, sig))
        raise signal_interruption

    with pytest.raises(_BoundedRunnerInterruption, match="signal interruption first") as caught:
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            selector_factory=lambda: selector,
            monotonic=clock,
            kill_group=group_signal,
        )
    assert caught.value is signal_interruption
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.events == ["signal", "reap"]
    assert close_order == ["pidfd", "selector", "stdout", "stderr"]


def test_bounded_docker_runner_cleanup_clock_interruption_overrides_signal_error_and_wait(monkeypatch):
    clock_interruption = _BoundedRunnerInterruption("cleanup clock interruption first")
    wait_interruption = _BoundedRunnerInterruption("wait interruption second")

    class ArmedClock:
        armed = False

        def __call__(self):
            if self.armed:
                self.armed = False
                raise clock_interruption
            return 0.0

    clock = ArmedClock()
    process = _FakeProcess(wait_error=wait_interruption)
    selector = _FakeSelector()
    streams = {101: [b""], 102: [b""]}
    _, selector, process, killed, spawn, _kill = _bounded_seams(
        monkeypatch, read_map=streams, clock=clock, selector=selector, process=process,
    )
    close_order = _track_successful_bounded_closes(monkeypatch, selector, process)

    def group_signal(pid, sig):
        process.events.append("signal")
        killed.append((pid, sig))
        clock.armed = True
        raise PermissionError("ordinary signal failure before clock")

    with pytest.raises(_BoundedRunnerInterruption, match="cleanup clock interruption first") as caught:
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            selector_factory=lambda: selector,
            monotonic=clock,
            kill_group=group_signal,
        )
    assert caught.value is clock_interruption
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.events == ["signal", "reap"]
    assert close_order == ["pidfd", "selector", "stdout", "stderr"]


def test_bounded_docker_runner_post_reap_interruption_overrides_signal_error_and_close(monkeypatch):
    post_reap_interruption = _BoundedRunnerInterruption("post-reap interruption first")
    close_interruption = _BoundedRunnerInterruption("close interruption second")

    class ArmedClock:
        armed = False

        def __call__(self):
            if self.armed:
                self.armed = False
                raise post_reap_interruption
            return 0.0

    clock = ArmedClock()
    process = _FakeProcess(wait_action=lambda: setattr(clock, "armed", True))
    selector = _FakeSelector()
    streams = {101: [b""], 102: [b""]}
    _, selector, process, killed, spawn, _kill = _bounded_seams(
        monkeypatch, read_map=streams, clock=clock, selector=selector, process=process,
    )
    close_order = _track_successful_bounded_closes(
        monkeypatch,
        selector,
        process,
        failures={"stdout": close_interruption},
    )

    def group_signal(pid, sig):
        process.events.append("signal")
        killed.append((pid, sig))
        raise PermissionError("ordinary signal failure before post-reap")

    with pytest.raises(_BoundedRunnerInterruption, match="post-reap interruption first") as caught:
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            selector_factory=lambda: selector,
            monotonic=clock,
            kill_group=group_signal,
        )
    assert caught.value is post_reap_interruption
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.events == ["signal", "reap"]
    assert close_order == ["pidfd", "selector", "stdout", "stderr"]


@pytest.mark.parametrize("interrupt_close", ["pidfd", "selector", "stdout", "stderr"])
def test_bounded_docker_runner_each_close_interruption_overrides_signal_error(monkeypatch, interrupt_close):
    process = _FakeProcess()
    clock = _FakeClock()
    selector = _FakeSelector()
    streams = {101: [b""], 102: [b""]}
    _, selector, process, killed, spawn, _kill = _bounded_seams(
        monkeypatch, read_map=streams, clock=clock, selector=selector, process=process,
    )
    close_interruption = _BoundedRunnerInterruption(f"{interrupt_close} close interrupted")
    close_order = _track_successful_bounded_closes(
        monkeypatch,
        selector,
        process,
        failures={interrupt_close: close_interruption},
    )

    def group_signal(pid, sig):
        process.events.append("signal")
        killed.append((pid, sig))
        raise PermissionError("ordinary signal failure before close")

    with pytest.raises(_BoundedRunnerInterruption, match=f"{interrupt_close} close interrupted") as caught:
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            selector_factory=lambda: selector,
            monotonic=clock,
            kill_group=group_signal,
        )
    assert caught.value is close_interruption
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.events == ["signal", "reap"]
    assert close_order == ["pidfd", "selector", "stdout", "stderr"]


def test_bounded_docker_runner_first_close_interruption_wins_over_later_close_interruptions(monkeypatch):
    process = _FakeProcess()
    clock = _FakeClock()
    selector = _FakeSelector()
    streams = {101: [b""], 102: [b""]}
    _, selector, process, killed, spawn, _kill = _bounded_seams(
        monkeypatch, read_map=streams, clock=clock, selector=selector, process=process,
    )
    first_interruption = _BoundedRunnerInterruption("pidfd close interruption first")
    second_interruption = _BoundedRunnerInterruption("stderr close interruption second")
    close_order = _track_successful_bounded_closes(
        monkeypatch,
        selector,
        process,
        failures={"pidfd": first_interruption, "stderr": second_interruption},
    )

    with pytest.raises(_BoundedRunnerInterruption, match="pidfd close interruption first") as caught:
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            selector_factory=lambda: selector,
            monotonic=clock,
            kill_group=_kill,
        )
    assert caught.value is first_interruption
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.events == ["signal", "reap"]
    assert close_order == ["pidfd", "selector", "stdout", "stderr"]


def test_bounded_docker_runner_handles_spawn_and_invalid_limit_failures():
    with pytest.raises(agent_providers.DockerInspectionError, match="process failed"):
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("missing")),
        )
    with pytest.raises(ValueError, match="positive integer"):
        agent_providers._run_bounded(["/usr/bin/docker"], 1, output_limit=0)


def test_bounded_docker_runner_handles_missing_pipe_and_deadline_at_reap(monkeypatch):
    clock = _FakeClock()
    selector = _FakeSelector()
    process = _FakeProcess()
    process.stdout = None
    _, selector, process, killed, spawn, kill_group = _bounded_seams(
        monkeypatch, read_map={}, clock=clock, selector=selector, process=process,
    )
    with pytest.raises(agent_providers.DockerInspectionError, match="output pipes"):
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            selector_factory=lambda: selector,
            monotonic=clock,
            kill_group=kill_group,
        )
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.stderr.closed
    assert process.events == ["signal", "reap"]

    for advance_during in ("read", "wait"):
        clock = _FakeClock()
        selector = _FakeSelector()
        process = _FakeProcess()
        _, selector, process, killed, spawn, kill_group = _bounded_seams(
            monkeypatch, read_map={101: [b""], 102: [b""]},
            clock=clock, selector=selector, process=process,
        )
        if advance_during == "read":
            original_read = agent_providers.os.read

            def deadline_read(fd, amount):
                value = original_read(fd, amount)
                if fd == 102:
                    clock.now = 1
                return value

            monkeypatch.setattr(agent_providers.os, "read", deadline_read)
        else:
            process.wait_action = lambda: setattr(clock, "now", 1)
        with pytest.raises(agent_providers.DockerInspectionError, match="query_timeout_s"):
            agent_providers._run_bounded(
                ["/usr/bin/docker"], 1,
                popen_factory=spawn,
                selector_factory=lambda: selector,
                monotonic=clock,
                kill_group=kill_group,
            )
        assert killed == [(process.pid, signal.SIGKILL)]
        assert process.events == ["signal", "reap"]


def test_bounded_docker_runner_rejects_unrelated_event_before_pidfd(monkeypatch):
    class UnrelatedThenPidfd(_FakeSelector):
        def __init__(self):
            super().__init__()
            self.pidfd_selects = 0

        def select(self, timeout):
            if any(isinstance(stream, int) for stream, _data in self.entries.values()):
                self.pidfd_selects += 1
                if self.pidfd_selects == 1:
                    return [(_FakeSelectorKey(object(), ("other", None)), agent_providers.selectors.EVENT_READ)]
            return super().select(timeout)

    selector = UnrelatedThenPidfd()
    process = _FakeProcess()
    _, selector, process, killed, spawn, kill_group = _bounded_seams(
        monkeypatch, read_map={101: [b""], 102: [b""]}, selector=selector, process=process,
    )
    with pytest.raises(agent_providers.DockerInspectionError, match="unrelated event"):
        agent_providers._run_bounded(
            ["/usr/bin/docker"], None,
            popen_factory=spawn,
            selector_factory=lambda: selector,
            kill_group=kill_group,
        )
    assert selector.pidfd_selects == 1
    assert killed == [(process.pid, signal.SIGKILL)]


def test_bounded_docker_runner_waits_for_pidfd_after_empty_selector_result(monkeypatch):
    class EmptyThenPidfd(_FakeSelector):
        def __init__(self):
            super().__init__()
            self.pidfd_selects = 0

        def select(self, timeout):
            if any(isinstance(stream, int) for stream, _data in self.entries.values()):
                self.pidfd_selects += 1
                if self.pidfd_selects == 1:
                    return []
            return super().select(timeout)

    selector = EmptyThenPidfd()
    process = _FakeProcess()
    _, selector, process, killed, spawn, kill_group = _bounded_seams(
        monkeypatch, read_map={101: [b""], 102: [b""]}, selector=selector, process=process,
    )
    result = agent_providers._run_bounded(
        ["/usr/bin/docker"], None,
        popen_factory=spawn,
        selector_factory=lambda: selector,
        kill_group=kill_group,
    )
    assert result.returncode == 0
    assert selector.pidfd_selects == 2
    assert killed == [(process.pid, signal.SIGKILL)]


def test_bounded_docker_runner_kills_on_selector_setup_error(monkeypatch):
    process = _FakeProcess()
    clock = _FakeClock()
    _, _selector, process, killed, spawn, kill_group = _bounded_seams(
        monkeypatch, read_map={}, clock=clock, process=process,
    )

    def broken_selector():
        raise RuntimeError("selector unavailable")

    with pytest.raises(agent_providers.DockerInspectionError, match="process failed"):
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            selector_factory=broken_selector,
            monotonic=clock,
            kill_group=kill_group,
        )
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.wait_timeouts == [1]
    assert process.events == ["signal", "reap"]


def test_bounded_docker_runner_reaps_after_interruption_during_selection(monkeypatch):
    process = _FakeProcess()
    clock = _FakeClock()
    selector = _FakeSelector()
    _, selector, process, killed, spawn, kill_group = _bounded_seams(
        monkeypatch, read_map={}, clock=clock, selector=selector, process=process,
    )

    def interrupt_select(_timeout):
        raise _BoundedRunnerInterruption("selection interrupted")

    selector.select = interrupt_select
    with pytest.raises(_BoundedRunnerInterruption, match="selection interrupted"):
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            selector_factory=lambda: selector,
            monotonic=clock,
            kill_group=kill_group,
        )
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.events == ["signal", "reap"]


def test_bounded_docker_runner_reaps_after_interruption_during_read(monkeypatch):
    process = _FakeProcess()
    clock = _FakeClock()
    selector = _FakeSelector()
    _, selector, process, killed, spawn, kill_group = _bounded_seams(
        monkeypatch, read_map={101: [b"data"]}, clock=clock, selector=selector, process=process,
    )
    monkeypatch.setattr(
        agent_providers.os,
        "read",
        lambda _fd, _amount: (_ for _ in ()).throw(_BoundedRunnerInterruption("read interrupted")),
    )

    with pytest.raises(_BoundedRunnerInterruption, match="read interrupted"):
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            selector_factory=lambda: selector,
            monotonic=clock,
            kill_group=kill_group,
        )
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.events == ["signal", "reap"]


def test_bounded_docker_runner_reaps_after_interruption_during_selector_setup(monkeypatch):
    process = _FakeProcess()
    clock = _FakeClock()
    _, _selector, process, killed, spawn, kill_group = _bounded_seams(
        monkeypatch, read_map={}, clock=clock, process=process,
    )

    def interrupt_setup():
        raise _BoundedRunnerInterruption("selector setup interrupted")

    with pytest.raises(_BoundedRunnerInterruption, match="selector setup interrupted"):
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            selector_factory=interrupt_setup,
            monotonic=clock,
            kill_group=kill_group,
        )
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.wait_timeouts == [1]
    assert process.events == ["signal", "reap"]


def test_bounded_docker_runner_reaps_after_interruption_during_pidfd_observation(monkeypatch):
    process = _FakeProcess()
    clock = _FakeClock()
    selector = _FakeSelector()
    streams = {101: [b""], 102: [b""]}
    _, selector, process, killed, spawn, kill_group = _bounded_seams(
        monkeypatch, read_map=streams, clock=clock, selector=selector, process=process,
    )
    original_select = selector.select

    def interrupt_pidfd(timeout):
        if any(isinstance(stream, int) for stream, _data in selector.entries.values()):
            raise _BoundedRunnerInterruption("pidfd observation interrupted")
        return original_select(timeout)

    selector.select = interrupt_pidfd
    with pytest.raises(_BoundedRunnerInterruption, match="pidfd observation interrupted"):
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            selector_factory=lambda: selector,
            monotonic=clock,
            kill_group=kill_group,
        )
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.events == ["signal", "reap"]


def test_bounded_docker_runner_reaps_when_group_signal_raises_base_exception(monkeypatch):
    process = _FakeProcess()
    clock = _FakeClock()
    selector = _FakeSelector()
    _, selector, process, _killed, spawn, _kill = _bounded_seams(
        monkeypatch, read_map={}, clock=clock, selector=selector, process=process,
    )
    calls = []

    def interrupted_signal(pid, sig):
        calls.append((pid, sig))
        process.events.append("signal")
        raise _BoundedRunnerInterruption("signal interrupted")

    with pytest.raises(_BoundedRunnerInterruption, match="signal interrupted"):
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            selector_factory=lambda: selector,
            monotonic=clock,
            kill_group=interrupted_signal,
        )
    assert calls == [(process.pid, signal.SIGKILL)]
    assert process.events == ["signal", "reap"]
    assert process.wait_timeouts == [1]


def test_bounded_docker_runner_attempts_every_close_and_reports_first_after_success(monkeypatch):
    process = _FakeProcess()
    clock = _FakeClock()
    selector = _FakeSelector()
    streams = {101: [b""], 102: [b""]}
    _, selector, process, killed, spawn, kill_group = _bounded_seams(
        monkeypatch, read_map=streams, clock=clock, selector=selector, process=process,
    )
    pidfd = 303
    monkeypatch.setattr(agent_providers.os, "pidfd_open", lambda _pid: pidfd)
    close_order = []
    close_errors = {
        "pidfd": OSError("pidfd close failed"),
        "selector": OSError("selector close failed"),
        "stdout": OSError("stdout close failed"),
        "stderr": OSError("stderr close failed"),
    }

    def close_pidfd(fd):
        assert fd == pidfd
        close_order.append("pidfd")
        raise close_errors["pidfd"]

    monkeypatch.setattr(agent_providers.os, "close", close_pidfd)

    def close_selector():
        close_order.append("selector")
        raise close_errors["selector"]

    selector.close = close_selector

    def close_stdout():
        close_order.append("stdout")
        raise close_errors["stdout"]

    def close_stderr():
        close_order.append("stderr")
        raise close_errors["stderr"]

    process.stdout.close = close_stdout
    process.stderr.close = close_stderr

    with pytest.raises(agent_providers.DockerInspectionError, match="cleanup failed") as caught:
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            selector_factory=lambda: selector,
            monotonic=clock,
            kill_group=kill_group,
        )
    assert caught.value.__cause__ is close_errors["pidfd"]
    assert close_order == ["pidfd", "selector", "stdout", "stderr"]
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.events == ["signal", "reap"]


def test_bounded_docker_runner_preserves_interruption_over_close_errors(monkeypatch):
    process = _FakeProcess()
    clock = _FakeClock()
    _, _selector, process, killed, spawn, kill_group = _bounded_seams(
        monkeypatch, read_map={}, clock=clock, process=process,
    )
    closed = []

    def fail_close(stream_name):
        def close():
            closed.append(stream_name)
            raise OSError(f"{stream_name} close failed")

        return close

    process.stdout.close = fail_close("stdout")
    process.stderr.close = fail_close("stderr")

    def interrupt_setup():
        raise _BoundedRunnerInterruption("setup interrupted")

    with pytest.raises(_BoundedRunnerInterruption, match="setup interrupted"):
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            selector_factory=interrupt_setup,
            monotonic=clock,
            kill_group=kill_group,
        )
    assert closed == ["stdout", "stderr"]
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.events == ["signal", "reap"]


@pytest.mark.parametrize(
    ("close_error", "error_type"),
    [
        pytest.param(
            _BoundedRunnerInterruption("descriptor close interrupted"),
            _BoundedRunnerInterruption,
            id="interruption",
        ),
        pytest.param(
            agent_providers.DockerInspectionError("descriptor close failed"),
            agent_providers.DockerInspectionError,
            id="docker-inspection-error",
        ),
    ],
)
def test_bounded_docker_runner_attempts_later_closes_after_special_close_errors(
    monkeypatch,
    close_error,
    error_type,
):
    process = _FakeProcess()
    clock = _FakeClock()
    selector = _FakeSelector()
    streams = {101: [b""], 102: [b""]}
    _, selector, process, killed, spawn, kill_group = _bounded_seams(
        monkeypatch, read_map=streams, clock=clock, selector=selector, process=process,
    )
    pidfd = 303
    monkeypatch.setattr(agent_providers.os, "pidfd_open", lambda _pid: pidfd)
    close_order = []

    def close_pidfd(_fd):
        close_order.append("pidfd")
        raise close_error

    monkeypatch.setattr(agent_providers.os, "close", close_pidfd)
    selector.close = lambda: close_order.append("selector")
    process.stdout.close = lambda: close_order.append("stdout")
    process.stderr.close = lambda: close_order.append("stderr")

    with pytest.raises(error_type):
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            selector_factory=lambda: selector,
            monotonic=clock,
            kill_group=kill_group,
        )
    assert close_order == ["pidfd", "selector", "stdout", "stderr"]
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.events == ["signal", "reap"]


@pytest.mark.parametrize(
    ("failure_call", "fault", "error_type", "match"),
    [
        pytest.param(
            7,
            RuntimeError("reap clock failed"),
            agent_providers.DockerInspectionError,
            "could not bound",
            id="ordinary-reap-clock-error",
        ),
        pytest.param(
            7,
            _BoundedRunnerInterruption("reap clock interrupted"),
            _BoundedRunnerInterruption,
            "reap clock interrupted",
            id="interrupted-reap-clock",
        ),
        pytest.param(
            8,
            RuntimeError("post-reap clock failed"),
            agent_providers.DockerInspectionError,
            "cleanup failed",
            id="post-reap-clock-error",
        ),
    ],
)
def test_bounded_docker_runner_reaps_and_reports_cleanup_clock_errors(
    monkeypatch,
    failure_call,
    fault,
    error_type,
    match,
):
    process = _FakeProcess()
    selector = _FakeSelector()
    streams = {101: [b""], 102: [b""]}
    _, selector, process, killed, spawn, kill_group = _bounded_seams(
        monkeypatch, read_map=streams, selector=selector, process=process,
    )

    class FaultClock:
        calls = 0

        def __call__(self):
            self.calls += 1
            if self.calls == failure_call:
                raise fault
            return 0.0

    clock = FaultClock()
    with pytest.raises(error_type, match=match):
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 5,
            popen_factory=spawn,
            selector_factory=lambda: selector,
            monotonic=clock,
            kill_group=kill_group,
        )
    assert clock.calls >= failure_call
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.events == ["signal", "reap"]


@pytest.mark.parametrize(
    ("wait_error", "error_type", "match"),
    [
        pytest.param(
            _BoundedRunnerInterruption("wait interrupted"),
            _BoundedRunnerInterruption,
            "wait interrupted",
            id="wait-interruption",
        ),
        pytest.param(
            agent_providers.DockerInspectionError("wait inspection error"),
            agent_providers.DockerInspectionError,
            "wait inspection error",
            id="wait-inspection-error",
        ),
    ],
)
def test_bounded_docker_runner_preserves_reap_errors_after_attempting_signal(
    monkeypatch,
    wait_error,
    error_type,
    match,
):
    process = _FakeProcess(wait_error=wait_error)
    clock = _FakeClock()
    selector = _FakeSelector()
    streams = {101: [b""], 102: [b""]}
    _, selector, process, killed, spawn, kill_group = _bounded_seams(
        monkeypatch, read_map=streams, clock=clock, selector=selector, process=process,
    )

    with pytest.raises(error_type, match=match):
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            selector_factory=lambda: selector,
            monotonic=clock,
            kill_group=kill_group,
        )
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.events == ["signal", "reap"]


def test_bounded_docker_runner_rejects_missing_reap_status(monkeypatch):
    process = _FakeProcess()
    clock = _FakeClock()
    selector = _FakeSelector()
    streams = {101: [b""], 102: [b""]}
    _, selector, process, killed, spawn, kill_group = _bounded_seams(
        monkeypatch, read_map=streams, clock=clock, selector=selector, process=process,
    )

    def no_status(*, timeout):
        assert process.events == ["signal"]
        process.events.append("reap")
        process.wait_timeouts.append(timeout)
        return None

    process.wait = no_status
    with pytest.raises(agent_providers.DockerInspectionError, match="no status"):
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            selector_factory=lambda: selector,
            monotonic=clock,
            kill_group=kill_group,
        )
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.events == ["signal", "reap"]


def test_bounded_docker_runner_rejects_cleanup_without_reap_status(monkeypatch):
    process = _FakeProcess()
    clock = _FakeClock()
    selector = _FakeSelector()
    streams = {101: [b""], 102: [b""]}
    _, selector, process, killed, spawn, kill_group = _bounded_seams(
        monkeypatch, read_map=streams, clock=clock, selector=selector, process=process,
    )

    def cleanup_without_status(child, deadline, active_clock, signal_group, *, failure_records=None):
        signal_group(child.pid, signal.SIGKILL)
        child.wait(timeout=max(0.0, deadline - active_clock()))
        return None

    monkeypatch.setattr(
        agent_providers,
        "_kill_process_group_and_reap",
        cleanup_without_status,
    )
    with pytest.raises(agent_providers.DockerInspectionError, match="did not complete cleanup"):
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            selector_factory=lambda: selector,
            monotonic=clock,
            kill_group=kill_group,
        )
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.events == ["signal", "reap"]


def test_bounded_docker_runner_kills_and_reaps_when_pidfd_open_fails(monkeypatch):
    clock = _FakeClock()
    selector = _FakeSelector()
    process = _FakeProcess()
    streams = {101: [b""], 102: [b""]}
    _, selector, process, killed, spawn, kill_group = _bounded_seams(
        monkeypatch, read_map=streams, clock=clock, selector=selector, process=process,
    )
    monkeypatch.setattr(
        agent_providers.os,
        "pidfd_open",
        lambda _pid: (_ for _ in ()).throw(OSError("pidfd unavailable")),
    )
    with pytest.raises(agent_providers.DockerInspectionError, match="pidfd"):
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            selector_factory=lambda: selector,
            monotonic=clock,
            kill_group=kill_group,
        )
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.events == ["signal", "reap"]


def test_bounded_docker_runner_kills_and_reaps_when_pidfd_wait_times_out(monkeypatch):
    clock = _FakeClock()
    selector = _FakeSelector()
    process = _FakeProcess()
    streams = {101: [b""], 102: [b""]}
    _, selector, process, killed, spawn, kill_group = _bounded_seams(
        monkeypatch, read_map=streams, clock=clock, selector=selector, process=process,
    )
    original_select = selector.select

    def no_pidfd_readiness(timeout):
        if any(isinstance(stream, int) for stream, _data in selector.entries.values()):
            return []
        return original_select(timeout)

    selector.select = no_pidfd_readiness
    with pytest.raises(agent_providers.DockerInspectionError, match="query_timeout_s"):
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            selector_factory=lambda: selector,
            monotonic=clock,
            kill_group=kill_group,
        )
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.events == ["signal", "reap"]


def test_bounded_docker_runner_cleans_up_when_pidfd_open_consumes_deadline(monkeypatch):
    clock = _FakeClock()
    selector = _FakeSelector()
    process = _FakeProcess()
    streams = {101: [b""], 102: [b""]}
    _, selector, process, killed, spawn, kill_group = _bounded_seams(
        monkeypatch, read_map=streams, clock=clock, selector=selector, process=process,
    )
    opened = []

    def late_pidfd_open(_pid):
        fd = os.open(os.devnull, os.O_RDONLY | os.O_CLOEXEC)
        opened.append(fd)
        clock.now = 1
        return fd

    monkeypatch.setattr(agent_providers.os, "pidfd_open", late_pidfd_open)
    with pytest.raises(agent_providers.DockerInspectionError, match="query_timeout_s"):
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            selector_factory=lambda: selector,
            monotonic=clock,
            kill_group=kill_group,
        )
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.events == ["signal", "reap"]
    with pytest.raises(OSError):
        os.fstat(opened[0])


def test_bounded_docker_runner_reports_a_non_timeout_reap_error(monkeypatch):
    clock = _FakeClock()
    selector = _FakeSelector()
    process = _FakeProcess(wait_error=PermissionError("wait denied"))
    streams = {101: [b""], 102: [b""]}
    _, selector, process, killed, spawn, kill_group = _bounded_seams(
        monkeypatch, read_map=streams, clock=clock, selector=selector, process=process,
    )
    with pytest.raises(agent_providers.DockerInspectionError, match="child reaping failed"):
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            selector_factory=lambda: selector,
            monotonic=clock,
            kill_group=kill_group,
        )
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.events == ["signal", "reap"]


def test_bounded_docker_runner_treats_an_already_gone_process_group_as_reaped():
    process = _FakeProcess()

    def already_gone(_pid, _sig):
        process.events.append("signal")
        raise ProcessLookupError("already gone")

    agent_providers._kill_process_group_and_reap(
        process,
        1,
        lambda: 0,
        already_gone,
    )
    assert process.wait_timeouts == [1]
    assert process.events == ["signal", "reap"]


def test_docker_process_boundary_drops_ambient_environment_and_uses_fixed_host(tmp_path, monkeypatch):
    record_file = tmp_path / "record.json"
    script = tmp_path / "fake-docker"
    script.write_text(
        "#!" + sys.executable + "\n"
        "import json, os, sys\n"
        f"with open({str(record_file)!r}, 'w', encoding='utf-8') as f:\n"
        "    json.dump({'env': dict(os.environ), 'cwd': os.getcwd(), 'argv': sys.argv[1:]}, f)\n"
        f"print({json.dumps([_docker_record()])!r})\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", "/ambient/path")
    monkeypatch.setenv("DOCKER_HOST", "tcp://ambient.invalid:2375")
    monkeypatch.setenv("DOCKER_CONTEXT", "ambient-context")
    monkeypatch.setenv("DOCKER_CONFIG", "/ambient/docker-config")
    kill_calls = []
    real_runner = agent_providers._run_bounded

    class GateBudgetSelector:
        def __init__(self):
            self.inner = agent_providers.selectors.DefaultSelector()

        def register(self, fileobj, events, data=None):
            self.inner.register(fileobj, events, data)

        def unregister(self, fileobj):
            return self.inner.unregister(fileobj)

        def get_map(self):
            return self.inner.get_map()

        def select(self, _timeout):
            return self.inner.select(None)

        def close(self):
            self.inner.close()

    def bounded_runner(argv, timeout):
        return real_runner(
            argv,
            timeout,
            selector_factory=GateBudgetSelector,
            monotonic=lambda: 0.0,
            kill_group=lambda pid, sig: kill_calls.append((pid, sig)),
        )

    monkeypatch.setattr(agent_providers, "_run_bounded", bounded_runner)

    inspect = docker_object_inspector(5, docker_binary=str(script))
    assert inspect("legacy-runner") is InspectorAnswer.PRESENT
    assert len(kill_calls) == 1
    assert kill_calls[0][1] == signal.SIGKILL
    observed = json.loads(record_file.read_text(encoding="utf-8"))
    assert observed == {
        "env": _DOCKER_ENV,
        "cwd": "/",
        "argv": ["--host", _DOCKER_HOST, "inspect", "--type=container", "legacy-runner"],
    }


def test_docker_process_boundary_kills_pipe_holding_descendant(tmp_path):
    ready_read, ready_write = os.pipe()
    pid_file = tmp_path / "descendant.pid"
    script = tmp_path / "fake-docker-descendant"
    script.write_text(
        "#!" + sys.executable + "\n"
        + "import os, signal\n"
        + "leader = os.getpid()\n"
        + "pid = os.fork()\n"
        + "if pid == 0:\n"
        + "    while os.getppid() == leader:\n"
        + "        pass\n"
        + f"    with open({str(pid_file)!r}, 'w', encoding='ascii') as output:\n"
        + "        output.write(str(os.getpid()))\n"
        + f"    os.write({ready_write}, b'R')\n"
        + "    while True:\n"
        + "        signal.pause()\n"
        + "os._exit(0)\n",
        encoding="utf-8",
    )
    script.chmod(0o755)

    class ReadyClock:
        def __init__(self):
            self.now = 0.0
            self.deadline = 1.0
            self.ready = False
            self.calls = 0
            self.pidfd = None

        def __call__(self):
            self.calls += 1
            if self.calls == 2 and not self.ready:
                assert os.read(ready_read, 1) == b"R"
                descendant_pid = int(pid_file.read_text(encoding="ascii"))
                self.pidfd = os.pidfd_open(descendant_pid)
                self.ready = True
            return self.now

    clock = ReadyClock()

    class ExpiringSelector:
        def __init__(self):
            self.entries = {}

        def register(self, stream, _events, data):
            self.entries[stream.fileno()] = (stream, data)

        def get_map(self):
            return dict(self.entries)

        def select(self, _timeout):
            assert clock.ready
            clock.now = clock.deadline
            return []

        def close(self):
            pass

    def spawn(argv, **options):
        options["pass_fds"] = (ready_write,)
        return subprocess.Popen(argv, **options)

    try:
        with pytest.raises(agent_providers.DockerInspectionError, match="query_timeout_s"):
            agent_providers._run_bounded(
                [str(script), "--host", _DOCKER_HOST, "inspect", "--type=container", "legacy-runner"],
                1,
                popen_factory=spawn,
                selector_factory=ExpiringSelector,
                monotonic=clock,
            )
        assert clock.pidfd is not None
        ready, _writable, _errors = select.select([clock.pidfd], [], [])
        assert ready, "pipe-holding descendant did not exit after process-group termination"
    finally:
        if clock.pidfd is not None:
            os.close(clock.pidfd)
        os.close(ready_read)
        os.close(ready_write)


@pytest.mark.parametrize("late", [False, True], ids=["success", "late-result"])
def test_docker_process_boundary_kills_pipe_closing_descendant_on_success_and_late_result(
    tmp_path,
    monkeypatch,
    late,
):
    ready_read, ready_write = os.pipe()
    pid_file = tmp_path / "descendant.pid"
    script = tmp_path / "fake-docker-descendant"
    script.write_text(
        "#!" + sys.executable + "\n"
        + "import os, signal\n"
        + "pid = os.fork()\n"
        + "if pid == 0:\n"
        + f"    os.close({ready_read})\n"
        + "    os.close(1)\n"
        + "    os.close(2)\n"
        + f"    with open({str(pid_file)!r}, 'w', encoding='ascii') as output:\n"
        + "        output.write(str(os.getpid()))\n"
        + f"    os.write({ready_write}, b'R')\n"
        + "    signal.pause()\n"
        + f"os.close({ready_write})\n"
        + f"assert os.read({ready_read}, 1) == b'R'\n"
        + f"os.close({ready_read})\n"
        + "os.write(1, b'[]')\n"
        + "os._exit(0)\n",
        encoding="utf-8",
    )
    script.chmod(0o755)

    class ControlledClock:
        now = 0.0
        deadline = 1.0

        def __call__(self):
            return self.now

    clock = ControlledClock()
    real_select = select.select
    descendant_pidfd = None
    ready_write_open = True

    class GateBudgetSelector:
        def __init__(self):
            self.inner = selectors.DefaultSelector()

        def register(self, fileobj, events, data=None):
            self.inner.register(fileobj, events, data)

        def unregister(self, fileobj):
            return self.inner.unregister(fileobj)

        def get_map(self):
            return self.inner.get_map()

        def select(self, _timeout):
            nonlocal descendant_pidfd
            ready = self.inner.select(None)
            if any(key.data[0] == "pidfd" for key, _events in ready):
                descendant_pid = int(pid_file.read_text(encoding="ascii"))
                descendant_pidfd = os.pidfd_open(descendant_pid)
                if late:
                    clock.now = clock.deadline
            return ready

        def close(self):
            self.inner.close()

    def spawn(argv, **options):
        nonlocal ready_write_open
        options["pass_fds"] = (ready_read, ready_write)
        child = subprocess.Popen(argv, **options)
        os.close(ready_write)
        ready_write_open = False
        return child

    try:
        if late:
            with pytest.raises(agent_providers.DockerInspectionError, match="query_timeout_s"):
                agent_providers._run_bounded(
                    [str(script), "--host", _DOCKER_HOST, "inspect", "--type=container", "legacy-runner"],
                    1,
                    popen_factory=spawn,
                    selector_factory=GateBudgetSelector,
                    monotonic=clock,
                )
        else:
            result = agent_providers._run_bounded(
                [str(script), "--host", _DOCKER_HOST, "inspect", "--type=container", "legacy-runner"],
                1,
                popen_factory=spawn,
                selector_factory=GateBudgetSelector,
                monotonic=clock,
            )
            assert result.returncode == 0
            assert result.stdout == b"[]"
        assert descendant_pidfd is not None
        ready, _writable, _errors = real_select([descendant_pidfd], [], [], None)
        assert ready, "pipe-closing descendant remained alive after group cleanup"
    finally:
        if descendant_pidfd is not None:
            os.close(descendant_pidfd)
        os.close(ready_read)
        if ready_write_open:
            os.close(ready_write)


def test_agent_entry_wires_real_providers_and_they_work_at_the_service_boundary(tmp_path, monkeypatch):
    policy, _checkout = _policy(tmp_path)
    jobs = policy.legacy_jobs_dir
    jobs.mkdir()
    (jobs / "running odd name").mkdir()
    finished = jobs / "finished"
    finished.mkdir()
    (finished / "EXIT").write_text("0\n", encoding="utf-8")
    labels = {"fleetd.lease": "controller-lease"}
    record = _docker_record(name="legacy-runner", labels=labels)
    calls = []

    def runner(argv, timeout):
        calls.append((argv, timeout))
        return _completed(argv, [record])

    monkeypatch.setattr(agent_entry, "installed_agent_version", lambda: "1.2.3")
    monkeypatch.setattr(agent_providers, "_run_bounded", runner)
    service, loaded_policy = agent_entry._service(tmp_path / "agent.toml", tmp_path / "state")
    service.admission._legacy_call = lambda callback: callback()
    service.admission._inspector_call = lambda callback: callback()

    assert loaded_policy.legacy_jobs_dir == jobs
    assert service.admission.legacy_jobs_provider is not None
    assert service.admission.inspector is not None
    assert service.admission.container_inspector is not None
    assert "lease" in service._capabilities()
    assert len(service.admission._legacy_provider()()) == 1
    assert service.admission._inspector_provider()("legacy-runner") is InspectorAnswer.PRESENT
    service.admission._verify_container_binding("controller-lease", "legacy-runner", _CONTAINER_ID)
    assert [call[0][-1] for call in calls] == ["legacy-runner", _CONTAINER_ID]
    assert all(call[1] == loaded_policy.query_timeout_s for call in calls)


def test_agent_entry_legacy_listing_errors_and_timeouts_map_to_exit_75(tmp_path, monkeypatch):
    policy, _checkout = _policy(tmp_path)
    policy.legacy_jobs_dir.mkdir()
    monkeypatch.setattr(agent_entry, "installed_agent_version", lambda: "1.2.3")
    service, _loaded = agent_entry._service(tmp_path / "agent.toml", tmp_path / "state")
    service.admission._legacy_call = lambda callback: callback()

    monkeypatch.setattr(
        agent_providers.os, "scandir",
        lambda _fd: (_ for _ in ()).throw(PermissionError("listing denied")),
    )
    provider = service.admission._legacy_provider()
    with pytest.raises(AdmissionError) as listing_error:
        service.admission.book._legacy_job_ids(provider)
    assert listing_error.value.exit_code == ExitCode.QUEUED_WITHDRAWN

    service.admission._legacy_call = lambda _callback: (_ for _ in ()).throw(TimeoutError("query timed out"))
    with pytest.raises(AdmissionError) as timeout_error:
        service.admission.book._legacy_job_ids(service.admission._legacy_provider())
    assert timeout_error.value.exit_code == ExitCode.QUEUED_WITHDRAWN


def test_agent_entry_maps_bounded_object_and_identity_inspector_failures(tmp_path, monkeypatch):
    _policy(tmp_path)
    monkeypatch.setattr(agent_entry, "installed_agent_version", lambda: "1.2.3")
    service, _loaded = agent_entry._service(tmp_path / "agent.toml", tmp_path / "state")
    monkeypatch.setattr(
        agent_providers, "_run_bounded",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("inspect timed out")),
    )

    assert service.admission._inspector_provider()("legacy-runner") is InspectorAnswer.ERROR
    with pytest.raises(AdmissionError) as identity_timeout:
        service.admission._verify_container_binding("controller-lease", "legacy-runner", _CONTAINER_ID)
    assert identity_timeout.value.exit_code == ExitCode.QUEUED_WITHDRAWN


def test_agent_policy_requires_an_absolute_legacy_jobs_directory(tmp_path):
    policy, _checkout = _policy(tmp_path)
    path = tmp_path / "agent.toml"
    original = path.read_text(encoding="utf-8")
    valid_line = f'legacy_jobs_dir = "{policy.legacy_jobs_dir}"'

    path.write_text(original.replace(valid_line + "\n", ""), encoding="utf-8")
    with pytest.raises(ContractError, match="missing key.*legacy_jobs_dir"):
        load_agent_policy(path)

    path.write_text(original.replace(valid_line, 'legacy_jobs_dir = "relative/jobs"'), encoding="utf-8")
    with pytest.raises(ContractError, match="legacy_jobs_dir: expected an absolute path"):
        load_agent_policy(path)
