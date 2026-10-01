---
kind: backlog-entry
schema_version: 1
id: NL-18
title: "nyxloom status/resync/digest have no on-ramp for manual-controller-dispatched tasks"
status: open
type: "feature"
severity: "medium"
component: "cli"
provenance: "dstdns tool-usage-policy session 2026-09-17, CONTROLLER-BRIEF.md investigation"
filed_date: "2026-09-17"
---

**Observed mechanism.** `nyxloom status --project-id <p>` and `nyxloom resync <p>`
both read/reconcile task state entirely from statefiles + git commit-log matching,
with no daemon required — confirmed live against dstdns 2026-09-17. But both only
operate on tasks that ALREADY have a statefile entry (created by the daemon's own
intake/dispatch flow historically). A project run entirely under the manual-
controller protocol (carve a handoff → dispatch via an external agent tool →
merge) never creates that entry, so its packages are permanently invisible to
`status`/`resync`/`digest` even though the handoff files, decisions ledger, and
git history fully describe what happened. Reproduced: dstdns's `nyxloom status
--project-id dstdns` shows only its original daemon-dispatch-era tasks (P08-P39,
July 2026) — none of a concurrent from-scratch wave of 7 packages (P194-P201,
Sept 2026) carved/dispatched/merged entirely by a manual controller session
appear, and `nyxloom project --help` confirms there is no verb to register one
by hand.

**Why nyxloom owns this, not the consumer.** The manual-controller protocol is
explicitly sanctioned (dstdns AGENTS.md/CLAUDE.md's own "core-workflow" routing
table) as a first-class alternative to daemon dispatch when the daemon is
unavailable/paused — but the daemon-produced bookkeeping surface (`status`,
`digest`, `resync`) has no on-ramp for it. A consumer working around this by
inventing its own markdown ledger (as dstdns's `CONTROLLER-BRIEF.md` does) is
exactly the kind of local-workaround-instead-of-upstream-fix the estate
convention exists to avoid.

**Proposed contract.** A new verb, tentatively `nyxloom task backfill
--project-id <p> --task-id <id> --handoff <path> [--state MERGED|...]`, that:
- creates a statefile task entry from a handoff file's frontmatter (title, id)
  plus an explicit or git-derived terminal state, so it becomes visible to
  every existing read verb (`status`, `resync`, `digest`) without ever having
  gone through daemon intake;
- refuses (never silently defaults) if the task id already exists, or if the
  handoff file's frontmatter can't be parsed;
- is idempotent re-run-safe (a second backfill of the same task+commit is a
  no-op, not a duplicate).

**Behavioral oracles.**
1. Backfilling a task whose id already exists in the statefile → refusal,
   never silent overwrite.
2. Backfilling from a handoff with an invalid/missing frontmatter id →
   refusal naming the missing field.
3. A controlled wrong implementation: one that appends a duplicate row instead
   of refusing on oracle 1, or one that silently drops the `--handoff` path
   provenance so the entry can't be traced back to its source file — both are
   the failure this contract exists to prevent.
4. After a successful backfill, `nyxloom status --project-id <p>` includes the
   new task_id in its table, and `nyxloom resync <p>` treats it as a normal
   entry (no special-cased "backfilled" branch needed downstream).

**Spec section.** `docs/SPEC.md` / `docs/ARCHITECTURE.md`'s task-state model
(the same one `status`/`resync` already read) — this is additive to that
model, not a new one.

**Secondary, smaller finding (same investigation):** `nyxloom finding
record`/`nyxloom finding list` require NO such registration — they work today
against any registered project with zero prerequisite. dstdns has recorded
zero findings via this mechanism despite one wave alone producing dozens of
carve/review blockers, all currently living only as markdown prose. No tool
change needed here — this is a dstdns-side adoption gap, tracked in dstdns's
own `CONTROLLER-BRIEF.md` 2026-09-17 entry, not a backlog item on nyxloom.
