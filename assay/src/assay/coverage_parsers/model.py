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
import cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

__all__ = ["CoverageProfile", "FileCoverage"]


@dataclass(frozen=True, kw_only=True)
class FileCoverage:
    """One file's line classification, format-normalized.

    ``executed`` and ``missing`` are disjoint: a line is one or the other,
    never both, in every format this registry parses. ``excluded`` is
    documented above; it is never validated against ``executed``/``missing``
    here because a format that HAS exclusions (coverage.py) reports an
    excluded line in neither of the other two sets — excluding is its own
    third bucket, not a subset of "missing".
    """

    executed: frozenset[int]
    missing: frozenset[int]
    excluded: frozenset[int] | None

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
