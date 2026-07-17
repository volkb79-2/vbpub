"""Tests for nyxloom.onboarding_scan (PACKAGE F3: read-only onboarding
assessment scan agent, docs/nyxloom-operating-model.md §2 step 3).

Cross-package seam (adapters.build_dispatch) is monkeypatched using the SAME
record-argv/emit shell-script convention test_decision_chat.py /
test_intake_chat.py establish: a script that `echo "$@" > "$RECORD_FILE"`
then `cat "$EMIT_FILE"`, so onboarding_scan's real subprocess-execution path
runs for real against a canned CLI. No live model/LLM is ever invoked
anywhere in this module (the F3 handoff's gate rule)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from nyxloom import adapters, cli, onboarding, onboarding_scan, paths

# --------------------------------------------------------------------------
# local fixtures / helpers (never added to conftest.py, per its own docstring)

ROUTES_TOML_WITH_SCAN = textwrap.dedent("""\
    revision = "test-rev"

    [tiers.frontier-review]
    routes = ["scan-agent-route"]

    [routes.scan-agent-route]
    cli = "claude"
    model = "claude-test-model"
    """)

ROUTES_TOML_NO_SCAN = textwrap.dedent("""\
    revision = "test-rev"

    [tiers.flash-high]
    routes = ["fake-cli"]

    [routes.fake-cli]
    cli = "fake"
    model = "fake-model"
    probe = ["true"]
    usage_source = "none"
    """)


def _use_scan_routes() -> None:
    paths.routes_path().write_text(ROUTES_TOML_WITH_SCAN, encoding="utf-8")


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
    """A project that already ran the F2 non-AI wizard (trove + nyxloom.toml
    + recorded answers) -- the state F3 always runs after."""
    project_root = tmp_path / "myproj"
    project_root.mkdir()
    kwargs = dict(maturity="mature", docs_present=False, mode="derive-from-code",
                  scan_paths=["src"])
    kwargs.update(answer_overrides)
    answers = onboarding.WizardAnswers(**kwargs)
    onboarding.run_wizard(project_root, answers)
    return project_root


VALID_ASSESSMENT_REPLY = (
    "I read through src/ and the existing tests.\n\n"
    "ASSESSMENT_JSON:\n"
    '{"maturity": "mature", "existing_docs": ["README.md"], '
    '"existing_tests": ["tests/test_foo.py"], '
    '"intent_summary": "A CLI tool for X.", '
    '"gaps": ["no roadmap", "no backlog items"]}\n'
)


# ==========================================================================
# Oracle 1: greenfield short-circuit -- no agent dispatched
# ==========================================================================

def test_greenfield_skips_scan_without_dispatch(tmp_path, tmp_state, monkeypatch):
    project_root = tmp_path / "empty-proj"
    project_root.mkdir()
    answers = onboarding.WizardAnswers(
        maturity="empty", docs_present=False, mode="greenfield-define-it",
        scan_paths=["."],
    )
    onboarding.run_wizard(project_root, answers)

    def boom(*a, **kw):
        raise AssertionError("adapters.build_dispatch must not be called for an empty repo")
    monkeypatch.setattr(adapters, "build_dispatch", boom)

    result = onboarding_scan.run_assessment_scan(project_root, answers)

    assert result.skipped is True
    assert result.skip_reason
    assert result.maturity == "empty"
    assert result.existing_docs == []
    assert result.existing_tests == []
    assert result.gaps == []

    trove_dir = project_root / "nyxloom-trove"
    reloaded = onboarding_scan.load_assessment(trove_dir)
    assert reloaded == result


# ==========================================================================
# Oracle 2: a valid structured reply is parsed, stored, and reloadable
# ==========================================================================

def test_valid_structured_reply_parsed_and_stored(tmp_path, tmp_state, monkeypatch):
    project_root = _onboarded_project(tmp_path)
    answers = onboarding.load_answers(project_root / "nyxloom-trove")
    _use_scan_routes()
    record_file = _stub_turn(tmp_path, monkeypatch, VALID_ASSESSMENT_REPLY, tag="valid")

    result = onboarding_scan.run_assessment_scan(project_root, answers)

    assert result.skipped is False
    assert result.maturity == "mature"
    assert result.existing_docs == ["README.md"]
    assert result.existing_tests == ["tests/test_foo.py"]
    assert result.intent_summary == "A CLI tool for X."
    assert result.gaps == ["no roadmap", "no backlog items"]
    assert result.scanned_at  # a non-empty timestamp was stamped

    trove_dir = project_root / "nyxloom-trove"
    stored_path = onboarding_scan.assessment_path(trove_dir)
    assert stored_path.is_file()
    reloaded = onboarding_scan.load_assessment(trove_dir)
    assert reloaded == result

    # READ-ONLY posture reached the actual dispatched argv, and the system
    # prompt carried the wizard context + scoped scan paths.
    recorded = record_file.read_text(encoding="utf-8")
    assert "--allowedTools" in recorded
    assert "Read Grep Glob" in recorded
    assert "--disallowedTools" in recorded
    assert "Edit Write Bash" in recorded
    assert "--append-system-prompt" in recorded
    assert "derive-from-code" in recorded
    assert "src" in recorded


def test_reply_may_wrap_json_in_a_markdown_fence(tmp_path, tmp_state, monkeypatch):
    """A harmless format slip (the agent fenced the JSON despite being told
    not to) is tolerated -- only genuinely broken/mistyped output fails."""
    project_root = _onboarded_project(tmp_path)
    answers = onboarding.load_answers(project_root / "nyxloom-trove")
    _use_scan_routes()
    fenced_reply = (
        "ASSESSMENT_JSON:\n"
        "```json\n"
        '{"maturity": "partial", "existing_docs": [], "existing_tests": [], '
        '"intent_summary": "early-stage tool", "gaps": ["no tests"]}\n'
        "```\n"
    )
    _stub_turn(tmp_path, monkeypatch, fenced_reply, tag="fenced")

    result = onboarding_scan.run_assessment_scan(project_root, answers)
    assert result.maturity == "partial"
    assert result.gaps == ["no tests"]


# ==========================================================================
# Oracle 3: fail-closed on unparseable output (never silently accepted)
# ==========================================================================

def test_missing_marker_fails_closed(tmp_path, tmp_state, monkeypatch):
    project_root = _onboarded_project(tmp_path)
    answers = onboarding.load_answers(project_root / "nyxloom-trove")
    _use_scan_routes()
    _stub_turn(tmp_path, monkeypatch, "Sure, here is my analysis in plain prose.\n", tag="nomarker")

    with pytest.raises(onboarding_scan.UnparseableAssessment):
        onboarding_scan.run_assessment_scan(project_root, answers)

    trove_dir = project_root / "nyxloom-trove"
    assert not onboarding_scan.assessment_path(trove_dir).exists()


def test_invalid_json_fails_closed(tmp_path, tmp_state, monkeypatch):
    project_root = _onboarded_project(tmp_path)
    answers = onboarding.load_answers(project_root / "nyxloom-trove")
    _use_scan_routes()
    _stub_turn(tmp_path, monkeypatch, "ASSESSMENT_JSON: {not valid json at all\n", tag="badjson")

    with pytest.raises(onboarding_scan.UnparseableAssessment):
        onboarding_scan.run_assessment_scan(project_root, answers)


def test_missing_required_field_fails_closed(tmp_path, tmp_state, monkeypatch):
    project_root = _onboarded_project(tmp_path)
    answers = onboarding.load_answers(project_root / "nyxloom-trove")
    _use_scan_routes()
    bad_reply = (
        "ASSESSMENT_JSON:\n"
        '{"maturity": "mature", "existing_docs": [], "existing_tests": [], "gaps": []}\n'
    )  # missing intent_summary
    _stub_turn(tmp_path, monkeypatch, bad_reply, tag="missingfield")

    with pytest.raises(onboarding_scan.UnparseableAssessment):
        onboarding_scan.run_assessment_scan(project_root, answers)


def test_invalid_maturity_value_fails_closed(tmp_path, tmp_state, monkeypatch):
    project_root = _onboarded_project(tmp_path)
    answers = onboarding.load_answers(project_root / "nyxloom-trove")
    _use_scan_routes()
    bad_reply = (
        "ASSESSMENT_JSON:\n"
        '{"maturity": "somewhat", "existing_docs": [], "existing_tests": [], '
        '"intent_summary": "x", "gaps": []}\n'
    )
    _stub_turn(tmp_path, monkeypatch, bad_reply, tag="badmaturity")

    with pytest.raises(onboarding_scan.UnparseableAssessment):
        onboarding_scan.run_assessment_scan(project_root, answers)


def test_wrong_typed_list_field_fails_closed(tmp_path, tmp_state, monkeypatch):
    """gaps must be a list of strings -- a list containing a non-string is
    just as fail-closed as a missing field."""
    project_root = _onboarded_project(tmp_path)
    answers = onboarding.load_answers(project_root / "nyxloom-trove")
    _use_scan_routes()
    bad_reply = (
        "ASSESSMENT_JSON:\n"
        '{"maturity": "mature", "existing_docs": [], "existing_tests": [], '
        '"intent_summary": "x", "gaps": [1, 2]}\n'
    )
    _stub_turn(tmp_path, monkeypatch, bad_reply, tag="badlist")

    with pytest.raises(onboarding_scan.UnparseableAssessment):
        onboarding_scan.run_assessment_scan(project_root, answers)


# ==========================================================================
# Oracle 4: no 'frontier-review' route configured -> typed error, no dispatch
# ==========================================================================

def test_no_scan_route_configured_raises_without_dispatch(tmp_path, tmp_state, monkeypatch):
    project_root = _onboarded_project(tmp_path)
    answers = onboarding.load_answers(project_root / "nyxloom-trove")
    paths.routes_path().write_text(ROUTES_TOML_NO_SCAN, encoding="utf-8")

    def boom(*a, **kw):
        raise AssertionError("adapters.build_dispatch must not be called without a route")
    monkeypatch.setattr(adapters, "build_dispatch", boom)

    with pytest.raises(onboarding_scan.NoScanRouteConfigured):
        onboarding_scan.run_assessment_scan(project_root, answers)

    trove_dir = project_root / "nyxloom-trove"
    assert not onboarding_scan.assessment_path(trove_dir).exists()


# ==========================================================================
# Oracle 5: the reply is redacted before it is parsed/stored
# ==========================================================================

def test_reply_redacted_before_storing(tmp_path, tmp_state, monkeypatch):
    project_root = _onboarded_project(tmp_path)
    answers = onboarding.load_answers(project_root / "nyxloom-trove")
    _use_scan_routes()
    secret = "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123"
    reply = (
        "ASSESSMENT_JSON:\n"
        '{"maturity": "mature", "existing_docs": [], "existing_tests": [], '
        f'"intent_summary": "token {secret} found in config", "gaps": []}}\n'
    )
    _stub_turn(tmp_path, monkeypatch, reply, tag="secret")

    result = onboarding_scan.run_assessment_scan(project_root, answers)

    assert secret not in result.intent_summary
    assert "[REDACTED]" in result.intent_summary

    trove_dir = project_root / "nyxloom-trove"
    stored_text = onboarding_scan.assessment_path(trove_dir).read_text(encoding="utf-8")
    assert secret not in stored_text


# ==========================================================================
# Oracle 6: the `onboard --scan` CLI flag runs F3 after the F2 wizard
# ==========================================================================

def test_cli_onboard_scan_flag_runs_scan_and_prints_result(tmp_path, tmp_state, monkeypatch, capsys):
    project_root = tmp_path / "cliproj"
    _use_scan_routes()
    _stub_turn(tmp_path, monkeypatch, VALID_ASSESSMENT_REPLY, tag="cli")

    exit_code = cli.main([
        "onboard", str(project_root),
        "--maturity", "mature", "--mode", "derive-from-code",
        "--scan-path", "src", "--scan",
    ])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "assessment recorded:" in out
    assert "maturity: mature" in out

    trove_dir = project_root / "nyxloom-trove"
    assert onboarding_scan.assessment_path(trove_dir).is_file()


def test_cli_onboard_scan_flag_skips_for_empty_maturity(tmp_path, tmp_state, monkeypatch, capsys):
    project_root = tmp_path / "cliproj-empty"

    def boom(*a, **kw):
        raise AssertionError("must not dispatch an agent for an empty/greenfield project")
    monkeypatch.setattr(adapters, "build_dispatch", boom)

    exit_code = cli.main(["onboard", str(project_root), "--scan"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "scan skipped:" in out


def test_cli_onboard_without_scan_flag_never_dispatches(tmp_path, tmp_state, monkeypatch, capsys):
    """The plain (no --scan) `onboard` verb stays exactly as F2 left it --
    no agent dispatch attempted even when a scan route IS configured, and
    none of F3's scan-output lines are printed."""
    project_root = tmp_path / "cliproj-plain"
    _use_scan_routes()

    def boom(*a, **kw):
        raise AssertionError("adapters.build_dispatch must not be called without --scan")
    monkeypatch.setattr(adapters, "build_dispatch", boom)

    exit_code = cli.main(["onboard", str(project_root), "--maturity", "mature"])
    assert exit_code == 0
    out = capsys.readouterr().out
    # F3's own output markers must be absent (a substring check on "scan"
    # alone would false-positive on the project path / --scan-path help).
    assert "assessment recorded:" not in out
    assert "scan skipped:" not in out
    # And no assessment file was written.
    trove_dir = project_root / "nyxloom-trove"
    assert not onboarding_scan.assessment_path(trove_dir).exists()
