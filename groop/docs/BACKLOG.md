# Backlog — identified, not yet carved

Distinct from two other trackers:
- `DECISIONS-INBOX.md` — **product** decisions needing the user's judgment
  (auth posture, framework picks). Not engineering work items.
- `ROADMAP.md` — large, already-scoped feature areas and package history.

This file is for concrete engineering work that someone identified — a
follow-up, a deferred fix, a named-but-unfixed flake, a "worth a package"
note — that didn't get carved into a handoff *this* cycle. Its purpose is to
make sure that insight survives past the session that found it, instead of
sitting undiscoverable in a REPORT/REVIEW/SELFREVIEW file until someone
happens to reread it.

## Who writes here

Any implementer, self-review, or frontier-review session that identifies
follow-up work it is not carving right now appends an entry. Frontier
reviewers are the primary source (post-merge, warm context) but are not the
only one — a self-review that spots a second, out-of-scope instance of a bug
it just fixed should log it here rather than only mentioning it in prose.

## Who reads here

**The carver, every cycle.** Per `docs/controller-workflow-v2.md` §8, the
carver picks its next handoffs by priority across ALL sources — this
backlog, `ROADMAP.md`'s open items, and standing product goals — not by a
fixed quota per source. An item picked up gets its entry marked `Carved` with
the resulting package ID; it stays in the table (struck through or noted) for
audit trail rather than deleted, until a periodic prune.

## Entry schema

| Field | Meaning |
|---|---|
| ID | `B-0XX`, monotonic |
| Source | review-derived / self-review / implementer-report / scan-backfill |
| Origin | file:line or package ID this was found in (e.g. `P85-SELFREVIEW.md`) |
| Finding | one or two sentences: what's wrong or missing |
| Priority | urgency/impact/importance, carver's call each cycle — not fixed at write time |
| Status | Open / Carved (→ package ID) / Declined (with reason) |

## Entries

| ID | Source | Origin | Finding | Priority | Status |
|---|---|---|---|---|---|
| ~~B-001~~ | self-review | `P85-SELFREVIEW.md` | `test_pilot_snapshot_success_reports_path` and `test_pilot_snapshot_handled_exception_reports_failure` share P85's exact flaky fixed-iteration-polling pattern (`for _ in range(20): await pilot.pause()`); not fixed in P85 per its own no-sweeping scope. | — | **Done** — fixed in P85 frontier review (pass #2). "No sweeping" forbids fixing them *silently*, not fixing them; leaving a test the package's own evidence showed failing would have defeated the package. Mutation-tested + 20x green. |
| B-002 | review-derived | `P85-REVIEW.md` (found validating from `main` post-merge) | `_wait_for_frame` — the shared first-frame helper most UI tests depend on — still uses `for _ in range(10): await pilot.pause()`, the same shape as the repaired flakes. Its producer has no `time.sleep` and it did not flake in 20x stress or 5 full-suite runs, so it was named rather than changed: rewriting the helper every UI test hangs off is a blast radius that wants its own package *if* it ever actually flakes. | Low — not observed flaky; revisit on first sighting | Open |
| B-003 | review-derived | `P84-REVIEW.md` | The P75 `mcp-smoke` acceptance tests **fail** rather than skip when the `mcp` extra is absent (3 failures, e.g. `test_subprocess_mcp_smoke_json_no_daemon` asserts exit 1, gets 0). P75's handoff required it to "skip honestly (and distinguishably) when the `mcp` extra is absent". P84 now pins `mcp` in `[dev]` so the gate env never hits this, which *masks* it — a contributor without the extra still gets three confusing failures instead of a clean skip. | Medium — small fix, but it is a broken degradation contract in an acceptance leg | Open |
| B-004 | review-derived | `P83-REVIEW.md` | The `ciu-grouped` view mode is wired into `action_toggle_view` but never exercised end-to-end: no test presses `F5` to reach it and asserts the app renders it. Every P83 test drives the renderer functions directly, so the app-level wiring (view cycling, status bar, drill-down rejection of synthetic `__group__` row keys) is unproven. P83's own REPORT lists this as its known gap #1. | Medium — a wired-but-untested view is one refactor away from silently breaking | Carved → **P86** |
