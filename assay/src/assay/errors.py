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
    EXCLUDED_LINES = "EXCLUDED_LINES"
    UNCLASSIFIED_LINES = "UNCLASSIFIED_LINES"
    MUTANTS_SURVIVED = "MUTANTS_SURVIVED"
    CANARY_SURVIVED = "CANARY_SURVIVED"
    COMMAND_FAILED = "COMMAND_FAILED"
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
    #: (P21/A-163, RESERVED for P27) a declared adapter prerequisite named in
    #: `LanguageAdapter.external_tools` is absent, so the check it gates
    #: cannot run. Reserved by the one pre-adoption schema migration so P27
    #: can render it without a second version bump (A-013/A-086/A-144).
    MISSING_EXTERNAL_TOOL = "MISSING_EXTERNAL_TOOL"
    # BUDGET_EXCEEDED
    LANE_TIMEOUT = "LANE_TIMEOUT"
    #: (P21/A-163) candidate discovery reached the declared `max_mutants`
    #: ceiling: exactly `max_mutants + 1` candidates were observed and assay
    #: deliberately stopped. A refusal BEFORE submission, never a truncated
    #: sample -- the sentinel payload proves zero mutants were attempted.
    MUTANT_LIMIT_EXCEEDED = "MUTANT_LIMIT_EXCEEDED"
    #: (P21/A-163, RESERVED for P22) a committed-object snapshot exceeded its
    #: declared bound. Reserved here for the same one-migration reason
    #: `MISSING_EXTERNAL_TOOL` is.
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
                ReasonCode.EXCLUDED_LINES,
                ReasonCode.UNCLASSIFIED_LINES,
                ReasonCode.MUTANTS_SURVIVED,
                ReasonCode.CANARY_SURVIVED,
                ReasonCode.COMMAND_FAILED,
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
                ReasonCode.DIRTY_TREE,
                ReasonCode.HEAD_CHANGED,
                ReasonCode.BASE_IS_HEAD,
                ReasonCode.EMPTY_COVERAGE,
                ReasonCode.MISSING_ATTESTATION,
                ReasonCode.STALE_ATTESTATION,
                ReasonCode.MISSING_EXTERNAL_TOOL,
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
