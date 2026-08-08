"""The verdict model, and the schema that REJECTS a malformed verdict.

The claim this module exists to defend is not *"can it describe a good
verdict"* — every model can do that, including one that requires nothing. It is
**does the contract refuse a bad one?** Three separate things enforce it, and
they are deliberately not the same thing:

* :data:`SCHEMA_RESOURCE` — a hand-written JSON Schema **shipped as data**
  (A-029), so ciu, a CI system or nyxloom validates an artifact against a *file*
  without importing assay. It is never generated from the classes below: a
  schema generated from the model could only prove the model agrees with itself.
* the dataclasses here — which refuse to *construct* a verdict the schema would
  refuse to accept, so a bug is a crash in assay rather than a bad artifact in a
  consumer's hands.
* :func:`rollup` — A-023's precedence, so an outcome is derived from the claims
  rather than chosen.

Three rules are worth stating in prose because they are the ones that get
"simplified" back out:

**Absent means unknowable; empty means known-and-empty.** When no measurement
happened there is no coverage payload, not a zeroed one (A-025) — a consumer
reading ``pct`` and ignoring ``outcome`` must find nothing to read. When no lane
ever resolved there is no ``argv_declared``, not an empty one: ``[]`` asserts
*"the lane declared no argv"*, which is false. So the lane-resolved group
(``declared_rigor``, ``declared_evidence``, the three ``argv_*``,
``argv_modified`` and the two ``env_*``) is all-present or all-absent, and
:class:`Verdict` refuses a mixture.

**One entry per declared rigor level, not a flat verdict** (A-024). A lane
declaring ``["R0","R1","R2"]`` can pass R0, pass R1 and be ``INCONCLUSIVE`` on
R2; a scalar verdict flattens that into a lie. It also means *"R2 was declared
but rendered no judgement"* stays distinguishable from *"R2 was never
declared"*, which is why :class:`Verdict` refuses claims that do not cover
``declared_rigor`` exactly.

**Evidence provenance is not rigor.** ``claims`` contains only evidence assay
computed for R0-R3. Adjudicated and attested requirements use the sibling
``declared_evidence`` / ``evidence`` arrays, keyed by ``(source, key)``. The two
arrays must cover the same identities exactly, preserving the distinction
between *declared but rendered no judgement* and *never declared* without
pretending an external review is an R3 computation.

What is NOT here, on purpose: ``gate_id``, ``phase`` and ``environment``.
DESIGN-GUIDE §6 describes the artifact as a superset of nyxloom's
``GateResult``, but assay has no source for nyxloom's phase vocabulary (§4.2a
forbids inventing one) and no environment knowledge at all (§7, permanently).
nyxloom's ``_Serde.from_dict`` rejects unknown keys, so a literal superset could
never have been fed to ``GateResult.from_dict`` regardless — the consumer
cherry-picks keys. assay carries its own names.

`Outcome`, `ReasonCode`, `EXIT_CODES` and `REASON_CODES` are **imported** from
:mod:`assay.errors` and re-exported here (A-066). They are never redefined.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from importlib.resources import files
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from .config import ENFORCEMENTS, RIGOR_LEVELS, SCOPES
from .errors import EXIT_CODES, REASON_CODES, Outcome, ReasonCode

__all__ = [
    "CLAIM_SOURCES",
    "EVIDENCE_SOURCES",
    "EXIT_CODES",
    "LANE_RESOLVED_FIELDS",
    "REASON_CODES",
    "ROLLUP_PRECEDENCE",
    "SCHEMA_RESOURCE",
    "VERDICT_SCHEMA_VERSION",
    "CanaryResult",
    "Claim",
    "Coverage",
    "Evidence",
    "EvidenceDeclaration",
    "Judgment",
    "JudgmentR1",
    "JudgmentR2",
    "JudgmentR3",
    "Mutation",
    "MutantOutcome",
    "Outcome",
    "ReasonCode",
    "Verdict",
    "iso_utc",
    "load_schema",
    "rollup",
    "schema_text",
]

#: The VERDICT artifact's schema version. **Distinct** from the lane file's own
#: `schema_version` (A-065): two independently versioned contracts, and
#: conflating them would couple a lane-file format change to every emitted
#: artifact. Bumped 2 -> 3 (P16): a breaking shape change (new mandatory
#: `judgment.r1` whenever an R1 claim carries `coverage`), never a silent
#: coercion of a v2 artifact -- `Verdict.schema_version` still refuses
#: anything but the current value, so an old artifact is rejected with a
#: named diagnostic rather than silently upgraded.
VERDICT_SCHEMA_VERSION = 3

#: Where the shipped schema lives inside the installed package. Declared as
#: package data in `pyproject.toml`; without that it exists in the source tree
#: and vanishes on install, silently breaking A-029 for every consumer while
#: every in-tree test stays green.
SCHEMA_RESOURCE = "schemas/verdict.schema.json"

#: Computed rigor claims and external evidence are deliberately separate axes.
CLAIM_SOURCES: tuple[str, ...] = ("computed",)
EVIDENCE_SOURCES: tuple[str, ...] = ("adjudicated", "attested")

#: (P16 review) the two reason codes an R2 claim can only ever read off a
#: :class:`Mutation`'s own buckets — :func:`assay.mutation.judge_mutation`
#: reaches both solely on its ``mutation is not None`` branch, and
#: ``execute_command``'s baseline vocabulary contains neither. A claim
#: carrying one of them without the payload it was read from is therefore
#: unproducible, and is refused at construction rather than left for a
#: consumer to trust. See
#: :meth:`Claim._check_a_judged_status_carries_its_own_payload`.
_MUTATION_ONLY_REASON_CODES: frozenset[ReasonCode] = frozenset(
    {ReasonCode.MUTANTS_SURVIVED, ReasonCode.NO_MUTANTS}
)

#: A-023, in order. `PASS` is not a member: it is what remains when no adverse
#: status is present, which is the same statement as "PASS only when every
#: declared claim passed".
ROLLUP_PRECEDENCE: tuple[Outcome, ...] = (
    Outcome.ERROR,
    Outcome.NO_MEASUREMENT,
    Outcome.BUDGET_EXCEEDED,
    Outcome.FAIL,
    Outcome.INCONCLUSIVE,
)

#: The fields that exist if and only if a lane actually resolved. All present or
#: all absent — see the module docstring on absent-vs-empty. `scope` and
#: `enforcement` joined this group in P16: both are static `Lane` attributes,
#: known the moment a lane loads, exactly like `argv_declared`/`env_declared` —
#: there is no honest intermediate state where a lane resolved but its own
#: declared scope/enforcement are unknown.
LANE_RESOLVED_FIELDS: tuple[str, ...] = (
    "declared_rigor",
    "declared_evidence",
    "argv_declared",
    "argv_appended",
    "argv_effective",
    "argv_modified",
    "env_declared",
    "env_effective",
    "scope",
    "enforcement",
)

# Kept deliberately identical in intent to `$defs/timestamp` in the schema. The
# duplication is real; the alternative is a model that can build an artifact its
# own shipped schema rejects, which is worse.
_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})$"
)


def schema_text() -> str:
    """The shipped JSON Schema, read from the packaged FILE.

    Resolved through :mod:`importlib.resources` rather than from a path derived
    from ``__file__``, so it works identically in a source tree and in an
    installed wheel — and so a test that reads it is really reading what a
    consumer would install.
    """
    return files(__package__).joinpath(SCHEMA_RESOURCE).read_text(encoding="utf-8")


def load_schema() -> dict[str, Any]:
    """The shipped JSON Schema, parsed."""
    return json.loads(schema_text())


def iso_utc(moment: datetime) -> str:
    """Format *moment* as the artifact's timestamp spelling.

    Rejects a naive datetime rather than assuming UTC: an artifact whose times
    cannot be placed on a timeline is not evidence, and guessing the zone is the
    invention §4.2a forbids.
    """
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise ValueError(f"timestamp {moment!r} is naive; an explicit offset is required")
    return moment.isoformat()


def rollup(statuses: Iterable[Outcome]) -> Outcome:
    """The verdict outcome implied by a set of claim statuses (A-023).

    ``ERROR > NO_MEASUREMENT > BUDGET_EXCEEDED > FAIL > INCONCLUSIVE``, and
    ``PASS`` only when every claim passed. ``ERROR`` and ``NO_MEASUREMENT``
    outrank ``FAIL`` because they invalidate everything computed beneath them;
    ``BUDGET_EXCEEDED`` outranks ``FAIL`` because a truncated run's finding may
    be partial.

    Raises on an empty input. A rollup over no claims is not a pass — returning
    ``PASS`` there would be a laundering default of exactly the kind this
    project exists to remove.
    """
    present = list(statuses)
    if not present:
        raise ValueError(
            "cannot roll up an empty set of claims; a verdict over no claim is "
            "not a PASS"
        )
    for outcome in ROLLUP_PRECEDENCE:
        if outcome in present:
            return outcome
    return Outcome.PASS


def _check_reason_code(
    outcome: Outcome, reason_code: ReasonCode | None, what: str
) -> None:
    """The A-022/A-051 pairing rule, applied identically to verdict and claim."""
    if outcome is Outcome.PASS:
        if reason_code is not None:
            raise ValueError(
                f"{what}: PASS carries no reason_code (got {reason_code}); a pass "
                f"has no cause to name, and it is OMITTED rather than null"
            )
        return
    if reason_code is None:
        raise ValueError(
            f"{what}: outcome {outcome} requires a reason_code; valid: "
            f"{sorted(REASON_CODES[outcome])}"
        )
    if reason_code not in REASON_CODES[outcome]:
        raise ValueError(
            f"{what}: reason_code {reason_code} is not valid for {outcome}; "
            f"valid: {sorted(REASON_CODES[outcome])}"
        )


def _check_line_location_mapping(value: Any, what: str) -> None:
    """The shape ``missing_lines`` and (P07) ``unclassified_lines`` both
    share: ``Mapping[str, frozenset[int]]``, non-empty-string keys, always a
    non-empty ``frozenset`` of positive line numbers — "a file contributing
    none is omitted from the mapping entirely", never present with an empty
    set. Factored out so P07's new field is held to the identical
    construction-time discipline as P05's, rather than a hand-copied
    near-duplicate that could quietly drift from it (A-096's "exact same...
    discipline").
    """
    if not isinstance(value, Mapping):
        raise ValueError(f"{what} must be a mapping, got {value!r}")
    for path, lines in value.items():
        if not isinstance(path, str) or not path:
            raise ValueError(f"{what} key must be a non-empty string, got {path!r}")
        if not isinstance(lines, frozenset) or not lines:
            raise ValueError(
                f"{what}[{path!r}] must be a non-empty frozenset of line "
                f"numbers, got {lines!r} — a file contributing none is "
                f"omitted from the mapping entirely"
            )
        for line in lines:
            if isinstance(line, bool) or not isinstance(line, int) or line < 1:
                raise ValueError(
                    f"{what}[{path!r}] must contain only positive line "
                    f"numbers, got {line!r}"
                )


def _check_file_tuple(value: Any, what: str) -> None:
    """The shape ``files_missing_coverage`` and (P07)
    ``files_with_unclassified_lines`` both share: a sorted tuple of unique,
    non-empty path strings. Factored out for the same reason
    :func:`_check_line_location_mapping` is.
    """
    if not isinstance(value, tuple):
        raise ValueError(f"{what} must be a tuple, got {value!r}")
    for path in value:
        if not isinstance(path, str) or not path:
            raise ValueError(f"{what} entries must be non-empty strings, got {path!r}")
    if len(set(value)) != len(value):
        raise ValueError(f"{what} contains a duplicate: {list(value)}")
    if list(value) != sorted(value):
        raise ValueError(f"{what} must be sorted, got {list(value)}")


@dataclass(frozen=True, kw_only=True)
class Coverage:
    """The R1 claim payload: changed-line coverage, and why its denominator is
    what it is.

    ``considered`` is srdm's fix and the reason a legitimate 0/0 pass is not
    textually identical to a broken measurement (A-026): *"3 changed file(s)
    under the roots, none contributing executable non-test lines (0/0)"* is a
    different sentence from *"0/0 covered (100.0%)"*. Identical strings for the
    two cases is what started this whole project.

    ``missing_lines`` and ``files_missing_coverage`` are P05's own additive
    pair (A-096): the R1 claim's totals say WHETHER the change passed;
    these say WHERE it did not. Both are ALWAYS present — possibly empty,
    never conditionally omitted — the same "absent means unknowable, empty
    means known-and-empty" discipline A-025 already applies to the coverage
    block as a whole, one level down inside it.

    ``unclassified_lines`` and ``files_with_unclassified_lines`` are P07's
    own additive THIRD pair (A-096), following the identical discipline: a
    changed line whose enclosing multi-line statement could not be
    confidently attributed — overlapping/malformed spans, or a genuinely
    untracked enclosing statement — renders ``FAIL``/``UNCLASSIFIED_LINES``
    (A-100) and names exactly where. Both default to empty so a producer
    that has not been updated to pass them through (P07 does not touch
    :mod:`assay.runner`, which is outside its ``scope.touch``) continues to
    build a valid, schema-conformant ``Coverage`` — always present in the
    OUTPUT (:meth:`to_dict` always emits both keys), never conditionally
    omitted, exactly like ``missing_lines``/``files_missing_coverage``.
    """

    covered: int
    changed_executable: int
    pct: float
    #: changed files under the source roots that were considered at all
    considered: int
    #: changed, executable, non-excluded lines that were not executed, keyed
    #: by path — same shape as :attr:`assay.diff.AddedLines.by_file`: a file
    #: contributing none is ABSENT, never present with an empty frozenset.
    missing_lines: Mapping[str, frozenset[int]]
    #: changed files under the source roots contributing executable lines
    #: with no entry at all in the coverage artifact, sorted.
    files_missing_coverage: tuple[str, ...]
    #: (P07) changed lines whose multi-line-statement attribution could not
    #: confidently resolve them — same shape as :attr:`missing_lines`: a
    #: file contributing none is ABSENT, never present with an empty
    #: frozenset. Non-empty renders the claim ``FAIL``/``UNCLASSIFIED_LINES``
    #: (A-100).
    unclassified_lines: Mapping[str, frozenset[int]] = MappingProxyType({})
    #: (P07) paths appearing in :attr:`unclassified_lines`, sorted — the
    #: same shape :attr:`files_missing_coverage` already established.
    files_with_unclassified_lines: tuple[str, ...] = ()
    #: (P16) changed, considered lines the coverage artifact classifies
    #: EXCLUDED (``pragma: no cover`` and its format-specific equivalents),
    #: keyed exactly like :attr:`missing_lines` — a file contributing none
    #: is ABSENT, never present with an empty frozenset. Always present
    #: (possibly empty), the same "empty means known-and-empty" discipline
    #: A-025 already applies one level up: an artifact that hides which
    #: lines were excluded is exactly sol finding 2's "an independent
    #: consumer cannot re-derive whether an R1 status was correct" gap —
    #: without this an ``allow_excluded=False`` lane's ``FAIL``/
    #: ``EXCLUDED_LINES`` claim could not be independently re-checked at
    #: all, only trusted.
    excluded_lines: Mapping[str, frozenset[int]] = MappingProxyType({})
    #: (P16) paths appearing in :attr:`excluded_lines`, sorted — the same
    #: "always present, possibly empty, sorted" shape
    #: :attr:`files_missing_coverage`/:attr:`files_with_unclassified_lines`
    #: already established.
    files_with_excluded_lines: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("covered", "changed_executable", "considered"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"coverage.{name} must be an integer, got {value!r}")
            if value < 0:
                raise ValueError(f"coverage.{name} must not be negative, got {value}")
        if isinstance(self.pct, bool) or not isinstance(self.pct, (int, float)):
            raise ValueError(f"coverage.pct must be a number, got {self.pct!r}")
        if not 0.0 <= float(self.pct) <= 100.0:
            raise ValueError(
                f"coverage.pct must be a percentage between 0 and 100, got {self.pct}"
            )
        if self.covered > self.changed_executable:
            raise ValueError(
                f"coverage.covered ({self.covered}) exceeds changed_executable "
                f"({self.changed_executable})"
            )
        _check_line_location_mapping(self.missing_lines, "coverage.missing_lines")
        _check_file_tuple(self.files_missing_coverage, "coverage.files_missing_coverage")
        _check_line_location_mapping(
            self.unclassified_lines, "coverage.unclassified_lines"
        )
        _check_file_tuple(
            self.files_with_unclassified_lines,
            "coverage.files_with_unclassified_lines",
        )
        _check_line_location_mapping(self.excluded_lines, "coverage.excluded_lines")
        _check_file_tuple(
            self.files_with_excluded_lines, "coverage.files_with_excluded_lines"
        )

        # P16 (sol finding 2): the arithmetic no earlier package enforced —
        # a `Coverage` reporting `PASS` at 0% simply disagreed with its own
        # `covered`/`changed_executable`, and construction accepted it.
        # `pct` is DERIVED, never independently supplied.
        expected_pct = (
            100.0
            if self.changed_executable == 0
            else 100.0 * self.covered / self.changed_executable
        )
        if abs(float(self.pct) - expected_pct) > 1e-9:
            raise ValueError(
                f"coverage.pct {self.pct} does not agree with "
                f"covered/changed_executable "
                f"({self.covered}/{self.changed_executable} = {expected_pct}); "
                f"pct is derived from those two fields, never independently "
                f"supplied"
            )
        total_missing = sum(len(lines) for lines in self.missing_lines.values())
        if total_missing != self.changed_executable - self.covered:
            raise ValueError(
                f"coverage.missing_lines names {total_missing} line(s) total, "
                f"but changed_executable - covered = "
                f"{self.changed_executable - self.covered}; the per-file "
                f"detail must sum to the summary"
            )
        self._check_buckets_pairwise_disjoint()
        self._check_summaries_name_their_own_detail()

    def _check_summaries_name_their_own_detail(self) -> None:
        """Each ``files_*`` summary agrees with the line-level mapping it
        summarises (P16's own work item 3: "missing/excluded/unclassified
        identities agree with their summary fields").

        Two different relations, because the two kinds of summary mean two
        different things:

        * :attr:`files_with_unclassified_lines` and
          :attr:`files_with_excluded_lines` are documented as *"paths
          appearing in* [the mapping]*, sorted"* — an EQUALITY, and
          :mod:`assay.evaluate` builds both by literally sorting the
          mapping it just filled.
        * :attr:`files_missing_coverage` is NOT the keys of
          :attr:`missing_lines`: it names only the considered files with no
          entry at all in the coverage artifact, whose changed lines are
          then all recorded as missing. Every such file therefore appears in
          :attr:`missing_lines` — a CONTAINMENT — while a file that does
          have an artifact entry can contribute missing lines without
          belonging here.

        Without this, a summary could name a file the detail never mentions,
        or stay empty while the detail names several: a consumer reading
        "which files have a problem" (the whole reason A-096 added these
        pairs) would be answered by a field nothing bound to the evidence.
        """
        for mapping, mapping_name, summary, summary_name in (
            (
                self.unclassified_lines,
                "unclassified_lines",
                self.files_with_unclassified_lines,
                "files_with_unclassified_lines",
            ),
            (
                self.excluded_lines,
                "excluded_lines",
                self.files_with_excluded_lines,
                "files_with_excluded_lines",
            ),
        ):
            expected = tuple(sorted(mapping))
            if summary != expected:
                raise ValueError(
                    f"coverage.{summary_name} {list(summary)} does not name "
                    f"exactly the paths in coverage.{mapping_name} "
                    f"{list(expected)}; the summary is derived from the "
                    f"detail, never supplied independently of it"
                )
        stray = sorted(set(self.files_missing_coverage) - set(self.missing_lines))
        if stray:
            raise ValueError(
                f"coverage.files_missing_coverage names {stray}, which "
                f"contribute no line to coverage.missing_lines; a file with "
                f"no coverage-artifact entry has every changed line recorded "
                f"as missing"
            )

    def _check_buckets_pairwise_disjoint(self) -> None:
        """A changed line has exactly one classification (P15's own
        ``FileCoverage`` invariant, restated one level up at the artifact):
        it cannot be simultaneously ``missing``, ``excluded`` and
        ``unclassified`` for the same file. Real producer output already
        satisfies this by construction (:mod:`assay.evaluate` derives all
        three from one already-disjoint partition); this is the defensive,
        construction-time check that makes a HAND-BUILT contradictory
        artifact impossible to instantiate, matching A-135's binding
        instruction for this package's own contradictory fixtures.
        """
        pairs = (
            ("missing_lines", self.missing_lines, "excluded_lines", self.excluded_lines),
            ("missing_lines", self.missing_lines, "unclassified_lines", self.unclassified_lines),
            ("excluded_lines", self.excluded_lines, "unclassified_lines", self.unclassified_lines),
        )
        for left_name, left, right_name, right in pairs:
            for path in set(left) & set(right):
                overlap = left[path] & right[path]
                if overlap:
                    raise ValueError(
                        f"coverage[{path!r}]: {left_name} and {right_name} "
                        f"both name line(s) {sorted(overlap)} — a changed "
                        f"line has exactly one classification"
                    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "covered": self.covered,
            "changed_executable": self.changed_executable,
            "pct": float(self.pct),
            "considered": self.considered,
            "missing_lines": {
                path: sorted(lines)
                for path, lines in sorted(self.missing_lines.items())
            },
            "files_missing_coverage": list(self.files_missing_coverage),
            "unclassified_lines": {
                path: sorted(lines)
                for path, lines in sorted(self.unclassified_lines.items())
            },
            "files_with_unclassified_lines": list(self.files_with_unclassified_lines),
            "excluded_lines": {
                path: sorted(lines)
                for path, lines in sorted(self.excluded_lines.items())
            },
            "files_with_excluded_lines": list(self.files_with_excluded_lines),
        }


@dataclass(frozen=True, kw_only=True)
class CanaryResult:
    """The R3 claim payload (P09, A-092/A-108): the evidence a cause-
    sensitive canary produces, carried separately from the JUDGEMENT
    :mod:`assay.canary` derives from it — the identical split
    :class:`Coverage` already keeps from :class:`Claim` (this is its exact
    construction-time-validation template, restated for canary's own four
    named fields, O2).

    A known-good CONTROL is run through the real pipeline and MUST have
    produced :attr:`control_outcome`; a deliberately minimal, known-bad
    TRANSFORM of the same input is then run the same way, producing
    :attr:`transformed_outcome` — unless the transform itself was malformed
    or a no-op (O3), in which case :attr:`transformed_outcome` (and
    :attr:`observed_reason_code`) are ``None``: the transformed half was
    never genuinely run, so there is nothing to report an outcome FOR.
    :attr:`expected_reason_code` is the SPECIFIC reason the mechanism is
    supposed to produce (never merely "any failure", O1's own negative);
    :attr:`observed_reason_code` is what the real pipeline actually
    rendered. :mod:`assay.canary`'s ``judge_canary`` compares the two.
    """

    #: A short, stable identity for the transform attempted (e.g.
    #: ``"import-break"``/``"uncovered-line"``) — NOT restricted to a closed
    #: set here, because a malformed/unrecognised mechanism name must still
    #: be representable (O3's own "malformed transform" case names the
    #: mechanism it could not resolve).
    mechanism: str
    #: Human-readable account of the concrete transform attempted, or of WHY
    #: nothing could be built (a malformed mechanism, or a no-op) — from the
    #: adapter's own ``inject_*``'s ``(text, description)`` return, never
    #: parsed back apart by any caller.
    description: str
    control_outcome: Outcome
    #: ``None`` exactly when the transformed half was never genuinely run
    #: (O3's two INCONCLUSIVE causes: a malformed/unrecognised mechanism, or
    #: a transform that changed nothing to judge).
    transformed_outcome: Outcome | None = None
    #: The FAIL-shaped reason the mechanism is supposed to produce, or
    #: ``None`` when no expectation could be derived (an unrecognised
    #: mechanism).
    expected_reason_code: ReasonCode | None = None
    #: What the real pipeline actually rendered for the transformed half, or
    #: ``None`` when it was never run, or when it PASSED (a PASS has no
    #: cause to name, mirroring :class:`Claim`'s own A-051 pairing rule).
    observed_reason_code: ReasonCode | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mechanism, str) or not self.mechanism:
            raise ValueError(
                f"canary.mechanism must be a non-empty string, got "
                f"{self.mechanism!r}"
            )
        if not isinstance(self.description, str) or not self.description:
            raise ValueError(
                f"canary.description must be a non-empty string, got "
                f"{self.description!r}"
            )
        if not isinstance(self.control_outcome, Outcome):
            raise ValueError(
                f"canary.control_outcome must be an Outcome, got "
                f"{self.control_outcome!r}"
            )
        if self.transformed_outcome is not None and not isinstance(
            self.transformed_outcome, Outcome
        ):
            raise ValueError(
                f"canary.transformed_outcome must be an Outcome or None, got "
                f"{self.transformed_outcome!r}"
            )
        if self.expected_reason_code is not None:
            if self.expected_reason_code not in REASON_CODES[Outcome.FAIL]:
                raise ValueError(
                    f"canary.expected_reason_code must be a FAIL reason code "
                    f"or None (a canary always expects a SPECIFIC failure), "
                    f"got {self.expected_reason_code!r}"
                )
        if self.transformed_outcome is None:
            if self.observed_reason_code is not None:
                raise ValueError(
                    "canary.observed_reason_code requires a transformed_outcome "
                    "— the transformed half was never run"
                )
        elif self.transformed_outcome is Outcome.PASS:
            if self.observed_reason_code is not None:
                raise ValueError(
                    "canary.observed_reason_code must be omitted when "
                    "transformed_outcome is PASS — a pass has no cause to name"
                )
        else:
            if self.observed_reason_code is None:
                raise ValueError(
                    f"canary.observed_reason_code is required when "
                    f"transformed_outcome is {self.transformed_outcome} — "
                    f"every non-PASS outcome names its cause"
                )
            if self.observed_reason_code not in REASON_CODES[self.transformed_outcome]:
                raise ValueError(
                    f"canary.observed_reason_code {self.observed_reason_code} "
                    f"is not valid for transformed_outcome "
                    f"{self.transformed_outcome}; valid: "
                    f"{sorted(REASON_CODES[self.transformed_outcome])}"
                )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "mechanism": self.mechanism,
            "description": self.description,
            "control_outcome": self.control_outcome.value,
        }
        # Absent means never run/unknowable; never null (mirrors A-025/A-051).
        if self.transformed_outcome is not None:
            payload["transformed_outcome"] = self.transformed_outcome.value
        if self.expected_reason_code is not None:
            payload["expected_reason_code"] = self.expected_reason_code.value
        if self.observed_reason_code is not None:
            payload["observed_reason_code"] = self.observed_reason_code.value
        return payload


@dataclass(frozen=True, kw_only=True)
class MutantOutcome:
    """One mutant's identity, projected for the R2 artifact (A-116): the
    lightweight subset of :class:`~assay.mutation.Mutant`'s own identity a
    reader needs to find and fix a surviving/crashed/budget-stopped site,
    plus the external *path* context :class:`~assay.mutation.Mutant` itself
    has no field for (one ``generate_mutants`` call is scoped to a single
    file; a verdict spans possibly many).

    Deliberately WITHOUT ``mutated_text`` — a verdict carrying many
    survivors would otherwise embed a full replacement file per entry,
    which bloats the artifact for no diagnostic gain :attr:`operator` +
    :attr:`description` + :attr:`lineno` do not already provide.

    :attr:`operator` is a plain non-empty ``str`` here, NOT validated
    against :data:`~assay.mutation.MUTATION_OPERATORS` — the identical
    choice :class:`CanaryResult`'s own ``mechanism`` field already makes
    (A-108), and for the same two reasons: this module must stay importable
    without importing :mod:`assay.mutation`'s own execution orchestration
    (which imports :class:`Mutation`/:class:`MutantOutcome` FROM here — a
    closed-set import the other direction would be circular), and in
    practice this field is always built from an already-validated
    :attr:`assay.mutation.Mutant.operator`, so re-closing the vocabulary
    here would only be able to catch a bug that :class:`~assay.mutation.
    Mutant`'s own construction already prevents.
    """

    path: str
    lineno: int
    operator: str
    description: str

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path:
            raise ValueError(
                f"MutantOutcome.path must be a non-empty string, got {self.path!r}"
            )
        if isinstance(self.lineno, bool) or not isinstance(self.lineno, int):
            raise ValueError(
                f"MutantOutcome.lineno must be an integer, got {self.lineno!r}"
            )
        if self.lineno < 1:
            raise ValueError(f"MutantOutcome.lineno must be >= 1, got {self.lineno}")
        if not isinstance(self.operator, str) or not self.operator:
            raise ValueError(
                f"MutantOutcome.operator must be a non-empty string, got "
                f"{self.operator!r}"
            )
        if not isinstance(self.description, str) or not self.description:
            raise ValueError(
                f"MutantOutcome.description must be a non-empty string, got "
                f"{self.description!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "lineno": self.lineno,
            "operator": self.operator,
            "description": self.description,
        }


def _mutant_outcome_sort_key(item: MutantOutcome) -> tuple[str, int, str, str]:
    return (item.path, item.lineno, item.operator, item.description)


def _check_mutant_outcome_tuple(value: Any, what: str) -> None:
    """Every one of :class:`Mutation`'s three non-killed buckets shares this
    shape: a tuple of :class:`MutantOutcome`, sorted by stable identity
    (``path``, ``lineno``, ``operator``, ``description``) — never by
    whichever mutant's subprocess happened to finish first (O3). Ties are
    legal (a 3+-operand boolean chain, A-115, can project two distinct
    mutants onto the identical ``MutantOutcome``), so this checks sortedness
    under that key, not uniqueness.
    """
    if not isinstance(value, tuple):
        raise ValueError(f"{what} must be a tuple, got {value!r}")
    for item in value:
        if not isinstance(item, MutantOutcome):
            raise ValueError(f"{what} entries must be MutantOutcome, got {item!r}")
    if list(value) != sorted(value, key=_mutant_outcome_sort_key):
        raise ValueError(
            f"{what} must be sorted by (path, lineno, operator, description); "
            f"got {[_mutant_outcome_sort_key(item) for item in value]}"
        )


@dataclass(frozen=True, kw_only=True)
class Mutation:
    """The R2 claim payload (P12, A-116): baseline-gated mutation execution
    against changed-line mutants, isolated per mutant, bounded by a
    caller-declared ``jobs`` executor (A-082).

    FOUR buckets, not three, matching O3/O4's own explicit four-way
    enumeration (killed / survived / crashed / budget-exceeded) — nyxloom's
    own ``MutationResult`` collapses crashed and budget-exceeded into
    "killed" (confirmed reading ``mutation_gate.py`` directly, A-122), which
    is exactly the ambiguity this project's own oracles exist to catch, so
    it is not ported. ``killed`` is a bare count (a killed mutant needs no
    further diagnosis — the suite already caught it); the other three carry
    the actual :class:`MutantOutcome` identities, because THOSE are what a
    reader needs to act on.
    """

    total: int
    killed: int
    survived: tuple[MutantOutcome, ...] = ()
    crashed: tuple[MutantOutcome, ...] = ()
    budget_exceeded: tuple[MutantOutcome, ...] = ()

    def __post_init__(self) -> None:
        for name in ("total", "killed"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"mutation.{name} must be an integer, got {value!r}")
            if value < 0:
                raise ValueError(f"mutation.{name} must not be negative, got {value}")
        _check_mutant_outcome_tuple(self.survived, "mutation.survived")
        _check_mutant_outcome_tuple(self.crashed, "mutation.crashed")
        _check_mutant_outcome_tuple(self.budget_exceeded, "mutation.budget_exceeded")
        expected_total = (
            self.killed + len(self.survived) + len(self.crashed) + len(self.budget_exceeded)
        )
        if self.total != expected_total:
            raise ValueError(
                f"mutation.total ({self.total}) must equal killed + "
                f"len(survived) + len(crashed) + len(budget_exceeded) "
                f"({expected_total})"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "killed": self.killed,
            "survived": [item.to_dict() for item in self.survived],
            "crashed": [item.to_dict() for item in self.crashed],
            "budget_exceeded": [item.to_dict() for item in self.budget_exceeded],
        }


@dataclass(frozen=True, kw_only=True)
class JudgmentR1:
    """The effective R1 policy (P16, sol finding 2): the resolved
    ``language``/``source_roots``/coverage format+artifact spelling the run
    used, the ``fail_under`` floor and ``allow_excluded`` choice that
    decided the R1 claim's ``PASS``/``FAIL``, and the FULL resolved
    comparison commit the diff was measured against — never the lane's own
    possibly-symbolic ``base`` ref. Without this, an independent consumer
    has a percentage but not the policy that judged it: "schema v2 does not
    record fail_under/allow_excluded, so an independent consumer cannot even
    in principle re-derive whether an R1 status was correct from the payload
    alone."
    """

    language: str
    source_roots: tuple[str, ...]
    coverage_format: str
    coverage_artifact: str
    fail_under: float
    allow_excluded: bool
    #: the full resolved comparison commit, never a symbolic ref
    base: str

    def __post_init__(self) -> None:
        _check_nonempty(self.language, "judgment.r1.language")
        if not isinstance(self.source_roots, tuple) or not self.source_roots:
            raise ValueError(
                f"judgment.r1.source_roots must be a non-empty tuple, got "
                f"{self.source_roots!r}"
            )
        for root in self.source_roots:
            _check_nonempty(root, "judgment.r1.source_roots entry")
        _check_nonempty(self.coverage_format, "judgment.r1.coverage_format")
        _check_nonempty(self.coverage_artifact, "judgment.r1.coverage_artifact")
        if isinstance(self.fail_under, bool) or not isinstance(
            self.fail_under, (int, float)
        ):
            raise ValueError(
                f"judgment.r1.fail_under must be a number, got {self.fail_under!r}"
            )
        if not 0.0 <= float(self.fail_under) <= 100.0:
            raise ValueError(
                f"judgment.r1.fail_under must be a percentage between 0 and "
                f"100, got {self.fail_under}"
            )
        if not isinstance(self.allow_excluded, bool):
            raise ValueError(
                f"judgment.r1.allow_excluded must be a boolean, got "
                f"{self.allow_excluded!r}"
            )
        _check_nonempty(self.base, "judgment.r1.base")

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "source_roots": list(self.source_roots),
            "coverage_format": self.coverage_format,
            "coverage_artifact": self.coverage_artifact,
            "fail_under": float(self.fail_under),
            "allow_excluded": self.allow_excluded,
            "base": self.base,
        }


@dataclass(frozen=True, kw_only=True)
class JudgmentR2:
    """Populated by ``assay run``'s own R2 CLI wiring (P18) whenever the
    rendered R2 claim carries a ``mutation`` payload. The mutation policy
    that decided which changed-line sites were candidates and how many ran
    concurrently: ``jobs`` and the ORDERED, closed ``operators`` list the
    lane declared — never a machine-derived job count (A-082/A-122).

    Unlike R1, an independent consumer can already re-derive the R2 claim's
    *status* from :class:`Mutation`'s own bucket fields alone
    (:func:`assay.mutation.judge_mutation`'s mapping needs no external
    policy input) — this record exists so a consumer can also confirm which
    POLICY produced those buckets. P19/A-148 gave this a construction-time
    correspondence check one level up
    (:meth:`Verdict._check_judgment_matches_claims`): present if and only
    if the R2 claim carries a ``mutation`` payload, and every operator a
    survived/crashed/budget-exceeded entry names must be one
    :attr:`operators` actually declared.
    """

    jobs: int
    operators: tuple[str, ...]

    def __post_init__(self) -> None:
        if isinstance(self.jobs, bool) or not isinstance(self.jobs, int):
            raise ValueError(f"judgment.r2.jobs must be an integer, got {self.jobs!r}")
        if self.jobs < 1:
            raise ValueError(f"judgment.r2.jobs must be >= 1, got {self.jobs}")
        if not isinstance(self.operators, tuple) or not self.operators:
            raise ValueError(
                f"judgment.r2.operators must be a non-empty tuple, got "
                f"{self.operators!r}"
            )
        for operator in self.operators:
            _check_nonempty(operator, "judgment.r2.operators entry")
        if len(set(self.operators)) != len(self.operators):
            raise ValueError(
                f"judgment.r2.operators contains a duplicate: "
                f"{list(self.operators)}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {"jobs": self.jobs, "operators": list(self.operators)}


@dataclass(frozen=True, kw_only=True)
class JudgmentR3:
    """Populated by ``assay run``'s own isolated R3 CLI wiring (P19)
    whenever the rendered R3 claim carries a ``canary`` payload. The canary
    declaration that produced it: the ``mechanism`` name and the
    project-relative ``target`` path, so a consumer can tell WHICH declared
    canary a rendered R3 claim answers for.

    :func:`assay.canary.judge_canary` already re-derives R3 *status* from
    :class:`CanaryResult`'s own fields alone, the identical reason
    :class:`JudgmentR2` gives for its own case — this record exists so a
    consumer can also confirm which declared canary produced that result.
    P19/A-148 gave this a construction-time correspondence check one level
    up (:meth:`Verdict._check_judgment_matches_claims`): present if and
    only if the R3 claim carries a ``canary`` payload, and that payload's
    own ``mechanism`` must match :attr:`mechanism` exactly.
    """

    mechanism: str
    target: str

    def __post_init__(self) -> None:
        _check_nonempty(self.mechanism, "judgment.r3.mechanism")
        _check_nonempty(self.target, "judgment.r3.target")

    def to_dict(self) -> dict[str, Any]:
        return {"mechanism": self.mechanism, "target": self.target}


@dataclass(frozen=True, kw_only=True)
class Judgment:
    """The resolved judge policy for whichever declared rigor levels
    actually rendered a real computed judgment (P16). ``r2``/``r3`` are
    RESERVED, closed shapes a future package populates additively — see
    their own docstrings for why only ``r1`` carries a construction-time
    correspondence check against :class:`Claim` today
    (:meth:`Verdict._check_judgment_matches_claims`).
    """

    r1: JudgmentR1 | None = None
    r2: JudgmentR2 | None = None
    r3: JudgmentR3 | None = None

    def __post_init__(self) -> None:
        if self.r1 is None and self.r2 is None and self.r3 is None:
            raise ValueError(
                "judgment declares none of r1/r2/r3 -- an empty judgment "
                "records no policy and should be omitted (None) instead"
            )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if self.r1 is not None:
            payload["r1"] = self.r1.to_dict()
        if self.r2 is not None:
            payload["r2"] = self.r2.to_dict()
        if self.r3 is not None:
            payload["r3"] = self.r3.to_dict()
        return payload


@dataclass(frozen=True, kw_only=True)
class Claim:
    """One COMPUTED declared rigor level's evidence (A-024)."""

    rigor: str
    source: str
    status: Outcome
    verified_by_assay: bool
    reason_code: ReasonCode | None = None
    coverage: Coverage | None = None
    canary: CanaryResult | None = None
    mutation: Mutation | None = None

    def __post_init__(self) -> None:
        if self.rigor not in RIGOR_LEVELS:
            raise ValueError(
                f"claim rigor must be one of {list(RIGOR_LEVELS)}, got {self.rigor!r}"
            )
        if self.source not in CLAIM_SOURCES:
            raise ValueError(
                f"claim[{self.rigor}] source must be one of {list(CLAIM_SOURCES)}, "
                f"got {self.source!r}"
            )
        if not isinstance(self.status, Outcome):
            raise ValueError(
                f"claim[{self.rigor}] status must be an Outcome, got {self.status!r}"
            )
        if not isinstance(self.verified_by_assay, bool):
            raise ValueError(
                f"claim[{self.rigor}] verified_by_assay must be a boolean, got "
                f"{self.verified_by_assay!r}"
            )
        _check_reason_code(self.status, self.reason_code, f"claim[{self.rigor}]")
        if self.reason_code in {
            ReasonCode.MISSING_ATTESTATION,
            ReasonCode.STALE_ATTESTATION,
        }:
            raise ValueError(
                f"claim[{self.rigor}]: {self.reason_code} belongs to external "
                f"attested evidence, not a computed rigor claim"
            )

        if not self.verified_by_assay:
            raise ValueError(
                f"claim[{self.rigor}]: a computed claim must carry "
                f"verified_by_assay=True; external evidence belongs in evidence[]"
            )

        if self.coverage is not None:
            if self.rigor != "R1":
                raise ValueError(
                    f"claim[{self.rigor}]: a coverage payload belongs to the R1 "
                    f"claim; R1 is the level that declares a coverage floor"
                )
            # A-025 at claim level.
            if self.status is Outcome.NO_MEASUREMENT:
                raise ValueError(
                    f"claim[{self.rigor}]: NO_MEASUREMENT carries no coverage "
                    f"payload at all — omitted, not zeroed. A consumer that reads "
                    f"pct and ignores status must find nothing to read"
                )

        if self.canary is not None:
            # A-108: the exact R1/coverage template above, restated for R3.
            if self.rigor != "R3":
                raise ValueError(
                    f"claim[{self.rigor}]: a canary payload belongs to the R3 "
                    f"claim; R3 is the level that declares a canary"
                )
            if self.status is Outcome.NO_MEASUREMENT:
                raise ValueError(
                    f"claim[{self.rigor}]: NO_MEASUREMENT carries no canary "
                    f"payload at all — omitted, not zeroed, the same discipline "
                    f"A-025 applies to coverage"
                )

        if self.mutation is not None:
            # A-116: the exact R1/coverage template above, restated for R2.
            # mutation's PRESENCE is baseline-conditional -- None means
            # mutation testing never started (the baseline itself never
            # resolved to PASS); present means it did, even if every
            # attempted mutant crashed.
            if self.rigor != "R2":
                raise ValueError(
                    f"claim[{self.rigor}]: a mutation payload belongs to the R2 "
                    f"claim; R2 is the level that declares mutation testing"
                )
            if self.status is Outcome.NO_MEASUREMENT:
                raise ValueError(
                    f"claim[{self.rigor}]: NO_MEASUREMENT carries no mutation "
                    f"payload at all — omitted, not zeroed, the same discipline "
                    f"A-025 applies to coverage"
                )

        self._check_a_judged_status_carries_its_own_payload()

    def _check_a_judged_status_carries_its_own_payload(self) -> None:
        """The converse of the three ``NO_MEASUREMENT`` rules above (P16
        review): those forbid a payload where none was measured; this
        forbids a *judged status* where no payload was measured.

        Without it, ``assay verify``'s R1/R2/R3 re-derivation is trivially
        evaded — not by a contradictory payload (sol finding 2's three
        artifacts) but by DELETING the payload. Each re-derivation function
        has nothing to judge and returns; the top-level rollup still agrees;
        the artifact is accepted. A ``PASS`` claim backed by no evidence at
        all is the strongest form of exactly the lie P16 exists to catch.

        Each rule states only what its own producer can prove:

        * R1 — :func:`assay.evaluate.evaluate_coverage` returns ``PASS`` or
          ``FAIL`` and *always* with a :class:`Coverage`; the three
          ``NO_MEASUREMENT`` causes are guarded before it is ever called
          (A-090) and carry no payload by the rule above.
        * R2 — :func:`assay.mutation.judge_mutation` reaches ``PASS`` only
          on the ``mutation is not None`` branch; ``None`` means the
          baseline never resolved to ``PASS`` (A-116) and the claim reuses
          that non-``PASS`` baseline's own outcome verbatim. Every other
          status stays representable without a payload, because a failed
          baseline is exactly how they arise.
        * R3 — :func:`assay.canary.judge_canary` returns ``PASS``/``FAIL``/
          ``INCONCLUSIVE``, and :func:`assay.canary.build_canary_claim`
          attaches the :class:`CanaryResult` it judged on every one of
          them. ``ERROR``/``BUDGET_EXCEEDED`` stay representable without
          one: those describe the canary machinery failing to produce a
          result at all, not a judgement of one.
        """
        if self.rigor == "R1" and self.coverage is None:
            if self.status in {Outcome.PASS, Outcome.FAIL}:
                raise ValueError(
                    f"claim[R1]: {self.status.value} without a coverage "
                    f"payload -- a changed-line judgement that names no "
                    f"coverage cannot be re-derived by anyone but its own "
                    f"producer"
                )
        if self.rigor == "R2" and self.mutation is None:
            if self.status is Outcome.PASS:
                raise ValueError(
                    "claim[R2]: PASS without a mutation payload -- an "
                    "absent payload means mutation testing never began "
                    "(the baseline never passed), which cannot itself pass"
                )
            if self.reason_code in _MUTATION_ONLY_REASON_CODES:
                raise ValueError(
                    f"claim[R2]: {self.reason_code.value} without a mutation "
                    f"payload -- that reason code can only be read off the "
                    f"mutation buckets, so a baseline that never got as far "
                    f"as running mutants cannot produce it"
                )
        if self.rigor == "R3" and self.canary is None:
            if self.status in {Outcome.PASS, Outcome.FAIL, Outcome.INCONCLUSIVE}:
                raise ValueError(
                    f"claim[R3]: {self.status.value} without a canary "
                    f"payload -- that status is a judgement OF a canary "
                    f"result, so the result it judged must be recorded"
                )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "rigor": self.rigor,
            "source": self.source,
            "status": self.status.value,
            "verified_by_assay": self.verified_by_assay,
        }
        # Omitted, never null (A-051).
        if self.reason_code is not None:
            payload["reason_code"] = self.reason_code.value
        if self.coverage is not None:
            payload["coverage"] = self.coverage.to_dict()
        if self.canary is not None:
            payload["canary"] = self.canary.to_dict()
        if self.mutation is not None:
            payload["mutation"] = self.mutation.to_dict()
        return payload


def _check_nonempty(value: str, what: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{what} must be a non-empty string, got {value!r}")


@dataclass(frozen=True, kw_only=True)
class EvidenceDeclaration:
    """One externally sourced requirement, identified independently of rigor."""

    source: str
    key: str

    def __post_init__(self) -> None:
        if self.source not in EVIDENCE_SOURCES:
            raise ValueError(
                f"evidence declaration source must be one of "
                f"{list(EVIDENCE_SOURCES)}, got {self.source!r}"
            )
        _check_nonempty(self.key, "evidence declaration key")

    @property
    def identity(self) -> tuple[str, str]:
        return self.source, self.key

    def to_dict(self) -> dict[str, str]:
        return {"source": self.source, "key": self.key}


@dataclass(frozen=True, kw_only=True)
class Evidence:
    """One adjudicated or attested result, keyed by ``(source, key)``.

    Attestation loading and git comparison are intentionally not implemented
    here. The contract records the inputs and the honest result: an equal
    commit, or an ancestor whose reviewed paths are unchanged, may satisfy the
    declaration; changed reviewed paths render NO_MEASUREMENT / STALE_ATTESTATION.
    """

    source: str
    key: str
    status: Outcome
    verified_by_assay: bool
    reason_code: ReasonCode | None = None
    producer: str | None = None
    attested_commit: str | None = None
    reviewed_paths: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if self.source not in EVIDENCE_SOURCES:
            raise ValueError(
                f"evidence source must be one of {list(EVIDENCE_SOURCES)}, got "
                f"{self.source!r}"
            )
        _check_nonempty(self.key, "evidence key")
        if not isinstance(self.status, Outcome):
            raise ValueError(
                f"evidence[{self.source}:{self.key}] status must be an Outcome, "
                f"got {self.status!r}"
            )
        if not isinstance(self.verified_by_assay, bool):
            raise ValueError(
                f"evidence[{self.source}:{self.key}] verified_by_assay must be "
                f"a boolean, got {self.verified_by_assay!r}"
            )
        label = f"evidence[{self.source}:{self.key}]"
        _check_reason_code(self.status, self.reason_code, label)
        if self.reason_code in {
            ReasonCode.MISSING_ATTESTATION,
            ReasonCode.STALE_ATTESTATION,
        } and self.source != "attested":
            raise ValueError(f"{label}: {self.reason_code} requires attested source")

        payload = (self.producer, self.attested_commit, self.reviewed_paths)
        if self.source == "adjudicated" and any(value is not None for value in payload):
            raise ValueError(f"{label}: attestation payload belongs only to attested evidence")
        if self.source == "attested":
            if self.verified_by_assay:
                raise ValueError(
                    f"{label}: attested evidence always carries "
                    f"verified_by_assay=False; assay never upgrades a review"
                )
            has_payload = any(value is not None for value in payload)
            if has_payload and not all(value is not None for value in payload):
                raise ValueError(
                    f"{label}: producer, attested_commit and reviewed_paths are "
                    f"all present or all absent"
                )
            if self.status is Outcome.PASS or self.reason_code is ReasonCode.STALE_ATTESTATION:
                if not all(value is not None for value in payload):
                    raise ValueError(
                        f"{label}: a present attestation requires producer, "
                        f"attested_commit and reviewed_paths"
                    )
            if self.reason_code is ReasonCode.MISSING_ATTESTATION and has_payload:
                raise ValueError(f"{label}: missing attestation cannot carry its payload")
            if self.producer is not None:
                _check_nonempty(self.producer, f"{label} producer")
                _check_nonempty(self.attested_commit, f"{label} attested_commit")
                if not self.reviewed_paths:
                    raise ValueError(f"{label} reviewed_paths must not be empty")
                for path in self.reviewed_paths:
                    _check_nonempty(path, f"{label} reviewed path")
                if len(set(self.reviewed_paths)) != len(self.reviewed_paths):
                    raise ValueError(f"{label} reviewed_paths contains a duplicate")

    @property
    def identity(self) -> tuple[str, str]:
        return self.source, self.key

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source": self.source,
            "key": self.key,
            "status": self.status.value,
            "verified_by_assay": self.verified_by_assay,
        }
        if self.reason_code is not None:
            payload["reason_code"] = self.reason_code.value
        if self.producer is not None:
            payload["producer"] = self.producer
            payload["attested_commit"] = self.attested_commit
            payload["reviewed_paths"] = list(self.reviewed_paths or ())
        return payload


@dataclass(frozen=True, kw_only=True)
class Verdict:
    """One verdict: one lane, one commit (§7).

    Every rule the shipped schema encodes is also refused here, plus the two it
    cannot encode — that ``claims`` covers ``declared_rigor`` exactly, and that
    ``argv_effective`` really is ``argv_declared + argv_appended``. Both compare
    two locations in one instance, which JSON Schema draft 2020-12 has no way to
    express (there is no ``$data``); so they are construction-time rejections
    rather than validation rejections, and the tests assert them with
    ``pytest.raises``, never by asserting the model emits the right thing.
    """

    lane: str
    commit: str
    outcome: Outcome
    started: str
    ended: str
    assay_version: str
    claims: tuple[Claim, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    reason_code: ReasonCode | None = None
    declared_rigor: tuple[str, ...] | None = None
    declared_evidence: tuple[EvidenceDeclaration, ...] | None = None
    argv_declared: tuple[str, ...] | None = None
    argv_appended: tuple[str, ...] | None = None
    argv_effective: tuple[str, ...] | None = None
    env_declared: Mapping[str, str] | None = None
    env_effective: Mapping[str, str] | None = None
    #: (P16) the lane's own declared scope/enforcement — join the
    #: lane-resolved group (:data:`LANE_RESOLVED_FIELDS`), always known the
    #: moment a lane loads.
    scope: str | None = None
    enforcement: str | None = None
    #: (P16) the resolved judge policy behind whichever claims rendered a
    #: real computed judgment — see :class:`Judgment` and
    #: :meth:`_check_judgment_matches_claims`. Independently optional (not
    #: part of the strict lane-resolved group): a lane may resolve and
    #: render only R0, which judges nothing R1+ policy could describe.
    judgment: Judgment | None = None
    schema_version: int = VERDICT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, Outcome):
            raise ValueError(f"outcome must be an Outcome, got {self.outcome!r}")
        for name in ("lane", "commit", "assay_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name!r} must be a non-empty string, got {value!r}")
        for name in ("started", "ended"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _TIMESTAMP_RE.fullmatch(value):
                raise ValueError(
                    f"{name!r} must be an ISO-8601 timestamp with an explicit "
                    f"offset, got {value!r}"
                )
        if self.schema_version != VERDICT_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {VERDICT_SCHEMA_VERSION}, got "
                f"{self.schema_version!r}"
            )
        if self.scope is not None and self.scope not in SCOPES:
            raise ValueError(f"scope must be one of {sorted(SCOPES)}, got {self.scope!r}")
        if self.enforcement is not None and self.enforcement not in ENFORCEMENTS:
            raise ValueError(
                f"enforcement must be one of {sorted(ENFORCEMENTS)}, got "
                f"{self.enforcement!r}"
            )

        _check_reason_code(self.outcome, self.reason_code, "verdict")
        self._check_lane_resolved_group()
        self._check_claims_cover_declared_rigor()
        self._check_evidence_covers_declarations()
        self._check_outcome_agrees_with_rollup()
        self._check_judgment_matches_claims()

    # --- the rules the schema cannot express ---------------------------------

    def _check_lane_resolved_group(self) -> None:
        """All present or all absent — absent means the lane never resolved."""
        present = [
            name
            for name in LANE_RESOLVED_FIELDS
            if name != "argv_modified" and getattr(self, name) is not None
        ]
        expected = [name for name in LANE_RESOLVED_FIELDS if name != "argv_modified"]
        if present and len(present) != len(expected):
            missing = sorted(set(expected) - set(present))
            raise ValueError(
                f"the lane-resolved fields are all present or all absent, but "
                f"{', '.join(missing)} is missing. Absent means UNKNOWABLE (no "
                f"lane loaded); empty means known-and-empty. argv_declared = [] "
                f"would assert that the lane declared no argv, which is false"
            )
        if not present:
            if self.claims or self.evidence:
                raise ValueError(
                    "claims or evidence were rendered but no lane resolved; "
                    "evidence without a declaration is evidence about nothing"
                )
            return

        if not self.declared_rigor:
            raise ValueError("declared_rigor must name at least one rigor level")
        for level in self.declared_rigor:
            if level not in RIGOR_LEVELS:
                raise ValueError(
                    f"declared_rigor must contain only {list(RIGOR_LEVELS)}, got "
                    f"{level!r}"
                )
        if len(set(self.declared_rigor)) != len(self.declared_rigor):
            raise ValueError(
                f"declared_rigor contains a duplicate level: "
                f"{list(self.declared_rigor)}"
            )
        if not self.argv_declared:
            raise ValueError("argv_declared is empty; a resolved lane declares argv")

        expected_effective = tuple(self.argv_declared) + tuple(self.argv_appended or ())
        if tuple(self.argv_effective or ()) != expected_effective:
            raise ValueError(
                f"argv_effective {list(self.argv_effective or ())} is not "
                f"argv_declared + argv_appended {list(expected_effective)}; an "
                f"appended flag that is invisible in the verdict lets a PASS "
                f"describe a run that skipped most of the suite"
            )

    def _check_claims_cover_declared_rigor(self) -> None:
        """A-024: exactly one claim per declared level, no more and no fewer."""
        if self.declared_rigor is None:
            return
        seen = [claim.rigor for claim in self.claims]
        duplicates = sorted({level for level in seen if seen.count(level) > 1})
        if duplicates:
            raise ValueError(
                f"more than one claim for rigor level(s) {', '.join(duplicates)}; "
                f"assay records one claim per declared level"
            )
        missing = [level for level in self.declared_rigor if level not in seen]
        if missing:
            raise ValueError(
                f"the lane declared rigor {list(self.declared_rigor)} but rendered "
                f"no claim for {', '.join(missing)}; without an entry, "
                f"'{missing[0]} was declared but rendered no judgement' is "
                f"indistinguishable from '{missing[0]} was never declared'"
            )
        surplus = sorted(set(seen) - set(self.declared_rigor))
        if surplus:
            raise ValueError(
                f"claim(s) for rigor level(s) {', '.join(surplus)} the lane never "
                f"declared {list(self.declared_rigor)}"
            )

    def _check_evidence_covers_declarations(self) -> None:
        """External evidence covers declared ``(source, key)`` identities exactly."""
        if self.declared_evidence is None:
            return
        declared = [item.identity for item in self.declared_evidence]
        rendered = [item.identity for item in self.evidence]
        duplicate_declarations = sorted(
            {identity for identity in declared if declared.count(identity) > 1}
        )
        if duplicate_declarations:
            raise ValueError(
                f"declared_evidence contains duplicate identities: "
                f"{duplicate_declarations}"
            )
        duplicate_evidence = sorted(
            {identity for identity in rendered if rendered.count(identity) > 1}
        )
        if duplicate_evidence:
            raise ValueError(
                f"more than one evidence entry for identities: {duplicate_evidence}"
            )
        missing = [identity for identity in declared if identity not in rendered]
        if missing:
            raise ValueError(
                f"declared evidence {missing} rendered no judgement; without an "
                f"entry it is indistinguishable from never declared"
            )
        surplus = [identity for identity in rendered if identity not in declared]
        if surplus:
            raise ValueError(f"evidence {surplus} was never declared")

    def _check_outcome_agrees_with_rollup(self) -> None:
        """A-023: the outcome is DERIVED from the claims, never chosen beside them."""
        statuses = [claim.status for claim in self.claims]
        statuses.extend(item.status for item in self.evidence)
        if not statuses:
            return
        implied = rollup(statuses)
        if self.outcome is not implied:
            raise ValueError(
                f"outcome {self.outcome} disagrees with the rollup of its claims "
                f"and evidence "
                f"({implied}); precedence is "
                f"{' > '.join(o.value for o in ROLLUP_PRECEDENCE)}, and PASS only "
                f"when every declared claim passed"
            )

    def _check_judgment_matches_claims(self) -> None:
        """P16: ``judgment.r1`` exists if and only if the rendered R1 claim
        (when there is one) carries a ``coverage`` payload — "the effective
        judge policy... whenever changed-line judgment occurred" (sol
        finding 2), never independently of it, and never omitted when it
        did. Run AFTER :meth:`_check_claims_cover_declared_rigor`, which
        already guarantees at most one claim per rigor level, so "the R1
        claim" is unambiguous here.

        **P19/A-148 widens the identical rule to R2 and R3**, in BOTH
        directions, plus one thing schema shape alone cannot express: every
        operator/mechanism a claim's own payload NAMES must be one the
        recorded policy actually declared. `judgment.rN` is tied to the
        claim it describes the same way `judgment.r1` already was — a
        deferral P18 left with no executor (`verdict.py`/`verify.py` were
        both outside every package's `scope.touch` from P16 through P18).
        """
        if self.judgment is not None and self.declared_rigor is None:
            raise ValueError(
                "judgment is present but no lane resolved; a policy for a "
                "lane that never resolved describes nothing"
            )
        r1_claim = next((claim for claim in self.claims if claim.rigor == "R1"), None)
        r1_judged = r1_claim is not None and r1_claim.coverage is not None
        judgment_r1 = None if self.judgment is None else self.judgment.r1
        if judgment_r1 is not None and not r1_judged:
            raise ValueError(
                "judgment.r1 is present but no R1 claim rendered a coverage "
                "payload -- a policy is recorded for a judgment that never "
                "happened"
            )
        if judgment_r1 is None and r1_judged:
            raise ValueError(
                "the R1 claim rendered a coverage payload but judgment.r1 "
                "is absent -- an independent consumer cannot re-derive R1's "
                "status without the policy that decided it"
            )

        r2_claim = next((claim for claim in self.claims if claim.rigor == "R2"), None)
        r2_judged = r2_claim is not None and r2_claim.mutation is not None
        judgment_r2 = None if self.judgment is None else self.judgment.r2
        if judgment_r2 is not None and not r2_judged:
            raise ValueError(
                "judgment.r2 is present but no R2 claim rendered a mutation "
                "payload -- a policy is recorded for a judgment that never "
                "happened"
            )
        if judgment_r2 is None and r2_judged:
            raise ValueError(
                "the R2 claim rendered a mutation payload but judgment.r2 "
                "is absent -- an independent consumer cannot re-derive R2's "
                "status without the policy that decided it"
            )
        if r2_judged and judgment_r2 is not None:
            observed_operators = {
                outcome.operator
                for bucket in (
                    r2_claim.mutation.survived,
                    r2_claim.mutation.crashed,
                    r2_claim.mutation.budget_exceeded,
                )
                for outcome in bucket
            }
            unknown_operators = sorted(observed_operators - set(judgment_r2.operators))
            if unknown_operators:
                raise ValueError(
                    f"claim[R2].mutation records outcome(s) for operator(s) "
                    f"{unknown_operators}, outside judgment.r2.operators "
                    f"{list(judgment_r2.operators)} -- a mutant this policy "
                    f"never selected cannot have been submitted"
                )

        r3_claim = next((claim for claim in self.claims if claim.rigor == "R3"), None)
        r3_judged = r3_claim is not None and r3_claim.canary is not None
        judgment_r3 = None if self.judgment is None else self.judgment.r3
        if judgment_r3 is not None and not r3_judged:
            raise ValueError(
                "judgment.r3 is present but no R3 claim rendered a canary "
                "payload -- a policy is recorded for a judgment that never "
                "happened"
            )
        if judgment_r3 is None and r3_judged:
            raise ValueError(
                "the R3 claim rendered a canary payload but judgment.r3 "
                "is absent -- an independent consumer cannot re-derive R3's "
                "status without the policy that decided it"
            )
        if r3_judged and judgment_r3 is not None:
            if r3_claim.canary.mechanism != judgment_r3.mechanism:
                raise ValueError(
                    f"claim[R3].canary was produced by mechanism "
                    f"{r3_claim.canary.mechanism!r}, but judgment.r3 "
                    f"records the policy as {judgment_r3.mechanism!r} -- the "
                    f"two must name the same mechanism"
                )

    # --- serialisation --------------------------------------------------------

    @property
    def exit_code(self) -> int:
        """The exit code IS the verdict; the artifact never disagrees with it."""
        return EXIT_CODES[self.outcome]

    @property
    def argv_modified(self) -> bool | None:
        """Derived, never stored (A-036).

        Deriving it removes a whole class of artifact in which ``argv_appended``
        and ``argv_modified`` disagree. ``None`` when no lane resolved.
        """
        if self.argv_appended is None:
            return None
        return bool(self.argv_appended)

    def to_dict(self) -> dict[str, Any]:
        """The artifact, as plain JSON types."""
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "assay_version": self.assay_version,
            "lane": self.lane,
            "commit": self.commit,
            "outcome": self.outcome.value,
            "exit_code": self.exit_code,
            "started": self.started,
            "ended": self.ended,
        }
        if self.reason_code is not None:
            payload["reason_code"] = self.reason_code.value
        if self.declared_rigor is not None:
            payload["declared_rigor"] = list(self.declared_rigor)
            payload["declared_evidence"] = [
                item.to_dict() for item in self.declared_evidence or ()
            ]
            payload["argv_declared"] = list(self.argv_declared or ())
            payload["argv_appended"] = list(self.argv_appended or ())
            payload["argv_effective"] = list(self.argv_effective or ())
            payload["argv_modified"] = bool(self.argv_modified)
            payload["env_declared"] = dict(self.env_declared or {})
            payload["env_effective"] = dict(self.env_effective or {})
            payload["scope"] = self.scope
            payload["enforcement"] = self.enforcement
        if self.judgment is not None:
            payload["judgment"] = self.judgment.to_dict()
        payload["claims"] = [claim.to_dict() for claim in self.claims]
        payload["evidence"] = [item.to_dict() for item in self.evidence]
        return payload

    def to_json(self) -> str:
        """The artifact as text, stable across runs and interpreters."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"
