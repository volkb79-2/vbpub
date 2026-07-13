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
| B-001 | self-review | `P85-SELFREVIEW.md` | `test_pilot_snapshot_success_reports_path` and `test_pilot_snapshot_handled_exception_reports_failure` share P85's exact flaky fixed-iteration-polling pattern (`for _ in range(20): await pilot.pause()`); not fixed in P85 per its own no-sweeping scope. | Low-medium — same fix shape as P85, mechanical | Open |
