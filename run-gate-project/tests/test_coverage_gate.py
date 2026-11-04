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

import pytest

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
    assert args.source is None  # main() then judges run_gate.py alone
    args = coverage_gate._build_arg_parser().parse_args(
        ["--coverage-json", "x.json", "--source", "a.py", "--source", "b.py"])
    assert args.source == ["a.py", "b.py"]


def test_parse_added_lines_basic():
    diff = (
        "diff --git a/run_gate.py b/run_gate.py\n"
        "--- a/run_gate.py\n"
        "+++ b/run_gate.py\n"
        "@@ -10,0 +11,2 @@\n"
        "+new line one\n"
        "+new line two\n"
    )
    added = coverage_gate.parse_added_lines(diff)
    assert added == {"run_gate.py": {11, 12}}


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


def test_parse_added_lines_does_not_count_the_no_newline_marker_as_a_line():
    """RG-116 item 1: `\\ No newline at end of file` follows the `-` side when
    the OLD file lacked a trailing newline. Counted as a context line it
    pushed every later `+` line one too far (the true lines 10-11 became
    11-12, and 12 does not exist), so a changed last line escaped the gate."""
    diff = (
        "diff --git a/run_gate.py b/run_gate.py\n"
        "--- a/run_gate.py\n"
        "+++ b/run_gate.py\n"
        "@@ -10 +10,2 @@\n"
        "-foo\n"
        "\\ No newline at end of file\n"
        "+foo2\n"
        "+bar\n"
        "\\ No newline at end of file\n"
    )
    assert coverage_gate.parse_added_lines(diff) == {"run_gate.py": {10, 11}}


def test_git_added_lines_ignores_a_colouring_user_config(tmp_path):
    """RG-116 item 5: `color.diff=always` wraps `+++` and `@@` in escapes; the
    parser then matched nothing and the gate reported 0 changed lines
    (SKIPPED, exit 0). The diff is asked for plain text regardless."""
    import subprocess

    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args],
                       check=True, capture_output=True, text=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    git("config", "color.diff", "always")
    git("config", "color.ui", "always")
    (tmp_path / "demo.py").write_text("a\nb\n", encoding="utf-8")
    git("add", "demo.py")
    git("commit", "-q", "-m", "base")
    base_rev = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True).stdout.strip()
    (tmp_path / "demo.py").write_text("a\nb\nc\n", encoding="utf-8")
    coloured = subprocess.run(
        ["git", "-C", str(tmp_path), "diff", "--unified=0", base_rev],
        check=True, capture_output=True, text=True).stdout
    assert "\x1b[" in coloured          # the config really colours a plain diff
    assert coverage_gate._git_added_lines(str(tmp_path), base_rev) == \
        {"demo.py": {3}}


def test_evaluate_covered_change_passes():
    added = {"run-gate-project/run_gate.py": {5}}
    coverage_files = {
        "run-gate-project/run_gate.py": {"executed_lines": [5], "missing_lines": []},
    }
    v = coverage_gate.evaluate(added, coverage_files,
                               source_prefix="run-gate-project/run_gate.py")
    assert v.passed
    assert v.covered == 1 and v.changed_executable == 1
    assert v.uncovered == {}


def test_evaluate_uncovered_change_fails():
    added = {"run-gate-project/run_gate.py": {5}}
    coverage_files = {
        "run-gate-project/run_gate.py": {"executed_lines": [], "missing_lines": [5]},
    }
    v = coverage_gate.evaluate(added, coverage_files,
                               source_prefix="run-gate-project/run_gate.py")
    assert not v.passed
    assert v.uncovered == {"run-gate-project/run_gate.py": {5}}


def test_evaluate_ignores_files_outside_source_prefix():
    added = {"run-gate-project/tests/test_run_gate.py": {5}}
    coverage_files = {
        "run-gate-project/tests/test_run_gate.py":
            {"executed_lines": [], "missing_lines": [5]},
    }
    v = coverage_gate.evaluate(added, coverage_files,
                               source_prefix="run-gate-project/run_gate.py")
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
    added = {"run-gate-project/run_gate.py": {5}}
    coverage_files = {
        "run-gate-project/run_gate.py": {"executed_lines": "not-a-list", "missing_lines": []},
    }
    import pytest
    with pytest.raises(coverage_gate.CoverageGateError):
        coverage_gate.evaluate(added, coverage_files,
                               source_prefix="run-gate-project/run_gate.py")


def test_evaluate_branch_partial_line_counts_as_uncovered():
    """RG-53: a changed line that RAN but left an arm untaken (present in
    `missing_branches`, absent from `missing_lines`) must count as
    uncovered — the whole point of `--cov-branch` for the diff judge."""
    added = {"run-gate-project/run_gate.py": {5}}
    coverage_files = {
        "run-gate-project/run_gate.py": {
            "executed_lines": [5],
            "missing_lines": [],
            "executed_branches": [[5, 6]],
            "missing_branches": [[5, 99]],
        },
    }
    v = coverage_gate.evaluate(added, coverage_files,
                               source_prefix="run-gate-project/run_gate.py")
    assert not v.passed
    assert v.uncovered == {"run-gate-project/run_gate.py": {5}}
    assert v.branch_partial_lines == {"run-gate-project/run_gate.py": {5}}
    assert v.covered == 0 and v.changed_executable == 1
    assert v.branches_total == 2 and v.branches_missed == 1


def test_evaluate_branch_fully_taken_line_stays_covered():
    """A changed, executed line whose every branch arm was taken stays
    covered — no `missing_branches` entry for it."""
    added = {"run-gate-project/run_gate.py": {5}}
    coverage_files = {
        "run-gate-project/run_gate.py": {
            "executed_lines": [5],
            "missing_lines": [],
            "executed_branches": [[5, 6], [5, 7]],
            "missing_branches": [],
        },
    }
    v = coverage_gate.evaluate(added, coverage_files,
                               source_prefix="run-gate-project/run_gate.py")
    assert v.passed
    assert v.uncovered == {}
    assert v.branch_partial_lines == {}
    assert v.covered == 1 and v.changed_executable == 1
    assert v.branches_total == 2 and v.branches_missed == 0


def test_evaluate_no_branch_data_is_a_noop_line_only_behavior():
    """A coverage record with no branch-arc keys at all (branch coverage was
    not collected) behaves exactly like the pre-RG-53 line-only gate."""
    added = {"run-gate-project/run_gate.py": {5}}
    coverage_files = {
        "run-gate-project/run_gate.py": {"executed_lines": [5], "missing_lines": []},
    }
    v = coverage_gate.evaluate(added, coverage_files,
                               source_prefix="run-gate-project/run_gate.py")
    assert v.passed
    assert v.branches_total == 0 and v.branches_missed == 0
    assert v.branch_partial_lines == {}


def test_evaluate_branches_total_missed_scoped_to_changed_lines_only():
    """A branch on a line the diff never touched must not pollute the
    branch totals — they are reported "beside" the changed-line counts,
    never a second, whole-file denominator."""
    added = {"run-gate-project/run_gate.py": {5}}
    coverage_files = {
        "run-gate-project/run_gate.py": {
            "executed_lines": [5, 10],
            "missing_lines": [],
            "executed_branches": [[5, 6]],
            # Line 10 is fully executed+covered but NOT in the diff -- its
            # branch data must not appear in the totals.
            "missing_branches": [[10, 11]],
        },
    }
    v = coverage_gate.evaluate(added, coverage_files,
                               source_prefix="run-gate-project/run_gate.py")
    assert v.passed
    assert v.branches_total == 1 and v.branches_missed == 0


def test_validate_cov_record_rejects_malformed_branch_arc():
    added = {"run-gate-project/run_gate.py": {5}}
    coverage_files = {
        "run-gate-project/run_gate.py": {
            "executed_lines": [5],
            "missing_lines": [],
            "missing_branches": [[5]],  # missing the target line
        },
    }
    import pytest
    with pytest.raises(coverage_gate.CoverageGateError):
        coverage_gate.evaluate(added, coverage_files,
                               source_prefix="run-gate-project/run_gate.py")


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
    v = coverage_gate.evaluate({}, {}, source_prefix="run-gate-project/run_gate.py")
    assert v.changed_executable == 0
    assert v.skipped is True
    assert v.pct == 100.0 and v.passed is True
    assert v.verdict == "skipped"


def test_verdict_nonzero_diff_is_ok_or_fail():
    added = {"run-gate-project/run_gate.py": {5}}
    ok = coverage_gate.evaluate(
        added,
        {"run-gate-project/run_gate.py": {"executed_lines": [5], "missing_lines": []}},
        source_prefix="run-gate-project/run_gate.py")
    assert ok.skipped is False
    assert ok.verdict == "ok"
    fail = coverage_gate.evaluate(
        added,
        {"run-gate-project/run_gate.py": {"executed_lines": [], "missing_lines": [5]}},
        source_prefix="run-gate-project/run_gate.py")
    assert fail.skipped is False
    assert fail.verdict == "fail"


def test_base_relation_ancestor_reads_head_is_on_the_base(tmp_path):
    """The exact shape `./run_gate.py selftest` hits on `main` itself:
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


def test_latest_release_tag_base_remains_valid_after_merge_to_main(tmp_path):
    """The release gate must not collapse its comparison to HEAD after merge."""
    import subprocess

    def git(*args):
        return subprocess.run(["git", "-C", str(tmp_path), *args],
                              check=True, capture_output=True, text=True).stdout

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    source = tmp_path / "run_gate.py"
    source.write_text("released = True\n", encoding="utf-8")
    git("add", "run_gate.py")
    git("commit", "-q", "-m", "released source")
    release = git("rev-parse", "HEAD").strip()
    git("tag", "run-gate-v23.10.0")

    source.write_text("released = True\ncandidate = True\n", encoding="utf-8")
    git("add", "run_gate.py")
    git("commit", "-q", "-m", "candidate source")

    assert coverage_gate._resolve_base(str(tmp_path), "latest-release-tag") == release


def test_latest_release_tag_base_uses_highest_reachable_stable_version(tmp_path):
    import subprocess

    def git(*args):
        return subprocess.run(["git", "-C", str(tmp_path), *args],
                              check=True, capture_output=True, text=True).stdout

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    (tmp_path / "run_gate.py").write_text("released = True\n", encoding="utf-8")
    git("add", "run_gate.py")
    git("commit", "-q", "-m", "base")
    older = git("rev-parse", "HEAD").strip()
    git("tag", "run-gate-v23.9.1")
    git("tag", "run-gate-v23.10.0")

    (tmp_path / "other.txt").write_text("later\n", encoding="utf-8")
    git("add", "other.txt")
    git("commit", "-q", "-m", "later source-neutral commit")

    assert coverage_gate._resolve_base(str(tmp_path), "latest-release-tag") == older


def test_latest_release_tag_base_refuses_when_no_release_tag_is_reachable(tmp_path):
    import subprocess
    import pytest

    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args],
                       check=True, capture_output=True, text=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    (tmp_path / "run_gate.py").write_text("candidate = True\n", encoding="utf-8")
    git("add", "run_gate.py")
    git("commit", "-q", "-m", "unreleased source")

    with pytest.raises(coverage_gate.CoverageGateError, match="no reachable stable run-gate-vX.Y.Z release tag"):
        coverage_gate._resolve_base(str(tmp_path), "latest-release-tag")


def test_latest_release_tag_base_can_be_skipped_for_an_explicit_first_release(tmp_path):
    import subprocess
    import pytest

    def git(*args):
        return subprocess.run(["git", "-C", str(tmp_path), *args],
                              check=True, capture_output=True, text=True).stdout

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    (tmp_path / "run_gate.py").write_text("first_release = True\n", encoding="utf-8")
    git("add", "run_gate.py")
    git("commit", "-q", "-m", "first stable source")

    with pytest.raises(coverage_gate.CoverageGateError, match="no reachable stable"):
        coverage_gate._resolve_base(str(tmp_path), "latest-release-tag")
    assert coverage_gate._resolve_base(
        str(tmp_path), "latest-release-tag", allow_first_release=True,
    ) is None


def test_first_release_option_does_not_hide_an_unreachable_stable_tag(tmp_path):
    import subprocess
    import pytest

    def git(*args):
        return subprocess.run(["git", "-C", str(tmp_path), *args],
                              check=True, capture_output=True, text=True).stdout

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    (tmp_path / "run_gate.py").write_text("released = True\n", encoding="utf-8")
    git("add", "run_gate.py")
    git("commit", "-q", "-m", "released source")
    git("tag", "run-gate-v23.10.0")
    git("checkout", "--orphan", "unrelated")
    (tmp_path / "run_gate.py").write_text("unrelated = True\n", encoding="utf-8")
    git("add", "run_gate.py")
    git("commit", "-q", "-m", "unrelated history")

    with pytest.raises(coverage_gate.CoverageGateError, match="tags exist but none are reachable"):
        coverage_gate._resolve_base(
            str(tmp_path), "latest-release-tag", allow_first_release=True,
        )


def test_selftest_first_release_coverage_is_reported_as_skipped(tmp_path, capsys):
    import json
    import subprocess

    def git(*args):
        return subprocess.run(["git", "-C", str(tmp_path), *args],
                              check=True, capture_output=True, text=True).stdout

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    (tmp_path / "run_gate.py").write_text("first_release = True\n", encoding="utf-8")
    git("add", "run_gate.py")
    git("commit", "-q", "-m", "first stable source")
    coverage = tmp_path / "coverage.json"
    coverage.write_text(json.dumps({"files": {}}), encoding="utf-8")

    result = coverage_gate.main([
        "--repo", str(tmp_path), "--base", "latest-release-tag",
        "--allow-first-release", "--coverage-json", str(coverage),
        "--source", "run_gate.py",
    ])

    assert result == 0
    assert "diff-coverage SKIPPED: no stable Run-Gate release tag exists" in capsys.readouterr().out


def test_first_release_option_requires_a_release_tag_base(tmp_path):
    import pytest

    coverage = tmp_path / "coverage.json"
    with pytest.raises(SystemExit) as exc:
        coverage_gate.main([
            "--allow-first-release", "--base", "main",
            "--coverage-json", str(coverage),
        ])
    assert exc.value.code == 2


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
        "deadbeefcafe", "run_gate.py", "HEAD is on the base",
        refuse_empty_diff=False)
    assert kind == "skipped"
    assert msg == (
        "diff-coverage SKIPPED: 0 changed executable lines under "
        "'run_gate.py' between deadbeefca and HEAD (HEAD is on the base)"
    )


def test_empty_diff_notice_error_when_refused():
    kind, msg = coverage_gate._empty_diff_notice(
        "deadbeefcafe", "run_gate.py",
        "HEAD is 3 commits ahead of the base; the diff touches no "
        "executable source line",
        refuse_empty_diff=True)
    assert kind == "error"
    assert msg.startswith(
        "diff-coverage ERROR: 0 changed executable lines under 'run_gate.py'"
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
    (tmp_path / "run_gate.py").write_text("a = 1\n", encoding="utf-8")
    git("add", "run_gate.py")
    git("commit", "-q", "-m", "base")

    cov_json = tmp_path / "coverage.json"
    cov_json.write_text('{"files": {}}', encoding="utf-8")

    rc = coverage_gate.main([
        "--repo", str(tmp_path),
        "--base", "main",
        "--coverage-json", str(cov_json),
        "--source", "run_gate.py",
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
        "--source", "run_gate.py",
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
    (tmp_path / "run_gate.py").write_text("a = 1\n", encoding="utf-8")
    git("add", "run_gate.py")
    git("commit", "-q", "-m", "base")
    (tmp_path / "run_gate.py").write_text("a = 1\nb = 2\n", encoding="utf-8")

    cov_json = tmp_path / "coverage.json"
    cov_json.write_text(json.dumps({
        "files": {
            "run_gate.py": {
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
        "--source", "run_gate.py",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "branches 1/1 taken" in out


def test_validate_cov_record_rejects_non_list_branch_key():
    """The isinstance(val, list) guard for missing_branches/executed_branches
    (RG-53) — present but the WRONG TYPE entirely (not even a list), the
    sibling case to test_validate_cov_record_rejects_malformed_branch_arc's
    "right container, wrong element shape"."""
    added = {"run-gate-project/run_gate.py": {5}}
    coverage_files = {
        "run-gate-project/run_gate.py": {
            "executed_lines": [5],
            "missing_lines": [],
            "missing_branches": "not-a-list",
        },
    }
    import pytest
    with pytest.raises(coverage_gate.CoverageGateError):
        coverage_gate.evaluate(added, coverage_files,
                               source_prefix="run-gate-project/run_gate.py")


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
    (tmp_path / "run_gate.py").write_text("a = 1\n", encoding="utf-8")
    git("add", "run_gate.py")
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
        "--source", "run_gate.py",
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

    added = coverage_gate._git_added_lines(str(tmp_path), base_rev)
    # `d`'s REAL on-disk position is 6, not the committed diff's 4 -- and the
    # two prepended uncommitted lines are correctly reported too, at 1 and 2,
    # since they are equally new relative to base_rev.
    assert added == {"demo.py": {1, 2, 6}}, added


def test_git_added_lines_tracks_content_when_symlink_becomes_module(tmp_path):
    """A filename filter hides the old path from Git's rename detector.

    Run-Gate's wheel migration replaces a tracked `run_gate.py` symlink with
    a real module while retiring `run-gate.py`. The changed-line judge must
    count the edited content, not certify or reject the whole destination as
    a new file. It still parses unrelated project changes; `evaluate()` owns
    the source-scope filter.
    """
    import subprocess

    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args],
                       check=True, capture_output=True, text=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    old = [f"original line {i}\n" for i in range(100)]
    (tmp_path / "run-gate.py").write_text("".join(old), encoding="utf-8")
    (tmp_path / "run_gate.py").symlink_to("run-gate.py")
    (tmp_path / "unrelated.py").write_text("answer = 1\n", encoding="utf-8")
    git("add", "run-gate.py", "run_gate.py", "unrelated.py")
    git("commit", "-q", "-m", "base")
    base_rev = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True).stdout.strip()

    (tmp_path / "run-gate.py").unlink()
    (tmp_path / "run_gate.py").unlink()
    additions = [f"new executable {i}\n" for i in range(8)]
    new = old[:50] + additions + old[50:]
    (tmp_path / "run_gate.py").write_text("".join(new), encoding="utf-8")
    (tmp_path / "unrelated.py").write_text("answer = 2\n", encoding="utf-8")

    added = coverage_gate._git_added_lines(str(tmp_path), base_rev)
    assert added["run_gate.py"] == set(range(51, 59)), added
    assert added["unrelated.py"] == {1}, added
    verdict = coverage_gate.evaluate(
        added,
        {"run_gate.py": {
            "executed_lines": list(range(51, 59)), "missing_lines": []}},
        source_prefix="run_gate.py",
    )
    assert verdict.passed
    assert verdict.changed_executable == 8


def test_parse_added_lines_does_not_read_a_plusplus_content_line_as_a_header():
    """RG-116 item 4: an added line whose text is `++ x` renders as `+++ x`
    in the diff. Read as a new-file header it re-targeted the following
    lines to a bogus path and dropped them from the denominator."""
    diff = (
        "diff --git a/run_gate.py b/run_gate.py\n"
        "--- a/run_gate.py\n"
        "+++ b/run_gate.py\n"
        "@@ -4,0 +5,3 @@\n"
        "+first\n"
        "+++ x\n"
        "+third\n"
        "@@ -9 +11,2 @@\n"
        "---- y\n"
        "+kept\n"
        "+also\n"
    )
    assert coverage_gate.parse_added_lines(diff) == {
        "run_gate.py": {5, 6, 7, 11, 12}}


def test_parse_added_lines_counts_context_lines_of_a_wider_hunk():
    diff = (
        "+++ b/m.py\n"
        "@@ -1,3 +1,3 @@\n"
        " keep\n"
        "-old\n"
        "+new\n"
        " tail\n"
        "+++ b/n.py\n"
        "@@ -1 +1 @@\n"
        "-a\n"
        "+b\n"
    )
    assert coverage_gate.parse_added_lines(diff) == {"m.py": {2}, "n.py": {1}}


def test_parse_added_lines_ignores_a_hunk_of_a_deleted_file_body():
    diff = (
        "--- a/gone.py\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-+ plus-looking old line\n"
        "-b\n"
        "+++ b/kept.py\n"
        "@@ -0,0 +1 @@\n"
        "+x\n"
    )
    assert coverage_gate.parse_added_lines(diff) == {"kept.py": {1}}


def test_statement_heads_maps_continuations_and_headers_not_bodies():
    source = (
        "def f(\n"            # 1
        "    a,\n"            # 2  continuation of the def header
        "    b=1,\n"          # 3
        "):\n"                # 4
        "    # note\n"        # 5  comment inside the body: no statement
        "    x = call(\n"     # 6
        "        a,\n"        # 7  continuation of a simple statement
        "        b)\n"        # 8
        "    if (a and\n"     # 9
        "            b):\n"   # 10 continuation of the if header
        "        return x\n"  # 11
        "    return None\n"   # 12
    )
    heads = coverage_gate.statement_heads(source)
    assert heads == {2: 1, 3: 1, 4: 1, 7: 6, 8: 6, 10: 9}
    assert 5 not in heads and 11 not in heads and 12 not in heads


def test_statement_heads_rejects_unparsable_source():
    with pytest.raises(coverage_gate.CoverageGateError, match="cannot parse"):
        coverage_gate.statement_heads("def (:\n")


def test_evaluate_judges_a_changed_continuation_line_as_its_statement():
    """RG-116 item 6: coverage.json lists line 6 only; a change confined to
    line 7 used to leave the denominator and the gate went green."""
    source = "def f():\n    pass\n\n\nx = 1\ny = call(\n    1,\n    2)\n"
    cov = {"m.py": {"executed_lines": [1, 5], "missing_lines": [2, 6]}}
    v = coverage_gate.evaluate({"m.py": {7}}, cov, "m.py",
                               read_source=lambda path: source)
    assert v.changed_executable == 1 and v.uncovered == {"m.py": {6}}
    assert not v.passed
    # Without the reader the change is invisible (the old behaviour).
    assert coverage_gate.evaluate({"m.py": {7}}, cov, "m.py").skipped


def test_evaluate_ignores_a_continuation_whose_head_is_not_executable():
    source = 'x = 1\n"""doc\nstring"""\n'
    cov = {"m.py": {"executed_lines": [1], "missing_lines": []}}
    v = coverage_gate.evaluate({"m.py": {3}}, cov, "m.py",
                               read_source=lambda path: source)
    assert v.skipped


def test_evaluate_judges_every_repeated_source_once():
    added = {"a.py": {1}, "b.py": {1}, "c.py": {1}}
    cov = {
        "a.py": {"executed_lines": [1], "missing_lines": []},
        "b.py": {"executed_lines": [], "missing_lines": [1]},
        "c.py": {"executed_lines": [], "missing_lines": [1]},
    }
    only_a = coverage_gate.evaluate(added, cov, "a.py")
    assert only_a.changed_executable == 1 and only_a.passed
    both = coverage_gate.evaluate(added, cov, ["a.py", "b.py", "a.py"])
    assert both.changed_executable == 2 and both.covered == 1
    assert both.uncovered == {"b.py": {1}}


def test_main_judges_every_source_and_reads_continuation_lines(tmp_path, capsys):
    import subprocess

    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args],
                       check=True, capture_output=True, text=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    (tmp_path / "one.py").write_text("a = 1\n", encoding="utf-8")
    (tmp_path / "two.py").write_text("b = 1\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-q", "-m", "base")
    (tmp_path / "one.py").write_text("a = 1\nc = f(\n    2)\n", encoding="utf-8")
    (tmp_path / "two.py").write_text("b = 1\nd = 3\n", encoding="utf-8")
    cov_json = tmp_path / "coverage.json"
    cov_json.write_text(json.dumps({"files": {
        "one.py": {"executed_lines": [1, 2], "missing_lines": []},
        "two.py": {"executed_lines": [1], "missing_lines": [2]},
    }}), encoding="utf-8")
    argv = ["--repo", str(tmp_path), "--base", "main",
            "--coverage-json", str(cov_json)]
    assert coverage_gate.main([*argv, "--source", "one.py"]) == 0
    assert coverage_gate.main([*argv, "--source", "one.py",
                               "--source", "two.py"]) == 1
    out = capsys.readouterr().out
    assert "1/2 changed executable lines covered" in out
    assert "  two.py: [2]" in out


def test_source_reader_reads_and_names_an_unreadable_source(tmp_path):
    (tmp_path / "m.py").write_text("a = 1\n", encoding="utf-8")
    assert coverage_gate._source_reader(str(tmp_path))("m.py") == "a = 1\n"
    with pytest.raises(coverage_gate.CoverageGateError, match="cannot read source"):
        coverage_gate._source_reader(str(tmp_path / "nowhere"))("m.py")


def test_statement_heads_handles_match_and_try_headers():
    source = (
        "match (a,\n"           # 1
        "       b):\n"          # 2
        "    case 1:\n"         # 3
        "        pass\n"        # 4
        "try:\n"                # 5
        "    x = 1\n"           # 6
        "finally:\n"            # 7
        "    y = 2\n"           # 8
    )
    assert coverage_gate.statement_heads(source) == {2: 1}


def test_evaluate_skips_a_file_entry_with_no_changed_lines():
    cov = {"m.py": {"executed_lines": [1], "missing_lines": []}}
    assert coverage_gate.evaluate({"m.py": set()}, cov, "m.py").skipped
    assert coverage_gate.evaluate({"n.py": set()}, cov, "n.py").skipped


def test_selftest_lane_judges_both_shipped_modules():
    """RG-116 item 3: the lane must repeat `--source` so the wheel's
    run_gate_admission.py is diff-judged too, not only run_gate.py."""
    import tomllib

    config = tomllib.loads(
        (Path(__file__).resolve().parent.parent / "run-gate.toml")
        .read_text(encoding="utf-8"))
    command = config["lanes"]["selftest"]["argv"][-1]
    sources = [part for part in command.split("--source ")[1:]]
    assert [s.split()[0].rstrip('"') for s in sources] == [
        "run_gate.py", "run_gate_admission.py", "run_gate_profile.py"]
