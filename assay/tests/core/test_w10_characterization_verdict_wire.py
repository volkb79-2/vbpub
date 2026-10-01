"""B129 / W10 step 1: characterization of the verdict wire validators.

These tests pin, against the UNCHANGED source and byte for byte, which values
each inline guard accepts and which it refuses, with the exact message. They
must pass before and after the guard helpers replace the inline spellings
(step 3), and are never edited to follow the refactor.

Values are chosen so each operand of each guard alone decides the outcome:
``True`` and ``False`` (a bool is not an int for these guards), a float where
an int is required, a value one under, at and one over each bound.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo

import pytest

from assay import verdict as models
from assay.verdict import (
    Coverage,
    JudgeProvenance,
    MutantOutcome,
    MutationWitnessReceipt,
    RefusalDetail,
    SourcePosition,
)

SHA_LTE = "b60080dc8b8982d2a2bff6f8f3715c1939614dc553cd223ef21832b88c815866"


def refusal(call, message: str) -> None:
    """The call raises ValueError whose text is exactly *message*."""
    with pytest.raises(ValueError) as caught:
        call()
    assert str(caught.value) == message


NOT_STRING_OR_EMPTY = [None, 0, 7, b"x", ["x"], ""]
NOT_STRICT_INT = [True, False, 1.5, "1", None, b"1"]


# ---- non-empty string family ---------------------------------------------


@pytest.mark.parametrize("value", NOT_STRING_OR_EMPTY)
def test_check_wire_path_refuses_a_non_string_or_empty_value(value):
    refusal(
        lambda: models._check_wire_path(value, "target"),
        f"target must be a non-empty string, got {value!r}",
    )


def test_check_wire_path_accepts_a_normalized_path():
    assert models._check_wire_path("src/a.py", "target") is None


@pytest.mark.parametrize("value", NOT_STRING_OR_EMPTY)
def test_check_nonempty_refuses_a_non_string_or_empty_value(value):
    refusal(
        lambda: models._check_nonempty(value, "field"),
        f"field must be a non-empty string, got {value!r}",
    )


def test_check_nonempty_accepts_a_one_character_string():
    assert models._check_nonempty("x", "field") is None


@pytest.mark.parametrize("path", [None, 0, b"a", ""])
def test_line_location_mapping_refuses_a_non_string_or_empty_key(path):
    refusal(
        lambda: models._check_line_location_mapping({path: frozenset({1})}, "missing_lines"),
        f"missing_lines key must be a non-empty string, got {path!r}",
    )


def test_line_location_mapping_accepts_a_positive_line_set_and_the_empty_mapping():
    assert models._check_line_location_mapping({"a.py": frozenset({1, 2})}, "m") is None
    assert models._check_line_location_mapping({}, "m") is None


@pytest.mark.parametrize("line", [True, False, 1.5, "1", None, 0, -1])
def test_line_location_mapping_refuses_a_non_positive_or_non_int_line(line):
    refusal(
        lambda: models._check_line_location_mapping({"a.py": frozenset({line})}, "m"),
        f"m['a.py'] must contain only positive line numbers, got {line!r}",
    )


def test_line_location_mapping_accepts_line_one_and_refuses_line_zero():
    assert models._check_line_location_mapping({"a.py": frozenset({1})}, "m") is None
    with pytest.raises(ValueError):
        models._check_line_location_mapping({"a.py": frozenset({0})}, "m")


@pytest.mark.parametrize("path", [None, 0, b"a", ""])
def test_file_tuple_refuses_a_non_string_or_empty_entry(path):
    refusal(
        lambda: models._check_file_tuple((path,), "files"),
        f"files entries must be non-empty strings, got {path!r}",
    )


def test_file_tuple_accepts_a_sorted_unique_tuple():
    assert models._check_file_tuple(("a.py", "b.py"), "files") is None
    assert models._check_file_tuple((), "files") is None


# ---- strict-int family, non-negative and positive floors -------------------

COVERAGE_COMMON = {
    "covered": 1,
    "executable": 1,
    "pct": 100.0,
    "considered": 1,
    "missing_lines": {},
    "files_missing_coverage": (),
    "exclusion_capability": "reported",
}


def _coverage(**overrides) -> Coverage:
    return Coverage(**{**COVERAGE_COMMON, **overrides})


def test_coverage_accepts_the_baseline_and_zero_counts():
    assert _coverage().covered == 1
    assert _coverage(covered=0, executable=0, considered=0, pct=100.0).considered == 0


@pytest.mark.parametrize("name", ["covered", "executable", "considered"])
@pytest.mark.parametrize("value", NOT_STRICT_INT)
def test_coverage_counts_refuse_a_bool_or_non_int(name, value):
    refusal(
        lambda: _coverage(**{name: value}),
        f"coverage.{name} must be an integer, got {value!r}",
    )


@pytest.mark.parametrize("name", ["covered", "executable", "considered"])
def test_coverage_counts_refuse_a_negative_count(name):
    refusal(
        lambda: _coverage(**{name: -1}),
        f"coverage.{name} must not be negative, got -1",
    )


@pytest.mark.parametrize("value", [True, False, "1", None, b"1", [1]])
def test_coverage_pct_refuses_a_bool_or_non_number(value):
    refusal(lambda: _coverage(pct=value), f"coverage.pct must be a number, got {value!r}")


@pytest.mark.parametrize(
    ("covered", "value"), [(0, 0), (0, 0.0), (1, 100), (1, 100.0)]
)
def test_coverage_pct_accepts_both_ends_and_int_spellings(covered, value):
    missing = {} if covered else {"a.py": frozenset({1})}
    files = () if covered else ("a.py",)
    coverage = _coverage(
        covered=covered,
        executable=1,
        pct=value,
        missing_lines=missing,
        files_missing_coverage=files,
    )
    assert coverage.pct == value


@pytest.mark.parametrize("value", [-0.0000001, -1, 100.0000001, 101, float("nan"), float("inf"), float("-inf")])
def test_coverage_pct_refuses_a_value_outside_zero_to_hundred(value):
    refusal(
        lambda: _coverage(pct=value),
        f"coverage.pct must be a percentage between 0 and 100, got {value}",
    )


def test_source_position_accepts_line_one_and_refuses_the_rest():
    assert SourcePosition(path="src/a.py", lineno=1).sort_key == ("src/a.py", 1)
    for value in NOT_STRICT_INT:
        refusal(
            lambda value=value: SourcePosition(path="src/a.py", lineno=value),
            f"source position lineno must be an integer, got {value!r}",
        )
    refusal(
        lambda: SourcePosition(path="src/a.py", lineno=0),
        "source position lineno must be >= 1, got 0",
    )


def test_refusal_detail_accepts_zero_dropped_bytes_and_refuses_the_rest():
    assert RefusalDetail(text="t", dropped_bytes=0).dropped_bytes == 0
    for value in NOT_STRICT_INT:
        refusal(
            lambda value=value: RefusalDetail(text="t", dropped_bytes=value),
            f"RefusalDetail.dropped_bytes must be an integer, got {value!r}",
        )
    refusal(
        lambda: RefusalDetail(text="t", dropped_bytes=-1),
        "RefusalDetail.dropped_bytes must not be negative, got -1",
    )


def _outcome(**overrides) -> MutantOutcome:
    fields = dict(
        path="pkg/mod.py",
        lineno=3,
        start_byte=40,
        end_byte=41,
        replacement_sha256=SHA_LTE,
        operator="python:compare-swap",
        description="Lt->LtE",
    )
    fields.update(overrides)
    return MutantOutcome(**fields)


@pytest.mark.parametrize("name", ["lineno", "start_byte", "end_byte"])
@pytest.mark.parametrize("value", NOT_STRICT_INT)
def test_mutant_outcome_positions_refuse_a_bool_or_non_int(name, value):
    refusal(
        lambda: _outcome(**{name: value}),
        f"MutantOutcome.{name} must be an integer, got {value!r}",
    )


def test_mutant_outcome_accepts_the_baseline_and_start_byte_zero():
    assert _outcome().lineno == 3
    assert _outcome(start_byte=0, end_byte=1).start_byte == 0


@pytest.mark.parametrize("value", ["a" * 63, "a" * 65, "A" * 64, "g" * 64, "a" * 64 + "\n", None, 7, ""])
def test_mutant_outcome_replacement_digest_refuses_a_non_lowercase_hex_digest(value):
    refusal(
        lambda: _outcome(replacement_sha256=value),
        f"MutantOutcome.replacement_sha256 must be 64 lowercase hex characters, got {value!r}",
    )


def test_mutant_outcome_replacement_digest_accepts_sixty_four_lowercase_hex():
    assert _outcome(replacement_sha256="0123456789abcdef" * 4).lineno == 3


@pytest.mark.parametrize("value", [0, 2, True, False, 1.0, "1", None])
def test_witness_receipt_exit_statuses_must_equal_exactly_one(value):
    for name in ("session_exit_status", "process_exit_status"):
        fields = dict(
            node_id="tests/test_a.py::t",
            when="call",
            outcome="failed",
            session_exit_status=1,
            process_exit_status=1,
        )
        fields[name] = value
        refusal(
            lambda fields=fields: MutationWitnessReceipt(**fields),
            f"mutation witness {name} must equal 1",
        )


def test_witness_receipt_accepts_exit_status_one():
    receipt = MutationWitnessReceipt(
        node_id="tests/test_a.py::t",
        when="call",
        outcome="failed",
        session_exit_status=1,
        process_exit_status=1,
    )
    assert receipt.session_exit_status == 1


@pytest.mark.parametrize("value", [None, 0, b"x", ""])
def test_witness_receipt_node_id_refuses_a_non_string_or_empty_value(value):
    # ``self.node_id`` is not one of the guard sites the brief lists; its own
    # inline spelling is left alone, and this pins that it stays refused.
    refusal(
        lambda: MutationWitnessReceipt(
            node_id=value,
            when="call",
            outcome="failed",
            session_exit_status=1,
            process_exit_status=1,
        ),
        "mutation witness node_id must be a non-empty string",
    )


# ---- sha256 digest of the judge provenance ---------------------------------


def _provenance(digest) -> JudgeProvenance:
    return JudgeProvenance(
        name="assay",
        version="7.1.0",
        artifact="wheel",
        digest_algorithm="sha256",
        digest=digest,
    )


@pytest.mark.parametrize("digest", ["a" * 63, "a" * 65, "A" * 64, "g" * 64, "a" * 64 + "\n", None, 7, ""])
def test_judge_provenance_digest_refuses_a_non_lowercase_hex_digest(digest):
    refusal(
        lambda: _provenance(digest),
        f"judge_provenance.digest must be exactly 64 lowercase hexadecimal characters, got {digest!r}",
    )


def test_judge_provenance_digest_accepts_every_lowercase_hex_character():
    assert _provenance("0123456789abcdef" * 4).digest == "0123456789abcdef" * 4


# ---- aware datetimes -------------------------------------------------------


class _NoOffset(tzinfo):
    """A tzinfo whose ``utcoffset`` reports no usable offset."""

    def utcoffset(self, dt):
        return None

    def dst(self, dt):
        return None

    def tzname(self, dt):
        return None


def test_iso_utc_formats_an_aware_moment():
    moment = datetime(2026, 9, 1, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    assert models.iso_utc(moment) == "2026-09-01T00:00:00+02:00"


@pytest.mark.parametrize("moment", [datetime(2026, 9, 1), datetime(2026, 9, 1, tzinfo=_NoOffset())])
def test_iso_utc_refuses_a_naive_moment_or_one_with_no_offset(moment):
    refusal(
        lambda: models.iso_utc(moment),
        f"timestamp {moment!r} is naive; an explicit offset is required",
    )


def test_instant_accepts_an_offset_and_refuses_a_naive_spelling():
    assert models._instant("2026-09-01T00:00:00+00:00", "started").utcoffset() == timedelta(0)
    refusal(
        lambda: models._instant("2026-09-01T00:00:00", "started"),
        "started '2026-09-01T00:00:00' carries no usable UTC offset",
    )
