"""Shared parsing for Linux ``/proc/<pid>/stat`` records."""

from __future__ import annotations

from typing import Optional, Tuple


def split_after_comm(
    text: str,
) -> Optional[Tuple[str, ...]]:
    """Return fields beginning at stat field 3 after the final ``)``.

    Field 2 (``comm``) may contain whitespace and closing parentheses. The
    final closing parenthesis is therefore the delimiter for the fixed fields
    that follow it.
    """
    close = text.rfind(")")
    if close < 0:
        return None
    return tuple(text[close + 1 :].split())
