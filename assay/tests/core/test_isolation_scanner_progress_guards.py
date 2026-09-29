"""B113/A-466: core loop guards (isolation.py tree parser, git.py pipe drain).

The scanner cases also kill their own target mutants textually under R2 (the
anchor stops matching), so those kills are not evidence that the guards work.
"""

import ast
import contextlib
import os
import signal
import subprocess
import sys
import threading

import pytest
from scanner_progress_support import (
    SRC,
    assert_case_is_refused,
    assert_cases_belong,
    assert_ordinary_input_terminates,
)

from assay import git, isolation
from assay.adapters.python import PythonAdapter
from assay.errors import AssayError, ReasonCode

CASES = [
    ("iso-1388", "isolation.py", "_parse_tree", "space == -1", "python:compare-swap", "Eq->NotEq", 0,
     lambda m: m._parse_tree(b"a\x00" + b"\x01" * 20 + b"b\x00" + b"\x01" * 20, "t")),
    ("iso-1392", "isolation.py", "_parse_tree", "nul == -1", "python:compare-swap", "Eq->NotEq", 0,
     lambda m: m._parse_tree(b"100644 aaaaaaaaaaaaaaaaaaaa bbbb", "t")),
]


@pytest.mark.parametrize("case", CASES, ids=[case[0] for case in CASES])
def test_a_stalled_scanner_mutant_is_refused_not_spun(case, monkeypatch):
    assert_case_is_refused(case, monkeypatch)


def test_core_cases_name_only_core_sources():
    assert_cases_belong(CASES, {"isolation.py"})


def test_every_guarded_function_still_terminates_on_ordinary_input():
    assert_ordinary_input_terminates(CASES, {"isolation.py": isolation}, (AssayError,))
    with pytest.raises(AssayError) as refused:
        CASES[0][7](isolation)
    assert refused.value.reason_code is ReasonCode.GIT_FAILED


def test_the_git_drain_loop_offers_no_mutation_site_in_its_exit_test():
    text = (SRC / "git.py").read_text(encoding="utf-8")
    (function,) = [n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef) and n.name == "_run_bounded"]
    lines = text.splitlines()
    span = range(function.lineno, function.end_lineno + 1)
    (loop,) = [n for n in span if "while selector.get_map():" in lines[n - 1]]
    (overflow,) = [n for n in span if lines[n - 1].strip() == "if overflowed:"]
    sites = PythonAdapter().generate_mutation_sites(
        text,
        {loop, overflow},
        operators=("python:compare-swap", "python:boolop-swap", "python:falsy-swap", "python:bool-const-flip"),
        limit=1000,
    )
    assert list(sites) == []


def test_the_git_drain_stops_after_an_output_overflow_even_without_a_deadline(monkeypatch):
    started: list[subprocess.Popen] = []
    killed: list[int] = []
    real_popen = subprocess.Popen
    real_kill = git._kill_owned_group

    def recording_kill(proc):
        killed.append(proc.pid)
        real_kill(proc)

    def recording_popen(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        real_wait = proc.wait

        def wait_only_after_the_group_kill(*a, **k):
            # The overflowing child never exits by itself: waiting on it before
            # its group is killed deadlocks on the full pipe and would sit idle
            # until the failsafe below (git.py wait-loop mutants). Refuse at once.
            if proc.pid not in killed:
                raise AssertionError("waited on the overflowing child before killing its group")
            return real_wait(*a, **k)

        proc.wait = wait_only_after_the_group_kill
        started.append(proc)
        return proc

    monkeypatch.setattr(git.subprocess, "Popen", recording_popen)
    monkeypatch.setattr(git, "_kill_owned_group", recording_kill)
    monkeypatch.setattr(git, "MAX_GIT_OUTPUT_BYTES", 4)  # overflow on the first chunk: minimal work before the verdict
    outcome: list[BaseException] = []

    def worker() -> None:
        try:
            git._run_bounded(
                [sys.executable, "-c", "import sys\nwhile True: sys.stdout.buffer.write(b'x' * 65536)"],
                remaining=None,
            )
        except BaseException as exc:  # recorded and asserted below
            outcome.append(exc)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        thread.join(timeout=120.0)  # failsafe only; never decides pass/fail for a run that returns
        if thread.is_alive():
            pytest.fail("the drain did not stop after an output overflow; the child group was killed")
        assert len(outcome) == 1 and isinstance(outcome[0], AssayError), outcome
        assert outcome[0].reason_code is ReasonCode.GIT_FAILED
        assert "standard output" in str(outcome[0])
        (child,) = started
        with pytest.raises(ProcessLookupError):
            os.killpg(child.pid, 0)
    finally:
        for proc in started:
            if proc.poll() is None:  # only a leader that is still ours and unreaped
                killed.append(proc.pid)
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(proc.pid, signal.SIGKILL)
        thread.join(timeout=60.0)
        if not thread.is_alive():
            for proc in started:
                proc.stdout.close()
                proc.stderr.close()
                proc.wait(timeout=60.0)
