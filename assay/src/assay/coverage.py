"""The coverage format registry: normalized profile, per-format parsers, and
the two guards a later package (P05) wires ahead of the four-way coverage
evaluation.

**The claim this module defends:** coverage format is declared data, not
language knowledge or artifact sniffing (DESIGN-GUIDE §5, §11; A-006, A-007).
A lane's ``judge.coverage.format`` SELECTS the parser; sniffing the artifact's
own content is only ever a cross-check on that declaration, never a second way
to pick one. Binding a parser to a language (the four existing
``coverage_gate.py`` copies' shared defect, one layer down from what this
project exists to fix) is exactly what a registry keyed by FORMAT prevents:
lcov is emitted by five languages, TypeScript alone emits three formats.

Three failure points, at three different times, never collapsed to one
`reason_code` (the ruling this package's handoff pins down):

* an unrecognised ``judge.coverage.format`` key — refused by
  :mod:`assay.config` at CONFIG-LOAD time (``ERROR``/``BAD_LANE_CONFIG``),
  cross-checked against :data:`FORMAT_REGISTRY`'s own keys rather than a
  second hardcoded list (A-068);
* a well-formed-looking artifact whose content does not match the DECLARED
  format's own signature — refused here at PARSE time
  (``ERROR``/``FORMAT_MISMATCH``, A-007);
* an artifact that does match its declared format but whose internal record
  shape is broken — refused by the matching parser module, also at PARSE
  time (``ERROR``/``UNREADABLE_ARTIFACT``).

:func:`check_empty_coverage` is the NAMED, independently callable
``EMPTY_COVERAGE`` guard A-093 requires: a well-formed artifact reporting zero
measured files is a different fact from a well-formed artifact whose files
each report zero executed lines, and only the first is vacuous
(DESIGN-GUIDE §6's "Nailing NO MEASUREMENT" table, its third and previously
unguarded row).

**P20 (A-174): "artifact absent" is explicit and bounded.**
:func:`read_coverage_artifact` no longer opens a path with ``Path.read_text``
— it reads through :func:`assay.safeio.read_bounded_file` (a nonblocking,
regular-file-only, no-follow, 16 MiB-bounded descriptor-relative read) and
hands the resulting ``bytes | None`` to :func:`parse_coverage_artifact`, the
new pure byte-level parsing seam. A genuinely MISSING artifact — the lane's
command never produced one — is ``NO_MEASUREMENT``/``EMPTY_COVERAGE``, the
same truthful "nothing to measure" as a well-formed profile reporting zero
files (:func:`check_empty_coverage`); it is not an unreadable artifact and is
never satisfied by a stale prior file, which :mod:`assay.runner`'s reservation
discipline guarantees by construction. An object that exists but is unsafe
(a symlink, FIFO, device, oversized file, or a race the reservation itself
detects) is a genuine ``ERROR``/``UNREADABLE_ARTIFACT`` — a different fact
from "there was nothing to read".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping

from . import safeio
from .coverage_parsers import cobertura, coverage_py_json, go_cover, lcov
from .coverage_parsers.model import CoverageProfile, FileCoverage
from .errors import AssayError, LaneConfigError, Outcome, ReasonCode

__all__ = [
    "FORMAT_REGISTRY",
    "MAX_COVERAGE_ARTIFACT_BYTES",
    "CoverageProfile",
    "FileCoverage",
    "FormatSpec",
    "check_empty_coverage",
    "derive_exclusion_capability",
    "load_coverage_profile",
    "parse_coverage_artifact",
    "read_coverage_artifact",
]

#: The fixed byte bound every coverage artifact read obeys (O4: a fixed
#: bound, never an ambient or elapsed-time guess). DESIGN-GUIDE §6/A-174.
MAX_COVERAGE_ARTIFACT_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True, kw_only=True)
class FormatSpec:
    """One registered format: how to recognise it and how to parse it.

    ``sniff`` answers "does this text's content match THIS format's own
    signature" — never "which format is this", which is the distinction A-007
    turns on: the registry is never asked to guess a format from content, only
    to check the declared one against it.
    """

    parse: Callable[[str], CoverageProfile]
    sniff: Callable[[str], bool]


#: Keyed by the exact string a lane's ``judge.coverage.format`` declares
#: (§12's table: "a key the parser registry knows"). Never keyed by, or
#: containing any reference to, a language — DESIGN-GUIDE §11's whole point.
FORMAT_REGISTRY: Mapping[str, FormatSpec] = MappingProxyType(
    {
        "coverage-py-json": FormatSpec(
            parse=coverage_py_json.parse, sniff=coverage_py_json.sniff
        ),
        "lcov": FormatSpec(parse=lcov.parse, sniff=lcov.sniff),
        "cobertura": FormatSpec(parse=cobertura.parse, sniff=cobertura.sniff),
        "go-cover": FormatSpec(parse=go_cover.parse, sniff=go_cover.sniff),
    }
)


def load_coverage_profile(text: str, *, declared_format: str) -> CoverageProfile:
    """Parse *text* as *declared_format*, cross-checked against the
    artifact's own sniffed signature before a single record is parsed
    (A-007).

    *declared_format* is expected to already be a :data:`FORMAT_REGISTRY` key
    — :mod:`assay.config`'s loader (A-068) refuses an unknown one at
    config-load time, well before a lane ever runs. Called directly (as a
    test, or a future caller that skips config loading, might) with a key the
    registry does not know, this raises the SAME :class:`LaneConfigError` the
    config loader would have, rather than a bare ``KeyError`` — one error
    class regardless of how the bad key arrived.

    Raises :class:`~assay.errors.AssayError` — ``ERROR``/``FORMAT_MISMATCH``
    on a signature mismatch, or whatever the matched parser raises (always
    ``ERROR``/``UNREADABLE_ARTIFACT``) on a malformed record.
    """
    spec = FORMAT_REGISTRY.get(declared_format)
    if spec is None:
        raise LaneConfigError(
            f"{declared_format!r} is not a coverage format this registry "
            f"knows; declared formats: {sorted(FORMAT_REGISTRY)}"
        )
    if not spec.sniff(text):
        raise AssayError(
            f"declared coverage format {declared_format!r}, but the "
            f"artifact's content does not match that format's own "
            f"signature. The lane's argv may have changed coverage format "
            f"without updating judge.coverage.format, or the wrong file "
            f"was named as judge.coverage.artifact.",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.FORMAT_MISMATCH,
        )
    return spec.parse(text)


def parse_coverage_artifact(raw: bytes | None, *, declared_format: str) -> CoverageProfile:
    """Parse *raw* coverage-artifact bytes as *declared_format* (see
    :func:`load_coverage_profile`), or render the artifact's ABSENCE.

    *raw* is ``None`` exactly when nothing was read — a lane's command that
    never produced its declared artifact at all (P20/A-174). That is
    ``NO_MEASUREMENT``/``EMPTY_COVERAGE``, the identical truthful cause a
    well-formed-but-empty profile already renders via
    :func:`check_empty_coverage`: in both cases there is no measurement to
    judge, only the STAGE at which that becomes apparent differs. It is
    deliberately NOT ``ERROR``/``UNREADABLE_ARTIFACT`` — that code is reserved
    for an object that exists but cannot be trusted (wrong type, undecodable,
    oversized, or a race the caller's own reservation already detected).

    Invalid UTF-8 is ``ERROR``/``UNREADABLE_ARTIFACT``; a declared-format
    sniff mismatch or malformed record raises whatever
    :func:`load_coverage_profile` raises.
    """
    if raw is None:
        raise AssayError(
            "no coverage artifact was produced by this run -- the lane's "
            "command exited without writing the declared artifact at all. "
            "This is distinct from an unreadable/malformed artifact: there "
            "is nothing here to have failed to read.",
            outcome=Outcome.NO_MEASUREMENT,
            reason_code=ReasonCode.EMPTY_COVERAGE,
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AssayError(
            f"coverage artifact is not valid UTF-8: {exc}",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.UNREADABLE_ARTIFACT,
        ) from exc
    return load_coverage_profile(text, declared_format=declared_format)


def read_coverage_artifact(
    project_root: Path, artifact: str, *, declared_format: str
) -> CoverageProfile:
    """Read the project-relative *artifact* and parse it as *declared_format*
    (see :func:`parse_coverage_artifact`).

    The thin I/O boundary around the pure byte-level parser: reads through
    :func:`assay.safeio.read_bounded_file` (nonblocking, no-follow,
    regular-file-only, bounded to :data:`MAX_COVERAGE_ARTIFACT_BYTES`) and
    calls the byte parser exactly once. No coverage owner calls
    ``Path.read_text`` (P20/A-174) -- a symlink, FIFO, device, or oversized
    object is refused by the safe read itself, never silently followed.

    This is the path a direct or canary caller uses when it has not already
    consumed the artifact through :mod:`assay.runner`'s own single-owner
    reservation (:func:`assay.runner.evaluate_r1`'s ``profile=None`` default).
    """
    raw = safeio.read_bounded_file(project_root, artifact, limit=MAX_COVERAGE_ARTIFACT_BYTES)
    return parse_coverage_artifact(raw, declared_format=declared_format)


def derive_exclusion_capability(profile: CoverageProfile) -> str:
    """Whether *profile*'s FORMAT could report exclusions at all (P21/A-183).

    ``"unavailable"`` when every measured record says ``excluded is None``
    (A-008's "the format cannot express exclusions"); ``"reported"`` when
    every record carries a real set, empty or not. Derived from unanimous
    parser evidence, never from a format NAME (a registry key is a
    declaration; the parsed records are the fact) and never from whether the
    exclusion mapping happens to be empty -- inferring "unavailable" from an
    empty mapping is precisely the collapse that made a clean Python lane
    indistinguishable from a blind Go one (A-O16).

    A MIXED profile is ``ERROR``/``UNREADABLE_ARTIFACT``, not a majority
    vote and not a default. One artifact describes one tool's output; records
    disagreeing about whether that tool tracks exclusions at all means the
    artifact cannot be read without inventing a precedence rule the lane
    never declared -- the same refusal :func:`assay.evaluate.evaluate_coverage`
    already makes for two raw keys normalising onto one repository path.

    An empty profile never reaches here: :func:`check_empty_coverage` runs
    first and refuses it with its own truthful terminal.
    """
    capabilities = {record.excluded is None for record in profile.files.values()}
    if len(capabilities) > 1:
        raise AssayError(
            "coverage artifact mixes files that report exclusions with files "
            "that cannot report them at all; one artifact is one tool's "
            "output, so this cannot be read without inventing a precedence "
            "rule the lane never declared",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.UNREADABLE_ARTIFACT,
        )
    return "unavailable" if capabilities == {True} else "reported"


def check_empty_coverage(profile: CoverageProfile) -> None:
    """Raise ``NO_MEASUREMENT``/``EMPTY_COVERAGE`` when *profile* measured
    ZERO files; otherwise return ``None`` (A-093).

    Same shape as :func:`assay.measurability.check_dirty_tree` and
    :func:`assay.measurability.check_base_is_head`: raise on the adverse
    case, return a typed value (here, nothing — the profile itself is
    already in the caller's hand) on the clear one.

    Deliberately does NOT look inside any one file's ``executed``/``missing``
    sets. A profile with files present, each reporting an empty ``executed``
    set, is a legitimate — if unusual — 0% measurement and must reach
    evaluation normally (DESIGN-GUIDE §6's own words: "a non-empty artifact
    whose executed-line sets are empty reaches evaluation"). Only the
    ABSENCE of any file entry at all means the artifact itself never measured
    anything, which is the one of DESIGN-GUIDE §6's three NO_MEASUREMENT
    causes none of the four existing `coverage_gate.py` copies guards.
    """
    if not profile.files:
        raise AssayError(
            "coverage artifact reports zero measured files — well-formed, "
            "but vacuous: no percentage computed from it means anything. "
            "This is distinct from a file reporting 0% coverage (which "
            "reaches evaluation normally) — here there is no file to have "
            "a percentage at all.",
            outcome=Outcome.NO_MEASUREMENT,
            reason_code=ReasonCode.EMPTY_COVERAGE,
        )
