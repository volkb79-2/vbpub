# assay — repaired v1 package series

Reissued 2026-08-07 from repaired input revision `9bd7d206`. P00 and P01 are
merged. P01c repaired their contract before any outstanding implementation was
dispatched: verdict schema v2 separates computed rigor from external evidence,
adds honest command failure, and freezes PATH/attestation/cgroup semantics.

The carving rule remains:

> Put the thing whose **claim** needs attacking in its own package. Do not split
> mechanical consequences that exist only to make that one claim observable.

That rule yields thirteen outstanding packages, not twenty. Runner + emission
remain one package because both answer which command result was recorded.
Canary construction + execution remain one because neither half can prove a
known-good/known-bad pair alone. Go parsing + its adapter remain one additive
second-language proof. Only mutation construction/execution and standalone/
self-hosting are split: in each pair, either half can be green while the other
is false, and each has an independent negative.

## Series

| # | Package | Claim to attack | Depends on |
|---|---|---|---|
| P00 | skeleton and lane config | does configuration refuse to invent? | — |
| P01 | verdict model and schema v1 | does the schema reject malformed artifacts? | P00 |
| P01c | merged contract repair (revision `9bd7d206`) | are computed claims, external evidence, command failure, PATH and gate placement honest? | P01 |
| P02 | changed lines and measurability | does assay refuse to judge a diff it cannot see? | P01 + repaired input |
| P03 | coverage formats registry | is coverage format explicit and language-independent? | P01 + repaired input |
| P04 | runner, CLI, verdict emission | is the real command result recorded on every terminal path? | P02 |
| P05 | language-free evaluation core | does the four-way union work through a fake language? | P02, P03, P04 |
| P06 | Python adapter union fidelity | does Python add the union without changing core? | P05 |
| P07 | statement-span attribution | is genuine line-to-statement ambiguity ever passed? | P06 |
| P08 | Go adapter boundary proof | is the second language additive with no Go toolchain? | P03, P07 |
| P09 | cause-sensitive canary | does the whole gate reject valid known-bad input for the intended cause? | P04, P08 |
| P10 | attested evidence staleness | is a review recorded as external and rejected only when its paths are stale? | P02, P04 |
| P11 | valid mutant construction | is every mutant a valid single changed-line experiment? | P08 |
| P12 | bounded mutation execution | do tests kill those mutants under an observed job bound and clean baseline? | P04, P11 |
| P13 | standalone wheel proof | does the built wheel run offline without source/dependency leakage? | P09, P10, P12 |
| P14 | self-hosted conformance | can assay gate itself while an independent oracle remains authoritative? | P13 |

## Why the four challenged bundles changed this way

- Old P04 split at its actual seam. P05 owns the language-free four-way union;
  P06 owns Python fidelity. A fake adapter can prove P05 while a broken Python
  classifier can fail P06, so these are independent claims.
- Old P06 stays one package as P08. Coverprofile interpretation has value here
  only as the evidence fed through the Go adapter; splitting parser from adapter
  would restate the same second-language-boundary oracle and double orientation.
- Old P08 stays one package as P09. The control and transformed executions are
  the two halves of one cause-sensitive canary claim, not separate products.
- Old P10 splits into P11/P12. Syntactically valid, one-at-a-time mutants can be
  proven without executing tests; a perfect generator can still be followed by
  a runner that skips the baseline, exceeds `jobs`, contaminates source, or
  launders crashes. Those negatives do not overlap.
- Old P11 splits into P13/P14. A clean installed wheel can be proved without
  self-gating, and a source-tree self-gate can be circular despite a good wheel.
  Conflating them allowed either half to stand in for the other.

## Independent conformance is incremental, not postponed

A-041 applies as soon as P04 introduces the first real producer. P04 and every
later package that adds a producer path must add a hand-written, complete
expected-verdict artifact and compare ordinary parsed JSON field-for-field;
assay may never generate its own expected artifacts. P14 audits vocabulary
completeness and adds `assay verify` only as a secondary layer. Thus P14 is not
the first independent oracle—it is the final completeness proof.

Before P04, P02 and P03 return typed domain results and use independently
written input/output fixtures; they do not emit verdicts and cannot pretend to
have artifact conformance.

## Tier 2 reservation

Schema v2 already carries the `declared_evidence[]` / `evidence[]` sibling
shape with `(source, key)` identity for `adjudicated` and `attested` evidence.
P10 implements only attested loading. No adjudicator registry is created until
a real integration exists, so that future addition is additive rather than a
consumer-wide schema bump (A-078).

## Not in this series

Consumer adoption, any Tier-2 integration, policy/enforcement machinery,
whole-project mutation, TypeScript, and toolchain/container promotion remain
separate work. Adoption declares and verifies; it does not remediate unrelated
test debt.
