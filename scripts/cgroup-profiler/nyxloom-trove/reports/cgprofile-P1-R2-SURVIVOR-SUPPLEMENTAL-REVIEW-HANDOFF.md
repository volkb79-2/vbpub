# P1 R2 survivor-oracle review — supplemental handoff, not a numbered round

The caller launches a genuinely fresh GPT-6-Sol/xhigh session. The reviewer
must not attest its own model or effort; the invocation/session metadata is
the route evidence.

## Context to read first

Read these sources in order; the controller supplies the exact current HEAD
and route in the invocation:

1. `AGENTS.md`; `nyxloom/reference/AUTHORING.md` §3b; and
   `nyxloom/reference/DOCTRINE.md` §§1, 2, 4, 5. There is no
   `scripts/cgroup-profiler/nyxloom-trove/AUTHORING.md` or `DOCTRINE.md`.
2. `run-gate-project/nyxloom-trove/WAVE-PLAN-2026-09-12-rg55-profiling.md`
   §5-P1 and the summary contract in
   `run-gate-project/nyxloom-trove/RG55-INTERFACE-CONTRACT.md` §§3, 7.
3. `scripts/cgroup-profiler/lib/summary.py` (the order-statistic multiset and
   `SummaryAccumulator.add_sample` / finalization paths),
   `scripts/cgroup-profiler/lib/targets.py` (proc-stat parsing), and the
   exact `main`-to-candidate diff in `tests/test_summary.py` and
   `tests/test_targets.py`.
4. Only after forming an independent test/oracle view, read the old-tree R2
   evidence at
   `/workspaces/vbpub/.worktrees/rg55-p1-r2-isolated/.worktrees/rg55-p1-r2-current-20261001/scripts/cgroup-profiler/.assay/verdict-r2.json`,
   `r2-progress.jsonl`, and `.run-gate/history.json` as separate artifacts;
   then read the survivor table in `cgprofile-P1-DAEMON-REPORT.md`, the
   controller entries in `cgprofile-P1-DAEMON-LOG.md`, and P1 review round 6.
   That R2 judged only commit `10a344e2` / tree
   `9c084d1f29783c6a61b24d186befdb132813f7fa`; it is a failed historical
   result, not evidence for the current candidate.
5. For the current daemon/runtime evidence boundary, inspect
   `cgprofile-P6-FOLLOWUPS-REVIEW-round7-fix-verification.md` and verify
   whether runtime/deployment files after `d48d1ed8` are unchanged. The
   P1-only supplemental delta must not be represented as a new full daemon
   review.

## Work

1. Confirm the selected branch is attached to its recorded CIU identity, the
   worktree is clean, and its exact HEAD matches the controller's invocation.
   Compare the direct tree delta with current `main` (not a three-dot diff).
2. Independently assess all 11 prior R2 survivors, before reading their
   authored dispositions: seven oracle gaps and four claimed equivalents.
   For each, state the actual behavior that distinguishes a defect from a
   valid implementation, and identify the exact test or a rigorous
   contract-based equivalence proof.
3. Adversarially evaluate the new tests themselves:
   - the ascending/descending insertion invariant oracle for both AVL
     threshold mutants;
   - duplicate compression for both insertion-branch mutants;
   - later unreadable usage and non-positive timestamp deltas after a valid
     earlier CPU pair;
   - the empty `/proc/<pid>/stat` comm boundary.
   Reject a test that only checks an incidental implementation detail or
   allows the named wrong behavior to pass. Check determinism, global-state
   isolation, order independence, and no wall-clock verdicts.
4. Independently determine how the current Assay/R2 policy handles a proven
   equivalent mutant. Do not call a mechanically red R2 lane green based only
   on a prose equivalence claim. Do not add brittle tests solely to encode an
   internal AVL shape. If the accepted policy cannot represent the four
   equivalents, report the exact remaining release blocker and a behavior-
   preserving, policy-compliant route to a green lane.
5. Review the inherited live-probe boundary. If runtime and deployment files
   are byte-identical to the P6 repair tree at `d48d1ed8`, cross-check the
   P6 fix-verification live evidence and say exactly which proof transfers;
   the supplemental branch does not change daemon behavior. If that identity
   check fails, follow the safe reviewer-owned live-probe recipe and
   restrictions in `cgprofile-P1-DAEMON-REVIEW-HANDOFF.md`; never start,
   stop, replace, or attach the main singleton or touch `/workspaces/dstdns`.
   Before each reviewer-owned container launch, require memory PSI `full
   avg10 <= 5`, a loaded authored cgroup parent, private PID/cgroup/network
   namespaces, and an immediate verified 3-CPU cap. Never use host
   namespaces, `ciu up/down`, or Docker network mutation. Remove only exact
   reviewer-owned containers by name.
6. Write a separate report at
   `scripts/cgroup-profiler/nyxloom-trove/reports/cgprofile-P1-R2-SURVIVOR-SUPPLEMENTAL-REVIEW-20261002.md`.
   This is not “round 7”: round 6 completed the numbered series. Start with
   the verdict; include candidate SHA, actual diff scope, each of the 11
   dispositions, evidence limits, and any blockers. Do not edit old review
   reports or claim release/daemon activation clearance.
7. If a test-only correction is necessary, make it only in the two named test
   files or this supplement, preserve the initial finding, and verify the fix
   in this same live reviewer session. Do not edit daemon implementation,
   deployment, contract, or unrelated main files. Do not merge, release,
   install, run R2/full gates, or commit; the controller owns those actions
   and will run exact-tip gates after the review artifact is finalized.

## Oracles

| Claim | Observable / negative | Owning gate |
|---|---|---|
| Each of the seven previously surviving defects is now behaviorally rejected | The named regression fails under that exact mutation and passes with restored source; a wrong AVL balance, duplicated node state, stale CPU maximum, or rejected valid empty comm must not pass | Current exact-tree Assay R2; registered `r0-r1` proves the test and changed-line coverage |
| The four equivalence dispositions are genuine | A contract-level proof shows identical observable summary and required complexity behavior for every reachable input; if not, a behavioral oracle distinguishes them | Current exact-tree Assay R2 plus independent review; prose alone cannot change its verdict |
| New tests are stable | No schedule/time/order/global-state dependency; the exact assertions check behavior, not merely calls or incidental log strings | Registered `./run-gate.py r0-r1`; controller reruns it after review record commit |
| R3 canaries remain rejected | All registered canaries rejected on exact final tip | `./run-gate.py r3`, controller-owned after review |
| Runtime evidence remains current | Runtime/deployment identity with `d48d1ed8` is mechanically verified, or safe live probes are recorded with exact resources and outputs | Prior P6 fix-verification live receipts only where byte-identical; otherwise reviewer-owned probe per the original P1 handoff |

Prior focused tests (176 passed) and short-gate receipts on `38e38349` are
historical only; current main reconciliation changed the tree. The controller
must run fresh exact-tip `r0-r1`, `r3`, doctor, R2, and full gate as appropriate.
Under the operator's provisional-integration policy, only the long R2/full
gate may continue asynchronously after the supplemental review and short
gates; the R2 result remains a release hold until green and fully dispositioned.

## Scope and forbidden actions

- Review only the supplemental P1 oracle/evidence delta relative to current
  main. Existing daemon implementation is not yours to rewrite in this
  review.
- Never touch `/workspaces/dstdns`, unrelated containers, networks, worktrees,
  or another campaign. Do not inspect or stop the unrelated R2 container
  observed at `2026-10-02T00:53Z` (`run-gate-vbpub-r2-1516264-1790900601`).
- Do not detach the managed P1 worktree, change its identity, or run a gate
  while the candidate HEAD is moving.

## BLOCKED rule

If the exact candidate, required old-tree evidence, or required current runtime
identity cannot be established; if safe probe prerequisites fail; or if a
requested conclusion would require editing a forbidden product surface, write
`BLOCKED: <exact condition>` in the supplemental report, name the missing
observable and read-only checks attempted, preserve all existing state, and
stop only that probe. Continue the scoped static review and report what did
pass; never invent a product ruling or substitute unsafe host access.
