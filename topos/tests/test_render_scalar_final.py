"""Final scalar-formatting boundary contracts for the text renderer."""

from __future__ import annotations

from topos.render import _format_number


class _UnsupportedScalar:
    """A JSONable-adjacent value whose display spelling is explicit for this test."""

    def __str__(self) -> str:
        return "unsupported-scalar"


def test_scalar_formatter_distinguishes_missing_from_an_unsupported_value() -> None:
    """None is absent, while other scalar-like inputs retain their display value."""
    assert _format_number(None) == "missing"
    assert _format_number(_UnsupportedScalar()) == "unsupported-scalar"
