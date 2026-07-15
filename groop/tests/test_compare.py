"""Tests for groop.compare — the P64 informational baseline delta helper.

Numbered acceptance oracles (handoff §Acceptance oracles):
  O1  positive/negative absolute and percentage deltas
  O2  zero baseline vs zero current -> explicit typed outcome, no division
  O3  zero baseline vs nonzero current -> explicit typed outcome
  O4  mismatched semantics/units -> explicit typed refusal
  O5  missing/redacted values -> explicit typed outcome, never a silent pass
  O6  unequal coverage -> explicit typed outcome
  O7  P61/P64 exit codes combine deterministically, preserving 0/1/2
  O8  deterministic output ordering across repeated runs
  O9  the helper never reads or re-profiles frames
"""

from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from groop.compare import (
    CompareError,
    OUTCOME_INCOMPLETE_COVERAGE,
    OUTCOME_MISSING,
    OUTCOME_MISSING_BASELINE,
    OUTCOME_MISSING_CURRENT,
    OUTCOME_OK,
    OUTCOME_REDACTED,
    OUTCOME_RESET_BOUNDARY,
    OUTCOME_SEMANTIC_MISMATCH,
    OUTCOME_ZERO_BASELINE,
    OUTCOME_ZERO_ZERO,
    combine_exit_codes,
    compare_exit_code,
    compare_summaries,
    compare_to_jsonable,
    evaluate_compare_rules,
    format_compare,
    parse_compare_rule,
)

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"


def _merged_env(extra: dict[str, str]) -> dict[str, str]:
    env = dict(os.environ)
    env.update(extra)
    return env


# ---------------------------------------------------------------------------
# Builders — hand-crafted P88 shape="summary" results (no recording, no Frame).
# ---------------------------------------------------------------------------

def _row(key: str, metrics: dict) -> dict:
    return {"key": key, "path": [], "metrics": metrics}


def _gauge_cell(p95: float, **overrides) -> dict:
    cell = {"semantic": "gauge", "sample_count": 5, "count": 5, "min": p95, "mean": p95, "p50": p95, "p95": p95, "max": p95}
    cell.update(overrides)
    return cell


def _rate_cell(p95: float, resets: int = 0, **overrides) -> dict:
    cell = {
        "semantic": "rate", "sample_count": 5, "min": p95, "mean": p95, "p50": p95, "p95": p95,
        "max": p95, "resets": resets,
    }
    cell.update(overrides)
    return cell


def _summary_result(rows: list[dict], *, complete: bool = True, gap_count: int = 0,
                     projection: str = "flat", visibility: str = "all", shape: str = "summary") -> dict:
    return {
        "meta": {
            "shape": shape,
            "projection": projection,
            "visibility": visibility,
            "requested_window": None,
            "observed_start_ts": 100.0,
            "observed_end_ts": 200.0,
            "sample_count": 5,
            "coverage": {"frames": 5, "span_s": 100.0, "gap_count": gap_count, "complete": complete},
            "gaps": [],
            "eviction": {"occurred": False},
            "resets": {"count": 0},
            "freshness": {"newest_ts": 200.0, "oldest_ts": 100.0},
            "source": {"kind": "recording"},
            "truncation": {"truncated": False, "policy": "error"},
        },
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# O1 — positive/negative absolute and percentage deltas
# ---------------------------------------------------------------------------

class TestDeltaMath:
    def test_positive_delta_and_percentage(self):
        current = _summary_result([_row("a.scope", {"ram": _gauge_cell(9.0)})])
        baseline = _summary_result([_row("a.scope", {"ram": _gauge_cell(6.0)})])
        [d] = compare_summaries(current, baseline)
        assert d.outcome == OUTCOME_OK
        assert d.current == 9.0 and d.baseline == 6.0
        assert d.delta == 3.0
        assert d.pct == 50.0

    def test_negative_delta_and_percentage(self):
        current = _summary_result([_row("a.scope", {"ram": _gauge_cell(4.0)})])
        baseline = _summary_result([_row("a.scope", {"ram": _gauge_cell(8.0)})])
        [d] = compare_summaries(current, baseline)
        assert d.outcome == OUTCOME_OK
        assert d.delta == -4.0
        assert d.pct == -50.0

    def test_counter_delta_semantic_uses_total_stat(self):
        cur_cell = {"semantic": "counter_delta", "total": 150.0, "intervals": 3, "resets": 0}
        base_cell = {"semantic": "counter_delta", "total": 100.0, "intervals": 3, "resets": 0}
        current = _summary_result([_row("a.scope", {"net_tx_bytes": cur_cell})])
        baseline = _summary_result([_row("a.scope", {"net_tx_bytes": base_cell})])
        [d] = compare_summaries(current, baseline)
        assert d.outcome == OUTCOME_OK
        assert d.delta == 50.0
        assert d.pct == 50.0


# ---------------------------------------------------------------------------
# O2 / O3 — zero baseline typed outcomes (never a division).
# ---------------------------------------------------------------------------

class TestZeroBaseline:
    def test_zero_zero_is_typed_not_divided(self):
        current = _summary_result([_row("a.scope", {"ram": _gauge_cell(0.0)})])
        baseline = _summary_result([_row("a.scope", {"ram": _gauge_cell(0.0)})])
        [d] = compare_summaries(current, baseline)
        assert d.outcome == OUTCOME_ZERO_ZERO
        assert d.delta == 0.0
        assert d.pct is None
        assert d.reason is not None

    def test_zero_baseline_nonzero_current_is_typed_not_infinite(self):
        current = _summary_result([_row("a.scope", {"ram": _gauge_cell(5.0)})])
        baseline = _summary_result([_row("a.scope", {"ram": _gauge_cell(0.0)})])
        [d] = compare_summaries(current, baseline)
        assert d.outcome == OUTCOME_ZERO_BASELINE
        assert d.delta == 5.0
        assert d.pct is None
        assert d.reason is not None


# ---------------------------------------------------------------------------
# O4 — mismatched semantics/units -> explicit typed refusal.
# ---------------------------------------------------------------------------

class TestSemanticMismatch:
    def test_mismatched_semantic_is_refused(self):
        current = _summary_result([_row("a.scope", {"ram": _gauge_cell(9.0)})])
        baseline = _summary_result([_row("a.scope", {"ram": _rate_cell(6.0)})])
        [d] = compare_summaries(current, baseline)
        assert d.outcome == OUTCOME_SEMANTIC_MISMATCH
        assert d.delta is None and d.pct is None

    def test_mismatched_projection_is_a_usage_error(self):
        current = _summary_result([_row("a.scope", {"ram": _gauge_cell(9.0)})], projection="flat")
        baseline = _summary_result([_row("a.scope", {"ram": _gauge_cell(6.0)})], projection="hierarchy")
        with pytest.raises(CompareError):
            compare_summaries(current, baseline)

    def test_mismatched_visibility_is_a_usage_error(self):
        current = _summary_result([_row("a.scope", {"ram": _gauge_cell(9.0)})], visibility="all")
        baseline = _summary_result([_row("a.scope", {"ram": _gauge_cell(6.0)})], visibility="available")
        with pytest.raises(CompareError):
            compare_summaries(current, baseline)

    def test_non_summary_shape_is_a_usage_error(self):
        current = _summary_result([_row("a.scope", {"ram": _gauge_cell(9.0)})], shape="current")
        baseline = _summary_result([_row("a.scope", {"ram": _gauge_cell(6.0)})])
        with pytest.raises(CompareError):
            compare_summaries(current, baseline)


# ---------------------------------------------------------------------------
# O5 — missing/redacted values -> explicit typed outcome, never silently pass.
# ---------------------------------------------------------------------------

class TestMissingAndRedacted:
    def test_metric_absent_from_current(self):
        current = _summary_result([_row("a.scope", {})])
        baseline = _summary_result([_row("a.scope", {"ram": _gauge_cell(6.0)})])
        [d] = compare_summaries(current, baseline)
        assert d.outcome == OUTCOME_MISSING_CURRENT
        assert d.delta is None and d.pct is None

    def test_metric_absent_from_baseline(self):
        current = _summary_result([_row("a.scope", {"ram": _gauge_cell(6.0)})])
        baseline = _summary_result([_row("a.scope", {})])
        [d] = compare_summaries(current, baseline)
        assert d.outcome == OUTCOME_MISSING_BASELINE

    def test_metric_absent_from_both(self):
        current = _summary_result([_row("a.scope", {})])
        baseline = _summary_result([_row("a.scope", {})], )
        deltas = compare_summaries(current, baseline, metrics=("ram",))
        [d] = deltas
        assert d.outcome == OUTCOME_MISSING

    def test_null_stat_zero_sample_window_is_missing(self):
        cur_cell = {"semantic": "gauge", "sample_count": 0, "count": 0, "min": None, "mean": None, "p50": None, "p95": None, "max": None}
        current = _summary_result([_row("a.scope", {"ram": cur_cell})])
        baseline = _summary_result([_row("a.scope", {"ram": _gauge_cell(6.0)})])
        [d] = compare_summaries(current, baseline)
        assert d.outcome == OUTCOME_MISSING_CURRENT

    def test_redacted_current_is_typed_not_a_silent_pass(self):
        current = _summary_result([_row("a.scope", {"ram": {"redacted": True, "sensitivity": "sensitive"}})])
        baseline = _summary_result([_row("a.scope", {"ram": _gauge_cell(6.0)})])
        [d] = compare_summaries(current, baseline)
        assert d.outcome == OUTCOME_REDACTED
        assert d.delta is None and d.pct is None

    def test_redacted_baseline_is_typed(self):
        current = _summary_result([_row("a.scope", {"ram": _gauge_cell(6.0)})])
        baseline = _summary_result([_row("a.scope", {"ram": {"redacted": True, "sensitivity": "operational"}})])
        [d] = compare_summaries(current, baseline)
        assert d.outcome == OUTCOME_REDACTED

    def test_reset_boundary_is_typed(self):
        current = _summary_result([_row("a.scope", {"io_r_bps": _rate_cell(9.0, resets=1)})])
        baseline = _summary_result([_row("a.scope", {"io_r_bps": _rate_cell(6.0, resets=0)})])
        [d] = compare_summaries(current, baseline)
        assert d.outcome == OUTCOME_RESET_BOUNDARY
        assert d.delta is None and d.pct is None


# ---------------------------------------------------------------------------
# O6 — unequal coverage -> explicit typed outcome, never an ignored delta.
# ---------------------------------------------------------------------------

class TestCoverage:
    def test_incomplete_baseline_coverage_is_typed(self):
        current = _summary_result([_row("a.scope", {"ram": _gauge_cell(9.0)})], complete=True)
        baseline = _summary_result([_row("a.scope", {"ram": _gauge_cell(6.0)})], complete=False, gap_count=1)
        [d] = compare_summaries(current, baseline)
        assert d.outcome == OUTCOME_INCOMPLETE_COVERAGE
        assert d.delta is None and d.pct is None

    def test_incomplete_current_coverage_is_typed(self):
        current = _summary_result([_row("a.scope", {"ram": _gauge_cell(9.0)})], complete=False, gap_count=2)
        baseline = _summary_result([_row("a.scope", {"ram": _gauge_cell(6.0)})], complete=True)
        [d] = compare_summaries(current, baseline)
        assert d.outcome == OUTCOME_INCOMPLETE_COVERAGE

    def test_both_complete_computes_a_normal_delta(self):
        current = _summary_result([_row("a.scope", {"ram": _gauge_cell(9.0)})], complete=True)
        baseline = _summary_result([_row("a.scope", {"ram": _gauge_cell(6.0)})], complete=True)
        [d] = compare_summaries(current, baseline)
        assert d.outcome == OUTCOME_OK


# ---------------------------------------------------------------------------
# O7 — P61/P64 exit codes combine deterministically.
# ---------------------------------------------------------------------------

class TestExitCodeCombination:
    @pytest.mark.parametrize(
        "codes,expected",
        [
            ((0, 0), 0),
            ((0, 1), 1),
            ((1, 0), 1),
            ((0, 2), 2),
            ((2, 0), 2),
            ((1, 2), 2),
            ((2, 1), 2),
            ((1, 1), 1),
            ((2, 2), 2),
            ((), 0),
        ],
    )
    def test_combine_is_order_independent_and_never_loses_a_gate(self, codes, expected):
        assert combine_exit_codes(*codes) == expected
        assert combine_exit_codes(*reversed(codes)) == expected

    def test_evaluate_rules_never_silently_passes_a_refused_comparison(self):
        current = _summary_result([_row("a.scope", {"ram": _gauge_cell(9.0)})])
        baseline = _summary_result([_row("a.scope", {"ram": {"redacted": True, "sensitivity": "sensitive"}})])
        deltas = compare_summaries(current, baseline)
        rule = parse_compare_rule("a.scope:ram:delta<=1000")
        results = evaluate_compare_rules(deltas, [rule])
        assert results[0].passed is False
        assert compare_exit_code(results) == 1

    def test_evaluate_rules_pass_and_breach(self):
        current = _summary_result([_row("a.scope", {"ram": _gauge_cell(9.0)})])
        baseline = _summary_result([_row("a.scope", {"ram": _gauge_cell(6.0)})])
        deltas = compare_summaries(current, baseline)
        passing = evaluate_compare_rules(deltas, [parse_compare_rule("a.scope:ram:pct<=100")])
        assert compare_exit_code(passing) == 0
        breaching = evaluate_compare_rules(deltas, [parse_compare_rule("a.scope:ram:pct<=10")])
        assert compare_exit_code(breaching) == 1

    def test_unknown_key_metric_rule_is_a_breach(self):
        current = _summary_result([_row("a.scope", {"ram": _gauge_cell(9.0)})])
        baseline = _summary_result([_row("a.scope", {"ram": _gauge_cell(6.0)})])
        deltas = compare_summaries(current, baseline)
        results = evaluate_compare_rules(deltas, [parse_compare_rule("missing.scope:ram:delta<=1")])
        assert results[0].passed is False
        assert compare_exit_code(results) == 1

    def test_malformed_rule_raises_compare_error(self):
        with pytest.raises(CompareError):
            parse_compare_rule("not-a-valid-spec")

    def test_full_p61_p64_combination_table(self):
        """Simulate composing a P61 report-assertion exit code with a P64
        compare-assertion exit code — the combined code must reflect the
        worst of the two, in either order (O7)."""
        current = _summary_result([_row("a.scope", {"ram": _gauge_cell(9.0)})])
        baseline = _summary_result([_row("a.scope", {"ram": _gauge_cell(6.0)})])
        deltas = compare_summaries(current, baseline)
        p64_pass = compare_exit_code(evaluate_compare_rules(deltas, [parse_compare_rule("a.scope:ram:pct<=100")]))
        p64_breach = compare_exit_code(evaluate_compare_rules(deltas, [parse_compare_rule("a.scope:ram:pct<=1")]))
        assert p64_pass == 0
        assert p64_breach == 1
        p61_pass, p61_breach, p61_usage_error = 0, 1, 2
        assert combine_exit_codes(p61_pass, p64_pass) == 0
        assert combine_exit_codes(p61_pass, p64_breach) == 1
        assert combine_exit_codes(p61_breach, p64_pass) == 1
        assert combine_exit_codes(p61_usage_error, p64_breach) == 2
        assert combine_exit_codes(p64_breach, p61_usage_error) == 2


# ---------------------------------------------------------------------------
# O8 — deterministic output ordering across repeated runs.
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_two_runs_byte_identical(self):
        current = _summary_result([
            _row("z.scope", {"ram": _gauge_cell(9.0), "anon": _gauge_cell(3.0)}),
            _row("a.scope", {"ram": _gauge_cell(4.0)}),
        ])
        baseline = _summary_result([
            _row("z.scope", {"ram": _gauge_cell(6.0), "anon": _gauge_cell(2.0)}),
            _row("a.scope", {"ram": _gauge_cell(8.0)}),
        ])
        a = format_compare(compare_summaries(current, baseline))
        b = format_compare(compare_summaries(current, baseline))
        assert a == b

    def test_output_is_sorted_by_key_then_metric(self):
        current = _summary_result([
            _row("z.scope", {"ram": _gauge_cell(9.0), "anon": _gauge_cell(3.0)}),
            _row("a.scope", {"ram": _gauge_cell(4.0)}),
        ])
        baseline = _summary_result([
            _row("z.scope", {"ram": _gauge_cell(6.0), "anon": _gauge_cell(2.0)}),
            _row("a.scope", {"ram": _gauge_cell(8.0)}),
        ])
        deltas = compare_summaries(current, baseline)
        pairs = [(d.key, d.metric) for d in deltas]
        assert pairs == sorted(pairs)

    def test_cli_stdout_byte_identical(self, tmp_path):
        current = _summary_result([_row("a.scope", {"ram": _gauge_cell(9.0)})])
        baseline = _summary_result([_row("a.scope", {"ram": _gauge_cell(6.0)})])
        cpath = tmp_path / "current.json"
        bpath = tmp_path / "baseline.json"
        cpath.write_text(json.dumps(current))
        bpath.write_text(json.dumps(baseline))
        cmd = [sys.executable, "-m", "groop.cli", "compare", str(cpath), str(bpath), "--json"]
        env = _merged_env({"PYTHONPATH": str(SRC_ROOT)})
        r1 = subprocess.run(cmd, capture_output=True, text=True, cwd=str(SRC_ROOT), env=env)
        r2 = subprocess.run(cmd, capture_output=True, text=True, cwd=str(SRC_ROOT), env=env)
        assert r1.returncode == 0, r1.stderr
        assert r1.stdout == r2.stdout
        data = json.loads(r1.stdout)
        assert data["deltas"][0]["outcome"] == OUTCOME_OK


# ---------------------------------------------------------------------------
# O9 — the helper never reads or re-profiles frames.
# ---------------------------------------------------------------------------

class TestNeverReadsFrames:
    def test_compare_module_has_no_frame_or_recording_dependency(self):
        """A mutation that makes the helper read/re-profile frames must turn
        this test red: it asserts the module source never names the frame or
        recording-reading primitives, and that it works from plain dicts with
        no Frame/RecordReader object ever constructed."""
        import groop.compare as compare_module

        src = inspect.getsource(compare_module)
        for forbidden in ("RecordReader", "RecordingFrameSource", "collect_points", "compute_profile", "iter_source_frames"):
            assert forbidden not in src, f"compare.py must not reference {forbidden}"
        assert "groop.model" not in src
        assert "groop.record" not in src

    def test_compare_works_from_plain_dicts_only(self):
        # No Frame, no recording, no query engine involved anywhere here —
        # only the JSON shape a summary query result produces.
        current = {"meta": {"shape": "summary", "projection": "flat", "visibility": "all",
                             "coverage": {"complete": True}}, "rows": [_row("a.scope", {"ram": _gauge_cell(9.0)})]}
        baseline = {"meta": {"shape": "summary", "projection": "flat", "visibility": "all",
                              "coverage": {"complete": True}}, "rows": [_row("a.scope", {"ram": _gauge_cell(6.0)})]}
        [d] = compare_summaries(current, baseline)
        assert d.outcome == OUTCOME_OK
        assert d.delta == 3.0


# ---------------------------------------------------------------------------
# CLI usage-error / exit-code behaviour.
# ---------------------------------------------------------------------------

class TestCompareCLI:
    def _run(self, *args: str, cwd=None):
        cmd = [sys.executable, "-m", "groop.cli", "compare", *args]
        env = _merged_env({"PYTHONPATH": str(SRC_ROOT)})
        return subprocess.run(cmd, capture_output=True, text=True, cwd=str(SRC_ROOT), env=env)

    def test_missing_files_exit_2(self):
        result = self._run("nonexistent-current.json", "nonexistent-baseline.json", "--json")
        assert result.returncode == 2
        assert "not found" in result.stderr

    def test_no_json_flag_exits_2(self, tmp_path):
        cpath = tmp_path / "current.json"
        bpath = tmp_path / "baseline.json"
        cpath.write_text("{}")
        bpath.write_text("{}")
        result = self._run(str(cpath), str(bpath))
        assert result.returncode == 2

    def test_malformed_json_exits_2(self, tmp_path):
        cpath = tmp_path / "current.json"
        bpath = tmp_path / "baseline.json"
        cpath.write_text("not json")
        bpath.write_text("{}")
        result = self._run(str(cpath), str(bpath), "--json")
        assert result.returncode == 2
        assert "not valid JSON" in result.stderr

    def test_incompatible_shape_exits_2(self, tmp_path):
        current = _summary_result([_row("a.scope", {"ram": _gauge_cell(9.0)})], shape="current")
        baseline = _summary_result([_row("a.scope", {"ram": _gauge_cell(6.0)})])
        cpath = tmp_path / "current.json"
        bpath = tmp_path / "baseline.json"
        cpath.write_text(json.dumps(current))
        bpath.write_text(json.dumps(baseline))
        result = self._run(str(cpath), str(bpath), "--json")
        assert result.returncode == 2

    def test_malformed_assert_spec_exits_2(self, tmp_path):
        current = _summary_result([_row("a.scope", {"ram": _gauge_cell(9.0)})])
        baseline = _summary_result([_row("a.scope", {"ram": _gauge_cell(6.0)})])
        cpath = tmp_path / "current.json"
        bpath = tmp_path / "baseline.json"
        cpath.write_text(json.dumps(current))
        bpath.write_text(json.dumps(baseline))
        result = self._run(str(cpath), str(bpath), "--json", "--assert", "bogus")
        assert result.returncode == 2

    def test_passing_assertion_exits_0(self, tmp_path):
        current = _summary_result([_row("a.scope", {"ram": _gauge_cell(9.0)})])
        baseline = _summary_result([_row("a.scope", {"ram": _gauge_cell(6.0)})])
        cpath = tmp_path / "current.json"
        bpath = tmp_path / "baseline.json"
        cpath.write_text(json.dumps(current))
        bpath.write_text(json.dumps(baseline))
        result = self._run(str(cpath), str(bpath), "--json", "--assert", "a.scope:ram:pct<=100")
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["assertions"][0]["passed"] is True

    def test_breaching_assertion_exits_1(self, tmp_path):
        current = _summary_result([_row("a.scope", {"ram": _gauge_cell(9.0)})])
        baseline = _summary_result([_row("a.scope", {"ram": _gauge_cell(6.0)})])
        cpath = tmp_path / "current.json"
        bpath = tmp_path / "baseline.json"
        cpath.write_text(json.dumps(current))
        bpath.write_text(json.dumps(baseline))
        result = self._run(str(cpath), str(bpath), "--json", "--assert", "a.scope:ram:pct<=10")
        assert result.returncode == 1, result.stderr
        data = json.loads(result.stdout)
        assert data["assertions"][0]["passed"] is False

    def test_no_assertions_given_exits_0(self, tmp_path):
        current = _summary_result([_row("a.scope", {"ram": _gauge_cell(9.0)})])
        baseline = _summary_result([_row("a.scope", {"ram": {"redacted": True, "sensitivity": "sensitive"}})])
        cpath = tmp_path / "current.json"
        bpath = tmp_path / "baseline.json"
        cpath.write_text(json.dumps(current))
        bpath.write_text(json.dumps(baseline))
        # No --assert given: this is purely informational (D-007), so a
        # refused/redacted comparison alone must NOT fail the process.
        result = self._run(str(cpath), str(bpath), "--json")
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["deltas"][0]["outcome"] == OUTCOME_REDACTED


# ---------------------------------------------------------------------------
# Integration against a genuine P88 query engine Result.
# ---------------------------------------------------------------------------

class TestAgainstRealQueryEngine:
    def test_compares_genuine_p88_summary_results(self):
        from groop.model import Entity, EntityFrame, Frame, MetricValue
        from groop.query import DaemonHistoryFrameSource, MetricRef, Query, run_query

        def frame(ts, value):
            return Frame(
                schema_version=1, ts=ts, interval_s=5.0, host={},
                entities={"a.scope": EntityFrame(
                    entity=Entity(key="a.scope", kind="scope", parent=None),
                    metrics={"ram": MetricValue(v=value, src="exact")},
                )},
            )

        current_frames = [frame(100.0 + i * 5, v) for i, v in enumerate([8.0, 9.0, 10.0])]
        baseline_frames = [frame(100.0 + i * 5, v) for i, v in enumerate([5.0, 6.0, 7.0])]
        q = Query(shape="summary", metrics=(MetricRef("ram"),))

        current_result = run_query(
            DaemonHistoryFrameSource(tuple(enumerate(current_frames))), q
        ).to_jsonable()
        baseline_result = run_query(
            DaemonHistoryFrameSource(tuple(enumerate(baseline_frames))), q
        ).to_jsonable()

        [d] = compare_summaries(current_result, baseline_result)
        assert d.outcome == OUTCOME_OK
        assert d.current == current_result["rows"][0]["metrics"]["ram"]["p95"]
        assert d.baseline == baseline_result["rows"][0]["metrics"]["ram"]["p95"]
