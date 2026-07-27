"""Semantic boundary tests for the canonical text renderer.

These fixtures are already JSONable renderer inputs: the tests deliberately
exercise presentation-only edge values without involving collection or query
aggregation.
"""

from __future__ import annotations

from topos.render import render_query, render_report


def _meta(shape: str) -> dict[str, object]:
    return {
        "shape": shape,
        "projection": "flat",
        "visibility": "all",
        "source": {"kind": "recording", "detail": {"path": "fixture.jsonl", "mode": "offline"}},
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


def test_raw_series_keeps_special_scalars_and_typed_unlimited_distinct() -> None:
    """A raw terminal view must not disguise exceptional stored values as absent."""
    text = render_query(
        {
            "meta": _meta("raw"),
            "rows": [
                {
                    "key": "unit.scope",
                    "metric": "latency",
                    "semantic": "gauge",
                    "points": [
                        {"ts": 1.0, "value": float("nan"), "src": "exact"},
                        {"ts": 2.0, "value": float("inf"), "src": "exact"},
                        {"ts": 3.0, "value": float("-inf"), "src": "exact"},
                        {"ts": 4.0, "value": None, "src": "unlimited"},
                    ],
                }
            ],
        }
    )

    assert "source: recording (path=fixture.jsonl mode=offline)" in text
    assert "series: key=unit.scope  metric=latency (gauge)" in text
    for value in ("nan", "inf", "-inf", "unlimited"):
        assert value in text
    assert "missing" not in text.split("series:", 1)[1]


def test_empty_raw_result_is_explicit_not_a_silent_blank_table() -> None:
    text = render_query({"meta": _meta("raw"), "rows": []})

    assert "(no rows)" in text
    assert "TS" not in text


def test_auto_window_and_assertion_rows_preserve_decision_semantics() -> None:
    """Report output distinguishes auto-window decisions and pass/fail reasons."""
    report = {
        "window_mode": "auto",
        "window_detected": True,
        "window_start_ts": 10.0,
        "window_end_ts": 20.5,
        "profiles": [],
        "assertions": [
            {
                "group": "unit.scope",
                "metric": "latency",
                "stat": "p95",
                "op": "<=",
                "threshold": 2.0,
                "actual": {"exact": 1.5, "absent": 0},
                "passed": True,
                "reason": None,
            },
            {
                "group": "unit.scope",
                "metric": "errors",
                "stat": "max",
                "op": "<=",
                "threshold": 0,
                "actual": {},
                "passed": False,
                "reason": "no samples",
            },
        ],
    }

    text = render_report(report)

    assert "window: auto  detected=true  [10, 20.5]" in text
    assert "assertions: 2" in text
    assert "absent=0,exact=1.5" in text
    assert "PASS" in text
    assert "FAIL" in text
    assert "no samples" in text
    # An empty state map is semantically no observed value, not a zero.
    assert "missing" in text
