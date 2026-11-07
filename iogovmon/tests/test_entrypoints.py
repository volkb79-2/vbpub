"""The daemon and operator CLIs expose disjoint command sets."""

from __future__ import annotations

import argparse
import re
import tomllib
from pathlib import Path

import pytest

from iogovmon import cli


DAEMON_COMMANDS = {"tick", "watch", "bench", "cleanup"}
CONTROL_COMMANDS = {
    "plan", "analyze", "status", "preflight", "devices", "annotate", "note",
    "quiet-experiment", "prune", "config",
}
COMMAND_ARGS = {
    "annotate": ["annotate", "test note"],
    "note": ["note", "test note"],
    "quiet-experiment": ["quiet-experiment", "--knob", "nr_requests", "--value", "8"],
}


def _commands(parser: argparse.ArgumentParser) -> set[str]:
    subparsers = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
    return set(subparsers.choices)


def test_distribution_has_only_the_two_named_console_scripts():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    scripts = tomllib.loads(pyproject.read_text())["project"]["scripts"]
    assert scripts == {
        "iogovmond": "iogovmon.cli:daemon_main",
        "iogovmonctl": "iogovmon.cli:control_main",
    }
    assert "iogovmon" not in scripts


def test_command_mapping_is_documented_for_adopters_and_in_design_notes():
    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text()
    changes = (root / "CHANGES.md").read_text()
    consumers = (root / "docs" / "CONSUMERS.md").read_text()
    design = (root / "docs" / "DESIGN-GUIDE.md").read_text()
    for binary, commands in (("iogovmond", DAEMON_COMMANDS), ("iogovmonctl", CONTROL_COMMANDS)):
        for doc in (readme, changes):
            row = next(line for line in doc.splitlines() if line.strip().startswith(f"| `{binary}` |"))
            assert set(re.findall(r"`([^`]+)`", row.split("|")[2])) == commands
    assert "iogovmond --version" in consumers and "iogovmonctl --version" in consumers
    assert "One console script for services and operators" in design
    assert "`python -m iogovmon` remains a combined developer entry point" in readme


def test_each_existing_subcommand_is_reachable_from_exactly_one_binary():
    daemon_parser = cli.make_daemon_parser()
    control_parser = cli.make_control_parser()
    assert _commands(daemon_parser) == DAEMON_COMMANDS
    assert _commands(control_parser) == CONTROL_COMMANDS

    for command in DAEMON_COMMANDS | CONTROL_COMMANDS:
        argv = COMMAND_ARGS.get(command, [command])
        daemon_accepts = daemon_parser.parse_args(argv).cmd == command if command in _commands(daemon_parser) else False
        control_accepts = control_parser.parse_args(argv).cmd == command if command in _commands(control_parser) else False
        assert daemon_accepts != control_accepts, command

    with pytest.raises(SystemExit):
        daemon_parser.parse_args(["status"])
    with pytest.raises(SystemExit):
        control_parser.parse_args(["tick"])


@pytest.mark.parametrize(
    ("entrypoint", "program"),
    [(cli.daemon_main, "iogovmond"), (cli.control_main, "iogovmonctl")],
)
def test_both_entrypoints_have_their_own_version(entrypoint, program, capsys):
    with pytest.raises(SystemExit) as exc:
        entrypoint(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.startswith(f"{program} ")


def test_python_module_keeps_the_unified_developer_command_set():
    assert _commands(cli.make_parser()) == DAEMON_COMMANDS | CONTROL_COMMANDS
