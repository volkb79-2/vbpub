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
from assay.verdict import Judgment, JudgmentR1

ADAPTER = FakeAdapter()


#: The placeholder every hand-written fixture spells `judgment.r1.base` as.
#: A real `base_rev` is a genuine git commit SHA and therefore NOT
#: deterministic across test runs (``GitRepo.commit_all`` does not pin
#: ``GIT_AUTHOR_DATE``) -- each test asserts the REAL value independently,
#: then normalizes the field to this placeholder before the full-document
#: comparison against the fixture, the same "hand-computed fact checked
#: separately, never smuggled into the literal comparison" discipline
#: `test_verdict_conformance.py` already applies to `argv_effective`.
FIXTURE_BASE_PLACEHOLDER = "0000000000000000000000000000000000000a"


def _r1_judgment(judge, base_rev: str) -> Judgment:
    """The effective R1 policy an independent consumer needs (P16) --
    derived from the same `judge`/`base_rev` each test already resolved,
    never a second hardcoded copy of the lane's own declaration.
    `source_roots` is deliberately the REPO-RELATIVE spelling a real
    ``assay.toml`` would declare (``pkg``), not `judge.source_root_paths`'s
    resolved absolute form -- `make_r1_judge`'s own `.source_roots` field
    is a test-helper shortcut that stores the same absolute, tmp_path-
    derived string as `.source_root_paths`, which would make this field
    non-deterministic across runs too."""
    return Judgment(
        r1=JudgmentR1(
            language=judge.language,
            source_roots=("pkg",),
            coverage_format=judge.coverage.format,
            coverage_artifact=judge.coverage.artifact,
            fail_under=judge.fail_under,
            allow_excluded=judge.allow_excluded,
            base=base_rev,
        )
    )


def _normalize_judgment_base(document: dict, base_rev: str) -> None:
    """Assert the REAL resolved base commit was recorded, then normalize it
    to the fixture's own fixed placeholder so the surrounding full-document
    comparison stays possible against a hand-written, stable JSON file."""
    assert document["judgment"]["r1"]["base"] == base_rev
    document["judgment"]["r1"]["base"] = FIXTURE_BASE_PLACEHOLDER


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
        judgment=_r1_judgment(judge, base_rev),
    )

    document = json.loads(verdict.to_json())
    _normalize_judgment_base(document, base_rev)
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
        judgment=_r1_judgment(judge, base_rev),
    )

    document = json.loads(verdict.to_json())
    _normalize_judgment_base(document, base_rev)
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
        judgment=_r1_judgment(judge, base_rev),
    )

    document = json.loads(verdict.to_json())
    _normalize_judgment_base(document, base_rev)
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
        judgment=_r1_judgment(judge, base_rev),
    )
    document = json.loads(verdict.to_json())
    _normalize_judgment_base(document, base_rev)

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


# --- structural: never raises for ANY AssayError from its own guard sequence -
# (P17 work item 6: GIT_FAILED, FORMAT_MISMATCH and UNREADABLE_ARTIFACT join
# the three pre-existing NO_MEASUREMENT causes as complete R1 claims, closing
# three of sol's "permanently unreachable" reason-code pairs.)


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


def test_evaluate_r1_renders_format_mismatch_as_a_complete_claim(git_repo: GitRepo):
    """A malformed coverage artifact (FORMAT_MISMATCH, an ERROR outcome) is
    now a JUDGED terminal path (work item 6), not a propagating exception --
    the whole point being that a consumer polling for an artifact gets one
    even here."""
    base_rev, head_rev = _seed_two_commits(git_repo)
    (git_repo.path / "cov.json").write_text("not json at all", encoding="utf-8")
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",))
    lane = make_lane(rigor=("R0", "R1"), judge=judge)

    claim = runner.evaluate_r1(
        lane,
        repo=git_repo.path,
        project_root=git_repo.path,
        base=base_rev,
        adapter=ADAPTER,
    )
    assert claim.status is Outcome.ERROR
    assert claim.reason_code is ReasonCode.FORMAT_MISMATCH
    assert claim.coverage is None


def test_evaluate_r1_renders_empty_coverage_as_a_complete_claim_when_the_artifact_is_missing(
    git_repo: GitRepo,
):
    """No coverage artifact at the declared path at all -- work item 4's
    "a command that writes nothing" -- is P20/A-174's truthful
    NO_MEASUREMENT/EMPTY_COVERAGE (the same cause a well-formed-but-empty
    artifact already renders, since in both cases there is nothing to
    measure), ALSO a judged terminal path, not a propagating exception."""
    base_rev, head_rev = _seed_two_commits(git_repo)
    # No cov.json written at all.
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


def test_evaluate_r1_renders_git_failed_as_a_complete_claim(git_repo: GitRepo):
    """A *base* that does not resolve at all (distinct from BASE_IS_HEAD's
    "resolves, but equals HEAD") is GIT_FAILED -- reachable through
    :func:`assay.git.resolve_base`'s own ``merge-base`` call, which fails
    outright for a ref that does not exist, rather than through
    ``check_base_is_head``'s own guard."""
    git_repo.write("pkg/mod.zzz", "BASE\n")
    git_repo.commit_all("add pkg base")
    write_coverage_json(git_repo.path / "cov.json", {"pkg/mod.zzz": {"executed_lines": [1]}})
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",))
    lane = make_lane(rigor=("R0", "R1"), judge=judge)

    claim = runner.evaluate_r1(
        lane,
        repo=git_repo.path,
        project_root=git_repo.path,
        base="this-ref-does-not-exist-anywhere",
        adapter=ADAPTER,
    )
    assert claim.status is Outcome.ERROR
    assert claim.reason_code is ReasonCode.GIT_FAILED
    assert claim.coverage is None


def test_evaluate_r1_still_propagates_a_genuine_programmer_error(git_repo: GitRepo):
    """Only a non-:class:`AssayError` exception still propagates -- work
    item 6's "do not catch programmer errors": a broken adapter raising
    something else entirely is not a judged outcome and must not be
    laundered into any claim."""
    base_rev, head_rev = _seed_two_commits(git_repo)
    write_coverage_json(
        git_repo.path / "cov.json", {"pkg/mod.zzz": {"executed_lines": [2, 3, 4, 5]}}
    )
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",))
    lane = make_lane(rigor=("R0", "R1"), judge=judge)

    @dataclass(frozen=True, kw_only=True)
    class _BrokenAdapter:
        name: str = "broken"
        source_globs: tuple[str, ...] = ("*.zzz",)
        excluded_dir_names: frozenset[str] = frozenset()
        requires_span_attribution: bool = False
        external_tools: tuple[str, ...] = ()

        def is_test_path(self, rel_path: str) -> bool:
            return False

        def has_executable_code(self, rel_path: str, text: str) -> bool:
            return True

        def normalize_coverage_key(self, key: str) -> str:
            raise RuntimeError("a real programmer error, not an AssayError")

    with pytest.raises(RuntimeError, match="a real programmer error"):
        runner.evaluate_r1(
            lane,
            repo=git_repo.path,
            project_root=git_repo.path,
            base=base_rev,
            adapter=_BrokenAdapter(),
        )


# --- on_base_resolved (P17): fires once, exactly when the base guard clears --


def test_on_base_resolved_fires_once_with_the_real_resolved_base(git_repo: GitRepo):
    base_rev, head_rev = _seed_two_commits(git_repo)
    write_coverage_json(
        git_repo.path / "cov.json", {"pkg/mod.zzz": {"executed_lines": [2, 3, 4, 5]}}
    )
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",))
    lane = make_lane(rigor=("R0", "R1"), judge=judge)
    seen: list[str] = []

    claim = runner.evaluate_r1(
        lane,
        repo=git_repo.path,
        project_root=git_repo.path,
        base=base_rev,
        adapter=ADAPTER,
        on_base_resolved=seen.append,
    )

    assert claim.status is Outcome.PASS
    assert seen == [base_rev]


def test_on_base_resolved_never_fires_when_the_base_is_head_guard_trips(
    git_repo: GitRepo,
):
    base_rev, head_rev = _seed_two_commits(git_repo)
    write_coverage_json(
        git_repo.path / "cov.json", {"pkg/mod.zzz": {"executed_lines": [2, 3, 4, 5]}}
    )
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",))
    lane = make_lane(rigor=("R0", "R1"), judge=judge)
    seen: list[str] = []

    claim = runner.evaluate_r1(
        lane,
        repo=git_repo.path,
        project_root=git_repo.path,
        base=head_rev,
        adapter=ADAPTER,
        on_base_resolved=seen.append,
    )

    assert claim.status is Outcome.NO_MEASUREMENT
    assert claim.reason_code is ReasonCode.BASE_IS_HEAD
    assert seen == []


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


# --- P20 work item 5: an expected source-read failure is a complete claim -----


def test_evaluate_r1_renders_unreadable_artifact_for_a_source_read_failure(
    git_repo: GitRepo, monkeypatch: pytest.MonkeyPatch
):
    """A changed, considered file with NO coverage-artifact entry is read
    from disk to answer ``has_executable_code`` -- an ``OSError`` there must
    land inside a complete R1 claim (``ERROR``/``UNREADABLE_ARTIFACT``),
    never propagate past ``evaluate_r1`` uncaught.

    The failure is injected at the filesystem boundary (AUTHORING.md §3b.E)
    rather than reproduced with a real broken file: making the committed
    content itself undecodable would make ``git diff`` fail first (a
    different, already-covered branch, since git's own diff output embeds
    that content), and a permission-revoking chmod turned out to dirty
    ``git status`` itself (verified directly against a real git binary --
    revoking read access changes the file's ctime, which git's own
    quick-comparison heuristic treats as a potential modification). Neither
    reproduces "the file is fine as far as git is concerned, but this
    process cannot read it right now" in isolation, so the boundary is
    mocked instead, scoped to exactly the one path under test.
    """
    git_repo.write("pkg/mod.zzz", "BASE\n")
    base_rev = git_repo.commit_all("add pkg base")
    git_repo.write("pkg/unreadable.zzz", "def real(): return 1\n")
    head_rev = git_repo.commit_all("add a file that will fail to read")
    # A dummy entry keeps the artifact non-empty (clearing check_empty_coverage)
    # without naming "pkg/unreadable.zzz" -- read_source_text must be reached
    # to answer has_executable_code for it.
    write_coverage_json(git_repo.path / "cov.json", {"pkg/mod.zzz": {}})

    real_read_text = Path.read_text
    target = (git_repo.path / "pkg" / "unreadable.zzz").resolve()

    def flaky_read_text(self: Path, *args, **kwargs):
        if self.resolve() == target:
            raise PermissionError(13, "Permission denied", str(self))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky_read_text)

    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",))
    lane = make_lane(rigor=("R0", "R1"), judge=judge)

    claim = runner.evaluate_r1(
        lane,
        repo=git_repo.path,
        project_root=git_repo.path,
        base=base_rev,
        adapter=ADAPTER,
    )

    assert claim.status is Outcome.ERROR
    assert claim.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert claim.coverage is None
