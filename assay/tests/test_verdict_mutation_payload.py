"""A-116 -- :class:`~assay.verdict.MutantOutcome` and
:class:`~assay.verdict.Mutation`'s own construction-time discipline: the
exact template :class:`~assay.verdict.Coverage`/:class:`~assay.verdict.
CanaryResult` already established, restated for the R2 payload's own FOUR
buckets.

The negative this defends (O4, verbatim): *dropping unattempted identities,
treating crashes as killed, or universal PASS differs from the complete
expected artifact.* Proven here at the construction boundary: ``total``
must equal the sum of all four buckets, each identity-carrying bucket must
be a tuple of real :class:`MutantOutcome`, sorted by stable identity (never
by whichever mutant happened to finish first).
"""

from __future__ import annotations

import dataclasses

import pytest

from assay.verdict import Mutation, MutantOutcome


def _outcome(**overrides) -> MutantOutcome:
    fields = dict(
        path="pkg/mod.py", lineno=3, operator="compare-swap", description="Lt->LtE"
    )
    fields.update(overrides)
    return MutantOutcome(**fields)


# --- MutantOutcome's own construction-time discipline --------------------


def test_a_mutant_outcome_constructs_with_valid_fields():
    outcome = _outcome()
    assert outcome.path == "pkg/mod.py"
    assert outcome.lineno == 3
    assert outcome.operator == "compare-swap"
    assert outcome.description == "Lt->LtE"


def test_a_mutant_outcome_is_frozen():
    outcome = _outcome()
    with pytest.raises(dataclasses.FrozenInstanceError):
        outcome.lineno = 99  # type: ignore[misc]


@pytest.mark.parametrize("bad_path", ["", None, 42])
def test_mutant_outcome_rejects_an_empty_or_non_string_path(bad_path):
    with pytest.raises(ValueError, match="path"):
        _outcome(path=bad_path)


@pytest.mark.parametrize("bad_lineno", [0, -1, 1.5, "3", True, False])
def test_mutant_outcome_rejects_a_non_positive_or_non_integer_lineno(bad_lineno):
    with pytest.raises(ValueError, match="lineno"):
        _outcome(lineno=bad_lineno)


@pytest.mark.parametrize("bad_operator", ["", None, 42])
def test_mutant_outcome_rejects_an_empty_or_non_string_operator(bad_operator):
    with pytest.raises(ValueError, match="operator"):
        _outcome(operator=bad_operator)


@pytest.mark.parametrize("bad_description", ["", None, 42])
def test_mutant_outcome_rejects_an_empty_or_non_string_description(bad_description):
    with pytest.raises(ValueError, match="description"):
        _outcome(description=bad_description)


def test_mutant_outcome_to_dict_carries_every_field():
    assert _outcome().to_dict() == {
        "path": "pkg/mod.py",
        "lineno": 3,
        "operator": "compare-swap",
        "description": "Lt->LtE",
    }


# --- Mutation's own construction-time discipline --------------------------


def test_a_killed_only_mutation_constructs():
    mutation = Mutation(total=3, killed=3)
    assert mutation.total == 3
    assert mutation.killed == 3
    assert mutation.survived == ()
    assert mutation.crashed == ()
    assert mutation.budget_exceeded == ()


def test_mutation_is_frozen():
    mutation = Mutation(total=0, killed=0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        mutation.killed = 1  # type: ignore[misc]


@pytest.mark.parametrize("field", ["total", "killed"])
def test_mutation_rejects_a_negative_count(field):
    with pytest.raises(ValueError, match=field):
        Mutation(**{"total": 0, "killed": 0, field: -1})


@pytest.mark.parametrize("field", ["total", "killed"])
def test_mutation_rejects_a_non_integer_count(field):
    with pytest.raises(ValueError, match=field):
        Mutation(**{"total": 0, "killed": 0, field: 1.5})


def test_mutation_total_must_equal_the_sum_of_all_four_buckets():
    """O4's own negative, made concrete: a total that silently drops an
    attempted identity (here: a survived mutant not counted) is refused --
    it can never render a complete expected artifact."""
    survivor = MutantOutcome(
        path="pkg/mod.py", lineno=3, operator="compare-swap", description="Lt->LtE"
    )
    # the correct total (0 killed + 1 survived) is accepted.
    Mutation(total=1, killed=0, survived=(survivor,))
    # total UNDER-counts the real attempted (and recorded) survivor.
    with pytest.raises(ValueError, match="mutation.total"):
        Mutation(total=0, killed=0, survived=(survivor,))


def test_a_killed_mutant_is_never_counted_as_a_crash_or_survivor():
    """O4's negative: 'treating crashes as killed' -- proven the other
    direction here: total must reflect EVERY bucket, so silently emptying
    crashed while inflating killed would violate the invariant."""
    crashed = MutantOutcome(
        path="pkg/mod.py", lineno=3, operator="compare-swap", description="Lt->LtE"
    )
    with pytest.raises(ValueError, match="mutation.total"):
        # total says 1 mutant ran, killed says it was killed, but a crashed
        # identity is ALSO recorded -- total (1) does not equal killed (1)
        # + len(crashed) (1) = 2.
        Mutation(total=1, killed=1, crashed=(crashed,))


def test_survived_must_be_a_tuple_of_mutant_outcome():
    with pytest.raises(ValueError, match="survived"):
        Mutation(total=1, killed=0, survived=("not-a-mutant-outcome",))  # type: ignore[arg-type]


@pytest.mark.parametrize("bucket", ["survived", "crashed", "budget_exceeded"])
def test_each_bucket_must_itself_be_a_tuple_not_a_list_or_other_sequence(bucket):
    survivor = MutantOutcome(
        path="pkg/mod.py", lineno=3, operator="compare-swap", description="Lt->LtE"
    )
    with pytest.raises(ValueError, match="must be a tuple"):
        Mutation(total=1, killed=0, **{bucket: [survivor]})


@pytest.mark.parametrize("bucket", ["survived", "crashed", "budget_exceeded"])
def test_each_bucket_must_be_sorted_by_stable_identity(bucket):
    """O3's own ordering discipline, restated at construction: never by
    whichever mutant happened to finish first."""
    first = MutantOutcome(
        path="pkg/mod.py", lineno=9, operator="compare-swap", description="Lt->LtE"
    )
    second = MutantOutcome(
        path="pkg/mod.py", lineno=3, operator="compare-swap", description="Lt->LtE"
    )
    with pytest.raises(ValueError, match="sorted"):
        Mutation(total=2, killed=0, **{bucket: (first, second)})
    # the correctly sorted order is accepted.
    Mutation(total=2, killed=0, **{bucket: (second, first)})


def test_ties_under_the_sort_key_are_legal():
    """A-115's own boolean-chain trap, one level up: two genuinely
    different mutants can project onto the IDENTICAL MutantOutcome (same
    path/lineno/operator/description, differing only in the discarded
    mutated_text) -- sortedness allows ties, never requires uniqueness."""
    tied = MutantOutcome(
        path="pkg/mod.py", lineno=5, operator="boolop-swap", description="And->Or"
    )
    mutation = Mutation(total=2, killed=0, survived=(tied, tied))
    assert mutation.survived == (tied, tied)


def test_to_dict_carries_all_four_buckets():
    survivor = MutantOutcome(
        path="pkg/mod.py", lineno=3, operator="compare-swap", description="Lt->LtE"
    )
    mutation = Mutation(total=2, killed=1, survived=(survivor,))
    assert mutation.to_dict() == {
        "total": 2,
        "killed": 1,
        "survived": [survivor.to_dict()],
        "crashed": [],
        "budget_exceeded": [],
    }
