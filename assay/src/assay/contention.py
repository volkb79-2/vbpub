"""Attribute cgroup pressure to one completed Assay command window.

The result is deliberately separate from the verdict wire format. A failed
R0/R1/R3 assertion inside a cgroup window with observed stalls is retried as
infrastructure because the failure may be time-sensitive. A completed pass is
retained unless a direct pids/OOM event occurred. An unavailable sample never
certifies an R0/R1/R3 result.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any, Self

from . import liveness_resources, resource_limits
from .errors import Outcome
from .records import positional_record


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
    ) -> None:
        if observer_socket_path is not None and (
            not isinstance(observer_socket_path, str) or not observer_socket_path
        ):
            raise ValueError("observer_socket_path must be None or a non-empty string")
        if not callable(counters_reader) or not callable(pressure_reader):
            raise TypeError("contention readers must be callable")
        if pid is not None and (type(pid) is not int or pid < 1):
            raise ValueError("pid must be a positive integer or None")
        self.observer_socket_path = observer_socket_path
        self.counters_reader = counters_reader
        self.pressure_reader = pressure_reader
        self.pid = os.getpid() if pid is None else pid
        self.observation = ContentionObservation("unobserved")
        self._before_counters: resource_limits.ResourceLimitCounters | None = None
        self._before_pressure: Mapping[str, Any] | None = None
        self._before_error: str | None = None

    def __enter__(self) -> Self:
        if self.observer_socket_path is None:
            return self
        try:
            self._before_counters = self.counters_reader(
                observer_socket_path=self.observer_socket_path
            )
            self._before_pressure = self.pressure_reader(self.pid)
        except Exception as exc:  # noqa: BLE001 -- any sampling failure is unknown evidence
            self._before_error = f"{type(exc).__name__}: {exc}"
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        if self.observer_socket_path is None:
            return False
        if self._before_error is not None:
            self.observation = ContentionObservation(
                "unknown", reasons=(f"before-sample-unavailable:{self._before_error}",)
            )
            return False
        try:
            after_counters = self.counters_reader(
                observer_socket_path=self.observer_socket_path
            )
            after_pressure = self.pressure_reader(self.pid)
            limit_evidence = resource_limits.ResourceLimitEvidence.between(
                self._before_counters, after_counters
            )
            pressure_status, pressure_deltas = (
                liveness_resources.compare_cgroup_resource_snapshots(
                    self._before_pressure, after_pressure
                )
            )
        except Exception as sample_error:  # noqa: BLE001 -- any sampling failure is unknown evidence
            self.observation = ContentionObservation(
                "unknown", reasons=(f"after-sample-unavailable:{type(sample_error).__name__}: {sample_error}",)
            )
            return False

        resource_limit_hit = limit_evidence.limit_hit
        reasons: list[str] = []
        if resource_limit_hit:
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
        if resource_limit_hit or pressure_status == "stalled":
            self.observation = ContentionObservation(
                "affected", resource_limit_hit=resource_limit_hit, reasons=tuple(reasons)
            )
        elif pressure_status == "clear":
            self.observation = ContentionObservation("clear")
        else:
            self.observation = ContentionObservation(
                "unknown", reasons=("cgroup-pressure-window-unavailable",)
            )
        return False


def withhold_canary_result(
    observation: ContentionObservation,
    *,
    outcome: Outcome,
    require_observation: bool = False,
) -> bool:
    """Whether a canary result is unclassifiable from its resource window.

    A cgroup OOM or pids-limit event can directly prevent a command from
    completing its assertion. A configured but unavailable window cannot
    support either canary half. Consumers without an observer retain their
    existing behavior unless the registered gate requires attribution. A
    measured PSI/throttling interval with a failed assertion makes that
    attempt inconclusive; an observed stall can affect a time-sensitive
    assertion, but does not overturn a completed pass by itself.
    Command timeouts and other incomplete outcomes are already infrastructure.
    """
    if not isinstance(observation, ContentionObservation):
        raise TypeError("observation must be a ContentionObservation")
    if not isinstance(outcome, Outcome):
        raise TypeError("outcome must be an Outcome")
    if type(require_observation) is not bool:
        raise TypeError("require_observation must be a bool")
    if observation.status == "unobserved":
        return require_observation
    if observation.resource_limit_hit or observation.status == "unknown":
        return True
    return outcome is Outcome.FAIL and observation.status == "affected"


def withhold_r0_result(
    observation: ContentionObservation,
    *,
    outcome: Outcome,
) -> bool:
    """Whether an R0 result is unclassifiable from its resource window.

    A direct pids/OOM event or an unavailable observation makes the R0
    measurement incomplete even if its command exited successfully. A failed
    command under measured cgroup PSI/throttling is also withheld because a
    time-sensitive assertion may have been affected. A completed pass under
    PSI/throttling alone is retained. Command-budget expiry is already an
    incomplete outcome.
    """
    if not isinstance(observation, ContentionObservation):
        raise TypeError("observation must be a ContentionObservation")
    if not isinstance(outcome, Outcome):
        raise TypeError("outcome must be an Outcome")
    if observation.resource_limit_hit or observation.status in {"unknown", "unobserved"}:
        return True
    return outcome is Outcome.FAIL and observation.status == "affected"


def withhold_r1_result(
    observation: ContentionObservation,
    *,
    outcome: Outcome,
) -> bool:
    """Whether an R1 coverage claim is incomplete under its R0 window.

    A configured but unavailable observation and a direct pids/OOM event
    cannot certify the coverage artifact, even when its computed claim is a
    pass. PSI/throttling alone retain a completed pass; if coverage fails in
    that same window, the report may reflect an interrupted or delayed test
    path and the claim is retried as infrastructure.
    """
    if not isinstance(observation, ContentionObservation):
        raise TypeError("observation must be a ContentionObservation")
    if not isinstance(outcome, Outcome):
        raise TypeError("outcome must be an Outcome")
    if observation.resource_limit_hit or observation.status in {"unknown", "unobserved"}:
        return True
    return outcome is Outcome.FAIL and observation.status == "affected"
