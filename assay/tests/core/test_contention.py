"""Deterministic attribution of cgroup pressure around R0 and R3 commands."""

from __future__ import annotations

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


def _limit_evidence(*, hit: bool = False):
    pids = int(hit)
    return resource_limits.ResourceLimitEvidence(
        pids_events_max=resource_limits.CounterDelta(
            before=0, after=pids, delta=pids
        ),
        memory_events_max=resource_limits.CounterDelta(
            before=0, after=0, delta=0
        ),
        memory_events_oom=resource_limits.CounterDelta(
            before=0, after=0, delta=0
        ),
        memory_events_oom_kill=resource_limits.CounterDelta(
            before=0, after=0, delta=0
        ),
        memory_events_oom_group_kill=resource_limits.CounterDelta(
            before=0, after=0, delta=0
        ),
    )


def _observation(status, *, hit=False, reasons=(), pressure_us=None):
    return contention.ContentionObservation(
        status,
        limits=_limit_evidence(hit=hit) if status == "clear" else None,
        pressure_us={} if pressure_us is None else pressure_us,
        reasons=reasons,
    )


def test_contention_observation_freezes_pressure_telemetry():
    pressure_us = {"cgroup_psi.cpu.some_us": 12}
    observation = contention.ContentionObservation(
        "unobserved", pressure_us=pressure_us
    )

    pressure_us["cgroup_psi.cpu.some_us"] = 99
    assert observation.pressure_us["cgroup_psi.cpu.some_us"] == 12
    with pytest.raises(TypeError):
        observation.pressure_us["cgroup_psi.cpu.some_us"] = 99


def _window(monkeypatch, *, limits=None, pressure=None, hit=False):
    if limits is None:
        limits = (object(), object())
    if pressure is None:
        pressure = (_pressure(), _pressure())
    limit_values = iter(limits)
    pressure_values = iter(pressure)

    def limit_evidence(*_args, **_kwargs):
        return _limit_evidence(hit=hit)

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
    with pytest.raises(ValueError, match="limits must be"):
        contention.ContentionObservation("clear", limits=object())
    with pytest.raises(ValueError, match="status is invalid"):
        contention.ContentionObservation("affected")
    with pytest.raises(ValueError, match="clear status must match"):
        contention.ContentionObservation("clear")
    with pytest.raises(ValueError, match="clear status must match"):
        contention.ContentionObservation("unavailable", limits=_limit_evidence())
    with pytest.raises(ValueError, match="pressure_us must map"):
        contention.ContentionObservation("unobserved", pressure_us=3)
    with pytest.raises(ValueError, match="pressure_us must map"):
        contention.ContentionObservation("unobserved", pressure_us={"": 1})
    with pytest.raises(ValueError, match="pressure_us must map"):
        contention.ContentionObservation("unobserved", pressure_us={"psi": True})
    with pytest.raises(ValueError, match="pressure_us must map"):
        contention.ContentionObservation("unobserved", pressure_us={"psi": -1})
    with pytest.raises(ValueError, match="non-empty strings"):
        _observation("clear", reasons=("",))
    with pytest.raises(ValueError, match="non-empty strings"):
        _observation("clear", reasons=["pressure"])
    with pytest.raises(ValueError, match="non-empty strings"):
        _observation("clear", reasons=(3,))


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
    assert window.observation == _observation("clear")


@pytest.mark.parametrize(
    ("pressure_kwargs", "expected_pressure_us"),
    [
        pytest.param({"host_total": 20}, {}, id="host-psi"),
        pytest.param(
            {
                "cpu_total": 5_340_037,
                "io_full": 1_578_273,
                "memory_full": 637_663,
            },
            {
                "cgroup_psi.cpu.some_us": 5_340_037,
                "cgroup_psi.memory.full_us": 637_663,
                "cgroup_psi.io.full_us": 1_578_273,
            },
            id="cgroup-psi",
        ),
        pytest.param(
            {"nr_throttled": 1, "throttled_usec": 500},
            {"cgroup_cpu.throttled_usec": 500},
            id="cpu-throttling",
        ),
    ],
)
def test_psi_and_throttling_never_withhold_a_failed_r0(
    monkeypatch, pressure_kwargs, expected_pressure_us
):
    window = _window(
        monkeypatch,
        pressure=(_pressure(), _pressure(**pressure_kwargs)),
    )
    with window:
        pass
    assert window.observation.status == "clear"
    assert window.observation.pressure_us == expected_pressure_us
    assert not contention.withhold_r0_result(
        window.observation, outcome=Outcome.FAIL
    )


def test_contention_window_keeps_resource_limit_evidence(monkeypatch):
    window = _window(monkeypatch, hit=True)
    with window:
        pass
    assert window.observation == _observation("clear", hit=True)
    assert window.observation.limits.limit_hit


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

    assert window.observation.status == "clear"
    assert window.observation.limits.limit_hit
    assert any(
        reason.startswith("after-sample-unavailable:cgroup-pressure:")
        for reason in window.observation.reasons
    )


def test_only_unavailable_direct_counters_make_the_window_unavailable(monkeypatch):
    before_failure = contention.ContentionWindow(
        observer_socket_path="/observer.sock",
        counters_reader=lambda **kwargs: (_ for _ in ()).throw(OSError("before")),
        pressure_reader=lambda pid: _pressure(),
    )
    with before_failure:
        pass
    assert before_failure.observation.status == "unavailable"
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
    assert after_failure.observation.status == "unavailable"
    assert after_failure.observation.reasons[0].startswith("after-sample-unavailable:")

    unknown_window = _window(
        monkeypatch,
        pressure=(_pressure(), _pressure(status="unavailable")),
    )
    with unknown_window:
        pass
    assert unknown_window.observation == _observation(
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


def test_contention_window_preserves_before_and_after_sample_errors():
    def fail_sample(*args, **kwargs):
        raise OSError("observer unavailable")

    window = contention.ContentionWindow(
        observer_socket_path="/observer.sock",
        counters_reader=fail_sample,
        pressure_reader=fail_sample,
        pid=41,
    )
    with window:
        pass

    assert window.observation.status == "unavailable"
    assert len(window.observation.reasons) == 2
    assert window.observation.reasons[0].startswith(
        "before-sample-unavailable:resource-counters:"
    )
    assert "; after-sample-unavailable:resource-counters:" in (
        window.observation.reasons[0]
    )
    assert window.observation.reasons[1].startswith(
        "before-sample-unavailable:cgroup-pressure:"
    )
    assert "; after-sample-unavailable:cgroup-pressure:" in (
        window.observation.reasons[1]
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

    assert window.observation.status == "unavailable"
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
def test_missing_counter_snapshot_is_unavailable_but_pressure_snapshot_is_clear(
    monkeypatch, limits, pressure, expected_reason
):
    window = _window(monkeypatch, limits=limits, pressure=pressure)
    with window:
        pass

    assert window.observation.status == (
        "unavailable" if expected_reason.startswith("resource-counter") else "clear"
    )
    assert expected_reason in window.observation.reasons


@pytest.mark.parametrize(
    ("observation", "outcome", "require_observation", "withhold"),
    [
        (_observation("clear"), Outcome.FAIL, False, False),
        (_observation("clear", hit=True), Outcome.FAIL, False, True),
        (_observation("clear", hit=True), Outcome.PASS, False, False),
        (_observation("unavailable"), Outcome.FAIL, False, True),
        (_observation("unavailable"), Outcome.PASS, False, False),
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
            _observation("clear"), outcome="FAIL"
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
        ("clear", True, Outcome.FAIL, True),
        ("unavailable", False, Outcome.FAIL, True),
        ("unobserved", False, Outcome.FAIL, False),
        ("clear", True, Outcome.PASS, False),
        ("unobserved", False, Outcome.PASS, False),
        ("unavailable", False, Outcome.PASS, False),
        ("clear", False, Outcome.PASS, False),
    ],
)
def test_withhold_r0_result_only_for_direct_limits_or_unclassifiable_failures(
    status, resource_limit_hit, outcome, withhold
):
    assert (
        contention.withhold_r0_result(
            _observation(status, hit=resource_limit_hit),
            outcome=outcome,
        )
        is withhold
    )

def test_limit_event_withholds_failed_r0():
    assert contention.withhold_r0_result(
        _observation("clear", hit=True),
        outcome=Outcome.FAIL,
    )


def test_unavailable_required_sample_withholds_failed_r0():
    assert contention.withhold_r0_result(
        _observation(
            "unavailable", reasons=("after-sample-unavailable:resource-counters",)
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
            _observation("clear"),
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
        (_observation("clear"), Outcome.FAIL, False),
        (_observation("clear", hit=True), Outcome.FAIL, True),
        (_observation("clear", hit=True), Outcome.PASS, False),
        (_observation("unavailable"), Outcome.PASS, False),
        (_observation("unavailable"), Outcome.FAIL, True),
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
            _observation("clear"), outcome="FAIL"
        )
