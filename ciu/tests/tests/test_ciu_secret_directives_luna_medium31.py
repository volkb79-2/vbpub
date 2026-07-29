"""Focused S4 contract for rejecting scalar secret declarations."""

import pytest

from ciu.secrets.directives import parse_value


def test_secret_value_must_be_string_or_inline_table() -> None:
    with pytest.raises(
        ValueError,
        match=r"\[S4\.4\].*must be a string or inline table.*'int'",
    ):
        parse_value("token", 42, "stack.secrets")
