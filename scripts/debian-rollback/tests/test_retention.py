"""D30 count-selection cases, restated over managed ``StateId`` values."""

from __future__ import annotations

import unittest

import _support  # noqa: F401
from dr_state import ids
from dr_state.retention import select_prune

A = "@os-000002-20261118-100000Z-apt"
B = "@os-000003-20261118-100100Z-apt"
C = "@os-000004-20261118-100200Z-apt"
D = "@os-000005-20261118-100300Z-apt"
E = "@os-000006-20261118-100400Z-manual-s8"
F = "@os-000007-20261118-100500Z-apt"
MAIN = "@os-000001-20261118-120000Z-install"


def parsed(names: tuple[str, ...]) -> tuple[ids.StateId, ...]:
    result = tuple(ids.parse(name) for name in names)
    assert all(state is not None for state in result)
    return result  # type: ignore[return-value]


def selected(
    available: tuple[str, ...],
    keep: int,
    protected: tuple[str, ...] = (),
    pinned: tuple[str, ...] = (),
) -> str:
    return " ".join(
        state.name
        for state in select_prune(
            parsed(available), keep, protected=protected, pinned=pinned
        )
    )


class RetentionSelectionTest(unittest.TestCase):
    def test_d30_count_cases(self) -> None:
        cases = (
            ("keep-two", (A, B, C, D, E), 2, (), (), f"{A} {B} {C}"),
            ("order-independent", (E, C, A, D, B), 2, (), (), f"{A} {B} {C}"),
            ("fewer-than-keep", (A, B, C), 10, (), (), ""),
            ("exactly-keep", (A, B, C), 3, (), (), ""),
            ("protected-oldest", (A, B, C, D, E), 2, (A,), (), f"{B} {C}"),
            ("protected-middle", (A, B, C, D, E), 2, (B,), (), f"{A} {C}"),
            ("protected-newest", (A, B, C, D, E), 2, (E,), (), f"{A} {B} {C}"),
            ("several-protected", (A, B, C, D, E), 2, (A, B), (), C),
            ("keep-zero", (A, B, C, D, E), 0, (A,), (), f"{B} {C} {D} {E}"),
            ("pinned-main", (MAIN, A, B, C), 2, (), (MAIN,), A),
            ("only-pinned-main", (MAIN,), 2, (), (MAIN,), ""),
            ("empty-protected", (A, B), 1, (), (), A),
            ("no-names", (), 3, (), (), ""),
            ("sequence-not-clock", (A, F, B), 2, (), (), A),
            ("pinned-now", (A, B, C, D), 2, (), (B,), A),
        )
        for label, available, keep, protected, pinned, wanted in cases:
            with self.subTest(label=label):
                self.assertEqual(selected(available, keep, protected, pinned), wanted)

    def test_large_keep_selects_nothing(self) -> None:
        self.assertEqual(selected((A, B, C), 10_000), "")

    def test_invalid_keep_is_refused(self) -> None:
        for value in (-1, True, 1.5):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "non-negative integer"):
                    select_prune(parsed((A,)), value)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
