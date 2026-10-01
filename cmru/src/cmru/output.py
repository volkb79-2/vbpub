"""Console presentation for CMRU's already severity-prefixed messages.

The project deliberately keeps the textual ``[INFO]``/``[WARN]``/``[ERROR]``
prefix as the machine-readable log contract.  This module only decorates that
contract at the terminal boundary: timestamps are opt-in and ANSI colour is
used only for an interactive stream, never in redirected logs.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import TextIO


_SEVERITIES = {
    "[INFO]": "\033[32m",   # green
    "[WARN]": "\033[33m",   # yellow
    "[ERROR]": "\033[31m",  # red
}
_RESET = "\033[0m"
_TIME_ENV = "CMRU_LOG_PREFIX_TIME_SHORT"


def _colour_enabled(stream: TextIO) -> bool:
    """Use ANSI only where it cannot contaminate a log or machine consumer."""
    return (
        bool(getattr(stream, "isatty", lambda: False)())
        and not os.environ.get("NO_COLOR")
        and os.environ.get("TERM", "").lower() != "dumb"
    )


class SeverityStream:
    """Decorate severity prefixes without delaying ordinary streamed output."""

    def __init__(self, stream: TextIO, *, time_short: bool, colour: bool) -> None:
        self._stream = stream
        self._time_short = time_short
        self._colour = colour
        self._line_start = True
        self._candidate = ""

    def configure(self, *, time_short: bool, colour: bool) -> None:
        self._time_short = time_short
        self._colour = colour

    def _prefix(self, severity: str) -> str:
        rendered = severity
        if self._time_short:
            rendered = f"{datetime.now().astimezone():%H:%M:%S} {rendered}"
        if self._colour:
            return f"{_SEVERITIES[severity]}{rendered}{_RESET}"
        return rendered

    def _write_normal(self, text: str) -> None:
        """Write through until a newline returns us to a new prefix boundary."""
        while text:
            newline = text.find("\n")
            if newline < 0:
                self._stream.write(text)
                return
            self._stream.write(text[: newline + 1])
            text = text[newline + 1 :]
            self._line_start = True

    def write(self, text: str) -> int:
        # ``print`` writes its payload and newline separately. Hold only the
        # possible seven-character severity token; ordinary partial output is
        # forwarded immediately, preserving progress visibility.
        original_length = len(text)
        if self._candidate:
            text = self._candidate + text
            self._candidate = ""

        while text:
            if not self._line_start:
                self._write_normal(text)
                return original_length

            matching = next((item for item in _SEVERITIES if text.startswith(item)), None)
            if matching is not None:
                self._stream.write(self._prefix(matching))
                text = text[len(matching):]
                self._line_start = False
                continue

            if any(item.startswith(text) for item in _SEVERITIES):
                self._candidate = text
                return original_length

            self._line_start = False
            self._write_normal(text)
            return original_length

        return original_length

    def flush(self) -> None:
        if self._candidate:
            self._stream.write(self._candidate)
            self._candidate = ""
            self._line_start = False
        self._stream.flush()

    def __getattr__(self, name: str):
        return getattr(self._stream, name)


def configure(time_short: bool) -> None:
    """Install presentation wrappers when explicitly requested or useful on a TTY."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        colour = _colour_enabled(stream)
        if isinstance(stream, SeverityStream):
            stream.configure(time_short=time_short, colour=colour)
        elif time_short or colour:
            setattr(sys, name, SeverityStream(stream, time_short=time_short, colour=colour))


def enable_short_time_prefix() -> None:
    """Enable timestamp prefixes and propagate the selected presentation to child CMRU processes."""
    os.environ[_TIME_ENV] = "1"
    configure(True)


def configure_from_environment() -> None:
    """Apply an inherited timestamp choice without inspecting command arguments."""
    configure(os.environ.get(_TIME_ENV) == "1")
