"""Review-leg progress watchdog. PACKAGE nyxloom-P105 (B29, LESSONS PL11).

THE DEFECT (PL11, a real incident). A bounded review leg can hold a session,
run a live process, and write transcript continuously -- and still be making
no progress at all. In the P173 Topos review a reviewer held its lease for
more than ten minutes producing hundreds of kilobytes of transcript that was
almost entirely delivery-runtime orchestration overhead: todo/sign-off
bookkeeping, capability routing, evidence-format retries. It had reached a
possible test omission but never a correction, a gate run, or a ship verdict.
A retry from scratch replayed the same broad setup.

WHY THE EXISTING LADDER CANNOT SEE THIS. nyxloom's attempt-recovery ladder
(rules_attempts.py) has exactly two liveness gates, and this shape defeats
both by construction:

  * the tier-1/tier-2 STALL gate (branch 5) keys on ``log_quiet_seconds`` --
    seconds since the attempt log's mtime -- confirmed by an unchanged
    ``/proc`` CPU signature. A reviewer writing transcript every few seconds
    is never quiet and never CPU-flat, so it is never a candidate;
  * the WALL-CLOCK cap (branch 3) does fire, but at
    ``policy.attempt_max_wall_seconds`` (10800s / 3h by default) -- three
    hours of a reviewer proving it is allowed to read a small diff, and then
    an interrupt carrying no reason and no salvage.

So the gap is precise: a leg that is LOUD and USELESS. daemon.py's
``_resume_failures`` already carries this lesson in the opposite direction
("do NOT score progress by log size -- the P26 bug this replaces scored a
noisily-dying session, stack traces and retry spam, as progress"); this
module is the same lesson applied to a live leg.

THE FOUR SIGNALS (PL11). A review leg is scored on four INDEPENDENT signals,
and the rule that makes the detector work is that the first one is NOT one of
the three that count:

  (a) transcript growth  -- ``transcript_growing`` / ``transcript_bytes`` /
      ``transcript_records``. Deliberately NOT progress. Its role is the
      DISCRIMINATOR: growth is exactly what hides this leg from the stall
      gate, so a leg whose transcript is quiet is left entirely to the P14
      ladder (which has the CPU-signature tier-2 confirmation this module
      does not attempt to duplicate) and is never judged here.
  (b) branch/worktree commit activity -- ``branch_advanced``. Progress.
  (c) gate activity                   -- ``gate_active``. Progress.
  (d) concrete finding or verdict     -- ``finding_recorded``. Progress.

Any ONE of (b)/(c)/(d) makes the leg immune. Only a leg that is growing its
transcript while producing none of them can be stalled here.

THE TWO BUDGETS (PL11: "set a wall-clock and orchestration-turn budget"):

  * WALL-CLOCK -- ``policy.review_progress_wall_seconds``. Enabled by
    default at a value strictly below ``attempt_max_wall_seconds``, so this
    can only ever fire EARLIER than the existing absolute backstop, and only
    for a leg with zero concrete progress.
  * ORCHESTRATION RECORDS -- ``policy.review_progress_max_records``, counted
    as newline-delimited records in the attempt log. For a claude route the
    log is ``--output-format stream-json --verbose`` (adapters.py), so one
    line IS one orchestration event -- an assistant turn, a tool call, a
    tool result -- which is the granularity PL11's "orchestration turn"
    means. It is a RECORD count and named as one rather than being called a
    turn count: separating assistant turns from tool traffic would mean
    JSON-parsing every line of a growing multi-megabyte log on every 30s
    reconcile pass, and the terminal ``result`` line's own ``num_turns``
    (which adapters.extract_usage already sees and discards) exists only
    once the leg has ALREADY exited, which is too late to budget against.
    DISABLED by default (0): no measured distribution of healthy-review
    record counts exists in this project yet, and enabling a threshold on a
    guess is how a healthy reviewer gets killed. Every stall this module
    records carries ``transcript_records``, which is what will supply that
    distribution.

WHAT HAPPENS THEN (PL11's remedy, and it is deliberately not a new kill
path). ``evaluate_leg`` returns a typed trigger; rules_attempts.py turns that
into ``MarkReviewStalled``; effects_lifecycle.py appends the compact summary
as ``REVIEW_PROGRESS_STALLED`` and puts the attempt into ``STALLED`` --
whereupon the ladder's ALREADY-EXISTING branch 4 ("already STALLED ->
InterruptAttempt") interrupts it on the next pass, through the same
incident-hardened signal path every other interrupt uses. Nothing about the
interrupt itself is new; what is new is a reason to reach it, and a durable
record of why.

PURITY. Every function here is pure: no clock, no I/O, no imports beyond
``.types`` -- the same contract watchdog.py states for its own detectors, and
for the same reason (a detector that cannot be exercised without a daemon is
a detector nobody exercises). The daemon measures the signals; this module
decides what they mean.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from .types import Event, EventType, Role

#: The typed interrupt reason PL11 names. Stable string, never prose -- it is
#: a dedup/routing key the same way watchdog.RunawaySignal.key is.
REVIEW_NO_CONCRETE_PROGRESS = "review-no-concrete-progress"

#: Which budget ran out. Also stable strings, also never prose.
TRIGGER_WALL_CLOCK = "wall-clock"
TRIGGER_ORCHESTRATION_RECORDS = "orchestration-records"
TRIGGER_BOTH = "both"

#: The roles this module supervises. A carver or an implementer is NOT a
#: bounded review leg: it is EXPECTED to spend a long stretch reading and
#: writing before anything commits, so "no commit, no gate, no finding yet"
#: is its normal early state rather than a defect. PL11 is specifically
#: about the leg whose whole job is to reach a verdict on an existing diff.
REVIEW_ROLES = frozenset({Role.REVIEW_INDEPENDENT, Role.SELF_REVIEW})

#: Signal (c): a gate run bound to this task.
_GATE_EVENT_TYPES = frozenset({EventType.GATE_STARTED, EventType.GATE_FINISHED})

#: Signal (d): a structured "concrete finding or correction" heartbeat. All
#: three are ALREADY-EMITTED structured records, never prose the reviewer
#: writes at will -- REVIEW_RECORDED is the verdict itself, FINDING_RECORDED
#: is the advisory findings channel (findings.py), and
#: SCOPE_AMENDMENT_REQUESTED is a reviewer that reached a concrete conclusion
#: about the packet's own boundary. A leg that has emitted any of them has
#: demonstrably got somewhere.
_FINDING_EVENT_TYPES = frozenset({
    EventType.REVIEW_RECORDED, EventType.FINDING_RECORDED,
    EventType.SCOPE_AMENDMENT_REQUESTED,
})

#: Bounds on the inspected-file list carried in a compact summary. The paths
#: come out of an agent-authored transcript, so they are bounded in BOTH
#: directions (how many, how long) before they ever reach an event payload.
INSPECTED_PATH_LIMIT = 40
INSPECTED_PATH_MAX_LEN = 200

#: Depth cap for the tool-use walk below. stream-json nests a tool_use block
#: about four levels down; eight is slack, and a cap means a pathological
#: (or hostile) record cannot cost unbounded recursion.
_WALK_MAX_DEPTH = 8

_TOOL_INPUT_PATH_KEYS = ("file_path", "notebook_path", "path")


@dataclass(frozen=True)
class ReviewLegSignals:
    """PL11's four signals for ONE in-flight review leg, as the daemon
    measured them this pass.

    ``branch_advanced`` is TRI-state on purpose. False means "measured, and
    the branch has not moved". None means the measurement is not available
    for this leg -- and the two are not the same thing:

      * a leg with no resolvable baseline (an ATTEMPT_CREATED written before
        this package, or a branch git cannot resolve) is STRUCTURALLY
        unmeasurable, permanently, and treating that as progress would
        disable the detector forever for exactly the legs PL11 is about --
        so None does NOT count as progress;
      * a TRANSIENT git read fault is different, and the daemon resolves it
        the other way (it reports ``branch_advanced=True``, making the leg
        immune for that pass) -- never interrupt on a signal that failed to
        read.

    That asymmetry is the whole safety argument, so it is stated here rather
    than left to be inferred from the daemon's exception handling.
    """
    elapsed_seconds: float
    transcript_bytes: int
    transcript_records: int
    transcript_growing: bool          # (a) -- discriminator, NOT progress
    branch_advanced: bool | None      # (b)
    gate_active: bool                 # (c)
    finding_recorded: bool            # (d)

    @property
    def concrete_progress(self) -> bool:
        """(b) OR (c) OR (d). (a) is deliberately absent from this sum."""
        return bool(self.branch_advanced) or self.gate_active or self.finding_recorded


@dataclass(frozen=True)
class ReviewProgressBudget:
    """Both budgets, in the same 0-disables convention the project already
    uses for opt-in policy thresholds (``test_health_interval_days``,
    ``gap_audit_after_changed_lines``)."""
    wall_seconds: int = 0
    max_records: int = 0

    @property
    def armed(self) -> bool:
        return self.wall_seconds > 0 or self.max_records > 0


def evaluate_leg(signals: ReviewLegSignals,
                 budget: ReviewProgressBudget) -> str | None:
    """The typed trigger for a stalled review leg, or None.

    Order of the guards is the contract, not an implementation detail:
    concrete progress wins over any budget, and a quiet transcript is handed
    back to the P14 stall ladder before a budget is ever consulted.
    """
    if not budget.armed:
        return None
    if not signals.transcript_growing:
        # Signal (a) absent: this is the ordinary quiet stall, and branch 5
        # of the ladder owns it (with a CPU-signature confirmation this
        # module does not have). Claiming it here would pre-empt a more
        # careful check with a less careful one.
        return None
    if signals.concrete_progress:
        return None
    over_wall = budget.wall_seconds > 0 and signals.elapsed_seconds > budget.wall_seconds
    over_records = budget.max_records > 0 and signals.transcript_records > budget.max_records
    if over_wall and over_records:
        return TRIGGER_BOTH
    if over_wall:
        return TRIGGER_WALL_CLOCK
    if over_records:
        return TRIGGER_ORCHESTRATION_RECORDS
    return None


def attempt_start_sequences(events: Sequence[Event]) -> dict[str, int]:
    """attempt_id -> the ``sequence`` of its FIRST ATTEMPT_* event.

    A SEQUENCE, not a timestamp, for the reason watchdog._last_resume_index
    already states: emission order is this log's ordering authority, and
    several events can share one wall-clock second within a single pass.

    Keyed off any ATTEMPT_* member rather than ATTEMPT_CREATED specifically
    so a legacy or partially-written attempt record still gets a usable
    lower bound instead of being silently skipped.
    """
    out: dict[str, int] = {}
    for ev in events:
        aid = ev.attempt_id
        if aid is None or not ev.type.value.startswith("ATTEMPT_"):
            continue
        if aid not in out or ev.sequence < out[aid]:
            out[aid] = ev.sequence
    return out


def event_progress(events: Sequence[Event], attempt_id: str, task_id: str,
                   *, since_sequence: int | None) -> tuple[bool, bool]:
    """Signals (c) and (d) for one leg: ``(gate_active, finding_recorded)``.

    Scoped to events AFTER ``since_sequence`` (this leg's own first event) so
    a gate or a verdict from an EARLIER attempt on the same task -- a prior
    rejected review, the pre-merge gate of a previous implementer round --
    can never be read as this leg's progress. ``since_sequence`` of None
    (this leg has no event of its own yet, so it cannot have produced
    anything) yields ``(False, False)`` rather than scanning the whole log.

    Gate activity is matched on ``task_id``: the gate runs the daemon
    authorizes are recorded against the task, not against the reviewer's
    attempt. Finding activity is matched on EITHER binding -- REVIEW_RECORDED
    carries the reviewer's attempt_id (the A7 verdict-attempt binding), while
    a FINDING_RECORDED or a scope-amendment request is task-bound.
    """
    if since_sequence is None:
        return False, False
    gate_active = False
    finding_recorded = False
    for ev in events:
        if ev.sequence <= since_sequence:
            continue
        if ev.type in _GATE_EVENT_TYPES and ev.task_id == task_id:
            gate_active = True
        elif ev.type in _FINDING_EVENT_TYPES and (
                ev.attempt_id == attempt_id or ev.task_id == task_id):
            finding_recorded = True
        if gate_active and finding_recorded:
            break
    return gate_active, finding_recorded


def already_recorded(events: Sequence[Event], attempt_id: str) -> bool:
    """Whether this leg's stall has ALREADY been recorded.

    Two distinct duplicates to suppress, which is why this exists at the
    effect boundary rather than being left to the planner:

      * a WAVE review is ONE attempt spanning N member tasks, and the ladder
        walks per task -- so a stalled 3-member review plans three
        MarkReviewStalled actions in a single pass;
      * the leg stays RUNNING until the next pass's interrupt lands, so
        without this the same stall re-records every pass in between.

    Same shape as effects.spec_attention_recently_emitted's debounce, and it
    reads the log rather than in-memory state so a daemon restart cannot
    resurrect the duplicate.
    """
    return any(ev.type is EventType.REVIEW_PROGRESS_STALLED
               and ev.attempt_id == attempt_id for ev in events)


def _walk_tool_use_paths(node: Any, depth: int, found: list[str]) -> None:
    if depth > _WALK_MAX_DEPTH or len(found) >= INSPECTED_PATH_LIMIT * 4:
        return
    if isinstance(node, dict):
        if node.get("type") == "tool_use":
            tool_input = node.get("input")
            if isinstance(tool_input, dict):
                for key in _TOOL_INPUT_PATH_KEYS:
                    value = tool_input.get(key)
                    if isinstance(value, str):
                        found.append(value)
        for value in node.values():
            _walk_tool_use_paths(value, depth + 1, found)
    elif isinstance(node, list):
        for value in node:
            _walk_tool_use_paths(value, depth + 1, found)


def _clean_path(raw: str) -> str | None:
    """A transcript path fit to put in an event payload, or None.

    The transcript is agent-authored, so this is a boundary: control
    characters (which would break a JSONL event line, and a terminal reading
    one) are rejected outright rather than stripped, and length is capped.
    Rendering escapes on top of this -- neither layer is trusted alone.
    """
    value = raw.strip()
    if not value:
        return None
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        return None
    return value[:INSPECTED_PATH_MAX_LEN]


def inspected_paths(lines: Iterable[str]) -> tuple[tuple[str, ...], bool]:
    """The files a review leg looked at, from its stream-json transcript.

    Returns ``(paths, truncated)`` -- paths sorted (determinism: a summary
    that reorders between two reads of the same log is not a summary), and
    ``truncated`` True when the cap dropped some.

    This is the half of PL11's compact summary that carries actual reviewer
    knowledge forward: it is derived STRUCTURE (tool-call arguments), never
    the reviewer's prose, which is why it can be persisted without inheriting
    the trust problem of the transcript it came from. A line that will not
    parse is skipped, not fatal -- a partially-flushed final line is the
    normal state of a log belonging to a process that is still running.
    """
    found: list[str] = []
    for line in lines:
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        _walk_tool_use_paths(record, 0, found)

    cleaned: set[str] = set()
    for raw in found:
        value = _clean_path(raw)
        if value is not None:
            cleaned.add(value)
    ordered = sorted(cleaned)
    truncated = len(ordered) > INSPECTED_PATH_LIMIT
    return tuple(ordered[:INSPECTED_PATH_LIMIT]), truncated


def compact_summary(*, attempt_id: str, task_id: str, role: str,
                    route_id: str, model: str, trigger: str,
                    signals: ReviewLegSignals, budget: ReviewProgressBudget,
                    paths: tuple[str, ...],
                    paths_truncated: bool) -> dict[str, Any]:
    """PL11's compact controller summary, as a plain JSON-safe dict.

    "Compact" is the operative word: this is what a restart is meant to be
    seeded FROM instead of replaying the original prompt and transcript, so
    it carries the leg's identity, what it spent, what it looked at, and --
    in ``signals`` -- the residual finding state, which at the moment this is
    written is by construction all-negative on (b)/(c)/(d). That negative IS
    the load-bearing claim: it tells a consumer the leg reached no
    correction, no gate and no verdict, so nothing of its output needs
    honouring and only its READING (``inspected_paths``) is worth carrying.

    Fixed fields only -- ids, enum values, counts and derived paths. No
    transcript prose, in keeping with the same injection boundary
    watchdog.RunawaySignal and reconcile.py's Action payloads hold.
    """
    return {
        "reason": REVIEW_NO_CONCRETE_PROGRESS,
        "trigger": trigger,
        "attempt_id": attempt_id,
        "task_id": task_id,
        "role": role,
        "route_id": route_id,
        "model": model,
        "elapsed_seconds": round(signals.elapsed_seconds, 1),
        "transcript_bytes": signals.transcript_bytes,
        "transcript_records": signals.transcript_records,
        "budget_wall_seconds": budget.wall_seconds,
        "budget_max_records": budget.max_records,
        "signals": {
            "transcript_growing": signals.transcript_growing,
            "branch_advanced": signals.branch_advanced,
            "gate_active": signals.gate_active,
            "finding_recorded": signals.finding_recorded,
        },
        "inspected_paths": list(paths),
        "inspected_paths_truncated": paths_truncated,
    }
