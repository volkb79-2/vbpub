"""Outcomes, reason codes, and the typed error every assay layer raises.

This module is the single home of two CLOSED vocabularies, taken verbatim from
`docs/DESIGN-GUIDE.md` §6 and frozen by A-021/A-022/A-050:

* :class:`Outcome` — the six exit codes. The **exit code is the verdict**: a
  consumer that checks only the exit code must never be wrong.
* :class:`ReasonCode` — the cause, so specificity does not require inventing
  new exit codes.

The enumeration is **closed** (A-050). An implementer who needs a code that is
not listed here stops and asks; they do not add one. :class:`AssayError`
enforces the pairing at construction time, so a wrong pairing is a crash in the
raising code rather than a wrong ``reason_code`` in a shipped artifact.

`PASS` carries no ``reason_code`` at all — omitted, never null (A-051), which is
why ``REASON_CODES[Outcome.PASS]`` is empty and constructing an ``AssayError``
with ``Outcome.PASS`` is a programming error.

This module imports nothing outside the stdlib and nothing from ``assay``, so
every other module may depend on it.
"""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

__all__ = [
    "AssayError",
    "EXIT_CODES",
    "LaneConfigError",
    "Outcome",
    "REASON_CODES",
    "ReasonCode",
]


class Outcome(StrEnum):
    """The six outcomes. 2-5 are all not-a-pass and all block a merge."""

    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    NO_MEASUREMENT = "NO_MEASUREMENT"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    INCONCLUSIVE = "INCONCLUSIVE"

    @property
    def exit_code(self) -> int:
        """The process exit code that carries this outcome."""
        return EXIT_CODES[self]


#: A-021. Frozen: renumbering these silently re-points every consumer's gate.
EXIT_CODES: Mapping[Outcome, int] = MappingProxyType(
    {
        Outcome.PASS: 0,
        Outcome.FAIL: 1,
        Outcome.ERROR: 2,
        Outcome.NO_MEASUREMENT: 3,
        Outcome.BUDGET_EXCEEDED: 4,
        Outcome.INCONCLUSIVE: 5,
    }
)


class ReasonCode(StrEnum):
    """The closed cause enumeration (DESIGN-GUIDE §6, A-022, A-050)."""

    # FAIL
    UNCOVERED_LINES = "UNCOVERED_LINES"
    #: (wave-1 §4, A-258) `pct < fail_under` with zero missing LINES and at
    #: least one uncovered branch arc. Ranked identically to
    #: `UNCOVERED_LINES` in the outcome precedence, but never the same
    #: sentence: "which mechanism refused" is the distinction this project
    #: exists to keep (B001's false-PASS story one layer up).
    UNCOVERED_BRANCHES = "UNCOVERED_BRANCHES"
    EXCLUDED_LINES = "EXCLUDED_LINES"
    UNCLASSIFIED_LINES = "UNCLASSIFIED_LINES"
    MUTANTS_SURVIVED = "MUTANTS_SURVIVED"
    CANARY_SURVIVED = "CANARY_SURVIVED"
    COMMAND_FAILED = "COMMAND_FAILED"
    #: (F015/M7, A-433 as amended by A-434/DA-R18) the R4 red-first claim's
    #: judged refusal: assay materialised both commits and ran the declared
    #: tests on both, and the ``fail-before/pass-after`` property did not
    #: hold -- either the test PASSED at the broken commit (it does not
    #: discriminate the fix) or it did not PASS at HEAD. Both halves take
    #: this one code; WHICH half is read off the claim's two recorded
    #: outcomes and its ``detail``. Deliberately a **FAIL**, not a
    #: ``NO_MEASUREMENT``: both runs completed, so assay measured something
    #: -- exactly ``CANARY_SURVIVED`` one rung down. Deliberately NOT
    #: ``COMMAND_FAILED``: that is R0's statement about the LANE's declared
    #: command, R3 likewise maps neither of its own two runs onto it, and on
    #: a claim whose before-run is *expected* to fail a bare
    #: ``COMMAND_FAILED`` could not say which side failed. Every failure of
    #: the MECHANISM itself (snapshot, overlay, deadline, unreadable output)
    #: keeps the code its own substrate already raises. **RESERVED at the
    #: v10 cut and RENDERED in phase 3** -- the ``MISSING_EXTERNAL_TOOL``
    #: pattern (A-013/A-086/A-144).
    RED_FIRST_UNPROVEN = "RED_FIRST_UNPROVEN"
    # ERROR
    GIT_FAILED = "GIT_FAILED"
    UNREADABLE_ARTIFACT = "UNREADABLE_ARTIFACT"
    FORMAT_MISMATCH = "FORMAT_MISMATCH"
    BAD_LANE_CONFIG = "BAD_LANE_CONFIG"
    EXEC_FAILED = "EXEC_FAILED"
    #: (P21/A-O14/A-181) assay could not write its OWN declared output
    #: artifact. Distinct from `UNREADABLE_ARTIFACT`, which is about reading
    #: an INPUT (coverage, attestation): "cannot write my own output" had no
    #: truthful code at all before v4, and surfaced as a bare `OSError` and
    #: exit 1, which a consumer reads as FAIL rather than as a tooling error.
    OUTPUT_WRITE_FAILED = "OUTPUT_WRITE_FAILED"
    #: (P21/A-171) a syntax-aware mutation-discovery boundary FAILED: invalid
    #: source the adapter genuinely cannot parse (P21, Python), or -- from
    #: P29 -- an invalid helper request/response, a nonzero helper, or an
    #: adapter that claims supported discovery and then contradicts itself.
    #: Never "the adapter has no mutation engine" (that is
    #: `MUTATION_UNSUPPORTED`) and never "a valid analysis found nothing"
    #: (that is `NO_MUTANTS`).
    MUTATION_DISCOVERY_FAILED = "MUTATION_DISCOVERY_FAILED"
    # NO_MEASUREMENT
    #: (wave-1 §4, A-259) `judge.require_branch = true` over an artifact
    #: whose branch capability is `"unavailable"` -- payload-free, guarded
    #: before any evaluation (beside `check_empty_coverage` in `evaluate_r1`'s
    #: own guard sequence, never inside `evaluate_coverage` itself: this is a
    #: measurability question, not an arithmetic one). Exists so dropping
    #: `--cov-branch` from an argv cannot silently downgrade a line+branch
    #: gate into a line-only one that still says PASS.
    BRANCH_UNAVAILABLE = "BRANCH_UNAVAILABLE"
    #: (wave-1 §5, A-260) B005's own anti-vacuity guard: a `whole_target`
    #: target absent from the coverage artifact, or present with zero
    #: executable lines. Payload-free -- the target was never measured, so
    #: there is nothing to judge. This is the terminal the argv stopgap it
    #: replaces silently fails to render (it reports `100%` of zero instead).
    TARGET_NOT_MEASURED = "TARGET_NOT_MEASURED"
    DIRTY_TREE = "DIRTY_TREE"
    #: (P21/A-178) the lane's command left a CLEAN tree but moved `HEAD`.
    #: P20 collapsed this into `DIRTY_TREE` to stay schema-v3-compatible,
    #: which is a false diagnosis for a command that commits: the tree is not
    #: dirty, the commit under measurement simply is not the one recorded.
    #: Dirt keeps precedence when both are true.
    HEAD_CHANGED = "HEAD_CHANGED"
    BASE_IS_HEAD = "BASE_IS_HEAD"
    EMPTY_COVERAGE = "EMPTY_COVERAGE"
    MISSING_ATTESTATION = "MISSING_ATTESTATION"
    STALE_ATTESTATION = "STALE_ATTESTATION"
    #: (P21/A-163) a declared adapter prerequisite named in
    #: `LanguageAdapter.external_tools` is absent from the effective PATH, so
    #: the check it gates cannot run. Reserved by the one pre-adoption schema
    #: migration so its eventual producer could render it without a second
    #: version bump (A-013/A-086/A-144) -- no longer reserved: P34/A-253
    #: lands the producer, `runner.run_lane`'s own preflight (A-284), before
    #: any snapshot, command or Git work.
    MISSING_EXTERNAL_TOOL = "MISSING_EXTERNAL_TOOL"
    #: (B004/A-276, rendered at schema v10 by A-430) assay could not
    #: establish WHICH artifact the lane's tests ran against: the adjudicated
    #: image-provenance document is absent, unreadable, carries an
    #: unaccepted ``schema_version``, or reports a mismatch. Payload-free --
    #: the discriminating detail lives in the input document (W2 carve §5.2)
    #: and, from v10, in the claim's ``detail``. `NO_MEASUREMENT` and never
    #: `FAIL` (carve §4.1): a provenance mismatch says the measurement's
    #: subject is unknown, not that the code is bad, and a `FAIL` would let
    #: a consumer read "the code is bad" off a deployment fact.
    PROVENANCE_UNVERIFIED = "PROVENANCE_UNVERIFIED"
    # BUDGET_EXCEEDED
    LANE_TIMEOUT = "LANE_TIMEOUT"
    #: (P21/A-163) candidate discovery reached the declared `max_mutants`
    #: ceiling: exactly `max_mutants + 1` candidates were observed and assay
    #: deliberately stopped. A refusal BEFORE submission, never a truncated
    #: sample -- the sentinel payload proves zero mutants were attempted.
    MUTANT_LIMIT_EXCEEDED = "MUTANT_LIMIT_EXCEEDED"
    #: (P21/A-163) a committed-object snapshot exceeded its declared bound.
    #: No longer reserved: P22/P23 landed both producers -- `isolation.py`'s
    #: `_limit_exceeded` and `git.py`'s object-closure bound raise it, and
    #: `tests/fixtures/verdicts/r0_budget_exceeded_snapshot_limit_exceeded.json`
    #: is the real artifact. `MISSING_EXTERNAL_TOOL` above is likewise no
    #: longer reserved as of P34/A-253; both are live.
    SNAPSHOT_LIMIT_EXCEEDED = "SNAPSHOT_LIMIT_EXCEEDED"
    # INCONCLUSIVE
    NO_MUTANTS = "NO_MUTANTS"
    #: (P21/A-183) the ADAPTER has no mutation implementation at all, so no
    #: candidate analysis ever happened. Payload-free, and deliberately NOT
    #: `NO_MUTANTS`: a zero/zero `Mutation` payload asserts that a supported
    #: analysis ran and observed nothing mutable, which is a measurement
    #: claim no Go analysis exists to support. Go carries this until P29
    #: lands real bounded sites; Python never renders it.
    MUTATION_UNSUPPORTED = "MUTATION_UNSUPPORTED"
    #: (P33/A-223d) every attempted mutant was PROVEN inert: `killed` and
    #: `survived` are both empty while `equivalent` is not. Deliberately not
    #: `PASS` -- a run in which nothing the suite could have caught was ever
    #: at risk proves nothing about the tests, so walking it to green would
    #: be A-026/A-035's 0/0-is-100% bug one layer down, reintroduced inside
    #: the very change that exists to fix a lossiness problem. Deliberately
    #: not `NO_MUTANTS` either: a supported analysis DID find candidates and
    #: DID run them, which is a different fact from finding none. Ranked
    #: after `survived` (A-117), and R2-only with a required payload, exactly
    #: like its three sibling terminals (A-228).
    ALL_MUTANTS_EQUIVALENT = "ALL_MUTANTS_EQUIVALENT"
    CANARY_INCONCLUSIVE = "CANARY_INCONCLUSIVE"


#: Which reason codes may accompany which outcome. A-050: closed, and PASS has
#: none because a pass has no cause to name.
REASON_CODES: Mapping[Outcome, frozenset[ReasonCode]] = MappingProxyType(
    {
        Outcome.PASS: frozenset(),
        Outcome.FAIL: frozenset(
            {
                ReasonCode.UNCOVERED_LINES,
                ReasonCode.UNCOVERED_BRANCHES,
                ReasonCode.EXCLUDED_LINES,
                ReasonCode.UNCLASSIFIED_LINES,
                ReasonCode.MUTANTS_SURVIVED,
                ReasonCode.CANARY_SURVIVED,
                ReasonCode.COMMAND_FAILED,
                ReasonCode.RED_FIRST_UNPROVEN,
            }
        ),
        Outcome.ERROR: frozenset(
            {
                ReasonCode.GIT_FAILED,
                ReasonCode.UNREADABLE_ARTIFACT,
                ReasonCode.FORMAT_MISMATCH,
                ReasonCode.BAD_LANE_CONFIG,
                ReasonCode.EXEC_FAILED,
                ReasonCode.OUTPUT_WRITE_FAILED,
                ReasonCode.MUTATION_DISCOVERY_FAILED,
            }
        ),
        Outcome.NO_MEASUREMENT: frozenset(
            {
                ReasonCode.BRANCH_UNAVAILABLE,
                ReasonCode.TARGET_NOT_MEASURED,
                ReasonCode.DIRTY_TREE,
                ReasonCode.HEAD_CHANGED,
                ReasonCode.BASE_IS_HEAD,
                ReasonCode.EMPTY_COVERAGE,
                ReasonCode.MISSING_ATTESTATION,
                ReasonCode.STALE_ATTESTATION,
                ReasonCode.MISSING_EXTERNAL_TOOL,
                ReasonCode.PROVENANCE_UNVERIFIED,
            }
        ),
        Outcome.BUDGET_EXCEEDED: frozenset(
            {
                ReasonCode.LANE_TIMEOUT,
                ReasonCode.MUTANT_LIMIT_EXCEEDED,
                ReasonCode.SNAPSHOT_LIMIT_EXCEEDED,
            }
        ),
        Outcome.INCONCLUSIVE: frozenset(
            {
                ReasonCode.NO_MUTANTS,
                ReasonCode.MUTATION_UNSUPPORTED,
                ReasonCode.ALL_MUTANTS_EQUIVALENT,
                ReasonCode.CANARY_INCONCLUSIVE,
            }
        ),
    }
)


class AssayError(Exception):
    """An assay failure that carries its own outcome and cause.

    Raising one is how a layer says *"this is the verdict"* without knowing
    whether it is running under the CLI, under a test, or inside a consumer.
    The CLI maps :attr:`exit_code` onto the process exit status.
    """

    def __init__(
        self, message: str, *, outcome: Outcome, reason_code: ReasonCode
    ) -> None:
        if outcome is Outcome.PASS:
            raise ValueError("an AssayError cannot carry Outcome.PASS")
        allowed = REASON_CODES[outcome]
        if reason_code not in allowed:
            raise ValueError(
                f"reason_code {reason_code} is not valid for outcome {outcome}; "
                f"valid: {sorted(allowed)}"
            )
        super().__init__(message)
        self.message = message
        self.outcome = outcome
        self.reason_code = reason_code

    @property
    def exit_code(self) -> int:
        return self.outcome.exit_code

    def __str__(self) -> str:
        return self.message


class LaneConfigError(AssayError):
    """The lane file is unusable: ``ERROR`` / ``BAD_LANE_CONFIG``.

    Every rejection in :mod:`assay.config` is one of these. The message names
    the file, the lane and the offending field, because a config error whose
    message does not say which field is wrong has moved the guess from the
    loader to the operator (AGENTS.md §4.2a).
    """

    def __init__(self, message: str) -> None:
        super().__init__(
            message, outcome=Outcome.ERROR, reason_code=ReasonCode.BAD_LANE_CONFIG
        )
