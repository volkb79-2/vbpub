"""Unit suite for tools/coverage_gate.py — the vendored diff-coverage gate
that backs the selftest lane's floor. Minimal by design (this file is
MIGRATION PENDING per its own header, do not grow it into a fork of the
donor topos/tools/coverage_gate.py suite): just enough to pin the pure
core (parse_added_lines/evaluate) and the one regression that already
bit run-gate-project once — an unscoped --source default silently
reproducing the exact false-FAIL/false-PASS the floor exists to prevent.
"""

import importlib.util
import json
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


def test_evaluate_branch_partial_line_counts_as_uncovered():
    """RG-53: a changed line that RAN but left an arm untaken (present in
    `missing_branches`, absent from `missing_lines`) must count as
    uncovered — the whole point of `--cov-branch` for the diff judge."""
    added = {"run-gate-project/run-gate.py": {5}}
    coverage_files = {
        "run-gate-project/run-gate.py": {
            "executed_lines": [5],
            "missing_lines": [],
            "executed_branches": [[5, 6]],
            "missing_branches": [[5, 99]],
        },
    }
    v = coverage_gate.evaluate(added, coverage_files,
                               source_prefix="run-gate-project/run-gate.py")
    assert not v.passed
    assert v.uncovered == {"run-gate-project/run-gate.py": {5}}
    assert v.branch_partial_lines == {"run-gate-project/run-gate.py": {5}}
    assert v.covered == 0 and v.changed_executable == 1
    assert v.branches_total == 2 and v.branches_missed == 1


def test_evaluate_branch_fully_taken_line_stays_covered():
    """A changed, executed line whose every branch arm was taken stays
    covered — no `missing_branches` entry for it."""
    added = {"run-gate-project/run-gate.py": {5}}
    coverage_files = {
        "run-gate-project/run-gate.py": {
            "executed_lines": [5],
            "missing_lines": [],
            "executed_branches": [[5, 6], [5, 7]],
            "missing_branches": [],
        },
    }
    v = coverage_gate.evaluate(added, coverage_files,
                               source_prefix="run-gate-project/run-gate.py")
    assert v.passed
    assert v.uncovered == {}
    assert v.branch_partial_lines == {}
    assert v.covered == 1 and v.changed_executable == 1
    assert v.branches_total == 2 and v.branches_missed == 0


def test_evaluate_no_branch_data_is_a_noop_line_only_behavior():
    """A coverage record with no branch-arc keys at all (branch coverage was
    not collected) behaves exactly like the pre-RG-53 line-only gate."""
    added = {"run-gate-project/run-gate.py": {5}}
    coverage_files = {
        "run-gate-project/run-gate.py": {"executed_lines": [5], "missing_lines": []},
    }
    v = coverage_gate.evaluate(added, coverage_files,
                               source_prefix="run-gate-project/run-gate.py")
    assert v.passed
    assert v.branches_total == 0 and v.branches_missed == 0
    assert v.branch_partial_lines == {}


def test_evaluate_branches_total_missed_scoped_to_changed_lines_only():
    """A branch on a line the diff never touched must not pollute the
    branch totals — they are reported "beside" the changed-line counts,
    never a second, whole-file denominator."""
    added = {"run-gate-project/run-gate.py": {5}}
    coverage_files = {
        "run-gate-project/run-gate.py": {
            "executed_lines": [5, 10],
            "missing_lines": [],
            "executed_branches": [[5, 6]],
            # Line 10 is fully executed+covered but NOT in the diff -- its
            # branch data must not appear in the totals.
            "missing_branches": [[10, 11]],
        },
    }
    v = coverage_gate.evaluate(added, coverage_files,
                               source_prefix="run-gate-project/run-gate.py")
    assert v.passed
    assert v.branches_total == 1 and v.branches_missed == 0


def test_validate_cov_record_rejects_malformed_branch_arc():
    added = {"run-gate-project/run-gate.py": {5}}
    coverage_files = {
        "run-gate-project/run-gate.py": {
            "executed_lines": [5],
            "missing_lines": [],
            "missing_branches": [[5]],  # missing the target line
        },
    }
    import pytest
    with pytest.raises(coverage_gate.CoverageGateError):
        coverage_gate.evaluate(added, coverage_files,
                               source_prefix="run-gate-project/run-gate.py")


def test_arg_parser_allow_empty_diff_defaults_false():
    args = coverage_gate._build_arg_parser().parse_args(
        ["--coverage-json", "x.json"])
    assert args.allow_empty_diff is False
    args2 = coverage_gate._build_arg_parser().parse_args(
        ["--coverage-json", "x.json", "--allow-empty-diff"])
    assert args2.allow_empty_diff is True


def test_check_nonempty_diff_none_when_lines_changed():
    assert coverage_gate._check_nonempty_diff(
        "base", "head", "run-gate.py", changed_executable=3,
        allow_empty_diff=False) is None


def test_check_nonempty_diff_none_when_allowed():
    assert coverage_gate._check_nonempty_diff(
        "base", "head", "run-gate.py", changed_executable=0,
        allow_empty_diff=True) is None


def test_check_nonempty_diff_refuses_zero_by_default():
    msg = coverage_gate._check_nonempty_diff(
        "deadbeef", "cafef00d", "run-gate.py", changed_executable=0,
        allow_empty_diff=False)
    assert msg is not None
    assert "deadbeef" in msg and "cafef00d" in msg
    assert "--allow-empty-diff" in msg
    # Names the three known routes, so a reader does not have to guess.
    assert "RG-54" in msg and "RG-51" in msg


def test_main_refuses_zero_changed_diff_without_flag(tmp_path, capsys):
    """End-to-end: a repo where base == HEAD (merge-base(base, HEAD) == HEAD,
    the RG-51/RG-54 degenerate-base shape) must exit 2, not print a `0/0
    100%` pass, unless --allow-empty-diff is given."""
    import subprocess

    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args],
                        check=True, capture_output=True, text=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    (tmp_path / "run-gate.py").write_text("a = 1\n", encoding="utf-8")
    git("add", "run-gate.py")
    git("commit", "-q", "-m", "base")

    cov_json = tmp_path / "coverage.json"
    cov_json.write_text('{"files": {}}', encoding="utf-8")

    rc = coverage_gate.main([
        "--repo", str(tmp_path),
        "--base", "main",
        "--coverage-json", str(cov_json),
        "--source", "run-gate.py",
    ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "0 changed executable lines" in err
    assert "--allow-empty-diff" in err

    rc_allowed = coverage_gate.main([
        "--repo", str(tmp_path),
        "--base", "main",
        "--coverage-json", str(cov_json),
        "--source", "run-gate.py",
        "--allow-empty-diff",
    ])
    assert rc_allowed == 0
    out = capsys.readouterr().out
    assert "diff-coverage OK" in out


def test_main_reports_branch_note_on_pass_and_fail(tmp_path, capsys):
    """The CLI's OK/FAIL lines carry the branch tally beside the line tally
    once branch data is present."""
    import subprocess

    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args],
                        check=True, capture_output=True, text=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    (tmp_path / "run-gate.py").write_text("a = 1\n", encoding="utf-8")
    git("add", "run-gate.py")
    git("commit", "-q", "-m", "base")
    (tmp_path / "run-gate.py").write_text("a = 1\nb = 2\n", encoding="utf-8")

    cov_json = tmp_path / "coverage.json"
    cov_json.write_text(json.dumps({
        "files": {
            "run-gate.py": {
                "executed_lines": [2],
                "missing_lines": [],
                "executed_branches": [[2, 3]],
                "missing_branches": [],
            }
        }
    }), encoding="utf-8")

    rc = coverage_gate.main([
        "--repo", str(tmp_path),
        "--base", "main",
        "--coverage-json", str(cov_json),
        "--source", "run-gate.py",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "branches 1/1 taken" in out


def test_git_added_lines_reports_the_working_trees_own_line_numbers_dirty(tmp_path):
    """RG-40: coverage.json is generated from whatever bytes are ON DISK
    (--allow-dirty runs the suite there directly), so the added-line numbers
    this gate cross-references against it must come from the SAME bytes —
    not from committed HEAD, which a dirty tree has already outgrown.
    Reproduces the exact mechanism: a committed change (`d`, line 4 at
    HEAD) is pushed to a DIFFERENT line (6) by two uncommitted lines
    prepended above it. The old `base_rev HEAD` diff would report line 4 —
    coverage.json, keyed to the dirty tree, actually has `c` there, not
    `d` — silently checking the wrong line's coverage status."""
    import subprocess

    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args],
                        check=True, capture_output=True, text=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    (tmp_path / "demo.py").write_text("a\nb\nc\n", encoding="utf-8")
    git("add", "demo.py")
    git("commit", "-q", "-m", "base")
    base_rev = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True).stdout.strip()

    # A committed change: `d` lands at line 4 relative to base.
    (tmp_path / "demo.py").write_text("a\nb\nc\nd\n", encoding="utf-8")
    git("add", "demo.py")
    git("commit", "-q", "-m", "add d")

    # An UNCOMMITTED edit on top: two lines prepended push `d` to line 6.
    (tmp_path / "demo.py").write_text("x\ny\na\nb\nc\nd\n", encoding="utf-8")

    added = coverage_gate._git_added_lines(str(tmp_path), base_rev, "demo.py")
    # `d`'s REAL on-disk position is 6, not the committed diff's 4 -- and the
    # two prepended uncommitted lines are correctly reported too, at 1 and 2,
    # since they are equally new relative to base_rev.
    assert added == {"demo.py": {1, 2, 6}}, added
