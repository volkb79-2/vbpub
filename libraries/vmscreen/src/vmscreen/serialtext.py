"""Pure rendering of a serial console log (-serial file:...) into lines of text.

A LOG renderer, not a terminal emulator.  Handled: LF, CR, BS, TAB, erase-in-line (ESC[K 0/1/2), erase-in-display
(ESC[J 0/1/2/3 and ESC c), which is what a prompt redraw or a screen clear does, and the COLUMN part of cursor movement
(ESC[H/f, G, C, D).  Dropped without effect: colours, OSC titles and vertical movement (ESC[nA/B, row of ESC[r;cH), so a
full-screen curses UI on a serial line is NOT reconstructed.  Columns are clamped to MAX_COL (a longer line is cut), so hostile
escape parameters cannot exhaust memory; rows only grow with the input.  An incomplete escape at the end of the input is dropped.
"""
from __future__ import annotations

import re

MAX_COL = 4096  # cursor movement is clamped: ESC[300000000G must not allocate a 300 MB line
MAX_PARAM = 1_000_000
MAX_CELLS = 4_000_000  # total cells kept: when exceeded the OLDEST lines are dropped (the newest output is what a wait needs)

_TOKEN = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"          # OSC
    r"|\x1b\[([0-?]*)[ -/]*([@-~])"                 # CSI (params, final)
    r"|\x1b([()#][0-9A-Za-z]|[=>78DEHMNOc])"        # short escapes (c = full reset)
    r"|\x1b(?:\[[0-?]*[ -/]*|\][^\x07\x1b]*\x1b?|[()#])?\Z"  # an incomplete sequence at end of input renders as nothing
    r"|(.)", re.S)


def render(raw: bytes) -> str:
    lines: list[list[str]] = [[]]
    col = 0
    total = 0  # cells in the finished lines (the current line is bounded by MAX_COL)
    for m in _TOKEN.finditer(raw.decode("utf-8", errors="replace")):
        params, final, short, ch = m.group(1), m.group(2), m.group(3), m.group(4)
        if final is not None:
            n = min(int(params[:12]), MAX_PARAM) if params.isdigit() else 0  # [:12]: no int() on a 5000-digit parameter
            if final == "K":
                line = lines[-1]
                if n == 0:
                    del line[col:]
                elif n == 1:
                    for i in range(min(col + 1, len(line))):
                        line[i] = " "
                elif n == 2:
                    line.clear()
            elif final in "Hf":  # cursor position: the column counts, the row cannot in a log
                parts = params.split(";")
                col = min(int(parts[1][:12]) - 1, MAX_COL) if len(parts) > 1 and parts[1].isdigit() and int(parts[1][:12]) > 0 else 0
            elif final == "G":
                col = min(max(0, n - 1), MAX_COL) if n else 0
            elif final == "C":
                col = min(col + (n or 1), MAX_COL)
            elif final == "D":
                col = max(0, col - (n or 1))
            elif final == "J":
                if n == 0:
                    del lines[-1][col:]
                elif n == 1:
                    del lines[:-1]
                    total = 0
                    for i in range(min(col + 1, len(lines[-1]))):
                        lines[-1][i] = " "
                elif n in (2, 3):
                    lines[:] = [[]]
                    total = 0
        elif short == "c":
            lines[:] = [[]]
            col = 0
            total = 0
        elif ch is not None:
            if ch == "\n":
                total += len(lines[-1])
                lines.append([])
                col = 0
                if total > MAX_CELLS:
                    drop, freed = 0, 0
                    while total - freed > MAX_CELLS // 2 and drop < len(lines) - 1:
                        freed += len(lines[drop])
                        drop += 1
                    del lines[:drop]
                    total -= freed
            elif ch == "\r":
                col = 0
            elif ch == "\b":
                col = max(0, col - 1)
            elif ch == "\t":
                for _ in range(min(8 - col % 8, MAX_COL - col)):
                    _put(lines[-1], col, " ")
                    col += 1
            elif ch >= " " and col < MAX_COL:  # a line longer than MAX_COL is truncated, not an allocation
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
