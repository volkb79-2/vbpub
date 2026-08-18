"""O1, ACCEPT direction — a complete lane loads and round-trips exactly.

This is the half of O1 that a loader which rejects everything cannot pass, and
it is deliberately the *first* module in the suite. Every value the file
declared is asserted to arrive intact, and :meth:`Lane.as_declared` is compared
against ``tomllib``'s own parse so that an invented default or a dropped field
shows up as an inequality rather than as something a reviewer has to spot.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import R0_LANE, R1_LANE, Project, lane_table

from assay.config import REQUIRED_LANE_FIELDS, load_lane_file
from assay.errors import LaneConfigError


def test_complete_r0_lane_loads_with_every_value_intact(project: Project):
    path = project.write(R0_LANE)

    lane_file = load_lane_file(path)

    assert lane_file.schema_version == 2
    assert lane_file.path == path.resolve()
    assert lane_file.project_root == project.root.resolve()
    assert list(lane_file.lanes) == ["package"]

    lane = lane_file.lane("package")
    assert lane.name == "package"
    assert lane.scope == "S1"
    assert lane.rigor == ("R0",)
    assert lane.enforcement == "gate"
    assert lane.argv == ("pytest", "tests/unit", "-q")
    assert dict(lane.env) == {"MOCK_MODE": "true"}
    assert lane.env_passthrough == ("HOME", "TMPDIR", "PATH")
    assert lane.budget == "5m"
    assert lane.budget_seconds == 300.0
    assert lane.allow_argv_append is False
    # A-048: an R0-only lane has no [judge] table, and that is correct.
    assert lane.judge is None
    assert lane.where is None


def test_complete_r1_lane_loads_with_judge_and_where_intact(project: Project):
    path = project.write(R1_LANE)

    lane = load_lane_file(path).lane("package")

    assert lane.scope == "S2"
    assert lane.rigor == ("R0", "R1")
    assert lane.enforcement == "advisory"
    assert lane.argv == ("pytest", "tests", "-q", "--cov-report=json:cov.json")
    assert dict(lane.env) == {"MOCK_MODE": "true", "TZ": "UTC"}
    assert lane.env_passthrough == ("PATH",)
    assert lane.budget == "1h30m"
    assert lane.budget_seconds == 5400.0
    assert lane.allow_argv_append is True

    judge = lane.judge
    assert judge is not None
    assert judge.language == "python"
    assert judge.source_roots == ("src", "scripts")
    assert judge.source_root_paths == (
        (project.root / "src").resolve(),
        (project.root / "scripts").resolve(),
    )
    assert judge.fail_under == 100.0
    assert judge.allow_excluded is False
    assert judge.coverage is not None
    assert judge.coverage.format == "coverage-py-json"
    assert judge.coverage.artifact == "cov.json"
    # Not declared by this lane, so absent — never a default.
    assert judge.mutation is None
    assert judge.canary is None


def test_r0_lane_round_trips_exactly_what_the_file_declared(project: Project):
    path = project.write(R0_LANE)

    declared = load_lane_file(path).lane("package").as_declared()

    assert declared == lane_table(R0_LANE)
    assert set(declared) == set(REQUIRED_LANE_FIELDS)


def test_r1_lane_round_trips_exactly_what_the_file_declared(project: Project):
    path = project.write(R1_LANE)

    declared = load_lane_file(path).lane("package").as_declared()

    assert declared == lane_table(R1_LANE)
    assert set(declared) == {*REQUIRED_LANE_FIELDS, "judge", "where", "isolation"}
    assert set(declared["judge"]) == {
        "language",
        "source_roots",
        "fail_under",
        "allow_excluded",
        "coverage",
        "base",
    }


def test_budget_seconds_is_additional_to_the_declaration_not_a_replacement(
    project: Project,
):
    # A-052: parsed at load. The declared string still round-trips verbatim, so
    # the parse cannot quietly rewrite what the project wrote.
    lane = load_lane_file(project.write(R1_LANE)).lane("package")
    assert lane.budget_seconds == 5400.0
    assert lane.as_declared()["budget"] == "1h30m"


def test_where_table_is_carried_verbatim_and_never_interpreted(project: Project):
    # DESIGN-GUIDE §7: WHERE is data assay parses and never interprets,
    # permanently. Keys assay has never heard of must survive untouched.
    text = R1_LANE + """
[lanes.package.where.env]
COMPOSE_PROFILES = "test"
"""
    lane = load_lane_file(project.write(text)).lane("package")

    assert lane.where is not None
    assert dict(lane.where) == {
        "service": "test-runner",
        "instance": "worktree",
        "env": {"COMPOSE_PROFILES": "test"},
    }


def test_two_lanes_load_independently(project: Project):
    text = R0_LANE + """
[lanes.acceptance]
scope = "S3"
rigor = ["R0"]
enforcement = "advisory"
argv = ["pytest", "tests/acceptance"]
env = {}
env_passthrough = ["DOCKER_HOST", "PATH"]
budget = "45m"
allow_argv_append = true
"""
    lane_file = load_lane_file(project.write(text))

    assert list(lane_file.lanes) == ["package", "acceptance"]
    assert lane_file.lane("package").scope == "S1"
    assert lane_file.lane("acceptance").scope == "S3"
    assert lane_file.lane("acceptance").budget_seconds == 2700.0
    assert lane_file.lane("acceptance").allow_argv_append is True
    assert dict(lane_file.lane("acceptance").env) == {}


def test_empty_env_and_empty_passthrough_are_declarations_not_omissions(
    project: Project,
):
    # A-019: `env` is declared-only. `env = {}` says "this lane adds nothing",
    # which is a different statement from omitting the key — and omitting it is
    # rejected (see test_config_reject.py).
    text = """\
schema_version = 2

[lanes.package]
scope = "S0"
rigor = ["R0"]
enforcement = "gate"
argv = ["/bin/true"]
env = {}
env_passthrough = []
budget = "1s"
allow_argv_append = false
"""
    lane = load_lane_file(project.write(text)).lane("package")

    assert dict(lane.env) == {}
    assert lane.env_passthrough == ()
    assert lane.budget_seconds == 1.0
    assert lane.as_declared() == lane_table(text)


def test_lane_lookup_names_the_declared_lanes_when_the_name_is_wrong(
    project: Project,
):
    lane_file = load_lane_file(project.write(R0_LANE))

    with pytest.raises(LaneConfigError) as excinfo:
        lane_file.lane("nope")
    assert "package" in str(excinfo.value)


def test_loader_accepts_a_file_given_by_relative_path(
    project: Project, monkeypatch
):
    project.write(R0_LANE)
    monkeypatch.chdir(project.root)

    lane_file = load_lane_file(Path("assay.toml"))

    assert lane_file.project_root == project.root.resolve()


def test_complete_sql_r2_lane_loads_with_both_artifact_keys_intact(project: Project):
    """P34/W4: the two v6 `judge.mutation` artifact keys are legal on a
    ``sql`` lane and round-trip exactly like every other declared value --
    O1's ACCEPT-direction promise, one language over. The carve's own
    §3.4 pasteable example, minus the fields this build does not yet wire
    (``language = "sql"`` itself is still refused by the CLI's registry
    until W6 -- this module proves only that CONFIG accepts the shape)."""
    text = """\
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["scripts/schema-gate.sh"]
env = {}
env_passthrough = ["PATH"]
budget = "20m"
allow_argv_append = false

[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.judge]
language = "sql"
source_roots = ["src"]
base = "origin/main"

[lanes.package.judge.mutation]
jobs = 1
max_mutants = 200
operators = ["sql:drop-check", "sql:weaken-delete-action"]
equivalence_artifact = ".assay/schema-dump.sql"
kill_signal_artifact = ".assay/kill-signal.txt"
"""
    path = project.write(text)

    lane = load_lane_file(path).lane("package")

    judge = lane.judge
    assert judge is not None
    assert judge.language == "sql"
    assert judge.mutation is not None
    assert judge.mutation.operators == ("sql:drop-check", "sql:weaken-delete-action")
    assert judge.mutation.equivalence_artifact == ".assay/schema-dump.sql"
    assert judge.mutation.kill_signal_artifact == ".assay/kill-signal.txt"
    assert lane.as_declared() == lane_table(text)
