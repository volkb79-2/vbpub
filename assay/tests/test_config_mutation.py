"""P18 work item 1 — ``judge.mutation`` is a CLOSED table, validated at
LOAD time rather than passed through opaque (P11/P12's own contract,
before this package): a required positive-integer ``jobs`` and a
non-empty, duplicate-free ``operators`` list drawn from
:data:`assay.mutation.MUTATION_OPERATORS`'s own closed vocabulary
(A-068's "cross-checked against the vocabulary's own owner" discipline,
one field over from ``judge.coverage.format``/``FORMAT_REGISTRY``).
"""

from __future__ import annotations

import pytest
from conftest import R0_LANE, Project, set_key

from assay.config import load_lane_file
from assay.errors import LaneConfigError
from assay.mutation import MUTATION_OPERATORS
from assay.vocabulary import MUTATION_OPERATORS_BY_LANGUAGE

R2_JUDGE = """
[lanes.package.judge]
language = "{language}"
source_roots = ["src"]
base = "main"
"""


#: B006a/A-269, schema v2: every R1+ lane requires an explicit [isolation]
#: table; this module's own subject is `judge.mutation`, so every case here
#: picks the plain "repository" selection.
_ISOLATION_TABLE = '\n[lanes.package.isolation]\nsnapshot_selection = "repository"\n'


def _lane_with_mutation(mutation_table: str, *, language: str = "python") -> str:
    """(P33/V5-2) *language* is now a parameter, because the operator
    vocabulary is closed PER LANGUAGE: which operators a lane may declare is
    a function of what it says it is."""
    body = set_key(R0_LANE, "rigor", '["R0", "R2"]')
    return body + _ISOLATION_TABLE + R2_JUDGE.format(language=language) + mutation_table


def test_a_minimal_valid_mutation_table_loads(project: Project):
    lane = _lane_with_mutation(
        "\n[lanes.package.judge.mutation]\njobs = 1\nmax_mutants = 50\n"
        "operators = [\"python:compare-swap\"]\n"
    )
    judge = load_lane_file(project.write(lane)).lane("package").judge

    assert judge is not None
    assert judge.mutation is not None
    assert judge.mutation.jobs == 1
    assert judge.mutation.operators == ("python:compare-swap",)


def test_every_declared_operator_is_accepted(project: Project):
    """Direction 1 of the closed-vocabulary pair: every member of
    :data:`MUTATION_OPERATORS` loads on its own -- **under the language that
    owns it** (P33/V5-2). Iterating the per-language map rather than the flat
    tuple is what makes this a real sweep now: the flat tuple would have
    declared `sql:drop-check` on a Python lane, which is precisely the
    combination v5 exists to refuse."""
    for language, operators in MUTATION_OPERATORS_BY_LANGUAGE.items():
        for operator in sorted(operators):
            lane = _lane_with_mutation(
                f"\n[lanes.package.judge.mutation]\njobs = 1\nmax_mutants = 50\n"
                f'operators = ["{operator}"]\n',
                language=language,
            )
            judge = load_lane_file(project.write(lane, name=f"{operator}.toml")).lane(
                "package"
            ).judge
            assert judge is not None and judge.mutation is not None
            assert judge.mutation.operators == (operator,)
    # The flat tuple is exactly the union, so the sweep above really is total.
    assert set(MUTATION_OPERATORS) == {
        operator
        for operators in MUTATION_OPERATORS_BY_LANGUAGE.values()
        for operator in operators
    }


def test_an_operator_belonging_to_another_language_is_refused(project: Project):
    """Direction 3 (P33/V5-2/O2): `sql:drop-check` IS a known operator, so
    this is not the unknown-operator rejection one field over -- it is the
    cross-language one, and the message has to say so or an operator name a
    consumer typed correctly reads as a typo. A rejected value per language,
    not one rejection standing for all three."""
    for language, foreign in (
        ("python", "sql:drop-check"),
        ("go", "python:falsy-swap"),
        ("sql", "go:compare-swap"),
    ):
        lane = _lane_with_mutation(
            f"\n[lanes.package.judge.mutation]\njobs = 1\nmax_mutants = 50\n"
            f'operators = ["{foreign}"]\n',
            language=language,
        )
        with pytest.raises(LaneConfigError) as excinfo:
            load_lane_file(project.write(lane, name=f"{language}-{foreign}.toml"))
        message = str(excinfo.value)
        assert foreign in message, "the refusal must name the offending operator"
        assert "another language" in message
        assert f"{language!r}" in message


def test_an_unknown_operator_is_rejected(project: Project):
    """Direction 2: a name outside the closed vocabulary is refused, and
    the message names both the offender and the known set."""
    lane = _lane_with_mutation(
        '\n[lanes.package.judge.mutation]\njobs = 1\nmax_mutants = 50\noperators = ["typo-swap"]\n'
    )
    with pytest.raises(LaneConfigError) as exc:
        load_lane_file(project.write(lane))
    assert "typo-swap" in str(exc.value)
    for operator in sorted(MUTATION_OPERATORS):
        assert operator in str(exc.value)


def test_operators_is_order_preserving(project: Project):
    lane = _lane_with_mutation(
        "\n[lanes.package.judge.mutation]\njobs = 1\nmax_mutants = 50\n"
        'operators = ["python:falsy-swap", "python:compare-swap", "python:boolop-swap"]\n'
    )
    judge = load_lane_file(project.write(lane)).lane("package").judge

    assert judge is not None and judge.mutation is not None
    assert judge.mutation.operators == ("python:falsy-swap", "python:compare-swap", "python:boolop-swap")


def test_an_empty_operators_list_is_rejected(project: Project):
    lane = _lane_with_mutation(
        "\n[lanes.package.judge.mutation]\njobs = 1\nmax_mutants = 50\noperators = []\n"
    )
    with pytest.raises(LaneConfigError, match="operators' is empty"):
        load_lane_file(project.write(lane))


def test_a_duplicate_operator_is_rejected(project: Project):
    lane = _lane_with_mutation(
        "\n[lanes.package.judge.mutation]\njobs = 1\nmax_mutants = 50\n"
        'operators = ["python:compare-swap", "python:compare-swap"]\n'
    )
    with pytest.raises(LaneConfigError, match="operators' contains a duplicate"):
        load_lane_file(project.write(lane))


@pytest.mark.parametrize("bad_jobs", ["0", "-1", "true", '"2"', "1.5"])
def test_jobs_must_be_a_positive_integer(bad_jobs: str, project: Project):
    lane = _lane_with_mutation(
        f'\n[lanes.package.judge.mutation]\njobs = {bad_jobs}\nmax_mutants = 50\noperators = ["python:compare-swap"]\n'
    )
    with pytest.raises(LaneConfigError, match="judge.mutation.jobs"):
        load_lane_file(project.write(lane))


def test_a_missing_jobs_field_is_rejected(project: Project):
    lane = _lane_with_mutation(
        '\n[lanes.package.judge.mutation]\noperators = ["python:compare-swap"]\n'
    )
    with pytest.raises(LaneConfigError, match="missing required field 'judge.mutation.jobs'"):
        load_lane_file(project.write(lane))


def test_a_missing_operators_field_is_rejected(project: Project):
    lane = _lane_with_mutation("\n[lanes.package.judge.mutation]\njobs = 1\nmax_mutants = 50\n")
    with pytest.raises(
        LaneConfigError, match="missing required field 'judge.mutation.operators'"
    ):
        load_lane_file(project.write(lane))


def test_an_unknown_mutation_key_is_rejected(project: Project):
    lane = _lane_with_mutation(
        "\n[lanes.package.judge.mutation]\njobs = 1\nmax_mutants = 50\n"
        'operators = ["python:compare-swap"]\ntimeout = 5\n'
    )
    with pytest.raises(LaneConfigError, match="unknown judge.mutation key"):
        load_lane_file(project.write(lane))


def test_mutation_as_declared_round_trips_exactly(project: Project):
    lane = _lane_with_mutation(
        "\n[lanes.package.judge.mutation]\njobs = 3\nmax_mutants = 50\n"
        'operators = ["python:compare-swap", "python:falsy-swap"]\n'
    )
    loaded = load_lane_file(project.write(lane)).lane("package")

    assert loaded.as_declared()["judge"]["mutation"] == {
        "jobs": 3,
        "max_mutants": 50,
        "operators": ["python:compare-swap", "python:falsy-swap"],
    }
