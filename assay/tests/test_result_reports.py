"""B078 -- the format-agnostic completeness core and the ``vitest-json``
reader, at the unit level.

``tests/test_runner_result_report.py`` proves what R0 DOES with a report;
this module proves the split the design rests on: a reader turns one format's
bytes into a normalized summary or refuses, and
:func:`~assay.result_reports.model.verify_complete` applies SR-2's bar to
every format identically. Checkpoints 2 (``pytest-json-report``) and 3
(``go test -json``) add a reader and reuse this core untouched, so the core's
contract is asserted here against a synthetic summary rather than only
through vitest's shape -- a core that could only be exercised through one
format would not be a core.
"""

from __future__ import annotations

import json

import pytest

from assay.result_reports import (
    MAX_RESULT_REPORT_BYTES,
    RESULT_REPORT_FORMATS,
    ReportUnusable,
    read_verified,
)
from assay.result_reports.model import ReportSummary, verify_complete
from assay.result_reports.vitest_json import FORMAT, read


def summary(**overrides) -> ReportSummary:
    fields = {"format": "synthetic", "total": 10, "failed": 0, "finished": True}
    fields.update(overrides)
    return ReportSummary(**fields)


# --- the format-agnostic core ------------------------------------------------


def test_a_complete_summary_passes_through_unchanged():
    complete = summary()

    assert verify_complete(complete) is complete


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        pytest.param({"finished": False}, "no completion marker", id="unfinished"),
        pytest.param({"total": 0}, "0 total tests", id="zero-tests"),
        pytest.param({"total": -1}, "-1 total tests", id="negative-total"),
        pytest.param({"failed": -1}, "not a count", id="negative-failed"),
        pytest.param(
            {"total": 3, "failed": 9}, "contradict each other", id="counts-contradict"
        ),
    ],
)
def test_the_core_refuses_every_incomplete_shape(overrides: dict, expected: str):
    """Each bullet SR-2 states is a separate refusal with its own sentence --
    one collapsed "bad report" message would tell a lane author nothing about
    which of their assumptions is wrong."""
    with pytest.raises(ReportUnusable) as caught:
        verify_complete(summary(**overrides))

    assert expected in str(caught.value)


def test_the_core_never_raises_an_assay_error():
    """A malformed third-party artifact is never an assay ERROR terminal: it
    is the absence of extra evidence, and R0 falls back to A-073. Asserted
    structurally, because the distinction is the whole reason
    :class:`ReportUnusable` exists rather than reusing ``AssayError``."""
    from assay.errors import AssayError

    assert not issubclass(ReportUnusable, AssayError)


# --- the vitest-json reader --------------------------------------------------


def document(**overrides) -> bytes:
    body = {
        "success": True,
        "numTotalTests": 140,
        "numPassedTests": 140,
        "numFailedTests": 0,
    }
    body.update(overrides)
    return json.dumps(body).encode("utf-8")


def test_the_reader_extracts_the_counts_not_the_success_flag():
    """``success`` is the finished MARKER; the counts are the verdict. A
    reader that returned ``success`` would inherit exactly the orchestrator
    bug B078 exists to route around."""
    result = read(document(success=True, numTotalTests=140, numFailedTests=3))

    assert result.format == FORMAT
    assert result.total == 140
    assert result.failed == 3
    assert result.finished is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(b'{"success": true, "numTotal', "well-formed JSON", id="truncated"),
        pytest.param(b"[]", "not an object", id="json-array"),
        pytest.param(b"null", "not an object", id="json-null"),
        pytest.param(b'{"stats": {}}', "no top-level 'success'", id="another-tool"),
        pytest.param(b'{"success": "yes"}', "not a boolean", id="success-not-bool"),
        pytest.param(
            b'{"success": true, "numFailedTests": 0}',
            "no 'numTotalTests' field",
            id="total-missing",
        ),
        pytest.param(
            b'{"success": true, "numTotalTests": true, "numFailedTests": 0}',
            "not an integer",
            id="bool-is-not-a-count",
        ),
        pytest.param(b"\xff\xfe", "not valid UTF-8", id="not-utf8"),
    ],
)
def test_the_reader_refuses_every_unusable_document(raw: bytes, expected: str):
    with pytest.raises(ReportUnusable) as caught:
        read(raw)

    assert expected in str(caught.value)


# --- the registry composition point -----------------------------------------


def test_read_verified_composes_the_reader_and_the_core():
    result = read_verified("vitest-json", document())

    assert result.total == 140 and result.failed == 0


def test_read_verified_applies_the_core_to_a_well_formed_but_empty_report():
    """The zero-test bullet is enforced through the registry, not only when a
    caller remembers to call the core itself."""
    with pytest.raises(ReportUnusable):
        read_verified("vitest-json", document(numTotalTests=0, numPassedTests=0))


def test_an_unregistered_format_refuses_rather_than_guessing():
    with pytest.raises(ReportUnusable) as caught:
        read_verified("pytest-json-report", document())

    assert "no reader is registered" in str(caught.value)
    assert "pytest-json-report" in str(caught.value)


def test_the_registered_vocabulary_is_exactly_checkpoint_one():
    """Checkpoints 2 and 3 are explicitly NOT this wave. This asserts it: a
    format declarable before its reader ships would be a lane that silently
    falls back forever."""
    assert RESULT_REPORT_FORMATS == frozenset({"vitest-json"})


def test_the_read_bound_is_a_fixed_positive_ceiling():
    assert MAX_RESULT_REPORT_BYTES == 8 * 1024 * 1024
