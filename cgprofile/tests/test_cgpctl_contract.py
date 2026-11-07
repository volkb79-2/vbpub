"""cgpctl obeys the shared cli-extended contract: the real executable, the committed surface artefacts and the audit."""
from __future__ import annotations

import pathlib

from cgprofile.cli import cgpctl
from cli_extended import (
    assert_cli_contract,
    check_cli_surface,
    load_project_config,
    make_invoker,
)
from cli_extended.audit import run_audit

PROJECT = pathlib.Path(__file__).resolve().parents[1]
VERBS = ("status", "host", "sessions", "show", "start", "stop", "watch", "report", "prune", "run", "mark", "config",
         "doctor", "skills")


def test_the_real_executable_obeys_the_cli_contract(tmp_path):
    invoke = make_invoker("cgprofile.cli.cgpctl", module=True, home=tmp_path / "home",
                          pythonpath=[PROJECT / "src"], cwd=tmp_path)
    assert_cli_contract(
        invoke, cgpctl.identity(), VERBS,
        invalid_invocations={"unknown verb": ["bogus"], "show without a session": ["show"],
                             "report formats together": ["report", "--run-dir", ".", "--html-only", "--md-only"]},
        known_verb_errors={"show without a session": "show", "report formats together": "report"},
    )


def test_the_committed_surface_artefacts_and_the_audit_pass():
    project = load_project_config(PROJECT / "pyproject.toml")
    entry = project.select("cgpctl")
    report = check_cli_surface(cgpctl.build_cli(), review_path=entry.review, manifest_path=entry.manifest,
                               spec_path=entry.spec, findings_path=entry.findings)
    assert report.passed, report.render()
    items = run_audit(entry, project)
    assert [i.checklist_id for i in items if i.status == "fail"] == []
    assert {i.status for i in items} <= {"pass", "manual", "warn"}
