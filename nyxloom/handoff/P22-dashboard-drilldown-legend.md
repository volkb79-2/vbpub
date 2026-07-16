# P22 — dashboard: state legend + read-only agent drilldown (live attach)

> Tier: sonnet5-high · Date: 2026-07-16 · Read handoff/STANDING.md. Encodes
> three user observations (2026-07-16): (a) the dashboard says "nothing
> running" while an agent IS in flight; (b) `interrupted-dead-end` and other
> states are unexplained — need a legend; (c) we want a read-only drilldown to
> follow a running agent's live output/reasoning. Zero-AI, read-only dashboard
> only. Depends on P21 (edits the same daemon.py dashboard region) — carve
> AFTER P21 merges; rebase onto it.

## Owned paths
- `src/nyxloom/daemon.py` — the dashboard HTTP handler / routes
  (`_start_http`, the Handler class ~line 1140-1186) and any HTML it emits.
- `src/nyxloom/render.py` — if the dashboard HTML/table is rendered there.
- `tests/test_daemon.py` and/or `tests/test_render.py`.
- Do NOT touch reconcile.py, storage.py, types.py, adapters.py, or ciu/docker.

## Work
1. **State legend.** Add a always-visible legend to the dashboard explaining
   each TaskState and the common attempt/blocker reasons in plain language —
   especially `interrupted-dead-end` (= the agent's CLI leg was interrupted
   mid-run and could not resume, so it dead-ended to BLOCKED; needs a manual
   resume/reset — pre-P17 stream-json capture gap), plus AWAITING_REVIEW,
   MERGE_READY, REVIEW_REJECTED, NEEDS_DECISION, BLOCKED. Source the text from
   a single dict so it stays in sync with the enum.

2. **"Nothing running" fix.** The board renders task STATEFILE state, which can
   lag behind reality (a dispatched task whose QUEUED->ACTIVE write was lost
   still shows QUEUED though its wrapper + `claude -p` child are live). Surface
   ATTEMPT-level liveness: for each task, if it has an attempt whose
   receipt.json is absent AND whose wrapper/child pid is alive (or attempt
   state RUNNING/PREFLIGHTING), show a "● running" indicator with the attempt
   id, regardless of the task statefile state. (P20 fixes the root state-write;
   this makes the UI robust to any residual lag.)

3. **Read-only agent drilldown (live attach).** Clicking a running/recent
   attempt opens a read-only view that tails that attempt's
   `attempts/<att-id>/attempt.log` (the claude stream-json transcript) and
   renders it as readable text — extract the assistant `text` deltas and tool
   names from each stream-json line into a human-readable running log
   (reasoning/output), newest last, bounded to the last N KB. This is the
   "follow its output/reasoning" surface. READ-ONLY: no controls that mutate
   state, no ability to send input to the agent. Poll/refresh is fine (the
   dashboard is already a polling read-only page); no websocket required.

## Oracles
1. The dashboard HTML contains a legend entry for `interrupted-dead-end` (and
   the other states) with explanatory text.
2. Given a task with a live attempt (receipt absent, attempt RUNNING), the
   rendered board marks it running even if the task statefile is QUEUED.
3. The drilldown endpoint for an attempt returns the human-readable rendering
   of that attempt's attempt.log stream-json (assistant text + tool names),
   not raw JSON, and never exposes a mutating control.
4. Full suite green.

## Rules
STANDING.md applies. READ-ONLY dashboard — no new AI/token use, no state
mutation. Notification-injection boundary is irrelevant here (local dashboard),
but the drilldown must HTML-escape agent text (it is untrusted CLI output) to
avoid injection into the dashboard page. Do not commit (worktree-merge flow:
the controller creates the worktree/branch and merges) — receipt-only final;
REPORT to handoff/reports/P22-REPORT.md.
