"""B129 / W10 step 1: characterization of the verdict model's policy and
execution guards (``MutationExecution``, ``MutantOutcome`` identity fields,
``Mutation``, ``JudgmentR1``/``JudgmentR2``, ``Claim`` detail, ``Verdict``).

Pins accept and refuse, with the exact message, for the non-empty-string,
strict-int, at-least, finite-positive, real-number, sha256 and percentage
guards. Run against the UNCHANGED source; never edited to follow the refactor.
"""

from __future__ import annotations

import math

import pytest

from assay.candidate_identity import candidate_id_from_fields
from assay.errors import Outcome, ReasonCode
from assay.resource_limits import CounterDelta, ResourceLimitEvidence
from assay.verdict import (
    Claim,
    JudgmentR1,
    JudgmentR2,
    MutantOutcome,
    Mutation,
    MutationExecution,
    MutationProducerTool,
    MutationWitnessReceipt,
    Verdict,
)

NOT_STRICT_INT = [True, False, 1.5, "1", None, b"1"]
NOT_REAL = [True, False, "1", None, b"1", [1]]
BAD_DIGESTS = [
    "a" * 63,
    "a" * 65,
    "A" * 64,
    "g" * 64,
    "a" * 64 + "\n",
    "",
    None,
    7,
    b"a" * 64,
]
HEX64 = "0123456789abcdef" * 4


def refusal(call, message: str, exc=ValueError) -> None:
    with pytest.raises(exc) as caught:
        call()
    assert str(caught.value) == message


# ---- MutationExecution: prior digest and node ids ------------------------------

NODE = "tests/test_example.py::test_case"


def _execution(**overrides) -> MutationExecution:
    values = dict(
        mode="witness-prefix",
        witness=MutationWitnessReceipt(
            node_id=NODE,
            when="call",
            outcome="failed",
            session_exit_status=1,
            process_exit_status=1,
        ),
        prior_verdict_sha256=HEX64,
        prior_node_id=NODE,
        current_node_id=NODE,
    )
    values.update(overrides)
    return MutationExecution(**values)


def test_execution_accepts_sixty_four_lowercase_hex_and_one_character_node_ids():
    assert _execution().prior_verdict_sha256 == HEX64
    receipt = MutationWitnessReceipt(
        node_id="n",
        when="call",
        outcome="failed",
        session_exit_status=1,
        process_exit_status=1,
    )
    assert _execution(witness=receipt, prior_node_id="n", current_node_id="n")


@pytest.mark.parametrize("digest", BAD_DIGESTS)
def test_execution_refuses_a_malformed_prior_verdict_digest(digest):
    refusal(
        lambda: _execution(prior_verdict_sha256=digest),
        "witness-prefix prior_verdict_sha256 must be a SHA-256 digest",
    )


@pytest.mark.parametrize("name", ["prior_node_id", "current_node_id"])
@pytest.mark.parametrize("value", ["", 1, None, True, b"n"])
def test_execution_refuses_an_empty_or_non_string_node_id(name, value):
    refusal(
        lambda: _execution(**{name: value}),
        f"witness-prefix {name} must be a non-empty string",
    )


# ---- MutantOutcome: native identity digests ------------------------------------


def _native_outcome(**overrides) -> MutantOutcome:
    source = "1" * 64
    mutated = "2" * 64
    fields = dict(
        path="pkg/mod.py",
        lineno=3,
        start_byte=40,
        end_byte=41,
        replacement_sha256=HEX64,
        operator="python:compare-swap",
        description="Lt->LtE",
        source_sha256=source,
        mutated_file_sha256=mutated,
        candidate_id=candidate_id_from_fields(
            path="pkg/mod.py",
            source_sha256=source,
            start_byte=40,
            end_byte=41,
            mutated_file_sha256=mutated,
            operator="python:compare-swap",
        ),
        execution=MutationExecution(mode="full"),
        resource_limit_evidence=ResourceLimitEvidence(
            pids_events_max=CounterDelta(before=0, after=0, delta=0),
            memory_events_max=CounterDelta(before=0, after=0, delta=0),
            memory_events_oom=CounterDelta(before=0, after=0, delta=0),
            memory_events_oom_kill=CounterDelta(before=0, after=0, delta=0),
            memory_events_oom_group_kill=CounterDelta(before=0, after=0, delta=0),
        ),
    )
    fields.update(overrides)
    return MutantOutcome(**fields)


def test_mutant_outcome_accepts_the_native_identity_fields():
    assert _native_outcome().source_sha256 == "1" * 64


@pytest.mark.parametrize("name", ["candidate_id", "source_sha256", "mutated_file_sha256"])
@pytest.mark.parametrize("value", [d for d in BAD_DIGESTS if d is not None])
def test_mutant_outcome_refuses_a_malformed_identity_digest(name, value):
    refusal(
        lambda: _native_outcome(**{name: value}),
        f"MutantOutcome.{name} must be a lowercase SHA-256 digest",
    )


# ---- Mutation: ints, candidate ids, derived budget -----------------------------


@pytest.mark.parametrize("name", ["candidate_count", "total"])
@pytest.mark.parametrize("value", NOT_STRICT_INT)
def test_mutation_counts_refuse_a_bool_or_non_int(name, value):
    values = {"candidate_count": 0, "total": 0}
    values[name] = value
    refusal(
        lambda: Mutation(**values),
        f"mutation.{name} must be an integer, got {value!r}",
    )


@pytest.mark.parametrize("name", ["candidate_count", "total"])
def test_mutation_counts_accept_zero_and_refuse_minus_one(name):
    assert Mutation(candidate_count=0, total=0).total == 0
    values = {"candidate_count": 0, "total": 0}
    values[name] = -1
    refusal(
        lambda: Mutation(**values),
        f"mutation.{name} must not be negative, got -1",
    )


def test_mutation_candidate_ids_accept_sixty_four_lowercase_hex():
    assert Mutation(candidate_count=0, total=0, candidate_ids=(HEX64,)).candidate_ids


@pytest.mark.parametrize("value", [d for d in BAD_DIGESTS if d is not None and d != ""] + [""])
def test_mutation_candidate_ids_refuse_a_malformed_entry(value):
    refusal(
        lambda: Mutation(candidate_count=0, total=0, candidate_ids=(value,)),
        "mutation.candidate_ids entry must be a 64-character hexadecimal "
        f"digest, got {value!r}",
    )


DERIVED_BAD = [True, False, "1", b"1", [1], 0, 0.0, -0.0, -1, -1.5, math.nan, math.inf, -math.inf]


@pytest.mark.parametrize("value", [1, 0.5, 1e-9, 10**6])
def test_mutation_derived_budget_accepts_a_positive_finite_number(value):
    assert (
        Mutation(
            candidate_count=0, total=0, budget_per_candidate_derived_s=value
        ).budget_per_candidate_derived_s
        == value
    )


@pytest.mark.parametrize("value", DERIVED_BAD)
def test_mutation_derived_budget_refuses_everything_else(value):
    refusal(
        lambda: Mutation(
            candidate_count=0, total=0, budget_per_candidate_derived_s=value
        ),
        "mutation.budget_per_candidate_derived_s must be a positive finite "
        f"number or None, got {value!r}",
    )


def test_mutation_derived_budget_lets_an_int_beyond_float_range_overflow():
    with pytest.raises(OverflowError):
        Mutation(candidate_count=0, total=0, budget_per_candidate_derived_s=10**400)


# ---- JudgmentR2 native policy ---------------------------------------------------


def _native_r2(**overrides) -> JudgmentR2:
    values = {
        "jobs": 1,
        "max_mutants": 10,
        "operators": ("python:compare-swap",),
        "kill_attribution": "unattributed",
    }
    values.update(overrides)
    return JudgmentR2(**values)


#: ``None`` is "absent" for a native R2 policy field: a different, earlier refusal.
NOT_STRICT_INT_PRESENT = [v for v in NOT_STRICT_INT if v is not None]


@pytest.mark.parametrize("value", NOT_STRICT_INT_PRESENT)
def test_native_r2_jobs_refuse_a_bool_or_non_int(value):
    refusal(
        lambda: _native_r2(jobs=value),
        f"judgment.r2.jobs must be an integer, got {value!r}",
    )


def test_native_r2_jobs_accept_one_and_refuse_zero():
    assert _native_r2(jobs=1).jobs == 1
    refusal(lambda: _native_r2(jobs=0), "judgment.r2.jobs must be >= 1, got 0")


@pytest.mark.parametrize("value", NOT_STRICT_INT_PRESENT)
def test_native_r2_max_mutants_refuse_a_bool_or_non_int(value):
    refusal(
        lambda: _native_r2(max_mutants=value),
        f"judgment.r2.max_mutants must be an integer, got {value!r}",
    )


@pytest.mark.parametrize("value", DERIVED_BAD)
def test_native_r2_derived_budget_refuses_everything_but_a_positive_finite_number(value):
    refusal(
        lambda: _native_r2(budget_per_candidate_derived_s=value),
        "judgment.r2.budget_per_candidate_derived_s must be a positive finite "
        f"number or None, got {value!r}",
    )


@pytest.mark.parametrize("value", [1, 0.5, 1e-9])
def test_native_r2_derived_budget_accepts_a_positive_finite_number(value):
    assert _native_r2(budget_per_candidate_derived_s=value)


def test_native_r2_derived_budget_lets_an_int_beyond_float_range_overflow():
    with pytest.raises(OverflowError):
        _native_r2(budget_per_candidate_derived_s=10**400)


def _liveness(**overrides):
    values = {"active": True, "reason": "x", "plugin": None}
    values.update(overrides)
    return values


def test_native_r2_liveness_accepts_one_character_reason_and_plugin():
    assert _native_r2(liveness=_liveness(reason="x", plugin="p"))
    assert _native_r2(liveness=_liveness(active=False, plugin=None))


@pytest.mark.parametrize("value", ["", 1, None, True, b"x"])
def test_native_r2_liveness_reason_refuses_an_empty_or_non_string(value):
    refusal(
        lambda: _native_r2(liveness=_liveness(reason=value)),
        "judgment.r2.liveness.reason must be a non-empty string, "
        f"got {value!r}",
    )


@pytest.mark.parametrize("value", ["", 1, True, False, b"x"])
def test_native_r2_liveness_plugin_refuses_an_empty_or_non_string(value):
    refusal(
        lambda: _native_r2(liveness=_liveness(plugin=value)),
        "judgment.r2.liveness.plugin must be a non-empty string "
        f"or None, got {value!r}",
    )


# ---- JudgmentR2 ingested and JudgmentR1: fail_under real number, percentage ----


def _ingested_r2(**overrides) -> JudgmentR2:
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


def _r1(**overrides) -> JudgmentR1:
    values = {
        "coverage_format": "coverage-py-json",
        "coverage_artifact": "coverage.json",
        "fail_under": 100.0,
        "allow_excluded": False,
    }
    values.update(overrides)
    return JudgmentR1(**values)


BUILDERS = [("r1", _r1), ("r2", _ingested_r2)]
PERCENT_BAD = [
    (-0.0000001, "-1e-07"),
    (100.0000001, "100.0000001"),
    (-1, "-1"),
    (101, "101"),
    (math.inf, "inf"),
    (-math.inf, "-inf"),
    (math.nan, "nan"),
]


#: ``None`` is "absent" on an ingested R2 (an earlier, different refusal), so
#: it is pinned for R1 only.
NOT_REAL_CASES = [
    (tier, build, value)
    for tier, build in BUILDERS
    for value in NOT_REAL
    if not (tier == "r2" and value is None)
]


@pytest.mark.parametrize(("tier", "build", "value"), NOT_REAL_CASES)
def test_fail_under_refuses_a_bool_or_non_number(tier, build, value):
    refusal(
        lambda: build(fail_under=value),
        f"judgment.{tier}.fail_under must be a number, got {value!r}",
    )


@pytest.mark.parametrize(("tier", "build"), BUILDERS)
@pytest.mark.parametrize("value", [0, 0.0, 50, 100, 100.0])
def test_fail_under_accepts_zero_to_one_hundred(tier, build, value):
    assert build(fail_under=value).fail_under == value


@pytest.mark.parametrize(("tier", "build"), BUILDERS)
@pytest.mark.parametrize(("value", "shown"), PERCENT_BAD)
def test_fail_under_refuses_outside_zero_to_one_hundred(tier, build, value, shown):
    refusal(
        lambda: build(fail_under=value),
        f"judgment.{tier}.fail_under must be a percentage between 0 and 100, "
        f"got {shown}",
    )


# ---- Claim._check_detail: detail_dropped_bytes non-negative int ----------------


def _claim(**overrides) -> Claim:
    values = dict(
        rigor="R0",
        source="computed",
        status=Outcome.ERROR,
        verified_by_assay=True,
        reason_code=ReasonCode.EXEC_FAILED,
        detail="refused",
        detail_dropped_bytes=0,
    )
    values.update(overrides)
    return Claim(**values)


def test_claim_detail_dropped_bytes_accept_zero_and_positive():
    assert _claim(detail_dropped_bytes=0).detail_dropped_bytes == 0
    assert _claim(detail_dropped_bytes=5).detail_dropped_bytes == 5


@pytest.mark.parametrize("value", NOT_STRICT_INT + [-1])
def test_claim_detail_dropped_bytes_refuse_a_bool_non_int_or_negative_count(value):
    refusal(
        lambda: _claim(detail_dropped_bytes=value),
        "claim[R0]: detail requires detail_dropped_bytes as a non-negative "
        f"integer, got {value!r} -- a silently truncated sentence is worse "
        "than no sentence (A-428/B014)",
    )


# ---- Verdict: identity strings and dropped-byte counters -----------------------


def _verdict(**overrides) -> Verdict:
    values = dict(
        lane="package",
        commit="b" * 40,
        outcome=Outcome.PASS,
        started="2026-09-01T00:00:00+00:00",
        ended="2026-09-01T00:00:01+00:00",
        assay_version="7.1.0",
    )
    values.update(overrides)
    return Verdict(**values)


def test_verdict_accepts_one_character_identity_strings_and_zero_counters():
    verdict = _verdict(
        lane="l",
        commit="c",
        assay_version="v",
        result_stdout_dropped_bytes=0,
        result_stderr_dropped_bytes=0,
    )
    assert (verdict.lane, verdict.commit, verdict.assay_version) == ("l", "c", "v")


@pytest.mark.parametrize("name", ["lane", "commit", "assay_version"])
@pytest.mark.parametrize("value", ["", 1, None, True, b"x"])
def test_verdict_refuses_an_empty_or_non_string_identity(name, value):
    refusal(
        lambda: _verdict(**{name: value}),
        f"{name!r} must be a non-empty string, got {value!r}",
    )


@pytest.mark.parametrize(
    "name", ["result_stdout_dropped_bytes", "result_stderr_dropped_bytes"]
)
@pytest.mark.parametrize("value", NOT_STRICT_INT + [-1])
def test_verdict_refuses_a_bool_non_int_or_negative_dropped_byte_count(name, value):
    refusal(
        lambda: _verdict(**{name: value}),
        f"{name} must be a non-negative integer",
    )
