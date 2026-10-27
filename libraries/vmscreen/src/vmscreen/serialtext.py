"""Pure rendering of a serial console log (-serial file:...) into lines of text."""
from __future__ import annotations

import re

_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_ESC2 = re.compile(r"\x1b[()#][0-9A-Za-z]|\x1b[=>78DEHMNOc]")


def render(raw: bytes) -> str:
    """Strip escape sequences and apply CR/BS/LF/TAB; the result is what the guest wrote, line by line.

    This is a log renderer, not a terminal emulator: cursor addressing (full-screen TUIs) is dropped,
    so a curses screen on a serial line is NOT reconstructed."""
    text = raw.decode("utf-8", errors="replace")
    text = _OSC.sub("", text)
    text = _CSI.sub("", text)
    text = _ESC2.sub("", text)
    lines: list[list[str]] = [[]]
    col = 0
    for ch in text:
        if ch == "\n":
            lines.append([])
            col = 0
        elif ch == "\r":
            col = 0
        elif ch == "\b":
            col = max(0, col - 1)
        elif ch == "\t":
            for _ in range(8 - col % 8):
                _put(lines[-1], col, " ")
                col += 1
        elif ch >= " ":
            _put(lines[-1], col, ch)
            col += 1
    return "\n".join("".join(line).rstrip() for line in lines).rstrip("\n")


def _put(line: list[str], col: int, ch: str) -> None:
    while len(line) < col:
        line.append(" ")
    if col < len(line):
        line[col] = ch
    else:
        line.append(ch)
