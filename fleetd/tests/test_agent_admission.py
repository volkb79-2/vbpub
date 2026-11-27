import json
import threading

import pytest

from fleetd.admission import AdmissionError, ExitCode, InspectorAnswer
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
    started.wait()
    with condition:
        released = True
        condition.notify_all()
    finished.wait()

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
