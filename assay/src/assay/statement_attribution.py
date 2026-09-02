"""Turn block extents plus statement positions into statement-granular lines.

This is A-239's third element, and its whole design is in one sentence: **the
join is on the extent, not on the position.**

# The problem, in one witness pair

`carve-assets/P27/witness/collision-colA.go` and `collision-colB.go` are both
gofmt-clean, both compile under the pinned toolchain, and emit a byte-identical
coverage profile::

    example.invalid/coll/f.go:3.22,7.2 2 1

Their statements begin on different lines: `{4, 6}` for A, `{4, 5}` for B. A
rule that reads only the profile is a function of the profile; identical input
forces identical output; the two correct answers differ; therefore no
profile-only rule is right on both (A-217, `BLOCKED-grammar.md` §1). The
missing information is in the SOURCE, and `assay/helpers/go/stmtpos/` is what
reads it back out.

# Why this module joins whole extents rather than intersecting positions

The obvious shape — "a statement at position P belongs to the block whose
extent contains P" — is wrong, and `shapes.go` witnesses why. `cmd/cover`
ends a block at the *start* position of its own last statement, so::

    shapes.go:28.22,29.2 1 1     <- func body prefix, last stmt is the bare block
    shapes.go:29.2,31.3  1 1     <- that bare block's own body

Position `29.2` is block 1's END and block 2's START, and the statement that
begins there belongs to block 1 only. Containment cannot express that: with a
closed interval the statement matches both, with a half-open one it matches
neither. The structure is not recoverable from positions, so this module does
not try — the oracle reports each block's own statement list, and the join is
on the block.

That also makes the result CHECKABLE, which a positional intersection would
not be. The instrumenter is deterministic (A-217), so re-running it over the
same source must reproduce the same extents. Every disagreement therefore means
something real is wrong — a profile from a different revision than the source,
a different toolchain, a file edited between the run and the judgment — and
this module refuses instead of attributing lines from a mismatched pair.

# Language-free, and Go-specific anyway

Nothing here mentions Go. It takes two plain data structures and returns a
third, exactly as :mod:`assay.evaluate`'s span attribution does for Python
(P07's precedent). But A-239 rules it is **built Go-specific, not as shared
infrastructure**: there is no third consumer to amortize against — TypeScript's
Istanbul format is already statement-precise, and SQL has no coverage tool at
all — so generalizing it further would be speculative generality. It lives in
the core because it is pure, not because it is shared.

# What it does NOT fix, stated here so nobody claims it does

An uncovered statement that shares a physical LINE with a covered one is still
laundered into `executed`. `carve-assets/P27/witness/lit.go` is the witness:
line 4 is `f := func() int { return 7 }`, whose assignment (block `3.14,4.18`,
count 1) and whose func-literal body (block `4.18,4.30`, count 0) are two
counted statements that genuinely both begin on line 4. Executed-wins promotes
the line, and it should — the line did run.

This is **line granularity's own limit, which `coverage.py` shares**
(`BLOCKED-grammar.md` §3, in those words), not a defect this oracle could
remove: a verdict's wire schema speaks in line numbers. What the oracle does
fix on `lit.go` is the fabrication — line 3, the `func H() int {` signature,
was reported executable by the old expansion and is not code. Recorded as
decision A-393 and backlog B055.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .errors import AssayError, Outcome, ReasonCode
from .coverage_parsers.model import CoverageBlock, CoverageProfile, FileCoverage

__all__ = ["StatementBlock", "attribute_statements"]


@dataclass(frozen=True, kw_only=True)
class StatementBlock:
    """One block as a source-side oracle derives it: the extent the profile
    will report, and the lines on which that block's own statements begin.

    ``stmt_lines`` is sorted, deduplicated and may therefore be SHORTER than
    ``num_stmts`` — ``x := 1; y := 2`` is two statements on one line. That is a
    property of the source, so this class never checks the two against each
    other; :func:`attribute_statements` compares ``num_stmts`` against the
    profile's own count, which is the comparison that means something.

    **Lines are 1-based; columns are ``>= 0`` (A-405)**, exactly as
    :class:`~assay.coverage_parsers.model.CoverageBlock`'s are and for the
    same reason: the oracle derives its positions with ``go/token``, so a
    source carrying a ``//line file:line`` directive with no column yields
    ``Column == 0`` here too — the case ``cmd/cover``'s own comment names
    (go1.25.14 ``/usr/local/go/src/cmd/cover/cover.go:1055-1060``, issues
    #27530 and #30746) and its ``dedup`` helper exists to handle. This class
    used to assert "a 1-based source position is never below 1" about all
    four coordinates; run over Go's own ``TestLineDup`` corpus the oracle
    produces six zero-column blocks, so the assertion would have refused the
    oracle's OWN output. The two classes must agree on this bound or the
    extent join could never match a remapped block against itself.
    """

    start_line: int
    start_col: int
    end_line: int
    end_col: int
    num_stmts: int
    stmt_lines: tuple[int, ...]

    def __post_init__(self) -> None:
        for name in ("start_line", "end_line"):
            value = getattr(self, name)
            if value < 1:
                raise ValueError(
                    f"StatementBlock.{name} is {value}; a 1-based source line "
                    f"is never below 1, and a `//line` directive's own line "
                    f"number must be positive too"
                )
        for name in ("start_col", "end_col"):
            value = getattr(self, name)
            if value < 0:
                raise ValueError(
                    f"StatementBlock.{name} is {value}; a column is 0 when the "
                    f"position came from a `//line` directive carrying no "
                    f"column, and >= 1 otherwise -- never negative"
                )
        if self.num_stmts < 0:
            raise ValueError(
                f"StatementBlock.num_stmts is {self.num_stmts}, must be >= 0"
            )
        non_positive = sorted(n for n in self.stmt_lines if n < 1)
        if non_positive:
            raise ValueError(
                f"StatementBlock.stmt_lines contains non-positive line "
                f"number(s): {non_positive}"
            )
        if list(self.stmt_lines) != sorted(set(self.stmt_lines)):
            raise ValueError(
                f"StatementBlock.stmt_lines must be sorted and duplicate-free, "
                f"got {list(self.stmt_lines)}"
            )

    @property
    def extent(self) -> tuple[int, int, int, int]:
        """The join key — see :meth:`assay.coverage_parsers.model.
        CoverageBlock.extent`, whose tuple this must equal exactly."""
        return (self.start_line, self.start_col, self.end_line, self.end_col)


def attribute_statements(
    profile: CoverageProfile,
    blocks_by_key: Mapping[str, tuple[StatementBlock, ...]],
) -> CoverageProfile:
    """*profile* with every block-bearing file's line sets rebuilt from
    *blocks_by_key*'s statement positions, marked ``statement_attributed``.

    *blocks_by_key* is keyed by the coverage artifact's OWN spelling of each
    path — the same keys :attr:`CoverageProfile.files` uses, before any
    ``normalize_coverage_key`` or source-root resolution. Keeping this function
    on the artifact's spelling is what keeps it language-free: resolving a key
    to a file on disk is the caller's job, exactly as it already is for
    :mod:`assay.evaluate`.

    A file whose ``FileCoverage.blocks`` is ``None`` (every line-based format)
    is passed through untouched — this function is not a no-op guard against
    being called on the wrong profile, it simply has nothing to correct there.

    A file whose
    :attr:`~assay.coverage_parsers.model.FileCoverage.line_directive_remapped`
    is ``True`` is emptied rather than attributed (A-405): its positions name
    another file's coordinates, so no source-side oracle can match them, and
    the refusal that mismatch would raise belongs to the whole artifact rather
    than to the one generated file that caused it. Emptying is safe ONLY
    because :mod:`assay.evaluate` refuses when such a file is in the judged
    set; without that companion check this would be a silent 0/0.

    Refuses ``ERROR``/``UNREADABLE_ARTIFACT`` when:

    * a block-bearing file has no entry in *blocks_by_key* at all;
    * the two sides' extent SETS differ in either direction;
    * a matched pair disagrees about ``num_stmts``;
    * two records for ONE extent disagree about ``num_stmts`` — the rule
      ``x/tools/cover``'s own merge loop states as ``inconsistent NumStmt``,
      transcribed with the fold it belongs to rather than half of it.

    The first three mean the profile and the source are not the same revision;
    the fourth means the records did not all come from one instrumentation.
    In none of them is there a safe direction to guess in: attributing anyway
    would publish a verdict about lines that are not the lines that ran. This
    is AGENTS.md's DERIVE / READ / **FAIL** ordering at its third branch.
    """
    corrected: dict[str, FileCoverage] = {}
    for path, file_cov in profile.files.items():
        if file_cov.blocks is None:
            corrected[path] = file_cov
            continue

        if file_cov.line_directive_remapped:
            # A-405. This file's positions were remapped by a `//line`
            # directive, so its line numbers name some OTHER file's
            # coordinates -- Go's own `TestLineDup` witness reports lines
            # 100-105 for a 24-line source. There is nothing to attribute:
            # the oracle derives PHYSICAL positions from the real file, so
            # asking it about this one produces an extent set that cannot
            # match, and the mismatch refusal would take down the entire
            # artifact for a profile that is exactly what `go test` wrote.
            #
            # The records contribute NOTHING instead: empty line sets, the
            # blocks kept so `line_directive_remapped` stays derivable one
            # layer up. That is not a silent 0/0 -- `assay.evaluate` refuses,
            # naming this file and `//line`, if any of its lines is in the
            # judged set. Outside the judged set the file is simply not
            # under review, which is the north star's own rule.
            corrected[path] = FileCoverage(
                executed=frozenset(),
                missing=frozenset(),
                excluded=file_cov.excluded,
                branches=file_cov.branches,
                blocks=file_cov.blocks,
                # (B054/A-410) Carried, not re-derived: it is metadata about
                # arcs the PARSER already dropped, so there is nothing left
                # here to derive it from. A rebuild that forgot it would
                # silently launder a defective record into a clean one.
                contradictory_branch_lines=file_cov.contradictory_branch_lines,
            )
            continue

        oracle_blocks = blocks_by_key.get(path)
        if oracle_blocks is None:
            raise _mismatch(
                f"no source-side statement positions were derived for "
                f"{path!r}, but its coverage records carry block extents that "
                f"cannot be read as statement truth without them"
            )

        # ONE EXTENT CAN APPEAR MANY TIMES IN ONE PROFILE, and folding those
        # records is not optional. `go test -coverpkg=./...` instruments every
        # package into EVERY test binary, and `go test` concatenates each
        # binary's own section into one file — so a block gets one record per
        # binary, and only the binary that actually ran it carries a non-zero
        # count. srdm's real profile (B061, F008-A5) carries **20 records per
        # block**, typically `0` nineteen times and `1` once.
        #
        # `{block.extent: block}` would keep whichever record came LAST, so a
        # genuinely covered block would be judged missing whenever its
        # non-zero record was not final — which is what happened: 255 lines
        # reported uncovered where 45 were. The fold is executed-wins, the
        # same rule `go_cover.parse` already applies to these records'
        # expansions one layer down, so the corrected line sets can no longer
        # DOWNGRADE what the uncorrected ones already called executed.
        #
        # THE RULE IS GO'S OWN, NOT ASSAY'S CONVENTION, and it is transcribed
        # here rather than inferred. `x/tools`' profile reader — vendored
        # into the toolchain at go1.25.14
        # `/usr/local/go/src/cmd/vendor/golang.org/x/tools/cover/profile.go`,
        # `ParseProfilesFromReader` (:54), its "Merge samples from the same
        # location" loop (:91) — matches records on all four coordinates
        # (:96-99) and then merges:
        #
        #     if mode == "set" { p.Blocks[j-1].Count |= b.Count }   // :104
        #     else             { p.Blocks[j-1].Count += b.Count }   // :106
        #
        # `|=` for `set`, `+=` for `count`/`atomic`. Both agree with
        # executed-wins on the only question read here (`count > 0`), so the
        # fold IS the profile format's own semantics. This citation replaces
        # an appeal to assay-internal precedent, which is not what this wave's
        # own standard asks for (adversarial review round 1, should-fix 4).
        #
        # THAT LOOP'S OTHER RULE IS TRANSCRIBED TOO (should-fix 6). Before it
        # merges, it REFUSES a disagreement about statement cardinality:
        #
        #     if b.NumStmt != last.NumStmt {
        #         return nil, fmt.Errorf("inconsistent NumStmt: changed from
        #                                 %d to %d", last.NumStmt, b.NumStmt)
        #     }                                                      // :100-102
        #
        # assay used to fold silently and then check only the SURVIVING
        # record's `num_stmts` against the oracle, so `[count=1 numStmts=1]`
        # plus `[count=0 numStmts=7]` for one extent was accepted whenever the
        # honest record won the fold. Transcribing half of a cited rule is the
        # pattern this wave keeps catching in its own work; the check is below,
        # inside the fold, so it sees every record rather than the survivor.
        parsed_extents: dict[tuple[int, int, int, int], CoverageBlock] = {}
        for block in file_cov.blocks:
            seen = parsed_extents.get(block.extent)
            if seen is not None and seen.num_stmts != block.num_stmts:
                raise _mismatch(
                    f"{path!r}: block {_spell(block.extent)} is recorded twice "
                    f"with different statement counts ({seen.num_stmts} then "
                    f"{block.num_stmts}). Go's own profile reader refuses this "
                    f"outright (`inconsistent NumStmt: changed from %d to %d`, "
                    f"x/tools/cover/profile.go), because one block cannot "
                    f"contain two different numbers of statements; the records "
                    f"did not all come from one instrumentation of this file"
                )
            if seen is None or (seen.count == 0 and block.count > 0):
                parsed_extents[block.extent] = block
        oracle_extents = {block.extent: block for block in oracle_blocks}

        only_profile = sorted(set(parsed_extents) - set(oracle_extents))
        only_oracle = sorted(set(oracle_extents) - set(parsed_extents))
        if only_profile or only_oracle:
            raise _mismatch(
                f"{path!r}: the coverage profile and the source-side oracle "
                f"disagree about which blocks exist, so they were not produced "
                f"from the same revision of this file. "
                f"{len(only_profile)} record(s) only in the profile "
                f"({_sample(only_profile)}); {len(only_oracle)} only from the "
                f"source ({_sample(only_oracle)})"
            )

        executed: set[int] = set()
        missing: set[int] = set()
        for extent, parsed in parsed_extents.items():
            oracle = oracle_extents[extent]
            if parsed.num_stmts != oracle.num_stmts:
                raise _mismatch(
                    f"{path!r}: block {_spell(extent)} carries "
                    f"{parsed.num_stmts} statement(s) in the coverage profile "
                    f"and {oracle.num_stmts} in the source; the profile and "
                    f"the source are not the same revision"
                )
            # Executed-wins on overlap, the rule `go_cover` already keeps one
            # layer down: once any block marks a line executed, a never-taken
            # block covering that same line must not downgrade it. Subtracted
            # AFTER the loop, so the order of DISTINCT blocks cannot matter;
            # the order of repeated records for ONE block is handled by the
            # fold above, which is a different problem and was a real defect.
            (executed if parsed.count > 0 else missing).update(
                oracle.stmt_lines
            )

        corrected[path] = FileCoverage(
            executed=frozenset(executed),
            missing=frozenset(missing - executed),
            excluded=file_cov.excluded,
            branches=file_cov.branches,
            blocks=file_cov.blocks,
            # (B054/A-410) See the sibling rebuild above: carried, never
            # re-derived.
            contradictory_branch_lines=file_cov.contradictory_branch_lines,
        )

    return CoverageProfile(
        files=MappingProxyType(corrected), statement_attributed=True
    )


def _spell(extent: tuple[int, int, int, int]) -> str:
    start_line, start_col, end_line, end_col = extent
    return f"{start_line}.{start_col},{end_line}.{end_col}"


def _sample(extents: list[tuple[int, int, int, int]], limit: int = 3) -> str:
    """The first few extents, spelled the way the profile spells them, so a
    consumer reading the refusal can grep their own artifact for it."""
    if not extents:
        return "none"
    shown = ", ".join(_spell(extent) for extent in extents[:limit])
    if len(extents) > limit:
        return f"{shown}, ..."
    return shown


def _mismatch(message: str) -> AssayError:
    return AssayError(
        f"go statement attribution: {message}",
        outcome=Outcome.ERROR,
        reason_code=ReasonCode.UNREADABLE_ARTIFACT,
    )
