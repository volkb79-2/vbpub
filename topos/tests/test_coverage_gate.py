"""Tests for the P96 mechanical diff-coverage gate (tools/coverage_gate.py).

Non-hollow by construction: the gate requires every added executable line of
coverage_gate.py to be covered, so each test below drives a real observable
behavior. Negatives prove the pass/fail branches discriminate correctly.
"""

from __future__ import annotations

import json

import pytest

from tools import coverage_gate as cg


# --------------------------------------------------------------------------- #
# parse_added_lines — the added-line hunk walk
# --------------------------------------------------------------------------- #

def test_parse_added_lines_multi_hunk_multi_file_and_counts():
    diff = (
        "diff --git a/src/topos/foo.py b/src/topos/foo.py\n"
        "--- a/src/topos/foo.py\n"
        "+++ b/src/topos/foo.py\n"
        "@@ -10,0 +11,3 @@ def existing():\n"
        "+    a = 1\n"
        "+    b = 2\n"
        "+    c = 3\n"
        "@@ -20,1 +25 @@ def other():\n"
        "+    d = 4\n"
        "diff --git a/src/topos/bar.py b/src/topos/bar.py\n"
        "--- a/src/topos/bar.py\n"
        "+++ b/src/topos/bar.py\n"
        "@@ -1,0 +2,1 @@\n"
        "+    only = 1\n"
    )
    assert cg.parse_added_lines(diff) == {
        "src/topos/foo.py": {11, 12, 13, 25},
        "src/topos/bar.py": {2},
    }


def test_parse_added_lines_ignores_deletions_and_deleted_files():
    diff = (
        "--- a/src/topos/foo.py\n"
        "+++ b/src/topos/foo.py\n"
        "@@ -5,2 +5,1 @@\n"
        "-    removed_one\n"
        "-    removed_two\n"
        "+    replacement\n"
        "diff --git a/src/topos/gone.py b/src/topos/gone.py\n"
        "--- a/src/topos/gone.py\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-    a\n"
        "-    b\n"
    )
    result = cg.parse_added_lines(diff)
    assert result == {"src/topos/foo.py": {5}}
    assert "gone.py" not in " ".join(result)


def test_parse_added_lines_advances_over_context_lines():
    diff = (
        "+++ b/src/topos/foo.py\n"
        "@@ -10,3 +10,4 @@\n"
        " ctx_at_10\n"
        " ctx_at_11\n"
        "+    added_at_12\n"
        " ctx_at_13\n"
    )
    assert cg.parse_added_lines(diff) == {"src/topos/foo.py": {12}}


def test_parse_added_lines_strips_b_prefix_only():
    diff = "+++ src/topos/noprefix.py\n@@ -0,0 +1 @@\n+x\n"
    assert cg.parse_added_lines(diff) == {"src/topos/noprefix.py": {1}}


# --------------------------------------------------------------------------- #
# _rel_to_source — path normalization
# --------------------------------------------------------------------------- #

def test_rel_to_source_finds_prefix_and_passes_through_when_absent():
    assert cg._rel_to_source("topos/src/topos/x.py", "topos/src/topos") == "topos/src/topos/x.py"
    assert cg._rel_to_source("/abs/topos/src/topos/x.py", "topos/src/topos") == "topos/src/topos/x.py"
    assert cg._rel_to_source("tests/test_x.py", "topos/src/topos") == "tests/test_x.py"


# --------------------------------------------------------------------------- #
# evaluate — the pure heart
# --------------------------------------------------------------------------- #

def _cov(executed, missing):
    return {"executed_lines": list(executed), "missing_lines": list(missing)}


def test_evaluate_uncovered_changed_line_fails():
    added = {"topos/src/topos/foo.py": {11, 12}}
    coverage = {"topos/src/topos/foo.py": _cov(executed=[11], missing=[12])}
    v = cg.evaluate(added, coverage)
    assert not v.passed
    assert v.uncovered == {"topos/src/topos/foo.py": {12}}
    assert (v.changed_executable, v.covered) == (2, 1)
    assert v.pct == 50.0


def test_evaluate_all_changed_lines_covered_passes():
    added = {"topos/src/topos/foo.py": {11, 12}}
    coverage = {"topos/src/topos/foo.py": _cov(executed=[11, 12], missing=[])}
    v = cg.evaluate(added, coverage)
    assert v.passed and not v.uncovered and v.pct == 100.0


def test_evaluate_ignores_non_python_files_under_source():
    added = {"topos/src/topos/schemas/config.json": {79, 80}}
    v = cg.evaluate(added, {})
    assert v.passed
    assert v.uncovered == {}
    assert v.files_missing_coverage == []
    assert v.changed_executable == 0


def test_evaluate_unmeasured_python_file_fails():
    added = {"topos/src/topos/brand_new.py": {1, 2}}
    v = cg.evaluate(added, {})
    assert not v.passed
    assert v.uncovered == {"topos/src/topos/brand_new.py": {1, 2}}
    assert v.files_missing_coverage == ["topos/src/topos/brand_new.py"]


def test_evaluate_ignores_non_executable_changed_lines():
    added = {"topos/src/topos/foo.py": {30}}
    coverage = {"topos/src/topos/foo.py": _cov(executed=[11], missing=[12])}
    v = cg.evaluate(added, coverage)
    assert v.passed and not v.uncovered
    assert v.changed_executable == 0

    coverage_missing = {"topos/src/topos/foo.py": _cov(executed=[11], missing=[12, 30])}
    v2 = cg.evaluate(added, coverage_missing)
    assert not v2.passed and v2.uncovered == {"topos/src/topos/foo.py": {30}}


def test_evaluate_fail_under_floor():
    added = {"topos/src/topos/foo.py": set(range(1, 21))}
    coverage = {"topos/src/topos/foo.py": _cov(executed=list(range(1, 20)), missing=[20])}
    assert cg.evaluate(added, coverage, fail_under=100.0).passed is False
    v90 = cg.evaluate(added, coverage, fail_under=90.0)
    assert v90.passed is True
    assert v90.uncovered == {"topos/src/topos/foo.py": {20}}


def test_evaluate_empty_diff_is_clean_pass():
    v = cg.evaluate({}, {"topos/src/topos/foo.py": _cov([], [12])})
    assert v.passed and v.pct == 100.0 and v.changed_executable == 0


def test_evaluate_ignores_changes_outside_source_prefix():
    added = {"tests/test_foo.py": {5, 6}, "docs/spec.md": {1}}
    coverage = {"tests/test_foo.py": _cov(executed=[], missing=[5, 6])}
    v = cg.evaluate(added, coverage)
    assert v.passed and not v.uncovered and v.changed_executable == 0


def test_evaluate_matches_across_path_spellings():
    added = {"topos/src/topos/x.py": {7}}
    coverage = {"topos/src/topos/x.py": _cov(executed=[], missing=[7])}
    v = cg.evaluate(added, coverage)
    assert not v.passed and v.uncovered == {"topos/src/topos/x.py": {7}}
    coverage_ok = {"topos/src/topos/x.py": _cov(executed=[7], missing=[])}
    assert cg.evaluate(added, coverage_ok).passed


def test_verdict_passed_property():
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
        assert args[0] == "rev-list"
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
        return "+++ b/topos/src/topos/x.py\n@@ -0,0 +1,2 @@\n+a\n+b\n"
    monkeypatch.setattr(cg, "_git", fake_git)
    out = cg._git_added_lines(".", "BASE", "topos/src/topos")
    assert out == {"topos/src/topos/x.py": {1, 2}}
    assert captured["args"] == [
        "diff", "--relative", "--unified=0", "BASE", "HEAD", "--", "topos/src/topos"
    ]


def test_load_coverage_reads_files_object(tmp_path):
    p = tmp_path / "cov.json"
    p.write_text(json.dumps({"files": {"topos/src/topos/x.py": {"executed_lines": [1]}}}))
    assert cg._load_coverage(str(p)) == {"topos/src/topos/x.py": {"executed_lines": [1]}}


def test_load_coverage_raises_on_missing_file_and_bad_shape(tmp_path):
    with pytest.raises(cg.CoverageGateError):
        cg._load_coverage(str(tmp_path / "nope.json"))
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"no_files_key": True}))
    with pytest.raises(cg.CoverageGateError):
        cg._load_coverage(str(bad))


# --------------------------------------------------------------------------- #
# main — CLI wiring
# --------------------------------------------------------------------------- #

def _write_cov(tmp_path, files):
    p = tmp_path / "cov.json"
    p.write_text(json.dumps({"files": files}))
    return str(p)


def test_main_pass_returns_0_and_prints_ok(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cg, "_resolve_base", lambda repo, base: "BASE")
    monkeypatch.setattr(cg, "_git_added_lines", lambda repo, base, source: {"topos/src/topos/x.py": {5}})
    cov = _write_cov(tmp_path, {"topos/src/topos/x.py": {"executed_lines": [5], "missing_lines": []}})
    rc = cg.main(["--coverage-json", cov])
    assert rc == 0
    assert "diff-coverage OK" in capsys.readouterr().out


def test_main_fail_returns_1_and_lists_uncovered(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cg, "_resolve_base", lambda repo, base: "BASE")
    monkeypatch.setattr(cg, "_git_added_lines", lambda repo, base, source: {"topos/src/topos/x.py": {5, 6}})
    cov = _write_cov(tmp_path, {"topos/src/topos/x.py": {"executed_lines": [5], "missing_lines": [6]}})
    rc = cg.main(["--coverage-json", cov, "--fail-under", "100"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "diff-coverage FAIL" in out and "topos/src/topos/x.py" in out and "[6]" in out


def test_main_unmeasured_file_tag_is_shown(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cg, "_resolve_base", lambda repo, base: "BASE")
    monkeypatch.setattr(cg, "_git_added_lines", lambda repo, base, source: {"topos/src/topos/orphan.py": {3}})
    cov = _write_cov(tmp_path, {})
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
        "main", "topos/src/topos", 100.0, ".")
