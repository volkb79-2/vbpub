"""Tests for frontmatter parsing and discovery."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from handoffctl import frontmatter


class TestSplitFrontmatter:
    """Test split_frontmatter function."""

    def test_valid_split(self):
        """Test valid frontmatter splitting."""
        text = "---\nkey: value\n---\nbody text"
        data, body, line = frontmatter.split_frontmatter(text)
        assert data == {"key": "value"}
        assert body == "body text"
        assert line == 4

    def test_missing_leading_dash(self):
        """Test missing leading ---."""
        text = "key: value\n---\nbody"
        with pytest.raises(frontmatter.HandoffParseError) as exc:
            frontmatter.split_frontmatter(text)
        assert "missing leading '---'" in str(exc.value)

    def test_unterminated_frontmatter(self):
        """Test unterminated frontmatter."""
        text = "---\nkey: value\nbody"
        with pytest.raises(frontmatter.HandoffParseError) as exc:
            frontmatter.split_frontmatter(text)
        assert "unterminated frontmatter" in str(exc.value)

    def test_yaml_parse_error(self):
        """Test YAML parse error."""
        text = "---\nkey: {invalid\n---\nbody"
        with pytest.raises(frontmatter.HandoffParseError) as exc:
            frontmatter.split_frontmatter(text)
        assert "YAML parse error" in str(exc.value)

    def test_not_a_mapping(self):
        """Test YAML that is not a mapping."""
        text = "---\n- item1\n- item2\n---\nbody"
        with pytest.raises(frontmatter.HandoffParseError) as exc:
            frontmatter.split_frontmatter(text)
        assert "not a mapping" in str(exc.value)

    def test_body_start_line_calculation(self):
        """Test body_start_line is correct."""
        text = "---\nkey: value\n---\nline1\nline2"
        data, body, line = frontmatter.split_frontmatter(text)
        assert line == 4  # 1-based


class TestSchemaErrors:
    """Test schema_errors function."""

    def test_valid_data(self):
        """Test valid frontmatter passes schema."""
        data = {
            "schema_version": 1,
            "id": "demo-P01-test",
            "project": "demo",
            "title": "Test",
            "tier": "flash-high",
            "input_revision": "0000000",
            "source": {"kind": "review"},
            "scope": {"touch": ["src/test.py"]},
            "oracles": [{"id": "O1", "observable": "pass", "negative": "fail", "gate": "gate1"}],
            "gates": ["gate1"],
            "escalate_if": ["trigger"],
        }
        errors = frontmatter.schema_errors(data)
        assert errors == []

    def test_missing_required_field(self):
        """Test missing required field."""
        data = {
            "schema_version": 1,
            "id": "demo-P01-test",
            "project": "demo",
            # title missing
            "tier": "flash-high",
            "input_revision": "0000000",
            "source": {"kind": "review"},
            "scope": {"touch": ["src/test.py"]},
            "oracles": [{"id": "O1", "observable": "pass", "negative": "fail", "gate": "gate1"}],
            "gates": ["gate1"],
            "escalate_if": ["trigger"],
        }
        errors = frontmatter.schema_errors(data)
        assert any("title" in str(e).lower() for e in errors)

    def test_two_violations_reported(self):
        """Test that two violations are both reported."""
        data = {
            "schema_version": 2,  # wrong version
            "id": "invalid id",  # doesn't match pattern
            "project": "demo",
            "title": "Test",
            "tier": "flash-high",
            "input_revision": "0000000",
            "source": {"kind": "review"},
            "scope": {"touch": ["src/test.py"]},
            "oracles": [{"id": "O1", "observable": "pass", "negative": "fail", "gate": "gate1"}],
            "gates": ["gate1"],
            "escalate_if": ["trigger"],
        }
        errors = frontmatter.schema_errors(data)
        assert len(errors) >= 2


class TestParseHandoff:
    """Test parse_handoff function."""

    def test_parse_valid(self, tmp_path):
        """Test parsing a valid handoff file."""
        content = textwrap.dedent("""\
            ---
            schema_version: 1
            id: demo-P01-test
            project: demo
            title: Test Package
            tier: flash-high
            input_revision: "0000000"
            source: {kind: review}
            scope: {touch: ["src/test.py"]}
            oracles:
              - id: O1
                observable: "test passes"
                negative: "test fails"
                gate: gate1
            gates: [gate1]
            escalate_if: ["trigger"]
            ---

            Body content here.
            """)
        path = tmp_path / "test.md"
        path.write_text(content)

        fm, body = frontmatter.parse_handoff(path)
        assert fm.id == "demo-P01-test"
        assert fm.project == "demo"
        assert "Body content here" in body

    def test_parse_error_bubbles_up(self, tmp_path):
        """Test that parse errors bubble up with correct path."""
        content = "invalid\n"
        path = tmp_path / "bad.md"
        path.write_text(content)

        with pytest.raises(frontmatter.HandoffParseError) as exc:
            frontmatter.parse_handoff(path)
        assert str(path) in str(exc.value.path)

    def test_round_trip(self, tmp_path):
        """Test that parse -> to_dict -> from_dict round-trips."""
        content = textwrap.dedent("""\
            ---
            schema_version: 1
            id: demo-P01-test
            project: demo
            title: Test Package
            tier: flash-high
            input_revision: "0000000"
            source: {kind: review}
            scope: {touch: ["src/test.py"]}
            oracles:
              - id: O1
                observable: "test passes"
                negative: "test fails"
                gate: gate1
            gates: [gate1]
            escalate_if: ["trigger"]
            ---

            Body content.
            """)
        path = tmp_path / "test.md"
        path.write_text(content)

        fm, body = frontmatter.parse_handoff(path)
        d = fm.to_dict()
        fm2 = fm.from_dict(d)
        assert fm.to_dict() == fm2.to_dict()


class TestDiscoverHandoffs:
    """Test discover_handoffs function."""

    def test_discover_in_sample_project(self, sample_project):
        """Test discovering handoffs in sample project."""
        handoffs = frontmatter.discover_handoffs(sample_project)
        assert len(handoffs) == 1
        assert handoffs[0].name == "demo-P01-sample.md"

    def test_excludes_reports_dir(self, sample_project, tmp_path):
        """Test that files under reports_dir are excluded."""
        reports_dir = sample_project.root / sample_project.reports_dir
        reports_dir.mkdir(parents=True, exist_ok=True)
        (reports_dir / "demo-P01-hidden.md").write_text("---\nid: test\n---\n")

        handoffs = frontmatter.discover_handoffs(sample_project)
        names = [h.name for h in handoffs]
        assert "demo-P01-hidden.md" not in names
        assert "demo-P01-sample.md" in names


class TestConvertLegacyHeader:
    """Test convert_legacy_header function."""

    def test_conversion_basic(self):
        """Test basic legacy header conversion."""
        text = textwrap.dedent("""\
            > **Tier:** flash-high
            > **Depends-on:** app-P03 (merged)
            > **Stack:** none

            Original body content.
            """)
        result = frontmatter.convert_legacy_header(text)
        assert result.startswith("---")
        assert "schema_version: 1" in result
        assert "tier: flash-high" in result
        assert "Original body content" in result

    def test_strips_merged_suffix(self):
        """Test that (merged) suffix is stripped from depends-on."""
        text = """> **Depends-on:** P03 (merged), P05 (merged)"""
        result = frontmatter.convert_legacy_header(text)
        assert "P03" in result
        assert "P05" in result
        assert "(merged)" not in result

    def test_serialize_with_becomes_mutexes(self):
        """Test that Serialize-with becomes mutexes."""
        text = "> **Serialize-with:** P02 (shared files: src/)"
        result = frontmatter.convert_legacy_header(text)
        assert "serialize-P02" in result

    def test_converts_none_values(self):
        """Test that none values are handled."""
        text = """> **Depends-on:** none"""
        result = frontmatter.convert_legacy_header(text)
        # Should not have depends_on if it's "none"
        assert "depends_on:" not in result or result.count("depends_on:") == 0

    def test_outputs_must_parse(self, tmp_path):
        """Test that converted output must parse (even if it fails lint)."""
        text = "> **Tier:** flash-high"
        result = frontmatter.convert_legacy_header(text)
        path = tmp_path / "converted.md"
        path.write_text(result)
        # Should parse without error, even if lint fails
        fm, body = frontmatter.parse_handoff(path)
        assert fm is not None
