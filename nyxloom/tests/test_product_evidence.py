"""Per-criterion product-evidence existence-check (CR-12a).

Mirrors tests/test_product_truth.py's shape: a `Test*RealRepo` class proving
the real, populated subset of nyxloom-trove/2-product-definition.md
(F001/F003) resolves against THIS repo's actual tests, plus
`Test*Discriminates` classes proving evidence_resolves/load_criteria
actually catch drift on a synthetic tmp_path tree -- a check nobody has seen
fail is not known to check anything.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nyxloom import product_evidence

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# real-repo assertions

class TestRealRepoCriteria:
    """The structured (id/text/status/evidence) acceptance items in THIS
    repo's own product-definition doc, checked against THIS repo's own
    tests -- both sides read real, already-authoritative sources, never a
    second hardcoded literal."""

    def test_the_real_doc_has_structured_criteria_to_check(self):
        """A check with nothing to run against would pass vacuously --
        confirming the F001/F003 migration actually populated criteria is
        what makes every other assertion in this class meaningful."""
        criteria = product_evidence.load_criteria(REPO_ROOT)
        assert len(criteria) >= 6, (
            "expected the F001/F003 structured migration's criteria; "
            f"found {len(criteria)}"
        )

    def test_every_proven_criterion_has_at_least_one_evidence_citation(self):
        criteria = product_evidence.load_criteria(REPO_ROOT)
        proven = [c for c in criteria if c.status == "proven"]
        assert proven, "expected at least one status: proven criterion"
        for c in proven:
            assert c.evidence, f"{c.id}: status=proven but no evidence citations"

    @pytest.mark.parametrize(
        "criterion_id,citation",
        [
            (c.id, citation)
            for c in product_evidence.load_criteria(REPO_ROOT)
            for citation in c.evidence
        ],
    )
    def test_evidence_citation_resolves(self, criterion_id, citation):
        assert product_evidence.evidence_resolves(REPO_ROOT, citation), (
            f"{criterion_id}: evidence citation '{citation}' does not resolve to a "
            f"real test in this tree -- update the citation or the criterion's status"
        )

    def test_an_absent_status_criterion_carries_no_evidence_obligation(self):
        """F001-A3 is the deliberate negative control: a real criterion with
        status=absent, proving the vocabulary's second state is meaningful
        (not every migrated criterion is trivially 'proven')."""
        criteria = product_evidence.load_criteria(REPO_ROOT)
        absent = [c for c in criteria if c.status == "absent"]
        assert absent, "expected at least one status: absent criterion (F001-A3)"


# ---------------------------------------------------------------------------
# evidence_resolves -- discriminates on a synthetic tree


class TestEvidenceResolvesDiscriminates:

    def test_an_existing_module_level_test_resolves(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_x.py").write_text("def test_original():\n    pass\n")
        assert product_evidence.evidence_resolves(
            tmp_path, "tests/test_x.py::test_original") is True

    def test_a_renamed_test_no_longer_resolves(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_x.py").write_text("def test_renamed():\n    pass\n")
        assert product_evidence.evidence_resolves(
            tmp_path, "tests/test_x.py::test_original") is False

    def test_a_removed_file_no_longer_resolves(self, tmp_path):
        assert product_evidence.evidence_resolves(
            tmp_path, "tests/test_gone.py::test_x") is False

    def test_a_class_method_citation_resolves(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_x.py").write_text(
            "class TestFoo:\n    def test_bar(self):\n        pass\n")
        assert product_evidence.evidence_resolves(
            tmp_path, "tests/test_x.py::TestFoo::test_bar") is True

    def test_a_renamed_class_no_longer_resolves(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_x.py").write_text(
            "class TestRenamed:\n    def test_bar(self):\n        pass\n")
        assert product_evidence.evidence_resolves(
            tmp_path, "tests/test_x.py::TestFoo::test_bar") is False

    def test_a_method_moved_out_of_its_class_no_longer_resolves(self, tmp_path):
        """The citation names a NESTED path (Class::method) -- a same-named
        function that moved to module scope is a different symbol, not the
        one cited."""
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_x.py").write_text(
            "class TestFoo:\n    pass\n\n\ndef test_bar():\n    pass\n")
        assert product_evidence.evidence_resolves(
            tmp_path, "tests/test_x.py::TestFoo::test_bar") is False

    def test_a_syntax_error_file_does_not_crash(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_x.py").write_text("def broken(:\n")
        assert product_evidence.evidence_resolves(
            tmp_path, "tests/test_x.py::test_x") is False

    def test_a_bare_file_citation_with_no_symbol_resolves_if_the_file_exists(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_x.py").write_text("# empty\n")
        assert product_evidence.evidence_resolves(tmp_path, "tests/test_x.py") is True

    def test_a_parametrized_node_id_does_not_resolve(self, tmp_path):
        """A real limitation, named in the module docstring: this function
        walks AST definitions, so a citation carrying a parametrize `[...]`
        suffix (no matching AST node name) returns False rather than a
        false positive."""
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_x.py").write_text("def test_foo(x):\n    pass\n")
        assert product_evidence.evidence_resolves(
            tmp_path, "tests/test_x.py::test_foo[1]") is False


# ---------------------------------------------------------------------------
# load_criteria -- discriminates on a synthetic tree


_DOC_HEADER = (
    "---\nkind: product-definition\nschema_version: 1\nproduct_version: 1\n"
    "features:\n"
)


class TestLoadCriteriaDiscriminates:

    def test_plain_string_acceptance_items_are_skipped(self, tmp_path):
        doc = tmp_path / "nyxloom-trove" / "2-product-definition.md"
        doc.parent.mkdir(parents=True)
        doc.write_text(
            _DOC_HEADER
            + "- id: F001\n  title: X\n  acceptance: ['plain text']\n  status: shipped\n"
            + "---\n\nBody\n")
        assert product_evidence.load_criteria(tmp_path) == []

    def test_structured_acceptance_items_are_loaded(self, tmp_path):
        doc = tmp_path / "nyxloom-trove" / "2-product-definition.md"
        doc.parent.mkdir(parents=True)
        doc.write_text(
            _DOC_HEADER
            + "- id: F001\n  title: X\n  status: shipped\n"
              "  acceptance:\n"
              "  - id: F001-A1\n    text: 'a claim'\n    status: proven\n"
              "    evidence: ['tests/test_x.py::test_y']\n"
            + "---\n\nBody\n")
        criteria = product_evidence.load_criteria(tmp_path)
        assert len(criteria) == 1
        assert criteria[0].feature_id == "F001"
        assert criteria[0].id == "F001-A1"
        assert criteria[0].text == "a claim"
        assert criteria[0].status == "proven"
        assert criteria[0].evidence == ("tests/test_x.py::test_y",)

    def test_a_structured_item_with_no_evidence_key_yields_an_empty_tuple(self, tmp_path):
        doc = tmp_path / "nyxloom-trove" / "2-product-definition.md"
        doc.parent.mkdir(parents=True)
        doc.write_text(
            _DOC_HEADER
            + "- id: F001\n  title: X\n  status: shipped\n"
              "  acceptance:\n"
              "  - id: F001-A1\n    text: 'a claim'\n    status: absent\n"
            + "---\n\nBody\n")
        criteria = product_evidence.load_criteria(tmp_path)
        assert criteria[0].evidence == ()

    def test_a_missing_doc_yields_no_criteria_rather_than_raising(self, tmp_path):
        assert product_evidence.load_criteria(tmp_path) == []

    def test_a_non_dict_feature_entry_is_skipped_rather_than_raising(self, tmp_path):
        """Defensive against a malformed `features` list (already someone
        else's S1 finding, or an unschema'd hand-edit) -- a bare string in
        the list must not raise AttributeError on `.get`."""
        doc = tmp_path / "nyxloom-trove" / "2-product-definition.md"
        doc.parent.mkdir(parents=True)
        doc.write_text(
            _DOC_HEADER
            + "- not-a-mapping\n"
              "- id: F001\n  title: X\n  status: shipped\n"
              "  acceptance:\n"
              "  - id: F001-A1\n    text: 'a claim'\n    status: proven\n"
              "    evidence: ['tests/test_x.py::test_y']\n"
            + "---\n\nBody\n")
        criteria = product_evidence.load_criteria(tmp_path)
        assert len(criteria) == 1
        assert criteria[0].id == "F001-A1"

    def test_an_unparsable_doc_yields_no_criteria_rather_than_raising(self, tmp_path):
        doc = tmp_path / "nyxloom-trove" / "2-product-definition.md"
        doc.parent.mkdir(parents=True)
        doc.write_text("no frontmatter here at all\n")
        assert product_evidence.load_criteria(tmp_path) == []

    def test_a_custom_doc_relpath_is_honored(self, tmp_path):
        doc = tmp_path / "custom" / "product.md"
        doc.parent.mkdir(parents=True)
        doc.write_text(
            _DOC_HEADER
            + "- id: F001\n  title: X\n  status: shipped\n"
              "  acceptance:\n"
              "  - id: F001-A1\n    text: 'a claim'\n    status: proven\n"
              "    evidence: ['tests/test_x.py::test_y']\n"
            + "---\n\nBody\n")
        assert product_evidence.load_criteria(tmp_path, "custom/product.md") != []
        assert product_evidence.load_criteria(tmp_path) == []
