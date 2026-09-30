"""B105's direct constructor controls for remaining whole-source branches."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import assay.verdict as models
from assay.errors import Outcome, ReasonCode
from assay.verdict import (
    CanaryAttempt,
    CanaryResult,
    Claim,
    Coverage,
    Helper,
    JudgeProvenance,
    Judgment,
    JudgmentR1,
    JudgmentR2,
    JudgmentR4,
    JudgmentResolved,
    Mutation,
    MutationExecution,
    MutationProducerTool,
    MutationWitnessReceipt,
    MutantOutcome,
    RedFirstResult,
    RefusalDetail,
    SnapshotPolicy,
    SourcePosition,
    Verdict,
    WorktreeIntegrity,
    refusal_detail,
)


def _attempt(**overrides):
    values = {
        "target": "src/check.py",
        "description": "bounded transform",
        "control_outcome": Outcome.PASS,
        "transformed_outcome": Outcome.FAIL,
        "expected_reason_code": ReasonCode.UNCOVERED_LINES,
        "observed_reason_code": ReasonCode.UNCOVERED_LINES,
    }
    values.update(overrides)
    return CanaryAttempt(**values)


def _base_verdict(**overrides):
    values = {
        "lane": "package",
        "commit": "b" * 40,
        "outcome": Outcome.PASS,
        "started": "2026-09-01T00:00:00+00:00",
        "ended": "2026-09-01T00:00:01+00:00",
        "assay_version": "7.1.0",
    }
    values.update(overrides)
    return Verdict(**values)


def _r0_claim():
    return Claim(
        rigor="R0",
        source="computed",
        status=Outcome.PASS,
        verified_by_assay=True,
    )


def _r1_verdict(**overrides):
    coverage = Coverage(
        covered=1,
        executable=1,
        pct=100.0,
        considered=1,
        exclusion_capability="reported",
        missing_lines={},
        files_missing_coverage=(),
    )
    r1_claim = Claim(
        rigor="R1",
        source="computed",
        status=Outcome.PASS,
        verified_by_assay=True,
        coverage=coverage,
    )
    judgment = Judgment(
        resolved=JudgmentResolved(
            language="python", source_roots=("src",), base="a" * 40
        ),
        r1=JudgmentR1(
            coverage_format="coverage-py-json",
            coverage_artifact="coverage.json",
            fail_under=100.0,
            allow_excluded=False,
        ),
    )
    values = {
        "claims": (_r0_claim(), r1_claim),
        "declared_rigor": ("R0", "R1"),
        "declared_evidence": (),
        "argv_declared": ("pytest", "-q"),
        "argv_appended": (),
        "argv_effective": ("pytest", "-q"),
        "env_declared": {},
        "env_effective": {},
        "scope": "S1",
        "enforcement": "gate",
        "judgment": judgment,
        "snapshot_policy": SnapshotPolicy(selection="repository"),
    }
    values.update(overrides)
    return _base_verdict(**values)


def _r4_verdict(
    *,
    policy_tests=("tests/test_fix.py",),
    result_tests=None,
    broken_commit="a" * 40,
    policy_commit=None,
    **overrides,
):
    actual_tests = policy_tests if result_tests is None else result_tests
    actual_commit = broken_commit if policy_commit is None else policy_commit
    red_first = RedFirstResult(
        broken_commit=broken_commit,
        tests=actual_tests,
        before_outcome=Outcome.FAIL,
        after_outcome=Outcome.PASS,
    )
    r4_claim = Claim(
        rigor="R4",
        source="computed",
        status=Outcome.PASS,
        verified_by_assay=True,
        red_first=red_first,
    )
    judgment = Judgment(
        resolved=JudgmentResolved(language="python", source_roots=("src",)),
        r4=JudgmentR4(
            tests=policy_tests,
            broken_commit=actual_commit,
            broken_commit_source="declared",
        ),
    )
    values = {
        "claims": (_r0_claim(), r4_claim),
        "declared_rigor": ("R0", "R4"),
        "declared_evidence": (),
        "argv_declared": ("pytest", "-q"),
        "argv_appended": (),
        "argv_effective": ("pytest", "-q"),
        "env_declared": {},
        "env_effective": {},
        "scope": "S1",
        "enforcement": "gate",
        "judgment": judgment,
        "snapshot_policy": SnapshotPolicy(selection="repository"),
    }
    values.update(overrides)
    return _base_verdict(**values)


def test_canary_attempt_accepts_all_honest_attempt_shapes():
    judged_failure = _attempt()
    unchanged_transform = _attempt(transformed_outcome=None, observed_reason_code=None)
    survived_transform = _attempt(
        transformed_outcome=Outcome.PASS,
        observed_reason_code=None,
    )
    skipped = CanaryAttempt(
        target="src/later.py",
        description="bounded transform",
        disposition="not_attempted",
        not_attempted_reason="short_circuited",
    )

    assert judged_failure.observed_reason_code is ReasonCode.UNCOVERED_LINES
    assert unchanged_transform.transformed_outcome is None
    assert survived_transform.transformed_outcome is Outcome.PASS
    assert skipped.not_attempted_reason == "short_circuited"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"description": ""}, "description must be a non-empty string"),
        ({"disposition": "skipped"}, "disposition must be one of"),
        (
            {"disposition": "not_attempted", "not_attempted_reason": "invented"},
            "requires a not_attempted_reason",
        ),
        ({"not_attempted_reason": "short_circuited"}, "on an attempted entry"),
        ({"control_outcome": "PASS"}, "control_outcome must be an Outcome"),
        ({"transformed_outcome": "FAIL"}, "transformed_outcome must be an Outcome"),
        ({"expected_reason_code": ReasonCode.GIT_FAILED}, "must be a FAIL reason code"),
        (
            {"transformed_outcome": None, "observed_reason_code": ReasonCode.UNCOVERED_LINES},
            "requires a transformed_outcome",
        ),
        (
            {"transformed_outcome": Outcome.PASS, "observed_reason_code": ReasonCode.UNCOVERED_LINES},
            "must be omitted when transformed_outcome is PASS",
        ),
        ({"observed_reason_code": None}, "is required when transformed_outcome is FAIL"),
        (
            {"observed_reason_code": ReasonCode.GIT_FAILED},
            "is not valid for transformed_outcome",
        ),
    ],
)
def test_canary_attempt_refuses_malformed_run_shapes(changes, message):
    with pytest.raises(ValueError, match=message):
        _attempt(**changes)


def test_canary_result_requires_a_mechanism_and_unique_typed_attempts():
    with pytest.raises(ValueError, match="mechanism must be a non-empty string"):
        CanaryResult(mechanism="", attempts=(_attempt(),))
    with pytest.raises(ValueError, match="tuple of CanaryAttempt"):
        CanaryResult(mechanism="import-break", attempts=[_attempt()])
    with pytest.raises(ValueError, match="between 1 and"):
        CanaryResult(mechanism="import-break", attempts=())
    with pytest.raises(ValueError, match="more than once"):
        CanaryResult(mechanism="import-break", attempts=(_attempt(), _attempt()))

    result = CanaryResult(
        mechanism="import-break",
        attempts=(_attempt(), CanaryAttempt(
            target="src/other.py",
            description="second transform",
            disposition="not_attempted",
            not_attempted_reason="earlier_target_terminal",
        )),
    )
    assert len(result.attempts) == 2


def test_coverage_exclusion_capability_accepts_reported_and_checks_unknown_values():
    common = {
        "covered": 1,
        "executable": 1,
        "pct": 100.0,
        "considered": 1,
        "missing_lines": {},
        "files_missing_coverage": (),
    }
    assert Coverage(**common, exclusion_capability="reported").exclusion_capability == "reported"
    with pytest.raises(ValueError, match="exclusion_capability must be one of"):
        Coverage(**common, exclusion_capability="mystery")
    with pytest.raises(ValueError, match="unavailable.*excluded lines"):
        Coverage(
            **common,
            exclusion_capability="unavailable",
            excluded_lines={"src/a.py": frozenset({1})},
            files_with_excluded_lines=("src/a.py",),
        )


def test_coverage_accepts_empty_reported_exclusions_and_rejects_invalid_summary():
    coverage = Coverage(
        covered=1,
        executable=1,
        pct=100.0,
        considered=1,
        exclusion_capability="reported",
        missing_lines={},
        files_missing_coverage=(),
        excluded_lines={},
        files_with_excluded_lines=(),
    )
    assert coverage.excluded_lines == {}
    with pytest.raises(ValueError, match="does not name exactly the paths"):
        Coverage(
            covered=1,
            executable=1,
            pct=100.0,
            considered=1,
            exclusion_capability="reported",
            missing_lines={},
            files_missing_coverage=(),
            excluded_lines={},
            files_with_excluded_lines=("src/a.py",),
        )

    disjoint = Coverage(
        covered=0,
        executable=1,
        pct=0.0,
        considered=2,
        exclusion_capability="reported",
        missing_lines={"src/a.py": frozenset({1})},
        files_missing_coverage=("src/a.py",),
        excluded_lines={"src/a.py": frozenset({2})},
        files_with_excluded_lines=("src/a.py",),
    )
    assert disjoint.missing_lines["src/a.py"] == frozenset({1})


def test_source_position_refuses_invalid_types_and_accepts_one_based_lines():
    assert SourcePosition(path="src/a.py", lineno=1).sort_key == ("src/a.py", 1)
    with pytest.raises(ValueError, match="NUL"):
        SourcePosition(path="src/\x00a.py", lineno=1)
    with pytest.raises(ValueError, match="must be an integer"):
        SourcePosition(path="src/a.py", lineno=True)
    with pytest.raises(ValueError, match="must be >= 1"):
        SourcePosition(path="src/a.py", lineno=0)


def test_judge_provenance_has_a_valid_wire_form_and_rejects_each_closed_field():
    valid = JudgeProvenance(
        name="assay",
        version="7.1.0",
        artifact="wheel",
        digest_algorithm="sha256",
        digest="a" * 64,
    )
    assert valid.to_dict()["digest"] == "a" * 64
    with pytest.raises(ValueError, match="artifact must be one of"):
        JudgeProvenance(
            name="assay", version="7.1.0", artifact="directory",
            digest_algorithm="sha256", digest="a" * 64,
        )
    with pytest.raises(ValueError, match="digest_algorithm must be one of"):
        JudgeProvenance(
            name="assay", version="7.1.0", artifact="wheel",
            digest_algorithm="md5", digest="a" * 64,
        )
    with pytest.raises(ValueError, match="exactly 64 lowercase"):
        JudgeProvenance(
            name="assay", version="7.1.0", artifact="wheel",
            digest_algorithm="sha256", digest="A" * 64,
        )


def test_resolved_policy_accepts_both_base_modes_and_refuses_misbound_resolution():
    assert JudgmentResolved(language="python", source_roots=("src",)).base is None
    assert JudgmentResolved(
        language="python", source_roots=("src",), base="a" * 40,
        base_resolution="first-parent",
    ).base_resolution == "first-parent"
    with pytest.raises(ValueError, match="requires judgment.resolved.base"):
        JudgmentResolved(
            language="python", source_roots=("src",), base_resolution="merge-base"
        )


def test_instant_parser_rejects_naive_timestamps():
    assert models._instant("2026-09-01T00:00:00+00:00", "started").utcoffset().total_seconds() == 0
    with pytest.raises(ValueError, match="carries no usable UTC offset"):
        models._instant("2026-09-01T00:00:00", "started")


def test_r1_policy_records_a_declared_coverage_producer():
    policy = JudgmentR1(
        coverage_format="coverage-py-json",
        coverage_artifact="coverage.json",
        coverage_producer="coverage.py",
        fail_under=100.0,
        allow_excluded=False,
    )

    assert policy.to_dict()["coverage_producer"] == "coverage.py"


def _native_r2(**overrides):
    values = {
        "jobs": 1,
        "max_mutants": 10,
        "operators": ("python:compare-swap",),
        "kill_attribution": "unattributed",
    }
    values.update(overrides)
    return JudgmentR2(**values)


def _ingested_r2(**overrides):
    values = {
        "producer": "ingested",
        "producer_tool": MutationProducerTool(
            name="StrykerJS", version="10.0.0", report_schema_version="1"
        ),
        "survived_uncovered": (),
        "discarded": (),
        "lines_without_candidates": (),
        "fail_under": 100.0,
        "kill_attribution": "unattributed",
    }
    values.update(overrides)
    return JudgmentR2(**values)


def _discarded_outcome(**overrides):
    values = {
        "path": "src/a.py",
        "lineno": 1,
        "start_byte": 0,
        "end_byte": 1,
        "replacement_sha256": "a" * 64,
        "operator": "stryker:BinaryExpression",
        "description": "invalid mutant",
        "discard_reason": "compile_error",
    }
    values.update(overrides)
    return MutantOutcome(**values)


def test_native_r2_shard_pair_is_optional_but_closed_when_present():
    assert _native_r2().shard_index is None
    assert _native_r2(shard_index=0, shard_count=1).shard_count == 1
    with pytest.raises(ValueError, match="integer shard_index and shard_count together"):
        _native_r2(shard_index=0)
    with pytest.raises(ValueError, match="shard_count must be in"):
        _native_r2(shard_index=0, shard_count=models.MAX_SHARD_COUNT + 1)
    with pytest.raises(ValueError, match="shard_index .* outside"):
        _native_r2(shard_index=1, shard_count=1)


def test_r2_target_policy_and_optional_mutant_provenance_serialize_when_present():
    policy = _native_r2(mode="whole_target", targets=("src/a.py",))
    assert policy.to_dict()["targets"] == ["src/a.py"]
    outcome = _discarded_outcome()
    assert outcome.to_dict()["discard_reason"] == "compile_error"


def test_candidate_inventory_accepts_a_digest_and_rejects_bad_or_duplicate_ids():
    outcome = MutantOutcome(
        path="src/a.py",
        lineno=1,
        start_byte=0,
        end_byte=1,
        replacement_sha256="b" * 64,
        operator="python:compare-swap",
        description="Lt to LtE",
    )
    mutation = Mutation(
        candidate_count=1,
        total=1,
        killed=(outcome,),
        candidate_ids=("a" * 64,),
    )
    assert mutation.candidate_ids == ("a" * 64,)
    assert Mutation(candidate_count=0, total=0, candidate_ids=()).candidate_ids == ()
    with pytest.raises(ValueError, match="candidate_ids contains a duplicate"):
        Mutation(candidate_count=1, total=0, candidate_ids=("a" * 64, "a" * 64))
    with pytest.raises(ValueError, match="64-character hexadecimal digest"):
        Mutation(candidate_count=0, total=0, candidate_ids=("bad",))


def test_ingested_r2_validates_tool_discarded_and_floor_fields(monkeypatch):
    assert _ingested_r2().to_dict()["discarded"] == []
    with pytest.raises(ValueError, match="producer_tool must be a MutationProducerTool"):
        _ingested_r2(producer_tool="stryker")

    monkeypatch.setattr(models, "MAX_INGESTED_MUTANTS", 0)
    with pytest.raises(ValueError, match="over the 0 document ceiling"):
        _ingested_r2(discarded=(_discarded_outcome(),))

    monkeypatch.setattr(models, "MAX_INGESTED_MUTANTS", 100_000)
    with pytest.raises(ValueError, match="carry a kill_signal"):
        _ingested_r2(
            discarded=(_discarded_outcome(kill_signal="failed"),)
        )
    with pytest.raises(ValueError, match="fail_under must be a number"):
        _ingested_r2(fail_under=True)
    with pytest.raises(ValueError, match="percentage between 0 and 100"):
        _ingested_r2(fail_under=100.1)


def test_ingested_r2_bounds_source_position_arrays_and_checks_member_types():
    with pytest.raises(ValueError, match="survived_uncovered must be a tuple"):
        _ingested_r2(survived_uncovered=[])
    with pytest.raises(ValueError, match="must be a SourcePosition"):
        _ingested_r2(survived_uncovered=("src/a.py:1",))

    oversized = tuple(
        models.SourcePosition(path=f"src/{index:05d}.py", lineno=1)
        for index in range(10_001)
    )
    with pytest.raises(ValueError, match="over the 10,000 ceiling"):
        _ingested_r2(survived_uncovered=oversized)


def test_r4_policy_and_red_first_result_enforce_their_two_run_contract():
    policy = JudgmentR4(
        tests=("tests/test_fix.py",),
        broken_commit="a" * 40,
        broken_commit_source="resolved_base",
    )
    assert policy.to_dict()["tests"] == ["tests/test_fix.py"]
    with pytest.raises(ValueError, match="non-empty tuple"):
        JudgmentR4(tests=(), broken_commit="a" * 40, broken_commit_source="declared")
    with pytest.raises(ValueError, match="more than once"):
        JudgmentR4(
            tests=("tests/test_fix.py", "tests/test_fix.py"),
            broken_commit="a" * 40,
            broken_commit_source="declared",
        )
    with pytest.raises(ValueError, match="broken_commit_source must be one of"):
        JudgmentR4(tests=("tests/test_fix.py",), broken_commit="a" * 40, broken_commit_source="unknown")

    red_first = RedFirstResult(
        broken_commit="a" * 40,
        tests=("tests/test_fix.py",),
        before_outcome=Outcome.FAIL,
        after_outcome=Outcome.PASS,
    )
    assert red_first.to_dict()["after_outcome"] == "PASS"
    assert RedFirstResult(
        broken_commit="a" * 40,
        tests=("tests/test_fix.py",),
        before_outcome=Outcome.PASS,
    ).after_outcome is None
    with pytest.raises(ValueError, match="before_outcome must be an Outcome"):
        RedFirstResult(
            broken_commit="a" * 40,
            tests=("tests/test_fix.py",),
            before_outcome="FAIL",
            after_outcome=Outcome.PASS,
        )
    with pytest.raises(ValueError, match="after_outcome must be an Outcome"):
        RedFirstResult(
            broken_commit="a" * 40,
            tests=("tests/test_fix.py",),
            before_outcome=Outcome.FAIL,
            after_outcome="PASS",
        )
    with pytest.raises(ValueError, match="PASSED at the broken commit"):
        RedFirstResult(
            broken_commit="a" * 40,
            tests=("tests/test_fix.py",),
            before_outcome=Outcome.PASS,
            after_outcome=Outcome.PASS,
        )
    with pytest.raises(ValueError, match="after_outcome cannot be absent"):
        RedFirstResult(
            broken_commit="a" * 40,
            tests=("tests/test_fix.py",),
            before_outcome=Outcome.FAIL,
        )
    with pytest.raises(ValueError, match="must be 'merge-base' or 'first-parent'"):
        JudgmentResolved(
            language="python", source_roots=("src",), base="a" * 40,
            base_resolution="branch-tip",
        )


def test_snapshot_policy_link_paths_are_optional_and_reject_git_components():
    assert SnapshotPolicy(selection="repository").link_paths is None
    assert SnapshotPolicy(selection="repository", link_paths=("a/link", "b/link")).link_paths
    with pytest.raises(ValueError, match="'.git' component"):
        SnapshotPolicy(selection="repository", link_paths=("a/.git/link",))
    with pytest.raises(ValueError, match="strictly ascending"):
        SnapshotPolicy(selection="repository", link_paths=("z/link", "a/link"))


def test_snapshot_policy_link_paths_reject_empty_or_unbounded_shapes():
    with pytest.raises(ValueError, match="tuple of 1..64 entries"):
        SnapshotPolicy(selection="repository", link_paths=[])
    with pytest.raises(ValueError, match="at most 4096 UTF-8 bytes"):
        SnapshotPolicy(selection="repository", link_paths=("a" * 4097,))


def test_worktree_integrity_requires_sorted_disjoint_non_git_paths():
    assert WorktreeIntegrity(ignored_dirty_paths=("a.py",)).ignored_dirty_paths == ("a.py",)
    with pytest.raises(ValueError, match="at least one dirty path"):
        WorktreeIntegrity()
    with pytest.raises(ValueError, match="must be a tuple"):
        WorktreeIntegrity(ignored_dirty_paths=["a.py"])
    with pytest.raises(ValueError, match="'.git' component"):
        WorktreeIntegrity(ignored_dirty_paths=(".git/config",))
    with pytest.raises(ValueError, match="unique and sorted"):
        WorktreeIntegrity(ignored_dirty_paths=("b.py", "a.py"))
    with pytest.raises(ValueError, match="both ignored and overridden"):
        WorktreeIntegrity(
            ignored_dirty_paths=("a.py",), overridden_dirty_paths=("a.py",)
        )


def test_helper_role_is_closed_and_r1_judgment_can_record_a_real_helper():
    with pytest.raises(ValueError, match=r"helpers\[\]\.role must be one of"):
        Helper(role="invented", tool="coverage", resolved_path="/bin/coverage", identity="v7")

    helper = Helper(
        role="statement-positions",
        tool="coverage",
        resolved_path="/bin/coverage",
        identity="coverage 7.16.1",
    )
    qualified = _r1_verdict(
        judge_provenance=JudgeProvenance(
            name="assay",
            version="7.1.0",
            artifact="wheel",
            digest_algorithm="sha256",
            digest="c" * 64,
        ),
        worktree_integrity=WorktreeIntegrity(ignored_dirty_paths=("generated.py",)),
        helpers=(helper,),
        result_stdout_tail="",
        result_stderr_tail="",
    )
    wire = qualified.to_dict()
    assert wire["judge_provenance"]["digest"] == "c" * 64
    assert wire["worktree_integrity"]["ignored_dirty_paths"] == ["generated.py"]
    assert wire["helpers"][0]["role"] == "statement-positions"


def test_verdict_rejects_malformed_provenance_environment_tail_and_helper_fields():
    with pytest.raises(ValueError, match="judge_provenance must be a JudgeProvenance"):
        _base_verdict(judge_provenance="wheel")
    with pytest.raises(ValueError, match="worktree_integrity must be a WorktreeIntegrity"):
        _base_verdict(worktree_integrity="clean")
    with pytest.raises(ValueError, match="requires the lane-resolved group"):
        _base_verdict(env_effective_incomplete=True)
    with pytest.raises(ValueError, match="result_stdout_tail must be a string"):
        _base_verdict(result_stdout_tail=1)
    with pytest.raises(ValueError, match="helpers must be a tuple"):
        _base_verdict(helpers=[])
    with pytest.raises(ValueError, match="helpers is present but empty"):
        _base_verdict(helpers=())


def test_cwd_declaration_requires_a_resolved_lane_and_normalized_path():
    with pytest.raises(ValueError, match="'.git' component"):
        _r1_verdict(cwd_declared="src/.git/config")
    with pytest.raises(ValueError, match="at most 4096 UTF-8 bytes"):
        _r1_verdict(cwd_declared="a" * 4097)
    with pytest.raises(ValueError, match="but no lane resolved"):
        _base_verdict(cwd_declared="src")
    assert _r1_verdict(cwd_declared="src").to_dict()["cwd_declared"] == "src"


def test_verdict_rederives_r4_policy_presence_and_exact_target_commit():
    assert _r4_verdict().judgment.r4.tests == ("tests/test_fix.py",)
    with pytest.raises(ValueError, match="judgment.r4 is present but no R4 claim"):
        _r4_verdict(claims=(_r0_claim(),), declared_rigor=("R0",))
    r1_base = _r1_verdict()
    r4_claim = _r4_verdict().claims[1]
    with pytest.raises(ValueError, match="red_first payload but judgment.r4 is absent"):
        _r4_verdict(
            claims=(*r1_base.claims, r4_claim),
            declared_rigor=("R0", "R1", "R4"),
            judgment=r1_base.judgment,
        )
    with pytest.raises(ValueError, match="different tests"):
        _r4_verdict(result_tests=("tests/other.py",))
    with pytest.raises(ValueError, match="same commit"):
        _r4_verdict(policy_commit="d" * 40)


def test_claim_red_first_and_refusal_detail_pairs_are_closed():
    red_first = RedFirstResult(
        broken_commit="a" * 40,
        tests=("tests/test_fix.py",),
        before_outcome=Outcome.FAIL,
        after_outcome=Outcome.PASS,
    )
    with pytest.raises(ValueError, match="belongs to the R4 claim"):
        Claim(
            rigor="R3", source="computed", status=Outcome.PASS,
            verified_by_assay=True, red_first=red_first,
        )
    with pytest.raises(ValueError, match="NO_MEASUREMENT carries no red_first"):
        Claim(
            rigor="R4", source="computed", status=Outcome.NO_MEASUREMENT,
            verified_by_assay=True, reason_code=ReasonCode.TARGET_NOT_MEASURED,
            red_first=red_first,
        )
    with pytest.raises(ValueError, match="non-empty tuple"):
        RedFirstResult(
            broken_commit="a" * 40, tests=(), before_outcome=Outcome.FAIL,
        )

    with pytest.raises(ValueError, match="detail_dropped_bytes.*with no detail"):
        Claim(
            rigor="R0", source="computed", status=Outcome.ERROR,
            verified_by_assay=True, reason_code=ReasonCode.EXEC_FAILED,
            detail_dropped_bytes=1,
        )
    with pytest.raises(ValueError, match="a PASS carries no detail"):
        Claim(
            rigor="R0", source="computed", status=Outcome.PASS,
            verified_by_assay=True, detail="unexpected", detail_dropped_bytes=0,
        )
    with pytest.raises(ValueError, match="detail must be a non-empty string"):
        Claim(
            rigor="R0", source="computed", status=Outcome.ERROR,
            verified_by_assay=True, reason_code=ReasonCode.EXEC_FAILED,
            detail=7, detail_dropped_bytes=0,
        )
    with pytest.raises(ValueError, match="over the .*byte bound"):
        Claim(
            rigor="R0", source="computed", status=Outcome.ERROR,
            verified_by_assay=True, reason_code=ReasonCode.EXEC_FAILED,
            detail="x" * (models.CLAIM_DETAIL_BYTES + 1), detail_dropped_bytes=1,
        )
    with pytest.raises(ValueError, match="requires detail_dropped_bytes"):
        Claim(
            rigor="R0", source="computed", status=Outcome.ERROR,
            verified_by_assay=True, reason_code=ReasonCode.EXEC_FAILED,
            detail="refused",
        )


def test_claim_r4_judged_status_requires_its_result_payload():
    for status, reason in (
        (Outcome.PASS, None),
        (Outcome.FAIL, ReasonCode.RED_FIRST_UNPROVEN),
    ):
        with pytest.raises(ValueError, match="without a red_first payload"):
            Claim(
                rigor="R4", source="computed", status=status,
                verified_by_assay=True, reason_code=reason,
            )
    error = Claim(
        rigor="R4", source="computed", status=Outcome.ERROR,
        verified_by_assay=True, reason_code=ReasonCode.EXEC_FAILED,
    )
    assert error.red_first is None


def test_claim_restricts_mutation_terminals_to_their_exact_payloads():
    with pytest.raises(ValueError, match="belongs to the R2 claim"):
        Claim(
            rigor="R1", source="computed", status=Outcome.ERROR,
            verified_by_assay=True,
            reason_code=ReasonCode.MUTATION_DISCOVERY_FAILED,
        )

    sentinel = Mutation(candidate_count=2, total=0)
    with pytest.raises(ValueError, match="legal only as BUDGET_EXCEEDED"):
        Claim(
            rigor="R2", source="computed", status=Outcome.ERROR,
            verified_by_assay=True, reason_code=ReasonCode.EXEC_FAILED,
            mutation=sentinel,
        )

    ordinary = Mutation(
        candidate_count=1,
        total=1,
        killed=(MutantOutcome(
            path="src/a.py", lineno=1, start_byte=0, end_byte=1,
            replacement_sha256="a" * 64, operator="python:compare-swap",
            description="comparison swap",
        ),),
    )
    with pytest.raises(ValueError, match="belongs to the R2 claim"):
        Claim(
            rigor="R1", source="computed", status=Outcome.BUDGET_EXCEEDED,
            verified_by_assay=True,
            reason_code=ReasonCode.MUTANT_LIMIT_EXCEEDED,
        )
    with pytest.raises(ValueError, match="requires the exact pre-submission sentinel"):
        Claim(
            rigor="R2", source="computed", status=Outcome.BUDGET_EXCEEDED,
            verified_by_assay=True,
            reason_code=ReasonCode.MUTANT_LIMIT_EXCEEDED,
            mutation=ordinary,
        )


def test_coverage_with_unavailable_exclusions_can_truthfully_report_none():
    coverage = Coverage(
        covered=1, executable=1, pct=100.0, considered=1,
        exclusion_capability="unavailable", missing_lines={},
        files_missing_coverage=(),
    )
    assert coverage.excluded_lines == {}


def test_verdict_refuses_unwitnessed_helpers():
    helper = Helper(
        role="mutation-sites", tool="mutator", resolved_path="/bin/mutator",
        identity="mutator 1",
    )
    with pytest.raises(ValueError, match="does not carry"):
        _r1_verdict(helpers=(helper,))


def _call_discarded(claim, policy):
    return Verdict._check_discarded_disposition(object(), claim, policy)


def _call_cardinality(mutation, policy):
    return Verdict._check_mutation_cardinality(object(), mutation, policy)


def _fake_mutation(**overrides):
    values = {
        "candidate_count": 0,
        "total": 0,
        "candidate_ids": (),
        "is_limit_sentinel": False,
        "killed": (),
        "survived": (),
        "crashed": (),
        "budget_exceeded": (),
        "equivalent": (),
        "hung": (),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _fake_policy(**overrides):
    values = {
        "producer": "native",
        "discarded": None,
        "lines_without_candidates": None,
        "max_mutants": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _fake_claim(mutation, status=Outcome.PASS, reason_code=None):
    return SimpleNamespace(mutation=mutation, status=status, reason_code=reason_code)


def test_model_discarded_disposition_checks_bucket_residual_and_native_sentinel():
    bad_bucket = SimpleNamespace(identity="x", discard_reason="CompileError")
    with pytest.raises(ValueError, match="carry discard_reason"):
        _call_discarded(
            _fake_claim(_fake_mutation(candidate_count=1, total=1, killed=(bad_bucket,))),
            _fake_policy(),
        )

    with pytest.raises(ValueError, match="native.*attempts every candidate"):
        _call_discarded(
            _fake_claim(_fake_mutation(candidate_count=2, total=1)), _fake_policy()
        )
    with pytest.raises(ValueError, match="PRE-SUBMISSION limit refusal"):
        _call_discarded(
            _fake_claim(
                _fake_mutation(candidate_count=2, is_limit_sentinel=True),
                Outcome.INCONCLUSIVE, ReasonCode.NO_MUTANTS,
            ),
            _fake_policy(),
        )
    _call_discarded(
        _fake_claim(
            _fake_mutation(candidate_count=2, is_limit_sentinel=True),
            Outcome.BUDGET_EXCEEDED, ReasonCode.MUTANT_LIMIT_EXCEEDED,
        ),
        _fake_policy(),
    )


def test_model_discarded_disposition_checks_ingested_attribution():
    discarded = SimpleNamespace(identity="discarded", path="src/a.py", lineno=3)
    policy = _fake_policy(producer="ingested", discarded=(discarded,))
    with pytest.raises(ValueError, match="residual of 2"):
        _call_discarded(_fake_claim(_fake_mutation(candidate_count=3, total=1)), policy)

    overlap_policy = _fake_policy(producer="ingested", discarded=(
        SimpleNamespace(identity="same", path="src/a.py", lineno=3),
    ))
    bucket_item = SimpleNamespace(identity="same", discard_reason=None)
    with pytest.raises(ValueError, match="was never run"):
        _call_discarded(
            _fake_claim(_fake_mutation(
                candidate_count=2, total=1, killed=(bucket_item,),
            )),
            overlap_policy,
        )

    barren = SimpleNamespace(sort_key=("src/a.py", 3))
    with pytest.raises(ValueError, match="starting on that exact line"):
        _call_discarded(
            _fake_claim(_fake_mutation(candidate_count=1, total=0)),
            _fake_policy(
                producer="ingested", discarded=(discarded,),
                lines_without_candidates=(barren,),
            ),
        )
    with pytest.raises(ValueError, match="honest terminal is INCONCLUSIVE/NO_MUTANTS"):
        _call_discarded(
            _fake_claim(
                _fake_mutation(candidate_count=1, total=0),
                Outcome.BUDGET_EXCEEDED, ReasonCode.MUTANT_LIMIT_EXCEEDED,
            ),
            _fake_policy(producer="ingested", discarded=(discarded,)),
        )
    _call_discarded(
        _fake_claim(
            _fake_mutation(candidate_count=1, total=0),
            Outcome.INCONCLUSIVE, ReasonCode.NO_MUTANTS,
        ),
        _fake_policy(producer="ingested", discarded=(discarded,)),
    )


def test_model_mutation_cardinality_checks_native_and_ingested_forks():
    with pytest.raises(ValueError, match="cannot carry mutation.candidate_ids"):
        _call_cardinality(
            _fake_mutation(candidate_count=51, is_limit_sentinel=True),
            _fake_policy(max_mutants=50),
        )
    with pytest.raises(ValueError, match="requires mutation.candidate_ids"):
        _call_cardinality(
            _fake_mutation(candidate_count=0, candidate_ids=None),
            _fake_policy(max_mutants=50),
        )
    incomplete = SimpleNamespace(
        candidate_id="c1", source_sha256=None, mutated_file_sha256=None,
        execution=None,
    )
    with pytest.raises(ValueError, match="requires B106 candidate identity"):
        _call_cardinality(
            _fake_mutation(candidate_count=1, total=1, killed=(incomplete,)),
            _fake_policy(max_mutants=50),
        )

    with pytest.raises(ValueError, match="cannot carry native candidate-scope"):
        _call_cardinality(
            _fake_mutation(candidate_ids=()),
            _fake_policy(producer="ingested"),
        )
    with pytest.raises(ValueError, match="cannot carry native B106"):
        _call_cardinality(
            _fake_mutation(candidate_ids=None, killed=(SimpleNamespace(
                candidate_id="c1", source_sha256=None, mutated_file_sha256=None,
                execution=None,
            ),)),
            _fake_policy(producer="ingested"),
        )

    with pytest.raises(ValueError, match="product ceiling"):
        _call_cardinality(
            _fake_mutation(
                candidate_count=models.MAX_CANDIDATE_CEILING + 1,
                total=models.MAX_CANDIDATE_CEILING + 1,
            ),
            _fake_policy(max_mutants=models.MAX_CANDIDATE_CEILING + 1),
        )
    with pytest.raises(ValueError, match="only honest sentinel count"):
        _call_cardinality(
            _fake_mutation(
                candidate_count=51, candidate_ids=None, is_limit_sentinel=True
            ),
            _fake_policy(max_mutants=40),
        )
    with pytest.raises(ValueError, match="exceeding judgment.r2.max_mutants"):
        _call_cardinality(
            _fake_mutation(candidate_count=2, total=2), _fake_policy(max_mutants=1)
        )


def test_ingested_operator_check_accepts_verdicts_without_an_r2_mutation():
    Verdict._check_ingested_operators_only(SimpleNamespace(claims=[]), None)


def test_refusal_detail_bounds_utf8_and_counts_discarded_bytes():
    assert refusal_detail("") is None
    bounded = refusal_detail("short refusal")
    assert bounded == RefusalDetail(text="short refusal", dropped_bytes=0)

    from assay.verdict import CLAIM_DETAIL_BYTES

    split_character = refusal_detail("a" * (CLAIM_DETAIL_BYTES - 1) + "é")
    assert split_character is not None
    assert split_character.dropped_bytes == 2
    assert len(split_character.text.encode("utf-8")) == CLAIM_DETAIL_BYTES - 1
    assert refusal_detail("a" * (CLAIM_DETAIL_BYTES + 1)).dropped_bytes == 1
    with pytest.raises(ValueError, match="over the"):
        RefusalDetail(text="x" * (CLAIM_DETAIL_BYTES + 1), dropped_bytes=1)
    with pytest.raises(ValueError, match="must be non-empty"):
        RefusalDetail(text="", dropped_bytes=0)
    with pytest.raises(ValueError, match="must be an integer"):
        RefusalDetail(text="ok", dropped_bytes=True)
    with pytest.raises(ValueError, match="must not be negative"):
        RefusalDetail(text="ok", dropped_bytes=-1)


@pytest.mark.parametrize("field", ["prior_node_id", "current_node_id"])
def test_witness_prefix_refuses_non_utf8_node_ids(field):
    values = {
        "mode": "witness-prefix",
        "witness": MutationWitnessReceipt(
            node_id="tests/test_example.py::test_case",
            when="call",
            outcome="failed",
            session_exit_status=1,
            process_exit_status=1,
        ),
        "prior_verdict_sha256": "a" * 64,
        "prior_node_id": "tests/test_example.py::test_case",
        "current_node_id": "tests/test_example.py::test_case",
    }
    values[field] = "tests/test_example.py::bad\ud800"

    with pytest.raises(ValueError, match=f"{field} must be valid UTF-8"):
        MutationExecution(**values)


def test_mutation_witness_receipt_refuses_non_utf8_node_id():
    with pytest.raises(ValueError, match="node_id must be valid UTF-8"):
        MutationWitnessReceipt(
            node_id="tests/test_example.py::bad\ud800",
            when="call",
            outcome="failed",
            session_exit_status=1,
            process_exit_status=1,
        )
