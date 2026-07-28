"""Remaining fail-closed boundaries for the privileged action executor."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from topos.actions import execute
from topos.actions.execute import AuditIdentity, ExecuteResult, execute_plan, execute_update


def _success(argv: tuple[str, ...], *, timeout: float) -> ExecuteResult:
    return ExecuteResult("", "", argv, 0, "ok", "", "success", 0.0)


def _execute(tmp_path: Path, **overrides: object) -> ExecuteResult:
    options: dict[str, object] = {
        "admin": True,
        "confirm": "EXECUTE",
        "audit_path": tmp_path / "audit.jsonl",
        "identity": lambda: AuditIdentity(0, "tester"),
        "root_check": lambda: True,
        "runner": _success,
        "clock": lambda: 1.0,
    }
    options.update(overrides)
    return execute_plan("docker-start", "demo", **options)


class _Stream:
    closed = False

    def close(self) -> None:
        self.closed = True

    def fileno(self) -> int:
        return 11


def test_drain_timeout_breaks_after_child_exit_and_retries_final_reap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A timed-out child that has exited is not polled forever and is reaped."""

    class Process:
        stdout = _Stream()
        stderr = _Stream()
        killed = False
        waits = 0

        def poll(self) -> int | None:
            return 137 if self.killed else None

        def kill(self) -> None:
            self.killed = True

        def wait(self, *, timeout: float) -> int:
            self.waits += 1
            if self.waits == 1:
                raise subprocess.TimeoutExpired(["fake"], timeout)
            return 137

    class Selector:
        def get_map(self) -> dict[int, object]:
            return {1: object()}

        def select(self, timeout: float) -> list[object]:
            return []

        def close(self) -> None:
            pass

        def register(self, *_args: object) -> None:
            pass

    process = Process()
    times = iter((0.0, 2.0, 2.0))
    monkeypatch.setattr(execute.selectors, "DefaultSelector", Selector)
    monkeypatch.setattr(execute.time, "monotonic", lambda: next(times))

    assert execute._drain_process(process, 1.0) == (b"", b"", True)
    assert process.killed is True
    assert process.waits == 2


def test_drain_allows_a_process_without_captured_pipes(monkeypatch: pytest.MonkeyPatch) -> None:
    """A runner with no readable pipes is a valid empty-output process."""

    class Process:
        stdout = None
        stderr = None

        def poll(self) -> int:
            return 0

        def wait(self, *, timeout: float) -> int:
            return 0

        def kill(self) -> None:
            raise AssertionError("an exited process must not be killed")

    assert execute._drain_process(Process(), 1.0) == (b"", b"", False)


def test_preview_build_failure_refuses_before_audit_or_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A preview builder exception cannot open an audit record or mutate state."""
    called: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        execute,
        "build_preview",
        lambda *_args: (_ for _ in ()).throw(ValueError("catalog unavailable")),
    )

    result = _execute(tmp_path, runner=lambda argv, *, timeout: called.append(argv) or _success(argv, timeout=timeout))

    assert result.outcome == result.action_outcome == "refusal"
    assert result.stderr == "catalog unavailable"
    assert called == []
    assert not (tmp_path / "audit.jsonl").exists()


def test_elapsed_clock_failure_preserves_audited_success(tmp_path: Path) -> None:
    """An unavailable elapsed-time read cannot turn a completed action into a crash."""
    samples: list[float | BaseException] = [10.0, 11.0, RuntimeError("clock down"), 12.0]

    def clock() -> float:
        value = samples.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    result = _execute(tmp_path, clock=clock)

    assert (result.outcome, result.action_outcome, result.duration_s) == (
        "success",
        "success",
        0.0,
    )


def test_revalidation_failure_is_audited_refusal_without_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A target that becomes invalid after pre-audit is not executed."""
    real_validate = execute.validate_target
    validations = 0
    called: list[tuple[str, ...]] = []

    def validate_once_then_reject(kind: object, target: object) -> None:
        nonlocal validations
        validations += 1
        if validations == 2:
            raise ValueError("target changed before execution")
        real_validate(kind, target)

    monkeypatch.setattr(execute, "validate_target", validate_once_then_reject)
    result = _execute(
        tmp_path,
        runner=lambda argv, *, timeout: called.append(argv) or _success(argv, timeout=timeout),
    )

    assert result.outcome == result.action_outcome == "refusal"
    assert result.stderr == "target changed before execution"
    assert called == []
    assert len((tmp_path / "audit.jsonl").read_text().splitlines()) == 2


def test_post_audit_close_failure_remains_a_typed_audit_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A handle-close error after a failed post audit never escapes the public API."""

    class Handle:
        def close(self) -> None:
            raise OSError("close failed")

    monkeypatch.setattr(execute, "_write_execution_audit_pre", lambda *_args, **_kwargs: Handle())
    monkeypatch.setattr(
        execute,
        "_write_execution_audit_post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("write failed")),
    )

    result = _execute(tmp_path)

    assert (result.outcome, result.audit_outcome, result.audit_error) == (
        "audit_failure",
        "post_failure",
        "write failed",
    )


def test_default_current_value_reader_delegates_to_governance_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The injectable stale-read seam retains the governance reader contract."""
    from topos.actions import governance

    monkeypatch.setattr(governance, "_systemctl_show_reader", lambda unit: "4096")
    assert execute._default_current_value_reader("demo.service") == "4096"


def test_update_refuses_when_current_memory_reader_fails(tmp_path: Path) -> None:
    """A failed current-usage read cannot authorize a potentially OOM update."""
    called: list[tuple[str, ...]] = []

    def unavailable_reader(target: str) -> int:
        raise OSError("docker unavailable")

    result = execute_update(
        "demo",
        memory="1024",
        admin=True,
        confirm="UPDATE",
        audit_path=tmp_path / "audit.jsonl",
        identity=lambda: AuditIdentity(0, "tester"),
        root_check=lambda: True,
        runner=lambda argv, *, timeout: called.append(argv) or _success(argv, timeout=timeout),
        current_memory_reader=unavailable_reader,
    )

    assert result.outcome == result.action_outcome == "refusal"
    assert "could not be established" in result.stderr
    assert called == []
    assert not (tmp_path / "audit.jsonl").exists()
