from __future__ import annotations

import threading
import time

import pytest

from assay._limit_ledger import (
    _BoundaryRequest,
    LimitBoundaryBroker,
    LimitLedger,
)
from assay.errors import Outcome
from assay.resource_limits import ResourceLimitCounters
from assay import termination


def _counters(*, pids: int = 0, ancestor_local_pids: int = 0):
    leaf = ("/sys/fs/cgroup/leaf", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    ancestor = (
        "/sys/fs/cgroup",
        0,
        0,
        0,
        0,
        0,
        ancestor_local_pids,
        0,
        0,
        0,
        0,
    )
    return ResourceLimitCounters(
        pids_max=pids,
        memory_oom=0,
        memory_max=0,
        memory_oom_kill=0,
        memory_oom_group_kill=0,
        path_event_counters=(leaf, ancestor) if ancestor_local_pids else (),
    )


def _ancestor_counters(local_pids: int):
    leaf = ("/sys/fs/cgroup/leaf", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    ancestor = (
        "/sys/fs/cgroup",
        0,
        0,
        0,
        0,
        0,
        local_pids,
        0,
        0,
        0,
        0,
    )
    return ResourceLimitCounters(
        pids_max=0,
        memory_oom=0,
        memory_max=0,
        memory_oom_kill=0,
        memory_oom_group_kill=0,
        path_event_counters=(leaf, ancestor),
    )


def _limited_counters(path: str, pids_max: int):
    return ResourceLimitCounters(
        pids_max=0,
        memory_oom=0,
        memory_max=0,
        memory_oom_kill=0,
        memory_oom_group_kill=0,
        limit_signature=(
            (path, 1, True, False, True, False, True, False, True, False),
        ),
        limit_values=((pids_max, None),),
    )
@pytest.mark.parametrize(
    ("samples", "windows", "expected"),
    [
        pytest.param(
            [_counters(), _counters(), _counters(pids=1), _counters(pids=1)],
            {"first": (0, 2), "overlap": (1, 3)},
            {"first": True, "overlap": True},
            id="event-overlaps-both-windows",
        ),
        pytest.param(
            [_counters(), _counters(), _counters(pids=1), _counters(pids=1)],
            {"first": (0, 1), "second": (2, 3)},
            {"first": False, "second": False},
            id="event-outside-both-windows",
        ),
        pytest.param(
            [
                _ancestor_counters(0),
                _ancestor_counters(0),
                _ancestor_counters(1),
                _ancestor_counters(1),
            ],
            {"first": (0, 2), "overlap": (1, 3)},
            {"first": True, "overlap": True},
            id="ancestor-event-is-conservative-for-overlap",
        ),
    ],
)
def test_limit_ledger_attributes_counter_deltas_to_overlapping_windows(
    samples, windows, expected
):
    iterator = iter(samples)
    ledger = LimitLedger(lambda: (next(iterator), None))
    boundaries = [ledger.snapshot() for _ in samples]

    assert boundaries == list(range(len(samples)))
    assert {
        name: ledger.limits_for(*sequence).evidence.limit_hit
        for name, sequence in windows.items()
    } == expected


def test_limit_ledger_marks_a_window_unobserved_if_any_boundary_is_missing():
    samples = iter(
        [
            (_counters(), None),
            (None, "ancestor counters unavailable"),
            (_counters(), None),
        ]
    )
    ledger = LimitLedger(lambda: next(samples))
    start = ledger.snapshot()
    ledger.snapshot()
    end = ledger.snapshot()

    result = ledger.limits_for(start, end)

    assert result.evidence is None
    assert result.reason == "ancestor counters unavailable"


@pytest.mark.parametrize(
    ("reader", "expected_reason"),
    [
        pytest.param(lambda: (_ for _ in ()).throw(ValueError("reader failed")), "reader failed", id="reader-exception"),
        pytest.param(lambda: (object(), None), "resource counter reader returned an invalid sample", id="invalid-sample"),
        pytest.param(lambda: (None, None), "resource counter sample is unavailable", id="missing-reason"),
        pytest.param(lambda: (None, "   "), "resource counter sample is unavailable", id="blank-reason"),
    ],
)
def test_limit_ledger_normalizes_unavailable_samples(reader, expected_reason):
    ledger = LimitLedger(reader)
    start = ledger.snapshot()
    end = ledger.snapshot()

    result = ledger.limits_for(start, end)

    assert result.evidence is None
    assert result.reason == expected_reason


def test_limit_ledger_requires_a_callable_reader():
    with pytest.raises(TypeError, match="reader must be callable"):
        LimitLedger(None)


def test_limit_ledger_reports_changed_identity_as_unobserved():
    samples = iter(
        [
            _limited_counters("/sys/fs/cgroup/a", 1),
            _limited_counters("/sys/fs/cgroup/b", 1),
        ]
    )
    ledger = LimitLedger(lambda: (next(samples), None))
    start = ledger.snapshot()
    end = ledger.snapshot()

    result = ledger.limits_for(start, end)

    assert result.evidence is None
    assert "observation changed" in result.reason


def test_limit_ledger_requires_coordinator_thread_for_snapshots():
    ledger = LimitLedger(lambda: (_counters(), None))
    errors: list[BaseException] = []

    def sample_from_worker():
        try:
            ledger.snapshot()
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=sample_from_worker)
    worker.start()
    worker.join()

    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)
    assert "coordinator thread" in str(errors[0])


def test_limit_boundary_broker_services_worker_requests_on_coordinator():
    owner = threading.get_ident()
    samples = iter([_counters(), _counters(pids=1)])
    reader_threads = []

    def reader():
        reader_threads.append(threading.get_ident())
        return next(samples), None

    broker = LimitBoundaryBroker(LimitLedger(reader))
    result = []

    def worker():
        start = broker.start_window()
        result.append(broker.end_window(start))

    thread = threading.Thread(target=worker)
    thread.start()
    while thread.is_alive():
        if broker.service_pending() == 0:
            time.sleep(0.001)
    thread.join()

    assert reader_threads == [owner, owner]
    assert result[0].evidence is not None
    assert result[0].evidence.limit_hit


def test_limit_boundary_broker_cancel_wakes_waiters_and_closes():
    broker = LimitBoundaryBroker(LimitLedger(lambda: (_counters(), None)))
    errors = []

    def worker():
        try:
            broker.start_window()
        except RuntimeError as exc:
            errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    while broker._requests.empty():
        time.sleep(0.001)
    reason = RuntimeError("campaign interrupted")
    broker.cancel_pending(reason)
    thread.join()

    assert errors == [reason]
    with pytest.raises(RuntimeError, match="closed"):
        broker.start_window()


def test_limit_boundary_broker_rejects_malformed_requests():
    broker = LimitBoundaryBroker(LimitLedger(lambda: (_counters(), None)))
    request = _BoundaryRequest("unknown", None, threading.Event())
    broker._requests.put(request)

    assert broker.service_pending() == 1
    assert request.ready.is_set()
    assert isinstance(request.error, ValueError)
    assert "malformed" in str(request.error)


def test_limit_boundary_broker_rejects_invalid_inputs_and_results(monkeypatch):
    with pytest.raises(TypeError, match="requires a LimitLedger"):
        LimitBoundaryBroker(None)
    broker = LimitBoundaryBroker(LimitLedger(lambda: (_counters(), None)))
    with pytest.raises(TypeError, match="start sequence"):
        broker.end_window(True)
    monkeypatch.setattr(broker, "_request", lambda *_args: "invalid")
    with pytest.raises(RuntimeError, match="invalid start sequence"):
        broker.start_window()
    with pytest.raises(RuntimeError, match="invalid window evidence"):
        broker.end_window(0)


def test_limit_boundary_broker_wakes_a_request_without_a_result():
    broker = LimitBoundaryBroker(LimitLedger(lambda: (_counters(), None)))
    errors = []

    def worker():
        try:
            broker.start_window()
        except RuntimeError as exc:
            errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    while broker._requests.empty():
        time.sleep(0.001)
    request = broker._requests.get_nowait()
    request.ready.set()
    thread.join()

    assert len(errors) == 1
    assert "without a result" in str(errors[0])


def test_limit_boundary_broker_services_a_queued_request_after_close():
    broker = LimitBoundaryBroker(LimitLedger(lambda: (_counters(), None)))
    broker.cancel_pending(RuntimeError("closed"))
    request = _BoundaryRequest("start", None, threading.Event())
    broker._requests.put(request)

    assert broker.service_pending() == 1
    assert request.ready.is_set()
    assert isinstance(request.error, RuntimeError)
    assert "closed" in str(request.error)


@pytest.mark.parametrize(
    ("outcome", "overlap", "expected"),
    [
        pytest.param(Outcome.FAIL, True, termination.Termination.LIMIT_EVENT, id="overlap-fail-crashed"),
        pytest.param(Outcome.FAIL, False, termination.Termination.FAILED, id="no-overlap-fail-killed"),
        pytest.param(Outcome.PASS, True, termination.Termination.PASSED, id="overlap-pass-survived"),
    ],
)
def test_overlapping_limit_events_follow_ap1_precedence(outcome, overlap, expected):
    event_samples = [_counters(), _counters(), _counters(pids=1)]
    ledger = LimitLedger(lambda: (event_samples.pop(0), None))
    start = ledger.snapshot()
    middle = ledger.snapshot()
    end = ledger.snapshot()
    window = ledger.limits_for(start, end if overlap else middle)

    classified = termination.classify(
        outcome=outcome,
        reason_code=None,
        returncode=1 if outcome is Outcome.FAIL else 0,
        decided_by_report=outcome is Outcome.FAIL,
        process_tree=None,
        limits=window.evidence,
        observation="clear",
        observation_required=True,
    )

    assert classified is expected


@pytest.mark.parametrize(
    ("start", "end"),
    [(-1, 0), (0, 0), (1, 0), (0, 1)],
    ids=["negative", "empty", "reversed", "end-out-of-range"],
)
def test_limit_ledger_rejects_invalid_window_sequences(start, end):
    ledger = LimitLedger(lambda: (_counters(), None))
    ledger.snapshot()

    with pytest.raises(ValueError, match="window sequence"):
        ledger.limits_for(start, end)
