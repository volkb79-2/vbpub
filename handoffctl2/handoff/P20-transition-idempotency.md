# P20 — idempotent from==to transitions (kill the QUEUED->QUEUED TICK_ERROR)

> Tier: sonnet5-high · Date: 2026-07-15 · Read handoff/STANDING.md. This is a
> FROZEN-CORE-adjacent fix — touches the transition APPLY path, not the
> transition GRAPH. Independent of P16/P18/P19/P21 (different concern); do not
> touch daemon.py review/dispatch or adapters prompts (that is P21).

## The bug
The daemon intermittently logs
`TICK_ERROR TransitionError('task transition QUEUED -> QUEUED not allowed')`
(observed 2026-07-15, ~hourly, self-recovering, cosmetic but pollutes the
event log). Root cause: two planning passes computed the same edge from a
shared state snapshot (e.g. both saw CARVED and planned CARVED->QUEUED) and
the first already applied it, so the second applies from==to. The error is
raised at **`storage.py:209`** (`check_task_transition(tsf.state, to)` inside
the TASK_TRANSITIONED apply/replay branch), NOT in reconcile (whose emitters
are all state-guarded).

A daemon-layer guard already exists (`daemon.py` `_execute`, commit `fdff733`:
skip a Transition whose target == current state) but it is necessary-not-
sufficient — it only covers the daemon's own emit path, and the storage layer
is the authoritative chokepoint that also runs during **replay** of any
from==to event already in a log.

## Owned paths
- `src/handoffctl/storage.py` — the apply/replay transition branch (~line
  201-209).
- `tests/test_storage.py` (+ `tests/test_properties.py` if the graph-shape
  invariants need a companion assertion — but see the design constraint).
- Do NOT touch `reconcile.py`, `daemon.py`, `adapters.py`, `types.py`
  `TASK_TRANSITIONS` graph contents, or the review/prompt logic.

## Design constraint (get this right)
Do NOT add X->X self-edges to `TASK_TRANSITIONS` in `types.py`. The graph must
stay pure ("X->X is not a real edge") so the exhaustive
`test_properties.py::test_check_task_transition_exhaustive` keeps its meaning.
Instead make **application** idempotent: in the storage apply/replay branch,
if `tsf.state == to` for a TASK_TRANSITIONED event, treat it as a **no-op**
(do not call `check_task_transition`, do not raise, leave state unchanged)
rather than raising. This (a) tolerates historical from==to events already in
a log on replay, and (b) makes a live from==to apply a silent no-op.

Decide + document: keep the `fdff733` daemon guard as cheap belt-and-suspenders
(avoids even constructing the no-op event) — recommended — OR remove it now
that storage is authoritative. If kept, add a one-line comment cross-referencing
this package so the duplication is intentional, not stale.

## Oracles
1. Applying a TASK_TRANSITIONED event with from==to (e.g. QUEUED->QUEUED) via
   `append_and_apply` returns cleanly, leaves state == QUEUED, raises nothing,
   and appends NO spurious state change / no TICK_ERROR.
2. `replay()` over a log that CONTAINS a from==to TASK_TRANSITIONED event
   reconstructs state without raising (regression: today it would raise).
3. A genuinely invalid transition (e.g. QUEUED->MERGED) STILL raises
   `TransitionError` — the no-op tolerance is from==to ONLY, not a blanket
   relaxation.
4. Full suite green (`/workspaces/vbpub/.venv/bin/python -m pytest tests/ -q`).

## Rules
STANDING.md applies. Do not commit — receipt-only final; REPORT to
`handoff/reports/P20-REPORT.md`. If the cleanest fix turns out to require a
`types.py`/`check_task_transition` signature change (a frozen-core edit),
STOP and BLOCKED with the specific reason rather than editing frozen core
without sign-off.
