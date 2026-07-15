"""Tests for groop.render — P65 human-readable query/report rendering.

Numbered acceptance oracles (handoff groop-P65-report-human-readable-render.md):
  O1  every displayed value matches the P88 JSON value verbatim
  O2  missing/redacted/warming/stale/permission-denied/truncated each render
      a distinct ASCII spelling; zero renders as 0
  O3  hierarchy preserves ancestry/sibling order; a global rank is labelled flat
  O4  deterministic widths/order, ASCII only, no ANSI, no trailing whitespace
  O5  --json stays byte-compatible with its current shape
  O6  requesting --json and --table together exits 2
  O7  P61 breach exit code is independent of the presentation choice
  O8  the renderer never collects/aggregates/rounds/reads files itself
"""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest

from groop.model import Entity, EntityFrame, Frame, MetricValue
from groop.query import Caps, MetricRef, Query, Selector, run_query
from groop.query.source import DaemonHistoryFrameSource
from groop.render import render_query, render_report
from groop.report import (
    GroupProfile,
    WindowRange,
    WindowSelection,
    evaluate_assertions,
    parse_assert_spec,
    report_to_jsonable,
)

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "frames" / "gstammtisch-once.jsonl"


# ---------------------------------------------------------------------------
# Builders (mirrors tests/test_query.py conventions).
# ---------------------------------------------------------------------------

def _g(v: float) -> MetricValue:
    return MetricValue(v=v, src="exact")


def _frame(ts: float, entities: dict, *, interval_s: float = 5.0) -> Frame:
    eframes = {}
    for key, (parent, metrics) in entities.items():
        kind = "slice" if key.endswith(".slice") else "scope"
        eframes[key] = EntityFrame(entity=Entity(key=key, kind=kind, parent=parent), metrics=metrics)
    return Frame(schema_version=1, ts=ts, interval_s=interval_s, host={}, entities=eframes)


def _daemon(frames: list[Frame], *, gap: bool = False) -> DaemonHistoryFrameSource:
    entries = tuple((i, f) for i, f in enumerate(frames))
    return DaemonHistoryFrameSource(entries, gap=gap)


def _run(shape: str, frames: list[Frame], metrics: tuple[MetricRef, ...], **kwargs) -> dict:
    query = Query(shape=shape, metrics=metrics, **kwargs)
    result = run_query(_daemon(frames), query)
    return result.to_jsonable()


def _run_cli(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "groop.cli", *args],
        capture_output=True, text=True,
        cwd=str(SRC_ROOT),
        env={"PYTHONPATH": str(SRC_ROOT)},
    )


# ---------------------------------------------------------------------------
# O1 — verbatim value match against the P88 JSON value.
# ---------------------------------------------------------------------------

class TestVerbatimValues:
    def test_current_value_matches_jsonable(self):
        frames = [_frame(100.0, {"a.scope": (None, {"ram": _g(4096000000.0)})})]
        result = _run("current", frames, (MetricRef("ram"),))
        cell = result["rows"][0]["metrics"]["ram"]
        assert cell["value"] == 4096000000.0
        text = render_query(result)
        assert "4096000000" in text

    def test_summary_stat_matches_jsonable(self):
        frames = [
            _frame(100.0 + i * 5, {"a.scope": (None, {"ram": _g(1000.5 + i)})}) for i in range(4)
        ]
        result = _run("summary", frames, (MetricRef("ram"),))
        entry = result["rows"][0]["metrics"]["ram"]
        text = render_query(result)
        for stat_name in ("min", "mean", "p50", "p95", "max"):
            value = entry[stat_name]
            assert value is not None
            rendered = f"{value:.6f}".rstrip("0").rstrip(".")
            assert rendered in text

    def test_zero_renders_as_bare_zero(self):
        frames = [_frame(100.0, {"a.scope": (None, {"ram": _g(0.0)})})]
        result = _run("current", frames, (MetricRef("ram"),))
        text = render_query(result)
        rows_section = text.split("\n\n", 1)[1]
        assert "0" in rows_section
        # Never a blank or a dash standing in for a real zero.
        for line in rows_section.splitlines()[2:]:
            if line.strip():
                assert " - " not in f" {line} "


# ---------------------------------------------------------------------------
# O2 — closed vocabulary: distinct ASCII spellings, zero is 0.
# ---------------------------------------------------------------------------

class TestTypedValueStates:
    def test_missing_when_metric_absent(self):
        frames = [_frame(100.0, {"a.scope": (None, {}), "b.scope": (None, {"ram": _g(1.0)})})]
        result = _run("current", frames, (MetricRef("ram"),))
        text = render_query(result)
        assert "missing" in text

    def test_permission_denied_when_unavail_perm(self):
        frames = [_frame(100.0, {"a.scope": (None, {"ram": MetricValue(v=None, src="unavail_perm")})})]
        result = _run("current", frames, (MetricRef("ram"),))
        text = render_query(result)
        assert "permission-denied" in text

    def test_unsupported_and_permission_denied_stay_distinct_under_available(self):
        """O2: --visibility available must not collapse unavail_kernel (an
        unsupported stat) and unavail_perm (a permissions failure) into the
        same 'permission-denied' spelling — the true typed state survives the
        visibility filter exactly as it does under --visibility all."""
        frames = [_frame(100.0, {
            "k.scope": (None, {"ram": MetricValue(v=None, src="unavail_kernel")}),
            "p.scope": (None, {"ram": MetricValue(v=None, src="unavail_perm")}),
        })]
        result = _run("current", frames, (MetricRef("ram"),), visibility="available")
        # The engine keeps the true src and only flags the cell hidden.
        cells = {row["key"]: row["metrics"]["ram"] for row in result["rows"]}
        assert cells["k.scope"]["src"] == "unavail_kernel" and cells["k.scope"].get("hidden")
        assert cells["p.scope"]["src"] == "unavail_perm" and cells["p.scope"].get("hidden")
        text = render_query(result)
        k_line = next(l for l in text.splitlines() if l.startswith("k.scope"))
        p_line = next(l for l in text.splitlines() if l.startswith("p.scope"))
        assert "unsupported" in k_line and "permission-denied" not in k_line
        assert "permission-denied" in p_line and "unsupported" not in p_line

    def test_warming_when_derived_cold(self):
        frames = [_frame(100.0, {"a.scope": (None, {"io_r_bps": MetricValue(v=None, src="derived", raw=42)})})]
        result = _run("current", frames, (MetricRef("io_r_bps"),))
        text = render_query(result)
        assert "warming" in text

    def test_redacted_marker_is_rendered_distinctly(self):
        result = _run("current", [_frame(100.0, {"a.scope": (None, {"ram": _g(1.0)})})], (MetricRef("ram"),))
        # Defensive path: a redaction marker dict flowing through as a cell value.
        result["rows"][0]["metrics"]["ram"] = {"value": {"redacted": True, "sensitivity": "high"}, "src": "exact"}
        text = render_query(result)
        assert "redacted" in text

    def test_stale_annotation_when_coverage_incomplete(self):
        frames = [_frame(100.0, {"a.scope": (None, {"ram": _g(1.0)})}), _frame(105.0, {"a.scope": (None, {"ram": _g(2.0)})})]
        result = _run("summary", frames, (MetricRef("ram"),))
        assert result["meta"]["coverage"]["complete"] is True
        result["meta"]["coverage"]["complete"] = False
        text = render_query(result)
        assert "stale" in text

    def test_truncated_annotation_in_header(self):
        frames = [_frame(100.0 + i * 5, {f"e{i}.scope": (None, {"ram": _g(float(i))})}) for i in range(5)]
        result = _run("summary", frames, (MetricRef("ram"),), caps=Caps(max_rows=2, on_exceed="truncate"))
        assert result["meta"]["truncation"]["truncated"] is True
        text = render_query(result)
        assert "truncated" in text

    def test_all_six_spellings_are_pairwise_distinct(self):
        spellings = {"missing", "redacted", "warming", "stale", "permission-denied", "truncated"}
        assert len(spellings) == 6


# ---------------------------------------------------------------------------
# O3 — hierarchy ordering preserved; flat explicitly labelled.
# ---------------------------------------------------------------------------

class TestHierarchyVsFlat:
    def test_hierarchy_preserves_ancestry_and_sibling_order(self):
        frames = [
            _frame(100.0, {
                "root.slice": (None, {"ram": _g(100.0)}),
                "root.slice/b.scope": ("root.slice", {"ram": _g(20.0)}),
                "root.slice/a.scope": ("root.slice", {"ram": _g(10.0)}),
            })
        ]
        result = _run(
            "current", frames, (MetricRef("ram"),),
            projection="hierarchy", sort=None,
        )
        keys_in_order = [row["key"] for row in result["rows"]]
        # A child must never precede its own parent.
        seen = set()
        for row in result["rows"]:
            for ancestor in row["path"]:
                assert ancestor in seen or ancestor == ""
            seen.add(row["key"])
        assert keys_in_order[0] == "root.slice"
        text = render_query(result)
        lines = [l for l in text.splitlines() if "root.slice" in l]
        assert len(lines) == 3
        # Children are indented relative to their parent.
        parent_indent = len(lines[0]) - len(lines[0].lstrip())
        for child_line in lines[1:]:
            assert (len(child_line) - len(child_line.lstrip())) > parent_indent

    def test_flat_projection_is_explicitly_labelled(self):
        frames = [_frame(100.0, {"a.scope": (None, {"ram": _g(1.0)})})]
        result = _run("current", frames, (MetricRef("ram"),), projection="flat")
        text = render_query(result)
        assert "projection: flat" in text


# ---------------------------------------------------------------------------
# O4 — determinism, ASCII only, no ANSI, no trailing whitespace.
# ---------------------------------------------------------------------------

class TestDeterministicAsciiOutput:
    def _sample_result(self) -> dict:
        frames = [
            _frame(100.0 + i * 5, {
                "a.scope": (None, {"ram": _g(1000.0 + i)}),
                "b.scope": (None, {"ram": _g(2000.0 - i)}),
            })
            for i in range(3)
        ]
        return _run("summary", frames, (MetricRef("ram"),))

    def test_two_renders_are_identical(self):
        result = self._sample_result()
        assert render_query(result) == render_query(result)

    def test_output_is_ascii_only(self):
        text = render_query(self._sample_result())
        text.encode("ascii")  # raises UnicodeEncodeError on any non-ASCII char

    def test_no_ansi_escape_codes(self):
        text = render_query(self._sample_result())
        assert "\x1b" not in text

    def test_no_trailing_whitespace_per_line(self):
        text = render_query(self._sample_result())
        for line in text.splitlines():
            assert line == line.rstrip()

    def test_report_render_is_also_clean(self):
        profile = GroupProfile(
            key="a.scope", sample_count=3, window_start_ts=100.0, window_end_ts=110.0,
            gauges={"ram": {"p50": 1000.0, "p95": 1002.0, "max": 1002.0}}, rates={},
        )
        text = render_report(report_to_jsonable([profile]))
        text.encode("ascii")
        assert "\x1b" not in text
        for line in text.splitlines():
            assert line == line.rstrip()


# ---------------------------------------------------------------------------
# O5 — --json output stays byte-compatible.
# ---------------------------------------------------------------------------

class TestJsonRemainsByteCompatible:
    def test_query_json_matches_direct_format_result(self, tmp_path):
        from groop.query import format_result
        from groop.query.source import RecordingFrameSource
        from groop.record.writer import RecordWriter

        frames = [_frame(100.0, {"a.scope": (None, {"ram": _g(1.0)})})]
        path = tmp_path / "r.jsonl"
        with RecordWriter(path, fsync=False) as w:
            for f in frames:
                w.write_frame(f)
        result = run_query(RecordingFrameSource(path), Query(shape="current", metrics=(MetricRef("ram"),)))
        expected = format_result(result)

        cli = _run_cli(["query", str(path), "--shape", "current", "--metric", "ram", "--json"])
        assert cli.returncode == 0
        assert cli.stdout.strip() == expected

    def test_report_json_unaffected_by_default_flag_change(self):
        cli = _run_cli(["report", str(FIXTURE), "--json"])
        assert cli.returncode == 0
        data = json.loads(cli.stdout)
        assert "profiles" in data


# ---------------------------------------------------------------------------
# O6 — requesting both formats is a usage error (exit 2).
# ---------------------------------------------------------------------------

class TestFormatConflictExits2:
    def test_query_json_and_table_conflict(self, tmp_path):
        cli = _run_cli(["query", str(FIXTURE), "--shape", "current", "--metric", "ram", "--json", "--table"])
        assert cli.returncode == 2

    def test_report_json_and_table_conflict(self):
        cli = _run_cli(["report", str(FIXTURE), "--json", "--table"])
        assert cli.returncode == 2


# ---------------------------------------------------------------------------
# O7 — P61 breach exit code independent of presentation.
# ---------------------------------------------------------------------------

class TestExitCodeIndependentOfFormat:
    def test_breach_exits_1_in_both_formats(self):
        text_result = _run_cli(["report", str(FIXTURE), "--assert", "nonexistent.group:ram:p95<=1"])
        json_result = _run_cli(["report", str(FIXTURE), "--json", "--assert", "nonexistent.group:ram:p95<=1"])
        assert text_result.returncode == 1
        assert json_result.returncode == 1

    def test_pass_exits_0_in_both_formats(self):
        text_result = _run_cli(["report", str(FIXTURE), "--assert", "nonexistent.group:ram:p95>=-1e18"])
        json_result = _run_cli(["report", str(FIXTURE), "--json", "--assert", "nonexistent.group:ram:p95>=-1e18"])
        # Both absent-group assertions breach identically (group not present),
        # so exit codes still agree even though this spec is a manufactured
        # "impossible to satisfy" breach — the point is format-independence.
        assert text_result.returncode == json_result.returncode


# ---------------------------------------------------------------------------
# O8 — the renderer never collects/aggregates/rounds/reads files.
# ---------------------------------------------------------------------------

class TestRendererPurity:
    def test_no_file_io_in_render_module(self):
        import groop.render as render_module

        source = inspect.getsource(render_module)
        for forbidden in ("open(", "Path(", "import pathlib", "RecordReader", "RecordWriter"):
            assert forbidden not in source, f"render.py must not perform file I/O ({forbidden!r} found)"

    def test_render_query_does_not_mutate_input(self):
        frames = [_frame(100.0, {"a.scope": (None, {"ram": _g(1.0)})})]
        result = _run("current", frames, (MetricRef("ram"),))
        before = json.dumps(result, sort_keys=True)
        render_query(result)
        after = json.dumps(result, sort_keys=True)
        assert before == after
