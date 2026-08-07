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

__all__ = ["CoverageProfile", "FileCoverage"]


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
    """

    executed: frozenset[int]
    missing: frozenset[int]
    excluded: frozenset[int] | None

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
