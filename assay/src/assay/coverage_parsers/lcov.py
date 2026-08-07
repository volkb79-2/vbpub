"""lcov ``.info`` tracefile parser — the ``SF:``/``DA:``/``end_of_record``
line syntax (geninfo's own format: https://ltp.sourceforge.net/coverage/lcov/
geninfo.1.php). No prior art anywhere in this estate (handoff item 6: zero
hits for ``lcov``/``SF:`` across dstdns, topos, nyxloom and
shared-ramdisk-depot-manager) — built from the public spec, and this format's
fixtures are independently hand-written (A-080), not copied from anywhere.

lcov is emitted by C, C++, Rust, PHP and TypeScript toolchains (DESIGN-GUIDE
§11) — the concrete case for a registry keyed by FORMAT rather than bound to
one of those five languages.

A record is delimited ``SF:<path>`` … ``end_of_record``; within it, the line
records this parser reads are::

    DA:<line number>,<execution count>[,<checksum>]

Every other record type (``TN:``, ``FN:``, ``FNDA:``, ``FNF:``, ``FNH:``,
``BRDA:``, ``BRF:``, ``BRH:``, ``VER:``, …) is real, legal lcov syntax this
parser does not need for a line-coverage model, and is ignored rather than
rejected — rejecting an unrecognised-but-legal record type would make this
parser STRICTER than lcov itself, which is not the strictness this registry
is for (that strictness is reserved for the ``DA:``/``SF:``/
``end_of_record`` structure this parser actually depends on).

``FileCoverage.excluded`` is ALWAYS ``None`` for this format (DESIGN-GUIDE
§11, A-008): a line skipped via a source-level exclusion marker (e.g.
``LCOV_EXCL_LINE``, processed by the ``lcov``/``geninfo`` COMMAND before the
``.info`` file is ever written) simply never gets a ``DA:`` record — by the
time this parser sees the tracefile, that line is indistinguishable from a
line that was never code at all. The format has no per-line field that means
"excluded", so this parser cannot report "zero exclusions" any more than it
can report "three exclusions" — the honest answer is ``None``, not
``frozenset()``.
"""

from __future__ import annotations

from types import MappingProxyType

from ..errors import AssayError, Outcome, ReasonCode
from .model import CoverageProfile, FileCoverage

_SF_PREFIX = "SF:"
_DA_PREFIX = "DA:"
_END_RECORD = "end_of_record"


def sniff(text: str) -> bool:
    """Any line starting with ``SF:`` (DESIGN-GUIDE §5's literal signature)."""
    return any(line.startswith(_SF_PREFIX) for line in text.splitlines())


def parse(text: str) -> CoverageProfile:
    files: dict[str, FileCoverage] = {}
    current_path: str | None = None
    # line number -> summed hit count across every DA: record seen for it in
    # the CURRENT open SF:/end_of_record block. geninfo can legally emit more
    # than one DA: for the same line (most often after merging tracefiles);
    # summing and then testing > 0 is what makes "multiple records for one
    # line" resolve the same way regardless of how the counts were split.
    current_hits: dict[int, int] = {}

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith(_SF_PREFIX):
            if current_path is not None:
                raise _malformed(
                    f"line {lineno}: 'SF:' opened while a previous record "
                    f"for {current_path!r} was never closed with "
                    f"'end_of_record'"
                )
            current_path = line[len(_SF_PREFIX):]
            if not current_path:
                raise _malformed(f"line {lineno}: 'SF:' names no path")
            current_hits = {}
            continue
        if line == _END_RECORD:
            if current_path is None:
                raise _malformed(
                    f"line {lineno}: 'end_of_record' with no open 'SF:' record"
                )
            files[current_path] = _finish_record(current_hits)
            current_path = None
            current_hits = {}
            continue
        if line.startswith(_DA_PREFIX):
            if current_path is None:
                raise _malformed(f"line {lineno}: 'DA:' outside any 'SF:' record")
            file_line, hit_count = _parse_da(line[len(_DA_PREFIX):], lineno)
            current_hits[file_line] = current_hits.get(file_line, 0) + hit_count
            continue
        # TN:/FN:/FNDA:/FNF:/FNH:/BRDA:/BRF:/BRH:/VER:/... — legal, ignored.

    if current_path is not None:
        raise _malformed(
            f"'SF:' record for {current_path!r} never closed with "
            f"'end_of_record'"
        )
    return CoverageProfile(files=MappingProxyType(files))


def _parse_da(fields_text: str, lineno: int) -> tuple[int, int]:
    fields = fields_text.split(",")
    if len(fields) < 2:
        raise _malformed(
            f"line {lineno}: 'DA:' needs at least <line>,<hits>, got "
            f"{fields_text!r}"
        )
    try:
        file_line = int(fields[0])
        hit_count = int(fields[1])
    except ValueError as exc:
        raise _malformed(
            f"line {lineno}: 'DA:' fields must be integers, got "
            f"{fields[0]!r},{fields[1]!r}"
        ) from exc
    if file_line <= 0:
        raise _malformed(f"line {lineno}: 'DA:' line number {file_line} is not positive")
    if hit_count < 0:
        raise _malformed(f"line {lineno}: 'DA:' hit count {hit_count} is negative")
    return file_line, hit_count


def _finish_record(hits: dict[int, int]) -> FileCoverage:
    executed = frozenset(line for line, count in hits.items() if count > 0)
    missing = frozenset(line for line, count in hits.items() if count <= 0)
    return FileCoverage(executed=executed, missing=missing, excluded=None)


def _malformed(message: str) -> AssayError:
    return AssayError(
        f"lcov tracefile: {message}",
        outcome=Outcome.ERROR,
        reason_code=ReasonCode.UNREADABLE_ARTIFACT,
    )
