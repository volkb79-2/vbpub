"""Attribute cgroup pressure to one completed Assay command window.

PSI and throttling are retained as telemetry. They make a completed failure
retryable only when they show material pressure: an observed CPU throttle or
one cgroup PSI counter accumulating at least 5% of the command window, with a
100 ms minimum. This filters shared-host stalls measured at 1.5% or less in
the provisional runs, including 5.34 seconds of CPU-some pressure over a
fifteen-minute peer run. CPU throttling remains direct pressure evidence. A
completed pass is never withheld for PSI/throttling alone; direct pids or
memory OOM events still withhold because they can kill or truncate work.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from typing import Any, Self

from . import liveness_resources, resource_limits
from .errors import Outcome
from .records import positional_record

_PSI_PRESSURE_FRACTION = 0.05
_PSI_MINIMUM_STALL_US = 100_000


@positional_record
class ContentionObservation:
    status: str
    resource_limit_hit: bool = False
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in {"unobserved", "clear", "affected", "unknown"}:
            raise ValueError("contention status is invalid")
        if type(self.resource_limit_hit) is not bool:
            raise ValueError("resource_limit_hit must be a bool")
        if type(self.reasons) is not tuple or any(
            not isinstance(reason, str) or not reason for reason in self.reasons
        ):
            raise ValueError("contention reasons must be non-empty strings")


class ContentionWindow:
    """Capture this process cgroup's counters immediately around one command.

    The observer socket is optional for Assay consumers.  B105 provides it,
    which makes an unavailable pre/post sample an explicit unknown rather than
    a claim that the run was pressure-free.
    """

    def __init__(
        self,
        *,
        observer_socket_path: str | None,
        counters_reader: Callable[..., resource_limits.ResourceLimitCounters] = (
            resource_limits.read_candidate_resource_counters
        ),
        pressure_reader: Callable[[int], Mapping[str, Any]] = (
            liveness_resources.read_liveness_resources
        ),
        pid: int | None = None,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        if observer_socket_path is not None and (
            not isinstance(observer_socket_path, str) or not observer_socket_path
        ):
            raise ValueError("observer_socket_path must be None or a non-empty string")
        if not callable(counters_reader) or not callable(pressure_reader):
            raise TypeError("contention readers must be callable")
        if not callable(monotonic_ns):
            raise TypeError("monotonic_ns must be callable")
        if pid is not None and (type(pid) is not int or pid < 1):
            raise ValueError("pid must be a positive integer or None")
        self.observer_socket_path = observer_socket_path
        self.counters_reader = counters_reader
        self.pressure_reader = pressure_reader
        self.monotonic_ns = monotonic_ns
        self.pid = os.getpid() if pid is None else pid
        self.observation = ContentionObservation("unobserved")
        self._before_counters: resource_limits.ResourceLimitCounters | None = None
        self._before_pressure: Mapping[str, Any] | None = None
        self._before_counter_error: str | None = None
        self._before_pressure_error: str | None = None
        self._started_ns: int | None = None

    def __enter__(self) -> Self:
        if self.observer_socket_path is None:
            return self
        self._started_ns = self.monotonic_ns()
        try:
            self._before_counters = self.counters_reader(
                observer_socket_path=self.observer_socket_path
            )
        except Exception as exc:  # noqa: BLE001 -- any sampling failure is unknown evidence
            self._before_counter_error = f"before-sample-unavailable:resource-counters:{type(exc).__name__}: {exc}"
        try:
            self._before_pressure = self.pressure_reader(self.pid)
        except Exception as exc:  # noqa: BLE001 -- any sampling failure is unknown evidence
            self._before_pressure_error = (
                f"before-sample-unavailable:cgroup-pressure:{type(exc).__name__}: {exc}"
            )
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        if self.observer_socket_path is None:
            return False
        reasons: list[str] = []
        after_counters = None
        after_pressure = None
        counter_error = self._before_counter_error
        pressure_error = self._before_pressure_error
        try:
            after_counters = self.counters_reader(
                observer_socket_path=self.observer_socket_path
            )
        except Exception as sample_error:  # noqa: BLE001 -- preserve other evidence
            after_error = (
                f"after-sample-unavailable:resource-counters:"
                f"{type(sample_error).__name__}: {sample_error}"
            )
            counter_error = (
                f"{counter_error}; {after_error}"
                if counter_error is not None
                else after_error
            )
        try:
            after_pressure = self.pressure_reader(self.pid)
        except Exception as sample_error:  # noqa: BLE001 -- preserve other evidence
            after_error = (
                f"after-sample-unavailable:cgroup-pressure:"
                f"{type(sample_error).__name__}: {sample_error}"
            )
            pressure_error = (
                f"{pressure_error}; {after_error}"
                if pressure_error is not None
                else after_error
            )
        ended_ns = self.monotonic_ns()

        limit_evidence = None
        if counter_error is not None:
            reasons.append(counter_error)
        elif self._before_counters is not None and after_counters is not None:
            try:
                limit_evidence = resource_limits.ResourceLimitEvidence.between(
                    self._before_counters, after_counters
                )
            except Exception as sample_error:  # noqa: BLE001 -- preserve pressure evidence
                reasons.append(
                    "resource-counter-comparison-unavailable:"
                    f"{type(sample_error).__name__}: {sample_error}"
                )
        else:
            reasons.append("resource-counter-sample-unavailable:missing-snapshot")

        pressure_status = "unknown"
        pressure_deltas: dict[str, int] = {}
        if pressure_error is not None:
            reasons.append(pressure_error)
        elif self._before_pressure is not None and after_pressure is not None:
            pressure_status, pressure_deltas = (
                liveness_resources.compare_cgroup_resource_snapshots(
                    self._before_pressure, after_pressure
                )
            )
            if pressure_status == "unknown":
                reasons.append("cgroup-pressure-window-unavailable")
        else:
            reasons.append("cgroup-pressure-window-unavailable")

        resource_limit_hit = bool(
            limit_evidence is not None and limit_evidence.limit_hit
        )
        if resource_limit_hit and limit_evidence is not None:
            reasons.extend(
                f"{name}={counter.delta}"
                for name, counter in (
                    ("pids_events_max", limit_evidence.pids_events_max),
                    ("memory_events_max", limit_evidence.memory_events_max),
                    ("memory_events_oom", limit_evidence.memory_events_oom),
                    ("memory_events_oom_kill", limit_evidence.memory_events_oom_kill),
                    (
                        "memory_events_oom_group_kill",
                        limit_evidence.memory_events_oom_group_kill,
                    ),
                )
                if counter.delta > 0
            )
        if pressure_status == "stalled":
            reasons.extend(
                f"{name}={value}" for name, value in sorted(pressure_deltas.items())
            )

        throttled = any(
            pressure_deltas.get(name, 0) > 0
            for name in ("cgroup_cpu.nr_throttled", "cgroup_cpu.throttled_usec")
        )
        started_ns = ended_ns if self._started_ns is None else self._started_ns
        duration_us = max(0, ended_ns - started_ns) // 1_000
        psi_threshold_us = max(
            _PSI_MINIMUM_STALL_US,
            int(duration_us * _PSI_PRESSURE_FRACTION),
        )
        material_psi = any(
            name.startswith("cgroup_psi.") and value >= psi_threshold_us
            for name, value in pressure_deltas.items()
        )
        if resource_limit_hit or throttled or material_psi:
            self.observation = ContentionObservation(
                "affected",
                resource_limit_hit=resource_limit_hit,
                reasons=tuple(reasons),
            )
        elif limit_evidence is not None and pressure_status in {"clear", "stalled"}:
            self.observation = ContentionObservation("clear", reasons=tuple(reasons))
        else:
            self.observation = ContentionObservation("unknown", reasons=tuple(reasons))
        return False


def withhold_canary_result(
    observation: ContentionObservation,
    *,
    outcome: Outcome,
    expected_failure: bool = False,
    require_observation: bool = False,
) -> bool:
    """Whether a canary result is unclassifiable from its resource window.

    A cgroup OOM or pids-limit event can directly prevent a command from
    completing its assertion. An unavailable observation does not overturn a
    completed pass or a cause-proven transformed failure, but a required
    unavailable sample prevents classifying a failed control or unproven
    failure. Consumers without an observer retain their existing behavior
    unless the registered gate requires attribution. Material PSI/throttling
    with an unexpected failed assertion makes that attempt inconclusive. The
    caller may exempt a transformed failure only after checking positive
    evidence for the intended canary effect; an R0 ``COMMAND_FAILED`` reason
    alone is too broad for import-break. Direct resource-limit events always
    withhold the result, even when that evidence is present.
    Command timeouts and other incomplete outcomes are already infrastructure.
    """
    if not isinstance(observation, ContentionObservation):
        raise TypeError("observation must be a ContentionObservation")
    if not isinstance(outcome, Outcome):
        raise TypeError("outcome must be an Outcome")
    if type(expected_failure) is not bool:
        raise TypeError("expected_failure must be a bool")
    if type(require_observation) is not bool:
        raise TypeError("require_observation must be a bool")
    if observation.resource_limit_hit:
        return True
    if outcome is not Outcome.FAIL or expected_failure:
        return False
    if observation.status == "unobserved":
        return require_observation
    return observation.status in {"affected", "unknown"}


def withhold_r0_result(
    observation: ContentionObservation,
    *,
    outcome: Outcome,
    require_observation: bool = False,
) -> bool:
    """Whether an R0 result is unclassifiable from its resource window.

    A direct pids/OOM event makes the R0 measurement incomplete even if its
    command exited successfully. A failed command under material pressure or
    an incomplete required observation is retried as infrastructure. PSI and
    throttling alone never withhold a completed pass. Command-budget expiry is
    already an incomplete outcome.
    """
    if not isinstance(observation, ContentionObservation):
        raise TypeError("observation must be a ContentionObservation")
    if not isinstance(outcome, Outcome):
        raise TypeError("outcome must be an Outcome")
    if type(require_observation) is not bool:
        raise TypeError("require_observation must be a bool")
    if observation.resource_limit_hit:
        return True
    if outcome is not Outcome.FAIL:
        return False
    return observation.status in {"affected", "unknown"} or (
        require_observation and observation.status == "unobserved"
    )


def withhold_r1_result(
    observation: ContentionObservation,
    *,
    outcome: Outcome,
) -> bool:
    """Whether an R1 coverage claim is incomplete under its R0 window.

    A direct pids/OOM event can truncate R0 or its coverage artifact and
    withholds either outcome. PSI/throttling retain a completed coverage
    claim; a failed claim under material pressure or an unavailable required
    observation is retried as infrastructure.
    """
    if not isinstance(observation, ContentionObservation):
        raise TypeError("observation must be a ContentionObservation")
    if not isinstance(outcome, Outcome):
        raise TypeError("outcome must be an Outcome")
    if observation.resource_limit_hit:
        return True
    return outcome is Outcome.FAIL and observation.status in {
        "affected",
        "unknown",
        "unobserved",
    }
