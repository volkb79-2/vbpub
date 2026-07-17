"""Tests for the direction-spine documents (PACKAGE F1,
docs/spine-documents-spec.md): schemas, config keys, and the non-AI
structural validator (lint.lint_spine, rule namespace S1-S4).

Fixtures use the TROVE layout (nyxloom-trove/nyxloom.toml +
nyxloom-trove/handoffs/), matching how a project that adopts the spine is
actually laid out (see nyxloom's own nyxloom-trove/nyxloom.toml)."""

from __future__ import annotations

import textwrap
from pathlib import Path

from nyxloom import config, lint

REPO_ROOT = Path(__file__).resolve().parent.parent

_BASE_TOML = """\
[project]
id = "demo"
handoff_globs = ["nyxloom-trove/handoffs/*.md"]
{spine_keys}
"""


def _write_project(tmp_path: Path, spine_keys: str, files: dict[str, str]) -> config.ProjectConfig:
    """A trove-layout project root with the given [project] spine keys and
    arbitrary extra files (path relative to root -> content)."""
    root = tmp_path / "proj"
    (root / "nyxloom-trove" / "handoffs").mkdir(parents=True)
    (root / "nyxloom-trove" / "nyxloom.toml").write_text(
        _BASE_TOML.format(spine_keys=spine_keys)
    )
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(content))
    return config.ProjectConfig.load(root)


VALID_PRODUCT_DEF = """\
    ---
    kind: product-definition
    schema_version: 1
    product_version: 1
    features:
      - id: F001
        title: "a feature"
        acceptance: ["it does the thing"]
        status: planned
    ---

    body
    """

VALID_ROADMAP = """\
    ---
    kind: roadmap
    schema_version: 1
    milestones:
      - id: M1
        title: "first milestone"
        target_product_version: 1
        features: [F001]
        status: planned
    ---

    body
    """

VALID_BACKLOG = """\
    ---
    kind: backlog
    schema_version: 1
    items:
      - id: B1
        title: "an item"
        type: feature
        folds_into: F001
    ---

    body
    """

VALID_NORTH_STAR = """\
    ---
    kind: north-star
    schema_version: 1
    ---

    body
    """

DUPLICATE_ID_PRODUCT_DEF = """\
    ---
    kind: product-definition
    schema_version: 1
    product_version: 1
    features:
      - id: F001
        title: "a feature"
        acceptance: ["it does the thing"]
        status: planned
      - id: F001
        title: "a duplicate-id feature"
        acceptance: ["it does another thing"]
        status: planned
    ---

    body
    """

DUPLICATE_ID_ROADMAP = """\
    ---
    kind: roadmap
    schema_version: 1
    milestones:
      - id: M1
        title: "first milestone"
        target_product_version: 1
        features: []
        status: planned
      - id: M1
        title: "duplicate-id milestone"
        target_product_version: 1
        features: []
        status: planned
    ---

    body
    """

DUPLICATE_ID_BACKLOG = """\
    ---
    kind: backlog
    schema_version: 1
    items:
      - id: B1
        title: "an item"
        type: feature
      - id: B1
        title: "a duplicate-id item"
        type: bugfix
    ---

    body
    """


def _findings_for(results: dict[str, list], rel: str):
    return results.get(rel, [])


# ---------------------------------------------------------------------------
# S1: schema validity

class TestS1SchemaValidity:
    def test_valid_product_definition_passes(self, tmp_path):
        cfg = _write_project(
            tmp_path,
            'product_definition = "nyxloom-trove/2-product-definition.md"\n',
            {"nyxloom-trove/2-product-definition.md": VALID_PRODUCT_DEF},
        )
        results = lint.lint_spine(cfg)
        assert _findings_for(results, "nyxloom-trove/2-product-definition.md") == []

    def test_missing_acceptance_fails_s1(self, tmp_path):
        """A feature with acceptance: [] violates the schema's minItems:1 --
        exactly the S1 oracle ('a product-def missing acceptance fails')."""
        bad = VALID_PRODUCT_DEF.replace(
            'acceptance: ["it does the thing"]', "acceptance: []"
        )
        cfg = _write_project(
            tmp_path,
            'product_definition = "nyxloom-trove/2-product-definition.md"\n',
            {"nyxloom-trove/2-product-definition.md": bad},
        )
        results = lint.lint_spine(cfg)
        findings = _findings_for(results, "nyxloom-trove/2-product-definition.md")
        assert findings, "expected an S1 finding"
        assert all(f.rule == "S1" and f.severity == "error" for f in findings)
        assert any("acceptance" in f.message for f in findings)

    def test_missing_required_field_fails_s1(self, tmp_path):
        # Must strip the WHOLE line (including its own leading indent) so
        # the following line's indentation -- and hence YAML validity --
        # is undisturbed; otherwise this produces a YAML syntax error (S4)
        # instead of the intended schema violation (S1).
        bad = VALID_PRODUCT_DEF.replace("    product_version: 1\n", "")
        cfg = _write_project(
            tmp_path,
            'product_definition = "nyxloom-trove/2-product-definition.md"\n',
            {"nyxloom-trove/2-product-definition.md": bad},
        )
        findings = lint.lint_spine(cfg)["nyxloom-trove/2-product-definition.md"]
        assert any(f.rule == "S1" for f in findings)

    def test_valid_north_star_passes(self, tmp_path):
        cfg = _write_project(
            tmp_path,
            'north_star = "nyxloom-trove/1-north-star.md"\n',
            {"nyxloom-trove/1-north-star.md": VALID_NORTH_STAR},
        )
        results = lint.lint_spine(cfg)
        assert _findings_for(results, "nyxloom-trove/1-north-star.md") == []

    def test_wrong_kind_fails_s1(self, tmp_path):
        """A north-star file whose frontmatter kind doesn't match its
        config-key role is a schema (const) violation."""
        bad = VALID_NORTH_STAR.replace("kind: north-star", "kind: roadmap")
        cfg = _write_project(
            tmp_path,
            'north_star = "nyxloom-trove/1-north-star.md"\n',
            {"nyxloom-trove/1-north-star.md": bad},
        )
        findings = lint.lint_spine(cfg)["nyxloom-trove/1-north-star.md"]
        assert any(f.rule == "S1" for f in findings)


# ---------------------------------------------------------------------------
# S2: cross-doc consistency

class TestS2CrossDocConsistency:
    def test_roadmap_milestone_unknown_feature_fails_s2(self, tmp_path):
        bad_roadmap = VALID_ROADMAP.replace("features: [F001]", "features: [F999]")
        cfg = _write_project(
            tmp_path,
            'product_definition = "nyxloom-trove/2-product-definition.md"\n'
            'roadmap = "nyxloom-trove/3-roadmap.md"\n',
            {
                "nyxloom-trove/2-product-definition.md": VALID_PRODUCT_DEF,
                "nyxloom-trove/3-roadmap.md": bad_roadmap,
            },
        )
        findings = lint.lint_spine(cfg)["nyxloom-trove/3-roadmap.md"]
        s2 = [f for f in findings if f.rule == "S2"]
        assert s2, findings
        assert all(f.severity == "error" for f in s2)
        assert "F999" in s2[0].message

    def test_roadmap_milestone_known_feature_passes_s2(self, tmp_path):
        cfg = _write_project(
            tmp_path,
            'product_definition = "nyxloom-trove/2-product-definition.md"\n'
            'roadmap = "nyxloom-trove/3-roadmap.md"\n',
            {
                "nyxloom-trove/2-product-definition.md": VALID_PRODUCT_DEF,
                "nyxloom-trove/3-roadmap.md": VALID_ROADMAP,
            },
        )
        results = lint.lint_spine(cfg)
        assert _findings_for(results, "nyxloom-trove/3-roadmap.md") == []

    def test_backlog_folds_into_unknown_fails_s2(self, tmp_path):
        bad_backlog = VALID_BACKLOG.replace("folds_into: F001", "folds_into: F999")
        cfg = _write_project(
            tmp_path,
            'product_definition = "nyxloom-trove/2-product-definition.md"\n'
            'backlog = "nyxloom-trove/4-backlog.md"\n',
            {
                "nyxloom-trove/2-product-definition.md": VALID_PRODUCT_DEF,
                "nyxloom-trove/4-backlog.md": bad_backlog,
            },
        )
        findings = lint.lint_spine(cfg)["nyxloom-trove/4-backlog.md"]
        s2 = [f for f in findings if f.rule == "S2"]
        assert s2, findings
        assert "F999" in s2[0].message

    def test_backlog_folds_into_feature_resolves(self, tmp_path):
        cfg = _write_project(
            tmp_path,
            'product_definition = "nyxloom-trove/2-product-definition.md"\n'
            'backlog = "nyxloom-trove/4-backlog.md"\n',
            {
                "nyxloom-trove/2-product-definition.md": VALID_PRODUCT_DEF,
                "nyxloom-trove/4-backlog.md": VALID_BACKLOG,
            },
        )
        results = lint.lint_spine(cfg)
        assert _findings_for(results, "nyxloom-trove/4-backlog.md") == []

    def test_backlog_folds_into_milestone_resolves(self, tmp_path):
        """folds_into may resolve to a roadmap MILESTONE id, not just a
        product-def feature id (spec: 'resolves to a real feature/milestone').
        Roadmap has no product-def configured, so its milestone lists no
        features -- keeps this test isolated to the milestone-id path."""
        roadmap_no_features = """\
            ---
            kind: roadmap
            schema_version: 1
            milestones:
              - id: M1
                title: "first milestone"
                target_product_version: 1
                features: []
                status: planned
            ---

            body
            """
        backlog_to_milestone = VALID_BACKLOG.replace("folds_into: F001", "folds_into: M1")
        cfg = _write_project(
            tmp_path,
            'roadmap = "nyxloom-trove/3-roadmap.md"\n'
            'backlog = "nyxloom-trove/4-backlog.md"\n',
            {
                "nyxloom-trove/3-roadmap.md": roadmap_no_features,
                "nyxloom-trove/4-backlog.md": backlog_to_milestone,
            },
        )
        results = lint.lint_spine(cfg)
        assert _findings_for(results, "nyxloom-trove/3-roadmap.md") == []
        assert _findings_for(results, "nyxloom-trove/4-backlog.md") == []

    def test_cross_doc_check_skipped_when_checking_doc_itself_invalid(self, tmp_path):
        """If the roadmap itself is S1/S4-dirty, S2 does not also fire for it
        (nothing coherent to cross-check against a doc that failed to parse)."""
        corrupt_roadmap = "not frontmatter at all\n"
        cfg = _write_project(
            tmp_path,
            'product_definition = "nyxloom-trove/2-product-definition.md"\n'
            'roadmap = "nyxloom-trove/3-roadmap.md"\n',
            {
                "nyxloom-trove/2-product-definition.md": VALID_PRODUCT_DEF,
                "nyxloom-trove/3-roadmap.md": corrupt_roadmap,
            },
        )
        findings = lint.lint_spine(cfg)["nyxloom-trove/3-roadmap.md"]
        assert all(f.rule != "S2" for f in findings)
        assert any(f.rule == "S4" for f in findings)


# ---------------------------------------------------------------------------
# S3: naming / placement / config-key resolution

class TestS3NamingPlacementConfig:
    def test_unset_spine_keys_produce_no_findings(self, tmp_path):
        """Adopting the spine is optional per project; no keys set -> no
        findings at all (not even an S3 'missing' complaint)."""
        cfg = _write_project(tmp_path, "", {})
        assert lint.lint_spine(cfg) == {}

    def test_configured_but_missing_file_fails_s3(self, tmp_path):
        cfg = _write_project(
            tmp_path, 'product_definition = "nyxloom-trove/2-product-definition.md"\n', {}
        )
        findings = lint.lint_spine(cfg)["nyxloom-trove/nyxloom.toml"]
        s3 = [f for f in findings if f.rule == "S3"]
        assert s3, findings
        assert all(f.severity == "error" for f in s3)

    def test_wrong_numeric_prefix_fails_s3(self, tmp_path):
        cfg = _write_project(
            tmp_path,
            'product_definition = "nyxloom-trove/product-definition.md"\n',
            {"nyxloom-trove/product-definition.md": VALID_PRODUCT_DEF},
        )
        findings = lint.lint_spine(cfg)["nyxloom-trove/nyxloom.toml"]
        s3 = [f for f in findings if f.rule == "S3"]
        assert s3, findings
        assert "numeric-prefix" in s3[0].message

    def test_outside_trove_fails_s3(self, tmp_path):
        cfg = _write_project(
            tmp_path,
            'backlog = "other-dir/4-backlog.md"\n',
            {"other-dir/4-backlog.md": VALID_BACKLOG},
        )
        findings = lint.lint_spine(cfg)["nyxloom-trove/nyxloom.toml"]
        s3 = [f for f in findings if f.rule == "S3"]
        assert any("trove" in f.message for f in s3)

    def test_s3_findings_do_not_block_schema_check(self, tmp_path):
        """A naming/placement violation is reported ALONGSIDE (not instead
        of) the doc's own S1 schema validation."""
        cfg = _write_project(
            tmp_path,
            'product_definition = "nyxloom-trove/product-definition.md"\n',
            {"nyxloom-trove/product-definition.md": VALID_PRODUCT_DEF},
        )
        results = lint.lint_spine(cfg)
        assert _findings_for(results, "nyxloom-trove/product-definition.md") == []
        assert any(f.rule == "S3" for f in results["nyxloom-trove/nyxloom.toml"])


# ---------------------------------------------------------------------------
# S4: fail-closed on tamper/corruption

class TestS4FailClosed:
    def test_unparsable_frontmatter_is_hard_error(self, tmp_path):
        cfg = _write_project(
            tmp_path,
            'north_star = "nyxloom-trove/1-north-star.md"\n',
            {"nyxloom-trove/1-north-star.md": "no frontmatter here at all\n"},
        )
        findings = lint.lint_spine(cfg)["nyxloom-trove/1-north-star.md"]
        assert findings, "a present-but-corrupt spine doc must not be a silent skip"
        assert all(f.rule == "S4" and f.severity == "error" for f in findings)

    def test_unterminated_frontmatter_is_hard_error(self, tmp_path):
        cfg = _write_project(
            tmp_path,
            'north_star = "nyxloom-trove/1-north-star.md"\n',
            {"nyxloom-trove/1-north-star.md": "---\nkind: north-star\nschema_version: 1\n"},
        )
        findings = lint.lint_spine(cfg)["nyxloom-trove/1-north-star.md"]
        assert any(f.rule == "S4" for f in findings)

    def test_unknown_schema_version_is_hard_error_not_silent_skip(self, tmp_path):
        bad = VALID_NORTH_STAR.replace("schema_version: 1", "schema_version: 99")
        cfg = _write_project(
            tmp_path,
            'north_star = "nyxloom-trove/1-north-star.md"\n',
            {"nyxloom-trove/1-north-star.md": bad},
        )
        findings = lint.lint_spine(cfg)["nyxloom-trove/1-north-star.md"]
        assert findings
        assert all(f.rule == "S4" and f.severity == "error" for f in findings)
        assert "schema_version" in findings[0].message

    def test_missing_schema_version_is_hard_error(self, tmp_path):
        bad = VALID_NORTH_STAR.replace("    schema_version: 1\n", "")
        cfg = _write_project(
            tmp_path,
            'north_star = "nyxloom-trove/1-north-star.md"\n',
            {"nyxloom-trove/1-north-star.md": bad},
        )
        findings = lint.lint_spine(cfg)["nyxloom-trove/1-north-star.md"]
        assert any(f.rule == "S4" for f in findings)


# ---------------------------------------------------------------------------
# S5: uniqueness of ids within a single doc's own collection

class TestS5UniqueIds:
    def test_duplicate_backlog_id_fails_s5(self, tmp_path):
        cfg = _write_project(
            tmp_path,
            'backlog = "nyxloom-trove/4-backlog.md"\n',
            {"nyxloom-trove/4-backlog.md": DUPLICATE_ID_BACKLOG},
        )
        findings = lint.lint_spine(cfg)["nyxloom-trove/4-backlog.md"]
        s5 = [f for f in findings if f.rule == "S5"]
        assert s5, findings
        assert all(f.severity == "error" for f in s5)
        assert "B1" in s5[0].message

    def test_duplicate_product_definition_id_fails_s5(self, tmp_path):
        cfg = _write_project(
            tmp_path,
            'product_definition = "nyxloom-trove/2-product-definition.md"\n',
            {"nyxloom-trove/2-product-definition.md": DUPLICATE_ID_PRODUCT_DEF},
        )
        findings = lint.lint_spine(cfg)["nyxloom-trove/2-product-definition.md"]
        s5 = [f for f in findings if f.rule == "S5"]
        assert s5, findings
        assert all(f.severity == "error" for f in s5)
        assert "F001" in s5[0].message

    def test_duplicate_roadmap_milestone_id_fails_s5(self, tmp_path):
        cfg = _write_project(
            tmp_path,
            'roadmap = "nyxloom-trove/3-roadmap.md"\n',
            {"nyxloom-trove/3-roadmap.md": DUPLICATE_ID_ROADMAP},
        )
        findings = lint.lint_spine(cfg)["nyxloom-trove/3-roadmap.md"]
        s5 = [f for f in findings if f.rule == "S5"]
        assert s5, findings
        assert all(f.severity == "error" for f in s5)
        assert "M1" in s5[0].message

    def test_unique_ids_pass_s5(self, tmp_path):
        """A backlog whose items all have distinct ids (no folds_into, so S2
        can't fire either) produces no S5 finding -- proves S5 doesn't just
        fire unconditionally."""
        unique_backlog = DUPLICATE_ID_BACKLOG.replace(
            '      - id: B1\n        title: "a duplicate-id item"\n        type: bugfix\n',
            '      - id: B2\n        title: "a second item"\n        type: bugfix\n',
        )
        cfg = _write_project(
            tmp_path,
            'backlog = "nyxloom-trove/4-backlog.md"\n',
            {"nyxloom-trove/4-backlog.md": unique_backlog},
        )
        results = lint.lint_spine(cfg)
        assert _findings_for(results, "nyxloom-trove/4-backlog.md") == []

    def test_duplicate_id_excludes_doc_from_s2_pool(self, tmp_path):
        """A product-definition with a duplicate feature id is NOT treated
        as S1/S4/S5-clean for the S2 cross-doc pass: a roadmap milestone
        referencing that (duplicated, therefore untrustworthy) feature id
        must still fail S2 -- the duplicate doesn't get to silently donate
        its id to the cross-doc feature pool."""
        cfg = _write_project(
            tmp_path,
            'product_definition = "nyxloom-trove/2-product-definition.md"\n'
            'roadmap = "nyxloom-trove/3-roadmap.md"\n',
            {
                "nyxloom-trove/2-product-definition.md": DUPLICATE_ID_PRODUCT_DEF,
                "nyxloom-trove/3-roadmap.md": VALID_ROADMAP,  # references F001
            },
        )
        results = lint.lint_spine(cfg)
        pd_findings = results["nyxloom-trove/2-product-definition.md"]
        assert any(f.rule == "S5" for f in pd_findings)
        roadmap_findings = results["nyxloom-trove/3-roadmap.md"]
        s2 = [f for f in roadmap_findings if f.rule == "S2"]
        assert s2, roadmap_findings
        assert "F001" in s2[0].message


# ---------------------------------------------------------------------------
# lint_project wiring (doctor surfaces this transparently -- doctor.py
# iterates lint.lint_project(cfg) rule-agnostically, so no doctor.py change
# is needed; this pins that lint_project actually carries the findings).

class TestLintProjectWiring:
    def test_spine_findings_appear_in_lint_project(self, tmp_path):
        bad = VALID_PRODUCT_DEF.replace('acceptance: ["it does the thing"]', "acceptance: []")
        cfg = _write_project(
            tmp_path,
            'product_definition = "nyxloom-trove/2-product-definition.md"\n',
            {"nyxloom-trove/2-product-definition.md": bad},
        )
        results = lint.lint_project(cfg)
        assert any(
            f.rule == "S1"
            for f in results.get("nyxloom-trove/2-product-definition.md", [])
        )

    def test_spine_config_findings_merge_with_cfg1_under_same_key(self, tmp_path):
        """An S3 finding (keyed to nyxloom.toml) must be MERGED with, not
        clobber, the CFG1 findings lint_config already put under that key."""
        cfg = _write_project(
            tmp_path,
            'product_definition = "nyxloom-trove/2-product-definition.md"\n',
            {},  # configured but missing -> S3
        )
        results = lint.lint_project(cfg)
        toml_findings = results["nyxloom-trove/nyxloom.toml"]
        assert any(f.rule == "S3" for f in toml_findings)
        # CFG1/CFG2/CFG3 machinery still ran (lint_config's own entry survives).
        assert lint.lint_config(cfg) == [
            f for f in toml_findings if f.rule.startswith("CFG")
        ]

    def test_has_blocking_true_for_spine_error(self, tmp_path):
        cfg = _write_project(
            tmp_path,
            'north_star = "nyxloom-trove/1-north-star.md"\n',
            {"nyxloom-trove/1-north-star.md": "broken\n"},
        )
        findings = lint.lint_project(cfg)["nyxloom-trove/1-north-star.md"]
        assert lint.has_blocking(findings)


# ---------------------------------------------------------------------------
# nyxloom's own migrated trove (dogfooding): S1-S4 clean end to end.

class TestRepoOwnSpine:
    def test_repo_own_config_declares_all_four_spine_keys(self):
        cfg = config.ProjectConfig.load(REPO_ROOT)
        assert cfg.north_star == "nyxloom-trove/1-north-star.md"
        assert cfg.product_definition == "nyxloom-trove/2-product-definition.md"
        assert cfg.roadmap == "nyxloom-trove/3-roadmap.md"
        assert cfg.backlog == "nyxloom-trove/4-backlog.md"

    def test_repo_own_spine_lints_clean(self):
        cfg = config.ProjectConfig.load(REPO_ROOT)
        results = lint.lint_spine(cfg)
        errors = [f for fs in results.values() for f in fs if f.severity == "error"]
        assert errors == []

    def test_repo_own_spine_docs_exist_on_disk(self):
        for name in ("1-north-star.md", "2-product-definition.md",
                     "3-roadmap.md", "4-backlog.md"):
            assert (REPO_ROOT / "nyxloom-trove" / name).is_file(), name
