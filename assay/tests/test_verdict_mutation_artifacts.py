"""O4/A-116/A-117 -- the mutation result attaches as ``Claim.mutation``,
gated to ``rigor == "R2"`` (the exact pattern already enforced for
``Claim.coverage``/``rigor == "R1"`` and ``Claim.canary``/``rigor ==
"R3"``), and records total/killed plus the three identity-carrying buckets
in an INDEPENDENTLY WRITTEN, schema-valid artifact.

Six hand-written fixtures (``tests/fixtures/verdicts/r2_*.json``) built
directly from ``Claim``/``Mutation``/``MutantOutcome``/``Verdict`` here --
never through :mod:`assay.mutation`'s own orchestration (this module is
``test_verdict_canary_artifacts.py``'s own pattern, restated for R2): the
model's own construction is the independent oracle the schema is checked
against, and the schema is the independent oracle the model's
serialisation is checked against. Neither is generated from the other.

The negative this defends (O4, verbatim): *dropping unattempted
identities, treating crashes as killed, or universal PASS differs from the
complete expected artifact.* Every fixture below carries the FULL
``mutation`` block for its own terminal case, including the two
DIFFERENT ``ERROR``/``EXEC_FAILED`` shapes A-116 itself calls out: a
crashed BASELINE (``mutation`` entirely ABSENT) versus a crashed MUTANT
(``mutation`` present with a non-empty ``crashed`` bucket) -- proving they
are correctly distinguishable, not conflated into one shape.

**(P34/O9) SQL, all four buckets simultaneously, and the three live
negatives.** The carve's own §5.2 target artifact -- ``language = "sql"``,
all seven operators declared, both mutation artifacts declared,
``kill_attribution = "declared"``, and a ``mutation`` payload populating
``killed`` (with a ``kill_signal``), ``survived``, ``crashed`` and
``equivalent`` simultaneously -- is accepted with ZERO changes to any of the
three layers (schema, model, raw verifier), which is O9's positive half.
The negative half is what makes that acceptance non-vacuous (O9, verbatim:
"the negatives are the proof the acceptance is not vacuous; a missing one
leaves an accept-everything oracle"): three targeted single-field breaks --
a killed mutant with no ``kill_signal`` under declared attribution, an
``equivalent`` bucket with no declared ``equivalence_artifact``, and a
``python:compare-swap`` operator declared on a ``sql`` lane -- each of which
the shipped JSON Schema CANNOT catch (A-182: no ``$data`` relation for
cross-object arithmetic), and each of which BOTH the model (at
``Verdict.__init__``) and :func:`assay.verify.verify_document` (over the raw
document, independently worded) refuse.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from conftest import mutation_verdict_fixture, why_invalid
from jsonschema import Draft202012Validator

from assay.errors import Outcome, ReasonCode
from assay.verdict import (
    Claim,
    Judgment,
    JudgmentR2,
    JudgmentResolved,
    Mutation,
    MutantOutcome,
    SnapshotPolicy,
    Verdict,
)
from assay.verify import verify_document

#: (wave-1 §6, A-269) the plain repository-mode policy every R0,R2 verdict
#: below carries -- none of these fixtures is itself testing the omission
#: axis.
REPOSITORY_POLICY = SnapshotPolicy(selection="repository")

BASE = {
    "lane": "package",
    "assay_version": "0.1.0",
    "declared_rigor": ("R0", "R2"),
    "declared_evidence": (),
    "argv_declared": ("pytest", "tests", "-q"),
    "argv_appended": (),
    "argv_effective": ("pytest", "tests", "-q"),
    "env_declared": {},
    "env_effective": {},
    "scope": "S1",
    "enforcement": "gate",
    "snapshot_policy": REPOSITORY_POLICY,
}

R0_PASS = Claim(rigor="R0", source="computed", status=Outcome.PASS, verified_by_assay=True)

#: (P33/V5-1) Every R0,R2 verdict below now records what it judged. Under v4
#: it recorded none of this -- no language, no source roots, no comparison
#: commit -- even though R2 scopes mutation to changed lines against exactly
#: that commit. These values match the committed expected fixtures byte for
#: byte, which is what makes the equality assertions below real.
RESOLVED = JudgmentResolved(
    language="python",
    source_roots=("pkg",),
    base="0000000000000000000000000000000000000a",
)

#: (P33/V5-4) Derived, not declared: `kill_signal_artifact` is refused at
#: config load until P34, so every lane this build can run renders
#: `unattributed`.
UNATTRIBUTED = {"kill_attribution": "unattributed"}

#: Replacement hashes, hand-computed rather than read back from the code
#: under test (A-067). Each is sha256 of the REPLACEMENT BYTES only.
SHA_LTE = "b60080dc8b8982d2a2bff6f8f3715c1939614dc553cd223ef21832b88c815866"
SHA_OR = "7175517a370b5cd2e664e3fd29c4ea9db5ce17058eb9772fe090a5485e49dad6"
SHA_NE = "c10987bd7cf853f6ea92ddac1b6c95fa830e3aee160cc5d4ba2fea3743be1aa2"
SHA_FALSE = "60a33e6cf5151f2d52eddae9685cfa270426aa89d8dbc7dfb854606f1d1a40fe"


def _validate(document: dict, validator: Draft202012Validator) -> None:
    assert why_invalid(validator, document) == []


# --- the six A-116/A-117 terminal shapes, each matched against its fixture ---


def test_pass_matches_the_hand_written_fixture(validator: Draft202012Validator):
    mutation = Mutation(
        candidate_count=2,
        total=2,
        killed=(
            MutantOutcome(
                path="pkg/checks.py", lineno=12, start_byte=100, end_byte=101,
                replacement_sha256=SHA_LTE, operator="python:compare-swap",
                description="Lt->LtE",
            ),
            MutantOutcome(
                path="pkg/checks.py", lineno=18, start_byte=240, end_byte=243,
                replacement_sha256=SHA_OR, operator="python:boolop-swap",
                description="And->Or",
            ),
        ),
    )
    r2_claim = Claim(
        rigor="R2", source="computed", status=Outcome.PASS,
        verified_by_assay=True, mutation=mutation,
    )
    verdict = Verdict(
        **BASE,
        commit="1" * 39 + "a",
        outcome=Outcome.PASS,
        started="2026-08-07T14:00:00+00:00",
        ended="2026-08-07T14:00:05+00:00",
        judgment=Judgment(
            resolved=RESOLVED,
            r2=JudgmentR2(
                jobs=1,
                max_mutants=50,
                operators=("python:compare-swap", "python:boolop-swap"),
                **UNATTRIBUTED,
            ),
        ),
        claims=(R0_PASS, r2_claim),
    )
    document = json.loads(verdict.to_json())
    assert document == mutation_verdict_fixture("r2_pass")
    _validate(document, validator)


def test_mutants_survived_matches_the_hand_written_fixture(validator: Draft202012Validator):
    survivor = MutantOutcome(
        path="pkg/checks.py", lineno=30, start_byte=300, end_byte=302,
        replacement_sha256=SHA_NE, operator="python:compare-swap",
        description="Eq->NotEq",
    )
    killed = MutantOutcome(
        path="pkg/checks.py", lineno=14, start_byte=120, end_byte=121,
        replacement_sha256=SHA_LTE, operator="python:compare-swap",
        description="Lt->LtE",
    )
    mutation = Mutation(
        candidate_count=2, total=2, killed=(killed,), survived=(survivor,)
    )
    r2_claim = Claim(
        rigor="R2", source="computed", status=Outcome.FAIL,
        verified_by_assay=True, reason_code=ReasonCode.MUTANTS_SURVIVED, mutation=mutation,
    )
    verdict = Verdict(
        **BASE,
        commit="2" * 39 + "b",
        outcome=Outcome.FAIL,
        reason_code=ReasonCode.MUTANTS_SURVIVED,
        started="2026-08-07T14:05:00+00:00",
        ended="2026-08-07T14:05:05+00:00",
        judgment=Judgment(
            resolved=RESOLVED,
            r2=JudgmentR2(
                jobs=2,
                max_mutants=50,
                operators=("python:compare-swap",),
                **UNATTRIBUTED,
            ),
        ),
        claims=(R0_PASS, r2_claim),
    )
    document = json.loads(verdict.to_json())
    assert document == mutation_verdict_fixture("r2_fail_mutants_survived")
    _validate(document, validator)


def test_no_mutants_matches_the_hand_written_fixture(validator: Draft202012Validator):
    mutation = Mutation(candidate_count=0, total=0)
    r2_claim = Claim(
        rigor="R2", source="computed", status=Outcome.INCONCLUSIVE,
        verified_by_assay=True, reason_code=ReasonCode.NO_MUTANTS, mutation=mutation,
    )
    verdict = Verdict(
        **BASE,
        commit="3" * 39 + "c",
        outcome=Outcome.INCONCLUSIVE,
        reason_code=ReasonCode.NO_MUTANTS,
        started="2026-08-07T14:10:00+00:00",
        ended="2026-08-07T14:10:05+00:00",
        judgment=Judgment(
            resolved=RESOLVED,
            r2=JudgmentR2(
                jobs=1,
                max_mutants=50,
                operators=("python:compare-swap",),
                **UNATTRIBUTED,
            ),
        ),
        claims=(R0_PASS, r2_claim),
    )
    document = json.loads(verdict.to_json())
    assert document == mutation_verdict_fixture("r2_inconclusive_no_mutants")
    _validate(document, validator)


def test_budget_exceeded_matches_the_hand_written_fixture(validator: Draft202012Validator):
    stopped = MutantOutcome(
        path="pkg/slow.py", lineno=9, start_byte=96, end_byte=99,
        replacement_sha256=SHA_OR, operator="python:boolop-swap", description="And->Or",
    )
    killed = MutantOutcome(
        path="pkg/slow.py", lineno=5, start_byte=40, end_byte=41,
        replacement_sha256=SHA_LTE, operator="python:compare-swap",
        description="Lt->LtE",
    )
    mutation = Mutation(
        candidate_count=2, total=2, killed=(killed,), budget_exceeded=(stopped,)
    )
    r2_claim = Claim(
        rigor="R2", source="computed", status=Outcome.BUDGET_EXCEEDED,
        verified_by_assay=True, reason_code=ReasonCode.LANE_TIMEOUT, mutation=mutation,
    )
    verdict = Verdict(
        **BASE,
        commit="4" * 39 + "d",
        outcome=Outcome.BUDGET_EXCEEDED,
        reason_code=ReasonCode.LANE_TIMEOUT,
        started="2026-08-07T14:15:00+00:00",
        ended="2026-08-07T14:15:05+00:00",
        judgment=Judgment(
            resolved=RESOLVED,
            r2=JudgmentR2(
                jobs=2,
                max_mutants=50,
                operators=("python:boolop-swap", "python:compare-swap"),
                **UNATTRIBUTED,
            ),
        ),
        claims=(R0_PASS, r2_claim),
    )
    document = json.loads(verdict.to_json())
    assert document == mutation_verdict_fixture("r2_budget_exceeded_lane_timeout")
    _validate(document, validator)


def test_a_crashed_mutant_matches_the_hand_written_fixture(validator: Draft202012Validator):
    """The FIRST of A-116's two distinct ERROR/EXEC_FAILED shapes: the
    baseline itself PASSED (an R2 mutation payload exists at all), but one
    attempted mutant's process crashed."""
    crashed = MutantOutcome(
        path="pkg/broken.py", lineno=4, start_byte=60, end_byte=64,
        replacement_sha256=SHA_FALSE, operator="python:bool-const-flip",
        description="True->False",
    )
    killed = MutantOutcome(
        path="pkg/broken.py", lineno=2, start_byte=20, end_byte=21,
        replacement_sha256=SHA_LTE, operator="python:compare-swap",
        description="Lt->LtE",
    )
    mutation = Mutation(
        candidate_count=2, total=2, killed=(killed,), crashed=(crashed,)
    )
    r2_claim = Claim(
        rigor="R2", source="computed", status=Outcome.ERROR,
        verified_by_assay=True, reason_code=ReasonCode.EXEC_FAILED, mutation=mutation,
    )
    verdict = Verdict(
        **BASE,
        commit="5" * 39 + "e",
        outcome=Outcome.ERROR,
        reason_code=ReasonCode.EXEC_FAILED,
        started="2026-08-07T14:20:00+00:00",
        ended="2026-08-07T14:20:05+00:00",
        judgment=Judgment(
            resolved=RESOLVED,
            r2=JudgmentR2(
                jobs=2,
                max_mutants=50,
                operators=("python:bool-const-flip", "python:compare-swap"),
                **UNATTRIBUTED,
            ),
        ),
        claims=(R0_PASS, r2_claim),
    )
    document = json.loads(verdict.to_json())
    assert document == mutation_verdict_fixture("r2_error_exec_failed_mutant_crashed")
    _validate(document, validator)
    assert document["claims"][1]["mutation"] is not None


def test_a_crashed_baseline_matches_the_hand_written_fixture(validator: Draft202012Validator):
    """The SECOND of A-116's two distinct ERROR/EXEC_FAILED shapes: the
    BASELINE never resolved to PASS, so mutation testing never started --
    ``mutation`` is entirely ABSENT here, never an empty/zeroed payload
    (A-025's own discipline, one level up), even though the R0 claim
    carries the identical (outcome, reason_code)."""
    r0_crashed = Claim(
        rigor="R0", source="computed", status=Outcome.ERROR,
        verified_by_assay=True, reason_code=ReasonCode.EXEC_FAILED,
    )
    r2_claim = Claim(
        rigor="R2", source="computed", status=Outcome.ERROR,
        verified_by_assay=True, reason_code=ReasonCode.EXEC_FAILED,
    )
    verdict = Verdict(
        lane="package",
        assay_version="0.1.0",
        declared_rigor=("R0", "R2"),
        declared_evidence=(),
        argv_declared=("/opt/does-not-exist/tool",),
        argv_appended=(),
        argv_effective=("/opt/does-not-exist/tool",),
        env_declared={},
        env_effective={},
        scope="S1",
        enforcement="gate",
        commit="6" * 39 + "f",
        outcome=Outcome.ERROR,
        reason_code=ReasonCode.EXEC_FAILED,
        started="2026-08-07T14:25:00+00:00",
        ended="2026-08-07T14:25:00+00:00",
        claims=(r0_crashed, r2_claim),
        snapshot_policy=REPOSITORY_POLICY,
    )
    document = json.loads(verdict.to_json())
    assert document == mutation_verdict_fixture("r2_error_exec_failed_baseline_crashed")
    _validate(document, validator)
    assert "mutation" not in document["claims"][1]


# --- the two ERROR/EXEC_FAILED shapes are genuinely distinguishable ----------


def test_baseline_crash_and_mutant_crash_are_both_error_exec_failed_but_differ_in_payload():
    """A-116's own explicit ruling, proven directly: two claims can both
    render (ERROR, EXEC_FAILED) -- the reason code names the outcome
    CLASS, the payload (when present) carries the mechanism."""
    baseline_shape = mutation_verdict_fixture("r2_error_exec_failed_baseline_crashed")
    mutant_shape = mutation_verdict_fixture("r2_error_exec_failed_mutant_crashed")
    assert baseline_shape["outcome"] == mutant_shape["outcome"] == "ERROR"
    assert baseline_shape["reason_code"] == mutant_shape["reason_code"] == "EXEC_FAILED"
    assert "mutation" not in baseline_shape["claims"][1]
    assert mutant_shape["claims"][1]["mutation"]["crashed"]


# --- the vacuity guard: recording only 'rejected=true' would differ ---------


def test_recording_only_rejected_true_would_differ_from_the_expected_artifact(
    validator: Draft202012Validator,
):
    """O4's own negative, made concrete at the SCHEMA level: a record that
    drops WHICH mutants survived is refused -- ``total`` alone can never
    prove the complete expected artifact."""
    document = mutation_verdict_fixture("r2_fail_mutants_survived")
    assert why_invalid(validator, document) == []

    del document["claims"][1]["mutation"]["survived"]
    messages = why_invalid(validator, document)
    assert messages, "a mutation payload without its survived bucket was accepted"


# --- A-116: the exact R1/coverage template, restated for R2 -----------------


def test_a_mutation_payload_outside_the_r2_branch_is_rejected(validator: Draft202012Validator):
    """The pattern's teeth (mirrors ``test_verdict_canary_artifacts.py``'s
    identical R3 test): ``mutation`` is legal in the R2 branch and nowhere
    else, because the claim is closed by ``additionalProperties: false``."""
    document = mutation_verdict_fixture("r2_pass")
    original_mutation_block = document["claims"][1]["mutation"]
    assert why_invalid(validator, document) == []

    for rigor in ("R0", "R1", "R3"):
        document["claims"][1]["rigor"] = rigor
        if rigor == "R1":
            document["declared_rigor"] = ["R0", "R1"]
        elif rigor == "R3":
            document["declared_rigor"] = ["R0", "R3"]
        messages = why_invalid(validator, document)
        assert messages, f"a mutation payload on an {rigor} claim was accepted"

    # sanity: the identical payload IS accepted back on R2.
    document["claims"][1]["rigor"] = "R2"
    document["declared_rigor"] = ["R0", "R2"]
    assert why_invalid(validator, document) == []
    assert document["claims"][1]["mutation"] == original_mutation_block


def test_the_model_refuses_a_mutation_payload_outside_r2():
    mutation = Mutation(candidate_count=0, total=0)
    with pytest.raises(ValueError, match="a mutation payload belongs to the R2 claim"):
        Claim(
            rigor="R1", source="computed", status=Outcome.PASS,
            verified_by_assay=True, mutation=mutation,
        )


def test_the_model_refuses_a_mutation_payload_on_a_no_measurement_claim():
    mutation = Mutation(candidate_count=0, total=0)
    with pytest.raises(ValueError, match="NO_MEASUREMENT carries no mutation payload"):
        Claim(
            rigor="R2", source="computed", status=Outcome.NO_MEASUREMENT,
            verified_by_assay=True, reason_code=ReasonCode.EMPTY_COVERAGE,
            mutation=mutation,
        )


def test_a_no_measurement_verdict_carrying_a_mutation_block_is_rejected_by_the_schema(
    validator: Draft202012Validator,
):
    document = mutation_verdict_fixture("r2_pass")
    document["claims"][1]["status"] = "NO_MEASUREMENT"
    document["claims"][1]["reason_code"] = "EMPTY_COVERAGE"

    messages = why_invalid(validator, document)
    assert messages, "a NO_MEASUREMENT claim was allowed to carry a mutation block"


def test_an_unknown_mutation_key_is_rejected(validator: Draft202012Validator):
    document = mutation_verdict_fixture("r2_pass")
    assert why_invalid(validator, document) == []

    document["claims"][1]["mutation"]["unattempted"] = 3
    assert not validator.is_valid(document)


def test_an_unknown_mutant_outcome_key_is_rejected(validator: Draft202012Validator):
    document = mutation_verdict_fixture("r2_fail_mutants_survived")
    assert why_invalid(validator, document) == []

    document["claims"][1]["mutation"]["survived"][0]["mutated_text"] = "x = 1\n"
    assert not validator.is_valid(document)


def test_an_operator_outside_the_closed_catalogue_is_rejected_by_the_schema(
    validator: Draft202012Validator,
):
    """The schema's own closed ``mutation_operator`` enum -- a SECOND,
    independently verified layer beyond ``Mutant``'s own construction-time
    validation (A-071's 'two independently verified layers' discipline;
    :class:`~assay.verdict.MutantOutcome` deliberately does not re-import
    the closed set to avoid a circular import, so the schema is where this
    particular guard actually lives for the ARTIFACT)."""
    document = mutation_verdict_fixture("r2_fail_mutants_survived")
    assert why_invalid(validator, document) == []

    document["claims"][1]["mutation"]["survived"][0]["operator"] = "arithmetic-swap"
    assert not validator.is_valid(document)


# --- P34/O9: SQL, all four buckets simultaneously, and the three live -------
# --- negatives (the carve's own §5.2 target artifact; A-279/A-287 material) --

SQL_RESOLVED = JudgmentResolved(
    language="sql",
    source_roots=("db",),
    base="0000000000000000000000000000000000000b",
)

#: (A-220/V5-2) the whole sql catalogue, schema order -- mirrors
#: ``vocabulary.MUTATION_OPERATORS_BY_LANGUAGE["sql"]`` as a literal rather
#: than importing it, so this module's own expectation is independent of
#: that module's (A-067's "not read back from the code under test" applied
#: to a vocabulary rather than a computed value).
SQL_OPERATORS: tuple[str, ...] = (
    "sql:drop-check",
    "sql:drop-unique",
    "sql:drop-not-null",
    "sql:drop-foreign-key",
    "sql:weaken-delete-action",
    "sql:drop-trigger",
    "sql:widen-check-in",
)


def _sha(seed: str) -> str:
    """A validly-shaped, arbitrary 64-hex-char placeholder -- these fixtures
    assert nothing about what SqlAdapter actually computes (that is
    ``tests/test_adapters_sql_generate_mutants.py``'s job), so a mechanical
    hash of a distinguishing seed is exactly as good as a hand-picked
    literal and does not require inventing four of them by hand."""
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


SQL_KILLED = MutantOutcome(
    path="db/schema.sql", lineno=12, start_byte=200, end_byte=222,
    replacement_sha256=_sha("o9-sql-killed"), operator="sql:drop-not-null",
    description="NOT NULL -> NULL",
    kill_signal='ERROR:  null value in column "label" violates not-null constraint',
)
SQL_SURVIVED = MutantOutcome(
    path="db/schema.sql", lineno=20, start_byte=300, end_byte=330,
    replacement_sha256=_sha("o9-sql-survived"), operator="sql:drop-check",
    description="CHECK (...) -> CHECK (true)",
)
SQL_CRASHED = MutantOutcome(
    path="db/schema.sql", lineno=30, start_byte=400, end_byte=440,
    replacement_sha256=_sha("o9-sql-crashed"), operator="sql:widen-check-in",
    description="widen the IN-list by one member",
)
SQL_EQUIVALENT = MutantOutcome(
    path="db/schema.sql", lineno=40, start_byte=500, end_byte=530,
    replacement_sha256=_sha("o9-sql-equivalent"), operator="sql:weaken-delete-action",
    description="RESTRICT -> CASCADE",
)


def _sql_r2_policy(
    *,
    operators: tuple[str, ...] = SQL_OPERATORS,
    equivalence_artifact: str | None = ".assay/schema-dump.sql",
    kill_signal_artifact: str | None = ".assay/kill-signal.txt",
    kill_attribution: str = "declared",
) -> JudgmentR2:
    return JudgmentR2(
        jobs=1,
        max_mutants=200,
        operators=operators,
        kill_attribution=kill_attribution,
        kill_signal_artifact=kill_signal_artifact,
        equivalence_artifact=equivalence_artifact,
    )


def _valid_sql_verdict() -> Verdict:
    """(O9, positive) The carve's own §5.2 target artifact, built through
    the MODEL: ``language = "sql"``, all seven operators declared, both
    artifacts declared, ``kill_attribution = "declared"``, and a mutation
    payload populating ``killed`` (carrying a ``kill_signal``), ``survived``,
    ``crashed`` and ``equivalent`` SIMULTANEOUSLY. ``crashed`` outranks every
    other bucket (``mutation.judge_mutation``'s own precedence), so the R2
    claim -- and the whole verdict -- renders ``ERROR``/``EXEC_FAILED``.
    Constructing this at all (no exception) IS the model-valid half of O9;
    the two assertions below are the schema-valid and raw-verifier-clean
    halves."""
    mutation = Mutation(
        candidate_count=4,
        total=4,
        killed=(SQL_KILLED,),
        survived=(SQL_SURVIVED,),
        crashed=(SQL_CRASHED,),
        equivalent=(SQL_EQUIVALENT,),
    )
    r2_claim = Claim(
        rigor="R2", source="computed", status=Outcome.ERROR,
        verified_by_assay=True, reason_code=ReasonCode.EXEC_FAILED, mutation=mutation,
    )
    return Verdict(
        **BASE,
        commit="7" * 39 + "1",
        outcome=Outcome.ERROR,
        reason_code=ReasonCode.EXEC_FAILED,
        started="2026-08-18T00:00:00+00:00",
        ended="2026-08-18T00:00:05+00:00",
        judgment=Judgment(resolved=SQL_RESOLVED, r2=_sql_r2_policy()),
        claims=(R0_PASS, r2_claim),
    )


def test_sql_full_artifact_all_four_mutation_buckets_is_valid_at_every_layer(
    validator: Draft202012Validator,
):
    """(O9, positive/must-succeed control) The full SQL artifact -- all four
    buckets, both declared artifacts, declared attribution -- is model-valid
    (construction above does not raise), schema-valid, and
    raw-verifier-clean: three independent layers, none generated from
    another (this module's own docstring). Every negative below is this
    SAME artifact with exactly one field broken."""
    verdict = _valid_sql_verdict()
    document = json.loads(verdict.to_json())

    _validate(document, validator)
    assert verify_document(document) == []

    assert document["outcome"] == "ERROR"
    assert document["reason_code"] == "EXEC_FAILED"
    assert document["judgment"]["resolved"]["language"] == "sql"
    assert document["judgment"]["r2"]["kill_attribution"] == "declared"
    mutation = document["claims"][1]["mutation"]
    assert len(mutation["killed"]) == 1
    assert len(mutation["survived"]) == 1
    assert len(mutation["crashed"]) == 1
    assert len(mutation["equivalent"]) == 1
    assert mutation["killed"][0]["kill_signal"]


def test_o9_negative_a_sql_killed_with_no_kill_signal_under_declared_attribution(
    validator: Draft202012Validator,
):
    """(O9 negative a) ``kill_attribution = "declared"`` requires a
    ``kill_signal`` on EVERY killed entry (§3.6) -- without it a lane would
    claim an attribution it never demonstrated. The schema alone accepts
    this (``kill_signal`` is per-bucket-legal, never per-bucket-required --
    A-182, no ``$data``); only the model and the raw verifier catch it,
    which is the whole point of this negative."""
    unsignalled = MutantOutcome(
        path=SQL_KILLED.path, lineno=SQL_KILLED.lineno,
        start_byte=SQL_KILLED.start_byte, end_byte=SQL_KILLED.end_byte,
        replacement_sha256=SQL_KILLED.replacement_sha256, operator=SQL_KILLED.operator,
        description=SQL_KILLED.description,
    )
    mutation = Mutation(candidate_count=1, total=1, killed=(unsignalled,))
    with pytest.raises(ValueError, match="kill_attribution 'declared' but"):
        Verdict(
            **BASE,
            commit="7" * 39 + "2",
            outcome=Outcome.PASS,
            started="2026-08-18T00:01:00+00:00",
            ended="2026-08-18T00:01:05+00:00",
            judgment=Judgment(resolved=SQL_RESOLVED, r2=_sql_r2_policy()),
            claims=(
                R0_PASS,
                Claim(
                    rigor="R2", source="computed", status=Outcome.PASS,
                    verified_by_assay=True, mutation=mutation,
                ),
            ),
        )

    document = json.loads(_valid_sql_verdict().to_json())
    del document["claims"][1]["mutation"]["killed"][0]["kill_signal"]
    assert why_invalid(validator, document) == [], (
        "the schema alone must accept this -- it has no cross-object $data "
        "relation to express 'declared attribution requires every kill to "
        "carry a signal', which is exactly why the deeper layers exist"
    )
    failures = verify_document(document)
    assert failures, "raw verifier accepted a declared kill with no kill_signal"


def test_o9_negative_b_sql_equivalent_bucket_with_no_declared_equivalence_artifact(
    validator: Draft202012Validator,
):
    """(O9 negative b) The ``equivalent`` bucket and
    ``judgment.r2.equivalence_artifact`` are both-present-or-both-absent
    (P33/V5-3 invariant 2). Declaring neither is legal; a lane with the
    bucket populated and no declared artifact would be claiming equivalence
    was proven by nothing."""
    mutation = Mutation(candidate_count=1, total=1, equivalent=(SQL_EQUIVALENT,))
    with pytest.raises(ValueError, match="declares no equivalence_artifact"):
        Verdict(
            **BASE,
            commit="7" * 39 + "3",
            outcome=Outcome.INCONCLUSIVE,
            reason_code=ReasonCode.ALL_MUTANTS_EQUIVALENT,
            started="2026-08-18T00:02:00+00:00",
            ended="2026-08-18T00:02:05+00:00",
            judgment=Judgment(
                resolved=SQL_RESOLVED,
                r2=_sql_r2_policy(equivalence_artifact=None),
            ),
            claims=(
                R0_PASS,
                Claim(
                    rigor="R2", source="computed", status=Outcome.INCONCLUSIVE,
                    verified_by_assay=True, reason_code=ReasonCode.ALL_MUTANTS_EQUIVALENT,
                    mutation=mutation,
                ),
            ),
        )

    document = json.loads(_valid_sql_verdict().to_json())
    del document["judgment"]["r2"]["equivalence_artifact"]
    assert why_invalid(validator, document) == [], (
        "the schema alone must accept this too -- equivalence pairing is a "
        "cross-object relation, not local grammar"
    )
    failures = verify_document(document)
    assert failures, (
        "raw verifier accepted an equivalent bucket with no declared "
        "equivalence_artifact"
    )


def test_o9_negative_c_a_python_operator_declared_on_a_sql_lane(
    validator: Draft202012Validator,
):
    """(O9 negative c) ``judgment.resolved.language`` qualifies every
    operator a lane may declare or record (P33/V5-2 invariant 1). A sql
    lane naming ``python:compare-swap`` -- a globally KNOWN operator, just
    the wrong language -- is refused independently by the model and the raw
    verifier; the schema's own ``mutation_operator`` enum is global across
    all three languages and has no way to relate an operator to the
    resolved language, so it alone accepts this document."""
    with pytest.raises(ValueError, match="another language's catalogue"):
        Verdict(
            **BASE,
            commit="7" * 39 + "4",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.EXEC_FAILED,
            started="2026-08-18T00:03:00+00:00",
            ended="2026-08-18T00:03:05+00:00",
            judgment=Judgment(
                resolved=SQL_RESOLVED,
                r2=_sql_r2_policy(operators=(*SQL_OPERATORS, "python:compare-swap")),
            ),
            claims=(
                R0_PASS,
                Claim(
                    rigor="R2", source="computed", status=Outcome.ERROR,
                    verified_by_assay=True, reason_code=ReasonCode.EXEC_FAILED,
                    mutation=Mutation(
                        candidate_count=4,
                        total=4,
                        killed=(SQL_KILLED,),
                        survived=(SQL_SURVIVED,),
                        crashed=(SQL_CRASHED,),
                        equivalent=(SQL_EQUIVALENT,),
                    ),
                ),
            ),
        )

    document = json.loads(_valid_sql_verdict().to_json())
    document["judgment"]["r2"]["operators"].append("python:compare-swap")
    assert why_invalid(validator, document) == [], (
        "the schema's operator enum is global, not per-language, so it "
        "must accept this -- the language cross-check is model/verifier-only"
    )
    failures = verify_document(document)
    assert failures, "raw verifier accepted a foreign-language operator on a sql lane"
