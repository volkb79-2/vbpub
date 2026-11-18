"""B245 U4: the ``cargo-mutants-outcomes`` R0 result-report reader.

Every case is REAL cargo-mutants output under
``tests/fixtures/rust/cargo-mutants`` (PROVENANCE.md); the refusal cases are
edits of ``full``, and each says which key it edits. The grammar itself is
``mutation_parsers.cargo_mutants_outcomes.load_document``'s and is tested in
``tests/parsers/mutation``; what is asserted here is that this reader
SURFACES those refusals as the registry's :class:`ReportUnusable` (never an
``AssayError``: an unusable report falls back to the exit code, SR-2).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from assay.errors import AssayError
from assay.result_reports import (
    RESULT_REPORT_FORMATS,
    ReportSummary,
    ReportUnusable,
    read_verified,
)
from assay.result_reports.cargo_mutants_outcomes import (
    CARGO_MUTANTS_CLOCK_TOLERANCE_SECONDS,
    FORMAT,
    ReportWindow,
    read,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "rust" / "cargo-mutants"
UTC = timezone.utc


def _raw(case: str) -> bytes:
    return (FIXTURES / case / "mutants.out" / "outcomes.json").read_bytes()


def _edited(case: str = "full", **top: object) -> bytes:
    document = json.loads(_raw(case))
    document.update(top)
    return json.dumps(document).encode()


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


#: ``full``'s own stamps (06:03:23.470100552Z to 06:03:53.332175732Z).
FULL_START = _at("2026-11-18T06:03:23.470100Z")
FULL_END = _at("2026-11-18T06:03:53.332175Z")
SLACK = timedelta(seconds=CARGO_MUTANTS_CLOCK_TOLERANCE_SECONDS)


def _window(started: datetime = FULL_START, ended: datetime = FULL_END) -> ReportWindow:
    return ReportWindow(started=started, ended=ended)


# --- the real cases ------------------------------------------------------------


@pytest.mark.parametrize(
    ("case", "total"),
    [
        pytest.param("full", 26, id="full"),
        pytest.param("unicode", 5, id="unicode"),
        pytest.param("paths", 11, id="paths"),
        pytest.param("methods", 12, id="methods"),
    ],
)
def test_a_green_baseline_reads_as_one_scenario_plus_every_mutant(case: str, total: int):
    summary = read(_raw(case))

    assert summary == ReportSummary(format=FORMAT, total=total, failed=0, finished=True)


@pytest.mark.parametrize("case", ["basefail-test", "basefail-build"])
def test_a_failed_baseline_is_one_failed_scenario_of_one(case: str):
    """Design R4: ``total = 1`` (not 0), so the report clears
    ``verify_complete`` and R0's FAIL is decided BY THE REPORT."""
    verified = read_verified(FORMAT, _raw(case))

    assert verified == ReportSummary(format=FORMAT, total=1, failed=1, finished=True)


def test_the_registry_reads_bytes_only_and_the_vocabulary_names_the_format():
    assert FORMAT == "cargo-mutants-outcomes"
    assert FORMAT in RESULT_REPORT_FORMATS
    assert read_verified(FORMAT, _raw("full")).total == 26


def test_the_reader_does_not_mutate_the_shared_parse():
    """The memoised tree is shared with ``parse_directory``; two reads of the
    same bytes agree and neither can change what the other sees."""
    assert read(_raw("full")) == read(_raw("full"))


# --- the window (design 1.3) ---------------------------------------------------


def test_a_report_inside_its_command_window_is_accepted():
    assert read(_raw("full"), window=_window()).finished is True


def test_the_tolerance_is_five_seconds_each_side_and_fixed():
    assert CARGO_MUTANTS_CLOCK_TOLERANCE_SECONDS == 5


def test_the_window_edges_are_inclusive_on_both_sides():
    # the command started 5 s AFTER the report's start and ended 5 s BEFORE
    # its end: exactly the tolerance, still accepted.
    window = _window(FULL_START + SLACK, FULL_END - SLACK)
    assert read(_raw("full"), window=window).finished is True


def test_nanosecond_stamps_are_truncated_to_microseconds_before_comparing():
    """``end_time`` ends ``.332175732``; a window ending 5 s before the
    truncated value (``.332175``) is on the edge, and only truncation (not
    rounding up) keeps it there."""
    assert read(_raw("full"), window=_window(FULL_START + SLACK, FULL_END - SLACK))


def test_a_report_that_ended_after_the_window_by_more_than_the_tolerance_refuses():
    window = _window(FULL_START, FULL_END - SLACK - timedelta(microseconds=1))

    with pytest.raises(ReportUnusable, match="not this command's report"):
        read(_raw("full"), window=window)


def test_a_report_that_started_before_the_window_by_more_than_the_tolerance_refuses():
    window = _window(FULL_START + SLACK + timedelta(microseconds=1), FULL_END)

    with pytest.raises(ReportUnusable, match="outside this command's window"):
        read(_raw("full"), window=window)


def test_a_canned_report_from_another_day_refuses():
    day = timedelta(days=1)

    with pytest.raises(ReportUnusable, match="not this command's report"):
        read(_raw("full"), window=_window(FULL_START + day, FULL_END + day))


def test_a_report_whose_end_precedes_its_start_refuses_even_inside_the_window():
    raw = _edited(
        start_time="2026-11-18T06:03:50.000000000Z",
        end_time="2026-11-18T06:03:40.000000000Z",
    )

    with pytest.raises(ReportUnusable, match="not this command's report"):
        read(raw, window=_window())


def test_a_fractionless_and_a_short_fraction_stamp_are_read():
    raw = _edited(
        start_time="2026-11-18T06:03:24Z", end_time="2026-11-18T06:03:53.3Z"
    )

    assert read(raw, window=_window()).total == 26


@pytest.mark.parametrize("field", ["start_time", "end_time"])
@pytest.mark.parametrize(
    "bad",
    [
        pytest.param("yesterday", id="not-rfc3339"),
        pytest.param("2026-11-18T06:03:53+00:00", id="offset-not-Z"),
        pytest.param("2026-13-45T06:03:53Z", id="impossible-date"),
    ],
)
def test_an_unreadable_stamp_refuses_when_a_window_is_checked(field: str, bad: str):
    raw = _edited(**{field: bad})

    with pytest.raises(ReportUnusable, match="timestamp|real date"):
        read(raw, window=_window())


def test_without_a_window_only_completeness_is_checked():
    """The registry's bytes-only call: a report from another day is complete."""
    assert read(_edited(start_time="x", end_time="y")).total == 26


def test_a_window_needs_aware_ordered_datetimes():
    with pytest.raises(ValueError, match="timezone-aware"):
        ReportWindow(started=datetime(2026, 11, 18), ended=FULL_END)
    with pytest.raises(ValueError, match="timezone-aware"):
        ReportWindow(started=FULL_START, ended=datetime(2026, 11, 18))
    with pytest.raises(ValueError, match="cannot end before"):
        ReportWindow(started=FULL_END, ended=FULL_START)


# --- refusals the shared grammar owns, surfaced as ReportUnusable --------------


def _refused(raw: bytes, match: str) -> ReportUnusable:
    with pytest.raises(ReportUnusable, match=match) as caught:
        read_verified(FORMAT, raw)
    # The registry's refusal type, chained to the grammar's own error, and
    # never an ``AssayError`` escaping to R0.
    assert not isinstance(caught.value, AssayError)
    assert isinstance(caught.value.__cause__, AssayError)
    return caught.value


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b"", id="empty"),
        pytest.param(b"not json at all", id="prose"),
        pytest.param(_raw("full")[:200], id="truncated"),
        pytest.param(b"[" * 100_000, id="pathologically-nested"),
    ],
)
def test_a_document_that_is_not_json_refuses(raw: bytes):
    _refused(raw, "not valid JSON")


def test_a_duplicate_key_refuses():
    _refused(b'{"end_time": null, "end_time": "2026-11-18T06:03:53Z"}', "repeats the key")


def test_an_unknown_top_level_key_refuses():
    _refused(_edited(surprise=1), "exactly the keys")


def test_a_non_object_document_refuses():
    _refused(b"[]", "must be an object")


def test_an_unfinished_run_refuses_as_incomplete():
    _refused(_edited(end_time=None), "end_time is null")


def test_an_unqualified_major_refuses():
    _refused(_edited(cargo_mutants_version="26.0.0"), "qualified major")


def test_a_baseline_that_is_not_first_refuses():
    document = json.loads(_raw("full"))
    document["outcomes"].append(document["outcomes"].pop(0))

    _refused(json.dumps(document).encode(), "Baseline as its first outcome")


def test_a_total_that_disagrees_with_the_outcomes_refuses():
    _refused(_edited(total_mutants=24), "total_mutants")


def test_a_failed_baseline_with_mutants_refuses():
    document = json.loads(_raw("full"))
    document["outcomes"][0]["summary"] = "Failure"
    for phase in document["outcomes"][0]["phase_results"]:
        phase["process_status"] = {"Failure": 1}

    _refused(json.dumps(document).encode(), "failed Baseline")
