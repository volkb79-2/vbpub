"""Coordinator-owned cgroup counter snapshots for parallel native R2 windows."""

from __future__ import annotations

from queue import Empty, Queue
import threading
from typing import Callable

from .resource_limits import (
    ResourceLimitCounters,
    ResourceLimitEvidence,
    ResourceLimitObservationError,
)
from .records import positional_record, record


CounterReader = Callable[
    [], tuple[ResourceLimitCounters | None, str | None]
]


@record
class LimitWindow:
    """Evidence and diagnostics derived for one start/end sequence range."""

    evidence: ResourceLimitEvidence | None
    reason: str | None = None
    changes: tuple[str, ...] = ()


@positional_record
class _Snapshot:
    counters: ResourceLimitCounters | None
    reason: str | None


@positional_record
class _BoundaryRequest:
    kind: str
    start_sequence: int | None
    response: Queue[int | LimitWindow | BaseException | None]


class LimitLedger:
    """Record every window boundary on its creating coordinator thread.

    Window ranges use the sequence number returned by :meth:`snapshot` at
    start and end. Adjacent snapshots are compared in order, so an event is
    included in every candidate window whose sequence range overlaps it.
    Ancestor-local counter attribution remains owned by
    :class:`ResourceLimitEvidence`.
    """

    def __init__(self, reader: CounterReader) -> None:
        if not callable(reader):
            raise TypeError("LimitLedger reader must be callable")
        self._reader = reader
        self._owner = threading.get_ident()
        self._snapshots: list[_Snapshot] = []

    def _require_coordinator(self) -> None:
        if threading.get_ident() != self._owner:
            raise RuntimeError("LimitLedger may only be used by its coordinator thread")

    def snapshot(self) -> int:
        """Take one cgroup sample and return its monotonically increasing ID."""
        self._require_coordinator()
        try:
            counters, reason = self._reader()
        except Exception as exc:
            counters, reason = None, str(exc)
        if counters is not None and not isinstance(counters, ResourceLimitCounters):
            counters = None
            reason = "resource counter reader returned an invalid sample"
        if counters is None and (not isinstance(reason, str) or not reason.strip()):
            reason = "resource counter sample is unavailable"
        sequence = len(self._snapshots)
        self._snapshots.append(_Snapshot(counters, reason))
        return sequence

    def limits_for(self, start_sequence: int, end_sequence: int) -> LimitWindow:
        """Aggregate every observed counter delta in an inclusive boundary range."""
        self._require_coordinator()
        if (
            type(start_sequence) is not int
            or type(end_sequence) is not int
            or start_sequence < 0
            or end_sequence <= start_sequence
            or end_sequence >= len(self._snapshots)
        ):
            raise ValueError(
                "LimitLedger window sequence must name an existing start and later end"
            )
        snapshots = self._snapshots[start_sequence : end_sequence + 1]
        missing = [sample.reason for sample in snapshots if sample.counters is None]
        if missing:
            reasons = tuple(reason for reason in missing if reason)
            return LimitWindow(
                evidence=None,
                reason="; ".join(dict.fromkeys(reasons))
                if reasons
                else "resource counter sample is unavailable",
            )

        changes: list[str] = []
        try:
            for before, after in zip(
                snapshots[:-1], snapshots[1:], strict=True
            ):
                assert before.counters is not None and after.counters is not None
                _evidence, sample_changes = ResourceLimitEvidence.between(
                    before.counters, after.counters
                )
                changes.extend(sample_changes)
            first = snapshots[0].counters
            last = snapshots[-1].counters
            assert first is not None and last is not None
            evidence, _outer_changes = ResourceLimitEvidence.between(first, last)
        except (ResourceLimitObservationError, ValueError) as exc:
            return LimitWindow(evidence=None, reason=str(exc))
        return LimitWindow(
            evidence=evidence,
            changes=tuple(dict.fromkeys(changes)),
        )


class LimitBoundaryBroker:
    """Let window threads request boundaries while only the coordinator samples."""

    def __init__(self, ledger: LimitLedger) -> None:
        if not isinstance(ledger, LimitLedger):
            raise TypeError("LimitBoundaryBroker requires a LimitLedger")
        self._ledger = ledger
        self._requests: Queue[_BoundaryRequest] = Queue()
        self._lock = threading.Lock()
        self._closed = False

    def start_window(self) -> int:
        result = self._request("start", None)
        if type(result) is not int:
            raise RuntimeError("LimitLedger returned an invalid start sequence")
        return result

    def end_window(self, start_sequence: int) -> LimitWindow:
        if type(start_sequence) is not int:
            raise TypeError("LimitLedger start sequence must be an integer")
        result = self._request("end", start_sequence)
        if not isinstance(result, LimitWindow):
            raise RuntimeError("LimitLedger returned invalid window evidence")
        return result

    def _request(self, kind: str, start_sequence: int | None) -> int | LimitWindow:
        response: Queue[int | LimitWindow | BaseException | None] = Queue(maxsize=1)
        request = _BoundaryRequest(kind, start_sequence, response)
        with self._lock:
            if self._closed:
                raise RuntimeError("LimitLedger boundary broker is closed")
            self._requests.put(request)
        result = response.get()
        if isinstance(result, BaseException):
            raise result
        if result is None:
            raise RuntimeError("LimitLedger boundary request completed without a result")
        return result

    def service_pending(self) -> int:
        """Serve queued requests on the coordinator and return their count."""
        served = 0
        while True:
            try:
                request = self._requests.get_nowait()
            except Empty:
                return served
            try:
                with self._lock:
                    closed = self._closed
                if closed:
                    raise RuntimeError("LimitLedger boundary broker is closed")
                if request.kind == "start" and request.start_sequence is None:
                    result: int | LimitWindow = self._ledger.snapshot()
                elif request.kind == "end" and request.start_sequence is not None:
                    end_sequence = self._ledger.snapshot()
                    result = self._ledger.limits_for(
                        request.start_sequence, end_sequence
                    )
                else:
                    raise ValueError("LimitLedger boundary request is malformed")
            except Exception as exc:
                request.response.put(exc)
            else:
                request.response.put(result)
            served += 1

    def cancel_pending(self, error: BaseException) -> None:
        """Wake queued callers and refuse any later boundary requests."""
        with self._lock:
            self._closed = True
            while True:
                try:
                    request = self._requests.get_nowait()
                except Empty:
                    break
                request.response.put(error)
