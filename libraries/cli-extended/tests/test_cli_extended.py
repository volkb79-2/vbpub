from __future__ import annotations

import argparse
import io
import logging

import pytest
from cli_extended import (
    CliFailure,
    CliIdentity,
    CliOutput,
    ExtendedArgumentParser,
    HelpCatalog,
    LogLevel,
    ProgressMode,
    ProgressRenderer,
    VerbSpec,
    add_common_options,
    install_logging,
    logging_context,
    run_cli,
    uninstall_logging,
)

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
        global_options=(("--help", "show help"), ("--version", "print version")),
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
            progress.update("working")
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
            stdout=stdout,
            stderr=stderr,
        )
        == 0
    )
    assert stdout.getvalue() == '{"state": "ready"}\n'
    assert "[INFO] working" in stderr.getvalue()


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
