"""Typed agent-result envelope and its fail-closed reader (CR-03, DR-13).

WHY THIS MODULE EXISTS
----------------------
The merge gate currently reads its authority out of prose. `daemon.
_parse_review_verdict` runs a regex over every ``*REVIEW*.md`` committed to a
branch, pools the ``VERDICT:`` lines it finds, and decides whether a change
may be merged from what that pool looks like. Its own docstring records the
history: a reviewer that named the file ``P42-REVIEW.md`` instead of
``<task>-REVIEW.md`` once fail-safed a genuinely approved task to REJECTED and
stranded it; a stale ``VERDICT: REJECTED`` left on the branch by a previous
attempt once re-implemented work that was fine.

Each of those was fixed by adding another rule to the regex. That is the
shape of the defect, not an accident of it: a natural-language artifact is
being used as a machine interface, so every new way of writing it is a new
way of being misread, and every fix is a new heuristic layered on the last.

This module replaces the interface. An agent's judgement arrives as a
VERSIONED, DISCRIMINATED, SCHEMA-VALIDATED record bound to the identity it was
produced for. Prose becomes what it should always have been: a rendering of
that record for humans (:func:`render_markdown`), which the controller never
reads. The acceptance criterion "human markdown can change formatting without
changing controller behaviour" is then true by construction rather than by
care.

THE THREE THINGS A RESULT CARRIES
---------------------------------
1. **A discriminated verdict.** :class:`ResultKind` is the discriminator and
   :data:`ADMISSIBLE_VERDICTS` is the per-kind vocabulary. A review may be
   APPROVED; an implementation may not, and asking for it is a rejection
   rather than a value quietly coerced into something else.

2. **An identity.** :class:`ResultIdentity` binds the record to the task,
   the attempt, the commits it was computed over, and the digests of the
   workflow and prompt that produced it. The reader is given the identity it
   EXPECTS and compares; a record that does not match is refused. This is
   what makes a stale verdict from a previous attempt inert instead of
   authoritative -- not a heuristic about which file is newest.

3. **Its evidence.** :class:`Evidence` names the raw agent output that was
   persisted separately and carries its digest. A result whose evidence is
   missing, or whose evidence does not hash to the recorded digest, is
   refused: an unauditable judgement is not a judgement.

FAIL-CLOSED IS THE ONLY DIRECTION
---------------------------------
:func:`load_result` returns a validated result or raises
:class:`ResultRejected` carrying a code from a CLOSED vocabulary
(:class:`RejectionCode`). It never returns a partially-trusted record, and it
has no "lenient" mode: every relaxation of this reader is a way for an
unreviewed change to reach the default branch, which is the one thing the
product's north star promises cannot happen.

A CAPABILITY DECLINE IS NOT A FAILURE
-------------------------------------
"This task is beyond me" and "I crashed" are different facts that call for
different routing -- promote versus retry -- and conflating them is what turns
a cheap first band into either a toll booth or a silent quality drop. A
DECLINED verdict therefore REQUIRES a :class:`DeclineReason` from a closed
vocabulary of mechanically checkable conditions. A decline naming no reason,
or a free-text one, is :data:`RejectionCode.MALFORMED_DECLINE` -- refused as a
result, never downgraded into an ordinary failure, so it can be routed as the
distinct thing it is.
"""

from __future__ import annotations

import enum
import hashlib
import importlib.resources
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

#: Bumped when a change to the envelope is not readable by the previous
#: reader. A record declaring a version this reader does not implement is
#: refused rather than best-effort parsed: "I do not understand this" and "I
#: understood it as ..." are different, and only one of them is safe.
SCHEMA_VERSION = 1

_SCHEMA_FILE = "agent-result.schema.json"
_JUDGEMENT_SCHEMA_FILE = "agent-judgement.schema.json"

def judgement_filename(task_id: str) -> str:
    """Where a leg's agent writes its judgement for one task, in its worktree.

    ONE derived name, never a search. "Find the file that looks like a review"
    is the defect this package removes: a misnamed file is now simply absent,
    which routes to a relaunch, instead of being hunted for by a broadening
    scan whose every widening was another way to read the wrong file.

    Per TASK rather than per attempt because a wave review judges several
    tasks in one run, and a single document holding a list would make one
    malformed entry contaminate every verdict in the wave.
    """
    return f"nyxloom-judgement-{task_id}.json"


def result_filename(task_id: str) -> str:
    """Where the wrapper writes the assembled, evidence-bound result for one
    task, relative to the attempt directory."""
    return f"result-{task_id}.json"


class ResultKind(enum.Enum):
    """The discriminator: which leg of the pipeline produced this result."""

    IMPLEMENT = "implement"
    SELF_REVIEW = "self_review"
    REVIEW_INDEPENDENT = "review_independent"
    DIAGNOSIS = "diagnosis"
    CARVE = "carve"
    GATE = "gate"
    OPERATOR = "operator"


class Verdict(enum.Enum):
    """The closed outcome vocabulary, shared across kinds.

    Shared rather than per-kind-enumerated so that the SAME word means the
    same thing everywhere -- a REJECTED review and a REJECTED carve proposal
    both mean "this artifact does not pass", and code that routes on the
    verdict does not need a kind-specific translation table. Which verdicts
    each kind may actually use is :data:`ADMISSIBLE_VERDICTS`.
    """

    #: The work was completed as specified.
    DONE = "done"
    #: The work could not proceed and names an unblock condition.
    BLOCKED = "blocked"
    #: The reviewed artifact passes.
    APPROVED = "approved"
    #: The reviewed artifact does not pass.
    REJECTED = "rejected"
    #: The agent judged the task outside its capability BEFORE doing it. See
    #: the module docstring: never routed as a failure.
    DECLINED = "declined"
    #: The leg ran and failed for a reason that is not a judgement (crash,
    #: provider outage, environment). Distinct from BLOCKED, which is a
    #: judgement about the CONTRACT, and from DECLINED, which is a judgement
    #: about the AGENT.
    FAILED = "failed"


#: Which verdicts each kind may carry. A verdict outside its kind's set is
#: refused rather than coerced -- an implementation reporting APPROVED is
#: either a confused agent or a mislabelled artifact, and both are reasons to
#: stop rather than to guess which was meant.
ADMISSIBLE_VERDICTS: dict[ResultKind, frozenset[Verdict]] = {
    ResultKind.IMPLEMENT: frozenset({
        Verdict.DONE, Verdict.BLOCKED, Verdict.DECLINED, Verdict.FAILED}),
    ResultKind.SELF_REVIEW: frozenset({
        Verdict.APPROVED, Verdict.REJECTED, Verdict.DECLINED, Verdict.FAILED}),
    ResultKind.REVIEW_INDEPENDENT: frozenset({
        Verdict.APPROVED, Verdict.REJECTED, Verdict.DECLINED, Verdict.FAILED}),
    ResultKind.DIAGNOSIS: frozenset({
        Verdict.DONE, Verdict.DECLINED, Verdict.FAILED}),
    ResultKind.CARVE: frozenset({
        Verdict.DONE, Verdict.BLOCKED, Verdict.DECLINED, Verdict.FAILED}),
    # A gate is a program, not an agent: it cannot decline and cannot judge
    # its own subject. It passed, it did not, or it could not run.
    ResultKind.GATE: frozenset({Verdict.DONE, Verdict.REJECTED, Verdict.FAILED}),
    # An operator is the authority, so an operator result is never DECLINED
    # (a human declining is simply an absent answer) and never FAILED.
    ResultKind.OPERATOR: frozenset({
        Verdict.APPROVED, Verdict.REJECTED, Verdict.BLOCKED, Verdict.DONE}),
}


class DeclineReason(enum.Enum):
    """Mechanically checkable conditions a capability decline may name.

    Closed on purpose, and every member is a condition an INDEPENDENT fact can
    corroborate (the amendment's CR-09 decline-validity rule). "This looked
    hard" is not on the list, because nothing outside the agent can check it,
    and an uncorroborable decline is indistinguishable from a lazy one.
    """

    #: The packet's context exceeds what the route can hold.
    CONTEXT_EXCEEDED = "context-exceeded"
    #: A tool or command the contract requires is absent from the environment.
    TOOL_UNAVAILABLE = "tool-unavailable"
    #: The contract names a file, symbol or interface that does not exist.
    CONTRACT_REFERENT_MISSING = "contract-referent-missing"
    #: Two clauses of the contract cannot both be satisfied.
    CONTRACT_SELF_CONTRADICTORY = "contract-self-contradictory"
    #: The work depends on another package that has not landed.
    DEPENDENCY_NOT_LANDED = "dependency-not-landed"


class RejectionCode(enum.Enum):
    """Why a record was refused. Stable, closed, and one per acceptance case.

    These are operator-facing and appear in audit events, so they are values
    rather than messages: an operator triaging "why did nothing merge" needs
    to distinguish "the reviewer answered about the wrong attempt" from "the
    reviewer's output was never persisted", and prose cannot be counted.
    """

    #: `schema_version` is not one this reader implements.
    UNSUPPORTED_VERSION = "unsupported-version"
    #: The document does not validate against the JSON schema.
    SCHEMA_VIOLATION = "schema-violation"
    #: Not JSON at all.
    MALFORMED_DOCUMENT = "malformed-document"
    #: A `kind` outside :class:`ResultKind`, or one the caller did not ask for.
    KIND_MISMATCH = "kind-mismatch"
    #: A `verdict` outside :class:`Verdict`, or outside the kind's admissible
    #: set.
    UNKNOWN_OUTCOME = "unknown-outcome"
    #: The record is about a different task.
    STALE_TASK = "stale-task"
    #: The record is about a different attempt -- the P59b staleness case,
    #: now a comparison rather than a heuristic.
    WRONG_ATTEMPT = "wrong-attempt"
    #: The record was computed over a different base or head commit.
    WRONG_REVISION = "wrong-revision"
    #: The record was produced under a different workflow or prompt than the
    #: one this attempt was dispatched with.
    WRONG_WORKFLOW_DIGEST = "wrong-workflow-digest"
    #: DECLINED without a reason from :class:`DeclineReason`.
    MALFORMED_DECLINE = "malformed-decline"
    #: The named raw-output evidence does not exist.
    MISSING_EVIDENCE = "missing-evidence"
    #: The evidence exists but does not hash to the recorded digest.
    EVIDENCE_DIGEST_MISMATCH = "evidence-digest-mismatch"


class ResultRejected(Exception):
    """A record was refused. Carries the closed-vocabulary reason.

    Deliberately an exception rather than a returned union: every call site
    that reads a result is an authority site, and a caller that forgets to
    check a returned status would silently proceed on an unvalidated record.
    Forgetting to catch this one stops the pass instead.
    """

    def __init__(self, code: RejectionCode, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code.value}: {detail}" if detail else code.value)


@dataclass(frozen=True)
class ResultIdentity:
    """What a result is ABOUT, and what produced it.

    Every field is mechanically knowable by the wrapper at launch time, which
    is the point: the agent is not asked to remember its own attempt id, the
    wrapper stamps it. The agent's copy is then checked against the daemon's
    copy, and disagreement is a refusal -- so an agent cannot claim a verdict
    for an attempt it did not run, whether by confusion or otherwise.

    ``workflow_digest`` and ``prompt_digest`` are empty-string-by-default
    because CR-07 introduces the compiled workflow that gives them values;
    until then they compare equal (both empty) and the check is inert rather
    than a lie. A caller that HAS a digest and receives a different one is
    always refused.
    """

    task_id: str
    attempt_id: str
    base_commit: str = ""
    head_commit: str = ""
    workflow_digest: str = ""
    prompt_digest: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "base_commit": self.base_commit,
            "head_commit": self.head_commit,
            "workflow_digest": self.workflow_digest,
            "prompt_digest": self.prompt_digest,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ResultIdentity":
        return cls(
            task_id=str(d.get("task_id", "")),
            attempt_id=str(d.get("attempt_id", "")),
            base_commit=str(d.get("base_commit", "")),
            head_commit=str(d.get("head_commit", "")),
            workflow_digest=str(d.get("workflow_digest", "")),
            prompt_digest=str(d.get("prompt_digest", "")),
        )


def digest_bytes(data: bytes) -> str:
    """The evidence digest function, named once so both sides agree."""
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Evidence:
    """The raw agent output, persisted separately and bound by digest.

    Separate because raw output is large, is not a contract, and must never
    be parsed for authority -- keeping it out of the result record removes the
    temptation to read it. Bound by digest because evidence that can be edited
    after the fact does not support the judgement it accompanies.
    """

    path: str
    sha256: str
    bytes_len: int

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "sha256": self.sha256, "bytes": self.bytes_len}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Evidence":
        return cls(path=str(d.get("path", "")), sha256=str(d.get("sha256", "")),
                   bytes_len=int(d.get("bytes", 0)))


@dataclass(frozen=True)
class AgentResult:
    """One validated agent judgement.

    Construct through :func:`load_result`; the constructor performs no
    validation on purpose, so there is exactly ONE place a record becomes
    trusted and it is the one that also checks identity and evidence.
    """

    kind: ResultKind
    verdict: Verdict
    identity: ResultIdentity
    evidence: Evidence
    summary: str = ""
    decline_reason: DeclineReason | None = None
    schema_version: int = SCHEMA_VERSION
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "schema_version": self.schema_version,
            "kind": self.kind.value,
            "verdict": self.verdict.value,
            "identity": self.identity.to_dict(),
            "evidence": self.evidence.to_dict(),
            "summary": self.summary,
        }
        if self.decline_reason is not None:
            out["decline_reason"] = self.decline_reason.value
        if self.details:
            out["details"] = self.details
        return out

    def to_json(self) -> str:
        """Deterministic serialization: sorted keys, stable separators.

        Byte-stability matters because this document is committed to a branch
        and diffed by humans; a serializer whose key order varies would make
        every rewrite look like a change.
        """
        return json.dumps(self.to_dict(), sort_keys=True, indent=2) + "\n"


def _schema() -> dict:
    text = (importlib.resources.files("nyxloom.schemas")
            .joinpath(_SCHEMA_FILE).read_text(encoding="utf-8"))
    return json.loads(text)


#: Schema failures on these fields get their own rejection code rather than
#: the generic one. The acceptance criteria are written per FAILURE MODE
#: ("unknown outcome", "malformed decline"), and those are what an operator
#: triaging a stalled project needs to tell apart -- collapsing them into
#: "schema-violation" would make the closed vocabulary a list of names nobody
#: could act on differently.
_FIELD_CODES: dict[str, RejectionCode] = {
    "verdict": RejectionCode.UNKNOWN_OUTCOME,
    "kind": RejectionCode.KIND_MISMATCH,
    "decline_reason": RejectionCode.MALFORMED_DECLINE,
}


def _code_for(error: jsonschema.ValidationError) -> RejectionCode:
    """The most specific rejection code for one schema error.

    A field-level error names its field in ``absolute_path``. The
    decline-conditional is different: it fails at the ROOT with a ``required``
    or ``not`` verdict, because "declined without a reason" and "a reason
    without declining" are relationships between two fields rather than a
    problem with either. Both are still malformed declines, so they are
    matched on the rule that produced them rather than on prose.
    """
    if error.absolute_path:
        code = _FIELD_CODES.get(str(error.absolute_path[0]))
        if code is not None:
            return code
    if error.validator in ("required", "not") and "decline_reason" in str(error.message):
        return RejectionCode.MALFORMED_DECLINE
    return RejectionCode.SCHEMA_VIOLATION


def validate_document(doc: dict) -> None:
    """Schema-validate a decoded record, or raise :class:`ResultRejected`.

    The schema is the structural authority, so it runs before the reader's own
    enum lookups and will usually be the thing that rejects an unknown value.
    That is why it maps to the specific codes rather than to one generic one:
    otherwise the named codes below would be unreachable in practice and the
    vocabulary would document a distinction the reader never actually made.
    """
    validator = jsonschema.Draft202012Validator(_schema())
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path))
    if errors:
        first = errors[0]
        where = ".".join(str(p) for p in first.absolute_path) or "$"
        raise ResultRejected(_code_for(first), f"{where}: {first.message}")


def _require_identity(claimed: ResultIdentity, expected: ResultIdentity) -> None:
    """Compare a record's identity against the one the caller dispatched.

    Order matters for the operator: task before attempt before revision,
    because a task mismatch makes every later comparison meaningless and
    reporting the innermost difference first would send them looking in the
    wrong place.
    """
    if claimed.task_id != expected.task_id:
        raise ResultRejected(
            RejectionCode.STALE_TASK,
            f"result is about {claimed.task_id!r}, expected {expected.task_id!r}")
    if claimed.attempt_id != expected.attempt_id:
        raise ResultRejected(
            RejectionCode.WRONG_ATTEMPT,
            f"result is about attempt {claimed.attempt_id!r}, "
            f"expected {expected.attempt_id!r}")
    for label, got, want in (
        ("base_commit", claimed.base_commit, expected.base_commit),
        ("head_commit", claimed.head_commit, expected.head_commit),
    ):
        # An expectation the caller does not hold is not checked: the daemon
        # legitimately does not always know the head commit before reading the
        # result. An expectation it DOES hold is always enforced.
        if want and got != want:
            raise ResultRejected(
                RejectionCode.WRONG_REVISION,
                f"{label} is {got!r}, expected {want!r}")
    for label, got, want in (
        ("workflow_digest", claimed.workflow_digest, expected.workflow_digest),
        ("prompt_digest", claimed.prompt_digest, expected.prompt_digest),
    ):
        if want and got != want:
            raise ResultRejected(
                RejectionCode.WRONG_WORKFLOW_DIGEST,
                f"{label} is {got!r}, expected {want!r}")


def _require_evidence(evidence: Evidence, evidence_root: Path | None) -> None:
    """The evidence exists and hashes to what the record claims.

    `evidence_root is None` means the caller is validating a record it has not
    yet persisted evidence for (the wrapper, at write time). The digest is
    still structurally required by the schema; only the on-disk check is
    skipped, and no caller that decides anything passes None.
    """
    if evidence_root is None:
        return
    path = evidence_root / evidence.path
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ResultRejected(
            RejectionCode.MISSING_EVIDENCE,
            f"{evidence.path}: {type(exc).__name__}") from exc
    actual = digest_bytes(raw)
    if actual != evidence.sha256:
        raise ResultRejected(
            RejectionCode.EVIDENCE_DIGEST_MISMATCH,
            f"{evidence.path}: recorded {evidence.sha256[:12]}, actual {actual[:12]}")


def load_result(raw: str | bytes | dict, *, expect: ResultIdentity,
                kind: ResultKind, evidence_root: Path | None = None) -> AgentResult:
    """Validate a record and return it, or raise :class:`ResultRejected`.

    THE single place an agent's judgement becomes authoritative. Every check
    an acceptance criterion names happens here, in an order chosen so the
    first failure is the most useful one to report.
    """
    if isinstance(raw, (str, bytes)):
        try:
            doc = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ResultRejected(RejectionCode.MALFORMED_DOCUMENT, str(exc)) from exc
    else:
        doc = raw
    if not isinstance(doc, dict):
        raise ResultRejected(RejectionCode.MALFORMED_DOCUMENT,
                             f"top level is {type(doc).__name__}, expected object")

    # Version FIRST: a record from a future writer may be schema-invalid here
    # only because this reader is old, and reporting that as a schema
    # violation would send an operator to fix a document that is correct.
    version = doc.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ResultRejected(
            RejectionCode.UNSUPPORTED_VERSION,
            f"record declares schema_version {version!r}, this reader implements "
            f"{SCHEMA_VERSION}")

    validate_document(doc)

    try:
        record_kind = ResultKind(doc["kind"])
    except ValueError as exc:
        raise ResultRejected(RejectionCode.KIND_MISMATCH, str(doc["kind"])) from exc
    if record_kind is not kind:
        raise ResultRejected(
            RejectionCode.KIND_MISMATCH,
            f"result is a {record_kind.value} result, expected {kind.value}")

    try:
        verdict = Verdict(doc["verdict"])
    except ValueError as exc:
        raise ResultRejected(RejectionCode.UNKNOWN_OUTCOME, str(doc["verdict"])) from exc
    if verdict not in ADMISSIBLE_VERDICTS[record_kind]:
        raise ResultRejected(
            RejectionCode.UNKNOWN_OUTCOME,
            f"{verdict.value} is not admissible for a {record_kind.value} result "
            f"(admissible: {sorted(v.value for v in ADMISSIBLE_VERDICTS[record_kind])})")

    raw_reason = doc.get("decline_reason")
    decline_reason: DeclineReason | None = None
    if verdict is Verdict.DECLINED:
        try:
            decline_reason = DeclineReason(raw_reason)
        except ValueError as exc:
            raise ResultRejected(
                RejectionCode.MALFORMED_DECLINE,
                f"decline_reason {raw_reason!r} is not one of "
                f"{sorted(r.value for r in DeclineReason)}") from exc
    elif raw_reason is not None:
        # A decline reason on a non-declined result is a mislabelled record,
        # and the safe reading is that the agent meant to decline and the
        # verdict is wrong -- which is exactly the ambiguity that must not be
        # resolved by guessing.
        raise ResultRejected(
            RejectionCode.MALFORMED_DECLINE,
            f"decline_reason present on a {verdict.value} result")

    identity = ResultIdentity.from_dict(doc["identity"])
    _require_identity(identity, expect)

    evidence = Evidence.from_dict(doc["evidence"])
    _require_evidence(evidence, evidence_root)

    return AgentResult(
        kind=record_kind,
        verdict=verdict,
        identity=identity,
        evidence=evidence,
        summary=str(doc.get("summary", "")),
        decline_reason=decline_reason,
        schema_version=SCHEMA_VERSION,
        details=dict(doc.get("details") or {}),
    )


@dataclass(frozen=True)
class Judgement:
    """The half of a result an AGENT authors: what it concluded.

    It deliberately cannot express evidence, commits or digests. Those are
    mechanically knowable, so the wrapper stamps them -- which means an agent
    cannot claim a fact about the repository that the repository disagrees
    with, and does not have to be trusted to remember one either.
    """

    kind: ResultKind
    verdict: Verdict
    task_id: str
    attempt_id: str
    summary: str = ""
    decline_reason: DeclineReason | None = None
    details: dict[str, Any] = field(default_factory=dict)


def _judgement_schema() -> dict:
    text = (importlib.resources.files("nyxloom.schemas")
            .joinpath(_JUDGEMENT_SCHEMA_FILE).read_text(encoding="utf-8"))
    return json.loads(text)


def load_judgement(raw: str | bytes, *, task_id: str, attempt_id: str,
                   kind: ResultKind) -> Judgement:
    """Validate an agent-authored judgement, or raise :class:`ResultRejected`.

    Same fail-closed contract as :func:`load_result`, over the smaller
    document an agent is allowed to write. `attempt_id` is compared rather
    than trusted: a judgement left on the branch by a previous attempt names
    THAT attempt, so it is inert here -- which is the P59b staleness case
    turned into a comparison instead of a rule about which file is newest.
    """
    try:
        doc = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ResultRejected(RejectionCode.MALFORMED_DOCUMENT, str(exc)) from exc
    if not isinstance(doc, dict):
        raise ResultRejected(RejectionCode.MALFORMED_DOCUMENT,
                             f"top level is {type(doc).__name__}, expected object")

    version = doc.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ResultRejected(
            RejectionCode.UNSUPPORTED_VERSION,
            f"judgement declares schema_version {version!r}, this reader "
            f"implements {SCHEMA_VERSION}")

    validator = jsonschema.Draft202012Validator(_judgement_schema())
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path))
    if errors:
        first = errors[0]
        where = ".".join(str(p) for p in first.absolute_path) or "$"
        raise ResultRejected(_code_for(first), f"{where}: {first.message}")

    record_kind = ResultKind(doc["kind"])
    if record_kind is not kind:
        raise ResultRejected(
            RejectionCode.KIND_MISMATCH,
            f"judgement is a {record_kind.value} judgement, expected {kind.value}")

    verdict = Verdict(doc["verdict"])
    if verdict not in ADMISSIBLE_VERDICTS[record_kind]:
        raise ResultRejected(
            RejectionCode.UNKNOWN_OUTCOME,
            f"{verdict.value} is not admissible for a {record_kind.value} judgement")

    if doc["task_id"] != task_id:
        raise ResultRejected(
            RejectionCode.STALE_TASK,
            f"judgement is about {doc['task_id']!r}, expected {task_id!r}")
    if doc["attempt_id"] != attempt_id:
        raise ResultRejected(
            RejectionCode.WRONG_ATTEMPT,
            f"judgement is about attempt {doc['attempt_id']!r}, expected {attempt_id!r}")

    decline_reason = (DeclineReason(doc["decline_reason"])
                      if verdict is Verdict.DECLINED else None)
    return Judgement(
        kind=record_kind, verdict=verdict,
        task_id=doc["task_id"], attempt_id=doc["attempt_id"],
        summary=str(doc.get("summary", "")),
        decline_reason=decline_reason,
        details=dict(doc.get("details") or {}),
    )


def bind_evidence(judgement: Judgement, *, evidence_path: str, evidence_bytes: bytes,
                  base_commit: str = "", head_commit: str = "",
                  workflow_digest: str = "", prompt_digest: str = "") -> AgentResult:
    """Assemble the authoritative record from a judgement plus the facts.

    Called by the wrapper, which is the only party that knows both. The
    digest is computed HERE from the bytes actually persisted, never taken
    from anything the agent wrote -- a self-reported digest would make the
    evidence binding a statement of intent rather than a check.
    """
    return AgentResult(
        kind=judgement.kind,
        verdict=judgement.verdict,
        identity=ResultIdentity(
            task_id=judgement.task_id,
            attempt_id=judgement.attempt_id,
            base_commit=base_commit,
            head_commit=head_commit,
            workflow_digest=workflow_digest,
            prompt_digest=prompt_digest,
        ),
        evidence=Evidence(path=evidence_path, sha256=digest_bytes(evidence_bytes),
                          bytes_len=len(evidence_bytes)),
        summary=judgement.summary,
        decline_reason=judgement.decline_reason,
        details=judgement.details,
    )


def failed_result(kind: ResultKind, *, task_id: str, attempt_id: str,
                  evidence_path: str, evidence_bytes: bytes, detail: str,
                  base_commit: str = "", head_commit: str = "") -> AgentResult:
    """The result for a leg that produced no usable judgement.

    An agent that crashed, timed out, or wrote an unreadable judgement has
    FAILED -- a fact about the run, not a judgement about the work. It is
    recorded as a typed result rather than an absence so the reason survives
    into the event log: "the reviewer never answered" and "the reviewer said
    no" route differently, and an absence cannot tell them apart.

    Deliberately never DECLINED: a decline is a claim about capability that
    only the agent can make, and manufacturing one here would put a promotion
    in the ladder that nobody asked for.
    """
    return AgentResult(
        kind=kind,
        verdict=Verdict.FAILED,
        identity=ResultIdentity(task_id=task_id, attempt_id=attempt_id,
                                base_commit=base_commit, head_commit=head_commit),
        evidence=Evidence(path=evidence_path, sha256=digest_bytes(evidence_bytes),
                          bytes_len=len(evidence_bytes)),
        summary=detail,
    )


def render_markdown(result: AgentResult) -> str:
    """The human rendering of a typed record.

    One direction only: record -> prose. Nothing in the controller reads what
    this produces, which is what makes "the formatting may change freely" a
    structural property rather than a promise. If this function is ever
    inverted into a parser, that property is gone.
    """
    lines = [
        f"# {result.kind.value} result: {result.verdict.value.upper()}",
        "",
        f"- task: `{result.identity.task_id}`",
        f"- attempt: `{result.identity.attempt_id}`",
    ]
    if result.identity.base_commit:
        lines.append(f"- base: `{result.identity.base_commit}`")
    if result.identity.head_commit:
        lines.append(f"- head: `{result.identity.head_commit}`")
    if result.decline_reason is not None:
        lines.append(f"- declined because: {result.decline_reason.value}")
    lines += [
        f"- evidence: `{result.evidence.path}` "
        f"(sha256 `{result.evidence.sha256[:12]}`, {result.evidence.bytes_len} bytes)",
        "",
    ]
    if result.summary:
        lines += [result.summary, ""]
    return "\n".join(lines)
