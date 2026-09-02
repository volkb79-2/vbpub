"""One module per ingested mutation-report FORMAT, never per tool (B046).

The exact structural counterpart of :mod:`assay.coverage_parsers`, one rigor
tier up, and deliberately so: R1 has ingested foreign evidence since the
beginning — the lane's argv runs a coverage tool inside the private snapshot,
the tool writes an artifact at a declared path, and assay reads it through a
FORMAT-keyed registry and computes the judgment. Ingested R2 is that same
sentence with "mutation" substituted, which is why it needs no new trust
boundary and gets none.

Each sibling module here exports exactly two functions, so the registry can
hold them uniformly:

* ``sniff(text: str) -> bool`` — does *text*'s content match THIS format's own
  signature. Cheap and structural; never "which format is this" (A-007: the
  registry is never asked to guess a format from content, only to check the
  declared one against it).
* ``parse(text: str) -> assay.mutation_parsers.model.IngestedMutationReport``
  — strict parsing, raising ``ERROR``/``UNREADABLE_ARTIFACT`` on any malformed
  record.

**Why the registry lives HERE rather than in `assay.mutation`.**
:mod:`assay.config` must close ``judge.mutation.format`` against the
registry's own keys rather than a second hardcoded list (A-068's rule, which
:data:`assay.coverage.FORMAT_REGISTRY` already gets one tier over) — and
:mod:`assay.mutation` imports ``Lane`` from :mod:`assay.config`, so putting
the registry there would close a real ``config -> mutation -> config`` cycle.
This package imports only :mod:`assay.errors` and :mod:`assay.vocabulary`,
both leaves, so the graph stays a strict DAG.

**`parse` takes no producer**, unlike its coverage counterpart, and the
asymmetry is real rather than an oversight. A coverage parser needs the
declared producer because one format has several producers that DISAGREE
about what a field means (A-344/A-346, measured). Here the producer's identity
is IN the document — ``framework.name``/``framework.version`` — and it is
recorded rather than dispatched on: assay reads the format the same way
whichever Stryker wrote it, and says on the wire which one that was.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Callable, Mapping, NamedTuple

from . import mutation_report_json
from .model import IngestedMutant, IngestedMutationReport, MutationProducerIdentity

__all__ = [
    "MAX_MUTATION_REPORT_BYTES",
    "MUTATION_FORMAT_REGISTRY",
    "IngestedMutant",
    "IngestedMutationReport",
    "MutationFormatSpec",
    "MutationProducerIdentity",
    "load_mutation_report",
]

#: The fixed byte bound every ingested mutation-report read obeys, the sibling
#: of :data:`assay.coverage.MAX_COVERAGE_ARTIFACT_BYTES` that B046's own
#: non-repudiation item (v) asks for. A fixed bound, never an ambient or
#: elapsed-time guess (O4).
#:
#: Larger than coverage's 16 MiB because the documents genuinely are: a
#: mutation report embeds the full SOURCE of every measured file alongside
#: every mutant's replacement text, so a repository whose coverage artifact is
#: a few megabytes can write a mutation report several times that. The
#: committed real fixture is one small probe package and is already ~90 KB.
MAX_MUTATION_REPORT_BYTES = 64 * 1024 * 1024


class MutationFormatSpec(NamedTuple):
    """One registered ingested-mutation format: how to recognise it, and how
    to parse it."""

    parse: Callable[[str], IngestedMutationReport]
    sniff: Callable[[str], bool]


#: Keyed by the exact string a lane's ``judge.mutation.format`` declares.
#: Never keyed by, or containing any reference to, a TOOL: StrykerJS,
#: Stryker.NET and Stryker4s all emit this one document, across three language
#: ecosystems, and a registry keyed by tool would need three entries for one
#: shape (B046 §5's "precedent" clause -- this is the general shape for any R2
#: producer assay has no native engine for).
MUTATION_FORMAT_REGISTRY: Mapping[str, MutationFormatSpec] = MappingProxyType(
    {
        "mutation-report-json": MutationFormatSpec(
            parse=mutation_report_json.parse, sniff=mutation_report_json.sniff
        ),
    }
)


def load_mutation_report(
    text: str, *, declared_format: str
) -> IngestedMutationReport:
    """Parse *text* as *declared_format*, cross-checked against the report's
    own sniffed signature before a single mutant is read (A-007).

    Word for word :func:`assay.coverage.load_coverage_profile`'s contract one
    tier over, including its three-failure-points-at-three-times split: an
    unrecognised format key is :mod:`assay.config`'s refusal at CONFIG-LOAD
    time, a signature mismatch is ``ERROR``/``FORMAT_MISMATCH`` here, and a
    broken record is ``ERROR``/``UNREADABLE_ARTIFACT`` from the matched
    parser.
    """
    from ..errors import AssayError, LaneConfigError, Outcome, ReasonCode

    spec = MUTATION_FORMAT_REGISTRY.get(declared_format)
    if spec is None:
        raise LaneConfigError(
            f"{declared_format!r} is not a mutation-report format this "
            f"registry knows; declared formats: "
            f"{sorted(MUTATION_FORMAT_REGISTRY)}"
        )
    if not spec.sniff(text):
        raise AssayError(
            f"declared mutation format {declared_format!r}, but the "
            f"artifact's content does not match that format's own signature. "
            f"The lane's argv may have changed reporter without updating "
            f"judge.mutation.format, or the wrong file was named as "
            f"judge.mutation.artifact.",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.FORMAT_MISMATCH,
        )
    return spec.parse(text)
