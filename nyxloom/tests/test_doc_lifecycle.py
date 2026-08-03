"""doc_lifecycle.py: archive containment + lifecycle frontmatter (CR-01, DR-04).

Anti-pattern notes (nyxloom-trove/STANDING.md / reference/AUTHORING.md §3b):
no wall-clock waits, no process-global mutation, every negative case actually
violates the contract it claims to guard, no coverage evasion.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from nyxloom import doc_lifecycle
from nyxloom.config import ProjectConfig


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "nyxloom-trove").mkdir(parents=True)
    (root / "nyxloom-trove" / "nyxloom.toml").write_text(textwrap.dedent("""\
        [project]
        id = "demo"
        default_branch = "main"
        handoff_globs = ["nyxloom-trove/handoffs/*.md"]

        [gates.pytest-q]
        argv = ["true"]
        phase = "implementation"
        timeout_seconds = 60
        """))
    return root


SUPERSEDED_FM = """\
---
lifecycle: archived
status: superseded
archived_date: "2026-08-03"
superseded_by: nyxloom-trove/3-roadmap.md
reason: test fixture
---

# archived
"""

HISTORICAL_FM = """\
---
lifecycle: archived
status: historical
archived_date: "2026-08-03"
reason: test fixture
---

# archived
"""


class TestIsArchived:
    """Pure path-containment (no content read) -- this is what daemon.py's
    context-collection surface relies on to exclude archived docs without
    ever having to open them."""

    def test_path_under_archive_is_archived(self, tmp_path):
        root = _project(tmp_path)
        target = root / "docs" / "archive" / "product-docs" / "X.md"
        target.parent.mkdir(parents=True)
        target.write_text("x")
        assert doc_lifecycle.is_archived(root, target) is True

    def test_path_outside_archive_is_not_archived(self, tmp_path):
        root = _project(tmp_path)
        target = root / "docs" / "SPEC.md"
        target.parent.mkdir(parents=True)
        target.write_text("x")
        assert doc_lifecycle.is_archived(root, target) is False

    def test_nonexistent_path_under_archive_is_still_archived(self, tmp_path):
        """Containment is lexical/resolve-based, not existence-based --
        fail-closed even for a path that doesn't (yet) exist on disk."""
        root = _project(tmp_path)
        target = root / "docs" / "archive" / "product-docs" / "NEVER-WRITTEN.md"
        assert doc_lifecycle.is_archived(root, target) is True

    def test_dotdot_normalization_into_archive_is_caught(self, tmp_path):
        root = _project(tmp_path)
        (root / "docs" / "archive" / "product-docs").mkdir(parents=True)
        target = root / "docs" / ".." / "docs" / "archive" / "product-docs" / "X.md"
        assert doc_lifecycle.is_archived(root, target) is True

    def test_symlink_escape_into_archive_is_caught(self, tmp_path):
        root = _project(tmp_path)
        archived_dir = root / "docs" / "archive" / "product-docs"
        archived_dir.mkdir(parents=True)
        real = archived_dir / "X.md"
        real.write_text("x")
        link = root / "docs" / "LOOKS-NORMAL.md"
        link.symlink_to(real)
        assert doc_lifecycle.is_archived(root, link) is True

    def test_relative_root_is_resolved_before_comparison(self, tmp_path, monkeypatch):
        root = _project(tmp_path)
        target = root / "docs" / "archive" / "product-docs" / "X.md"
        target.parent.mkdir(parents=True)
        target.write_text("x")
        monkeypatch.chdir(tmp_path)
        rel_root = Path("proj")
        assert doc_lifecycle.is_archived(rel_root, target) is True


class TestDescribe:
    """Best-effort message enrichment -- must never raise, and must never be
    the thing an exclusion decision depends on (is_archived owns that)."""

    def test_superseded_reason(self, tmp_path):
        root = _project(tmp_path)
        p = root / "docs" / "archive" / "product-docs" / "X.md"
        p.parent.mkdir(parents=True)
        p.write_text(SUPERSEDED_FM)
        assert doc_lifecycle.describe(p) == "superseded by nyxloom-trove/3-roadmap.md"

    def test_historical_reason(self, tmp_path):
        root = _project(tmp_path)
        p = root / "docs" / "archive" / "product-docs" / "X.md"
        p.parent.mkdir(parents=True)
        p.write_text(HISTORICAL_FM)
        assert doc_lifecycle.describe(p) == "historical (no current successor)"

    def test_unreadable_metadata_reason_is_generic_not_a_crash(self, tmp_path):
        root = _project(tmp_path)
        p = root / "docs" / "archive" / "product-docs" / "X.md"
        p.parent.mkdir(parents=True)
        p.write_text("not frontmatter\n")
        assert doc_lifecycle.describe(p) == "archived document (lifecycle metadata unreadable)"

    def test_missing_file_reason_is_generic_not_a_crash(self, tmp_path):
        root = _project(tmp_path)
        p = root / "docs" / "archive" / "product-docs" / "GONE.md"
        assert doc_lifecycle.describe(p) == "archived document (lifecycle metadata unreadable)"


class TestValidateLifecycleFrontmatter:
    def test_valid_superseded_has_no_errors(self):
        fm = {
            "lifecycle": "archived", "status": "superseded",
            "archived_date": "2026-08-03", "superseded_by": "x.md", "reason": "r",
        }
        assert doc_lifecycle.validate_lifecycle_frontmatter(fm) == []

    def test_valid_historical_has_no_errors(self):
        fm = {
            "lifecycle": "archived", "status": "historical",
            "archived_date": "2026-08-03", "reason": "r",
        }
        assert doc_lifecycle.validate_lifecycle_frontmatter(fm) == []

    def test_superseded_without_successor_is_an_error(self):
        """A superseded doc MUST name its successor -- 'superseded_by only
        when a named successor truly exists' (CR-01 contract item 1)."""
        fm = {
            "lifecycle": "archived", "status": "superseded",
            "archived_date": "2026-08-03", "reason": "r",
        }
        errors = doc_lifecycle.validate_lifecycle_frontmatter(fm)
        assert errors
        assert any("superseded_by" in e for e in errors)

    def test_historical_with_fake_successor_is_an_error(self):
        """'historical has no fake successor' (CR-01 contract) -- a
        historical doc carrying superseded_by is rejected, not silently
        accepted."""
        fm = {
            "lifecycle": "archived", "status": "historical",
            "archived_date": "2026-08-03", "superseded_by": "x.md", "reason": "r",
        }
        errors = doc_lifecycle.validate_lifecycle_frontmatter(fm)
        assert errors

    def test_missing_required_field_is_an_error(self):
        fm = {"lifecycle": "archived", "status": "historical", "archived_date": "2026-08-03"}
        errors = doc_lifecycle.validate_lifecycle_frontmatter(fm)
        assert any("reason" in e for e in errors)


class TestLintArchive:
    """ARC1: every doc under docs/archive/product-docs/ must carry valid
    lifecycle frontmatter -- fail-closed, mirroring S4."""

    def test_valid_archived_doc_has_no_findings(self, tmp_path):
        root = _project(tmp_path)
        p = root / "docs" / "archive" / "product-docs" / "OK.md"
        p.parent.mkdir(parents=True)
        p.write_text(SUPERSEDED_FM)
        cfg = ProjectConfig.load(root)
        assert doc_lifecycle.lint_archive(cfg) == {}

    def test_unparsable_archived_doc_is_arc1_error(self, tmp_path):
        root = _project(tmp_path)
        p = root / "docs" / "archive" / "product-docs" / "BAD.md"
        p.parent.mkdir(parents=True)
        p.write_text("no frontmatter at all\n")
        cfg = ProjectConfig.load(root)
        results = doc_lifecycle.lint_archive(cfg)
        assert "docs/archive/product-docs/BAD.md" in results
        findings = results["docs/archive/product-docs/BAD.md"]
        assert all(f.rule == "ARC1" and f.severity == "error" for f in findings)

    def test_schema_invalid_archived_doc_is_arc1_error(self, tmp_path):
        root = _project(tmp_path)
        p = root / "docs" / "archive" / "product-docs" / "BAD2.md"
        p.parent.mkdir(parents=True)
        # status=superseded but no superseded_by -- schema-invalid.
        p.write_text(textwrap.dedent("""\
            ---
            lifecycle: archived
            status: superseded
            archived_date: "2026-08-03"
            reason: test
            ---

            body
            """))
        cfg = ProjectConfig.load(root)
        results = doc_lifecycle.lint_archive(cfg)
        assert "docs/archive/product-docs/BAD2.md" in results
        assert all(f.rule == "ARC1" for f in results["docs/archive/product-docs/BAD2.md"])

    def test_docs_outside_product_docs_are_not_scanned(self, tmp_path):
        """docs/archive/sessions/ (pre-existing free-form archive content)
        carries no lifecycle contract and must not be swept into ARC1 --
        only docs/archive/product-docs/ is in scope."""
        root = _project(tmp_path)
        p = root / "docs" / "archive" / "sessions" / "log.md"
        p.parent.mkdir(parents=True)
        p.write_text("no frontmatter, never meant to have any\n")
        cfg = ProjectConfig.load(root)
        assert doc_lifecycle.lint_archive(cfg) == {}

    def test_no_archive_dir_yields_no_findings(self, tmp_path):
        root = _project(tmp_path)
        cfg = ProjectConfig.load(root)
        assert doc_lifecycle.lint_archive(cfg) == {}
