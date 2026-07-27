"""Tests for the D-064-L2 mechanical diff-coverage gate.

Doubly non-hollow by construction: this gate, run on its own branch, requires
every added executable line of `coverage_gate.py` to be exercised — so each test
below drives a real observable, and the negatives below prove the ignore/pass
branches discriminate on *executability* (not merely on "a test ran").
"""

from __future__ import annotations

import json
import subprocess

import pytest

from nyxloom import coverage_gate as cg


# --------------------------------------------------------------------------- #
# parse_added_lines — the added-line hunk walk
# --------------------------------------------------------------------------- #

def test_parse_added_lines_multi_hunk_multi_file_and_counts():
    diff = (
        "diff --git a/src/nyxloom/foo.py b/src/nyxloom/foo.py\n"
        "--- a/src/nyxloom/foo.py\n"
        "+++ b/src/nyxloom/foo.py\n"
        "@@ -10,0 +11,3 @@ def existing():\n"
        "+    a = 1\n"
        "+    b = 2\n"
        "+    c = 3\n"
        "@@ -20,1 +25 @@ def other():\n"   # new-side count omitted → defaults to 1
        "+    d = 4\n"
        "diff --git a/src/nyxloom/bar.py b/src/nyxloom/bar.py\n"
        "--- a/src/nyxloom/bar.py\n"
        "+++ b/src/nyxloom/bar.py\n"
        "@@ -1,0 +2,1 @@\n"
        "+    only = 1\n"
    )
    assert cg.parse_added_lines(diff) == {
        "src/nyxloom/foo.py": {11, 12, 13, 25},
        "src/nyxloom/bar.py": {2},
    }


def test_parse_added_lines_ignores_deletions_and_deleted_files():
    diff = (
        "--- a/src/nyxloom/foo.py\n"
        "+++ b/src/nyxloom/foo.py\n"
        "@@ -5,2 +5,1 @@\n"
        "-    removed_one\n"          # deletion: no new-side line
        "-    removed_two\n"
        "+    replacement\n"          # the single added line at new-side 5
        "diff --git a/src/nyxloom/gone.py b/src/nyxloom/gone.py\n"
        "--- a/src/nyxloom/gone.py\n"
        "+++ /dev/null\n"             # deleted file → no added lines
        "@@ -1,2 +0,0 @@\n"
        "-    a\n"
        "-    b\n"
    )
    result = cg.parse_added_lines(diff)
    assert result == {"src/nyxloom/foo.py": {5}}
    assert "gone.py" not in " ".join(result)  # deleted file contributes nothing


def test_parse_added_lines_advances_over_context_lines():
    # -U>0 emits space-prefixed context; the new-side counter must advance past it
    # so a `+` after context lands on the right number.
    diff = (
        "+++ b/src/nyxloom/foo.py\n"
        "@@ -10,3 +10,4 @@\n"
        " ctx_at_10\n"
        " ctx_at_11\n"
        "+    added_at_12\n"
        " ctx_at_13\n"
    )
    assert cg.parse_added_lines(diff) == {"src/nyxloom/foo.py": {12}}


def test_parse_added_lines_strips_b_prefix_only():
    diff = "+++ src/nyxloom/noprefix.py\n@@ -0,0 +1 @@\n+x\n"
    assert cg.parse_added_lines(diff) == {"src/nyxloom/noprefix.py": {1}}


# --------------------------------------------------------------------------- #
# _rel_to_source — path normalization
# --------------------------------------------------------------------------- #

def test_rel_to_source_finds_prefix_and_passes_through_when_absent():
    assert cg._rel_to_source("nyxloom/src/nyxloom/x.py", "src/nyxloom") == "src/nyxloom/x.py"
    assert cg._rel_to_source("/abs/src/nyxloom/x.py", "src/nyxloom") == "src/nyxloom/x.py"
    assert cg._rel_to_source("tests/test_x.py", "src/nyxloom") == "tests/test_x.py"


# --------------------------------------------------------------------------- #
# evaluate — the pure heart; each pass/fail branch with its discriminating negative
# --------------------------------------------------------------------------- #

def _cov(executed, missing):
    return {"executed_lines": list(executed), "missing_lines": list(missing)}


def test_evaluate_uncovered_changed_line_fails():
    added = {"src/nyxloom/foo.py": {11, 12}}
    coverage = {"src/nyxloom/foo.py": _cov(executed=[11], missing=[12])}
    v = cg.evaluate(added, coverage)
    assert not v.passed
    assert v.uncovered == {"src/nyxloom/foo.py": {12}}
    assert (v.changed_executable, v.covered) == (2, 1)
    assert v.pct == 50.0


def test_evaluate_all_changed_lines_covered_passes():
    added = {"src/nyxloom/foo.py": {11, 12}}
    coverage = {"src/nyxloom/foo.py": _cov(executed=[11, 12], missing=[])}
    v = cg.evaluate(added, coverage)
    assert v.passed and not v.uncovered and v.pct == 100.0


def test_evaluate_ignores_non_python_files_under_the_source_prefix():
    """B63 2026-07-20 (false positive this gate produced on its own repo): a
    JSON schema lives under src/nyxloom/ but coverage.py measures only .py, so
    it can NEVER appear in the report. The unmeasured-file branch below treated
    that absence as uncovered and failed the gate with a verdict no test could
    clear. Unmeasurable is not unmeasured."""
    added = {"src/nyxloom/schemas/nyxloom-config.schema.json": {79, 80}}
    v = cg.evaluate(added, {})
    assert v.passed
    assert v.uncovered == {}
    assert v.files_missing_coverage == []
    assert v.changed_executable == 0


def test_evaluate_still_fails_an_unmeasured_PYTHON_file_THE_NEGATIVE():
    """The paired negative: the fix must skip only non-Python paths. A .py
    file absent from the report is still a real unmeasured-source failure --
    otherwise the exemption would silently swallow untested new modules."""
    added = {"src/nyxloom/brand_new.py": {1, 2}}
    v = cg.evaluate(added, {})
    assert not v.passed
    assert v.uncovered == {"src/nyxloom/brand_new.py": {1, 2}}
    assert v.files_missing_coverage == ["src/nyxloom/brand_new.py"]


def test_evaluate_ignores_non_executable_changed_lines_THE_DISCRIMINATOR():
    # Line 30 is a changed comment/blank: coverage lists it in NEITHER executed
    # nor missing. Editing it must NOT be an uncovered-code event.
    added = {"src/nyxloom/foo.py": {30}}
    coverage = {"src/nyxloom/foo.py": _cov(executed=[11], missing=[12])}
    v = cg.evaluate(added, coverage)
    assert v.passed and not v.uncovered
    assert v.changed_executable == 0  # nothing to cover — the line is not code

    # NEGATIVE (proves the pass above discriminates on executability, not on
    # "we included the line"): make that SAME line number executable-but-missing
    # and it now fails.
    coverage_missing = {"src/nyxloom/foo.py": _cov(executed=[11], missing=[12, 30])}
    v2 = cg.evaluate(added, coverage_missing)
    assert not v2.passed and v2.uncovered == {"src/nyxloom/foo.py": {30}}


def test_evaluate_fail_under_floor_is_a_partial_gate():
    added = {"src/nyxloom/foo.py": set(range(1, 21))}            # 20 changed lines
    coverage = {"src/nyxloom/foo.py": _cov(executed=list(range(1, 20)), missing=[20])}
    assert cg.evaluate(added, coverage, fail_under=100.0).passed is False   # 95% < 100
    v90 = cg.evaluate(added, coverage, fail_under=90.0)
    assert v90.passed is True                                    # 95% ≥ 90
    assert v90.uncovered == {"src/nyxloom/foo.py": {20}}         # still reported


def test_evaluate_empty_diff_is_a_clean_pass_post_merge_no_op():
    v = cg.evaluate({}, {"src/nyxloom/foo.py": _cov([], [12])})
    assert v.passed and v.pct == 100.0 and v.changed_executable == 0


def test_evaluate_b5_shaped_new_branch_with_no_test_is_flagged():
    # A new eligibility-branch (the B5 `_attempt_scan` gap shape): lines added,
    # coverage reports them all missing (no test drives the branch) → hard fail.
    added = {"src/nyxloom/reconcile.py": {814, 815, 816}}
    coverage = {"src/nyxloom/reconcile.py": _cov(executed=[810, 811], missing=[814, 815, 816])}
    v = cg.evaluate(added, coverage)
    assert not v.passed and v.pct == 0.0
    assert v.uncovered == {"src/nyxloom/reconcile.py": {814, 815, 816}}


def test_evaluate_ignores_changes_outside_source_prefix():
    # A test file and a doc changed with lines coverage would call missing — no
    # coverage obligation on non-source paths, so this passes.
    added = {"tests/test_foo.py": {5, 6}, "docs/spec.md": {1}}
    coverage = {"tests/test_foo.py": _cov(executed=[], missing=[5, 6])}
    v = cg.evaluate(added, coverage)
    assert v.passed and not v.uncovered and v.changed_executable == 0


def test_evaluate_source_file_absent_from_coverage_is_unmeasured_fail():
    # A source file changed but coverage never saw it (should not happen under
    # --source, but must fail loudly, not silently pass untested new code).
    added = {"src/nyxloom/orphan.py": {3, 4}}
    v = cg.evaluate(added, coverage_files={}, fail_under=100.0)
    assert not v.passed
    assert v.uncovered == {"src/nyxloom/orphan.py": {3, 4}}
    assert v.files_missing_coverage == ["src/nyxloom/orphan.py"]


def test_evaluate_matches_across_path_spellings_guards_the_live_bug():
    # git emits repo-root-relative `nyxloom/src/nyxloom/x.py`; coverage emits
    # cwd-relative `src/nyxloom/x.py`. If normalization failed they'd not match
    # and a missing line would be silently passed (the classic live-only bug).
    added = {"nyxloom/src/nyxloom/x.py": {7}}
    coverage = {"src/nyxloom/x.py": _cov(executed=[], missing=[7])}
    v = cg.evaluate(added, coverage)
    assert not v.passed and v.uncovered == {"src/nyxloom/x.py": {7}}
    # negative: same spelling mismatch, but the line IS executed → passes
    coverage_ok = {"src/nyxloom/x.py": _cov(executed=[7], missing=[])}
    assert cg.evaluate(added, coverage_ok).passed


def test_verdict_passed_property_is_pct_vs_floor():
    assert cg.Verdict({}, 4, 4, 100.0, 100.0).passed is True
    assert cg.Verdict({}, 4, 3, 75.0, 100.0).passed is False
    assert cg.Verdict({}, 4, 3, 75.0, 70.0).passed is True


# --------------------------------------------------------------------------- #
# I/O boundary — git / coverage-json / base resolution
# --------------------------------------------------------------------------- #

class _Proc:
    def __init__(self, rc, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_git_returns_stdout_on_success_and_raises_on_failure(monkeypatch):
    monkeypatch.setattr(cg.subprocess, "run", lambda *a, **k: _Proc(0, "ok\n"))
    assert cg._git(".", ["rev-parse", "HEAD"]) == "ok\n"
    monkeypatch.setattr(cg.subprocess, "run", lambda *a, **k: _Proc(128, "", "bad object"))
    with pytest.raises(cg.CoverageGateError):
        cg._git(".", ["diff", "deadbeef"])


def test_resolve_base_merge_commit_uses_first_parent(monkeypatch):
    def fake_git(repo, args):
        assert args[0] == "rev-list"  # merge-base must NOT be consulted
        return "MERGESHA P1SHA P2SHA\n"
    monkeypatch.setattr(cg, "_git", fake_git)
    assert cg._resolve_base(".", "main") == "P1SHA"


def test_resolve_base_linear_commit_uses_merge_base(monkeypatch):
    def fake_git(repo, args):
        if args[0] == "rev-list":
            return "TIPSHA P1SHA\n"
        assert args[:3] == ["merge-base", "main", "HEAD"]
        return "BASESHA\n"
    monkeypatch.setattr(cg, "_git", fake_git)
    assert cg._resolve_base(".", "main") == "BASESHA"


def test_git_added_lines_diffs_and_parses(monkeypatch):
    captured = {}

    def fake_git(repo, args):
        captured["args"] = args
        return "+++ b/src/nyxloom/x.py\n@@ -0,0 +1,2 @@\n+a\n+b\n"

    monkeypatch.setattr(cg, "_git", fake_git)
    out = cg._git_added_lines(".", "BASE", "src/nyxloom")
    assert out == {"src/nyxloom/x.py": {1, 2}}
    assert captured["args"] == [
        "diff", "--relative", "--unified=0", "BASE", "HEAD", "--", "src/nyxloom"
    ]


def test_load_coverage_reads_files_object(tmp_path):
    p = tmp_path / "cov.json"
    p.write_text(json.dumps({"files": {"src/nyxloom/x.py": {"executed_lines": [1]}}}))
    assert cg._load_coverage(str(p)) == {"src/nyxloom/x.py": {"executed_lines": [1]}}


def test_load_coverage_raises_on_missing_file_and_on_bad_shape(tmp_path):
    with pytest.raises(cg.CoverageGateError):
        cg._load_coverage(str(tmp_path / "nope.json"))
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"no_files_key": True}))
    with pytest.raises(cg.CoverageGateError):
        cg._load_coverage(str(bad))


# --------------------------------------------------------------------------- #
# NO MEASUREMENT (2026-07-27) — the third outcome, unit level (mocked _git)
# --------------------------------------------------------------------------- #

def test_head_rev_strips_and_returns(monkeypatch):
    monkeypatch.setattr(cg, "_git", lambda repo, args: "DEADBEEF\n")
    assert cg._head_rev(".") == "DEADBEEF"


def test_dirty_paths_under_source_parses_porcelain_and_normalizes(monkeypatch):
    """`git status --porcelain` ALWAYS reports paths relative to the repo TOP
    LEVEL, even with `-C` into a subdirectory -- unlike `git diff --relative`,
    which is why `_git_added_lines` passes that flag explicitly. Route every
    line through `_rel_to_source` so a self-hosting subdir (nyxloom inside
    vbpub) still reports the `source`-prefixed spelling everything else in
    this module uses. Also exercises: staged (`A `), unstaged (` M`), a
    rename (only the NEW path survives), and a blank trailing line ignored."""
    def fake_git(repo, args):
        assert args[:2] == ["status", "--porcelain"]
        return (
            " M nyxloom/src/nyxloom/a.py\n"
            "A  nyxloom/src/nyxloom/b.py\n"
            "R  nyxloom/src/nyxloom/old.py -> nyxloom/src/nyxloom/new.py\n"
            "\n"
        )
    monkeypatch.setattr(cg, "_git", fake_git)
    assert cg._dirty_paths_under_source(".", "src/nyxloom") == [
        "src/nyxloom/a.py", "src/nyxloom/b.py", "src/nyxloom/new.py",
    ]


def test_dirty_paths_under_source_none_when_clean(monkeypatch):
    monkeypatch.setattr(cg, "_git", lambda repo, args: "")
    assert cg._dirty_paths_under_source(".", "src/nyxloom") == []


def test_check_measurable_raises_on_dirty_tree_and_names_paths_BEFORE_head_check(monkeypatch):
    """Dirty-tree is checked FIRST: `_head_rev` must never even be called
    when the tree is already dirty (it would raise if it were)."""
    monkeypatch.setattr(
        cg, "_dirty_paths_under_source",
        lambda repo, source: ["src/nyxloom/a.py", "src/nyxloom/b.py"],
    )

    def must_not_be_called(repo):
        raise AssertionError("base==HEAD check ran despite a dirty tree")
    monkeypatch.setattr(cg, "_head_rev", must_not_be_called)

    with pytest.raises(cg.NoMeasurementError) as exc:
        cg._check_measurable(".", "BASE", "src/nyxloom")
    msg = str(exc.value)
    assert "2 uncommitted file(s)" in msg
    assert "src/nyxloom/a.py" in msg and "src/nyxloom/b.py" in msg
    assert "src/nyxloom" in msg  # names the --source scope, not just the files


def test_check_measurable_raises_on_base_equals_head(monkeypatch):
    monkeypatch.setattr(cg, "_dirty_paths_under_source", lambda repo, source: [])
    monkeypatch.setattr(cg, "_head_rev", lambda repo: "SAMESHA")
    with pytest.raises(cg.NoMeasurementError) as exc:
        cg._check_measurable(".", "SAMESHA", "src/nyxloom")
    assert "IS HEAD" in str(exc.value)


def test_check_measurable_passes_on_clean_tree_and_real_delta_NEGATIVE(monkeypatch):
    """The negative proving the guard is scoped to the two real preconditions
    -- a clean tree with base != HEAD must not raise at all."""
    monkeypatch.setattr(cg, "_dirty_paths_under_source", lambda repo, source: [])
    monkeypatch.setattr(cg, "_head_rev", lambda repo: "HEADSHA")
    cg._check_measurable(".", "BASESHA", "src/nyxloom")  # must not raise


# --------------------------------------------------------------------------- #
# main — the CLI wiring (parse → evaluate → print → exit code)
# --------------------------------------------------------------------------- #

def _write_cov(tmp_path, files):
    p = tmp_path / "cov.json"
    p.write_text(json.dumps({"files": files}))
    return str(p)


def test_main_pass_returns_0_and_prints_ok(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cg, "_resolve_base", lambda repo, base: "BASE")
    # This test drives ONLY the pass/fail wiring below _check_measurable, not
    # the guard itself (that has its own dedicated tests) -- stub it out so
    # the assertion is not coupled to real git state at the default --repo ".".
    monkeypatch.setattr(cg, "_check_measurable", lambda repo, base_rev, source: None)
    monkeypatch.setattr(cg, "_git_added_lines", lambda repo, base, source: {"src/nyxloom/x.py": {5}})
    cov = _write_cov(tmp_path, {"src/nyxloom/x.py": {"executed_lines": [5], "missing_lines": []}})
    rc = cg.main(["--coverage-json", cov])
    assert rc == 0
    assert "diff-coverage OK" in capsys.readouterr().out


def test_main_fail_returns_1_and_lists_uncovered(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cg, "_resolve_base", lambda repo, base: "BASE")
    monkeypatch.setattr(cg, "_check_measurable", lambda repo, base_rev, source: None)
    monkeypatch.setattr(cg, "_git_added_lines", lambda repo, base, source: {"src/nyxloom/x.py": {5, 6}})
    cov = _write_cov(tmp_path, {"src/nyxloom/x.py": {"executed_lines": [5], "missing_lines": [6]}})
    rc = cg.main(["--coverage-json", cov, "--fail-under", "100"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "diff-coverage FAIL" in out and "src/nyxloom/x.py" in out and "[6]" in out


def test_main_unmeasured_file_tag_is_shown(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cg, "_resolve_base", lambda repo, base: "BASE")
    monkeypatch.setattr(cg, "_check_measurable", lambda repo, base_rev, source: None)
    monkeypatch.setattr(cg, "_git_added_lines", lambda repo, base, source: {"src/nyxloom/orphan.py": {3}})
    cov = _write_cov(tmp_path, {})  # file absent from coverage
    rc = cg.main(["--coverage-json", cov])
    out = capsys.readouterr().out
    assert rc == 1 and "[file unmeasured]" in out


def test_main_io_error_returns_2(monkeypatch, tmp_path, capsys):
    def boom(repo, base):
        raise cg.CoverageGateError("git exploded")
    monkeypatch.setattr(cg, "_resolve_base", boom)
    rc = cg.main(["--coverage-json", str(tmp_path / "irrelevant.json")])
    assert rc == 2
    assert "diff-coverage ERROR" in capsys.readouterr().err


def test_arg_parser_defaults():
    args = cg._build_arg_parser().parse_args(["--coverage-json", "c.json"])
    assert (args.base, args.source, args.fail_under, args.repo) == (
        "main", "src/nyxloom", 100.0, ".")
    # GA5: strict by default -- a pragma on a changed line must be opted into.
    assert args.allow_excluded is False


# --------------------------------------------------------------------------- #
# GA5 — `pragma: no cover` on CHANGED lines cannot launder the gate
# --------------------------------------------------------------------------- #

def test_excluded_changed_lines_are_invisible_to_the_percentage_THE_HOLE():
    """The defect this guard closes, stated as an executable fact.

    coverage.py sorts every line into exactly one of executed / missing /
    excluded. The ratio is built from executed ∪ missing, so an EXCLUDED line
    leaves the numerator AND the denominator. Adding `pragma: no cover` to an
    uncovered changed line therefore does not merely hide it -- it raises the
    reported percentage. This test pins that arithmetic so the guard below can
    never be quietly regressed into a no-op."""
    added = {"src/nyxloom/x.py": {1, 2}}
    # Line 2 uncovered -> 50%.
    honest = cg.evaluate(added, {"src/nyxloom/x.py": {
        "executed_lines": [1], "missing_lines": [2], "excluded_lines": []}})
    assert (honest.covered, honest.changed_executable, honest.pct) == (1, 2, 50.0)

    # Same code, line 2 pragma'd -> the denominator shrinks and it reads 100%.
    laundered = cg.evaluate(added, {"src/nyxloom/x.py": {
        "executed_lines": [1], "missing_lines": [], "excluded_lines": [2]}},
        allow_excluded=True)
    assert (laundered.covered, laundered.changed_executable, laundered.pct) == (1, 1, 100.0)


def test_excluded_changed_line_fails_the_gate_by_default():
    """The guard: the same laundered input above must NOT pass. Note the
    percentage is a perfect 100.0 -- the verdict is False anyway, which is the
    whole point. No coverage floor could have produced this."""
    v = cg.evaluate({"src/nyxloom/x.py": {1, 2}}, {"src/nyxloom/x.py": {
        "executed_lines": [1], "missing_lines": [], "excluded_lines": [2]}})
    assert v.pct == 100.0
    assert v.excluded == {"src/nyxloom/x.py": {2}}
    assert v.excluded_count == 1
    assert v.passed is False


def test_allow_excluded_restores_the_escape_hatch():
    """The escape hatch survives, but only as a deliberate, reviewable choice in
    the project's declared gate argv -- never as an invisible comment."""
    v = cg.evaluate({"src/nyxloom/x.py": {1, 2}}, {"src/nyxloom/x.py": {
        "executed_lines": [1], "missing_lines": [], "excluded_lines": [2]}},
        allow_excluded=True)
    assert v.excluded_count == 1 and v.passed is True


def test_pragma_on_an_UNCHANGED_line_is_not_penalised_NEGATIVE():
    """Deliberate negative: the guard is scoped to lines this diff touched.
    Pre-existing pragmas elsewhere in a changed file are none of its business,
    or every edit to a file containing one would fail forever."""
    v = cg.evaluate({"src/nyxloom/x.py": {1}}, {"src/nyxloom/x.py": {
        "executed_lines": [1], "missing_lines": [], "excluded_lines": [99]}})
    assert v.excluded == {} and v.passed is True


def test_main_excluded_changed_line_returns_1_and_explains(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cg, "_resolve_base", lambda repo, base: "BASE")
    monkeypatch.setattr(cg, "_check_measurable", lambda repo, base_rev, source: None)
    monkeypatch.setattr(cg, "_git_added_lines",
                        lambda repo, base, source: {"src/nyxloom/x.py": {5}})
    cov = _write_cov(tmp_path, {"src/nyxloom/x.py": {
        "executed_lines": [], "missing_lines": [], "excluded_lines": [5]}})
    rc = cg.main(["--coverage-json", cov])
    out = capsys.readouterr().out
    assert rc == 1
    assert "EXCLUDED" in out and "src/nyxloom/x.py" in out and "[5]" in out
    assert "--allow-excluded" in out  # tells the operator the deliberate route


def test_main_allow_excluded_passes_and_says_so(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cg, "_resolve_base", lambda repo, base: "BASE")
    monkeypatch.setattr(cg, "_check_measurable", lambda repo, base_rev, source: None)
    monkeypatch.setattr(cg, "_git_added_lines",
                        lambda repo, base, source: {"src/nyxloom/x.py": {5}})
    cov = _write_cov(tmp_path, {"src/nyxloom/x.py": {
        "executed_lines": [], "missing_lines": [], "excluded_lines": [5]}})
    rc = cg.main(["--coverage-json", cov, "--allow-excluded"])
    out = capsys.readouterr().out
    assert rc == 0 and "diff-coverage OK" in out
    assert "excluded by pragma" in out  # never silent, even when allowed


def test_main_reports_BOTH_failures_when_a_diff_has_each(monkeypatch, tmp_path, capsys):
    """A diff can be laundered AND undertested at once; the operator needs both
    lists, not whichever branch happened to run first."""
    monkeypatch.setattr(cg, "_resolve_base", lambda repo, base: "BASE")
    monkeypatch.setattr(cg, "_check_measurable", lambda repo, base_rev, source: None)
    monkeypatch.setattr(cg, "_git_added_lines",
                        lambda repo, base, source: {"src/nyxloom/x.py": {5, 6}})
    cov = _write_cov(tmp_path, {"src/nyxloom/x.py": {
        "executed_lines": [], "missing_lines": [6], "excluded_lines": [5]}})
    rc = cg.main(["--coverage-json", cov])
    out = capsys.readouterr().out
    assert rc == 1
    assert "EXCLUDED" in out and "[5]" in out          # the laundering
    assert "Uncovered changed lines" in out and "[6]" in out  # the plain gap


def test_all_four_exit_codes_are_mutually_distinct(monkeypatch, tmp_path):
    """ORACLE: 0 (pass), 1 (fail), 2 (tool error), 3 (no measurement) are four
    DIFFERENT integers. A caller must be able to tell "ship it" (0) apart
    from "fix your code" (1) apart from "the tool broke, maybe retry" (2)
    apart from "commit first, this isn't a verdict at all" (3) -- collapsing
    any two of these into the same code is the exact ambiguity this whole
    package exists to remove."""
    monkeypatch.setattr(cg, "_resolve_base", lambda repo, base: "BASE")
    monkeypatch.setattr(cg, "_check_measurable", lambda repo, base_rev, source: None)
    monkeypatch.setattr(cg, "_git_added_lines", lambda repo, base, source: {"src/nyxloom/x.py": {1}})

    cov_pass = _write_cov(tmp_path, {"src/nyxloom/x.py": {"executed_lines": [1], "missing_lines": []}})
    rc_pass = cg.main(["--coverage-json", cov_pass])

    cov_fail = _write_cov(tmp_path, {"src/nyxloom/x.py": {"executed_lines": [], "missing_lines": [1]}})
    rc_fail = cg.main(["--coverage-json", cov_fail])

    def boom_tool(repo, base):
        raise cg.CoverageGateError("git exploded")
    monkeypatch.setattr(cg, "_resolve_base", boom_tool)
    rc_error = cg.main(["--coverage-json", cov_pass])

    monkeypatch.setattr(cg, "_resolve_base", lambda repo, base: "BASE")

    def boom_no_measurement(repo, base_rev, source):
        raise cg.NoMeasurementError("dirty tree")
    monkeypatch.setattr(cg, "_check_measurable", boom_no_measurement)
    rc_no_measurement = cg.main(["--coverage-json", cov_pass])

    assert (rc_pass, rc_fail, rc_error, rc_no_measurement) == (0, 1, 2, 3)
    assert len({rc_pass, rc_fail, rc_error, rc_no_measurement}) == 4


# --------------------------------------------------------------------------- #
# NO MEASUREMENT — end-to-end oracles against a REAL git repo (2026-07-27)
# --------------------------------------------------------------------------- #

def _run_git(args, cwd):
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    assert r.returncode == 0, f"git {args} failed: {r.stderr}"
    return r.stdout


def _init_feature_repo(tmp_path):
    """A real git repo, branch `main` at one commit, branch `feature`
    (checked out == HEAD) also at that SAME commit -- the exact shape
    `--base main` is designed for, and also the degenerate base==HEAD case
    before `_advance_feature` is called. `src/nyxloom/x.py` + `README.md`
    both exist from the first commit so later tests can dirty either one."""
    repo = tmp_path / "repo"
    (repo / "src" / "nyxloom").mkdir(parents=True)
    (repo / "src" / "nyxloom" / "x.py").write_text("a = 1\n")
    (repo / "README.md").write_text("hello\n")
    _run_git(["init", "-q", "-b", "main"], repo)
    _run_git(["config", "user.email", "t@t.com"], repo)
    _run_git(["config", "user.name", "T"], repo)
    _run_git(["add", "-A"], repo)
    _run_git(["commit", "-q", "-m", "initial"], repo)
    _run_git(["checkout", "-q", "-b", "feature"], repo)
    return repo


def _advance_feature(repo, rel_path, content, message="feature work"):
    """Commit one more change on `feature` so base(`main`) != HEAD. Used to
    isolate the dirty-tree oracles from the base==HEAD guard: without this,
    a fresh `_init_feature_repo` (feature == main == HEAD) would ALSO trip
    the base==HEAD check, and a test naming "the dirty tree" as its subject
    would actually be exercising two conditions at once."""
    p = repo / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    _run_git(["add", "-A"], repo)
    _run_git(["commit", "-q", "-m", message], repo)


def test_oracle_dirty_source_tree_exits_3_and_names_the_path(tmp_path, capsys):
    repo = _init_feature_repo(tmp_path)
    _advance_feature(repo, "README.md", "hello v2\n")  # real delta, base != HEAD
    (repo / "src" / "nyxloom" / "x.py").write_text("a = 2\n")  # unstaged, uncommitted
    cov = _write_cov(tmp_path, {})
    rc = cg.main(["--repo", str(repo), "--coverage-json", cov])
    err = capsys.readouterr().err
    assert rc == 3
    assert "diff-coverage NO MEASUREMENT" in err
    assert "src/nyxloom/x.py" in err


def test_oracle_NEGATIVE_dirty_tree_outside_source_does_not_trip(tmp_path, capsys):
    repo = _init_feature_repo(tmp_path)
    _advance_feature(repo, "src/nyxloom/x.py", "a = 2\n")  # real delta, base != HEAD
    (repo / "README.md").write_text("dirty docs, not under --source\n")  # dirty, out of scope
    cov = _write_cov(tmp_path, {"src/nyxloom/x.py": {"executed_lines": [1], "missing_lines": []}})
    rc = cg.main(["--repo", str(repo), "--coverage-json", cov, "--source", "src/nyxloom"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "diff-coverage OK" in out


def test_oracle_staged_but_uncommitted_also_trips_it(tmp_path, capsys):
    repo = _init_feature_repo(tmp_path)
    _advance_feature(repo, "README.md", "hello v2\n")
    (repo / "src" / "nyxloom" / "x.py").write_text("a = 3\n")
    _run_git(["add", "src/nyxloom/x.py"], repo)  # staged, NOT committed
    cov = _write_cov(tmp_path, {})
    rc = cg.main(["--repo", str(repo), "--coverage-json", cov])
    err = capsys.readouterr().err
    assert rc == 3
    assert "src/nyxloom/x.py" in err


def test_oracle_base_equals_head_exits_3(tmp_path, capsys):
    repo = _init_feature_repo(tmp_path)  # no further commits: feature == main == HEAD
    cov = _write_cov(tmp_path, {})
    rc = cg.main(["--repo", str(repo), "--coverage-json", cov])
    err = capsys.readouterr().err
    assert rc == 3
    assert "IS HEAD" in err


def test_oracle_legitimate_0_of_0_docs_only_change_still_passes_THE_FALSE_FAIL_GUARD(
    tmp_path, capsys,
):
    """The most important negative in the package: a committed docs-only
    change on a clean tree is a real, non-degenerate delta that happens to
    touch zero source lines. It must still exit 0 with the ordinary OK
    message -- this package must not turn every docs-only commit into a
    false NO MEASUREMENT."""
    repo = _init_feature_repo(tmp_path)
    _advance_feature(repo, "README.md", "hello v2\n")  # committed, clean tree
    cov = _write_cov(tmp_path, {"src/nyxloom/x.py": {"executed_lines": [], "missing_lines": []}})
    rc = cg.main(["--repo", str(repo), "--coverage-json", cov])
    out = capsys.readouterr().out
    assert rc == 0
    assert out == "diff-coverage OK: 0/0 changed executable lines covered (100.0% ≥ 100.0% floor)\n"


def test_oracle_normal_pass_and_fail_are_unchanged_SNAPSHOT(tmp_path, capsys):
    """Normal pass/fail wiring, on a real committed clean tree, is exactly
    what it was before this package -- pinned so the NO MEASUREMENT guard
    can never regress it."""
    repo = _init_feature_repo(tmp_path)
    _advance_feature(repo, "src/nyxloom/x.py", "a = 2\n")

    cov_pass = _write_cov(tmp_path, {"src/nyxloom/x.py": {
        "executed_lines": [1], "missing_lines": [], "excluded_lines": []}})
    rc_pass = cg.main(["--repo", str(repo), "--coverage-json", cov_pass])
    out_pass = capsys.readouterr().out
    assert rc_pass == 0
    assert out_pass == "diff-coverage OK: 1/1 changed executable lines covered (100.0% ≥ 100.0% floor)\n"

    cov_fail = _write_cov(tmp_path, {"src/nyxloom/x.py": {
        "executed_lines": [], "missing_lines": [1], "excluded_lines": []}})
    rc_fail = cg.main(["--repo", str(repo), "--coverage-json", cov_fail])
    out_fail = capsys.readouterr().out
    assert rc_fail == 1
    assert out_fail == (
        "diff-coverage FAIL: 0/1 changed executable lines covered "
        "(0.0% < 100.0% floor). Uncovered changed lines:\n"
        "  src/nyxloom/x.py: [1]\n"
        "Add a test that exercises these lines.\n"
    )
