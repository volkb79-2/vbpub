"""Go coverprofile parser (``go test -coverprofile=...``).

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
"""

from __future__ import annotations

from types import MappingProxyType

from ..errors import AssayError, Outcome, ReasonCode
from .model import CoverageProfile, FileCoverage

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
    for raw_line in lines[1:]:
        path, start, end, count = _parse_block(raw_line)
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
