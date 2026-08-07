"""coverage.py's own JSON report (``coverage json`` / ``pytest
--cov-report=json``).

Format (the part this parser reads; ``meta``/``totals`` are ignored — this
registry emits a normalized per-file model, not a re-serialization of the
artifact)::

    {
      "files": {
        "<path>": {
          "executed_lines": [1, 2, 3],
          "missing_lines": [4, 5],
          "excluded_lines": [6]
        }
      }
    }

coverage.py is the ONE format in this registry that can express an exclusion
(``# pragma: no cover``) as a first-class, dedicated field distinct from
"missing" — so ``FileCoverage.excluded`` here is ALWAYS a ``frozenset``
(possibly empty), never ``None`` (DESIGN-GUIDE §11, A-008). Cross-referenced
against ``topos/tools/coverage_gate.py``'s ``_validate_cov_record`` (handoff
item 4) for the shape of "malformed": both ``executed_lines`` and
``missing_lines`` must be lists of ints, and this parser extends the same
check to ``excluded_lines`` for the same reason topos gives — coverage.py
guarantees the shape, so a deviation means the JSON was tampered with,
misread, or produced by a non-standard tool, and a gate that trusts an
untyped value there risks a silent green (dstdns's own returned
``dict[str, dict]`` shape is exactly what A-092 forbids repeating here).
"""

from __future__ import annotations

import json
from types import MappingProxyType

from ..errors import AssayError, Outcome, ReasonCode
from .model import CoverageProfile, FileCoverage

_SIGNATURE_KEY = '"files"'


def sniff(text: str) -> bool:
    """A JSON object whose text contains the top-level ``"files"`` key
    (DESIGN-GUIDE §5's literal signature: ``{"files":`` → coverage.py JSON).

    Deliberately a cheap substring/prefix check, not a full ``json.loads`` —
    sniffing answers "does this look like the declared format", parsing
    answers "is it well-formed"; collapsing the two would make an
    unparsable-JSON fixture raise ``FORMAT_MISMATCH`` from a hand-rolled
    parser inside the sniffer instead of the real parser's own
    ``UNREADABLE_ARTIFACT`` path.
    """
    stripped = text.lstrip()
    return stripped.startswith("{") and _SIGNATURE_KEY in text


def parse(text: str) -> CoverageProfile:
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _malformed(f"not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise _malformed(f"top level is {type(document).__name__}, expected object")
    files_raw = document.get("files")
    if not isinstance(files_raw, dict):
        raise _malformed(
            f"no top-level 'files' object (got "
            f"{type(files_raw).__name__ if files_raw is not None else 'nothing'})"
        )

    files: dict[str, FileCoverage] = {}
    for path, record in files_raw.items():
        files[path] = _parse_record(path, record)
    return CoverageProfile(files=MappingProxyType(files))


def _parse_record(path: str, record: object) -> FileCoverage:
    if not isinstance(record, dict):
        raise _malformed(
            f"record for {path!r} is {type(record).__name__}, expected object"
        )
    executed = _int_list(record, path, "executed_lines")
    missing = _int_list(record, path, "missing_lines")
    # excluded_lines is the one field a genuinely minimal coverage.py JSON
    # might omit (a report generated with no exclusion patterns configured
    # can still include it as []; some hand-built fixtures might not) — its
    # ABSENCE is not itself malformed, since it is meaningfully identical to
    # "reports zero exclusions" for a format that CAN express them. Only a
    # present-but-wrongly-typed value is rejected.
    if "excluded_lines" not in record:
        excluded: frozenset[int] = frozenset()
    else:
        excluded = frozenset(_int_list(record, path, "excluded_lines"))
    try:
        return FileCoverage(
            executed=frozenset(executed), missing=frozenset(missing), excluded=excluded
        )
    except ValueError as exc:
        # P15 (finding 4): the common model enforces positive line numbers
        # and pairwise-disjoint buckets in ONE place (model.py) rather than
        # per format; this is the one parser whose input (three independent
        # JSON arrays) can actually violate either -- a line claimed both
        # executed and missing simultaneously, or a non-positive line
        # number, both real artifact defects.
        raise _malformed(f"record for {path!r}: {exc}") from exc


def _int_list(record: dict, path: str, key: str) -> list[int]:
    value = record.get(key)
    if value is None:
        raise _malformed(f"record for {path!r} is missing {key!r}")
    if not isinstance(value, list):
        raise _malformed(
            f"record for {path!r}: {key!r} is {type(value).__name__}, expected list"
        )
    for item in value:
        if not isinstance(item, int) or isinstance(item, bool):
            raise _malformed(
                f"record for {path!r}: {key!r} contains "
                f"{type(item).__name__} ({item!r}), expected int"
            )
    return value


def _malformed(message: str) -> AssayError:
    return AssayError(
        f"coverage.py JSON: {message}",
        outcome=Outcome.ERROR,
        reason_code=ReasonCode.UNREADABLE_ARTIFACT,
    )
