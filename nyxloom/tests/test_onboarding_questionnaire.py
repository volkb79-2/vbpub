"""Tests for nyxloom.onboarding_questionnaire (PACKAGE F4b: the guided
onboarding questionnaire, one-shot draft, docs/nyxloom-operating-model.md §2
step 4).

Cross-package seam (adapters.build_dispatch) is monkeypatched using the SAME
record-argv/emit shell-script convention test_onboarding_scan.py /
test_decision_chat.py / test_intake_chat.py establish: a script that
`echo "$@" > "$RECORD_FILE"` then `cat "$EMIT_FILE"`, so run_questionnaire's
real subprocess-execution path runs for real against a canned CLI. No live
model/LLM is ever invoked anywhere in this module."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from nyxloom import (
    adapters,
    cli,
    frontmatter,
    lint,
    onboarding,
    onboarding_questionnaire,
    onboarding_scan,
    paths,
)
from nyxloom.config import ProjectConfig

# --------------------------------------------------------------------------
# local fixtures / helpers (never added to conftest.py, per its own docstring)

ROUTES_TOML_WITH_QUESTIONNAIRE = textwrap.dedent("""\
    revision = "test-rev"

    [tiers.frontier-review]
    routes = ["questionnaire-agent-route"]

    [routes.questionnaire-agent-route]
    cli = "claude"
    model = "claude-test-model"
    """)

ROUTES_TOML_NO_QUESTIONNAIRE = textwrap.dedent("""\
    revision = "test-rev"

    [tiers.flash-high]
    routes = ["fake-cli"]

    [routes.fake-cli]
    cli = "fake"
    model = "fake-model"
    probe = ["true"]
    usage_source = "none"
    """)


def _use_questionnaire_routes() -> None:
    paths.routes_path().write_text(ROUTES_TOML_WITH_QUESTIONNAIRE, encoding="utf-8")


def _record_and_emit_script(tmp_path):
    script = tmp_path / "record_and_emit.sh"
    script.write_text('#!/bin/sh\necho "$@" > "$RECORD_FILE"\ncat "$EMIT_FILE"\n')
    script.chmod(0o755)
    return script


def _stub_turn(tmp_path, monkeypatch, reply_text: str, *, tag: str = "turn") -> Path:
    """Wire EMIT_FILE/RECORD_FILE for one subprocess turn; returns the
    record_file path (assert on it for argv-shape checks)."""
    script = _record_and_emit_script(tmp_path)
    emit_file = tmp_path / f"emit-{tag}.txt"
    emit_file.write_text(reply_text)
    record_file = tmp_path / f"record-{tag}.txt"
    monkeypatch.setenv("EMIT_FILE", str(emit_file))
    monkeypatch.setenv("RECORD_FILE", str(record_file))
    monkeypatch.setattr(adapters, "build_dispatch",
                         lambda route, **kw: ([str(script)], "prompt"))
    return record_file


def _onboarded_project(tmp_path, **answer_overrides) -> Path:
    """A project that already ran the F2 non-AI wizard (trove + wired
    nyxloom.toml + minimal-valid placeholder spine docs) -- the state F4b
    always runs against (mirrors test_onboarding_scan.py's / test_spine_
    writer.py's helper of the same name)."""
    project_root = tmp_path / "myproj"
    project_root.mkdir()
    kwargs = dict(maturity="mature", docs_present=False, mode="derive-from-code",
                  scan_paths=["src"])
    kwargs.update(answer_overrides)
    answers = onboarding.WizardAnswers(**kwargs)
    onboarding.run_wizard(project_root, answers)
    return project_root


def _seed_assessment(project_root: Path, **overrides) -> onboarding_scan.AssessmentResult:
    """Seed a non-skipped F3 AssessmentResult by writing
    onboarding-assessment.json directly (no agent dispatch) -- the state F4b
    always consumes."""
    trove_dir = project_root / "nyxloom-trove"
    kwargs = dict(
        scanned_at="2026-07-17T00:00:00+00:00",
        skipped=False,
        maturity="mature",
        existing_docs=["README.md"],
        existing_tests=["tests/test_foo.py"],
        intent_summary="A CLI tool for widgets.",
        gaps=["no roadmap", "no backlog items"],
    )
    kwargs.update(overrides)
    result = onboarding_scan.AssessmentResult(**kwargs)
    path = onboarding_scan.assessment_path(trove_dir)
    path.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=False) + "\n",
                     encoding="utf-8")
    return result


def _doc_paths(project_root: Path) -> dict[str, Path]:
    trove_dir = project_root / "nyxloom-trove"
    return {
        "north_star": trove_dir / "1-north-star.md",
        "product_definition": trove_dir / "2-product-definition.md",
        "roadmap": trove_dir / "3-roadmap.md",
        "backlog": trove_dir / "4-backlog.md",
    }


def _snapshot(paths_map: dict[str, Path]) -> dict[str, str]:
    return {k: p.read_text(encoding="utf-8") for k, p in paths_map.items()}


def _all_lint_findings(project_root: Path):
    cfg = ProjectConfig.load(project_root)
    per_doc = lint.lint_spine(cfg)
    flat = [f for findings in per_doc.values() for f in findings]
    return per_doc, flat


def _draft_reply(payload: dict, *, preamble: str = "Thinking about the north star first.\n\n") -> str:
    return preamble + "SPINE_DRAFT_JSON:\n" + json.dumps(payload) + "\n"


VALID_DRAFT_PAYLOAD = {
    "north_star_body": "# Vision\n\nThis project builds tools for widget makers.",
    "product_version": 1,
    "features": [
        {"id": "F001", "title": "Feature One", "acceptance": ["it does the first thing"],
         "status": "planned", "milestone": "M1"},
        {"id": "F002", "title": "Feature Two", "acceptance": ["it does the second thing"],
         "status": "building"},
    ],
    "non_goals": ["not a mobile app"],
    "milestones": [
        {"id": "M1", "title": "First milestone", "target_product_version": 1,
         "features": ["F001", "F002"], "status": "planned"},
    ],
    "backlog_items": [
        {"id": "B1", "title": "A real idea", "type": "feature", "folds_into": "F001"},
    ],
}


# ==========================================================================
# O1 -- happy path drafts a lint-green spine (non-hollow: real features land)
# ==========================================================================

def test_happy_path_drafts_lint_green_spine(tmp_path, tmp_state, monkeypatch):
    project_root = _onboarded_project(tmp_path)
    _seed_assessment(project_root)
    _use_questionnaire_routes()
    _stub_turn(tmp_path, monkeypatch, _draft_reply(VALID_DRAFT_PAYLOAD), tag="happy")

    result = onboarding_questionnaire.run_questionnaire(project_root)

    assert result.feature_count == 2
    assert result.milestone_count == 1
    assert result.backlog_count == 1
    assert result.lint_clean is True
    assert len(result.drafted_paths) == 4

    _per_doc, flat = _all_lint_findings(project_root)
    assert lint.has_blocking(flat) is False

    doc_paths = _doc_paths(project_root)
    pd_fm, _pd_body, _line = frontmatter.split_frontmatter(
        doc_paths["product_definition"].read_text(encoding="utf-8")
    )
    # NEGATIVE (non-hollow guard): a no-op leaving the placeholder
    # `features: []` would fail this assertion.
    assert len(pd_fm["features"]) == 2
    assert {f["id"] for f in pd_fm["features"]} == {"F001", "F002"}

    ns_text = doc_paths["north_star"].read_text(encoding="utf-8")
    assert "This project builds tools for widget makers." in ns_text


# ==========================================================================
# O2 -- fail-closed on unparseable reply (spine left UNCHANGED)
# ==========================================================================

def test_missing_marker_fails_closed_and_spine_unchanged(tmp_path, tmp_state, monkeypatch):
    project_root = _onboarded_project(tmp_path)
    _seed_assessment(project_root)
    _use_questionnaire_routes()
    doc_paths = _doc_paths(project_root)
    before = _snapshot(doc_paths)

    _stub_turn(tmp_path, monkeypatch, "Sure, here is my proposal in plain prose.\n", tag="nomarker")

    with pytest.raises(onboarding_questionnaire.UnparseableDraft):
        onboarding_questionnaire.run_questionnaire(project_root)

    assert _snapshot(doc_paths) == before
    pd_fm, _body, _line = frontmatter.split_frontmatter(before["product_definition"])
    assert pd_fm["features"] == []


def test_invalid_json_fails_closed_and_spine_unchanged(tmp_path, tmp_state, monkeypatch):
    project_root = _onboarded_project(tmp_path)
    _seed_assessment(project_root)
    _use_questionnaire_routes()
    doc_paths = _doc_paths(project_root)
    before = _snapshot(doc_paths)

    _stub_turn(tmp_path, monkeypatch, "SPINE_DRAFT_JSON: {not valid json at all\n", tag="badjson")

    with pytest.raises(onboarding_questionnaire.UnparseableDraft):
        onboarding_questionnaire.run_questionnaire(project_root)

    assert _snapshot(doc_paths) == before


def test_missing_required_key_fails_closed_and_spine_unchanged(tmp_path, tmp_state, monkeypatch):
    project_root = _onboarded_project(tmp_path)
    _seed_assessment(project_root)
    _use_questionnaire_routes()
    doc_paths = _doc_paths(project_root)
    before = _snapshot(doc_paths)

    payload = dict(VALID_DRAFT_PAYLOAD)
    del payload["milestones"]  # missing required top-level key
    _stub_turn(tmp_path, monkeypatch, _draft_reply(payload), tag="missingkey")

    with pytest.raises(onboarding_questionnaire.UnparseableDraft):
        onboarding_questionnaire.run_questionnaire(project_root)

    assert _snapshot(doc_paths) == before


def test_missing_feature_required_field_fails_closed(tmp_path, tmp_state, monkeypatch):
    project_root = _onboarded_project(tmp_path)
    _seed_assessment(project_root)
    _use_questionnaire_routes()
    doc_paths = _doc_paths(project_root)
    before = _snapshot(doc_paths)

    payload = json.loads(json.dumps(VALID_DRAFT_PAYLOAD))  # deep copy
    del payload["features"][0]["acceptance"]  # missing required entry field
    _stub_turn(tmp_path, monkeypatch, _draft_reply(payload), tag="missingfeaturefield")

    with pytest.raises(onboarding_questionnaire.UnparseableDraft):
        onboarding_questionnaire.run_questionnaire(project_root)

    assert _snapshot(doc_paths) == before


# ==========================================================================
# O3 -- fail-closed on lint-dirty draft (the false-green guard): the spine
# docs are RESTORED bytewise to their pre-call (placeholder) content.
# ==========================================================================

def test_dangling_milestone_reference_restores_prior_spine(tmp_path, tmp_state, monkeypatch):
    project_root = _onboarded_project(tmp_path)
    _seed_assessment(project_root)
    _use_questionnaire_routes()
    doc_paths = _doc_paths(project_root)
    before = _snapshot(doc_paths)

    payload = {
        "north_star_body": "Vision text.",
        "product_version": 1,
        "features": [
            {"id": "F001", "title": "Feature One", "acceptance": ["thing"], "status": "planned"},
        ],
        "milestones": [
            # F999 was never defined in 'features' above -- dangling S2 ref.
            {"id": "M1", "title": "Dangling", "target_product_version": 1,
             "features": ["F999"], "status": "planned"},
        ],
        "backlog_items": [],
    }
    _stub_turn(tmp_path, monkeypatch, _draft_reply(payload), tag="dangling")

    with pytest.raises(onboarding_questionnaire.UnapprovableDraft) as excinfo:
        onboarding_questionnaire.run_questionnaire(project_root)

    assert len(excinfo.value.findings) > 0
    # RESTORE is real (bytewise-equal to the pre-call snapshot), not a claim.
    assert _snapshot(doc_paths) == before


def test_bad_feature_id_shape_restores_prior_spine(tmp_path, tmp_state, monkeypatch):
    project_root = _onboarded_project(tmp_path)
    _seed_assessment(project_root)
    _use_questionnaire_routes()
    doc_paths = _doc_paths(project_root)
    before = _snapshot(doc_paths)

    payload = {
        "north_star_body": "Vision text.",
        "product_version": 1,
        "features": [
            # 'feat-1' does not match the required ^F[0-9]{3,}$ id pattern.
            {"id": "feat-1", "title": "Bad id shape", "acceptance": ["x"], "status": "planned"},
        ],
        "milestones": [],
        "backlog_items": [],
    }
    _stub_turn(tmp_path, monkeypatch, _draft_reply(payload), tag="badid")

    with pytest.raises(onboarding_questionnaire.UnapprovableDraft) as excinfo:
        onboarding_questionnaire.run_questionnaire(project_root)

    assert len(excinfo.value.findings) > 0
    assert _snapshot(doc_paths) == before


# ==========================================================================
# O4 -- read-only + redacted dispatch
# ==========================================================================

def test_dispatch_is_readonly_context_carrying_and_redacted(tmp_path, tmp_state, monkeypatch):
    project_root = _onboarded_project(tmp_path)
    _seed_assessment(project_root, intent_summary="A CLI tool for widgets.",
                      gaps=["no roadmap present"])
    _use_questionnaire_routes()

    secret = "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123"
    payload = {
        "north_star_body": f"Vision. token {secret} found in config.",
        "product_version": 1,
        "features": [
            {"id": "F001", "title": "Feature One", "acceptance": ["a thing"], "status": "planned"},
        ],
        "milestones": [],
        "backlog_items": [],
    }
    record_file = _stub_turn(tmp_path, monkeypatch, _draft_reply(payload), tag="redact")

    result = onboarding_questionnaire.run_questionnaire(project_root)
    assert result.lint_clean is True

    recorded = record_file.read_text(encoding="utf-8")
    assert "--allowedTools" in recorded
    assert "Read Grep Glob" in recorded
    assert "--disallowedTools" in recorded
    assert "Edit Write Bash" in recorded
    assert "--append-system-prompt" in recorded
    assert "A CLI tool for widgets." in recorded
    assert "no roadmap present" in recorded

    ns_text = _doc_paths(project_root)["north_star"].read_text(encoding="utf-8")
    assert secret not in ns_text
    assert "[REDACTED]" in ns_text


# ==========================================================================
# O5 -- no route configured -> typed error, no dispatch, no spine change
# ==========================================================================

def test_no_questionnaire_route_configured_raises_without_dispatch(tmp_path, tmp_state, monkeypatch):
    project_root = _onboarded_project(tmp_path)
    _seed_assessment(project_root)
    paths.routes_path().write_text(ROUTES_TOML_NO_QUESTIONNAIRE, encoding="utf-8")
    doc_paths = _doc_paths(project_root)
    before = _snapshot(doc_paths)

    def boom(*a, **kw):
        raise AssertionError("adapters.build_dispatch must not be called without a route")
    monkeypatch.setattr(adapters, "build_dispatch", boom)

    with pytest.raises(onboarding_questionnaire.NoQuestionnaireRoute):
        onboarding_questionnaire.run_questionnaire(project_root)

    assert _snapshot(doc_paths) == before


# ==========================================================================
# greenfield: skipped assessment -> typed refusal, no dispatch
# ==========================================================================

def test_greenfield_assessment_raises_typed_error_without_dispatch(tmp_path, tmp_state, monkeypatch):
    project_root = tmp_path / "empty-proj"
    project_root.mkdir()
    answers = onboarding.WizardAnswers(
        maturity="empty", docs_present=False, mode="greenfield-define-it",
        scan_paths=["."],
    )
    onboarding.run_wizard(project_root, answers)
    _seed_assessment(project_root, skipped=True, maturity="empty",
                      existing_docs=[], existing_tests=[], intent_summary="",
                      gaps=[], skip_reason="greenfield project: nothing to scan")

    def boom(*a, **kw):
        raise AssertionError("adapters.build_dispatch must not be called for a greenfield assessment")
    monkeypatch.setattr(adapters, "build_dispatch", boom)

    with pytest.raises(onboarding_questionnaire.GreenfieldQuestionnaireUnsupported):
        onboarding_questionnaire.run_questionnaire(project_root)


# ==========================================================================
# O6 -- the `onboard --questionnaire` CLI flag
# ==========================================================================

def test_cli_onboard_questionnaire_flag_runs_and_prints_summary(tmp_path, tmp_state, monkeypatch, capsys):
    project_root = tmp_path / "cliproj"
    _use_questionnaire_routes()

    exit_code = cli.main([
        "onboard", str(project_root), "--maturity", "mature", "--mode", "derive-from-code",
    ])
    assert exit_code == 0
    capsys.readouterr()

    _seed_assessment(project_root)
    _stub_turn(tmp_path, monkeypatch, _draft_reply(VALID_DRAFT_PAYLOAD), tag="cli-happy")

    exit_code = cli.main([
        "onboard", str(project_root), "--maturity", "mature", "--mode", "derive-from-code",
        "--questionnaire",
    ])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "spine drafted:" in out
    assert "2 features" in out
    assert "1 milestones" in out
    assert "1 backlog items" in out


def test_cli_onboard_questionnaire_flag_errors_without_stored_assessment(tmp_path, tmp_state, monkeypatch, capsys):
    project_root = tmp_path / "cliproj-noassess"

    def boom(*a, **kw):
        raise AssertionError("must not dispatch without a stored assessment")
    monkeypatch.setattr(adapters, "build_dispatch", boom)

    exit_code = cli.main(["onboard", str(project_root), "--questionnaire"])
    assert exit_code != 0
    err = capsys.readouterr().err
    assert "assessment" in err.lower()


def test_cli_onboard_questionnaire_flag_errors_clearly_on_greenfield(tmp_path, tmp_state, monkeypatch, capsys):
    project_root = tmp_path / "cliproj-greenfield"

    exit_code = cli.main([
        "onboard", str(project_root), "--maturity", "empty", "--mode", "greenfield-define-it",
    ])
    assert exit_code == 0
    capsys.readouterr()

    # A stored (skipped/greenfield) assessment -- bypasses the "no assessment
    # stored" check so the greenfield-specific typed refusal is what's
    # actually exercised here.
    _seed_assessment(project_root, skipped=True, maturity="empty", existing_docs=[],
                      existing_tests=[], intent_summary="", gaps=[],
                      skip_reason="greenfield project: nothing to scan")

    def boom(*a, **kw):
        raise AssertionError("must not dispatch for a greenfield assessment")
    monkeypatch.setattr(adapters, "build_dispatch", boom)

    exit_code = cli.main(["onboard", str(project_root), "--questionnaire"])
    assert exit_code != 0
    err = capsys.readouterr().err
    assert "greenfield" in err.lower() or "skipped" in err.lower()
