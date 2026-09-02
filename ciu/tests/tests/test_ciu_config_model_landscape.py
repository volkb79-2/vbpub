"""
Tests for CIU-36 — [deploy].landscape_id as a validated first-class identity key.

Normative contract: docs/SPEC.md S3.11, docs/CONFIG.md [deploy].

Covers:
- O1: validation on the FINAL merged global config — a valid slug passes; an
  invalid (or non-string) value fails with a tagged error naming the key and
  the pattern. Validation must run once on the merged value, never per chain
  directory, so a later layer that corrects an earlier bad value is honored,
  and the worktree overlay's value is covered too.
- O2: the existing context plumbing needs no change — a stack TOML template
  referencing {{ deploy.landscape_id }} receives the declared value through
  _make_render_context + render_toml_template.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.config_model import (  # noqa: E402
    _make_render_context,
    render_global_chain,
    render_toml_template,
)

LANDSCAPE_PATTERN = "^[a-z][a-z0-9-]{0,62}$"


def _write_global_defaults(directory: Path, content: str) -> None:
    (directory / "ciu.global.defaults.toml.j2").write_text(content, encoding="utf-8")


def _write_global_overrides(directory: Path, content: str) -> None:
    (directory / "ciu.global.toml.j2").write_text(content, encoding="utf-8")


def _assert_landscape_error(exc: ValueError) -> None:
    message = str(exc)
    assert "S3.11" in message
    assert "landscape_id" in message
    assert LANDSCAPE_PATTERN in message


def test_landscape_id_valid_slug_passes(tmp_path):
    """A DNS-label-safe slug is accepted and reaches the merged config."""
    _write_global_defaults(tmp_path, '[deploy]\nlandscape_id = "prod-eu"\n')
    result = render_global_chain(tmp_path, tmp_path, write_rendered=False)
    assert result["deploy"]["landscape_id"] == "prod-eu"


def test_landscape_id_invalid_slug_fails_naming_key_and_pattern(tmp_path):
    """Uppercase violates the grammar; the tagged error names key and pattern."""
    _write_global_defaults(tmp_path, '[deploy]\nlandscape_id = "Prod-EU"\n')
    with pytest.raises(ValueError) as exc:
        render_global_chain(tmp_path, tmp_path, write_rendered=False)
    _assert_landscape_error(exc.value)


def test_landscape_id_non_string_fails(tmp_path):
    """A non-string value cannot match the slug grammar and must abort."""
    _write_global_defaults(tmp_path, "[deploy]\nlandscape_id = 123\n")
    with pytest.raises(ValueError) as exc:
        render_global_chain(tmp_path, tmp_path, write_rendered=False)
    _assert_landscape_error(exc.value)


def test_landscape_id_validated_on_final_merged_value_not_per_layer(tmp_path):
    """Validation runs once on the merged config, not per chain directory:
    an invalid value in an EARLY layer corrected by a later layer must pass."""
    _write_global_defaults(tmp_path, '[deploy]\nlandscape_id = "Prod-EU"\n')
    stack = tmp_path / "infra" / "api"
    stack.mkdir(parents=True)
    _write_global_defaults(stack, '[deploy]\nlandscape_id = "prod-eu"\n')
    result = render_global_chain(stack, tmp_path, write_rendered=False)
    assert result["deploy"]["landscape_id"] == "prod-eu"


def test_landscape_id_worktree_overlay_value_is_validated(tmp_path):
    """The worktree overlay merges LAST, so its landscape_id is part of the
    final merged config and is validated too."""
    _write_global_defaults(tmp_path, '[deploy]\nlandscape_id = "prod-eu"\n')
    (tmp_path / "ciu.global.instance.toml.j2").write_text(
        '[deploy]\nlandscape_id = "prod_eu"\n', encoding="utf-8"
    )
    with pytest.raises(ValueError) as exc:
        render_global_chain(tmp_path, tmp_path, write_rendered=False)
    _assert_landscape_error(exc.value)


def test_landscape_id_reaches_stack_template_through_existing_context(tmp_path):
    """O2: {{ deploy.landscape_id }} reaches a stack TOML template through the
    EXISTING context plumbing (_make_render_context + render_toml_template) —
    proving no plumbing change is needed and pinning it against regression."""
    _write_global_defaults(tmp_path, '[deploy]\nlandscape_id = "prod-eu"\n')
    stack = tmp_path / "infra" / "vault"
    stack.mkdir(parents=True)
    defaults_path = stack / "ciu.defaults.toml.j2"
    defaults_path.write_text(
        '[vault]\nkv_root = "dstdns/{{ deploy.landscape_id }}"\n',
        encoding="utf-8",
    )
    global_config = render_global_chain(tmp_path, tmp_path, write_rendered=False)
    stack_config = render_toml_template(
        defaults_path, _make_render_context(global_config)
    )
    assert stack_config["vault"]["kv_root"] == "dstdns/prod-eu"
