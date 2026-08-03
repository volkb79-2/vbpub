"""The CR-07a workflow corpus: every manifest any workflow test compiles.

WHY THIS FILE EXISTS RATHER THAN LITERALS IN THE TESTS
------------------------------------------------------
CR-06 finished with the same defect three times, and the report names it
precisely: *the oracle was sound and the population it ran over was not.*
CR-06a's permutation test passed over a corpus with zero ``SELF_REVIEWING``
tasks; CR-06b's anti-absorption control was bound to a two-action case;
CR-06c's reservation branch was unreachable from all 15,966 differential
passes. Coverage saw none of it -- every one of those lines was covered.

CR-07a introduces a whole new vocabulary (nodes, kernel verbs, outcome edges,
diagnostic codes, IR shapes), so it is exactly the package that would repeat
the defect. The countermeasure is structural rather than diligent:

* every manifest a workflow test compiles lives HERE, in :func:`all_sources`;
* ``tests/test_workflow_vocabulary.py`` compiles that same population and fails
  naming any member of the IR's own vocabulary no input reaches;
* an AST guard in that module refuses a manifest literal written inside a
  ``tests/test_workflow_*.py`` module, so "the tests' population" and "the
  oracle's population" cannot drift apart by someone adding a case in a hurry.

The guard's limit, stated because an unstated limit is how this goes wrong
again: it prevents new manifests being INVENTED in a test module. It cannot
prevent a corpus entry existing that no test uses -- but that direction is
harmless, because the oracle runs over the corpus, not over the tests.
"""

from __future__ import annotations

from nyxloom.stages import PRESETS
from nyxloom.types import TASK_TRANSITIONS, TaskState
from nyxloom.workflow_ir import KERNEL_EDGES, KERNEL_OWNED_STATES
from nyxloom.workflow_shadow import (
    LEGACY_PIPELINE,
    full_vocabulary_sources,
    preset_source,
)

_SCHEMA = "nyxloom.workflow/v1"


def _base() -> dict:
    return full_vocabulary_sources()[0]


def _legacy_base() -> dict:
    return full_vocabulary_sources()[1]


# --- valid manifests ------------------------------------------------------


def _guard_showcase() -> dict:
    """Every registered guard bound to some outcome, so the vocabulary oracle
    sees all eight and both :class:`GuardRef` shapes (with and without an
    argument)."""
    src = _base()
    src["id"] = "guard-showcase"
    src["nodes"]["implement"]["guards"] = {
        "done": {"name": "touches_tests"},
        "incomplete": {"name": "attempts_remaining"},
        "dead_end": {"name": "touches_infra"},
    }
    src["nodes"]["self_review"]["guards"] = {
        "approved": {"name": "gate_rigor_below", "arg": 3},
        "rejected": {"name": "diff_larger_than", "arg": 40},
    }
    src["nodes"]["review"]["guards"] = {
        "approved": {"name": "effective_band_at_least", "arg": 2},
        "rejected": {"name": "touches_security_boundary"},
    }
    src["nodes"]["triage"]["guards"] = {"exhausted": {"name": "decision_open"}}
    return src


def _parallel_reviews(mode: str, on_cancel: str, workflow_id: str) -> dict:
    """A typed join group: two distinguishable branches on AWAITING_REVIEW.

    The independent reviewer keeps the approval authority; the second branch is
    a self-review leg that can only reject onward, which is what keeps §4.3
    condition 7 satisfied while condition 8's join policy is exercised.
    """
    src = _base()
    src["id"] = workflow_id
    join = {"group": "independent_reviews", "mode": mode, "on_cancel": on_cancel}
    src["nodes"]["review"]["join"] = dict(join)
    src["nodes"]["review_second"] = {
        "handler": "dispatch_agent", "role": "self-review",
        "prompt": "second_opinion/v3",
        "entry_state": "AWAITING_REVIEW", "exit_state": "AWAITING_REVIEW",
        "outcomes": {"approved": "triage", "rejected": "triage"},
        "join": dict(join),
    }
    return src


def _integer_concurrency() -> dict:
    src = _base()
    src["id"] = "integer-concurrency"
    src["nodes"]["implement"]["concurrency"] = 4
    src["nodes"]["review"]["concurrency"] = "serial"
    return src


def valid_sources() -> dict:
    """Manifests that MUST compile."""
    sources: dict = {
        "full-vocabulary": _base(),
        "full-vocabulary-legacy": _legacy_base(),
        "guard-showcase": _guard_showcase(),
        "join-all-cancel": _parallel_reviews(
            "all", "cancel_siblings", "join-all-cancel"),
        "join-any-leave": _parallel_reviews(
            "any", "leave_siblings", "join-any-leave"),
        "integer-concurrency": _integer_concurrency(),
        "shadow-legacy": preset_source(LEGACY_PIPELINE, workflow_id="legacy"),
    }
    for name, pipeline in sorted(PRESETS.items()):
        sources[f"shadow-{name}"] = preset_source(pipeline, workflow_id=name)
    return sources


# --- kernel-edge negatives: the 38 x 3 refusal corpus ----------------------


def _probe_node(state: TaskState) -> tuple:
    """A node that can sit on ``state``, and the outcome label to abuse."""
    if state in KERNEL_OWNED_STATES:
        return {"entry_state": state.value}, "resume_implement"
    return {
        "handler": "dispatch_agent", "role": "implementer",
        "prompt": "probe/v1",
        "entry_state": state.value, "exit_state": state.value,
    }, "done"


def _probe_document(state: TaskState) -> tuple:
    node, label = _probe_node(state)
    return {
        "schema": _SCHEMA, "id": "probe", "version": 1, "start": "probe",
        "nodes": {"probe": node},
    }, label


def kernel_edges() -> list:
    """The 38 kernel edges, sorted for a stable parameter id."""
    return sorted(KERNEL_EDGES, key=lambda e: (e[0].value, e[1].value))


def redirect_source(from_state: TaskState, to_state: TaskState) -> dict:
    """The ADD/REDIRECT attempt: name the kernel target as an outcome value."""
    src, label = _probe_document(from_state)
    src["id"] = "redirect"
    src["nodes"]["probe"]["outcomes"] = {label: to_state.value}
    return src


def suppress_source(from_state: TaskState, to_state: TaskState) -> dict:
    """The REMOVE attempt: the reserved ``suppress`` key."""
    src, _label = _probe_document(from_state)
    src["id"] = "suppress"
    src["nodes"]["probe"]["suppress"] = [to_state.value]
    return src


def predecessor_source(from_state: TaskState, to_state: TaskState) -> dict:
    """The REDEFINE-PREDECESSORS attempt: the reserved top-level key the
    inventory names for both the K-escape and K-merge-spine obligations."""
    src, _label = _probe_document(from_state)
    src["id"] = "predecessors"
    src["predecessors"] = {to_state.value: [from_state.value]}
    return src


def kernel_edge_sources() -> dict:
    out: dict = {}
    for from_state, to_state in kernel_edges():
        tag = f"{from_state.value}-{to_state.value}"
        out[f"redirect:{tag}"] = redirect_source(from_state, to_state)
        out[f"suppress:{tag}"] = suppress_source(from_state, to_state)
        out[f"predecessors:{tag}"] = predecessor_source(from_state, to_state)
    return out


# --- rejection-condition negatives ----------------------------------------


def _drop(src: dict, node: str, section: str, label: str) -> dict:
    del src["nodes"][node][section][label]
    return src


def _mutate(name: str, fn) -> dict:
    src = _base()
    src["id"] = name
    fn(src)
    return src


def _set_node(src: dict, node_id: str, **fields) -> None:
    src["nodes"][node_id].update(fields)


def negative_sources() -> dict:  # noqa: C901 - a flat catalogue, one entry per code
    """One manifest per compile refusal. Flat and repetitive on purpose: a
    reader checking "is condition N covered" reads one entry, not a builder."""
    out: dict = dict(kernel_edge_sources())

    out["reserved-escapes-list"] = _mutate(
        "reserved-escapes-list",
        lambda s: s.__setitem__("escapes", []))
    out["reserved-escapes-str"] = _mutate(
        "reserved-escapes-str",
        lambda s: s.__setitem__("escapes", "SUPERSEDED"))
    out["reserved-transitions"] = _mutate(
        "reserved-transitions", lambda s: s.__setitem__("transitions", 5))
    out["reserved-predecessors-bare"] = _mutate(
        "reserved-predecessors-bare",
        lambda s: s.__setitem__("predecessors", {"MERGE_READY": "anything"}))
    out["reserved-on-unparsable-node"] = {
        "schema": _SCHEMA, "id": "unparsable", "version": 1, "start": "probe",
        "nodes": {"probe": {"entry_state": 7, "suppress": ["BLOCKED"]}},
    }
    out["reserved-node-body-not-object"] = {
        "schema": _SCHEMA, "id": "notobject", "version": 1, "start": "probe",
        "predecessors": {"MERGED": ["MERGE_READY"]},
        "nodes": {"probe": "oops"},
    }
    out["reserved-nodes-not-object"] = {
        "schema": _SCHEMA, "id": "nodeslist", "version": 1, "start": "probe",
        "escapes": ["CANCELLED"], "nodes": [],
    }

    out["schema-unknown-field"] = _mutate(
        "schema-unknown-field",
        lambda s: _set_node(s, "implement", retry_forever=True))
    out["schema-wrong-const"] = _mutate(
        "schema-wrong-const", lambda s: s.__setitem__("schema", "workflow/v2"))

    out["unknown-state"] = _mutate(
        "unknown-state", lambda s: _set_node(s, "implement", entry_state="LIMBO"))
    out["unknown-exit-state"] = _mutate(
        "unknown-exit-state", lambda s: _set_node(s, "implement", exit_state="LIMBO"))
    out["unknown-handler"] = _mutate(
        "unknown-handler", lambda s: _set_node(s, "triage", handler="frobnicate"))
    out["unknown-role"] = _mutate(
        "unknown-role", lambda s: _set_node(s, "review", role="reviewer"))
    out["agent-without-role"] = _mutate(
        "agent-without-role", lambda s: s["nodes"]["review"].pop("role"))
    out["mechanism-with-role"] = _mutate(
        "mechanism-with-role", lambda s: _set_node(s, "triage", role="carver"))
    out["agent-without-prompt"] = _mutate(
        "agent-without-prompt", lambda s: s["nodes"]["review"].pop("prompt"))
    out["agent-unversioned-prompt"] = _mutate(
        "agent-unversioned-prompt", lambda s: _set_node(s, "review", prompt="review"))
    out["mechanism-with-prompt"] = _mutate(
        "mechanism-with-prompt", lambda s: _set_node(s, "triage", prompt="triage/v1"))
    out["unknown-context-flag"] = _mutate(
        "unknown-context-flag", lambda s: _set_node(s, "review", context=["session_reuse"]))

    out["guard-unknown"] = _mutate(
        "guard-unknown",
        lambda s: _set_node(s, "review",
                            guards={"rejected": {"name": "reviewer_said_ok"}}))
    out["guard-missing-arg"] = _mutate(
        "guard-missing-arg",
        lambda s: _set_node(s, "review",
                            guards={"rejected": {"name": "gate_rigor_below"}}))
    out["guard-unwanted-arg"] = _mutate(
        "guard-unwanted-arg",
        lambda s: _set_node(s, "review",
                            guards={"rejected": {"name": "touches_tests", "arg": 1}}))
    out["guard-on-unbound-outcome"] = _mutate(
        "guard-on-unbound-outcome",
        lambda s: _set_node(s, "review",
                            guards={"escalated": {"name": "touches_tests"}}))

    out["node-on-terminal-state"] = _mutate(
        "node-on-terminal-state",
        lambda s: _set_node(s, "triage", entry_state="COMPLETED",
                            exit_state="COMPLETED"))
    out["agent-on-kernel-owned-state"] = _mutate(
        "agent-on-kernel-owned-state",
        lambda s: _set_node(s, "review", entry_state="BLOCKED",
                            exit_state="BLOCKED"))
    out["wait-node-off-kernel-state"] = _mutate(
        "wait-node-off-kernel-state",
        lambda s: _set_node(s, "queue_admission", entry_state="ACTIVE"))
    out["illegal-entry-exit-pair"] = _mutate(
        "illegal-entry-exit-pair",
        lambda s: _set_node(s, "implement", entry_state="QUEUED",
                            exit_state="MERGED"))
    out["illegal-outcome-transition"] = _mutate(
        "illegal-outcome-transition",
        lambda s: _set_node(s, "carve", outcomes={"done": "self_review"}))

    out["outcome-names-compiler-state"] = _mutate(
        "outcome-names-compiler-state",
        lambda s: _set_node(s, "implement",
                            outcomes={"done": "QUEUED", "incomplete": "implement"}))
    out["outcome-routes-to-kernel-node"] = _mutate(
        "outcome-routes-to-kernel-node",
        lambda s: _set_node(s, "review", outcomes={"rejected": "merge"}))
    out["absent-node"] = _mutate(
        "absent-node",
        lambda s: _set_node(s, "review", outcomes={"rejected": "ghost"}))
    out["unknown-kernel-verb"] = _mutate(
        "unknown-kernel-verb",
        lambda s: _set_node(s, "review", kernel_outcomes={"approved": "teleport"}))
    out["kernel-verb-unavailable"] = _mutate(
        "kernel-verb-unavailable",
        lambda s: _set_node(s, "post_merge",
                            kernel_outcomes={"pass": "advance", "fail": "cancel"}))

    out["undeclared-outcome"] = _mutate(
        "undeclared-outcome",
        lambda s: s["nodes"]["implement"]["outcomes"].__setitem__(
            "approved", "review"))
    out["unrouted-outcome"] = _mutate(
        "unrouted-outcome",
        lambda s: _drop(s, "implement", "outcomes", "incomplete"))
    out["duplicate-outcome"] = _mutate(
        "duplicate-outcome",
        lambda s: s["nodes"]["implement"]["kernel_outcomes"].__setitem__(
            "done", "block"))
    out["wait-resume-wrong-region"] = _mutate(
        "wait-resume-wrong-region",
        lambda s: _set_node(s, "decision_hold",
                            outcomes={"resume_implement": "carve"}))

    out["unknown-start"] = _mutate(
        "unknown-start", lambda s: s.__setitem__("start", "ghost"))
    out["unreachable-node"] = _mutate(
        "unreachable-node",
        lambda s: s["nodes"]["implement"]["outcomes"].__setitem__(
            "done", "review"))
    out["unbounded-cycle"] = _mutate(
        "unbounded-cycle", lambda s: s["nodes"]["implement"].pop("budget"))
    out["no-success-path"] = _no_success_path()
    out["merge-without-review"] = _mutate(
        "merge-without-review",
        lambda s: _set_node(s, "review", role="self-review",
                            prompt="self_review/v1"))
    out["mechanism-approves"] = _mechanism_approves()
    out["agent-holds-merge"] = _agent_holds_merge()
    out["join-policy-missing"] = _join_missing()
    out["join-policy-inconsistent"] = _join_inconsistent()
    out["duplicate-state-owner"] = _duplicate_owner()
    return out


def _no_success_path() -> dict:
    """A workflow that can start work it can never finish.

    K-escape makes SUPERSEDED and CANCELLED reachable from every non-terminal
    node, so "a terminal state is reachable" is satisfied by the kernel and
    carries no information -- this is the property that does.
    """
    return {
        "schema": _SCHEMA, "id": "no-success-path", "version": 1,
        "start": "implement",
        "nodes": {
            "implement": {
                "handler": "dispatch_agent", "role": "implementer",
                "prompt": "implement/v1", "budget": 2,
                "entry_state": "QUEUED", "exit_state": "ACTIVE",
                "outcomes": {"done": "self_review", "incomplete": "implement"},
                "kernel_outcomes": {"dead_end": "block"},
            },
            "self_review": {
                "handler": "dispatch_agent", "role": "self-review",
                "prompt": "self_review/v1",
                "entry_state": "SELF_REVIEWING", "exit_state": "SELF_REVIEWING",
                "outcomes": {"approved": "implement", "rejected": "implement"},
            },
        },
    }


def _mechanism_approves() -> dict:
    """A mechanism node holding the approval authority at AWAITING_REVIEW --
    no agent involved, and still refused: the approval must come from a
    declared independent REVIEWER, not from an automation."""
    src = _base()
    src["id"] = "mechanism-approves"
    src["nodes"]["review"] = {
        "handler": "auto_merge",
        "entry_state": "AWAITING_REVIEW", "exit_state": "AWAITING_REVIEW",
        "outcomes": {"rejected": "triage"},
        "kernel_outcomes": {"merged": "advance"},
    }
    return src


def _agent_holds_merge() -> dict:
    """An agent node owning MERGE_READY and binding `advance`: §4.3 condition
    7's "able to emit merge directly"."""
    src = _base()
    src["id"] = "agent-holds-merge"
    src["nodes"]["merge"] = {
        "handler": "dispatch_agent", "role": "review-independent",
        "prompt": "merge/v1",
        "entry_state": "MERGE_READY", "exit_state": "MERGE_READY",
        "outcomes": {"rejected": "triage"},
        "kernel_outcomes": {"approved": "advance"},
    }
    return src


def _join_missing() -> dict:
    src = _parallel_reviews("all", "cancel_siblings", "join-policy-missing")
    del src["nodes"]["review_second"]["join"]
    return src


def _join_inconsistent() -> dict:
    src = _parallel_reviews("all", "cancel_siblings", "join-policy-inconsistent")
    src["nodes"]["review_second"]["join"]["mode"] = "any"
    return src


def _duplicate_owner() -> dict:
    src = _parallel_reviews("all", "cancel_siblings", "duplicate-state-owner")
    src["nodes"]["review_second"]["role"] = "review-independent"
    src["nodes"]["review_second"]["kernel_outcomes"] = {"approved": "advance"}
    src["nodes"]["review_second"]["outcomes"] = {"rejected": "triage"}
    return src


def all_sources() -> dict:
    """The whole population: what the vocabulary oracle runs over."""
    merged = {f"valid:{k}": v for k, v in valid_sources().items()}
    merged.update({f"negative:{k}": v for k, v in negative_sources().items()})
    return merged


def non_terminal_states() -> list:
    return [s for s in TASK_TRANSITIONS if TASK_TRANSITIONS[s]]
