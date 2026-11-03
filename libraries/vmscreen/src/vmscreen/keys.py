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


# QEMU QKeyCode names (qapi/ui.json).  Client-side validation so a typo in a long sequence cannot be half-sent.
QCODES = frozenset(
    "shift shift_r alt alt_r ctrl ctrl_r menu esc minus equal backspace tab bracket_left bracket_right ret semicolon apostrophe "
    "grave_accent backslash comma dot slash asterisk spc caps_lock num_lock scroll_lock kp_divide kp_multiply kp_subtract kp_add "
    "kp_enter kp_decimal sysrq less home pgup pgdn end left up down right insert delete stop again props undo front copy open paste "
    "find cut lf help meta_l meta_r compose pause ro hiragana henkan yen power sleep wake audionext audioprev audiostop audioplay "
    "audiomute volumeup volumedown mediaselect mail calculator computer ac_home ac_back ac_forward ac_refresh ac_bookmarks "
    "f1 f2 f3 f4 f5 f6 f7 f8 f9 f10 f11 f12 f13 f14 f15 f16 f17 f18 f19 f20 f21 f22 f23 f24 "
    "kp_0 kp_1 kp_2 kp_3 kp_4 kp_5 kp_6 kp_7 kp_8 kp_9 0 1 2 3 4 5 6 7 8 9 "
    "a b c d e f g h i j k l m n o p q r s t u v w x y z".split())


def validate_keys(chords: list[list[str]]) -> None:
    for chord in chords:
        for k in chord:
            if k not in QCODES:
                raise ValueError(f"unknown QEMU key name {k!r} (qcodes such as ret, spc, f12, ctrl, alt, shift, left; "
                                 "--allow-unknown-keys skips this check)")


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
