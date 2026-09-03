"""Tests for lint rules L1-L12, plus config lint rules CFG1-CFG3 (P24)."""

from __future__ import annotations

import logging
import subprocess
import textwrap
from pathlib import Path

import pytest
import structlog.contextvars

from nyxloom import cli, config, frontmatter, lint, log, paths


@pytest.fixture(autouse=True)
def _silence_nyxloom_logging():
    """PACKAGE P05c safety net -- see test_backlog_items.py's copy of this
    fixture for the full rationale (byte-unchanged CLI oracle,
    docs/plan-logging.md P05c)."""
    log.configure(level=log.CRITICAL, console=False)
    yield
    structlog.contextvars.clear_contextvars()
    nyxloom_logger = logging.getLogger("nyxloom")
    for handler in list(nyxloom_logger.handlers):
        nyxloom_logger.removeHandler(handler)
        handler.close()


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

    def test_source_ref_into_archive_is_blocking_l7(self, sample_project, tmp_path):
        """CR-01 (DR-04): a source.ref pointing at an archived/superseded
        doc fails L7, naming the reference and its lifecycle reason -- even
        though the archived file genuinely exists (existence alone is not
        enough; L7's existence check runs AFTER the archive check)."""
        _write_archived_doc(sample_project.root, "docs/archive/product-docs/OLD.md")
        content = textwrap.dedent("""\
            ---
            schema_version: 1
            id: demo-P01-test
            project: demo
            title: Test
            tier: flash-high
            input_revision: "0000000"
            source: {kind: review, ref: "docs/archive/product-docs/OLD.md"}
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
        assert l7_errors, findings
        assert any("archived document" in f.message for f in l7_errors)
        assert any("docs/archive/product-docs/OLD.md" in f.message for f in l7_errors)
        assert any("superseded by nyxloom-trove/3-roadmap.md" in f.message for f in l7_errors)

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


def _l10_project_root(tmp_path: Path, extra_toml: str, subdir: str = "l10-repo") -> Path:
    """A real on-disk project (mirrors conftest.sample_project's own
    git-init+add+commit shape -- NOT `_write_config_project`'s plain-files
    one, further below in this file) with `extra_toml` appended to
    SAMPLE_PROJECT_TOML under `.nyxloom/project.toml`. Caller calls
    ProjectConfig.load(root) itself (never dataclasses.replace) -- each of
    O1/O3/O4 needs a DIFFERENT [lint.l10] table on disk, so this builds a
    fresh root per call rather than reusing the `sample_project` fixture
    instance directly (NL-3 carve review B1)."""
    from conftest import SAMPLE_PROJECT_TOML
    root = tmp_path / subdir
    (root / ".nyxloom").mkdir(parents=True)
    (root / "handoff").mkdir()
    (root / ".nyxloom" / "project.toml").write_text(SAMPLE_PROJECT_TOML + extra_toml)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=root, check=True)
    return root


def _handoff_text_at_token_count(tokens: int) -> str:
    """Handoff content (same shape as TestL10Size's own existing fixtures
    above) whose L10 token estimate (len(full_text)//4, lint.py's own
    formula) is EXACTLY `tokens` -- pads the body filler to hit the target
    byte length precisely, so boundary assertions (`> N` vs `>= N`) are
    unambiguous rather than merely far-from-boundary."""
    template = """---
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

{body}
BLOCKED: marker.
worktree branch out of scope read first context to read
"""
    target_len = tokens * 4
    base_len = len(template.format(body=""))
    body_len = target_len - base_len
    assert body_len >= 0, f"tokens={tokens} too small for template overhead ({base_len} chars)"
    text = template.format(body="x" * body_len)
    assert len(text) // 4 == tokens
    return text


class TestL10Size:
    """Test L10: size limits."""

    def test_large_handoff_warning(self, sample_project, tmp_path):
        """Test L10 warning for handoff over 10k tokens (NL-3: f3b89f46 raised
        the L10 thresholds 6k/12k -> 10k/18k but left these fixtures sized for
        the old floor, so they stopped tripping the new one -- a silent
        regression discovered by nyxloom-P48's live gate run)."""
        large_body = "x" * 45000  # 11250 tokens
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
        """Test L10 error for handoff over 18k tokens (NL-3: see the sibling
        warning test's docstring for why this size changed)."""
        huge_body = "x" * 80000  # 20000 tokens
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

    def test_o1_partial_override_reaches_load_and_pins_new_boundary(self, tmp_path):
        """O1: a REAL ProjectConfig.load() (not dataclasses.replace) on a
        project whose .nyxloom/project.toml has [lint.l10] error_tokens =
        25000 (warn_tokens absent) produces cfg.l10.error_tokens == 25000
        and cfg.l10.warn_tokens == 10000 (untouched default); a handoff at
        exactly the new boundary (25000 tokens) is WARNING not ERROR, and
        one token over (25001) IS ERROR. Proves the override reaches the
        instance .load() returns, a partial override leaves the other
        field at its default, and the exact strict `>` boundary survives
        parameterization."""
        root = _l10_project_root(tmp_path, "\n[lint.l10]\nerror_tokens = 25000\n")
        cfg = config.ProjectConfig.load(root)
        assert cfg.l10.error_tokens == 25000
        assert cfg.l10.warn_tokens == 10000

        at_boundary = tmp_path / "at-boundary.md"
        at_boundary.write_text(_handoff_text_at_token_count(25000))
        findings = lint.lint_file(at_boundary, cfg)
        l10 = [f for f in findings if f.rule == "L10"]
        assert any(f.severity == "warning" for f in l10)
        assert not any(f.severity == "error" for f in l10)

        over_boundary = tmp_path / "over-boundary.md"
        over_boundary.write_text(_handoff_text_at_token_count(25001))
        findings2 = lint.lint_file(over_boundary, cfg)
        l10_2 = [f for f in findings2 if f.rule == "L10"]
        assert any(f.severity == "error" for f in l10_2)

    def test_default_thresholds_boundary_values(self, sample_project, tmp_path):
        """O2 addendum: pins today's strict `>` boundary as part of the
        frozen contract, not an implementation detail free to drift once
        the literals become variables -- a handoff at exactly 10000
        tokens is not flagged at all, and one at exactly 18000 tokens is
        WARNING, not ERROR, against the plain (no-override) default
        config."""
        at_warn = tmp_path / "at-warn.md"
        at_warn.write_text(_handoff_text_at_token_count(10000))
        findings = lint.lint_file(at_warn, sample_project)
        assert not any(f.rule == "L10" for f in findings)

        at_error = tmp_path / "at-error.md"
        at_error.write_text(_handoff_text_at_token_count(18000))
        findings2 = lint.lint_file(at_error, sample_project)
        l10 = [f for f in findings2 if f.rule == "L10"]
        assert any(f.severity == "warning" for f in l10)
        assert not any(f.severity == "error" for f in l10)

    def test_o3_malformed_warn_greater_than_error_raises(self, tmp_path):
        """O3, case 1: warn_tokens > error_tokens raises ValueError at
        load time, before any handoff is linted."""
        root = _l10_project_root(
            tmp_path, "\n[lint.l10]\nwarn_tokens = 20000\nerror_tokens = 10000\n")
        with pytest.raises(ValueError):
            config.ProjectConfig.load(root)

    def test_o3_malformed_warn_equals_error_raises(self, tmp_path):
        """O3, case 2 (the finding that failed the first carve draft): the
        validation is warn_tokens >= error_tokens, not strict `>`, so
        EQUALITY is also malformed -- a different boundary than
        _check_l10's own strict `>` comparison (Work item 2/3 state this
        explicitly; do not confuse the two)."""
        root = _l10_project_root(
            tmp_path, "\n[lint.l10]\nwarn_tokens = 10000\nerror_tokens = 10000\n")
        with pytest.raises(ValueError):
            config.ProjectConfig.load(root)

    def test_o3_malformed_non_positive_raises(self, tmp_path):
        """O3, case 3: a non-positive value raises ValueError at load
        time."""
        root = _l10_project_root(
            tmp_path, "\n[lint.l10]\nerror_tokens = -5\n")
        with pytest.raises(ValueError):
            config.ProjectConfig.load(root)

    def test_o4_lowered_thresholds_apply_symmetrically(self, tmp_path):
        """O4: a full override LOWERING both thresholds below the
        tool-wide defaults (warn_tokens=500, error_tokens=1000) lints a
        ~700-token handoff (far under the OLD 10000/18000 defaults, but
        between the NEW tighter numbers) as L10 WARNING -- same code path
        as the raising case in O1, no special-cased direction."""
        root = _l10_project_root(
            tmp_path, "\n[lint.l10]\nwarn_tokens = 500\nerror_tokens = 1000\n")
        cfg = config.ProjectConfig.load(root)
        assert cfg.l10.warn_tokens == 500
        assert cfg.l10.error_tokens == 1000

        handoff = tmp_path / "lowered.md"
        handoff.write_text(_handoff_text_at_token_count(700))
        findings = lint.lint_file(handoff, cfg)
        l10 = [f for f in findings if f.rule == "L10"]
        assert any(f.severity == "warning" for f in l10)
        assert not any(f.severity == "error" for f in l10)

    def test_o5_schema_accepts_partial_l10_override(self, tmp_path):
        """O5: nyxloom lint's own config-schema check (CFG1 /
        lint.lint_config) run against a nyxloom.toml declaring ONLY
        [lint.l10] error_tokens = 25000 (the same partial-override shape
        O1 uses) produces no schema-validation finding -- proves
        warn_tokens/error_tokens were NOT marked required, and lint/l10's
        additionalProperties:false doesn't reject the legal partial
        shape."""
        root = _l10_project_root(tmp_path, "\n[lint.l10]\nerror_tokens = 25000\n")
        cfg = config.ProjectConfig.load(root)
        findings = lint.lint_config(cfg)
        assert not any(f.rule == "CFG1" for f in findings)


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


class TestL13OracleScopeCoverage:
    """Test L13 (B22): every oracle-referenced repo path must be covered by
    scope.touch -- an oracle the implementer cannot satisfy without editing
    a file outside scope.touch is an authoring defect."""

    def test_oracle_path_in_scope_no_finding(self, sample_project, tmp_path):
        """O2: the referenced path IS in scope.touch -> no L13 finding."""
        content = textwrap.dedent("""\
            ---
            schema_version: 1
            id: demo-P01-test
            project: demo
            title: Test
            tier: flash-high
            input_revision: "0000000"
            source: {kind: review}
            scope: {touch: ["src/nyxloom/foo.py"]}
            oracles:
              - id: O1
                observable: "pytest verifies src/nyxloom/foo.py behaves as expected"
                negative: "a violation raises"
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
        l13 = [f for f in findings if f.rule == "L13"]
        assert l13 == []

    def test_oracle_path_out_of_scope_flagged(self, sample_project, tmp_path):
        """O1: the referenced path is NOT in scope.touch -> exactly one L13
        finding citing the oracle id and the offending path."""
        content = textwrap.dedent("""\
            ---
            schema_version: 1
            id: demo-P01-test
            project: demo
            title: Test
            tier: flash-high
            input_revision: "0000000"
            source: {kind: review}
            scope: {touch: ["src/nyxloom/bar.py"]}
            oracles:
              - id: O1
                observable: "pytest verifies src/nyxloom/foo.py behaves as expected"
                negative: "a violation raises"
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
        l13 = [f for f in findings if f.rule == "L13"]
        assert len(l13) == 1
        assert l13[0].severity == "warning"
        assert "O1" in l13[0].message
        assert "src/nyxloom/foo.py" in l13[0].message

    def test_oracle_prose_only_no_false_positive(self, sample_project, tmp_path):
        """O3: pure prose with no path token -> no L13 finding (no false
        positive on ordinary English)."""
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
                observable: "the response contains every expected field and status ok"
                negative: "a missing field raises a validation error"
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
        l13 = [f for f in findings if f.rule == "L13"]
        assert l13 == []

    def test_same_path_in_observable_and_negative_dedupes(self, sample_project, tmp_path):
        """The same out-of-scope path repeated across observable/negative for
        one oracle still yields exactly one finding (not one per mention)."""
        content = textwrap.dedent("""\
            ---
            schema_version: 1
            id: demo-P01-test
            project: demo
            title: Test
            tier: flash-high
            input_revision: "0000000"
            source: {kind: review}
            scope: {touch: ["src/nyxloom/bar.py"]}
            oracles:
              - id: O1
                observable: "src/nyxloom/foo.py behaves as expected"
                negative: "src/nyxloom/foo.py raises on a bad value"
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
        l13 = [f for f in findings if f.rule == "L13"]
        assert len(l13) == 1

    def test_empty_scope_touch_flags_rather_than_crashes(self):
        """Edge case: scope.touch cannot actually be empty through the public
        schema-validated path (minItems: 1), so this exercises the rule
        function directly against a hand-built Frontmatter -- an empty
        scope.touch matches nothing, so a referenced path is (correctly)
        flagged, and the rule must not crash."""
        from nyxloom.types import Frontmatter, Oracle, Scope, Source

        fm = Frontmatter(
            schema_version=1,
            id="demo-P01-test",
            project="demo",
            title="Test",
            tier="flash-high",
            input_revision="0000000",
            source=Source(kind="review"),
            scope=Scope(touch=[]),
            oracles=[
                Oracle(id="O1", observable="see src/nyxloom/foo.py for the fix",
                       negative="fail", gate="pytest-q"),
            ],
            gates=["pytest-q"],
            escalate_if=["trigger"],
        )
        findings: list = []
        lint._check_l13(findings, Path("demo-P01-test.md"), fm)
        assert len(findings) == 1
        assert findings[0].rule == "L13"
        assert findings[0].severity == "warning"

    def test_empty_scope_touch_no_path_reference_no_finding(self):
        """Same empty scope.touch, but the oracle has no path token at all --
        must not crash and must not fabricate a finding."""
        from nyxloom.types import Frontmatter, Oracle, Scope, Source

        fm = Frontmatter(
            schema_version=1,
            id="demo-P01-test",
            project="demo",
            title="Test",
            tier="flash-high",
            input_revision="0000000",
            source=Source(kind="review"),
            scope=Scope(touch=[]),
            oracles=[
                Oracle(id="O1", observable="the test passes", negative="it fails",
                       gate="pytest-q"),
            ],
            gates=["pytest-q"],
            escalate_if=["trigger"],
        )
        findings: list = []
        lint._check_l13(findings, Path("demo-P01-test.md"), fm)
        assert findings == []


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
            ("demo-P21-huge.md", "L10", False),  # L10 warning becomes error over 18k
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

    def test_asserts_valid_list_lints_clean(self, tmp_path):
        """GA2: a gate declaring a schema-valid `asserts` list lints clean."""
        root = _write_config_project(tmp_path, VALID_CONFIG_TOML)
        cfg = config.ProjectConfig.load(root)
        good = VALID_CONFIG_TOML.replace(
            "timeout_seconds = 60\n",
            'timeout_seconds = 60\nasserts = ["tests-pass", "canary-verified"]\n',
        )
        (root / "nyxloom-trove" / "nyxloom.toml").write_text(good)

        assert lint.lint_config(cfg) == []

    def test_asserts_unknown_value_is_blocking_cfg1(self, tmp_path):
        """GA2: `asserts` is schema-enum-constrained -- an unknown value is
        a CFG1 error, not silently accepted."""
        root = _write_config_project(tmp_path, VALID_CONFIG_TOML)
        cfg = config.ProjectConfig.load(root)
        bad = VALID_CONFIG_TOML.replace(
            "timeout_seconds = 60\n",
            'timeout_seconds = 60\nasserts = ["bogus"]\n',
        )
        (root / "nyxloom-trove" / "nyxloom.toml").write_text(bad)

        findings = lint.lint_config(cfg)
        assert any(f.rule == "CFG1" and f.severity == "error" for f in findings)
        assert lint.has_blocking(findings)

    def test_asserts_non_list_is_blocking_cfg1(self, tmp_path):
        """GA2: `asserts` must be a list -- a bare string is a CFG1 error."""
        root = _write_config_project(tmp_path, VALID_CONFIG_TOML)
        cfg = config.ProjectConfig.load(root)
        bad = VALID_CONFIG_TOML.replace(
            "timeout_seconds = 60\n",
            'timeout_seconds = 60\nasserts = "tests-pass"\n',
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

    def test_absolute_ref_outside_root_is_blocking_cfg3(self, tmp_path):
        """An absolute ref exists on disk but is not under the project root:
        existence alone must not clear CFG3 (handoff: 'resolves under cfg.root')."""
        root = _write_config_project(tmp_path, VALID_CONFIG_TOML)
        cfg = config.ProjectConfig.load(root)
        outside = tmp_path / "outside.md"
        outside.write_text("x\n")
        bad = VALID_CONFIG_TOML.replace(
            'spec = "docs/SPEC.md"', f'spec = "{outside}"'
        )
        (root / "nyxloom-trove" / "nyxloom.toml").write_text(bad)

        findings = lint.lint_config(cfg)
        cfg3 = [f for f in findings if f.rule == "CFG3"]
        assert cfg3, findings
        assert all(f.severity == "error" for f in cfg3)
        assert "spec" in cfg3[0].message
        assert lint.has_blocking(findings)

    def test_parent_escaping_ref_is_blocking_cfg3(self, tmp_path):
        """A '..'-escaping ref that resolves to a real file outside the root."""
        root = _write_config_project(tmp_path, VALID_CONFIG_TOML)
        cfg = config.ProjectConfig.load(root)
        (tmp_path / "outside.md").write_text("x\n")
        bad = VALID_CONFIG_TOML.replace(
            'spec = "docs/SPEC.md"', 'spec = "../outside.md"'
        )
        (root / "nyxloom-trove" / "nyxloom.toml").write_text(bad)

        findings = lint.lint_config(cfg)
        cfg3 = [f for f in findings if f.rule == "CFG3"]
        assert cfg3, findings
        assert "../outside.md" in cfg3[0].message
        assert lint.has_blocking(findings)


_ARCHIVED_SUPERSEDED_DOC = """\
---
lifecycle: archived
status: superseded
archived_date: "2026-08-03"
superseded_by: nyxloom-trove/3-roadmap.md
reason: test fixture
---

# archived
"""


def _write_archived_doc(root: Path, rel: str = "docs/archive/product-docs/OLD.md") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_ARCHIVED_SUPERSEDED_DOC)


class TestConfigLintArchiveRefs:
    """CFG4 (CR-01, DR-04): a [refs] entry that resolves into docs/archive/
    is rejected -- naming the ref, the resolved archive path, and (when the
    archived doc's own lifecycle frontmatter is readable) its reason."""

    def test_ref_into_archive_is_blocking_cfg4(self, tmp_path):
        root = _write_config_project(tmp_path, VALID_CONFIG_TOML)
        _write_archived_doc(root)
        bad = VALID_CONFIG_TOML.replace(
            'spec = "docs/SPEC.md"', 'spec = "docs/archive/product-docs/OLD.md"'
        )
        (root / "nyxloom-trove" / "nyxloom.toml").write_text(bad)

        findings = lint.lint_config(config.ProjectConfig.load(root))
        cfg4 = [f for f in findings if f.rule == "CFG4"]
        assert cfg4, findings
        assert all(f.severity == "error" for f in cfg4)
        assert "spec" in cfg4[0].message
        assert "docs/archive/product-docs/OLD.md" in cfg4[0].message
        assert "superseded by nyxloom-trove/3-roadmap.md" in cfg4[0].message
        assert lint.has_blocking(findings)

    def test_ref_into_archive_via_dotdot_normalization_is_blocking_cfg4(self, tmp_path):
        """A ref that stays nominally 'under docs/' but normalizes (via '..')
        into the archive must be caught the same as a direct path -- proves
        containment is checked post-resolve, not by string prefix."""
        root = _write_config_project(tmp_path, VALID_CONFIG_TOML)
        _write_archived_doc(root)
        bad = VALID_CONFIG_TOML.replace(
            'spec = "docs/SPEC.md"',
            'spec = "docs/../docs/archive/product-docs/OLD.md"',
        )
        (root / "nyxloom-trove" / "nyxloom.toml").write_text(bad)

        findings = lint.lint_config(config.ProjectConfig.load(root))
        cfg4 = [f for f in findings if f.rule == "CFG4"]
        assert cfg4, findings

    def test_ref_into_archive_via_symlink_is_blocking_cfg4(self, tmp_path):
        """A ref path that is NOT lexically under docs/archive/ but resolves
        there through a symlink must still be caught (containment is
        checked after symlink resolution)."""
        root = _write_config_project(tmp_path, VALID_CONFIG_TOML)
        _write_archived_doc(root)
        link = root / "docs" / "SNEAKY.md"
        link.symlink_to(root / "docs" / "archive" / "product-docs" / "OLD.md")
        bad = VALID_CONFIG_TOML.replace(
            'spec = "docs/SPEC.md"', 'spec = "docs/SNEAKY.md"'
        )
        (root / "nyxloom-trove" / "nyxloom.toml").write_text(bad)

        findings = lint.lint_config(config.ProjectConfig.load(root))
        cfg4 = [f for f in findings if f.rule == "CFG4"]
        assert cfg4, findings
        assert "docs/SNEAKY.md" in cfg4[0].message

    def test_ref_into_archive_with_unreadable_metadata_still_blocks(self, tmp_path):
        """Fail-closed (CR-01 contract item 6): an archived doc whose OWN
        frontmatter is missing/unparsable is still excluded -- containment
        alone decides, metadata only enriches the message."""
        root = _write_config_project(tmp_path, VALID_CONFIG_TOML)
        junk = root / "docs" / "archive" / "product-docs" / "JUNK.md"
        junk.parent.mkdir(parents=True, exist_ok=True)
        junk.write_text("not frontmatter at all\n")
        bad = VALID_CONFIG_TOML.replace(
            'spec = "docs/SPEC.md"', 'spec = "docs/archive/product-docs/JUNK.md"'
        )
        (root / "nyxloom-trove" / "nyxloom.toml").write_text(bad)

        findings = lint.lint_config(config.ProjectConfig.load(root))
        cfg4 = [f for f in findings if f.rule == "CFG4"]
        assert cfg4, findings
        assert "lifecycle metadata unreadable" in cfg4[0].message

    def test_ref_outside_archive_has_no_cfg4_finding(self, tmp_path):
        root = _write_config_project(tmp_path, VALID_CONFIG_TOML)
        findings = lint.lint_config(config.ProjectConfig.load(root))
        assert [f for f in findings if f.rule == "CFG4"] == []


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


class TestArchiveLintFoldedIntoProject:
    """ARC1 (CR-01, DR-04): `lint_project` folds one entry per archived
    product doc, keyed by that DOC's root-relative path.

    The rule only earns its keep if it reaches the surface an operator and
    the daemon actually run -- `nyxloom lint` calls `lint_project`, not
    `doc_lifecycle.lint_archive`. A rule that fires in its own unit test and
    is never folded into the project result is a rule nothing enforces."""

    def test_a_malformed_archived_doc_surfaces_under_its_own_path(self, tmp_path):
        root = _write_config_project(tmp_path, VALID_CONFIG_TOML)
        cfg = config.ProjectConfig.load(root)
        junk = root / "docs" / "archive" / "product-docs" / "JUNK.md"
        junk.parent.mkdir(parents=True, exist_ok=True)
        junk.write_text("not frontmatter at all\n")

        results = lint.lint_project(cfg)

        key = "docs/archive/product-docs/JUNK.md"
        assert key in results, sorted(results)
        arc1 = [f for f in results[key] if f.rule == "ARC1"]
        assert arc1, results[key]
        assert all(f.severity == "error" for f in arc1)
        # Blocking, like S4: a mis-tagged archive entry must not pass review
        # by being invisible.
        assert lint.has_blocking(results[key])

    def test_a_schema_violating_archived_doc_surfaces_its_schema_error(self, tmp_path):
        """status=historical forbids superseded_by -- a "historical" doc that
        names a successor is claiming a replacement that does not exist as a
        lifecycle relation, and the message must say which field is wrong."""
        root = _write_config_project(tmp_path, VALID_CONFIG_TOML)
        cfg = config.ProjectConfig.load(root)
        doc = root / "docs" / "archive" / "product-docs" / "BAD.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(
            "---\nlifecycle: archived\nstatus: historical\n"
            'archived_date: "2026-08-03"\nsuperseded_by: docs/SPEC.md\n'
            "reason: test fixture\n---\n\n# archived\n")

        results = lint.lint_project(cfg)

        key = "docs/archive/product-docs/BAD.md"
        assert [f.rule for f in results.get(key, [])] == ["ARC1"], results.get(key)

    def test_a_well_formed_archived_doc_adds_no_findings(self, tmp_path):
        """The negative half: containment is not itself a finding. Only a
        BROKEN lifecycle record is."""
        root = _write_config_project(tmp_path, VALID_CONFIG_TOML)
        cfg = config.ProjectConfig.load(root)
        _write_archived_doc(root, "docs/archive/product-docs/OK.md")

        results = lint.lint_project(cfg)

        assert results.get("docs/archive/product-docs/OK.md", []) == []


# ---------------------------------------------------------------------------
# P35: lint path resolution -- owning project (O1-O3), project-driven L7
# cross-repo check (O4), trove-location depends_on resolution (O5). Fixtures
# use the TROVE layout (nyxloom-trove/nyxloom.toml + nyxloom-trove/handoffs/),
# unlike sample_project's legacy .nyxloom/project.toml + handoff/, because the
# bug this package fixes is specific to the trove layout nyxloom itself uses.

def _write_trove_project(tmp_path: Path, dirname: str, project_id: str,
                          gate_id: str = "tester-unified",
                          archive_dir: str | None = None) -> Path:
    """A trove-layout project root: nyxloom-trove/nyxloom.toml +
    nyxloom-trove/handoffs/ dir."""
    root = tmp_path / dirname
    (root / "nyxloom-trove" / "handoffs").mkdir(parents=True)
    archive_line = f'\narchive_dir = "{archive_dir}"' if archive_dir else ""
    (root / "nyxloom-trove" / "nyxloom.toml").write_text(textwrap.dedent(f"""\
        [project]
        id = "{project_id}"
        handoff_globs = ["nyxloom-trove/handoffs/*.md"]{archive_line}

        [gates.{gate_id}]
        argv = ["true"]
        phase = "implementation"
        timeout_seconds = 60

        [policy]
        max_active_tasks = 2
        """))
    return root


def _handoff_text(project_id: str, handoff_id: str, *,
                   touch: tuple[str, ...] = ("src/thing.py",),
                   forbid: tuple[str, ...] = (),
                   gate_id: str = "tester-unified",
                   depends_on: tuple[str, ...] = ()) -> str:
    """A schema-valid, otherwise-clean handoff's text (mirrors
    conftest.SAMPLE_HANDOFF, parameterized by project)."""
    lines = [
        "---",
        "schema_version: 1",
        f"id: {handoff_id}",
        f"project: {project_id}",
        "title: Real thing",
        "tier: flash-high",
        'input_revision: "0000000"',
        "source: {kind: roadmap}",
        "scope:",
        "  touch: [" + ", ".join(f'"{t}"' for t in touch) + "]",
    ]
    if forbid:
        lines.append("  forbid: [" + ", ".join(f'"{f}"' for f in forbid) + "]")
    if depends_on:
        lines.append("depends_on: [" + ", ".join(f'"{d}"' for d in depends_on) + "]")
    lines += [
        "oracles:",
        "  - id: O1",
        '    observable: "pytest passes"',
        '    negative: "a violation raises"',
        f"    gate: {gate_id}",
        f"gates: [{gate_id}]",
        'escalate_if: ["a named contract cannot be met as specified"]',
        "---",
        "",
        "# Body",
        "",
        "worktree branch out of scope read first context to read",
        "",
        "BLOCKED: nothing to block on.",
        "",
    ]
    return "\n".join(lines)


def _write_real_handoff(root: Path, project_id: str, handoff_id: str,
                         **kwargs) -> Path:
    """Write a schema-valid, otherwise-clean handoff under root's configured
    handoff dir."""
    path = root / "nyxloom-trove" / "handoffs" / f"{handoff_id}.md"
    path.write_text(_handoff_text(project_id, handoff_id, **kwargs))
    return path


class TestResolveProjectForPath:
    """O1: a resolver maps a path to its OWNING project's config by walking
    ancestors for the nearest nyxloom.toml / matching registered roots --
    never by registry iteration order."""

    def test_each_handoff_resolves_to_its_own_project(self, tmp_path):
        root_a = _write_trove_project(tmp_path, "proj-a", "proja")
        root_b = _write_trove_project(tmp_path, "proj-b", "projb")
        handoff_a = _write_real_handoff(root_a, "proja", "proja-P01-real")
        handoff_b = _write_real_handoff(root_b, "projb", "projb-P01-real")

        # projb listed FIRST -- the accident cli.py used to depend on.
        registry = {"projb": root_b, "proja": root_a}

        cfg_a = lint.resolve_project_for_path(handoff_a, registry)
        cfg_b = lint.resolve_project_for_path(handoff_b, registry)

        assert cfg_a is not None and cfg_a.project_id == "proja"
        assert cfg_b is not None and cfg_b.project_id == "projb"

    def test_unregistered_checkout_resolves_via_ancestor_walk(self, tmp_path):
        """A checkout with its own nyxloom.toml but absent from the registry
        still resolves -- the fallback alone (registry-only) can't do this."""
        root = _write_trove_project(tmp_path, "unregistered", "orphan-proj")
        handoff = _write_real_handoff(root, "orphan-proj", "orphan-proj-P01-real")

        cfg = lint.resolve_project_for_path(handoff, {})
        assert cfg is not None and cfg.project_id == "orphan-proj"


class TestCmdLintResolvesOwnProject:
    """O2: cmd_lint lints each path arg against its OWN resolved project,
    not whichever project happens to be first in the registry dict."""

    def test_known_good_handoff_lints_clean_with_different_project_first(
        self, tmp_path, tmp_state, monkeypatch, capsys
    ):
        root_other = _write_trove_project(tmp_path, "other-repo", "other", gate_id="other-gate")
        root_mine = _write_trove_project(tmp_path, "mine-repo", "mine", gate_id="tester-unified")
        # A file that exists in "mine" but not in "other" -- reproduces the
        # live bug's L7 wall when scope.forbid resolves against the wrong root.
        (root_mine / "src").mkdir()
        (root_mine / "src" / "other.py").write_text("# stub\n")
        handoff = _write_real_handoff(
            root_mine, "mine", "mine-P01-real",
            forbid=("src/other.py",),
        )

        registry = {"other": root_other, "mine": root_mine}  # "other" first
        monkeypatch.setattr("nyxloom.config.load_registry", lambda: registry)

        exit_code = cli.main(["lint", str(handoff)])
        out = capsys.readouterr().out

        assert "does not match config" not in out
        assert "not declared in project.toml" not in out
        assert "does not exist" not in out
        assert exit_code == 0


def test_cmd_lint_all_fails_closed_on_invalid_project_config(
    tmp_path, tmp_state, monkeypatch, capsys
):
    root = tmp_path / "broken-project"
    (root / "nyxloom-trove").mkdir(parents=True)
    config_path = root / "nyxloom-trove" / "nyxloom.toml"
    config_path.write_text("this is not = valid toml\n", encoding="utf-8")
    monkeypatch.setattr("nyxloom.config.load_registry", lambda: {"broken": root})

    exit_code = cli.main(["lint"])
    output = capsys.readouterr().out

    assert exit_code == 1
    assert str(config_path) in output
    assert "L0 error project config could not be loaded" in output


class TestCmdLintUnresolvedPath:
    """O3: a path with no owning project gets a typed diagnostic naming the
    path and a non-zero exit -- never lints against an arbitrary project's
    config, and never crashes."""

    def test_orphan_path_gets_diagnostic_not_wrong_project_findings(
        self, tmp_path, tmp_state, monkeypatch, capsys
    ):
        root_other = _write_trove_project(tmp_path, "other-repo", "other")
        registry = {"other": root_other}
        monkeypatch.setattr("nyxloom.config.load_registry", lambda: registry)

        # A SCHEMA-VALID handoff for an unregistered project, outside every
        # project root. It must be valid and name its own project: a garbage
        # file has no `project` field to mismatch, so it cannot distinguish
        # the typed diagnostic from the old bug's wrong-config findings.
        orphan = tmp_path / "orphan-dir" / "orphanproj-P01-real.md"
        orphan.parent.mkdir()
        orphan.write_text(_handoff_text("orphanproj", "orphanproj-P01-real"))

        exit_code = cli.main(["lint", str(orphan)])
        out = capsys.readouterr().out

        assert exit_code != 0
        assert str(orphan) in out
        # The typed, actionable diagnostic -- not an arbitrary project's findings.
        assert "L0" in out
        assert "no owning project" in out
        # Never silently linted against "other"'s config.
        assert "does not match config" not in out
        assert "not declared in project.toml" not in out


class TestL7CrossRepoProjectDriven:
    """O4: the cross-repo body check exempts the PROJECT'S OWN /workspaces/
    segment (derived from cfg.root), not a hardcoded name."""

    def test_own_repo_reference_clean_foreign_repo_still_warns(self, tmp_path):
        root = tmp_path / "workspaces" / "myrepo" / "proj"
        (root / "nyxloom-trove" / "handoffs").mkdir(parents=True)
        (root / "nyxloom-trove" / "nyxloom.toml").write_text(textwrap.dedent("""\
            [project]
            id = "proj"
            handoff_globs = ["nyxloom-trove/handoffs/*.md"]

            [gates.tester-unified]
            argv = ["true"]
            phase = "implementation"
            timeout_seconds = 60
            """))
        cfg = config.ProjectConfig.load(root)

        handoff = _write_real_handoff(root, "proj", "proj-P01-real")
        text = handoff.read_text()
        text += (
            "\nGate lives at `/workspaces/myrepo/proj` -- this repo's own path.\n"
            "See also `/workspaces/otherrepo/docs/spec.md` for unrelated context.\n"
        )
        handoff.write_text(text)

        findings = lint.lint_file(handoff, cfg)
        messages = [f.message for f in findings if f.rule == "L7" and f.severity == "warning"]

        assert not any("myrepo" in m for m in messages)
        assert any("otherrepo" in m for m in messages)


class TestL1DependsOnTroveResolution:
    """O5: depends_on resolves against the project's CONFIGURED handoff
    location (and archive_dir), not the hardcoded legacy handoff/<id>.md
    that a trove-standard project like nyxloom doesn't even have."""

    def test_dep_only_under_trove_handoffs_resolves_with_no_statefile(
        self, tmp_path, tmp_state
    ):
        root = _write_trove_project(tmp_path, "trove-only", "troveproj")
        _write_real_handoff(root, "troveproj", "troveproj-P01-dep")
        dependent = _write_real_handoff(
            root, "troveproj", "troveproj-P02-real",
            depends_on=("troveproj-P01-dep",),
        )
        cfg = config.ProjectConfig.load(root)

        assert not paths.state_dir(cfg.project_id).exists()

        findings = lint.lint_file(dependent, cfg)
        l1_dep_errors = [f for f in findings if f.rule == "L1" and "depends_on" in f.message]
        assert l1_dep_errors == []

    def test_dep_resolvable_nowhere_still_errors(self, tmp_path, tmp_state):
        root = _write_trove_project(tmp_path, "trove-ghost", "ghostproj")
        dependent = _write_real_handoff(
            root, "ghostproj", "ghostproj-P02-real",
            depends_on=("ghostproj-P99-ghost",),
        )
        cfg = config.ProjectConfig.load(root)

        findings = lint.lint_file(dependent, cfg)
        l1_dep_errors = [f for f in findings if f.rule == "L1" and "depends_on" in f.message]
        assert l1_dep_errors != []

    def test_dep_archived_on_merge_resolves(self, tmp_path, tmp_state):
        root = _write_trove_project(tmp_path, "trove-archived", "archproj",
                                     archive_dir="nyxloom-trove/archive")
        archive_dir = root / "nyxloom-trove" / "archive"
        archive_dir.mkdir()

        dep_handoff = _write_real_handoff(root, "archproj", "archproj-P01-dep")
        archive_dir.joinpath("archproj-P01-dep.md").write_text(dep_handoff.read_text())
        dep_handoff.unlink()

        dependent = _write_real_handoff(
            root, "archproj", "archproj-P02-real",
            depends_on=("archproj-P01-dep",),
        )
        cfg = config.ProjectConfig.load(root)

        findings = lint.lint_file(dependent, cfg)
        l1_dep_errors = [f for f in findings if f.rule == "L1" and "depends_on" in f.message]
        assert l1_dep_errors == []
