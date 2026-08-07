"""The pure four-way changed-line coverage evaluation — the language-free
core P05 exists to prove (DESIGN-GUIDE §11, A-097).

**The claim this module defends:** every changed line is judged by exactly
one of four rules, and the language never enters the decision — only
:class:`~assay.adapters.base.LanguageAdapter`'s five attributes and three
methods do:

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
   line the format simply does not track — is genuinely non-executable and
   is silently ignored: it affects neither the numerator nor the
   denominator, and never fails the claim.

An **uncovered executable line the diff never touched** cannot appear in any
of the four rules above by construction: this module only ever iterates
``added.by_file``, so a file's pre-existing uncovered lines are invisible to
it — changed-line coverage, not whole-file coverage (DESIGN-GUIDE §7:
"never instruments, traces or computes global coverage").

**What this module deliberately does NOT do.** No multi-line statement
attribution and no ``UNCLASSIFIED_LINES`` bucket — both are P07's addition
(``statement_spans``, A-084), and P05's adapter protocol has no such method
to call. A changed line outside the coverage artifact's executable∪excluded
sets is simply rule 4, "non-executable", full stop; P05 never treats a gap
in its own knowledge as an ambiguity to surface.

Nothing here raises: this is pure set arithmetic over already-validated
inputs (a :class:`~assay.diff.AddedLines`, a
:class:`~assay.coverage_parsers.model.CoverageProfile` that already cleared
:func:`assay.coverage.check_empty_coverage`, and an adapter). Every
adverse-outcome decision (``DIRTY_TREE``, ``BASE_IS_HEAD``,
``EMPTY_COVERAGE``, an unknown ``language``) happens strictly BEFORE this
module is ever called — see :mod:`assay.runner`'s ``evaluate_r1``.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping, Sequence

from .adapters.base import LanguageAdapter
from .coverage_parsers.model import CoverageProfile, FileCoverage
from .diff import AddedLines
from .errors import Outcome, ReasonCode

__all__ = ["CoverageEvaluation", "evaluate_coverage"]


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
    §3b.E): called ONLY for a considered file with no coverage-artifact
    entry, to hand its text to
    :meth:`~assay.adapters.base.LanguageAdapter.has_executable_code`. A test
    supplies a dict lookup; :mod:`assay.runner` supplies a real file read.
    """
    cov_by_repo_path: dict[str, FileCoverage] = {
        adapter.normalize_coverage_key(raw_key): file_cov
        for raw_key, file_cov in profile.files.items()
    }

    total_changed_exec = 0
    total_covered = 0
    missing_lines: dict[str, frozenset[int]] = {}
    files_missing: list[str] = []
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
        if lines & excluded and not allow_excluded:
            has_disallowed_excluded = True

        executable = file_cov.executed | file_cov.missing
        changed_exec = lines & executable
        changed_missing = changed_exec & file_cov.missing

        total_changed_exec += len(changed_exec)
        total_covered += len(changed_exec & file_cov.executed)
        if changed_missing:
            missing_lines[path] = frozenset(changed_missing)

    pct = (
        100.0
        if total_changed_exec == 0
        else 100.0 * total_covered / total_changed_exec
    )

    if has_disallowed_excluded:
        outcome = Outcome.FAIL
        reason_code: ReasonCode | None = ReasonCode.EXCLUDED_LINES
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
        outcome=outcome,
        reason_code=reason_code,
    )
