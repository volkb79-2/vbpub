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

import tomllib

import pytest
from conftest import R0_LANE, R1_LANE, Project, drop_key, set_key

from assay.config import CanaryConfig, JUDGE_FIELDS_BY_RIGOR, load_lane_file
from assay.errors import LaneConfigError

#: O2's six, spelled out rather than imported, so a change to the source of
#: truth has to be made deliberately in two places. P17 adds ``base`` (the
#: declared comparison ref, required wherever a diff is measured).
R1_JUDGE_FIELDS = (
    "coverage",
    "fail_under",
    "allow_excluded",
    "source_roots",
    "language",
    "base",
)

JUDGE_TABLE = """
[lanes.package.judge]
language = "python"
source_roots = ["src"]
fail_under = 100.0
allow_excluded = false
coverage = { format = "coverage-py-json", artifact = "cov.json" }
base = "main"
"""

MUTATION_TABLE = """
[lanes.package.judge.mutation]
jobs = 4
operators = ["compare-swap", "boolop-swap"]
"""

CANARY_TABLE = """
[lanes.package.judge.canary]
mechanism = "import-break"
target = "src/mod.py"
"""

# Exactly what R2 requires and nothing more (A-062): language + source_roots
# + base (P17 -- mutation measures the same declared diff R1 does), with the
# mutation sub-table appended separately. A lane declaring R2 alone may not
# carry R1's fail_under/allow_excluded/coverage.
R2_MINIMAL_JUDGE = """
[lanes.package.judge]
language = "python"
source_roots = ["src"]
base = "main"
"""


def _lane_with(rigor: list[str], *tables: str) -> str:
    body = set_key(R0_LANE, "rigor", "[" + ", ".join(f'"{r}"' for r in rigor) + "]")
    return body + "".join(tables)


def test_r0_only_lane_with_no_judge_table_loads_clean(project: Project):
    lane = load_lane_file(project.write(R0_LANE)).lane("package")

    assert lane.rigor == ("R0",)
    assert lane.judge is None


def test_r1_lane_with_all_six_loads(project: Project):
    lane = load_lane_file(project.write(R1_LANE)).lane("package")

    judge = lane.judge
    assert judge is not None
    assert judge.language == "python"
    assert judge.source_roots == ("src", "scripts")
    assert judge.fail_under == 100.0
    assert judge.allow_excluded is False
    assert judge.coverage is not None
    assert judge.base == "main"


@pytest.mark.parametrize("field", R1_JUDGE_FIELDS)
def test_r1_lane_missing_any_of_the_six_is_rejected(field: str, project: Project):
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
    assert judge.mutation.jobs == 4
    assert judge.mutation.operators == ("compare-swap", "boolop-swap")
    assert judge.canary is None


def test_r3_additionally_requires_canary(project: Project):
    project.file("src/mod.py", "x = 1\n")
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
    assert judge.canary == CanaryConfig(mechanism="import-break", target="src/mod.py")
    assert judge.mutation is None


def test_full_ladder_lane_loads_with_every_table(project: Project):
    project.file("src/mod.py", "x = 1\n")
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
base = "main"
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


def test_surplus_judge_config_for_an_undeclared_level_is_refused(
    project: Project,
):
    # A-062. `mutation` on a lane that does not declare R2 is INERT: nothing
    # reads it, so if it is wrong nothing fails -- while it reads to a human
    # exactly like the capability it is not providing. Same shape as a lane
    # table listing five lanes over two real ones, one level down.
    surplus = _lane_with(["R0", "R1"], JUDGE_TABLE, MUTATION_TABLE)
    path = project.write(surplus)

    with pytest.raises(LaneConfigError) as exc:
        load_lane_file(path)
    # The message must name the inert key AND the two remedies, or the reader
    # cannot act on it.
    assert "mutation" in str(exc.value)
    assert "declare the rigor level" in str(exc.value)
    assert "delete it" in str(exc.value)

    # The mirror, in the same body (the anti-hollow pattern this suite uses):
    # declaring the level that consumes it makes the SAME table load.
    declared = _lane_with(["R0", "R1", "R2"], JUDGE_TABLE, MUTATION_TABLE)
    judge = load_lane_file(project.write(declared)).lane("package").judge

    assert judge is not None
    assert judge.mutation is not None


def test_judge_requirements_table_matches_the_decisions(project: Project):
    assert JUDGE_FIELDS_BY_RIGOR["R0"] == ()
    assert set(JUDGE_FIELDS_BY_RIGOR["R1"]) == set(R1_JUDGE_FIELDS)
    assert "mutation" in JUDGE_FIELDS_BY_RIGOR["R2"]
    assert "canary" in JUDGE_FIELDS_BY_RIGOR["R3"]


def test_a_populated_judge_table_on_an_r0_lane_is_refused(project: Project):
    # A-062, and the direct reading of DESIGN-GUIDE 12: "an R0-only lane has no
    # [judge] table at all". R0 requires no judge field, so every key in such a
    # table is inert -- `fail_under = 100` here would read to a human as a
    # coverage floor while nothing whatsoever enforces it.
    path = project.write(_lane_with(["R0"], JUDGE_TABLE))
    with pytest.raises(LaneConfigError) as exc:
        load_lane_file(path)
    assert "reads none of" in str(exc.value)

    # The mirror: declaring the level that consumes it makes the SAME table load.
    same_table_with_r1 = _lane_with(["R0", "R1"], JUDGE_TABLE)
    judge = load_lane_file(project.write(same_table_with_r1)).lane("package").judge

    assert judge is not None
    assert judge.language == "python"


def test_full_ladder_lane_round_trips_its_mutation_and_canary_tables(
    project: Project,
):
    # The proof that this loader does not silently invent or drop a field
    # is that the closed mutation/canary tables come back byte-for-byte.
    project.file("src/mod.py", "x = 1\n")
    text = _lane_with(
        ["R0", "R1", "R2", "R3"], JUDGE_TABLE, MUTATION_TABLE, CANARY_TABLE
    )
    declared = load_lane_file(project.write(text)).lane("package").as_declared()

    assert declared == tomllib.loads(text)["lanes"]["package"]
    assert declared["judge"]["mutation"] == {
        "jobs": 4,
        "operators": ["compare-swap", "boolop-swap"],
    }
    assert declared["judge"]["canary"] == {
        "mechanism": "import-break",
        "target": "src/mod.py",
    }


def test_a_judge_table_may_declare_only_what_it_needs(project: Project):
    # Nothing in `judge` is filled in on absence: a field this level does not
    # require, and the file did not declare, stays None rather than being
    # invented. An R2 lane needs language + source_roots + mutation + base
    # and NOTHING else -- so R1's three coverage-floor fields must come back
    # None, not defaulted.
    path = project.write(_lane_with(["R2"], R2_MINIMAL_JUDGE, MUTATION_TABLE))
    lane = load_lane_file(path).lane("package")

    judge = lane.judge
    assert judge is not None
    assert judge.mutation is not None
    assert judge.fail_under is None
    assert judge.allow_excluded is None
    assert judge.coverage is None
    assert judge.canary is None
    assert judge.base == "main"
    # as_declared() reconstructs exactly the file's own keys -- no absent field
    # reappears with a filled-in value (P07 depends on this for argv_declared).
    assert lane.as_declared()["judge"] == {
        "language": "python",
        "source_roots": ["src"],
        "mutation": {"jobs": 4, "operators": ["compare-swap", "boolop-swap"]},
        "base": "main",
    }


def test_an_empty_judge_table_is_the_only_one_an_r0_lane_may_carry(
    project: Project,
):
    # The exact boundary A-062 draws. R0 requires no judge field, so any
    # POPULATED table is entirely surplus and refused -- but an empty table
    # declares nothing and claims nothing, so there is nothing to refuse.
    # Every judge field must then come back None rather than invented, and
    # as_declared() must report an empty table rather than a filled-in one.
    path = project.write(_lane_with(["R0"]) + "\n[lanes.package.judge]\n")
    lane = load_lane_file(path).lane("package")

    judge = lane.judge
    assert judge is not None
    assert judge.language is None
    assert judge.source_roots is None
    assert judge.source_root_paths is None
    assert judge.fail_under is None
    assert judge.allow_excluded is None
    assert judge.coverage is None
    assert judge.mutation is None
    assert judge.canary is None
    assert lane.as_declared()["judge"] == {}


def test_mutation_that_is_not_a_table_is_rejected(project: Project):
    # Declared at R2, so `mutation` is REQUIRED here and the surplus guard
    # (A-062) is not what rejects it -- the type check is. Using an R0 lane
    # would pass this test for the wrong reason.
    path = project.write(
        _lane_with(["R2"], R2_MINIMAL_JUDGE).rstrip("\n") + "\nmutation = 4\n"
    )
    with pytest.raises(LaneConfigError, match="'judge.mutation' must be a table"):
        load_lane_file(path)


def test_canary_that_is_not_a_table_is_rejected(project: Project):
    # The identical shape one field over from `test_mutation_that_is_not_a_
    # table_is_rejected`, now that `canary` has its own dedicated loader
    # (P19) -- declared at R3, so `canary` is REQUIRED and the type check,
    # not the surplus guard, is what rejects it.
    r3_judge = """
[lanes.package.judge]
language = "python"
source_roots = ["src"]
"""
    path = project.write(
        _lane_with(["R3"], r3_judge).rstrip("\n") + "\ncanary = 4\n"
    )
    with pytest.raises(LaneConfigError, match="'judge.canary' must be a table"):
        load_lane_file(path)


def test_empty_judge_language_is_rejected(project: Project):
    path = project.write(set_key(R1_LANE, "language", '""'))
    with pytest.raises(LaneConfigError, match="'judge.language' is empty"):
        load_lane_file(path)


def test_coverage_that_is_not_a_table_is_rejected(project: Project):
    path = project.write(set_key(R1_LANE, "coverage", '"coverage-py-json"'))
    with pytest.raises(LaneConfigError, match="'judge.coverage' must be a table"):
        load_lane_file(path)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ('{ format = "", artifact = "cov.json" }', "judge.coverage.format' is empty"),
        ('{ format = "lcov", artifact = "" }', "judge.coverage.artifact' is empty"),
    ],
)
def test_empty_coverage_field_is_rejected(
    value: str, expected: str, project: Project
):
    path = project.write(set_key(R1_LANE, "coverage", value))
    with pytest.raises(LaneConfigError, match=expected):
        load_lane_file(path)


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


def test_coverage_format_is_cross_checked_against_the_parser_registry(
    project: Project,
):
    # §12 says the vocabulary is "a key the parser registry knows" — and the
    # registry is P03's `assay.coverage.FORMAT_REGISTRY`, imported here rather
    # than duplicated (A-068). "lcov" is a real registry key, so it loads;
    # tests/test_config_coverage_format.py covers the reject direction (an
    # unregistered key) and the full accept sweep over every registered key.
    path = project.write(
        set_key(R1_LANE, "coverage", '{ format = "lcov", artifact = "cov.info" }')
    )
    judge = load_lane_file(path).lane("package").judge
    assert judge is not None
    assert judge.coverage is not None
    assert judge.coverage.format == "lcov"
