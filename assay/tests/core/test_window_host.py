"""PR1a: command windows cross one isolated Assay host per candidate."""

from __future__ import annotations

from concurrent.futures import Future
import json
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from conftest import (
    GitRepo,
    completed_process,
    make_deadline,
    make_lane,
    make_plan,
    prepared_snapshot,
    process_tree_evidence_dict,
)

from assay import _window_host, mutation, runner
from assay.adapters.python import PythonAdapter
from assay.errors import Outcome, ReasonCode
from assay.mutation import MutationExecution, MutationTarget, MutationWitnessReceipt
from assay.process_tree import ProcessTreeEvidence, ProcessTreeProcessRunner
from assay.resource_limits import CounterDelta, ResourceLimitEvidence
from assay.runner import CommandResult, execute_command
from assay.termination import Termination


_ZERO_LIMITS = ResourceLimitEvidence(
    pids_events_max=CounterDelta(before=0, after=0, delta=0),
    memory_events_max=CounterDelta(before=0, after=0, delta=0),
    memory_events_oom=CounterDelta(before=0, after=0, delta=0),
    memory_events_oom_kill=CounterDelta(before=0, after=0, delta=0),
    memory_events_oom_group_kill=CounterDelta(before=0, after=0, delta=0),
)


def _plan(argv: tuple[str, ...]):
    return make_plan(make_lane(argv=argv))


def _command_result(
    plan,
    *,
    outcome: Outcome,
    reason_code: ReasonCode | None,
    returncode: int | None,
    leader_status: str = "exited",
    termination: Termination | None = None,
) -> CommandResult:
    return CommandResult(
        plan=plan,
        outcome=outcome,
        reason_code=reason_code,
        returncode=returncode,
        stdout_tail="candidate stdout",
        stderr_tail="candidate stderr",
        stdout_dropped_bytes=4,
        stderr_dropped_bytes=5,
        started="2026-10-30T00:00:00+00:00",
        ended="2026-10-30T00:00:01+00:00",
        liveness_resource_evidence={"pressure": {"some": 1}},
        process_tree_evidence=process_tree_evidence_dict(
            leader_status=leader_status,
        ),
        termination=termination,
        decided_by_report=outcome is Outcome.FAIL,
    )


@pytest.mark.parametrize(
    ("bucket", "outcome", "reason_code", "returncode", "leader_status", "expected"),
    [
        ("killed", Outcome.FAIL, ReasonCode.COMMAND_FAILED, 1, "exited", "killed"),
        ("survived", Outcome.PASS, None, 0, "exited", "survived"),
        ("crashed", Outcome.ERROR, ReasonCode.EXEC_FAILED, None, "not-started", "crashed"),
        (
            "budget_exceeded",
            Outcome.BUDGET_EXCEEDED,
            ReasonCode.LANE_TIMEOUT,
            None,
            "timeout-killed",
            "budget_exceeded",
        ),
        (
            "hung",
            Outcome.BUDGET_EXCEEDED,
            ReasonCode.CANDIDATE_HUNG,
            None,
            "timeout-killed",
            "hung",
        ),
        ("equivalent", Outcome.PASS, None, 0, "exited", "equivalent"),
    ],
)
def test_host_result_protocol_preserves_mutation_bucket_inputs(
    bucket,
    outcome,
    reason_code,
    returncode,
    leader_status,
    expected,
):
    """The host wire round-trip preserves the old classifier's full inputs."""
    plan = _plan((sys.executable, "-c", "pass"))
    result = _command_result(
        plan,
        outcome=outcome,
        reason_code=reason_code,
        returncode=returncode,
        leader_status=leader_status,
        termination={
            "budget_exceeded": Termination.TIMED_OUT,
            "hung": Termination.HUNG,
        }.get(bucket),
    )
    restored = _window_host._command_result_from_payload(
        plan,
        _window_host._command_result_to_payload(result),
    )
    assert restored.outcome is result.outcome
    assert restored.reason_code is result.reason_code
    assert restored.returncode == result.returncode
    assert restored.stdout_tail == result.stdout_tail
    assert restored.stderr_tail == result.stderr_tail
    assert restored.stdout_dropped_bytes == result.stdout_dropped_bytes
    assert restored.stderr_dropped_bytes == result.stderr_dropped_bytes
    assert restored.liveness_resource_evidence == result.liveness_resource_evidence
    assert restored.process_tree_evidence == result.process_tree_evidence
    assert restored.termination is result.termination
    assert restored.decided_by_report is result.decided_by_report

    process_tree = ProcessTreeEvidence.from_dict(restored.process_tree_evidence)
    witness = (
        MutationWitnessReceipt(
            node_id="test_case::failed_call",
            when="call",
            outcome="failed",
            session_exit_status=1,
            process_exit_status=1,
        )
        if bucket == "killed"
        else None
    )
    run = mutation._MutantRun(
        result=restored,
        equivalence_bytes=b"same" if bucket == "equivalent" else b"different",
        execution=MutationExecution(mode="full", witness=witness),
        resource_limit_evidence=_ZERO_LIMITS,
        process_tree_evidence=process_tree,
    )
    assert mutation._bucket_for_run(
        run,
        equivalence_artifact="result.json" if bucket == "equivalent" else None,
        baseline_equivalence=b"same" if bucket == "equivalent" else None,
        kill_signal_artifact=None,
    ) == expected


def test_window_host_runs_a_real_command_and_returns_process_tree_evidence(tmp_path):
    plan = _plan((sys.executable, "-c", "print('window host')"))

    result = _window_host.run_window(
        plan,
        cwd=tmp_path,
        timeout=5.0,
        process_runner=ProcessTreeProcessRunner(),
        clock=runner._utc_now,
    )

    assert result.outcome is Outcome.PASS
    assert result.returncode == 0
    assert "window host" in (result.stdout_tail or "")
    assert ProcessTreeEvidence.from_dict(result.process_tree_evidence).clean


def test_candidate_budget_kills_the_child_tree_inside_the_window_host(tmp_path):
    child = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']); time.sleep(60)"
    plan = _plan((sys.executable, "-c", child))

    result = _window_host.run_window(
        plan,
        cwd=tmp_path,
        timeout=0.1,
        process_runner=ProcessTreeProcessRunner(),
        clock=runner._utc_now,
    )

    assert result.outcome is Outcome.BUDGET_EXCEEDED
    evidence = ProcessTreeEvidence.from_dict(result.process_tree_evidence)
    assert evidence.leader_status == "timeout-killed"
    assert evidence.clean
    assert evidence.outliving_descendants == evidence.descendants_signalled == 1


def test_abrupt_window_host_exit_becomes_crashed_with_exec_failed_cause(
    monkeypatch, tmp_path
):
    def crash_host(*_args, **_kwargs):
        return -signal.SIGSEGV, b""

    monkeypatch.setattr(_window_host, "_launch_host_process", crash_host)
    plan = _plan((sys.executable, "-c", "pass"))

    result = _window_host.run_window(
        plan,
        cwd=tmp_path,
        timeout=5.0,
        process_runner=ProcessTreeProcessRunner(),
        clock=runner._utc_now,
    )

    assert result.outcome is Outcome.ERROR
    assert result.reason_code is ReasonCode.EXEC_FAILED
    assert ProcessTreeEvidence.from_dict(result.process_tree_evidence).leader_status == "not-started"


def test_keyboard_interrupt_terminates_and_waits_for_the_window_host(
    monkeypatch, tmp_path
):
    class InterruptedHost:
        pid = 987654
        returncode = None

        def communicate(self, **_kwargs):
            raise KeyboardInterrupt

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            self.returncode = -signal.SIGTERM
            self.waited = True
            return self.returncode

    child = InterruptedHost()
    monkeypatch.setattr(_window_host.subprocess, "Popen", lambda *_a, **_kw: child)

    with pytest.raises(KeyboardInterrupt):
        _window_host._launch_host_process(
            command=(sys.executable, "-I", "-c", "pass"),
            request=b"{}",
            result_fd=4,
            pass_fds=(4,),
            timeout=5.0,
        )

    assert child.terminated
    assert child.waited


class _ImmediateExecutor:
    def __init__(self, _jobs: int) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def submit(self, function, *args):
        future = Future()
        try:
            future.set_result(function(*args))
        except BaseException as exc:
            future.set_exception(exc)
        return future


_FLAGS = "def flags():\n    value = True\n    return value\n"
_TARGETS = (MutationTarget(path="pkg/flags.py", text=_FLAGS, lines=frozenset({2})),)


def _mutation_result(plan, *, outcome: Outcome, reason_code, returncode, leader="exited"):
    return CommandResult(
        plan=plan,
        outcome=outcome,
        reason_code=reason_code,
        returncode=returncode,
        started="2026-10-30T00:00:00+00:00",
        ended="2026-10-30T00:00:01+00:00",
        process_tree_evidence=process_tree_evidence_dict(leader_status=leader),
    )


def _run_with_scripted_host(
    tmp_path: Path,
    monkeypatch,
    *,
    state_root: Path,
    resume: bool,
    host_results: list[CommandResult],
):
    counters = mutation.ResourceLimitCounters(
        pids_max=0,
        memory_oom=0,
        memory_max=0,
        memory_oom_kill=0,
        memory_oom_group_kill=0,
    )
    monkeypatch.setattr(mutation, "_read_candidate_resource_counters", lambda: counters)
    calls = []

    def scripted_host(plan, **_kwargs):
        calls.append(plan)
        return host_results.pop(0)

    monkeypatch.setattr(_window_host, "run_window", scripted_host)
    repo = GitRepo(path=tmp_path / "repo")
    repo.path.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "assay-tests@example.com")
    repo.git("config", "user.name", "assay tests")
    repo.write("pkg/flags.py", _FLAGS)
    repo.commit_all("seed window-host mutation")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    lane = make_lane(argv=(sys.executable, "-c", "pass"))
    baseline = execute_command(
        lane,
        cwd=repo.path,
        process_runner=lambda argv, **_kwargs: completed_process(list(argv), 0, "", ""),
    )
    process_runner = ProcessTreeProcessRunner()
    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        result = mutation._run_mutation_impl(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(),
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=10,
            operators=("python:bool-const-flip",),
            process_runner=process_runner,
            clock=runner._utc_now,
            executor_factory=_ImmediateExecutor,
            state_root=state_root,
            resume=resume,
        )
    assert result is not None
    return result, calls


def test_jobs_one_mutation_uses_window_host_and_retry_uses_it_again(
    monkeypatch, tmp_path
):
    state_root = tmp_path / "state"
    host_results = [
        _mutation_result(
            make_plan(make_lane(argv=(sys.executable, "-c", "pass"))),
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.EXEC_FAILED,
            returncode=None,
            leader="not-started",
        ),
        _mutation_result(
            make_plan(make_lane(argv=(sys.executable, "-c", "pass"))),
            outcome=Outcome.PASS,
            reason_code=None,
            returncode=0,
        ),
    ]

    result, calls = _run_with_scripted_host(
        tmp_path,
        monkeypatch,
        state_root=state_root,
        resume=True,
        host_results=host_results,
    )

    assert len(calls) == 2
    assert len(result.survived) == 1
    assert result.crashed == ()


def test_resume_reuses_host_path_candidate_without_launching_another_host(
    monkeypatch, tmp_path
):
    state_root = tmp_path / "state"
    plan = make_plan(make_lane(argv=(sys.executable, "-c", "pass")))
    first, first_calls = _run_with_scripted_host(
        tmp_path,
        monkeypatch,
        state_root=state_root,
        resume=True,
        host_results=[
            _mutation_result(
                plan,
                outcome=Outcome.PASS,
                reason_code=None,
                returncode=0,
            )
        ],
    )
    assert len(first.survived) == 1
    assert len(first_calls) == 1

    second, second_calls = _run_with_scripted_host(
        tmp_path,
        monkeypatch,
        state_root=state_root,
        resume=True,
        host_results=[],
    )
    assert len(second.survived) == 1
    assert second_calls == []
