"""Public report parsing, assertion, and serialization boundary contracts."""

from __future__ import annotations

import pytest

from topos.report import (
    Assertion,
    GroupProfile,
    evaluate_assertions,
    parse_assert_spec,
    report_to_jsonable,
    select_report_window,
)


def test_report_rejects_overflowing_assertion_threshold_as_non_finite() -> None:
    """A syntactically numeric threshold must still be finite to gate a report."""
    with pytest.raises(
        ValueError,
        match=r"invalid --assert value '1e999' — must be finite",
    ):
        parse_assert_spec("worker.slice:ram:p95<=1e999")


@pytest.mark.parametrize("window_spec", [None, 7])
def test_report_rejects_non_string_window_spec_before_frame_access(
    window_spec: object,
) -> None:
    """Typed window input failures are stable even when no recording frames exist."""
    with pytest.raises(
        ValueError,
        match="invalid window spec — expected 'all', 'auto', or 'last:Ns'",
    ):
        select_report_window([], window_spec=window_spec)  # type: ignore[arg-type]


def test_empty_recording_still_validates_manual_window_spelling() -> None:
    """An empty input cannot turn a malformed manual window into an all-window report."""
    with pytest.raises(
        ValueError,
        match=r"invalid window spec 'last:broken' — expected 'all' or 'last:Ns'",
    ):
        select_report_window([], window_spec="last:broken")


def test_report_serializes_assertion_breach_reason_and_rate_profile() -> None:
    """A threshold breach remains machine-readable alongside a rate-only profile."""
    profile = GroupProfile(
        key="worker.slice",
        sample_count=2,
        window_start_ts=None,
        window_end_ts=None,
        gauges={},
        rates={"io_r_bps": {"p50": 12.3456789, "p95": 30.0, "max": 30.0}},
    )
    assertion = Assertion("worker.slice", "io_r_bps", "p50", ">=", 20.0)

    results = evaluate_assertions([profile], [assertion])
    assert len(results) == 1
    assert results[0].passed is False
    assert results[0].reason == "breached: 12.3456789 >= 20.0"

    payload = report_to_jsonable([profile], assertions=results)
    assert payload == {
        "metrics_version": 1,
        "profiles": [{
            "key": "worker.slice",
            "sample_count": 2,
            "rates": {"io_r_bps": {"p50": 12.345679, "p95": 30.0, "max": 30.0}},
        }],
        "assertions": [{
            "group": "worker.slice",
            "metric": "io_r_bps",
            "stat": "p50",
            "op": ">=",
            "threshold": 20.0,
            "actual": 12.345679,
            "passed": False,
            "reason": "breached: 12.3456789 >= 20.0",
        }],
    }
