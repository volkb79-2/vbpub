# P20 REPORT — idempotent from==to transitions

Result: **done**
Date: 2026-07-16

## What changed

`src/nyxloom/storage.py`, `apply_event()`, the `TASK_TRANSITIONED` /
`TASK_BLOCKED` / `TASK_SUPERSEDED` / `TASK_CANCELLED` branch (~line 201-225):

Added a check, scoped to `EventType.TASK_TRANSITIONED` only, that treats
`tsf.state == to` as a silent no-op: skip `check_task_transition`, raise
nothing, return with the statefile completely untouched (state, since,
notes, blocker all left as-is; no task_id reported in `affected`, so
`append_and_apply` does not call `save_state` for it either). The other
three event types in that branch (`TASK_BLOCKED`/`SUPERSEDED`/`CANCELLED`)
are unaffected — their targets are fixed by the event type itself, not a
free parameter, so the from==to ambiguity only applies to
`TASK_TRANSITIONED`.

`types.py` / `TASK_TRANSITIONS` / `check_task_transition` were NOT touched —
no X->X self-edges were added to the graph. `check_task_transition` still
raises for `cur == new` when called directly (see
`test_check_task_transition_exhaustive` in `tests/test_properties.py`,
untouched and still green) — the idempotency lives only in the storage
apply/replay path, exactly as the design constraint requires.

The `daemon.py` `_execute` belt-and-suspenders guard (commit `fdff733`) was
left in place (kept, not removed) per the handoff's recommendation; it was
not touched (out of scope) and the storage-layer comment cross-references it
by commit hash so the duplication reads as intentional.

## Deviation: tests/test_properties.py also touched

The handoff explicitly names this file as in-scope ("+ tests/test_properties.py
if the graph-shape invariants need a companion assertion"), and it turned out
to be necessary: the pre-existing exhaustive property test
`test_apply_event_task_transition_enforces_graph` asserted that
`storage.apply_event` raises `TransitionError` for EVERY `(cur, to)` pair not
in `TASK_TRANSITIONS[cur]` — which, before this fix, included every
`cur == to` pair (since the graph never contains X->X edges). That is
precisely the old (buggy) contract this package removes, so the test failed
against the fix as originally written.

Changes made there:
- Added `assume(cur != to)` to `test_apply_event_task_transition_enforces_graph`
  so it now covers only genuinely invalid, non-identity edges (still raises,
  unchanged in spirit).
- Added a new companion test,
  `test_apply_event_task_transition_from_equals_to_is_noop`, exhaustive over
  every `TaskState` via hypothesis, asserting `apply_event` never raises for
  `cur == to` and leaves the statefile's `state` unchanged with `affected == []`.

No other files were touched. This satisfies the handoff's own contingency
clause; flagging it here per the STANDING review conventions.

## Per-oracle results

| Oracle | Description | Result |
|---|---|---|
| 1 | `append_and_apply` with from==to TASK_TRANSITIONED returns cleanly, state unchanged, nothing raised, no spurious event | PASS — `tests/test_storage.py::test_from_equals_to_apply_is_silent_noop`, `::test_from_equals_to_apply_does_not_overwrite_notes_or_since` |
| 2 | `replay()` over a log containing a from==to TASK_TRANSITIONED event does not raise | PASS — `tests/test_storage.py::test_replay_tolerates_from_equals_to_event_in_log` |
| 3 | A genuinely invalid transition (e.g. QUEUED->MERGED) still raises `TransitionError` | PASS — `tests/test_storage.py::test_invalid_transition_still_raises`, `::test_invalid_transition_still_raises_on_replay`, plus the pre-existing (adjusted) `tests/test_properties.py::test_apply_event_task_transition_enforces_graph` and `test_apply_event_task_transitioned_violating_graph_raises` |
| 4 | Full suite green | PASS — see gate output below |

## Files touched

- `src/nyxloom/storage.py` (edited — owned path per handoff)
- `tests/test_storage.py` (created — new, 5 tests)
- `tests/test_properties.py` (edited — 1 existing test narrowed + 1 new
  companion test added, per handoff's explicit contingency clause)
- `handoff/reports/P20-REPORT.md` (this file)

## Gate output (verbatim tail)

Command run exactly as specified by STANDING.md / the handoff:

```
cd /workspaces/vbpub/.worktrees/nyxloom-P20/nyxloom && PYTHONPATH=src /workspaces/vbpub/.venv/bin/python -m pytest tests/ -q
```

Exit code: `0`. Note: under pytest 9.1.1 in this venv, `-q` alone does not
print the final `"N passed in Xs"` summary line (progress dots to 100% only,
then the process exits 0 with no failures reported) — this appears to be a
quirk of this pytest version's quiet-mode summary suppression, not specific
to this change (verified against a scratch single-test file outside this
repo, which DOES print the summary under plain `-q` in a fresh test dir; the
suppression here is reproducible across every invocation of `pytest tests/
-q` in this repo, including before this change touched anything test-count
related). To get an actual pass count for this report, the identical
suite was re-run without `-q` (same tests, same exit code semantics,
`addopts = "-q"` is the only default in `pyproject.toml` so this is the same
collection):

```
$ PYTHONPATH=src /workspaces/vbpub/.venv/bin/python -m pytest tests/
........................................................................ [ 19%]
........................................................................ [ 38%]
........................................................................ [ 57%]
........................................................................ [ 76%]
........................................................................ [ 95%]
................                                                         [100%]
376 passed in 64.55s (0:01:04)
```

376 passed, 0 failed, 0 errors.

## Assumptions

- "Leave state unchanged" was read as "leave the whole statefile object
  unchanged" (state, since, notes, blocker all untouched), not just the
  `state` field narrowly — covered by
  `test_from_equals_to_apply_does_not_overwrite_notes_or_since`.
- The daemon-layer guard (`fdff733`) was kept as recommended; no changes
  were made to `daemon.py` (out of scope for this package regardless).

## Suggestions for the reviewer (not acted on)

- Consider whether `tests/test_daemon.py::test_transition_noop_when_from_equals_to_is_silent`
  should get a follow-up comment noting it is now belt-and-suspenders on top
  of the storage-layer fix (both layers currently tested independently;
  no code change requested here, just a documentation thought).
