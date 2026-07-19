---
schema_version: 1
id: nyxloom-P45-ready-to-carve-triage
project: nyxloom
title: "Close the READY_TO_CARVE dead-end: review-initiated micro-carve routes to the single strategic carver"
tier: sonnet5-high
input_revision: "f38d306"
depends_on: [nyxloom-P44-role-scoped-dispatch]
session: fresh
source: {kind: product-goal, ref: nyxloom-trove/4-backlog.md}
scope:
  touch:
    - "src/nyxloom/reconcile.py"
    - "tests/test_reconcile.py"
    - "tests/test_invariants.py"
oracles:
  - id: O1
    observable: "`reconcile.py`'s REJECT LOOP (plan_project, ~L444-455, the `if tsf.state == TaskState.REVIEW_REJECTED:` block) no longer leaves the exhausted-attempts case as a documented no-op. When `rejected_attempts_count >= inp.cfg.policy.max_attempts_per_task`, plan a `Transition(task_id=fm_id, to=TaskState.READY_TO_CARVE, notes=...)` (a transition already legal in types.py's FROZEN `TASK_TRANSITIONS[REVIEW_REJECTED]` — do not touch types.py). Non-hollow anchors REQUIRED in tests/test_reconcile.py: (a) a test with `rejected_attempts_count >= max_attempts_per_task` asserts the planned action for that task IS `Transition(to=READY_TO_CARVE)`; (b) a REGRESSION PIN: a test with attempts remaining still asserts the existing `Transition(to=QUEUED)` behavior is byte-for-byte unchanged."
    negative: "The exhausted-attempts branch still plans nothing (today's behavior) — a rejected task whose attempt budget is spent is stranded forever, exactly the KNOWN GAP the current code comments document at reconcile.py L425-443."
    gate: tester-unified
  - id: O2
    observable: "`tests/test_invariants.py::test_no_dead_end_ready_to_carve` (currently `@pytest.mark.xfail(strict=True, reason=_READY_TO_CARVE_GAP_REASON)`) has its xfail marker REMOVED and passes for real: a task in `TaskState.READY_TO_CARVE` gets a non-empty, task-attributable action from `plan_project` when no carver attempt is already in flight and a healthy `frontier-review` route exists. The action MUST be (or include) an instance of the EXISTING `reconcile.CarveDispatch` dispatched through the EXISTING `daemon._execute_carve_dispatch` path (reuse `task_id=fm_id`, leave `item_id=None` — do NOT thread this task's id through `item_id`, since `_build_carve_packet`'s targeted-item path treats `item_id` as a nyxloom-trove/backlog.md item id via `backlog_items.parse`, which this rejected TASK id is not; leaving `item_id=None` correctly takes the existing untargeted carve-packet path, which already embeds 'recent REVIEW_RECORDED follow-ups' as one of its carve sources per the `_execute_carve_dispatch` module docstring — this is the review rejection's own context, already flowing to the carver for free). No new Daemon method, no new Action subclass — the point of this oracle is that the single existing carve-dispatch mechanism is the ONLY carve authority, never a second one."
    negative: "The xfail is merely deleted while the test is also weakened/rewritten to assert something trivial (e.g. 'actions is a list'), or the READY_TO_CARVE handler invents a second, parallel way to start a carver attempt instead of reusing `reconcile.CarveDispatch`/`daemon._execute_carve_dispatch` — both are hollow: the first fakes the fix, the second creates a second carve authority, defeating the whole point (the operator's explicit ask: general/strategic carving stays with ONE carver)."
    gate: tester-unified
  - id: O3
    observable: "AT MOST ONE `CarveDispatch` is ever planned in a single `plan_project` pass, shared across BOTH the pre-existing item-9 untargeted headroom-refill trigger AND this package's new READY_TO_CARVE handler: find and REUSE the exact same 'no carver attempt already in flight' predicate item 9's CARVE TRIGGER code already uses (it lives in `plan_project`, further down from the REJECT LOOP block — read it before writing a second, possibly-inconsistent check). Non-hollow anchors REQUIRED: (a) a test with a carver attempt already in flight (any non-terminal task carrying an Attempt with role CARVER) asserts a READY_TO_CARVE task gets NO new CarveDispatch that pass (it stays in READY_TO_CARVE, picked up later); (b) a test with TWO tasks simultaneously in READY_TO_CARVE asserts exactly ONE CarveDispatch total is planned across the whole returned action list, not two."
    negative: "Each READY_TO_CARVE task independently emits its own CarveDispatch with no shared in-flight guard, so N simultaneously-rejected tasks spawn N concurrent carver attempts — this is precisely the 'single strategic carver' invariant broken, the one thing the operator was explicit about NOT wanting."
    gate: tester-unified
  - id: O4
    observable: "The specific task whose READY_TO_CARVE state triggers a CarveDispatch is ALSO transitioned in the SAME pass to `TaskState.SUPERSEDED` (legal per `TASK_TRANSITIONS[READY_TO_CARVE]`) — self-limiting, the same pattern every other bookkeeping transition in this module already follows (e.g. CARVED->QUEUED, MERGED->VALIDATING), so it does not re-fire the CarveDispatch on a later pass once the carve slot frees up. Non-hollow: the action list for that task_id includes BOTH the CarveDispatch (O2) and a `Transition(task_id=fm_id, to=TaskState.SUPERSEDED, notes=...)`; a follow-up pass with that same task now in SUPERSEDED plans nothing further for it (SUPERSEDED is terminal per `TERMINAL_TASK_STATES`)."
    negative: "The triggering task is left sitting in READY_TO_CARVE indefinitely after its CarveDispatch fires, so it re-triggers a fresh CarveDispatch attempt every single subsequent pass once the in-flight guard clears — an infinite-carve loop for one stale task, and a second real-world way to violate the single-carver-in-flight invariant over time even if O3's same-pass check holds."
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "the `frontier-review` route health check or the in-flight-carver predicate needed for O3 cannot be found/reused cleanly without importing daemon.py into reconcile.py (reconcile.py is meant to stay a pure planning module, no daemon-execution side effects — see module docstring/contract) — then re-derive the SAME predicate from `ReconcileInput` fields alone (mirroring how item 9 already does this without importing daemon.py); if `ReconcileInput` is missing a field item 9 itself relies on for this check, BLOCKED and name exactly which field."
  - "closing this gap seems to require adding BLOCKED to `TASK_TRANSITIONS[REVIEW_REJECTED]` or otherwise editing types.py — it does not (READY_TO_CARVE is already a legal edge, see O1) — if you find yourself needing a types.py edit anyway, BLOCKED, do not edit the frozen table."
  - "reusing `item_id=None` for the READY_TO_CARVE carve packet loses too much context to be useful (the carver genuinely needs the rejected task's own review history, not just the generic 'recent REVIEW_RECORDED follow-ups' source note) — this is a real, known tension (see P44/P45 handoff commentary); do NOT invent a new `item_id`-adjacent field to route around it under time pressure. BLOCKED — describe exactly what daemon.py/backlog_items.py change would be needed, for review."
---

# P45 — Close the `READY_TO_CARVE` dead-end (review-initiated micro-carve, single carver)

`tests/test_invariants.py` already pins this as a known, filed absence bug
(2026-07-17, `_READY_TO_CARVE_GAP_REASON`): `TaskState.READY_TO_CARVE` has
legal outgoing edges in the frozen `TASK_TRANSITIONS` table and a
`STATE_LEGEND` entry ("waiting to become a real task (CARVED)"), but **no
code path anywhere ever assigns it, and nothing handles a task already in
it** — a `pytest.mark.xfail(strict=True)` proves this today; no backlog item
tracked it until now (`B8`/`#26`, "smart reject-triage").

Separately, `reconcile.py`'s REJECT LOOP (2026-07-16) has its own documented
`KNOWN GAP`: when a `REVIEW_REJECTED` task exhausts its attempt budget, NO
action is planned — the original handoff wanted `REVIEW_REJECTED -> BLOCKED`,
but that edge does not exist in the frozen transition table, so the exhausted
case was left stranded rather than crash the daemon.

**These two gaps are the same gap.** `REVIEW_REJECTED -> READY_TO_CARVE` IS
already legal (unlike `-> BLOCKED`). Routing the exhausted-budget case there,
and giving `READY_TO_CARVE` a real handler that re-dispatches the EXISTING
single carver (`reconcile.CarveDispatch` / `daemon._execute_carve_dispatch` —
the same mechanism `P16`'s automatic headroom-refill trigger and `P41`'s
operator-initiated `dispatch_targeted_carve` already share), closes both at
once with **zero new dispatch machinery** — which is exactly the operator's
explicit design ask: review/reject logic may recognize that a task needs
fresh, re-scoped work, but the **single strategic carver remains the only
carve authority** (no reviewer-authored ad-hoc handoff, no second dispatch
path).

## Worktree / branch

Depends on P44 (`nyxloom-P44-role-scoped-dispatch`) — carve this from
`main` **after** P44 is merged (fill in `input_revision` with that merge
commit). Create a git worktree for branch
`feat/nyxloom-P45-ready-to-carve-triage` from local `main` at
`/workspaces/vbpub/nyxloom/.worktrees/nyxloom-P45-ready-to-carve-triage` and
do all work there — never modify the main `/workspaces/vbpub/nyxloom`
checkout directly:

```
git worktree add -b feat/nyxloom-P45-ready-to-carve-triage \
  .worktrees/nyxloom-P45-ready-to-carve-triage main
```

Commit all work on that branch.

## Context to read first (read ONLY these)

- `src/nyxloom/reconcile.py` L372-456 — `plan_project`'s task-lifecycle loop,
  in particular the REJECT LOOP block (`if tsf.state == TaskState.
  REVIEW_REJECTED:`, ~L444-455) and its KNOWN GAP comment (~L425-443) — this
  is exactly what O1 replaces.
- `src/nyxloom/reconcile.py` — the CARVE TRIGGER code (module contract item
  9; further down in `plan_project`, past L470) — the existing
  "no carver attempt already in flight" / healthy-`frontier-review`-route
  predicate. REUSE it verbatim for O3; do not write a second, subtly
  different version.
- `src/nyxloom/reconcile.py` L284 area — the `CarveDispatch` dataclass (it
  already has `item_id` via its own field and inherits `task_id` from
  `Action` — no dataclass changes needed for this package).
- `src/nyxloom/types.py` L67-94 — `TASK_TRANSITIONS` (read-only; confirms
  `REVIEW_REJECTED -> READY_TO_CARVE` and `READY_TO_CARVE -> SUPERSEDED` are
  both already legal — do not edit this file).
- `src/nyxloom/daemon.py` L1656-1741 (`_execute_carve_dispatch`) and
  L1470-1553 (`_carve_source_note_lines` / `_targeted_item_note_lines`) —
  READ ONLY (out of scope/forbidden to edit this package): confirms
  `item_id=None` takes the untargeted path (embeds "recent REVIEW_RECORDED
  follow-ups" automatically) while `item_id=<backlog id>` takes P41's
  targeted-brief path — this is WHY O2 says leave `item_id=None`.
- `tests/test_invariants.py` L385-414 — `_READY_TO_CARVE_GAP_REASON` and
  `test_no_dead_end_ready_to_carve` (the xfail this package removes), plus
  whatever `_action_touches_task`/`_base_input` helpers it calls (read their
  definitions too — likely earlier in the same file).
- `tests/test_reconcile.py` — skim existing REJECT LOOP tests (if any) so
  O1(b)'s regression pin extends real coverage rather than duplicating it.

## Work

1. Extend the REJECT LOOP block per O1.
2. Add a `READY_TO_CARVE` handler to `plan_project`'s task-lifecycle loop per
   O2/O3/O4 — reusing the existing in-flight/route-health predicate from item
   9, emitting `CarveDispatch(project=inp.cfg.project_id, task_id=fm_id)`
   (item_id left `None`) plus the self-limiting `Transition(...,
   TaskState.SUPERSEDED)`, capped at one `CarveDispatch` per pass shared with
   item 9.
3. Remove the `xfail` marker from `test_no_dead_end_ready_to_carve` (it must
   pass for real, unmodified in intent — you may need to adjust its
   `_base_input` fixture if it lacks a field your predicate needs, per the
   `escalate_if` guidance above).
4. Add the O1/O3/O4 non-hollow tests to `tests/test_reconcile.py`.

## Gate

```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd /workspaces/vbpub/nyxloom/.worktrees/nyxloom-P45-ready-to-carve-triage && \
  PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -p no:cacheprovider 2>&1 | tail -80'
```

Never run this in the devcontainer/cockpit venv — only `tester-unified` counts.

## LOG/REPORT

Write `nyxloom-trove/reports/P45-LOG.md` during implementation and
`nyxloom-trove/reports/P45-REPORT.md` after (gate output, commit hash, what
each oracle's non-hollow anchor actually asserts).
