"""Tests for GA3 v1 (src/nyxloom/onboarding_gate.py,
docs/plan-gate-adoption.md §GA3): onboarding DETECTS + OFFERS a missing
verification gate, but does not scaffold one (v2). Engine-level coverage
here; the CLI `--check-gate` wiring is exercised in tests/test_cli.py."""

from __future__ import annotations

import pytest

from nyxloom import onboarding, onboarding_gate


def _answers(**overrides) -> onboarding.WizardAnswers:
    kwargs = dict(
        maturity="empty",
        docs_present=False,
        mode="greenfield-define-it",
        scan_paths=["."],
    )
    kwargs.update(overrides)
    return onboarding.WizardAnswers(**kwargs)


# ---------------------------------------------------------------------------
# assess_gate: NO_GATE

def test_assess_gate_reports_no_gate_for_a_fresh_greenfield_project(tmp_path):
    """A freshly-scaffolded project (run_wizard's own template comments out
    `[gates.*]` -- see onboarding._INIT_NYXLOOM_TOML) has no gate at all.
    Oracle: has_gate is False, gate_id is None, and the recommendation is a
    non-empty string naming the concrete next step."""
    project_root = tmp_path / "myproj"
    project_root.mkdir()
    onboarding.run_wizard(project_root, _answers())

    offer = onboarding_gate.assess_gate(project_root)

    assert offer.has_gate is False
    assert offer.gate_id is None
    assert offer.recommendation
    assert "[gates." in offer.recommendation
    assert "nyxloom gate verify" in offer.recommendation


def test_assess_gate_no_gate_recommendation_names_the_standard(tmp_path):
    """The offer should point at the gate-contract doc so an operator knows
    where the rules live, not just that something is missing."""
    project_root = tmp_path / "myproj"
    project_root.mkdir()
    onboarding.run_wizard(project_root, _answers())

    offer = onboarding_gate.assess_gate(project_root)

    assert "STANDARD.md" in offer.recommendation


# ---------------------------------------------------------------------------
# assess_gate: HAS a gate

def test_assess_gate_reports_has_gate_for_sample_project(tmp_state, sample_project):
    """sample_project declares one `[gates.pytest-q]` (phase=implementation)
    -- assess_gate must report has_gate=True with that gate's id, and the
    recommendation must NOT contain the "no gate" offer language."""
    offer = onboarding_gate.assess_gate(sample_project.root)

    assert offer.has_gate is True
    assert offer.gate_id == "pytest-q"
    assert offer.recommendation
    assert "no verification gate declared" not in offer.recommendation
    assert "nyxloom gate verify" in offer.recommendation


def test_assess_gate_is_a_pure_config_read_no_subprocess(tmp_state, sample_project, monkeypatch):
    """assess_gate must not shell out -- it is a cheap config check, never a
    real gate invocation (that stays the operator's/GA1's/GA4's job)."""
    import subprocess

    def _boom(*a, **kw):
        raise AssertionError("assess_gate must not run a subprocess")

    monkeypatch.setattr(subprocess, "run", _boom)

    offer = onboarding_gate.assess_gate(sample_project.root)
    assert offer.has_gate is True


# ---------------------------------------------------------------------------
# GateOffer shape

def test_gate_offer_is_a_frozen_dataclass():
    offer = onboarding_gate.GateOffer(has_gate=False, gate_id=None, recommendation="x")
    with pytest.raises(Exception):
        offer.has_gate = True  # type: ignore[misc]
