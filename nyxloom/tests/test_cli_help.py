"""Generated help, version identity, and side-effect contracts for CLI registries."""

from __future__ import annotations

import re

import pytest

from nyxloom import paths
from nyxloom.cli import main as nyxloom_main
from nyxloom.cli_ctl import main as ctl_main
from nyxloom.cli_harness import main as harness_main
from nyxloom.cli_registry import harness_cli, operator_cli, primary_cli


@pytest.mark.parametrize(
    ("factory", "entrypoint", "command", "root_verbs"),
    [
        (primary_cli, nyxloom_main, "nyxloom", {"lint", "init", "onboard", "backlog"}),
        (harness_cli, harness_main, "nyxloom-harness", {"extract", "extract-lossless", "extract-debug", "extract-report", "extract-sessions"}),
        (operator_cli, ctl_main, "nyxloomctl", {"project", "lint", "doctor", "status", "resync", "daemon", "auth", "events"}),
    ],
)
def test_registry_catalog_matches_parser_and_version_is_cli_specific(
    factory, entrypoint, command, root_verbs, capsys
):
    registered = factory()
    registered.catalog.validate_parser(registered.parser)
    names = {verb.name for verb in registered.catalog.verbs}
    assert root_verbs <= names

    assert entrypoint(["--version"]) == 0
    assert capsys.readouterr().out.startswith(f"{command} ")
    assert entrypoint(["version"]) == 0
    assert capsys.readouterr().out.startswith(f"{command} ")

    assert entrypoint(["--help"]) == 0
    help_text = capsys.readouterr().out
    assert f"Usage: {command} <verb> [options]" in help_text
    assert "--help" in help_text
    assert not re.search(r"(?<!\S)-h(?!\S)", help_text)


def test_nested_help_comes_from_delegated_registries(capsys):
    assert nyxloom_main(["help", "backlog"]) == 0
    output = capsys.readouterr().out
    assert "Manage project-local managed backlog entries" in output
    assert "edit" in output and "new" in output

    assert ctl_main(["help", "project"]) == 0
    output = capsys.readouterr().out
    assert "Manage the host project registry" in output
    assert "add" in output and "list" in output

    assert harness_main(["extract", "--help"]) == 0
    output = capsys.readouterr().out
    assert "usage: nyxloom-harness extract" in output
    assert "--gap-marker" in output


def test_moved_paths_have_one_owner_and_no_forwarding_aliases(capsys):
    assert nyxloom_main(["doctor"]) == 2
    assert "doctor" in capsys.readouterr().err
    assert nyxloom_main(["extract", "session.jsonl"]) == 2
    capsys.readouterr()
    assert ctl_main(["extract", "session.jsonl"]) == 2
    capsys.readouterr()
    assert harness_main(["doctor"]) == 2
    capsys.readouterr()

    assert ctl_main(["doctor", "--help"]) == 0
    assert "usage: nyxloomctl doctor" in capsys.readouterr().out
    assert harness_main(["extract", "--help"]) == 0
    assert "usage: nyxloom-harness extract" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("entrypoint", "argv"),
    [
        (nyxloom_main, ["--help"]),
        (nyxloom_main, ["--version"]),
        (nyxloom_main, ["init"]),
        (ctl_main, ["--help"]),
        (ctl_main, ["--version"]),
        (ctl_main, ["doctor", "--not-an-option"]),
        (harness_main, ["--help"]),
        (harness_main, ["--version"]),
        (harness_main, ["extract"]),
    ],
)
def test_help_version_and_usage_errors_do_not_create_host_log_files(
    entrypoint, argv, tmp_state
):
    log_path = paths.nyxloom_log_path()
    assert not log_path.exists()
    assert entrypoint(argv) in {0, 2}
    assert not log_path.exists()


def test_short_help_alias_is_removed_and_common_verbosity_conflicts_are_rejected(capsys):
    assert nyxloom_main(["-h"]) == 2
    capsys.readouterr()
    assert harness_main(["-h"]) == 2
    capsys.readouterr()
    assert ctl_main(["-h"]) == 2
    capsys.readouterr()

    assert ctl_main(["status", "--debug", "--log-level", "warn"]) == 2
    assert "verbosity control" in capsys.readouterr().err


def test_removed_and_prohibited_syntax_is_rejected(capsys):
    assert harness_main(["extract-lossless", "session.jsonl", "--redact-pattern", "secret"]) == 2
    capsys.readouterr()
    assert ctl_main(["doctor", "--write"]) == 2
    assert "requires --rebuild" in capsys.readouterr().err
    assert ctl_main(["doctor", "--liveness", "--rebuild"]) == 2
    assert "cannot be combined" in capsys.readouterr().err
    assert ctl_main(["resync", "demo", "--apply-content-merges"]) == 2
    assert "requires --apply" in capsys.readouterr().err
    assert ctl_main(["capability-map", "refresh", "--dry-run", "--emit-findings", "demo"]) == 2
    assert "cannot be combined" in capsys.readouterr().err


def test_primary_local_commands_do_not_advertise_host_write_controls(capsys):
    assert nyxloom_main(["backlog", "list", "--help"]) == 0
    output = capsys.readouterr().out
    assert "--yes" not in output
    assert "--write" not in output
    assert "mutating" not in output.lower()
