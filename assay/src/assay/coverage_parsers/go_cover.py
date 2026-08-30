"""
STALE PREMISE, TRACKED (A-234/A-217). The reasoning below states that "a block
spans [startLine, endLine] and every line in that range is executable". A-172's
probe disproved exactly that: a block carries a positional extent plus a
statement COUNT, never the statements' own positions, so the expansion here is a
strict over-approximation that attributes function signatures, closing braces and
statement continuations. A-218 closed the inclusive-versus-half-open question as
inert; A-217 ruled option 2. This file has an explicit scope status for the
re-carve per A-217(b). Do not re-derive the correction from this docstring.

**B039/B047 item 4.** This module's own block-range expansion below (``for
file_line in range(start, end + 1)``) had no bound at all until this wave: a
single ~60-byte block line reading ``pkg/x.go:1.1,999999999.1 1 1`` sits far
inside the 16 MiB ``MAX_COVERAGE_ARTIFACT_BYTES`` read bound and would
materialize close to a billion dict entries — the identical shape
:mod:`.coverage_istanbul_json` was given a fixed ceiling for precisely
because "the shape is dangerous," while this parser's own equivalent
expansion shipped with none. `parse` now spends
:class:`~assay.coverage_parsers.model.ClassifiedLineBudget` (the ONE shared
bound both expanding parsers enforce, per B039's own acceptance box 2) before
materializing each block's range, refusing ``ERROR``/``UNREADABLE_ARTIFACT``
past it exactly as the istanbul parser does.

Go coverprofile parser (``go test -coverprofile=...``).

Format: a ``mode: <mode>`` header line, then one BLOCK per subsequent line::

    <path>:<startLine>.<startCol>,<endLine>.<endCol> <numStmts> <count>

Ported from the REASONING in
``/workspaces/vbpub/shared-ramdisk-depot-manager/tools/covergate/profile.go``
(handoff item 5), not its Go code — assay has no Go toolchain (A-042, §10)
and this is pure text parsing; no ``go`` binary is invoked anywhere in this
module or its tests.

Two things that Go implementation gets right and this keeps:

* the profile is BLOCK-based, not line-based: a block spans
  ``[startLine, endLine]`` and every line in that range is executable, so a
  block covering several lines expands to all of them. Two blocks can
  disagree about the SAME line (e.g. an ``if``/``else`` split across two
  blocks, one taken and one not) — this is DESIGN-GUIDE §11's "including
  multiple blocks per line" case — and EXECUTED WINS: once any block marks a
  line executed, a later never-taken block covering that same line must not
  downgrade it back to missing;
* the position spec is split on the LAST colon in the block's first
  whitespace-delimited field, not the first — a Windows path carries its own
  colon after the drive letter (``C:\\pkg\\file.go:10.5,12.9 1 1``), so
  splitting on the FIRST colon would cut the path in half instead of finding
  the ``<line>.<col>`` separator that actually delimits path from position.

Go's coverprofile format has no exclusion concept at all — no pragma, no
field anywhere in the grammar that could carry one — so
``FileCoverage.excluded`` is always ``None`` here (DESIGN-GUIDE §11's own
worked example: "a Go cover profile has no such concept").

**Branch capability (wave-1 §3.3, A-257) is always ``None``, unconditionally.**
A cover profile's records are ``<path>:<startLine>.<startCol>,<endLine>.
<endCol> <numStmts> <count>``: a BLOCK's position and a STATEMENT COUNT,
never an arc between two branch targets. There is no field anywhere in this
grammar that could distinguish "this block's untaken exit went to the
`else`" from "this block's untaken exit fell through" -- the format simply
does not carry that information, so ``FileCoverage.branches`` is always
``None`` here, exactly as ``excluded`` is, and for the identical reason:
faking a ``BranchCoverage`` from block extents would be the exact
``declared_unverified``-class lie A-O12 was corrected for. This makes the
capability a MEASURED property of the format (a test asserts it directly)
rather than an omission that later looks like an oversight (A-O16's exact
failure).
"""

from __future__ import annotations

from types import MappingProxyType

from ..errors import AssayError, Outcome, ReasonCode
from .model import ClassifiedLineBudget, CoverageProfile, FileCoverage
from .model import MAX_CLASSIFIED_LINES as MAX_CLASSIFIED_LINES

_MODE_PREFIX = "mode:"


def sniff(text: str) -> bool:
    """The first non-blank line starts with ``mode:`` (DESIGN-GUIDE §5's
    literal signature: ``mode:`` → go-cover)."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        return stripped.startswith(_MODE_PREFIX)
    return False


def parse(text: str) -> CoverageProfile:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or not lines[0].startswith(_MODE_PREFIX):
        raise _malformed("profile has no 'mode:' header line")

    # line number -> 1 (executed) or 0 (missing), across every block seen for
    # that line in the whole profile (a path can also recur across multiple
    # block lines, not only within one block's own range).
    hits_by_file: dict[str, dict[int, int]] = {}
    budget = ClassifiedLineBudget(
        format_name="go coverprofile", remaining=MAX_CLASSIFIED_LINES
    )
    for raw_line in lines[1:]:
        path, start, end, count = _parse_block(raw_line)
        budget.spend(end - start + 1, path)
        hits = hits_by_file.setdefault(path, {})
        for file_line in range(start, end + 1):
            already_executed = hits.get(file_line) == 1
            if count > 0:
                hits[file_line] = 1
            elif not already_executed:
                hits[file_line] = 0

    files = {
        path: FileCoverage(
            executed=frozenset(n for n, c in h.items() if c == 1),
            missing=frozenset(n for n, c in h.items() if c == 0),
            excluded=None,
            branches=None,
        )
        for path, h in hits_by_file.items()
    }
    return CoverageProfile(files=MappingProxyType(files))


def _parse_block(line: str) -> tuple[str, int, int, int]:
    fields = line.split()
    if len(fields) != 3:
        raise _malformed(f"want 3 fields, got {len(fields)}: {line!r}")
    position_field, num_stmts_field, count_field = fields

    # A path may itself contain a colon (a Windows drive letter) — the
    # position spec is delimited by the LAST colon, never the first.
    idx = position_field.rfind(":")
    if idx < 0:
        raise _malformed(f"no position in {position_field!r}")
    path = position_field[:idx]
    spec = position_field[idx + 1:]
    if not path:
        raise _malformed(f"no path before the position spec in {position_field!r}")

    if "," not in spec:
        raise _malformed(f"no range in {spec!r}")
    start_spec, end_spec = spec.split(",", 1)
    start = _parse_pos(start_spec)
    end = _parse_pos(end_spec)
    if end < start:
        raise _malformed(f"block ends ({end}) before it starts ({start})")

    if not num_stmts_field.isdigit():
        raise _malformed(f"bad numStmts {num_stmts_field!r}")

    try:
        count = int(count_field)
    except ValueError as exc:
        raise _malformed(f"bad count {count_field!r}") from exc
    if count < 0:
        raise _malformed(f"negative count {count}")
    return path, start, end, count


def _parse_pos(spec: str) -> int:
    if "." not in spec:
        raise _malformed(f"bad position {spec!r}")
    line_str, _, _ = spec.partition(".")
    try:
        value = int(line_str)
    except ValueError as exc:
        raise _malformed(f"bad line number in {spec!r}") from exc
    if value <= 0:
        raise _malformed(f"line number {value} in {spec!r} is not positive")
    return value


def _malformed(message: str) -> AssayError:
    return AssayError(
        f"go coverprofile: {message}",
        outcome=Outcome.ERROR,
        reason_code=ReasonCode.UNREADABLE_ARTIFACT,
    )
