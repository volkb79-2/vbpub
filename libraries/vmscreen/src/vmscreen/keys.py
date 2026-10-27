"""Key-sequence parsing for QMP ``send-key`` (US keyboard layout, ASCII only)."""
from __future__ import annotations

import time
from typing import Callable

_SHIFT = dict(zip("!@#$%^&*()", "1234567890"))
_PUNCT = {" ": "spc", "-": "minus", "=": "equal", ".": "dot", ",": "comma", "/": "slash", ";": "semicolon",
          "'": "apostrophe", "[": "bracket_left", "]": "bracket_right", "\\": "backslash", "`": "grave_accent",
          "\n": "ret", "\t": "tab"}
_SHIFTP = {"_": "minus", "+": "equal", ":": "semicolon", '"': "apostrophe", "<": "comma", ">": "dot", "?": "slash",
           "{": "bracket_left", "}": "bracket_right", "|": "backslash", "~": "grave_accent"}


def text_to_keys(text: str) -> list[list[str]]:
    """One key chord (list of qcodes) per character; raises ValueError for anything outside US ASCII."""
    out: list[list[str]] = []
    for ch in text:
        if ch.isascii() and ch.isalpha():
            out.append(["shift", ch.lower()] if ch.isupper() else [ch])
        elif ch.isascii() and ch.isdigit():
            out.append([ch])
        elif ch in _SHIFT:
            out.append(["shift", _SHIFT[ch]])
        elif ch in _SHIFTP:
            out.append(["shift", _SHIFTP[ch]])
        elif ch in _PUNCT:
            out.append([_PUNCT[ch]])
        else:
            raise ValueError(f"unmappable character {ch!r} (US-layout ASCII only)")
    return out


def parse_keys(spec: str) -> list[list[str]]:
    """``ret,down,ctrl+alt+f2`` -> [[ret],[down],[ctrl,alt,f2]]: comma = successive sends, plus = chord."""
    seqs = []
    for part in spec.split(","):
        chord = [k.strip() for k in part.split("+")]
        if not part.strip() or any(not k for k in chord):
            raise ValueError(f"empty key in {spec!r}")
        seqs.append(chord)
    return seqs


def send_sequence(client, chords: list[list[str]], delay: float = 0.05, hold_ms: int | None = None,
                  sleep: Callable[[float], None] = time.sleep) -> int:
    for i, chord in enumerate(chords):
        args: dict = {"keys": [{"type": "qcode", "data": k} for k in chord]}
        if hold_ms is not None:
            args["hold-time"] = hold_ms
        client.execute("send-key", **args)
        if delay and i < len(chords) - 1:
            sleep(delay)
    return len(chords)
