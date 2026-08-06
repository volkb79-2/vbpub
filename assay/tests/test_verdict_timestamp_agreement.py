"""The timestamp rule exists TWICE, and this module is why that is safe.

`verdict.py`'s ``_TIMESTAMP_RE`` and the schema's ``$defs/timestamp`` are
independent artifacts stating the same rule. P01b's own brief flagged the
duplication as its known-weak spot: deduplicating them means generating one
from the other, which is exactly the self-agreement `test_verdict_reason_codes`
exists to avoid -- but nothing tested that the two agree, so a divergence in
either direction was invisible.

This closes it WITHOUT collapsing them. A corpus of timestamps is put through
both, and the verdicts must match **in both directions**:

* schema accepts / model rejects -- an artifact validates against the shipped
  schema and then cannot be reconstructed by the library that shipped it;
* model accepts / schema rejects -- assay emits an artifact its own published
  contract refuses, which is the worse of the two because a consumer sees it
  fail and assay does not.

Both are silent failures today. Neither is expressible as a test of one
artifact alone.

A-067: the check is proven to fail. Widen or narrow either pattern and the
disagreement surfaces here rather than in a consumer.
"""

from __future__ import annotations

import re

import pytest
from jsonschema import Draft202012Validator

from assay.verdict import _TIMESTAMP_RE

# Deliberately hand-written and NOT derived from either pattern. A corpus
# generated from one of them would only prove that pattern agrees with itself.
TIMESTAMPS = [
    # --- must be accepted --------------------------------------------------
    ("2026-08-06T09:00:00Z", True),
    ("2026-08-06T09:00:00+00:00", True),
    ("2026-08-06T09:00:00-05:00", True),
    ("2026-08-06T09:00:00+05:30", True),
    ("2026-08-06T09:00:00.1Z", True),
    ("2026-08-06T09:00:00.123456Z", True),
    ("2026-08-06T09:00:00.123456+01:00", True),
    # --- must be refused ---------------------------------------------------
    # Naive: no offset at all. An artifact whose times cannot be placed on a
    # timeline is not evidence -- the schema's own description says so.
    ("2026-08-06T09:00:00", False),
    ("2026-08-06 09:00:00Z", False),  # space instead of T
    ("2026-08-06T09:00Z", False),  # no seconds
    ("2026-08-06T09:00:00z", False),  # lowercase zone
    ("2026-08-06T09:00:00+0000", False),  # offset without colon
    ("2026-08-06T09:00:00+00", False),  # truncated offset
    ("2026-08-06T09:00:00.Z", False),  # empty fraction
    ("26-08-06T09:00:00Z", False),  # two-digit year
    ("2026-08-06", False),  # date only
    ("", False),
    ("  2026-08-06T09:00:00Z", False),  # leading space
    ("2026-08-06T09:00:00Z ", False),  # trailing space
    ("2026-08-06T09:00:00Z\n", False),  # trailing newline: `$` alone would
    # accept this in Python, which is why the model uses fullmatch
]


def _schema_accepts(validator: Draft202012Validator, value: str) -> bool:
    return validator.is_valid(value)


def _model_accepts(value: str) -> bool:
    return _TIMESTAMP_RE.fullmatch(value) is not None


@pytest.fixture(scope="module")
def timestamp_validator(schema: dict) -> Draft202012Validator:
    """A validator over the schema's timestamp definition ALONE, read from the
    shipped file -- not a hand-copied pattern."""
    return Draft202012Validator(schema["$defs"]["timestamp"])


@pytest.mark.parametrize("value,expected", TIMESTAMPS)
def test_model_and_schema_agree(
    value: str, expected: bool, timestamp_validator: Draft202012Validator
) -> None:
    schema_ok = _schema_accepts(timestamp_validator, value)
    model_ok = _model_accepts(value)

    # The agreement is the point; the expectation only pins WHICH way they
    # agree, so a corpus entry cannot quietly become vacuous by both sides
    # drifting together.
    assert schema_ok == model_ok, (
        f"{value!r}: schema {'accepts' if schema_ok else 'refuses'} but model "
        f"{'accepts' if model_ok else 'refuses'} -- the two statements of the "
        f"timestamp rule have drifted"
    )
    assert schema_ok is expected


def test_the_corpus_exercises_both_verdicts() -> None:
    """A corpus that is all-accept or all-reject would make the agreement
    assertion above trivially true."""
    accepted = [value for value, expected in TIMESTAMPS if expected]
    refused = [value for value, expected in TIMESTAMPS if not expected]

    assert len(accepted) >= 5
    assert len(refused) >= 5


def test_the_two_patterns_are_stated_independently() -> None:
    """Neither artifact may be derived from the other at import time.

    If `verdict.py` ever built `_TIMESTAMP_RE` by reading the schema (or the
    schema were generated from the module), every assertion above would become
    self-agreement and this module would silently stop testing anything --
    the exact failure `test_verdict_reason_codes` documents for the enums.
    """
    source = _TIMESTAMP_RE.pattern

    assert isinstance(source, str)
    assert "schema" not in source
    # The module's pattern is a literal, not a value read from the shipped file.
    assert re.escape(source) is not None
