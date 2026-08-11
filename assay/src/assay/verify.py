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

Three stages:

1. Checks the packaged schema v5 (`load_schema()` is not called here — schema
   *conformance* is the RECONSTRUCTION below; loading the raw schema document
   would only be useful to a real JSON-Schema evaluator, which this module
   deliberately is not) cannot skip: reconstructing ``claims``/``evidence``
   and every nested payload (``coverage``/``canary``/``mutation``/
   ``judgment``) into their real dataclasses, and rejecting any top-level or
   nested key the reconstructed object's own ``to_dict()`` does not also
   carry — the "unknown-key" rejection O2 names, achieved without ever
   calling ``Verdict(**document)`` blindly (an unexpected top-level key like
   ``exit_code``/``argv_modified`` is a LEGAL schema field but not a
   constructor parameter — both are *derived* properties — so a blind
   ``**document`` unpack would reject those two universally, which is wrong).
2. What JSON Schema 2020-12 cannot express at all, because it needs to
   compare two locations in the SAME instance: outcome-agrees-with-rollup
   (:func:`assay.verdict.rollup`, imported — never reimplemented),
   ``argv_effective == argv_declared + argv_appended``, claims-cover-
   declared-rigor exactly, evidence-covers-declared-evidence exactly, and
   (P16) ``judgment.r1`` exists exactly when the R1 claim carries
   ``coverage``. These run against the RAW parsed document, ahead of
   reconstruction, so their own rejection branches are independently
   reachable by a test — reconstruction ALONE would also catch every one of
   them (they live inside :meth:`~assay.verdict.Verdict.__post_init__` too),
   which would make a test written the other way around exercise
   unreachable code.
3. (P16, sol finding 2) INDEPENDENT re-derivation of R1/R2/R3 *status* from
   their own payload plus recorded policy — never merely trusting that the
   producer-selected ``status`` agrees with its own ``coverage``/
   ``mutation``/``canary`` evidence, which schema shape and rollup agreement
   alone cannot catch (a ``PASS`` reporting 0% coverage, a ``PASS`` with a
   surviving mutant, or a ``PASS`` canary whose transform never actually
   failed are all schema-valid AND rollup-consistent). R3 reuses
   :func:`assay.canary.judge_canary` directly — it is already pure over an
   already-reconstructed :class:`~assay.verdict.CanaryResult`, needing no
   lane/process/filesystem state. R2 reuses
   :func:`assay.mutation.judge_mutation`'s bucket precedence rather than
   copying its mapping, via a minimal stand-in for the ``runner.
   CommandResult`` baseline it expects (see :func:`_check_r2_rederivation`
   for why that is safe). R1 has no existing pure judgment function to
   import — :mod:`assay.evaluate`'s own precedence (unclassified > excluded
   > uncovered-floor > pass) is restated directly against ``Coverage`` plus
   ``judgment.r1`` instead of importing that module, which would pull
   assay's whole diff/git/adapter pipeline into an artifact validator that
   never re-runs a lane.

Exit 0 only when all three stages find nothing to report; otherwise every
distinct failure is printed to stderr, one per line, and the process exits 1.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from types import MappingProxyType, SimpleNamespace
from typing import Any, TextIO

from .canary import judge_canary
from .mutation import judge_mutation
from .verdict import (
    EXIT_CODES,
    MUTATION_BUCKETS,
    REASON_CODES,
    VERDICT_SCHEMA_VERSION,
    CanaryResult,
    Claim,
    Coverage,
    Evidence,
    EvidenceDeclaration,
    Helper,
    Judgment,
    JudgmentR1,
    JudgmentR2,
    JudgmentR3,
    JudgmentResolved,
    MutantOutcome,
    Mutation,
    Outcome,
    ReasonCode,
    Verdict,
    operator_language,
    rollup,
)

__all__ = ["build_verify_parser", "cmd_verify", "verify_document", "verify_text"]

#: The ten-field lane-resolved group, exactly `verdict.LANE_RESOLVED_FIELDS`
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
    "scope",
    "enforcement",
)


#: (A-251) ``(rigor, payload, judged statuses)``, transcribed from
#: :meth:`assay.verdict.Claim._check_a_judged_status_carries_its_own_payload`.
#: A table rather than open-coded conditionals so the SCOPE is auditable at a
#: glance -- the first attempt at this rule failed on scope, not mechanism.
_JUDGED_STATUS_REQUIRES_PAYLOAD: tuple[tuple[str, str, frozenset[str]], ...] = (
    ("R1", "coverage", frozenset({"PASS", "FAIL"})),
    ("R2", "mutation", frozenset({"PASS"})),
    ("R3", "canary", frozenset({"PASS", "FAIL", "INCONCLUSIVE"})),
)

#: ``verdict._MUTATION_ONLY_REASON_CODES`` by name, for the raw layer, which
#: reads strings and must never import the model's enum to compare them.
_MUTATION_ONLY_REASON_CODE_NAMES: frozenset[str] = frozenset(
    {"MUTANTS_SURVIVED", "NO_MUTANTS", "ALL_MUTANTS_EQUIVALENT"}
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


def _check_judgment_matches_claims(document: dict, failures: list[str]) -> None:
    """P16's ``judgment.r1``-vs-``Claim.coverage`` correspondence
    (:meth:`assay.verdict.Verdict._check_judgment_matches_claims`), reached
    on the RAW document the same way the other cross-field checks above are
    — reconstruction alone would also catch this (the identical rule lives
    in ``Verdict.__post_init__`` too), so a test exercising this branch
    specifically can stay schema-valid rather than depend on reconstruction.
    Worded DELIBERATELY differently from the model's own two messages (never
    the identical string): a mutation test proved that an identical wording
    makes the raw check's own failure indistinguishable from reconstruction
    independently catching the same defect, which would make "is this raw
    check even reached" untestable by message content alone.

    **P19/A-148 widens this to R2 and R3**, worded differently again from
    :meth:`assay.verdict.Verdict._check_judgment_matches_claims`'s own R2/R3
    messages for the identical reason, plus the one check no schema and no
    reconstruction alone can express as cleanly on the raw document: every
    operator/mechanism a claim's payload NAMES must be one the recorded
    policy actually declared.
    """
    claims = document.get("claims")
    if not isinstance(claims, list):
        return
    judgment = document.get("judgment")

    r1_claim = next(
        (item for item in claims if isinstance(item, dict) and item.get("rigor") == "R1"),
        None,
    )
    r1_judged = r1_claim is not None and "coverage" in r1_claim
    judgment_r1_present = isinstance(judgment, dict) and "r1" in judgment
    if judgment_r1_present and not r1_judged:
        failures.append(
            "judgment.r1 is declared without a corresponding R1 coverage claim"
        )
    if r1_judged and not judgment_r1_present:
        failures.append(
            "an R1 coverage claim is declared without a corresponding judgment.r1"
        )

    r2_claim = next(
        (item for item in claims if isinstance(item, dict) and item.get("rigor") == "R2"),
        None,
    )
    r2_judged = r2_claim is not None and "mutation" in r2_claim
    # P21/A-183: the unsupported-capability terminal ALSO resolved and applied
    # the policy, so it records one. Worded from the raw document rather than
    # reusing the model's own phrasing, for the reason this function's
    # docstring already gives.
    r2_attempted = r2_judged or (
        r2_claim is not None
        and r2_claim.get("reason_code") == "MUTATION_UNSUPPORTED"
    )
    judgment_r2 = judgment.get("r2") if isinstance(judgment, dict) else None
    judgment_r2_present = isinstance(judgment, dict) and "r2" in judgment
    if judgment_r2_present and not r2_attempted:
        failures.append(
            "judgment.r2 is declared without a corresponding R2 mutation claim "
            "or unsupported-capability terminal"
        )
    if r2_attempted and not judgment_r2_present:
        failures.append(
            "an R2 mutation claim or unsupported-capability terminal is "
            "declared without a corresponding judgment.r2"
        )
    if r2_judged and judgment_r2_present and isinstance(judgment_r2, dict):
        mutation = r2_claim.get("mutation")
        operators = judgment_r2.get("operators")
        if isinstance(mutation, dict) and isinstance(operators, list):
            observed: set = set()
            # (P21 phase-2 review) `killed` belongs in this sweep exactly as
            # it does in the model's own (A-180): under v3 it was a bare
            # count, so a killed mutant produced by an operator the lane
            # never selected was structurally unobservable, and closing that
            # is this package's own work item 3. Omitting it here left the
            # single largest bucket with only ONE witness -- the producer
            # model reached through reconstruction -- which is precisely the
            # independence A-182 assigns to model AND raw verifier
            # separately.
            # (P33/A-228) `equivalent` joins the sweep for the identical
            # reason `killed` did: a bucket this loop does not visit is a
            # bucket in which an operator the policy never selected is
            # structurally unobservable to the raw layer, leaving the model
            # as its only witness -- which is precisely the independence
            # A-182 assigns to model AND raw verifier separately.
            for _bucket_name, entry in _mutant_entries(mutation):
                if isinstance(entry.get("operator"), str):
                    observed.add(entry["operator"])
            unknown_operators = sorted(observed - set(operators))
            if unknown_operators:
                failures.append(
                    f"the R2 mutation payload names operator(s) "
                    f"{unknown_operators} that judgment.r2.operators "
                    f"{operators} never declared"
                )

    if isinstance(judgment, dict):
        _check_resolved_language_owns_every_operator(document, judgment, failures)
        _check_base_matches_the_tiers_present(judgment, failures)
    if isinstance(judgment_r2, dict) and r2_judged:
        _check_equivalence_pairing(r2_claim, judgment_r2, failures)
        _check_kill_attribution(r2_claim, judgment_r2, failures)

    r3_claim = next(
        (item for item in claims if isinstance(item, dict) and item.get("rigor") == "R3"),
        None,
    )
    r3_judged = r3_claim is not None and "canary" in r3_claim
    judgment_r3 = judgment.get("r3") if isinstance(judgment, dict) else None
    judgment_r3_present = isinstance(judgment, dict) and "r3" in judgment
    if judgment_r3_present and not r3_judged:
        failures.append(
            "judgment.r3 is declared without a corresponding R3 canary claim"
        )
    if r3_judged and not judgment_r3_present:
        failures.append(
            "an R3 canary claim is declared without a corresponding judgment.r3"
        )
    if r3_judged and judgment_r3_present and isinstance(judgment_r3, dict):
        canary = r3_claim.get("canary")
        if isinstance(canary, dict):
            observed_mechanism = canary.get("mechanism")
            declared_mechanism = judgment_r3.get("mechanism")
            if observed_mechanism != declared_mechanism:
                failures.append(
                    f"the R3 canary payload's mechanism "
                    f"{observed_mechanism!r} does not match "
                    f"judgment.r3.mechanism {declared_mechanism!r}"
                )
            # P21/A-152/A-O18: the half schema v3 could not witness at all,
            # which is why this migration exists. Both sides are normalized
            # by their own grammar, so this equality is over one spelling.
            observed_target = canary.get("target")
            declared_target = judgment_r3.get("target")
            if observed_target != declared_target:
                failures.append(
                    f"the R3 canary payload ran against {observed_target!r} "
                    f"while judgment.r3.target declares {declared_target!r}; "
                    f"the recorded canary answers for a different file than "
                    f"the policy asked about"
                )


def _mutant_entries(mutation: Any) -> list[tuple[str, dict]]:
    """Every ``(bucket_name, entry)`` pair in a raw mutation payload.

    One helper rather than five open-coded loops, for A-228's own reason: a
    bucket that reaches some of the layers that constrain the others and not
    the rest is precisely how the fifth bucket's constraints went missing the
    first time.
    """
    if not isinstance(mutation, dict):
        return []
    pairs: list[tuple[str, dict]] = []
    for name in MUTATION_BUCKETS:
        entries = mutation.get(name)
        if not isinstance(entries, list):
            continue
        pairs.extend((name, entry) for entry in entries if isinstance(entry, dict))
    return pairs


def _check_resolved_language_owns_every_operator(
    document: dict, judgment: dict, failures: list[str]
) -> None:
    """(P33/V5-2, invariant 1) every operator's prefix equals
    ``judgment.resolved.language``, on the RAW document.

    The schema closes each language's vocabulary but cannot relate one field
    to another (no ``$data`` in draft 2020-12), so on its own it accepts a
    Python lane recording a complete set of SQL operators. Worded
    deliberately differently from the model's own two messages, the
    convention this module has followed since P16: identical wording would
    make the raw check's failure indistinguishable from reconstruction
    catching the same defect, and "is this raw branch even reached" would
    stop being testable by message content.
    """
    resolved = judgment.get("resolved")
    if not isinstance(resolved, dict):
        return
    language = resolved.get("language")
    if not isinstance(language, str):
        return
    r2 = judgment.get("r2")
    declared = r2.get("operators") if isinstance(r2, dict) else None
    if isinstance(declared, list):
        foreign = sorted(
            {
                operator
                for operator in declared
                if isinstance(operator, str)
                and operator_language(operator) != language
            }
        )
        if foreign:
            failures.append(
                f"judgment.r2.operators declares {foreign}, whose language "
                f"prefix is not the resolved language {language!r} recorded in "
                f"judgment.resolved"
            )
    claims = document.get("claims")
    if not isinstance(claims, list):
        return
    r2_claim = next(
        (item for item in claims if isinstance(item, dict) and item.get("rigor") == "R2"),
        None,
    )
    applied = sorted(
        {
            entry["operator"]
            for _, entry in _mutant_entries(_mutation_of(r2_claim))
            if isinstance(entry.get("operator"), str)
            and operator_language(entry["operator"]) != language
        }
    )
    if applied:
        failures.append(
            f"the R2 mutation payload records mutants produced by {applied} "
            f"while judgment.resolved.language is {language!r}; a run cannot "
            f"apply a catalogue belonging to another language"
        )


def _check_base_matches_the_tiers_present(judgment: dict, failures: list[str]) -> None:
    """(P33/A-223a/A-227) ``judgment.resolved.base`` is required IF ``r1`` or
    ``r2`` is present and forbidden UNLESS one of them is.

    The producer cannot reach the forbidden half -- A-062 refuses
    ``judge.base`` on an ``R0,R3`` lane as inert config -- but this verifier
    exists for FOREIGN documents, which is exactly the population that can.
    """
    resolved = judgment.get("resolved")
    if not isinstance(resolved, dict):
        return
    compares_a_base = "r1" in judgment or "r2" in judgment
    has_base = "base" in resolved
    if compares_a_base and not has_base:
        failures.append(
            "judgment.resolved omits base while judgment records an r1 or r2 "
            "tier, both of which judge a diff against a resolved comparison "
            "commit"
        )
    if not compares_a_base and has_base:
        failures.append(
            "judgment.resolved records a base although judgment carries "
            "neither r1 nor r2; no tier present here reads a comparison commit"
        )


def _check_equivalence_pairing(
    claim: dict, judgment_r2: dict, failures: list[str]
) -> None:
    """(P33/V5-3, invariant 2) equivalent entries require a declared
    ``equivalence_artifact``.

    Equivalence is established by byte comparison of that artifact against
    the baseline run's, so with none declared the claim was inferred from
    something else -- and "inferred from something else" is the whole thing
    A-209's both-present-or-both-absent pattern refuses.
    """
    mutation = _mutation_of(claim)
    if not isinstance(mutation, dict):
        return
    equivalent = mutation.get("equivalent")
    if not isinstance(equivalent, list) or not equivalent:
        return
    if "equivalence_artifact" not in judgment_r2:
        failures.append(
            f"the R2 payload claims {len(equivalent)} provably-inert mutant(s) "
            f"while judgment.r2 declares no equivalence_artifact to have "
            f"compared them against"
        )


def _check_kill_attribution(
    claim: dict, judgment_r2: dict, failures: list[str]
) -> None:
    """(P33/V5-4, invariant 4) attribution consistency, one clause at a time.

    Three separate failures rather than one, because they are three separate
    defects: an attribution whose declared source is missing, an attributed
    run that left some kill unexplained, and an unattributed run carrying a
    signal it could not have observed. A single collapsed diagnostic would
    make a document violating two of them look like it violated one.
    """
    attribution = judgment_r2.get("kill_attribution")
    artifact_declared = "kill_signal_artifact" in judgment_r2
    if attribution == "declared" and not artifact_declared:
        failures.append(
            "judgment.r2 claims attributed kills but names no "
            "kill_signal_artifact for them to have been read from"
        )
    if attribution == "unattributed" and artifact_declared:
        failures.append(
            "judgment.r2 names a kill_signal_artifact while reporting kills as "
            "unattributed; the attribution value is derived from that field's "
            "presence, so the two cannot disagree"
        )
    mutation = _mutation_of(claim)
    entries = _mutant_entries(mutation)
    if attribution == "declared":
        unexplained = [
            entry.get("path")
            for name, entry in entries
            if name == "killed" and "kill_signal" not in entry
        ]
        if unexplained:
            failures.append(
                f"an attributed R2 run leaves killed mutant(s) in "
                f"{sorted(set(unexplained))} with no kill_signal; attribution "
                f"that covers only some kills is not attribution"
            )
    elif attribution == "unattributed":
        signalled = sorted(
            {name for name, entry in entries if "kill_signal" in entry}
        )
        if signalled:
            failures.append(
                f"an unattributed R2 run records a kill_signal in bucket(s) "
                f"{signalled}; observing an exit status supplies no mechanism "
                f"to name"
            )


def _check_helpers_have_a_judged_claim(document: dict, failures: list[str]) -> None:
    """(P33/V5-5, A-223c/A-227) every ``helpers`` entry requires a
    correspondingly-judged claim.

    Only the OBSERVABLE direction. The converse -- a claim produced with a
    helper requires an entry -- has no readable antecedent in the artifact
    bytes, so implementing it here would be vacuous; P34 owns it, with the
    adapter that makes the state reachable.
    """
    helpers = document.get("helpers")
    if not isinstance(helpers, list):
        return
    if not helpers:
        failures.append(
            "helpers is present but empty; a run that invoked no helper omits "
            "the array rather than recording an empty one"
        )
        return
    claims = document.get("claims")
    claims = claims if isinstance(claims, list) else []
    def judged(rigor: str, payload: str) -> bool:
        return any(
            isinstance(item, dict) and item.get("rigor") == rigor and payload in item
            for item in claims
        )
    r1_judged = judged("R1", "coverage")
    r2_judged = judged("R2", "mutation")
    needs = {
        "statement-positions": r1_judged,
        "mutation-sites": r2_judged,
        "executable-code": r1_judged or r2_judged,
    }
    for helper in helpers:
        if not isinstance(helper, dict):
            continue
        role = helper.get("role")
        if role in needs and not needs[role]:
            failures.append(
                f"a helper with role {role!r} is recorded, but no claim in this "
                f"verdict carries the payload such a helper would have produced"
            )


def _check_a_judged_status_carries_its_own_payload(document: dict, failures: list[str]) -> None:
    """(A-251) A judged status must carry the payload it was judged FROM.

    The converse of :func:`_check_mutation_payload_shapes`' first rule: that
    forbids a payload where nothing was measured, this forbids a judged status
    where no payload was measured. Deleting a payload -- rather than
    contradicting it -- makes every re-derivation below return with nothing to
    judge while the rollup still agrees, so a ``PASS`` backed by no evidence at
    all was accepted. That is the strongest form of the lie P16 exists to catch,
    and it is the *gate-defeating* direction.

    Mirrors :meth:`assay.verdict.Claim.
    _check_a_judged_status_carries_its_own_payload` and
    ``verdict._MUTATION_ONLY_REASON_CODES`` VERBATIM -- transcribed as a table
    rather than hand-picked, because scope is exactly what the first attempt at
    this got wrong (A-240 refused an overbroad branch set and drew the wrong
    conclusion from the breakage). Two shapes must stay LEGAL and are not
    listed:

    * a payload-free R2 **FAIL** -- A-116's own truthful propagation, the R2
      claim reusing a failed baseline's ``(outcome, reason_code)`` verbatim;
    * a payload-free R1/R2/R3 ``ERROR``/``NO_MEASUREMENT``/``BUDGET_EXCEEDED``
      -- those describe the machinery failing to produce a result at all, not a
      judgement of one.

    Worded differently from both the model's messages and the schema's, for the
    reason P16 recorded: identical wording makes "is this raw branch even
    reached" untestable by message content alone.
    """
    claims = document.get("claims")
    if not isinstance(claims, list):
        return
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        rigor = claim.get("rigor")
        status = claim.get("status")
        for level, payload, judged in _JUDGED_STATUS_REQUIRES_PAYLOAD:
            if rigor == level and status in judged and payload not in claim:
                failures.append(
                    f"the {level} claim reports {status} with no {payload} "
                    f"payload; that status is a judgement OF one, so nothing "
                    f"but its own producer could re-derive it"
                )
        if rigor == "R2" and "mutation" not in claim:
            reason = claim.get("reason_code")
            if reason in _MUTATION_ONLY_REASON_CODE_NAMES:
                failures.append(
                    f"the R2 claim reports {reason} with no mutation payload; "
                    f"that reason code is read off the mutation buckets and "
                    f"nowhere else, so a run that never got as far as mutants "
                    f"could not have produced it"
                )


def _check_interval_is_ordered(document: dict, failures: list[str]) -> None:
    """(P21/A-182, work item 7) ``ended >= started`` on the RAW document.

    Parsed as offset-aware INSTANTS, never compared as strings: the
    canonical combined artifact runs ``12:00+01:00`` -> ``11:01+00:00``,
    which is a valid one-minute window whose lexical order is backwards, so
    a string comparison would reject a correct artifact. Draft 2020-12 has
    no ``$data`` and therefore cannot express this at all -- the schema is
    deliberately not credited with it, and this check plus the model's own
    are the only two places it exists.
    """
    started_raw = document.get("started")
    ended_raw = document.get("ended")
    if not isinstance(started_raw, str) or not isinstance(ended_raw, str):
        return
    moments: dict[str, datetime] = {}
    for name, value in (("started", started_raw), ("ended", ended_raw)):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            failures.append(f"{name} {value!r} is not a parseable timestamp")
            continue
        if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
            failures.append(f"{name} {value!r} has no UTC offset to place it on a timeline")
            continue
        moments[name] = parsed
    if len(moments) == 2 and moments["ended"] < moments["started"]:
        failures.append(
            f"the recorded interval runs backwards: ended {ended_raw!r} is an "
            f"earlier instant than started {started_raw!r} once both offsets "
            f"are applied"
        )


def _mutation_of(claim: Any) -> Any:
    return claim.get("mutation") if isinstance(claim, dict) else None


def _check_mutation_payload_shapes(document: dict, failures: list[str]) -> None:
    """(P21/A-163/A-183) the R2 terminal-versus-payload rules, re-derived on
    the RAW document and worded differently from the model's own messages.

    Same reasoning P16 recorded for the ``judgment.r1`` correspondence: a
    mutation test proved that identical wording makes the raw check's own
    failure indistinguishable from reconstruction catching the same defect,
    which would leave "is this raw branch even reached" untestable by
    message content alone.

    Four rules, matching :meth:`assay.verdict.Claim.
    _check_mutation_terminal_correspondence` and
    :meth:`assay.verdict.Verdict._check_mutation_cardinality` in substance
    and in nothing else.
    """
    claims = document.get("claims")
    if not isinstance(claims, list):
        return
    claim = next(
        (item for item in claims if isinstance(item, dict) and item.get("rigor") == "R2"),
        None,
    )
    if claim is None:
        return
    reason = claim.get("reason_code")
    mutation = _mutation_of(claim)

    if reason in ("MUTATION_UNSUPPORTED", "MUTATION_DISCOVERY_FAILED") and mutation is not None:
        failures.append(
            f"the R2 claim reports {reason} yet still attaches a mutation "
            f"payload; that terminal means no candidate set was ever produced"
        )
    if reason == "NO_MUTANTS" and mutation is None:
        failures.append(
            "the R2 claim reports NO_MUTANTS with no mutation payload; a "
            "supported analysis that observed nothing still records its "
            "zero/zero result, and omitting it is indistinguishable from an "
            "adapter that cannot analyse the language at all"
        )
    if not isinstance(mutation, dict):
        return

    total = mutation.get("total")
    candidate_count = mutation.get("candidate_count")
    # P33/A-228: `equivalent` is an ATTEMPTED mutant like any other -- it was
    # built, submitted and run; what it failed to do is change anything. So
    # it counts here, and only the mutation SCORE excludes it.
    buckets = MUTATION_BUCKETS
    sizes = [
        len(mutation[name]) for name in buckets if isinstance(mutation.get(name), list)
    ]
    if len(sizes) == len(buckets) and isinstance(total, int) and not isinstance(total, bool):
        if total != sum(sizes):
            failures.append(
                f"the R2 mutation payload says {total} attempted mutant(s) but "
                f"lists {sum(sizes)} identity/identities across its five buckets"
            )
    _check_identities_are_unique(mutation, buckets, failures)

    policy = document.get("judgment")
    policy_r2 = policy.get("r2") if isinstance(policy, dict) else None
    max_mutants = policy_r2.get("max_mutants") if isinstance(policy_r2, dict) else None
    if not isinstance(candidate_count, int) or isinstance(candidate_count, bool):
        return
    if not isinstance(max_mutants, int) or isinstance(max_mutants, bool):
        return
    if total == 0 and candidate_count > 0:
        if candidate_count != max_mutants + 1:
            failures.append(
                f"the R2 payload refuses before submission after seeing "
                f"{candidate_count} candidate(s), which is not one more than "
                f"the declared ceiling of {max_mutants}; a refusal that does "
                f"not sit exactly at the cap proves nothing about the cap"
            )
        if reason != "MUTANT_LIMIT_EXCEEDED":
            failures.append(
                f"the R2 payload has the pre-submission refusal shape but the "
                f"claim reports {reason!r} rather than MUTANT_LIMIT_EXCEEDED"
            )
    elif isinstance(total, int) and not isinstance(total, bool) and total > max_mutants:
        failures.append(
            f"the R2 payload attempted {total} mutant(s) against a declared "
            f"ceiling of {max_mutants}; a completed run above the cap means "
            f"the cap bounded nothing"
        )


def _check_identities_are_unique(
    mutation: dict, buckets: tuple[str, ...], failures: list[str]
) -> None:
    """One mutant, one bucket (A-182) -- read off the raw identity tuples."""
    seen: dict[tuple, str] = {}
    for name in buckets:
        entries = mutation.get(name)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            identity = (
                entry.get("path"),
                entry.get("start_byte"),
                entry.get("end_byte"),
                entry.get("replacement_sha256"),
                entry.get("operator"),
            )
            start, end = entry.get("start_byte"), entry.get("end_byte")
            if isinstance(start, int) and isinstance(end, int) and end <= start:
                failures.append(
                    f"mutant identity {identity} spans no bytes: end_byte "
                    f"{end} does not follow start_byte {start}"
                )
            previous = seen.get(identity)
            if previous is not None:
                failures.append(
                    f"mutant identity {identity} is recorded in both "
                    f"{previous!r} and {name!r}; a mutant has one outcome"
                )
            seen[identity] = name


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
        exclusion_capability=raw["exclusion_capability"],
        missing_lines={key: frozenset(value) for key, value in raw["missing_lines"].items()},
        files_missing_coverage=tuple(raw["files_missing_coverage"]),
        unclassified_lines={
            key: frozenset(value) for key, value in raw["unclassified_lines"].items()
        },
        files_with_unclassified_lines=tuple(raw["files_with_unclassified_lines"]),
        excluded_lines={
            key: frozenset(value) for key, value in raw["excluded_lines"].items()
        },
        files_with_excluded_lines=tuple(raw["files_with_excluded_lines"]),
    )
    _reject_unknown_keys(raw, coverage.to_dict(), "coverage")
    return coverage


def _reconstruct_judgment_resolved(raw: dict) -> JudgmentResolved:
    resolved = JudgmentResolved(
        language=raw["language"],
        source_roots=tuple(raw["source_roots"]),
        base=raw.get("base"),
    )
    _reject_unknown_keys(raw, resolved.to_dict(), "judgment.resolved")
    return resolved


def _reconstruct_judgment_r1(raw: dict) -> JudgmentR1:
    r1 = JudgmentR1(
        coverage_format=raw["coverage_format"],
        coverage_artifact=raw["coverage_artifact"],
        fail_under=raw["fail_under"],
        allow_excluded=raw["allow_excluded"],
    )
    _reject_unknown_keys(raw, r1.to_dict(), "judgment.r1")
    return r1


def _reconstruct_judgment_r2(raw: dict) -> JudgmentR2:
    r2 = JudgmentR2(
        jobs=raw["jobs"],
        max_mutants=raw["max_mutants"],
        operators=tuple(raw["operators"]),
        kill_attribution=raw["kill_attribution"],
        kill_signal_artifact=raw.get("kill_signal_artifact"),
        equivalence_artifact=raw.get("equivalence_artifact"),
    )
    _reject_unknown_keys(raw, r2.to_dict(), "judgment.r2")
    return r2


def _reconstruct_judgment_r3(raw: dict) -> JudgmentR3:
    r3 = JudgmentR3(mechanism=raw["mechanism"], target=raw["target"])
    _reject_unknown_keys(raw, r3.to_dict(), "judgment.r3")
    return r3


def _reconstruct_judgment(raw: dict) -> Judgment:
    judgment = Judgment(
        resolved=_reconstruct_judgment_resolved(raw["resolved"]),
        r1=_reconstruct_judgment_r1(raw["r1"]) if "r1" in raw else None,
        r2=_reconstruct_judgment_r2(raw["r2"]) if "r2" in raw else None,
        r3=_reconstruct_judgment_r3(raw["r3"]) if "r3" in raw else None,
    )
    _reject_unknown_keys(raw, judgment.to_dict(), "judgment")
    return judgment


def _reconstruct_canary(raw: dict) -> CanaryResult:
    transformed = raw.get("transformed_outcome")
    expected_rc = raw.get("expected_reason_code")
    observed_rc = raw.get("observed_reason_code")
    canary = CanaryResult(
        mechanism=raw["mechanism"],
        target=raw["target"],
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
        start_byte=raw["start_byte"],
        end_byte=raw["end_byte"],
        replacement_sha256=raw["replacement_sha256"],
        operator=raw["operator"],
        description=raw["description"],
        kill_signal=raw.get("kill_signal"),
    )
    _reject_unknown_keys(raw, item.to_dict(), "mutant outcome")
    return item


def _reconstruct_mutation(raw: dict) -> Mutation:
    mutation = Mutation(
        candidate_count=raw["candidate_count"],
        total=raw["total"],
        **{
            name: tuple(_reconstruct_mutant_outcome(item) for item in raw[name])
            for name in MUTATION_BUCKETS
        },
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


def _reconstruct_helper(raw: dict) -> Helper:
    helper = Helper(
        role=raw["role"],
        tool=raw["tool"],
        resolved_path=raw["resolved_path"],
        identity=raw["identity"],
    )
    _reject_unknown_keys(raw, helper.to_dict(), "helpers entry")
    return helper


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
            scope=document["scope"],
            enforcement=document["enforcement"],
        )

    judgment_kwargs: dict[str, Any] = {}
    if "judgment" in document:
        judgment_kwargs["judgment"] = _reconstruct_judgment(document["judgment"])
    if "helpers" in document:
        judgment_kwargs["helpers"] = tuple(
            _reconstruct_helper(item) for item in document["helpers"]
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
        **judgment_kwargs,
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


#: Handed to :func:`assay.mutation.judge_mutation` on the branch where its
#: own early return proves it never reads the argument. Deliberately NOT a
#: plausible-looking baseline: if that proof ever stops holding, an
#: ``AttributeError`` here is a loud, immediate failure rather than a
#: silently wrong re-derivation against a fabricated outcome.
_BASELINE_NEVER_READ = object()

#: (P21 phase-2 review) The payload-free R2 terminals a producer reaches for
#: R2's OWN reason, independently of whether the baseline passed.
#:
#: ``judge_mutation``'s ``mutation is None`` branch propagates the baseline's
#: own ``(outcome, reason_code)`` verbatim, which is correct for exactly one
#: cause: mutation testing never began because the lane's command did not
#: PASS (A-116). But ``run_lane`` renders SIX other payload-free R2 claims
#: from a caught :class:`~assay.errors.AssayError` while R0 passed, and each
#: was being compared against that passing baseline and rejected -- so
#: ``assay run`` emitted artifacts its own ``assay verify`` refused. The one
#: this package itself makes reachable is the discovery boundary; the rest
#: predate it on the same branch:
#:
#: * ``MUTATION_DISCOVERY_FAILED`` -- ``run_mutation``'s discovery boundary
#:   (P21 work item 4 / A-171), reachable today for unparseable Python.
#: * ``DIRTY_TREE`` / ``HEAD_CHANGED`` -- the post-command refusal preserves
#:   the real R0 claim and marks each higher declared claim (A-175/A-178).
#: * ``DIRTY_TREE`` / ``BASE_IS_HEAD`` -- R2's own measurability guards.
#: * ``GIT_FAILED`` -- the diff R2 resolves its targets from.
#: * ``UNREADABLE_ARTIFACT`` -- the bounded source read during target
#:   resolution (P20 work item 5).
#:
#: This is NOT a licence to accept any pairing: the STATUS is re-derived from
#: the closed vocabulary below rather than taken from the artifact, so a
#: forged ``FAIL``/``DIRTY_TREE`` or ``PASS``/``BASE_IS_HEAD`` is still
#: rejected. ``PASS`` additionally cannot reach here at all -- a payload-free
#: R2 ``PASS`` is unconstructible (``Claim.
#: _check_a_judged_status_carries_its_own_payload``).
#:
#: ``refuse_lane``'s ``BAD_LANE_CONFIG`` is deliberately absent: it renders
#: the IDENTICAL pair on every declared level including R0, so the ordinary
#: baseline comparison below already re-derives it correctly.
#:
#: (P33/A-228) ``ALL_MUTANTS_EQUIVALENT`` is deliberately absent too, and for
#: the opposite reason to ``BAD_LANE_CONFIG``'s: this set exists for
#: PAYLOAD-FREE R2 terminals, and the new one is the reverse -- it is read
#: off the ``equivalent``/``killed``/``survived`` buckets and cannot exist
#: without them (``_MUTATION_ONLY_REASON_CODES`` refuses the payload-free
#: form at construction, and the schema pins it to a ``mutation`` payload and
#: rigor R2). So it never reaches the ``claim.mutation is None`` branch this
#: set guards; it is re-derived by ``judge_mutation`` below like every other
#: bucket-borne status. Adding it here would advertise a payload-free
#: spelling that no layer accepts.
_INDEPENDENT_R2_TERMINALS: frozenset[ReasonCode] = frozenset(
    {
        ReasonCode.MUTATION_DISCOVERY_FAILED,
        ReasonCode.DIRTY_TREE,
        ReasonCode.HEAD_CHANGED,
        ReasonCode.BASE_IS_HEAD,
        ReasonCode.GIT_FAILED,
        ReasonCode.UNREADABLE_ARTIFACT,
    }
)

#: (A-245, closing A-241) The subset of the above that is independent of the
#: baseline's REASON but not of whether the baseline PASSED at all.
#:
#: ``_INDEPENDENT_R2_TERMINALS`` stopped after re-deriving the OUTCOME, so a
#: payload-free R2 claim could name any of the six beside any baseline -- and
#: three of them are producible only on a branch ``run_lane`` reaches after
#: the command PASSED, because target resolution and mutation discovery are
#: both inside ``if r2_declared and result.outcome is Outcome.PASS``
#: (``runner.py``); the not-PASS arm renders
#: ``build_mutation_claim(result, None)``, which propagates the baseline's own
#: pair verbatim (A-116). So one of these three beside a non-passing baseline
#: is a misreported reason code, and that was accepted.
#:
#: The other three are deliberately NOT here, each with a producer path that
#: renders it beside a baseline that did NOT pass -- proven by driving
#: ``run_lane``, not by reading it (``tests/test_runner_p23_cleanup_and_
#: budget.py``, ``tests/test_verify_layer_independence.py``):
#:
#: * ``GIT_FAILED`` -- ``_replace_highest_higher_rigor_claim_with_git_failed``
#:   runs on outer-scratch/snapshot cleanup failure and leaves EVERY lower
#:   completed claim (including a failed R0) exactly as it was (A-193/A-194).
#: * ``DIRTY_TREE`` / ``HEAD_CHANGED`` -- the post-command refusal keeps the
#:   REAL R0 claim and marks every other declared level (A-195/A-178), so
#:   ``R0 = FAIL/COMMAND_FAILED`` beside ``R2 = NO_MEASUREMENT/DIRTY_TREE`` is
#:   a truthful artifact.
#:
#: A-241's own headline example was ``ERROR``/``GIT_FAILED`` against an
#: ``ERROR``/``EXEC_FAILED`` baseline. That example is WRONG -- it is exactly
#: the cleanup path above and is legitimate; the gap is real for the other
#: three. Recorded here because the wrong example is the more memorable half.
_POST_BASELINE_R2_TERMINALS: frozenset[ReasonCode] = frozenset(
    {
        ReasonCode.MUTATION_DISCOVERY_FAILED,
        ReasonCode.BASE_IS_HEAD,
        ReasonCode.UNREADABLE_ARTIFACT,
    }
)


def _outcome_owning(reason_code: ReasonCode) -> Outcome | None:
    """The single :class:`Outcome` the closed vocabulary binds *reason_code*
    to, or ``None`` if it is claimed by none.

    DERIVED from ``REASON_CODES`` rather than transcribed: every reason code
    belongs to exactly one outcome (``tests/test_errors.py`` proves the
    partition), so re-deriving the status is a fact lookup, not a second
    hand-maintained table that could drift from the vocabulary it checks.
    """
    for outcome, codes in REASON_CODES.items():
        if reason_code in codes:
            return outcome
    return None


def _fmt(outcome: Outcome, reason_code: ReasonCode | None) -> str:
    return f"({outcome.value}, {reason_code.value if reason_code is not None else None})"


def _check_r1_rederivation(verdict: Verdict, failures: list[str]) -> None:
    """P16 (sol finding 2, O2's first reproduction): re-derive the R1
    claim's status from its ``coverage`` payload plus ``judgment.r1``'s
    policy — never merely trust that the producer-selected status agrees
    with its own evidence. Mirrors :mod:`assay.evaluate`'s own precedence
    (unclassified > disallowed-excluded > uncovered-floor > pass) restated
    directly, rather than importing that module — see the module docstring
    for why.

    A missing ``coverage`` payload means nothing to re-derive: a legitimate
    non-R1-judged claim (NO_MEASUREMENT, or no R1 declared at all). When
    ``coverage`` IS present, ``verdict.judgment.r1`` is guaranteed present
    too -- :meth:`~assay.verdict.Verdict._check_judgment_matches_claims`
    already refuses to construct a ``Verdict`` (real OR reconstructed;
    both go through the same ``__init__``) where an R1 claim carries
    ``coverage`` but ``judgment.r1`` is absent, so a second None-check here
    would only guard an unreachable branch.
    """
    claim = next((item for item in verdict.claims if item.rigor == "R1"), None)
    if claim is None or claim.coverage is None:
        return
    policy = verdict.judgment.r1
    coverage = claim.coverage
    if coverage.unclassified_lines:
        expected: tuple[Outcome, ReasonCode | None] = (
            Outcome.FAIL,
            ReasonCode.UNCLASSIFIED_LINES,
        )
    elif coverage.excluded_lines and not policy.allow_excluded:
        expected = (Outcome.FAIL, ReasonCode.EXCLUDED_LINES)
    elif coverage.pct < policy.fail_under:
        expected = (Outcome.FAIL, ReasonCode.UNCOVERED_LINES)
    else:
        expected = (Outcome.PASS, None)
    if (claim.status, claim.reason_code) != expected:
        failures.append(
            f"R1 claim status {_fmt(claim.status, claim.reason_code)} "
            f"disagrees with the re-derived judgment from coverage plus "
            f"policy {_fmt(*expected)}"
        )


def _check_r2_rederivation(verdict: Verdict, failures: list[str]) -> None:
    """Re-derive the R2 claim's status from its ``mutation`` payload,
    reusing :func:`assay.mutation.judge_mutation`'s own bucket precedence
    directly rather than copying its mapping.

    ``judge_mutation``'s ``baseline`` parameter is typed as a
    ``runner.CommandResult``, but its body reads ``.outcome``/
    ``.reason_code`` ONLY on the ``mutation is None`` branch (confirmed by
    reading its source: the very first statement is
    ``if mutation is None: return baseline.outcome, baseline.reason_code``,
    an early return — when ``mutation`` is not ``None``, ``baseline`` is
    never touched). A minimal stand-in exposing exactly those two
    attributes, built from the artifact's own R0 claim, is therefore
    behaviourally exact and avoids pulling ``runner.py``'s own
    subprocess-executing import chain into an artifact validator that never
    re-runs a lane.

    That stand-in is needed ONLY for the payload-less branch. A REAL
    ``mutation`` payload is re-judged whether or not the artifact carries an
    R0 claim at all — ``rigor = ["R2"]`` is a legal lane declaration
    (:mod:`assay.config` requires only a non-empty subset of R0-R3), so
    returning early on a missing R0 sibling would have let sol finding 2's
    second contradictory artifact — a ``PASS`` with a genuine surviving
    mutant — pass unexamined simply by not declaring R0 (P16 review).
    """
    claim = next((item for item in verdict.claims if item.rigor == "R2"), None)
    if claim is None:
        return
    if claim.reason_code is ReasonCode.MUTATION_UNSUPPORTED:
        # P21/A-183: capability absence is not derivable from a baseline --
        # the producer reaches it from the adapter's own marker, so the
        # honest re-derivation feeds that marker through the SAME shared
        # mapping. This is not tautological: it binds the recorded STATUS to
        # the recorded reason, so a forged `FAIL`/`MUTATION_UNSUPPORTED` (or
        # any other pairing) is caught here rather than only by the model.
        expected = judge_mutation(_BASELINE_NEVER_READ, "UNSUPPORTED")
        if (claim.status, claim.reason_code) != expected:
            failures.append(
                f"R2 claim status {_fmt(claim.status, claim.reason_code)} "
                f"disagrees with the re-derived judgment for an adapter with "
                f"no mutation implementation {_fmt(*expected)}"
            )
        return
    if claim.mutation is None and claim.reason_code in _INDEPENDENT_R2_TERMINALS:
        # (P21 phase-2 review) R2 refused for its OWN reason while the lane's
        # command may well have passed, so the baseline is not what decided
        # this claim and comparing against it rejects a truthful artifact.
        # The status is still re-derived -- from the closed vocabulary, not
        # from the document -- so only the one pairing the product can
        # actually emit is accepted.
        expected_outcome = _outcome_owning(claim.reason_code)
        if claim.status is not expected_outcome:
            failures.append(
                f"R2 claim status {_fmt(claim.status, claim.reason_code)} "
                f"pairs a refusal reason with an outcome the closed "
                f"vocabulary does not bind it to; "
                f"{claim.reason_code.value} is "
                f"{'no outcome' if expected_outcome is None else expected_outcome.value}"
            )
        # (A-245, closing A-241) "Independent of the baseline's REASON" is not
        # "independent of whether the baseline ran". For the three terminals
        # only a PASSING baseline can lead to, a non-passing R0 sibling means
        # the producer would have propagated ITS pair verbatim instead
        # (A-116) -- so the recorded reason code is a misreport, and only the
        # OUTCOME agreeing was hiding it.
        #
        # The equal-pair escape is not redundant: a whole-lane refusal
        # (`_refuse_lane_with_plan`) renders the IDENTICAL pair on every
        # declared level, R0 included, which is verbatim propagation by
        # construction. Accepting it keeps this check fail-safe against any
        # refusal path whose reason lands in this set.
        if claim.reason_code in _POST_BASELINE_R2_TERMINALS:
            r0_sibling = next(
                (item for item in verdict.claims if item.rigor == "R0"), None
            )
            if (
                r0_sibling is not None
                and r0_sibling.status is not Outcome.PASS
                and (r0_sibling.status, r0_sibling.reason_code)
                != (claim.status, claim.reason_code)
            ):
                failures.append(
                    f"R2 claim status {_fmt(claim.status, claim.reason_code)} "
                    f"names a refusal reachable only after a PASSING "
                    f"baseline, but the R0 claim is "
                    f"{_fmt(r0_sibling.status, r0_sibling.reason_code)}: a "
                    f"payload-free R2 claim beside a baseline that did not "
                    f"pass must reuse the baseline's own pair verbatim"
                )
        return
    if claim.mutation is None:
        r0_claim = next((item for item in verdict.claims if item.rigor == "R0"), None)
        if r0_claim is None:
            # Nothing in the artifact records the baseline this claim reused,
            # so there is no honest comparison to make. The one status that
            # would be a contradiction regardless -- PASS -- is already
            # impossible to construct
            # (:meth:`assay.verdict.Claim._check_a_judged_status_carries_its_own_payload`).
            return
        baseline: Any = SimpleNamespace(
            outcome=r0_claim.status, reason_code=r0_claim.reason_code
        )
    else:
        # Never read on this branch; named so, rather than fabricated from a
        # claim that has nothing to do with it.
        baseline = _BASELINE_NEVER_READ
    expected = judge_mutation(baseline, claim.mutation)
    if (claim.status, claim.reason_code) != expected:
        failures.append(
            f"R2 claim status {_fmt(claim.status, claim.reason_code)} "
            f"disagrees with the re-derived judgment from mutation buckets "
            f"{_fmt(*expected)}"
        )


def _check_r3_rederivation(verdict: Verdict, failures: list[str]) -> None:
    """Re-derive the R3 claim's status from its ``canary`` payload, reusing
    :func:`assay.canary.judge_canary` directly — it is already pure over an
    already-reconstructed :class:`~assay.verdict.CanaryResult`, needing no
    lane/process/filesystem state.
    """
    claim = next((item for item in verdict.claims if item.rigor == "R3"), None)
    if claim is None or claim.canary is None:
        return
    expected = judge_canary(claim.canary)
    if (claim.status, claim.reason_code) != expected:
        failures.append(
            f"R3 claim status {_fmt(claim.status, claim.reason_code)} "
            f"disagrees with the re-derived judgment from the canary "
            f"result {_fmt(*expected)}"
        )


def verify_document(document: Any) -> list[str]:
    """Every reason *document* is not a valid, internally self-consistent
    verdict artifact. Empty means valid — the only condition under which
    :func:`cmd_verify` exits 0.
    """
    if not isinstance(document, dict):
        return [f"artifact must be a JSON object, got {type(document).__name__}"]

    failures: list[str] = []

    # P21/A-182: the version is checked FIRST, immediately after the
    # top-level object check and BEFORE required-field or foreign-shape
    # inspection. A sparse v2 artifact otherwise reports its missing v4
    # fields alongside the version, burying the one actionable sentence
    # under a pile of consequences of it.
    version = document.get("schema_version")
    if "schema_version" in document and version != VERDICT_SCHEMA_VERSION:
        # P16 work item 7: a foreign version is reported AS a version
        # problem and nothing else. Every later check reads a shape this
        # verifier was never written against, so continuing would bury the
        # one actionable sentence under a pile of consequences of it -- a
        # real v2 artifact otherwise reports `schema: 'excluded_lines'`,
        # a bare KeyError naming a field its producer had never heard of.
        # The artifact is REJECTED, never coerced or upgraded in place:
        # re-produce it with a matching assay (docs/DESIGN-GUIDE.md §6).
        return failures + [
            f"schema_version {version!r} is not this verifier's version "
            f"{VERDICT_SCHEMA_VERSION}: a verdict artifact is rejected, "
            f"never upgraded in place -- re-produce it with an assay whose "
            f"VERDICT_SCHEMA_VERSION is {VERDICT_SCHEMA_VERSION}"
        ]

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
    _check_interval_is_ordered(document, failures)
    _check_lane_resolved_group(document, failures)
    _check_claims_cover_declared_rigor(document, failures)
    _check_evidence_covers_declared_evidence(document, failures)
    _check_outcome_agrees_with_rollup(document, outcome, failures)
    _check_judgment_matches_claims(document, failures)
    _check_mutation_payload_shapes(document, failures)
    _check_a_judged_status_carries_its_own_payload(document, failures)
    _check_helpers_have_a_judged_claim(document, failures)

    try:
        verdict = _reconstruct_verdict(document)
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        failures.append(f"schema: {exc}")
    else:
        # Stage 3 (P16): only meaningful once reconstruction produced a
        # real Verdict — re-deriving status from garbage/partial data would
        # only add a confusing second failure alongside the schema one.
        _check_r1_rederivation(verdict, failures)
        _check_r2_rederivation(verdict, failures)
        _check_r3_rederivation(verdict, failures)

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
