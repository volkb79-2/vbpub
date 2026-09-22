from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from cli_extended import CliIdentity


@pytest.mark.parametrize("field", ("name", "version", "long_name"))
@pytest.mark.parametrize("value", ("", "two\nlines", "two\rlines"))
def test_required_identity_fields_are_nonempty_single_lines(field, value):
    values = {"name": "TOOL", "version": "1.2.3", "long_name": "Tool"}
    values[field] = value
    with pytest.raises(ValueError, match="single line"):
        CliIdentity(**values)


@pytest.mark.parametrize("command", ("", "two\nlines", "two\rlines"))
def test_explicit_command_must_be_a_nonempty_single_line(command):
    with pytest.raises(ValueError, match="identity command"):
        CliIdentity("TOOL", "1", "Tool", command=command)


def test_identity_derived_command_and_exact_display_lines():
    default_command = CliIdentity("TOOL", "1.2.3", "Tool")
    explicit_command = CliIdentity("TOOL", "1.2.3", "Tool", command="tool-cli")

    assert default_command.command_name == "tool"
    assert default_command.headline == "TOOL 1.2.3 — Tool"
    assert default_command.version_line == "tool 1.2.3"
    assert explicit_command.command_name == "tool-cli"
    assert explicit_command.version_line == "tool-cli 1.2.3"


def test_identity_is_immutable():
    identity = CliIdentity("TOOL", "1.2.3", "Tool")
    with pytest.raises(FrozenInstanceError):
        identity.version = "9.9.9"
