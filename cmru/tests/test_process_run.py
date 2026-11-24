"""Behavioural contract for CMRU's shared process-run seam."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from cmru import delegated, doctor, process_run

pytestmark = pytest.mark.requires_contained_cgroup


def test_captured_probe_keeps_stdout_and_stderr_separate_and_strictly_decoded(tmp_path):
    result = process_run.run_process(
        [
            sys.executable,
            "-c",
            "import sys; print('out'); print('err', file=sys.stderr)",
        ],
        cwd=tmp_path,
    )

    assert result.returncode == 0
    assert result.stdout == "out\n"
    assert result.stderr == "err\n"


def test_captured_probe_keeps_strict_text_decoding(tmp_path):
    with pytest.raises(UnicodeDecodeError):
        process_run.run_process(
            [
                sys.executable,
                "-c",
                "import sys; sys.stdout.buffer.write(b'ok \\xff end\\n')",
            ],
            cwd=tmp_path,
        )


def test_captured_probe_text_false_returns_bytes(tmp_path):
    result = process_run.run_process(
        [sys.executable, "-c", "import sys; print('bytes')"],
        cwd=tmp_path,
        text=False,
    )

    assert result.returncode == 0
    assert result.stdout == b"bytes\n"
    assert result.stderr == b""


def test_captured_probe_check_raises_with_the_child_output(tmp_path):
    argv = [
        sys.executable,
        "-c",
        "import sys; print('partial'); print('bad', file=sys.stderr); raise SystemExit(7)",
    ]
    with pytest.raises(subprocess.CalledProcessError) as failed:
        process_run.run_process(argv, cwd=tmp_path, check=True)

    assert failed.value.returncode == 7
    assert failed.value.cmd == argv
    assert failed.value.stdout == "partial\n"
    assert failed.value.stderr == "bad\n"


def test_captured_probe_uses_the_requested_cwd_and_environment(tmp_path):
    environment = {**os.environ, "CMRU_PROCESS_VALUE": "scoped"}
    result = process_run.run_process(
        [
            sys.executable,
            "-c",
            "import os; print(os.getcwd()); print(os.environ['CMRU_PROCESS_VALUE'])",
        ],
        cwd=tmp_path,
        env=environment,
    )

    assert result.stdout == f"{tmp_path}\nscoped\n"


def test_uncaptured_probe_inherits_streams_and_returns_a_completed_process(tmp_path):
    result = process_run.run_process(
        [sys.executable, "-c", "print('inherited')"],
        cwd=tmp_path,
        capture=False,
    )

    assert result.returncode == 0
    assert result.stdout is None and result.stderr is None


def test_doctor_process_probe_returns_the_real_result(tmp_path):
    result = doctor._run(
        [sys.executable, "-c", "print('doctor-ok')"],
        timeout=120,
        cwd=tmp_path,
    )

    assert result.returncode == 0
    assert result.stdout == "doctor-ok\n"


def test_delegated_process_runs_the_real_child_and_returns_its_status(tmp_path):
    result = delegated._run(
        [sys.executable, "-c", "print('delegated'); raise SystemExit(3)"],
        cwd=tmp_path,
    )

    assert result == 3
