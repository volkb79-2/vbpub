"""Deterministic attribution of cgroup pressure around R0 and R3 commands."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from assay.errors import Outcome

from assay import contention, resource_limits


def _pressure(
    *,
    cpu_total: int = 0,
    host_total: int = 0,
    io_full: int = 0,
    memory_full: int = 0,
    nr_throttled: int = 0,
    throttled_usec: int = 0,
    status: str = "available",
):
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
            "memory": {"some": 0, "full": memory_full},
            "io": {"some": 0, "full": io_full},
        },
        "cgroup_cpu": {
            "nr_throttled": nr_throttled,
            "throttled_usec": throttled_usec,
        },
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
    with pytest.raises(ValueError, match="affected status must match"):
        contention.ContentionObservation("affected")
    with pytest.raises(ValueError, match="affected status must match"):
        contention.ContentionObservation("clear", resource_limit_hit=True)
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


@pytest.mark.parametrize(
    "pressure_kwargs",
    [
        pytest.param({"host_total": 20}, id="host-psi"),
        pytest.param(
            {
                "cpu_total": 5_340_037,
                "io_full": 1_578_273,
                "memory_full": 637_663,
            },
            id="cgroup-psi",
        ),
        pytest.param(
            {"nr_throttled": 1, "throttled_usec": 500},
            id="cpu-throttling",
        ),
    ],
)
def test_psi_and_throttling_never_withhold_a_failed_r0(
    monkeypatch, pressure_kwargs
):
    window = _window(
        monkeypatch,
        pressure=(_pressure(), _pressure(**pressure_kwargs)),
    )
    with window:
        pass
    assert window.observation.status == "clear"
    assert not contention.withhold_r0_result(
        window.observation, outcome=Outcome.FAIL
    )


def test_contention_window_marks_a_resource_limit_event_as_affected(monkeypatch):
    window = _window(monkeypatch, hit=True)
    with window:
        pass
    assert window.observation == contention.ContentionObservation(
        "affected",
        resource_limit_hit=True,
        reasons=("pids_events_max=1",),
    )


def test_direct_limit_event_is_preserved_when_pressure_sample_is_unavailable(
    monkeypatch,
):
    pressure_values = iter(
        (
            _pressure(),
            OSError("pressure snapshot unavailable"),
        )
    )

    def pressure_reader(_pid):
        value = next(pressure_values)
        if isinstance(value, Exception):
            raise value
        return value

    window = _window(monkeypatch, hit=True)
    window.pressure_reader = pressure_reader
    with window:
        pass

    assert window.observation.status == "affected"
    assert window.observation.resource_limit_hit
    assert "pids_events_max=1" in window.observation.reasons
    assert any(
        reason.startswith("after-sample-unavailable:cgroup-pressure:")
        for reason in window.observation.reasons
    )


def test_only_unavailable_direct_counters_make_the_window_unknown(monkeypatch):
    before_failure = contention.ContentionWindow(
        observer_socket_path="/observer.sock",
        counters_reader=lambda **kwargs: (_ for _ in ()).throw(OSError("before")),
        pressure_reader=lambda pid: _pressure(),
    )
    with before_failure:
        pass
    assert before_failure.observation.status == "unknown"
    assert before_failure.observation.reasons[0].startswith(
        "before-sample-unavailable:"
    )

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
        "clear", reasons=("cgroup-pressure-window-unavailable",)
    )
    assert not contention.withhold_r0_result(
        unknown_window.observation,
        outcome=Outcome.FAIL,
        require_observation=True,
    )


def test_contention_window_records_a_failed_before_pressure_sample(monkeypatch):
    window = _window(monkeypatch)
    pressure_values = iter((OSError("before pressure unavailable"), _pressure()))

    def pressure_reader(_pid):
        value = next(pressure_values)
        if isinstance(value, Exception):
            raise value
        return value

    window.pressure_reader = pressure_reader
    with window:
        pass

    assert window.observation.status == "clear"
    assert window.observation.reasons == (
        "before-sample-unavailable:cgroup-pressure:OSError: before pressure unavailable",
    )


def test_contention_window_records_a_failed_resource_counter_comparison(monkeypatch):
    window = _window(monkeypatch)

    def fail_comparison(cls, before, after):
        raise OSError("counter identities changed")

    monkeypatch.setattr(
        resource_limits.ResourceLimitEvidence,
        "between",
        classmethod(fail_comparison),
    )
    with window:
        pass

    assert window.observation.status == "unknown"
    assert window.observation.reasons == (
        "resource-counter-comparison-unavailable:OSError: counter identities changed",
    )


@pytest.mark.parametrize(
    ("limits", "pressure", "expected_reason"),
    [
        (
            (None, object()),
            (_pressure(), _pressure()),
            "resource-counter-sample-unavailable:missing-snapshot",
        ),
        (
            (object(), object()),
            (None, _pressure()),
            "cgroup-pressure-window-unavailable",
        ),
    ],
)
def test_missing_counter_snapshot_is_unknown_but_pressure_snapshot_is_clear(
    monkeypatch, limits, pressure, expected_reason
):
    window = _window(monkeypatch, limits=limits, pressure=pressure)
    with window:
        pass

    assert window.observation.status == (
        "unknown" if expected_reason.startswith("resource-counter") else "clear"
    )
    assert expected_reason in window.observation.reasons


@pytest.mark.parametrize(
    ("observation", "outcome", "require_observation", "withhold"),
    [
        (contention.ContentionObservation("clear"), Outcome.FAIL, False, False),
        (
            contention.ContentionObservation("affected", resource_limit_hit=True),
            Outcome.FAIL,
            False,
            True,
        ),
        (
            contention.ContentionObservation("affected", resource_limit_hit=True),
            Outcome.PASS,
            False,
            False,
        ),
        (contention.ContentionObservation("unknown"), Outcome.FAIL, False, True),
        (contention.ContentionObservation("unknown"), Outcome.PASS, False, False),
        (contention.ContentionObservation("unobserved"), Outcome.FAIL, False, False),
        (contention.ContentionObservation("unobserved"), Outcome.PASS, True, False),
        (contention.ContentionObservation("unobserved"), Outcome.FAIL, True, True),
    ],
)
def test_withhold_canary_result_for_incomplete_or_resource_limited_results(
    observation, outcome, require_observation, withhold
):
    assert (
        contention.withhold_canary_result(
            observation,
            outcome=outcome,
            require_observation=require_observation,
        )
        is withhold
    )


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


def test_gate_canary_retains_pass_without_a_resource_sample():
    assert not contention.withhold_canary_result(
        contention.ContentionObservation("unobserved"),
        outcome=Outcome.PASS,
        require_observation=True,
    )
    assert contention.withhold_canary_result(
        contention.ContentionObservation("unobserved"),
        outcome=Outcome.FAIL,
        require_observation=True,
    )


@pytest.mark.parametrize(
    ("status", "resource_limit_hit", "outcome", "withhold"),
    [
        ("clear", False, Outcome.FAIL, False),
        ("affected", True, Outcome.FAIL, True),
        ("unknown", False, Outcome.FAIL, True),
        ("unobserved", False, Outcome.FAIL, False),
        ("affected", True, Outcome.PASS, False),
        ("unobserved", False, Outcome.PASS, False),
        ("unknown", False, Outcome.PASS, False),
        ("clear", False, Outcome.PASS, False),
    ],
)
def test_withhold_r0_result_only_for_direct_limits_or_unclassifiable_failures(
    status, resource_limit_hit, outcome, withhold
):
    assert (
        contention.withhold_r0_result(
            contention.ContentionObservation(
                status, resource_limit_hit=resource_limit_hit
            ),
            outcome=outcome,
        )
        is withhold
    )

def test_limit_event_withholds_failed_r0():
    assert contention.withhold_r0_result(
        contention.ContentionObservation("affected", resource_limit_hit=True),
        outcome=Outcome.FAIL,
    )


def test_unavailable_required_sample_withholds_failed_r0():
    assert contention.withhold_r0_result(
        contention.ContentionObservation(
            "unknown", reasons=("after-sample-unavailable:resource-counters",)
        ),
        outcome=Outcome.FAIL,
        require_observation=True,
    )


def test_r0_unobserved_window_does_not_withhold_a_completed_pass():
    assert not contention.withhold_r0_result(
        contention.ContentionObservation("unobserved"),
        outcome=Outcome.PASS,
    )
    assert (
        contention.withhold_r0_result(
            contention.ContentionObservation("unobserved"),
            outcome=Outcome.PASS,
            require_observation=True,
        )
        is False
    )


def test_withhold_r0_result_validates_inputs():
    with pytest.raises(TypeError, match="observation must"):
        contention.withhold_r0_result("clear", outcome=Outcome.FAIL)
    with pytest.raises(TypeError, match="outcome must"):
        contention.withhold_r0_result(
            contention.ContentionObservation("clear"),
            outcome="FAIL",
        )
    with pytest.raises(TypeError, match="require_observation must be a bool"):
        contention.withhold_r0_result(
            contention.ContentionObservation("unobserved"),
            outcome=Outcome.FAIL,
            require_observation=1,
        )


@pytest.mark.parametrize(
    ("observation", "outcome", "withhold"),
    [
        (contention.ContentionObservation("clear"), Outcome.FAIL, False),
        (
            contention.ContentionObservation("affected", resource_limit_hit=True),
            Outcome.FAIL,
            True,
        ),
        (
            contention.ContentionObservation("affected", resource_limit_hit=True),
            Outcome.PASS,
            False,
        ),
        (contention.ContentionObservation("unknown"), Outcome.PASS, False),
        (contention.ContentionObservation("unknown"), Outcome.FAIL, True),
        (contention.ContentionObservation("unobserved"), Outcome.FAIL, True),
    ],
)
def test_withhold_r1_result_for_direct_limits_or_unavailable_failures(
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
