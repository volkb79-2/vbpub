"""Deterministic resilience contracts for the privileged action runner."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from topos.actions import execute


class _Stream:
    def __init__(self, fd: int) -> None:
        self.fd = fd
        self.closed = False

    def fileno(self) -> int:
        return self.fd

    def close(self) -> None:
        self.closed = True


class _Process:
    def __init__(self) -> None:
        self.stdout = _Stream(10)
        self.stderr = _Stream(11)
        self.killed = False
        self.wait_calls: list[float] = []

    def poll(self) -> int | None:
        return 137 if self.killed else None

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float) -> int:
        self.wait_calls.append(timeout)
        return 137


class _Selector:
    def __init__(self) -> None:
        self.registered: dict[object, str] = {}
        self.unregistered: list[object] = []
        self.closed = False

    def register(self, stream: object, _event: int, name: str) -> None:
        self.registered[stream] = name

    def get_map(self) -> dict[object, str]:
        return self.registered

    def unregister(self, stream: object) -> None:
        self.unregistered.append(stream)
        self.registered.pop(stream)

    def select(self, timeout: float):
        if timeout == 0:
            return [
                (SimpleNamespace(fileobj=stream, data=name), 1)
                for stream, name in tuple(self.registered.items())
            ]
        return []

    def close(self) -> None:
        self.closed = True


def test_drain_deadline_kills_child_and_closes_both_eof_streams(monkeypatch) -> None:
    """An expired deadline kills the child, then drains EOF without leaking fds."""
    process = _Process()
    selector = _Selector()
    monotonic_values = iter((0.0, 2.0, 2.0))

    monkeypatch.setattr(execute.selectors, "DefaultSelector", lambda: selector)
    monkeypatch.setattr(execute.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(
        execute.os,
        "read",
        lambda _fd, _size: (_ for _ in ()).throw(OSError("pipe unavailable")),
    )

    stdout, stderr, timed_out = execute._drain_process(process, timeout=1.0)

    assert (stdout, stderr, timed_out) == (b"", b"", True)
    assert process.killed is True
    assert process.wait_calls == [1.0]
    assert selector.unregistered == [process.stdout, process.stderr]
    assert process.stdout.closed is True
    assert process.stderr.closed is True
    assert selector.closed is True


def test_drain_selector_and_child_wait_errors_close_streams_before_reraising(
    monkeypatch,
) -> None:
    """Unexpected drain failures do not leave child pipes open when reaping fails."""
    process = _Process()
    selector = _Selector()

    def select_failure(_timeout: float):
        raise RuntimeError("selector broken")

    selector.select = select_failure  # type: ignore[method-assign]

    def wait_failure(*, timeout: float) -> int:
        process.wait_calls.append(timeout)
        raise OSError("wait unavailable")

    process.wait = wait_failure  # type: ignore[method-assign]
    monkeypatch.setattr(execute.selectors, "DefaultSelector", lambda: selector)

    with pytest.raises(RuntimeError, match="selector broken"):
        execute._drain_process(process, timeout=1.0)

    assert process.killed is True
    assert process.wait_calls == [1.0]
    assert process.stdout.closed is True
    assert process.stderr.closed is True
    assert selector.closed is True


@pytest.mark.parametrize(
    ("failure", "expected_outcome", "expected_error"),
    [
        (OSError("permission denied"), "runner_failure", "OSError: permission denied"),
        (
            subprocess.TimeoutExpired(("/usr/bin/docker", "start", "demo"), 1.0),
            "timeout",
            "",
        ),
    ],
)
def test_default_runner_converts_spawn_and_drain_exceptions_to_typed_results(
    monkeypatch, failure: BaseException, expected_outcome: str, expected_error: str
) -> None:
    """The runner never exposes raw spawn/drain exceptions to action callers."""
    if isinstance(failure, OSError):
        monkeypatch.setattr(
            execute.subprocess,
            "Popen",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
        )
    else:
        monkeypatch.setattr(execute.subprocess, "Popen", lambda *_args, **_kwargs: object())
        monkeypatch.setattr(
            execute,
            "_drain_process",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
        )

    result = execute._default_runner(("/usr/bin/docker", "start", "demo"), timeout=1.0)

    assert result.outcome == expected_outcome
    assert result.returncode is None
    assert result.stderr == expected_error
    assert result.argv == ("/usr/bin/docker", "start", "demo")
