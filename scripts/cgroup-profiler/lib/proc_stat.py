"""Shared parsing for Linux ``/proc/<pid>/stat`` records."""

from __future__ import annotations

from typing import Optional, Tuple


def split_after_comm(
    text: str,
) -> Optional[Tuple[int, Tuple[str, ...]]]:
    """Return the final ``)`` offset and fields beginning at stat field 3.

    Field 2 (``comm``) may contain whitespace and closing parentheses. The
    final closing parenthesis is therefore the delimiter for the fixed fields
    that follow it. The offset is returned so callers can preserve any
    additional validation they impose on the record prefix.
    """
    close = text.rfind(")")
    if close < 0:
        return None
    return close, tuple(text[close + 1 :].split())
