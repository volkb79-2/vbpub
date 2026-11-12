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
| CTL-01 | OpenCode and incomplete Codex run-name guards can reuse stale completion or mixed run records | Major | OPEN 2026-11-12 |

---

## CTL-01 — a reused run name silently consumes stale completion and report records

**Status:** OPEN (filed 2026-11-12); severity: major (a caller can report a completed or passing job while the new child is still running or has failed). `codex-impl.sh` now refuses a new run when `.done`, `.jsonl`, or `.err` exists unless `CONTROLLER_RUN_FORCE=1`; the remaining record-set and OpenCode gaps below are open.

**Problem.** The current Codex launcher checks only `.done`, `.jsonl`, and `.err` before a new run; other name-owned records such as `.last.txt`, `.wt`, `.route`, `.thread`, and auto-resume records are not part of that check. `opencode-impl.sh` has no collision preflight: it truncates the named `.jsonl` and `.err` files, then writes `.done` only after the child exits. A previous `<run>.done` therefore remains visible while the new OpenCode child runs, so `job-check.sh` can observe a stale `EXIT=0`, evaluate a stale report or gate log, and present a completed result for a job that has only just started. Brief/report paths can also be silently replaced or mixed across jobs. Two controller incidents observed the collision behavior with the run names `review-rg-selftest-fix` and `r2-jobs-fix`.

**Proposed minimal solution.** Use one run-name ownership preflight across Codex, OpenCode, review, resume, and scheduling launchers. For a new job, refuse if any name-owned record exists in the selected runs directory, including `.done`, event/error streams, final text, worktree binding, route, thread, brief/report identity, and auto-resume records; print the conflicting files before any overwrite or child start. Preserve an explicit force path (Codex currently uses `CONTROLLER_RUN_FORCE=1`) only for an intentional replacement, and make it reset the complete name-owned record set atomically while leaving unrelated runs and controller logs untouched. A deliberate same-thread resume uses the existing resume flow and must not acquire ownership through implicit name reuse. Bind resolved brief/report paths in run metadata and refuse a changed path unless forced.

**Where.** `scripts/controller/lib.sh` shared configuration; `scripts/controller/codex-impl.sh`; `scripts/controller/opencode-impl.sh`; `scripts/controller/codex-review.sh`; `scripts/controller/resume-usage-limited.sh` and `start-after.sh` where they invoke a launcher; `scripts/controller/job-check.sh` completion lookup; `scripts/controller/tests/test-wrappers.sh` and focused resume/job-check fake-root tests; `scripts/controller/README.md`, `docs/CONSUMERS.md`, and `docs/DESIGN-GUIDE.md`.

**Acceptance test idea.** Pre-seed a fake runs directory separately with each record type (`.done`, `.jsonl`, `.err`, `.last.txt`, `.wt`, `.route`, `.thread`, and auto-resume files) and assert every new-run launcher refuses before truncating a file or starting a fake child. A concurrent `job-check` while a fake child is running cannot see an old completion marker. The explicit force path replaces exactly the named run's full record set and records its previous identity. A same-thread resume retains its explicit session identity without being mistaken for a fresh name collision.

**Priority.** Major. The failure mode can falsely report success and undermine the controller's central completion-evidence guarantee.

**Close when.** Every launcher refuses the full pre-existing run-name record set by default, the explicit force path is atomic and name-scoped with prior identity disclosed, brief/report/worktree identity is bound for the run, and fake-root collision, force, resume, and concurrent job-check tests pass on a clean final commit with the user docs synchronized.
