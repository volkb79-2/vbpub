# B131 — analysis-package R2 pilot report

**Date:** 2026-10-09  
**Status:** Stage 1 implementation is provisionally merged to local `main` as
`2cad53d9`, from reviewed branch tip `b8713d56`. The full since-8.0.0 Sol
xhigh review found no code defect; its sole P1 (a missing registered gate)
was resolved by the pass below. The follow-up repair review accepted its
exact diff. The
registered `tester-unified` lane and MDT `smoke` passed on `b8713d56`.
`self-qualification-preflight` refused before launching its child because the
gates slice had 1,280,720,896 bytes of headroom against its 2 GiB requirement.
The B110 and B131 bounded R2 pilots have not run. This package does not claim
full analysis R2 qualification or make it a release requirement.

## Claim and boundary

B131 adds a separate whole-target `analysis-r2` lane over the `assay_analysis`
package and a registered `analysis-r2-pilot` gate. The deterministic selector
derives its candidate inventory from the exact unsharded Assay plan, covers
every candidate-bearing file and operator, and binds the selection and report
to the source tree and plan. The pilot runs with cold witnesses, resumable
state, a persisted deadline, host resource admission, and no verdict output.
Exit 6 can mean only that the bounded measurement completed; it is not a
qualification result.

The existing `analysis` R0/R1 lane and B105's `src/assay` lanes remain
separate. The B105 selector is not reused because its inventory requires Go
scanner sites that the analysis package does not have. A full `analysis-r2`
qualification is a later decision based on this pilot's measured candidate
count, per-file/operator coverage, elapsed-time distribution, resource peaks,
and event counters.

## Integration

- B131 lane and pilot implementation commits: `bfa33c79`, `63801dc2`.
- Latest Assay main/B114 reconciliation: merge `89608701`, with B114 at
  `5bf6db82`.
- B087 JavaScript R3 is provisionally merged at `1b5f61a9`; its retained
  dstdns canary evidence and current status are in
  [the B087 qualification report](B087-js-r3-qualification.md).
- B118 P7c artifact attestation passed registered `tester-unified` at
  `68f8a477`; its implementation is present in this integration. The
  integration reviews found seventeen progress, inventory, state and evidence
  binding gaps, now repaired here. A follow-up strengthened two behavioral
  oracles; the final remediation review accepted the exact delta.

## Validation state

The sixth exact-tree Sol xhigh review found two more defects: B110 passed
proc-fd paths that Assay's no-follow input loader refused, and it accepted a
full kill paired with R2 collection evidence. Assay now opens only the explicit
proc-fd directory link and walks its child components without following
symlinks; state-store locks and writes stay on that admitted directory. The
B110 checker binds kill evidence to execution mode. The focused B131/B110
checker suites pass **117 tests in 43.65 seconds**; safe-I/O, campaign and
pilot-path core suites pass **124 tests in 20.96 seconds**; documentation
contract and shipped-source pyflakes checks pass **57 tests in 10.57 seconds**.
Python compilation and `git diff --check` pass. The final remediation review
accepted the exact delta with no findings. The integrated `tester-unified`
gate passed on `b8713d56`; MDT `smoke` passed with 119 tests, 6 skips, and 6
subtests. The B105 preflight refused before child launch at 1,280,720,896 bytes
of gates-slice headroom (2 GiB required). Both bounded R2 pilots remain
pending; the shared launcher applies the same headroom check to the B110 and
B131 pilots. The analysis pilot's inventory, sample outcome, time projection,
worker envelope and GO decision are outstanding.

Retry the preflight after host capacity recovers. Then run `b110-pilot` and
`analysis-r2-pilot` serially, record their reports and decide from their
measurements whether either full R2 campaign is justified. A full analysis R2
run may start asynchronously only if its completed pilot supports the
B131-specific GO policy recorded from those measurements.

## B110 dependency decisions

The current dependency disposition is recorded in the
[B131 implementation plan](assay-B131-PLAN-2026-10-09.md). In brief, B114/P3,
B115/P4, and B117/P6 acceptance must close on the final integrated gates;
B118/P7c is the active prerequisite. B112/P1 requires fresh B105 timing,
while P5/B116, P9/B119, P10/B120, and P11/B121 remain deferred or decision
gated as described in that plan. B148 remains separate hardening.

## Controller update — integration repairs (2026-10-10)

The current remediation closes two review findings in the integrated B131/B110
surface. The analysis-pilot shell now accepts only the checker's exact success
marker and emits the completion marker once, after the final source and
deadline checks. The B110 survivor-screen checker now rebuilds the full
ordered B105 inventory from the committed lane declaration before accepting a
plan and verifier-valid verdict. Regression coverage includes the real
checker-to-inner-shell-to-outer-launcher marker path and a truncated plan with
a matching survivor verdict. The focused B131/B110 regression set passes
**163 tests in 83.41 seconds**; Python compilation, shell syntax, and
`git diff --check` pass. The public documentation contract passes **56 tests**.

The B087 public status is corrected from the retained dstdns evidence: both
current canaries passed, and the registered integration gate passed on
`a5659aff`; neither fact is a release claim for the current remediation tip.
B107 is already shipped in `assay-v8.0.0`. B135 and B148 are folded into B114
under A-484; the previous paragraph's separate-hardening note is superseded.

This worktree's `assay/.assay` directory was absent before the new gates, so no
prior Assay state was present to replace. The unrelated `assay-r1` run from
`rg89-p1-r2-20261009` completed R0/R1 PASS at commit `8b8d1480`; it did not run
R2 and provides no evidence for this worktree. The current remediation still
needs an exact-tip Sol xhigh review and registered `tester-unified`,
`self-qualification-preflight`, and MDT `smoke` acceptance before the B131
analysis-pilot gate can start.

## Controller update — provisional registered gate and P10 deferral (2026-10-10)

Registered `tester-unified` passed on `e568fbaf429576fef729cbd6ac852943e9858a16`
(tree `82ef718f5b6acebb7eb271ae3fc91165f0a75d70`; gate log
`/tmp/run-gate/lanes/tester-unified/de548b3853edb09a6b5cb0f0801396d4.log`).
The pass covered the self-hosted Assay lane, analysis lane, independent
self-hosting, SQL qualification and B145 process-limit probes. The receipt is
preserved under `assay/.assay/registered-gate/tester-unified.json`. This is a
provisional pass: the final direct B105 refusal regression and exact merged
tree gates are still pending.

P10a remains **REVISE**, and P10b producer/audit is deferred from this release.
The reserved non-null ledger wire remains unusable for a B105 qualification:
the source-bound B105 checker rejects it with
`ledger binding not implemented (B110-P10b)`. A direct checker regression now
asserts that refusal, rather than relying on the implementation guard alone.

## Controller follow-up — second exact-tip review repairs (2026-10-10)

The second Sol xhigh read-only review of the B131 remediation at HEAD
`1ee2a4ed6b06ec5dc4c4179f4a794717d08be486` found five issues. B131 evidence
readers now bind each regular file and its parent directory to device/inode,
link count, mode, size and timestamps, reject hard links, and recheck the
snapshot after all evidence reads. A source-commit-bound host verifier repeats
the attestation, deadline, state inventory and verdict-absence checks after the
judge container exits and before outer success. The B110 report checker now
reconciles every prior terminal with its candidate events and requires exact
pilot rigor `R0/R1/R2`. The registered lane prints the resolved commit-specific
deadline artifact path. Its output reports container exit before host
verification and outer success.

The focused B131/B110 suite passed **333 tests in 238.56 seconds**; the
marker-path regression passed separately (**1 test in 2.73 seconds**). The
final `git diff --check`, shell syntax and Python compilation checks passed.
The exact-tip review and registered gates are still pending, as are the B131
bounded measurement and separate B110 fixed pilot. This follow-up records
controller-side repairs and does not claim B114/B118/B131 acceptance yet.

The public documentation contract was rerun after the synced README,
DESIGN-GUIDE, and CONSUMERS changes and passed **56 tests in 1.44 seconds**.

## Controller follow-up — round-three review repairs (2026-10-10)

The exact-tree Sol xhigh review found three issues in the full integration and
current diff; its report is `/tmp/assay-b131-final-review-round3.md`. HEAD and
worktree-status snapshots matched before and after. The host verifier now
rehashes every attested artifact after the final source check, including
ignored summary and progress files that can change without making Git dirty.
The outer launcher performs a final host verification after its own source
recheck and emits the completion marker only after both pass.

The B110 progress checker now permits a prior resumed attempt to stop before
`resume_merged` and permits sparse candidate indexes in incomplete segments.
It still requires `resume_merged` and complete pending-index coverage when a
prior segment reports a completed `end`. A three-attempt producer-shaped
regression covers a partial first run, a sparse interrupted resumed run, and a
complete third resume.

The full focused B131/B110 suite passed **341 tests in 249.57 seconds**; the
documentation contract passed **56 tests in 0.30 seconds**. `git diff
--check`, shell syntax and Python compilation passed. The follow-up exact-tree
review, registered same-tip gates, B105 fixed pilot, and B131 measured pilot
remain pending.

## Controller follow-up — exact-tree reviews rounds seven–nine (2026-10-10)

Round seven found that interrupted attempts could claim prior candidate
dispositions that did not exist and that B110 could accept candidate indexes
detached from their identities. The shared resume-queue check now binds each
emitted candidate's selected position and pending index to dispositions seen
before that attempt, including incomplete attempts without an `end` record;
B110's sparse indexes remain supported when they address the actual pending
queue. Producer-shaped regressions cover the replayed-resume and swapped-index
cases.

Round eight found that `.assay/analysis-r2-pilot-run.log` was registered but
outside both attestations. The inner checker now snapshots and hashes that
single-link file; attestation schema 2 includes it, and the host inventory
rechecks it after container exit and after source verification. Tests cover
replacement and post-scan mutation.

The round-nine Sol xhigh review accepted the complete Assay diff with no
actionable findings. It was bound to HEAD
`1ee2a4ed6b06ec5dc4c4179f4a794717d08be486`, working-diff SHA256
`9ae7c55c8214610afcd7b77d09d0ea4289d50038927e7ab91a6a94da07beb5da`, and
matching before/after HEAD, status and untracked-file hashes. Python
compilation, shell syntax and `git diff --check` pass after these repairs.
Registered `tester-unified`, `self-qualification-preflight`, the B114 MDT
`smoke` lane, the fixed B105 bounded pilot, and the B131 analysis pilot have
not yet run on this tip. The earlier 341-test focused run predates rounds
seven–nine and is not current acceptance evidence.
