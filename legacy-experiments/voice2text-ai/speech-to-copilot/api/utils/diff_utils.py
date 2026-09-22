"""Utility functions for incremental transcription diffing.

Provides a stable, testable implementation for computing the incremental
suffix between the previous transcript and the new transcript.
"""
from __future__ import annotations

def compute_incremental_suffix(previous: str, current: str) -> tuple[str, int]:
    """Return the suffix of `current` that was not present in `previous`.

    Strategy: longest common prefix. This is sufficient for an append-only
    streaming model (whisper batch recomputations). If future logic introduces
    mid-string edits, this function can be swapped for a diff algorithm.

    Parameters
    ----------
    previous: str
        The last acknowledged full transcript.
    current: str
        The new full transcript candidate.

    Returns
    -------
    (suffix, prefix_len): tuple[str, int]
        suffix: the new incremental part (may be empty string)
        prefix_len: length of the matched common prefix
    """
    if not current:
        return "", 0
    if not previous:
        return current, 0

    common_prefix_len = 0
    for a_char, b_char in zip(previous, current):
        if a_char == b_char:
            common_prefix_len += 1
        else:
            break
    return current[common_prefix_len:], common_prefix_len

__all__ = ["compute_incremental_suffix"]
