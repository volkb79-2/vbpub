"""The CLI shell and the module entry point: identity, dispatch, and the two exit paths.

No test here reads a source: the estate handler is replaced by a stub, so this module pins
the shell's wiring only.
"""

from __future__ import annotations

import runpy
import sys

import pytest
from cli_extended import CliIdentity, VersionLookupError

from agctl import cli as agcli

LONG_NAME = "agent control: controller state and the estate view"


def test_the_identity_is_the_installed_distribution_and_never_a_literal(monkeypatch):
    seen: dict[str, object] = {}

    def fake_resolve(**kwargs):
        seen.update(kwargs)
        return CliIdentity("AGCTL", "4.5.6", LONG_NAME, "agctl")

    monkeypatch.setattr(agcli.CliIdentity, "resolve", fake_resolve)

    identity = agcli.identity()

    assert identity.version == "4.5.6"
    # The distribution name is the one the package is installed under; there is no literal
    # version fallback anywhere on this path.
    assert seen["distribution"] == "agctl"
    assert seen["command"] == "agctl"
    assert seen["long_name"] == LONG_NAME


def test_the_estate_verb_dispatches_to_the_reader(monkeypatch):
    called: list[object] = []
    monkeypatch.setattr("agctl.estate.run", lambda args, runtime: called.append((args, runtime)) or 0)

    assert agcli.build_cli(CliIdentity("AGCTL", "1.0.0", LONG_NAME, "agctl")).run(argv=["estate", "--header"]) == 0

    (args, runtime) = called[0]
    assert args.header is True
    assert runtime.json_mode is False


def test_the_estate_handler_wraps_the_reader_in_a_function():
    # Registration refuses a missing handler; the wrapper exists so the reader is imported only
    # when the verb is actually invoked (a broken reader cannot break `--help`).
    assert agcli._estate.__name__ == "_estate"


def test_main_runs_the_registered_cli(capsys):
    assert agcli.main(["--version"]) == 0
    assert "agctl" in capsys.readouterr().out.lower()


def test_main_reports_a_missing_distribution_as_one_line_and_exit_two(monkeypatch, capsys):
    def refuse(**kwargs):
        raise VersionLookupError("agctl is not installed under this name")

    monkeypatch.setattr(agcli.CliIdentity, "resolve", refuse)

    assert agcli.main(["--version"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("[ERROR] agctl:")
    assert "not installed" in err


def test_the_module_entry_point_exits_through_system_exit(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["agctl", "--version"])
    with pytest.raises(SystemExit) as raised:
        runpy.run_module("agctl.__main__", run_name="__main__")

    assert raised.value.code == 0


# --- the CLI boundary (help, version, usage, registration) ------------------------------------


def test_help_names_the_estate_verb(capsys):
    assert agcli.build_cli(_ident()).run(argv=["--help"]) == 0
    assert "estate" in capsys.readouterr().out


def test_version_reports_the_identity_and_never_a_literal(capsys):
    assert agcli.build_cli(_ident("1.2.3")).run(argv=["--version"]) == 0
    assert "agctl 1.2.3" in capsys.readouterr().out


def test_an_unknown_verb_is_a_usage_error(capsys):
    assert agcli.build_cli(_ident()).run(argv=["estate-of-the-world"]) == 2
    assert "agctl" in capsys.readouterr().err


def test_the_estate_verb_is_registered_with_the_read_only_options_it_declares(capsys):
    app = agcli.build_cli(_ident())
    assert app.run(argv=["estate", "--help"]) == 0
    out = capsys.readouterr().out
    assert "--json" in out
    assert "--header" in out


def test_the_estate_header_flag_is_not_a_mutating_option():
    verb = next(verb for verb in agcli.build_cli(_ident()).registered_verbs if verb.name == "estate")
    flags = [flag for option in verb.options for flag in option.flags]

    assert "--header" in flags
    assert verb.mutating is False
    assert verb.include_progress is False


def test_a_source_tree_without_the_installed_distribution_refuses_loudly():
    # A distribution name that is never installed exercises the refusal path of `main()`, not
    # the happy path, and there is deliberately no literal fallback version.
    with pytest.raises(VersionLookupError, match="not-installed-under-this-name"):
        CliIdentity.resolve(
            name="AGCTL",
            long_name=LONG_NAME,
            command="agctl",
            distribution="agctl-not-installed-under-this-name",
        )


def _ident(version: str = "9.9.9") -> CliIdentity:
    return CliIdentity("AGCTL", version, LONG_NAME, "agctl")


def test_the_cli_module_entry_point_guard_runs(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["agctl", "--version"])
    with pytest.raises(SystemExit) as raised:
        runpy.run_module("agctl.cli", run_name="__main__")

    assert raised.value.code == 0
