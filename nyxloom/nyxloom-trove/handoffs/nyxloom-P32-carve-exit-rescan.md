---
schema_version: 1
id: nyxloom-P32-carve-exit-rescan
project: nyxloom
title: "Finalize carve tasks whose exit pass was missed (CARVER re-scan gap)"
tier: sonnet5-high
input_revision: "593a585"
depends_on: [nyxloom-P26-daemon-resume-safety]
session: fresh
source: {kind: backlog, ref: nyxloom-trove/backlog.md}
scope:
  touch:
    - "src/nyxloom/reconcile.py"
    - "tests/test_reconcile.py"
  forbid:
    - "src/nyxloom/daemon.py"
    - "src/nyxloom/config.py"
oracles:
  - id: O1
    observable: "In `reconcile.plan_project`, an ACTIVE carve task (`task_id` matching `^carve-.*-\\d+$`) whose latest attempt has `role == Role.CARVER`, `state == EXITED`, and a receipt — and whose task transition is still pending — yields an `EmitAttemptExit` action (so daemon.py's existing CARVER branch runs `_consume_carve_exit` -> retires it to SUPERSEDED, freeing the carve/wip slot). A unit test in tests/test_reconcile.py builds that ReconcileInput and asserts the actions contain EmitAttemptExit for the carve task."
    negative: "the EXITED carver attempt is never re-scanned (the current trigger matches only IMPLEMENTER and FRONTIER_REVIEW roles), so a carve whose live exit-pass was missed — e.g. a daemon restart landing on the carver's exit — stays ACTIVE forever and permanently consumes a wip slot (observed 2026-07-16: carve-nyxloom-1 stuck ACTIVE ~2h, throttling the factory to 2/3 capacity)."
    gate: tester-unified
  - id: O2
    observable: "The added CARVER branch is bounded exactly like the existing IMPLEMENTER/FRONTIER_REVIEW ones: it fires ONLY for state==EXITED + role==CARVER + task ACTIVE + a pending transition, and does NOT re-fire once the task is SUPERSEDED/terminal (idempotent — no duplicate EmitAttemptExit across passes). A test asserts no EmitAttemptExit is emitted for an already-SUPERSEDED carve task."
    negative: "the branch re-emits EmitAttemptExit every pass for an already-finalized carve (event spam), or fires for a non-carve task"
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "finalizing the carve cannot be done by extending the EmitAttemptExit trigger alone and requires changing daemon.py's _consume_carve_exit (forbidden — the handler already works; only its trigger is missing)"
---

# P32 — Finalize carve tasks whose exit pass was missed (CARVER re-scan gap)

A crash/restart-robustness fix. The daemon finalizes a carve via
`_consume_carve_exit` (retires the synthetic carve task to SUPERSEDED, clearing
reconcile.py's carve slot) — but that handler is only reached when the reconcile
planner emits `EmitAttemptExit` for the carver's EXITED attempt. The planner's
re-scan for a pending exit transition matches `role == IMPLEMENTER` and
`role == FRONTIER_REVIEW` but **not `role == CARVER`**. So if the live pass that
would have processed the carver's exit is missed (a daemon restart landing on
the exit), the carve is never finalized and its task is stranded ACTIVE,
permanently eating a wip slot. Add the missing CARVER branch so the re-scan
finalizes it on any later pass.

## Worktree / branch

The daemon runs this on a dedicated `implement` branch in its own git worktree
under `.worktrees/` (branch `feat/nyxloom-P32-carve-exit-rescan` from `main`);
commit all work on that branch. Do not touch the main checkout.

## Context to read first (read ONLY these, in order)

- `src/nyxloom/reconcile.py` — the attempt-lifecycle scan in `plan_project`
  (~lines 380-400): the condition that includes an EXITED attempt of an
  ACTIVE task for `EmitAttemptExit`. It currently has a branch for
  `role == Role.IMPLEMENTER` and one for `role == Role.FRONTIER_REVIEW`. Add
  the analogous `role == Role.CARVER` branch (carve tasks have no review, so —
  like the others — an EXITED carver attempt of an ACTIVE task with a pending
  transition should yield `EmitAttemptExit`).
- `src/nyxloom/daemon.py` `_consume_carve_exit` (~1231) — the EXISTING handler
  the action reaches (retires the carve to SUPERSEDED). READ for context; do
  NOT edit it (forbidden) — it already works; only its trigger is missing.
- `tests/test_reconcile.py` — mirror the existing test that exercises the
  IMPLEMENTER/FRONTIER_REVIEW EmitAttemptExit branch to build the CARVER fixture.

## Work

1. `src/nyxloom/reconcile.py`: add a `role == Role.CARVER` branch to the
   EmitAttemptExit trigger, bounded identically to the existing role branches
   (state EXITED + task ACTIVE + pending transition; idempotent once terminal).
2. `tests/test_reconcile.py`: prove O1 (CARVER EXITED -> EmitAttemptExit) and
   O2 (no re-fire once SUPERSEDED; no fire for non-carve).

## Scope / forbid

Touch ONLY `reconcile.py` + `tests/test_reconcile.py`. Do NOT edit `daemon.py`
(the handler is correct) or `config.py`.

## BLOCKED rule

If finalizing the carve provably requires more than extending the
EmitAttemptExit trigger (i.e. a daemon.py change), STOP — write
`BLOCKED: <reason>` to the LOG, commit, and exit. Do NOT improvise.

## Gate

`tester-unified`:
```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd {worktree}/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
```
