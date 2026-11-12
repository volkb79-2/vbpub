"""cgprofilectl and cgprofile obey the shared cli-extended contract: the real executables, the committed surface artefacts and the audit."""
from __future__ import annotations

import pathlib

import pytest

from cgprofile.cli import cgprofile, cgprofilectl
from cli_extended import (
    assert_cli_contract,
    check_cli_surface,
    load_project_config,
    make_invoker,
)
from cli_extended.audit import run_audit

PROJECT = pathlib.Path(__file__).resolve().parents[1]
CTL_VERBS = ("status", "host", "sessions", "show", "start", "stop", "watch", "prune", "config", "doctor")
CLIENT_VERBS = ("report", "analyze", "run", "mark", "skills")


def test_the_socket_client_obeys_the_cli_contract(tmp_path):
    invoke = make_invoker("cgprofile.cli.cgprofilectl", module=True, home=tmp_path / "home",
                          pythonpath=[PROJECT / "src"], cwd=tmp_path)
    assert_cli_contract(
        invoke, cgprofilectl.identity(), CTL_VERBS,
        invalid_invocations={"unknown verb": ["bogus"], "show without a session": ["show"]},
        known_verb_errors={"show without a session": "show"},
    )


def test_the_end_user_client_obeys_the_cli_contract(tmp_path):
    invoke = make_invoker("cgprofile.cli.cgprofile", module=True, home=tmp_path / "home",
                          pythonpath=[PROJECT / "src"], cwd=tmp_path)
    assert_cli_contract(
        invoke, cgprofile.identity(), CLIENT_VERBS,
        invalid_invocations={"unknown verb": ["bogus"],
                             "report formats together": ["report", "--run-dir", ".", "--html-only", "--md-only"]},
        known_verb_errors={"report formats together": "report"},
    )


@pytest.mark.parametrize("cli_id,module", [("cgprofilectl", cgprofilectl), ("cgprofile", cgprofile)])
def test_the_committed_surface_artefacts_and_the_audit_pass(cli_id, module):
    project = load_project_config(PROJECT / "pyproject.toml")
    entry = project.select(cli_id)
    report = check_cli_surface(module.build_cli(), review_path=entry.review, manifest_path=entry.manifest,
                               spec_path=entry.spec, findings_path=entry.findings)
    assert report.passed, report.render()
    items = run_audit(entry, project)
    assert [i.checklist_id for i in items if i.status == "fail"] == []
    assert {i.status for i in items} <= {"pass", "manual", "warn"}
