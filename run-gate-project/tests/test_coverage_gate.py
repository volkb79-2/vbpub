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


def test_rel_to_source_rejects_a_leading_substring_match():
    """S8 (round-1 review): the OLD version checked only the boundary
    AFTER a match — an empty tail (the prefix landing at the very end of
    the path) short-circuited that check without ever looking at what
    PRECEDED the match, so 'subject.py' matched inside 'test_subject.py'
    as a bare substring (no directory separator before it). Reproduced
    end to end by the review: with --source subject.py, coverage.py's
    OWN record for tests/test_subject.py collided with subject.py's under
    the old normalization, last-wins, and a genuinely uncovered branch
    reported 100%."""
    got = coverage_gate._rel_to_source("tests/test_subject.py", "subject.py")
    assert got == "tests/test_subject.py"  # unmatched, returned as-is
    # A REAL directory-boundary match for the same prefix still works.
    assert coverage_gate._rel_to_source("src/subject.py", "subject.py") == "subject.py"
    assert coverage_gate._rel_to_source("subject.py", "subject.py") == "subject.py"


def test_evaluate_does_not_collide_source_and_test_file_coverage():
    """End-to-end reproduction of S8's false-green: a changed line in the
    real source file must be judged against the REAL source file's own
    coverage record, never the test file's, even though 'subject.py' is a
    literal substring of 'test_subject.py'."""
    added = {"subject.py": {8}}
    coverage_files = {
        # the test file's own coverage record: line 8 not relevant there,
        # but under the OLD bug it was the record actually consulted.
        "tests/test_subject.py": {"executed_lines": [1, 2, 3], "missing_lines": []},
        # the REAL source record: line 8 is genuinely uncovered.
        "subject.py": {"executed_lines": [], "missing_lines": [8]},
    }
    v = coverage_gate.evaluate(added, coverage_files, source_prefix="subject.py")
    assert not v.passed
    assert v.uncovered == {"subject.py": {8}}


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


def test_arg_parser_refuse_empty_diff_defaults_false():
    args = coverage_gate._build_arg_parser().parse_args(
        ["--coverage-json", "x.json"])
    assert args.refuse_empty_diff is False
    args2 = coverage_gate._build_arg_parser().parse_args(
        ["--coverage-json", "x.json", "--refuse-empty-diff"])
    assert args2.refuse_empty_diff is True


def test_verdict_zero_zero_is_skipped_never_ok():
    """RW-5: a 0/0 diff's Verdict.verdict must read "skipped", never "ok" —
    `pct`/`passed` stay the pure, UNCHANGED 0/0-is-100% classification
    (existing direct callers of `evaluate()` see identical numbers); the
    tri-state `verdict` property is what a caller must check first, so a
    0/0 is never reported or serialized as a plain 100% pass."""
    v = coverage_gate.evaluate({}, {}, source_prefix="run-gate-project/run-gate.py")
    assert v.changed_executable == 0
    assert v.skipped is True
    assert v.pct == 100.0 and v.passed is True
    assert v.verdict == "skipped"


def test_verdict_nonzero_diff_is_ok_or_fail():
    added = {"run-gate-project/run-gate.py": {5}}
    ok = coverage_gate.evaluate(
        added,
        {"run-gate-project/run-gate.py": {"executed_lines": [5], "missing_lines": []}},
        source_prefix="run-gate-project/run-gate.py")
    assert ok.skipped is False
    assert ok.verdict == "ok"
    fail = coverage_gate.evaluate(
        added,
        {"run-gate-project/run-gate.py": {"executed_lines": [], "missing_lines": [5]}},
        source_prefix="run-gate-project/run-gate.py")
    assert fail.skipped is False
    assert fail.verdict == "fail"


def test_base_relation_ancestor_reads_head_is_on_the_base(tmp_path):
    """The exact shape `./run-gate.py selftest` hits on `main` itself:
    merge-base(main, HEAD) == HEAD."""
    import subprocess

    def git(*args):
        return subprocess.run(["git", "-C", str(tmp_path), *args],
                               check=True, capture_output=True, text=True).stdout

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    (tmp_path / "f.py").write_text("a = 1\n", encoding="utf-8")
    git("add", "f.py")
    git("commit", "-q", "-m", "base")
    head = git("rev-parse", "HEAD").strip()
    assert (coverage_gate._base_relation(str(tmp_path), head, head)
            == "HEAD is on the base")


def test_base_relation_ahead_names_commit_count(tmp_path):
    import subprocess

    def git(*args):
        return subprocess.run(["git", "-C", str(tmp_path), *args],
                               check=True, capture_output=True, text=True).stdout

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    (tmp_path / "f.py").write_text("a = 1\n", encoding="utf-8")
    git("add", "f.py")
    git("commit", "-q", "-m", "base")
    base = git("rev-parse", "HEAD").strip()
    (tmp_path / "other.txt").write_text("x\n", encoding="utf-8")
    git("add", "other.txt")
    git("commit", "-q", "-m", "unrelated, does not touch f.py")
    head = git("rev-parse", "HEAD").strip()
    relation = coverage_gate._base_relation(str(tmp_path), base, head)
    assert relation == (
        "HEAD is 1 commits ahead of the base; the diff touches no "
        "executable source line"
    )


def test_empty_diff_notice_skipped_by_default():
    kind, msg = coverage_gate._empty_diff_notice(
        "deadbeefcafe", "run-gate.py", "HEAD is on the base",
        refuse_empty_diff=False)
    assert kind == "skipped"
    assert msg == (
        "diff-coverage SKIPPED: 0 changed executable lines under "
        "'run-gate.py' between deadbeefca and HEAD (HEAD is on the base)"
    )


def test_empty_diff_notice_error_when_refused():
    kind, msg = coverage_gate._empty_diff_notice(
        "deadbeefcafe", "run-gate.py",
        "HEAD is 3 commits ahead of the base; the diff touches no "
        "executable source line",
        refuse_empty_diff=True)
    assert kind == "error"
    assert msg.startswith(
        "diff-coverage ERROR: 0 changed executable lines under 'run-gate.py'"
    )
    assert "--refuse-empty-diff" in msg
    # Names the three known routes, so a reader does not have to guess.
    assert "RG-54" in msg and "RG-51" in msg


def test_main_reports_skipped_on_zero_changed_diff_by_default(tmp_path, capsys):
    """End-to-end: a repo where base == HEAD (merge-base(base, HEAD) ==
    HEAD, both the RG-51/RG-54 degenerate-base shape and the ordinary
    `main`-itself shape) prints a SKIPPED notice on STDOUT and exits 0 by
    default — never a silent `0/0 100%` OK, and never refused unless
    --refuse-empty-diff is passed (RW-5)."""
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
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "diff-coverage SKIPPED: 0 changed executable lines" in captured.out
    assert "HEAD is on the base" in captured.out
    assert "OK" not in captured.out
    assert "100.0%" not in captured.out

    rc_refused = coverage_gate.main([
        "--repo", str(tmp_path),
        "--base", "main",
        "--coverage-json", str(cov_json),
        "--source", "run-gate.py",
        "--refuse-empty-diff",
    ])
    assert rc_refused == 2
    err = capsys.readouterr().err
    assert "diff-coverage ERROR" in err
    assert "--refuse-empty-diff" in err
    assert "RG-54" in err and "RG-51" in err


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


def test_validate_cov_record_rejects_non_list_branch_key():
    """The isinstance(val, list) guard for missing_branches/executed_branches
    (RG-53) — present but the WRONG TYPE entirely (not even a list), the
    sibling case to test_validate_cov_record_rejects_malformed_branch_arc's
    "right container, wrong element shape"."""
    added = {"run-gate-project/run-gate.py": {5}}
    coverage_files = {
        "run-gate-project/run-gate.py": {
            "executed_lines": [5],
            "missing_lines": [],
            "missing_branches": "not-a-list",
        },
    }
    import pytest
    with pytest.raises(coverage_gate.CoverageGateError):
        coverage_gate.evaluate(added, coverage_files,
                               source_prefix="run-gate-project/run-gate.py")


def test_is_ancestor_raises_on_real_git_failure(tmp_path):
    """`merge-base --is-ancestor` exiting >1 (an invalid rev — never one of
    the ordinary 0/1 yes-or-no outcomes `_base_relation`'s own tests already
    cover) is a real git failure and must raise, not be misread as 'no'."""
    import subprocess

    import pytest

    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args],
                        check=True, capture_output=True, text=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    (tmp_path / "f.py").write_text("a = 1\n", encoding="utf-8")
    git("add", "f.py")
    git("commit", "-q", "-m", "base")
    head = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True).stdout.strip()
    with pytest.raises(coverage_gate.CoverageGateError):
        coverage_gate._is_ancestor(
            str(tmp_path), "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef", head)


def test_main_reports_error_when_base_relation_fails(tmp_path, monkeypatch, capsys):
    """The 0/0 path's own `_base_relation` call can fail for the same reason
    `_is_ancestor` can (a real git failure, never an ordinary ancestor/
    not-ancestor answer) — main() must map that to exit 2 with the same
    ERROR shape every other CoverageGateError gets, not propagate a
    traceback out of its own except-clause."""
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

    def _boom(repo, base_rev, head_rev):
        raise coverage_gate.CoverageGateError("simulated git failure")

    monkeypatch.setattr(coverage_gate, "_base_relation", _boom)

    rc = coverage_gate.main([
        "--repo", str(tmp_path),
        "--base", "main",
        "--coverage-json", str(cov_json),
        "--source", "run-gate.py",
    ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "diff-coverage ERROR: simulated git failure" in err


def test_main_reports_fail_with_uncovered_and_unmeasured_files(tmp_path, capsys):
    """End-to-end the FAIL branch's own rendering, which only ever executes
    when the diff actually fails: one file with a changed line that ran but
    left a branch untaken (rendered with the "(branch)" tag), and a second,
    brand-new file the coverage JSON never measured at all (rendered with
    the "[file unmeasured]" tag) — the loop body and its two tag branches
    have no other caller."""
    import subprocess

    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args],
                        check=True, capture_output=True, text=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("a = 1\n", encoding="utf-8")
    git("add", "pkg")
    git("commit", "-q", "-m", "base")
    (tmp_path / "pkg" / "a.py").write_text("a = 1\nc = 2\nd = 3\n",
                                           encoding="utf-8")
    (tmp_path / "pkg" / "newb.py").write_text("b = 1\ne = 2\n",
                                              encoding="utf-8")
    git("add", "pkg/newb.py")  # staged, uncommitted — still a real new file

    cov_json = tmp_path / "coverage.json"
    cov_json.write_text(json.dumps({
        "files": {
            # pkg/newb.py is entirely absent -- "[file unmeasured]".
            "pkg/a.py": {
                "executed_lines": [2, 3],
                "missing_lines": [],
                # Line 3 ran but left an arm untaken -- rendered "(branch)".
                "executed_branches": [[2, 10]],
                "missing_branches": [[3, 11]],
            },
        }
    }), encoding="utf-8")

    rc = coverage_gate.main([
        "--repo", str(tmp_path),
        "--base", "main",
        "--coverage-json", str(cov_json),
        "--source", "pkg",
    ])
    assert rc == 1
    out = capsys.readouterr().out
    assert "diff-coverage FAIL: 1/4 changed executable lines covered" in out
    assert "Uncovered changed lines:" in out
    assert "  pkg/a.py: [3(branch)]" in out
    assert "  pkg/newb.py: [file unmeasured] [1, 2]" in out
    assert "Add a test that exercises these lines" in out


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
