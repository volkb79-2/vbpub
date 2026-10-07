# B114 implementation log

## 2026-10-07 — resume and re-scope

- Worktree: `assay-b114-cold-witness`, CIU-managed, attached, clean at start.
- Assay base: `4670f53a67038a8a19b27ffe33e8308ec6f93fde` (Assay 8.0.0 / verdict v14).
- Current `main` advanced to `840a9791c` during this work, but the intervening commits change only `libraries/cli-extended/`; there are no intervening changes under `assay/`, `run-gate-project/`, or `tester-unified/`. Keep the B114 branch based on the last Assay-specific main and reconcile against current main before merge.
- Sol xhigh implementation plan saved at `assay-B114-PLAN-2026-10-07.md`.
- Independent read-only Sol xhigh review of P10a saved at `assay-B110-P10a-SOL-XHIGH-REVIEW-2026-10-07.md`. The review exited 0, recorded HEAD before/after as `4670f53a67038a8a19b27ffe33e8308ec6f93fde`, and left the worktree unchanged. Verdict: **REVISE**. It found the B145 ledger evidence conflict, unchecked ambient identity equality, stale review-scope/review-reference risks, incomplete receipt binding, and unresolved deadline policy. No P10a probes have committed evidence.
- P3a already defines a `BLOCKED-PARTIAL` path for the unsettled ledger anchor. Use that only for the reserved v15 wire: do not implement P10b, do not claim ledger audit support, and keep the B105 checker refusing non-null ledger use until the design is reviewed and ratified.
- B114's original v14 hard cut is stale because v14 shipped in 8.0.0. Implement a **v15 hard cut** and advance judge identity `/7` to `/8`; keep mutation-state schema version 1.
- P3b's fallback currently overclaims: a positive exit alone does not prove that a test call failed. Amend the contract so a cold or declared attempt counts as killed only with cause-sensitive, verified call-phase failure evidence. Collection/setup/teardown/auxiliary failures, signals, termination, resource events, missing or invalid receipts, and other uncertain results never become kills.
- B117/P6 is a direct B114 prerequisite for persisted deadlines and process-group termination. B115/P4 is a direct P3b prerequisite in the reviewed brief because it owns the executor loop P3b extends. B112/P1 is required for suite scope and `zz_slow`; P2/B113 code is already on main, but its remaining test acceptance must be audited. B111/P0 functionality used by P3d is present in the current gate/checker path; audit its open status rather than duplicating it.
- Keep B116/P5 out unless measured snapshot costs justify it. Keep P10b out until P10a is repaired and its pending operator choices are ratified. B118/P7 is the planned bounded pilot step after B114 and before the B131 analysis qualification.
- Parallel B087 session is active in `assay-b087-js-canary` at `cac4a92f`. It touches `assay/README.md`, `assay/docs/{CONSUMERS,DESIGN-GUIDE}.md`, and `assay/src/assay/cli.py`, which overlap this wave. I asked it to finish isolated acceptance/review but hold merge/release until coordination; do not edit its worktree or stop its job.
- No Assay tests, gates, mutation campaigns, or containers were started by this session during this review/re-scope.

## Intended sequence

1. Amend the B110/B114 contract and release-facing version references for v15, cause-sensitive kills, the P10a partial path, and the corrected dependency order.
2. Reconcile/finish B111 and B113 evidence; implement B112 suite scope.
3. Implement B117 campaign deadline and process-group termination, then B115 bounded work queue.
4. Implement B114 P3a–P3d: v15 schema/model/raw verifier, no-coverage command and baselines, cold witness, liveness disclosure, and source-bound B105 report checks.
5. Sync README, DESIGN-GUIDE, CONSUMERS, CHANGES, schemas, fixtures, backlog and evidence reports; focused tests, Sol xhigh review, then registered gates on the reviewed commit.
6. After B114 is integrated, implement B118's bounded pilot and continue with B131 on the updated Assay main.

## Evidence log

Append commands, captured exit statuses, review findings, gates, and merge/release outcomes here as they occur. Do not record a gate or probe as passing until its own exit status and receipt are captured.
