"""Focused S4 contracts for invalid inline directive options."""

import pytest

from ciu.secrets.directives import parse_value


def test_inline_directive_must_be_string() -> None:
    with pytest.raises(ValueError, match=r"\[S4\.4\].*'directive'.*string"):
        parse_value("token", {"directive": 42}, "stack.secrets")


def test_ask_vault_rejects_empty_field_selector() -> None:
    with pytest.raises(ValueError, match=r"\[S4\.2\].*Empty.*#field"):
        parse_value("token", "ASK_VAULT:secret/path#", "stack.secrets")


def test_non_ask_vault_rejects_field_selector() -> None:
    with pytest.raises(ValueError, match=r"\[S4\.2\].*#field.*only valid for ASK_VAULT"):
        parse_value("token", "ASK_FILE:secret/path#password", "stack.secrets")


def test_inline_uid_must_be_integer() -> None:
    with pytest.raises(ValueError, match=r"\[S4\.4\].*'uid'.*integer"):
        parse_value(
            "token",
            {"directive": "ASK_VAULT:secret/path", "uid": "1000"},
            "stack.secrets",
        )
