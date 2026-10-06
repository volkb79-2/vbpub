"""B105 branch coverage for Git's bounded process and status boundaries."""

from __future__ import annotations

import errno
import math
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from assay import git
from assay.errors import AssayError, Outcome, ReasonCode


def test_remaining_normalizes_absence_and_infinity_and_preserves_expiry():
    assert git._sample_remaining(None) is None
    assert git._sample_remaining(lambda: math.inf) is None
    assert git._sample_remaining(lambda: 0.25) == 0.25

    expired = AssayError(
        "same deadline", outcome=Outcome.ERROR, reason_code=ReasonCode.GIT_FAILED
    )

    def raise_expired():
        raise expired

    with pytest.raises(AssayError) as caught:
        git._sample_remaining(raise_expired)
    assert caught.value is expired


def test_spawn_retries_only_eagain_and_caps_sleep_to_remaining(monkeypatch):
    calls = []
    sleeps = []
    result = object()

    def spawn():
        calls.append(None)
        if len(calls) < 3:
            raise OSError(errno.EAGAIN, "fork table temporarily full")
        return result

    monkeypatch.setattr(git.time, "sleep", sleeps.append)
    assert git._spawn_with_eagain_retry(
        spawn,
        remaining=lambda: 0.01,
        what="test Git",
    ) is result
    assert len(calls) == 3
    assert sleeps == [0.01, 0.01]


def test_spawn_does_not_retry_permanent_or_exhausted_errors(monkeypatch):
    monkeypatch.setattr(git.time, "sleep", lambda _seconds: None)
    permanent = OSError(errno.EPERM, "forbidden")
    with pytest.raises(OSError) as caught:
        git._spawn_with_eagain_retry(
            lambda: (_ for _ in ()).throw(permanent),
            remaining=lambda: 1.0,
            what="test Git",
        )
    assert caught.value is permanent

    exhausted = []

    def always_eagain():
        error = OSError(errno.EAGAIN, "still full")
        exhausted.append(error)
        raise error

    with pytest.raises(OSError) as caught:
        git._spawn_with_eagain_retry(
            always_eagain,
            remaining=lambda: 1.0,
            what="test Git",
        )
    assert caught.value is exhausted[-1]
    assert len(exhausted) == git._GIT_SPAWN_RETRY_ATTEMPTS


def test_spawn_retry_budget_must_not_be_zero(monkeypatch):
    monkeypatch.setattr(git, "_GIT_SPAWN_RETRY_ATTEMPTS", 0)
    with pytest.raises(AssertionError, match="retry budget must be positive"):
        git._spawn_with_eagain_retry(
            lambda: pytest.fail("zero attempts must not spawn"),
            remaining=lambda: 1.0,
            what="test Git",
        )


def test_owned_group_kill_falls_back_to_direct_child_and_stays_best_effort(monkeypatch):
    class Process:
        pid = 73
        killed = 0

        def kill(self):
            self.killed += 1
            raise OSError("already gone")

    process = Process()
    monkeypatch.setattr(git.os, "killpg", lambda *_: (_ for _ in ()).throw(OSError("race")))
    git._kill_owned_group(process)
    assert process.killed == 1

    process.killed = 0
    monkeypatch.setattr(
        git.os,
        "killpg",
        lambda *_: (_ for _ in ()).throw(ProcessLookupError("gone")),
    )
    git._kill_owned_group(process)
    assert process.killed == 0


def test_private_git_kill_falls_back_when_its_group_has_already_exited(monkeypatch):
    class Process:
        pid = 74
        killed = 0

        @staticmethod
        def poll():
            return None

        def kill(self):
            self.killed += 1

    process = Process()
    monkeypatch.setattr(
        git.os,
        "killpg",
        lambda *_: (_ for _ in ()).throw(ProcessLookupError("group exited")),
    )
    git._p22_kill(process)
    assert process.killed == 1


class _Pipe:
    def __init__(self):
        self.closed = False

    def read1(self, _size):
        return b""

    def close(self):
        self.closed = True


class _Selector:
    def __init__(self):
        self.keys = {}

    def register(self, fileobj, _events, data):
        self.keys[fileobj] = SimpleNamespace(fileobj=fileobj, data=data)

    def get_map(self):
        return self.keys

    def select(self, timeout):
        assert timeout in (None, 1.0)
        return [(key, 1) for key in self.keys.values()]

    def unregister(self, fileobj):
        del self.keys[fileobj]

    def close(self):
        self.keys.clear()


class _WaitProcess:
    pid = 88
    returncode = 0

    def __init__(self, *, first_wait_times_out=False, wait_fails=False):
        self.stdout = _Pipe()
        self.stderr = _Pipe()
        self.first_wait_times_out = first_wait_times_out
        self.wait_fails = wait_fails
        self.waits = 0

    def poll(self):
        if self.first_wait_times_out and self.waits < 2:
            return None
        return 0

    def wait(self, timeout=None):
        self.waits += 1
        if self.wait_fails:
            raise OSError("wait failed")
        if self.first_wait_times_out and self.waits == 1:
            raise subprocess.TimeoutExpired("fake git", timeout)
        return 0


def _fake_run_bounded(monkeypatch, process):
    monkeypatch.setattr(git.selectors, "DefaultSelector", _Selector)
    monkeypatch.setattr(git.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(git, "_kill_owned_group", lambda _proc: None)


def test_run_bounded_retries_a_wait_timeout_while_deadline_remains(monkeypatch):
    process = _WaitProcess(first_wait_times_out=True)
    _fake_run_bounded(monkeypatch, process)
    assert git._run_bounded(["git", "status"], remaining=lambda: 1.0) == (
        0,
        b"",
        b"",
    )
    assert process.waits == 3


def test_run_bounded_surfaces_reap_failure_only_after_normal_completion(monkeypatch):
    process = _WaitProcess(wait_fails=True)
    _fake_run_bounded(monkeypatch, process)
    with pytest.raises(OSError, match="wait failed"):
        git._run_bounded(["git", "status"])


def test_run_bounded_kills_a_child_that_exceeds_its_stdout_limit(monkeypatch):
    process = _WaitProcess(wait_fails=True)
    process.stdout.read1 = lambda _size: b"x" * 11
    monkeypatch.setattr(git, "MAX_GIT_OUTPUT_BYTES", 10)
    _fake_run_bounded(monkeypatch, process)

    with pytest.raises(AssayError, match="more than 10 bytes on standard output"):
        git._run_bounded(["git", "status"])
    assert process.stdout.closed and process.stderr.closed
    assert process.waits == 1


def test_run_bounded_types_process_start_failure(monkeypatch):
    monkeypatch.setattr(
        git.subprocess,
        "Popen",
        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("git missing")),
    )
    with pytest.raises(AssayError, match="could not start git"):
        git._run_bounded(["git", "status"])


def test_raw_git_input_is_bounded_before_spawning_and_uses_a_seekable_file(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(git, "MAX_GIT_OUTPUT_BYTES", 4)
    monkeypatch.setattr(git, "_resolve_git_executable", lambda: Path("/git"))
    monkeypatch.setattr(
        git,
        "_resolve_repo",
        lambda *_a, **_k: SimpleNamespace(git_dir=tmp_path / ".git", repo_top=tmp_path),
    )
    calls = []

    def bounded(argv, *, remaining=None, stdin=None):
        calls.append((argv, remaining, stdin.read()))
        return 0, b"", b""

    monkeypatch.setattr(git, "_run_bounded", bounded)
    with pytest.raises(AssayError, match="input exceeds"):
        git._run_raw(tmp_path, "hash-object", input_bytes=b"12345")
    assert calls == []

    result = git._run_raw(tmp_path, "hash-object", input_bytes=b"1234")
    assert result == (0, b"", b"")
    assert calls[0][2] == b"1234"
    assert "--git-dir=" + str(tmp_path / ".git") in calls[0][0]


def test_ignore_helpers_distinguish_each_git_exit_code_and_validate_raw_shape(
    monkeypatch, tmp_path
):
    result = {"value": (0, b"", b"")}
    monkeypatch.setattr(git, "_run_raw", lambda *a, **k: result["value"])

    assert git.path_is_ignored(tmp_path, "x") is True
    result["value"] = (1, b"", b"")
    assert git.path_is_ignored(tmp_path, "x") is False
    result["value"] = (2, b"", b"fatal")
    with pytest.raises(AssayError, match="check-ignore x failed"):
        git.path_is_ignored(tmp_path, "x")

    for code, output in ((1, b""), (0, b"")):
        result["value"] = (code, output, b"")
        if code == 1:
            assert git.ignore_rule_source(tmp_path, "src/a.py") is None
        else:
            with pytest.raises(AssayError, match="malformed git check-ignore"):
                git.ignore_rule_source(tmp_path, "src/a.py")

    result["value"] = (0, b".gitignore\0" + b"5\0*.py\0src/a.py\0", b"")
    assert git.ignore_rule_source(tmp_path, "src/a.py") == ".gitignore"
    result["value"] = (2, b"", b"fatal check-ignore")
    with pytest.raises(AssayError, match=r"check-ignore failed \(2\).*fatal check-ignore"):
        git.ignore_rule_source(tmp_path, "src/a.py")
    result["value"] = (
        0,
        b".gitignore\0" + b"5\0!src/a.py\0src/a.py\0",
        b"",
    )
    assert git.ignore_rule_source(tmp_path, "src/a.py") is None
    with pytest.raises(AssayError, match="NUL byte"):
        git.ignore_rule_source(tmp_path, "bad\x00path")
    with pytest.raises(AssayError, match="not representable as UTF-8"):
        git.ignore_rule_source(tmp_path, "\ud800")


def test_exact_commit_rejects_replacement_and_reuses_git_exit_semantics(monkeypatch, tmp_path):
    monkeypatch.setattr(git, "run", lambda *_a, **_k: "f" * 40 + "\n")
    with pytest.raises(AssayError, match="does not resolve to itself"):
        git.verify_exact_commit(tmp_path, "a" * 40, remaining=lambda: 1.0)

    monkeypatch.setattr(git, "run", lambda *_a, **_k: "a" * 40 + "\n")
    git.verify_exact_commit(tmp_path, "a" * 40, remaining=lambda: 1.0)

    monkeypatch.setattr(git, "_run_raw", lambda *_a, **_k: (1, b"", b""))
    assert git.is_ancestor(tmp_path, "a" * 40, "b" * 40, remaining=lambda: 1.0) is False
    assert git.path_is_current(
        tmp_path, "a" * 40, "b" * 40, "src/a.py", remaining=lambda: 1.0
    ) is False


def test_linked_worktree_diagnostic_treats_an_unreadable_target_as_missing(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    repo.mkdir()
    target = tmp_path / "main.git"
    marker = repo / ".git"
    marker.write_text(f"gitdir: {target}\n", encoding="utf-8")
    real_is_dir = Path.is_dir

    def is_dir(path):
        if path == target:
            raise OSError("permission denied")
        return real_is_dir(path)

    monkeypatch.setattr(Path, "is_dir", is_dir)
    detail = git._linked_worktree_gap(repo)
    assert detail is not None
    assert "LINKED git worktree" in detail
    assert str(target) in detail


def test_ignore_rule_source_surfaces_an_unknown_git_status(monkeypatch, tmp_path):
    monkeypatch.setattr(
        git, "_run_raw", lambda *_args, **_kwargs: (2, b"", b"repository unavailable")
    )
    with pytest.raises(AssayError, match=r"check-ignore failed \(2\)"):
        git.ignore_rule_source(tmp_path, "src/a.py")


def test_p22_limit_uses_snapshot_limit_terminal():
    error = git._p22_limit("snapshot object ceiling")
    assert error.outcome is Outcome.BUDGET_EXCEEDED
    assert error.reason_code is ReasonCode.SNAPSHOT_LIMIT_EXCEEDED


def test_p22_deadline_fails_closed_after_its_single_expiry(monkeypatch):
    readings = iter((10.0, 12.0))
    monkeypatch.setattr(git.time, "monotonic", lambda: next(readings))
    deadline = git._P22Deadline(1.0)
    with pytest.raises(AssayError) as caught:
        deadline.remaining("reading a private snapshot")
    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT


def test_p22_kill_is_a_noop_for_absent_or_reaped_processes(monkeypatch):
    calls = []

    class Process:
        pid = 91

        @staticmethod
        def poll():
            return 0

        def kill(self):
            calls.append("kill")

    monkeypatch.setattr(git.os, "killpg", lambda *_args: calls.append("killpg"))
    git._p22_kill(None)
    git._p22_kill(Process())
    assert calls == []


class _PumpSelector:
    def __init__(self, *, empty=False):
        self.keys = {}
        self.empty = empty

    def register(self, fd, _events):
        self.keys[fd] = SimpleNamespace(fd=fd, fileobj=fd)

    def get_map(self):
        return self.keys

    def select(self, timeout):
        assert timeout == 1.0
        if self.empty:
            return []
        return [(next(iter(self.keys.values())), git.selectors.EVENT_WRITE)]

    def unregister(self, fd):
        del self.keys[fd]

    def close(self):
        self.keys.clear()


def test_p22_pump_retries_blocked_writes_and_preserves_partial_buffers(monkeypatch):
    selector = _PumpSelector()
    monkeypatch.setattr(git.selectors, "DefaultSelector", lambda: selector)
    monkeypatch.setattr(git.os, "set_blocking", lambda *_args: None)
    closed = []
    monkeypatch.setattr(git.os, "close", closed.append)
    writes = []

    def write(_fd, buffer):
        if not writes:
            writes.append("blocked")
            raise BlockingIOError
        chunk = bytes(buffer[:1])
        writes.append(chunk)
        return len(chunk)

    monkeypatch.setattr(git.os, "write", write)
    git._p22_pump(
        (),
        deadline=SimpleNamespace(remaining=lambda _what: 1.0),
        what="test write",
        writers={101: b"abc"},
        readers={},
    )
    assert writes == ["blocked", b"a", b"b", b"c"]
    assert closed == [101]


def test_p22_pump_closes_a_broken_pipe_and_stops_submitting_that_writer(
    monkeypatch,
):
    selector = _PumpSelector()
    monkeypatch.setattr(git.selectors, "DefaultSelector", lambda: selector)
    monkeypatch.setattr(git.os, "set_blocking", lambda *_args: None)
    closed = []
    monkeypatch.setattr(git.os, "close", closed.append)
    monkeypatch.setattr(
        git.os, "write", lambda *_args: (_ for _ in ()).throw(BrokenPipeError())
    )
    git._p22_pump(
        (),
        deadline=SimpleNamespace(remaining=lambda _what: 1.0),
        what="test broken pipe",
        writers={102: b"payload"},
        readers={},
    )
    assert closed == [102]
    assert selector.keys == {}


def test_p22_pump_timeout_closes_pending_descriptor_even_if_close_races(
    monkeypatch,
):
    selector = _PumpSelector(empty=True)
    monkeypatch.setattr(git.selectors, "DefaultSelector", lambda: selector)
    monkeypatch.setattr(git.os, "set_blocking", lambda *_args: None)
    closed = []

    def close(fd):
        closed.append(fd)
        raise OSError("descriptor was already closed")

    monkeypatch.setattr(git.os, "close", close)
    with pytest.raises(AssayError) as caught:
        git._p22_pump(
            (),
            deadline=SimpleNamespace(remaining=lambda _what: 1.0),
            what="test pump timeout",
            writers={103: b"payload"},
            readers={},
        )
    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT
    assert closed == [103]


def test_p22_pump_kills_a_child_whose_reap_wait_times_out(monkeypatch):
    selector = _PumpSelector()
    monkeypatch.setattr(git.selectors, "DefaultSelector", lambda: selector)
    killed = []
    monkeypatch.setattr(git, "_p22_kill", lambda proc: killed.append(proc))

    class Process:
        def wait(self, *, timeout):
            assert timeout == 1.0
            raise subprocess.TimeoutExpired("git", timeout)

    process = Process()
    with pytest.raises(AssayError) as caught:
        git._p22_pump(
            (process,),
            deadline=SimpleNamespace(remaining=lambda _what: 1.0),
            what="waiting for test child",
            writers={},
            readers={},
        )
    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT
    assert killed == [process]


def test_p22_deadline_refuses_an_expired_child_budget():
    with pytest.raises(AssayError) as caught:
        git._P22Deadline(0).remaining("running the boundary probe")
    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT


def test_p22_deadline_maps_an_unbounded_budget_to_no_timeout():
    assert git._P22Deadline(math.inf).remaining("running unbounded Git") is None


def test_p22_environment_adds_the_run_identity_only_when_requested(monkeypatch):
    monkeypatch.setattr(git, "_REPLACEMENT_ENV", {"PATH": "/bin"})
    monkeypatch.setattr(git, "_P22_ENV_EXTRA", {"GIT_CONFIG_NOSYSTEM": "1"})
    monkeypatch.setattr(git, "_P22_REPLACEMENT_IDENTITY", {"HOME": "/tmp/assay"})

    without_identity = git._p22_env(identity=False)
    with_identity = git._p22_env(identity=True)

    assert without_identity == {"PATH": "/bin", "GIT_CONFIG_NOSYSTEM": "1"}
    assert with_identity == {
        "PATH": "/bin",
        "GIT_CONFIG_NOSYSTEM": "1",
        "HOME": "/tmp/assay",
    }


class _FakeP22Pipe:
    def __init__(self, fd):
        self._fd = fd
        self.closed = False

    def fileno(self):
        return self._fd

    def close(self):
        self.closed = True


class _FakeP22Process:
    def __init__(self, base_fd):
        self.stdin = _FakeP22Pipe(base_fd)
        self.stdout = _FakeP22Pipe(base_fd + 1)
        self.stderr = _FakeP22Pipe(base_fd + 2)
        self.returncode = 0
        self.waits = 0

    def wait(self):
        self.waits += 1
        return self.returncode


def _stub_p22_pack_pipeline(
    monkeypatch,
    *,
    producer_code=0,
    consumer_code=0,
    second_spawn_error=None,
    pump_error=None,
):
    producer = _FakeP22Process(200)
    consumer = _FakeP22Process(300)
    spawned = []
    killed = []
    writes = []

    def spawn(*_args, **_kwargs):
        if second_spawn_error is not None and spawned:
            raise second_spawn_error
        process = producer if not spawned else consumer
        spawned.append(process)
        return process

    monkeypatch.setattr(git, "_p22_spawn", spawn)
    monkeypatch.setattr(git.os, "dup", lambda _fd: 400)
    monkeypatch.setattr(git.os, "set_blocking", lambda *_args: None)
    monkeypatch.setattr(
        git.os,
        "write",
        lambda fd, data: writes.append((fd, bytes(data))) or len(data),
    )
    monkeypatch.setattr(git, "_p22_kill", lambda proc: killed.append(proc))
    monkeypatch.setattr(git, "_MAX_GIT_STDERR_BYTES", 4)
    pump_calls = []

    def pump(procs, *, readers, **_kwargs):
        process = procs[0]
        pump_calls.append(process)
        if pump_error is not None:
            raise pump_error
        if process is producer:
            readers[producer.stdout.fileno()](b"PACK")
            stderr_sink = readers[producer.stderr.fileno()]
            stderr_sink(b"abcd")
            stderr_sink(b"excess")
            producer.returncode = producer_code
        else:
            readers[consumer.stdout.fileno()](b"ignored")
            stderr_sink = readers[consumer.stderr.fileno()]
            stderr_sink(b"wxyz")
            stderr_sink(b"excess")
            consumer.returncode = consumer_code

    monkeypatch.setattr(git, "_p22_pump", pump)
    return producer, consumer, spawned, killed, writes, pump_calls


def _stream_pack(monkeypatch, *, max_pack_bytes=4, **pipeline):
    return git._p22_stream_pack(
        Path("/git"),
        source_git_dir=Path("/source/.git"),
        source_work_tree=Path("/source"),
        source_cwd=Path("/source"),
        seed_git_dir=Path("/seed.git"),
        oid_payload=b"a" * 40 + b"\n",
        deadline=SimpleNamespace(remaining=lambda _what: 10.0),
        max_pack_bytes=max_pack_bytes,
    )


def test_p22_stream_pack_counts_and_relays_the_frozen_pack(monkeypatch):
    producer, consumer, _spawned, _killed, writes, pumps = _stub_p22_pack_pipeline(
        monkeypatch
    )
    assert _stream_pack(monkeypatch) == 4
    assert pumps == [producer, consumer]
    assert writes == [(consumer.stdin.fileno(), b"PACK")]
    assert producer.stdout.closed and producer.stderr.closed
    assert consumer.stdin.closed and consumer.stdout.closed and consumer.stderr.closed


def test_p22_stream_pack_refuses_overflow_and_reaps_both_children(monkeypatch):
    producer, consumer, _spawned, killed, _writes, pumps = _stub_p22_pack_pipeline(
        monkeypatch
    )
    with pytest.raises(AssayError) as caught:
        _stream_pack(monkeypatch, max_pack_bytes=3)
    assert caught.value.reason_code is ReasonCode.SNAPSHOT_LIMIT_EXCEEDED
    assert pumps == [producer]
    assert killed == [producer, consumer]
    assert producer.waits == 1 and consumer.waits == 1
    assert consumer.stdin.closed


@pytest.mark.parametrize(
    ("producer_code", "consumer_code", "message"),
    [
        (1, 0, "pack-objects failed"),
        (0, 1, "index-pack failed"),
    ],
)
def test_p22_stream_pack_reports_each_child_failure(
    monkeypatch, producer_code, consumer_code, message
):
    _stub_p22_pack_pipeline(
        monkeypatch,
        producer_code=producer_code,
        consumer_code=consumer_code,
    )
    with pytest.raises(AssayError, match=message):
        _stream_pack(monkeypatch)


def test_p22_stream_pack_cleans_the_producer_when_consumer_cannot_start(
    monkeypatch,
):
    producer, _consumer, spawned, killed, _writes, _pumps = _stub_p22_pack_pipeline(
        monkeypatch, second_spawn_error=RuntimeError("consumer launch failed")
    )
    with pytest.raises(RuntimeError, match="consumer launch failed"):
        _stream_pack(monkeypatch)
    assert spawned == [producer]
    assert killed == [producer]
    assert producer.waits == 1
    assert producer.stdin.closed
    assert producer.stdout.closed and producer.stderr.closed


def test_p22_stream_pack_cleans_both_children_when_the_relay_fails(monkeypatch):
    producer, consumer, _spawned, killed, _writes, pumps = _stub_p22_pack_pipeline(
        monkeypatch, pump_error=RuntimeError("pump failed")
    )
    with pytest.raises(RuntimeError, match="pump failed"):
        _stream_pack(monkeypatch)
    assert pumps == [producer]
    assert killed == [producer, consumer]
    assert producer.waits == 1 and consumer.waits == 1
    assert consumer.stdin.closed


def test_p22_stream_pack_closes_an_open_producer_stdin_when_dup_fails(monkeypatch):
    producer, _consumer, _spawned, _killed, _writes, _pumps = (
        _stub_p22_pack_pipeline(monkeypatch)
    )
    monkeypatch.setattr(
        git.os,
        "dup",
        lambda _fd: (_ for _ in ()).throw(OSError("cannot duplicate producer pipe")),
    )

    with pytest.raises(OSError, match="cannot duplicate producer pipe"):
        _stream_pack(monkeypatch)

    assert producer.stdin.closed


def test_p22_init_private_surfaces_git_init_failure_and_captures_stderr(monkeypatch):
    process = _FakeP22Process(500)
    process.returncode = 1
    monkeypatch.setattr(git, "_p22_spawn", lambda *_args, **_kwargs: process)

    def pump(_procs, *, readers, **_kwargs):
        readers[process.stderr.fileno()](b"template was not empty")

    monkeypatch.setattr(git, "_p22_pump", pump)
    with pytest.raises(AssayError, match="git init failed.*template was not empty"):
        git._p22_init_private(
            Path("/git"),
            path=Path("/seed.git"),
            template=Path("/empty-template"),
            bare=True,
            deadline=SimpleNamespace(remaining=lambda _what: 5.0),
        )
    assert process.stdout.closed and process.stderr.closed


def test_p22_init_private_pins_auto_maintenance_config(monkeypatch):
    process = _FakeP22Process(505)
    captured: list[tuple[str, ...]] = []

    def spawn(argv, **_kwargs):
        captured.append(tuple(argv))
        return process

    monkeypatch.setattr(git, "_p22_spawn", spawn)
    monkeypatch.setattr(git, "_p22_pump", lambda *_args, **_kwargs: None)
    git._p22_init_private(
        Path("/git"),
        path=Path("/seed.git"),
        template=Path("/empty-template"),
        bare=True,
        deadline=SimpleNamespace(remaining=lambda _what: 5.0),
    )

    assert len(captured) == 1
    pairs = tuple(zip(captured[0], captured[0][1:]))
    assert ("-c", "maintenance.auto=false") in pairs
    assert ("-c", "maintenance.autoDetach=false") in pairs
    assert ("-c", "gc.autoDetach=false") in pairs


def test_p22_init_private_kills_and_reaps_when_the_pump_fails(monkeypatch):
    process = _FakeP22Process(510)
    killed = []
    monkeypatch.setattr(git, "_p22_spawn", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(git, "_p22_kill", killed.append)
    monkeypatch.setattr(
        git,
        "_p22_pump",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("pump failed")),
    )

    with pytest.raises(RuntimeError, match="pump failed"):
        git._p22_init_private(
            Path("/git"),
            path=Path("/seed.git"),
            template=Path("/empty-template"),
            bare=True,
            deadline=SimpleNamespace(remaining=lambda _what: 5.0),
        )
    assert killed == [process]
    assert process.waits == 1
    assert process.stdout.closed and process.stderr.closed


def test_p22_git_bounds_stderr_and_reports_child_failure(monkeypatch, tmp_path):
    process = _FakeP22Process(520)
    process.returncode = 1
    monkeypatch.setattr(git, "_MAX_GIT_STDERR_BYTES", 2)
    monkeypatch.setattr(git, "_p22_spawn", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(git.os, "dup", lambda _fd: 600)

    def pump(_procs, *, readers, **_kwargs):
        readers[process.stdout.fileno()](b"ignored output")
        read_stderr = readers[process.stderr.fileno()]
        read_stderr(b"ab")
        read_stderr(b"cdef")

    monkeypatch.setattr(git, "_p22_pump", pump)
    with pytest.raises(AssayError, match=r"\(1\): ab$"):
        git._p22_git(
            Path("/git"),
            git_dir=tmp_path / ".git",
            work_tree=tmp_path,
            args=("status", "--short"),
            deadline=SimpleNamespace(remaining=lambda _what: 5.0),
            cwd=tmp_path,
            stdin_bytes=b"payload",
        )
    assert process.stdin.closed
    assert process.stdout.closed and process.stderr.closed


def test_p22_git_refuses_oversized_stdout_and_reaps_the_child(monkeypatch, tmp_path):
    process = _FakeP22Process(530)
    killed = []
    monkeypatch.setattr(git, "_p22_spawn", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(git.os, "dup", lambda _fd: 610)
    monkeypatch.setattr(git, "_p22_kill", killed.append)
    monkeypatch.setattr(
        git,
        "_p22_pump",
        lambda _procs, *, readers, **_kwargs: readers[process.stdout.fileno()](b"abc"),
    )

    with pytest.raises(AssayError, match="more than 2 bytes on standard output"):
        git._p22_git(
            Path("/git"),
            git_dir=tmp_path / ".git",
            work_tree=tmp_path,
            args=("status",),
            deadline=SimpleNamespace(remaining=lambda _what: 5.0),
            cwd=tmp_path,
            stdin_bytes=b"payload",
            max_stdout=2,
        )

    assert killed == [process]
    assert process.waits == 1
    assert process.stdin.closed
    assert process.stdout.closed and process.stderr.closed


def _p22_source(tmp_path):
    common = tmp_path / "common.git"
    common.mkdir()
    return git._P22Source(
        git_executable=Path("/git"),
        repo_top=tmp_path,
        git_dir=common,
        common_dir=common,
    )


def test_p22_rejects_a_nonempty_local_object_alternates_file(tmp_path):
    source = _p22_source(tmp_path)
    alternates = source.common_dir / "objects/info/alternates"
    alternates.parent.mkdir(parents=True)
    alternates.write_text("/outside/object-store\n", encoding="utf-8")

    with pytest.raises(AssayError, match="names an external object store"):
        git._p22_reject_external_topology(
            source, SimpleNamespace(remaining=lambda _what: 5.0)
        )


@pytest.mark.parametrize(
    ("config", "message"),
    [
        (b"extensions.partialClone\norigin\x00", "extensions.partialClone"),
        (b"remote.origin.promisor\ntrue\x00", "promisor remote"),
    ],
)
def test_p22_rejects_partial_clone_and_promisor_configuration(
    tmp_path, monkeypatch, config, message
):
    source = _p22_source(tmp_path)
    responses = iter((b"sha1\n", config))
    monkeypatch.setattr(
        git, "_p22_git", lambda *_args, **_kwargs: next(responses)
    )
    with pytest.raises(AssayError, match=message):
        git._p22_reject_external_topology(
            source, SimpleNamespace(remaining=lambda _what: 5.0)
        )


def test_p22_open_source_rejects_a_relative_common_git_directory(
    tmp_path, monkeypatch
):
    resolved = SimpleNamespace(git_dir=tmp_path / ".git", repo_top=tmp_path)
    monkeypatch.setattr(git, "_resolve_git_executable", lambda: Path("/git"))
    monkeypatch.setattr(git, "_resolve_repo", lambda *_args: resolved)
    monkeypatch.setattr(git, "_p22_git", lambda *_args, **_kwargs: b"relative.git\n")
    with pytest.raises(AssayError, match="not an absolute, existing directory"):
        git._p22_open_source(
            tmp_path, SimpleNamespace(remaining=lambda _what: 5.0)
        )


def test_p22_accepts_a_local_repository_with_ordinary_config(tmp_path, monkeypatch):
    source = _p22_source(tmp_path)
    responses = iter(
        (
            b"sha1\n",
            b"core.repositoryformatversion\n0\x00core.filemode\ntrue\x00",
        )
    )
    monkeypatch.setattr(git, "_p22_git", lambda *_args, **_kwargs: next(responses))

    git._p22_reject_external_topology(
        source, SimpleNamespace(remaining=lambda _what: 5.0)
    )


def test_p22_accepts_an_empty_alternates_marker(tmp_path, monkeypatch):
    source = _p22_source(tmp_path)
    alternates = source.common_dir / "objects/info/alternates"
    alternates.parent.mkdir(parents=True)
    alternates.write_bytes(b" \n")
    responses = iter((b"sha1\n", b"core.repositoryformatversion\n0\x00"))
    monkeypatch.setattr(git, "_p22_git", lambda *_args, **_kwargs: next(responses))

    git._p22_reject_external_topology(
        source, SimpleNamespace(remaining=lambda _what: 5.0)
    )


@pytest.mark.parametrize("marker", ["info/grafts", "shallow"])
def test_p22_rejects_grafted_or_shallow_history(tmp_path, marker):
    source = _p22_source(tmp_path)
    path = source.common_dir / marker
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("boundary\n", encoding="ascii")

    with pytest.raises(AssayError, match="grafted or shallow"):
        git._p22_reject_external_topology(
            source, SimpleNamespace(remaining=lambda _what: 5.0)
        )


def test_p22_rejects_a_non_sha1_object_format(tmp_path, monkeypatch):
    source = _p22_source(tmp_path)
    monkeypatch.setattr(git, "_p22_git", lambda *_args, **_kwargs: b"sha256\n")

    with pytest.raises(AssayError, match="does not use the SHA-1 object format"):
        git._p22_reject_external_topology(
            source, SimpleNamespace(remaining=lambda _what: 5.0)
        )


def test_p22_accepts_a_promisor_key_whose_value_is_false(tmp_path, monkeypatch):
    source = _p22_source(tmp_path)
    responses = iter(
        (b"sha1\n", b"remote.origin.promisor\nfalse\x00")
    )
    monkeypatch.setattr(git, "_p22_git", lambda *_args, **_kwargs: next(responses))

    git._p22_reject_external_topology(
        source, SimpleNamespace(remaining=lambda _what: 5.0)
    )
