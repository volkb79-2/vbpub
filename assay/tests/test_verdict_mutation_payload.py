"""A-116/A-180/A-163 -- :class:`~assay.verdict.MutantOutcome` and
:class:`~assay.verdict.Mutation`'s own construction-time discipline, in the
v4 shape.

The negative this defends (O2/O4): *dropping unattempted identities,
treating crashes as killed, keeping killed as a bare count, or reporting a
truncated sample as a complete run differs from the complete expected
artifact.* Proven here at the construction boundary, where a forged artifact
is refused rather than merely unaccepted (A-136's discipline: where a
producer proves a state unreachable, make it unconstructible).

Three v4 changes drive this module:

* **killed is an identity list.** v3's bare count meant the one bucket a
  reader most needs to audit could not be bound to the declared operator
  policy at all.
* **identity is the syntax site**, ``(path, start_byte, end_byte,
  replacement_sha256, operator)`` -- line and description diagnose, they do
  not distinguish, so two `and` tokens on one line no longer collapse.
* **candidate_count** distinguishes a supported empty analysis from a
  pre-submission refusal at the declared cap.
"""

from __future__ import annotations

import dataclasses

import pytest

from assay.verdict import MAX_CANDIDATE_CEILING, Mutation, MutantOutcome
from assay.vocabulary import MAX_INGESTED_MUTANTS

#: sha256(b"<=") and sha256(b"or"), hand-computed rather than read back from
#: the code under test (A-067).
SHA_LTE = "b60080dc8b8982d2a2bff6f8f3715c1939614dc553cd223ef21832b88c815866"
SHA_OR = "7175517a370b5cd2e664e3fd29c4ea9db5ce17058eb9772fe090a5485e49dad6"


def _outcome(**overrides) -> MutantOutcome:
    fields = dict(
        path="pkg/mod.py",
        lineno=3,
        start_byte=40,
        end_byte=41,
        replacement_sha256=SHA_LTE,
        operator="python:compare-swap",
        description="Lt->LtE",
    )
    fields.update(overrides)
    return MutantOutcome(**fields)


# --- MutantOutcome's own construction-time discipline --------------------


def test_a_mutant_outcome_constructs_with_valid_fields():
    outcome = _outcome()
    assert outcome.path == "pkg/mod.py"
    assert outcome.lineno == 3
    assert outcome.start_byte == 40
    assert outcome.end_byte == 41
    assert outcome.replacement_sha256 == SHA_LTE
    assert outcome.operator == "python:compare-swap"
    assert outcome.description == "Lt->LtE"


def test_a_mutant_outcome_is_frozen():
    outcome = _outcome()
    with pytest.raises(dataclasses.FrozenInstanceError):
        outcome.lineno = 99  # type: ignore[misc]


def test_identity_is_the_site_not_its_diagnosis():
    """A-180: two records differing only in line and description name the
    SAME experiment; two differing in span do not. v3's identity got both
    of these backwards."""
    assert _outcome().identity == _outcome(lineno=99, description="other").identity
    assert _outcome().identity != _outcome(start_byte=50, end_byte=51).identity
    assert _outcome().identity != _outcome(operator="python:boolop-swap").identity
    assert _outcome().identity != _outcome(replacement_sha256=SHA_OR).identity


@pytest.mark.parametrize(
    "overrides,match",
    [
        ({"path": ""}, "non-empty string"),
        ({"path": None}, "non-empty string"),
        ({"path": 42}, "non-empty string"),
        ({"path": "/abs/mod.py"}, "must be relative"),
        ({"path": "pkg/../mod.py"}, "not normalized"),
        ({"path": "pkg/./mod.py"}, "not normalized"),
        ({"path": "pkg//mod.py"}, "not normalized"),
        ({"path": "pkg/mod.py/"}, "must be relative"),
        ({"path": "pkg\\mod.py"}, "backslash"),
        ({"lineno": 0}, "lineno"),
        ({"lineno": -1}, "lineno"),
        ({"lineno": 1.5}, "must be an integer"),
        ({"lineno": True}, "must be an integer"),
        ({"start_byte": -1}, "start_byte must be >= 0"),
        ({"start_byte": "40"}, "must be an integer"),
        ({"start_byte": 41, "end_byte": 41}, "empty or reversed"),
        ({"start_byte": 42, "end_byte": 41}, "empty or reversed"),
        ({"replacement_sha256": SHA_LTE.upper()}, "lowercase hex"),
        ({"replacement_sha256": SHA_LTE[:-1]}, "lowercase hex"),
        ({"replacement_sha256": "zz" + SHA_LTE[2:]}, "lowercase hex"),
        ({"replacement_sha256": None}, "lowercase hex"),
        ({"operator": "invented-swap"}, "must be one of"),
        ({"operator": ""}, "must be one of"),
        ({"description": ""}, "non-empty string"),
    ],
)
def test_a_mutant_outcome_that_could_not_name_a_real_site_is_refused(
    overrides: dict, match: str
):
    with pytest.raises(ValueError, match=match):
        _outcome(**overrides)


def test_to_dict_carries_the_whole_identity_plus_its_diagnosis():
    assert _outcome().to_dict() == {
        "path": "pkg/mod.py",
        "lineno": 3,
        "start_byte": 40,
        "end_byte": 41,
        "replacement_sha256": SHA_LTE,
        "operator": "python:compare-swap",
        "description": "Lt->LtE",
    }


# --- Mutation: the two legal arithmetic shapes ---------------------------


def test_a_normal_payload_agrees_with_its_four_buckets():
    killed = _outcome()
    survivor = _outcome(start_byte=50, end_byte=51)
    mutation = Mutation(
        candidate_count=2, total=2, killed=(killed,), survived=(survivor,)
    )

    assert mutation.total == 2
    assert mutation.candidate_count == 2
    assert not mutation.is_limit_sentinel


def test_a_supported_empty_analysis_is_zero_over_zero():
    """Distinct from the sentinel below, and from capability absence, which
    carries no payload at all (A-183)."""
    mutation = Mutation(candidate_count=0, total=0)

    assert mutation.total == 0
    assert not mutation.is_limit_sentinel


def test_the_limit_sentinel_records_observation_without_attempt():
    """A-163: candidates were observed and assay deliberately STOPPED. The
    shape itself is the evidence for the refusal -- a truncated list would
    be indistinguishable from a complete small run."""
    mutation = Mutation(candidate_count=51, total=0)

    assert mutation.is_limit_sentinel
    assert mutation.to_dict()["candidate_count"] == 51
    assert mutation.to_dict()["killed"] == []


@pytest.mark.parametrize(
    "kwargs,match",
    [
        # total must equal the recorded identities
        ({"candidate_count": 2, "total": 3, "killed": (_outcome(),)}, "must equal"),
        ({"candidate_count": 1, "total": 0, "killed": (_outcome(),)}, "must equal"),
        # (B070/v11) fewer candidates than attempted: a mutant that was
        # attempted was first observed as a candidate, so this direction stays
        # refused. The OTHER direction -- `candidate_count > total` -- became
        # legal at v11 and is asserted below rather than here: a candidate can
        # now be observed and never attempted (the discarded disposition), and
        # attributing that residual is `Verdict`'s job, not this object's.
        (
            {"candidate_count": 0, "total": 1, "killed": (_outcome(),)},
            "candidate_count is never below total",
        ),
        ({"candidate_count": -1, "total": 0}, "must not be negative"),
        ({"candidate_count": "2", "total": 2}, "must be an integer"),
        ({"candidate_count": True, "total": 1}, "must be an integer"),
        ({"total": -1, "candidate_count": 0}, "must not be negative"),
        # (B070 fix round 1) The bound `Mutation` alone can state is the
        # DOCUMENT ceiling, not the native product ceiling: this object cannot
        # see `judgment.r2.producer`, and `MAX_CANDIDATE_CEILING` defends
        # against a malicious DECLARED cap that only a native lane has.
        # `MAX_CANDIDATE_CEILING + 1` is now a legal ingested payload -- see
        # `test_the_payload_ceiling_is_the_document_bound_not_the_native_one`
        # below -- and the refusal here is at the document bound.
        (
            {"candidate_count": MAX_INGESTED_MUTANTS + 1, "total": 0},
            "document ceiling",
        ),
    ],
)
def test_a_payload_between_the_two_legal_shapes_is_refused(kwargs: dict, match: str):
    with pytest.raises(ValueError, match=match):
        Mutation(**kwargs)


def test_the_payload_ceiling_is_the_document_bound_not_the_native_one():
    """(B070 fix round 1) The boundary, both sides, at the level the bound
    lives.

    `MAX_CANDIDATE_CEILING` is `max_mutants + 1` and is documented as a
    defence against a malicious DECLARED cap — something only a native lane
    has (A-360). While `candidate_count` was the bucket sum, applying it here
    was harmless; the moment B070 made it `attempted + discarded`, it started
    refusing a truthful ingested report for DISCARDING too much, which is the
    failure DA-R26 rejected route 3 for. The native ceiling now lives one
    level up, in `Verdict._check_mutation_cardinality`, where the producer is
    visible.
    """
    one = _outcome()

    # The review's own reproduction, scaled: over the NATIVE ceiling, and
    # legal here because this object cannot know it is not ingested.
    over_native = Mutation(
        candidate_count=MAX_CANDIDATE_CEILING + 1, total=1, killed=(one,)
    )
    assert over_native.candidate_count == 10_002

    # ACCEPTED exactly at the document bound...
    at_bound = Mutation(candidate_count=MAX_INGESTED_MUTANTS, total=1, killed=(one,))
    assert at_bound.candidate_count == 100_000

    # ...and REFUSED one past it. (The parametrised row above asserts the
    # message; this asserts the boundary is where it is claimed to be.)
    with pytest.raises(ValueError, match="document ceiling"):
        Mutation(candidate_count=MAX_INGESTED_MUTANTS + 1, total=1, killed=(one,))


def test_a_residual_is_now_LEGAL_in_the_payload_and_attributed_one_level_up():
    """(B070, schema v11) The rule this method used to enforce, and why it
    could not survive the fifth disposition.

    Through v10 ``candidate_count != total`` outside the limit sentinel was
    refused HERE — a rule written when the five buckets were the only
    dispositions a candidate could have. An ingested report now records the
    mutants it marked ``CompileError``/``RuntimeError`` on
    ``judgment.r2.discarded`` instead of dropping them, and those mutants
    genuinely WERE candidates and genuinely were never attempted, so the
    honest document has a residual. Refusing it here would make the honest
    document illegal, which is B070's own diagnosis of why the old
    ``discarded`` count could never be verified.

    The residual is never left unexplained: ``Verdict.
    _check_discarded_disposition`` requires it to equal
    ``len(judgment.r2.discarded)`` under ``producer = "ingested"`` and to be
    zero outside the limit sentinel under ``"native"``, and ``assay.verify``
    states the same rule independently at the raw layer. This test asserts
    only the half this object owns.
    """
    payload = Mutation(candidate_count=5, total=1, killed=(_outcome(),))

    assert payload.candidate_count - payload.total == 4
    assert not payload.is_limit_sentinel, (
        "a residual beside attempted work is not the pre-submission sentinel; "
        "the sentinel attempts NOTHING"
    )

    # And the sentinel's own shape is untouched by the widening.
    sentinel = Mutation(candidate_count=51, total=0)
    assert sentinel.is_limit_sentinel


@pytest.mark.parametrize("bucket", ["killed", "survived", "crashed", "budget_exceeded"])
def test_every_bucket_must_be_a_tuple_of_real_outcomes(bucket: str):
    with pytest.raises(ValueError, match="must be a tuple"):
        Mutation(candidate_count=1, total=1, **{bucket: [_outcome()]})
    with pytest.raises(ValueError, match="must be MutantOutcome"):
        Mutation(candidate_count=1, total=1, **{bucket: ("not-an-outcome",)})


@pytest.mark.parametrize("bucket", ["killed", "survived", "crashed", "budget_exceeded"])
def test_every_bucket_is_sorted_by_identity_not_by_completion(bucket: str):
    """O3: `jobs=1` and `jobs=3` must render identical records, so the order
    can never be whichever subprocess finished first."""
    first = _outcome(start_byte=10, end_byte=11)
    second = _outcome(start_byte=20, end_byte=21)

    with pytest.raises(ValueError, match="must be sorted"):
        Mutation(candidate_count=2, total=2, **{bucket: (second, first)})

    Mutation(candidate_count=2, total=2, **{bucket: (first, second)})


@pytest.mark.parametrize("bucket", ["killed", "survived", "crashed", "budget_exceeded"])
def test_one_identity_cannot_appear_twice_in_a_bucket(bucket: str):
    """v3 tolerated ties because line+description could genuinely collide.
    Under the site identity two equal entries ARE one experiment recorded
    twice, which would inflate the bucket a reader acts on."""
    outcome = _outcome()
    with pytest.raises(ValueError, match="same mutant identity twice"):
        Mutation(candidate_count=2, total=2, **{bucket: (outcome, outcome)})


def test_one_identity_cannot_appear_in_two_buckets():
    """A-182: one mutant, one outcome. Cross-bucket duplication is how a
    forged artifact would claim a kill it did not earn while still
    reporting the survivor honestly."""
    outcome = _outcome()
    with pytest.raises(ValueError, match="appears in both"):
        Mutation(
            candidate_count=2, total=2, killed=(outcome,), survived=(outcome,)
        )


def test_two_sites_sharing_a_line_and_description_stay_distinct():
    """A-115's boolean chain, which v3's identity collapsed: `a and b and c`
    is one AST node but two independently targetable `and` tokens, so two
    outcomes legitimately share line AND description."""
    first = _outcome(
        operator="python:boolop-swap",
        description="And->Or",
        replacement_sha256=SHA_OR,
        start_byte=44,
        end_byte=47,
    )
    second = _outcome(
        operator="python:boolop-swap",
        description="And->Or",
        replacement_sha256=SHA_OR,
        start_byte=52,
        end_byte=55,
    )

    mutation = Mutation(candidate_count=2, total=2, survived=(first, second))

    assert len({item.identity for item in mutation.survived}) == 2


def test_to_dict_emits_all_four_buckets_and_the_candidate_count():
    mutation = Mutation(candidate_count=1, total=1, killed=(_outcome(),))
    payload = mutation.to_dict()

    assert payload["candidate_count"] == 1
    assert payload["total"] == 1
    assert payload["killed"] == [_outcome().to_dict()]
    assert payload["survived"] == []
    assert payload["crashed"] == []
    assert payload["budget_exceeded"] == []
