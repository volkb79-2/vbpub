"""Public execution contracts for defensive gate and runner failures.

These tests use the executor's injectable seams only: no Docker command, root
operation, or live service is started.  Each assertion observes the public
``ExecuteResult`` and durable audit shape rather than internal call counts.
"""

from __future__ import annotations

import json
from pathlib import Path

from topos.actions.execute import AuditIdentity, ExecuteResult, execute_plan


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


def test_root_probe_exception_refuses_before_runner_or_audit(tmp_path: Path) -> None:
    """An unavailable privilege probe is fail-closed, not an executor crash."""

    def unavailable_root_probe() -> bool:
        raise OSError("credential lookup unavailable")

    result = _execute(tmp_path, root_check=unavailable_root_probe)

    assert (result.outcome, result.action_outcome, result.stderr) == (
        "refusal",
        "refusal",
        "root privileges are required",
    )
    assert not (tmp_path / "audit.jsonl").exists()


def test_invalid_runner_value_becomes_audited_typed_failure(tmp_path: Path) -> None:
    """A runner cannot smuggle an arbitrary result shape past the gate."""

    result = _execute(tmp_path, runner=lambda *_args, **_kwargs: object())

    assert (result.outcome, result.action_outcome, result.returncode) == (
        "runner_failure",
        "runner_failure",
        None,
    )
    assert result.stderr == "runner returned an invalid result type"
    records = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert [record["stage"] for record in records] == ["pre", "post"]
    assert records[-1]["outcome"] == records[-1]["action_outcome"] == "runner_failure"


def test_unstable_elapsed_clock_preserves_success_with_safe_duration(tmp_path: Path) -> None:
    """A bad elapsed-time read cannot turn a successful action into a crash."""

    # pre-audit, start, elapsed, post-audit: only the start read is broken.
    samples: list[float | BaseException] = [
        10.0,
        RuntimeError("clock unavailable"),
        11.0,
        12.0,
    ]

    def clock() -> float:
        sample = samples.pop(0)
        if isinstance(sample, BaseException):
            raise sample
        return sample

    result = _execute(tmp_path, clock=clock)

    assert (result.outcome, result.action_outcome, result.duration_s) == (
        "success",
        "success",
        0.0,
    )
    records = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert records[-1]["duration_s"] == 0.0
