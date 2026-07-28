"""Public report contracts for empty windows and optional JSON fields."""

from __future__ import annotations

import pytest

from topos.model import Entity, EntityFrame, Frame, MetricValue
from topos.report import (
    GroupProfile,
    WindowSelection,
    detect_steady_window,
    parse_window_spec,
    report_to_jsonable,
    select_report_window,
)


def test_manual_window_on_empty_recording_is_a_valid_empty_selection() -> None:
    """A valid explicit window is retained even when there are no frames."""
    selection = select_report_window([], window_spec="last:30s")

    assert selection == WindowSelection(mode="last", window=None)


def test_parse_window_rejects_non_string_input_with_parser_contract() -> None:
    """The direct public parser does not leak an attribute error for typed input."""
    with pytest.raises(
        ValueError,
        match="invalid window spec — expected 'all' or 'last:Ns'",
    ):
        parse_window_spec(30, last_frame_ts=100.0)  # type: ignore[arg-type]


def test_short_recording_cannot_claim_an_auto_steady_window() -> None:
    """Auto detection needs its configured minimum number of frames."""
    frame = Frame(
        schema_version=1,
        ts=100.0,
        interval_s=5.0,
        host={},
        entities={
            "worker.scope": EntityFrame(
                entity=Entity("worker.scope", "scope", ""),
                metrics={"ram": MetricValue(128.0, "exact")},
            ),
        },
    )

    assert detect_steady_window([frame], min_frames=2) is None


def test_auto_report_omits_undetected_bounds_and_preserves_null_metric_stat() -> None:
    """Optional report fields are omitted while unknown metric statistics stay null."""
    profile = GroupProfile(
        key="worker.scope",
        sample_count=0,
        window_start_ts=None,
        window_end_ts=None,
        gauges={"ram": {"p50": None, "p95": None, "max": None}},
        rates={},
    )

    payload = report_to_jsonable(
        [profile],
        window_selection=WindowSelection(mode="auto", window=None, detected=False),
    )

    assert payload == {
        "metrics_version": 1,
        "profiles": [{
            "key": "worker.scope",
            "sample_count": 0,
            "gauges": {"ram": {"p50": None, "p95": None, "max": None}},
        }],
        "window_mode": "auto",
        "window_detected": False,
    }
