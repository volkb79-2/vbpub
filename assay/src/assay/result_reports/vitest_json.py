"""``vitest-json`` -- vitest's own native ``--reporter=json`` document.

Produced by ``vitest run --reporter=json --outputFile=<path>``: no plugin, no
new dependency for the consuming project (SR-4a). The document is
Jest-compatible, so the fields read here (``numTotalTests``,
``numFailedTests``, ``success``) are the ones both tools have carried for
years rather than a vitest-specific shape that moves between minor versions.

**A-217, "adapt, do not invent"** (``adapters/go_stmtpos.py``'s own words):
this module reads the framework's own maintained, structured report and never
parses its stdout prose. It is also deliberately the ONLY vitest knowledge in
assay -- no stderr signature allowlist (SR-3 rules that out explicitly, and
RG-44 demonstrated the cost of one live, one layer over, the same day).
"""

from __future__ import annotations

import json
from typing import Any

from .model import ReportSummary, ReportUnusable

__all__ = ["FORMAT", "read"]

FORMAT = "vitest-json"


def _count(document: dict[str, Any], field: str) -> int:
    """One non-negative integer count, or refuse naming the field.

    ``bool`` is excluded explicitly: it is an ``int`` subclass in Python, so
    ``{"numTotalTests": true}`` would otherwise read as a one-test run.
    """
    if field not in document:
        raise ReportUnusable(f"{FORMAT} report has no {field!r} field")
    value = document[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReportUnusable(
            f"{FORMAT} report's {field!r} is {type(value).__name__}, not an integer"
        )
    return value


def read(raw: bytes) -> ReportSummary:
    """Parse *raw* into the normalized summary, or raise
    :class:`~assay.result_reports.model.ReportUnusable`.

    Every refusal path here is a fall-back-to-A-073 path, and the three named
    fault-injection shapes land on three different ones:

    * a write truncated mid-flush is not valid JSON -> the decode refuses;
    * a document of the wrong shape (a JSON array, another tool's report)
      has no ``success`` field -> the finished-marker refusal;
    * a run that collected nothing reports ``numTotalTests = 0`` -> caught by
      :func:`~assay.result_reports.model.verify_complete`, not here, because
      that bullet is format-agnostic.

    ``success`` is vitest's own "this run finished" marker: the reporter
    writes it as the last thing it knows, and its PRESENCE (not its value) is
    what SR-2 requires. Its value is deliberately NOT trusted as the verdict
    -- ``success`` is computed by the same orchestrator whose unhandled
    internal error is the defect B078 exists to route around, so the counts
    are read instead.
    """
    try:
        document = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ReportUnusable(f"{FORMAT} report is not valid UTF-8: {exc}") from exc
    except (json.JSONDecodeError, RecursionError) as exc:
        # `RecursionError` belongs in the SAME clause (the estate-wide rule
        # `tests/test_untrusted_json_parse_sweep.py` enforces): a deeply
        # nested document blows CPython's stack inside the decoder, and this
        # is an untrusted third-party artifact. Here it is not merely a
        # crash-vs-refusal question -- an uncaught `RecursionError` would
        # escape R0's own terminal mapping entirely, turning a hostile report
        # into an unhandled exception where every other malformed shape is a
        # quiet fallback to A-073.
        raise ReportUnusable(
            f"{FORMAT} report is not well-formed JSON (a truncated, "
            f"partially-written or pathologically nested report lands here): "
            f"{exc}"
        ) from exc
    if not isinstance(document, dict):
        raise ReportUnusable(
            f"{FORMAT} report's top level is {type(document).__name__}, not an object"
        )
    finished = "success" in document
    if finished and not isinstance(document["success"], bool):
        raise ReportUnusable(
            f"{FORMAT} report's 'success' is "
            f"{type(document['success']).__name__}, not a boolean; this is not "
            f"a vitest JSON report"
        )
    if not finished:
        # Refuse HERE rather than returning `finished=False`, so the counts
        # below are never read out of a document that is not this format at
        # all. `verify_complete` still owns the marker RULE; this is the
        # reader declining to guess what an unrecognised document's integers
        # would mean.
        raise ReportUnusable(
            f"{FORMAT} report has no top-level 'success' field; the run it "
            f"describes never finished, or the document is not a vitest JSON "
            f"report"
        )
    return ReportSummary(
        format=FORMAT,
        total=_count(document, "numTotalTests"),
        failed=_count(document, "numFailedTests"),
        finished=True,
    )
