"""Sample cgroup resource events around one completed Assay command window.

PSI and CPU throttling are advisory. Direct resource-limit counters and sample
availability are retained for the caller's verdict decision.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import field
from types import MappingProxyType
from typing import Any, Literal, Self

from . import liveness_resources, resource_limits
from .errors import Outcome
from .records import positional_record


@positional_record
class ContentionObservation:
    status: Literal["unobserved", "clear", "unavailable"]
    limits: resource_limits.ResourceLimitEvidence | None = None
    pressure_us: Mapping[str, int] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in {"unobserved", "clear", "unavailable"}:
            raise ValueError("contention status is invalid")
        if self.limits is not None and not isinstance(
            self.limits, resource_limits.ResourceLimitEvidence
        ):
            raise ValueError("limits must be ResourceLimitEvidence or None")
        if (self.status == "clear") != (self.limits is not None):
            raise ValueError("clear status must match the presence of limits evidence")
        if not isinstance(self.pressure_us, Mapping) or any(
            not isinstance(name, str)
            or not name
            or type(value) is not int
            or value < 0
            for name, value in self.pressure_us.items()
        ):
            raise ValueError(
                "pressure_us must map non-empty names to non-negative integers"
            )
        object.__setattr__(self, "pressure_us", MappingProxyType(dict(self.pressure_us)))
        if type(self.reasons) is not tuple or any(
            not isinstance(reason, str) or not reason for reason in self.reasons
        ):
            raise ValueError("contention reasons must be non-empty strings")


class ContentionWindow:
    """Capture this process cgroup's counters immediately around one command.

    The observer socket is optional for Assay consumers. B105 provides it,
    which makes an unavailable direct-counter pre/post sample explicit.
    PSI and CPU-stat samples remain advisory telemetry and do not affect
    classification.
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
        self._before_counter_error: str | None = None
        self._before_pressure_error: str | None = None

    def __enter__(self) -> Self:
        if self.observer_socket_path is None:
            return self
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
            pressure_deltas = {
                name: delta
                for name, delta in pressure_deltas.items()
                if name != "cgroup_cpu.nr_throttled"
            }
            if pressure_status == "unknown":
                reasons.append("cgroup-pressure-window-unavailable")
        else:
            reasons.append("cgroup-pressure-window-unavailable")

        self.observation = ContentionObservation(
            "clear" if limit_evidence is not None else "unavailable",
            limits=limit_evidence,
            pressure_us=pressure_deltas,
            reasons=tuple(reasons),
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
    completing when the command failed. An unavailable direct-counter sample
    prevents classifying a failed control or transform; an unobserved window
    does so only when observation is required. Completed passes remain usable
    without a sample. PSI and CPU throttling do not change the result. Command
    timeouts and other incomplete outcomes are already infrastructure.
    """
    if not isinstance(observation, ContentionObservation):
        raise TypeError("observation must be a ContentionObservation")
    if not isinstance(outcome, Outcome):
        raise TypeError("outcome must be an Outcome")
    if type(require_observation) is not bool:
        raise TypeError("require_observation must be a bool")
    if outcome is not Outcome.FAIL:
        return False
    if observation.limits is not None and observation.limits.limit_hit:
        return True
    if observation.status == "unobserved":
        return require_observation
    return observation.status == "unavailable"


def withhold_r0_result(
    observation: ContentionObservation,
    *,
    outcome: Outcome,
    require_observation: bool = False,
) -> bool:
    """Whether an R0 result is unclassifiable from its resource window.

    A direct pids/OOM event makes a failed R0 measurement incomplete.
    Completed PASS results stay final. A failed command with an unavailable
    direct-counter observation is incomplete. PSI and throttling do not affect
    the result. Command-budget expiry is already an incomplete outcome.
    """
    if not isinstance(observation, ContentionObservation):
        raise TypeError("observation must be a ContentionObservation")
    if not isinstance(outcome, Outcome):
        raise TypeError("outcome must be an Outcome")
    if type(require_observation) is not bool:
        raise TypeError("require_observation must be a bool")
    if outcome is not Outcome.FAIL:
        return False
    if observation.limits is not None and observation.limits.limit_hit:
        return True
    return observation.status == "unavailable" or (
        require_observation and observation.status == "unobserved"
    )


def withhold_r1_result(
    observation: ContentionObservation,
    *,
    outcome: Outcome,
) -> bool:
    """Whether an R1 coverage claim is incomplete under its R0 window.

    A direct pids/OOM event can make a failed R0 command or coverage result
    incomplete. Completed PASS results stay final. PSI and throttling do not
    affect a completed coverage claim; an unavailable direct-counter
    observation withholds a failed claim.
    """
    if not isinstance(observation, ContentionObservation):
        raise TypeError("observation must be a ContentionObservation")
    if not isinstance(outcome, Outcome):
        raise TypeError("outcome must be an Outcome")
    if outcome is not Outcome.FAIL:
        return False
    return (
        (observation.limits is not None and observation.limits.limit_hit)
        or observation.status in {"unavailable", "unobserved"}
    )
