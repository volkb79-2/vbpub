# B131 — analysis-package R2 pilot report

**Date:** 2026-10-09  
**Status:** Stage 1 implementation is provisionally integrated on
`assay-b131-r2`. Final exact-tip review, registered gates, and the measured
analysis pilot remain pending. This package does not claim full analysis R2
qualification or make it a release requirement.

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
- B118 P7c artifact attestation is being reviewed and its latest race fixes
  have not yet been integrated into this branch.

## Validation state

The B131-focused suite passed **183 tests in 51.77 seconds** after latest-main
reconciliation, before B087 and B118 P7c integration. The merged tip still
needs an independent exact-diff Sol xhigh review and the registered gates.
The analysis pilot itself has not run; there is no measured plan inventory,
sample outcome, time projection, worker envelope, or GO decision yet.

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
