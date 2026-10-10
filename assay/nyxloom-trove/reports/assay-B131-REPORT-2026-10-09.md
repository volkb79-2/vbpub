# B131 — analysis-package R2 pilot report

**Date:** 2026-10-09  
**Status:** Stage 1 implementation is provisionally integrated on
`assay-b131-r2`. The exact-tree Sol xhigh review of the final remediation
delta is accepted. Registered gates and the measured analysis pilot remain
pending. This package does not claim full analysis R2 qualification or make
it a release requirement.

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
accepted the exact delta with no findings. Registered gates and the measured
analysis pilot remain pending. The analysis pilot's inventory, sample
outcome, time projection, worker envelope and GO decision are outstanding.

After integrating the reviewed B118 fix, run the final registered acceptance
serially on the same tip: `tester-unified`,
`self-qualification-preflight`, the B114 MDT `smoke` lane, then
`analysis-r2-pilot`. Record actual gate/container identities and exit markers
here. A full analysis R2 run may start asynchronously only if the completed
pilot supports the B131-specific GO policy recorded from its measurements.

## B110 dependency decisions

The current dependency disposition is recorded in the
[B131 implementation plan](assay-B131-PLAN-2026-10-09.md). In brief, B114/P3,
B115/P4, and B117/P6 acceptance must close on the final integrated gates;
B118/P7c is the active prerequisite. B112/P1 requires fresh B105 timing,
while P5/B116, P9/B119, P10/B120, and P11/B121 remain deferred or decision
gated as described in that plan. B148 remains separate hardening.
