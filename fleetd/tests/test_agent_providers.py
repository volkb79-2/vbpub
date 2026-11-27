import hashlib
import json
import math
import os
import select
import signal
import subprocess
import sys

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

    def wait(self, *, timeout):
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
        self.entries[stream.fileno()] = (stream, data)

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
        del self.entries[stream.fileno()]

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

    def process_factory(*_argv, **_options):
        if spawn is not None:
            spawn()
        return active_process

    def kill_group(pid, sig):
        killed.append((pid, sig))

    return active_clock, active_selector, active_process, killed, process_factory, kill_group


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
    assert process.wait_timeouts == [5]
    assert killed == []


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


def test_bounded_docker_runner_kills_when_child_exits_but_descendant_holds_pipe(monkeypatch):
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

    clock = _FakeClock()
    selector = _FakeSelector()
    process = _FakeProcess(wait_error=subprocess.TimeoutExpired("docker", 1))
    streams = {101: [b""], 102: [b""]}
    clock, selector, process, killed, spawn, kill_group = _bounded_seams(
        monkeypatch, read_map=streams, clock=clock, selector=selector, process=process,
    )
    clock.now = 0
    with pytest.raises(agent_providers.DockerInspectionError, match="while reaping"):
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            selector_factory=lambda: selector,
            monotonic=clock,
            kill_group=kill_group,
        )
    assert killed == [(process.pid, signal.SIGKILL)]
    assert process.wait_timeouts == [1, 1]


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
    with pytest.raises(agent_providers.DockerInspectionError, match="output pipe failed"):
        agent_providers._run_bounded(
            ["/usr/bin/docker"], 1,
            popen_factory=spawn,
            selector_factory=lambda: selector,
            monotonic=clock,
            kill_group=lambda pid, sig: kills.append((pid, sig)),
        )
    assert kills == [(process.pid, signal.SIGKILL)]

    with pytest.raises(agent_providers.DockerInspectionError, match="could not kill"):
        agent_providers._kill_process_group_and_reap(
            process,
            1,
            lambda: 0,
            lambda _pid, _sig: (_ for _ in ()).throw(PermissionError("denied")),
        )


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
        with pytest.raises(agent_providers.DockerInspectionError, match="while reaping"):
            agent_providers._run_bounded(
                ["/usr/bin/docker"], 1,
                popen_factory=spawn,
                selector_factory=lambda: selector,
                monotonic=clock,
                kill_group=kill_group,
            )
        assert killed == [(process.pid, signal.SIGKILL)]


def test_bounded_docker_runner_treats_an_already_gone_process_group_as_reaped():
    process = _FakeProcess()
    agent_providers._kill_process_group_and_reap(
        process,
        1,
        lambda: 0,
        lambda _pid, _sig: (_ for _ in ()).throw(ProcessLookupError("already gone")),
    )
    assert process.wait_timeouts == [1]


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

    inspect = docker_object_inspector(5, docker_binary=str(script))
    assert inspect("legacy-runner") is InspectorAnswer.PRESENT
    observed = json.loads(record_file.read_text(encoding="utf-8"))
    assert observed == {
        "env": _DOCKER_ENV,
        "cwd": "/",
        "argv": ["--host", _DOCKER_HOST, "inspect", "--type=container", "legacy-runner"],
    }


@pytest.mark.parametrize(
    "script_body",
    ["import os, signal\npid = os.fork()\nif pid == 0:\n    while True: signal.pause()\n"],
    ids=["pipe-holding-descendant"],
)
def test_docker_process_boundary_kills_pipe_holding_descendant(tmp_path, script_body):
    pid_file = tmp_path / "descendant.pid"
    script = tmp_path / "fake-docker-descendant"
    script.write_text(
        "#!" + sys.executable + "\n"
        + script_body
        + f"with open({str(pid_file)!r}, 'w', encoding='ascii') as output:\n"
        + "    output.write(str(pid))\n"
        + "os._exit(0)\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    inspect = docker_object_inspector(1, docker_binary=str(script))
    assert inspect("legacy-runner") is InspectorAnswer.ERROR
    descendant_pid = int(pid_file.read_text(encoding="ascii"))
    try:
        pidfd = os.pidfd_open(descendant_pid)
    except ProcessLookupError:
        return
    try:
        ready, _writable, _errors = select.select([pidfd], [], [], 5)
        assert ready, "pipe-holding descendant remained alive after Docker inspection"
    finally:
        os.close(pidfd)


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
