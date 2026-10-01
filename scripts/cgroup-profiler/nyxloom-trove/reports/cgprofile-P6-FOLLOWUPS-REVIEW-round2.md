# cgprofile P6 follow-ups — controller review round 2

**Reviewer:** Luna xhigh controller (not the independent Sol xhigh release
review)

**Reviewed tree:** `cc9d13b60cbd734b889eb4b4196a30c07104fb39`

**Date:** 2026-09-23 14:39:08Z

**Verdict:** ACCEPT-CONDITIONAL

## Scope

This is a fresh controller pass after reconciling the P6 branch with current
`main`. The reconciliation adopts the current source-backed Assay tree,
including B101's shallow snapshot seed, while retaining the reviewed P6
cgprofile implementation. The package path has no source delta after the
prior controller review at `54e0a364`; the merge changes the dependency
source and current-main documentation/history around it.

I rechecked the daemon's socket and exec carriers, strict wire validation,
placement write whitelist and refusal paths, liveness state transitions and
kill finalization, DAMON degradation, sample-zero accounting, retention and
recovery, history/summary/report serialization, versioned contract fixtures,
the user-facing documentation trio, and the P6 backlog identities. The
contract fixture mirrors remain byte-identical. The reconciled focused suite
passes `445 passed, 6 skipped`; `git diff --check` passes. The cockpit's
ordinary full collection is not evidence because it lacks the report-tier
NumPy dependency.

No new merge-blocking defect was found. The earlier controller findings and
the CP-11 out-of-scope follow-up remain unchanged.

## Conditions before release

This conditional acceptance does not replace the required registered
`r0-r1`/`r3` gates, exact-tree R2 mutation evidence and survivor disposition,
fresh independent Sol xhigh review, or current real-daemon probes. Any
non-equivalent survivor or regression requires a backport and revalidation.
