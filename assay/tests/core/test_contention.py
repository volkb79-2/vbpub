"""Deterministic attribution of cgroup pressure around R0 and R3 commands."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from assay.errors import Outcome

from assay import contention, resource_limits


def _pressure(*, cpu_total: int = 0, host_total: int = 0, status: str = "available"):
    return {
        "schema_version": 1,
        "status": status,
        "cgroup_identity": "cgroup-a",
        "host_psi": {
            "cpu": {"some": host_total},
            "memory": {"some": 0, "full": 0},
            "io": {"some": 0, "full": 0},
        },
        "cgroup_psi": {
            "cpu": {"some": cpu_total},
            "memory": {"some": 0, "full": 0},
            "io": {"some": 0, "full": 0},
        },
        "cgroup_cpu": {"nr_throttled": 0, "throttled_usec": 0},
    }


def _window(monkeypatch, *, limits=None, pressure=None, hit=False):
    if limits is None:
        limits = (object(), object())
    if pressure is None:
        pressure = (_pressure(), _pressure())
    limit_values = iter(limits)
    pressure_values = iter(pressure)

    def limit_evidence(*_args, **_kwargs):
        zero = SimpleNamespace(delta=0)
        one = SimpleNamespace(delta=1)
        return SimpleNamespace(
            limit_hit=hit,
            pids_events_max=one if hit else zero,
            memory_events_max=zero,
            memory_events_oom=zero,
            memory_events_oom_kill=zero,
            memory_events_oom_group_kill=zero,
        )

    monkeypatch.setattr(
        resource_limits.ResourceLimitEvidence,
        "between",
        classmethod(lambda cls, before, after: limit_evidence(before, after)),
    )
    return contention.ContentionWindow(
        observer_socket_path="/observer.sock",
        counters_reader=lambda **kwargs: next(limit_values),
        pressure_reader=lambda pid: next(pressure_values),
        pid=41,
    )


def test_contention_observation_rejects_malformed_values():
    with pytest.raises(ValueError, match="status is invalid"):
        contention.ContentionObservation("busy")
    with pytest.raises(ValueError, match="must be a bool"):
        contention.ContentionObservation("clear", resource_limit_hit=1)
    with pytest.raises(ValueError, match="non-empty strings"):
        contention.ContentionObservation("clear", reasons=("",))
    with pytest.raises(ValueError, match="non-empty strings"):
        contention.ContentionObservation("clear", reasons=["pressure"])
    with pytest.raises(ValueError, match="non-empty strings"):
        contention.ContentionObservation("clear", reasons=(3,))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"observer_socket_path": ""},
        {"observer_socket_path": 3},
        {"observer_socket_path": None, "pid": 0},
        {"observer_socket_path": None, "pid": True},
    ],
)
def test_contention_window_rejects_invalid_configuration(kwargs):
    with pytest.raises(ValueError):
        contention.ContentionWindow(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"observer_socket_path": None, "counters_reader": 3},
        {"observer_socket_path": None, "pressure_reader": 3},
    ],
)
def test_contention_window_rejects_non_callable_readers(kwargs):
    with pytest.raises(TypeError, match="must be callable"):
        contention.ContentionWindow(**kwargs)


def test_contention_window_without_observer_is_explicitly_unobserved(monkeypatch):
    def must_not_read(*args, **kwargs):
        pytest.fail("unconfigured observation must not read host cgroups")

    window = contention.ContentionWindow(
        observer_socket_path=None,
        counters_reader=must_not_read,
        pressure_reader=must_not_read,
    )
    with window:
        pass
    assert window.observation == contention.ContentionObservation("unobserved")


def test_contention_window_accepts_a_clear_cgroup_window(monkeypatch):
    window = _window(monkeypatch)
    with window:
        pass
    assert window.observation == contention.ContentionObservation("clear")


def test_contention_window_ignores_host_pressure_but_detects_own_cgroup_pressure(monkeypatch):
    window = _window(
        monkeypatch,
        pressure=(_pressure(host_total=0), _pressure(host_total=20)),
    )
    with window:
        pass
    assert window.observation == contention.ContentionObservation("clear")

    window = _window(
        monkeypatch,
        pressure=(_pressure(), _pressure(cpu_total=1)),
    )
    with window:
        pass
    assert window.observation.status == "affected"
    assert window.observation.reasons == ("cgroup_psi.cpu.some_us=1",)


def test_contention_window_marks_a_resource_limit_event_as_affected(monkeypatch):
    window = _window(monkeypatch, hit=True)
    with window:
        pass
    assert window.observation == contention.ContentionObservation(
        "affected",
        resource_limit_hit=True,
        reasons=("pids_events_max=1",),
    )


def test_contention_window_fails_closed_when_either_sample_is_unavailable(monkeypatch):
    before_failure = contention.ContentionWindow(
        observer_socket_path="/observer.sock",
        counters_reader=lambda **kwargs: (_ for _ in ()).throw(OSError("before")),
        pressure_reader=lambda pid: _pressure(),
    )
    with before_failure:
        pass
    assert before_failure.observation.status == "unknown"
    assert before_failure.observation.reasons[0].startswith("before-sample-unavailable:")

    after_values = iter((object(),))
    after_failure = contention.ContentionWindow(
        observer_socket_path="/observer.sock",
        counters_reader=lambda **kwargs: next(after_values),
        pressure_reader=lambda pid: _pressure(status="unavailable"),
    )
    with after_failure:
        pass
    assert after_failure.observation.status == "unknown"
    assert after_failure.observation.reasons[0].startswith("after-sample-unavailable:")

    unknown_window = _window(
        monkeypatch,
        pressure=(_pressure(), _pressure(status="unavailable")),
    )
    with unknown_window:
        pass
    assert unknown_window.observation == contention.ContentionObservation(
        "unknown", reasons=("cgroup-pressure-window-unavailable",)
    )


@pytest.mark.parametrize(
    ("observation", "outcome", "withhold"),
    [
        (contention.ContentionObservation("clear"), Outcome.FAIL, False),
        (contention.ContentionObservation("affected"), Outcome.FAIL, True),
        (contention.ContentionObservation("affected", resource_limit_hit=True), Outcome.FAIL, True),
        (contention.ContentionObservation("unknown"), Outcome.FAIL, True),
        (contention.ContentionObservation("unknown"), Outcome.PASS, True),
        (contention.ContentionObservation("affected", resource_limit_hit=True), Outcome.PASS, True),
        (contention.ContentionObservation("affected"), Outcome.PASS, False),
        (contention.ContentionObservation("unobserved"), Outcome.PASS, True),
        (contention.ContentionObservation("unobserved"), Outcome.FAIL, False),
    ],
)
def test_withhold_canary_result_for_incomplete_or_affected_failures(
    observation, outcome, withhold
):
    assert contention.withhold_canary_result(
        observation, outcome=outcome
    ) is withhold


def test_withhold_canary_result_validates_inputs():
    with pytest.raises(TypeError, match="observation must"):
        contention.withhold_canary_result("clear", outcome=Outcome.FAIL)
    with pytest.raises(TypeError, match="outcome must"):
        contention.withhold_canary_result(
            contention.ContentionObservation("clear"), outcome="FAIL"
        )
    with pytest.raises(TypeError, match="require_observation must be a bool"):
        contention.withhold_canary_result(
            contention.ContentionObservation("unobserved"),
            outcome=Outcome.PASS,
            require_observation=1,
        )


def test_gate_canary_must_not_certify_without_a_required_resource_sample():
    assert contention.withhold_canary_result(
        contention.ContentionObservation("unobserved"),
        outcome=Outcome.PASS,
        require_observation=True,
    )
    assert not contention.withhold_canary_result(
        contention.ContentionObservation("unobserved"),
        outcome=Outcome.PASS,
    )


@pytest.mark.parametrize(
    ("status", "outcome", "withhold"),
    [
        ("clear", Outcome.FAIL, False),
        ("affected", Outcome.FAIL, True),
        ("unknown", Outcome.FAIL, True),
        ("affected", Outcome.PASS, False),
        ("unobserved", Outcome.FAIL, True),
        ("unknown", Outcome.PASS, True),
        ("clear", Outcome.PASS, False),
    ],
)
def test_withhold_r0_result_only_for_unclassifiable_shapes(
    status, outcome, withhold
):
    assert contention.withhold_r0_result(
        contention.ContentionObservation(status),
        outcome=outcome,
    ) is withhold


def test_r0_resource_limit_event_withholds_the_result():
    assert contention.withhold_r0_result(
        contention.ContentionObservation("affected", resource_limit_hit=True),
        outcome=Outcome.FAIL,
    )


def test_r0_unobserved_window_withholds_even_a_completed_pass():
    assert contention.withhold_r0_result(
        contention.ContentionObservation("unobserved"),
        outcome=Outcome.PASS,
    )


def test_withhold_r0_result_validates_inputs():
    with pytest.raises(TypeError, match="observation must"):
        contention.withhold_r0_result(
            "clear", outcome=Outcome.FAIL
        )
    with pytest.raises(TypeError, match="outcome must"):
        contention.withhold_r0_result(
            contention.ContentionObservation("clear"),
            outcome="FAIL",
        )


@pytest.mark.parametrize(
    ("observation", "outcome", "withhold"),
    [
        (contention.ContentionObservation("clear"), Outcome.FAIL, False),
        (contention.ContentionObservation("affected"), Outcome.FAIL, True),
        (contention.ContentionObservation("affected"), Outcome.PASS, False),
        (
            contention.ContentionObservation("affected", resource_limit_hit=True),
            Outcome.PASS,
            True,
        ),
        (contention.ContentionObservation("unknown"), Outcome.PASS, True),
        (contention.ContentionObservation("unobserved"), Outcome.FAIL, True),
    ],
)
def test_withhold_r1_result_for_unclassifiable_or_pressure_affected_claims(
    observation, outcome, withhold
):
    assert contention.withhold_r1_result(observation, outcome=outcome) is withhold


def test_withhold_r1_result_validates_inputs():
    with pytest.raises(TypeError, match="observation must"):
        contention.withhold_r1_result("clear", outcome=Outcome.FAIL)
    with pytest.raises(TypeError, match="outcome must"):
        contention.withhold_r1_result(
            contention.ContentionObservation("clear"), outcome="FAIL"
        )

