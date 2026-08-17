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
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

__all__ = ["BranchCoverage", "CoverageProfile", "FileCoverage"]


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
    """

    executed: frozenset[int]
    missing: frozenset[int]
    excluded: frozenset[int] | None
    branches: "BranchCoverage | None" = None

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


@dataclass(frozen=True, kw_only=True)
class CoverageProfile:
    """A whole parsed coverage artifact: one :class:`FileCoverage` per file
    path exactly as that format's artifact names it (no source-root
    resolution, no path normalization against a project layout — that stays
    the caller's job, the same separation :mod:`assay.diff` keeps from
    :mod:`assay.measurability`).
    """

    files: Mapping[str, FileCoverage]
