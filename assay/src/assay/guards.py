"""Shared value predicates for the judge's producer side (B129, I2).

Each function is the body of an inline idiom that used to be spelled out at many
call sites (``isinstance(v, bool) or not isinstance(v, int)`` and its relatives).
Each keeps the inline code's short-circuit order and raises only what the inline
code raised: ``is_finite_positive(10**400)`` and ``is_positive_or_inf(10**400)``
raise :class:`OverflowError` (an int beyond float range), and nothing here catches
it.

A core leaf module: stdlib imports only. ``assay.verify`` deliberately imports
neither this module nor :mod:`assay.records` (A-182: the raw verifier shares no
code with the producer it checks), so it keeps its own private twins
(``tests/core/test_trust_boundary.py``).
"""

from __future__ import annotations

import math
from datetime import datetime

__all__ = [
    "is_aware",
    "is_finite_positive",
    "is_int_at_least",
    "is_nonempty_str",
    "is_percentage",
    "is_positive_or_inf",
    "is_real",
    "is_sha256_hex",
    "is_strict_int",
]


def is_strict_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def is_int_at_least(v: object, minimum: int) -> bool:
    return is_strict_int(v) and v >= minimum  # type: ignore[operator]


def is_real(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def is_finite_positive(v: object) -> bool:
    return is_real(v) and math.isfinite(v) and v > 0  # type: ignore[arg-type,operator]


def is_positive_or_inf(v: object) -> bool:
    return is_real(v) and (math.isfinite(v) or v == math.inf) and v > 0  # type: ignore[arg-type,operator]


def is_nonempty_str(v: object) -> bool:
    return isinstance(v, str) and v != ""


def is_sha256_hex(v: object) -> bool:
    return isinstance(v, str) and len(v) == 64 and all(c in "0123456789abcdef" for c in v)


def is_aware(m: datetime) -> bool:
    return m.tzinfo is not None and m.tzinfo.utcoffset(m) is not None


def is_percentage(v: float) -> bool:
    return 0.0 <= v <= 100.0
