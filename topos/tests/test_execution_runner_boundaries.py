"""Regression boundaries for action runner normalization and execution."""

from __future__ import annotations

import math

import pytest

from topos.actions.execute import ExecuteResult, _default_runner, _normalise_runner_result
from topos.actions.preview import build_preview


def _result(**changes: object) -> ExecuteResult:
    base = ExecuteResult(
        kind="",
        target="",
        argv=("/usr/bin/docker", "start", "demo"),
        returncode=0,
        stdout="ok",
        stderr="",
        outcome="success",
        duration_s=0.1,
    )
    return base.__class__(**{**base.__dict__, **changes})


@pytest.mark.parametrize(
    "result, message",
    [
        (object(), "invalid result type"),
        (_result(argv=("/usr/bin/docker", "start", "other")), "does not match"),
        (_result(outcome="made-up"), "invalid outcome"),
        (_result(returncode=True), "invalid return code"),
        (_result(returncode=1), "successful runner result"),
        (_result(outcome="nonzero", returncode=0), "nonzero runner result"),
        (_result(outcome="timeout", returncode=1), "must not have a return code"),
        (_result(action_outcome="nonzero"), "inconsistent action outcome"),
        (_result(duration_s=math.nan), "invalid duration"),
        (_result(stdout=b"bytes"), "output must be text"),
    ],
)
def test_normalise_runner_result_rejects_untrusted_runner_shapes(result: object, message: str) -> None:
    plan = build_preview("docker-start", "demo")

    with pytest.raises(ValueError, match=message):
        _normalise_runner_result(result, plan)


def test_normalise_runner_result_canonicalises_identity_and_bounds_output() -> None:
    plan = build_preview("docker-start", "demo")
    raw = _result(kind=plan.kind.value, target=plan.target, stdout="x" * 5000, stderr="y" * 5000)

    normalized = _normalise_runner_result(raw, plan)

    assert normalized.kind == "docker-start"
    assert normalized.target == "demo"
    assert normalized.argv == plan.argv
    assert normalized.stdout.endswith(" ... (truncated)")
    assert normalized.stderr.endswith(" ... (truncated)")


def test_default_runner_classifies_success_and_nonzero_without_shell_interpolation() -> None:
    success = _default_runner(("/bin/sh", "-c", "printf out; printf err >&2"), timeout=1.0)
    failure = _default_runner(("/bin/sh", "-c", "printf bad >&2; exit 7"), timeout=1.0)

    assert (success.outcome, success.returncode, success.stdout, success.stderr) == (
        "success",
        0,
        "out",
        "err",
    )
    assert (failure.outcome, failure.returncode, failure.stderr) == ("nonzero", 7, "bad")


def test_default_runner_returns_typed_failure_when_executable_cannot_start() -> None:
    result = _default_runner(("/definitely/not/a/topos-command",), timeout=1.0)

    assert result.outcome == "runner_failure"
    assert result.returncode is None
    assert "FileNotFoundError" in result.stderr
