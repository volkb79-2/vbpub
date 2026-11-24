"""Console presentation for CIU's severity-prefixed output.

CIU keeps ``[INFO]``/``[WARN]``/``[ERROR]`` plain and stable for logs and
automation. The shared cli-extended renderer owns line boundaries, severity
recognition, and input-escape sanitisation; this boundary adapter adds CIU's
explicitly requested short time prefix and its resolved colour policy.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from typing import TextIO

from cli_extended.line_stream import LineStreamRenderer


# The renderer emits an SGR decoration before a recognised severity. The
# sanitizer has already removed input-origin ESC bytes, so any remaining SGR at
# line start belongs to the renderer. The adapter uses that boundary to place
# the timestamp and honour CIU's final colour decision; it does not inspect or
# classify severity text.
_RENDERED_SEVERITY = re.compile(r"^(\033\[[0-9;]*m)([^\x1b\n]*)(\033\[0m)")
_TIME_ENV = "CIU_LOG_PREFIX_TIME_SHORT"


def _colour_enabled(stream: TextIO) -> bool:
    """CIU's no-color.org policy for automatic terminal colour."""
    try:
        is_tty = bool(getattr(stream, "isatty", lambda: False)())
    except (AttributeError, OSError):
        return False
    return (
        is_tty
        and not os.environ.get("NO_COLOR", "")
        and os.environ.get("TERM", "").lower() != "dumb"
    )


class _TimePrefixSink:
    """Apply CIU's timestamp/final-colour policy to renderer output."""

    def __init__(self, stream: TextIO, *, time_short: bool, colour: bool) -> None:
        self._stream = stream
        self._time_short = time_short
        self._colour = colour
        self._at_line_start = True

    def configure(self, *, time_short: bool, colour: bool) -> None:
        self._time_short = time_short
        self._colour = colour

    def write(self, text: str) -> int:
        remaining = text
        while remaining:
            newline = remaining.find("\n")
            if newline < 0:
                segment = remaining
                remaining = ""
            else:
                segment = remaining[: newline + 1]
                remaining = remaining[newline + 1 :]
            if self._at_line_start:
                segment = self._decorate(segment)
            self._stream.write(segment)
            self._at_line_start = segment.endswith("\n")
        return len(text)

    def flush(self) -> None:
        self._stream.flush()

    def _decorate(self, segment: str) -> str:
        match = _RENDERED_SEVERITY.match(segment)
        if match is None:
            return segment
        marker, prefix, reset = match.groups()
        rest = segment[match.end() :]
        if self._time_short:
            prefix = f"{datetime.now().astimezone():%H:%M:%S} {prefix}"
        if not self._colour:
            marker = reset = ""
        return f"{marker}{prefix}{reset}{rest}"


class SeverityStream:
    """Thin adapter over cli-extended's shared severity line renderer."""

    def __init__(self, stream: TextIO, *, time_short: bool, colour: bool) -> None:
        self._sink = _TimePrefixSink(stream, time_short=time_short, colour=colour)
        # Always decorates internally so the adapter can use the shared
        # renderer's own marker to locate the prefix without parsing severity
        # text; the sink strips it when CIU's resolved policy is plain.
        self._renderer = LineStreamRenderer(self._sink, color=True, json_mode=False)

    def configure(self, *, time_short: bool, colour: bool) -> None:
        self._sink.configure(time_short=time_short, colour=colour)

    def write(self, text: str) -> int:
        return self._renderer.write(text)

    def flush(self) -> None:
        self._renderer.flush()

    def __getattr__(self, name: str):
        return getattr(self._sink._stream, name)


def configure(time_short: bool) -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        colour = _colour_enabled(stream)
        if isinstance(stream, SeverityStream):
            stream.configure(time_short=time_short, colour=colour)
        elif time_short or colour:
            setattr(sys, name, SeverityStream(stream, time_short=time_short, colour=colour))
