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
:meth:`~assay.adapters.base.LanguageAdapter.normalize_coverage_key`, then
this module's own project-relative-to-repo-relative join, B006 —
:func:`_normalized_profile_files`) to the SAME repository path — e.g. a Go
module path stripped two different ways, or an artifact that simply repeats
a file under two spellings. Silently keeping
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
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Callable, Iterable, Mapping, NoReturn, Sequence

from .adapters.base import LanguageAdapter, StatementSpan
from .coverage import derive_branch_capability, derive_exclusion_capability
from .coverage_parsers.model import CoverageProfile, FileCoverage
from .diff import AddedLines
from .errors import AssayError, Outcome, ReasonCode

__all__ = [
    "CoverageEvaluation",
    "evaluate_coverage",
    "evaluate_targets",
    "resolve_coverage_keys",
]


def _check_statement_attribution(
    profile: CoverageProfile, adapter: LanguageAdapter
) -> None:
    """Refuse a profile *adapter* says needs statement attribution and that
    reports it never received any (A-392).

    The guard exists because the failure it catches is INVISIBLE otherwise.
    An uncorrected Go coverprofile parses cleanly, produces well-formed line
    sets, and yields a plausible percentage -- it is simply about the wrong
    lines, attributing function signatures, closing braces and ``case``
    labels as executable code. That is AGENTS.md's **masked default** in its
    purest form: rendered harmless by every context that happens to run the
    correction, and therefore reachable only on the one path that skips it.
    "The runner remembers to call the oracle" is precisely the check that
    cannot fail, so it is made into one here.

    ``ERROR``/``UNREADABLE_ARTIFACT``, the same pair
    :func:`assay.statement_attribution.attribute_statements` refuses with,
    and for the same reason: what cannot be done is READING this artifact as
    statement truth. It is deliberately not ``NO_MEASUREMENT``/
    ``EMPTY_COVERAGE`` -- a complete artifact reported as empty would be
    AGENTS.md's "absence for emptiness", a false certification rather than a
    missing one (A-392's own rejected alternative). No new reason code is
    minted: :class:`~assay.errors.ReasonCode` is a closed vocabulary on the
    wire, and this wave cuts no schema.
    """
    # Direct attribute access, never `getattr(..., False)`: a default here
    # would let an adapter that forgot to declare the attribute skip the
    # guard silently, which is the exact masked default this guard exists to
    # remove. A missing attribute is a broken adapter and says so loudly.
    if not adapter.requires_statement_attribution:
        return
    if profile.statement_attributed:
        return
    raise AssayError(
        f"the {adapter.name!r} adapter requires statement attribution, but "
        f"the coverage profile it was handed reports "
        f"statement_attributed=False -- its records carry block EXTENTS, "
        f"which are not statement positions, so judging them directly would "
        f"attribute function signatures, closing braces and `case` labels as "
        f"executable code. The source-side oracle "
        f"(LanguageAdapter.statement_blocks) never ran, or its result was "
        f"never joined onto this profile.",
        outcome=Outcome.ERROR,
        reason_code=ReasonCode.UNREADABLE_ARTIFACT,
    )


def _refuse_line_directive_remapped(path: str, *, judged_as: str) -> NoReturn:
    """Refuse a file whose coverage records were remapped by a ``//line``
    directive AND that is in this lane's judged set (A-405).

    **Why refusing is the only honest answer here.** ``go/token`` gives a
    position produced by a ``//line file:line`` directive with no column a
    zero column, and — the part that matters — the LINE NUMBER it carries is
    the directive's, not the file's. Go's own ``TestLineDup`` witness
    (``carve-assets/P27-recarve/linedup.out``) reports lines 100 to 105 for a
    24-line source. ``git diff`` names PHYSICAL lines, so a changed line in
    such a file intersects the profile's virtual line numbers by accident or
    not at all: the file would measure 0 executable of 0 changed and the lane
    would report a clean ``100%`` over a file nothing measured. That is
    DESIGN-GUIDE §5's laundering gate and the north star's "0/0 is never
    100%", reached through an artifact that is exactly what ``go test``
    wrote.

    **Why ``ERROR``/``BAD_LANE_CONFIG`` and not ``UNREADABLE_ARTIFACT``.**
    Nothing is wrong with the artifact — it is byte-for-byte what the real
    toolchain emits for this source, and assay parses all of it. What is
    wrong is that the LANE asked assay to judge a generated file it
    structurally cannot judge, and the remedy is in the lane file: a
    generated source belongs outside ``judge.source_roots`` (or outside
    ``judge.targets``), where assay already ignores it. Blaming the artifact
    for a lane-config fault is precisely the misdirection
    :func:`assay.adapters.go_modfile._refuse`'s own docstring exists to
    prevent, and the reviewer that found this case named that same
    docstring. No new reason code is minted: ``ReasonCode`` is a closed
    vocabulary on the wire, and this wave cuts no schema.

    Not raised for a remapped file OUTSIDE the judged set — that file is not
    under review, its records contribute nothing, and taking a lane down for
    it would leave every Go project carrying one generated file unable to use
    R1 at all.
    """
    raise AssayError(
        f"{path!r} carries coverage records whose positions were remapped by "
        f"a `//line` directive (the profile reports a zero column, which "
        f"`go/token` produces for a `//line file:line` position that names no "
        f"column), so its recorded line numbers are the directive's and not "
        f"this file's. {judged_as}, and assay will not judge a file whose "
        f"measured lines cannot be matched to the lines a diff names -- that "
        f"would report a clean percentage over nothing measured. Generated "
        f"sources belong outside the lane's judge.source_roots (or its "
        f"judge.targets); assay already ignores a `//line`-remapped file that "
        f"is not in the judged set.",
        outcome=Outcome.ERROR,
        reason_code=ReasonCode.BAD_LANE_CONFIG,
    )


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
    #: (wave-1 §4, A-262) changed, considered, non-excluded LINES this
    #: evaluation's own mode counted -- renamed from ``changed_executable``:
    #: under :func:`evaluate_targets`'s whole-target mode nothing about these
    #: lines was "changed" by anything, so the old name would put a false
    #: statement on the wire. Line-only; the branch side gets its own two
    #: integers below, so ``pct`` is the combined value (A-263) while an
    #: independent consumer can still re-derive it from the four counters
    #: alone.
    executable: int
    #: (wave-1 §4, A-263) the COMBINED line+branch percentage: ``(covered +
    #: branches_covered) / (executable + branches_total)``, exactly
    #: ``coverage.py``'s own ``summary.percent_covered`` under
    #: ``--cov-branch``. Degenerates to today's line-only value with no
    #: special case when ``branches_total`` is 0 (``branch_capability ==
    #: "unavailable"``, or a "reported" artifact with genuinely zero arcs on
    #: the judged lines).
    pct: float
    #: changed files under the source roots this evaluation actually
    #: considered — under a source root, adapter-recognised as source, not a
    #: test path, not inside an excluded directory. Present whether or not
    #: the file ended up contributing any executable lines (srdm's
    #: ``considered`` fix: a 0/0 pass explains itself). Under
    #: :func:`evaluate_targets`'s whole-target mode this is instead the
    #: number of declared TARGETS judged (§5 rule 6) -- there is no diff to
    #: intersect, so "changed files considered" has no meaning there.
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
    #: (wave-1 §4, A-257/A-258) branch arcs COVERED across every branch
    #: source line this evaluation's mode counted -- zero when
    #: :attr:`branch_capability` is ``"unavailable"``.
    branches_covered: int
    #: (wave-1 §4) branch arcs this evaluation's mode counted in total --
    #: zero when :attr:`branch_capability` is ``"unavailable"``.
    branches_total: int
    #: (wave-1 §3.2/§4, A-257) whether the coverage FORMAT could report
    #: branch arcs at all -- derived from the parsed profile by
    #: :func:`assay.coverage.derive_branch_capability` BEFORE any per-file
    #: evaluation (Addendum A7), mirroring :attr:`exclusion_capability`
    #: exactly.
    branch_capability: str
    #: (wave-1 §4) counted, branch-source lines with at least one uncovered
    #: arc -- same shape/discipline as :attr:`missing_lines` (absent means
    #: none, never an empty set). A line reached only through rule 3b's span
    #: attribution never appears here (crediting arcs leaving a DIFFERENT
    #: physical line would invent a measurement).
    missing_branch_lines: Mapping[str, frozenset[int]]
    #: (wave-1 §4) paths appearing in :attr:`missing_branch_lines`, sorted --
    #: the same "always present, possibly empty, sorted" discipline every
    #: other ``files_with_*`` summary in this project keeps.
    files_with_missing_branch_lines: tuple[str, ...]
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
    project_root: Path,
    source_root_paths: Sequence[Path],
    fail_under: float,
    allow_excluded: bool,
    read_source_text: Callable[[str], str],
) -> CoverageEvaluation:
    """Intersect *added* with *profile* under *adapter*'s classification.

    *repo_top* and every path in *added.by_file* share one spelling: ``git
    diff``'s — forward-slash, relative to the repository's top level.
    *profile.files* does NOT share that spelling by construction (B006's own
    finding): the lane's coverage command always runs with ``cwd =
    project_root`` (:mod:`assay.runner`'s own ``evaluate_r1``), so a real
    coverage tool's own keys are PROJECT-relative, not repo-relative — the
    two coincide only when *project_root* IS *repo_top* (a root-level
    project, ``project_prefix == "."``). *project_root* is what lets
    :func:`_normalized_profile_files` reconstruct the repo-relative spelling
    (:func:`_project_prefix`, :func:`_to_repo_relative_key`) before this
    function ever compares a profile key against *added.by_file* — see that
    function's own docstring for the adapter-STRIP-vs-core-PREPEND split
    (DESIGN-GUIDE §11). *source_root_paths* are RESOLVED, ABSOLUTE,
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
    docstring, P15), or (``ERROR``/``BAD_LANE_CONFIG``) if *project_root* is
    not contained by *repo_top* (:func:`_project_prefix`, mirroring
    :func:`evaluate_targets`'s own identical, separately-raised guard) —
    the only two ways this function raises at all.
    """
    _check_statement_attribution(profile, adapter)
    project_prefix = _project_prefix(repo_top, project_root)
    cov_by_repo_path = _normalized_profile_files(
        profile, adapter, repo_top=repo_top, project_prefix=project_prefix
    )

    # P21/A-183: derived from the PARSED PROFILE, before any per-file
    # evaluation, so it is a property of the artifact rather than of what
    # this diff happened to touch. A mixed profile refuses here.
    exclusion_capability = derive_exclusion_capability(profile)
    # wave-1 §3.2/§4, A-257/Addendum A7: the identical "artifact-level,
    # before any per-file evaluation" derivation, one field over. The
    # REFUSAL this guards (`judge.require_branch`) is a measurability
    # question and lives beside `check_empty_coverage` in `evaluate_r1`'s
    # own guard sequence -- never here, which is arithmetic only.
    branch_capability = derive_branch_capability(profile)

    total_changed_exec = 0
    total_covered = 0
    total_branches_covered = 0
    total_branches_total = 0
    missing_lines: dict[str, frozenset[int]] = {}
    files_missing: list[str] = []
    unclassified_lines: dict[str, frozenset[int]] = {}
    excluded_lines: dict[str, frozenset[int]] = {}
    missing_branch_lines: dict[str, frozenset[int]] = {}
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

        if file_cov.line_directive_remapped:
            # A-405. Reached only for a file that cleared `_is_considered`
            # above -- source-root boundary, excluded dirs, source globs and
            # `is_test_path` -- AND has changed lines in this diff. A
            # `//line`-remapped file the lane does not judge never gets here,
            # which is the whole point of putting the check inside the loop
            # rather than over the profile.
            _refuse_line_directive_remapped(
                path,
                judged_as=(
                    f"This lane judges it: {len(lines)} changed line(s) in it "
                    f"are inside judge.source_roots"
                ),
            )

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

        # wave-1 §4 rule 1 (A-258): a changed, considered line that is a
        # branch source contributes its arcs. Deliberately over `changed_exec`
        # ONLY, never over any line rule 3b (below) resolves by attribution
        # -- rule 2 says a span-attributed line "contributes nothing to the
        # branch side", because attribution answers which STATEMENT's status
        # a line inherits, and crediting arcs leaving a different physical
        # line (the anchor) would invent a measurement. Rule 3 (a branch
        # source line that is excluded) is unreachable here by construction:
        # `changed_exec` is drawn from `executable = executed | missing`,
        # already disjoint from `excluded` (FileCoverage's own invariant).
        branch_covered, branch_total, branch_missing = _tally_branches(
            changed_exec, file_cov
        )
        total_branches_covered += branch_covered
        total_branches_total += branch_total
        if branch_missing:
            missing_branch_lines[path] = branch_missing

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

    # A-263: the COMBINED line+branch denominator/numerator. Degenerates to
    # today's line-only value with no special case when `total_branches_total`
    # is 0 -- an "unavailable" branch capability, or a "reported" artifact
    # genuinely reporting zero arcs on the lines this evaluation counted.
    total_denominator = total_changed_exec + total_branches_total
    pct = (
        100.0
        if total_denominator == 0
        else 100.0 * (total_covered + total_branches_covered) / total_denominator
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
        # wave-1 §4 outcome precedence: a floor missed PURELY because of
        # branches -- zero missing LINES, at least one uncovered arc -- is
        # UNCOVERED_BRANCHES, never UNCOVERED_LINES. "Which mechanism
        # refused" is the distinction this project exists to keep.
        if not missing_lines and total_branches_covered < total_branches_total:
            reason_code = ReasonCode.UNCOVERED_BRANCHES
        else:
            reason_code = ReasonCode.UNCOVERED_LINES
    else:
        outcome = Outcome.PASS
        reason_code = None

    return CoverageEvaluation(
        covered=total_covered,
        executable=total_changed_exec,
        pct=pct,
        considered=considered,
        missing_lines=MappingProxyType(dict(missing_lines)),
        files_missing_coverage=tuple(sorted(files_missing)),
        unclassified_lines=MappingProxyType(dict(unclassified_lines)),
        files_with_unclassified_lines=tuple(sorted(unclassified_lines)),
        excluded_lines=MappingProxyType(dict(excluded_lines)),
        files_with_excluded_lines=tuple(sorted(excluded_lines)),
        exclusion_capability=exclusion_capability,
        branches_covered=total_branches_covered,
        branches_total=total_branches_total,
        branch_capability=branch_capability,
        missing_branch_lines=MappingProxyType(dict(missing_branch_lines)),
        files_with_missing_branch_lines=tuple(sorted(missing_branch_lines)),
        outcome=outcome,
        reason_code=reason_code,
    )


def _project_prefix(repo_top: Path, project_root: Path) -> PurePosixPath:
    """*project_root*'s own repo-top-relative POSIX location -- ``.`` when
    *project_root* IS *repo_top* (a root-level project, P05/B005's original
    shape), otherwise the real segment(s) beneath it (B006's nested-project
    shape, e.g. ``cmru``). The ONE fact both :func:`evaluate_coverage` (this
    package's own fix) and :func:`evaluate_targets` (wave-1 §5, which
    already needed it to resolve ``judge.targets`` against the profile's own
    keys) need identically -- computed here ONCE so the two modes cannot
    silently disagree about where their shared project sits in its own
    repository, the same "one implementation shared... so drift is
    structurally impossible" discipline :func:`_normalized_profile_files`
    already applies to the key-collision refusal one level over.

    A structural, language-free fact -- never adapter-specific -- so this
    lives in the core, never in a :class:`~assay.adapters.base.LanguageAdapter`
    (DESIGN-GUIDE §11's own split: "the prefix-BOUNDARY reconciliation... is
    universal and lives in the core").

    Raises :class:`~assay.errors.AssayError` (``ERROR``/``BAD_LANE_CONFIG``)
    if *project_root* is not contained by *repo_top* at all -- a lane's
    project has no repo-relative identity there. Mirrors (but cannot share
    code with, since :mod:`assay.evaluate` is never permitted to import
    :mod:`assay.runner`) that module's own structurally-identical
    ``_resolved_project_prefix``, which the real CLI path already runs
    BEFORE this one is ever reached, so this raises here only for a caller
    that skips that upstream guard (e.g. a direct unit test, or
    :mod:`assay.canary`'s own synthetic Go sentinel repo).
    """
    resolved_project_root = project_root.resolve()
    resolved_repo_top = repo_top.resolve()
    try:
        relative = resolved_project_root.relative_to(resolved_repo_top)
    except ValueError as exc:
        raise AssayError(
            f"the project root {resolved_project_root} is not contained by "
            f"its own repository top {resolved_repo_top}; a lane's project "
            f"has no repo-relative identity there",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.BAD_LANE_CONFIG,
        ) from exc
    return (
        PurePosixPath(".") if relative == Path(".") else PurePosixPath(relative.as_posix())
    )


def _to_repo_relative_key(
    key: str, *, repo_top: Path, project_prefix: PurePosixPath
) -> str:
    """*key* (already adapter-normalized, i.e. any language-specific STRIP
    :meth:`~assay.adapters.base.LanguageAdapter.normalize_coverage_key` had
    to apply is already done) resolved to the ONE repo-top-relative spelling
    every consumer of :func:`_normalized_profile_files`'s return value
    shares with ``git diff`` (module docstring; :func:`evaluate_targets`'s
    own ``project_prefix``-joined lookup key depends on this exactly as
    :func:`evaluate_coverage`'s ``added.by_file`` comparison does).

    Two cases, distinguished by :meth:`PurePosixPath.is_absolute` -- exact
    and unambiguous for the forward-slash-only spellings this project's own
    path contract requires (``adapters/base.py``'s own module docstring):

    * *key* is RELATIVE. The lane's coverage command always runs with
      ``cwd = project_root`` (:mod:`assay.runner`'s own ``evaluate_r1``), so
      a relative key a real coverage tool produced is PROJECT-relative by
      construction (confirmed empirically against real ``coverage.py``
      7.15.3: a measured file under the process's own cwd reports a
      cwd-relative path with no special configuration needed) -- never a
      guess from the key's own text, since *which cwd produced it* is a
      fact this function's caller already knows structurally, not
      something inferred here. Joining *project_prefix* (computed ONCE by
      :func:`_project_prefix`, identically for both evaluate entry points)
      turns it into the repo-relative spelling; a no-op for a root-level
      project (``project_prefix == PurePosixPath(".")``, verified directly
      by ``PurePosixPath(".") / "x"  == PurePosixPath("x")`` -- no residual
      ``./`` ever survives), which is why every existing root-project
      consumer is unaffected by this function's own existence.
    * *key* is ABSOLUTE -- real ``coverage.py``'s own fallback when the
      measured file sits outside the ``cwd``-relative tree it can express
      relatively (confirmed the same empirical way: a ``--cov`` target
      outside the process's own cwd reports an absolute path). Joining
      *project_prefix* onto an absolute key would be nonsensical, and is
      never attempted: this resolves *key* against *repo_top* directly
      instead. A key that genuinely names a path beneath the repository
      returns ITS repo-relative identity; a key that does not (a stdlib
      module, a dependency outside the repository entirely, ...) is
      returned UNCHANGED -- inert, never a raise, because every genuine
      lookup key this module's two callers ever construct (``added.by_file``,
      :func:`evaluate_targets`'s own resolved target paths) is relative by
      construction and can therefore never collide with a bare absolute
      string left behind here.
    """
    candidate = PurePosixPath(key)
    if not candidate.is_absolute():
        return (project_prefix / candidate).as_posix()
    try:
        return Path(key).resolve().relative_to(repo_top.resolve()).as_posix()
    except ValueError:
        return key


def _normalized_profile_files(
    profile: CoverageProfile,
    adapter: LanguageAdapter,
    *,
    repo_top: Path,
    project_prefix: PurePosixPath,
) -> dict[str, FileCoverage]:
    """*profile.files*, keyed by REPO-TOP-RELATIVE path after
    :meth:`~assay.adapters.base.LanguageAdapter.normalize_coverage_key` AND
    :func:`_to_repo_relative_key` (module docstring, P15; B006's own fix) --
    the one implementation shared by :func:`evaluate_coverage` and
    :func:`evaluate_targets`, so the "two raw keys, one repository path"
    refusal cannot drift between the two modes.

    **The two-stage split, and why it is two stages, not one (A-145).**
    ``normalize_coverage_key`` speaks the adapter's own LANGUAGE-SPECIFIC
    dialect only -- a Go module path, a coverage-artifact prefix a wider CI
    cwd left behind -- and returns *key* unchanged when there is nothing of
    that kind to strip (every adapter's own documented default). It was
    never responsible for, and cannot by itself resolve, the fact that this
    project's own coverage command ran from *project_root* rather than
    *repo_top* -- that is a structural fact about WHERE this project sits in
    ITS repository, identical for every language, so it belongs in the core
    (:func:`_to_repo_relative_key`) exactly the way source-root boundary
    membership already does (``evaluate.py``'s own ``_is_considered``,
    DESIGN-GUIDE §11). Collapsing the two into one adapter-side step would
    mean re-deriving *project_prefix* once per adapter, and getting it wrong
    once per adapter, instead of once, here.

    Raises :class:`~assay.errors.AssayError`
    (``ERROR``/``UNREADABLE_ARTIFACT``) on a collision -- the only way this
    function raises.
    """
    repo_path_by_raw_key = _repo_path_by_raw_key(
        profile, adapter, repo_top=repo_top, project_prefix=project_prefix
    )
    return {
        repo_path: profile.files[raw_key]
        for raw_key, repo_path in repo_path_by_raw_key.items()
    }


def _repo_path_by_raw_key(
    profile: CoverageProfile,
    adapter: LanguageAdapter,
    *,
    repo_top: Path,
    project_prefix: PurePosixPath,
) -> dict[str, str]:
    """The JOIN ITSELF, in one place: every raw artifact key mapped to its
    repo-top-relative path, in *profile.files*' own iteration order.

    :func:`_normalized_profile_files` inverts this to key by repository path;
    :func:`resolve_coverage_keys` exposes it as-is for a caller that needs to
    go the OTHER way (raw key -> a real file on disk). Both directions are the
    same mapping, computed once here, which is the whole point: A-385/A-367
    rule that there is exactly ONE key resolution, and a second caller
    re-deriving ``normalize_coverage_key`` + ``project_prefix`` for itself is
    precisely the drift this function's own docstring one level up says it
    exists to prevent.

    Raises :class:`~assay.errors.AssayError`
    (``ERROR``/``UNREADABLE_ARTIFACT``) on a collision -- the only way this
    function raises.
    """
    repo_path_by_raw_key: dict[str, str] = {}
    raw_key_by_repo_path: dict[str, str] = {}
    for raw_key in profile.files:
        adapter_key = adapter.normalize_coverage_key(raw_key)
        repo_path = _to_repo_relative_key(
            adapter_key, repo_top=repo_top, project_prefix=project_prefix
        )
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
        repo_path_by_raw_key[raw_key] = repo_path
    return repo_path_by_raw_key


def resolve_coverage_keys(
    profile: CoverageProfile,
    adapter: LanguageAdapter,
    *,
    repo_top: Path,
    project_root: Path,
) -> dict[str, str]:
    """Every key in *profile.files*, spelled as the artifact spells it,
    mapped to its repo-top-relative path -- the SAME resolution
    :func:`evaluate_coverage` and :func:`evaluate_targets` judge by.

    Public because :mod:`assay.runner` needs it (the P27 re-carve): the Go
    statement-position oracle must be handed real files on disk, and
    :func:`assay.statement_attribution.attribute_statements` is keyed by the
    ARTIFACT's own spelling, so the runner needs both ends of exactly this
    mapping. Exposing it is the alternative to the runner calling
    ``adapter.normalize_coverage_key`` and re-deriving ``project_prefix``
    itself, which would put a second, independently-maintained copy of the
    join beside the one that decides the verdict (A-385/A-367: there is ONE
    join). If they ever disagreed, the oracle would correct lines for one
    file while the evaluator judged another, and nothing would say so.

    Raises the identical two errors :func:`evaluate_coverage` raises:
    ``ERROR``/``BAD_LANE_CONFIG`` when *project_root* is not contained by
    *repo_top*, and ``ERROR``/``UNREADABLE_ARTIFACT`` on a normalized-key
    collision. A caller reaching this before evaluation therefore meets the
    same refusals in the same order, one step earlier.
    """
    project_prefix = _project_prefix(repo_top, project_root)
    return _repo_path_by_raw_key(
        profile, adapter, repo_top=repo_top, project_prefix=project_prefix
    )


def _tally_branches(
    lines: Iterable[int], file_cov: FileCoverage
) -> tuple[int, int, frozenset[int]]:
    """Branch arcs contributed by *lines* against *file_cov*'s own branch
    detail (wave-1 §4). *lines* must already be the DIRECTLY-classified set
    a caller wants credited -- this function does not know or care whether
    that is a changed-line intersection (:func:`evaluate_coverage`) or a
    whole target's complete executable set (:func:`evaluate_targets`); both
    modes share the identical per-line arithmetic (A-258's rule extends
    unchanged to B005, per §5 rule 5).

    Returns ``(covered, total, missing_lines)``. ``missing_lines`` is the
    subset of *lines* that are branch sources carrying at least one
    uncovered arc -- same "absent means none" discipline as every other
    line-set field in this project. A line absent from
    :attr:`~assay.coverage_parsers.model.FileCoverage.branches`' own
    ``by_line`` (not a branch source at all) contributes nothing, and
    ``file_cov.branches is None`` (branch capability ``"unavailable"`` for
    this file) contributes nothing to any of the three.
    """
    if file_cov.branches is None:
        return 0, 0, frozenset()
    covered = 0
    total = 0
    missing: set[int] = set()
    for line in lines:
        arc = file_cov.branches.by_line.get(line)
        if arc is None:
            continue
        line_covered, line_total = arc
        covered += line_covered
        total += line_total
        if line_covered < line_total:
            missing.add(line)
    return covered, total, frozenset(missing)


def _resolve_whole_target(
    raw_target: str,
    *,
    adapter: LanguageAdapter,
    project_root: Path,
    source_root_paths: Sequence[Path],
) -> str:
    """One declared ``judge.targets`` entry, resolved and structurally
    validated against the REAL filesystem of the snapshot being judged
    (wave-1 §5 rule 2) -- deliberately never at lane-load time, because a
    whole-target lane must be judgeable from any commit including a
    post-merge ``main`` (A-260's whole point), and a target's existence and
    kind are facts of the commit being judged, not of the declaration.

    Checks run in exactly :func:`assay.config._load_canary`'s own order --
    symlink, then containment, then existence/kind -- so the two
    project-relative-path gates in this file agree about which failure a
    consumer sees first.

    Refuses ``ERROR``/``BAD_LANE_CONFIG``, naming *raw_target* and the gate
    it failed: a symlink (``is_symlink`` never raises for a non-existent
    path, so it is checked before anything that would); outside every
    declared source root; anything other than a REGULAR FILE -- never a
    directory, deliberately: a directory target expanding to N files of
    which only one is measured would PASS while leaving the rest unjudged,
    which is precisely the vacuity hole this whole mode exists to close;
    inside an adapter-excluded directory; not adapter-recognised source; or
    a test path per the adapter's own convention.

    Returns the target's PROJECT-relative POSIX spelling (the profile
    lookup's repo-top-relative conversion is the caller's job, §5 rule 1).
    """
    candidate = project_root / raw_target
    if candidate.is_symlink():
        raise AssayError(
            f"judge.targets entry {raw_target!r} is a symlink; a "
            f"whole-target entry must be a real, ordinary source file",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.BAD_LANE_CONFIG,
        )
    resolved = candidate.resolve()
    if not any(resolved.is_relative_to(root) for root in source_root_paths):
        raise AssayError(
            f"judge.targets entry {raw_target!r} resolves to {resolved}, "
            f"which is not contained beneath any declared source root "
            f"{[str(root) for root in source_root_paths]}",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.BAD_LANE_CONFIG,
        )
    if not resolved.is_file():
        raise AssayError(
            f"judge.targets entry {raw_target!r} does not exist as a "
            f"regular file under the project root {project_root} (looked "
            f"for {resolved}); a whole-target entry is always a regular "
            f"file, never a directory",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.BAD_LANE_CONFIG,
        )
    rel_to_project = resolved.relative_to(project_root).as_posix()
    if any(
        part in adapter.excluded_dir_names
        for part in Path(rel_to_project).parts[:-1]
    ):
        raise AssayError(
            f"judge.targets entry {raw_target!r} sits inside an excluded "
            f"directory ({sorted(adapter.excluded_dir_names)})",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.BAD_LANE_CONFIG,
        )
    if not any(
        fnmatch.fnmatch(rel_to_project, glob) for glob in adapter.source_globs
    ):
        raise AssayError(
            f"judge.targets entry {raw_target!r} is not adapter-recognised "
            f"source ({list(adapter.source_globs)})",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.BAD_LANE_CONFIG,
        )
    if adapter.is_test_path(rel_to_project):
        raise AssayError(
            f"judge.targets entry {raw_target!r} is a test path per the "
            f"adapter's own convention",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.BAD_LANE_CONFIG,
        )
    return rel_to_project


def evaluate_targets(
    *,
    profile: CoverageProfile,
    adapter: LanguageAdapter,
    repo_top: Path,
    project_root: Path,
    targets: Sequence[str],
    source_root_paths: Sequence[Path],
    fail_under: float,
    allow_excluded: bool,
) -> CoverageEvaluation:
    """B005's whole-target judge (wave-1 §5, A-260): intersect a FIXED,
    lane-declared set of source files with *profile* -- never a diff.

    Returns the SAME :class:`CoverageEvaluation` type
    :func:`evaluate_coverage` returns, keeping one claim-assembly path for
    both modes. Deliberately NOT ``evaluate_coverage`` with a mode flag:
    that function's whole contract is "intersect a diff with a profile", and
    a mode parameter making half its parameters meaningless is the shape
    that produces vacuous passes.

    There is no diff, no base, and no span attribution here: every judged
    line comes straight from the artifact, so
    :attr:`CoverageEvaluation.unclassified_lines` is always empty and
    :attr:`~CoverageEvaluation.files_with_unclassified_lines` always ``()``,
    and :attr:`~CoverageEvaluation.files_missing_coverage` is always ``()``
    too -- an unmeasured target refuses outright (below) rather than
    falling through to that field the way an unmeasured changed-line file
    does.

    *targets* are the lane's declared, already load-validated (§5's
    canonical-spelling grammar, :mod:`assay.config`) project-relative
    paths. Each is resolved and validated against the real snapshot
    filesystem by :func:`_resolve_whole_target`, then looked up in *profile*
    by its REPO-TOP-RELATIVE spelling -- the profile's own RAW keys are
    PROJECT-relative (the lane's coverage command always runs with ``cwd =
    project_root``); :func:`_normalized_profile_files` (via
    :func:`_to_repo_relative_key`) is what turns them into the
    repo-top-relative spelling this lookup needs, B006's own fix (rule 1
    now holds by construction rather than by assumption). A target absent
    from the profile, or present with zero executable lines, is B005's own
    load-bearing anti-vacuity guard: ``NO_MEASUREMENT``/``TARGET_NOT_MEASURED``,
    naming the target, rather than a vacuous ``100%`` of nothing -- the
    stopgap this mode replaces reports exactly that vacuous pass when
    ``--cov=`` names a module nothing ever imported.

    *considered* is the number of declared TARGETS judged (§5 rule 6),
    never a count of files -- there is no diff to have "considered" a file
    against.

    Raises :class:`~assay.errors.AssayError` -- ``ERROR``/``BAD_LANE_CONFIG``
    when *project_root* is not contained by *repo_top*
    (:func:`_project_prefix`), plus ``ERROR``/``UNREADABLE_ARTIFACT`` on the
    identical normalized-key collision :func:`evaluate_coverage` refuses
    (module docstring, P15).
    """
    _check_statement_attribution(profile, adapter)
    project_prefix = _project_prefix(repo_top, project_root)
    cov_by_repo_path = _normalized_profile_files(
        profile, adapter, repo_top=repo_top, project_prefix=project_prefix
    )
    exclusion_capability = derive_exclusion_capability(profile)
    branch_capability = derive_branch_capability(profile)

    total_executable = 0
    total_covered = 0
    total_branches_covered = 0
    total_branches_total = 0
    missing_lines: dict[str, frozenset[int]] = {}
    excluded_lines: dict[str, frozenset[int]] = {}
    missing_branch_lines: dict[str, frozenset[int]] = {}
    has_disallowed_excluded = False

    for raw_target in targets:
        rel_to_project = _resolve_whole_target(
            raw_target,
            adapter=adapter,
            project_root=project_root.resolve(),
            source_root_paths=source_root_paths,
        )
        repo_path = (project_prefix / rel_to_project).as_posix()
        file_cov = cov_by_repo_path.get(repo_path)
        if file_cov is not None and file_cov.line_directive_remapped:
            # A-405, whole-target mode's own half of the same rule. Checked
            # BEFORE the `TARGET_NOT_MEASURED` guard below, which such a file
            # would otherwise trip (its line sets are emptied by
            # `attribute_statements`): "this target has zero executable
            # lines" is true but names the wrong cause, and the remedy it
            # implies -- write tests -- is not the remedy.
            _refuse_line_directive_remapped(
                repo_path,
                judged_as=(
                    f"This lane judges it: it is declared in judge.targets as "
                    f"{raw_target!r}"
                ),
            )
        target_executable = (
            file_cov.executed | file_cov.missing
            if file_cov is not None
            else frozenset()
        )
        if file_cov is None or not target_executable:
            raise AssayError(
                f"judge.targets entry {raw_target!r} (coverage artifact key "
                f"{repo_path!r}) has "
                f"{'no entry at all' if file_cov is None else 'zero executable lines'} "
                f"in the coverage artifact -- a whole-target floor refuses "
                f"rather than pass on a target that was never measured",
                outcome=Outcome.NO_MEASUREMENT,
                reason_code=ReasonCode.TARGET_NOT_MEASURED,
            )

        excluded = file_cov.excluded if file_cov.excluded is not None else frozenset()
        if excluded:
            excluded_lines[repo_path] = frozenset(excluded)
            if not allow_excluded:
                has_disallowed_excluded = True
        if file_cov.missing:
            missing_lines[repo_path] = frozenset(file_cov.missing)

        total_executable += len(target_executable)
        total_covered += len(file_cov.executed)

        # wave-1 §5 rule 5: branch arithmetic identical to §4, over EVERY
        # branch line of the target rather than a changed subset -- there
        # is no span attribution here, so every branch-source line in
        # `target_executable` is directly classified.
        branch_covered, branch_total, branch_missing = _tally_branches(
            target_executable, file_cov
        )
        total_branches_covered += branch_covered
        total_branches_total += branch_total
        if branch_missing:
            missing_branch_lines[repo_path] = branch_missing

    total_denominator = total_executable + total_branches_total
    pct = (
        100.0
        if total_denominator == 0
        else 100.0 * (total_covered + total_branches_covered) / total_denominator
    )

    if has_disallowed_excluded:
        outcome = Outcome.FAIL
        reason_code: ReasonCode | None = ReasonCode.EXCLUDED_LINES
    elif pct < fail_under:
        outcome = Outcome.FAIL
        if not missing_lines and total_branches_covered < total_branches_total:
            reason_code = ReasonCode.UNCOVERED_BRANCHES
        else:
            reason_code = ReasonCode.UNCOVERED_LINES
    else:
        outcome = Outcome.PASS
        reason_code = None

    return CoverageEvaluation(
        covered=total_covered,
        executable=total_executable,
        pct=pct,
        considered=len(targets),
        missing_lines=MappingProxyType(dict(missing_lines)),
        files_missing_coverage=(),
        unclassified_lines=MappingProxyType({}),
        files_with_unclassified_lines=(),
        excluded_lines=MappingProxyType(dict(excluded_lines)),
        files_with_excluded_lines=tuple(sorted(excluded_lines)),
        exclusion_capability=exclusion_capability,
        branches_covered=total_branches_covered,
        branches_total=total_branches_total,
        branch_capability=branch_capability,
        missing_branch_lines=MappingProxyType(dict(missing_branch_lines)),
        files_with_missing_branch_lines=tuple(sorted(missing_branch_lines)),
        outcome=outcome,
        reason_code=reason_code,
    )
