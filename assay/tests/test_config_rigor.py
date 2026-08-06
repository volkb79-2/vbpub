"""O2 — declared rigor is ENFORCED, and the judge fields are CONDITIONAL.

A-017: a lane claiming a rigor level it cannot exercise is the
lane-table-implies-capability failure in data form, so R1 requires the coverage
five, R2 requires mutation and R3 requires canary.

A-048 is the other half, and it is the half the pre-flight found broken: those
five are **conditionally** required. An R0-only lane has no ``[judge]`` table at
all — which is exactly the lane this package ships as assay's own ``assay.toml``
— so a loader that requires them unconditionally rejects its own project.

Requirements are per DECLARED level, not per highest level, because ``rigor`` is
a list of independently declared methods (§6: a lane declaring
``["R0","R1","R2"]`` can pass R0, pass R1 and be INCONCLUSIVE on R2). R2 and R3
pull in ``language`` and ``source_roots`` as well as their own table, because
mutation and canary cannot act at all without knowing the adapter and the tree.
"""

from __future__ import annotations

import pytest
from conftest import R0_LANE, R1_LANE, Project, drop_key, set_key

from assay.config import JUDGE_FIELDS_BY_RIGOR, load_lane_file
from assay.errors import LaneConfigError

#: O2's five, spelled out rather than imported, so a change to the source of
#: truth has to be made deliberately in two places.
R1_JUDGE_FIELDS = ("coverage", "fail_under", "allow_excluded", "source_roots", "language")

JUDGE_TABLE = """
[lanes.package.judge]
language = "python"
source_roots = ["src"]
fail_under = 100.0
allow_excluded = false
coverage = { format = "coverage-py-json", artifact = "cov.json" }
"""

MUTATION_TABLE = """
[lanes.package.judge.mutation]
jobs = 4
operators = ["compare-swap", "boolop-swap"]
"""

CANARY_TABLE = """
[lanes.package.judge.canary]
mode = "import-break"
"""


def _lane_with(rigor: list[str], *tables: str) -> str:
    body = set_key(R0_LANE, "rigor", "[" + ", ".join(f'"{r}"' for r in rigor) + "]")
    return body + "".join(tables)


def test_r0_only_lane_with_no_judge_table_loads_clean(project: Project):
    lane = load_lane_file(project.write(R0_LANE)).lane("package")

    assert lane.rigor == ("R0",)
    assert lane.judge is None


def test_r1_lane_with_all_five_loads(project: Project):
    lane = load_lane_file(project.write(R1_LANE)).lane("package")

    judge = lane.judge
    assert judge is not None
    assert judge.language == "python"
    assert judge.source_roots == ("src", "scripts")
    assert judge.fail_under == 100.0
    assert judge.allow_excluded is False
    assert judge.coverage is not None


@pytest.mark.parametrize("field", R1_JUDGE_FIELDS)
def test_r1_lane_missing_any_of_the_five_is_rejected(field: str, project: Project):
    # Direction 1 — the complete R1 lane loads.
    assert load_lane_file(project.write(R1_LANE)).lane("package").judge is not None

    # Direction 2 — the same lane minus one judge field does not.
    mutant = project.write(drop_key(R1_LANE, field), name="mutant.toml")
    with pytest.raises(LaneConfigError) as excinfo:
        load_lane_file(mutant)
    assert f"judge.{field}" in str(excinfo.value)


def test_r1_lane_with_no_judge_table_at_all_is_rejected(project: Project):
    path = project.write(_lane_with(["R0", "R1"]))
    with pytest.raises(LaneConfigError) as excinfo:
        load_lane_file(path)
    message = str(excinfo.value)
    assert "no [judge] table" in message
    for field in R1_JUDGE_FIELDS:
        assert field in message


def test_r2_additionally_requires_mutation(project: Project):
    without = project.write(
        _lane_with(["R0", "R1", "R2"], JUDGE_TABLE), name="without.toml"
    )
    with pytest.raises(LaneConfigError, match="judge.mutation"):
        load_lane_file(without)

    with_it = project.write(
        _lane_with(["R0", "R1", "R2"], JUDGE_TABLE, MUTATION_TABLE),
        name="with.toml",
    )
    judge = load_lane_file(with_it).lane("package").judge
    assert judge is not None
    assert judge.mutation is not None
    assert dict(judge.mutation) == {
        "jobs": 4,
        "operators": ["compare-swap", "boolop-swap"],
    }
    assert judge.canary is None


def test_r3_additionally_requires_canary(project: Project):
    without = project.write(
        _lane_with(["R0", "R1", "R3"], JUDGE_TABLE), name="without.toml"
    )
    with pytest.raises(LaneConfigError, match="judge.canary"):
        load_lane_file(without)

    with_it = project.write(
        _lane_with(["R0", "R1", "R3"], JUDGE_TABLE, CANARY_TABLE), name="with.toml"
    )
    judge = load_lane_file(with_it).lane("package").judge
    assert judge is not None
    assert dict(judge.canary or {}) == {"mode": "import-break"}
    assert judge.mutation is None


def test_full_ladder_lane_loads_with_every_table(project: Project):
    path = project.write(
        _lane_with(["R0", "R1", "R2", "R3"], JUDGE_TABLE, MUTATION_TABLE, CANARY_TABLE)
    )
    judge = load_lane_file(path).lane("package").judge

    assert judge is not None
    assert judge.mutation is not None
    assert judge.canary is not None
    assert judge.coverage is not None


def test_r2_without_r1_requires_only_what_mutation_needs(project: Project):
    # Per-declared-level, not a ladder: a lane that declares mutation but not a
    # coverage floor needs `language` and `source_roots` (mutation cannot act
    # without them) but not `fail_under`/`allow_excluded`/`coverage`.
    partial = """
[lanes.package.judge]
language = "python"
source_roots = ["src"]
"""
    path = project.write(_lane_with(["R0", "R2"], partial, MUTATION_TABLE))
    judge = load_lane_file(path).lane("package").judge

    assert judge is not None
    assert judge.mutation is not None
    assert judge.fail_under is None
    assert judge.coverage is None


def test_r2_without_r1_still_requires_source_roots(project: Project):
    partial = """
[lanes.package.judge]
language = "python"
"""
    path = project.write(_lane_with(["R0", "R2"], partial, MUTATION_TABLE))
    with pytest.raises(LaneConfigError, match="judge.source_roots"):
        load_lane_file(path)


def test_surplus_judge_config_for_an_undeclared_level_is_allowed(
    project: Project,
):
    # The rigor LIST is the claim. A mutation table on a lane that does not
    # declare R2 makes no claim, and refusing it would break the ordinary
    # workflow of writing the config before declaring the level.
    path = project.write(_lane_with(["R0", "R1"], JUDGE_TABLE, MUTATION_TABLE))
    judge = load_lane_file(path).lane("package").judge

    assert judge is not None
    assert judge.mutation is not None


def test_judge_requirements_table_matches_the_decisions(project: Project):
    assert JUDGE_FIELDS_BY_RIGOR["R0"] == ()
    assert set(JUDGE_FIELDS_BY_RIGOR["R1"]) == set(R1_JUDGE_FIELDS)
    assert "mutation" in JUDGE_FIELDS_BY_RIGOR["R2"]
    assert "canary" in JUDGE_FIELDS_BY_RIGOR["R3"]


def test_judge_table_on_an_r0_lane_is_allowed_but_not_required(project: Project):
    # The conditional requirement is one-directional: R0 does not *forbid* a
    # judge table, it merely does not require one.
    path = project.write(_lane_with(["R0"], JUDGE_TABLE))
    judge = load_lane_file(path).lane("package").judge

    assert judge is not None
    assert judge.language == "python"


def test_judge_that_is_not_a_table_is_rejected(project: Project):
    path = project.write(R0_LANE + 'judge = "python"\n')
    with pytest.raises(LaneConfigError, match="'judge' must be a table"):
        load_lane_file(path)


def test_empty_source_roots_list_is_rejected(project: Project):
    path = project.write(set_key(R1_LANE, "source_roots", "[]"))
    with pytest.raises(LaneConfigError, match="measures nothing"):
        load_lane_file(path)


def test_coverage_missing_a_key_is_rejected(project: Project):
    path = project.write(
        set_key(R1_LANE, "coverage", '{ format = "coverage-py-json" }')
    )
    with pytest.raises(LaneConfigError, match="judge.coverage.artifact"):
        load_lane_file(path)


def test_coverage_with_an_unknown_key_is_rejected(project: Project):
    path = project.write(
        set_key(
            R1_LANE,
            "coverage",
            '{ format = "lcov", artifact = "c.info", branch = true }',
        )
    )
    with pytest.raises(LaneConfigError, match="unknown judge.coverage key"):
        load_lane_file(path)


def test_coverage_format_is_not_enumerated_here(project: Project):
    # §12 says the vocabulary is "a key the parser registry knows" — and the
    # registry is P03's. Duplicating its key list in the loader would recreate
    # the four-copies divergence one layer down, so any non-empty string loads
    # and P03/P04 does the cross-check.
    path = project.write(
        set_key(R1_LANE, "coverage", '{ format = "lcov", artifact = "cov.info" }')
    )
    judge = load_lane_file(path).lane("package").judge
    assert judge is not None
    assert judge.coverage is not None
    assert judge.coverage.format == "lcov"
