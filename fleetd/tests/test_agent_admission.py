import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleetd.admission import AdmissionError, AdmissionResult, ExitCode, InspectorAnswer
from fleetd.agent_admission import AgentAdmission, BoundedCall
from fleetd.contracts import ContractError, validate_host_facts
from fleetd import agent_admission
from test_agent_capacity import _collect
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


def test_submit_lock_closes_directory_when_lock_open_is_refused(tmp_path, monkeypatch):
    policy, _checkout = _policy(tmp_path)
    agent = AgentAdmission(tmp_path / "state", policy, lambda: {})
    real_open = os.open
    opened_directory_fd = []

    def refuse_lock_open(path, flags, *args, **kwargs):
        if path == "submit.lock":
            raise AdmissionError(ExitCode.REFUSAL, "injected lock-file refusal")
        fd = real_open(path, flags, *args, **kwargs)
        opened_directory_fd.append(fd)
        return fd

    monkeypatch.setattr(agent_admission.os, "open", refuse_lock_open)
    with pytest.raises(AdmissionError, match="injected lock-file refusal") as error:
        agent._submit_lock("controller-run-1")
    assert error.value.exit_code == ExitCode.REFUSAL
    assert len(opened_directory_fd) == 1
    with pytest.raises(OSError):
        os.fstat(opened_directory_fd[0])


def test_bounded_call_refuses_a_second_outstanding_query_and_recovers():
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    first_result = {}
    call = BoundedCall(5, name="one-slot")

    def blocked_query():
        started.set()
        assert release.wait(3), "query was not released before the failure watchdog"
        return "first result"

    def run_first():
        first_result["value"] = call(blocked_query)
        finished.set()

    first = threading.Thread(target=run_first, name="bounded-first")
    first.start()
    assert started.wait(3), "first bounded query did not start before the failure watchdog"
    with pytest.raises(TimeoutError, match="still running"):
        call(lambda: "must not start")
    release.set()
    assert finished.wait(3), "first bounded query did not finish before the failure watchdog"
    first.join(timeout=3)
    assert not first.is_alive()
    assert first_result == {"value": "first result"}
    assert call(lambda: "recovered") == "recovered"


def test_bounded_call_releases_its_slot_when_thread_start_fails(monkeypatch):
    real_start = threading.Thread.start
    attempts = 0

    def fail_first_start(thread):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("thread creation refused")
        return real_start(thread)

    monkeypatch.setattr(threading.Thread, "start", fail_first_start)
    call = BoundedCall(5, name="thread-start")
    with pytest.raises(RuntimeError, match="thread creation refused"):
        call(lambda: "unstarted")
    assert call(lambda: "slot released") == "slot released"


def test_bounded_call_rejects_an_invalid_worker_result(monkeypatch):
    class InvalidResultQueue:
        def __init__(self, *, maxsize):
            assert maxsize == 1

        def put(self, _value):
            pass

        def get_nowait(self):
            return False, "not an exception object"

    monkeypatch.setattr(agent_admission.queue, "Queue", InvalidResultQueue)
    call = BoundedCall(5, name="invalid-worker-result")
    with pytest.raises(RuntimeError, match="invalid worker result"):
        call(lambda: "ignored")


def test_capacity_maps_validated_policy_and_refuses_invalid_or_missing_principals(tmp_path):
    policy, _checkout = _policy(tmp_path)
    facts = _collect()
    agent = AgentAdmission(tmp_path / "state", policy, lambda: facts)

    worker = policy.resolve_forced_group("worker")
    capacity = agent._capacity(worker)
    assert capacity.policy.priority_classes_by_principal.value == {
        "worker": ("dev", "gate", "campaign"),
    }

    facts_dict = _collect().to_dict()
    priorities = facts_dict["policy"]["priority_classes_by_principal"]
    priorities["v"] = None
    priorities["reason"] = "priority classes are not configured"
    unknown_agent = AgentAdmission(
        tmp_path / "unknown-state", policy,
        lambda: validate_host_facts(facts_dict),
    )
    unknown = unknown_agent._capacity(policy.resolve_peer(1000, 100))
    assert unknown.policy.priority_classes_by_principal.value is None
    assert unknown.policy.priority_classes_by_principal.reason == "priority classes are not configured"

    with pytest.raises(AdmissionError, match="priority policy is missing") as missing:
        agent._capacity(policy.resolve_peer(1000, 100))
    assert missing.value.exit_code == ExitCode.REFUSAL

    invalid_agent = AgentAdmission(tmp_path / "invalid-state", policy, lambda: {})
    with pytest.raises(AdmissionError, match="host capacity unavailable") as invalid:
        invalid_agent._capacity(worker)
    assert invalid.value.exit_code == ExitCode.QUEUED_WITHDRAWN


def test_legacy_and_inspector_providers_report_configuration_and_query_results(tmp_path):
    policy, _checkout = _policy(tmp_path)
    unconfigured = AgentAdmission(tmp_path / "state", policy, lambda: {})
    with pytest.raises(AdmissionError, match="not configured") as missing_legacy:
        unconfigured._legacy_provider()()
    assert missing_legacy.value.exit_code == ExitCode.QUEUED_WITHDRAWN
    assert unconfigured._inspector_provider() is None

    configured = AgentAdmission(
        tmp_path / "configured-state", policy, lambda: {},
        legacy_jobs_provider=lambda: ["legacy-job"],
        inspector=lambda _name: InspectorAnswer.NO_SUCH_OBJECT,
    )
    assert tuple(configured._legacy_provider()()) == ("legacy-job",)
    assert configured._inspector_provider()("container") is InspectorAnswer.NO_SUCH_OBJECT


def test_job_dir_refuses_symlinks_and_nonprivate_directories(tmp_path):
    policy, _checkout = _policy(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    jobs = state / "jobs"
    jobs.symlink_to(outside, target_is_directory=True)
    agent = AgentAdmission(state, policy, lambda: {})
    with pytest.raises(AdmissionError, match="jobs path is not a private real directory") as jobs_link:
        agent._job_dir("job")
    assert jobs_link.value.exit_code == ExitCode.REFUSAL

    jobs.unlink()
    jobs.mkdir(mode=0o755)
    with pytest.raises(AdmissionError, match="jobs path is not a private real directory") as jobs_mode:
        agent._job_dir("job")
    assert jobs_mode.value.exit_code == ExitCode.REFUSAL

    jobs.chmod(0o700)
    job_link = jobs / "linked-job"
    job_link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(AdmissionError, match="must not be a symlink") as job_symlink:
        agent._job_dir("linked-job")
    assert job_symlink.value.exit_code == ExitCode.REFUSAL

    shared_job = jobs / "shared-job"
    shared_job.mkdir(mode=0o755)
    with pytest.raises(AdmissionError, match="accessible by other users") as job_mode:
        agent._job_dir("shared-job")
    assert job_mode.value.exit_code == ExitCode.REFUSAL


def test_job_dir_maps_creation_errors_and_refuses_non_directory_job_path(tmp_path, monkeypatch):
    policy, _checkout = _policy(tmp_path)
    state = tmp_path / "state"
    agent = AgentAdmission(state, policy, lambda: {})
    real_mkdir = Path.mkdir

    def fail_jobs_directory(path, *args, **kwargs):
        if path == state / "jobs":
            raise OSError("jobs directory unavailable")
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_jobs_directory)
    with pytest.raises(AdmissionError, match="cannot create agent jobs directory") as jobs_error:
        agent._job_dir("job")
    assert jobs_error.value.exit_code == ExitCode.RUNNER_FAILURE
    monkeypatch.setattr(Path, "mkdir", real_mkdir)

    def fail_job_directory(path, *args, **kwargs):
        if path == state / "jobs" / "failed-job":
            raise OSError("job directory unavailable")
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_job_directory)
    with pytest.raises(AdmissionError, match="cannot create durable job directory") as job_error:
        agent._job_dir("failed-job")
    assert job_error.value.exit_code == ExitCode.RUNNER_FAILURE
    monkeypatch.setattr(Path, "mkdir", real_mkdir)

    def skip_job_creation(path, *args, **kwargs):
        if path == state / "jobs" / "missing-job":
            return None
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", skip_job_creation)
    with pytest.raises(AdmissionError, match="is not a real directory") as not_dir:
        agent._job_dir("missing-job")
    assert not_dir.value.exit_code == ExitCode.REFUSAL


def test_existing_request_checks_symlink_size_json_schema_group_digest_and_payload(tmp_path):
    policy, _checkout = _policy(tmp_path)
    agent = AgentAdmission(tmp_path / "state", policy, lambda: {})
    identity = policy.resolve_peer(1000, 100)
    path = agent._job_dir("controller-run-1") / "request.json"
    assert agent._existing_request(path, group=identity.group, digest="d" * 64) is False

    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    path.symlink_to(outside)
    with pytest.raises(AdmissionError, match="is a symlink") as link_error:
        agent._existing_request(path, group=identity.group, digest="d" * 64)
    assert link_error.value.exit_code == ExitCode.REFUSAL
    path.unlink()

    path.write_bytes(b"x" * (64 * 1024 + 1))
    with pytest.raises(AdmissionError, match="exceeds 64 KiB") as size_error:
        agent._existing_request(path, group=identity.group, digest="d" * 64)
    assert size_error.value.exit_code == ExitCode.QUEUED_WITHDRAWN

    path.write_bytes(b"\xff")
    with pytest.raises(AdmissionError, match="unreadable") as encoding_error:
        agent._existing_request(path, group=identity.group, digest="d" * 64)
    assert encoding_error.value.exit_code == ExitCode.QUEUED_WITHDRAWN

    request = _submit()
    valid = {
        "schema": "fleet-host-job-request/1", "group": identity.group,
        "digest": "d" * 64, "request": request,
    }
    for record in ([], {**valid, "schema": "other"}, {**valid, "request": []}):
        path.write_text(json.dumps(record), encoding="utf-8")
        with pytest.raises(AdmissionError, match="invalid schema|payload is invalid") as schema_error:
            agent._existing_request(path, group=identity.group, digest="d" * 64)
        assert schema_error.value.exit_code == ExitCode.QUEUED_WITHDRAWN

    path.write_text(json.dumps({**valid, "group": "worker"}), encoding="utf-8")
    with pytest.raises(AdmissionError, match="different authenticated group") as group_error:
        agent._existing_request(path, group=identity.group, digest="d" * 64)
    assert group_error.value.exit_code == ExitCode.REFUSAL

    path.write_text(json.dumps(valid), encoding="utf-8")
    with pytest.raises(AdmissionError, match="different request digest") as digest_error:
        agent._existing_request(path, group=identity.group, digest="e" * 64)
    assert digest_error.value.exit_code == ExitCode.REFUSAL
    assert agent._existing_request(path, group=identity.group, digest="d" * 64) is True


def test_write_request_rejects_unserializable_and_oversized_records(tmp_path):
    policy, _checkout = _policy(tmp_path)
    agent = AgentAdmission(tmp_path / "state", policy, lambda: {})
    identity = policy.resolve_peer(1000, 100)
    request = _submit()
    request["env"] = {"BAD": object()}
    with pytest.raises(AdmissionError, match="not durable JSON") as serialization_error:
        agent._write_request(request, identity, "d" * 64)
    assert serialization_error.value.exit_code == ExitCode.REFUSAL

    request = _submit()
    request["env"] = {"LARGE": "x" * (64 * 1024)}
    with pytest.raises(AdmissionError, match="exceeds the 64 KiB limit") as size_error:
        agent._write_request(request, identity, "d" * 64)
    assert size_error.value.exit_code == ExitCode.REFUSAL


def test_write_request_handles_file_exists_race_and_durable_cleanup_errors(tmp_path, monkeypatch):
    policy, _checkout = _policy(tmp_path)
    agent = AgentAdmission(tmp_path / "state", policy, lambda: {})
    identity = policy.resolve_peer(1000, 100)
    request = _submit()
    real_link = os.link

    def create_then_report_race(source, destination, *, follow_symlinks):
        real_link(source, destination, follow_symlinks=follow_symlinks)
        raise FileExistsError("another writer linked request.json")

    monkeypatch.setattr(agent_admission.os, "link", create_then_report_race)
    path, created = agent._write_request(request, identity, "d" * 64)
    assert created is False
    assert json.loads(path.read_text(encoding="utf-8"))["request"] == request
    assert tuple(path.parent.glob("request-*")) == ()

    monkeypatch.setattr(agent_admission.os, "link", real_link)
    path.unlink()
    real_unlink = os.unlink
    failed_temp_unlink = False

    def unlink_then_fail_once(target, *args, **kwargs):
        nonlocal failed_temp_unlink
        if Path(target).name.startswith("request-") and not failed_temp_unlink:
            failed_temp_unlink = True
            real_unlink(target, *args, **kwargs)
            raise OSError("temporary record cleanup failed")
        return real_unlink(target, *args, **kwargs)

    monkeypatch.setattr(agent_admission.os, "unlink", unlink_then_fail_once)
    request["priority"] = "campaign"
    with pytest.raises(AdmissionError, match="cannot persist durable request") as persist_error:
        agent._write_request(request, identity, "e" * 64)
    assert persist_error.value.exit_code == ExitCode.RUNNER_FAILURE
    assert tuple(path.parent.glob("request-*")) == ()


def test_remove_unadmitted_request_is_safe_when_cleanup_fails(tmp_path, monkeypatch):
    policy, _checkout = _policy(tmp_path)
    agent = AgentAdmission(tmp_path / "state", policy, lambda: {})
    path = agent._job_dir("controller-run-1") / "request.json"
    path.write_text("durable", encoding="utf-8")
    original = Path.unlink

    def fail_unlink(target, *args, **kwargs):
        if target == path:
            raise OSError("cleanup refused")
        return original(target, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_unlink)
    agent._remove_unadmitted_request(path, created=True)
    assert path.read_text(encoding="utf-8") == "durable"
    agent._remove_unadmitted_request(path, created=False)


def test_submit_admission_failure_removes_only_a_confirmed_unadmitted_record(tmp_path):
    policy, _checkout = _policy(tmp_path)
    identity = policy.resolve_peer(1000, 100)
    request = _submit()
    agent = AgentAdmission(tmp_path / "state", policy, lambda: {})
    agent.book.submit = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AdmissionError(ExitCode.QUEUED_WITHDRAWN, "capacity unavailable"),
    )
    agent.book.status = lambda *_args: None
    with pytest.raises(AdmissionError, match="capacity unavailable") as error:
        agent.submit(request, identity, object())
    assert error.value.exit_code == ExitCode.QUEUED_WITHDRAWN
    request_path = agent._job_dir(request["job_id"]) / "request.json"
    assert not request_path.exists()

    uncertain = AgentAdmission(tmp_path / "uncertain-state", policy, lambda: {})
    uncertain.book.submit = agent.book.submit
    uncertain.book.status = lambda *_args: (_ for _ in ()).throw(
        AdmissionError(ExitCode.QUEUED_WITHDRAWN, "status uncertain"),
    )
    with pytest.raises(AdmissionError, match="capacity unavailable"):
        uncertain.submit(request, identity, object())
    uncertain_path = uncertain._job_dir(request["job_id"]) / "request.json"
    assert json.loads(uncertain_path.read_text(encoding="utf-8"))["request"] == request


def test_submit_returns_queue_and_idempotent_state_or_refuses_missing_executor_state(tmp_path):
    policy, _checkout = _policy(tmp_path)
    identity = policy.resolve_peer(1000, 100)
    request = _submit()
    agent = AgentAdmission(tmp_path / "state", policy, lambda: {})
    agent.book.submit = lambda *_args, **_kwargs: AdmissionResult(
        {"schema": "fleet-admission-waiting/1", "job_id": request["job_id"]}, None, False,
    )
    assert agent.submit(request, identity, object()) == {
        "job_id": request["job_id"], "state": "queued", "reason": None,
    }

    request["job_id"] = "controller-run-2"
    agent.book.submit = lambda *_args, **_kwargs: AdmissionResult(
        {"schema": "fleet-admission-job/1", "job_id": request["job_id"]}, None, True,
    )
    executor = SimpleNamespace(read_state=lambda job_id: {"job_id": job_id, "state": "running"})
    assert agent.submit(request, identity, executor) == {
        "job_id": request["job_id"], "state": "running",
    }

    request["job_id"] = "controller-run-3"
    missing = SimpleNamespace(read_state=lambda _job_id: None)
    with pytest.raises(AdmissionError, match="no state for the admitted idempotent job") as no_state:
        agent.submit(request, identity, missing)
    assert no_state.value.exit_code == ExitCode.RUNNER_FAILURE


def test_submit_validates_admission_handles_and_preserves_charge_when_start_fails(tmp_path, monkeypatch):
    policy, _checkout = _policy(tmp_path)
    identity = policy.resolve_peer(1000, 100)
    request = _submit()
    agent = AgentAdmission(tmp_path / "state", policy, lambda: {})
    fake_handle = object()
    monkeypatch.setattr(agent_admission, "AdmissionHandle", type(fake_handle))
    agent.book.submit = lambda *_args, **_kwargs: AdmissionResult(
        {"schema": "fleet-admission-job/1"}, fake_handle, False,
    )
    executor = SimpleNamespace(start=lambda *_args: {"job_id": request["job_id"], "state": "running"})
    assert agent.submit(request, identity, executor) == {
        "job_id": request["job_id"], "state": "running",
    }

    request["job_id"] = "controller-run-2"
    executor.start = lambda *_args: (_ for _ in ()).throw(RuntimeError("executor unavailable"))
    with pytest.raises(AdmissionError, match="charge are retained") as start_error:
        agent.submit(request, identity, executor)
    assert start_error.value.exit_code == ExitCode.RUNNER_FAILURE
    assert agent._uncertain_start_handles[request["job_id"]] is fake_handle
    assert (agent._job_dir(request["job_id"]) / "request.json").is_file()

    request["job_id"] = "controller-run-3"
    executor.start = lambda *_args: None
    with pytest.raises(AdmissionError, match="start returned no job state") as invalid_state:
        agent.submit(request, identity, executor)
    assert invalid_state.value.exit_code == ExitCode.RUNNER_FAILURE
    assert agent._uncertain_start_handles[request["job_id"]] is fake_handle


def test_submit_refuses_an_invalid_admission_handle(tmp_path):
    policy, _checkout = _policy(tmp_path)
    identity = policy.resolve_peer(1000, 100)
    agent = AgentAdmission(tmp_path / "state", policy, lambda: {})
    agent.book.submit = lambda *_args, **_kwargs: AdmissionResult({}, object(), False)
    with pytest.raises(AdmissionError, match="non-job handle") as error:
        agent.submit(_submit(), identity, object())
    assert error.value.exit_code == ExitCode.RUNNER_FAILURE


def test_status_uses_durable_records_and_maps_executor_failures(tmp_path):
    policy, _checkout = _policy(tmp_path)
    identity = policy.resolve_peer(1000, 100)
    agent = AgentAdmission(tmp_path / "state", policy, lambda: {})
    agent.book.status = lambda *_args: None
    with pytest.raises(AdmissionError, match="unknown job") as unknown:
        agent.status("missing", identity, None)
    assert unknown.value.exit_code == ExitCode.REFUSAL

    waiting = {"schema": "fleet-admission-waiting/1"}
    agent.book.status = lambda *_args: waiting
    assert agent.status("queued", identity, None) == {
        "job_id": "queued", "state": "queued", "reason": None,
    }

    terminal = {
        "schema": "fleet-admission-terminal/1", "state": "failed", "completed_at": 10.5,
        "result": "record only",
    }
    agent.book.status = lambda *_args: terminal
    assert agent.status("done", identity, None) == {
        "job_id": "done", "state": "failed", "completed_at": 10.5,
    }

    active = {"schema": "fleet-admission-job/1"}
    agent.book.status = lambda *_args: active
    with pytest.raises(AdmissionError, match="executor unavailable") as unavailable:
        agent.status("active", identity, None)
    assert unavailable.value.exit_code == ExitCode.RUNNER_FAILURE
    with pytest.raises(AdmissionError, match="executor has no state") as absent:
        agent.status("active", identity, SimpleNamespace(read_state=lambda _job_id: None))
    assert absent.value.exit_code == ExitCode.QUEUED_WITHDRAWN
    assert agent.status(
        "active", identity,
        SimpleNamespace(read_state=lambda job_id: {"job_id": job_id, "state": "running"}),
    ) == {"job_id": "active", "state": "running"}


def test_cancel_handles_waiting_terminal_and_executor_stop_paths(tmp_path):
    policy, _checkout = _policy(tmp_path)
    identity = policy.resolve_peer(1000, 100)
    agent = AgentAdmission(tmp_path / "state", policy, lambda: {})
    agent.book.cancel = lambda *_args: None
    with pytest.raises(AdmissionError, match="unknown job") as unknown:
        agent.cancel("missing", "reason", identity, None)
    assert unknown.value.exit_code == ExitCode.REFUSAL

    agent.book.cancel = lambda *_args: {"schema": "fleet-admission-waiting/1"}
    assert agent.cancel("queued", "operator request", identity, None) == {
        "job_id": "queued", "state": "cancelled", "reason": "operator request",
    }

    agent.book.cancel = lambda *_args: {"schema": "fleet-admission-terminal/1"}
    with pytest.raises(AdmissionError, match="finished job") as terminal:
        agent.cancel("done", "reason", identity, None)
    assert terminal.value.exit_code == ExitCode.REFUSAL

    agent.book.cancel = lambda *_args: {"schema": "fleet-admission-job/1"}
    with pytest.raises(AdmissionError, match="executor unavailable") as unavailable:
        agent.cancel("active", "reason", identity, None)
    assert unavailable.value.exit_code == ExitCode.RUNNER_FAILURE
    executor = SimpleNamespace(
        request_stop=lambda *_args: None,
        read_state=lambda _job_id: {"job_id": "active", "state": "stopping"},
    )
    assert agent.cancel("active", "reason", identity, executor) == {
        "job_id": "active", "state": "stopping",
    }
    executor.read_state = lambda _job_id: None
    with pytest.raises(AdmissionError, match="executor has no state") as absent:
        agent.cancel("active", "reason", identity, executor)
    assert absent.value.exit_code == ExitCode.QUEUED_WITHDRAWN
    executor.request_stop = lambda *_args: {"job_id": "active", "state": "running"}
    assert agent.cancel("active", "reason", identity, executor) == {
        "job_id": "active", "state": "running",
    }


def test_reserve_lease_validates_reconnects_and_creates_only_with_inspection(tmp_path):
    policy, _checkout = _policy(tmp_path)
    identity = policy.resolve_peer(1000, 100)
    agent = AgentAdmission(tmp_path / "state", policy, lambda: {})

    agent.book.status = lambda *_args: None
    with pytest.raises(AdmissionError, match="unknown lease id") as unknown:
        agent.reserve_lease("lease-1", identity, container_id="container-id")
    assert unknown.value.exit_code == ExitCode.REFUSAL

    record = {
        "schema": "fleet-admission-lease/1", "state": "bound", "released": False,
        "container_id": "container-id", "container_name": "runner",
    }
    agent.book.status = lambda *_args: {**record, "state": "tombstone"}
    with pytest.raises(AdmissionError, match="revoked lease") as revoked:
        agent.reserve_lease("lease-1", identity, container_id="container-id")
    assert revoked.value.exit_code == ExitCode.REFUSAL

    agent.book.status = lambda *_args: record
    with pytest.raises(AdmissionError, match="immutable binding") as mismatch:
        agent.reserve_lease("lease-1", identity, container_id="other-id")
    assert mismatch.value.exit_code == ExitCode.REFUSAL

    agent.container_inspector = lambda _container_id: {
        "id": "container-id", "name": "/runner", "labels": {"fleetd.lease": "lease-1"},
    }
    agent.book.resume_lease = lambda *_args: AdmissionResult({"state": "bound"}, object(), True)
    resumed = agent.reserve_lease("lease-1", identity, container_id="container-id")
    assert resumed.record == {"state": "bound"}
    assert resumed.handle is not None

    agent.book.resume_lease = lambda *_args: AdmissionResult({"state": "bound"}, None, True)
    with pytest.raises(AdmissionError, match="already has a live connection") as live:
        agent.reserve_lease("lease-1", identity, container_id="container-id")
    assert live.value.exit_code == ExitCode.QUEUED_WITHDRAWN

    no_identity = AgentAdmission(tmp_path / "no-identity", policy, lambda: {})
    no_identity.book.status = lambda *_args: record
    with pytest.raises(AdmissionError, match="requires a container identity inspector") as no_identity_error:
        no_identity.reserve_lease(
            "lease-1", identity, container_id="container-id",
        )
    assert no_identity_error.value.exit_code == ExitCode.REFUSAL

    for kwargs in (
        {}, {"container_name": "runner"},
        {"container_name": "runner", "footprint": "small", "planned_cores": 1.0},
    ):
        with pytest.raises(AdmissionError, match="new lease requires") as missing_fields:
            agent.reserve_lease("lease-new", identity, **kwargs)
        assert missing_fields.value.exit_code == ExitCode.REFUSAL

    unconfigured_inspector = AgentAdmission(tmp_path / "no-inspector", policy, lambda: {})
    with pytest.raises(AdmissionError, match="injected object inspector") as no_inspector:
        unconfigured_inspector.reserve_lease(
            "lease-new", identity, container_name="runner", footprint="small",
            planned_cores=1.0, priority="gate",
        )
    assert no_inspector.value.exit_code == ExitCode.REFUSAL


def test_new_lease_returns_grant_or_maps_existing_and_revoked_records(tmp_path):
    policy, _checkout = _policy(tmp_path)
    identity = policy.resolve_peer(1000, 100)
    agent = AgentAdmission(
        tmp_path / "state", policy, lambda: {},
        inspector=lambda _name: InspectorAnswer.NO_SUCH_OBJECT,
    )
    request = {
        "container_name": "runner", "footprint": "small", "planned_cores": 1.0,
        "priority": "gate",
    }
    agent.book.reserve_lease = lambda *_args, **_kwargs: AdmissionResult(
        {"state": "tombstone", "released": True}, None, True,
    )
    with pytest.raises(AdmissionError, match="revoked lease") as revoked:
        agent.reserve_lease("lease-1", identity, **request)
    assert revoked.value.exit_code == ExitCode.REFUSAL

    agent.book.reserve_lease = lambda *_args, **_kwargs: AdmissionResult(
        {"state": "pending"}, None, True,
    )
    with pytest.raises(AdmissionError, match="lease already exists") as existing:
        agent.reserve_lease("lease-1", identity, **request)
    assert existing.value.exit_code == ExitCode.QUEUED_WITHDRAWN

    grant = AdmissionResult({"state": "pending"}, object(), False)
    agent.book.reserve_lease = lambda *_args, **_kwargs: grant
    assert agent.reserve_lease("lease-1", identity, **request) is grant


def test_container_binding_inspection_failures_and_identity_mismatches_are_refused(tmp_path):
    policy, _checkout = _policy(tmp_path)
    identity = policy.resolve_peer(1000, 100)
    agent = AgentAdmission(tmp_path / "state", policy, lambda: {})
    with pytest.raises(AdmissionError, match="requires a container identity inspector") as no_inspector:
        agent._verify_container_binding("lease-1", "runner", "container-id")
    assert no_inspector.value.exit_code == ExitCode.REFUSAL

    agent.container_inspector = lambda _container_id: None
    with pytest.raises(AdmissionError, match="returned ERROR") as error_answer:
        agent._verify_container_binding("lease-1", "runner", "container-id")
    assert error_answer.value.exit_code == ExitCode.QUEUED_WITHDRAWN

    agent._inspector_call = lambda _callback: (_ for _ in ()).throw(TimeoutError("slow identity"))
    with pytest.raises(AdmissionError, match="timed out") as timeout:
        agent._verify_container_binding("lease-1", "runner", "container-id")
    assert timeout.value.exit_code == ExitCode.QUEUED_WITHDRAWN

    agent._inspector_call = lambda _callback: (_ for _ in ()).throw(RuntimeError("inspector broke"))
    with pytest.raises(AdmissionError, match="inspector failed") as failed:
        agent._verify_container_binding("lease-1", "runner", "container-id")
    assert failed.value.exit_code == ExitCode.QUEUED_WITHDRAWN

    for observed, message in (
        ({"id": "other", "name": "runner", "labels": {"fleetd.lease": "lease-1"}}, "immutable id"),
        ({"id": "container-id", "name": "other", "labels": {"fleetd.lease": "lease-1"}}, "name does not match"),
        ({"id": "container-id", "name": 42, "labels": {"fleetd.lease": "lease-1"}}, "name does not match"),
        ({"id": "container-id", "name": "runner", "labels": []}, "missing its fleetd.lease label"),
        ({"id": "container-id", "name": "runner", "labels": {}}, "missing its fleetd.lease label"),
    ):
        agent._inspector_call = lambda callback: callback()
        agent.container_inspector = lambda _container_id, value=observed: value
        with pytest.raises(AdmissionError, match=message) as refusal:
            agent._verify_container_binding("lease-1", "runner", "container-id")
        assert refusal.value.exit_code == ExitCode.REFUSAL

    agent.container_inspector = lambda _container_id: {
        "id": "container-id", "name": "/runner", "labels": {"fleetd.lease": "lease-1"},
    }
    assert agent._verify_container_binding("lease-1", "runner", "container-id") is None


def test_bind_revoke_and_reconcile_lease_results_preserve_exact_records(tmp_path):
    policy, _checkout = _policy(tmp_path)
    identity = policy.resolve_peer(1000, 100)
    inspector = lambda _container_id: {
        "id": "container-id", "name": "runner", "labels": {"fleetd.lease": "lease-1"},
    }
    agent = AgentAdmission(
        tmp_path / "state", policy, lambda: {}, container_inspector=inspector,
        inspector=lambda _name: InspectorAnswer.NO_SUCH_OBJECT,
    )
    agent.book.status = lambda *_args: None
    with pytest.raises(AdmissionError, match="unknown lease id") as unknown:
        agent.bind_lease("lease-1", "container-id", identity, object())
    assert unknown.value.exit_code == ExitCode.REFUSAL

    record = {"schema": "fleet-admission-lease/1", "container_name": "runner"}
    agent.book.status = lambda *_args: record
    handle = object()
    agent.book.bind_lease = lambda *args: {"lease_id": args[0], "group": args[1], "state": "bound"}
    assert agent.bind_lease("lease-1", "container-id", identity, handle) == {
        "lease_id": "lease-1", "group": identity.group, "state": "bound",
    }
    agent.book.revoke_lease = lambda *_args: {"lease_id": "lease-1", "state": "tombstone"}
    assert agent.revoke_lease("lease-1", identity) == {"lease_id": "lease-1", "state": "tombstone"}

    no_inspector = AgentAdmission(tmp_path / "no-inspector", policy, lambda: {})
    with pytest.raises(AdmissionError, match="requires an injected object inspector") as no_reconcile:
        no_inspector.reconcile_lease("lease-1", identity)
    assert no_reconcile.value.exit_code == ExitCode.REFUSAL
    agent.book.reconcile_lease = lambda *args: {"lease_id": args[0], "state": "pending"}
    assert agent.reconcile_lease("lease-1", identity) == {"lease_id": "lease-1", "state": "pending"}

    assert no_inspector.reconcile_leases() == ()
    records = (
        {"lease_id": "bad", "group": identity.group},
        {"lease_id": "reconciled", "group": identity.group},
        {"lease_id": "unchanged", "group": identity.group},
    )
    agent.book.lease_records = lambda: records

    def reconcile(lease_id, _group, _inspector):
        if lease_id == "bad":
            raise RuntimeError("inspection uncertain")
        if lease_id == "unchanged":
            return None
        return {"lease_id": lease_id, "state": "released"}

    agent.book.reconcile_lease = reconcile
    assert agent.reconcile_leases() == ({"lease_id": "reconciled", "state": "released"},)
