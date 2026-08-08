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

R2_JUDGE = """
[lanes.package.judge]
language = "python"
source_roots = ["src"]
base = "main"
"""


def _lane_with_mutation(mutation_table: str) -> str:
    body = set_key(R0_LANE, "rigor", '["R0", "R2"]')
    return body + R2_JUDGE + mutation_table


def test_a_minimal_valid_mutation_table_loads(project: Project):
    lane = _lane_with_mutation(
        "\n[lanes.package.judge.mutation]\njobs = 1\noperators = [\"compare-swap\"]\n"
    )
    judge = load_lane_file(project.write(lane)).lane("package").judge

    assert judge is not None
    assert judge.mutation is not None
    assert judge.mutation.jobs == 1
    assert judge.mutation.operators == ("compare-swap",)


def test_every_declared_operator_is_accepted(project: Project):
    """Direction 1 of the closed-vocabulary pair: every member of
    :data:`MUTATION_OPERATORS` loads on its own."""
    for operator in sorted(MUTATION_OPERATORS):
        lane = _lane_with_mutation(
            f'\n[lanes.package.judge.mutation]\njobs = 1\noperators = ["{operator}"]\n'
        )
        judge = load_lane_file(project.write(lane, name=f"{operator}.toml")).lane(
            "package"
        ).judge
        assert judge is not None and judge.mutation is not None
        assert judge.mutation.operators == (operator,)


def test_an_unknown_operator_is_rejected(project: Project):
    """Direction 2: a name outside the closed vocabulary is refused, and
    the message names both the offender and the known set."""
    lane = _lane_with_mutation(
        '\n[lanes.package.judge.mutation]\njobs = 1\noperators = ["typo-swap"]\n'
    )
    with pytest.raises(LaneConfigError) as exc:
        load_lane_file(project.write(lane))
    assert "typo-swap" in str(exc.value)
    for operator in sorted(MUTATION_OPERATORS):
        assert operator in str(exc.value)


def test_operators_is_order_preserving(project: Project):
    lane = _lane_with_mutation(
        "\n[lanes.package.judge.mutation]\njobs = 1\n"
        'operators = ["falsy-swap", "compare-swap", "boolop-swap"]\n'
    )
    judge = load_lane_file(project.write(lane)).lane("package").judge

    assert judge is not None and judge.mutation is not None
    assert judge.mutation.operators == ("falsy-swap", "compare-swap", "boolop-swap")


def test_an_empty_operators_list_is_rejected(project: Project):
    lane = _lane_with_mutation(
        "\n[lanes.package.judge.mutation]\njobs = 1\noperators = []\n"
    )
    with pytest.raises(LaneConfigError, match="operators' is empty"):
        load_lane_file(project.write(lane))


def test_a_duplicate_operator_is_rejected(project: Project):
    lane = _lane_with_mutation(
        "\n[lanes.package.judge.mutation]\njobs = 1\n"
        'operators = ["compare-swap", "compare-swap"]\n'
    )
    with pytest.raises(LaneConfigError, match="operators' contains a duplicate"):
        load_lane_file(project.write(lane))


@pytest.mark.parametrize("bad_jobs", ["0", "-1", "true", '"2"', "1.5"])
def test_jobs_must_be_a_positive_integer(bad_jobs: str, project: Project):
    lane = _lane_with_mutation(
        f'\n[lanes.package.judge.mutation]\njobs = {bad_jobs}\noperators = ["compare-swap"]\n'
    )
    with pytest.raises(LaneConfigError, match="judge.mutation.jobs"):
        load_lane_file(project.write(lane))


def test_a_missing_jobs_field_is_rejected(project: Project):
    lane = _lane_with_mutation(
        '\n[lanes.package.judge.mutation]\noperators = ["compare-swap"]\n'
    )
    with pytest.raises(LaneConfigError, match="missing required field 'judge.mutation.jobs'"):
        load_lane_file(project.write(lane))


def test_a_missing_operators_field_is_rejected(project: Project):
    lane = _lane_with_mutation("\n[lanes.package.judge.mutation]\njobs = 1\n")
    with pytest.raises(
        LaneConfigError, match="missing required field 'judge.mutation.operators'"
    ):
        load_lane_file(project.write(lane))


def test_an_unknown_mutation_key_is_rejected(project: Project):
    lane = _lane_with_mutation(
        "\n[lanes.package.judge.mutation]\njobs = 1\n"
        'operators = ["compare-swap"]\ntimeout = 5\n'
    )
    with pytest.raises(LaneConfigError, match="unknown judge.mutation key"):
        load_lane_file(project.write(lane))


def test_mutation_as_declared_round_trips_exactly(project: Project):
    lane = _lane_with_mutation(
        "\n[lanes.package.judge.mutation]\njobs = 3\n"
        'operators = ["compare-swap", "falsy-swap"]\n'
    )
    loaded = load_lane_file(project.write(lane)).lane("package")

    assert loaded.as_declared()["judge"]["mutation"] == {
        "jobs": 3,
        "operators": ["compare-swap", "falsy-swap"],
    }
