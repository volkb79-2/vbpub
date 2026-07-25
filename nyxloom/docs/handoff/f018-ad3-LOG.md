# F018 AD3 — implementation LOG

**Status:** DONE (implemented + SOLO-gated + self-reviewed). Feature-DARK
(inert unless `cfg.carve.session == "project-persistent"`).

## What shipped

The persistent carver's WARM ladder now REPAIRS a structurally-invalid carve
proposal before ingesting new feeds. Files (all within the handoff's `Touch`):

- `src/nyxloom/carver_session.py` — `CarveRepairRequest` pure planner-view DTO
  (no fold change).
- `src/nyxloom/reconcile.py` — `ReconcileInput.pending_carve_repairs` + the
  WARM-ladder repair slot (`admit > repair > merge-feed > compaction`,
  demoting the merge-feed `if` to `elif`). Plans
  `ResumeCarverSession(mode="repair-proposal", generation)` with NO
  `source_ids`. Planner stays pure.
- `src/nyxloom/daemon.py` — `_pending_carve_repairs` (reuses P3b's exact
  per-artifact counting, gated `1 <= invalid < max_proposal_repairs` so it
  composes with `_carve_proposal_repair_escalations` by a single `<`/`>=`
  boundary and never double-fires); `_build_input` wiring; the
  `_build_carver_resume_prompt` `repair-proposal` write-authority packet
  (invalid ids + 5-point re-validation checklist). No new dispatch path —
  `repair-proposal` was already in `_CARVE_WRITE_AUTHORITY_MODES` and flows
  through the existing resume executor + exit-consumption unchanged (which
  records the corrected `CARVER_PROPOSAL_RECORDED` and skips the
  merge-feed-only cursor advance).

No new `EventType` / `TaskState` / `TASK_TRANSITIONS` edge. Oracles O1–O7 pass
(O2 compose/no-double-fire and O5 cursor-untouched are the load-bearing ones).

## Discovered pre-existing defect (OUT OF AD3 SCOPE — needs its own package)

While writing O5 (a full launch → exit → re-fold cycle, the way the live
daemon replays the log every tick) I found a **pre-existing frozen-core
projector bug** in `carver_session.project_session`:

- `CARVER_PROPOSAL_RECORDED` sets `snap.last_turn_sequence = payload["turn_id"]`
  — a **string** (the turn/task id, e.g. `carver-session-demo-3`) — at
  `carver_session.py:216`, while the field is typed `int | None`.
- `CARVER_SESSION_RESUMED` then does `snap.last_turn_sequence += 1`
  (`carver_session.py:200-201`).
- So **any `CARVER_PROPOSAL_RECORDED` followed by any `CARVER_SESSION_RESUMED`**
  makes the fold raise `TypeError: can only concatenate str (not "int") to str`.

Reachability / severity:
- **Pre-existing, not AD3-specific.** Confirmed with a minimal repro that two
  ordinary merge-feed/carve turns around one recorded proposal crash the fold
  — no repair mode involved.
- `daemon._carver_session` wraps only `storage.iter_events` in try/except, NOT
  the `project_session(events)` call (`daemon.py:1904-1908`), so the exception
  propagates out of `_build_input` → `run_pass` as a **persistent per-tick
  TICK_ERROR** once such an event ordering exists in a project's log.
- AD3's repair flow *reliably* produces this ordering (invalid
  `CARVER_PROPOSAL_RECORDED` → repair `CARVER_SESSION_RESUMED`), so this is a
  **hard blocker for F018 `project-persistent` enablement** — but enablement is
  already the operator's pending decision and the feature is DARK, so nothing
  regresses today.

Why not fixed here: the projector fold is on AD3's explicit FORBID list, and
the fix is a genuine design call (should `last_turn_sequence` hold the turn id
as-is and drop the `+= 1`, or should it be a real int counter incremented only
by RESUMED and never overwritten by the proposal's turn_id?). That belongs in
its own small frozen-core package with an oracle that re-folds a
proposal-then-resume stream. O5 therefore asserts the cursor invariant on the
RAW event log (no `project_session` call) so it stays in-scope and green.

**Recommended follow-up package (P2, pre-enablement):** fix
`project_session`'s `last_turn_sequence` handling; oracle = `project_session`
over `[STARTED, RESUMED, PROPOSAL_RECORDED, RESUMED]` returns a snapshot
without raising, with a well-defined `last_turn_sequence`.
