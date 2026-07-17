"""Tests for nyxloom.spine_writer (PACKAGE F4a: the spine-doc frontmatter
writer). `lint` and `frontmatter` are used HERE (the test) to prove the
written docs round-trip and lint-green -- spine_writer.py itself imports
neither. Local fixtures only (conftest.py is FROZEN)."""

from __future__ import annotations

from pathlib import Path

from nyxloom import frontmatter, lint, onboarding
from nyxloom.config import ProjectConfig
from nyxloom.spine_writer import (
    BacklogItem,
    Feature,
    Milestone,
    write_backlog,
    write_north_star,
    write_product_definition,
    write_roadmap,
)

# --------------------------------------------------------------------------
# local fixtures / helpers (never added to conftest.py, per its own docstring)


def _onboarded_project(tmp_path, **answer_overrides) -> Path:
    """A project that already ran the F2 non-AI wizard (trove + wired
    nyxloom.toml + minimal-valid placeholder spine docs) -- the state this
    writer always runs against (mirrors test_onboarding_scan.py's helper of
    the same name)."""
    project_root = tmp_path / "myproj"
    project_root.mkdir()
    kwargs = dict(maturity="mature", docs_present=False, mode="derive-from-code",
                  scan_paths=["src"])
    kwargs.update(answer_overrides)
    answers = onboarding.WizardAnswers(**kwargs)
    onboarding.run_wizard(project_root, answers)
    return project_root


def _all_lint_findings(project_root: Path):
    cfg = ProjectConfig.load(project_root)
    per_doc = lint.lint_spine(cfg)
    flat = [f for findings in per_doc.values() for f in findings]
    return per_doc, flat


# ==========================================================================
# O1 -- a schema-valid, cross-consistent populated spine is lint-green;
# a bad id shape / a dangling cross-doc reference genuinely trip S1/S2 (so
# the positive case isn't vacuously green).
# ==========================================================================


def test_populated_spine_with_real_cross_references_is_lint_green(tmp_path):
    project_root = _onboarded_project(tmp_path)

    write_north_star(project_root, body="# Vision\n\nWhy this project exists.\n")
    write_product_definition(
        project_root,
        product_version=1,
        features=[
            Feature(id="F001", title="Feature One",
                    acceptance=["it does the first thing"],
                    status="planned", milestone="M1"),
            Feature(id="F002", title="Feature Two",
                    acceptance=["it does the second thing"],
                    status="building"),
        ],
    )
    write_roadmap(
        project_root,
        milestones=[
            Milestone(id="M1", title="First milestone", target_product_version=1,
                       features=["F001", "F002"], status="planned"),
        ],
    )
    write_backlog(
        project_root,
        items=[
            BacklogItem(id="B1", title="A real idea", type="feature",
                        folds_into="F001"),
        ],
    )

    per_doc, flat = _all_lint_findings(project_root)
    assert lint.has_blocking(flat) is False
    for relpath, findings in per_doc.items():
        assert not any(f.severity == "error" for f in findings), (relpath, findings)


def test_bad_feature_id_and_dangling_milestone_reference_trip_lint_errors(tmp_path):
    """NEGATIVE for O1: an `id` that doesn't match the schema pattern (S1)
    and a roadmap milestone referencing a feature id that doesn't exist in
    product-definition (S2) both genuinely fail lint -- proving the positive
    test above isn't vacuously green (it truly exercises S1 + S2, not just
    a shape that always passes)."""
    project_root = _onboarded_project(tmp_path)

    write_north_star(project_root, body="prose")
    write_product_definition(
        project_root,
        product_version=1,
        features=[
            Feature(id="feat-1", title="Bad id shape", acceptance=["x"],
                    status="planned"),
        ],
    )
    write_roadmap(
        project_root,
        milestones=[
            Milestone(id="M1", title="Dangling ref", target_product_version=1,
                       features=["F999"], status="planned"),
        ],
    )
    write_backlog(project_root, items=[])

    per_doc, flat = _all_lint_findings(project_root)
    assert lint.has_blocking(flat) is True

    product_def_findings = per_doc["nyxloom-trove/2-product-definition.md"]
    assert any(f.rule == "S1" for f in product_def_findings), product_def_findings

    # product-definition is S1-dirty, so it's excluded from the S2 cross-doc
    # pass -- the roadmap's reference is still flagged because feature_ids
    # resolves to an EMPTY set (no clean product-def to resolve against).
    roadmap_findings = per_doc["nyxloom-trove/3-roadmap.md"]
    assert any(f.rule == "S2" for f in roadmap_findings), roadmap_findings


def test_duplicate_feature_ids_trip_s5(tmp_path):
    """NEGATIVE (S5): duplicate ids within one doc's own collection are a
    hard error, independent of an otherwise schema-valid shape."""
    project_root = _onboarded_project(tmp_path)
    write_product_definition(
        project_root,
        product_version=1,
        features=[
            Feature(id="F001", title="One", acceptance=["a"], status="planned"),
            Feature(id="F001", title="Duplicate", acceptance=["b"], status="planned"),
        ],
    )

    _per_doc, flat = _all_lint_findings(project_root)
    assert any(f.rule == "S5" for f in flat), flat


# ==========================================================================
# O2 -- overwrites the F2 placeholder IN PLACE: no duplicate/sibling doc,
# no leftover empty collection.
# ==========================================================================


def test_write_product_definition_replaces_placeholder_in_place(tmp_path):
    project_root = _onboarded_project(tmp_path)
    cfg = ProjectConfig.load(project_root)
    target = project_root / cfg.product_definition

    placeholder_fm, _body, _line = frontmatter.split_frontmatter(target.read_text())
    assert placeholder_fm["features"] == []  # F2's minimal-valid placeholder

    written_path = write_product_definition(
        project_root,
        product_version=1,
        features=[
            Feature(id="F001", title="One", acceptance=["a"], status="planned"),
            Feature(id="F002", title="Two", acceptance=["b"], status="planned"),
        ],
    )
    assert written_path == target

    fm, _body, _line = frontmatter.split_frontmatter(target.read_text())
    assert len(fm["features"]) == 2  # placeholder emptiness is gone
    assert [f["id"] for f in fm["features"]] == ["F001", "F002"]

    trove_dir = project_root / "nyxloom-trove"
    matches = sorted(p.name for p in trove_dir.glob("2-product-definition*"))
    assert matches == ["2-product-definition.md"]  # exactly one doc, no duplicate/sibling


# ==========================================================================
# O3 -- round-trips: parsed-back frontmatter equals the structured input
# EXACTLY (hardcoded expected dicts, not recomputed via to_frontmatter(), so
# a bug in to_frontmatter() itself can't cancel out against the assertion).
# Unset optionals must be ABSENT from the emitted yaml, never `null`.
# ==========================================================================


def test_north_star_round_trips(tmp_path):
    project_root = _onboarded_project(tmp_path)
    path = write_north_star(project_root, body="# Vision\n\nA real narrative about why.\n")

    fm, parsed_body, _line = frontmatter.split_frontmatter(path.read_text())
    assert fm == {"kind": "north-star", "schema_version": 1}
    assert "A real narrative about why." in parsed_body


def test_product_definition_round_trips_and_omits_none_milestone(tmp_path):
    project_root = _onboarded_project(tmp_path)
    features = [
        Feature(id="F001", title="With milestone",
                acceptance=["criterion a", "criterion b"],
                status="planned", milestone="M1"),
        Feature(id="F002", title="No milestone", acceptance=["criterion c"],
                status="shipped"),
    ]
    path = write_product_definition(
        project_root, product_version=2, features=features,
        non_goals=["not building X"],
    )

    raw = path.read_text()
    assert "null" not in raw  # NEGATIVE: omit, don't emit `milestone: null`

    fm, _body, _line = frontmatter.split_frontmatter(raw)
    assert fm["product_version"] == 2
    assert fm["non_goals"] == ["not building X"]
    assert fm["features"] == [
        {
            "id": "F001", "title": "With milestone",
            "acceptance": ["criterion a", "criterion b"],
            "status": "planned", "milestone": "M1",
        },
        {
            "id": "F002", "title": "No milestone",
            "acceptance": ["criterion c"], "status": "shipped",
        },
    ]
    assert "milestone" not in fm["features"][1]  # F002 had milestone=None -> absent


def test_roadmap_round_trips(tmp_path):
    project_root = _onboarded_project(tmp_path)
    milestones = [
        Milestone(id="M1", title="First", target_product_version=1,
                   features=["F001", "F002"], status="active"),
    ]
    path = write_roadmap(project_root, milestones=milestones)

    fm, _body, _line = frontmatter.split_frontmatter(path.read_text())
    assert fm["milestones"] == [
        {
            "id": "M1", "title": "First", "target_product_version": 1,
            "features": ["F001", "F002"], "status": "active",
        },
    ]


def test_backlog_round_trips_and_omits_none_optionals(tmp_path):
    project_root = _onboarded_project(tmp_path)
    items = [
        BacklogItem(id="B1", title="Full", type="bugfix", component="ui",
                    context_estimate="small", folds_into="F001"),
        BacklogItem(id="B2", title="Bare", type="feature"),
    ]
    path = write_backlog(project_root, items=items)

    raw = path.read_text()
    assert "null" not in raw  # NEGATIVE: omit, don't emit null optionals

    fm, _body, _line = frontmatter.split_frontmatter(raw)
    assert fm["items"] == [
        {
            "id": "B1", "title": "Full", "type": "bugfix", "component": "ui",
            "context_estimate": "small", "folds_into": "F001",
        },
        {"id": "B2", "title": "Bare", "type": "feature"},
    ]
    bare = fm["items"][1]
    assert "component" not in bare
    assert "context_estimate" not in bare
    assert "folds_into" not in bare
