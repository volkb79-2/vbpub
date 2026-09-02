"""The normalized coverage model every format parser returns.

DESIGN-GUIDE §11's registry contract, verbatim:

    FileCoverage(executed, missing, excluded: frozenset[int] | None)

``excluded`` is ``frozenset[int] | None`` and the two are NOT interchangeable
(A-008): ``None`` means the format itself has no way to say "this line was
deliberately excluded from measurement" — asking it the question is
meaningless, not merely unanswered. ``frozenset()`` means the format CAN
express exclusions and this file reports zero of them. Collapsing the two
loses the fact that a Go or lcov or Cobertura lane can never claim "0 lines
excluded — verified", because nothing verified it; only coverage.py's own
JSON format carries a dedicated ``excluded_lines`` field (see each parser
module's own docstring for why the other three cannot).

This module is deliberately a leaf: it imports nothing from a sibling parser
module or from :mod:`assay.coverage`, so every parser module (and
``coverage.py`` itself, which assembles the registry) can import it with no
import cycle. **P15 (A-067 finding 4, sol's post-series review) enforces the
common model's own invariants here, in the one place every format's output
passes through**, rather than trusting each parser to have gotten it right
independently — see :meth:`FileCoverage.__post_init__`.

**B039/B047 item 4: this module also owns the one shared classified-line
ceiling every EXPANDING parser enforces** (:data:`MAX_CLASSIFIED_LINES`,
:class:`ClassifiedLineBudget`) — an "expanding" parser being one that turns a
compact RANGE declaration (a byte-span extent, a block's start/end line) into
one dict entry per physical line it covers, as opposed to a parser that reads
one summed count per line directly and never expands anything
(``coverage_py_json``, ``lcov``, ``cobertura``). :mod:`.coverage_istanbul_json`
(statement extents) shipped this ceiling first, alone; :mod:`.go_cover` (block
ranges) had the identical unbounded-expansion shape with no bound at all —
B039's own finding. Importing :class:`~assay.errors.AssayError` here does not
break the leaf property above: :mod:`assay.errors` is itself a leaf (stdlib
only, no import of ``model`` or any parser), so no cycle is introduced.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..errors import AssayError, Outcome, ReasonCode

__all__ = [
    "MAX_CLASSIFIED_LINES",
    "BranchCoverage",
    "ClassifiedLineBudget",
    "CoverageBlock",
    "CoverageProfile",
    "FileCoverage",
]

#: A fixed ceiling on how many (line) classifications one artifact may ask an
#: EXPANDING parser to materialize — O4's "a fixed bound, never an ambient or
#: elapsed-time guess", one level below
#: :data:`assay.coverage.MAX_COVERAGE_ARTIFACT_BYTES`. A range is expanded
#: line by line, so a single ~60-100-byte record declaring an end line of
#: ``999999999`` would otherwise materialize close to a billion entries from
#: an artifact well inside the 16 MiB read bound — measured for both formats
#: this ceiling now guards: istanbul's ``"end": {"line": 999999999}`` and Go's
#: ``pkg/x.go:1.1,999999999.1 1 1``.
#:
#: **Why two million and not something tighter** (originally
#: ``coverage_istanbul_json``'s own reasoning, round-1 review, Minor; carried
#: over verbatim since the argument is about real artifact SIZE, not about
#: which format produced it). The budget is spent per RANGE, not per distinct
#: line, so nested or overlapping ranges charge their own span at every
#: level: a real ``@vitest/coverage-istanbul`` artifact charges roughly 3-4x
#: its source line count, where a Vitest-3 ``@vitest/coverage-v8`` one
#: charges about 1x, and a Go coverprofile charges close to 1x per block
#: (blocks do not nest the way istanbul statement extents can). A 300k-line
#: monorepo measured in one artifact therefore lands near 1.2M, so a ceiling
#: of, say, 500k would refuse honest output from a project either format
#: genuinely serves — the false-refusal direction A-272 already warned
#: against one parser over. Two million keeps real headroom above that while
#: still refusing the ~60-100-byte billion-line record this bound exists for.
#:
#: The bound is on line COUNT, and the memory that count implies is real
#: (roughly 175 bytes per classified line in CPython): a document sitting
#: exactly at the ceiling peaks near 350 MB. That is the deliberate cost of
#: not refusing honest large artifacts. **No test drives the shipped value to
#: its own limit** — the boundary arithmetic is exercised by monkeypatching
#: each parser module's own re-exported name down to a handful of lines, so
#: the suite pays nothing to prove the same off-by-one twice over.
MAX_CLASSIFIED_LINES = 2_000_000


class ClassifiedLineBudget:
    """The remaining share of a classified-line ceiling for one whole
    artifact, spent as an expanding parser materializes range extents.

    A plain mutable holder, not a running total threaded through return
    values: the bound is a property of the ARTIFACT, not of any one record,
    so a document made of a million small records is refused by the same
    counter that refuses one record with a million-line extent.

    *format_name* names the caller in the refusal message (``"istanbul
    coverage JSON"``, ``"go coverprofile"``, ...) so a consumer reading the
    error is told which artifact tripped it, exactly as each parser's own
    ``_malformed`` helper already does for every other refusal it raises.
    *remaining* is a REQUIRED keyword, never defaulted to
    :data:`MAX_CLASSIFIED_LINES` here: each call site passes its own
    module's re-exported ``MAX_CLASSIFIED_LINES`` name explicitly, read
    fresh at call time, which is what keeps that module's own
    ``monkeypatch.setattr(<module>, "MAX_CLASSIFIED_LINES", ...)`` test
    idiom working after this class moved here — a default bound into this
    class's own signature would capture the value once, at class-definition
    time, and a later monkeypatch of either module would silently stop
    mattering.
    """

    __slots__ = ("remaining", "_format_name", "_ceiling")

    def __init__(self, *, format_name: str, remaining: int) -> None:
        self.remaining = remaining
        self._format_name = format_name
        self._ceiling = remaining

    def spend(self, amount: int, path: str) -> None:
        self.remaining -= amount
        if self.remaining < 0:
            raise AssayError(
                f"{self._format_name}: record for {path!r} pushes this "
                f"artifact past {self._ceiling} classified lines; a "
                f"document declaring more line classifications than any "
                f"real codebase contains is refused rather than expanded",
                outcome=Outcome.ERROR,
                reason_code=ReasonCode.UNREADABLE_ARTIFACT,
            )


@dataclass(frozen=True, kw_only=True)
class BranchCoverage:
    """One file's per-line branch-arc counts, format-normalized (wave-1 §3.1).

    ``by_line`` maps a branch SOURCE line to ``(covered_arcs, total_arcs)``.
    A line with no branch at all is ABSENT from the mapping, never present as
    ``(0, 0)`` -- the same "absent means none, not empty" contract
    :class:`FileCoverage`'s own ``excluded`` field keeps one level up (A-008),
    and every other payload mapping in this project keeps.

    Enforces invariants 1-2 of §3.1's five: every branch line is positive,
    and ``1 <= total`` with ``0 <= covered <= total`` -- a recorded line with
    zero total arcs is malformed, not "no branches" (that case is simply
    absent from ``by_line`` instead). The remaining three invariants (a
    branch line must be in ``executed | missing``, never in ``excluded``, and
    a line in ``missing`` must carry ``covered == 0``) are cross-bucket
    relations against a *file's* other line sets, so they are enforced in
    :meth:`FileCoverage.__post_init__` instead, the one place that already
    holds all three buckets alongside ``branches``.
    """

    by_line: Mapping[int, tuple[int, int]]

    def __post_init__(self) -> None:
        non_positive = sorted(line for line in self.by_line if line < 1)
        if non_positive:
            raise ValueError(
                f"BranchCoverage.by_line contains non-positive line "
                f"number(s): {non_positive}"
            )
        for line, (covered, total) in self.by_line.items():
            if total < 1:
                raise ValueError(
                    f"BranchCoverage.by_line[{line}] has total={total}, "
                    f"must be >= 1 -- a recorded line with zero total arcs "
                    f"is malformed, not \"no branches\""
                )
            if not (0 <= covered <= total):
                raise ValueError(
                    f"BranchCoverage.by_line[{line}] has covered={covered}, "
                    f"outside the range 0..{total}"
                )


@dataclass(frozen=True, kw_only=True)
class CoverageBlock:
    """One coverage record's positional EXTENT, kept unmerged (A-239).

    A Go cover record is ``<path>:<startLine>.<startCol>,<endLine>.<endCol>
    <numStmts> <count>``: an extent plus a statement CARDINALITY, never the
    statements' own positions. :mod:`.go_cover` used to expand that extent
    straight into line sets with ``range(start, end + 1)``, which attributes
    function signatures, closing braces, ``case`` labels and
    statement-continuation lines as executable code.

    **Why the extent is stored instead of only its expansion.** A-239 rejected
    correcting that expansion afterwards, at the adapter/evaluate boundary, as
    *information-theoretically insufficient* rather than merely untidy: two
    blocks may share a boundary position, the parser's own executed-wins
    overlap merge collapses them DURING parsing, and no later pass can recover
    the per-block column data the correction would need, because the merge has
    already discarded it. Keeping the record whole is what makes the
    correction possible at all — see
    :func:`assay.statement_attribution.attribute_statements`, which joins these
    extents against a source-side oracle's.

    Columns are carried even though a verdict speaks only in LINES. They are
    load-bearing here for exactly the reason above: ``28.22,29.2`` and
    ``29.2,31.3`` are two different blocks that a line-only key would fuse.

    ``count`` is the record's own execution count, kept per block rather than
    folded into a line classification, for the same reason.

    **Lines are 1-based; columns are ``>= 0`` (A-405).** A zero column is not
    a malformed record — it is what :class:`go/token.Position` carries for a
    position remapped by a ``//line file:line`` directive that specifies no
    column, and it is why ``cmd/cover`` has a ``dedup`` helper at all. Its
    own comment, go1.25.14 ``/usr/local/go/src/cmd/cover/cover.go:1055-1060``:
    "It is possible for positions to repeat when there is a line directive
    that does not specify column information and the input has not been
    passed through gofmt. See issues #27530 and #30746. Tests are
    TestHtmlUnformatted and TestLineDup." This class used to assert "a 1-based
    source position is never below 1" about all four coordinates, which is
    true of the LINE and false of the COLUMN, and the real toolchain's own
    ``TestLineDup`` corpus (committed at
    ``nyxloom-trove/carve-assets/P27-recarve/linedup.out``) disproved it.
    Negative stays refused in both coordinates: nothing in ``go/token``
    emits one.
    """

    start_line: int
    start_col: int
    end_line: int
    end_col: int
    num_stmts: int
    count: int

    def __post_init__(self) -> None:
        for name in ("start_line", "end_line"):
            value = getattr(self, name)
            if value < 1:
                raise ValueError(
                    f"CoverageBlock.{name} is {value}; a 1-based source line "
                    f"is never below 1, and a `//line` directive's own line "
                    f"number must be positive too"
                )
        for name in ("start_col", "end_col"):
            value = getattr(self, name)
            if value < 0:
                raise ValueError(
                    f"CoverageBlock.{name} is {value}; a column is 0 when the "
                    f"position came from a `//line` directive carrying no "
                    f"column, and >= 1 otherwise -- never negative"
                )
        if self.num_stmts < 0:
            raise ValueError(
                f"CoverageBlock.num_stmts is {self.num_stmts}, must be >= 0"
            )
        if self.count < 0:
            raise ValueError(
                f"CoverageBlock.count is {self.count}, must be >= 0"
            )
        if (self.end_line, self.end_col) < (self.start_line, self.start_col):
            raise ValueError(
                f"CoverageBlock ends at {self.end_line}.{self.end_col}, "
                f"before it starts at {self.start_line}.{self.start_col}"
            )

    @property
    def extent(self) -> tuple[int, int, int, int]:
        """The join key: the whole four-part position, never a line alone.

        :func:`assay.statement_attribution.attribute_statements` matches a
        parsed record to an oracle-derived one on exactly this tuple, so a
        shared boundary position cannot fuse two records.
        """
        return (self.start_line, self.start_col, self.end_line, self.end_col)

    @property
    def has_remapped_position(self) -> bool:
        """This record carries a position a ``//line`` directive remapped
        (A-405): either coordinate's column is 0.

        DERIVED, never stored. A stored flag would be a second fact about
        the same four numbers, and the two could disagree; there is nothing
        here a parser could set wrongly.
        """
        return self.start_col == 0 or self.end_col == 0


@dataclass(frozen=True, kw_only=True)
class FileCoverage:
    """One file's line classification, format-normalized.

    ``executed``, ``missing``, and (when not ``None``) ``excluded`` are
    ENFORCED pairwise disjoint at construction, and every line number in any
    of the three is enforced positive — a line is at most one of executed,
    missing, or (known) excluded, never two, and never line 0 or negative.
    lcov/cobertura/go-cover's own parsers can never violate this (each
    classifies a line from one summed hit count, so a line lands in exactly
    one bucket by construction, and each already validates positivity before
    it ever reaches this class); coverage.py's own JSON format reads three
    INDEPENDENT arrays straight from external, potentially adversarial input,
    which is exactly where an artifact claiming a line is simultaneously
    ``executed`` and ``missing`` — sol's reproduction of a false ``PASS
    100.0`` that still reports the same line as missing — was possible before
    this check existed (finding 4). Raises :class:`ValueError` on violation;
    :mod:`assay.coverage_parsers.coverage_py_json` is this project's one
    caller whose input can actually trigger it, and wraps it into its own
    ``ERROR``/``UNREADABLE_ARTIFACT`` the same way it wraps every other
    malformed-record defect.

    ``branches`` (wave-1 §3.1) carries the exact ``None``/``BranchCoverage``
    split ``excluded`` already established (A-008): ``None`` means the
    FORMAT cannot express branch arcs at all (``go-cover`` always; lcov and
    Cobertura when the artifact was produced without branch tracking
    enabled), a different fact from "expressed, and there are zero". Three
    of §3.1's five invariants are enforced HERE rather than on
    :class:`BranchCoverage` itself, because they are relations against
    ``executed``/``missing``/``excluded`` that only this class holds
    together: a branch line must be a considered line (in ``executed |
    missing``), must never also be ``excluded``, and — the strongest
    anti-tamper invariant available here, which all three branch-bearing
    formats agree on — a line in ``missing`` can never carry a covered arc,
    because a line that never ran cannot have taken one.

    ``blocks`` (A-239) keeps the SAME ``None``/populated split ``excluded`` and
    ``branches`` already establish (A-008): ``None`` means the format has no
    block-extent concept to report (every line-based format — ``coverage.py``
    JSON, lcov, Cobertura, istanbul), a different fact from "block-based, and
    there are zero blocks". Only :mod:`.go_cover` populates it today. A
    populated tuple means the line sets above are still the format's own
    over-approximation and are corrected by
    :func:`assay.statement_attribution.attribute_statements`; see
    :class:`CoverageProfile.statement_attributed` for why that correction
    cannot be silently skipped.
    """

    executed: frozenset[int]
    missing: frozenset[int]
    excluded: frozenset[int] | None
    branches: "BranchCoverage | None" = None
    blocks: "tuple[CoverageBlock, ...] | None" = None

    def __post_init__(self) -> None:
        for name in ("executed", "missing", "excluded"):
            lines = getattr(self, name)
            if lines is None:
                continue
            non_positive = sorted(line for line in lines if line < 1)
            if non_positive:
                raise ValueError(
                    f"FileCoverage.{name} contains non-positive line "
                    f"number(s): {non_positive}"
                )
        overlap_executed_missing = self.executed & self.missing
        if overlap_executed_missing:
            raise ValueError(
                f"FileCoverage.executed and .missing are not disjoint: "
                f"shared line(s) {sorted(overlap_executed_missing)}"
            )
        if self.excluded is not None:
            overlap_executed_excluded = self.executed & self.excluded
            if overlap_executed_excluded:
                raise ValueError(
                    f"FileCoverage.executed and .excluded are not disjoint: "
                    f"shared line(s) {sorted(overlap_executed_excluded)}"
                )
            overlap_missing_excluded = self.missing & self.excluded
            if overlap_missing_excluded:
                raise ValueError(
                    f"FileCoverage.missing and .excluded are not disjoint: "
                    f"shared line(s) {sorted(overlap_missing_excluded)}"
                )
        if self.branches is not None:
            branch_lines = frozenset(self.branches.by_line)
            # Checked BEFORE "unconsidered" below so this invariant is
            # independently reachable: `excluded` is already disjoint from
            # `executed`/`missing` (checked above), and a branch line
            # confined to `executed | missing` (invariant 3) can therefore
            # never ALSO be in `excluded` -- the two checks would otherwise
            # never both be exercisable by one honest input, and reversing
            # the order is what keeps invariant 4 a real, tested code path
            # rather than one only invariant 3's own message could reach.
            if self.excluded is not None:
                branch_excluded = branch_lines & self.excluded
                if branch_excluded:
                    raise ValueError(
                        f"FileCoverage.branches has line(s) "
                        f"{sorted(branch_excluded)} that are also in "
                        f".excluded"
                    )
            unconsidered = branch_lines - (self.executed | self.missing)
            if unconsidered:
                raise ValueError(
                    f"FileCoverage.branches has line(s) "
                    f"{sorted(unconsidered)} that are in neither .executed "
                    f"nor .missing"
                )
            tampered_missing = sorted(
                line
                for line in branch_lines & self.missing
                if self.branches.by_line[line][0] != 0
            )
            if tampered_missing:
                raise ValueError(
                    f"FileCoverage.branches has line(s) "
                    f"{tampered_missing} in .missing with a nonzero covered "
                    f"arc count -- a line that never ran cannot have taken "
                    f"an arc"
                )

    @property
    def executable(self) -> frozenset[int]:
        """Every line this file's format considers code at all.

        The union parsers and consumers alike need: "was this changed line
        code the format could have measured", independent of whether it ran.
        Not part of DESIGN-GUIDE §11's literal shape, so kept as a derived
        property rather than a fourth stored field — it can never disagree
        with ``executed``/``missing`` because it is computed from them.
        """
        return self.executed | self.missing

    @property
    def line_directive_remapped(self) -> bool:
        """This file's coverage records describe positions a ``//line``
        directive remapped, so its line numbers are VIRTUAL (A-405).

        **Per FILE, not per record, and deliberately conservative.** One
        record with a zero column flags the whole file. Within a file that
        carries a ``//line`` directive there is no way to tell a physical
        position from a virtual one: the directive applies from where it
        appears to the next one or to end of file, and the profile records
        the remapped result with no marker saying which happened. Go's own
        ``TestLineDup`` corpus is the witness — its profile
        (``carve-assets/P27-recarve/linedup.out``) mixes ``6.21,7.25``
        (physical, columns present) with ``100.0,102.1`` (virtual, columns
        zeroed) in one file, and ``linedup.go`` is 24 lines long. Trusting
        the records that LOOK physical would mean trusting a guess about
        where the directive's scope began.

        **DERIVED from ``blocks``, never stored.** A stored flag could
        disagree with the records it describes, and every layer that rebuilds
        a :class:`FileCoverage` (``attribute_statements``,
        ``_normalized_profile_files``) would have to remember to carry it —
        the "check that cannot fail" shape
        :attr:`CoverageProfile.statement_attributed`'s own docstring argues
        against. ``blocks`` is carried through every one of those rebuilds
        already, so this property is carried by construction. ``False`` for
        every line-based format, which has no ``blocks`` at all.

        What CONSUMES it: :func:`assay.statement_attribution.
        attribute_statements` skips such a file rather than sending it to a
        source-side oracle that would derive PHYSICAL positions and refuse
        the whole artifact for the resulting extent mismatch; and
        :mod:`assay.evaluate` refuses — naming the file, the ``//line``
        cause and the remedy — if such a file is in the judged set, while
        ignoring it entirely if it is not. The asymmetry is the north star's
        own rule: code outside the diff is invisible to the verdict by
        construction, and 0/0 is never 100%.
        """
        if self.blocks is None:
            return False
        return any(block.has_remapped_position for block in self.blocks)


@dataclass(frozen=True, kw_only=True)
class CoverageProfile:
    """A whole parsed coverage artifact: one :class:`FileCoverage` per file
    path exactly as that format's artifact names it (no source-root
    resolution, no path normalization against a project layout — that stays
    the caller's job, the same separation :mod:`assay.diff` keeps from
    :mod:`assay.measurability`).

    ``statement_attributed`` records whether this profile's line sets have been
    corrected against a source-side statement-position oracle
    (:func:`assay.statement_attribution.attribute_statements`, which is the
    ONLY producer of a ``True`` — no parser sets it, and no consumer should).

    **Why a flag rather than a convention.** For a block-based format the
    parser's line sets are a strict over-approximation, and a consumer that
    reads them un-corrected gets a wrong answer that looks exactly like a right
    one. That is AGENTS.md's *masked default*, anti-pattern 3: wrong, harmless
    in every context that happens to run the correction, and invisible to
    testing for precisely that reason. So the omission is made to FAIL rather
    than to pass quietly — :func:`assay.evaluate.evaluate_coverage` and
    :func:`assay.evaluate.evaluate_targets` refuse when an adapter declares
    ``requires_statement_attribution`` and the profile they were handed
    reports ``False``. The flag is not a promise anybody keeps by remembering;
    it is a check that can go red.
    """

    files: Mapping[str, FileCoverage]
    statement_attributed: bool = False
