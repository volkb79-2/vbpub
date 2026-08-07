"""assay's committed Python canary fixture (P09, A-105/A-107): a real,
minimal package whose only function is fully exercised by
tests/test_greet.py, proven through the genuine R0+R1 pipeline as the
KNOWN-GOOD control half of the cause-sensitive canary (O1)."""
from __future__ import annotations


def greet(name: str) -> str:
    """Return a friendly greeting."""
    return f"Hello, {name}!"
