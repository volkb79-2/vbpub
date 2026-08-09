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
        operator="compare-swap",
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
    assert outcome.operator == "compare-swap"
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
    assert _outcome().identity != _outcome(operator="boolop-swap").identity
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
        "operator": "compare-swap",
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
        # neither normal nor sentinel: attempted work with a mismatched count
        (
            {"candidate_count": 5, "total": 1, "killed": (_outcome(),)},
            "either normal",
        ),
        # a sentinel may not carry identities
        (
            {"candidate_count": 51, "total": 1, "killed": (_outcome(),)},
            "either normal",
        ),
        ({"candidate_count": -1, "total": 0}, "must not be negative"),
        ({"candidate_count": "2", "total": 2}, "must be an integer"),
        ({"candidate_count": True, "total": 1}, "must be an integer"),
        ({"total": -1, "candidate_count": 0}, "must not be negative"),
        (
            {"candidate_count": MAX_CANDIDATE_CEILING + 1, "total": 0},
            "product ceiling",
        ),
    ],
)
def test_a_payload_between_the_two_legal_shapes_is_refused(kwargs: dict, match: str):
    with pytest.raises(ValueError, match=match):
        Mutation(**kwargs)


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
        operator="boolop-swap",
        description="And->Or",
        replacement_sha256=SHA_OR,
        start_byte=44,
        end_byte=47,
    )
    second = _outcome(
        operator="boolop-swap",
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
