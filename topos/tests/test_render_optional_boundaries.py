"""Public output contracts for absent renderer data."""

from __future__ import annotations

from topos.render import render_query, render_report


def _meta(shape: str) -> dict[str, object]:
    return {
        "shape": shape,
        "projection": "flat",
        "visibility": "all",
        "source": {"kind": "recording"},
        "requested_window": None,
        "observed_start_ts": None,
        "observed_end_ts": None,
        "sample_count": 0,
        "coverage": {"frames": 0, "span_s": 0.0, "gap_count": 0, "complete": True},
        "freshness": {"newest_ts": None, "oldest_ts": None},
        "resets": {"count": 0},
        "eviction": {"occurred": False},
        "truncation": {"truncated": False, "policy": "error"},
    }


def test_current_and_summary_without_rows_are_explicit() -> None:
    """Empty current/summary data must be visible rather than a blank table."""
    for shape in ("current", "summary"):
        text = render_query({"meta": _meta(shape), "rows": []})

        assert "(no rows)" in text
        assert "KEY" not in text


def test_summary_zero_sample_metric_renders_missing_not_zero() -> None:
    """A requested metric with no samples is absent, not a measured zero."""
    text = render_query(
        {
            "meta": _meta("summary"),
            "rows": [
                {
                    "key": "idle.scope",
                    "metrics": {
                        "latency": {
                            "semantic": "gauge",
                            "sample_count": 0,
                            "p50": None,
                            "p95": None,
                            "max": None,
                        }
                    },
                }
            ],
        }
    )

    row = next(line for line in text.splitlines() if line.startswith("idle.scope"))
    assert row.split() == ["idle.scope", "missing", "missing", "missing"]
    assert " 0" not in row


def test_auto_window_not_detected_and_absent_assertion_actual_are_explained() -> None:
    """Optional report data retains its absence and failed auto-window decision."""
    text = render_report(
        {
            "window_mode": "auto",
            "window_detected": False,
            "profiles": [],
            "assertions": [
                {
                    "group": "idle.scope",
                    "metric": "latency",
                    "stat": "p95",
                    "op": "<=",
                    "threshold": 1.0,
                    "actual": None,
                    "passed": False,
                    "reason": "no samples",
                }
            ],
        }
    )

    assert "window: auto  detected=false  (no stable trailing window found)" in text
    assert "missing" in text
    assert "FAIL" in text
    assert "no samples" in text
