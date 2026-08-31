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
    BRDA:<line number>,<block>,<branch>,<taken>
    BRF:<branches found>
    BRH:<branches hit>

Every other record type (``TN:``, ``FN:``, ``FNDA:``, ``FNF:``, ``FNH:``,
``VER:``, …) is real, legal lcov syntax this parser does not need for either
model, and is ignored rather than rejected — rejecting an unrecognised-but-
legal record type would make this parser STRICTER than lcov itself, which is
not the strictness this registry is for (that strictness is reserved for the
``DA:``/``BRDA:``/``SF:``/``end_of_record`` structure this parser actually
depends on).

``FileCoverage.excluded`` is ALWAYS ``None`` for this format (DESIGN-GUIDE
§11, A-008): a line skipped via a source-level exclusion marker (e.g.
``LCOV_EXCL_LINE``, processed by the ``lcov``/``geninfo`` COMMAND before the
``.info`` file is ever written) simply never gets a ``DA:`` record — by the
time this parser sees the tracefile, that line is indistinguishable from a
line that was never code at all. The format has no per-line field that means
"excluded", so this parser cannot report "zero exclusions" any more than it
can report "three exclusions" — the honest answer is ``None``, not
``frozenset()``.

**Branch capability (wave-1 §3.2/§3.3) is ARTIFACT-level, and detail is the
ONLY signal this format has.** lcov carries no capability metadata anywhere
(unlike coverage.py JSON's ``meta.branch_coverage`` or Cobertura's root
``branches-valid``), so §3.2's disagreement case cannot arise here: if ANY
``BRDA:``/``BRF:``/``BRH:`` record appears anywhere in the artifact, branch
tracking was on and every file gets a real
:class:`~.model.BranchCoverage` (empty for a branch-free record, per
``lcov.branch.info``'s own witness — ``check_sample.py`` carries no
``BRF``/``BRH`` at all while ``sample.py`` does, so a PER-FILE capability
rule would call that single real artifact "mixed" and refuse it, which this
parser does not). Deciding this requires seeing the WHOLE artifact before any
one file's ``FileCoverage`` can be built, so :func:`parse` pre-scans for a
branch marker before its single pass over the tracefile's lines.
"""

from __future__ import annotations

from types import MappingProxyType

from ..errors import AssayError, Outcome, ReasonCode
from .model import BranchCoverage, CoverageProfile, FileCoverage

_SF_PREFIX = "SF:"
_DA_PREFIX = "DA:"
_BRDA_PREFIX = "BRDA:"
_BRF_PREFIX = "BRF:"
_BRH_PREFIX = "BRH:"
_END_RECORD = "end_of_record"


def sniff(text: str) -> bool:
    """Any line starting with ``SF:`` (DESIGN-GUIDE §5's literal signature)."""
    return any(line.startswith(_SF_PREFIX) for line in text.splitlines())


def parse(text: str, *, producer: str | None) -> CoverageProfile:
    # `producer` is part of the uniform parser protocol (package docstring,
    # B045) and is deliberately unread here: `lcov`'s producer vocabulary is
    # still CLOSED AND EMPTY (no speculative names, DESIGN-GUIDE §5), so no
    # lane can declare one to branch on.
    del producer
    raw_lines = text.splitlines()
    # Artifact-level (§3.2): whether branch tracking was on for this WHOLE
    # tracefile, decided before any one file's FileCoverage is built, since
    # deciding it per-record would call a real branch-free record "mixed"
    # (module docstring).
    has_branch_detail = any(
        stripped.startswith((_BRDA_PREFIX, _BRF_PREFIX, _BRH_PREFIX))
        for stripped in (line.strip() for line in raw_lines)
    )

    files: dict[str, FileCoverage] = {}
    current_path: str | None = None
    # line number -> summed hit count across every DA: record seen for it in
    # the CURRENT open SF:/end_of_record block. geninfo can legally emit more
    # than one DA: for the same line (most often after merging tracefiles);
    # summing and then testing > 0 is what makes "multiple records for one
    # line" resolve the same way regardless of how the counts were split.
    current_hits: dict[int, int] = {}
    # (line, block, branch) identity -> nothing (a set); duplicates refused
    # BEFORE aggregation (§3.1a), whatever their `taken` values.
    current_branch_identities: set[tuple[int, str, str]] = set()
    current_branch_total: dict[int, int] = {}
    current_branch_covered: dict[int, int] = {}
    current_brf: int | None = None
    current_brh: int | None = None

    for lineno, raw in enumerate(raw_lines, start=1):
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
            current_branch_identities = set()
            current_branch_total = {}
            current_branch_covered = {}
            current_brf = None
            current_brh = None
            continue
        if line == _END_RECORD:
            if current_path is None:
                raise _malformed(
                    f"line {lineno}: 'end_of_record' with no open 'SF:' record"
                )
            files[current_path] = _finish_record(
                current_path,
                current_hits,
                has_branch_detail=has_branch_detail,
                branch_total=current_branch_total,
                branch_covered=current_branch_covered,
                brf=current_brf,
                brh=current_brh,
            )
            current_path = None
            current_hits = {}
            continue
        if line.startswith(_DA_PREFIX):
            if current_path is None:
                raise _malformed(f"line {lineno}: 'DA:' outside any 'SF:' record")
            file_line, hit_count = _parse_da(line[len(_DA_PREFIX):], lineno)
            current_hits[file_line] = current_hits.get(file_line, 0) + hit_count
            continue
        if line.startswith(_BRDA_PREFIX):
            if current_path is None:
                raise _malformed(f"line {lineno}: 'BRDA:' outside any 'SF:' record")
            file_line, block, branch_id, taken = _parse_brda(
                line[len(_BRDA_PREFIX):], lineno
            )
            identity = (file_line, block, branch_id)
            if identity in current_branch_identities:
                raise _malformed(
                    f"line {lineno}: 'BRDA:' repeats the identity "
                    f"(line={file_line}, block={block!r}, branch={branch_id!r})"
                )
            current_branch_identities.add(identity)
            current_branch_total[file_line] = current_branch_total.get(file_line, 0) + 1
            if _taken_is_covered(taken, lineno):
                current_branch_covered[file_line] = (
                    current_branch_covered.get(file_line, 0) + 1
                )
            continue
        if line.startswith(_BRF_PREFIX):
            current_brf = _parse_branch_total(line[len(_BRF_PREFIX):], lineno, "BRF")
            continue
        if line.startswith(_BRH_PREFIX):
            current_brh = _parse_branch_total(line[len(_BRH_PREFIX):], lineno, "BRH")
            continue
        # TN:/FN:/FNDA:/FNF:/FNH:/VER:/... — legal, ignored.

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


def _parse_brda(fields_text: str, lineno: int) -> tuple[int, str, str, str]:
    """``BRDA:<line>,<block>,<branch>,<taken>`` -- ``line`` and ``block`` off
    the LEFT on the first two commas, ``taken`` off the RIGHT with
    ``rsplit(",", 1)``, ``branch`` is the remainder. Defensive, not a
    fixture-proven necessity (A2/§3.3): every witnessed record has exactly
    three delimiter commas and would survive a naive four-field split, but
    this degrades safely for an unwitnessed branch id containing a comma,
    which the LEFT/RIGHT split does not assume away. ``block``/``branch``
    are opaque identity fields (coverage.py writes ``jump to line 6`` and
    ``return from function 'falls_off_the_end'`` there), never parsed as
    numbers.
    """
    parts = fields_text.split(",", 2)
    if len(parts) != 3:
        raise _malformed(
            f"line {lineno}: 'BRDA:' needs <line>,<block>,<branch>,<taken>, "
            f"got {fields_text!r}"
        )
    line_field, block_field, remainder = parts
    if "," not in remainder:
        raise _malformed(
            f"line {lineno}: 'BRDA:' has no <taken> field: {fields_text!r}"
        )
    branch_id, taken = remainder.rsplit(",", 1)
    try:
        file_line = int(line_field)
    except ValueError as exc:
        raise _malformed(
            f"line {lineno}: 'BRDA:' line number must be an integer, got "
            f"{line_field!r}"
        ) from exc
    if file_line <= 0:
        raise _malformed(
            f"line {lineno}: 'BRDA:' line number {file_line} is not positive"
        )
    return file_line, block_field, branch_id, taken


def _taken_is_covered(taken: str, lineno: int) -> bool:
    """``taken`` is ``-`` (the block was never entered) or a decimal count;
    covered means a count ``> 0`` -- ``-`` and ``0`` are both uncovered."""
    if taken == "-":
        return False
    try:
        count = int(taken)
    except ValueError as exc:
        raise _malformed(
            f"line {lineno}: 'BRDA:' <taken> must be '-' or an integer, got "
            f"{taken!r}"
        ) from exc
    return count > 0


def _parse_branch_total(value_text: str, lineno: int, tag: str) -> int:
    try:
        return int(value_text)
    except ValueError as exc:
        raise _malformed(
            f"line {lineno}: {tag}: value must be an integer, got {value_text!r}"
        ) from exc


def _finish_record(
    path: str,
    hits: dict[int, int],
    *,
    has_branch_detail: bool,
    branch_total: dict[int, int],
    branch_covered: dict[int, int],
    brf: int | None,
    brh: int | None,
) -> FileCoverage:
    executed = frozenset(line for line, count in hits.items() if count > 0)
    missing = frozenset(line for line, count in hits.items() if count <= 0)
    branches = None
    if has_branch_detail:
        by_line = {
            line: (branch_covered.get(line, 0), total)
            for line, total in branch_total.items()
        }
        derived_total = sum(total for _covered, total in by_line.values())
        derived_covered = sum(covered for covered, _total in by_line.values())
        if brf is not None and brf != derived_total:
            raise _malformed(
                f"{path!r}: BRF:{brf} does not match the derived branch "
                f"total {derived_total}"
            )
        if brh is not None and brh != derived_covered:
            raise _malformed(
                f"{path!r}: BRH:{brh} does not match the derived covered "
                f"branch count {derived_covered}"
            )
        # Deliberately NOT wrapped in its own try/except: every (line,
        # covered, total) triple in `by_line` was built from THIS parser's
        # own BRDA aggregation above, which already enforces a positive line
        # (`_parse_brda`) and `covered <= total` (every covered increment in
        # the loop above is paired with a total increment for the identical
        # record) before it ever reaches here. Unlike coverage-py-json's arc
        # arrays -- externally supplied `[src, dst]` pairs whose `src` this
        # parser deliberately does NOT re-validate, trusting the model
        # instead -- nothing here can violate BranchCoverage's own
        # invariants 1-2, and a try/except around a call that cannot raise
        # would be a line no honest test could cover.
        branches = BranchCoverage(by_line=by_line)
    try:
        return FileCoverage(
            executed=executed, missing=missing, excluded=None, branches=branches
        )
    except ValueError as exc:
        raise _malformed(f"{path!r}: {exc}") from exc


def _malformed(message: str) -> AssayError:
    return AssayError(
        f"lcov tracefile: {message}",
        outcome=Outcome.ERROR,
        reason_code=ReasonCode.UNREADABLE_ARTIFACT,
    )
