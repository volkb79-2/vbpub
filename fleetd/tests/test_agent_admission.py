import json
import threading

import pytest

from fleetd.admission import AdmissionError, AdmissionResult, ExitCode, InspectorAnswer
from fleetd.agent_admission import AgentAdmission, BoundedCall
from test_agent_protocol import _policy, _submit


def test_bounded_call_returns_values_and_propagates_callback_errors(tmp_path):
    call = BoundedCall(5, name="test")

    assert call(lambda: "answer") == "answer"
    with pytest.raises(ValueError, match="query failed"):
        call(lambda: (_ for _ in ()).throw(ValueError("query failed")))


def test_bounded_call_times_out_without_leaking_unbounded_workers():
    condition = threading.Condition()
    started = threading.Event()
    finished = threading.Event()
    released = False

    def blocked_query():
        nonlocal released
        started.set()
        with condition:
            condition.wait_for(lambda: released)
        finished.set()
        return "late result"

    call = BoundedCall(5, name="test", completion_waiter=lambda _event, _timeout: False)
    with pytest.raises(TimeoutError, match="timed out"):
        call(blocked_query)
    assert started.wait(3), "bounded query did not start before the failure watchdog"
    with condition:
        released = True
        condition.notify_all()
    assert finished.wait(3), "bounded query did not finish before the failure watchdog"
    worker = call._last_worker
    assert worker is not None
    worker.join(timeout=3)
    assert not worker.is_alive(), "bounded query worker remained alive after its completion event"

    call.completion_waiter = lambda event, timeout: event.wait(timeout)
    assert call(lambda: "recovered") == "recovered"


def test_capacity_and_legacy_timeouts_map_to_exit_75(tmp_path):
    policy, _checkout = _policy(tmp_path)
    agent = AgentAdmission(tmp_path / "state", policy, lambda: {}, legacy_jobs_provider=lambda: ())
    identity = policy.resolve_peer(1000, 100)
    agent._capacity_call = lambda _callback: (_ for _ in ()).throw(TimeoutError("slow capacity"))
    agent._legacy_call = lambda _callback: (_ for _ in ()).throw(TimeoutError("slow legacy"))

    with pytest.raises(AdmissionError, match="host capacity query timed out") as capacity_error:
        agent._capacity_provider(identity)()
    with pytest.raises(AdmissionError, match="legacy job list query timed out") as legacy_error:
        agent._legacy_provider()()

    assert capacity_error.value.exit_code == ExitCode.QUEUED_WITHDRAWN
    assert legacy_error.value.exit_code == ExitCode.QUEUED_WITHDRAWN


def test_inspector_timeout_becomes_error_answer(tmp_path):
    policy, _checkout = _policy(tmp_path)
    agent = AgentAdmission(
        tmp_path / "state", policy, lambda: {}, inspector=lambda _name: InspectorAnswer.NO_SUCH_OBJECT,
    )
    agent._inspector_call = lambda _callback: (_ for _ in ()).throw(TimeoutError("slow inspect"))

    assert agent._inspector_provider()("container") is InspectorAnswer.ERROR


def test_request_model_digest_and_durable_request_record_are_idempotent(tmp_path):
    policy, _checkout = _policy(tmp_path)
    agent = AgentAdmission(tmp_path / "state", policy, lambda: {})
    identity = policy.resolve_peer(1000, 100)
    request = _submit()
    model = agent.request_model(request)

    path, created = agent._write_request(request, identity, model.digest())

    assert created is True
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schema": "fleet-host-job-request/1",
        "group": "controller",
        "digest": model.digest(),
        "request": request,
    }
    same_path, created_again = agent._write_request(request, identity, model.digest())
    assert same_path == path
    assert created_again is False

    request["priority"] = "campaign"
    with pytest.raises(AdmissionError, match="different request digest"):
        agent._write_request(request, identity, agent.request_model(request).digest())


def test_same_id_submits_serialize_request_cleanup_and_leave_second_record(tmp_path):
    policy, _checkout = _policy(tmp_path)
    agent = AgentAdmission(tmp_path / "state", policy, lambda: {})
    identity = policy.resolve_peer(1000, 100)
    request = _submit()
    first_entered_admission = threading.Event()
    allow_first_failure = threading.Event()
    second_lock_attempted = threading.Event()
    second_lock_acquired = threading.Event()
    second_entered_admission = threading.Event()
    first_done = threading.Event()
    second_done = threading.Event()
    submit_count = [0]
    outcomes = {}
    results = {}
    real_submit_lock = agent._submit_lock

    def observed_submit_lock(job_id):
        if threading.current_thread().name == "second-submit":
            second_lock_attempted.set()
            lock_fd = real_submit_lock(job_id)
            second_lock_acquired.set()
            return lock_fd
        return real_submit_lock(job_id)

    def controlled_admission(_model, **_kwargs):
        submit_count[0] += 1
        if submit_count[0] == 1:
            first_entered_admission.set()
            assert allow_first_failure.wait(3), "first submit was not released before the failure watchdog"
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "controlled capacity refusal")
        second_entered_admission.set()
        return AdmissionResult(
            {"schema": "fleet-admission-waiting/1", "job_id": request["job_id"]}, None, True,
        )

    agent._submit_lock = observed_submit_lock
    agent.book.submit = controlled_admission

    def run_submit(key, done):
        try:
            results[key] = agent.submit(dict(request), identity, object())
        except BaseException as exc:
            outcomes[key] = exc
        finally:
            done.set()

    first = threading.Thread(target=run_submit, args=("first", first_done), name="first-submit")
    first.start()
    assert first_entered_admission.wait(3), "first submit did not reach admission before the failure watchdog"
    request_path = agent._job_dir(request["job_id"]) / "request.json"
    assert request_path.is_file()

    second = threading.Thread(target=run_submit, args=("second", second_done), name="second-submit")
    second.start()
    assert second_lock_attempted.wait(3), "second submit did not attempt its job lock before the failure watchdog"
    assert not second_lock_acquired.is_set()
    assert not second_entered_admission.is_set()

    # Status and cancel consult only the admission book; neither queues behind this submit flock.
    with pytest.raises(AdmissionError, match="unknown job"):
        agent.status(request["job_id"], identity, None)
    with pytest.raises(AdmissionError, match="unknown job"):
        agent.cancel(request["job_id"], "test", identity, None)

    allow_first_failure.set()
    assert first_done.wait(3), "first submit did not finish before the failure watchdog"
    first.join(timeout=3)
    assert not first.is_alive()
    assert isinstance(outcomes["first"], AdmissionError)
    assert outcomes["first"].exit_code == ExitCode.QUEUED_WITHDRAWN

    assert second_done.wait(3), "second submit did not finish before the failure watchdog"
    second.join(timeout=3)
    assert not second.is_alive()
    assert second_lock_acquired.is_set()
    assert second_entered_admission.is_set()
    assert results["second"]["state"] == "queued"
    assert outcomes.get("second") is None
    assert request_path.is_file()
    durable = json.loads(request_path.read_text(encoding="utf-8"))
    assert durable["request"] == request


def test_submit_lock_refuses_symlink_and_group_accessible_lock_files(tmp_path):
    policy, _checkout = _policy(tmp_path)
    agent = AgentAdmission(tmp_path / "state", policy, lambda: {})
    job_dir = agent._job_dir("controller-run-1")
    outside = tmp_path / "outside.lock"
    outside.write_text("not a lock", encoding="utf-8")
    (job_dir / "submit.lock").symlink_to(outside)

    with pytest.raises(AdmissionError, match="cannot lock durable job state") as symlink_error:
        agent._submit_lock("controller-run-1")
    assert symlink_error.value.exit_code == ExitCode.RUNNER_FAILURE

    (job_dir / "submit.lock").unlink()
    (job_dir / "submit.lock").write_text("not private", encoding="utf-8")
    (job_dir / "submit.lock").chmod(0o644)
    with pytest.raises(AdmissionError, match="private regular file") as mode_error:
        agent._submit_lock("controller-run-1")
    assert mode_error.value.exit_code == ExitCode.REFUSAL


def test_submit_lock_maps_flock_oserror_and_closes_the_descriptor(tmp_path, monkeypatch):
    policy, _checkout = _policy(tmp_path)
    agent = AgentAdmission(tmp_path / "state", policy, lambda: {})

    def fail_flock(_fd, _operation):
        raise OSError("flock unavailable")

    monkeypatch.setattr("fleetd.agent_admission.fcntl.flock", fail_flock)
    with pytest.raises(AdmissionError, match="cannot lock durable job state") as error:
        agent._submit_lock("controller-run-1")

    assert error.value.exit_code == ExitCode.RUNNER_FAILURE
