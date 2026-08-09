"""The pure changed-line coverage evaluation — the language-free core P05
exists to prove (DESIGN-GUIDE §11, A-097), extended once by P07 to attempt
statement-span attribution before giving up on an unattributed line
(A-084/A-100/A-101).

**The claim this module defends:** every changed line is judged
deterministically, and the language never enters the decision — only
:class:`~assay.adapters.base.LanguageAdapter`'s attributes and methods do:

1. a changed line the coverage artifact calls **executed** is covered —
   contributes to both the numerator and the denominator;
2. a changed line the coverage artifact calls **missing** is uncovered —
   contributes to the denominator only, and is recorded in
   :attr:`CoverageEvaluation.missing_lines`;
3. a changed line the coverage artifact calls **excluded** (``pragma: no
   cover`` and its format-specific equivalents) FAILS the claim unless the
   lane declared ``allow_excluded`` — a deliberate opt-in, never a silent
   pass (A-018);
4. a changed line in **none** of the above — a comment, a blank line, a
   line the format simply does not track, OR the interior of a multi-line
   statement whose own attribution (rule 3b, below) could not resolve it —
   is genuinely non-executable and is silently ignored: it affects neither
   the numerator nor the denominator, and never fails the claim;
3b. **(P07)** a changed line in none of 1-3, on an adapter whose
   :attr:`~assay.adapters.base.LanguageAdapter.requires_span_attribution` is
   ``True``, is first offered to
   :meth:`~assay.adapters.base.LanguageAdapter.statement_spans`: if its
   enclosing statement's own tracked line resolves it to executed or
   missing, it inherits that status (contributing to the numerator/
   denominator exactly as rules 1/2 do); if no span contains it, or the
   line IS itself an untracked statement start, it falls through to rule 4
   unchanged; if the spans covering it are internally ambiguous (an
   adapter's own inconsistent analysis, A-101) or its enclosing statement's
   own status is itself unknown, the line is genuinely **unclassified** —
   recorded in :attr:`CoverageEvaluation.unclassified_lines` and FAILING the
   claim unconditionally (A-100), never silently passed or silently
   dropped like rule 4's genuine non-code.

An **uncovered executable line the diff never touched** cannot appear in any
rule above by construction: this module only ever iterates
``added.by_file``, so a file's pre-existing uncovered lines are invisible to
it — changed-line coverage, not whole-file coverage (DESIGN-GUIDE §7:
"never instruments, traces or computes global coverage").

Almost nothing here raises: this is pure set arithmetic (plus, for rule 3b, a
pure containment resolution over already-validated
:class:`~assay.adapters.base.StatementSpan` values) over already-validated
inputs (a :class:`~assay.diff.AddedLines`, a
:class:`~assay.coverage_parsers.model.CoverageProfile` that already cleared
:func:`assay.coverage.check_empty_coverage`, and an adapter). Every
adverse-outcome decision (``DIRTY_TREE``, ``BASE_IS_HEAD``,
``EMPTY_COVERAGE``, an unknown ``language``) happens strictly BEFORE this
module is ever called — see :mod:`assay.runner`'s ``evaluate_r1``.

**One deliberate exception (P15, A-067 finding 4).** Two distinct raw
coverage-artifact keys can normalize (via
:meth:`~assay.adapters.base.LanguageAdapter.normalize_coverage_key`) to the
SAME repository path — e.g. a Go module path stripped two different ways, or
an artifact that simply repeats a file under two spellings. Silently keeping
"whichever key came last in the artifact's own JSON object" (a bare dict
comprehension's natural behaviour) makes a verdict depend on byte order in a
file nothing about the *lane* declares — reversing the two keys in the
artifact can flip PASS to FAIL on otherwise-identical data. This is refused,
never resolved by a precedence rule, as :class:`~assay.errors.AssayError`
(``ERROR``/``UNREADABLE_ARTIFACT`` — the artifact's own claims about itself
are not self-consistent) — a structural failure exactly like a malformed
record, propagating uncaught through :mod:`assay.runner`'s ``evaluate_r1``
the same way ``FORMAT_MISMATCH``/``UNREADABLE_ARTIFACT`` already do.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping, Sequence

from .adapters.base import LanguageAdapter, StatementSpan
from .coverage import derive_exclusion_capability
from .coverage_parsers.model import CoverageProfile, FileCoverage
from .diff import AddedLines
from .errors import AssayError, Outcome, ReasonCode

__all__ = ["CoverageEvaluation", "evaluate_coverage"]


class _Attribution(Enum):
    """The three-way result of resolving one unattributed line against a
    set of :class:`~assay.adapters.base.StatementSpan` values (P07).

    Not exported: internal to this module's own rule 3b, and tested
    directly (per the handoff's own instruction) via
    ``assay.evaluate._Attribution``/``_attribute_line`` rather than only
    through :func:`evaluate_coverage`'s public surface.
    """

    #: No span contains the line at all — a comment or blank line sitting
    #: between statements. Correctly "not code", the same as rule 4's
    #: existing treatment of a line outside every tracked set.
    NOT_CODE = auto()
    #: Two or more spans contain the line but do not form a strict
    #: containment chain (neither is a subset of the other) — an adapter's
    #: own internally-inconsistent analysis (A-101), never a naturally-
    #: occurring case for correctly-nested real Python.
    AMBIGUOUS = auto()


def _attribute_line(
    line: int, spans: Sequence[StatementSpan]
) -> int | _Attribution:
    """Resolve *line* against *spans* (already-validated, possibly nested,
    possibly overlapping :class:`~assay.adapters.base.StatementSpan`
    values).

    Returns the START LINE of the smallest span properly nesting *line*
    when exactly one such anchor is determined unambiguously (every pair of
    spans containing *line* forms a strict containment chain — the smallest
    wins, the same resolution dstdns's own ``attribute_line`` performs by
    picking the smallest span, made explicit and defensive here: a set of
    spans that do NOT nest is refused as :attr:`_Attribution.AMBIGUOUS`
    rather than silently resolved by whichever happened to sort first).
    Returns :attr:`_Attribution.NOT_CODE` when no span contains *line* at
    all.

    Pure and total: never raises, given any sequence of already-validated
    spans (malformed spans — a non-positive or backwards range — cannot
    reach here at all, because :class:`~assay.adapters.base.StatementSpan`
    refuses to construct one, A-092).
    """
    containing = [span for span in spans if span.start_line <= line <= span.end_line]
    if not containing:
        return _Attribution.NOT_CODE
    ordered = sorted(containing, key=lambda span: span.end_line - span.start_line)
    for index, inner in enumerate(ordered):
        for outer in ordered[index + 1 :]:
            if not (
                outer.start_line <= inner.start_line
                and inner.end_line <= outer.end_line
            ):
                return _Attribution.AMBIGUOUS
    return ordered[0].start_line


@dataclass(frozen=True, kw_only=True)
class CoverageEvaluation:
    """The four-way union's result — everything :mod:`assay.runner` needs to
    build an R1 :class:`~assay.verdict.Claim`, already typed rather than a
    bare tuple or dict (A-092).
    """

    covered: int
    changed_executable: int
    pct: float
    #: changed files under the source roots this evaluation actually
    #: considered — under a source root, adapter-recognised as source, not a
    #: test path, not inside an excluded directory. Present whether or not
    #: the file ended up contributing any executable lines (srdm's
    #: ``considered`` fix: a 0/0 pass explains itself).
    considered: int
    #: changed, executable, non-excluded lines that were NOT executed,
    #: keyed exactly like :attr:`assay.diff.AddedLines.by_file` (A-096). A
    #: file with zero such lines is absent from this mapping, never present
    #: with an empty set — the same "absent means none, not empty" contract
    #: ``AddedLines`` itself keeps.
    missing_lines: Mapping[str, frozenset[int]]
    #: changed, considered, source files with NO entry at all in the
    #: coverage artifact but with real executable content per
    #: :meth:`~assay.adapters.base.LanguageAdapter.has_executable_code`,
    #: sorted (A-096).
    files_missing_coverage: tuple[str, ...]
    #: (P07, A-096's third pair) changed lines rule 3b could not resolve —
    #: neither confidently non-code nor confidently attributed to a tracked
    #: statement's status — keyed exactly like :attr:`missing_lines`. A file
    #: contributing none is absent, never present with an empty set. Always
    #: FAILS the claim when non-empty (A-100), independent of ``pct``.
    unclassified_lines: Mapping[str, frozenset[int]]
    #: (P07) paths appearing in :attr:`unclassified_lines`, sorted — the
    #: same "always present, possibly empty, sorted" discipline
    #: :attr:`files_missing_coverage` already established, offered for a
    #: consumer that wants "which files have a problem" without iterating
    #: the line-level mapping.
    files_with_unclassified_lines: tuple[str, ...]
    #: (P16) changed, considered lines the coverage artifact classifies
    #: EXCLUDED, keyed exactly like :attr:`missing_lines` — a file
    #: contributing none is absent, never present with an empty set. Recorded
    #: regardless of ``allow_excluded``: this is WHICH lines were excluded,
    #: not whether that was permitted (:attr:`outcome`/:attr:`reason_code`
    #: already carry the permitted-or-not judgement). Without this, an
    #: independent consumer has a FAIL/EXCLUDED_LINES verdict but no way to
    #: re-derive it from the payload alone (sol finding 2).
    excluded_lines: Mapping[str, frozenset[int]]
    #: (P16) paths appearing in :attr:`excluded_lines`, sorted — the same
    #: "always present, possibly empty, sorted" discipline
    #: :attr:`files_with_unclassified_lines` already established.
    files_with_excluded_lines: tuple[str, ...]
    #: (P21/A-183) whether the coverage FORMAT could report exclusions at
    #: all — derived from the parsed profile by
    #: :func:`assay.coverage.derive_exclusion_capability` BEFORE any
    #: evaluation, so it describes the artifact rather than this
    #: evaluation's own outcome. Carried through to
    #: :attr:`assay.verdict.Coverage.exclusion_capability` unchanged.
    exclusion_capability: str
    #: PASS or FAIL — never any other :class:`~assay.errors.Outcome`. The
    #: three NO_MEASUREMENT causes are guarded before this function is ever
    #: called (A-090) and are not this module's concern.
    outcome: Outcome
    #: ``None`` iff ``outcome is Outcome.PASS`` (A-051's pairing rule,
    #: mirrored here so :mod:`assay.runner` can pass this straight into a
    #: :class:`~assay.verdict.Claim`).
    reason_code: ReasonCode | None


def _is_considered(
    path: str,
    *,
    abs_path: Path,
    source_root_paths: Sequence[Path],
    adapter: LanguageAdapter,
) -> bool:
    """Every gate a changed file must clear before it counts toward
    ``considered`` at all — source-root boundary, excluded directories,
    the adapter's own source globs, and test-path exclusion, in that order.
    """
    if not any(abs_path.is_relative_to(root) for root in source_root_paths):
        return False
    if any(part in adapter.excluded_dir_names for part in Path(path).parts[:-1]):
        return False
    if not any(fnmatch.fnmatch(path, glob) for glob in adapter.source_globs):
        return False
    if adapter.is_test_path(path):
        return False
    return True


def evaluate_coverage(
    *,
    added: AddedLines,
    profile: CoverageProfile,
    adapter: LanguageAdapter,
    repo_top: Path,
    source_root_paths: Sequence[Path],
    fail_under: float,
    allow_excluded: bool,
    read_source_text: Callable[[str], str],
) -> CoverageEvaluation:
    """Intersect *added* with *profile* under *adapter*'s classification.

    *repo_top* and every path in *added.by_file* / *profile.files* (after
    :meth:`~assay.adapters.base.LanguageAdapter.normalize_coverage_key`)
    share one spelling: ``git diff``'s — forward-slash, relative to the
    repository's top level. *source_root_paths* are RESOLVED, ABSOLUTE,
    existing directories (:attr:`assay.config.JudgeConfig.source_root_paths`'s
    own contract); boundary membership is decided by
    :meth:`pathlib.Path.is_relative_to` on the resolved absolute path, never
    by string prefix — the same discipline
    :func:`assay.measurability.check_dirty_tree` already applies, for the
    same reason (``src/foo`` must not match ``src/foo_evil``).

    *read_source_text* is the injectable filesystem boundary (AUTHORING.md
    §3b.E): called for a considered file with no coverage-artifact entry, to
    hand its text to
    :meth:`~assay.adapters.base.LanguageAdapter.has_executable_code`; and
    (P07) also for a considered file that DOES have a coverage-artifact
    entry but has at least one changed line rule 3b's span attribution must
    attempt, on an adapter whose ``requires_span_attribution`` is ``True`` —
    to hand its text to
    :meth:`~assay.adapters.base.LanguageAdapter.statement_spans`. Never
    called for a file with no unattributed lines, or on an adapter that
    declares ``requires_span_attribution=False``. A test supplies a dict
    lookup; :mod:`assay.runner` supplies a real file read.

    Raises :class:`~assay.errors.AssayError`
    (``ERROR``/``UNREADABLE_ARTIFACT``) if two distinct raw keys in
    *profile.files* normalize to the same repository path (module
    docstring, P15) — the only way this function raises at all.
    """
    cov_by_repo_path: dict[str, FileCoverage] = {}
    raw_key_by_repo_path: dict[str, str] = {}
    for raw_key, file_cov in profile.files.items():
        repo_path = adapter.normalize_coverage_key(raw_key)
        colliding_raw_key = raw_key_by_repo_path.get(repo_path)
        if colliding_raw_key is not None:
            raise AssayError(
                f"coverage artifact keys {colliding_raw_key!r} and "
                f"{raw_key!r} both normalize to repository path "
                f"{repo_path!r} -- ambiguous which one's data applies, so "
                f"the artifact cannot be judged without inventing a "
                f"precedence rule the lane never declared.",
                outcome=Outcome.ERROR,
                reason_code=ReasonCode.UNREADABLE_ARTIFACT,
            )
        raw_key_by_repo_path[repo_path] = raw_key
        cov_by_repo_path[repo_path] = file_cov

    # P21/A-183: derived from the PARSED PROFILE, before any per-file
    # evaluation, so it is a property of the artifact rather than of what
    # this diff happened to touch. A mixed profile refuses here.
    exclusion_capability = derive_exclusion_capability(profile)

    total_changed_exec = 0
    total_covered = 0
    missing_lines: dict[str, frozenset[int]] = {}
    files_missing: list[str] = []
    unclassified_lines: dict[str, frozenset[int]] = {}
    excluded_lines: dict[str, frozenset[int]] = {}
    considered = 0
    has_disallowed_excluded = False

    for path, lines in added.by_file.items():
        abs_path = (repo_top / path).resolve()
        if not _is_considered(
            path,
            abs_path=abs_path,
            source_root_paths=source_root_paths,
            adapter=adapter,
        ):
            continue

        considered += 1

        file_cov = cov_by_repo_path.get(path)
        if file_cov is None:
            text = read_source_text(path)
            if not adapter.has_executable_code(path, text):
                continue
            missing_lines[path] = frozenset(lines)
            files_missing.append(path)
            total_changed_exec += len(lines)
            continue

        excluded = file_cov.excluded if file_cov.excluded is not None else frozenset()
        changed_excluded = lines & excluded
        if changed_excluded:
            # Recorded regardless of allow_excluded (P16): this is WHICH
            # lines were excluded, not whether that was permitted --
            # outcome/reason_code already carry the permitted-or-not
            # judgement (rule 3, module docstring).
            excluded_lines[path] = frozenset(changed_excluded)
            if not allow_excluded:
                has_disallowed_excluded = True

        executable = file_cov.executed | file_cov.missing
        changed_exec = lines & executable
        changed_missing = set(changed_exec & file_cov.missing)

        total_changed_exec += len(changed_exec)
        total_covered += len(changed_exec & file_cov.executed)

        # Rule 3b (P07): a changed line in none of executed/missing/excluded
        # is offered to span attribution before falling through to rule 4,
        # on an adapter that declares it needs this at all.
        unattributed = lines - executable - excluded
        file_unclassified: set[int] = set()
        if unattributed and adapter.requires_span_attribution:
            spans = adapter.statement_spans(read_source_text(path))
            for line in sorted(unattributed):
                if spans is None:
                    # The file could not be parsed at all — cannot attribute
                    # ANY of these lines; flag them all rather than guess.
                    file_unclassified.add(line)
                    continue
                anchor = _attribute_line(line, spans)
                if anchor is _Attribution.NOT_CODE:
                    continue  # a comment/blank line between statements
                if anchor is _Attribution.AMBIGUOUS:
                    file_unclassified.add(line)
                    continue
                if anchor == line:
                    # The line IS itself a statement's own start, yet still
                    # untracked by the coverage format (e.g. a decorator
                    # line, or a construct the format simply never traces)
                    # — genuinely non-executable, same as a comment.
                    continue
                if anchor in file_cov.executed:
                    total_changed_exec += 1
                    total_covered += 1
                elif anchor in file_cov.missing:
                    total_changed_exec += 1
                    changed_missing.add(line)
                else:
                    # A real enclosing statement was found, but its OWN
                    # status is unknown too (e.g. excluded, or some other
                    # untracked construct) — a genuine ambiguity, not a
                    # guess (A-100's "genuinely unattributable" case).
                    file_unclassified.add(line)

        if changed_missing:
            missing_lines[path] = frozenset(changed_missing)
        if file_unclassified:
            unclassified_lines[path] = frozenset(file_unclassified)

    pct = (
        100.0
        if total_changed_exec == 0
        else 100.0 * total_covered / total_changed_exec
    )

    if unclassified_lines:
        # A-100: unattributable/overlapping/malformed spans are an
        # unconditional FAIL, ranked ahead of the other two FAIL causes —
        # the same precedence dstdns's own `Verdict.passed` applies (its
        # `unclassified` bucket is checked before `excluded`, before `pct`).
        # No ambiguity becomes PASS, and no ambiguity is merely folded into
        # a percentage that might still clear the floor.
        outcome = Outcome.FAIL
        reason_code: ReasonCode | None = ReasonCode.UNCLASSIFIED_LINES
    elif has_disallowed_excluded:
        outcome = Outcome.FAIL
        reason_code = ReasonCode.EXCLUDED_LINES
    elif pct < fail_under:
        outcome = Outcome.FAIL
        reason_code = ReasonCode.UNCOVERED_LINES
    else:
        outcome = Outcome.PASS
        reason_code = None

    return CoverageEvaluation(
        covered=total_covered,
        changed_executable=total_changed_exec,
        pct=pct,
        considered=considered,
        missing_lines=MappingProxyType(dict(missing_lines)),
        files_missing_coverage=tuple(sorted(files_missing)),
        unclassified_lines=MappingProxyType(dict(unclassified_lines)),
        files_with_unclassified_lines=tuple(sorted(unclassified_lines)),
        excluded_lines=MappingProxyType(dict(excluded_lines)),
        files_with_excluded_lines=tuple(sorted(excluded_lines)),
        exclusion_capability=exclusion_capability,
        outcome=outcome,
        reason_code=reason_code,
    )
