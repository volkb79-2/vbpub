from __future__ import annotations

"""ASCII sparkline helper for compact trend rendering.

Pure ASCII, deterministic, width-bounded.  Designed for use in TUI table
cells and banner lines where Unicode block characters are not acceptable
(intended for --once --json output and other ASCII-only contexts).
"""

# 8-level ASCII ramp: low → high.  These are all printable ASCII characters
# that visually increase in density/height.
_CHARS: tuple[str, ...] = ("_", ",", "-", "~", "=", "+", "%", "#")
_MISSING: str = "."
_N_CHARS: int = len(_CHARS)


def render_sparkline(
    values: list[float | None],
    *,
    width: int = 8,
) -> str:
    """Render a compact ASCII sparkline for *values*, at most *width* chars.

    The series is down-sampled (evenly spaced) to fit *width* when the input
    is longer.  Missing values (``None``) are rendered as ``.`` (period).

    Edge cases
    ----------
    - Empty list returns ``"(empty)"``.
    - All-``None`` list returns *width* copies of ``.``.
    - Flat series (hi == lo) renders the middle character for every sample.
    - Stable and width-bounded: same input -> same output.
    """
    if not values:
        return "(empty)"

    n = len(values)
    finite = [v for v in values if v is not None]

    if not finite:
        # All missing — nothing to map against.
        return _MISSING * width

    lo = min(finite)
    hi = max(finite)
    span = hi - lo

    # Sample the series at *width* equally-spaced positions.
    if n <= width:
        # Short series — render every element directly.
        sampled_indices = list(range(n))
    else:
        sampled_indices = [
            int(round(i * (n - 1) / (width - 1))) for i in range(width)
        ]

    chars: list[str] = []
    for idx in sampled_indices:
        v = values[idx]
        if v is None:
            chars.append(_MISSING)
        elif span == 0:
            # Flat series: use middle character.
            chars.append(_CHARS[_N_CHARS // 2])
        else:
            fraction = (v - lo) / span
            char_idx = int(round(fraction * (_N_CHARS - 1)))
            char_idx = max(0, min(_N_CHARS - 1, char_idx))
            chars.append(_CHARS[char_idx])

    result = "".join(chars)
    if len(result) > width:
        result = result[:width]
    elif len(result) < width:
        result = result + _MISSING * (width - len(result))

    return result


def sparkline_from_history(
    history: list[float | None],
    *,
    width: int = 6,
) -> str:
    """Convenience wrapper: call ``render_sparkline`` then return ``f" [{s}]"``.

    Returns ``""`` (empty string) when *history* is empty or all-None so
    callers can append conditionally without showing bracketed whitespace.
    """
    if not history:
        return ""
    finite = [v for v in history if v is not None]
    if not finite:
        return ""
    s = render_sparkline(history, width=width)
    return f" [{s}]"
