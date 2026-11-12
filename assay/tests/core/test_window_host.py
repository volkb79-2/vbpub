"""PR1a: command windows cross one isolated Assay host per candidate."""

from __future__ import annotations

from concurrent.futures import Future
import io
import json
import math
import os
import runpy
import signal
import sys
from types import ModuleType
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

from assay import _window_host, cli, liveness, mutation, runner
from assay.adapters.python import PythonAdapter
from assay.errors import AssayError, Outcome, ReasonCode
from assay.mutation import MutationExecution, MutationTarget, MutationWitnessReceipt
from assay import process_tree as process_tree_module
from assay.process_tree import (
    ProcessTreeDescendant,
    ProcessTreeEvidence,
    ProcessTreeProcessRunner,
)
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
    original_run = mutation._MutantRun(
        result=result,
        equivalence_bytes=b"same" if bucket == "equivalent" else b"different",
        execution=MutationExecution(mode="full", witness=witness),
        resource_limit_evidence=_ZERO_LIMITS,
        process_tree_evidence=ProcessTreeEvidence.from_dict(
            result.process_tree_evidence
        ),
    )
    run = mutation._MutantRun(
        result=restored,
        equivalence_bytes=b"same" if bucket == "equivalent" else b"different",
        execution=MutationExecution(mode="full", witness=witness),
        resource_limit_evidence=_ZERO_LIMITS,
        process_tree_evidence=process_tree,
    )
    host_bucket = mutation._bucket_for_run(
        run,
        equivalence_artifact="result.json" if bucket == "equivalent" else None,
        baseline_equivalence=b"same" if bucket == "equivalent" else None,
        kill_signal_artifact=None,
    )
    in_process_bucket = mutation._bucket_for_run(
        original_run,
        equivalence_artifact="result.json" if bucket == "equivalent" else None,
        baseline_equivalence=b"same" if bucket == "equivalent" else None,
        kill_signal_artifact=None,
    )
    assert host_bucket == in_process_bucket == expected


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
    assert result.stdout_tail is None
    assert ProcessTreeEvidence.from_dict(result.process_tree_evidence).clean


@pytest.mark.parametrize(
    ("returncode", "underlying"),
    [(0, Outcome.PASS), (1, Outcome.FAIL)],
)
def test_window_host_preserves_a_completed_outcome_beneath_a_descendant_leak(
    tmp_path, returncode, underlying
):
    """The isolated protocol retains the command result when its leader exited.

    The candidate waits for its detached child to acknowledge ``setsid`` before
    exiting, so the process-tree window observes a real outliving descendant.
    """
    child = (
        "import os,sys,time; os.setsid(); os.write(int(sys.argv[1]), b'x'); "
        "time.sleep(60)"
    )
    parent = (
        "import os,subprocess,sys; r,w=os.pipe(); "
        f"child={child!r}; "
        "subprocess.Popen([sys.executable,'-c',child,str(w)], pass_fds=(w,), "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
        "stderr=subprocess.DEVNULL); "
        "os.close(w); assert os.read(r,1)==b'x'; os.close(r); "
        "sys.exit(int(sys.argv[1]))"
    )
    result = _window_host.run_window(
        _plan((sys.executable, "-c", parent, str(returncode))),
        cwd=tmp_path,
        timeout=math.inf,
        process_runner=ProcessTreeProcessRunner(),
        clock=runner._utc_now,
    )

    evidence = ProcessTreeEvidence.from_dict(result.process_tree_evidence)
    assert result.outcome is Outcome.ERROR
    assert result.reason_code is ReasonCode.EXEC_FAILED
    assert evidence.leader_status == "exited"
    assert evidence.outliving_descendants == 1
    assert evidence.clean
    assert result.underlying_outcome is not None
    assert result.underlying_outcome.outcome is underlying
    assert result.underlying_outcome.returncode == returncode

    run = mutation._MutantRun(
        result=result,
        resource_limit_evidence=_ZERO_LIMITS,
        process_tree_evidence=evidence,
    )
    assert mutation._bucket_for_run(
        run,
        equivalence_artifact=None,
        baseline_equivalence=None,
        kill_signal_artifact=None,
    ) == "crashed"


def test_window_host_cleanup_does_not_reap_an_unrelated_parent_child(tmp_path):
    unrelated = _window_host.subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"]
    )
    try:
        result = _window_host.run_window(
            _plan((sys.executable, "-c", "pass")),
            cwd=tmp_path,
            timeout=5.0,
            process_runner=ProcessTreeProcessRunner(),
            clock=runner._utc_now,
        )
        assert result.outcome is Outcome.PASS
        assert unrelated.poll() is None
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=2.0)


def test_worker_entry_point_executes_one_json_request_on_fd_three(tmp_path):
    plan = _plan((sys.executable, "-c", "pass"))
    request = {
        "protocol": _window_host._PROTOCOL,
        "plan": _window_host._command_plan_to_payload(plan),
        "cwd": str(tmp_path),
        "timeout": 5.0,
        "runner": ProcessTreeProcessRunner().assay_window_host_spec(),
    }
    with _window_host.tempfile.TemporaryFile(mode="w+b") as result_file:
        status = _window_host.main(
            io.BytesIO(json.dumps(request).encode("utf-8")),
            result_fd=result_file.fileno(),
        )
        result_file.seek(0)
        response = json.loads(result_file.read())

    assert status == 0
    assert response["protocol"] == _window_host._PROTOCOL
    assert response["kind"] == "result"
    assert response["result"]["outcome"] == "PASS"


def test_worker_entry_point_reports_invalid_requests_as_protocol_errors():
    with _window_host.tempfile.TemporaryFile(mode="w+b") as result_file:
        status = _window_host.main(io.BytesIO(b"not json"), result_fd=result_file.fileno())
        result_file.seek(0)
        response = json.loads(result_file.read())

    assert status == 1
    assert response["kind"] == "error"
    assert response["error_type"] == "JSONDecodeError"


def test_liveness_window_host_keeps_the_spawn_time_sidecar_and_no_measured_numbers(tmp_path):
    process_runner = liveness.LivenessRunner(
        events_dir=tmp_path / "events",
        expect_next_event_within_s=1.0,
        cpu_window_s=0.1,
    )
    plan = _plan((sys.executable, "-c", "pass"))

    result = _window_host.run_window(
        plan,
        cwd=tmp_path,
        timeout=5.0,
        process_runner=process_runner,
        clock=runner._utc_now,
    )

    assert result.outcome is Outcome.PASS
    events_path = process_runner._events_path_for_cwd(tmp_path)
    metrics = liveness.read_resource_sidecar(events_path)
    assert metrics is not None
    assert set(metrics) == {"format", "spawned_at"}
    startup = mutation._startup_seconds(events_path)
    assert set(startup) == {"to_session_start", "to_first_test"}


def test_the_host_opens_the_window_and_returns_the_golden_profile(tmp_path):
    """B206 over the real host protocol: the worker (a separate process, whose
    environment holds only PATH) gets the socket path in its request, opens the
    window on the candidate root, closes it before the reap, and the exchange's
    result comes back beside the command result."""
    from assay import observer_windows as ow
    from observer_support import FakeObserver, golden, golden_handler

    config = ow.WindowConfig(str(tmp_path / "o.sock"), "cgroup", 250, None)
    with FakeObserver(tmp_path / "o.sock", golden_handler("close-cgroup")) as observer:
        with ow.activate(config) as slot:
            result = _window_host.run_window(
                _plan((sys.executable, "-c", "pass")),
                cwd=tmp_path,
                timeout=30.0,
                process_runner=ProcessTreeProcessRunner(),
                clock=runner._utc_now,
                profile=slot,
            )
    assert result.outcome is Outcome.PASS
    assert [r["verb"] for r in observer.requests] == ["window-open", "window-close"]
    assert slot.result is not None and slot.result.reply == golden("close-cgroup")["response"]
    assert slot.result.asked is True


def test_the_host_reports_an_absent_observer_as_a_reason_and_still_runs_the_command(tmp_path):
    from assay import observer_windows as ow

    config = ow.WindowConfig(str(tmp_path / "absent.sock"), "cgroup", 250, None)
    with ow.activate(config) as slot:
        result = _window_host.run_window(
            _plan((sys.executable, "-c", "pass")),
            cwd=tmp_path,
            timeout=30.0,
            process_runner=ProcessTreeProcessRunner(),
            clock=runner._utc_now,
            profile=slot,
        )
    assert result.outcome is Outcome.PASS
    assert slot.result is not None
    assert slot.result.reason == ow.OBSERVER_UNAVAILABLE and slot.result.asked is True


def test_a_malformed_profile_record_never_changes_the_command_result(monkeypatch, tmp_path):
    from assay import observer_windows as ow

    def refuse(cls, value):
        raise ValueError("malformed")

    monkeypatch.setattr(ow.WindowResult, "from_wire", classmethod(refuse))
    config = ow.WindowConfig(str(tmp_path / "absent.sock"), "cgroup", 250, None)
    with ow.activate(config) as slot:
        result = _window_host.run_window(
            _plan((sys.executable, "-c", "pass")),
            cwd=tmp_path,
            timeout=30.0,
            process_runner=ProcessTreeProcessRunner(),
            clock=runner._utc_now,
            profile=slot,
        )
    assert result.outcome is Outcome.PASS
    assert slot.result is not None and slot.result.reason == ow.OBSERVER_ERROR


def test_the_host_request_shape_is_strict_but_the_profile_key_is_optional(tmp_path):
    base = {
        "protocol": _window_host._PROTOCOL,
        "plan": _window_host._command_plan_to_payload(_plan((sys.executable, "-c", "pass"))),
        "cwd": str(tmp_path), "timeout": 5.0,
        "runner": ProcessTreeProcessRunner().assay_window_host_spec(),
    }
    assert _window_host._execute_request(base)["profile"] is None
    with pytest.raises(ValueError, match="unexpected shape"):
        _window_host._execute_request({**base, "extra": 1})
    with pytest.raises(ValueError, match="profile config"):
        _window_host._execute_request({**base, "profile": {"mode": "cgroup"}})


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
    assert result.underlying_outcome is None

    restored = _window_host._command_result_from_payload(
        plan, _window_host._command_result_to_payload(result)
    )
    assert restored.underlying_outcome is None
    restored_evidence = ProcessTreeEvidence.from_dict(restored.process_tree_evidence)
    run = mutation._MutantRun(
        result=restored,
        resource_limit_evidence=_ZERO_LIMITS,
        process_tree_evidence=restored_evidence,
    )
    assert mutation._bucket_for_run(
        run,
        equivalence_artifact=None,
        baseline_equivalence=None,
        kill_signal_artifact=None,
    ) == "budget_exceeded"


def test_abrupt_window_host_exit_becomes_crashed_with_exec_failed_cause(
    monkeypatch, tmp_path
):
    candidate = _window_host.subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
    )

    def crash_host(*_args, **kwargs):
        status_fd = kwargs["pass_fds"][1]
        os.write(status_fd, f"{candidate.pid}\n".encode("ascii"))
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
    evidence = ProcessTreeEvidence.from_dict(result.process_tree_evidence)
    assert evidence.leader_status == "signaled"
    assert evidence.clean
    assert type(evidence.signal) is int
    assert evidence.signal == signal.SIGKILL
    run = mutation._MutantRun(
        result=result,
        equivalence_bytes=None,
        execution=MutationExecution(mode="full", witness=None),
        resource_limit_evidence=_ZERO_LIMITS,
        process_tree_evidence=evidence,
    )
    assert mutation._bucket_for_run(
        run,
        equivalence_artifact=None,
        baseline_equivalence=None,
        kill_signal_artifact=None,
    ) == "crashed"
    assert candidate.wait(timeout=1.0) == -signal.SIGKILL


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


def test_coordinator_sigterm_is_forwarded_to_the_window_host_group(
    monkeypatch, capfd
):
    runner._reset_termination_for_tests()
    forwarded: list[tuple[int, int]] = []
    monkeypatch.setattr(
        liveness, "_killpg_group", lambda pgid, sig: forwarded.append((pgid, sig))
    )

    class SignaledHost:
        pid = 987653
        returncode = 0

        def communicate(self, **_kwargs):
            cli._termination_signal_handler(signal.SIGTERM, None)
            return b"", b""

    host = SignaledHost()
    monkeypatch.setattr(_window_host.subprocess, "Popen", lambda *_a, **_kw: host)
    try:
        code, stderr = _window_host._launch_host_process(
            command=(sys.executable, "-I", "-c", "pass"),
            request=b"{}",
            result_fd=3,
            pass_fds=(),
            timeout=5.0,
        )
        assert (host.pid, signal.SIGTERM) in forwarded
        assert host.pid not in liveness._LIVE_WINDOW_HOST_GROUPS
        assert code == 0
        assert stderr == b""
        capfd.readouterr()
    finally:
        runner._reset_termination_for_tests()


def test_liveness_cancellation_forces_candidate_process_group_cleanup(
    monkeypatch, tmp_path
):
    killed: list[tuple[int, int]] = []
    registered: list[int] = []
    unregistered: list[int] = []

    class FakeCandidate:
        pid = 987652
        returncode = None

        def wait(self, timeout=None):
            return self.returncode

    candidate = FakeCandidate()

    class FakeWindow:
        def __init__(self, *, cleanup_seconds):
            self.cleanup_seconds = cleanup_seconds
            self.evidence = None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def attach(self, process):
            assert process is candidate

        def finish(self, *, timed_out=False, force=False):
            assert force
            killed.append((candidate.pid, signal.SIGKILL))
            candidate.returncode = -signal.SIGKILL
            self.evidence = ProcessTreeEvidence(
                leader_status="signaled",
                group_signalled_before_reap=True,
                outliving_descendants=0,
                descendants_signalled=0,
                signal=int(signal.SIGKILL),
                clean=True,
            )
            return self.evidence

    monkeypatch.setattr(liveness, "ProcessTreeWindow", FakeWindow)
    monkeypatch.setattr(liveness, "register_live_group", registered.append)
    monkeypatch.setattr(liveness, "unregister_live_group", unregistered.append)
    live_runner = liveness.LivenessRunner(
        events_dir=tmp_path / "events",
        expect_next_event_within_s=1.0,
        popen=lambda *_args, **_kwargs: candidate,
    )

    def interrupt_candidate(*_args, **_kwargs):
        _window_host._handle_sigterm(signal.SIGTERM, None)

    monkeypatch.setattr(live_runner, "_monitor", interrupt_candidate)
    with pytest.raises(_window_host._WindowHostCancelled):
        live_runner(
            (sys.executable, "-c", "pass"),
            env={},
            cwd=tmp_path,
            timeout=5.0,
        )

    assert registered == [candidate.pid]
    assert unregistered == [candidate.pid]
    assert killed == [(candidate.pid, signal.SIGKILL)]
    assert candidate.returncode == -signal.SIGKILL


def test_process_tree_runner_cancellation_forces_candidate_process_group_cleanup(
    monkeypatch, tmp_path
):
    killed: list[tuple[int, int]] = []
    registered: list[int] = []
    unregistered: list[int] = []

    class FakeCandidate:
        pid = 987651
        returncode = None

        def wait(self, timeout=None):
            _window_host._handle_sigterm(signal.SIGTERM, None)

    candidate = FakeCandidate()

    class FakeWindow:
        def __init__(self, *, cleanup_seconds):
            self.cleanup_seconds = cleanup_seconds
            self.finished = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, _exc, _traceback):
            if not self.finished:
                self.finish(force=exc_type is not None)
            return False

        def spawn(self, _argv, **kwargs):
            assert kwargs["start_new_session"] is True
            return candidate

        def finish(self, *, timed_out=False, force=False):
            assert force
            killed.append((candidate.pid, signal.SIGKILL))
            candidate.returncode = -signal.SIGKILL
            self.finished = True
            return ProcessTreeEvidence(
                leader_status="signaled",
                group_signalled_before_reap=True,
                outliving_descendants=0,
                descendants_signalled=0,
                signal=int(signal.SIGKILL),
                clean=True,
            )

    monkeypatch.setattr(process_tree_module, "ProcessTreeWindow", FakeWindow)
    monkeypatch.setattr(liveness, "register_live_group", registered.append)
    monkeypatch.setattr(liveness, "unregister_live_group", unregistered.append)

    with pytest.raises(_window_host._WindowHostCancelled):
        ProcessTreeProcessRunner()(
            (sys.executable, "-c", "pass"),
            env={},
            cwd=tmp_path,
            timeout=5.0,
        )

    assert registered == [candidate.pid]
    assert unregistered == [candidate.pid]
    assert killed == [(candidate.pid, signal.SIGKILL)]
    assert candidate.returncode == -signal.SIGKILL


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
    host_callback=None,
    repo: GitRepo | None = None,
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
        if host_callback is not None:
            host_callback()
        return host_results.pop(0)

    monkeypatch.setattr(_window_host, "run_window", scripted_host)
    if repo is None:
        repo = GitRepo(path=tmp_path / "repo")
        repo.path.mkdir()
        repo.git("init", "-q", "-b", "main")
        repo.git("config", "user.email", "assay-tests@example.com")
        repo.git("config", "user.name", "assay tests")
        repo.write("pkg/flags.py", _FLAGS)
        repo.commit_all("seed window-host mutation")
    scratch = tmp_path / "scratch"
    scratch.mkdir(exist_ok=True)
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
    return result, calls, repo


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

    result, calls, _repo = _run_with_scripted_host(
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
    first, first_calls, repo = _run_with_scripted_host(
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

    second, second_calls, _repo = _run_with_scripted_host(
        tmp_path,
        monkeypatch,
        state_root=state_root,
        resume=True,
        host_results=[],
        repo=repo,
    )
    assert len(second.survived) == 1
    assert second_calls == []


def test_coordinator_termination_keeps_hosted_candidate_unclassified(
    monkeypatch, tmp_path
):
    runner._reset_termination_for_tests()
    state_root = tmp_path / "state"
    plan = make_plan(make_lane(argv=(sys.executable, "-c", "pass")))
    try:
        result, calls, _repo = _run_with_scripted_host(
            tmp_path,
            monkeypatch,
            state_root=state_root,
            resume=False,
            host_results=[
                _mutation_result(
                    plan,
                    outcome=Outcome.PASS,
                    reason_code=None,
                    returncode=0,
                )
            ],
            host_callback=runner.request_termination,
        )
        assert len(calls) == 1
        assert len(result.budget_exceeded) == 1
        assert result.killed == result.survived == result.crashed == result.hung == ()
        assert list(state_root.glob("*.json")) == []
    finally:
        runner._reset_termination_for_tests()


@pytest.mark.parametrize(
    "spec",
    [
        None,
        {"kind": "unknown"},
        {"kind": "factory", "factory": "missing-colon", "config": {}},
        {"kind": "factory", "factory": "mod:factory", "config": [], "python_paths": []},
        {"kind": "factory", "factory": "mod:factory", "config": {}, "python_paths": [1]},
        {"kind": "process_tree", "bad": object()},
    ],
)
def test_runner_spec_rejects_unhostable_or_unserializable_runner_specs(spec):
    class SpecRunner:
        def assay_window_host_spec(self):
            return spec

    assert _window_host._runner_spec(SpecRunner()) is None
    assert not _window_host.can_host(SpecRunner(), runner._utc_now)


def test_runner_spec_requires_an_opt_in_protocol():
    assert _window_host._runner_spec(object()) is None
    assert not _window_host.can_host(ProcessTreeProcessRunner(), lambda: None)


def test_command_plan_codec_checks_shape_and_preserves_optional_fields():
    plan = _plan((sys.executable, "-c", "pass"))
    payload = _window_host._command_plan_to_payload(plan)
    assert _window_host._command_plan_from_payload(payload).argv_effective == plan.argv_effective
    with pytest.raises(ValueError, match="unexpected shape"):
        _window_host._command_plan_from_payload({})


def test_evidence_and_observation_codecs_cover_supported_shapes():
    class Record:
        def to_dict(self):
            return {"record": True}

    assert _window_host._resource_evidence_payload(None) is None
    assert _window_host._resource_evidence_payload(Record()) == {"record": True}
    assert _window_host._resource_evidence_payload({"mapped": True}) == {"mapped": True}
    with pytest.raises(ValueError, match="mapping or record"):
        _window_host._resource_evidence_payload(7)
    assert _window_host._observation_to_payload(None) is None

    from assay.contention import ContentionObservation

    observation = ContentionObservation(
        status="clear",
        limits=_ZERO_LIMITS,
        pressure_us={"some": 5},
        reasons=("sampled",),
    )
    encoded = _window_host._observation_to_payload(observation)
    decoded = _window_host._observation_from_payload(encoded)
    assert decoded.status == "clear"
    assert decoded.limits == _ZERO_LIMITS
    assert dict(decoded.pressure_us) == {"some": 5}
    assert decoded.reasons == ("sampled",)
    assert _window_host._observation_from_payload(None) is None
    with pytest.raises(ValueError, match="must be an object"):
        _window_host._observation_from_payload([])


def test_result_codec_checks_shape():
    plan = _plan((sys.executable, "-c", "pass"))
    with pytest.raises(ValueError, match="unexpected shape"):
        _window_host._command_result_from_payload(plan, {})


@pytest.mark.parametrize("value", [0, -1, math.inf, math.nan, True, "5"])
def test_timeout_codec_rejects_nonpositive_or_nonfinite_values(value):
    with pytest.raises(ValueError, match="positive and finite"):
        _window_host._timeout_from_payload(value)


def test_timeout_codec_preserves_infinity_and_finite_timeouts():
    assert _window_host._timeout_payload(math.inf) == "infinity"
    assert _window_host._timeout_from_payload("infinity") == math.inf
    assert _window_host._timeout_from_payload(2) == 2.0


def test_worker_runner_rebuilds_both_builtin_runner_types(tmp_path):
    plan = _plan((sys.executable, "-c", "pass"))
    tree = _window_host._worker_runner(
        {"kind": "process_tree", "cleanup_seconds": 2.0}, plan
    )
    assert isinstance(tree, ProcessTreeProcessRunner)
    live = _window_host._worker_runner(
        {
            "kind": "liveness",
            "events_dir": str(tmp_path),
            "expect_next_event_within_s": 1.0,
            "pre_first_event_within_s": None,
            "cpu_window_s": 0.1,
        },
        plan,
    )
    assert isinstance(live, liveness.LivenessRunner)
    with pytest.raises(ValueError, match="unsupported"):
        _window_host._worker_runner({"kind": "unknown"}, plan)


def test_worker_factory_runner_resolves_nested_factory(monkeypatch):
    class Factory:
        @staticmethod
        def build(*, value):
            return {"made": value}

    module = ModuleType("window_host_test_factory")
    module.Factory = Factory
    monkeypatch.setitem(sys.modules, "window_host_test_factory", module)
    result = _window_host._worker_runner(
        {
            "kind": "factory",
            "factory": "window_host_test_factory:Factory.build",
            "config": {"value": 9},
            "python_paths": [],
        },
        _plan((sys.executable, "-c", "pass")),
    )
    assert result == {"made": 9}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"protocol": "wrong", "plan": {}, "cwd": ".", "timeout": 1, "runner": {}},
        {"protocol": _window_host._PROTOCOL, "plan": [], "cwd": ".", "timeout": 1, "runner": {}},
    ],
)
def test_execute_request_rejects_invalid_protocol_shapes(payload):
    with pytest.raises(ValueError):
        _window_host._execute_request(payload)


def test_response_writer_handles_partial_writes_and_rejects_bad_output(monkeypatch):
    writes = []

    def partial_write(_fd, view):
        chunk = bytes(view[:2])
        writes.append(chunk)
        return len(chunk)

    monkeypatch.setattr(_window_host.os, "write", partial_write)
    _window_host._write_response(1, {"ok": True})
    assert b"".join(writes) == b'{"ok": true}'
    monkeypatch.setattr(_window_host, "_RESPONSE_LIMIT_BYTES", 1)
    with pytest.raises(ValueError, match="size limit"):
        _window_host._write_response(1, {"ok": True})
    monkeypatch.setattr(_window_host, "_RESPONSE_LIMIT_BYTES", 100)
    monkeypatch.setattr(_window_host.os, "write", lambda *_a: 0)
    with pytest.raises(OSError, match="stopped accepting"):
        _window_host._write_response(1, {"ok": True})


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ({"message": "bad"}, _window_host.WindowHostError),
        ({"message": "tree", "error_type": "ProcessTreeError"}, Exception),
        (
            {
                "message": "assay",
                "error_type": "AssayError",
                "outcome": "ERROR",
                "reason_code": "EXEC_FAILED",
            },
            Exception,
        ),
        ({"message": "keyboard", "error_type": "KeyboardInterrupt"}, KeyboardInterrupt),
    ],
)
def test_worker_error_records_are_reconstructed(payload, error):
    with pytest.raises(error):
        _window_host._raise_for_worker_error(payload)


def test_worker_error_unknown_type_and_invalid_message():
    with pytest.raises(_window_host.WindowHostError, match="mystery"):
        _window_host._raise_for_worker_error(
            {"message": "bad", "error_type": "mystery"}
        )
    with pytest.raises(ValueError, match="message is invalid"):
        _window_host._raise_for_worker_error({"message": 3})


def test_candidate_pid_parser_distinguishes_empty_and_invalid_records():
    with _window_host.tempfile.TemporaryFile(mode="w+b") as status:
        assert _window_host._candidate_pid(status) is None
        status.write(b"abc\n")
        status.flush()
        with pytest.raises(Exception, match="invalid candidate process identity"):
            _window_host._candidate_pid(status)
    with _window_host.tempfile.TemporaryFile(mode="w+b") as status:
        status.write(b"0\n")
        status.flush()
        with pytest.raises(Exception, match="invalid candidate pid"):
            _window_host._candidate_pid(status)


def test_process_group_inspector_and_crash_cleanup_handle_absent_candidate(monkeypatch):
    class StatFile:
        def read_bytes(self):
            return b"malformed"

    class ProcEntry:
        name = "123"

        def __truediv__(self, _child):
            return StatFile()

    monkeypatch.setattr(Path, "iterdir", lambda _path: (ProcEntry(),))
    with pytest.raises(Exception, match="malformed process stat"):
        _window_host._process_group_members(-1)
    assert _window_host._cleanup_crashed_candidate(None).leader_status == "not-started"
    monkeypatch.setattr(_window_host, "_process_group_members", lambda _pid: ())
    evidence = _window_host._cleanup_crashed_candidate(123456789)
    assert evidence.clean
    assert evidence.leader_status == "exited"


def test_outer_timeout_and_receipt_descriptor_validation():
    assert _window_host._outer_timeout(math.inf, {}) is None
    assert _window_host._outer_timeout(2.0, {"cleanup_seconds": 1.0}) == 5.0
    assert _window_host._outer_timeout(2.0, {"cleanup_seconds": math.nan}) == 14.0
    plan = _plan((sys.executable, "-c", "pass"))
    assert _window_host._receipt_fd(plan) is None
    for value in ("bad", "2"):
        plan = runner.CommandPlan(
            argv_declared=plan.argv_declared,
            argv_appended=plan.argv_appended,
            argv_effective=plan.argv_effective,
            env_declared={},
            env_effective={"ASSAY_MUTATION_WITNESS_FD": value},
            env_passthrough=(),
            allow_argv_append=False,
            budget_seconds=5.0,
            project_prefix=None,
        )
        with pytest.raises(ValueError, match="descriptor is invalid"):
            _window_host._receipt_fd(plan)


def test_run_window_rejects_nonserializable_dependencies_and_large_requests(monkeypatch, tmp_path):
    plan = _plan((sys.executable, "-c", "pass"))
    with pytest.raises(ValueError, match="serializable runner"):
        _window_host.run_window(
            plan,
            cwd=tmp_path,
            timeout=1.0,
            process_runner=lambda *_a, **_kw: None,
            clock=runner._utc_now,
        )
    huge = _plan(("x" * (_window_host._REQUEST_LIMIT_BYTES + 1),))
    with pytest.raises(ValueError, match="request exceeds"):
        _window_host.run_window(
            huge,
            cwd=tmp_path,
            timeout=1.0,
            process_runner=ProcessTreeProcessRunner(),
            clock=runner._utc_now,
        )


def test_window_host_factory_spec_validation_and_factory_path(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "path", list(sys.path))
    class SpecRunner:
        def assay_window_host_spec(self):
            return {
                "kind": "factory",
                "factory": "window_host_test_factory:Factory.build",
                "config": {"value": 9},
                "python_paths": [str(tmp_path)],
            }

    module = ModuleType("window_host_test_factory")
    module.Factory = type(
        "Factory",
        (),
        {"build": staticmethod(lambda *, value: {"made": value})},
    )
    monkeypatch.setitem(sys.modules, "window_host_test_factory", module)
    spec = _window_host._runner_spec(SpecRunner())
    assert spec is not None
    assert _window_host.can_host(SpecRunner(), runner._utc_now)
    assert _window_host._worker_runner(spec, _plan(("true",))) == {"made": 9}


def test_execute_request_injects_candidate_witness_descriptor(monkeypatch, tmp_path):
    plan = _plan((sys.executable, "-c", "pass"))
    request = {
        "protocol": _window_host._PROTOCOL,
        "plan": _window_host._command_plan_to_payload(plan),
        "cwd": str(tmp_path),
        "timeout": 5.0,
        "runner": ProcessTreeProcessRunner().assay_window_host_spec(),
    }
    monkeypatch.setenv(_window_host._WITNESS_FD_ENV, "7")
    captured = {}

    def execute(captured_plan, **_kwargs):
        captured.update(captured_plan.env_effective)
        return _command_result(
            captured_plan,
            outcome=Outcome.PASS,
            reason_code=None,
            returncode=0,
        )

    monkeypatch.setattr(_window_host._runner, "execute_plan", execute)
    response = _window_host._execute_request(request)
    assert response["kind"] == "result"
    assert captured["ASSAY_MUTATION_WITNESS_FD"] == "7"


def test_main_rejects_oversized_and_non_object_requests():
    with _window_host.tempfile.TemporaryFile(mode="w+b") as result_file:
        status = _window_host.main(
            io.BytesIO(b"x" * (_window_host._REQUEST_LIMIT_BYTES + 1)),
            result_fd=result_file.fileno(),
        )
        result_file.seek(0)
        response = json.loads(result_file.read())
    assert status == 1
    assert response["error_type"] == "ValueError"

    with _window_host.tempfile.TemporaryFile(mode="w+b") as result_file:
        status = _window_host.main(io.BytesIO(b"[]"), result_fd=result_file.fileno())
        result_file.seek(0)
        response = json.loads(result_file.read())
    assert status == 1
    assert response["message"] == "window-host request must be a JSON object"


@pytest.mark.parametrize("boundary", ["worker_request", "host_response"])
def test_json_recursion_error_is_classified_at_each_window_host_boundary(
    boundary, monkeypatch, tmp_path
):
    original_loads = json.loads

    def recurse(*_args, **_kwargs):
        raise RecursionError("nested JSON exceeded the parser stack")

    monkeypatch.setattr(_window_host.json, "loads", recurse)
    if boundary == "worker_request":
        with _window_host.tempfile.TemporaryFile(mode="w+b") as result_file:
            status = _window_host.main(
                io.BytesIO(b"{}"), result_fd=result_file.fileno()
            )
            result_file.seek(0)
            response = original_loads(result_file.read())

        assert status == 1
        assert response["kind"] == "error"
        assert response["error_type"] == "RecursionError"
        return

    def return_nested_response(**kwargs):
        os.write(kwargs["result_fd"], b"{}")
        return 0, b""

    monkeypatch.setattr(_window_host, "_launch_host_process", return_nested_response)
    result = _window_host.run_window(
        _plan((sys.executable, "-c", "pass")),
        cwd=tmp_path,
        timeout=5.0,
        process_runner=ProcessTreeProcessRunner(),
        clock=runner._utc_now,
    )

    assert result.outcome is Outcome.ERROR
    assert result.reason_code is ReasonCode.EXEC_FAILED
    assert result.stderr_tail == "window host returned malformed JSON"


@pytest.mark.parametrize("worker_result", ["crashed", "completed"])
def test_lane_termination_overrides_each_window_host_result_path(
    worker_result, monkeypatch, tmp_path
):
    plan = _plan((sys.executable, "-c", "pass"))
    if worker_result == "crashed":
        response_bytes = b"not json"
    else:
        result = _command_result(
            plan,
            outcome=Outcome.PASS,
            reason_code=None,
            returncode=0,
        )
        response_bytes = json.dumps(
            {
                "protocol": _window_host._PROTOCOL,
                "kind": "result",
                "result": _window_host._command_result_to_payload(result),
            }
        ).encode("utf-8")

    def return_worker_result(**kwargs):
        os.write(kwargs["result_fd"], response_bytes)
        return 0, b""

    monkeypatch.setattr(_window_host, "_launch_host_process", return_worker_result)
    monkeypatch.setattr(_window_host._runner, "termination_requested", lambda: True)

    with pytest.raises(AssayError) as failure:
        _window_host.run_window(
            plan,
            cwd=tmp_path,
            timeout=5.0,
            process_runner=ProcessTreeProcessRunner(),
            clock=runner._utc_now,
        )

    assert failure.value.outcome is Outcome.BUDGET_EXCEEDED
    assert failure.value.reason_code is ReasonCode.LANE_TIMEOUT


def test_sigterm_handler_requests_worker_unwind():
    with pytest.raises(_window_host._WindowHostCancelled):
        _window_host._handle_sigterm(signal.SIGTERM, None)


def test_host_process_timeout_and_stop_escalation(monkeypatch):
    class TimedOutHost:
        pid = 987650
        returncode = None

        def communicate(self, **_kwargs):
            raise _window_host.subprocess.TimeoutExpired("worker", 1.0)

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            self.returncode = -signal.SIGTERM
            return self.returncode

    child = TimedOutHost()
    monkeypatch.setattr(_window_host.subprocess, "Popen", lambda *_a, **_kw: child)
    code, stderr = _window_host._launch_host_process(
        command=(sys.executable,),
        request=b"{}",
        result_fd=3,
        pass_fds=(),
        timeout=0.01,
    )
    assert child.terminated
    assert code == -signal.SIGTERM
    assert stderr == b""

    class Vanished:
        def terminate(self):
            raise ProcessLookupError

        def wait(self, timeout=None):
            self.waited = True
            return 0

    vanished = Vanished()
    _window_host._stop_host_process(vanished)
    assert vanished.waited

    class NeedsKill:
        def __init__(self):
            self.waits = 0

        def terminate(self):
            pass

        def wait(self, timeout=None):
            self.waits += 1
            if self.waits == 1:
                raise _window_host.subprocess.TimeoutExpired("worker", timeout)
            return 0

        def kill(self):
            self.killed = True

    stuck = NeedsKill()
    _window_host._stop_host_process(stuck)
    assert stuck.killed
    assert stuck.waits == 2


def test_process_group_inspector_skips_disappearing_entries_and_reports_io_errors(
    monkeypatch,
):
    class Entry:
        name = "123"

        def __truediv__(self, _child):
            raise FileNotFoundError

    monkeypatch.setattr(Path, "iterdir", lambda _path: (Entry(),))
    assert _window_host._process_group_members(123) == ()

    def cannot_list(_path):
        raise OSError("procfs unavailable")

    monkeypatch.setattr(Path, "iterdir", cannot_list)
    with pytest.raises(Exception, match="cannot inspect candidate process group"):
        _window_host._process_group_members(123)

    class UnreadableEntry:
        name = "123"

        def __truediv__(self, _child):
            return self

        def read_bytes(self):
            raise PermissionError("denied")

    monkeypatch.setattr(Path, "iterdir", lambda _path: (UnreadableEntry(),))
    with pytest.raises(Exception, match="cannot inspect candidate process 123"):
        _window_host._process_group_members(123)

    class BadIdentityEntry:
        name = "123"

        def __truediv__(self, _child):
            return self

        def read_bytes(self):
            return b"123 (candidate) S 1 nope"

    monkeypatch.setattr(Path, "iterdir", lambda _path: (BadIdentityEntry(),))
    with pytest.raises(Exception, match="malformed process identity"):
        _window_host._process_group_members(123)


@pytest.mark.parametrize(
    "ppid", ["0", "malformed"], ids=["nonpositive", "malformed"]
)
def test_process_group_inspector_omits_an_invalid_parent_identity(monkeypatch, ppid):
    class InvalidIdentityEntry:
        name = "123"

        def __truediv__(self, _child):
            return self

        def read_bytes(self):
            return f"123 (candidate) S {ppid} 123".encode("ascii")

    monkeypatch.setattr(Path, "iterdir", lambda _path: (InvalidIdentityEntry(),))

    members = _window_host._process_group_members(123)
    assert len(members) == 1
    identity, state = members[0]
    assert state == "S"
    assert identity.to_dict() == {"pid": 123, "ppid": None, "comm": None}


@pytest.mark.parametrize(
    ("name", "expected_comm"),
    [("wörker", "wörker"), ("", None)],
    ids=["non_ascii", "empty_name"],
)
def test_process_group_inspector_keeps_nonstandard_names_as_tolerant_identities(
    monkeypatch, name, expected_comm
):
    stat_bytes = f"123 ({name}) S 1 123 0 0".encode("utf-8")

    class StatFile:
        def read_bytes(self):
            return stat_bytes

    class ProcEntry:
        name = "123"

        def __truediv__(self, _child):
            return StatFile()

    monkeypatch.setattr(Path, "iterdir", lambda _path: (ProcEntry(),))
    members = _window_host._process_group_members(123)

    assert len(members) == 1
    identity, state = members[0]
    assert state == "S"
    assert identity.pid == 123
    assert identity.comm == expected_comm
    assert identity.ppid == (1 if expected_comm is not None else None)


def test_crash_cleanup_surfaces_kill_errors_and_live_group_timeout(monkeypatch):
    monkeypatch.setattr(_window_host, "_process_group_members", lambda _pid: ())
    monkeypatch.setattr(
        _window_host.os,
        "killpg",
        lambda *_args: (_ for _ in ()).throw(PermissionError("denied")),
    )
    with pytest.raises(Exception, match="cannot clean candidate process group"):
        _window_host._cleanup_crashed_candidate(123)

    monkeypatch.setattr(_window_host.os, "killpg", lambda *_args: None)
    monkeypatch.setattr(_window_host, "_process_group_members", lambda _pid: ((123, "S"),))
    times = iter((0.0, 0.0, 11.0))
    monkeypatch.setattr(_window_host.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(_window_host.time, "sleep", lambda _delay: None)
    with pytest.raises(Exception, match="remained live"):
        _window_host._cleanup_crashed_candidate(123)


def test_crash_cleanup_omits_snapshot_descendants_when_group_signal_loses_race(
    monkeypatch,
):
    identity = ProcessTreeDescendant(pid=124, ppid=1, comm="child")
    snapshots = iter((((identity, "S"),), ()))
    monkeypatch.setattr(
        _window_host, "_process_group_members", lambda _pid: next(snapshots)
    )
    monkeypatch.setattr(
        _window_host.os,
        "killpg",
        lambda *_args: (_ for _ in ()).throw(ProcessLookupError()),
    )

    evidence = _window_host._cleanup_crashed_candidate(123)

    assert evidence.leader_status == "exited"
    assert evidence.group_signalled_before_reap is False
    assert evidence.outliving_descendants == 0
    assert evidence.descendants == ()


def test_run_window_receipt_fd_and_worker_failure_records(monkeypatch, tmp_path):
    plan = _plan((sys.executable, "-c", "pass"))
    with _window_host.tempfile.TemporaryFile(mode="w+b") as receipt:
        plan = runner.CommandPlan(
            argv_declared=plan.argv_declared,
            argv_appended=plan.argv_appended,
            argv_effective=plan.argv_effective,
            env_declared={},
            env_effective={"ASSAY_MUTATION_WITNESS_FD": str(receipt.fileno())},
            env_passthrough=(),
            allow_argv_append=False,
            budget_seconds=5.0,
            project_prefix=None,
        )
        response = {
            "protocol": _window_host._PROTOCOL,
            "kind": "result",
            "result": _window_host._command_result_to_payload(
                _command_result(
                    plan,
                    outcome=Outcome.PASS,
                    reason_code=None,
                    returncode=0,
                )
            ),
        }

        def write_response(*, result_fd, **_kwargs):
            os.write(result_fd, json.dumps(response).encode())
            return 0, b""

        monkeypatch.setattr(_window_host, "_launch_host_process", write_response)
        hosted = _window_host.run_window(
            plan,
            cwd=tmp_path,
            timeout=2.0,
            process_runner=ProcessTreeProcessRunner(),
            clock=runner._utc_now,
        )
        assert hosted.outcome is Outcome.PASS

    def mock_response(payload, *, code=0):
        def launch(*, result_fd, **_kwargs):
            if payload is not None:
                os.write(result_fd, payload)
            return code, b"worker diagnostic"

        return launch

    cases = [
        (None, 1),
        (b"not json", 0),
        (b'{"protocol":"wrong"}', 0),
        (b'{"protocol":"assay-window-host/2","kind":"odd"}', 0),
        (b'{"protocol":"assay-window-host/2","kind":"result","result":[]}', 0),
        (
            b'{"protocol":"assay-window-host/2","kind":"result","result":{}}',
            0,
        ),
        (b'{"protocol":"assay-window-host/2","kind":"result","result":{}}', 3),
    ]
    for payload, code in cases:
        monkeypatch.setattr(_window_host, "_launch_host_process", mock_response(payload, code=code))
        result = _window_host.run_window(
            _plan((sys.executable, "-c", "pass")),
            cwd=tmp_path,
            timeout=1.0,
            process_runner=ProcessTreeProcessRunner(),
            clock=runner._utc_now,
        )
        assert result.outcome is Outcome.ERROR
        assert result.reason_code is ReasonCode.EXEC_FAILED

    monkeypatch.setattr(
        _window_host,
        "_launch_host_process",
        mock_response(b'{"protocol":"assay-window-host/2","kind":"error","error_type":"WindowHostError","message":"raised"}'),
    )
    with pytest.raises(_window_host.WindowHostError, match="raised"):
        _window_host.run_window(
            _plan((sys.executable, "-c", "pass")),
            cwd=tmp_path,
            timeout=1.0,
            process_runner=ProcessTreeProcessRunner(),
            clock=runner._utc_now,
        )

    monkeypatch.setattr(
        _window_host,
        "_launch_host_process",
        mock_response(b"x" * (_window_host._RESPONSE_LIMIT_BYTES + 1)),
    )
    result = _window_host.run_window(
        _plan((sys.executable, "-c", "pass")),
        cwd=tmp_path,
        timeout=1.0,
        process_runner=ProcessTreeProcessRunner(),
        clock=runner._utc_now,
    )
    assert result.reason_code is ReasonCode.EXEC_FAILED


def test_cancelled_worker_after_coordinator_sigterm_is_lane_timeout(
    monkeypatch, tmp_path
):
    response = {
        "protocol": _window_host._PROTOCOL,
        "kind": "error",
        "error_type": "_WindowHostCancelled",
        "message": "window host was asked to stop",
        "outcome": None,
        "reason_code": None,
    }

    def write_cancelled_response(*, result_fd, **_kwargs):
        os.write(result_fd, json.dumps(response).encode())
        return 1, b""

    monkeypatch.setattr(_window_host, "_launch_host_process", write_cancelled_response)
    monkeypatch.setattr(_window_host._runner, "termination_requested", lambda: True)

    with pytest.raises(AssayError) as caught:
        _window_host.run_window(
            _plan((sys.executable, "-c", "pass")),
            cwd=tmp_path,
            timeout=1.0,
            process_runner=ProcessTreeProcessRunner(),
            clock=runner._utc_now,
        )

    assert caught.value.outcome is Outcome.BUDGET_EXCEEDED
    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT


def test_host_module_script_entrypoint_runs_main(monkeypatch):
    import tempfile

    monkeypatch.delitem(sys.modules, "assay._window_host")
    with tempfile.TemporaryFile(mode="w+b") as output:
        old_three = None
        try:
            old_three = os.dup(3)
        except OSError:
            pass
        os.dup2(output.fileno(), 3)
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"bad json")))
        try:
            with pytest.raises(SystemExit) as stopped:
                runpy.run_module("assay._window_host", run_name="__main__")
            assert stopped.value.code == 1
        finally:
            if old_three is None:
                os.close(3)
            else:
                os.dup2(old_three, 3)
                os.close(old_three)
        output.seek(0)
        assert json.loads(output.read())["kind"] == "error"


def test_window_host_pid_report_validation_and_candidate_cleanup(
    monkeypatch, tmp_path
):
    process_tree_module._report_window_host_candidate_pid(123)
    monkeypatch.setenv("ASSAY_WINDOW_HOST_CANDIDATE_FD", "invalid")
    with pytest.raises(Exception, match="descriptor is invalid"):
        process_tree_module._report_window_host_candidate_pid(123)
    monkeypatch.setenv("ASSAY_WINDOW_HOST_CANDIDATE_FD", "2")
    with pytest.raises(Exception, match="descriptor or pid is invalid"):
        process_tree_module._report_window_host_candidate_pid(123)
    monkeypatch.setenv("ASSAY_WINDOW_HOST_CANDIDATE_FD", "3")
    with pytest.raises(Exception, match="descriptor or pid is invalid"):
        process_tree_module._report_window_host_candidate_pid(0)
    monkeypatch.setattr(
        process_tree_module.os,
        "write",
        lambda *_args: (_ for _ in ()).throw(OSError("closed")),
    )
    with pytest.raises(Exception, match="cannot report candidate process"):
        process_tree_module._report_window_host_candidate_pid(123)

    def refuse_report(_pid):
        raise process_tree_module.ProcessTreeError("status channel unavailable")

    monkeypatch.setattr(process_tree_module, "_report_window_host_candidate_pid", refuse_report)
    with pytest.raises(Exception, match="status channel unavailable"):
        ProcessTreeProcessRunner()(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            env={},
            cwd=tmp_path,
            timeout=5.0,
        )


def test_liveness_host_spec_declines_injected_process_seams(tmp_path):
    process_runner = liveness.LivenessRunner(
        events_dir=tmp_path / "events",
        expect_next_event_within_s=1.0,
        sleep=lambda _delay: None,
    )
    assert process_runner.assay_window_host_spec() is None


def test_a_non_object_underlying_outcome_is_refused_on_the_host_wire():
    """B251: the window-host result carries the completed command outcome as
    an object or null; any other wire value is a protocol error, not data."""
    plan = _plan((sys.executable, "-c", "pass"))
    payload = _window_host._command_result_to_payload(
        _command_result(plan, outcome=Outcome.PASS, reason_code=None, returncode=0)
    )
    payload["underlying_outcome"] = "PASS"
    with pytest.raises(ValueError, match="underlying outcome must be an object or null"):
        _window_host._command_result_from_payload(plan, payload)
