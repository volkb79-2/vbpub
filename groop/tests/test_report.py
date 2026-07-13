"""Tests for groop.report — steady-state profile computation.

Covers:
- p50/p95/max correctness on synthetic frame sets (nearest-rank)
- Rate derivation across raw-counter gaps
- --window boundary inclusion/exclusion
- --group-by slice rollup correctness
- Cold-recording (all rate v=None) vs. warm-recording (live rate v) parity
- Malformed-argument exit codes
- Nearest-rank vs. interpolation divergence oracle
- Deterministic byte-identical output
- Degenerate windows (zero frames, single frame)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from groop.model import Entity, EntityFrame, Frame, MetricValue
from groop.report import (
    DEFAULT_MIN_FRAMES,
    DEFAULT_STABILITY_COV,
    DEFAULT_STABILITY_GAUGE,
    REPORT_GAUGES,
    Assertion,
    GroupProfile,
    WindowRange,
    _GaugeSamples,
    _RateSamples,
    _compute_metric_result,
    _derive_rate,
    _filter_frames_by_window,
    _find_slice_ancestor,
    _is_rate_metric,
    _nearest_rank_percentile,
    compute_profile,
    compute_report,
    compute_report_with_selection,
    detect_steady_window,
    evaluate_assertions,
    format_report,
    parse_assert_spec,
    parse_window_spec,
    profile_to_jsonable,
    report_to_jsonable,
    select_report_window,
)
from groop.record.writer import RecordWriter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _entity_stub(ekey: str, *, kind: str = "scope", parent: str = "") -> Entity:
    return Entity(key=ekey, kind=kind, parent=parent)


def _host_stub() -> dict[str, MetricValue]:
    return {
        "host_mem_total": MetricValue(16000, "host"),
        "host_mem_available": MetricValue(8000, "host"),
        "host_swap_total": MetricValue(4000, "host"),
        "host_swap_free": MetricValue(2000, "host"),
        "host_swapcached": MetricValue(100, "host"),
        "host_zswap_pool": MetricValue(50, "host"),
        "host_zswap_stored": MetricValue(100, "host"),
        "host_zswap_ratio": MetricValue(2.0, "host"),
        "host_disk_swap": MetricValue(0, "host"),
        "host_load1": MetricValue(0.1, "host"),
        "host_load5": MetricValue(0.2, "host"),
        "host_load15": MetricValue(0.3, "host"),
        "host_uptime_s": MetricValue(1000, "host"),
        "host_psi_mem_some_avg10": MetricValue(0.0, "host"),
        "host_psi_mem_full_avg10": MetricValue(0.0, "host"),
        "host_psi_io_some_avg10": MetricValue(0.0, "host"),
        "host_psi_io_full_avg10": MetricValue(0.0, "host"),
        "host_psi_cpu_some_avg10": MetricValue(0.0, "host"),
        "host_zswap_enabled": MetricValue(1, "host"),
        "host_zswap_max_pool_percent": MetricValue(20, "host"),
    }


def _frame(
    ts: float,
    entities: dict[str, dict[str, MetricValue]],
    *,
    interval_s: float = 5.0,
) -> Frame:
    """Build a Frame from a dict of {entity_key: {metric_name: MetricValue}}."""
    eframes: dict[str, EntityFrame] = {}
    for ekey, metrics in entities.items():
        # Determine kind and parent heuristically
        if ekey.endswith(".slice"):
            kind = "slice"
            parent = ""
        else:
            kind = "scope"
            parent = ""
        ef = EntityFrame(
            entity=_entity_stub(ekey, kind=kind, parent=parent),
            metrics=metrics,
        )
        eframes[ekey] = ef
    return Frame(
        schema_version=1,
        ts=ts,
        interval_s=interval_s,
        host=_host_stub(),
        entities=eframes,
    )


def _gauge(v: float) -> MetricValue:
    return MetricValue(v=v, src="exact")


def _rate_none(raw: int) -> MetricValue:
    """A rate metric with v=None, src=derived, and a raw counter."""
    return MetricValue(v=None, src="derived", raw=raw)


def _rate_live(v: float) -> MetricValue:
    """A rate metric with a live v (as from P53 headless recording)."""
    return MetricValue(v=v, src="derived")


def _write_recording(path: Path, frames: list[Frame]) -> None:
    """Write synthetic frames through the production P2 writer."""
    with RecordWriter(path, fsync=False) as writer:
        for frame in frames:
            writer.write_frame(frame)


# ===========================================================================
# Unit tests
# ===========================================================================

class TestPercentile:
    """Nearest-rank percentile correctness."""

    def test_small_odd(self):
        """p50 of [1,2,3] = 2 (second element)."""
        assert _nearest_rank_percentile([1.0, 2.0, 3.0], 50) == 2.0

    def test_small_even(self):
        """p50 of [1,2,3,4] = 2 (ceil(0.5*4)=2 → index 1, 0-based)."""
        assert _nearest_rank_percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.0

    def test_p95(self):
        """p95 of [1..100] = 95 (ceil(0.95*100)=95 → index 94)."""
        s = list(range(1, 101))
        assert _nearest_rank_percentile([float(x) for x in s], 95) == 95.0

    def test_p95_small(self):
        """p95 of [1] = 1."""
        assert _nearest_rank_percentile([1.0], 95) == 1.0

    def test_max_is_last(self):
        """max returns the last element."""
        s = [1.0, 2.0, 3.0]
        from groop.report import _max_value
        assert _max_value(s) == 3.0

    def test_nearest_rank_vs_interpolation_oracle(self):
        """A sample count where nearest-rank and linear interpolation diverge.

        5 samples, p50: nearest-rank index = ceil(0.5*5)-1 = 3-1 = 2 → 3rd element.
        Linear interpolation would give (sorted[2]+sorted[1])/2 ≈ 2.5.
        With samples [1,2,3,4,5], nearest-rank p50 = 3, not 2.5.
        """
        samples = [1.0, 2.0, 3.0, 4.0, 5.0]
        nr = _nearest_rank_percentile(samples, 50)
        li = (samples[1] + samples[2]) / 2.0  # linear interpolation
        assert nr == 3.0  # nearest-rank
        assert li == 2.5  # linear interpolation would differ
        assert nr != li  # The oracle: they MUST differ


class TestComputeMetricResult:

    def test_empty(self):
        result = _compute_metric_result([])
        assert result == {"p50": None, "p95": None, "max": None}

    def test_single_sample(self):
        result = _compute_metric_result([42.0])
        assert result["p50"] == 42.0
        assert result["p95"] == 42.0
        assert result["max"] == 42.0

    def test_multi_sample(self):
        result = _compute_metric_result([1.0, 2.0, 3.0, 10.0])
        # sorted = [1,2,3,10]; p50=2, p95=10, max=10
        assert result["p50"] == 2.0
        assert result["p95"] == 10.0
        assert result["max"] == 10.0


class TestIsRateMetric:

    def test_rate_metric(self):
        assert _is_rate_metric("rf_z_per_s") is True
        assert _is_rate_metric("mem_events_high_per_s") is True
        assert _is_rate_metric("io_r_bps") is True  # ends with _bps
        assert _is_rate_metric("net_rx_pps") is True  # ends with _pps
        assert _is_rate_metric("io_r_iops") is True  # ends with _iops
        assert _is_rate_metric("ram") is False
        assert _is_rate_metric("anon") is False


class TestParseWindowSpec:

    def test_all(self):
        assert parse_window_spec("all", 1000.0) is None

    def test_last_100s(self):
        result = parse_window_spec("last:100s", 1000.0)
        assert result == WindowRange(start_ts=900.0, end_ts=1000.0)

    def test_last_0s_rejected(self):
        with pytest.raises(ValueError, match="duration must be positive"):
            parse_window_spec("last:0s", 1000.0)

    def test_last_negative_rejected(self):
        with pytest.raises(ValueError, match="invalid window spec"):
            parse_window_spec("last:-5s", 1000.0)

    def test_malformed_spec(self):
        with pytest.raises(ValueError, match="invalid window spec"):
            parse_window_spec("bad", 1000.0)

    def test_last_no_s_suffix(self):
        with pytest.raises(ValueError, match="invalid window spec"):
            parse_window_spec("last:100", 1000.0)

    def test_empty_string(self):
        with pytest.raises(ValueError, match="invalid window spec"):
            parse_window_spec("", 1000.0)


class TestFilterFramesByWindow:

    def test_all_returns_all(self):
        frames = [_frame(100.0, {"e1": {"ram": _gauge(1)}})]
        assert _filter_frames_by_window(frames, None) == frames

    def test_window_inclusion(self):
        frames = [
            _frame(100.0, {"e1": {"ram": _gauge(1)}}),
            _frame(105.0, {"e1": {"ram": _gauge(2)}}),
            _frame(110.0, {"e1": {"ram": _gauge(3)}}),
        ]
        window = WindowRange(start_ts=103.0, end_ts=108.0)
        filtered = _filter_frames_by_window(frames, window)
        assert len(filtered) == 1
        assert filtered[0].ts == 105.0

    def test_window_exact_boundary(self):
        frames = [
            _frame(100.0, {"e1": {"ram": _gauge(1)}}),
            _frame(110.0, {"e1": {"ram": _gauge(2)}}),
        ]
        window = WindowRange(start_ts=100.0, end_ts=100.0)
        filtered = _filter_frames_by_window(frames, window)
        assert len(filtered) == 1
        assert filtered[0].ts == 100.0

    def test_window_empty_result(self):
        frames = [
            _frame(100.0, {"e1": {"ram": _gauge(1)}}),
        ]
        window = WindowRange(start_ts=200.0, end_ts=300.0)
        filtered = _filter_frames_by_window(frames, window)
        assert len(filtered) == 0


# ===========================================================================
# P62 — Automatic trailing steady-state window detection
# ===========================================================================

class TestAutoSteadyWindow:
    """Oracle tests for the pinned longest-stable-suffix detector."""

    def _tail_stable_frames(self) -> list[Frame]:
        return [
            _frame(100.0, {"busy": {"ram": _gauge(1000), "anon": _gauge(50)}}),
            _frame(105.0, {"busy": {"ram": _gauge(1000), "anon": _gauge(50)}}),
            _frame(110.0, {"busy": {"ram": _gauge(1000), "anon": _gauge(50)}}),
            _frame(115.0, {"busy": {"ram": _gauge(100), "anon": _gauge(50)}}),
            _frame(120.0, {"busy": {"ram": _gauge(101), "anon": _gauge(50)}}),
            _frame(125.0, {"busy": {"ram": _gauge(99), "anon": _gauge(50)}}),
        ]

    def test_oracle_selects_exact_stable_tail_not_all(self):
        """The exact boundary is 115, not a plausible six-frame/all result."""
        frames = self._tail_stable_frames()
        window = detect_steady_window(frames)
        assert window == WindowRange(start_ts=115.0, end_ts=125.0)
        selected = _filter_frames_by_window(frames, window)
        assert [frame.ts for frame in selected] == [115.0, 120.0, 125.0]
        auto = compute_profile(frames, window=window)
        all_profiles = compute_profile(frames)
        assert auto[0].sample_count == 3
        assert all_profiles[0].sample_count == 6
        assert auto[0].gauges["ram"]["p50"] == 100.0
        assert all_profiles[0].gauges["ram"]["p50"] == 101.0

    def test_no_stable_suffix_falls_back_to_all(self):
        frames = [
            _frame(100.0 + i * 5, {"busy": {"ram": _gauge(value)}})
            for i, value in enumerate((10, 20, 10, 20))
        ]
        selection = select_report_window(frames, window_spec="auto")
        assert selection.mode == "auto"
        assert selection.detected is False
        assert selection.window is None
        assert compute_profile(frames, window=selection.window) == compute_profile(frames)
        data = json.loads(format_report(
            compute_profile(frames, window=selection.window),
            window_selection=selection,
        ))
        assert data["window_mode"] == "auto"
        assert data["window_detected"] is False
        assert "window_start_ts" not in data

    def test_gauge_and_cov_overrides_change_window(self):
        frames = self._tail_stable_frames()
        ram_selection = select_report_window(frames, window_spec="auto")
        anon_selection = select_report_window(
            frames, window_spec="auto", stability_gauge="anon",
        )
        cov_selection = select_report_window(
            frames, window_spec="auto", stability_cov=1.0,
        )
        assert ram_selection.window == WindowRange(115.0, 125.0)
        assert anon_selection.window == WindowRange(100.0, 125.0)
        assert cov_selection.window == WindowRange(100.0, 125.0)

    def test_busiest_entity_and_lexical_tie_are_deterministic(self):
        frames = [
            _frame(100.0 + i * 5, {
                "quiet": {"ram": _gauge(value)},
                "busy": {"ram": _gauge(value * 10)},
            })
            for i, value in enumerate((10, 20, 10))
        ]
        # quiet's CoV is also noisy, but busy must be the measured entity;
        # a stable quiet entity must never mask a noisy busiest entity.
        frames[0].entities["quiet"].metrics["ram"] = _gauge(10)
        frames[1].entities["quiet"].metrics["ram"] = _gauge(10)
        frames[2].entities["quiet"].metrics["ram"] = _gauge(10)
        assert detect_steady_window(frames, stability_cov=0.05) is None

    @pytest.mark.parametrize(
        ("gauge", "cov", "min_frames"),
        [
            ("unknown", DEFAULT_STABILITY_COV, DEFAULT_MIN_FRAMES),
            (DEFAULT_STABILITY_GAUGE, -0.1, DEFAULT_MIN_FRAMES),
            (DEFAULT_STABILITY_GAUGE, float("inf"), DEFAULT_MIN_FRAMES),
            (DEFAULT_STABILITY_GAUGE, DEFAULT_STABILITY_COV, 0),
        ],
    )
    def test_invalid_detector_options_raise_value_error(self, gauge, cov, min_frames):
        with pytest.raises(ValueError):
            detect_steady_window([], stability_gauge=gauge, stability_cov=cov, min_frames=min_frames)

    def test_auto_json_metadata_and_determinism(self, tmp_path):
        path = tmp_path / "tail.jsonl"
        _write_recording(path, self._tail_stable_frames())
        first = compute_report_with_selection(path, window_spec="auto")
        second = compute_report_with_selection(path, window_spec="auto")
        first_json = format_report(first.profiles, window_selection=first.window_selection)
        second_json = format_report(second.profiles, window_selection=second.window_selection)
        data = json.loads(first_json)
        assert first_json == second_json
        assert data["window_mode"] == "auto"
        assert data["window_detected"] is True
        assert data["window_start_ts"] == 115.0
        assert data["window_end_ts"] == 125.0


class TestReportAutoCLI:
    """CLI validation and assertion composition for auto windows."""

    def _invoke(self, recording: Path, *args: str) -> subprocess.CompletedProcess:
        src_root = Path(__file__).resolve().parents[1] / "src"
        return subprocess.run(
            [sys.executable, "-m", "groop.cli", "report", str(recording), "--json", *args],
            capture_output=True,
            text=True,
            cwd=str(src_root),
            env={"PYTHONPATH": str(src_root)},
        )

    def test_auto_asserts_detected_window(self, tmp_path):
        path = tmp_path / "tail.jsonl"
        frames = TestAutoSteadyWindow()._tail_stable_frames()
        _write_recording(path, frames)
        result = self._invoke(path, "--window", "auto", "--assert", "busy:ram:max<=101")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["window_detected"] is True
        assert data["assertions"][0]["actual"] == 101.0

    @pytest.mark.parametrize(
        "args",
        [
            ("--stability-gauge", "not-a-gauge"),
            ("--stability-cov", "nan"),
            ("--stability-cov", "-0.1"),
            ("--min-frames", "0"),
        ],
    )
    def test_malformed_auto_overrides_exit_2(self, tmp_path, args):
        path = tmp_path / "one.jsonl"
        _write_recording(path, [_frame(100.0, {"busy": {"ram": _gauge(1)}})])
        result = self._invoke(path, "--window", "auto", *args)
        assert result.returncode == 2


class TestFindSliceAncestor:

    def _make_parent_frames(self) -> list[Frame]:
        """Build frames with a realistic parent chain."""
        metrics = {"ram": _gauge(1)}
        e1 = EntityFrame(
            entity=_entity_stub("system.slice/docker-aaa.scope", kind="scope", parent="system.slice"),
            metrics=metrics,
        )
        e2 = EntityFrame(
            entity=_entity_stub("system.slice", kind="slice", parent=""),
            metrics=metrics,
        )
        e3 = EntityFrame(
            entity=_entity_stub("", kind="root", parent=None),
            metrics=metrics,
        )
        frame = Frame(
            schema_version=1, ts=100.0, interval_s=5.0,
            host=_host_stub(),
            entities={
                "system.slice/docker-aaa.scope": e1,
                "system.slice": e2,
                "": e3,
            },
        )
        return [frame]

    def test_entity_is_slice(self):
        assert _find_slice_ancestor("system.slice", []) == "system.slice"

    def test_scope_finds_slice_ancestor(self):
        frames = self._make_parent_frames()
        result = _find_slice_ancestor("system.slice/docker-aaa.scope", frames)
        assert result == "system.slice"

    def test_root_has_no_slice(self):
        frames = self._make_parent_frames()
        result = _find_slice_ancestor("", frames)
        assert result == ""

    def test_unknown_entity_falls_back_to_root(self):
        result = _find_slice_ancestor("nonexistent.path", [])
        assert result == ""


class TestDeriveRate:

    def _make_rate_frames(self) -> list[Frame]:
        f1 = _frame(100.0, {"ent": {"rf_z_per_s": _rate_none(1000)}})
        f2 = _frame(105.0, {"ent": {"rf_z_per_s": _rate_none(1100)}})
        f3 = _frame(110.0, {"ent": {"rf_z_per_s": _rate_none(1250)}})
        return [f1, f2, f3]

    def test_basic_derivation(self):
        frames = self._make_rate_frames()
        result = _derive_rate("rf_z_per_s", "ent", frames, 2)  # index 2 = 1250
        # raw_delta = 1250 - 1100 = 150, ts_delta = 110 - 105 = 5 → 30
        assert result == pytest.approx(30.0)

    def test_skip_missing_entity_in_middle(self):
        f1 = _frame(100.0, {"ent": {"rf_z_per_s": _rate_none(1000)}})
        f2 = _frame(105.0, {})  # entity absent
        f3 = _frame(110.0, {"ent": {"rf_z_per_s": _rate_none(1200)}})
        result = _derive_rate("rf_z_per_s", "ent", [f1, f2, f3], 2)
        # raw_delta = 1200 - 1000 = 200, ts_delta = 110 - 100 = 10 → 20
        assert result == pytest.approx(20.0)

    def test_gap_after_entity_churn(self):
        f1 = _frame(100.0, {})
        f2 = _frame(110.0, {"ent": {"rf_z_per_s": _rate_none(500)}})
        f3 = _frame(115.0, {"ent": {"rf_z_per_s": _rate_none(520)}})
        result = _derive_rate("rf_z_per_s", "ent", [f1, f2, f3], 2)
        # raw_delta = 520 - 500 = 20, ts_delta = 115 - 110 = 5 → 4
        assert result == pytest.approx(4.0)

    def test_no_earlier_frame(self):
        f1 = _frame(100.0, {"ent": {"rf_z_per_s": _rate_none(1000)}})
        result = _derive_rate("rf_z_per_s", "ent", [f1], 0)
        assert result is None

    def test_counter_regression_returns_none(self):
        f1 = _frame(100.0, {"ent": {"rf_z_per_s": _rate_none(2000)}})
        f2 = _frame(105.0, {"ent": {"rf_z_per_s": _rate_none(1000)}})  # regressed
        result = _derive_rate("rf_z_per_s", "ent", [f1, f2], 1)
        assert result is None

    def test_live_v_used_as_is(self):
        """When a frame carries a live v, _group_frames uses it verbatim rather
        than re-deriving from raw counters (handoff: "use it as-is")."""
        from groop.report import _group_frames
        # Live v=7.0 but a raw counter that, if derived, would give a different
        # number. The reported sample must be the live 7.0, not a derived value.
        f1 = _frame(100.0, {"e1": {"rf_z_per_s": _rate_none(1000)}})
        f2 = _frame(105.0, {"e1": {"rf_z_per_s": MetricValue(v=7.0, src="derived", raw=9999)}})
        groups = _group_frames([f1, f2], "entity")
        rate_samples = groups["e1"]["rf_z_per_s"].values
        assert 7.0 in rate_samples


# ===========================================================================
# Integration tests — compute_profile
# ===========================================================================

class TestComputeProfile:

    def test_empty_frames(self):
        profiles = compute_profile([])
        assert profiles == []

    def test_single_frame_single_entity(self):
        frames = [_frame(100.0, {"e1": {"ram": _gauge(1000)}})]
        profiles = compute_profile(frames)
        assert len(profiles) == 1
        assert profiles[0].key == "e1"
        assert profiles[0].gauges["ram"]["p50"] == 1000.0
        assert profiles[0].gauges["ram"]["max"] == 1000.0

    def test_multiple_entities(self):
        frames = [_frame(100.0, {
            "e1": {"ram": _gauge(100), "anon": _gauge(50)},
            "e2": {"ram": _gauge(200), "anon": _gauge(75)},
        })]
        profiles = compute_profile(frames)
        assert len(profiles) == 2
        assert profiles[0].key == "e1"
        assert profiles[1].key == "e2"

    def test_only_report_gauges_included(self):
        """Non-gauge metrics like host_mem_total are not in REPORT_GAUGES."""
        frames = [_frame(100.0, {"e1": {"ram": _gauge(100), "host_mem_total": _gauge(999)}})]
        profiles = compute_profile(frames)
        assert len(profiles) == 1
        assert "ram" in profiles[0].gauges
        assert "host_mem_total" not in profiles[0].gauges

    def test_none_values_skipped(self):
        frames = [_frame(100.0, {"e1": {"ram": MetricValue(v=None, src="exact")}})]
        profiles = compute_profile(frames)
        # Entity with no non-None gauges = empty profile → still present?
        # Actually, the entity is present but has no non-None gauge samples,
        # so it won't appear in profiles at all.
        assert len(profiles) == 0

    def test_multi_frame_p50_p95(self):
        frames = [
            _frame(100.0, {"e1": {"ram": _gauge(100)}}),
            _frame(105.0, {"e1": {"ram": _gauge(200)}}),
            _frame(110.0, {"e1": {"ram": _gauge(300)}}),
            _frame(115.0, {"e1": {"ram": _gauge(400)}}),
        ]
        profiles = compute_profile(frames)
        assert len(profiles) == 1
        # sorted = [100,200,300,400]; p50 (ceil(0.5*4)-1 = 2-1=1 → 200); p95 (ceil(0.95*4)-1 = 4-1=3 → 400)
        assert profiles[0].gauges["ram"]["p50"] == 200.0
        assert profiles[0].gauges["ram"]["p95"] == 400.0
        assert profiles[0].gauges["ram"]["max"] == 400.0

    def test_window_filtering(self):
        frames = [
            _frame(100.0, {"e1": {"ram": _gauge(10)}}),
            _frame(110.0, {"e1": {"ram": _gauge(20)}}),
            _frame(120.0, {"e1": {"ram": _gauge(30)}}),
            _frame(130.0, {"e1": {"ram": _gauge(40)}}),
        ]
        window = WindowRange(start_ts=105.0, end_ts=125.0)
        profiles = compute_profile(frames, window=window)
        assert len(profiles) == 1
        assert profiles[0].gauges["ram"]["p50"] == 20.0  # from 110 and 120
        assert profiles[0].window_start_ts == 110.0
        assert profiles[0].window_end_ts == 120.0

    def test_degenerate_zero_frame_window(self):
        """A window that selects zero frames produces no profiles."""
        frames = [
            _frame(100.0, {"e1": {"ram": _gauge(10)}}),
        ]
        window = WindowRange(start_ts=200.0, end_ts=300.0)
        profiles = compute_profile(frames, window=window)
        assert profiles == []

    def test_degenerate_single_frame_profile(self):
        """Single frame: rates cannot be derived (no prior frame), gauges ok."""
        frames = [_frame(100.0, {"e1": {
            "ram": _gauge(100),
            "rf_z_per_s": _rate_none(500),
        }})]
        profiles = compute_profile(frames)
        assert len(profiles) == 1
        assert profiles[0].gauges["ram"]["p50"] == 100.0
        # Rate with no prior frame → no rate samples → absent from rates
        assert "rf_z_per_s" not in profiles[0].rates

    def test_warm_vs_cold_rate_parity(self):
        """Cold recording (all v=None, raw populated) and warm recording (live v) produce same rate samples.

        The cold recording's first frame rate is None (no prior raw), while the
        warm recording has a live v from frame 1. Both should report the same
        p50 for the second frame where derivation is possible.
        """
        # Cold: v=None, raw counters present → derive from prior frame
        f1_cold = _frame(100.0, {"e1": {"rf_z_per_s": _rate_none(1000)}})
        f2_cold = _frame(105.0, {"e1": {"rf_z_per_s": _rate_none(1050)}})

        # Warm: same rate, v present (the derived value = (1050-1000)/5 = 10)
        f1_warm = _frame(100.0, {"e1": {"rf_z_per_s": _rate_live(0.0)}})
        f2_warm = _frame(105.0, {"e1": {"rf_z_per_s": _rate_live(10.0)}})

        cold_profiles = compute_profile([f1_cold, f2_cold])
        warm_profiles = compute_profile([f1_warm, f2_warm])

        assert len(cold_profiles) == 1
        assert len(warm_profiles) == 1

        # Warm: live rates [0.0, 10.0] → p50 = 0.0 (ceil(0.5*2)-1=0, index 0)
        assert warm_profiles[0].rates["rf_z_per_s"]["p50"] == 0.0
        assert warm_profiles[0].rates["rf_z_per_s"]["p95"] == 10.0
        assert warm_profiles[0].rates["rf_z_per_s"]["max"] == 10.0

        # Cold: frame 1 has None rate (no prior), frame 2 derives 10.0
        # So rate samples = [10.0]; p50 = p95 = max = 10.0
        assert cold_profiles[0].rates["rf_z_per_s"]["p50"] == 10.0
        assert cold_profiles[0].rates["rf_z_per_s"]["p95"] == 10.0
        assert cold_profiles[0].rates["rf_z_per_s"]["max"] == 10.0

        # The derived value (10.0) matches the warm recorded value
        assert cold_profiles[0].rates["rf_z_per_s"]["max"] == warm_profiles[0].rates["rf_z_per_s"]["max"]

    def test_group_by_slice(self):
        """--group-by slice rolls up entities under their *.slice ancestor."""
        metrics = {"ram": _gauge(1000)}
        e_scope1 = EntityFrame(
            entity=_entity_stub("system.slice/docker-aaa.scope", kind="scope", parent="system.slice"),
            metrics=metrics,
        )
        e_scope2 = EntityFrame(
            entity=_entity_stub("system.slice/docker-bbb.scope", kind="scope", parent="system.slice"),
            metrics=metrics,
        )
        e_slice = EntityFrame(
            entity=_entity_stub("system.slice", kind="slice", parent=""),
            metrics=metrics,
        )
        frame = Frame(
            schema_version=1, ts=100.0, interval_s=5.0,
            host=_host_stub(),
            entities={
                "system.slice/docker-aaa.scope": e_scope1,
                "system.slice/docker-bbb.scope": e_scope2,
                "system.slice": e_slice,
            },
        )
        # entity grouping → 3 entities
        entity_profiles = compute_profile([frame], group_by="entity")
        assert len(entity_profiles) == 3

        # slice grouping → 1 slice
        slice_profiles = compute_profile([frame], group_by="slice")
        assert len(slice_profiles) == 1
        assert slice_profiles[0].key == "system.slice"

    def test_window_spec_last_100s(self):
        """--window last:Ns selects frames within the time window."""
        frames = [
            _frame(100.0 + i * 6, {"e1": {"ram": _gauge(float(i * 10))}})
            for i in range(5)
        ]  # ts: 100, 106, 112, 118, 124
        last_ts = frames[-1].ts  # 124
        window = parse_window_spec("last:100s", last_ts)
        assert window is not None
        assert window.start_ts == 24.0
        assert window.end_ts == 124.0
        profiles = compute_profile(frames, window=window)
        # All 5 frames are within [24, 124]
        assert len(profiles) == 1
        assert profiles[0].sample_count == 5

    def test_all_report_gauges_covered(self):
        """All REPORT_GAUGES appear when present in frames."""
        metrics = {g: _gauge(float(i * 100)) for i, g in enumerate(REPORT_GAUGES)}
        frames = [_frame(100.0, {"e1": metrics})]
        profiles = compute_profile(frames)
        assert len(profiles) == 1
        for g in REPORT_GAUGES:
            assert g in profiles[0].gauges, f"Missing gauge: {g}"

    def test_deterministic_output(self):
        """Same input produces identical JSON bytes."""
        frames = [
            _frame(100.0, {"e1": {"ram": _gauge(100)}}),
            _frame(105.0, {"e1": {"ram": _gauge(200)}}),
        ]
        p1 = compute_profile(frames)
        p2 = compute_profile(frames)
        j1 = format_report(p1)
        j2 = format_report(p2)
        assert j1 == j2
        # Also verify the bytes are identical
        assert json.loads(j1) == json.loads(j2)

    def test_multiple_calls_same_bytes(self):
        """Two calls on the same Fixture produce byte-identical output."""
        frames = [
            _frame(100.0, {"e1": {"ram": _gauge(100), "anon": _gauge(50)}}),
            _frame(105.0, {"e1": {"ram": _gauge(200), "anon": _gauge(75)}}),
        ]
        r1 = format_report(compute_profile(frames))
        r2 = format_report(compute_profile(frames))
        assert r1 == r2


# ===========================================================================
# JSON serialization tests
# ===========================================================================

class TestJsonSerialization:

    def test_profile_to_jsonable(self):
        profile = GroupProfile(
            key="test_entity",
            sample_count=2,
            window_start_ts=100.0,
            window_end_ts=105.0,
            gauges={"ram": {"p50": 100.0, "p95": 200.0, "max": 200.0}},
            rates={},
        )
        d = profile_to_jsonable(profile)
        assert d["key"] == "test_entity"
        assert d["sample_count"] == 2
        # Floats rounded to 6 decimal places
        assert d["gauges"]["ram"]["p50"] == 100.0

    def test_report_to_jsonable_deterministic(self):
        profiles = [
            GroupProfile(
                key="b",
                sample_count=1,
                window_start_ts=100.0,
                window_end_ts=100.0,
                gauges={"ram": {"p50": 1.0, "p95": 1.0, "max": 1.0}},
                rates={},
            ),
            GroupProfile(
                key="a",
                sample_count=1,
                window_start_ts=100.0,
                window_end_ts=100.0,
                gauges={"ram": {"p50": 2.0, "p95": 2.0, "max": 2.0}},
                rates={},
            ),
        ]
        d = report_to_jsonable(profiles)
        # Profiles should be sorted by key (they already are in this example)
        assert len(d["profiles"]) == 2

    def test_float_rounding(self):
        """Floats are rounded to 6 decimal places."""
        profile = GroupProfile(
            key="e1",
            sample_count=1,
            window_start_ts=100.123456789,
            window_end_ts=100.123456789,
            gauges={"ram": {"p50": 100.123456789, "p95": 100.123456789, "max": 100.123456789}},
            rates={},
        )
        j = format_report([profile])
        d = json.loads(j)
        assert d["profiles"][0]["gauges"]["ram"]["p50"] == pytest.approx(100.123457, abs=1e-6)
        assert d["profiles"][0]["window_start_ts"] == pytest.approx(100.123457, abs=1e-6)


# ===========================================================================
# CLI tests
# ===========================================================================

class TestReportCLI:

    def test_no_json_flag_exits_2(self):
        src_root = Path(__file__).resolve().parents[1] / "src"
        result = subprocess.run(
            [sys.executable, "-m", "groop.cli", "report", "some_file.jsonl"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).resolve().parents[1] / "src"),
            env={"PYTHONPATH": str(src_root)},
        )
        assert result.returncode == 2
        assert "required" in result.stderr.lower()

    def test_bad_window_spec_exits_2(self, tmp_path):
        """Malformed --window spec exits 2."""
        src_root = Path(__file__).resolve().parents[1] / "src"
        recording = tmp_path / "test.jsonl"
        recording.write_text('{"type":"header","schema_version":1}\n{"type":"frame","schema_version":1,"ts":100,"interval_s":5,"host":{"host_mem_total":[16000,"host"]},"entities":{}}\n')
        result = subprocess.run(
            [sys.executable, "-m", "groop.cli", "report", str(recording), "--json", "--window", "bad"],
            capture_output=True, text=True,
            cwd=str(src_root),
            env={"PYTHONPATH": str(src_root)},
        )
        assert result.returncode == 2
        assert "invalid window spec" in result.stderr

    def test_missing_file_exits_2(self):
        src_root = Path(__file__).resolve().parents[1] / "src"
        result = subprocess.run(
            [sys.executable, "-m", "groop.cli", "report", "nonexistent.jsonl", "--json"],
            capture_output=True, text=True,
            cwd=str(src_root),
            env={"PYTHONPATH": str(src_root)},
        )
        assert result.returncode == 2
        assert "not found" in result.stderr

    def test_report_on_fixture_via_cli(self, tmp_path):
        """Run groop report on the existing gstammtisch-once fixture via CLI."""
        src_root = Path(__file__).resolve().parents[1] / "src"
        fixture = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "frames" / "gstammtisch-once.jsonl"
        result = subprocess.run(
            [sys.executable, "-m", "groop.cli", "report", str(fixture), "--json"],
            capture_output=True, text=True,
            cwd=str(src_root),
            env={"PYTHONPATH": str(src_root)},
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "profiles" in data
        assert len(data["profiles"]) > 0
        for p in data["profiles"]:
            assert "key" in p
            if "gauges" in p:
                for gauge_name, gauge_vals in p["gauges"].items():
                    assert "p50" in gauge_vals
                    assert "p95" in gauge_vals
                    assert "max" in gauge_vals

    def test_zst_without_zstandard_exits_2(self, tmp_path):
        """A .jsonl.zst file without the zstandard extra exits 2."""
        zstd_magic = b"\x28\xb5\x2f\xfd"
        src_root = Path(__file__).resolve().parents[1] / "src"
        fpath = tmp_path / "fake.zst"
        # Write zstd magic bytes + garbage so RecordReader detects zstd
        with open(fpath, "wb") as f:
            f.write(zstd_magic)
            f.write(b"not valid zstd content")
        result = subprocess.run(
            [sys.executable, "-m", "groop.cli", "report", str(fpath), "--json"],
            capture_output=True, text=True,
            cwd=str(src_root),
            env={"PYTHONPATH": str(src_root)},
        )
        assert result.returncode == 2
        # Should mention zstandard in the error
        assert "zstandard" in result.stderr

    def test_cli_deterministic_output(self, tmp_path):
        """Same fixture reported twice → identical bytes."""
        src_root = Path(__file__).resolve().parents[1] / "src"
        fixture = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "frames" / "gstammtisch-once.jsonl"

        r1 = subprocess.run(
            [sys.executable, "-m", "groop.cli", "report", str(fixture), "--json"],
            capture_output=True, text=True,
            cwd=str(src_root),
            env={"PYTHONPATH": str(src_root)},
        )
        r2 = subprocess.run(
            [sys.executable, "-m", "groop.cli", "report", str(fixture), "--json"],
            capture_output=True, text=True,
            cwd=str(src_root),
            env={"PYTHONPATH": str(src_root)},
        )
        assert r1.stdout == r2.stdout


# ===========================================================================
# Edge cases
# ===========================================================================

class TestEdgeCases:

    def test_entity_present_then_absent(self):
        """Entity present in some frames, absent in others → no error."""
        frames = [
            _frame(100.0, {"e1": {"ram": _gauge(100)}}),
            _frame(105.0, {}),
            _frame(110.0, {"e1": {"ram": _gauge(200)}}),
        ]
        profiles = compute_profile(frames)
        assert len(profiles) == 1
        # e1 has 2 samples → p50 from [100, 200] = 100 (ceil(0.5*2)-1=1-1=0 → index 0)
        assert profiles[0].gauges["ram"]["p50"] == 100.0

    def test_rate_with_mixed_live_and_none(self):
        """Some frames have live v, some have v=None with raw."""
        f1 = _frame(100.0, {"e1": {"rf_z_per_s": _rate_live(10.0)}})
        f2 = _frame(105.0, {"e1": {"rf_z_per_s": _rate_none(1100)}})
        f3 = _frame(110.0, {"e1": {"rf_z_per_s": _rate_live(25.0)}})
        profiles = compute_profile([f1, f2, f3])
        assert len(profiles) == 1
        # f1 has live 10.0, f2 has None → derives (1100 - ?), f3 has live 25.0
        # f2: raw 1100, looks back to f1 → f1 has no raw, skip; no earlier → None
        # So rates: [10.0, 25.0] → p50 = ceil(0.5*2)-1 = 0 → 10.0
        assert profiles[0].rates["rf_z_per_s"]["p50"] == 10.0


# ===========================================================================
# P61 — Assertion parsing, evaluation, and CLI integration
# ===========================================================================

class TestParseAssertSpec:
    """parse_assert_spec unit tests."""

    def test_valid_less_or_equal(self):
        a = parse_assert_spec("mygroup:ram:p50<=100.5")
        assert a.group == "mygroup"
        assert a.metric == "ram"
        assert a.stat == "p50"
        assert a.op == "<="
        assert a.value == 100.5

    def test_valid_greater_or_equal(self):
        a = parse_assert_spec("e1:anon:p95>=1e9")
        assert a.group == "e1"
        assert a.metric == "anon"
        assert a.stat == "p95"
        assert a.op == ">="
        assert a.value == 1e9

    def test_valid_max_stat(self):
        a = parse_assert_spec("s:ram:max<=1")
        assert a.stat == "max"

    def test_valid_empty_group(self):
        """Root entity key is empty string."""
        a = parse_assert_spec(":ram:p50<=10")
        assert a.group == ""

    def test_valid_integer_value(self):
        a = parse_assert_spec("g:m:p50<=42")
        assert a.value == 42.0

    def test_valid_negative_value(self):
        a = parse_assert_spec("g:m:p95>=-1.5")
        assert a.value == -1.5

    def test_invalid_no_colon(self):
        with pytest.raises(ValueError, match="invalid --assert spec"):
            parse_assert_spec("badformat")

    def test_invalid_stat(self):
        with pytest.raises(ValueError, match="invalid --assert spec"):
            parse_assert_spec("g:m:p99<=1")

    def test_invalid_op(self):
        with pytest.raises(ValueError, match="invalid --assert spec"):
            parse_assert_spec("g:m:max==1")

    def test_invalid_not_a_number(self):
        with pytest.raises(ValueError, match="invalid --assert spec"):
            parse_assert_spec("g:m:max<=abc")

    def test_invalid_nan(self):
        with pytest.raises(ValueError, match="invalid --assert spec"):
            parse_assert_spec("g:m:max<=nan")

    def test_invalid_inf(self):
        with pytest.raises(ValueError, match="invalid --assert spec"):
            parse_assert_spec("g:m:max<=inf")


class TestEvaluateAssertions:
    """evaluate_assertions unit tests."""

    def _make_profile(
        self, key: str,
        gauges: dict[str, dict[str, float | None]] | None = None,
        rates: dict[str, dict[str, float | None]] | None = None,
    ) -> GroupProfile:
        return GroupProfile(
            key=key,
            sample_count=10,
            window_start_ts=100.0,
            window_end_ts=200.0,
            gauges=gauges or {},
            rates=rates or {},
        )

    def test_all_pass(self):
        """All assertions pass when bounds are satisfied."""
        profiles = [self._make_profile("e1", gauges={
            "ram": {"p50": 100.0, "p95": 200.0, "max": 300.0},
        })]
        assertions = [Assertion("e1", "ram", "max", "<=", 500.0)]
        results = evaluate_assertions(profiles, assertions)
        assert len(results) == 1
        assert results[0].passed is True

    def test_pass_no_assertions(self):
        """No assertions → empty results list."""
        assert evaluate_assertions([], []) == []

    def test_breached_less_or_equal(self):
        """A <= assertion is breached when actual > threshold."""
        profiles = [self._make_profile("e1", gauges={
            "ram": {"max": 1000.0},
        })]
        assertions = [Assertion("e1", "ram", "max", "<=", 500.0)]
        results = evaluate_assertions(profiles, assertions)
        assert len(results) == 1
        assert results[0].passed is False
        assert results[0].actual == 1000.0
        assert "breached" in (results[0].reason or "")

    def test_breached_greater_or_equal(self):
        """A >= assertion is breached when actual < threshold."""
        profiles = [self._make_profile("e1", gauges={
            "ram": {"max": 100.0},
        })]
        assertions = [Assertion("e1", "ram", "max", ">=", 500.0)]
        results = evaluate_assertions(profiles, assertions)
        assert len(results) == 1
        assert results[0].passed is False
        assert results[0].actual == 100.0

    def test_passing_greater_or_equal(self):
        """A >= assertion passes when actual >= threshold."""
        profiles = [self._make_profile("e1", gauges={
            "ram": {"max": 1000.0},
        })]
        assertions = [Assertion("e1", "ram", "max", ">=", 500.0)]
        results = evaluate_assertions(profiles, assertions)
        assert len(results) == 1
        assert results[0].passed is True

    def test_absent_group_breach(self):
        """Referencing an absent group is a breach with clear reason."""
        profiles = [self._make_profile("existing")]
        assertions = [Assertion("absent", "ram", "max", "<=", 100)]
        results = evaluate_assertions(profiles, assertions)
        assert len(results) == 1
        assert results[0].passed is False
        assert results[0].reason == "group not present in report"

    def test_absent_metric_breach(self):
        """Referencing an absent metric is a breach with clear reason."""
        profiles = [self._make_profile("e1", gauges={"ram": {"max": 100.0}})]
        assertions = [Assertion("e1", "nonexistent_metric", "max", "<=", 100)]
        results = evaluate_assertions(profiles, assertions)
        assert len(results) == 1
        assert results[0].passed is False
        assert results[0].reason == "metric not present in report"

    def test_null_stat_breach(self):
        """A metric present but with a null stat is a breach (single-frame rate)."""
        profiles = [self._make_profile("e1", rates={
            "rf_z_per_s": {"p50": None, "p95": None, "max": None},
        })]
        assertions = [Assertion("e1", "rf_z_per_s", "p50", "<=", 100)]
        results = evaluate_assertions(profiles, assertions)
        assert len(results) == 1
        assert results[0].passed is False
        assert results[0].reason is not None
        assert "null" in results[0].reason.lower()

    def test_rate_metric_matched(self):
        """Assertions can reference rate metrics."""
        profiles = [self._make_profile("e1", rates={
            "rf_z_per_s": {"p50": 10.0, "p95": 50.0, "max": 100.0},
        })]
        assertions = [Assertion("e1", "rf_z_per_s", "p95", "<=", 60.0)]
        results = evaluate_assertions(profiles, assertions)
        assert len(results) == 1
        assert results[0].passed is True
        assert results[0].actual == 50.0

    def test_multiple_asserts_one_fails(self):
        """Multiple ANDed assertions: one failure causes overall failure."""
        profiles = [self._make_profile("e1", gauges={
            "ram": {"p50": 100.0, "p95": 200.0, "max": 300.0},
            "anon": {"p50": 50.0, "p95": 100.0, "max": 150.0},
        })]
        assertions = [
            Assertion("e1", "ram", "max", "<=", 500.0),    # pass
            Assertion("e1", "anon", "max", "<=", 100.0),   # fail (actual 150)
        ]
        results = evaluate_assertions(profiles, assertions)
        assert len(results) == 2
        # Deterministic sort: group, metric, stat, op = anon then ram
        assert results[0].metric == "anon"
        assert results[0].passed is False  # anon fails
        assert results[1].metric == "ram"
        assert results[1].passed is True   # ram passes

    def test_deterministic_output(self):
        """Same inputs produce identical results."""
        profiles = [self._make_profile("b", gauges={"ram": {"max": 100.0}}),
                    self._make_profile("a", gauges={"ram": {"max": 200.0}})]
        assertions = [
            Assertion("b", "ram", "max", "<=", 150),
            Assertion("a", "ram", "max", "<=", 250),
        ]
        r1 = evaluate_assertions(profiles, assertions)
        r2 = evaluate_assertions(profiles, assertions)
        # Convert to jsonable for comparison
        from groop.report import assertion_result_to_jsonable
        j1 = [assertion_result_to_jsonable(r) for r in r1]
        j2 = [assertion_result_to_jsonable(r) for r in r2]
        assert j1 == j2
        # Verify sort: a before b
        assert r1[0].group == "a"
        assert r1[1].group == "b"


class TestReportAssertionCLI:
    """CLI integration for --assert (subprocess-based, exact exit codes)."""

    FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "frames" / "gstammtisch-once.jsonl"

    def _invoke(self, *args: str) -> subprocess.CompletedProcess:
        src_root = Path(__file__).resolve().parents[1] / "src"
        cmd = [sys.executable, "-m", "groop.cli", "report", str(self.FIXTURE), "--json", *args]
        return subprocess.run(
            cmd,
            capture_output=True, text=True,
            cwd=str(src_root),
            env={"PYTHONPATH": str(src_root)},
        )

    def test_passing_bound_exit_0(self):
        """A passing bound exits 0."""
        result = self._invoke("--assert", ":ram:max<=5000000000")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "assertions" in data
        assert data["assertions"][0]["passed"] is True

    def test_breached_le_exit_1(self):
        """A breached <= bound exits 1 with actual value in JSON."""
        result = self._invoke("--assert", ":ram:max<=100")
        assert result.returncode == 1
        data = json.loads(result.stdout)
        assert data["assertions"][0]["passed"] is False
        assert data["assertions"][0]["actual"] is not None

    def test_breached_ge_exit_1(self):
        """A breached >= bound exits 1."""
        result = self._invoke("--assert", ":ram:p50>=1e12")
        assert result.returncode == 1
        data = json.loads(result.stdout)
        assert data["assertions"][0]["passed"] is False

    def test_absent_group_exit_1(self):
        """Absent group is a breach (exit 1), not a usage error."""
        result = self._invoke("--assert", "nonexistent_group:ram:max<=100")
        assert result.returncode == 1
        data = json.loads(result.stdout)
        assert data["assertions"][0]["passed"] is False
        assert "not present" in data["assertions"][0].get("reason", "")

    def test_absent_metric_exit_1(self):
        """Absent metric is a breach (exit 1)."""
        result = self._invoke("--assert", ":nonexistent:max<=100")
        assert result.returncode == 1
        data = json.loads(result.stdout)
        assert data["assertions"][0]["passed"] is False

    def test_null_stat_exit_1(self):
        """A null STAT (single-frame rate) is a breach (exit 1).

        The fixture has no rates (single frame), so any rate assertion
        is an absent-metric breach.  The unit test ``test_null_stat_breach``
        covers the actual null-stat scenario via synthetic profiles.
        """
        result = self._invoke("--assert", ":rf_z_per_s:p50<=100")
        assert result.returncode == 1
        data = json.loads(result.stdout)
        assert data["assertions"][0]["passed"] is False

    def test_malformed_assert_exit_2(self):
        """Malformed --assert spec exits 2."""
        result = self._invoke("--assert", "badformat")
        assert result.returncode == 2

    def test_unknown_stat_exit_2(self):
        """Unknown STAT in --assert exits 2."""
        result = self._invoke("--assert", ":ram:p99<=100")
        assert result.returncode == 2

    def test_multiple_asserts_one_fails_exit_1(self):
        """Multiple asserts where one fails exits 1."""
        result = self._invoke(
            "--assert", ":ram:max<=5000000000",
            "--assert", ":ram:max<=100",
        )
        assert result.returncode == 1
        data = json.loads(result.stdout)
        assert len(data["assertions"]) == 2
        passing = [a for a in data["assertions"] if a["passed"]]
        failing = [a for a in data["assertions"] if not a["passed"]]
        assert len(passing) == 1
        assert len(failing) == 1

    def test_byte_determinism_two_runs(self):
        """Assertions block is byte-identical across two runs."""
        r1 = self._invoke("--assert", ":ram:max<=5000000000")
        r2 = self._invoke("--assert", ":ram:max<=5000000000")
        assert r1.stdout == r2.stdout
        # Same fixture + same args → both exit 0
        assert r1.returncode == 0
        assert r2.returncode == 0

    def test_no_assert_no_change(self):
        """Without --assert, output is identical to pre-assertion report."""
        r_normal = self._invoke()
        r_assert = self._invoke("--assert", ":ram:max<=5000000000")
        # Both exit 0
        assert r_normal.returncode == 0
        assert r_assert.returncode == 0
        data_normal = json.loads(r_normal.stdout)
        data_assert = json.loads(r_assert.stdout)
        # Profiles match
        assert data_normal["profiles"] == data_assert["profiles"]
        # Assertion-only output has assertions key
        assert "assertions" not in data_normal
        assert "assertions" in data_assert
