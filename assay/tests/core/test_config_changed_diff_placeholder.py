"""B245 U5b -- lane-load validation of the ``{assay.changed_diff}`` argv
placeholder (design 2.2): allowed only on an ingested ``cargo-mutants-outcomes``
R2 lane judging ``changed_lines``, exactly once, REQUIRED there, spelled as a
WHOLE argv token and absent from ``env`` and ``environment_command``."""

from __future__ import annotations

import pytest
from conftest import Project

from assay.config import load_lane_file
from assay.errors import LaneConfigError, Outcome, ReasonCode
from test_config_cargo_mutants_lane import LANE

PLACEHOLDER = '"--in-diff", "{assay.changed_diff}", '
GITIGNORE_FREE_NATIVE = """\
schema_version = 2

[lanes.hostobs]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
cwd = "crate"
argv = ["cargo", "mutants", "--in-diff", "{assay.changed_diff}"]
env = {}
env_passthrough = ["PATH"]
budget = "45m"
allow_argv_append = false

[lanes.hostobs.isolation]
snapshot_selection = "repository"

[lanes.hostobs.judge]
language = "python"
source_roots = ["crate/src"]
base = "main"

[lanes.hostobs.judge.mutation]
jobs = 1
max_mutants = 20
operators = ["python:compare-swap"]
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


def test_allowed_on_cargo_mutants_changed_lines(crate_project: Project):
    lane = _load(crate_project, LANE).lane("hostobs")
    assert "{assay.changed_diff}" in lane.argv


def test_allowed_with_a_request_base(crate_project: Project):
    text = LANE.replace('base = "main"', 'base_source = "request"')
    assert "{assay.changed_diff}" in _load(crate_project, text).lane("hostobs").argv


def test_refused_on_native_r2(crate_project: Project):
    message = _refusal(crate_project, GITIGNORE_FREE_NATIVE)
    assert "{assay.changed_diff}" in message
    assert "cargo-mutants-outcomes" in message and "changed_lines" in message


def test_refused_on_stryker_format(crate_project: Project):
    text = (
        LANE.replace(
            '[lanes.hostobs.judge.mutation]\nformat = "cargo-mutants-outcomes"',
            '[lanes.hostobs.judge.mutation]\nformat = "mutation-report-json"',
        )
        .replace('artifact = "crate/target/out"', 'artifact = "crate/target/out.json"')
        .replace(
            '\n[lanes.hostobs.result_report]\nformat = "cargo-mutants-outcomes"\npath = "target/out"\n',
            "",
        )
    )
    message = _refusal(crate_project, text)
    assert "{assay.changed_diff}" in message and "cargo-mutants-outcomes" in message


def test_refused_on_r0_only(crate_project: Project):
    text = (
        'schema_version = 2\n\n[lanes.hostobs]\nscope = "S1"\nrigor = ["R0"]\n'
        'enforcement = "gate"\nargv = ["cargo", "mutants", "--in-diff", '
        '"{assay.changed_diff}"]\nenv = {}\nenv_passthrough = ["PATH"]\n'
        'budget = "1m"\nallow_argv_append = false\n'
    )
    message = _refusal(crate_project, text)
    assert "{assay.changed_diff}" in message and "declares R2" in message


def test_refused_whole_target(crate_project: Project):
    text = LANE.replace('base = "main"', 'mode = "whole_target"\ntargets = ["crate/src"]')
    message = _refusal(crate_project, text)
    assert "whole_target" in message and "{assay.changed_diff}" in message


def test_whole_target_without_the_placeholder_loads(crate_project: Project):
    text = LANE.replace('base = "main"', 'mode = "whole_target"\ntargets = ["crate/src"]').replace(
        PLACEHOLDER, ""
    )
    assert "{assay.changed_diff}" not in _load(crate_project, text).lane("hostobs").argv


def test_required_on_cargo_mutants_changed_lines(crate_project: Project):
    message = _refusal(crate_project, LANE.replace(PLACEHOLDER, ""))
    assert "must carry" in message and "{assay.changed_diff}" in message
    assert "mutates the whole crate" in message


def test_refused_twice(crate_project: Project):
    text = LANE.replace(PLACEHOLDER, PLACEHOLDER + '"--in-diff", "{assay.changed_diff}", ')
    assert "exactly once" in _refusal(crate_project, text)


def test_refused_embedded_form(crate_project: Project):
    text = LANE.replace(PLACEHOLDER, '"--in-diff={assay.changed_diff}", ')
    message = _refusal(crate_project, text)
    assert "--in-diff={assay.changed_diff}" in message and "whole" in message


def test_refused_unknown_assay_placeholder(crate_project: Project):
    text = LANE.replace(PLACEHOLDER, PLACEHOLDER + '"--other", "{assay.changed_dif}", ')
    message = _refusal(crate_project, text)
    assert "{assay.changed_dif}" in message and "misspelled" in message


@pytest.mark.parametrize(
    "old, new, label",
    [
        ("env = {}", 'env = {X = "{assay.changed_diff}"}', "env"),
        (
            "allow_argv_append = false",
            'allow_argv_append = false\nenvironment_command = ["echo", "{assay.changed_diff}"]',
            "environment_command",
        ),
    ],
)
def test_refused_in_env_and_environment_command(
    crate_project: Project, old: str, new: str, label: str
):
    message = _refusal(crate_project, LANE.replace(old, new, 1))
    assert f"'{label}' contains" in message


def test_refused_without_base(crate_project: Project):
    # A changed-lines R2 lane always declares a base (`judge.base` or
    # `base_source = "request"`): the loader's required-field rule refuses a
    # lane that declares neither, before the placeholder is looked at.
    message = _refusal(crate_project, LANE.replace('base = "main"\n', ""))
    assert "judge.base" in message


def test_refused_beside_r3(crate_project: Project):
    # A canary half-run executes the lane command without the handoff, so the
    # literal placeholder would reach a process: refused at load instead.
    crate_project.file("crate/src/lib.rs", "pub fn f() {}\n")
    text = LANE.replace('rigor = ["R0", "R2"]', 'rigor = ["R0", "R1", "R2", "R3"]').replace(
        'base = "main"', 'base = "main"\nfail_under = 80.0\nallow_excluded = false'
    ) + (
        '\n[lanes.hostobs.judge.coverage]\nformat = "lcov"\nartifact = "crate/lcov.info"\n'
        '\n[lanes.hostobs.judge.canary]\nmechanism = "uncovered-line"\n'
        'target = "crate/src/lib.rs"\n'
    )
    message = _refusal(crate_project, text)
    assert "{assay.changed_diff}" in message and "R3" in message
