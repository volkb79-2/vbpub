"""P103 — Complete query engine projection and execution coverage.

Closes final 17 lines and 19 branch pairs in query/engine.py,
achieving whole-file 100%. Uses real FrameSource/Query behavior.
"""

from __future__ import annotations

import json

from topos.model import Entity, EntityFrame, Frame, MetricValue
from topos.query import Query, MetricRef, SortSpec, Caps
from topos.query.engine import (
    _in_slice, _cell_stat, _summary_cells, _project,
    run_query, _enforce_byte_cap, format_result,
    subtree_aggregate,
)
from topos.query.semantics import ValueSemantic
from topos.query.source import DaemonHistoryFrameSource


def _g(v: float) -> MetricValue:
    return MetricValue(v=v, src="exact")


def _rr(raw: int) -> MetricValue:
    return MetricValue(v=None, src="derived", raw=raw)


def _ef(k: str, metrics: dict[str, MetricValue]) -> EntityFrame:
    return EntityFrame(entity=Entity(key=k, kind="scope", parent="", docker=None), metrics=metrics)


def _frame(ts: float, entities: dict[str, EntityFrame] | None = None, host: dict | None = None) -> Frame:
    return Frame(ts=ts, interval_s=1, host=host or {}, entities=entities or {}, schema_version=1)


def _daemon(frames: list[Frame]) -> DaemonHistoryFrameSource:
    return DaemonHistoryFrameSource(tuple((i, f) for i, f in enumerate(frames)))


# line 397-398: _in_slice parent chain exhausts + cycle detection
def test_in_slice_not_found():
    parents = {"c": "b", "b": "a", "a": None}
    assert _in_slice("c", "root", parents) is False

def test_in_slice_cycle_safe():
    """Cycle-safe: traverse a→b→a hits seen and returns False (F4)."""
    parents = {"a": "b", "b": "a"}
    assert _in_slice("a", "missing", parents) is False


# line 477: format_result pretty
def test_format_result_pretty():
    src = _daemon([_frame(0, {"e": _ef("e", {"ram": _g(1.0)})})])
    q = Query(shape="summary", metrics=(MetricRef(name="ram"),))
    r = run_query(src, q)
    result = format_result(r, pretty=True)
    expected = json.dumps(r.to_jsonable(), sort_keys=True, indent=2)
    assert result == expected


# line 581: _summary_cells available visibility
def test_summary_cells_available_visibility():
    frames = [
        _frame(0, {"e": _ef("e", {"ram": _g(1.0)})}),
        _frame(1, {"e": _ef("e", {"ram": MetricValue(9.0, "unavail_kernel")})}),
    ]
    from topos.query.engine import _ResolvedMetric
    rm = _ResolvedMetric(name="ram", semantic=ValueSemantic.GAUGE)
    cells, resets = _summary_cells(frames, ["e"], [rm], "available")
    assert cells == {
        "e": {
            "ram": {
                "semantic": "gauge",
                "sample_count": 1,
                "count": 1,
                "min": 1.0,
                "mean": 1.0,
                "p50": 1.0,
                "p95": 1.0,
                "max": 1.0,
            }
        }
    }
    assert resets == 0


# line 597: _cell_stat None
def test_cell_stat_none():
    assert _cell_stat(None, "value") is None


# lines 660-662: project hierarchy no sort — exact key/depth/path/metrics
def test_project_hierarchy_no_sort():
    parents = {"a": None, "b": "a", "c": "a"}
    cells = {"a": {}, "b": {}, "c": {}}
    rows = _project(
        Query(shape="summary", metrics=(MetricRef(name="ram"),), projection="hierarchy"),
        ["a", "b", "c"], parents, cells, sort=None, own_values={},
    )
    assert rows == [
        {"key": "a", "depth": 0, "path": [], "metrics": {}},
        {"key": "b", "depth": 1, "path": ["a"], "metrics": {}},
        {"key": "c", "depth": 1, "path": ["a"], "metrics": {}},
    ]


# line 754: enforce_byte_cap with prior truncation
def test_enforce_byte_cap_prior_truncation():
    rows = [{"key": "e", "metrics": {"ram": {"x": "y" * 2000}}}]
    meta = {"k": "v"}
    result, trunc = _enforce_byte_cap(meta, rows, Caps(10, 100, 500, "truncate"),
                                        truncation={"truncated": True, "reason": "max_rows"})
    assert result == []
    assert trunc == {
        "truncated": True,
        "policy": "truncate",
        "reason": "max_bytes",
        "dropped_rows": 1,
        "also": "max_rows",
    }


# line 784: _run_current empty frames
def test_run_current_empty():
    src = _daemon([])
    q = Query(shape="current", metrics=(MetricRef(name="ram"),))
    r = run_query(src, q)
    assert r.rows == []


# line 855: entity absent from some frames -> continue
def test_run_raw_no_entity():
    ef1 = _ef("key1", {"ram": _g(1.0)})
    src = _daemon([_frame(0, {"key1": ef1}), _frame(1, {})])
    q = Query.from_dict({
        "shape": "raw", "metrics": ["ram"],
        "caps": {"max_rows": 100, "max_points": 1000, "max_bytes": 10000, "on_exceed": "truncate"},
    })
    r = run_query(src, q)
    assert r.rows == [
        {
            "key": "key1",
            "metric": "ram",
            "semantic": "gauge",
            "points": [{"ts": 0, "value": 1.0, "src": "exact"}],
        }
    ]
    assert r.meta["truncation"] == {"truncated": False, "policy": "truncate"}


# line 858: metric absent -> continue
def test_run_raw_no_metric():
    src = _daemon([_frame(0, {"e": _ef("e", {"other": _g(1.0)})})])
    q = Query.from_dict({
        "shape": "raw", "metrics": ["ram"],
        "caps": {"max_rows": 100, "max_points": 1000, "max_bytes": 10000, "on_exceed": "truncate"},
    })
    r = run_query(src, q)
    assert r.rows == []
    assert r.meta["truncation"] == {"truncated": False, "policy": "truncate"}


# line 860: hidden visibility skip
def test_run_raw_hidden_visibility():
    mv = MetricValue(1.0, "unavail_kernel")
    src = _daemon([_frame(0, {"e": _ef("e", {"ram": mv})})])
    q = Query.from_dict({
        "shape": "raw", "metrics": ["ram"], "visibility": "available",
        "caps": {"max_rows": 100, "max_points": 1000, "max_bytes": 10000, "on_exceed": "truncate"},
    })
    r = run_query(src, q)
    assert r.rows == []
    assert r.meta["truncation"] == {"truncated": False, "policy": "truncate"}


# line 862: point cap truncation
def test_run_raw_point_cap():
    frames = [_frame(i, {"e": _ef("e", {"ram": _g(float(i))})}) for i in range(20)]
    src = _daemon(frames)
    q = Query.from_dict({
        "shape": "raw", "metrics": ["ram"],
        "caps": {"max_rows": 100, "max_points": 3, "max_bytes": 10000, "on_exceed": "truncate"},
    })
    r = run_query(src, q)
    assert r.rows == [
        {
            "key": "e",
            "metric": "ram",
            "semantic": "gauge",
            "points": [
                {"ts": 0, "value": 0.0, "src": "exact"},
                {"ts": 1, "value": 1.0, "src": "exact"},
                {"ts": 2, "value": 2.0, "src": "exact"},
            ],
        }
    ]
    assert r.meta["truncation"] == {
        "truncated": True,
        "policy": "truncate",
        "reason": "max_points",
        "emitted_series": 1,
        "total_series": 1,
    }


# line 866: raw counter included
def test_run_raw_raw_field():
    src = _daemon([_frame(0, {"e": _ef("e", {"ram": _rr(500)})})])
    q = Query.from_dict({
        "shape": "raw", "metrics": ["ram"],
        "caps": {"max_rows": 100, "max_points": 1000, "max_bytes": 10000, "on_exceed": "truncate"},
    })
    r = run_query(src, q)
    assert r.rows == [
        {
            "key": "e",
            "metric": "ram",
            "semantic": "gauge",
            "points": [
                {"ts": 0, "value": None, "src": "derived", "raw": 500}
            ],
        }
    ]
    assert r.meta["truncation"] == {"truncated": False, "policy": "truncate"}


# line 882: both truncated and row_capped
def test_run_raw_both_caps():
    frames = [_frame(i, {"e1": _ef("e1", {"ram": _g(float(i))}),
                         "e2": _ef("e2", {"ram": _g(float(i))})}) for i in range(20)]
    src = _daemon(frames)
    q = Query.from_dict({
        "shape": "raw", "metrics": ["ram"],
        "caps": {"max_rows": 1, "max_points": 3, "max_bytes": 10000, "on_exceed": "truncate"},
    })
    r = run_query(src, q)
    assert r.rows == [
        {
            "key": "e1",
            "metric": "ram",
            "semantic": "gauge",
            "points": [
                {"ts": 0, "value": 0.0, "src": "exact"},
                {"ts": 1, "value": 1.0, "src": "exact"},
                {"ts": 2, "value": 2.0, "src": "exact"},
            ],
        }
    ]
    assert r.meta["truncation"] == {
        "truncated": True,
        "policy": "truncate",
        "reason": "max_points",
        "emitted_series": 1,
        "total_series": 2,
        "also": "max_rows",
    }


# arc 429-427: subtree_aggregate child None
def test_subtree_aggregate_child_none():
    result = subtree_aggregate("root", "net_rx_bps",
                                {"root": 100.0, "child": None},
                                {"root": ["child"]})
    assert result == 100.0


# arc 675-684: project hierarchy with sort — exact descending order
def test_project_hierarchy_with_sort():
    parents = {"a": None, "b": "a", "c": "a"}
    cells = {"a": {"ram": {"value": 10.0}}, "b": {"ram": {"value": 5.0}}, "c": {"ram": {"value": 15.0}}}
    own_values = {k: _cell_stat(cells.get(k, {}).get("ram"), "value") for k in ["a", "b", "c"]}
    sort = SortSpec(metric="ram", stat="value", order="desc")
    rows = _project(
        Query(shape="summary", metrics=(MetricRef(name="ram"),), projection="hierarchy"),
        ["a", "b", "c"], parents, cells, sort, own_values,
    )
    assert rows == [
        {
            "key": "a",
            "depth": 0,
            "path": [],
            "metrics": {"ram": {"value": 10.0}},
            "subtree": {
                "metric": "ram",
                "policy": "kernel_subtree",
                "additive": False,
                "value": 10.0,
            },
        },
        {
            "key": "c",
            "depth": 1,
            "path": ["a"],
            "metrics": {"ram": {"value": 15.0}},
            "subtree": {
                "metric": "ram",
                "policy": "kernel_subtree",
                "additive": False,
                "value": 15.0,
            },
        },
        {
            "key": "b",
            "depth": 1,
            "path": ["a"],
            "metrics": {"ram": {"value": 5.0}},
            "subtree": {
                "metric": "ram",
                "policy": "kernel_subtree",
                "additive": False,
                "value": 5.0,
            },
        },
    ]
