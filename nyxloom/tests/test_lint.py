"""Tests for lint rules L1-L12, plus config lint rules CFG1-CFG3 (P24)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from nyxloom import config, frontmatter, lint


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


# ---------------------------------------------------------------------------
# CFG1-CFG3: nyxloom.toml schema + semantic config lint (P24). Fixtures are
# built fresh under tmp_path (not the repo tree) mirroring the sections the
# repo's own nyxloom-trove/nyxloom.toml uses: [project], [refs], [gates.*],
# [policy], [notify], [mutexes.*].

VALID_CONFIG_TOML = """\
[project]
id = "demo"
default_branch = "main"
handoff_globs = ["nyxloom-trove/handoffs/*.md"]
worktree_root = "../.worktrees"

[refs]
spec = "docs/SPEC.md"

[gates.tester-unified]
argv = ["true"]
phase = "implementation"
timeout_seconds = 60

[policy]
max_active_tasks = 3

[notify]
ntfy_url = "https://example.invalid"

[mutexes.stack]
scope = "project"
capacity = 1
"""


def _write_config_project(tmp_path: Path, toml_text: str, *, ref_stubs: tuple[str, ...] = ("docs/SPEC.md",)):
    """A project root with nyxloom-trove/nyxloom.toml = toml_text, plus any
    files referenced by [refs] the caller wants to actually resolve."""
    root = tmp_path / "cfgproj"
    (root / "nyxloom-trove").mkdir(parents=True)
    (root / "nyxloom-trove" / "nyxloom.toml").write_text(toml_text)
    for rel in ref_stubs:
        stub = root / rel
        stub.parent.mkdir(parents=True, exist_ok=True)
        stub.write_text("stub\n")
    return root


class TestConfigLintSchema:
    """O1: schema violations -> blocking CFG1 finding; valid config -> none."""

    def test_valid_config_no_findings(self, tmp_path):
        root = _write_config_project(tmp_path, VALID_CONFIG_TOML)
        cfg = config.ProjectConfig.load(root)
        assert lint.lint_config(cfg) == []

    def test_repos_own_config_no_findings(self, tmp_path):
        """The repo's own nyxloom-trove/nyxloom.toml (O1: 'the repo's own'),
        copied under tmp_path with its [refs] targets stubbed out."""
        repo_toml = Path(__file__).resolve().parent.parent / "nyxloom-trove" / "nyxloom.toml"
        root = _write_config_project(
            tmp_path,
            repo_toml.read_text(encoding="utf-8"),
            ref_stubs=(
                "docs/SPEC.md",
                "docs/ARCHITECTURE.md",
                "docs/ROADMAP.md",
                "docs/EVOLUTION.md",
            ),
        )
        cfg = config.ProjectConfig.load(root)
        assert lint.lint_config(cfg) == []

    def test_empty_gate_argv_is_blocking_cfg1(self, tmp_path):
        root = _write_config_project(tmp_path, VALID_CONFIG_TOML)
        cfg = config.ProjectConfig.load(root)
        bad = VALID_CONFIG_TOML.replace('argv = ["true"]', "argv = []")
        (root / "nyxloom-trove" / "nyxloom.toml").write_text(bad)

        findings = lint.lint_config(cfg)
        cfg1 = [f for f in findings if f.rule == "CFG1"]
        assert cfg1, findings
        assert all(f.severity == "error" for f in cfg1)
        assert lint.has_blocking(findings)

    def test_missing_project_id_is_blocking_cfg1(self, tmp_path):
        root = _write_config_project(tmp_path, VALID_CONFIG_TOML)
        cfg = config.ProjectConfig.load(root)
        bad = VALID_CONFIG_TOML.replace('id = "demo"\n', "")
        (root / "nyxloom-trove" / "nyxloom.toml").write_text(bad)

        findings = lint.lint_config(cfg)
        assert any(f.rule == "CFG1" and f.severity == "error" for f in findings)
        assert lint.has_blocking(findings)

    def test_missing_handoff_globs_is_blocking_cfg1(self, tmp_path):
        root = _write_config_project(tmp_path, VALID_CONFIG_TOML)
        cfg = config.ProjectConfig.load(root)
        bad = VALID_CONFIG_TOML.replace(
            'handoff_globs = ["nyxloom-trove/handoffs/*.md"]\n', ""
        )
        (root / "nyxloom-trove" / "nyxloom.toml").write_text(bad)

        findings = lint.lint_config(cfg)
        assert any(f.rule == "CFG1" and f.severity == "error" for f in findings)
        assert lint.has_blocking(findings)

    def test_policy_wrong_type_is_blocking_cfg1(self, tmp_path):
        root = _write_config_project(tmp_path, VALID_CONFIG_TOML)
        cfg = config.ProjectConfig.load(root)
        bad = VALID_CONFIG_TOML.replace(
            "max_active_tasks = 3", 'max_active_tasks = "three"'
        )
        (root / "nyxloom-trove" / "nyxloom.toml").write_text(bad)

        findings = lint.lint_config(cfg)
        assert any(f.rule == "CFG1" and f.severity == "error" for f in findings)
        assert lint.has_blocking(findings)


class TestConfigLintRefs:
    """O2: an unresolved [refs] path is flagged (CFG3), naming the ref;
    all-resolving [refs] lints clean."""

    def test_unresolved_ref_is_blocking_cfg3(self, tmp_path):
        root = _write_config_project(tmp_path, VALID_CONFIG_TOML)
        cfg = config.ProjectConfig.load(root)
        bad = VALID_CONFIG_TOML.replace(
            'spec = "docs/SPEC.md"', 'spec = "docs/MISSING.md"'
        )
        (root / "nyxloom-trove" / "nyxloom.toml").write_text(bad)

        findings = lint.lint_config(cfg)
        cfg3 = [f for f in findings if f.rule == "CFG3"]
        assert cfg3, findings
        assert all(f.severity == "error" for f in cfg3)
        assert "spec" in cfg3[0].message
        assert "docs/MISSING.md" in cfg3[0].message
        assert lint.has_blocking(findings)

    def test_resolving_refs_lint_clean(self, tmp_path):
        root = _write_config_project(tmp_path, VALID_CONFIG_TOML)
        cfg = config.ProjectConfig.load(root)
        findings = lint.lint_config(cfg)
        assert [f for f in findings if f.rule == "CFG3"] == []


class TestConfigLintWorktreeRoot:
    """CFG2: [project].worktree_root, when present, must be non-empty."""

    def test_empty_worktree_root_is_blocking_cfg2(self, tmp_path):
        root = _write_config_project(tmp_path, VALID_CONFIG_TOML)
        cfg = config.ProjectConfig.load(root)
        bad = VALID_CONFIG_TOML.replace('worktree_root = "../.worktrees"', 'worktree_root = ""')
        (root / "nyxloom-trove" / "nyxloom.toml").write_text(bad)

        findings = lint.lint_config(cfg)
        cfg2 = [f for f in findings if f.rule == "CFG2"]
        assert cfg2, findings
        assert all(f.severity == "error" for f in cfg2)
        assert lint.has_blocking(findings)


class TestConfigLintFoldedIntoProject:
    """lint_project(cfg) surfaces config findings under the config's
    root-relative path key, alongside handoff findings."""

    def test_invalid_config_appears_in_lint_project(self, tmp_path):
        root = _write_config_project(tmp_path, VALID_CONFIG_TOML)
        cfg = config.ProjectConfig.load(root)
        bad = VALID_CONFIG_TOML.replace('argv = ["true"]', "argv = []")
        (root / "nyxloom-trove" / "nyxloom.toml").write_text(bad)

        results = lint.lint_project(cfg)
        key = "nyxloom-trove/nyxloom.toml"
        assert key in results
        assert any(f.rule == "CFG1" for f in results[key])

    def test_valid_config_appears_clean_in_lint_project(self, tmp_path):
        root = _write_config_project(tmp_path, VALID_CONFIG_TOML)
        cfg = config.ProjectConfig.load(root)

        results = lint.lint_project(cfg)
        key = "nyxloom-trove/nyxloom.toml"
        assert key in results
        assert results[key] == []
