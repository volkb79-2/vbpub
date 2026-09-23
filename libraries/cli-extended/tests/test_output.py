from __future__ import annotations

import io
import logging

import pytest

from cli_extended import (
    CliIdentity,
    CliLoggingHandler,
    CliOutput,
    CliRuntime,
    LogLevel,
    install_logging,
    logging_context,
    redact_text,
    redact_value,
    uninstall_logging,
)
from cli_extended.output import _colorize_help

IDENTITY = CliIdentity("TEST", "1.2.3", "Test Tool", command="test")


class _FlushTrackingStream(io.StringIO):
    def __init__(self, initial_value=""):
        super().__init__(initial_value)
        self.flush_count = 0

    def flush(self):
        self.flush_count += 1
        super().flush()


class _TTYInput(io.StringIO):
    def isatty(self):
        return True


@pytest.mark.parametrize("value", ("TRACE", "", "notice"))
def test_log_level_parse_rejects_unknown_values(value):
    with pytest.raises(ValueError, match="invalid log level"):
        LogLevel.parse(value)


def test_log_levels_map_to_stable_severity_numbers():
    assert {level.value: level.numeric for level in LogLevel} == {
        "error": 40,
        "warn": 30,
        "info": 20,
        "debug": 10,
    }
    assert LogLevel.parse("DEBUG") is LogLevel.DEBUG


def test_redaction_is_explicit_longest_first_and_recursive():
    assert redact_text("secret-prefix", ()) == "secret-prefix"
    assert redact_text("token-123", ("token", "token-123", "")) == "<redacted>"
    value = {"text": "top-secret", "items": ["secret", ("top-secret", 3)]}
    assert redact_value(value, ("secret",)) == {
        "text": "top-<redacted>",
        "items": ["<redacted>", ("top-<redacted>", 3)],
    }
    assert redact_value(17, ("17",)) == 17


def test_colorized_help_covers_identity_sections_usage_verbs_options_and_tags():
    plain = (
        f"{IDENTITY.headline}\nEXPLORATION\nUsage: tool <verb>\n"
        "  status  show state\n  --json  emit JSON\n"
        "[INFO] working\nnormal prose\n"
    )
    rendered = _colorize_help(plain, identity=IDENTITY, command_names=("status",))

    assert "\033[1;36mTEST 1.2.3 — Test Tool\033[0m" in rendered
    assert "\033[1m\033[34mEXPLORATION\033[0m" in rendered
    assert "\033[1m\033[1;36mUsage:\033[0m" in rendered
    assert "\033[1;36mstatus\033[0m" in rendered
    assert "\033[1;36m--json\033[0m" in rendered
    assert "\033[36m[INFO]\033[0m" in rendered
    assert "normal prose\n" in rendered


@pytest.mark.parametrize("stream", (object(),))
def test_non_tty_probe_without_isatty_is_safe(stream):
    output = CliOutput(IDENTITY, stdin=stream, stdout=stream, stderr=stream)
    assert not output.color_enabled()
    assert not output.is_interactive


def test_tty_probe_oserror_is_treated_as_noninteractive():
    class BrokenTTY(io.StringIO):
        def isatty(self):
            raise OSError("descriptor closed")

    output = CliOutput(IDENTITY, stdin=BrokenTTY(), stdout=BrokenTTY())
    assert not output.is_interactive


def test_help_redacts_tokens_and_uses_destination_stream_for_auto_color(monkeypatch):
    class TTY(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.delenv("NO_COLOR", raising=False)
    output = CliOutput(IDENTITY, secrets=("token-value",))
    text = output.format_help(
        f"{IDENTITY.headline}\n\n--token token-value\n", stream=TTY()
    )
    assert "token-value" not in text
    assert "<redacted>" in text
    assert "\033[" in text

    monkeypatch.setenv("NO_COLOR", "")
    assert "\033[" not in output.format_help(f"{IDENTITY.headline}\n", stream=TTY())
    forced = CliOutput(IDENTITY, color=True)
    assert "\033[" in forced.format_help(f"{IDENTITY.headline}\n", stream=io.StringIO())


def test_debug_raw_warning_is_once_and_raw_output_is_explicit():
    stderr, stdout = io.StringIO(), io.StringIO()
    output = CliOutput(
        IDENTITY,
        debug_raw=True,
        secrets=("token-value",),
        stderr=stderr,
        stdout=stdout,
    )
    output.raw_warning()
    output.raw_warning()
    output.error("token-value visible")
    output.error("second failure")
    output.primary("token-value visible")
    assert stderr.getvalue().count("--debug-raw is active") == 1
    assert stderr.getvalue().count(IDENTITY.headline) == 1
    assert "token-value visible" in stderr.getvalue()
    assert "token-value visible\n" == stdout.getvalue()

    plain_stderr = io.StringIO()
    CliOutput(IDENTITY, stderr=plain_stderr).raw_warning()
    assert plain_stderr.getvalue() == ""


def test_primary_json_and_json_mode_redact_nested_values():
    stdout = io.StringIO()
    output = CliOutput(
        IDENTITY,
        json_mode=True,
        secrets=("private",),
        stdout=stdout,
    )
    output.primary({"token": "private", "items": ["private"]})
    assert stdout.getvalue() == '{"token": "<redacted>", "items": ["<redacted>"]}\n'

    raw_stdout = io.StringIO()
    CliOutput(
        IDENTITY, debug_raw=True, secrets=("private",), stdout=raw_stdout
    ).primary_json({"token": "private"})
    assert raw_stdout.getvalue() == '{"token": "private"}\n'

    safe_stdout = io.StringIO()
    CliOutput(IDENTITY, secrets=("private",), stdout=safe_stdout).primary_json(
        {"token": "private"}
    )
    assert safe_stdout.getvalue() == '{"token": "<redacted>"}\n'


def test_streaming_output_flushes_diagnostics_primary_results_and_headings():
    stderr = _FlushTrackingStream()
    stdout = _FlushTrackingStream()
    output = CliOutput(IDENTITY, stderr=stderr, stdout=stdout)

    output.info("diagnostic")
    assert stderr.flush_count == 1
    output.hint("next step")
    assert stderr.flush_count == 2
    output.primary("result")
    assert stdout.flush_count == 1
    output.error("failed")
    assert stderr.flush_count == 4
    assert stderr.getvalue().startswith("[INFO] diagnostic")
    assert IDENTITY.headline in stderr.getvalue()


def test_forced_warnings_and_cancellation_survive_error_only_verbosity():
    stderr = io.StringIO()
    output = CliOutput(IDENTITY, level=LogLevel.ERROR, debug_raw=True, stderr=stderr)

    output.raw_warning()
    output.cancelled()

    assert "--debug-raw is active" in stderr.getvalue()
    assert "[INFO] Cancelled." in stderr.getvalue()


@pytest.mark.parametrize(
    ("answer", "expected"),
    (("", "No confirmation received"), ("n\n", "Declined; no changes made.")),
)
def test_confirmation_refusal_is_visible_when_normal_info_is_filtered(answer, expected):
    stderr = io.StringIO()
    output = CliOutput(
        IDENTITY,
        level=LogLevel.ERROR,
        stdin=_TTYInput(answer),
        stderr=stderr,
    )
    runtime = CliRuntime(IDENTITY, output)

    assert runtime.confirm("Make a change?") is False
    assert expected in stderr.getvalue()


def test_uninstall_unowned_handler_is_safe():
    handler = CliLoggingHandler(CliOutput(IDENTITY, stderr=io.StringIO()))
    uninstall_logging(handler)
    assert handler._closed


def test_uninstall_logging_with_unknown_previous_propagation_restores_only_known_state():
    logger = logging.getLogger("cli-extended-unknown-previous-state-test")
    logger.handlers.clear()
    logger.setLevel(logging.WARNING)
    logger.propagate = True
    handler = CliLoggingHandler(CliOutput(IDENTITY, stderr=io.StringIO()))
    handler._owner_logger = logger
    handler._previous_level = None
    handler._previous_propagate = None
    logger.addHandler(handler)

    uninstall_logging(handler)

    assert logger.handlers == []
    assert logger.level == logging.WARNING
    assert logger.propagate is True


def test_uninstall_restores_independently_optional_logger_state():
    logger = logging.getLogger("cli-extended-state-restore-test")
    logger.handlers.clear()
    logger.setLevel(logging.WARNING)
    logger.propagate = True
    handler = CliLoggingHandler(CliOutput(IDENTITY, stderr=io.StringIO()))
    handler._owner_logger = logger
    handler._previous_level = None
    handler._previous_propagate = False
    logger.addHandler(handler)

    uninstall_logging(handler)

    assert logger.handlers == []
    assert logger.level == logging.WARNING
    assert logger.propagate is False


def test_cli_logging_handler_and_install_context_select_levels_and_preserve_foreign_handlers():
    logger = logging.getLogger("cli-extended-foreign-handler-test")
    logger.handlers.clear()
    logger.setLevel(logging.WARNING)
    logger.propagate = False
    foreign_output = CliOutput(IDENTITY, stderr=io.StringIO())
    output = CliOutput(IDENTITY, level=LogLevel.DEBUG, stderr=io.StringIO())
    foreign = CliLoggingHandler(foreign_output)
    logger.addHandler(foreign)

    handler = install_logging(output, logger)
    try:
        logger.debug("debug level")
        logger.info("info level")
        logger.warning("warning level")
        logger.error("error level")
        assert "[DEBUG] debug level" in output.stderr.getvalue()
        assert "[INFO] info level" in output.stderr.getvalue()
        assert "[WARN] warning level" in output.stderr.getvalue()
        assert "[ERROR] error level" in output.stderr.getvalue()
    finally:
        uninstall_logging(handler)
        logger.removeHandler(foreign)
        foreign.close()


def test_logging_context_cleans_up_after_handler_exception():
    logger = logging.getLogger("cli-extended-output-exception-test")
    logger.handlers.clear()
    logger.propagate = False
    original_level = logger.level
    output = CliOutput(IDENTITY, stderr=io.StringIO())

    with (
        pytest.raises(RuntimeError, match="test failure"),
        logging_context(output, logger),
    ):
        raise RuntimeError("test failure")

    assert logger.handlers == []
    assert logger.level == original_level


def test_logging_context_does_not_leave_a_handler_beside_a_foreign_one():
    logger = logging.getLogger("cli-extended-context-foreign-handler-test")
    logger.handlers.clear()
    logger.propagate = False
    existing = CliLoggingHandler(CliOutput(IDENTITY, stderr=io.StringIO()))
    logger.addHandler(existing)
    output = CliOutput(IDENTITY, stderr=io.StringIO())

    try:
        with logging_context(output, logger):
            assert len(logger.handlers) == 2
        assert logger.handlers == [existing]
    finally:
        for handler in tuple(logger.handlers):
            logger.removeHandler(handler)
            handler.close()


def test_existing_nonmatching_cli_handler_is_preserved_and_not_reused():
    logger = logging.getLogger("cli-extended-existing-handler-test")
    logger.handlers.clear()
    logger.propagate = False
    existing_output = CliOutput(IDENTITY, stderr=io.StringIO())
    new_output = CliOutput(IDENTITY, stderr=io.StringIO())
    existing = CliLoggingHandler(existing_output)
    logger.addHandler(existing)

    added = new_output and __import__("cli_extended").install_logging(
        new_output, logger
    )
    try:
        assert added is not existing
        assert logger.handlers == [existing, added]
    finally:
        uninstall_logging(added)
        logger.removeHandler(existing)
        existing.close()
