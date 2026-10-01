"""B078 -- ``[lanes.X.result_report]``, the opt-in structured-report
declaration, at the loader boundary.

Both directions, because the loader is the only thing standing between a
typo'd declaration and a lane that silently reverts to A-073 forever: every
accepted field must arrive intact AND round-trip through
:meth:`~assay.config.Lane.as_declared`, and every malformed shape must refuse
by naming the specific offending field (AGENTS.md §4.2a).

The vocabulary is DERIVED from the shipped reader registry, never hand-copied
here -- a hand-copied list is exactly how a format becomes declarable in
config before a reader exists to parse it.
"""

from __future__ import annotations

import tomllib

import pytest
from conftest import R0_LANE, Project

from assay.config import RESULT_REPORT_FORMATS, load_lane_file
from assay.errors import LaneConfigError
from assay.result_reports import RESULT_REPORT_FORMATS as REGISTRY_FORMATS

DECLARATION = """
[lanes.package.result_report]
format = "vitest-json"
path = "vitest-report.json"
"""


def test_the_config_vocabulary_is_the_reader_registry(   ):
    """One vocabulary, one source. If these two ever diverge, a lane can
    declare a format nothing can read."""
    assert RESULT_REPORT_FORMATS is REGISTRY_FORMATS
    assert RESULT_REPORT_FORMATS, "an empty registry would make every check vacuous"


# --- accept ------------------------------------------------------------------


def test_a_declared_result_report_loads_with_both_values_intact(project: Project):
    path = project.write(R0_LANE + DECLARATION)

    lane = load_lane_file(path).lane("package")

    assert lane.result_report is not None
    assert lane.result_report.format == "vitest-json"
    assert lane.result_report.path == "vitest-report.json"


def test_a_declared_result_report_round_trips_through_as_declared(project: Project):
    """A-052's rule at this field: what the file said is exactly what the
    object reproduces -- no invented default, no dropped key."""
    text = R0_LANE + DECLARATION
    path = project.write(text)

    lane = load_lane_file(path).lane("package")

    assert lane.as_declared() == tomllib.loads(text)["lanes"]["package"]


def test_a_lane_omitting_the_table_carries_none_and_declares_nothing(
    project: Project,
):
    """The overwhelmingly common case, and the one SR-1's whole safety
    argument rests on: no declaration, no field, no key in the round-trip."""
    path = project.write(R0_LANE)

    lane = load_lane_file(path).lane("package")

    assert lane.result_report is None
    assert "result_report" not in lane.as_declared()


def test_a_nested_report_path_is_accepted(project: Project):
    """A subdirectory spelling is legal at load -- whether that directory
    exists is a RUNTIME fact (the reservation's), not something the loader can
    know for a snapshot lane whose checkout does not exist yet."""
    path = project.write(
        R0_LANE
        + '\n[lanes.package.result_report]\nformat = "vitest-json"\n'
        + 'path = ".assay/vitest-report.json"\n'
    )

    lane = load_lane_file(path).lane("package")

    assert lane.result_report is not None
    assert lane.result_report.path == ".assay/vitest-report.json"


# --- reject ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param(
            'result_report = "vitest-report.json"\n',
            "must be a table",
            id="not-a-table",
        ),
        pytest.param(
            '\n[lanes.package.result_report]\nformat = "vitest-json"\n',
            "missing required field 'path'",
            id="path-missing",
        ),
        pytest.param(
            '\n[lanes.package.result_report]\npath = "r.json"\n',
            "missing required field 'format'",
            id="format-missing",
        ),
        pytest.param(
            '\n[lanes.package.result_report]\nformat = "vitest-json"\n'
            'path = "r.json"\nreporter = "json"\n',
            "unknown key(s): reporter",
            id="unknown-key",
        ),
        pytest.param(
            '\n[lanes.package.result_report]\nformat = "jest-json"\npath = "r.json"\n',
            "'result_report.format' must be one of",
            id="unregistered-format",
        ),
        pytest.param(
            '\n[lanes.package.result_report]\nformat = "vitest-json"\n'
            'path = "/tmp/r.json"\n',
            "must not be absolute",
            id="absolute-path",
        ),
        pytest.param(
            '\n[lanes.package.result_report]\nformat = "vitest-json"\n'
            'path = "../outside/r.json"\n',
            "invalid path component",
            id="escaping-path",
        ),
        pytest.param(
            '\n[lanes.package.result_report]\nformat = "vitest-json"\npath = ""\n',
            "must not be empty",
            id="empty-path",
        ),
        pytest.param(
            '\n[lanes.package.result_report]\nformat = "vitest-json"\npath = 7\n',
            "must be a string",
            id="path-not-a-string",
        ),
        pytest.param(
            '\n[lanes.package.result_report]\nformat = 7\npath = "r.json"\n',
            "'result_report.format' must be a string",
            id="format-not-a-string",
        ),
        pytest.param(
            '\n[lanes.package.result_report]\nformat = "vitest-json"\n'
            'path = ".git/r.json"\n',
            "invalid path component",
            id="dot-git-component",
        ),
    ],
)
def test_a_malformed_declaration_refuses_naming_the_field(
    project: Project, body: str, expected: str
):
    path = project.write(R0_LANE + body)

    with pytest.raises(LaneConfigError) as caught:
        load_lane_file(path)

    assert expected in str(caught.value)
    assert "lane 'package'" in str(caught.value), "the refusal names the lane"
