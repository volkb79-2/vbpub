"""B245 U5a -- the lane-load validation of a ``cargo-mutants-outcomes`` lane
(design 1.5 and 2.2): the three spellings of ``<out>`` must name one
directory, ``mutants.out`` is never the declared artifact, ``--output`` is
named exactly once and no second spelling is accepted, and the
``{assay.changed_diff}`` placeholder stays refused until U5b."""

from __future__ import annotations

import pytest
from conftest import Project

from assay.config import load_lane_file
from assay.errors import LaneConfigError, Outcome, ReasonCode

LANE = """\
schema_version = 2

[lanes.hostobs]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
cwd = "crate"
argv = ["cargo", "mutants", "--test-tool", "nextest", "--output", "target/out"]
env = {}
env_passthrough = ["PATH"]
budget = "45m"
allow_argv_append = false

[lanes.hostobs.isolation]
snapshot_selection = "repository"

[lanes.hostobs.judge]
language = "rust"
source_roots = ["crate/src"]
base = "main"

[lanes.hostobs.judge.mutation]
format = "cargo-mutants-outcomes"
artifact = "crate/target/out"
fail_under = 100.0

[lanes.hostobs.result_report]
format = "cargo-mutants-outcomes"
path = "target/out"
"""


@pytest.fixture
def crate_project(project: Project) -> Project:
    project.dir("crate", "src")
    return project


def _load(project: Project, text: str):
    return load_lane_file(project.write(text))


def _refusal(project: Project, text: str) -> str:
    with pytest.raises(LaneConfigError) as excinfo:
        _load(project, text)
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    return str(excinfo.value)


def test_the_complete_lane_loads(crate_project: Project):
    lane = _load(crate_project, LANE).lane("hostobs")
    assert lane.judge.mutation.format == "cargo-mutants-outcomes"
    assert lane.result_report.path == "target/out"


def test_the_output_may_be_spelled_with_an_equals_sign(crate_project: Project):
    text = LANE.replace('"--output", "target/out"', '"--output=target/out"')
    assert _load(crate_project, text).lane("hostobs").argv[-1] == "--output=target/out"


def test_a_lane_at_the_project_root_needs_no_cwd_prefix(crate_project: Project):
    text = (
        LANE.replace('cwd = "crate"\n', "")
        .replace('artifact = "crate/target/out"', 'artifact = "target/out"')
        .replace('source_roots = ["crate/src"]', 'source_roots = ["crate/src"]')
    )
    assert _load(crate_project, text).lane("hostobs").cwd is None


def test_the_matching_result_report_is_required(crate_project: Project):
    text = LANE[: LANE.index("[lanes.hostobs.result_report]")]
    message = _refusal(crate_project, text)
    assert "result_report" in message and "exit codes 2 and 3" in message


def test_the_result_report_format_must_be_the_matching_one(crate_project: Project):
    text = LANE.replace(
        '[lanes.hostobs.result_report]\nformat = "cargo-mutants-outcomes"',
        '[lanes.hostobs.result_report]\nformat = "vitest-json"',
    )
    assert "must also declare" in _refusal(crate_project, text)


def test_another_ingested_format_cannot_declare_the_directory_report(
    crate_project: Project,
):
    text = LANE.replace(
        '[lanes.hostobs.judge.mutation]\nformat = "cargo-mutants-outcomes"',
        '[lanes.hostobs.judge.mutation]\nformat = "mutation-report-json"',
    )
    assert "R0-only" in _refusal(crate_project, text)


def test_the_report_path_must_be_the_mutation_artifact(crate_project: Project):
    text = LANE.replace('path = "target/out"', 'path = "target/other"')
    assert "same directory" in _refusal(crate_project, text)


def test_the_artifact_may_not_be_the_tools_own_directory(crate_project: Project):
    text = (
        LANE.replace('"target/out"]', '"target/mutants.out"]')
        .replace('artifact = "crate/target/out"', 'artifact = "crate/target/mutants.out"')
        .replace('path = "target/out"', 'path = "target/mutants.out"')
    )
    message = _refusal(crate_project, text)
    assert "mutants.out.old" in message


def test_the_argv_must_name_the_output_directory(crate_project: Project):
    text = LANE.replace(', "--output", "target/out"]', "]")
    assert "exactly once" in _refusal(crate_project, text)


def test_the_argv_may_not_name_the_output_directory_twice(crate_project: Project):
    text = LANE.replace(
        '"--output", "target/out"]', '"--output", "target/out", "--output=target/out"]'
    )
    assert "found 2" in _refusal(crate_project, text)


def test_a_dangling_output_flag_is_refused(crate_project: Project):
    text = LANE.replace('"--output", "target/out"]', '"--output"]')
    assert "names no directory" in _refusal(crate_project, text)


def test_the_argv_output_must_equal_the_report_path(crate_project: Project):
    text = LANE.replace('"--output", "target/out"]', '"--output", "target/else"]')
    assert "one directory and the command would write another" in _refusal(
        crate_project, text
    )


def test_the_environment_spelling_of_output_is_refused(crate_project: Project):
    text = LANE.replace("env = {}", 'env = {CARGO_MUTANTS_OUTPUT = "target/out"}')
    assert "CARGO_MUTANTS_OUTPUT" in _refusal(crate_project, text)


def test_the_passthrough_spelling_of_output_is_refused(crate_project: Project):
    text = LANE.replace(
        'env_passthrough = ["PATH"]', 'env_passthrough = ["PATH", "CARGO_MUTANTS_OUTPUT"]'
    )
    assert "CARGO_MUTANTS_OUTPUT" in _refusal(crate_project, text)


@pytest.mark.parametrize(
    "old, new",
    [
        ('"--test-tool"', '"--in-diff", "{assay.changed_diff}", "--test-tool"'),
        ("env = {}", 'env = {X = "{assay.changed_diff}"}'),
        (
            'allow_argv_append = false',
            'allow_argv_append = false\nenvironment_command = ["echo", "{assay.x}"]',
        ),
    ],
)
def test_the_changed_diff_placeholder_is_refused_until_u5b(
    crate_project: Project, old: str, new: str
):
    message = _refusal(crate_project, LANE.replace(old, new, 1))
    assert "U5b" in message and "{assay." in message
