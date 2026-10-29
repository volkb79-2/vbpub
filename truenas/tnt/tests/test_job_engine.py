from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from tnt.actions import Action, Kind, RpcCall, apply_plan, make_plan
from tnt.errors import PlanExecutionError, TransportError
from tnt.jobs import JobRegistry, JobWaitCancelled
from tnt.transport import GuardedRpc


class RemoteJob:
    def __init__(self, job_id, callback, *, result="done", error=None, wait=False):
        self.job_id = job_id
        self.callback = callback
        self.value = result
        self.error = error
        self.event = threading.Event()
        if not wait:
            self.finish()

    def finish(self):
        state = "FAILED" if self.error else "SUCCESS"
        if self.callback:
            self.callback({"id": self.job_id, "method": "app.start", "state": state,
                           "progress": {"percent": 100, "description": "Complete"},
                           "logs_excerpt": "job finished", "error": self.error})
        self.event.set()

    def result(self):
        self.event.wait()
        if self.error:
            raise RuntimeError(self.error)
        return self.value


class JobClient:
    def __init__(self, *, delayed=False, fail_second=False):
        self.delayed = delayed
        self.fail_second = fail_second
        self.jobs = []
        self.lock = threading.Lock()

    def call(self, method, *params, job=False, callback=None):
        if method == "app.query":
            return []
        if job == "RETURN":
            with self.lock:
                index = len(self.jobs)
                error = "start job failed" if self.fail_second and index == 1 else None
                remote = RemoteJob(str(index + 1), callback, error=error, wait=self.delayed and index == 0)
                self.jobs.append(remote)
                return remote
        raise AssertionError(f"unexpected call {method} {params!r} job={job!r}")

    def close(self):
        pass


def _action(*calls):
    return Action("T-JOB", Kind.MUTATE, "Start app", tuple(calls),
                  (RpcCall("app.query", ([], {})),))


def test_submitted_job_releases_rpc_lock_and_returns_job_id_with_progress_records():
    client = JobClient(delayed=True)
    rpc = GuardedRpc(client, read_only=False)
    registry = JobRegistry("lab")
    rpc.attach_job_registry(registry, generation=7)
    plan = make_plan(_action(RpcCall("app.start", ("demo",), job=True)), rpc)
    output = []
    errors = []

    def run_plan():
        try:
            output.append(apply_plan(plan, rpc, allow_write=True))
        except Exception as exc:  # surfaced in the main test thread below
            errors.append(exc)

    worker = threading.Thread(target=run_plan)
    worker.start()
    for _ in range(100):
        if client.jobs:
            break
        threading.Event().wait(0.01)
    assert client.jobs, "job submission should return before its remote job completes"

    # The remote job is still waiting, but its handle no longer owns the RPC call lock.
    assert rpc.call("app.query") == []
    client.jobs[0].finish()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert errors == []

    result = output[0]
    assert result == ["done"]
    assert result.calls[0].job_id == "1"
    assert result.calls[0].status == "completed"
    assert result.calls[0].generation == 7
    record = registry.snapshot()[0]
    assert record["job_id"] == "1" and record["state"] == "SUCCESS"
    assert record["progress"]["percent"] == 100
    assert record["events"][-1]["kind"] in {"progress", "state", "log"}


def test_failed_second_job_reports_completed_first_call_and_failed_job_id():
    client = JobClient(fail_second=True)
    rpc = GuardedRpc(client, read_only=False)
    rpc.attach_job_registry(JobRegistry("lab"), generation=2)
    action = Action("T-JOB", Kind.DESTRUCTIVE, "Restart demo app",
                    (RpcCall("app.stop", ("demo",), job=True), RpcCall("app.start", ("demo",), job=True)),
                    (RpcCall("app.query", ([], {})),), "demo")
    plan = make_plan(action, rpc)

    with pytest.raises(PlanExecutionError) as caught:
        apply_plan(plan, rpc, allow_write=True, typed_confirmation="demo")

    partial = caught.value.partial
    assert partial == ["done"]
    assert [(call.status, call.job_id) for call in partial.calls] == [
        ("completed", "1"), ("failed", "2")]
    assert "review Jobs" in str(caught.value)


def test_reconnect_reconciles_only_matching_job_and_missing_job_stays_unknown():
    client = JobClient(delayed=True)
    rpc = GuardedRpc(client, read_only=False)
    registry = JobRegistry("lab")
    rpc.attach_job_registry(registry, generation=1)
    plan = make_plan(_action(RpcCall("app.start", ("demo",), job=True)), rpc)
    started = threading.Event()

    def run_plan():
        try:
            apply_plan(plan, rpc, allow_write=True)
        except Exception:
            pass
        finally:
            started.set()

    worker = threading.Thread(target=run_plan)
    worker.start()
    for _ in range(100):
        if client.jobs:
            break
        threading.Event().wait(0.01)
    registry.mark_disconnected()

    class Reconnected:
        def call(self, method, *params, job=False, callback=None):
            assert method == "core.get_jobs"
            return [{"id": 1, "method": "app.start", "state": "SUCCESS",
                     "progress": {"percent": 100, "description": "Complete"},
                     "logs_excerpt": None, "error": None}]

    registry.reconcile(Reconnected())
    item = registry.snapshot()[0]
    assert item["job_id"] == "1" and item["state"] == "SUCCESS"
    assert item["reconciliation"] == "matched"

    client.jobs[0].finish()
    worker.join(timeout=2)
    assert started.is_set()


def test_disconnected_job_that_is_not_visible_remains_unknown():
    registry = JobRegistry("lab")
    registry.record_remote(job_id="9", method="app.stop", state="RUNNING", generation=3)
    registry.mark_disconnected()

    class Reconnected:
        def call(self, method, *params, job=False, callback=None):
            return []

    registry.reconcile(Reconnected())
    item = registry.snapshot()[0]
    assert item["state"] == "UNKNOWN"
    assert item["reconciliation"] == "not-visible"
    assert "cannot establish" in item["error"].lower()


def test_job_values_and_prebind_callbacks_are_bounded_sanitized_and_advisory():
    callbacks = []

    def broken_callback(fields):
        callbacks.append(fields)
        raise RuntimeError("UI callback failed")

    registry = JobRegistry("lab", scrub=lambda value: value.replace("pw-value", "<redacted>"))
    ticket = registry.ticket("app.start", 4, broken_callback)
    ticket.on_update(None)
    ticket.on_update({"state": "running", "progress": {"percent": True, "description": 7},
                      "logs_excerpt": "pw-value", "password": "pw-value", "raw_result": "ignored"})
    remote = RemoteJob(9, None)
    handle = ticket.bind(remote)

    record = registry.get(handle.local_id)
    assert record["job_id"] == "9" and record["generation"] == 4
    assert record["state"] == "RUNNING"
    assert record["progress"] == {"percent": None, "description": None}
    assert record["logs_excerpt"] == "<redacted>"
    assert len(callbacks) == 1 and "raw_result" not in callbacks[0]
    registry._apply_update("missing", {})
    registry._detached("missing")
    registry._apply_update(handle.local_id, {"state": "PAUSED", "progress": {"percent": "bad", "description": 7}})
    assert registry.get(handle.local_id)["state"] == "UNKNOWN"
    cleaned = registry.sanitize_value({"password": "pw-value", "items": ({"api_key": "pw-value"}, object()),
                                       object(): "dropped"})
    assert cleaned["password"] == "***" and cleaned["items"][0]["api_key"] == "***"
    assert cleaned["items"][1].startswith("<object object at")
    assert len(cleaned) == 2


def test_job_ticket_rejects_invalid_remote_ids_and_clears_failed_submission_callbacks():
    registry = JobRegistry("lab")
    invalid = registry.ticket("app.start", 1)
    with pytest.raises(TypeError, match="job handle with an ID"):
        invalid.bind(SimpleNamespace(job_id=True))

    failed = registry.ticket("app.start", 1)
    failed.on_update({"state": "RUNNING"})
    failed.submission_failed()
    assert registry.snapshot() == []


def test_job_wait_cancellation_detaches_locally_and_incomplete_or_failed_handles_are_unknown():
    registry = JobRegistry("lab", scrub=lambda value: value.replace("pw-value", "<redacted>"))
    delayed = RemoteJob("12", None, wait=True)
    handle = registry.ticket("app.start", 5).bind(delayed)
    with pytest.raises(JobWaitCancelled, match="remote job was not aborted"):
        handle.wait(cancelled=lambda: True)
    pending = registry.get(handle.local_id)
    assert pending["reconciliation"] == "pending"
    assert pending["state"] == "WAITING"
    assert "left running" in pending["events"][-1]["text"]

    incomplete = registry.ticket("app.stop", 5).bind(SimpleNamespace(job_id="13", event=None, result=None))
    with pytest.raises(RuntimeError, match="incomplete"):
        incomplete.wait()
    assert registry.get(incomplete.local_id)["state"] == "UNKNOWN"

    failed_ticket = registry.ticket("app.stop", 5)
    failed = failed_ticket.bind(RemoteJob("14", failed_ticket.on_update, error="pw-value middleware failure"))
    with pytest.raises(RuntimeError, match="<redacted>"):
        failed.wait()
    failed_record = registry.get(failed.local_id)
    assert failed_record["state"] == "FAILED" and "pw-value" not in failed_record["error"]


def test_job_registry_validates_remote_rows_caps_events_and_reports_reconcile_failures():
    registry = JobRegistry("lab", scrub=lambda value: value.replace("pw-value", "<redacted>"))
    with pytest.raises(ValueError, match="valid ID and method"):
        registry.record_remote(job_id=True, method="app.start", state="RUNNING")

    registry.record_remote(job_id=20, method="app.start", state="RUNNING", generation=1)
    local_id = registry.snapshot()[0]["local_id"]
    registry.record_remote(job_id=20, method="app.start", state="SUCCESS", generation=2)
    assert registry.snapshot()[0]["local_id"] == local_id
    assert registry.snapshot()[0]["state"] == "SUCCESS"
    registry._detached(local_id)  # a terminal remote job is not changed to pending

    registry.add_event(None, "log", "ignored")
    registry.add_event("missing", "log", "ignored")
    for index in range(205):
        registry.add_event(local_id, "log", str(index))
    assert len(registry.get(local_id)["events"]) == 200

    registry.record_remote(job_id=21, method="app.stop", state="RUNNING")
    pending_id = next(row["local_id"] for row in registry.snapshot() if row["job_id"] == "21")
    registry.mark_disconnected()

    class Reconnected:
        def call(self, *_args, **_kwargs):
            raise TransportError("call", "pw-value reconciliation failed")

    registry.reconcile(Reconnected())
    pending = registry.get(pending_id)
    assert pending["state"] == "UNKNOWN" and pending["reconciliation"] == "unavailable"
    assert "<redacted>" in pending["error"] and "pw-value" not in pending["error"]


@pytest.mark.parametrize("rows", [None, [{"id": True, "method": "app.start", "state": "SUCCESS"}]])
def test_job_reconciliation_rejects_malformed_remote_results(rows):
    registry = JobRegistry("lab")
    registry.record_remote(job_id=30, method="app.start", state="RUNNING")
    registry.mark_disconnected()

    class Reconnected:
        def call(self, *_args, **_kwargs):
            return rows

    registry.reconcile(Reconnected())
    record = registry.snapshot()[0]
    assert record["state"] == "UNKNOWN" and record["reconciliation"] == "unavailable"


def test_job_wait_polls_until_completion_without_treating_missing_state_as_an_update():
    registry = JobRegistry("lab")
    remote = RemoteJob("41", None, wait=True)
    handle = registry.ticket("app.start", 6).bind(remote)
    before = registry.get(handle.local_id)
    registry._apply_update(handle.local_id, {"state": None})
    assert registry.get(handle.local_id)["state"] == before["state"]

    finisher = threading.Thread(target=lambda: (threading.Event().wait(0.03), remote.finish()))
    finisher.start()
    assert handle.wait(cancelled=lambda: False) == "done"
    finisher.join(timeout=1)
    assert not finisher.is_alive()
    assert registry.get(handle.local_id)["state"] == "SUCCESS"


def test_repeated_job_log_updates_do_not_duplicate_events():
    registry = JobRegistry("lab")
    registry.record_remote(job_id=42, method="app.start", state="RUNNING")
    record = registry.snapshot()[0]
    registry._apply_update(record["local_id"], {"logs_excerpt": "same log"})
    event_count = len(registry.get(record["local_id"])["events"])
    registry._apply_update(record["local_id"], {"logs_excerpt": "same log"})
    assert len(registry.get(record["local_id"])["events"]) == event_count
    assert registry.get(record["local_id"])["logs_excerpt"] == "same log"
