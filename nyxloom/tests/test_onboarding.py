"""Tests for the F2 onboarding engine + non-AI wizard
(src/nyxloom/onboarding.py, docs/nyxloom-operating-model.md §2).

These exercise the engine directly (no CLI/argparse layer -- see
tests/test_cli.py for the `onboard` verb tests). No AI/LLM is invoked
anywhere in this module or in onboarding.py itself."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nyxloom import lint, onboarding
from nyxloom.config import ProjectConfig

_SPINE_FILENAMES = (
    "1-north-star.md",
    "2-product-definition.md",
    "3-roadmap.md",
    "4-backlog.md",
)


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
# WizardAnswers validation

def test_wizard_answers_rejects_invalid_maturity():
    with pytest.raises(ValueError):
        onboarding.WizardAnswers(maturity="nonsense", docs_present=False,
                                  mode="greenfield-define-it")


def test_wizard_answers_rejects_invalid_mode():
    with pytest.raises(ValueError):
        onboarding.WizardAnswers(maturity="empty", docs_present=False,
                                  mode="nonsense")


def test_wizard_answers_accepts_every_documented_choice():
    for maturity in onboarding.MATURITY_CHOICES:
        for mode in onboarding.MODE_CHOICES:
            onboarding.WizardAnswers(maturity=maturity, docs_present=True, mode=mode)


# ---------------------------------------------------------------------------
# scaffold_trove (moved here from cli.py, PACKAGE F2)

def test_scaffold_trove_creates_the_p23_layout(tmp_path):
    project_folder = tmp_path / "myproj"
    trove_dir = onboarding.scaffold_trove(project_folder)

    assert trove_dir == project_folder / "nyxloom-trove"
    for name in ("nyxloom.toml", "STANDARD.md", "AUTHORING.md", "decisions.md",
                 "roadmap.md", "backlog.md", ".gitignore"):
        assert (trove_dir / name).is_file(), name
    for name in ("handoffs", "reports", "archive", "agent-logs"):
        assert (trove_dir / name).is_dir(), name


def test_scaffold_trove_refuses_existing(tmp_path):
    project_folder = tmp_path / "myproj"
    onboarding.scaffold_trove(project_folder)

    with pytest.raises(onboarding.TroveAlreadyExists):
        onboarding.scaffold_trove(project_folder)


# ---------------------------------------------------------------------------
# run_wizard: the greenfield oracle

def test_onboard_greenfield_instantiates_lint_clean_spine(tmp_path):
    """Oracle: onboarding a fresh (no trove) project instantiates all 4
    spine docs, minimal-valid per the F1 schemas -- lint.lint_spine reports
    zero findings and lint.lint_project has no blocking findings. No AI/LLM
    is invoked (run_wizard takes already-collected answers, no input())."""
    project_root = tmp_path / "myproj"
    project_root.mkdir()

    result = onboarding.run_wizard(project_root, _answers())

    trove_dir = project_root / "nyxloom-trove"
    assert result.trove_dir == trove_dir
    for filename in _SPINE_FILENAMES:
        assert (trove_dir / filename).is_file()

    assert sorted(Path(p).name for p in result.created_docs) == sorted(_SPINE_FILENAMES)
    assert result.skipped_docs == []
    assert set(result.wired_keys) == {
        "north_star", "product_definition", "roadmap", "backlog",
    }

    cfg = ProjectConfig.load(project_root)
    assert cfg.north_star == "nyxloom-trove/1-north-star.md"
    assert cfg.product_definition == "nyxloom-trove/2-product-definition.md"
    assert cfg.roadmap == "nyxloom-trove/3-roadmap.md"
    assert cfg.backlog == "nyxloom-trove/4-backlog.md"

    spine_findings = lint.lint_spine(cfg)
    assert set(spine_findings.keys()) == {
        f"nyxloom-trove/{name}" for name in _SPINE_FILENAMES
    }
    assert all(findings == [] for findings in spine_findings.values()), spine_findings

    project_findings = lint.lint_project(cfg)
    all_findings = [f for findings in project_findings.values() for f in findings]
    assert not lint.has_blocking(all_findings), all_findings


def test_onboard_no_ai_scan_paths_are_only_recorded(tmp_path):
    """scan_paths is recorded verbatim, never walked/read -- F2 does not
    build the F3 scan. A nonexistent scan path must not raise."""
    project_root = tmp_path / "myproj"
    project_root.mkdir()

    result = onboarding.run_wizard(
        project_root, _answers(scan_paths=["this/path/does/not/exist"])
    )

    reloaded = onboarding.load_answers(result.trove_dir)
    assert reloaded.scan_paths == ["this/path/does/not/exist"]


# ---------------------------------------------------------------------------
# answers recording + reload

def test_onboard_records_answers_reloadable(tmp_path):
    project_root = tmp_path / "myproj"
    project_root.mkdir()
    answers = _answers(maturity="partial", mode="derive-from-code",
                        docs_present=True, scan_paths=["src", "docs"])

    result = onboarding.run_wizard(project_root, answers)

    assert result.answers_path == result.trove_dir / "onboarding-answers.json"
    assert result.answers_path.is_file()

    data = json.loads(result.answers_path.read_text())
    assert data == {
        "maturity": "partial",
        "docs_present": True,
        "mode": "derive-from-code",
        "scan_paths": ["src", "docs"],
    }

    reloaded = onboarding.load_answers(result.trove_dir)
    assert reloaded == answers


def test_load_answers_missing_raises(tmp_path):
    trove_dir = tmp_path / "nyxloom-trove"
    trove_dir.mkdir()
    with pytest.raises(FileNotFoundError):
        onboarding.load_answers(trove_dir)


# ---------------------------------------------------------------------------
# idempotency

def test_onboard_idempotent_leaves_existing_spine_untouched(tmp_path):
    """Oracle: re-running onboard never overwrites an existing spine doc or
    an already-wired config key, even with different answers on the second
    call -- only the answers file (wizard STATE, not user content) is
    expected to move."""
    project_root = tmp_path / "myproj"
    project_root.mkdir()

    first = onboarding.run_wizard(project_root, _answers())
    trove_dir = first.trove_dir

    # Simulate a human/AI having started filling in the north-star.
    north_star_path = trove_dir / "1-north-star.md"
    custom_north_star = north_star_path.read_text() + "\nHand-authored addition.\n"
    north_star_path.write_text(custom_north_star)

    toml_before = (trove_dir / "nyxloom.toml").read_text()

    second = onboarding.run_wizard(
        project_root,
        _answers(maturity="mature", mode="derive-from-code", docs_present=True),
    )

    assert second.created_docs == []
    assert sorted(Path(p).name for p in second.skipped_docs) == sorted(_SPINE_FILENAMES)
    assert north_star_path.read_text() == custom_north_star
    assert second.wired_keys == []
    assert (trove_dir / "nyxloom.toml").read_text() == toml_before

    # ... but the answers file DOES reflect the latest call.
    reloaded = onboarding.load_answers(trove_dir)
    assert reloaded.maturity == "mature"
    assert reloaded.mode == "derive-from-code"
    assert reloaded.docs_present is True


def test_onboard_reuses_existing_trove_without_rescaffolding(tmp_path):
    """A project that already ran `scaffold_trove`/`init` (trove present, no
    spine docs yet) gets ONLY the spine instantiated -- scaffold_trove is
    not invoked again (no refusal, no re-copy)."""
    project_root = tmp_path / "myproj"
    trove_dir = onboarding.scaffold_trove(project_root)
    standard_before = (trove_dir / "STANDARD.md").read_text()

    result = onboarding.run_wizard(project_root, _answers())

    assert (trove_dir / "STANDARD.md").read_text() == standard_before
    assert len(result.created_docs) == 4
    assert result.skipped_docs == []


def test_onboard_a_partially_adopted_spine_only_fills_gaps(tmp_path):
    """A project with SOME spine docs already present (e.g. hand-authored
    or a prior partial onboard) gets only the missing ones created."""
    project_root = tmp_path / "myproj"
    trove_dir = onboarding.scaffold_trove(project_root)
    existing_north_star = (
        "---\nkind: north-star\nschema_version: 1\n---\n\n"
        "# myproj — real vision\n\nHand-authored already.\n"
    )
    (trove_dir / "1-north-star.md").write_text(existing_north_star)

    result = onboarding.run_wizard(project_root, _answers())

    assert result.skipped_docs == ["nyxloom-trove/1-north-star.md"]
    assert sorted(Path(p).name for p in result.created_docs) == sorted(
        n for n in _SPINE_FILENAMES if n != "1-north-star.md"
    )
    assert (trove_dir / "1-north-star.md").read_text() == existing_north_star
