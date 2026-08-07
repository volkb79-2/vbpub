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
from typing import Any, Iterable, Mapping

from .config import RIGOR_LEVELS
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
    "Claim",
    "Coverage",
    "Evidence",
    "EvidenceDeclaration",
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
#: artifact.
VERDICT_SCHEMA_VERSION = 2

#: Where the shipped schema lives inside the installed package. Declared as
#: package data in `pyproject.toml`; without that it exists in the source tree
#: and vanishes on install, silently breaking A-029 for every consumer while
#: every in-tree test stays green.
SCHEMA_RESOURCE = "schemas/verdict.schema.json"

#: Computed rigor claims and external evidence are deliberately separate axes.
CLAIM_SOURCES: tuple[str, ...] = ("computed",)
EVIDENCE_SOURCES: tuple[str, ...] = ("adjudicated", "attested")

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
#: all absent — see the module docstring on absent-vs-empty.
LANE_RESOLVED_FIELDS: tuple[str, ...] = (
    "declared_rigor",
    "declared_evidence",
    "argv_declared",
    "argv_appended",
    "argv_effective",
    "argv_modified",
    "env_declared",
    "env_effective",
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


@dataclass(frozen=True, kw_only=True)
class Coverage:
    """The R1 claim payload: changed-line coverage, and why its denominator is
    what it is.

    ``considered`` is srdm's fix and the reason a legitimate 0/0 pass is not
    textually identical to a broken measurement (A-026): *"3 changed file(s)
    under the roots, none contributing executable non-test lines (0/0)"* is a
    different sentence from *"0/0 covered (100.0%)"*. Identical strings for the
    two cases is what started this whole project.
    """

    covered: int
    changed_executable: int
    pct: float
    #: changed files under the source roots that were considered at all
    considered: int

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "covered": self.covered,
            "changed_executable": self.changed_executable,
            "pct": float(self.pct),
            "considered": self.considered,
        }


@dataclass(frozen=True, kw_only=True)
class Claim:
    """One COMPUTED declared rigor level's evidence (A-024)."""

    rigor: str
    source: str
    status: Outcome
    verified_by_assay: bool
    reason_code: ReasonCode | None = None
    coverage: Coverage | None = None

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

        _check_reason_code(self.outcome, self.reason_code, "verdict")
        self._check_lane_resolved_group()
        self._check_claims_cover_declared_rigor()
        self._check_evidence_covers_declarations()
        self._check_outcome_agrees_with_rollup()

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
        payload["claims"] = [claim.to_dict() for claim in self.claims]
        payload["evidence"] = [item.to_dict() for item in self.evidence]
        return payload

    def to_json(self) -> str:
        """The artifact as text, stable across runs and interpreters."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"
