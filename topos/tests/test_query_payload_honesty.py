"""Payload honesty for query/report/compare/MCP history (B-10..B-13, B-15)."""

from __future__ import annotations

from topos.compare import OUTCOME_INCOMPLETE_COVERAGE, OUTCOME_OK, compare_summaries
from topos.daemon.client import DaemonHistoryResult
from topos.mcp.server import McpServer
from topos.model import Entity, EntityFrame, Frame, MetricValue
from topos.query import MetricRef, Query, run_query
from topos.query.source import DaemonHistoryFrameSource


def _ent(key: str, parent: str | None, ram: float = 100.0) -> EntityFrame:
    return EntityFrame(
        entity=Entity(key=key, kind="cgroup", parent=parent),
        metrics={"ram": MetricValue(ram, "exact")},
    )


def _frame(ts: float, *, present: bool = True, ram: float = 100.0) -> Frame:
    entities = {"a": _ent("a", None, ram)} if present else {}
    return Frame(schema_version=1, ts=ts, interval_s=5.0, host={}, entities=entities)


def _source(frames: list[Frame]) -> DaemonHistoryFrameSource:
    return DaemonHistoryFrameSource(tuple(enumerate(frames)))


def _summary(frames: list[Frame], ref: MetricRef, window: str = "all"):
    return run_query(
        _source(frames), Query(shape="summary", metrics=(ref,), window_spec=window)
    )


# --- B-10 -------------------------------------------------------------------


def test_coverage_is_incomplete_when_data_does_not_span_the_requested_window() -> None:
    frames = [_frame(1000.0 + 5 * i) for i in range(10)]

    short = _summary(frames, MetricRef("ram"), "last:3600s")
    assert short.meta["coverage"]["complete"] is False
    assert short.meta["coverage"]["requested_span_s"] == 3600.0

    covered = _summary(frames, MetricRef("ram"), "last:45s")
    assert covered.meta["coverage"]["complete"] is True

    everything = _summary(frames, MetricRef("ram"), "all")
    assert everything.meta["coverage"]["complete"] is True
    assert everything.meta["coverage"]["requested_span_s"] is None


# --- B-11 -------------------------------------------------------------------


def test_integral_and_state_duration_do_not_bridge_a_hole() -> None:
    frames = [_frame(0.0), _frame(5.0), _frame(1000.0)]

    integral = _summary(frames, MetricRef("ram", "integral")).rows[0]["metrics"]["ram"]
    # Only the 0..5 s interval was observed: 100 * 5, not 100 * 1000.
    assert integral["integral"] == 500.0
    assert integral["covered_s"] == 5.0
    assert integral["span_s"] == 1000.0

    states = _summary(frames, MetricRef("ram", "state_duration")).rows[0]["metrics"]["ram"]
    assert states["states"] == {"100.0": 5.0}
    assert states["covered_s"] == 5.0 and states["span_s"] == 1000.0


def test_derived_rate_is_not_smeared_across_a_hole() -> None:
    def counter_frame(ts: float, raw: int) -> Frame:
        entity = EntityFrame(
            entity=Entity(key="a", kind="cgroup", parent=None),
            metrics={"io_r_bps": MetricValue(None, "derived", raw=raw)},
        )
        return Frame(schema_version=1, ts=ts, interval_s=5.0, host={}, entities={"a": entity})

    frames = [counter_frame(0.0, 0), counter_frame(5.0, 500), counter_frame(1000.0, 99500)]
    cell = _summary(frames, MetricRef("io_r_bps")).rows[0]["metrics"]["io_r_bps"]
    # The 5 s -> 1000 s interval is a hole: only the 100 B/s sample exists.
    assert cell["count"] == 1
    assert cell["max"] == 100.0


# --- B-12 -------------------------------------------------------------------


def test_slice_grouping_builds_the_parent_map_once(monkeypatch) -> None:
    import topos.report as report

    frames = []
    for i in range(6):
        ents = {
            f"s{j}.slice/c{k}": _ent(f"s{j}.slice/c{k}", f"s{j}.slice")
            for j in range(3)
            for k in range(4)
        }
        ents.update({f"s{j}.slice": _ent(f"s{j}.slice", None) for j in range(3)})
        frames.append(Frame(schema_version=1, ts=float(i), interval_s=5.0, host={}, entities=ents))

    builds = []
    original = report._build_parent_map
    monkeypatch.setattr(
        report, "_build_parent_map", lambda fs: builds.append(1) or original(fs)
    )

    groups = report._group_frames(frames, "slice")

    assert len(builds) == 1
    assert sorted(groups) == ["s0.slice", "s1.slice", "s2.slice"]
    # 4 children + the slice itself, over 6 frames.
    assert len(groups["s0.slice"]["ram"].values) == 5 * 6


# --- B-13 -------------------------------------------------------------------


def _summary_result(semantic: str, stat: str, value: float, span: float) -> dict:
    cell = {"semantic": semantic, stat: value, "resets": 0}
    return {
        "meta": {
            "shape": "summary",
            "projection": "full",
            "visibility": "all",
            "coverage": {"complete": True, "span_s": span},
        },
        "rows": [{"key": "a", "metrics": {"m": cell}}],
    }


def test_compare_refuses_totals_over_different_window_lengths() -> None:
    cur = _summary_result("counter_delta", "total", 900.0, 30.0)
    base = _summary_result("counter_delta", "total", 100.0, 3600.0)
    [delta] = compare_summaries(cur, base)
    assert delta.outcome == OUTCOME_INCOMPLETE_COVERAGE
    assert "window length differs" in (delta.reason or "")

    same = _summary_result("counter_delta", "total", 100.0, 3550.0)
    [ok] = compare_summaries(base, same)
    assert ok.outcome == OUTCOME_OK


def test_compare_still_compares_intensive_stats_over_different_lengths() -> None:
    cur = _summary_result("gauge", "p95", 90.0, 30.0)
    base = _summary_result("gauge", "p95", 100.0, 3600.0)
    [delta] = compare_summaries(cur, base)
    assert delta.outcome == OUTCOME_OK


# --- B-15 -------------------------------------------------------------------


class _HistoryClient:
    def __init__(self, result: DaemonHistoryResult) -> None:
        self._result = result

    def request_history(self, *, limit, since_ts=None, until_ts=None):
        return self._result


def test_mcp_history_reports_gap_truncation_and_returned_range() -> None:
    meta = {"ram": {"sensitivity": "operational", "unit": "bytes"}}
    frames = tuple((i, _frame(float(100 + i), ram=float(i))) for i in range(3))
    # `a` is the entity key; the selector resolves it directly.
    result = DaemonHistoryResult(
        entries=frames,
        oldest_seq=0,
        latest_seq=2,
        next_cursor=2,
        gap=True,
        metrics_meta=meta,
    )

    data = McpServer(_HistoryClient(result))._handle_history("a", "ram", "last:86400", 3)["data"]

    assert data["gap"] is True
    assert data["truncated"] is True  # 3 frames returned for limit 3
    assert data["frames_returned"] == 3
    assert (data["oldest_ts"], data["newest_ts"]) == (100.0, 102.0)
    assert data["requested_since_ts"] > 0
    roomy = McpServer(_HistoryClient(result))._handle_history("a", "ram", "last:86400", 50)["data"]
    assert roomy["truncated"] is False


def test_empty_source_with_a_requested_window_is_never_complete_coverage() -> None:
    result = _summary([], MetricRef("ram"), "last:60s")
    assert result.meta["coverage"]["frames"] == 0
    assert result.meta["coverage"]["complete"] is False
    assert result.meta["observed_start_ts"] is None
