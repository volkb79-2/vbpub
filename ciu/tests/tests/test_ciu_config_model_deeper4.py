"""Additional behavioural contracts for CIU's template-render input boundary."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from jinja2 import TemplateError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.config_model import (  # noqa: E402
    expand_env_vars_or_fail,
    render_toml_template,
    scan_override_for_secrets,
)


def test_env_expansion_preserves_literal_dollar_values_and_toml_line_endings(monkeypatch):
    """S3.2 substitutes template tokens once without treating value data as syntax.

    A password/credential supplied by the environment may legitimately contain
    ``$NAME``.  CIU must write it verbatim rather than attempting a second,
    undocumented round of expansion.  The renderer also preserves the input's
    CRLF and CR records so TOML passed to the next pipeline stage is unchanged
    apart from substitutions.
    """
    monkeypatch.setenv("PASSWORD", "literal$not_a_placeholder")
    monkeypatch.setenv("HOST", "db.internal")

    rendered = expand_env_vars_or_fail(
        'password = "$PASSWORD"\r\nhost = "$HOST"\r# $IGNORED_COMMENT\r\n',
        "stack/ciu.defaults.toml.j2",
    )

    assert rendered == (
        'password = "literal$not_a_placeholder"\r\n'
        'host = "db.internal"\r# $IGNORED_COMMENT\r\n'
    )


def test_env_expansion_handles_escaped_quote_before_hash(monkeypatch):
    """S3.2 does not mistake a hash after an escaped quote for a TOML comment."""
    monkeypatch.setenv("LABEL", "blue")
    monkeypatch.delenv("COMMENT_ONLY", raising=False)

    rendered = expand_env_vars_or_fail(
        'label = "quote: \\"; #$LABEL" # $COMMENT_ONLY\n',
        "stack/ciu.defaults.toml.j2",
    )

    assert rendered == 'label = "quote: \\"; #blue" # $COMMENT_ONLY\n'


def test_sensitive_path_reference_is_not_a_hardcoded_credential():
    """S3.1a permits a long Vault/file path while rejecting literal secrets."""
    scan_override_for_secrets(
        'private_key = "vault/transit/production/controller/private-key"\n',
        "ciu.global.toml.j2",
    )


def test_template_syntax_error_names_the_template_path(tmp_path):
    """S3.2 wraps Jinja failures with the file that needs repairing."""
    template = tmp_path / "broken.toml.j2"
    template.write_text("[service]\nname = {{ unclosed\n", encoding="utf-8")

    with pytest.raises(TemplateError) as exc_info:
        render_toml_template(template, {})

    assert str(template) in str(exc_info.value)
