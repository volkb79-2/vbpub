"""Tests for GA3 v2 (src/nyxloom/gate_scaffold.py, docs/plan-gate-adoption.md
§GA3 "v2 scaffolding"): onboarding ACTS on a missing-gate offer by writing a
reviewable gate skeleton (Dockerfile + `[gates.*]` TOML section) into the
project's trove. Engine-level coverage here; the CLI `--scaffold-gate`
wiring is exercised in tests/test_cli.py.

D-GA3v2 (see the handoff this package was built from): oracles test the
MECHANISM (files written, config parses, gate detected, idempotent/no-
overwrite, adjust markers present) -- NOT that the scaffolded gate actually
runs/passes. No `docker build`/`docker run` anywhere in this file."""

from __future__ import annotations

import pytest

from nyxloom import gate_scaffold, onboarding, onboarding_gate
from nyxloom.config import ProjectConfig


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
# oracle 1: end-to-end flip (the headline contract)

def test_scaffold_gate_flips_assess_gate_to_has_gate_true(tmp_path):
    project_root = tmp_path / "myproj"
    project_root.mkdir()
    onboarding.run_wizard(project_root, _answers())

    offer = onboarding_gate.assess_gate(project_root)
    assert offer.has_gate is False

    result = gate_scaffold.scaffold_gate(project_root, offer)
    assert result.scaffolded is True

    new_offer = onboarding_gate.assess_gate(project_root)
    assert new_offer.has_gate is True
    assert new_offer.gate_id == "scaffolded-pytest"


# ---------------------------------------------------------------------------
# oracle 2: config reloads

def test_scaffold_gate_config_reloads_with_a_valid_gatedef(tmp_path):
    project_root = tmp_path / "myproj"
    project_root.mkdir()
    onboarding.run_wizard(project_root, _answers())
    offer = onboarding_gate.assess_gate(project_root)

    gate_scaffold.scaffold_gate(project_root, offer)

    cfg = ProjectConfig.load(project_root)
    gate = cfg.gates["scaffolded-pytest"]
    assert gate.phase == "post-merge"
    assert gate.asserts == ["tests-pass", "changed-line-coverage"]
    assert gate.argv


# ---------------------------------------------------------------------------
# oracle 3: Dockerfile written, markers present in both artifacts

def test_scaffold_gate_writes_a_dockerfile_with_adjust_markers(tmp_path):
    project_root = tmp_path / "myproj"
    project_root.mkdir()
    onboarding.run_wizard(project_root, _answers())
    offer = onboarding_gate.assess_gate(project_root)

    result = gate_scaffold.scaffold_gate(project_root, offer)

    assert result.dockerfile_path is not None
    assert result.dockerfile_path.is_file()
    dockerfile_text = result.dockerfile_path.read_text()
    assert "pytest" in dockerfile_text
    assert "pytest-cov" in dockerfile_text
    assert gate_scaffold.ADJUST_MARKER in dockerfile_text

    cfg = ProjectConfig.load(project_root)
    gate = cfg.gates["scaffolded-pytest"]
    assert any(gate_scaffold.ADJUST_MARKER in tok for tok in gate.argv)


def test_scaffolded_gate_uses_the_detached_transport_safe_form():
    """The scaffolded argv must run the container DETACHED (docker run -d ->
    docker wait -> docker logs), never an attached `docker run --rm`. An
    attached run reads its verdict off the hijacked stream, which a truncating
    transport corrupts (reference/LESSONS.md L18 defeat 4). Guards against a
    future 'simplification' back to the vulnerable form."""
    gate = gate_scaffold.render_gate_def()
    outer = gate.argv[-1]
    assert "docker run -d" in outer
    assert "docker wait" in outer
    assert "docker logs" in outer
    assert 'exit "$code"' in outer
    # the vulnerable attached form must be gone
    assert "docker run --rm" not in outer


def test_scaffolded_gate_runs_in_the_batch_cgroup_slice():
    """The scaffolded argv must place the gate container in the dedicated
    gates slice: a gate is CPU/IO-heavy batch work that must not compete with
    whatever else the host runs. A missing slice fails OPEN (systemd creates a
    transient limitless one), so `nyxloom doctor` re-checks it exists; that
    check only has something to check because the slice is named HERE."""
    gate = gate_scaffold.render_gate_def()
    outer = gate.argv[-1]
    assert f"--cgroup-parent={gate_scaffold.GATE_SLICE}" in outer
    assert gate_scaffold.GATE_SLICE == "nyxloom-gates.slice"
    # the slice is host-provisioned, so it must be flagged for review, not
    # silently assumed to exist on an arbitrary target host
    assert gate_scaffold.ADJUST_MARKER in outer


# ---------------------------------------------------------------------------
# oracle 4: no-overwrite / idempotent

def test_scaffold_gate_skips_and_does_not_mutate_when_a_gate_already_exists(tmp_path):
    project_root = tmp_path / "proj"
    project_root.mkdir()
    onboarding.run_wizard(project_root, _answers())
    toml_path = project_root / "nyxloom-trove" / "nyxloom.toml"
    toml_path.write_text(
        toml_path.read_text() + (
            "\n[gates.pytest-q]\n"
            "argv = [\"true\"]\n"
            "phase = \"implementation\"\n"
            "timeout_seconds = 60\n"
        )
    )
    before = toml_path.read_bytes()

    offer = onboarding_gate.assess_gate(project_root)
    assert offer.has_gate is True

    result = gate_scaffold.scaffold_gate(project_root, offer)

    assert result.scaffolded is False
    assert result.skipped_reason
    assert toml_path.read_bytes() == before


def test_scaffold_gate_called_twice_does_not_duplicate_the_gates_section(tmp_path):
    project_root = tmp_path / "proj"
    project_root.mkdir()
    onboarding.run_wizard(project_root, _answers())
    toml_path = project_root / "nyxloom-trove" / "nyxloom.toml"

    offer1 = onboarding_gate.assess_gate(project_root)
    first = gate_scaffold.scaffold_gate(project_root, offer1)
    assert first.scaffolded is True
    after_first = toml_path.read_text()
    assert after_first.count("[gates.scaffolded-pytest]") == 1

    offer2 = onboarding_gate.assess_gate(project_root)
    assert offer2.has_gate is True
    second = gate_scaffold.scaffold_gate(project_root, offer2)

    assert second.scaffolded is False
    assert second.skipped_reason
    assert toml_path.read_text() == after_first
    assert after_first.count("[gates.scaffolded-pytest]") == 1


# ---------------------------------------------------------------------------
# oracle 5: missing trove precondition

def test_scaffold_gate_on_a_bare_dir_with_no_trove_is_a_noop(tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    offer = onboarding_gate.GateOffer(has_gate=False, gate_id=None, recommendation="x")

    result = gate_scaffold.scaffold_gate(bare, offer)

    assert result.scaffolded is False
    assert result.skipped_reason
    assert not (bare / "nyxloom-trove").exists()
    assert list(bare.iterdir()) == []


# ---------------------------------------------------------------------------
# oracle 6: emitters are pure -- no subprocess

def test_render_functions_do_not_shell_out(monkeypatch):
    import subprocess

    def _boom(*a, **kw):
        raise AssertionError("gate_scaffold render_* must not run a subprocess")

    monkeypatch.setattr(subprocess, "run", _boom)

    dockerfile_text = gate_scaffold.render_gate_dockerfile()
    assert dockerfile_text

    gatedef = gate_scaffold.render_gate_def("scaffolded-pytest")
    assert gatedef.gate_id == "scaffolded-pytest"

    section = gate_scaffold.render_gate_toml_section(gatedef)
    assert "[gates.scaffolded-pytest]" in section


# ---------------------------------------------------------------------------
# ScaffoldResult shape + edge case: config file with no trailing newline
# (a genuine changed-line branch in scaffold_gate's surgical text-append --
# the freshly-scaffolded template always ends with one, so this exercises
# the "add the missing separator" path explicitly rather than leaving it
# uncovered).

def test_scaffold_gate_appends_a_separating_newline_when_config_lacks_one(tmp_path):
    project_root = tmp_path / "proj"
    project_root.mkdir()
    onboarding.run_wizard(project_root, _answers())
    toml_path = project_root / "nyxloom-trove" / "nyxloom.toml"
    toml_path.write_text(toml_path.read_text().rstrip("\n"))
    assert not toml_path.read_text().endswith("\n")

    offer = onboarding_gate.assess_gate(project_root)
    result = gate_scaffold.scaffold_gate(project_root, offer)

    assert result.scaffolded is True
    cfg = ProjectConfig.load(project_root)
    assert "scaffolded-pytest" in cfg.gates


def test_scaffold_result_is_a_frozen_dataclass():
    result = gate_scaffold.ScaffoldResult(
        scaffolded=False, gate_id="x", dockerfile_path=None,
        config_path=None, skipped_reason="because",
    )
    with pytest.raises(Exception):
        result.scaffolded = True  # type: ignore[misc]
