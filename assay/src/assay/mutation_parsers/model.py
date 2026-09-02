"""The normalized types every ingested mutation-report parser produces.

The exact counterpart of :mod:`assay.coverage_parsers.model` one tier over,
and separate from :mod:`assay.verdict`'s own :class:`~assay.verdict.
MutantOutcome` for a reason that is not style: :mod:`assay.config` imports
this package to close ``judge.mutation.format`` against the registry's own
keys (A-068's discipline), and :mod:`assay.verdict` already imports
:mod:`assay.config`. A parser that built :class:`~assay.verdict.MutantOutcome`
directly would close a real ``config -> mutation_parsers -> verdict ->
config`` cycle. So a parser produces THESE types, and the layer that knows
about scope, buckets and judgment translates them -- which is also where the
translation belongs, since a parser cannot know a lane's diff.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

__all__ = [
    "INGESTED_MUTANT_STATUSES",
    "IngestedMutant",
    "IngestedMutationReport",
    "MutationProducerIdentity",
]

#: Every ``MutantStatus`` the upstream ``mutation-testing-report-schema``
#: defines, transcribed from that package's own
#: ``src/mutation-testing-report-schema.json`` (version 3.8.4, vendored under
#: ``tests/fixtures/mutation/PROVENANCE.md``) rather than from documentation
#: or from what one real run happened to emit.
#:
#: Closed on purpose. A status this set does not name is REFUSED rather than
#: ignored: a future Stryker that adds one would otherwise have its new
#: outcome silently dropped from both the numerator and the denominator, and
#: a mutation score computed over a subset of the mutants a tool actually ran
#: is a number that reads like the real one and is not.
INGESTED_MUTANT_STATUSES: frozenset[str] = frozenset(
    {
        "Killed",
        "Survived",
        "NoCoverage",
        "Timeout",
        "CompileError",
        "RuntimeError",
        "Ignored",
        "Pending",
    }
)


@dataclass(frozen=True, kw_only=True)
class MutationProducerIdentity:
    """WHO wrote the report, copied verbatim out of it.

    Declared by artifact, never verified -- assay read three strings from a
    file the lane's own command wrote. This is why it is not a ``helpers[]``
    entry (A-230a/A-361): ``helpers[]`` records tools assay itself invoked.
    """

    name: str
    version: str
    report_schema_version: str


@dataclass(frozen=True, kw_only=True)
class IngestedMutant:
    """One mutant, normalized onto assay's own identity grammar.

    :attr:`path` is the report's own ``files`` KEY, verbatim and unresolved.
    Resolving it against the lane's ``cwd`` and the declared source roots is
    the ingesting layer's job -- a parser cannot know either, and a parser
    that guessed would be the sniffing-versus-declaration collapse one tier
    over.

    :attr:`start_byte`/:attr:`end_byte` are zero-based, half-open UTF-8 byte
    offsets into the report's own ``source`` text for that file, derived from
    the mutant's ``location`` (one-based line, one-based column, end
    exclusive -- measured against the committed real fixture, not assumed).
    They are the same identity coordinates a natively-generated mutant
    carries, which is what lets an ingested and a native mutant be compared
    at all.

    :attr:`status` is the UPSTREAM status string, verbatim. The mapping onto
    assay's buckets is deliberately not done here: it depends on the lane's
    scope (a ``NoCoverage`` mutant outside the diff is not a survivor, it is
    not a candidate), and a parser has no scope.
    """

    path: str
    lineno: int
    start_byte: int
    end_byte: int
    replacement_sha256: str
    operator: str
    description: str
    status: str


@dataclass(frozen=True, kw_only=True)
class IngestedMutationReport:
    """One parsed mutation report.

    :attr:`sources` carries each measured file's own ``source`` text, keyed
    exactly as :attr:`IngestedMutant.path` is. It is not diagnostic padding:
    ``judgment.r2.lines_without_candidates`` is a statement about lines the
    foreign tool declined to mutate, and the only place assay can see which
    of a file's lines exist at all -- as the TOOL saw them -- is the text the
    tool put in its own report.
    """

    producer: MutationProducerIdentity
    #: the report's own ``projectRoot``, verbatim and absolute.
    project_root: str
    mutants: tuple[IngestedMutant, ...]
    sources: Mapping[str, str]
