"""Replay every reviewed fleet CLI case (``docs/cli-review.toml``) against the real registry.

Each active catalog row carries an invocation and its expected status/output; the
parametrized test below executes that invocation through ``tests/cli_review_harness.py``,
which runs the real ``fleetd.cli.build_cli`` registry in a throw-away HOME and a private,
non-repository working directory. The catalog therefore cannot drift from behaviour: it is
generated from the observed outcome (``tools/gen-cli-review-catalog.py``) and re-checked
here.

The ``cli_case`` marker links each test node to its row; ``cli_extended.pytest_plugin``
(``tests/conftest.py``) fails the run when an active case has no collected marked test, or
when a node's ``test_ids`` entry does not name it. A campaign command would contact a host,
so those rows carry a plan/campaign that cannot exist in the sandbox: the reviewed outcome
is the documented refusal before any transport call, which also proves the reviewed spelling
reached the handler.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from cli_extended.review import load_cli_review_catalog

import cli_review_harness as harness

PROJECT = Path(__file__).resolve().parents[1]
CATALOG = PROJECT / "docs" / "cli-review.toml"
TEST_FILE = "tests/test_cli_review_cases.py"

CASES = [case for case in load_cli_review_catalog(CATALOG).cases if case.state == "active"]


@pytest.mark.parametrize(
    "case",
    [pytest.param(case, id=harness.short_id(case.case_id), marks=pytest.mark.cli_case(case.case_id)) for case in CASES],
)
def test_reviewed_case(case) -> None:
    status, stdout, stderr = harness.run(list(case.invocation))
    assert status == case.expected_exit_status, (case.invocation, stdout, stderr)
    assert case.expected_stdout_contains in stdout, (case.invocation, stdout, stderr)
    assert case.expected_stderr_contains in stderr, (case.invocation, stdout, stderr)
    assert "Traceback" not in stderr, stderr


def test_every_active_case_names_this_test_node_and_records_its_decision() -> None:
    assert CASES
    for case in CASES:
        node = f"{TEST_FILE}::test_reviewed_case[{harness.short_id(case.case_id)}]"
        assert case.test_ids == (node,), case.case_id
        assert case.decision in {"accept", "refuse"}, case.case_id
        assert case.rationale.strip(), case.case_id
        assert case.effects, case.case_id
        assert case.reviewed_signature and case.reviewed_signature.startswith("sha256:"), case.case_id


def test_case_ids_are_unique_so_node_ids_are_stable() -> None:
    """Two rows may legitimately run the same argv (a required positional's minimum is also
    its argument shape); the node id is derived from the case id, so only case ids must be
    unique, and the catalog's own ids are checked again here rather than trusted."""

    rows = tomllib.loads(CATALOG.read_text(encoding="utf-8"))["cases"]
    ids = [row["id"] for row in rows]
    assert len(ids) == len(set(ids))
    node_ids = [f"{TEST_FILE}::test_reviewed_case[{harness.short_id(case_id)}]" for case_id in ids]
    assert len(node_ids) == len(set(node_ids))


def test_the_catalog_is_current_against_the_real_registry():
    """A grammar change without a regenerated catalog fails here, not in review.

    ``surface check`` compares the manifest, the generated SPEC region, the catalog and
    the findings file with the built registry; running it in-process keeps that check
    inside the gate that collects the catalog's own tests.
    """

    from cli_extended import check_cli_surface
    from cli_extended.config import load_project_config

    from fleetd.cli import build_cli

    project = load_project_config(PROJECT / "pyproject.toml")
    assert [cli.id for cli in project.clis] == ["fleet"]
    cli = project.clis[0]
    report = check_cli_surface(
        build_cli(),
        review_path=cli.review,
        manifest_path=cli.manifest,
        spec_path=cli.spec,
        findings_path=cli.findings,
    )
    assert list(report.findings) == [], report.render()
