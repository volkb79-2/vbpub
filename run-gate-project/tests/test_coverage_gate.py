"""Unit suite for tools/coverage_gate.py — the vendored diff-coverage gate
that backs the selftest lane's floor. Minimal by design (this file is
MIGRATION PENDING per its own header, do not grow it into a fork of the
donor topos/tools/coverage_gate.py suite): just enough to pin the pure
core (parse_added_lines/evaluate) and the one regression that already
bit run-gate-project once — an unscoped --source default silently
reproducing the exact false-FAIL/false-PASS the floor exists to prevent.
"""

import importlib.util
import sys
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent.parent / "tools"
_spec = importlib.util.spec_from_file_location(
    "coverage_gate", TOOL_DIR / "coverage_gate.py")
coverage_gate = importlib.util.module_from_spec(_spec)
sys.modules["coverage_gate"] = coverage_gate
_spec.loader.exec_module(coverage_gate)


def test_arg_parser_default_source_is_run_gate_py():
    """Regression pin: an unscoped default (the whole project directory)
    silently reproduces the exact bug fixed in run-gate.toml — sweeping in
    the test suite and leaving this tool itself [file unmeasured]."""
    args = coverage_gate._build_arg_parser().parse_args(
        ["--coverage-json", "x.json"])
    assert args.source == "run-gate-project/run-gate.py"


def test_parse_added_lines_basic():
    diff = (
        "diff --git a/run-gate.py b/run-gate.py\n"
        "--- a/run-gate.py\n"
        "+++ b/run-gate.py\n"
        "@@ -10,0 +11,2 @@\n"
        "+new line one\n"
        "+new line two\n"
    )
    added = coverage_gate.parse_added_lines(diff)
    assert added == {"run-gate.py": {11, 12}}


def test_parse_added_lines_ignores_deleted_files():
    diff = (
        "diff --git a/gone.py b/gone.py\n"
        "--- a/gone.py\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-old line one\n"
        "-old line two\n"
    )
    assert coverage_gate.parse_added_lines(diff) == {}


def test_evaluate_covered_change_passes():
    added = {"run-gate-project/run-gate.py": {5}}
    coverage_files = {
        "run-gate-project/run-gate.py": {"executed_lines": [5], "missing_lines": []},
    }
    v = coverage_gate.evaluate(added, coverage_files,
                               source_prefix="run-gate-project/run-gate.py")
    assert v.passed
    assert v.covered == 1 and v.changed_executable == 1
    assert v.uncovered == {}


def test_evaluate_uncovered_change_fails():
    added = {"run-gate-project/run-gate.py": {5}}
    coverage_files = {
        "run-gate-project/run-gate.py": {"executed_lines": [], "missing_lines": [5]},
    }
    v = coverage_gate.evaluate(added, coverage_files,
                               source_prefix="run-gate-project/run-gate.py")
    assert not v.passed
    assert v.uncovered == {"run-gate-project/run-gate.py": {5}}


def test_evaluate_ignores_files_outside_source_prefix():
    added = {"run-gate-project/tests/test_run_gate.py": {5}}
    coverage_files = {
        "run-gate-project/tests/test_run_gate.py":
            {"executed_lines": [], "missing_lines": [5]},
    }
    v = coverage_gate.evaluate(added, coverage_files,
                               source_prefix="run-gate-project/run-gate.py")
    assert v.passed  # nothing in scope changed
    assert v.changed_executable == 0


def test_rel_to_source_directory_boundary():
    """Docstring's own guarantee: a prefix match must land on a directory
    boundary, not a substring — 'run-gate-project' must not match
    'run-gate-project-other/mod.py'."""
    got = coverage_gate._rel_to_source(
        "run-gate-project-other/mod.py", "run-gate-project")
    assert got == "run-gate-project-other/mod.py"  # unmatched, returned as-is


def test_malformed_coverage_record_raises():
    added = {"run-gate-project/run-gate.py": {5}}
    coverage_files = {
        "run-gate-project/run-gate.py": {"executed_lines": "not-a-list", "missing_lines": []},
    }
    import pytest
    with pytest.raises(coverage_gate.CoverageGateError):
        coverage_gate.evaluate(added, coverage_files,
                               source_prefix="run-gate-project/run-gate.py")
