"""Contract tests for CMRU's shared subprocess keyword seam."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from cmru import process_run
from cmru import delegated, doctor


_DEFAULT = object()


@pytest.mark.parametrize(
    ("parameter", "value", "expected"),
    [
        ("cwd", _DEFAULT, {"check": False, "capture_output": True, "text": True}),
        ("cwd", "/workspace", {
            "cwd": "/workspace", "check": False,
            "capture_output": True, "text": True,
        }),
        ("cwd", None, {"check": False, "capture_output": True, "text": True}),
        ("env", _DEFAULT, {"check": False, "capture_output": True, "text": True}),
        ("env", {"KEY": "value"}, {
            "env": {"KEY": "value"}, "check": False,
            "capture_output": True, "text": True,
        }),
        ("env", None, {"check": False, "capture_output": True, "text": True}),
        ("timeout", _DEFAULT, {"check": False, "capture_output": True, "text": True}),
        ("timeout", 2.5, {
            "timeout": 2.5, "check": False,
            "capture_output": True, "text": True,
        }),
        ("timeout", None, {"check": False, "capture_output": True, "text": True}),
        ("check", _DEFAULT, {"check": False, "capture_output": True, "text": True}),
        ("check", True, {"check": True, "capture_output": True, "text": True}),
        ("check", None, {"check": None, "capture_output": True, "text": True}),
        ("capture", _DEFAULT, {"check": False, "capture_output": True, "text": True}),
        ("capture", False, {"check": False, "text": True}),
        ("capture", None, {"check": False, "text": True}),
        ("text", _DEFAULT, {"check": False, "capture_output": True, "text": True}),
        ("text", False, {"check": False, "capture_output": True, "text": False}),
        ("text", None, {"check": False, "capture_output": True}),
        ("input", _DEFAULT, {"check": False, "capture_output": True, "text": True}),
        ("input", "payload", {
            "input": "payload", "check": False,
            "capture_output": True, "text": True,
        }),
        ("input", None, {"check": False, "capture_output": True, "text": True}),
    ],
)
def test_run_process_forwards_exact_subprocess_call(
    monkeypatch, parameter, value, expected,
):
    run = Mock()
    monkeypatch.setattr(process_run.subprocess, "run", run)
    options = {} if value is _DEFAULT else {parameter: value}

    process_run.run_process(("tool", "argument"), **options)

    run.assert_called_once_with(["tool", "argument"], **expected)
    assert "shell" not in run.call_args.kwargs


def test_run_process_copies_argv_to_a_list(monkeypatch):
    run = Mock()
    monkeypatch.setattr(process_run.subprocess, "run", run)
    argv = ["tool", "argument"]

    process_run.run_process(argv)

    assert run.call_args.args[0] == argv
    assert run.call_args.args[0] is not argv


@pytest.mark.parametrize(
    "exception",
    [
        subprocess.TimeoutExpired(["tool"], 1),
        subprocess.CalledProcessError(1, ["tool"]),
        FileNotFoundError("tool"),
    ],
)
def test_run_process_propagates_subprocess_exceptions_unchanged(
    monkeypatch, exception,
):
    monkeypatch.setattr(
        process_run.subprocess, "run", Mock(side_effect=exception),
    )

    with pytest.raises(type(exception)) as raised:
        process_run.run_process(["tool"])

    assert raised.value is exception


def test_run_process_returns_completed_process_unchanged(monkeypatch):
    completed = subprocess.CompletedProcess(["tool"], 0, stdout="out", stderr="err")
    run = Mock(return_value=completed)
    monkeypatch.setattr(process_run.subprocess, "run", run)

    result = process_run.run_process(["tool"])

    assert result is completed


def test_doctor_run_uses_shared_process_wrapper(monkeypatch):
    completed = subprocess.CompletedProcess(["tool"], 0, stdout="ok", stderr="")
    calls = []
    monkeypatch.setattr(
        doctor, "run_process",
        lambda argv, **kwargs: calls.append((argv, kwargs)) or completed,
    )

    result = doctor._run(["tool"], timeout=3)

    assert result is completed
    assert calls == [(
        ["tool"], {"capture": True, "text": True, "timeout": 3, "check": False},
    )]


def test_delegated_run_inherits_streams_and_returns_returncode(monkeypatch, capsys):
    completed = subprocess.CompletedProcess(["tool", "argument"], 7)
    calls = []
    monkeypatch.setattr(
        delegated, "run_process",
        lambda argv, **kwargs: calls.append((argv, kwargs)) or completed,
    )

    result = delegated._run(("tool", "argument"), cwd=Path("/work"))

    assert result == 7
    assert calls == [(
        ("tool", "argument"), {"cwd": Path("/work"), "capture": False},
    )]
    assert "external-tool: tool argument" in capsys.readouterr().out
