"""Tests for CIU terminal-only severity presentation."""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import output
from ciu import cli_utils
from ciu.output import SeverityStream


def test_short_time_prefixes_a_split_warning_print_without_delaying_the_message():
    target = io.StringIO()
    stream = SeverityStream(target, time_short=True, colour=False)

    stream.write("[WARN] host inventory is incomplete")
    stream.write("\n")

    assert re.fullmatch(
        r"\d{2}:\d{2}:\d{2} \[WARN\] host inventory is incomplete\n",
        target.getvalue(),
    )


def test_error_colour_wraps_only_the_machine_readable_prefix():
    target = io.StringIO()
    stream = SeverityStream(target, time_short=False, colour=True)

    stream.write("[ERROR] deployment failed\n")

    assert target.getvalue() == "\033[31m[ERROR]\033[0m deployment failed\n"


def test_plain_partial_progress_is_forwarded_immediately_and_not_decorated():
    target = io.StringIO()
    stream = SeverityStream(target, time_short=True, colour=True)

    stream.write("rendering 50%")

    assert target.getvalue() == "rendering 50%"


def test_split_severity_is_buffered_only_until_it_can_be_classified_and_flushes_cleanly():
    target = io.StringIO()
    stream = SeverityStream(target, time_short=False, colour=False)

    stream.write("[IN")
    assert target.getvalue() == ""
    stream.write("FO] ready\n")
    stream.write("[ER")
    stream.flush()

    assert target.getvalue() == "[INFO] ready\n[ER"
    # Delegating unknown stream attributes keeps the wrapper transparent to
    # callers which need StringIO's diagnostic helpers.
    assert stream.getvalue() == target.getvalue()
    assert stream.write("") == 0
    stream.flush()


class _InteractiveBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_configure_colours_ttys_and_reconfigures_existing_wrappers(monkeypatch):
    stdout = _InteractiveBuffer()
    stderr = _InteractiveBuffer()
    monkeypatch.setattr(output.sys, "stdout", stdout)
    monkeypatch.setattr(output.sys, "stderr", stderr)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")

    output.configure(time_short=True)
    assert isinstance(output.sys.stdout, SeverityStream)
    output.sys.stdout.write("[INFO] first\n")
    assert re.fullmatch(r"\033\[32m\d{2}:\d{2}:\d{2} \[INFO\]\033\[0m first\n", stdout.getvalue())

    output.configure(time_short=False)
    output.sys.stderr.write("[WARN] second\n")
    assert stderr.getvalue() == "\033[33m[WARN]\033[0m second\n"


def test_colour_detection_refuses_no_colour_and_dumb_terminal(monkeypatch):
    stream = _InteractiveBuffer()
    monkeypatch.setenv("TERM", "dumb")
    assert not output._colour_enabled(stream)
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setenv("NO_COLOR", "1")
    assert not output._colour_enabled(stream)
    monkeypatch.delenv("NO_COLOR")
    assert output._colour_enabled(stream)
    assert not output._colour_enabled(io.StringIO())


def test_configure_leaves_plain_noninteractive_streams_unwrapped(monkeypatch):
    stdout = io.StringIO()
    stderr = io.StringIO()
    monkeypatch.setattr(output.sys, "stdout", stdout)
    monkeypatch.setattr(output.sys, "stderr", stderr)

    output.configure(time_short=False)

    assert output.sys.stdout is stdout
    assert output.sys.stderr is stderr


def test_cli_error_honors_an_explicit_stream_and_keeps_the_headline_first():
    target = io.StringIO()

    cli_utils.cli_error("bad configuration", stream=target)

    assert target.getvalue().splitlines() == [
        cli_utils.cli_headline(),
        "bad configuration",
    ]


def test_argument_parser_usage_starts_with_the_product_headline():
    parser = cli_utils.CiuArgumentParser(prog="ciu", add_help=False)
    parser.add_argument("verb")

    assert parser.format_usage().splitlines()[0] == cli_utils.cli_headline()


def test_time_prefix_is_a_registered_global_and_never_read_from_a_passthrough_command(monkeypatch):
    """`--log-prefix-time-short` is a global of the registry: accepted before or after
    the verb, never consumed from the command after `--`."""
    import sys

    from ciu import cli

    calls: list[bool] = []
    monkeypatch.setattr(output, "configure", calls.append)
    monkeypatch.setenv("CIU_LOG_PREFIX_TIME_SHORT", "0")  # restored by monkeypatch afterwards
    seen: list[list[str]] = []
    monkeypatch.setattr(cli, "_legacy_dispatch", lambda tokens: seen.append(tokens))

    monkeypatch.setattr(sys, "argv", ["ciu", "up", "--log-prefix-time-short"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    assert calls[-1] is True
    assert output.os.environ["CIU_LOG_PREFIX_TIME_SHORT"] == "1"

    calls.clear()
    monkeypatch.delenv("CIU_LOG_PREFIX_TIME_SHORT")
    monkeypatch.setattr(sys, "argv", ["ciu", "ssh", "web", "--", "--log-prefix-time-short"])
    with pytest.raises(SystemExit):
        cli.main()
    assert calls == [False]
    assert "CIU_LOG_PREFIX_TIME_SHORT" not in output.os.environ
    assert seen[-1] == ["ssh", "web", "--", "--log-prefix-time-short"]
