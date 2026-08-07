"""``assay verify`` — an independent ARTIFACT VALIDATOR, not a lane-canary
runner (A-129, correcting a stale planning note that predates this ruling).

``assay verify <path>`` (``-`` for stdin, matching ``--verdict-json``'s own
convention) reads a verdict-JSON artifact and answers one question: *is this
a schema-conformant, internally self-consistent verdict?* It never re-runs a
lane, never re-judges a change, and never becomes the sole witness to its own
correctness (DESIGN-GUIDE §9) — it is a SECONDARY consumer, exercised
alongside the independent jsonschema fixture suite (O2), never in place of
it.

Two things this module refuses to be, on purpose:

* **A JSON-Schema engine.** assay ships zero runtime dependencies (A-005);
  ``jsonschema`` is a *test*-only extra. So "validates against packaged
  schema v2" is not achieved by running a general Draft 2020-12 evaluator at
  runtime — it is achieved by RECONSTRUCTING the same
  :mod:`assay.verdict` dataclass graph the schema is maintained equivalent
  to. That module's own docstring states the invariant this leans on: the
  dataclasses "refuse to construct a verdict the schema would refuse to
  accept." Reusing that (never reimplementing JSON Schema by hand, which
  would only prove a hand-rolled checker agrees with itself, A-056) is what
  ``verdict.load_schema()``/``verdict.rollup()`` being explicitly importable
  from this forbidden-to-edit module is FOR.
* **A generator of expected artifacts.** Nothing here ever builds a
  "reference" verdict to diff against — it only ever asks "would the
  existing, already-proven construction-time rules accept this JSON as
  written," field by field, the same rules every producer test in this
  project is already held to.

Two stages, matching the handoff's own two sentences exactly:

1. Checks the packaged schema v2 (`load_schema()` is not called here — schema
   *conformance* is the RECONSTRUCTION below; loading the raw schema document
   would only be useful to a real JSON-Schema evaluator, which this module
   deliberately is not) cannot skip: reconstructing ``claims``/``evidence``
   and every nested payload (``coverage``/``canary``/``mutation``) into their
   real dataclasses, and rejecting any top-level or nested key the
   reconstructed object's own ``to_dict()`` does not also carry — the
   "unknown-key" rejection O2 names, achieved without ever calling
   ``Verdict(**document)`` blindly (an unexpected top-level key like
   ``exit_code``/``argv_modified`` is a LEGAL schema field but not a
   constructor parameter — both are *derived* properties — so a blind
   ``**document`` unpack would reject those two universally, which is wrong).
2. What JSON Schema 2020-12 cannot express at all, because it needs to
   compare two locations in the SAME instance: outcome-agrees-with-rollup
   (:func:`assay.verdict.rollup`, imported — never reimplemented),
   ``argv_effective == argv_declared + argv_appended``, claims-cover-
   declared-rigor exactly, evidence-covers-declared-evidence exactly. These
   run against the RAW parsed document, ahead of reconstruction, so their own
   rejection branches are independently reachable by a test — reconstruction
   ALONE would also catch every one of them (they live inside
   :meth:`~assay.verdict.Verdict.__post_init__` too), which would make a
   test written the other way around exercise unreachable code.

Exit 0 only when both stages find nothing to report; otherwise every
distinct failure is printed to stderr, one per line, and the process exits 1.
"""

from __future__ import annotations

import argparse
import json
from types import MappingProxyType
from typing import Any, TextIO

from .verdict import (
    EXIT_CODES,
    REASON_CODES,
    CanaryResult,
    Claim,
    Coverage,
    Evidence,
    EvidenceDeclaration,
    MutantOutcome,
    Mutation,
    Outcome,
    ReasonCode,
    Verdict,
    rollup,
)

__all__ = ["build_verify_parser", "cmd_verify", "verify_document", "verify_text"]

#: The eight-field lane-resolved group, exactly `verdict.LANE_RESOLVED_FIELDS`
#: minus the derived `argv_modified` — transcribed by hand rather than
#: imported, the same independence `tests/test_errors.py` already applies to
#: the outcome/reason_code tables (A-092's house style).
_LANE_RESOLVED_FIELDS: tuple[str, ...] = (
    "declared_rigor",
    "declared_evidence",
    "argv_declared",
    "argv_appended",
    "argv_effective",
    "env_declared",
    "env_effective",
)


def _outcome_of(value: Any) -> Outcome | None:
    try:
        return Outcome(value)
    except (ValueError, TypeError):
        return None


def _reason_code_of(value: Any) -> ReasonCode | None:
    try:
        return ReasonCode(value)
    except (ValueError, TypeError):
        return None


def _check_outcome_and_reason_code(document: dict, failures: list[str]) -> Outcome | None:
    outcome = _outcome_of(document.get("outcome"))
    if "outcome" in document and outcome is None:
        failures.append(
            f"outcome {document['outcome']!r} is not one of "
            f"{[member.value for member in Outcome]}"
        )

    if "reason_code" in document:
        reason_code = _reason_code_of(document["reason_code"])
        if reason_code is None:
            failures.append(
                f"reason_code {document['reason_code']!r} is not a recognised "
                f"reason code"
            )
        elif outcome is not None and reason_code not in REASON_CODES[outcome]:
            valid = sorted(code.value for code in REASON_CODES[outcome])
            failures.append(
                f"reason_code {reason_code.value} is not valid for outcome "
                f"{outcome.value}; valid: {valid}"
            )
    return outcome


def _check_exit_code(document: dict, outcome: Outcome | None, failures: list[str]) -> None:
    if outcome is None or "exit_code" not in document:
        return
    expected = EXIT_CODES[outcome]
    if document["exit_code"] != expected:
        failures.append(
            f"exit_code {document['exit_code']!r} does not match outcome "
            f"{outcome.value}'s exit code {expected}"
        )


def _check_lane_resolved_group(document: dict, failures: list[str]) -> None:
    """A-129's ``argv_effective == argv_declared + argv_appended`` check,
    plus the schema's own all-present-or-all-absent rule one level up, and
    ``argv_modified``'s own derivation — every one compared on the RAW
    document so a test can trip each branch without depending on whether
    reconstruction later would also have caught it.
    """
    present = [name for name in _LANE_RESOLVED_FIELDS if name in document]
    if not present:
        return
    if len(present) != len(_LANE_RESOLVED_FIELDS):
        missing = sorted(set(_LANE_RESOLVED_FIELDS) - set(present))
        failures.append(
            f"the lane-resolved fields are all present or all absent, but "
            f"{', '.join(missing)} is missing"
        )
        return

    declared = document["argv_declared"]
    appended = document["argv_appended"]
    effective = document["argv_effective"]
    expected_effective = list(declared) + list(appended)
    if list(effective) != expected_effective:
        failures.append(
            f"argv_effective {effective!r} is not argv_declared + "
            f"argv_appended {expected_effective!r}"
        )

    expected_modified = bool(appended)
    if "argv_modified" not in document:
        failures.append("argv_modified is required once a lane has resolved")
    elif document["argv_modified"] != expected_modified:
        failures.append(
            f"argv_modified {document['argv_modified']!r} does not match "
            f"bool(argv_appended) = {expected_modified}"
        )


def _check_claims_cover_declared_rigor(document: dict, failures: list[str]) -> None:
    if "declared_rigor" not in document:
        return
    declared_rigor = document["declared_rigor"]
    claims = document.get("claims") or []
    claim_rigors = [item.get("rigor") for item in claims if isinstance(item, dict)]
    missing = [level for level in declared_rigor if level not in claim_rigors]
    if missing:
        failures.append(
            f"declared_rigor {list(declared_rigor)} has no claim for {missing}"
        )
    surplus = sorted({level for level in claim_rigors if level not in declared_rigor})
    if surplus:
        failures.append(f"claim(s) for undeclared rigor level(s): {surplus}")


def _check_evidence_covers_declared_evidence(document: dict, failures: list[str]) -> None:
    if "declared_evidence" not in document:
        return
    declared = [
        (item.get("source"), item.get("key"))
        for item in document["declared_evidence"]
        if isinstance(item, dict)
    ]
    rendered = [
        (item.get("source"), item.get("key"))
        for item in (document.get("evidence") or [])
        if isinstance(item, dict)
    ]
    missing = [identity for identity in declared if identity not in rendered]
    if missing:
        failures.append(f"declared evidence {missing} rendered no judgement")
    surplus = [identity for identity in rendered if identity not in declared]
    if surplus:
        failures.append(f"evidence {surplus} was never declared")


def _check_outcome_agrees_with_rollup(
    document: dict, outcome: Outcome | None, failures: list[str]
) -> None:
    """A-129's ``rollup()`` re-import, applied to the RAW claim/evidence
    statuses rather than a reconstructed :class:`~assay.verdict.Verdict` —
    independently reachable the same way the other three checks above are.
    """
    claims = document.get("claims")
    evidence = document.get("evidence")
    if not isinstance(claims, list) or not isinstance(evidence, list):
        return
    statuses: list[Outcome] = []
    for item in (*claims, *evidence):
        if not isinstance(item, dict):
            return
        status = _outcome_of(item.get("status"))
        if status is None:
            return
        statuses.append(status)
    if not statuses or outcome is None:
        return
    implied = rollup(statuses)
    if implied is not outcome:
        failures.append(
            f"outcome {outcome.value} disagrees with the rollup of its claims "
            f"and evidence ({implied.value})"
        )


def _reject_unknown_keys(raw: dict, built: dict, what: str) -> None:
    unknown = sorted(set(raw.keys()) - set(built.keys()))
    if unknown:
        raise ValueError(f"unknown {what} field(s): {unknown}")


def _reconstruct_coverage(raw: dict) -> Coverage:
    coverage = Coverage(
        covered=raw["covered"],
        changed_executable=raw["changed_executable"],
        pct=raw["pct"],
        considered=raw["considered"],
        missing_lines={key: frozenset(value) for key, value in raw["missing_lines"].items()},
        files_missing_coverage=tuple(raw["files_missing_coverage"]),
        unclassified_lines={
            key: frozenset(value) for key, value in raw["unclassified_lines"].items()
        },
        files_with_unclassified_lines=tuple(raw["files_with_unclassified_lines"]),
    )
    _reject_unknown_keys(raw, coverage.to_dict(), "coverage")
    return coverage


def _reconstruct_canary(raw: dict) -> CanaryResult:
    transformed = raw.get("transformed_outcome")
    expected_rc = raw.get("expected_reason_code")
    observed_rc = raw.get("observed_reason_code")
    canary = CanaryResult(
        mechanism=raw["mechanism"],
        description=raw["description"],
        control_outcome=Outcome(raw["control_outcome"]),
        transformed_outcome=Outcome(transformed) if transformed is not None else None,
        expected_reason_code=ReasonCode(expected_rc) if expected_rc is not None else None,
        observed_reason_code=ReasonCode(observed_rc) if observed_rc is not None else None,
    )
    _reject_unknown_keys(raw, canary.to_dict(), "canary")
    return canary


def _reconstruct_mutant_outcome(raw: dict) -> MutantOutcome:
    item = MutantOutcome(
        path=raw["path"],
        lineno=raw["lineno"],
        operator=raw["operator"],
        description=raw["description"],
    )
    _reject_unknown_keys(raw, item.to_dict(), "mutant outcome")
    return item


def _reconstruct_mutation(raw: dict) -> Mutation:
    mutation = Mutation(
        total=raw["total"],
        killed=raw["killed"],
        survived=tuple(_reconstruct_mutant_outcome(item) for item in raw["survived"]),
        crashed=tuple(_reconstruct_mutant_outcome(item) for item in raw["crashed"]),
        budget_exceeded=tuple(
            _reconstruct_mutant_outcome(item) for item in raw["budget_exceeded"]
        ),
    )
    _reject_unknown_keys(raw, mutation.to_dict(), "mutation")
    return mutation


def _reconstruct_claim(raw: dict) -> Claim:
    reason_code = raw.get("reason_code")
    claim = Claim(
        rigor=raw["rigor"],
        source=raw["source"],
        status=Outcome(raw["status"]),
        verified_by_assay=raw["verified_by_assay"],
        reason_code=ReasonCode(reason_code) if reason_code is not None else None,
        coverage=_reconstruct_coverage(raw["coverage"]) if "coverage" in raw else None,
        canary=_reconstruct_canary(raw["canary"]) if "canary" in raw else None,
        mutation=_reconstruct_mutation(raw["mutation"]) if "mutation" in raw else None,
    )
    _reject_unknown_keys(raw, claim.to_dict(), "claim")
    return claim


def _reconstruct_evidence(raw: dict) -> Evidence:
    reason_code = raw.get("reason_code")
    evidence = Evidence(
        source=raw["source"],
        key=raw["key"],
        status=Outcome(raw["status"]),
        verified_by_assay=raw["verified_by_assay"],
        reason_code=ReasonCode(reason_code) if reason_code is not None else None,
        producer=raw.get("producer"),
        attested_commit=raw.get("attested_commit"),
        reviewed_paths=tuple(raw["reviewed_paths"]) if "reviewed_paths" in raw else None,
    )
    _reject_unknown_keys(raw, evidence.to_dict(), "evidence")
    return evidence


def _reconstruct_verdict(document: dict) -> Verdict:
    """Attempt the same construction the real producer path already goes
    through — refusing anything the packaged schema would refuse, per
    :mod:`assay.verdict`'s own module docstring. Raises ``TypeError``/
    ``ValueError``/``KeyError`` on any shape the schema would reject; the
    caller turns that into one more entry in the failure list.
    """
    claims = tuple(_reconstruct_claim(item) for item in document["claims"])
    evidence = tuple(_reconstruct_evidence(item) for item in document["evidence"])

    lane_resolved = "declared_rigor" in document
    lane_kwargs: dict[str, Any] = {}
    if lane_resolved:
        lane_kwargs = dict(
            declared_rigor=tuple(document["declared_rigor"]),
            declared_evidence=tuple(
                EvidenceDeclaration(source=item["source"], key=item["key"])
                for item in document["declared_evidence"]
            ),
            argv_declared=tuple(document["argv_declared"]),
            argv_appended=tuple(document["argv_appended"]),
            argv_effective=tuple(document["argv_effective"]),
            env_declared=MappingProxyType(dict(document["env_declared"])),
            env_effective=MappingProxyType(dict(document["env_effective"])),
        )

    reason_code = document.get("reason_code")
    verdict = Verdict(
        lane=document["lane"],
        commit=document["commit"],
        outcome=Outcome(document["outcome"]),
        reason_code=ReasonCode(reason_code) if reason_code is not None else None,
        started=document["started"],
        ended=document["ended"],
        assay_version=document["assay_version"],
        claims=claims,
        evidence=evidence,
        schema_version=document["schema_version"],
        **lane_kwargs,
    )

    expected_exit = verdict.exit_code
    if document.get("exit_code") != expected_exit:
        raise ValueError(
            f"exit_code {document.get('exit_code')!r} does not match outcome "
            f"{verdict.outcome.value}'s exit code {expected_exit}"
        )
    if lane_resolved:
        expected_modified = verdict.argv_modified
        if document.get("argv_modified") != expected_modified:
            raise ValueError(
                f"argv_modified {document.get('argv_modified')!r} does not "
                f"match bool(argv_appended) = {expected_modified}"
            )
    elif "argv_modified" in document:
        raise ValueError("argv_modified present without a resolved lane")

    _reject_unknown_keys(document, verdict.to_dict(), "top-level")
    return verdict


def verify_document(document: Any) -> list[str]:
    """Every reason *document* is not a valid, internally self-consistent
    verdict artifact. Empty means valid — the only condition under which
    :func:`cmd_verify` exits 0.
    """
    if not isinstance(document, dict):
        return [f"artifact must be a JSON object, got {type(document).__name__}"]

    failures: list[str] = []

    required = (
        "schema_version",
        "assay_version",
        "lane",
        "commit",
        "outcome",
        "exit_code",
        "started",
        "ended",
        "claims",
        "evidence",
    )
    missing_required = [key for key in required if key not in document]
    if missing_required:
        failures.append(f"missing required field(s): {missing_required}")

    outcome = _check_outcome_and_reason_code(document, failures)
    _check_exit_code(document, outcome, failures)
    _check_lane_resolved_group(document, failures)
    _check_claims_cover_declared_rigor(document, failures)
    _check_evidence_covers_declared_evidence(document, failures)
    _check_outcome_agrees_with_rollup(document, outcome, failures)

    try:
        _reconstruct_verdict(document)
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        failures.append(f"schema: {exc}")

    return failures


def verify_text(text: str) -> list[str]:
    """:func:`verify_document`, from raw JSON text — the shape both stdin
    and a file path resolve to before validation begins."""
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        return [f"not valid JSON: {exc}"]
    return verify_document(document)


def build_verify_parser(subparsers: "argparse._SubParsersAction[Any]") -> None:
    verify = subparsers.add_parser(
        "verify",
        help="validate a verdict-JSON artifact independently of how it was produced",
        description=(
            "Read a verdict-JSON artifact and check it is schema-conformant "
            "and internally self-consistent. Never re-runs a lane; never the "
            "sole witness to a producer's correctness (DESIGN-GUIDE §9)."
        ),
    )
    verify.add_argument(
        "path",
        help="path to a verdict-JSON artifact, or '-' for stdin",
    )


def cmd_verify(path: str, *, stdin: TextIO, stderr: TextIO) -> int:
    text = stdin.read() if path == "-" else _read_file(path)
    if text is None:
        print(f"assay verify: cannot read {path!r}", file=stderr)
        return 1
    failures = verify_text(text)
    if not failures:
        return 0
    for failure in failures:
        print(f"assay verify: {failure}", file=stderr)
    return 1


def _read_file(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None
