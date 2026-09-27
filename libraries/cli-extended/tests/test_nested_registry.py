from __future__ import annotations

import argparse
import io

from cli_extended import ArgumentSpec, CliIdentity, CliRegistry, OptionSpec, VerbSpec


def _leaf_cli(seen: list[tuple[str, str | None]]):
    identity = CliIdentity("LEAF", "1.0", "nested leaf CLI", "leaf")
    registry = CliRegistry(
        identity,
        prog="parent leaf",
        description="Nested commands.",
    )
    registry.register(VerbSpec(
        "inspect",
        description="Inspect one object.",
        options=(OptionSpec(("--name",), "object name", parser_kwargs={"required": True}),),
        handler=lambda args, _runtime: seen.append((args.verb, args.name)) or 0,
    ))
    return registry.build()


def _parent_cli(child):
    identity = CliIdentity("PARENT", "1.0", "parent CLI", "parent")
    registry = CliRegistry(identity, prog="parent", description="Parent commands.")
    registry.register(VerbSpec(
        "leaf", description="Run leaf commands.", delegate=child,
    ))
    return registry.build()


def test_delegated_cli_receives_and_validates_the_subcommand_argv():
    seen: list[tuple[str, str | None]] = []
    parent = _parent_cli(_leaf_cli(seen))

    assert parent.run(argv=["leaf", "inspect", "--name", "artifact"]) == 0
    assert seen == [("inspect", "artifact")]


def test_delegated_help_uses_child_registry_for_both_help_forms():
    parent = _parent_cli(_leaf_cli([]))
    for argv in (["leaf", "--help"], ["help", "leaf"]):
        stdout, stderr = io.StringIO(), io.StringIO()
        assert parent.run(argv=argv, stdout=stdout, stderr=stderr) == 0
        assert "LEAF 1.0 — nested leaf CLI" in stdout.getvalue()
        assert "inspect" in stdout.getvalue()
        assert stderr.getvalue() == ""

    stdout, stderr = io.StringIO(), io.StringIO()
    assert parent.run(
        argv=["leaf", "inspect", "--help"], stdout=stdout, stderr=stderr,
    ) == 0
    assert "--name" in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_root_common_options_are_carried_into_a_delegated_registry():
    parent = _parent_cli(_leaf_cli([]))
    stdout, stderr = io.StringIO(), io.StringIO()
    assert parent.run(
        argv=["--log-level", "debug", "leaf", "inspect"],
        stdout=stdout,
        stderr=stderr,
    ) == 2
    assert "the following arguments are required: --name" in stderr.getvalue()
    assert "LEAF 1.0 — nested leaf CLI" in stderr.getvalue()


def test_command_handler_gets_its_unmodified_command_argv():
    seen: list[tuple[str, ...]] = []
    identity = CliIdentity("ROOT", "1.0", "root CLI", "root")
    registry = CliRegistry(identity, prog="root", description="Root commands.")
    registry.register(VerbSpec(
        "run",
        description="Run a command.",
        arguments=(ArgumentSpec(
            "args", "remaining raw arguments", metavar="ARGS",
            parser_kwargs={"nargs": argparse.REMAINDER},
        ),),
        handler=lambda _args, runtime: seen.append(runtime.command_argv) or 0,
    ))
    app = registry.build()

    assert app.run(argv=["--log-level", "debug", "run", "--", "--literal"]) == 0
    assert seen == [("--", "--literal")]


def test_handler_system_exit_preserves_its_status_at_the_shared_boundary():
    identity = CliIdentity("EXIT", "1.0", "exit test", "exit-test")
    registry = CliRegistry(identity, prog="exit-test", description="Exit handling.")
    registry.register(VerbSpec(
        "run",
        description="Return a legacy process status.",
        handler=lambda _args, _runtime: (_ for _ in ()).throw(SystemExit(17)),
    ))

    assert registry.build().run(argv=["run"]) == 17
