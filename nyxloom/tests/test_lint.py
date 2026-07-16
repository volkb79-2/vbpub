"""Tests for lint rules L1-L12."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from nyxloom import frontmatter, lint


class TestL1SchemaAndResolution:
    """Test L1: schema-valid frontmatter, id matches filename, project matches, deps resolve."""

    def test_good_sample_no_l1_error(self, sample_project):
        """Test that good sample has no L1 errors."""
        path = sample_project.root / "handoff" / "demo-P01-sample.md"
        findings = lint.lint_file(path, sample_project)
        l1_errors = [f for f in findings if f.rule == "L1" and f.severity == "error"]
        assert l1_errors == []

    def test_id_mismatch(self, sample_project, tmp_path):
        """Test L1 error for id mismatch."""
        content = textwrap.dedent("""\
            ---
            schema_version: 1
            id: demo-P01-wrong
            project: demo
            title: Test
            tier: flash-high
            input_revision: "0000000"
            source: {kind: review}
            scope: {touch: ["src/test.py"]}
            oracles:
              - id: O1
                observable: "pass"
                negative: "fail"
                gate: pytest-q
            gates: [pytest-q]
            escalate_if: ["trigger"]
            ---

            Body.
            """)
        path = tmp_path / "demo-P01-correct.md"
        path.write_text(content)

        findings = lint.lint_file(path, sample_project)
        l1_errors = [f for f in findings if f.rule == "L1" and "id" in f.message]
        assert len(l1_errors) > 0

    def test_project_mismatch(self, sample_project, tmp_path):
        """Test L1 error for project mismatch."""
        content = textwrap.dedent("""\
            ---
            schema_version: 1
            id: demo-P01-test
            project: other
            title: Test
            tier: flash-high
            input_revision: "0000000"
            source: {kind: review}
            scope: {touch: ["src/test.py"]}
            oracles:
              - id: O1
                observable: "pass"
                negative: "fail"
                gate: pytest-q
            gates: [pytest-q]
            escalate_if: ["trigger"]
            ---

            Body.
            """)
        path = tmp_path / "demo-P01-test.md"
        path.write_text(content)

        findings = lint.lint_file(path, sample_project)
        l1_errors = [f for f in findings if f.rule == "L1" and "project" in f.message]
        assert len(l1_errors) > 0

    def test_unresolvable_dependency(self, sample_project, tmp_path):
        """Test L1 error for unresolvable dependency."""
        content = textwrap.dedent("""\
            ---
            schema_version: 1
            id: demo-P01-test
            project: demo
            title: Test
            tier: flash-high
            input_revision: "0000000"
            source: {kind: review}
            scope: {touch: ["src/test.py"]}
            depends_on: [demo-P99-ghost]
            oracles:
              - id: O1
                observable: "pass"
                negative: "fail"
                gate: pytest-q
            gates: [pytest-q]
            escalate_if: ["trigger"]
            ---

            Body.
            """)
        path = tmp_path / "demo-P01-test.md"
        path.write_text(content)

        findings = lint.lint_file(path, sample_project)
        l1_errors = [f for f in findings if f.rule == "L1" and "resolve" in f.message]
        assert len(l1_errors) > 0

    def test_parse_error_is_l1(self, tmp_path, sample_project):
        """Test that parse error becomes L1 error."""
        content = "invalid content\n"
        path = tmp_path / "demo-P01-test.md"
        path.write_text(content)

        findings = lint.lint_file(path, sample_project)
        assert len(findings) == 1
        assert findings[0].rule == "L1"
        assert findings[0].severity == "error"


class TestL2GatesAndBareTests:
    """Test L2: gate ids exist, no bare pytest."""

    def test_unknown_gate(self, sample_project, tmp_path):
        """Test L2 error for unknown gate."""
        content = textwrap.dedent("""\
            ---
            schema_version: 1
            id: demo-P01-test
            project: demo
            title: Test
            tier: flash-high
            input_revision: "0000000"
            source: {kind: review}
            scope: {touch: ["src/test.py"]}
            oracles:
              - id: O1
                observable: "pass"
                negative: "fail"
                gate: no-such-gate
            gates: [no-such-gate]
            escalate_if: ["trigger"]
            ---

            Body.
            """)
        path = tmp_path / "demo-P01-test.md"
        path.write_text(content)

        findings = lint.lint_file(path, sample_project)
        l2_errors = [f for f in findings if f.rule == "L2"]
        assert len(l2_errors) > 0

    def test_bare_pytest_without_gate(self, sample_project, tmp_path):
        """Test L2 error for bare pytest block."""
        content = textwrap.dedent("""\
            ---
            schema_version: 1
            id: demo-P01-test
            project: demo
            title: Test
            tier: flash-high
            input_revision: "0000000"
            source: {kind: review}
            scope: {touch: ["src/test.py"]}
            oracles:
              - id: O1
                observable: "pass"
                negative: "fail"
                gate: pytest-q
            gates: [pytest-q]
            escalate_if: ["trigger"]
            ---

            Body.

            ```
            pytest tests/ -q
            ```
            """)
        path = tmp_path / "demo-P01-test.md"
        path.write_text(content)

        findings = lint.lint_file(path, sample_project)
        l2_errors = [f for f in findings if f.rule == "L2" and "bare pytest" in f.message]
        assert len(l2_errors) > 0


class TestL3Oracles:
    """Test L3: non-trivial oracle negatives."""

    def test_trivial_negative_none(self, sample_project, tmp_path):
        """Test L3 error for trivial negative 'none'."""
        content = textwrap.dedent("""\
            ---
            schema_version: 1
            id: demo-P01-test
            project: demo
            title: Test
            tier: flash-high
            input_revision: "0000000"
            source: {kind: review}
            scope: {touch: ["src/test.py"]}
            oracles:
              - id: O1
                observable: "test passes"
                negative: "none"
                gate: pytest-q
            gates: [pytest-q]
            escalate_if: ["trigger"]
            ---

            Body with BLOCKED: marker.
            worktree branch out of scope read first context to read
            """)
        path = tmp_path / "demo-P01-test.md"
        path.write_text(content)

        findings = lint.lint_file(path, sample_project)
        l3_errors = [f for f in findings if f.rule == "L3"]
        assert len(l3_errors) > 0

    def test_trivial_negative_na(self, sample_project, tmp_path):
        """Test L3 error for trivial negative 'n/a'."""
        content = textwrap.dedent("""\
            ---
            schema_version: 1
            id: demo-P01-test
            project: demo
            title: Test
            tier: flash-high
            input_revision: "0000000"
            source: {kind: review}
            scope: {touch: ["src/test.py"]}
            oracles:
              - id: O1
                observable: "test passes"
                negative: "n/a"
                gate: pytest-q
            gates: [pytest-q]
            escalate_if: ["trigger"]
            ---

            Body with BLOCKED: marker.
            worktree branch out of scope read first context to read
            """)
        path = tmp_path / "demo-P01-test.md"
        path.write_text(content)

        findings = lint.lint_file(path, sample_project)
        l3_errors = [f for f in findings if f.rule == "L3"]
        assert len(l3_errors) > 0


class TestL4UniversalContract:
    """Test L4: no enumerated oracle under universal contract."""

    def test_enumerated_under_universal(self, sample_project, tmp_path):
        """Test L4 warning for enumerated oracle with universal contract."""
        content = textwrap.dedent("""\
            ---
            schema_version: 1
            id: demo-P01-test
            project: demo
            title: Test
            tier: flash-high
            input_revision: "0000000"
            source: {kind: review}
            scope: {touch: ["src/test.py"]}
            oracles:
              - id: O1
                observable: "every audit record field matches: `outcome`, `stderr`"
                negative: "fail"
                gate: pytest-q
            gates: [pytest-q]
            escalate_if: ["trigger"]
            ---

            Body with BLOCKED: marker.
            worktree branch out of scope read first context to read
            """)
        path = tmp_path / "demo-P01-test.md"
        path.write_text(content)

        findings = lint.lint_file(path, sample_project)
        l4_errors = [f for f in findings if f.rule == "L4"]
        assert len(l4_errors) > 0


class TestL5ReviewerDeliverables:
    """Test L5: no reviewer-only deliverables."""

    def test_decisions_inbox_in_body(self, sample_project, tmp_path):
        """Test L5 error for DECISIONS-INBOX."""
        content = textwrap.dedent("""\
            ---
            schema_version: 1
            id: demo-P01-test
            project: demo
            title: Test
            tier: flash-high
            input_revision: "0000000"
            source: {kind: review}
            scope: {touch: ["src/test.py"]}
            oracles:
              - id: O1
                observable: "pass"
                negative: "fail"
                gate: pytest-q
            gates: [pytest-q]
            escalate_if: ["trigger"]
            ---

            Body with BLOCKED: marker.
            Update DECISIONS-INBOX.md with the results.
            worktree branch out of scope read first context to read
            """)
        path = tmp_path / "demo-P01-test.md"
        path.write_text(content)

        findings = lint.lint_file(path, sample_project)
        l5_errors = [f for f in findings if f.rule == "L5"]
        assert len(l5_errors) > 0

    def test_decisions_inbox_negated_is_ok(self, sample_project, tmp_path):
        """Test L5 allows DECISIONS-INBOX in negated context."""
        content = textwrap.dedent("""\
            ---
            schema_version: 1
            id: demo-P01-test
            project: demo
            title: Test
            tier: flash-high
            input_revision: "0000000"
            source: {kind: review}
            scope: {touch: ["src/test.py"]}
            oracles:
              - id: O1
                observable: "pass"
                negative: "fail"
                gate: pytest-q
            gates: [pytest-q]
            escalate_if: ["trigger"]
            ---

            Body with BLOCKED: marker.
            Do not update DECISIONS-INBOX.md.
            worktree branch out of scope read first context to read
            """)
        path = tmp_path / "demo-P01-test.md"
        path.write_text(content)

        findings = lint.lint_file(path, sample_project)
        l5_errors = [f for f in findings if f.rule == "L5" and "DECISIONS-INBOX" in f.message]
        # Should be OK because it's negated
        assert len(l5_errors) == 0


class TestL6OracleDeferal:
    """Test L6: no oracle deferral."""

    def test_reviewer_will_validate(self, sample_project, tmp_path):
        """Test L6 error for deferred oracle."""
        content = textwrap.dedent("""\
            ---
            schema_version: 1
            id: demo-P01-test
            project: demo
            title: Test
            tier: flash-high
            input_revision: "0000000"
            source: {kind: review}
            scope: {touch: ["src/test.py"]}
            oracles:
              - id: O1
                observable: "the reviewer will validate the venv build"
                negative: "fail"
                gate: pytest-q
            gates: [pytest-q]
            escalate_if: ["trigger"]
            ---

            Body with BLOCKED: marker.
            worktree branch out of scope read first context to read
            """)
        path = tmp_path / "demo-P01-test.md"
        path.write_text(content)

        findings = lint.lint_file(path, sample_project)
        l6_errors = [f for f in findings if f.rule == "L6"]
        assert len(l6_errors) > 0


class TestL7Paths:
    """Test L7: paths resolve."""

    def test_nonexistent_source_ref(self, sample_project, tmp_path):
        """Test L7 error for non-existent source.ref."""
        content = textwrap.dedent("""\
            ---
            schema_version: 1
            id: demo-P01-test
            project: demo
            title: Test
            tier: flash-high
            input_revision: "0000000"
            source: {kind: review, ref: "docs/nonexistent.md"}
            scope: {touch: ["src/test.py"]}
            oracles:
              - id: O1
                observable: "pass"
                negative: "fail"
                gate: pytest-q
            gates: [pytest-q]
            escalate_if: ["trigger"]
            ---

            Body with BLOCKED: marker.
            worktree branch out of scope read first context to read
            """)
        path = tmp_path / "demo-P01-test.md"
        path.write_text(content)

        findings = lint.lint_file(path, sample_project)
        l7_errors = [f for f in findings if f.rule == "L7"]
        assert len(l7_errors) > 0

    def test_relative_up_path_error(self, sample_project, tmp_path):
        """Test L7 error for relative-up path."""
        content = textwrap.dedent("""\
            ---
            schema_version: 1
            id: demo-P01-test
            project: demo
            title: Test
            tier: flash-high
            input_revision: "0000000"
            source: {kind: review, ref: "../dstdns/docs/spec.md"}
            scope: {touch: ["src/test.py"]}
            oracles:
              - id: O1
                observable: "pass"
                negative: "fail"
                gate: pytest-q
            gates: [pytest-q]
            escalate_if: ["trigger"]
            ---

            Body with BLOCKED: marker.
            worktree branch out of scope read first context to read
            """)
        path = tmp_path / "demo-P01-test.md"
        path.write_text(content)

        findings = lint.lint_file(path, sample_project)
        l7_errors = [f for f in findings if f.rule == "L7" and "non-resolving" in f.message]
        assert len(l7_errors) > 0


class TestL8EscalateIf:
    """Test L8: escalate_if triggers are mechanical."""

    def test_introspective_escalate(self, sample_project, tmp_path):
        """Test L8 error for introspective escalation trigger."""
        content = textwrap.dedent("""\
            ---
            schema_version: 1
            id: demo-P01-test
            project: demo
            title: Test
            tier: flash-high
            input_revision: "0000000"
            source: {kind: review}
            scope: {touch: ["src/test.py"]}
            oracles:
              - id: O1
                observable: "pass"
                negative: "fail"
                gate: pytest-q
            gates: [pytest-q]
            escalate_if: ["reflect whether this suits your expertise"]
            ---

            Body with BLOCKED: marker.
            worktree branch out of scope read first context to read
            """)
        path = tmp_path / "demo-P01-test.md"
        path.write_text(content)

        findings = lint.lint_file(path, sample_project)
        l8_errors = [f for f in findings if f.rule == "L8"]
        assert len(l8_errors) > 0


class TestL9InfraMutex:
    """Test L9: infra touches require stack mutex."""

    def test_infra_without_stack_mutex(self, sample_project, tmp_path):
        """Test L9 error for infra touch without stack mutex."""
        content = textwrap.dedent("""\
            ---
            schema_version: 1
            id: demo-P01-test
            project: demo
            title: Test
            tier: flash-high
            input_revision: "0000000"
            source: {kind: review}
            scope: {touch: ["infra/deploy.yml"]}
            oracles:
              - id: O1
                observable: "pass"
                negative: "fail"
                gate: pytest-q
            gates: [pytest-q]
            escalate_if: ["trigger"]
            ---

            Body with BLOCKED: marker.
            worktree branch out of scope read first context to read
            """)
        path = tmp_path / "demo-P01-test.md"
        path.write_text(content)

        findings = lint.lint_file(path, sample_project)
        l9_errors = [f for f in findings if f.rule == "L9"]
        assert len(l9_errors) > 0


class TestL10Size:
    """Test L10: size limits."""

    def test_large_handoff_warning(self, sample_project, tmp_path):
        """Test L10 warning for handoff over 6k tokens."""
        large_body = "x" * 25000  # 6250 tokens
        content = f"""---
schema_version: 1
id: demo-P01-test
project: demo
title: Test
tier: flash-high
input_revision: "0000000"
source: {{kind: review}}
scope: {{touch: ["src/test.py"]}}
oracles:
  - id: O1
    observable: "pass"
    negative: "fail"
    gate: pytest-q
gates: [pytest-q]
escalate_if: ["trigger"]
---

{large_body}
BLOCKED: marker.
worktree branch out of scope read first context to read
"""
        path = tmp_path / "demo-P01-test.md"
        path.write_text(content)

        findings = lint.lint_file(path, sample_project)
        l10_errors = [f for f in findings if f.rule == "L10"]
        assert any(f.severity == "warning" for f in l10_errors)

    def test_huge_handoff_error(self, sample_project, tmp_path):
        """Test L10 error for handoff over 12k tokens."""
        huge_body = "x" * 49000  # 12250 tokens
        content = f"""---
schema_version: 1
id: demo-P01-test
project: demo
title: Test
tier: flash-high
input_revision: "0000000"
source: {{kind: review}}
scope: {{touch: ["src/test.py"]}}
oracles:
  - id: O1
    observable: "pass"
    negative: "fail"
    gate: pytest-q
gates: [pytest-q]
escalate_if: ["trigger"]
---

{huge_body}
BLOCKED: marker.
worktree branch out of scope read first context to read
"""
        path = tmp_path / "demo-P01-test.md"
        path.write_text(content)

        findings = lint.lint_file(path, sample_project)
        l10_errors = [f for f in findings if f.rule == "L10"]
        assert any(f.severity == "error" for f in l10_errors)


class TestL11BodySections:
    """Test L11: body contains required sections."""

    def test_missing_sections(self, sample_project, tmp_path):
        """Test L11 error for missing body sections."""
        content = textwrap.dedent("""\
            ---
            schema_version: 1
            id: demo-P01-test
            project: demo
            title: Test
            tier: flash-high
            input_revision: "0000000"
            source: {kind: review}
            scope: {touch: ["src/test.py"]}
            oracles:
              - id: O1
                observable: "pass"
                negative: "fail"
                gate: pytest-q
            gates: [pytest-q]
            escalate_if: ["trigger"]
            ---

            Body with BLOCKED: marker.
            """)
        path = tmp_path / "demo-P01-test.md"
        path.write_text(content)

        findings = lint.lint_file(path, sample_project)
        l11_errors = [f for f in findings if f.rule == "L11"]
        assert len(l11_errors) > 0


class TestL12BlockedMarker:
    """Test L12: BLOCKED marker present, no policy violations."""

    def test_missing_blocked_marker(self, sample_project, tmp_path):
        """Test L12 error for missing BLOCKED marker."""
        content = textwrap.dedent("""\
            ---
            schema_version: 1
            id: demo-P01-test
            project: demo
            title: Test
            tier: flash-high
            input_revision: "0000000"
            source: {kind: review}
            scope: {touch: ["src/test.py"]}
            oracles:
              - id: O1
                observable: "pass"
                negative: "fail"
                gate: pytest-q
            gates: [pytest-q]
            escalate_if: ["trigger"]
            ---

            Body without blocked marker.
            worktree branch out of scope read first context to read
            """)
        path = tmp_path / "demo-P01-test.md"
        path.write_text(content)

        findings = lint.lint_file(path, sample_project)
        l12_errors = [f for f in findings if f.rule == "L12" and "BLOCKED:" in f.message]
        assert len(l12_errors) > 0

    def test_skip_the_gate_violation(self, sample_project, tmp_path):
        """Test L12 error for policy violation."""
        content = textwrap.dedent("""\
            ---
            schema_version: 1
            id: demo-P01-test
            project: demo
            title: Test
            tier: flash-high
            input_revision: "0000000"
            source: {kind: review}
            scope: {touch: ["src/test.py"]}
            oracles:
              - id: O1
                observable: "pass"
                negative: "fail"
                gate: pytest-q
            gates: [pytest-q]
            escalate_if: ["trigger"]
            ---

            Body with BLOCKED: marker.
            Skip the gate if tests pass.
            worktree branch out of scope read first context to read
            """)
        path = tmp_path / "demo-P01-test.md"
        path.write_text(content)

        findings = lint.lint_file(path, sample_project)
        l12_errors = [f for f in findings if f.rule == "L12" and "policy" in f.message]
        assert len(l12_errors) > 0


class TestGoldenCorpus:
    """Test golden corpus fixtures against expected rules."""

    @pytest.mark.parametrize(
        "fixture_name,expected_rule,is_error",
        [
            ("demo-P01-sample.md", None, False),
            ("demo-P10-schema.md", "L1", True),
            ("demo-P11-dangling.md", "L1", True),
            ("demo-P12-bare.md", "L2", True),
            ("demo-P13-unknown.md", "L2", True),
            ("demo-P14-trivial.md", "L3", True),
            ("demo-P15-enum.md", "L4", False),  # L4 is warning
            ("demo-P16-review.md", "L5", True),
            ("demo-P17-deferred.md", "L6", True),
            ("demo-P18-path.md", "L7", True),
            ("demo-P19-intro.md", "L8", True),
            ("demo-P20-infra.md", "L9", True),
            ("demo-P21-huge.md", "L10", False),  # L10 warning becomes error over 12k
            ("demo-P22-missing.md", "L11", True),
            ("demo-P23-blocked.md", "L12", True),
        ],
    )
    def test_golden_corpus(self, fixture_name, expected_rule, is_error, sample_project):
        """Test that fixtures trigger expected rules."""
        from pathlib import Path
        fixtures_dir = Path(__file__).parent / "fixtures" / "handoffs"
        fixture_path = fixtures_dir / fixture_name

        if not fixture_path.exists():
            pytest.skip(f"Fixture {fixture_name} not found")

        findings = lint.lint_file(fixture_path, sample_project)

        if expected_rule is None:
            # good-sample should have no error-level findings
            error_findings = [f for f in findings if f.severity == "error"]
            assert len(error_findings) == 0, f"Expected no errors, got: {error_findings}"
        else:
            # Expected rule should fire
            expected_findings = [f for f in findings if f.rule == expected_rule]
            assert len(expected_findings) > 0, f"Expected {expected_rule} to fire, findings: {findings}"

            # Check blocking status
            if is_error:
                assert lint.has_blocking(findings), f"Expected L{expected_rule} to be blocking"

            # No OTHER error-level rules should fire
            other_errors = [
                f for f in findings
                if f.severity == "error" and f.rule != expected_rule
            ]
            assert len(other_errors) == 0, f"Unexpected errors in {fixture_name}: {other_errors}"
