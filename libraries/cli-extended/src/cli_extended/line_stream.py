"""Incremental, terminal-safe severity rendering for text streams."""

from __future__ import annotations

from typing import TextIO

__all__ = ["LineStreamRenderer"]

_SEVERITY_COLOUR = {
    "[INFO]": "\033[32m",
    "[WARN]": "\033[33m",
    "[ERROR]": "\033[31m",
}
_RESET = "\033[0m"

_NORMAL = 0
_ESCAPE = 1
_CSI = 2
_OSC = 3
_OSC_ESCAPE = 4


class _InputSanitizer:
    """Drop input-origin terminal escapes and unsafe control characters."""

    def __init__(self) -> None:
        self._state = _NORMAL

    def feed(self, text: str) -> str:
        safe: list[str] = []
        for character in text:
            self._feed_character(character, safe)
        return "".join(safe)

    def finish(self) -> None:
        """Discard an incomplete escape sequence at the end of a flush."""

        self._state = _NORMAL

    def _feed_character(self, character: str, safe: list[str]) -> None:
        if self._state == _NORMAL:
            self._normal(character, safe)
        elif self._state == _ESCAPE:
            if character in ("[", "\x9b"):
                self._state = _CSI
            elif character in ("]", "\x9d"):
                self._state = _OSC
            else:
                self._state = _NORMAL
                self._normal(character, safe)
        elif self._state == _CSI:
            code = ord(character)
            if 0x40 <= code <= 0x7E:
                self._state = _NORMAL
            elif character == "\x1b":
                self._state = _ESCAPE
            elif 0x20 <= code <= 0x3F:
                return
            else:
                self._state = _NORMAL
                self._normal(character, safe)
        elif self._state == _OSC:
            if character in ("\x07", "\x9c"):
                self._state = _NORMAL
            elif character == "\x1b":
                self._state = _OSC_ESCAPE
        else:
            if character == "\\":
                self._state = _NORMAL
            else:
                self._state = _OSC
                if character in ("\x07", "\x9c"):
                    self._state = _NORMAL
                elif character == "\x1b":
                    self._state = _OSC_ESCAPE

    def _normal(self, character: str, safe: list[str]) -> None:
        if character == "\x1b":
            self._state = _ESCAPE
            return
        if character == "\x9b":
            self._state = _CSI
            return
        if character == "\x9d":
            self._state = _OSC
            return
        code = ord(character)
        if code < 0x20 and character not in ("\n", "\t", "\r"):
            return
        if 0x7F <= code <= 0x9F:
            return
        safe.append(character)


class LineStreamRenderer:
    """Render textual severity prefixes without letting input escapes through.

    The renderer owns line boundaries and partial-line buffering. Its caller
    resolves colour and JSON policy; this class never reads the environment or
    probes the destination stream.
    """

    def __init__(
        self, stream: TextIO, *, color: bool, json_mode: bool
    ) -> None:
        self._stream = stream
        self._color = color
        self._json_mode = json_mode
        self._sanitizer = _InputSanitizer()
        self._partial_line: list[str] = []
        self._at_line_start = True

    def write(self, text: str) -> int:
        """Consume text, emitting only complete or flushed logical lines."""

        original_length = len(text)
        rendered: list[str] = []
        for character in self._sanitizer.feed(text):
            if character == "\n":
                rendered.append(self._take_line())
                rendered.append("\n")
                self._at_line_start = True
            else:
                self._partial_line.append(character)
        if rendered:
            self._stream.write("".join(rendered))
        return original_length

    def flush(self) -> None:
        """Render the unterminated line, dropping any incomplete input escape."""

        self._sanitizer.finish()
        if self._partial_line:
            self._stream.write(self._take_line())
            self._at_line_start = False
        self._stream.flush()

    def _take_line(self) -> str:
        line = "".join(self._partial_line)
        self._partial_line.clear()
        if not self._at_line_start:
            return line
        self._at_line_start = False
        if not self._color or self._json_mode:
            return line
        for severity, colour in _SEVERITY_COLOUR.items():
            if line.startswith(severity):
                return f"{colour}{severity}{_RESET}{line[len(severity):]}"
        return line
