"""Small shared helpers for removing secret values from captured text."""

from __future__ import annotations

from typing import Sequence


def redact_passthrough_text(text: str, values: Sequence[str]) -> str:
    """Mask exact passthrough values while preserving UTF-8 byte width."""
    covered = bytearray(len(text))
    candidates = {
        value.encode("utf-8", errors="surrogateescape").decode(
            "utf-8", errors="replace"
        )
        for value in values
        if value
    }
    for candidate in candidates:
        offset = 0
        while True:
            start = text.find(candidate, offset)
            if start < 0:
                break
            covered[start : start + len(candidate)] = b"\x01" * len(candidate)
            offset = start + 1
    if not any(covered):
        return text
    return "".join(
        "*" * len(character.encode("utf-8")) if covered[index] else character
        for index, character in enumerate(text)
    )
