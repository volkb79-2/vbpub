"""O3/O4 — runner integration: :func:`assay.runner.evaluate_r1`, wired ahead
of the four-way union behind P02's two measurability guards and P03's
``EMPTY_COVERAGE`` guard (A-090), producing a schema-valid R1
:class:`~assay.verdict.Claim` that a REAL :func:`assay.runner.execute_command`
+ :func:`assay.runner.assemble_verdict` pipeline turns into a full verdict —
compared, by ordinary JSON loading, against an INDEPENDENTLY hand-written
fixture (O3's negative: *a universal PASS evaluator, rounded threshold
comparison, or producer/schema drift fails an independent full-artifact
comparison*).

O4's three ``NO_MEASUREMENT`` branches are proven the same way: real git
state materialised under ``tmp_path`` (dirty tree, ``base == HEAD``) and a
real, well-formed-but-empty coverage artifact, each rendering the SAME shape
as O3's PASS/FAIL cases — an R0 claim, an R1 claim, one verdict — with the
coverage block OMITTED, never zeroed (A-025), matching the hand-written
fixture's own omission of the ``coverage`` key entirely.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import (
    FakeAdapter,
    GitRepo,
    fixed_clock,
    make_lane,
    make_r1_judge,
    r1_verdict_fixture,
    write_coverage_json,
)
from jsonschema import Draft202012Validator

from assay import runner
from assay.adapters.base import StatementSpan
from assay.errors import AssayError, Outcome, ReasonCode

ADAPTER = FakeAdapter()


@dataclass(frozen=True, kw_only=True)
class _UnparsableSpanAdapter:
    """A synthetic adapter whose ``statement_spans`` always reports the file
    as unparseable (``None``) — the shortest real path to a genuinely
    unclassified line, used ONLY to prove
    :func:`assay.runner.evaluate_r1`'s ``Coverage(...)`` call site actually
    wires ``unclassified_lines``/``files_with_unclassified_lines`` through
    end to end (controller repair after P07: the original call site
    silently dropped both new fields even though
    :func:`assay.evaluate.evaluate_coverage` computed them correctly)."""

    name: str = "unparsable"
    source_globs: tuple[str, ...] = ("*.zzz",)
    excluded_dir_names: frozenset[str] = frozenset()
    requires_span_attribution: bool = True
    external_tools: tuple[str, ...] = ()

    def is_test_path(self, rel_path: str) -> bool:
        return False

    def has_executable_code(self, rel_path: str, text: str) -> bool:
        return True

    def normalize_coverage_key(self, key: str) -> str:
        return key

    def statement_spans(self, text: str) -> tuple[StatementSpan, ...] | None:
        return None


def _seed_two_commits(repo: GitRepo) -> tuple[str, str]:
    """Commit ``pkg/mod.zzz`` with 1 line, then again with 4 lines appended
    (new-side added lines 2, 3, 4, 5 -- verified against a real ``git diff``
    while authoring the fixtures below)."""
    repo.write("pkg/mod.zzz", "BASE\n")
    base_rev = repo.commit_all("add pkg base")
    repo.write("pkg/mod.zzz", "BASE\nLINE2\nLINE3\nLINE4\nLINE5\n")
    head_rev = repo.commit_all("add pkg head")
    return base_rev, head_rev


def _validate(document: dict, validator: Draft202012Validator) -> None:
    from conftest import why_invalid

    assert why_invalid(validator, document) == []


def test_r1_pass_matches_the_hand_written_fixture(
    git_repo: GitRepo, validator: Draft202012Validator
):
    base_rev, head_rev = _seed_two_commits(git_repo)
    write_coverage_json(
        git_repo.path / "cov.json",
        {"pkg/mod.zzz": {"executed_lines": [2, 3, 4, 5]}},
    )
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",))
    lane = make_lane(rigor=("R0", "R1"), judge=judge)
    clock = fixed_clock(
        datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 7, 12, 0, 1, tzinfo=timezone.utc),
    )

    result = runner.execute_command(lane, cwd=git_repo.path, clock=clock)
    r0_claim = runner.build_r0_claim(result)
    r1_claim = runner.evaluate_r1(
        lane,
        repo=git_repo.path,
        project_root=git_repo.path,
        base=base_rev,
        adapter=ADAPTER,
    )
    verdict = runner.assemble_verdict(
        lane=lane,
        commit="6" * 40,
        result=result,
        claims=(r0_claim, r1_claim),
        assay_version="0.1.0",
    )

    document = json.loads(verdict.to_json())
    assert document == r1_verdict_fixture("r1_pass")
    _validate(document, validator)


def test_r1_fail_uncovered_lines_matches_the_hand_written_fixture(
    git_repo: GitRepo, validator: Draft202012Validator
):
    base_rev, head_rev = _seed_two_commits(git_repo)
    write_coverage_json(
        git_repo.path / "cov.json",
        {"pkg/mod.zzz": {"executed_lines": [2, 3], "missing_lines": [4, 5]}},
    )
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",))
    lane = make_lane(rigor=("R0", "R1"), judge=judge)
    clock = fixed_clock(
        datetime(2026, 8, 7, 12, 5, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 7, 12, 5, 1, tzinfo=timezone.utc),
    )

    result = runner.execute_command(lane, cwd=git_repo.path, clock=clock)
    r0_claim = runner.build_r0_claim(result)
    r1_claim = runner.evaluate_r1(
        lane,
        repo=git_repo.path,
        project_root=git_repo.path,
        base=base_rev,
        adapter=ADAPTER,
    )
    verdict = runner.assemble_verdict(
        lane=lane,
        commit="7" * 40,
        result=result,
        claims=(r0_claim, r1_claim),
        assay_version="0.1.0",
    )

    document = json.loads(verdict.to_json())
    assert document == r1_verdict_fixture("r1_fail_uncovered_lines")
    _validate(document, validator)


def test_r1_fail_excluded_lines_matches_the_hand_written_fixture(
    git_repo: GitRepo, validator: Draft202012Validator
):
    base_rev, head_rev = _seed_two_commits(git_repo)
    write_coverage_json(
        git_repo.path / "cov.json",
        {"pkg/mod.zzz": {"executed_lines": [2, 3, 4], "excluded_lines": [5]}},
    )
    judge = make_r1_judge(
        source_root_paths=(git_repo.path / "pkg",), allow_excluded=False
    )
    lane = make_lane(rigor=("R0", "R1"), judge=judge)
    clock = fixed_clock(
        datetime(2026, 8, 7, 12, 10, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 7, 12, 10, 1, tzinfo=timezone.utc),
    )

    result = runner.execute_command(lane, cwd=git_repo.path, clock=clock)
    r0_claim = runner.build_r0_claim(result)
    r1_claim = runner.evaluate_r1(
        lane,
        repo=git_repo.path,
        project_root=git_repo.path,
        base=base_rev,
        adapter=ADAPTER,
    )
    verdict = runner.assemble_verdict(
        lane=lane,
        commit="8" * 40,
        result=result,
        claims=(r0_claim, r1_claim),
        assay_version="0.1.0",
    )

    document = json.loads(verdict.to_json())
    assert document == r1_verdict_fixture("r1_fail_excluded_lines")
    _validate(document, validator)


def test_r1_no_measurement_dirty_tree_matches_the_hand_written_fixture(
    git_repo: GitRepo, validator: Draft202012Validator
):
    base_rev, head_rev = _seed_two_commits(git_repo)
    write_coverage_json(
        git_repo.path / "cov.json",
        {"pkg/mod.zzz": {"executed_lines": [2, 3, 4, 5]}},
    )
    # An UNCOMMITTED change under the source root -- the guard's own subject.
    (git_repo.path / "pkg" / "mod.zzz").write_text("BASE\nLINE2\nLINE3\nLINE4\nLINE5\nUNCOMMITTED\n")

    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",))
    lane = make_lane(rigor=("R0", "R1"), judge=judge)
    clock = fixed_clock(
        datetime(2026, 8, 7, 12, 15, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 7, 12, 15, 1, tzinfo=timezone.utc),
    )

    result = runner.execute_command(lane, cwd=git_repo.path, clock=clock)
    r0_claim = runner.build_r0_claim(result)
    r1_claim = runner.evaluate_r1(
        lane,
        repo=git_repo.path,
        project_root=git_repo.path,
        base=base_rev,
        adapter=ADAPTER,
    )
    verdict = runner.assemble_verdict(
        lane=lane,
        commit="9" * 40,
        result=result,
        claims=(r0_claim, r1_claim),
        assay_version="0.1.0",
    )

    document = json.loads(verdict.to_json())
    assert document == r1_verdict_fixture("r1_no_measurement_dirty_tree")
    assert "coverage" not in document["claims"][1]
    _validate(document, validator)


def test_r1_no_measurement_base_is_head_matches_the_hand_written_fixture(
    git_repo: GitRepo, validator: Draft202012Validator
):
    base_rev, head_rev = _seed_two_commits(git_repo)
    write_coverage_json(
        git_repo.path / "cov.json",
        {"pkg/mod.zzz": {"executed_lines": [2, 3, 4, 5]}},
    )
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",))
    lane = make_lane(rigor=("R0", "R1"), judge=judge)
    clock = fixed_clock(
        datetime(2026, 8, 7, 12, 20, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 7, 12, 20, 1, tzinfo=timezone.utc),
    )

    result = runner.execute_command(lane, cwd=git_repo.path, clock=clock)
    r0_claim = runner.build_r0_claim(result)
    # base = HEAD's own revision -- resolve_base's merge-base(HEAD, HEAD) is
    # HEAD itself, so the guard trips deterministically with no merge commit
    # required.
    r1_claim = runner.evaluate_r1(
        lane,
        repo=git_repo.path,
        project_root=git_repo.path,
        base=head_rev,
        adapter=ADAPTER,
    )
    verdict = runner.assemble_verdict(
        lane=lane,
        commit="a" * 40,
        result=result,
        claims=(r0_claim, r1_claim),
        assay_version="0.1.0",
    )

    document = json.loads(verdict.to_json())
    assert document == r1_verdict_fixture("r1_no_measurement_base_is_head")
    assert "coverage" not in document["claims"][1]
    _validate(document, validator)


def test_r1_no_measurement_empty_coverage_matches_the_hand_written_fixture(
    git_repo: GitRepo, validator: Draft202012Validator
):
    base_rev, head_rev = _seed_two_commits(git_repo)
    write_coverage_json(git_repo.path / "cov.json", {})  # zero measured files

    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",))
    lane = make_lane(rigor=("R0", "R1"), judge=judge)
    clock = fixed_clock(
        datetime(2026, 8, 7, 12, 25, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 7, 12, 25, 1, tzinfo=timezone.utc),
    )

    result = runner.execute_command(lane, cwd=git_repo.path, clock=clock)
    r0_claim = runner.build_r0_claim(result)
    r1_claim = runner.evaluate_r1(
        lane,
        repo=git_repo.path,
        project_root=git_repo.path,
        base=base_rev,
        adapter=ADAPTER,
    )
    verdict = runner.assemble_verdict(
        lane=lane,
        commit="b" * 40,
        result=result,
        claims=(r0_claim, r1_claim),
        assay_version="0.1.0",
    )

    document = json.loads(verdict.to_json())
    assert document == r1_verdict_fixture("r1_no_measurement_empty_coverage")
    assert "coverage" not in document["claims"][1]
    _validate(document, validator)


# --- vacuity guard: a constant-PASS evaluator would fail the comparison -------


def test_a_hard_coded_constant_pass_would_fail_the_fail_fixture_comparison(
    git_repo: GitRepo,
):
    base_rev, head_rev = _seed_two_commits(git_repo)
    write_coverage_json(
        git_repo.path / "cov.json",
        {"pkg/mod.zzz": {"executed_lines": [2, 3], "missing_lines": [4, 5]}},
    )
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",))
    lane = make_lane(rigor=("R0", "R1"), judge=judge)
    clock = fixed_clock(
        datetime(2026, 8, 7, 12, 5, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 7, 12, 5, 1, tzinfo=timezone.utc),
    )
    result = runner.execute_command(lane, cwd=git_repo.path, clock=clock)
    r0_claim = runner.build_r0_claim(result)
    r1_claim = runner.evaluate_r1(
        lane,
        repo=git_repo.path,
        project_root=git_repo.path,
        base=base_rev,
        adapter=ADAPTER,
    )
    verdict = runner.assemble_verdict(
        lane=lane,
        commit="7" * 40,
        result=result,
        claims=(r0_claim, r1_claim),
        assay_version="0.1.0",
    )
    document = json.loads(verdict.to_json())

    # Simulate "the producer emits a constant PASS": force outcome/reason to
    # PASS regardless of the real (50%, fail_under=100) result.
    document["outcome"] = "PASS"
    del document["reason_code"]
    document["exit_code"] = 0
    document["claims"][1]["status"] = "PASS"
    del document["claims"][1]["reason_code"]
    del document["claims"][1]["coverage"]

    assert document != r1_verdict_fixture("r1_fail_uncovered_lines")


def test_evaluate_r1_reads_real_file_text_for_a_file_missing_from_coverage(
    git_repo: GitRepo,
):
    """The one part of evaluate_r1 no other test here exercises: a changed,
    considered file with NO entry in the coverage artifact is read from the
    REAL working tree (not a fake) to answer has_executable_code -- proving
    the injected read_source_text closure actually reaches the filesystem,
    the same real boundary check.check_dirty_tree already guarantees is
    clean before this ever runs."""
    git_repo.write("pkg/mod.zzz", "BASE\n")
    base_rev = git_repo.commit_all("add pkg base")
    git_repo.write("pkg/newfile.zzz", "def real(): return 1\n")
    head_rev = git_repo.commit_all("add pkg newfile")

    write_coverage_json(git_repo.path / "cov.json", {"pkg/mod.zzz": {}})
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",))
    lane = make_lane(rigor=("R0", "R1"), judge=judge)

    claim = runner.evaluate_r1(
        lane,
        repo=git_repo.path,
        project_root=git_repo.path,
        base=base_rev,
        adapter=ADAPTER,
    )
    assert claim.status is Outcome.FAIL
    assert claim.reason_code is ReasonCode.UNCOVERED_LINES
    assert claim.coverage.files_missing_coverage == ("pkg/newfile.zzz",)
    assert claim.coverage.missing_lines == {"pkg/newfile.zzz": frozenset({1})}


# --- structural: never raises for a JUDGED outcome; propagates a real error ---


def test_evaluate_r1_never_raises_for_any_of_the_three_no_measurement_causes(
    git_repo: GitRepo,
):
    """Mirrors execute_command's own never-raises contract for a judged
    outcome (A-094's pattern, applied to R1)."""
    base_rev, head_rev = _seed_two_commits(git_repo)
    write_coverage_json(git_repo.path / "cov.json", {})
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",))
    lane = make_lane(rigor=("R0", "R1"), judge=judge)

    claim = runner.evaluate_r1(
        lane,
        repo=git_repo.path,
        project_root=git_repo.path,
        base=base_rev,
        adapter=ADAPTER,
    )
    assert claim.status is Outcome.NO_MEASUREMENT
    assert claim.reason_code is ReasonCode.EMPTY_COVERAGE
    assert claim.coverage is None


def test_evaluate_r1_propagates_a_genuinely_structural_failure(git_repo: GitRepo):
    """A malformed coverage artifact (FORMAT_MISMATCH, an ERROR outcome, not
    NO_MEASUREMENT) is NOT a judged outcome and must propagate, unlike the
    three guard branches above."""
    base_rev, head_rev = _seed_two_commits(git_repo)
    (git_repo.path / "cov.json").write_text("not json at all", encoding="utf-8")
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",))
    lane = make_lane(rigor=("R0", "R1"), judge=judge)

    with pytest.raises(AssayError) as excinfo:
        runner.evaluate_r1(
            lane,
            repo=git_repo.path,
            project_root=git_repo.path,
            base=base_rev,
            adapter=ADAPTER,
        )
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.FORMAT_MISMATCH


def test_evaluate_r1_wires_unclassified_lines_through_to_the_real_coverage_payload(
    git_repo: GitRepo,
):
    """Controller repair (post-P07 review): ``evaluate_r1``'s ``Coverage(...)``
    call site originally named only the original six keyword arguments, so a
    real run silently dropped ``unclassified_lines``/
    ``files_with_unclassified_lines`` even when ``evaluate_coverage`` found
    them — the claim's own ``status``/``reason_code`` were still correct,
    but the diagnostic "WHERE" payload was not. Line 3 is neither executed
    nor missing in the coverage artifact below, and the adapter reports the
    file as unparseable, so line 3 is genuinely unattributable."""
    base_rev, head_rev = _seed_two_commits(git_repo)
    write_coverage_json(
        git_repo.path / "cov.json",
        {"pkg/mod.zzz": {"executed_lines": [2], "missing_lines": [4, 5]}},
    )
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",))
    lane = make_lane(rigor=("R0", "R1"), judge=judge)

    r1_claim = runner.evaluate_r1(
        lane,
        repo=git_repo.path,
        project_root=git_repo.path,
        base=base_rev,
        adapter=_UnparsableSpanAdapter(),
    )

    assert r1_claim.status is Outcome.FAIL
    assert r1_claim.reason_code is ReasonCode.UNCLASSIFIED_LINES
    assert r1_claim.coverage.unclassified_lines == {"pkg/mod.zzz": frozenset({3})}
    assert r1_claim.coverage.files_with_unclassified_lines == ("pkg/mod.zzz",)
