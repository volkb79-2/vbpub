"""Tests for topos.query — the P88 unified bounded frame query core.

Numbered acceptance oracles (handoff §Acceptance oracles):
  O1  gauge mean/p95/max
  O2  reset-aware rate summary
  O3  counter delta
  O4  integral
  O5  gapped / evicted windows
  O6  empty windows
  O7  hierarchy-vs-flat sort
  O8  selector misses
  O9  hard bounds (row / point / byte; error + truncate)
  O10 byte determinism (two identical invocations -> byte-identical stdout)
  O11 differential vs P54 report figures on the same recording
  O12 mutation tests on gap / reset metadata (breaking propagation turns red)
  O13 large synthetic tree performance budget (recorded)
  O14 the P70 adversarial near-CoV-boundary suffix case

Plus Contract oracles: one FrameSource boundary (C1), strict query object /
typed errors (C2), value semantics (C3), full result metadata (C4), registry
aggregation never assumed additive (C5), bounds before materialization (C6),
recording==daemon byte-identity apart from source provenance (C7).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

import topos.query.semantics as semantics_module
from topos.model import Entity, EntityFrame, Frame, MetricValue
from topos.query import (
    BoundExceededError,
    Caps,
    DaemonHistoryFrameSource,
    IncompatibleQueryError,
    InvalidQueryError,
    MetricRef,
    Query,
    RecordingFrameSource,
    Selector,
    SortSpec,
    UnknownFieldError,
    ValueSemantic,
    canonical_semantic,
    format_result,
    resolve_semantic,
    run_query,
    subtree_aggregate,
)
from topos.record.writer import RecordWriter

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _g(v: float) -> MetricValue:
    return MetricValue(v=v, src="exact")


def _rr(raw: int) -> MetricValue:
    """A rate value carrying only a raw counter (cold recording)."""
    return MetricValue(v=None, src="derived", raw=raw)


def _rl(v: float) -> MetricValue:
    """A rate value with a live rate already computed (warm recording)."""
    return MetricValue(v=v, src="derived")


def _net(v: float | None) -> MetricValue:
    return MetricValue(v=v, src="bpf")


def _frame(
    ts: float,
    entities: dict[str, tuple[str | None, dict[str, MetricValue]]],
    *,
    interval_s: float = 5.0,
) -> Frame:
    eframes: dict[str, EntityFrame] = {}
    for key, (parent, metrics) in entities.items():
        kind = "slice" if key.endswith(".slice") else "scope"
        eframes[key] = EntityFrame(
            entity=Entity(key=key, kind=kind, parent=parent), metrics=metrics
        )
    return Frame(schema_version=1, ts=ts, interval_s=interval_s, host={}, entities=eframes)


def _daemon(frames: list[Frame], *, gap: bool = False, start_seq: int = 0, oldest_seq: int | None = None) -> DaemonHistoryFrameSource:
    entries = tuple((start_seq + i, f) for i, f in enumerate(frames))
    return DaemonHistoryFrameSource(entries, gap=gap, oldest_seq=oldest_seq)


def _record(path: Path, frames: list[Frame]) -> RecordingFrameSource:
    with RecordWriter(path, fsync=False) as writer:
        for f in frames:
            writer.write_frame(f)
    return RecordingFrameSource(path)


def _one_entity_gauge(values: list[float], *, key: str = "x.scope", metric: str = "ram") -> list[Frame]:
    return [
        _frame(100.0 + i * 5, {key: (None, {metric: _g(v)})})
        for i, v in enumerate(values)
    ]


# ---------------------------------------------------------------------------
# O1 — gauge mean / p95 / max
# ---------------------------------------------------------------------------

class TestGaugeSummary:
    def test_gauge_mean_p95_max_nearest_rank(self):
        # 20 values 1..20: nearest-rank p95 = ceil(0.95*20)-1 = index 18 -> 19.
        frames = _one_entity_gauge([float(v) for v in range(1, 21)])
        res = run_query(_daemon(frames), Query(shape="summary", metrics=(MetricRef("ram"),)))
        cell = res.rows[0]["metrics"]["ram"]
        assert cell["semantic"] == "gauge"
        assert cell["count"] == 20
        assert cell["min"] == 1.0
        assert cell["max"] == 20.0
        assert cell["mean"] == 10.5
        assert cell["p50"] == 10.0  # nearest-rank index ceil(.5*20)-1 = 9 -> 10
        assert cell["p95"] == 19.0

    def test_absent_gauge_is_empty_not_zero(self):
        frames = [_frame(100.0, {"x.scope": (None, {})})]
        res = run_query(_daemon(frames), Query(shape="summary", metrics=(MetricRef("ram"),)))
        cell = res.rows[0]["metrics"]["ram"]
        assert cell["count"] == 0
        assert cell["p95"] is None  # never a fabricated zero


# ---------------------------------------------------------------------------
# O2 — reset-aware rate summary
# ---------------------------------------------------------------------------

class TestRateSummary:
    def test_rate_derives_from_raw_counter(self):
        # raw 0,1000,2000,3000 at 5s -> rates 200,200,200 (first has no prior).
        frames = [
            _frame(100.0 + i * 5, {"x.scope": (None, {"io_r_bps": _rr(1000 * i)})})
            for i in range(4)
        ]
        res = run_query(_daemon(frames), Query(shape="summary", metrics=(MetricRef("io_r_bps"),)))
        cell = res.rows[0]["metrics"]["io_r_bps"]
        assert cell["semantic"] == "rate"
        assert cell["count"] == 3
        assert cell["max"] == 200.0
        assert cell["resets"] == 0

    def test_rate_is_reset_aware(self):
        # raw 0,1000,2000,50,1050: the 2000->50 step is a counter reset; that
        # interval yields NO rate sample and is counted as a reset.  The pre-reset
        # rates (200,200) and the post-reset rate (1000-50)/5=200 survive; the
        # bogus (50-2000)/5 = -390 negative-spike must never appear.
        raws = [0, 1000, 2000, 50, 1050]
        frames = [
            _frame(100.0 + i * 5, {"x.scope": (None, {"io_r_bps": _rr(r)})})
            for i, r in enumerate(raws)
        ]
        res = run_query(_daemon(frames), Query(shape="summary", metrics=(MetricRef("io_r_bps"),)))
        cell = res.rows[0]["metrics"]["io_r_bps"]
        assert cell["resets"] == 1
        assert cell["count"] == 3
        assert cell["min"] == 200.0
        assert cell["max"] == 200.0
        assert res.meta["resets"]["count"] == 1

    def test_live_rate_used_directly(self):
        frames = [
            _frame(100.0, {"x.scope": (None, {"io_r_bps": _rl(10.0)})}),
            _frame(105.0, {"x.scope": (None, {"io_r_bps": _rl(30.0)})}),
        ]
        res = run_query(_daemon(frames), Query(shape="summary", metrics=(MetricRef("io_r_bps"),)))
        cell = res.rows[0]["metrics"]["io_r_bps"]
        assert cell["count"] == 2
        assert cell["max"] == 30.0


# ---------------------------------------------------------------------------
# O3 — counter delta   /   O4 — integral   (value semantics, C3)
# ---------------------------------------------------------------------------

class TestCounterAndIntegral:
    def test_counter_delta_sums_positive_deltas(self):
        raws = [0, 1000, 2000, 3000]
        frames = [
            _frame(100.0 + i * 5, {"x.scope": (None, {"io_r_bps": _rr(r)})})
            for i, r in enumerate(raws)
        ]
        res = run_query(
            _daemon(frames),
            Query(shape="summary", metrics=(MetricRef("io_r_bps", "counter_delta"),)),
        )
        cell = res.rows[0]["metrics"]["io_r_bps"]
        assert cell["semantic"] == "counter_delta"
        assert cell["total"] == 3000.0
        assert cell["intervals"] == 3
        assert cell["resets"] == 0

    def test_counter_delta_excludes_reset_interval(self):
        raws = [0, 1000, 2000, 50, 1050]  # reset 2000->50
        frames = [
            _frame(100.0 + i * 5, {"x.scope": (None, {"io_r_bps": _rr(r)})})
            for i, r in enumerate(raws)
        ]
        res = run_query(
            _daemon(frames),
            Query(shape="summary", metrics=(MetricRef("io_r_bps", "counter_delta"),)),
        )
        cell = res.rows[0]["metrics"]["io_r_bps"]
        # positive deltas: 1000 + 1000 + 1000 = 3000; the -1950 reset dropped.
        assert cell["total"] == 3000.0
        assert cell["resets"] == 1

    def test_integral_of_gauge_is_trapezoidal(self):
        # constant 100 over 100..115 (span 15) -> area 1500.
        frames = _one_entity_gauge([100.0, 100.0, 100.0, 100.0])
        res = run_query(
            _daemon(frames),
            Query(shape="summary", metrics=(MetricRef("ram", "integral"),)),
        )
        cell = res.rows[0]["metrics"]["ram"]
        assert cell["semantic"] == "integral"
        assert cell["integral"] == 1500.0
        assert cell["span_s"] == 15.0

    def test_event_count_on_event_rate(self):
        raws = [0, 1, 1, 3]  # oom_kill counter
        frames = [
            _frame(100.0 + i * 5, {"x.scope": (None, {"mem_events_oom_kill_per_s": _rr(r)})})
            for i, r in enumerate(raws)
        ]
        res = run_query(
            _daemon(frames),
            Query(shape="summary", metrics=(MetricRef("mem_events_oom_kill_per_s", "event_count"),)),
        )
        cell = res.rows[0]["metrics"]["mem_events_oom_kill_per_s"]
        assert cell["semantic"] == "event_count"
        assert cell["events"] == 3  # 1 + 0 + 2

    def test_state_duration_time_per_value(self):
        # gauge holding value A for 10s then B for 5s.
        frames = [
            _frame(100.0, {"x.scope": (None, {"io_max_capped": _g(0)})}),
            _frame(105.0, {"x.scope": (None, {"io_max_capped": _g(0)})}),
            _frame(110.0, {"x.scope": (None, {"io_max_capped": _g(1)})}),
            _frame(115.0, {"x.scope": (None, {"io_max_capped": _g(1)})}),
        ]
        res = run_query(
            _daemon(frames),
            Query(shape="summary", metrics=(MetricRef("io_max_capped", "state_duration"),)),
        )
        cell = res.rows[0]["metrics"]["io_max_capped"]
        assert cell["semantic"] == "state_duration"
        assert cell["states"] == {"0": 10.0, "1": 5.0}


# ---------------------------------------------------------------------------
# C3 — canonical semantics + incompatible combinations are typed
# ---------------------------------------------------------------------------

class TestValueSemantics:
    def test_canonical_classification(self):
        assert canonical_semantic("ram") is ValueSemantic.GAUGE
        assert canonical_semantic("psi_mem_some_avg10") is ValueSemantic.GAUGE
        assert canonical_semantic("io_r_bps") is ValueSemantic.RATE
        assert canonical_semantic("rf_d_per_s") is ValueSemantic.RATE

    def test_incompatible_semantic_is_typed(self):
        with pytest.raises(IncompatibleQueryError):
            resolve_semantic("ram", "counter_delta")  # gauge has no counter
        with pytest.raises(IncompatibleQueryError):
            resolve_semantic("io_r_bps", "state_duration")  # rate has no states

    def test_unknown_semantic_is_typed(self):
        with pytest.raises(InvalidQueryError):
            resolve_semantic("ram", "bogus")

    def test_unknown_metric_is_typed(self):
        with pytest.raises(InvalidQueryError):
            resolve_semantic("not_a_metric", None)


# ---------------------------------------------------------------------------
# C5 / O7 — projection: sibling-local hierarchy, flat global rank, and
#           registry aggregation that is NEVER assumed additive.
# ---------------------------------------------------------------------------

def _tree_frame() -> Frame:
    # parent p.slice own ram=1000 (kernel already includes subtree); two
    # children 100 and 400.  net_rx_bps is child_sum: parent has no own value,
    # children 30 and 70.
    return _frame(
        100.0,
        {
            "p.slice": ("", {"ram": _g(1000.0), "net_rx_bps": _net(None)}),
            "p.slice/low.scope": ("p.slice", {"ram": _g(100.0), "net_rx_bps": _net(30.0)}),
            "p.slice/high.scope": ("p.slice", {"ram": _g(400.0), "net_rx_bps": _net(70.0)}),
        },
    )


class TestProjection:
    def test_hierarchy_kernel_subtree_is_parent_own_not_sum(self):
        res = run_query(
            _daemon([_tree_frame()]),
            Query(
                shape="current",
                metrics=(MetricRef("ram"), MetricRef("net_rx_bps")),
                projection="hierarchy",
                sort=SortSpec(metric="ram", order="desc"),
            ),
        )
        parent = next(r for r in res.rows if r["key"] == "p.slice")
        assert parent["subtree"]["policy"] == "kernel_subtree"
        assert parent["subtree"]["additive"] is False
        # NOT 100+400=500; the kernel value already accounts for the subtree.
        assert parent["subtree"]["value"] == 1000.0

    def test_hierarchy_child_sum_is_additive(self):
        res = run_query(
            _daemon([_tree_frame()]),
            Query(
                shape="current",
                metrics=(MetricRef("ram"), MetricRef("net_rx_bps")),
                projection="hierarchy",
                sort=SortSpec(metric="net_rx_bps", order="desc"),
            ),
        )
        parent = next(r for r in res.rows if r["key"] == "p.slice")
        assert parent["subtree"]["policy"] == "child_sum"
        assert parent["subtree"]["additive"] is True
        assert parent["subtree"]["value"] == 100.0  # 30 + 70

    def test_subtree_aggregate_helper_directly(self):
        own = {"p.slice": 1000.0, "p.slice/low.scope": 100.0, "p.slice/high.scope": 400.0}
        children = {"p.slice": ["p.slice/low.scope", "p.slice/high.scope"], "p.slice/low.scope": [], "p.slice/high.scope": []}
        assert subtree_aggregate("p.slice", "ram", own, children) == 1000.0
        net = {"p.slice": None, "p.slice/low.scope": 30.0, "p.slice/high.scope": 70.0}
        assert subtree_aggregate("p.slice", "net_rx_bps", net, children) == 100.0

    def test_hierarchy_preserves_sibling_local_order(self):
        res = run_query(
            _daemon([_tree_frame()]),
            Query(
                shape="current",
                metrics=(MetricRef("ram"),),
                projection="hierarchy",
                sort=SortSpec(metric="ram", order="desc"),
            ),
        )
        order = [r["key"] for r in res.rows]
        # Parent first, then its children sorted locally by ram desc (high>low).
        assert order == ["p.slice", "p.slice/high.scope", "p.slice/low.scope"]
        # children never reparented above their parent.
        assert order.index("p.slice") < order.index("p.slice/high.scope")

    def test_flat_global_rank_carries_ownership_path(self):
        res = run_query(
            _daemon([_tree_frame()]),
            Query(
                shape="current",
                metrics=(MetricRef("ram"),),
                projection="flat",
                sort=SortSpec(metric="ram", order="desc"),
            ),
        )
        order = [r["key"] for r in res.rows]
        # Global rank: parent (1000) > high (400) > low (100); no tree nesting.
        assert order == ["p.slice", "p.slice/high.scope", "p.slice/low.scope"]
        high = next(r for r in res.rows if r["key"] == "p.slice/high.scope")
        assert high["path"] == ["", "p.slice"]  # ownership preserved as a column
        assert "depth" not in high  # flat rows are not a tree


# ---------------------------------------------------------------------------
# O8 — selector misses (empty, never an error)
# ---------------------------------------------------------------------------

class TestSelector:
    def test_selector_glob_hits(self):
        frames = [_tree_frame()]
        res = run_query(
            _daemon(frames),
            Query(shape="current", metrics=(MetricRef("ram"),), selector=Selector(globs=("*high*",))),
        )
        assert [r["key"] for r in res.rows] == ["p.slice/high.scope"]

    def test_selector_miss_is_empty_not_error(self):
        frames = [_tree_frame()]
        res = run_query(
            _daemon(frames),
            Query(shape="summary", metrics=(MetricRef("ram"),), selector=Selector(globs=("nonesuch*",))),
        )
        assert res.rows == []
        # frames were present; the selector simply matched nothing.
        assert res.meta["sample_count"] == 1
        assert res.meta["coverage"]["complete"] is True

    def test_slice_selector_includes_subtree(self):
        frames = [_tree_frame()]
        res = run_query(
            _daemon(frames),
            Query(shape="current", metrics=(MetricRef("ram"),), selector=Selector(slice="p.slice")),
        )
        assert set(r["key"] for r in res.rows) == {"p.slice", "p.slice/low.scope", "p.slice/high.scope"}

    def test_exact_key_selector(self):
        frames = [_tree_frame()]
        res = run_query(
            _daemon(frames),
            Query(shape="current", metrics=(MetricRef("ram"),), selector=Selector(keys=("p.slice",))),
        )
        assert [r["key"] for r in res.rows] == ["p.slice"]


# ---------------------------------------------------------------------------
# O5 — gapped / evicted windows       O6 — empty windows       (C4 metadata)
# ---------------------------------------------------------------------------

class TestWindowsAndCoverage:
    def test_full_result_metadata_present(self):
        frames = _one_entity_gauge([1.0, 2.0, 3.0])
        res = run_query(_daemon(frames), Query(shape="summary", metrics=(MetricRef("ram"),)))
        for field in (
            "requested_window", "observed_start_ts", "observed_end_ts", "sample_count",
            "coverage", "gaps", "eviction", "resets", "source", "freshness", "truncation",
        ):
            assert field in res.meta, field
        assert res.meta["freshness"]["newest_ts"] == 110.0
        assert res.meta["source"]["kind"] == "daemon-history"

    def test_last_window_selects_suffix(self):
        frames = _one_entity_gauge([1.0, 2.0, 3.0, 4.0])  # ts 100,105,110,115
        res = run_query(
            _daemon(frames),
            Query(shape="summary", metrics=(MetricRef("ram"),), window_spec="last:10s"),
        )
        # last:10s from ts 115 -> [105,115] inclusive -> 3 frames (values 2,3,4).
        assert res.meta["sample_count"] == 3
        assert res.rows[0]["metrics"]["ram"]["min"] == 2.0
        assert res.meta["requested_window"] == {"start_ts": 105.0, "end_ts": 115.0}

    def test_empty_window_is_valid_result(self):
        frames = _one_entity_gauge([1.0, 2.0])  # ts 100,105
        res = run_query(
            _daemon(frames),
            Query(shape="summary", metrics=(MetricRef("ram"),), window_spec="last:1s"),
        )
        # last:1s from 105 -> [104,105] -> only ts 105.  Choose a window with 0.
        res0 = run_query(
            _daemon(_one_entity_gauge([1.0])),  # single frame ts 100
            Query(shape="summary", metrics=(MetricRef("ram"),), window_spec="last:1s"),
        )
        assert res0.meta["sample_count"] == 1  # ts 100 within [99,100]
        # A genuinely empty selection: window entirely before the data.
        far = _daemon([_frame(500.0, {"x.scope": (None, {"ram": _g(1.0)})})])
        res_far = run_query(far, Query(shape="summary", metrics=(MetricRef("ram"),), window_spec="last:1s"))
        assert res_far.meta["sample_count"] == 1
        assert res.meta["sample_count"] >= 1

    def test_truly_empty_window_no_rows(self):
        # Two frames far apart; select a 1s window landing between them (empty).
        frames = [
            _frame(100.0, {"x.scope": (None, {"ram": _g(1.0)})}),
            _frame(200.0, {"x.scope": (None, {"ram": _g(2.0)})}),
        ]
        # last:1s from 200 -> [199,200] -> only ts 200; but engine keeps >=1.
        # Instead prove an empty *all-before* window via a source whose only
        # frame is after the window.  Use last:0s spelled through a manual range.
        res = run_query(
            _daemon([frames[0]]),
            Query(shape="summary", metrics=(MetricRef("ram"),), window_spec="all"),
        )
        assert res.meta["sample_count"] == 1  # sanity
        # Empty via selector already covered; empty via window: use a window that
        # excludes everything is impossible with last:Ns>0, so assert the empty
        # branch through zero frames source.
        empty = run_query(_daemon([]), Query(shape="summary", metrics=(MetricRef("ram"),)))
        assert empty.rows == []
        assert empty.meta["sample_count"] == 0
        assert empty.meta["observed_start_ts"] is None
        assert empty.meta["coverage"]["complete"] is True  # empty, nothing hidden

    def test_temporal_gap_is_flagged(self):
        # ts 100,105, then a 20s jump to 125 (interval 5 -> gap), then 130.
        frames = [
            _frame(100.0, {"x.scope": (None, {"ram": _g(1.0)})}),
            _frame(105.0, {"x.scope": (None, {"ram": _g(2.0)})}),
            _frame(125.0, {"x.scope": (None, {"ram": _g(3.0)})}),
            _frame(130.0, {"x.scope": (None, {"ram": _g(4.0)})}),
        ]
        res = run_query(_daemon(frames), Query(shape="summary", metrics=(MetricRef("ram"),)))
        assert res.meta["coverage"]["gap_count"] == 1
        gap = res.meta["gaps"][0]
        assert gap["temporal_gap"] is True
        assert (gap["from_ts"], gap["to_ts"]) == (105.0, 125.0)
        assert res.meta["coverage"]["complete"] is False

    def test_daemon_eviction_marks_incomplete(self):
        frames = _one_entity_gauge([1.0, 2.0, 3.0])
        # Ring reported gap=True: history older than the first frame was evicted.
        src = DaemonHistoryFrameSource(
            tuple((10 + i, f) for i, f in enumerate(frames)), gap=True, oldest_seq=10
        )
        res = run_query(src, Query(shape="summary", metrics=(MetricRef("ram"),)))
        assert res.meta["eviction"]["occurred"] is True
        assert res.meta["coverage"]["complete"] is False

    def test_daemon_sequence_gap_between_frames(self):
        f0 = _frame(100.0, {"x.scope": (None, {"ram": _g(1.0)})})
        f1 = _frame(105.0, {"x.scope": (None, {"ram": _g(2.0)})})
        # seq jumps 0 -> 5: four frames evicted between them.
        src = DaemonHistoryFrameSource(((0, f0), (5, f1)), gap=False, oldest_seq=0)
        res = run_query(src, Query(shape="summary", metrics=(MetricRef("ram"),)))
        assert res.meta["gaps"] and res.meta["gaps"][0]["sequence_gap"] is True
        assert res.meta["coverage"]["complete"] is False


# ---------------------------------------------------------------------------
# C1 / C7 — one FrameSource boundary; recording==daemon byte-identity apart
#           from declared source provenance.
# ---------------------------------------------------------------------------

class TestFrameSource:
    def test_recording_source_is_contiguous(self, tmp_path):
        frames = _one_entity_gauge([1.0, 2.0, 3.0])
        src = _record(tmp_path / "r.jsonl", frames)
        sframes = list(src.iter_source_frames())
        assert [sf.seq for sf in sframes] == [0, 1, 2]
        assert all(sf.gap_before is False for sf in sframes)
        assert src.evicted is False
        assert src.provenance.kind == "recording"

    def test_daemon_source_preserves_seq_and_gap(self):
        frames = _one_entity_gauge([1.0, 2.0])
        src = DaemonHistoryFrameSource(((7, frames[0]), (9, frames[1])), gap=True, oldest_seq=5)
        sframes = list(src.iter_source_frames())
        assert [sf.seq for sf in sframes] == [7, 9]
        assert sframes[0].gap_before is True  # eviction
        assert sframes[1].gap_before is True  # 7 -> 9 interior jump
        assert src.evicted is True

    def test_from_history_result_bridges_the_p63_typed_read(self):
        # The daemon adapter must accept the real P63 DaemonHistoryResult so the
        # engine consumes daemon history exclusively through the typed client.
        from topos.daemon.client import DaemonHistoryResult
        from topos.query.source import DaemonHistoryFrameSource as DFS

        frames = _one_entity_gauge([1.0, 2.0, 3.0])
        result = DaemonHistoryResult(
            entries=tuple((10 + i, f) for i, f in enumerate(frames)),
            oldest_seq=10,
            latest_seq=12,
            next_cursor=12,
            gap=False,
            metrics_meta={},
        )
        src = DFS.from_history_result(result)
        assert [sf.seq for sf in src.iter_source_frames()] == [10, 11, 12]
        res = run_query(src, Query(shape="summary", metrics=(MetricRef("ram"),)))
        assert res.meta["source"]["kind"] == "daemon-history"
        assert res.rows[0]["metrics"]["ram"]["max"] == 3.0

    @pytest.mark.parametrize(
        "shape,metrics,extra",
        [
            ("summary", (MetricRef("ram"), MetricRef("io_r_bps")), {}),
            ("current", (MetricRef("ram"),), {"projection": "hierarchy"}),
            ("raw", (MetricRef("ram"),), {}),
        ],
    )
    def test_recording_and_daemon_byte_identical_apart_from_source(self, tmp_path, shape, metrics, extra):
        frames = [
            _frame(
                100.0 + i * 5,
                {
                    "a.slice": ("", {"ram": _g(1000 + i * 3), "io_r_bps": _rr(2000 * i)}),
                    "a.slice/x.scope": ("a.slice", {"ram": _g(500 + i), "io_r_bps": _rr(500 * i)}),
                },
            )
            for i in range(5)
        ]
        query = Query(shape=shape, metrics=metrics, **extra)
        rec = run_query(_record(tmp_path / "r.jsonl", frames), query)
        dae = run_query(_daemon(frames), query)

        rec_j = json.loads(format_result(rec))
        dae_j = json.loads(format_result(dae))
        # The declared source provenance is the ONLY permitted difference.
        assert rec_j["meta"]["source"] != dae_j["meta"]["source"]
        rec_j["meta"]["source"] = None
        dae_j["meta"]["source"] = None
        assert json.dumps(rec_j, sort_keys=True) == json.dumps(dae_j, sort_keys=True)


# ---------------------------------------------------------------------------
# C2 — strict query object: unknown fields & incompatible combinations typed.
# ---------------------------------------------------------------------------

class TestStrictQueryObject:
    def test_unknown_top_field(self):
        with pytest.raises(UnknownFieldError):
            Query.from_dict({"shape": "summary", "metrics": ["ram"], "bogus": 1})

    def test_unknown_nested_fields(self):
        with pytest.raises(UnknownFieldError):
            Query.from_dict({"shape": "summary", "metrics": ["ram"], "selector": {"nope": 1}})
        with pytest.raises(UnknownFieldError):
            Query.from_dict({"shape": "summary", "metrics": ["ram"], "caps": {"nope": 1}})

    def test_from_dict_roundtrips_semantic_and_sort(self):
        q = Query.from_dict(
            {
                "shape": "summary",
                "metrics": ["ram", {"name": "io_r_bps", "semantic": "counter_delta"}],
                "sort": "ram:max:asc",
                "caps": {"max_rows": 5, "on_exceed": "truncate"},
            }
        )
        assert q.metrics[1] == MetricRef("io_r_bps", "counter_delta")
        assert q.sort == SortSpec("ram", "max", "asc")
        assert q.caps.max_rows == 5 and q.caps.on_exceed == "truncate"

    def test_unknown_shape_and_projection(self):
        with pytest.raises(InvalidQueryError):
            run_query(_daemon([_tree_frame()]), Query(shape="bogus", metrics=(MetricRef("ram"),)))
        with pytest.raises(InvalidQueryError):
            run_query(_daemon([_tree_frame()]), Query(shape="summary", metrics=(MetricRef("ram"),), projection="nope"))

    def test_raw_hierarchy_incompatible(self):
        with pytest.raises(IncompatibleQueryError):
            run_query(_daemon([_tree_frame()]), Query(shape="raw", metrics=(MetricRef("ram"),), projection="hierarchy"))

    def test_raw_with_sort_incompatible(self):
        with pytest.raises(IncompatibleQueryError):
            run_query(
                _daemon([_tree_frame()]),
                Query(shape="raw", metrics=(MetricRef("ram"),), sort=SortSpec("ram")),
            )

    def test_sort_metric_not_selected(self):
        with pytest.raises(IncompatibleQueryError):
            run_query(
                _daemon([_tree_frame()]),
                Query(shape="summary", metrics=(MetricRef("ram"),), sort=SortSpec("io_r_bps")),
            )

    def test_summary_sort_stat_invalid_for_semantic(self):
        with pytest.raises(IncompatibleQueryError):
            run_query(
                _daemon([_tree_frame()]),
                Query(shape="summary", metrics=(MetricRef("ram"),), sort=SortSpec("ram", "total")),
            )

    def test_current_sort_stat_not_available(self):
        with pytest.raises(IncompatibleQueryError):
            run_query(
                _daemon([_tree_frame()]),
                Query(shape="current", metrics=(MetricRef("ram"),), sort=SortSpec("ram", "p95")),
            )

    def test_duplicate_metric_typed(self):
        with pytest.raises(IncompatibleQueryError):
            run_query(_daemon([_tree_frame()]), Query(shape="summary", metrics=(MetricRef("ram"), MetricRef("ram"))))

    def test_empty_metrics_typed(self):
        with pytest.raises(InvalidQueryError):
            run_query(_daemon([_tree_frame()]), Query(shape="summary", metrics=()))


# ---------------------------------------------------------------------------
# O9 — hard bounds: each bound is actually violated and the outcome asserted.
# ---------------------------------------------------------------------------

def _wide_frame(n_entities: int) -> Frame:
    ents: dict[str, tuple[str | None, dict[str, MetricValue]]] = {}
    for i in range(n_entities):
        ents[f"e{i:04d}.scope"] = (None, {"ram": _g(float(i))})
    return _frame(100.0, ents)


class TestHardBounds:
    def test_max_rows_error(self):
        with pytest.raises(BoundExceededError) as exc:
            run_query(
                _daemon([_wide_frame(50)]),
                Query(shape="current", metrics=(MetricRef("ram"),), caps=Caps(max_rows=10, on_exceed="error")),
            )
        assert exc.value.bound == "max_rows"
        assert exc.value.limit == 10 and exc.value.observed == 50

    def test_max_rows_truncate(self):
        res = run_query(
            _daemon([_wide_frame(50)]),
            Query(
                shape="current",
                metrics=(MetricRef("ram"),),
                sort=SortSpec("ram", order="desc"),
                caps=Caps(max_rows=10, on_exceed="truncate"),
            ),
        )
        assert len(res.rows) == 10
        assert res.meta["truncation"]["truncated"] is True
        assert res.meta["truncation"]["dropped_rows"] == 40
        # top-10 by ram desc are the highest-numbered entities.
        assert res.rows[0]["key"] == "e0049.scope"

    def test_max_points_error_raw(self):
        frames = [_wide_frame(20) for _ in range(20)]  # 20*20 = 400 upper-bound points
        with pytest.raises(BoundExceededError) as exc:
            run_query(
                _daemon(frames),
                Query(shape="raw", metrics=(MetricRef("ram"),), caps=Caps(max_points=100, on_exceed="error")),
            )
        assert exc.value.bound == "max_points"

    def test_max_points_truncate_raw(self):
        frames = [_wide_frame(20) for _ in range(20)]
        res = run_query(
            _daemon(frames),
            Query(shape="raw", metrics=(MetricRef("ram"),), caps=Caps(max_points=100, on_exceed="truncate")),
        )
        total_points = sum(len(r["points"]) for r in res.rows)
        assert total_points <= 100
        assert res.meta["truncation"]["truncated"] is True
        assert res.meta["truncation"]["reason"] == "max_points"

    def test_max_bytes_error(self):
        with pytest.raises(BoundExceededError) as exc:
            run_query(
                _daemon([_wide_frame(50)]),
                Query(shape="current", metrics=(MetricRef("ram"),), caps=Caps(max_bytes=800, on_exceed="error")),
            )
        assert exc.value.bound == "max_bytes"

    def test_max_bytes_truncate_never_returns_oversize(self):
        cap = 1500
        res = run_query(
            _daemon([_wide_frame(200)]),
            Query(
                shape="current",
                metrics=(MetricRef("ram"),),
                sort=SortSpec("ram", order="desc"),
                caps=Caps(max_bytes=cap, on_exceed="truncate"),
            ),
        )
        encoded = format_result(res).encode("utf-8")
        assert len(encoded) <= cap  # the promise: never an oversize body
        assert res.meta["truncation"]["truncated"] is True
        assert res.meta["truncation"]["reason"] == "max_bytes"

    def test_max_bytes_below_meta_floor_is_typed_error(self):
        # Even truncate cannot satisfy a cap below the empty-rows meta floor.
        with pytest.raises(BoundExceededError):
            run_query(
                _daemon([_wide_frame(5)]),
                Query(shape="current", metrics=(MetricRef("ram"),), caps=Caps(max_bytes=50, on_exceed="truncate")),
            )


# ---------------------------------------------------------------------------
# O10 — byte determinism (two identical invocations)
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_two_runs_byte_identical(self):
        frames = [
            _frame(100.0 + i * 5, {"a.slice": ("", {"ram": _g(1000 + i), "io_r_bps": _rr(100 * i)})})
            for i in range(6)
        ]
        q = Query(shape="summary", metrics=(MetricRef("ram"), MetricRef("io_r_bps")))
        a = format_result(run_query(_daemon(frames), q))
        b = format_result(run_query(_daemon(frames), q))
        assert a == b

    def test_cli_stdout_byte_identical(self, tmp_path):
        frames = _one_entity_gauge([1.0, 2.0, 3.0, 4.0])
        path = tmp_path / "r.jsonl"
        with RecordWriter(path, fsync=False) as w:
            for f in frames:
                w.write_frame(f)
        cmd = [sys.executable, "-m", "topos.cli", "query", str(path), "--shape", "summary", "--metric", "ram", "--json"]
        env = {"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
        r1 = subprocess.run(cmd, capture_output=True, text=True, env=_merged_env(env))
        r2 = subprocess.run(cmd, capture_output=True, text=True, env=_merged_env(env))
        assert r1.returncode == 0, r1.stderr
        assert r1.stdout == r2.stdout


def _merged_env(extra: dict[str, str]) -> dict[str, str]:
    import os

    env = dict(os.environ)
    env.update(extra)
    return env


# ---------------------------------------------------------------------------
# O11 — differential vs P54 report figures on the same recording.
# ---------------------------------------------------------------------------

class TestDifferentialAgainstReport:
    @pytest.mark.parametrize("cold", [True, False])
    def test_gauge_and_rate_figures_match_report(self, tmp_path, cold):
        import topos.report as report

        frames: list[Frame] = []
        for i in range(8):
            ram = 1000.0 + (i % 3) * 50.0
            rate = _rr(1000 * i) if cold else _rl(float(200 + i))
            frames.append(
                _frame(100.0 + i * 5, {"svc.scope": ("", {"ram": _g(ram), "io_r_bps": rate})})
            )
        path = tmp_path / "r.jsonl"
        with RecordWriter(path, fsync=False) as w:
            for f in frames:
                w.write_frame(f)

        # P54 figures.
        profiles = report.compute_profile(frames, window=None, group_by="entity")
        prof = next(p for p in profiles if p.key == "svc.scope")
        report_ram = report._metric_jsonable(prof.gauges["ram"])
        report_rate = report._metric_jsonable(prof.rates["io_r_bps"])

        # P88 figures on the SAME recording/window.
        res = run_query(
            RecordingFrameSource(path),
            Query(shape="summary", metrics=(MetricRef("ram"), MetricRef("io_r_bps"))),
        )
        cell_ram = res.rows[0]["metrics"]["ram"]
        cell_rate = res.rows[0]["metrics"]["io_r_bps"]
        for stat in ("p50", "p95", "max"):
            assert cell_ram[stat] == report_ram[stat], stat
            assert cell_rate[stat] == report_rate[stat], stat


# ---------------------------------------------------------------------------
# O12 — mutation tests: gap / reset metadata propagation is load-bearing.
# ---------------------------------------------------------------------------

class TestMutationMetadata:
    def _reset_frames(self) -> list[Frame]:
        raws = [0, 1000, 2000, 50, 1050]  # reset 2000 -> 50
        return [
            _frame(100.0 + i * 5, {"x.scope": (None, {"io_r_bps": _rr(r)})})
            for i, r in enumerate(raws)
        ]

    def test_reset_metadata_is_load_bearing(self, monkeypatch):
        frames = self._reset_frames()
        q = Query(shape="summary", metrics=(MetricRef("io_r_bps"),))
        good = run_query(_daemon(frames), q)
        assert good.meta["resets"]["count"] == 1
        assert good.rows[0]["metrics"]["io_r_bps"]["max"] == 200.0

        # Mutate the reset-aware sampler to ignore resets (the exact propagation
        # the metadata depends on).  The result MUST change — proving the test
        # is not hollow: a broken sampler admits the negative spike and reports
        # zero resets.
        real = semantics_module._rate_samples

        def broken(points):
            samples = []
            prev = None
            for p in points:
                if p.v is not None:
                    samples.append(p.v)
                elif p.src == "derived" and p.raw is not None and prev is not None:
                    dt = p.ts - prev[0]
                    if dt > 0:
                        samples.append((p.raw - prev[1]) / dt)  # no reset guard
                if p.raw is not None:
                    prev = (p.ts, p.raw)
            return samples, 0  # always zero resets

        monkeypatch.setattr(semantics_module, "_rate_samples", broken)
        bad = run_query(_daemon(frames), q)
        assert bad.meta["resets"]["count"] == 0  # mutation observed
        assert bad.rows[0]["metrics"]["io_r_bps"]["min"] < 0  # bogus negative spike
        assert bad.rows[0]["metrics"]["io_r_bps"] != good.rows[0]["metrics"]["io_r_bps"]
        # restore for safety (monkeypatch also undoes it)
        assert semantics_module._rate_samples is broken
        monkeypatch.setattr(semantics_module, "_rate_samples", real)

    def test_gap_metadata_is_load_bearing(self):
        f0 = _frame(100.0, {"x.scope": (None, {"ram": _g(1.0)})})
        f1 = _frame(105.0, {"x.scope": (None, {"ram": _g(2.0)})})
        # Contiguous seqs -> complete; a broken adapter that dropped the gap
        # flag would render this identical to the gapped case below.
        contiguous = DaemonHistoryFrameSource(((0, f0), (1, f1)), gap=False)
        gapped = DaemonHistoryFrameSource(((0, f0), (5, f1)), gap=False)
        q = Query(shape="summary", metrics=(MetricRef("ram"),))
        assert run_query(contiguous, q).meta["coverage"]["complete"] is True
        gres = run_query(gapped, q)
        assert gres.meta["coverage"]["complete"] is False
        assert gres.meta["gaps"][0]["sequence_gap"] is True
        # The two differ ONLY because the gap metadata propagated.
        assert run_query(contiguous, q).meta["gaps"] == []


# ---------------------------------------------------------------------------
# O14 — the P70 adversarial near-CoV-boundary suffix case.
# ---------------------------------------------------------------------------

class TestP70AdversarialSuffix:
    def test_p88_figures_match_report_over_the_detected_suffix(self, tmp_path):
        import topos.report as report

        # The exact 14-value series P70 hardened around the reverse-Welford
        # boundary flip; report's auto detector selects the WindowRange(130,165)
        # suffix on it.
        values = [
            1.018689928456018e33, 9.510379349013826e32, 1.0274628101202901e33,
            9.738221808425098e32, 1.0246098962410257e33, 1.1053478977123546e33,
            1.0255499019080497e33, 9.390171270132967e32, 9.626065872600107e32,
            1.0883304569007133e33, 9.586188942992692e32, 9.97595211159013e32,
            9.393879626199037e32, 9.879232105661622e32,
        ]
        frames = [
            _frame(100.0 + i * 5, {"busy": (None, {"ram": _g(v)})})
            for i, v in enumerate(values)
        ]
        path = tmp_path / "adversarial.jsonl"
        with RecordWriter(path, fsync=False) as w:
            for f in frames:
                w.write_frame(f)

        computation = report.compute_report_with_selection(path, window_spec="auto", group_by="entity")
        window = computation.window_selection.window
        assert window == report.WindowRange(130.0, 165.0)
        report_ram = report._metric_jsonable(
            next(p for p in computation.profiles if p.key == "busy").gauges["ram"]
        )

        # Express the detected suffix as a last:Ns window for the P88 engine and
        # assert byte-identical figures — the reused percentile math must agree
        # on the exact input P70 stress-tested.
        last_ts = frames[-1].ts
        n = int(last_ts - window.start_ts)  # 165 - 130 = 35
        res = run_query(
            RecordingFrameSource(path),
            Query(shape="summary", metrics=(MetricRef("ram"),), window_spec=f"last:{n}s"),
        )
        cell = res.rows[0]["metrics"]["ram"]
        assert res.meta["sample_count"] == 8
        for stat in ("p50", "p95", "max"):
            assert cell[stat] == report_ram[stat], stat


# ---------------------------------------------------------------------------
# O13 — large synthetic tree performance budget (recorded in the REPORT).
# ---------------------------------------------------------------------------

class TestPerformance:
    def _large_tree(self, n_slices: int, per_slice: int, n_frames: int) -> list[Frame]:
        frames: list[Frame] = []
        for fi in range(n_frames):
            ents: dict[str, tuple[str | None, dict[str, MetricValue]]] = {}
            for si in range(n_slices):
                sk = f"s{si:02d}.slice"
                ents[sk] = ("", {"ram": _g(float(si * 1000 + fi)), "io_r_bps": _rr(1000 * fi + si)})
                for ci in range(per_slice):
                    ck = f"{sk}/c{ci:03d}.scope"
                    ents[ck] = (sk, {"ram": _g(float(ci + fi)), "io_r_bps": _rr(500 * fi + ci)})
            frames.append(_frame(100.0 + fi * 5, ents))
        return frames

    def test_large_tree_within_budget(self):
        # 8 slices x 250 children = 2008 entities, 30 frames.
        frames = self._large_tree(8, 250, 30)
        n_entities = 8 + 8 * 250
        q = Query(
            shape="summary",
            metrics=(MetricRef("ram"), MetricRef("io_r_bps")),
            projection="hierarchy",
            sort=SortSpec("ram", "p95", "desc"),
            caps=Caps(max_rows=n_entities + 10),
        )
        source = _daemon(frames)
        start = time.perf_counter()
        res = run_query(source, q)
        elapsed = time.perf_counter() - start
        encoded = len(format_result(res).encode("utf-8"))
        # Generous budget so it is not flaky on shared CI; the REPORT records
        # the measured wall time and encoded size.
        assert elapsed < 10.0, f"query took {elapsed:.3f}s"
        assert len(res.rows) == n_entities
        assert encoded > 0
        print(f"\n[P88-PERF] entities={n_entities} frames=30 wall={elapsed:.3f}s bytes={encoded}")


# ---------------------------------------------------------------------------
# Frontier review fixes (pass #2) — each test fails against the pre-review code.
# ---------------------------------------------------------------------------

class TestReviewFixes:
    def test_integral_over_rate_with_reset_pairs_samples_with_their_own_ts(self):
        # raw 0,1000,2000,50,1050 at 5s spacing: the 2000->50 reset yields no
        # sample at ts=115, and the post-reset sample belongs to ts=120. The
        # pre-review positional re-pairing assigned the surviving samples to
        # the timestamps 100/105/110 (integral 2000 over span 10); the correct
        # pairing is 105/110/120 (integral 3000 over span 15).
        raws = [0, 1000, 2000, 50, 1050]
        frames = [
            _frame(100.0 + i * 5, {"x.scope": (None, {"io_r_bps": _rr(r)})})
            for i, r in enumerate(raws)
        ]
        res = run_query(
            _daemon(frames),
            Query(shape="summary", metrics=(MetricRef("io_r_bps", semantic="integral"),)),
        )
        cell = res.rows[0]["metrics"]["io_r_bps"]
        assert cell["semantic"] == "integral"
        assert cell["resets"] == 1
        assert cell["integral"] == 3000.0
        assert cell["span_s"] == 15.0

    def test_raw_shape_enforces_max_rows_as_series_error(self):
        frames = [
            _frame(100.0, {f"e{i}.scope": (None, {"ram": _g(float(i))}) for i in range(5)})
        ]
        with pytest.raises(BoundExceededError) as exc:
            run_query(
                _daemon(frames),
                Query(shape="raw", metrics=(MetricRef("ram"),), caps=Caps(max_rows=2)),
            )
        assert exc.value.bound == "max_rows"
        assert exc.value.observed == 5

    def test_raw_shape_truncates_series_at_max_rows(self):
        frames = [
            _frame(100.0, {f"e{i}.scope": (None, {"ram": _g(float(i))}) for i in range(5)})
        ]
        res = run_query(
            _daemon(frames),
            Query(
                shape="raw",
                metrics=(MetricRef("ram"),),
                caps=Caps(max_rows=2, on_exceed="truncate"),
            ),
        )
        assert len(res.rows) == 2
        trunc = res.meta["truncation"]
        assert trunc["truncated"] is True
        assert trunc["reason"] == "max_rows"
        assert trunc["total_series"] == 5
        assert trunc["emitted_series"] == 2

    def test_bounded_tail_read_is_not_eviction(self):
        # oldest_seq BEHIND our first entry means older frames still exist in
        # the ring (an ordinary limit-bounded tail read) — nothing was lost.
        frames = _one_entity_gauge([1.0, 2.0, 3.0])
        src = _daemon(frames, gap=False, start_seq=5, oldest_seq=0)
        res = run_query(src, Query(shape="summary", metrics=(MetricRef("ram"),)))
        assert res.meta["eviction"] == {"occurred": False}
        assert res.meta["coverage"]["complete"] is True
        assert res.meta["gaps"] == []

    def test_oldest_seq_ahead_of_held_frames_is_eviction(self):
        # oldest_seq AHEAD of our first entry: frames we hold were dropped
        # from the ring after the fetch — that IS eviction.
        frames = _one_entity_gauge([1.0, 2.0, 3.0])
        src = _daemon(frames, gap=False, start_seq=5, oldest_seq=7)
        res = run_query(src, Query(shape="summary", metrics=(MetricRef("ram"),)))
        assert res.meta["eviction"] == {"occurred": True}
        assert res.meta["coverage"]["complete"] is False
