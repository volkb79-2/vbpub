"""P17 work item 1 — ``judge.base`` is required for R1 and R2, never
defaulted to ``"main"`` or another guessed ref (A-018's own "no invented
values"): the string a real ``git merge-base``/``resolve_base`` call
resolves at RUN time, recorded verbatim on the loaded :class:`~assay.config.
JudgeConfig`.
"""

from __future__ import annotations

from conftest import R0_LANE, R1_LANE, Project, drop_key, set_key

import pytest

from assay.config import JUDGE_FIELDS_BY_RIGOR, load_lane_file
from assay.errors import LaneConfigError

MUTATION_TABLE = """
[lanes.package.judge.mutation]
jobs = 4
max_mutants = 50
operators = ["compare-swap"]
"""

R2_JUDGE = """
[lanes.package.judge]
language = "python"
source_roots = ["src"]
base = "main"
"""


def _lane_with(rigor: list[str], *tables: str) -> str:
    body = set_key(R0_LANE, "rigor", "[" + ", ".join(f'"{r}"' for r in rigor) + "]")
    return body + "".join(tables)


def test_base_is_declared_in_judge_fields_by_rigor_for_r1_and_r2():
    assert "base" in JUDGE_FIELDS_BY_RIGOR["R1"]
    assert "base" in JUDGE_FIELDS_BY_RIGOR["R2"]
    assert "base" not in JUDGE_FIELDS_BY_RIGOR["R3"]
    assert JUDGE_FIELDS_BY_RIGOR["R0"] == ()


def test_r1_lane_missing_base_is_rejected_by_name(project: Project):
    mutant = project.write(drop_key(R1_LANE, "base"))
    with pytest.raises(LaneConfigError, match="judge.base"):
        load_lane_file(mutant)


def test_r1_lane_records_the_declared_base_string_verbatim(project: Project):
    lane = set_key(R1_LANE, "base", '"origin/release-42"')
    judge = load_lane_file(project.write(lane)).lane("package").judge

    assert judge is not None
    assert judge.base == "origin/release-42"


def test_r1_lane_with_an_empty_base_is_rejected(project: Project):
    lane = set_key(R1_LANE, "base", '""')
    with pytest.raises(LaneConfigError, match="'judge.base' is empty"):
        load_lane_file(project.write(lane))


def test_r1_lane_with_a_non_string_base_is_rejected(project: Project):
    lane = set_key(R1_LANE, "base", "42")
    with pytest.raises(LaneConfigError, match="'judge.base' must be a string"):
        load_lane_file(project.write(lane))


def test_r2_without_r1_also_requires_base(project: Project):
    without_base = """
[lanes.package.judge]
language = "python"
source_roots = ["src"]
"""
    path = project.write(_lane_with(["R0", "R2"], without_base, MUTATION_TABLE))
    with pytest.raises(LaneConfigError, match="judge.base"):
        load_lane_file(path)

    with_base = project.write(_lane_with(["R0", "R2"], R2_JUDGE, MUTATION_TABLE))
    judge = load_lane_file(with_base).lane("package").judge
    assert judge is not None
    assert judge.base == "main"


def test_base_is_never_invented_for_an_r0_only_lane(project: Project):
    # A-048's own shape: an R0-only lane has no [judge] table at all, so
    # `base` never defaults to "main" -- it stays genuinely absent (None),
    # never a guessed value a caller could mistake for a real declaration.
    lane = load_lane_file(project.write(R0_LANE)).lane("package")
    assert lane.judge is None


def test_base_round_trips_through_as_declared(project: Project):
    declared = load_lane_file(project.write(R1_LANE)).lane("package").as_declared()
    assert declared["judge"]["base"] == "main"
