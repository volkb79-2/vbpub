"""The shared producer-side value predicates (B129, W10 I2, oracle O1).

Every comparison is exercised on both sides, and every operand with a value that
alone decides the result, so a flipped comparison or a dropped operand is caught
here rather than at one of the many call sites. The expectations are the inline
idioms these functions replaced, written out as literal cases.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone, tzinfo

import pytest

from assay import guards
from assay.guards import (
    is_aware,
    is_finite_positive,
    is_int_at_least,
    is_nonempty_str,
    is_percentage,
    is_positive_or_inf,
    is_real,
    is_sha256_hex,
    is_strict_int,
)

HEX64 = "0123456789abcdef" * 4
NAN = float("nan")
INF = math.inf


def test_the_module_exports_exactly_the_nine_predicates():
    assert sorted(guards.__all__) == [
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


@pytest.mark.parametrize(
    "value, expected",
    [
        (0, True),
        (1, True),
        (-1, True),
        (10**400, True),
        (True, False),
        (False, False),
        (1.5, False),
        (1.0, False),
        (NAN, False),
        ("1", False),
        (None, False),
    ],
)
def test_is_strict_int(value, expected):
    assert is_strict_int(value) is expected


@pytest.mark.parametrize("minimum", [0, 1, 5])
def test_is_int_at_least_accepts_the_minimum_and_above_and_refuses_below(minimum):
    assert is_int_at_least(minimum - 1, minimum) is False
    assert is_int_at_least(minimum, minimum) is True
    assert is_int_at_least(minimum + 1, minimum) is True


@pytest.mark.parametrize("value", [True, False, 1.5, 2.0, NAN, INF, "1", None])
def test_is_int_at_least_refuses_a_non_strict_int_however_large(value):
    assert is_int_at_least(value, 0) is False
    assert is_int_at_least(value, -10) is False


def test_is_int_at_least_true_is_not_one():
    assert is_int_at_least(True, 1) is False
    assert is_int_at_least(False, 0) is False


@pytest.mark.parametrize(
    "value, expected",
    [
        (0, True),
        (-1, True),
        (1, True),
        (1.5, True),
        (-0.0, True),
        (NAN, True),
        (INF, True),
        (-INF, True),
        (10**400, True),
        (True, False),
        (False, False),
        ("1", False),
        (None, False),
        (b"1", False),
    ],
)
def test_is_real(value, expected):
    assert is_real(value) is expected


@pytest.mark.parametrize(
    "value, expected",
    [
        (1, True),
        (0.5, True),
        (1e300, True),
        (0, False),
        (0.0, False),
        (-0.0, False),
        (-1, False),
        (-0.5, False),
        (NAN, False),
        (INF, False),
        (-INF, False),
        (True, False),
        (False, False),
        ("1", False),
        (None, False),
    ],
)
def test_is_finite_positive(value, expected):
    assert is_finite_positive(value) is expected


def test_is_finite_positive_lets_an_int_beyond_float_range_raise_overflow():
    with pytest.raises(OverflowError):
        is_finite_positive(10**400)


def test_is_finite_positive_does_not_reach_the_math_call_for_a_non_number():
    assert is_finite_positive("x") is False
    assert is_finite_positive(True) is False


@pytest.mark.parametrize(
    "value, expected",
    [
        (1, True),
        (0.5, True),
        (INF, True),
        (0, False),
        (-0.0, False),
        (-1, False),
        (-INF, False),
        (NAN, False),
        (True, False),
        (False, False),
        ("1", False),
        (None, False),
    ],
)
def test_is_positive_or_inf(value, expected):
    assert is_positive_or_inf(value) is expected


def test_is_positive_or_inf_lets_an_int_beyond_float_range_raise_overflow():
    with pytest.raises(OverflowError):
        is_positive_or_inf(10**400)


@pytest.mark.parametrize(
    "value, expected",
    [
        ("x", True),
        (" ", True),
        ("0", True),
        ("", False),
        (0, False),
        (None, False),
        (b"x", False),
        (["x"], False),
    ],
)
def test_is_nonempty_str(value, expected):
    assert is_nonempty_str(value) is expected


@pytest.mark.parametrize(
    "value, expected",
    [
        (HEX64, True),
        ("0" * 64, True),
        ("f" * 64, True),
        (HEX64[:63], False),
        (HEX64 + "0", False),
        (HEX64.upper(), False),
        ("A" + HEX64[1:], False),
        (HEX64 + "\n", False),
        (HEX64[:63] + "\n", False),
        (HEX64[:63] + "g", False),
        ("", False),
        (HEX64.encode(), False),
        (None, False),
        (64, False),
    ],
)
def test_is_sha256_hex(value, expected):
    assert is_sha256_hex(value) is expected


class _NoOffset(tzinfo):
    def utcoffset(self, dt):
        return None

    def dst(self, dt):
        return None

    def tzname(self, dt):
        return None


def test_is_aware_needs_a_tzinfo_whose_utcoffset_is_not_none():
    assert is_aware(datetime(2026, 1, 1)) is False
    assert is_aware(datetime(2026, 1, 1, tzinfo=_NoOffset())) is False
    assert is_aware(datetime(2026, 1, 1, tzinfo=timezone.utc)) is True
    assert is_aware(datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=-5)))) is True


@pytest.mark.parametrize(
    "value, expected",
    [
        (0.0, True),
        (-0.0, True),
        (50.0, True),
        (100.0, True),
        (100.0000001, False),
        (-0.0000001, False),
        (-1.0, False),
        (101.0, False),
        (NAN, False),
        (INF, False),
        (-INF, False),
    ],
)
def test_is_percentage(value, expected):
    assert is_percentage(value) is expected
