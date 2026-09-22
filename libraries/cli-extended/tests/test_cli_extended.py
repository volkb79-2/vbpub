from __future__ import annotations

import argparse
import io
import logging
import os
from types import SimpleNamespace

import pytest
from cli_extended import (
    ArgumentSpec,
    CliFailure,
    CliIdentity,
    CliOutput,
    CliRegistry,
    ExtendedArgumentParser,
    HelpCatalog,
    LogLevel,
    OptionSpec,
    ProgressMode,
    ProgressRenderer,
    VerbSpec,
    VersionLookupError,
    add_common_options,
    assert_cli_contract,
    install_logging,
    logging_context,
    run_cli,
    uninstall_logging,
)
from cli_extended import identity as identity_module
from cli_extended import parser as parser_module

IDENTITY = CliIdentity(
    name="TEST",
    command="test-tool",
    version="1.2.3",
    long_name="Test Operator Tool",
)


def make_cli():
    catalog = HelpCatalog(
        IDENTITY,
        prog="test-tool",
        getting_started=("test-tool status",),
        verbs=(
            VerbSpec("status", "", "show state", group="EXPLORATION"),
            VerbSpec("apply", "FILE", "apply a change", group="MODIFICATION"),
        ),
        width=100,
    )
    parser = ExtendedArgumentParser(
        prog="test-tool",
        identity=IDENTITY,
        catalog=catalog,
        top_level=True,
    )
    add_common_options(parser, IDENTITY)
    subparsers = parser.add_subparsers(dest="verb", required=True)
    status = subparsers.add_parser("status", description="show state")
    add_common_options(status, IDENTITY, suppress_defaults=True)
    apply = subparsers.add_parser("apply", description="apply a change")
    apply.add_argument("file")
    add_common_options(apply, IDENTITY, suppress_defaults=True)
    return parser, {"status": status, "apply": apply}


def handler(args: argparse.Namespace, runtime):
    if args.verb == "status":
        runtime.output.primary({"state": "ready"} if runtime.json_mode else "ready")
        return 0
    runtime.output.info(f"applying {args.file}")
    return 0


def test_identity_lines_and_version_output():
    parser, command_parsers = make_cli()
    stdout, stderr = io.StringIO(), io.StringIO()
    assert (
        run_cli(
            parser,
            {"status": handler, "apply": handler},
            identity=IDENTITY,
            argv=[],
            command_parsers=command_parsers,
            stdout=stdout,
            stderr=stderr,
        )
        == 0
    )
    assert stdout.getvalue().startswith("TEST 1.2.3 — Test Operator Tool\n")
    assert (
        run_cli(
            parser,
            {},
            identity=IDENTITY,
            argv=["version"],
            stdout=stdout,
            stderr=stderr,
        )
        == 0
    )
    assert stdout.getvalue().endswith("test-tool 1.2.3\n")
    stdout.seek(0)
    stdout.truncate(0)
    assert (
        run_cli(
            parser,
            {},
            identity=IDENTITY,
            argv=["--version"],
            command_parsers=command_parsers,
            stdout=stdout,
            stderr=stderr,
        )
        == 0
    )
    assert stdout.getvalue() == "test-tool 1.2.3\n"


def test_help_and_version_verbs_follow_leading_global_options():
    app = _registered_cli()

    stdout, stderr = io.StringIO(), io.StringIO()
    assert app.run(argv=["--debug", "version"], stdout=stdout, stderr=stderr) == 0
    assert stdout.getvalue() == IDENTITY.version_line + "\n"
    assert stderr.getvalue() == ""

    stdout, stderr = io.StringIO(), io.StringIO()
    assert (
        app.run(
            argv=["--config", "settings.json", "help", "status"],
            stdout=stdout,
            stderr=stderr,
        )
        == 0
    )
    assert stdout.getvalue() == app.command_parsers["status"].format_help()
    assert stderr.getvalue() == ""


@pytest.mark.parametrize("name", ("help", "version"))
def test_registry_reserves_help_and_version_verbs(name):
    registry = CliRegistry(IDENTITY, prog="test-tool", description="test CLI")
    with pytest.raises(ValueError, match="reserved for standard CLI behavior"):
        registry.register(VerbSpec(name, "", "reserved", handler=lambda *_: 0))


@pytest.mark.parametrize(
    "argv",
    (
        ("--quiet", "status", "--debug"),
        ("--no-color", "status", "--color"),
        ("--log-level", "debug", "status", "--quiet"),
        ("--quiet", "status", "--quiet"),
        ("--json", "apply", "change.json", "--yes", "--color", "--no-color"),
    ),
)
def test_common_option_conflicts_are_rejected_across_parser_levels(argv):
    app = _registered_cli()
    stderr = io.StringIO()
    assert app.run(argv=argv, stdout=io.StringIO(), stderr=stderr) == 2
    assert IDENTITY.headline in stderr.getvalue()
    assert "[ERROR]" in stderr.getvalue()
    assert any(
        message in stderr.getvalue()
        for message in ("mutually exclusive", "not allowed with", "use only one")
    )
    assert "usage:" in stderr.getvalue()


def test_identity_distribution_version_is_authoritative_and_missing_is_not_invented(
    monkeypatch,
):
    monkeypatch.setattr(identity_module, "installed_version", lambda name: "9.8.7")
    derived = CliIdentity.from_distribution(
        name="TOOL", distribution="tool-package", long_name="Tool"
    )
    assert derived.version == "9.8.7"

    def missing(distribution):
        raise identity_module.PackageNotFoundError(distribution)

    monkeypatch.setattr(identity_module, "installed_version", missing)
    with pytest.raises(
        VersionLookupError, match="cannot determine the installed version"
    ):
        CliIdentity.from_distribution(
            name="TOOL", distribution="missing-package", long_name="Tool"
        )


def test_help_actions_use_the_invocation_stream():
    parser, command_parsers = make_cli()
    stdout, stderr = io.StringIO(), io.StringIO()
    assert (
        run_cli(
            parser,
            {},
            identity=IDENTITY,
            argv=["--help"],
            command_parsers=command_parsers,
            stdout=stdout,
            stderr=stderr,
        )
        == 0
    )
    assert stdout.getvalue().startswith("TEST 1.2.3 — Test Operator Tool\n")
    assert stderr.getvalue() == ""


def test_grouped_help_has_each_verb_once_and_no_short_help():
    parser, _ = make_cli()
    text = parser.format_help()
    assert text.count("status") == 2  # getting-started example plus verb entry
    assert text.count("apply FILE") == 1
    assert "\n  -h" not in text
    assert "EXPLORATION" in text and "MODIFICATION" in text
    assert "--debug/--verbose" in text
    assert text.count("--help") == 1


def test_help_and_missing_argument_are_side_effect_free_and_actionable():
    parser, command_parsers = make_cli()
    stdout, stderr = io.StringIO(), io.StringIO()
    assert (
        run_cli(
            parser,
            {},
            identity=IDENTITY,
            argv=["help", "status"],
            stdout=stdout,
            stderr=stderr,
        )
        == 0
    )
    assert "TEST 1.2.3" in stdout.getvalue()
    stdout.seek(0)
    stdout.truncate(0)
    assert (
        run_cli(
            parser,
            {"status": handler, "apply": handler},
            identity=IDENTITY,
            argv=["apply"],
            command_parsers=command_parsers,
            stdout=stdout,
            stderr=stderr,
        )
        == 2
    )
    assert "required: file" in stderr.getvalue()
    assert "TEST 1.2.3" in stderr.getvalue()
    assert "usage:" in stderr.getvalue()


def test_unknown_help_topic_has_one_identity_line():
    parser, command_parsers = make_cli()
    stderr = io.StringIO()
    assert (
        run_cli(
            parser,
            {"status": handler, "apply": handler},
            identity=IDENTITY,
            argv=["help", "missing"],
            command_parsers=command_parsers,
            stdout=io.StringIO(),
            stderr=stderr,
        )
        == 2
    )
    assert stderr.getvalue().count("TEST 1.2.3") == 1


def test_catalog_can_render_markdown_and_parser_map_is_discovered():
    parser, command_parsers = make_cli()
    markdown = parser.catalog.render(output_format="markdown")
    assert markdown.startswith("# TEST 1.2.3 — Test Operator Tool\n")
    assert "## Exploration" in markdown
    assert "| `--help` |" in markdown
    assert "## Modification" in markdown
    assert set(command_parsers) == {"status", "apply"}


def test_command_help_formatter_uses_wide_dynamic_terminal_width(monkeypatch):
    monkeypatch.setattr(
        parser_module,
        "get_terminal_size",
        lambda fallback: os.terminal_size((138, 30)),
    )
    parser = ExtendedArgumentParser(prog="wide", identity=IDENTITY)
    parser.add_argument("--setting", help="a long setting description")
    assert parser._get_formatter()._width == 138


def test_catalog_parser_drift_fails_loudly():
    parser, _ = make_cli()
    parser.catalog.verbs = (parser.catalog.verbs[0],)
    with pytest.raises(ValueError, match="help catalog/parser mismatch"):
        run_cli(
            parser,
            {"status": handler, "apply": handler},
            identity=IDENTITY,
            argv=[],
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )


def test_conflicting_option_documentation_is_rejected():
    catalog = HelpCatalog(
        IDENTITY,
        prog="test-tool",
        global_options=(("--config FILE", "read one config"),),
    )
    with pytest.raises(ValueError, match="conflicting top-level help metadata"):
        catalog.add_global_options(
            (
                OptionSpec(
                    ("--config",), "read another config", metavar="FILE", group="INPUT"
                ),
            )
        )


def _registered_cli(*, side_effects=None):
    registry = CliRegistry(
        IDENTITY,
        prog="test-tool",
        description="A test command registry.",
        getting_started=("test-tool status",),
        global_options=(
            OptionSpec(
                ("--config",),
                "select an input configuration",
                group="INPUT",
                metavar="FILE",
            ),
        ),
    )

    def status(args, runtime):
        if side_effects is not None:
            side_effects.append("status")
        runtime.output.primary(
            {"server_id": args.server_id, "filter": args.filter}
            if runtime.json_mode
            else "ready"
        )
        return 0

    def apply(args, runtime):
        if side_effects is not None:
            side_effects.append("apply")
        runtime.output.primary(
            {"path": args.path, "yes": runtime.yes} if runtime.json_mode else "applied"
        )
        return 0

    registry.register(
        VerbSpec(
            "status",
            "[server_id]",
            "show current state",
            group="EXPLORATION",
            examples=("test-tool status srv-1",),
            arguments=(
                ArgumentSpec(
                    "server_id",
                    "optional server selector",
                    metavar="server_id",
                    parser_kwargs={"nargs": "?"},
                ),
            ),
            options=(
                OptionSpec(
                    ("--filter",),
                    "filter status results",
                    group="FILTERS",
                    metavar="TEXT",
                ),
                OptionSpec(
                    ("--format",),
                    "choose result format",
                    group="OUTPUT CONTROL",
                    parser_kwargs={"choices": ("table", "json"), "default": "table"},
                ),
            ),
            handler=status,
        )
    )
    registry.register(
        VerbSpec(
            "apply",
            "FILE",
            "apply a reviewed change",
            group="MODIFICATION",
            mutating=True,
            examples=("test-tool apply change.json --yes",),
            arguments=(ArgumentSpec("path", "change file", metavar="FILE"),),
            handler=apply,
        )
    )
    return registry.build()


def test_registry_builds_parser_dispatch_help_options_and_markdown_once():
    app = _registered_cli()
    help_text = app.parser.format_help()
    assert "EXPLORATION" in help_text and "MODIFICATION" in help_text
    assert "DEBUGGING" in help_text and "OUTPUT CONTROL" in help_text
    assert "INPUT" in help_text and "--config FILE" in help_text
    assert "status [server_id] show current state" in help_text
    assert "apply FILE apply a reviewed change [mutating]" in help_text

    status_help = app.command_parsers["status"].format_help()
    apply_help = app.command_parsers["apply"].format_help()
    assert "FILTERS" in status_help and "--filter TEXT" in status_help
    assert "--format {table,json}" in status_help
    assert "--yes" not in status_help
    assert "--yes" in apply_help
    assert "test-tool apply change.json --yes" in apply_help
    markdown = app.catalog.render(output_format="markdown")
    assert "| `server_id` | optional server selector |" in markdown
    assert "| `--filter TEXT` | filter status results |" in markdown
    assert (
        "| `--format {table,json}` | choose result format (choices: `table`, `json`; default: `table`) |"
        in markdown
    )
    status_markdown = markdown[
        markdown.index("### `status`") : markdown.index("### `apply`")
    ]
    apply_markdown = markdown[markdown.index("### `apply`") :]
    assert "| `--json` | emit machine-readable output |" in status_markdown
    assert "| `--yes` |" not in status_markdown
    assert "| `--yes` | accept confirmation prompts |" in apply_markdown
    assert "## Input" in markdown


def test_custom_parser_extensions_remain_visible_in_generated_markdown():
    def configure(parser):
        parser.add_argument_group("ADVANCED").add_argument(
            "--raw-response", action="store_true", help="include the provider response"
        )

    registry = CliRegistry(IDENTITY, prog="extended", description="test custom parser")
    registry.register(
        VerbSpec(
            "inspect",
            "",
            "inspect a provider object",
            configure=configure,
            handler=lambda args, runtime: 0,
        )
    )
    app = registry.build()
    markdown = app.catalog.render(output_format="markdown")
    assert "```text" in markdown
    assert "ADVANCED:" in markdown
    assert "--raw-response" in markdown
    assert "include the provider response" in markdown


def test_registry_dispatch_and_global_values_survive_subparser_defaults():
    app = _registered_cli()
    stdout, stderr = io.StringIO(), io.StringIO()
    assert (
        app.run(
            argv=["--json", "status", "srv-1", "--filter", "edge"],
            stdout=stdout,
            stderr=stderr,
        )
        == 0
    )
    assert stdout.getvalue() == '{"server_id": "srv-1", "filter": "edge"}\n'
    assert stderr.getvalue() == ""

    stdout, stderr = io.StringIO(), io.StringIO()
    assert (
        app.run(argv=["apply", "change.json", "--yes"], stdout=stdout, stderr=stderr)
        == 0
    )
    assert stdout.getvalue() == "applied\n"

    stderr = io.StringIO()
    assert app.run(argv=["status", "--yes"], stdout=io.StringIO(), stderr=stderr) == 2
    assert "--yes" not in app.command_parsers["status"].format_help()

    missing_verb = io.StringIO()
    assert app.run(argv=["--json"], stdout=io.StringIO(), stderr=missing_verb) == 2
    assert "required: VERB" in missing_verb.getvalue()
    assert IDENTITY.headline in missing_verb.getvalue()


def test_black_box_contract_helper_and_help_paths_do_not_call_handlers():
    side_effects = []
    app = _registered_cli(side_effects=side_effects)

    def invoke(argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        code = app.run(argv=argv, stdout=stdout, stderr=stderr)
        return SimpleNamespace(
            returncode=code, stdout=stdout.getvalue(), stderr=stderr.getvalue()
        )

    assert_cli_contract(
        invoke,
        IDENTITY,
        ("status", "apply"),
        invalid_invocations={
            "missing apply file": ("apply",),
            "short help option is unsupported": ("-h",),
        },
    )
    assert side_effects == []


def test_registry_supports_single_command_shape_without_a_verb():
    registry = CliRegistry(
        IDENTITY,
        prog="monitor-task",
        description="Monitor one task.",
        single_command=True,
    )
    observed = []

    def watch(args, runtime):
        observed.append(args.task_uuid)
        return 0

    registry.register(
        VerbSpec(
            "watch",
            "TASK_UUID",
            "poll one task until terminal state",
            arguments=(ArgumentSpec("task_uuid", "task UUID", metavar="TASK_UUID"),),
            handler=watch,
        )
    )
    app = registry.build()
    stdout, stderr = io.StringIO(), io.StringIO()
    assert app.run(argv=[], stdout=stdout, stderr=stderr) == 0
    assert stdout.getvalue().startswith(IDENTITY.headline)
    assert "TASK_UUID" in stdout.getvalue()
    assert observed == []
    assert app.run(argv=["task-123"], stdout=io.StringIO(), stderr=io.StringIO()) == 0
    assert observed == ["task-123"]


def test_runtime_confirmation_is_default_no_noninteractive_and_yes_aware():
    app = _registered_cli()
    # A read-only verb cannot accidentally request or consume --yes.
    stderr = io.StringIO()
    assert app.run(argv=["status", "--yes"], stdout=io.StringIO(), stderr=stderr) == 2

    def ask(args, runtime):
        return 0 if runtime.confirm("Replace the live firewall policy?") else 3

    registry = CliRegistry(IDENTITY, prog="test-tool", description="test")
    registry.register(VerbSpec("apply", "", "change", mutating=True, handler=ask))
    confirm_app = registry.build()

    non_tty = io.StringIO()
    assert (
        confirm_app.run(
            argv=["apply"], stdout=io.StringIO(), stderr=non_tty, stdin=io.StringIO()
        )
        == 2
    )
    assert "stdin is not interactive" in non_tty.getvalue()

    yes_err = io.StringIO()
    assert (
        confirm_app.run(argv=["apply", "--yes"], stdout=io.StringIO(), stderr=yes_err)
        == 0
    )
    assert "accepted via --yes" in yes_err.getvalue()

    class TTYInput(io.StringIO):
        def isatty(self):
            return True

    accepted_err = io.StringIO()
    assert (
        confirm_app.run(
            argv=["apply"],
            stdout=io.StringIO(),
            stderr=accepted_err,
            stdin=TTYInput("yes\n"),
        )
        == 0
    )
    assert "Replace the live firewall policy? [y/N]" in accepted_err.getvalue()

    declined_err = io.StringIO()
    assert (
        confirm_app.run(
            argv=["apply"],
            stdout=io.StringIO(),
            stderr=declined_err,
            stdin=TTYInput("no\n"),
        )
        == 3
    )
    assert "Declined; no changes made." in declined_err.getvalue()

    eof_err = io.StringIO()
    assert (
        confirm_app.run(
            argv=["apply"],
            stdout=io.StringIO(),
            stderr=eof_err,
            stdin=TTYInput(""),
        )
        == 3
    )
    assert "No confirmation received; no changes made." in eof_err.getvalue()


def test_registry_routes_standard_logging_through_selected_cli_level():
    logger_name = "cli-extended-registry-log-test"
    logger = logging.getLogger(logger_name)
    logger.handlers.clear()
    logger.setLevel(logging.WARNING)
    logger.propagate = True

    def inspect(args, runtime):
        logging.getLogger(f"{logger_name}.api").debug("request detail")
        return 0

    registry = CliRegistry(
        IDENTITY,
        prog="logged-tool",
        description="test logging routing",
        logging_logger=logger_name,
    )
    registry.register(VerbSpec("status", "", "read", handler=inspect))
    app = registry.build()
    stderr = io.StringIO()
    assert app.run(argv=["status", "--debug"], stdout=io.StringIO(), stderr=stderr) == 0
    assert "[DEBUG] request detail" in stderr.getvalue()
    assert logger.handlers == []
    assert logger.level == logging.WARNING
    assert logger.propagate is True


def test_quiet_keeps_warnings_but_suppresses_info():
    def report(args, runtime):
        runtime.output.info("informational detail")
        runtime.output.warn("actionable warning")
        return 0

    registry = CliRegistry(IDENTITY, prog="quiet-tool", description="quiet test")
    registry.register(VerbSpec("inspect", "", "inspect", handler=report))
    app = registry.build()
    stderr = io.StringIO()
    assert (
        app.run(argv=["inspect", "--quiet"], stdout=io.StringIO(), stderr=stderr) == 0
    )
    assert "informational detail" not in stderr.getvalue()
    assert "[WARN] actionable warning" in stderr.getvalue()


def test_single_command_registry_is_supported_by_black_box_contract_helper():
    registry = CliRegistry(
        IDENTITY, prog="single", description="single", single_command=True
    )
    registry.register(VerbSpec("run", "", "run once", handler=lambda args, runtime: 0))
    app = registry.build()

    def invoke(argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        code = app.run(argv=argv, stdout=stdout, stderr=stderr)
        return SimpleNamespace(
            returncode=code, stdout=stdout.getvalue(), stderr=stderr.getvalue()
        )

    assert_cli_contract(invoke, IDENTITY, ())


def test_no_argument_action_requires_explicit_single_command_opt_in():
    observed = []
    registry = CliRegistry(
        IDENTITY,
        prog="snapshot",
        description="Take a default snapshot.",
        single_command=True,
        no_args_action=True,
    )
    registry.register(
        VerbSpec(
            "snapshot",
            "",
            "take the default snapshot",
            handler=lambda args, runtime: observed.append("run") or 0,
        )
    )
    app = registry.build()
    assert app.run(argv=[], stdout=io.StringIO(), stderr=io.StringIO()) == 0
    assert observed == ["run"]
    help_output = io.StringIO()
    assert app.run(argv=["--help"], stdout=help_output, stderr=io.StringIO()) == 0
    assert help_output.getvalue().startswith(IDENTITY.headline)
    assert observed == ["run"]


def test_output_levels_colour_and_redaction(monkeypatch):
    stdout, stderr = io.StringIO(), io.StringIO()
    output = CliOutput(
        IDENTITY,
        level=LogLevel.WARN,
        color=True,
        secrets=("secret-token",),
        stdout=stdout,
        stderr=stderr,
    )
    output.info("hidden secret-token")
    output.warn("secret-token needs attention")
    output.error("secret-token failed", hint="inspect the configuration")
    text = stderr.getvalue()
    assert "INFO" not in text
    assert "<redacted>" in text
    assert "Hint:" in text and "inspect" in text
    assert "\033[" in text

    quiet_stderr = io.StringIO()
    quiet = CliOutput(IDENTITY, level=LogLevel.WARN, stderr=quiet_stderr)
    quiet.info("hidden info")
    quiet.warn("visible warning")
    quiet.error("visible error")
    assert "hidden info" not in quiet_stderr.getvalue()
    assert "[WARN] visible warning" in quiet_stderr.getvalue()
    assert "[ERROR] visible error" in quiet_stderr.getvalue()

    monkeypatch.setenv("NO_COLOR", "1")
    no_color = CliOutput(IDENTITY, color=None, stderr=io.StringIO())
    no_color.warn("warning")
    assert "\033[" not in no_color.stderr.getvalue()


def test_logging_adapter_uses_contract_levels():
    stderr = io.StringIO()
    output = CliOutput(IDENTITY, stderr=stderr)
    logger = logging.getLogger("cli-extended-test")
    logger.handlers.clear()
    logger.propagate = False
    handler = install_logging(output, logger)
    try:
        logger.info("hello")
        logger.warning("careful")
        logger.error("broken")
    finally:
        logger.removeHandler(handler)
        handler.close()
    text = stderr.getvalue()
    assert "[INFO] hello" in text
    assert "[WARN] careful" in text
    assert "[ERROR] broken" in text


def test_logging_installation_is_bounded_and_restorable():
    logger = logging.getLogger("cli-extended-bounded-test")
    logger.handlers.clear()
    logger.setLevel(logging.WARNING)
    logger.propagate = True
    output = CliOutput(IDENTITY, stderr=io.StringIO())
    with logging_context(output, logger):
        assert logger.level == logging.DEBUG
        assert logger.propagate is False
        logger.info("inside")
    assert logger.level == logging.WARNING
    assert logger.propagate is True
    assert logger.handlers == []

    handler = install_logging(output, logger)
    assert install_logging(output, logger) is handler
    uninstall_logging(handler)
    assert logger.handlers == []


def test_nested_logging_context_preserves_existing_handler():
    logger = logging.getLogger("cli-extended-nested-test")
    logger.handlers.clear()
    logger.propagate = False
    output = CliOutput(IDENTITY, stderr=io.StringIO())
    outer = install_logging(output, logger)
    try:
        with logging_context(output, logger) as inner:
            assert inner is outer
        assert logger.handlers == [outer]
    finally:
        uninstall_logging(outer)
    assert logger.handlers == []


def test_progress_auto_falls_back_to_plain_and_rawjson_is_machine_readable():
    plain = io.StringIO()
    progress = ProgressRenderer(ProgressMode.AUTO, stream=plain)
    progress.update("working", current=1, total=2)
    progress.finish()
    assert progress.mode is ProgressMode.PLAIN
    assert "[INFO] working (1/2)" in plain.getvalue()

    raw = io.StringIO()
    progress = ProgressRenderer(ProgressMode.RAWJSON, stream=raw)
    progress.update("working", current=1, total=2)
    progress.finish()
    assert '"type": "progress"' in raw.getvalue()
    assert "\\033" not in raw.getvalue()

    quiet = io.StringIO()
    ProgressRenderer(ProgressMode.PLAIN, stream=quiet, level=LogLevel.WARN).update(
        "hidden"
    )
    assert quiet.getvalue() == ""
    json_quiet = io.StringIO()
    renderer = ProgressRenderer(ProgressMode.RAWJSON, stream=json_quiet, json_mode=True)
    renderer.update("hidden")
    assert json_quiet.getvalue() == ""

    redacted = io.StringIO()
    ProgressRenderer(
        ProgressMode.RAWJSON,
        stream=redacted,
        secrets=("refresh-secret",),
    ).update("refresh-secret used")
    ProgressRenderer(
        ProgressMode.PLAIN,
        stream=redacted,
        secrets=("refresh-secret",),
    ).finish("finished refresh-secret")
    assert "refresh-secret" not in redacted.getvalue()
    assert "<redacted> used" in redacted.getvalue()
    assert "finished <redacted>" in redacted.getvalue()


def test_yes_and_debug_raw_are_available_to_handlers():
    parser, command_parsers = make_cli()
    seen = {}

    def inspect(args, runtime):
        seen.update(yes=runtime.yes, debug=runtime.debug, raw=runtime.debug_raw)
        return 0

    stdout, stderr = io.StringIO(), io.StringIO()
    assert (
        run_cli(
            parser,
            {"status": inspect, "apply": inspect},
            identity=IDENTITY,
            argv=["--yes", "status", "--debug-raw"],
            command_parsers=command_parsers,
            stdout=stdout,
            stderr=stderr,
        )
        == 0
    )
    assert seen == {"yes": True, "debug": True, "raw": True}
    assert "secrets may be exposed" in stderr.getvalue()


def test_json_mode_mutes_raw_json_progress_but_keeps_plain_progress_on_stderr():
    parser, command_parsers = make_cli()

    def progress_handler(args, runtime):
        with runtime.progress() as progress:
            progress.update("working refresh-secret")
            progress.finish()
        runtime.output.primary({"state": "ready"})
        return 0

    stdout, stderr = io.StringIO(), io.StringIO()
    assert (
        run_cli(
            parser,
            {"status": progress_handler, "apply": progress_handler},
            identity=IDENTITY,
            argv=["status", "--json", "--progress", "rawjson"],
            command_parsers=command_parsers,
            secrets=("refresh-secret",),
            stdout=stdout,
            stderr=stderr,
        )
        == 0
    )
    assert stdout.getvalue() == '{"state": "ready"}\n'
    assert stderr.getvalue() == ""

    stdout, stderr = io.StringIO(), io.StringIO()
    assert (
        run_cli(
            parser,
            {"status": progress_handler, "apply": progress_handler},
            identity=IDENTITY,
            argv=["status", "--json", "--progress", "plain"],
            command_parsers=command_parsers,
            secrets=("refresh-secret",),
            stdout=stdout,
            stderr=stderr,
        )
        == 0
    )
    assert stdout.getvalue() == '{"state": "ready"}\n'
    assert "[INFO] working <redacted>" in stderr.getvalue()
    assert "refresh-secret" not in stderr.getvalue()


def test_debug_raw_rejects_a_lower_explicit_level():
    parser, command_parsers = make_cli()
    stderr = io.StringIO()
    assert (
        run_cli(
            parser,
            {"status": handler, "apply": handler},
            identity=IDENTITY,
            argv=["status", "--log-level", "error", "--debug-raw"],
            command_parsers=command_parsers,
            stdout=io.StringIO(),
            stderr=stderr,
        )
        == 2
    )
    assert "secrets may be exposed" not in stderr.getvalue()
    assert "implies --log-level=debug" in stderr.getvalue()


def test_ctrl_c_is_clean_and_expected_failures_are_concise():
    parser, command_parsers = make_cli()

    def cancel(args, runtime):
        raise KeyboardInterrupt

    def fail(args, runtime):
        raise CliFailure("bad input", exit_code=2, hint="use --help")

    stdout, stderr = io.StringIO(), io.StringIO()
    assert (
        run_cli(
            parser,
            {"status": cancel, "apply": cancel},
            identity=IDENTITY,
            argv=["status"],
            command_parsers=command_parsers,
            stdout=stdout,
            stderr=stderr,
        )
        == 130
    )
    assert "Traceback" not in stderr.getvalue()
    stderr.seek(0)
    stderr.truncate(0)
    assert (
        run_cli(
            parser,
            {"status": fail, "apply": fail},
            identity=IDENTITY,
            argv=["status"],
            command_parsers=command_parsers,
            stdout=stdout,
            stderr=stderr,
        )
        == 2
    )
    assert "bad input" in stderr.getvalue()
    assert "Hint: use --help" in stderr.getvalue()
    assert "Traceback" not in stderr.getvalue()

    stderr.seek(0)
    stderr.truncate(0)

    def external_failure(args, runtime):
        raise OSError("provider unavailable")

    assert (
        run_cli(
            parser,
            {"status": external_failure, "apply": external_failure},
            identity=IDENTITY,
            argv=["status", "--debug"],
            command_parsers=command_parsers,
            expected_exceptions=(OSError,),
            stdout=io.StringIO(),
            stderr=stderr,
        )
        == 1
    )
    assert "provider unavailable" in stderr.getvalue()
    assert "[DEBUG] handled OSError in status" in stderr.getvalue()
    assert "Traceback" not in stderr.getvalue()


def test_unexpected_exception_remains_a_traceback():
    parser, command_parsers = make_cli()

    def broken(args, runtime):
        raise RuntimeError("programming bug")

    with pytest.raises(RuntimeError, match="programming bug"):
        run_cli(
            parser,
            {"status": broken, "apply": broken},
            identity=IDENTITY,
            argv=["status"],
            command_parsers=command_parsers,
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )


def test_debug_unexpected_exception_is_not_printed_twice_by_the_helper():
    parser, command_parsers = make_cli()
    stderr = io.StringIO()

    def broken(args, runtime):
        raise RuntimeError("programming bug")

    with pytest.raises(RuntimeError, match="programming bug"):
        run_cli(
            parser,
            {"status": broken, "apply": broken},
            identity=IDENTITY,
            argv=["status", "--debug"],
            command_parsers=command_parsers,
            stdout=io.StringIO(),
            stderr=stderr,
        )
    assert stderr.getvalue() == ""
