"""``cargo-mutants-outcomes`` -- the R0 result-report reader for
``mutants.out/outcomes.json`` (B245 U4).

cargo-mutants runs its own BASELINE (the unmutated test suite) before any
mutant, and writes the verdict of that run into ``outcomes.json``. R0 for a
Rust R2 lane means "the command ran and its baseline was green", so the
report is read as a test-report of ``1 + total_mutants`` scenarios in which at
most the baseline can have failed:

* ``total = 1 + total_mutants`` (the baseline counts as a scenario, so even a
  zero-mutant run has ``total = 1`` and clears ``verify_complete``);
* ``failed = 0`` when the Baseline summary is ``Success``, else ``1``.

**Why a failed baseline is decided by the report (design R4).** With this
``total`` a baseline failure (cargo-mutants exit 4) is a verified-complete
report with ``failed = 1``, so R0 is a FAIL decided by the report
(``CommandResult.decided_by_report``), distinguishable from an exit-code FAIL.
A refusal riding exit 4 would lose its reason in the runner's
``_consume_result_report``.

**Grammar is not re-implemented here.** ``mutation_parsers.cargo_mutants_outcomes.load_document`` owns the closed grammar, the duplicate
key refusal, the size bound, the Baseline-first rule and the ``end_time``
null refusal, and is memoised on the bytes, so this reader, ``diff_paths`` and
``parse_directory`` share ONE parse of one document (design 7.14). Its
``AssayError`` refusals are surfaced here as :class:`ReportUnusable`, the
registry's refusal type (R0 falls back to the exit code; R2 carries the
reason, design 1.4).

**The time window (design 1.3).** ``finished`` means ``end_time`` is set AND
the document's ``[start_time, end_time]`` lies inside the command's own
window widened by :data:`CARGO_MUTANTS_CLOCK_TOLERANCE_SECONDS` on each side.
A report outside it is "not this command's report" (a canned report copied
into place). The registry convention is ``read(raw)`` with bytes only and
``read_verified(format, raw)`` has no way to carry a window yet, so the
window is a keyword-only parameter of :func:`read` defaulting to ``None``
("no window supplied: the bytes-only registry call"). Passing the runner's
window through ``read_verified`` is U5's wiring (design 1.5); until then the
registry path performs no window comparison.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from ..errors import AssayError
from ..mutation_parsers.cargo_mutants_outcomes import load_document
from ..records import record
from .model import ReportSummary, ReportUnusable

__all__ = [
    "CARGO_MUTANTS_CLOCK_TOLERANCE_SECONDS",
    "FORMAT",
    "ReportWindow",
    "read",
]

FORMAT = "cargo-mutants-outcomes"

#: Slack on each side of the command's window. cargo-mutants stamps its times
#: from the same host clock the runner reads, so this absorbs only rounding
#: and process start-up, and it is a fixed constant: it must never become
#: load-dependent (design R10), nor be loosened for a test.
CARGO_MUTANTS_CLOCK_TOLERANCE_SECONDS = 5

#: RFC 3339 UTC with an optional fraction of up to nanoseconds, as cargo-mutants
#: writes it (``2026-11-18T04:36:08.039112716Z``).
_TIMESTAMP_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?Z"
)


@record
class ReportWindow:
    """The wall-clock interval the command ran in, as the runner measured it
    (``CommandResult.started`` / ``ended``). Both must be timezone-aware."""

    started: datetime
    ended: datetime

    def __post_init__(self) -> None:
        if self.started.utcoffset() is None or self.ended.utcoffset() is None:
            raise ValueError("a report window needs timezone-aware datetimes")
        if self.ended < self.started:
            raise ValueError("a report window cannot end before it starts")


def _timestamp(value: str, field: str) -> datetime:
    """*value* as an aware UTC datetime, fraction truncated to microseconds."""
    match = _TIMESTAMP_RE.fullmatch(value)
    if match is None:
        raise ReportUnusable(
            f"{FORMAT} report's {field} {value!r} is not an RFC 3339 UTC "
            f"timestamp"
        )
    year, month, day, hour, minute, second = (int(part) for part in match.groups()[:6])
    micros = int((match.group(7) or "").ljust(9, "0")[:6])
    try:
        return datetime(
            year, month, day, hour, minute, second, micros, tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise ReportUnusable(
            f"{FORMAT} report's {field} {value!r} is not a real date: {exc}"
        ) from exc


def _check_window(start_time: str, end_time: str, window: ReportWindow) -> None:
    slack = timedelta(seconds=CARGO_MUTANTS_CLOCK_TOLERANCE_SECONDS)
    start = _timestamp(start_time, "start_time")
    end = _timestamp(end_time, "end_time")
    if not window.started - slack <= start <= end <= window.ended + slack:
        raise ReportUnusable(
            f"{FORMAT} report ran {start_time} to {end_time}, outside this "
            f"command's window {window.started.isoformat()} to "
            f"{window.ended.isoformat()} (+/- "
            f"{CARGO_MUTANTS_CLOCK_TOLERANCE_SECONDS} s); it is not this "
            f"command's report"
        )


def read(raw: bytes, *, window: ReportWindow | None = None) -> ReportSummary:
    """Parse *raw* into the normalized summary, or raise
    :class:`~assay.result_reports.model.ReportUnusable`.

    Every grammar, size, duplicate-key, Baseline-first and unfinished
    (``end_time`` null) refusal comes from the shared ``load_document`` and is
    re-raised as :class:`ReportUnusable` with its message. With a *window*, the
    report's own ``start_time``/``end_time`` must lie inside it (see the module
    docstring); without one, only completeness is checked.
    """
    try:
        document = load_document(raw)
    except AssayError as exc:
        raise ReportUnusable(str(exc)) from exc
    if window is not None:
        _check_window(document["start_time"], document["end_time"], window)
    baseline = document["outcomes"][0]
    return ReportSummary(
        format=FORMAT,
        total=1 + document["total_mutants"],
        failed=0 if baseline["summary"] == "Success" else 1,
        finished=True,
    )
