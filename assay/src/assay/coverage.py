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
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping

from .coverage_parsers import cobertura, coverage_py_json, go_cover, lcov
from .coverage_parsers.model import CoverageProfile, FileCoverage
from .errors import AssayError, LaneConfigError, Outcome, ReasonCode

__all__ = [
    "CoverageProfile",
    "FORMAT_REGISTRY",
    "FileCoverage",
    "FormatSpec",
    "check_empty_coverage",
    "load_coverage_profile",
    "read_coverage_artifact",
]


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


def read_coverage_artifact(path: Path, *, declared_format: str) -> CoverageProfile:
    """Read *path* and parse it as *declared_format* (see
    :func:`load_coverage_profile`).

    The thin I/O boundary around the pure text parser, mirroring
    :mod:`assay.git`'s split from :mod:`assay.diff`: an unreadable or
    undecodable file is ``ERROR``/``UNREADABLE_ARTIFACT`` — the artifact
    could not be READ, which is the same class of failure as an artifact that
    reads fine but does not parse, just caught one step earlier.

    A symlink at *path* is refused the same way (P17): ``read_text`` would
    otherwise follow it silently, letting a coverage artifact this run
    never produced (potentially outside the declared project entirely)
    stand in for a real measurement. Checked here, at the one I/O boundary
    every format passes through, rather than by every caller separately.
    """
    if Path(path).is_symlink():
        raise AssayError(
            f"coverage artifact {path}: is a symlink, refused -- a "
            f"measurement must read what this run itself produced, not "
            f"whatever the link happens to point at",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.UNREADABLE_ARTIFACT,
        )
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise AssayError(
            f"coverage artifact {path}: cannot be read: {exc}",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.UNREADABLE_ARTIFACT,
        ) from exc
    except UnicodeDecodeError as exc:
        raise AssayError(
            f"coverage artifact {path}: not valid UTF-8: {exc}",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.UNREADABLE_ARTIFACT,
        ) from exc
    return load_coverage_profile(text, declared_format=declared_format)


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
