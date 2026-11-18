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
from typing import Any, Callable, Literal, Mapping, NamedTuple

from . import cargo_mutants_outcomes, mutation_report_json
from .model import (
    MAX_MUTATION_REPORT_BYTES,
    DirectoryArtifactLike,
    IngestedMutant,
    IngestedMutationReport,
    MutationProducerIdentity,
    ParseContext,
)

__all__ = [
    "MAX_MUTATION_REPORT_BYTES",
    "MUTATION_FORMAT_REGISTRY",
    "DirectoryArtifactLike",
    "IngestedMutant",
    "IngestedMutationReport",
    "MutationFormatSpec",
    "MutationProducerIdentity",
    "ParseContext",
    "load_mutation_directory",
    "load_mutation_report",
]

# `MAX_MUTATION_REPORT_BYTES` (the fixed byte bound every ingested
# mutation-report read obeys, B046 non-repudiation item (v)) is defined in
# `.model` -- see there for the rationale -- and re-exported above.


class MutationFormatSpec(NamedTuple):
    """One registered ingested-mutation format: how to recognise it, how to
    parse it, and how its statuses map onto assay's buckets.

    ``artifact_kind`` says what the lane's artifact IS: ``"file"`` (one
    document, ``parse`` set) or ``"directory"`` (``parse_directory`` set).
    ``status_buckets`` maps the parser's status strings to a bucket name and
    ``discarded_statuses`` maps those that are LISTED as discarded (not
    bucketed) to their :data:`assay.vocabulary.DISCARD_REASONS` entry.
    ``accepts_changed_diff`` is whether the format's argv may carry the
    ``{assay.changed_diff}`` placeholder (D7). ``sniff`` receives the decoded
    text for a file kind and the report bytes for a directory kind.
    """

    parse: Callable[[str], IngestedMutationReport] | None
    sniff: Callable[[Any], bool]
    artifact_kind: Literal["file", "directory"]
    parse_directory: Callable[[DirectoryArtifactLike, ParseContext], IngestedMutationReport] | None
    status_buckets: Mapping[str, str]
    discarded_statuses: Mapping[str, str]
    accepts_changed_diff: bool


#: Keyed by the exact string a lane's ``judge.mutation.format`` declares.
#: Never keyed by, or containing any reference to, a TOOL: StrykerJS,
#: Stryker.NET and Stryker4s all emit this one document, across three language
#: ecosystems, and a registry keyed by tool would need three entries for one
#: shape (B046 §5's "precedent" clause -- this is the general shape for any R2
#: producer assay has no native engine for).
MUTATION_FORMAT_REGISTRY: Mapping[str, MutationFormatSpec] = MappingProxyType(
    {
        "mutation-report-json": MutationFormatSpec(
            parse=mutation_report_json.parse,
            sniff=mutation_report_json.sniff,
            artifact_kind="file",
            parse_directory=None,
            status_buckets=MappingProxyType(mutation_report_json.STATUS_BUCKETS),
            discarded_statuses=MappingProxyType(
                mutation_report_json.DISCARDED_STATUSES
            ),
            accepts_changed_diff=False,
        ),
        "cargo-mutants-outcomes": MutationFormatSpec(
            parse=None,
            sniff=cargo_mutants_outcomes.sniff,
            artifact_kind="directory",
            parse_directory=cargo_mutants_outcomes.parse_directory,
            status_buckets=MappingProxyType(cargo_mutants_outcomes.STATUS_BUCKETS),
            discarded_statuses=MappingProxyType(
                cargo_mutants_outcomes.DISCARDED_STATUSES
            ),
            accepts_changed_diff=True,
        ),
    }
)


def _spec_of(declared_format: str, kind: str) -> MutationFormatSpec:
    from ..errors import LaneConfigError

    spec = MUTATION_FORMAT_REGISTRY.get(declared_format)
    if spec is None:
        raise LaneConfigError(
            f"{declared_format!r} is not a mutation-report format this "
            f"registry knows; declared formats: "
            f"{sorted(MUTATION_FORMAT_REGISTRY)}"
        )
    if spec.artifact_kind != kind:
        raise LaneConfigError(
            f"mutation-report format {declared_format!r} is a "
            f"{spec.artifact_kind}-kind artifact and cannot be loaded as a "
            f"{kind}"
        )
    return spec


def load_mutation_directory(
    artifact: DirectoryArtifactLike, *, declared_format: str, context: ParseContext
) -> IngestedMutationReport:
    """The directory-kind sibling of :func:`load_mutation_report`: the same
    declared-versus-sniffed check (A-007), over the artifact's report bytes."""
    from ..errors import AssayError, Outcome, ReasonCode

    spec = _spec_of(declared_format, "directory")
    assert spec.parse_directory is not None
    if not spec.sniff(artifact.report):
        raise AssayError(
            f"declared mutation format {declared_format!r}, but the "
            f"artifact's report does not match that format's own signature.",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.FORMAT_MISMATCH,
        )
    return spec.parse_directory(artifact, context)


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
    from ..errors import AssayError, Outcome, ReasonCode

    spec = _spec_of(declared_format, "file")
    assert spec.parse is not None
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
