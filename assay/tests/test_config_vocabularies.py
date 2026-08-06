"""O4 — closed vocabularies (A-053) and a budget that is PARSED (A-052).

Both directions, everywhere: every member of every vocabulary is asserted to
load, and non-members are asserted to fail. A loader that rejects `S9` because
it rejects everything fails the first half of each pair.

The budget half is the sharper one. A-052 says the duration is *parsed* at load,
not merely checked for presence, because a malformed duration discovered at run
time in P07 is a failure the config layer could have caught. So the tests assert
the numeric value, not just that loading succeeded.
"""

from __future__ import annotations

import pytest
from conftest import R0_LANE, R1_LANE, Project, set_key

from assay.config import ENFORCEMENTS, RIGOR_LEVELS, SCOPES, load_lane_file, parse_duration
from assay.errors import LaneConfigError

JUDGE_TABLE = """
[lanes.package.judge]
language = "python"
source_roots = ["src"]
fail_under = 100.0
allow_excluded = false
coverage = { format = "coverage-py-json", artifact = "cov.json" }

[lanes.package.judge.mutation]
jobs = 2

[lanes.package.judge.canary]
mode = "import-break"
"""


def test_the_vocabularies_are_exactly_what_the_decisions_close():
    assert SCOPES == {"S0", "S1", "S2", "S3", "S4"}
    assert RIGOR_LEVELS == ("R0", "R1", "R2", "R3")
    assert ENFORCEMENTS == {"gate", "advisory"}


@pytest.mark.parametrize("scope", sorted(SCOPES))
def test_every_declared_scope_loads(scope: str, project: Project):
    lane = load_lane_file(
        project.write(set_key(R0_LANE, "scope", f'"{scope}"'))
    ).lane("package")
    assert lane.scope == scope


@pytest.mark.parametrize("scope", ["S5", "s1", "S", "", "S1 ", "SCOPE"])
def test_scope_outside_the_vocabulary_is_rejected(scope: str, project: Project):
    path = project.write(set_key(R0_LANE, "scope", f'"{scope}"'))
    with pytest.raises(LaneConfigError, match="'scope' must be one of"):
        load_lane_file(path)


@pytest.mark.parametrize("level", RIGOR_LEVELS)
def test_every_declared_rigor_level_loads(level: str, project: Project):
    # Each level brings its own judge requirement, so the lane carries the full
    # judge table; the point under test is that the LEVEL NAME is accepted.
    text = set_key(R0_LANE, "rigor", f'["{level}"]') + JUDGE_TABLE
    lane = load_lane_file(project.write(text)).lane("package")
    assert lane.rigor == (level,)


@pytest.mark.parametrize("level", ["R4", "r1", "R", "", "R0 "])
def test_rigor_outside_the_vocabulary_is_rejected(level: str, project: Project):
    path = project.write(set_key(R0_LANE, "rigor", f'["{level}"]'))
    with pytest.raises(LaneConfigError, match="'rigor' must contain only"):
        load_lane_file(path)


def test_rigor_may_not_be_empty(project: Project):
    path = project.write(set_key(R0_LANE, "rigor", "[]"))
    with pytest.raises(LaneConfigError, match="declares no level"):
        load_lane_file(path)


def test_duplicate_rigor_level_is_rejected(project: Project):
    # assay records one claim per declared level (A-024); two R0 entries would
    # mean two claims for one method.
    path = project.write(set_key(R0_LANE, "rigor", '["R0", "R0"]'))
    with pytest.raises(LaneConfigError, match="duplicate level"):
        load_lane_file(path)


@pytest.mark.parametrize("enforcement", sorted(ENFORCEMENTS))
def test_every_declared_enforcement_loads(enforcement: str, project: Project):
    lane = load_lane_file(
        project.write(set_key(R0_LANE, "enforcement", f'"{enforcement}"'))
    ).lane("package")
    assert lane.enforcement == enforcement


@pytest.mark.parametrize("enforcement", ["warn", "block", "Gate", "", "required"])
def test_enforcement_outside_the_vocabulary_is_rejected(
    enforcement: str, project: Project
):
    path = project.write(set_key(R0_LANE, "enforcement", f'"{enforcement}"'))
    with pytest.raises(LaneConfigError, match="'enforcement' must be one of"):
        load_lane_file(path)


# --- budget: parsed, not merely present (A-052) ------------------------------


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("1s", 1.0),
        ("90s", 90.0),
        ("5m", 300.0),
        ("30m", 1800.0),
        ("2h", 7200.0),
        ("1h30m", 5400.0),
        ("1h30m15s", 5415.0),
        ("0.5m", 30.0),
        ("  5m  ", 300.0),
    ],
)
def test_valid_budget_parses_to_seconds(text: str, seconds: float, project: Project):
    lane = load_lane_file(
        project.write(set_key(R0_LANE, "budget", f'"{text}"'))
    ).lane("package")

    assert lane.budget_seconds == seconds
    # ...and the declaration itself is preserved verbatim.
    assert lane.budget == text


@pytest.mark.parametrize(
    "text",
    [
        "300",  # unit-less: guessing the unit is the invention we refuse
        "",
        "soon",
        "5x",
        "m",
        "-5m",
        "0s",
        "0m0s",
        "30s1m",  # segments out of order
        "5 m",
        "5m30",
        "1d",
    ],
)
def test_unparseable_budget_is_rejected(text: str, project: Project):
    path = project.write(set_key(R0_LANE, "budget", f'"{text}"'))
    with pytest.raises(LaneConfigError, match="'budget'"):
        load_lane_file(path)


@pytest.mark.parametrize(
    ("text", "seconds"), [("1s", 1.0), ("5m", 300.0), ("1h30m", 5400.0)]
)
def test_parse_duration_is_reusable_on_its_own(text: str, seconds: float):
    assert parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["", "5", "5x", "0s", "-1s"])
def test_parse_duration_raises_value_error_for_a_non_duration(text: str):
    with pytest.raises(ValueError):
        parse_duration(text)


def test_parse_duration_rejects_a_non_string():
    with pytest.raises(ValueError, match="must be a string"):
        parse_duration(300)  # type: ignore[arg-type]


# --- fail_under --------------------------------------------------------------


@pytest.mark.parametrize("value", ["0.0", "50.5", "100.0", "100", "0"])
def test_fail_under_inside_the_percentage_range_loads(value: str, project: Project):
    judge = load_lane_file(
        project.write(set_key(R1_LANE, "fail_under", value))
    ).lane("package").judge

    assert judge is not None
    assert judge.fail_under == float(value)


@pytest.mark.parametrize("value", ["-1.0", "100.1", "101", "1000"])
def test_fail_under_outside_the_percentage_range_is_rejected(
    value: str, project: Project
):
    path = project.write(set_key(R1_LANE, "fail_under", value))
    with pytest.raises(LaneConfigError, match="between 0 and 100"):
        load_lane_file(path)


@pytest.mark.parametrize("value", ['"100"', "true"])
def test_fail_under_that_is_not_a_number_is_rejected(value: str, project: Project):
    path = project.write(set_key(R1_LANE, "fail_under", value))
    with pytest.raises(LaneConfigError, match="must be a number"):
        load_lane_file(path)


def test_language_and_allow_excluded_are_typed(project: Project):
    with pytest.raises(LaneConfigError, match="must be a string"):
        load_lane_file(project.write(set_key(R1_LANE, "language", "1")))
    with pytest.raises(LaneConfigError, match="must be a boolean"):
        load_lane_file(
            project.write(set_key(R1_LANE, "allow_excluded", '"false"'))
        )


def test_allow_excluded_true_is_a_position_not_a_default(project: Project):
    # A-018: the union contains a genuine policy contradiction (topos tells
    # users to add `# pragma: no cover`; nyxloom and dstdns fail the gate for
    # it), so BOTH positions must be declarable.
    judge = load_lane_file(
        project.write(set_key(R1_LANE, "allow_excluded", "true"))
    ).lane("package").judge

    assert judge is not None
    assert judge.allow_excluded is True
