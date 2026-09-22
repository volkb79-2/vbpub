from __future__ import annotations

import argparse
import io

import pytest

from cli_extended import (
    CliFailure,
    CliIdentity,
    CliOutput,
    CliRegistry,
    CliRuntime,
    ExtendedArgumentParser,
    HelpCatalog,
    OptionSpec,
    VerbSpec,
    add_common_options,
    run_cli,
)
from cli_extended import parser as parser_module
from cli_extended.parser import UsageError

IDENTITY = CliIdentity("TEST", "1", "Test Tool")


def _parser_with_leading_options():
    parser = ExtendedArgumentParser(prog="tool", identity=IDENTITY)
    parser.add_argument("--switch", action="store_true")
    parser.add_argument("--value")
    parser.add_argument("--maybe", nargs="?")
    parser.add_argument("--pair", nargs=2)
    parser.add_argument("--many", nargs="*")
    return parser


@pytest.mark.parametrize(
    ("argv", "expected"),
    (
        (("--", "status"), None),
        (("status", "--switch"), ["status", "--switch"]),
        (("--help",), None),
        (("--version",), None),
        (("--unknown",), None),
        (("--switch=value",), None),
        (("--value=x", "status"), ["status"]),
        (("--switch", "status"), ["status"]),
        (("--value",), None),
        (("--maybe", "value", "status"), ["status"]),
        (("--maybe", "--switch"), []),
        (("--maybe",), []),
        (("--maybe", "status"), []),
        (("--pair", "one", "two", "status"), ["status"]),
        (("--pair", "one"), None),
        (("--pair", "one", "two"), []),
        (("--many", "status"), None),
        (("--switch",), []),
    ),
)
def test_leading_global_option_scan_handles_argparse_arity(argv, expected):
    assert (
        parser_module._leading_option_remainder(argv, _parser_with_leading_options())
        == expected
    )


def test_common_option_conflict_scan_stops_at_separator_and_handles_missing_value():
    assert parser_module._common_option_conflict(("--log-level",)) is None
    assert parser_module._common_option_conflict(("--quiet", "--", "--debug")) is None
    assert parser_module._common_option_conflict(("--log-level=debug",)) is None
    assert (
        parser_module._common_option_conflict(("--log-level", "debug", "--quiet"))
        == "use only one verbosity control; received --log-level=debug, --quiet"
    )
    assert (
        parser_module._common_option_conflict(("--log-level=", "--quiet"))
        == "use only one verbosity control; received --log-level, --quiet"
    )


def test_common_options_are_idempotent_and_conditional():
    parser = ExtendedArgumentParser(prog="tool", identity=IDENTITY)
    add_common_options(
        parser,
        IDENTITY,
        include_json=False,
        include_progress=False,
        include_confirmation=False,
    )
    count = len(parser._actions)
    add_common_options(parser, IDENTITY)
    assert len(parser._actions) == count
    help_text = parser.format_help()
    assert "--json" not in help_text
    assert "--progress" not in help_text
    assert "--yes" not in help_text

    catalog = parser_module.HelpCatalog(IDENTITY, prog="tool")
    catalog_parser = ExtendedArgumentParser(
        prog="tool", identity=IDENTITY, catalog=catalog, top_level=True
    )
    add_common_options(
        catalog_parser,
        IDENTITY,
        include_json=False,
        include_progress=False,
        include_confirmation=False,
    )
    assert "--json" not in catalog_parser.format_help()


def test_option_spec_registration_preserves_defaults_when_not_suppressed():
    parser = ExtendedArgumentParser(prog="tool", identity=IDENTITY)
    parser_module.CliRegistry._add_option_specs(
        parser,
        (OptionSpec(("--mode",), "mode"),),
    )
    parsed = parser.parse_args([])
    assert hasattr(parsed, "mode")
    assert parsed.mode is None


def test_common_options_keep_their_normal_parse_defaults():
    parser = ExtendedArgumentParser(prog="tool", identity=IDENTITY)
    add_common_options(parser, IDENTITY)

    parsed = parser.parse_args([])
    assert parsed.log_level is None
    assert parsed.quiet is None
    assert parsed.debug is None
    assert parsed.debug_raw is None


def test_registry_and_option_group_validation_edges():
    registry = CliRegistry(IDENTITY, prog="tool", description="test")
    with pytest.raises(ValueError, match="at least one command"):
        registry.build()

    registry = CliRegistry(
        IDENTITY, prog="tool", description="test", single_command=True
    )
    registry.register(VerbSpec("one", description="one", handler=lambda *_: 0))
    assert registry.verbs[0].name == "one"
    with pytest.raises(ValueError, match="already registered"):
        registry.register(VerbSpec("one", description="duplicate"))
    registry.register(VerbSpec("two", description="two", handler=lambda *_: 0))
    with pytest.raises(ValueError, match="exactly one command"):
        registry.build()

    registry = CliRegistry(
        IDENTITY, prog="tool", description="test", no_args_action=True
    )
    registry.register(VerbSpec("one", description="one", handler=lambda *_: 0))
    with pytest.raises(ValueError, match="only valid for a single-command"):
        registry.build()

    registry = CliRegistry(IDENTITY, prog="tool", description="test")
    registry.register(VerbSpec("one", description="one"))
    with pytest.raises(ValueError, match="missing handlers: one"):
        registry.build()

    assert CliFailure("expected refusal").show_help is False

    base = {
        "group": "SOURCE",
        "mutually_exclusive_group": "source",
        "mutually_exclusive_required": True,
    }
    malformed_groups = (
        (OptionSpec(("--one",), "one", **base),),
        (
            OptionSpec(("--one",), "one", **base),
            OptionSpec(
                ("--two",),
                "two",
                group="OTHER",
                mutually_exclusive_group="source",
                mutually_exclusive_required=True,
            ),
        ),
        (
            OptionSpec(("--one",), "one", **base),
            OptionSpec(
                ("--two",),
                "two",
                group="SOURCE",
                mutually_exclusive_group="source",
            ),
        ),
    )
    for options, expected in zip(
        malformed_groups,
        ("at least two options", "one help group", "inconsistent required"),
        strict=True,
    ):
        registry = CliRegistry(
            IDENTITY,
            prog="tool",
            description="test",
            single_command=True,
            global_options=options,
        )
        registry.register(VerbSpec("one", description="one", handler=lambda *_: 0))
        with pytest.raises(ValueError, match=expected):
            registry.build()


def test_help_catalog_is_not_used_by_a_non_top_level_parser():
    catalog = HelpCatalog(
        IDENTITY,
        prog="catalog-tool",
        description="Catalog-only description",
    )
    parser = ExtendedArgumentParser(
        prog="ordinary-parser", identity=IDENTITY, catalog=catalog
    )
    parser.add_argument("--value")
    add_common_options(parser, IDENTITY)

    help_text = parser.format_help()
    assert "usage: ordinary-parser" in help_text
    assert "Catalog-only description" not in help_text
    assert catalog.global_options == ()


def test_registry_single_command_configure_and_runtime_progress_variants():
    registry = CliRegistry(
        IDENTITY,
        prog="tool",
        description="test",
        single_command=True,
        no_args_action=True,
    )
    registry.register(
        VerbSpec(
            "run",
            description="run",
            configure=lambda parser: parser.add_argument("--custom"),
            handler=lambda args, runtime: 0 if args.custom == "yes" else None,
        )
    )
    app = registry.build()
    assert (
        app.run(argv=["--custom", "yes"], stdout=io.StringIO(), stderr=io.StringIO())
        == 0
    )

    output = CliOutput(
        IDENTITY, json_mode=True, stdout=io.StringIO(), stderr=io.StringIO()
    )
    runtime = CliRuntime(IDENTITY, output, json_mode=True)
    assert runtime.progress(mode="plain").mode.value == "plain"
    assert runtime.progress(mode="rawjson").mode.value == "quiet"
    assert runtime.progress(mode=None).mode.value == "plain"


def test_multiverb_confirmation_option_is_only_on_the_mutating_verb():
    registry = CliRegistry(IDENTITY, prog="tool", description="test")
    registry.register(
        VerbSpec(
            "change",
            description="change state",
            group="MODIFICATION",
            mutating=True,
            handler=lambda *_: 0,
        )
    )
    registry.register(
        VerbSpec("inspect", description="inspect state", handler=lambda *_: 0)
    )
    app = registry.build()

    assert "--yes" not in app.parser.format_help()
    assert "--yes" in app.command_parsers["change"].format_help()
    assert "--yes" not in app.command_parsers["inspect"].format_help()


def test_registry_defaults_to_usage_instead_of_a_no_argument_action():
    invoked = []
    registry = CliRegistry(IDENTITY, prog="tool", description="test")
    registry.register(
        VerbSpec(
            "run",
            description="run operation",
            handler=lambda *_: invoked.append(True) or 0,
        )
    )
    app = registry.build()
    stdout = io.StringIO()

    assert app.no_args_action is False
    assert app.run(argv=[], stdout=stdout, stderr=io.StringIO()) == 0
    assert stdout.getvalue().startswith(IDENTITY.headline)
    assert invoked == []


def test_unknown_verb_never_falls_through_to_a_default_handler():
    parser = ExtendedArgumentParser(prog="tool", identity=IDENTITY)
    add_common_options(parser, IDENTITY)
    parser.add_argument("verb", nargs="?")
    invoked = []
    stderr = io.StringIO()

    assert (
        run_cli(
            parser,
            {},
            identity=IDENTITY,
            argv=["unknown"],
            default_handler=lambda *_: invoked.append(True) or 0,
            stderr=stderr,
        )
        == 2
    )
    assert invoked == []
    assert "no handler registered" in stderr.getvalue()


def test_registered_global_option_default_survives_an_omitted_subcommand_copy():
    seen = []
    registry = CliRegistry(
        IDENTITY,
        prog="tool",
        description="test",
        global_options=(
            OptionSpec(("--mode",), "select a mode", parser_kwargs={"default": "safe"}),
        ),
    )
    registry.register(
        VerbSpec(
            "run",
            description="run operation",
            options=(OptionSpec(("--mode",), "select a mode"),),
            handler=lambda args, _runtime: seen.append(args.mode) or 0,
        )
    )
    app = registry.build()

    assert (
        app.run(
            argv=["--mode", "requested", "run"],
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )
        == 0
    )
    assert seen == ["requested"]


def test_verb_without_json_support_runs_without_a_json_option():
    registry = CliRegistry(IDENTITY, prog="tool", description="test")
    registry.register(
        VerbSpec(
            "inspect",
            description="inspect data",
            include_json=False,
            handler=lambda *_: 0,
        )
    )
    app = registry.build()

    assert app.run(argv=["inspect"], stdout=io.StringIO(), stderr=io.StringIO()) == 0


def test_registered_handler_can_run_without_a_generated_command_parser():
    parser = ExtendedArgumentParser(prog="tool", identity=IDENTITY)
    add_common_options(parser, IDENTITY)
    parser.add_argument("verb", choices=("run",))
    assert (
        run_cli(
            parser,
            {"run": lambda *_: 0},
            identity=IDENTITY,
            argv=["run"],
            command_parsers={},
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )
        == 0
    )


def test_parse_time_system_exit_status_is_preserved():
    class ExitAction(argparse.Action):
        def __call__(self, parser, namespace, values, option_string=None):
            raise SystemExit(2)

    registry = CliRegistry(IDENTITY, prog="tool", description="test")
    registry.register(
        VerbSpec(
            "run",
            description="run operation",
            configure=lambda parser: parser.add_argument(
                "--exit", action=ExitAction, nargs=0
            ),
            handler=lambda *_: 0,
        )
    )
    app = registry.build()

    assert (
        app.run(argv=["run", "--exit"], stdout=io.StringIO(), stderr=io.StringIO()) == 2
    )


def test_parse_time_system_exit_nonzero_status_is_not_coerced_to_success():
    class ExitAction(argparse.Action):
        def __call__(self, parser, namespace, values, option_string=None):
            raise SystemExit(7)

    registry = CliRegistry(IDENTITY, prog="tool", description="test")
    registry.register(
        VerbSpec(
            "run",
            description="run operation",
            configure=lambda parser: parser.add_argument(
                "--exit", action=ExitAction, nargs=0
            ),
            handler=lambda *_: 0,
        )
    )
    assert (
        registry.build().run(
            argv=["run", "--exit"], stdout=io.StringIO(), stderr=io.StringIO()
        )
        == 7
    )


def test_parse_time_system_exit_without_status_is_success():
    class ExitAction(argparse.Action):
        def __call__(self, parser, namespace, values, option_string=None):
            raise SystemExit

    registry = CliRegistry(IDENTITY, prog="tool", description="test")
    registry.register(
        VerbSpec(
            "run",
            description="run operation",
            configure=lambda parser: parser.add_argument(
                "--exit", action=ExitAction, nargs=0
            ),
            handler=lambda *_: 0,
        )
    )
    assert (
        registry.build().run(
            argv=["run", "--exit"], stdout=io.StringIO(), stderr=io.StringIO()
        )
        == 0
    )


def test_registered_cli_no_args_action_defaults_to_false():
    registered = parser_module.RegisteredCli(
        IDENTITY,
        ExtendedArgumentParser(prog="tool", identity=IDENTITY),
        {},
        {},
    )
    assert registered.no_args_action is False


def _simple_cli(handler):
    registry = CliRegistry(IDENTITY, prog="tool", description="test")
    registry.register(VerbSpec("run", description="run", handler=handler))
    return registry.build()


def test_run_cli_reports_missing_verb_and_missing_dispatch_handler():
    parser = ExtendedArgumentParser(prog="tool", identity=IDENTITY)
    add_common_options(parser, IDENTITY)
    stderr = io.StringIO()
    assert run_cli(parser, {}, identity=IDENTITY, argv=["--json"], stderr=stderr) == 2
    assert "a verb is required" in stderr.getvalue()

    parser = ExtendedArgumentParser(prog="tool", identity=IDENTITY)
    add_common_options(parser, IDENTITY)
    parser.add_subparsers(dest="verb").add_parser("ghost")
    stderr = io.StringIO()
    assert run_cli(parser, {}, identity=IDENTITY, argv=["ghost"], stderr=stderr) == 2
    assert "no handler registered" in stderr.getvalue()


def test_default_runtime_does_not_preaccept_confirmation():
    output = CliOutput(
        IDENTITY,
        stdin=io.StringIO(),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    runtime = parser_module.CliRuntime(IDENTITY, output)

    assert runtime.yes is False
    assert runtime.debug is False
    assert runtime.debug_raw is False
    assert runtime.json_mode is False
    with pytest.raises(CliFailure, match="confirmation is required"):
        runtime.confirm("Make a change?")


def test_runtime_defaults_are_safe_when_optional_attributes_are_absent():
    runtime = parser_module._runtime_from_args(
        argparse.Namespace(),
        IDENTITY,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        stdin=io.StringIO(),
        secrets=(),
    )
    assert runtime.debug is False
    assert runtime.debug_raw is False
    assert runtime.json_mode is False
    assert runtime.output.level is parser_module.LogLevel.INFO


def test_default_cli_invocation_builds_a_non_debug_non_json_runtime():
    seen = []
    app = _simple_cli(
        lambda _args, runtime: (
            seen.append(
                (
                    runtime.debug,
                    runtime.debug_raw,
                    runtime.json_mode,
                    runtime.output.level,
                    runtime.progress_mode,
                )
            )
            or 0
        )
    )

    assert app.run(argv=["run"], stdout=io.StringIO(), stderr=io.StringIO()) == 0
    assert seen == [
        (
            False,
            False,
            False,
            parser_module.LogLevel.INFO,
            parser_module.ProgressMode.AUTO,
        )
    ]


def test_runtime_creation_and_expected_failures_are_reported_without_tracebacks(
    monkeypatch,
):
    stderr = io.StringIO()

    def fail_runtime(*_args, **_kwargs):
        raise CliFailure("runtime setup refused", hint="check settings")

    monkeypatch.setattr(parser_module, "_runtime_from_args", fail_runtime)
    app = _simple_cli(lambda *_: 0)
    assert app.run(argv=["run"], stderr=stderr) == 1
    assert "runtime setup refused" in stderr.getvalue()
    assert "Hint: check settings" in stderr.getvalue()

    stderr = io.StringIO()
    monkeypatch.setattr(
        parser_module,
        "_runtime_from_args",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            CliFailure("show help", show_help=True)
        ),
    )
    assert app.run(argv=["run"], stderr=stderr) == 1
    assert "show help" in stderr.getvalue()
    assert "usage: tool run" in stderr.getvalue()

    monkeypatch.setattr(parser_module, "_runtime_from_args", None)


def test_runtime_explicit_log_level_and_contradictory_debug_raw_options():
    app = _simple_cli(lambda _args, runtime: runtime.output.info("ran") or 0)
    stderr = io.StringIO()
    assert (
        app.run(
            argv=["run", "--log-level", "debug"],
            stderr=stderr,
            stdout=io.StringIO(),
        )
        == 0
    )
    assert "[INFO] ran" in stderr.getvalue()

    stderr = io.StringIO()
    assert (
        app.run(
            argv=["run", "--quiet", "--debug-raw"],
            stderr=stderr,
            stdout=io.StringIO(),
        )
        == 2
    )
    assert "--quiet and --debug-raw are contradictory" in stderr.getvalue()
    assert "usage: tool run" in stderr.getvalue()

    stderr = io.StringIO()
    assert (
        app.run(
            argv=["run", "--debug-raw", "--log-level", "error"],
            stderr=stderr,
            stdout=io.StringIO(),
        )
        == 2
    )
    assert "implies --log-level=debug" in stderr.getvalue()
    assert "usage: tool run" in stderr.getvalue()


@pytest.mark.parametrize("debug", (False, True))
def test_runtime_expected_exception_paths_follow_debug_policy(monkeypatch, debug):
    from cli_extended.parser import _runtime_from_args

    def fail(*_args, **_kwargs):
        raise ValueError("expected settings refusal")

    app = _simple_cli(fail)
    stderr = io.StringIO()
    result = app.run(
        argv=["run", *(["--debug"] if debug else [])],
        stderr=stderr,
        stdout=io.StringIO(),
        expected_exceptions=(ValueError,),
    )
    assert result == 1
    assert "expected settings refusal" in stderr.getvalue()
    assert ("handled ValueError" in stderr.getvalue()) is debug
    monkeypatch.setattr(parser_module, "_runtime_from_args", _runtime_from_args)


def test_pre_runtime_expected_exception_and_keyboard_interrupt(monkeypatch):
    app = _simple_cli(lambda *_: 0)

    def expected_failure(*_args, **_kwargs):
        raise ValueError("pre-runtime failure")

    monkeypatch.setattr(parser_module, "_runtime_from_args", expected_failure)
    stderr = io.StringIO()
    assert app.run(argv=["run"], stderr=stderr, expected_exceptions=(ValueError,)) == 1
    assert "pre-runtime failure" in stderr.getvalue()

    def interrupted(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(parser_module, "_runtime_from_args", interrupted)
    stderr = io.StringIO()
    assert app.run(argv=["run"], stderr=stderr) == 130
    assert "Cancelled." in stderr.getvalue()
    assert "Traceback" not in stderr.getvalue()


def test_cli_failure_debug_detail_and_unexpected_exception_propagation():
    app = _simple_cli(lambda *_: (_ for _ in ()).throw(CliFailure("expected refusal")))
    stderr = io.StringIO()
    assert app.run(argv=["run", "--debug"], stderr=stderr) == 1
    assert "handled CLI refusal in run" in stderr.getvalue()

    app = _simple_cli(
        lambda *_: (_ for _ in ()).throw(
            CliFailure("help refusal", show_help=True, hint="read first")
        )
    )
    stderr = io.StringIO()
    assert app.run(argv=["run", "--debug"], stderr=stderr) == 1
    assert "Hint: read first" in stderr.getvalue()
    assert "handled CLI refusal in run" in stderr.getvalue()

    app = _simple_cli(lambda *_: (_ for _ in ()).throw(RuntimeError("programming bug")))
    with pytest.raises(RuntimeError, match="programming bug"):
        app.run(argv=["run"], stderr=io.StringIO())


def test_help_color_scanner_ignores_separators_and_unrelated_tokens():
    assert parser_module._help_color_setting(("--", "--color")) is None
    assert parser_module._help_color_setting(("unexpected", "--color")) is True
    assert parser_module._help_color_setting(("--color", "--no-color")) is False


def test_usage_error_render_and_common_option_declarations():
    parser = ExtendedArgumentParser(prog="tool", identity=IDENTITY)
    assert (
        UsageError("bad option", parser)
        .render()
        .startswith("[ERROR] tool: bad option\n\nTEST 1 — Test Tool")
    )

    options = parser_module._common_option_specs(
        include_json=False,
        include_progress=False,
        include_confirmation=False,
    )
    assert {option.flags for option in options}.isdisjoint(
        {("--json",), ("--progress",), ("--yes",)}
    )
