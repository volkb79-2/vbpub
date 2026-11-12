# controller — known issues, TODO, and backlog

Created 2026-11-12 from the backlog-filing review of the controller-side job
launchers and resume tooling. IDs are `CTL-N`, allocated sequentially in this
file (no other allocator exists).

Normative behavior belongs in this directory's `README.md`, `rules.md`, and
`docs/` guides. A FIXED entry means code, tests, and documentation landed
together.

## Status

| ID | Summary | Severity | Status |
|---|---|---|---|
| CTL-01 | Reusing a prior run name leaves its old `.done` and records in place, so completion checks can consume stale evidence | Major | OPEN 2026-11-12 |

---

## CTL-01 — a reused run name silently consumes stale completion and report records

**Status:** OPEN (filed 2026-11-12); severity: major (a caller can report a completed or passing job while the new child is still running or has failed). No implementation in this documentation-only filing.

**Problem.** `codex-impl.sh` validates the run-name grammar and immediately writes into the selected runs directory. `opencode-impl.sh` likewise opens the same named stream and error files. Neither launcher refuses an existing run record. The previous `<run>.done` remains visible until the new child writes its own marker, so `job-check.sh` can observe a stale `EXIT=0`, evaluate a stale report or gate log, and present a completed result for a job that has only just started. Brief/report paths and `.wt`/`.route`/`.thread` records can also be silently replaced or mixed across jobs. Two controller incidents observed the collision behavior with the run names `review-rg-selftest-fix` and `r2-jobs-fix`.

**Proposed minimal solution.** Add one shared run-name ownership preflight used by Codex, OpenCode, review, resume and scheduling launchers. For a new job, refuse if any record for that name already exists in the selected runs directory, including `.done`, event stream, error stream, final text, worktree binding, route, thread, or auto-resume record; print the first conflicting file and require an explicit `--force` option before replacing records. `--force` must perform one atomic, disclosed reset of the complete name-owned record set before the child starts, never delete unrelated run names or report/controller logs. A deliberate same-thread resume uses the existing resume flow and must not acquire ownership through an implicit name reuse. Keep brief/report path collisions visible by binding their resolved paths in run metadata and refusing a changed path unless forced.

**Where.** `scripts/controller/lib.sh` shared configuration; `scripts/controller/codex-impl.sh`; `scripts/controller/opencode-impl.sh`; `scripts/controller/codex-review.sh`; `scripts/controller/resume-usage-limited.sh` and `start-after.sh` where they invoke a launcher; `scripts/controller/job-check.sh` completion lookup; `scripts/controller/tests/test-wrappers.sh` and focused resume/job-check fake-root tests; `scripts/controller/README.md`, `docs/CONSUMERS.md`, and `docs/DESIGN-GUIDE.md`.

**Acceptance test idea.** Pre-seed a fake runs directory with only `<run>.done` and assert both implementation launchers refuse before overwriting any file or starting a fake child. Repeat for an event stream, `.wt`, `.route`, and changed brief/report identity. `--force` removes/replaces exactly the named run's record set, records the previous identities, and lets the fake child start. A concurrent `job-check` during a non-forced new launch can never see an old completion marker because the collision was refused. A same-thread resume retains its explicit session identity without being mistaken for a fresh name collision.

**Priority.** Major. The failure mode can falsely report success and undermine the controller's central completion-evidence guarantee.

**Close when.** Every launcher refuses pre-existing run-name records unless explicit `--force` is supplied, forced replacement is atomic and name-scoped with the prior identity disclosed, brief/report/worktree identity is bound for the run, and fake-root collision, force, resume, and job-check tests pass on a clean final commit with the user docs synchronized.
