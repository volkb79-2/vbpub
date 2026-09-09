"""One module per test-report FORMAT (B078), never per language.

The registry :mod:`assay.runner` reads when a lane declares
``[lanes.<name>.result_report]``. Each sibling module here exports exactly
two names, so a new format is a module plus one line in
:data:`_READERS` and nothing else:

* ``FORMAT: str`` -- the value a lane types in ``result_report.format``;
* ``read(raw: bytes) -> assay.result_reports.model.ReportSummary`` --
  strict parsing of that format's own document, raising
  :class:`~assay.result_reports.model.ReportUnusable` on anything it cannot
  use.

**Why a package of its own, and not ``assay.adapters/``.** ``adapters/`` is
the LANGUAGE registry: keyed by language, and every member implements the
whole ``LanguageAdapter`` protocol (mutant generation, canary injection,
coverage-key normalisation). A test-report reader shares neither that key
space nor that protocol, so adding one there would mean either widening
``LanguageAdapter`` with methods four of its five members cannot answer, or
keeping a second, unrelated kind of object in a namespace whose name promises
the first. ``coverage_parsers/`` and ``mutation_parsers/`` are the real
precedent -- per-ARTIFACT-FORMAT parser packages, sibling to ``adapters/``,
each with a tiny uniform protocol and a ``model.py`` of normalized types --
and this package is deliberately built to look exactly like them.

The dependency graph stays a strict DAG, the same way
:mod:`assay.coverage_parsers` states it: no module here imports a sibling
reader, and nothing here imports :mod:`assay.runner`.

**Absence is never evidence** (SR-2). Every refusal in this package means "R0
falls back to A-073's exit-code rule", never "refuse the run": a report can
only ever be evidence FOR a determination. That is why nothing here raises
:class:`~assay.errors.AssayError`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType

from . import vitest_json
from .model import ReportSummary, ReportUnusable, verify_complete

__all__ = [
    "MAX_RESULT_REPORT_BYTES",
    "RESULT_REPORT_FORMATS",
    "ReportSummary",
    "ReportUnusable",
    "read_verified",
    "verify_complete",
]

#: The fixed ceiling on one result-report read (O4: a FIXED byte bound, never
#: an ambient guess), enforced by :func:`assay.safeio.reserve_output`'s own
#: ``limit + 1`` read. Sized like :data:`assay.runner.MAX_SOURCE_FILE_BYTES`:
#: far above any plausible real report (a 10,000-test vitest document is
#: single-digit MB at worst) so it can never decide a verdict on a legitimate
#: suite, only make the work finite. An oversized report refuses the READ,
#: which -- like every other refusal here -- falls back to A-073.
MAX_RESULT_REPORT_BYTES = 8 * 1024 * 1024

_READERS: Mapping[str, Callable[[bytes], ReportSummary]] = MappingProxyType(
    {
        vitest_json.FORMAT: vitest_json.read,
    }
)

#: The closed vocabulary a lane may type in ``result_report.format``, DERIVED
#: from the registry rather than hand-listed beside it -- a hand-listed copy
#: is how a format becomes declarable before its reader exists.
RESULT_REPORT_FORMATS: frozenset[str] = frozenset(_READERS)


def read_verified(format: str, raw: bytes) -> ReportSummary:
    """*raw*, read as *format* and checked against SR-2's completeness bar.

    The one composition point: a caller never invokes a reader directly, so
    "was this report verified complete" cannot become a question each call
    site answers for itself. Raises
    :class:`~assay.result_reports.model.ReportUnusable` for every reason a
    report cannot be used, including an unregistered *format* -- which
    :mod:`assay.config` already refuses at load time, so reaching it here
    would mean the two vocabularies had drifted apart.
    """
    reader = _READERS.get(format)
    if reader is None:
        raise ReportUnusable(
            f"no reader is registered for result-report format {format!r}; "
            f"registered formats: {sorted(RESULT_REPORT_FORMATS)}"
        )
    return verify_complete(reader(raw))
