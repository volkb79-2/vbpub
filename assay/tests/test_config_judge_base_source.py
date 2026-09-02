"""B019/A-328 — ``judge.base_source``: WHO owns the comparison commit.

A lane declaring ``base_source = "request"`` still requires changed-line
judging; what it delegates is only the base's IDENTITY, to whatever gate
request invokes it. That is what keeps one static lane declaration correct
across every branch and worktree while CIU V8 owns branch-aware
orchestration.

The two owners are mutually exclusive by construction, and every disagreement
between them is refused BY NAME rather than settled by a precedence rule.
A-062's argument, applied one field over: whichever side lost a precedence
contest would be configuration that nothing reads, and therefore
configuration that cannot fail loudly if it is wrong. Both refusals name the
one line to delete.
"""

from __future__ import annotations

from conftest import R0_LANE, R1_LANE, Project, drop_key, set_key

import pytest

from assay.config import JUDGE_BASE_SOURCES, load_lane_file
from assay.errors import LaneConfigError

MUTATION_TABLE = """
[lanes.package.judge.mutation]
jobs = 4
max_mutants = 50
operators = ["python:compare-swap"]
"""


def _delegating_r1_lane() -> str:
    """`R1_LANE` with the base DELEGATED instead of declared -- literally the
    one-line edit a consumer adopting B019 makes, applied to the shared
    template so nothing else about the lane can drift."""
    lane = R1_LANE.replace('base = "main"', 'base_source = "request"', 1)
    assert 'base_source = "request"' in lane and "base = " not in lane
    return lane


# --- the vocabulary is closed -----------------------------------------------


def test_the_two_owners_are_the_whole_vocabulary():
    assert JUDGE_BASE_SOURCES == frozenset({"declared", "request"})


def test_an_unknown_base_source_is_refused_by_name(project: Project):
    lane = R1_LANE.replace('base = "main"', 'base_source = "inherited"', 1)
    with pytest.raises(LaneConfigError, match="judge.base_source' must be one of"):
        load_lane_file(project.write(lane))


def test_a_non_string_base_source_is_refused(project: Project):
    lane = R1_LANE.replace('base = "main"', "base_source = 3", 1)
    with pytest.raises(LaneConfigError, match="judge.base_source"):
        load_lane_file(project.write(lane))


# --- the delegating lane loads, and records what it declared ----------------


def test_a_delegating_r1_lane_loads_without_declaring_a_base(project: Project):
    """The point of the whole feature: `judge.base` is no longer required for
    a changed-line lane that named someone else to supply it."""
    judge = load_lane_file(project.write(_delegating_r1_lane())).lane("package").judge

    assert judge is not None
    assert judge.base is None
    assert judge.base_source == "request"


def test_a_declaring_lane_still_records_base_source_as_absent(project: Project):
    """`None` means ABSENT FROM THE FILE, never "assay chose declared" --
    `mode`'s own contract, and the reason the loader stores the declared
    value rather than a resolved default."""
    judge = load_lane_file(project.write(R1_LANE)).lane("package").judge

    assert judge is not None
    assert judge.base == "main"
    assert judge.base_source is None


def test_base_source_round_trips_through_as_declared(project: Project):
    declared = load_lane_file(project.write(_delegating_r1_lane())).lane("package")
    assert declared.as_declared()["judge"]["base_source"] == "request"
    assert "base" not in declared.as_declared()["judge"]


# --- and every placement that reads nothing is refused ----------------------


def test_declaring_both_a_base_and_a_request_source_is_refused(project: Project):
    """The conflict this feature could most easily have resolved silently.
    Refused instead, naming both lines and the one to delete."""
    lane = R1_LANE.replace(
        'base = "main"', 'base = "main"\nbase_source = "request"', 1
    )
    with pytest.raises(LaneConfigError, match="declares BOTH 'judge.base' and"):
        load_lane_file(project.write(lane))


def test_declaring_base_source_on_an_r0_only_lane_is_refused(project: Project):
    """A-062's inert-config rule: R0 reads no comparison commit, so naming
    who supplies one declares nothing."""
    lane = R0_LANE + '\n[lanes.package.judge]\nbase_source = "request"\n'
    with pytest.raises(LaneConfigError, match="includes neither R1 nor R2"):
        load_lane_file(project.write(lane))


def test_declaring_base_source_on_an_r3_only_lane_is_refused(project: Project):
    lane = set_key(R0_LANE, "rigor", '["R0", "R3"]')
    lane += '\n[lanes.package.isolation]\nsnapshot_selection = "repository"\n'
    lane += (
        "\n[lanes.package.judge]\n"
        'language = "python"\n'
        'source_roots = ["src"]\n'
        'base_source = "request"\n'
        "\n[lanes.package.judge.canary]\n"
        'mechanism = "import-break"\n'
        'target = "src/mod.py"\n'
    )
    with pytest.raises(LaneConfigError, match="includes neither R1 nor R2"):
        load_lane_file(project.write(lane))


def test_declaring_base_source_under_whole_target_mode_is_refused(project: Project):
    """A-325's rule, inherited: whole-target scope replaces the diff at every
    tier, so there is no comparison commit for anyone to supply. `judge.base`
    is refused there; a policy ABOUT `judge.base` has to be refused with it,
    or the loader would accept a lane declaring who owns a value no tier
    reads."""
    lane = set_key(R0_LANE, "rigor", '["R0", "R2"]')
    lane += '\n[lanes.package.isolation]\nsnapshot_selection = "repository"\n'
    lane += (
        "\n[lanes.package.judge]\n"
        'language = "python"\n'
        'source_roots = ["src"]\n'
        'mode = "whole_target"\n'
        'targets = ["src/mod.py"]\n'
        'base_source = "request"\n'
    ) + MUTATION_TABLE
    with pytest.raises(LaneConfigError, match="whole-target"):
        load_lane_file(project.write(lane))


def test_a_delegating_r2_lane_loads_the_same_way_an_r1_one_does(project: Project):
    """R2 is the tier CIU V8 actually cares about most, so its own path is
    proven rather than assumed to follow R1's."""
    lane = set_key(R0_LANE, "rigor", '["R0", "R2"]')
    lane += '\n[lanes.package.isolation]\nsnapshot_selection = "repository"\n'
    lane += (
        "\n[lanes.package.judge]\n"
        'language = "python"\n'
        'source_roots = ["src"]\n'
        'base_source = "request"\n'
    ) + MUTATION_TABLE
    judge = load_lane_file(project.write(lane)).lane("package").judge

    assert judge is not None
    assert judge.base is None
    assert judge.base_source == "request"


def test_declaring_base_source_declared_still_requires_the_base(project: Project):
    """The explicit spelling of the default is not a way to omit the value:
    "I own this" and "here it is" are different statements, and only the
    second one supplies a commit."""
    lane = drop_key(R1_LANE, "base")
    lane = lane.replace(
        'allow_excluded = false', 'allow_excluded = false\nbase_source = "declared"', 1
    )
    with pytest.raises(LaneConfigError, match="missing required field 'judge.base'"):
        load_lane_file(project.write(lane))
