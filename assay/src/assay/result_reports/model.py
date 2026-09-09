"""The normalized shape every result-report reader produces, and the ONE
format-agnostic completeness bar every one of them is judged against.

This module is the half of B078 that Checkpoints 2 (``pytest-json-report``)
and 3 (``go test -json``) reuse verbatim: a reader's whole job is to turn one
format's own bytes into a :class:`ReportSummary` (or refuse), and
:func:`verify_complete` then applies the SAME three-bullet bar (SR-2) to every
format. A reader that re-implemented any part of that bar would be a second
place for "verified complete" to mean something slightly different, which is
exactly the drift this split exists to make impossible.

Nothing here raises :class:`~assay.errors.AssayError`. A report that cannot be
used is not an assay ERROR terminal -- it is the absence of extra evidence,
and R0 falls back to A-073's exit-code rule (SR-2). Using the framework's own
refusal type keeps that distinction structural rather than remembered.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "ReportSummary",
    "ReportUnusable",
    "verify_complete",
]


class ReportUnusable(Exception):
    """This report cannot participate in R0's determination.

    Deliberately NOT an :class:`~assay.errors.AssayError`: every raise of this
    means "fall back to the exit code", never "refuse the run". A reader
    raising an ``AssayError`` would turn a malformed third-party artifact into
    an assay ERROR terminal, which SR-2 explicitly rules out -- a report is
    only ever evidence FOR a determination, never against one.
    """


@dataclass(frozen=True, kw_only=True)
class ReportSummary:
    """One test framework's own account of the run it just finished.

    Every field is what the READER extracted from the format's own document;
    nothing here is inferred, defaulted, or reconciled against the process's
    exit code (that comparison is R0's, one layer up, and it is the whole
    point of B078 that the two can legitimately disagree).
    """

    #: the declared format this came from, carried so a message can name it
    format: str
    #: the framework's own total test count. ``verify_complete`` requires this
    #: to be strictly positive: a zero-test report is ``NO_MEASUREMENT``
    #: territory, never something this tiebreak may wave through as a PASS.
    total: int
    #: the framework's own failed-test count. Zero is what makes a
    #: verified-complete report a PASS regardless of the exit code (SR-2).
    failed: int
    #: whether the format's own "this run finished" marker was present --
    #: vitest's top-level ``success`` field, pytest-json-report's
    #: ``exitcode``+``summary`` pair, a terminal per-package event for
    #: ``go test -json``. A truncated write is exactly the state this catches,
    #: and it is per-FORMAT knowledge, which is why the reader supplies it and
    #: this module only enforces it.
    finished: bool


def verify_complete(summary: ReportSummary) -> ReportSummary:
    """SR-2's completeness bar, applied identically to every format.

    Well-formedness (the first bullet) is the reader's own parse succeeding at
    all -- it cannot be checked format-agnostically, because "well-formed" is
    JSON for vitest and NDJSON for Go. The two bullets that CAN be stated once
    are stated once, here:

    * the format's own finished marker was present;
    * the total test count is strictly positive.

    Returns *summary* unchanged on success so a call site can compose
    ``verify_complete(reader.read(raw))`` in one expression; raises
    :class:`ReportUnusable` otherwise.
    """
    if not summary.finished:
        raise ReportUnusable(
            f"{summary.format} report carries no completion marker; the run it "
            f"describes never finished writing"
        )
    if summary.total <= 0:
        raise ReportUnusable(
            f"{summary.format} report claims {summary.total} total tests; a "
            f"report that measured nothing is not evidence of a passing run"
        )
    if summary.failed < 0:
        raise ReportUnusable(
            f"{summary.format} report claims {summary.failed} failed tests, "
            f"which is not a count"
        )
    if summary.failed > summary.total:
        raise ReportUnusable(
            f"{summary.format} report claims {summary.failed} failed of "
            f"{summary.total} total; the two counts contradict each other"
        )
    return summary
