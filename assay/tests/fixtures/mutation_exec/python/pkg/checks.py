"""assay's committed P12 mutation-EXECUTION fixture (A-041/A-067): a real,
minimal package proving genuine KILLED and genuine SURVIVED mutants through
a real ``pytest`` subprocess run -- never faked or mocked.

``is_adult``'s boundary is proven by a real, meaningful assertion: its
``compare-swap`` mutant (``age >= 18`` -> ``age > 18``) is genuinely KILLED,
because flipping the operator flips ``is_adult(18)`` from ``True`` to
``False``, and the test below asserts exactly that boundary. It is
deliberately the ONLY changed-line site declared for this function, so its
mutant count is predictable.

``is_valid_status`` is deliberately hollow-tested: its own ``compare-swap``
mutant (``code == 200`` -> ``code != 200``) genuinely SURVIVES, because the
test below runs the function but never checks WHICH boolean came back, only
that something did -- staging the real ``MUTANTS_SURVIVED`` case P12's own
oracle set requires without inventing one.
"""
from __future__ import annotations


def is_adult(age: int) -> bool:
    """True at and above the legal boundary."""
    if age >= 18:
        return True
    return False


def is_valid_status(code: int) -> bool:
    """Only 200 is a valid status."""
    if code == 200:
        return True
    return False
