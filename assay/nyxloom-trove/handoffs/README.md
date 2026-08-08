# assay — package series

## Current pre-adoption queue: P20–P32

P00–P19 are merged. P20–P32 are the only active implementation queue,
recarved at `2f2167f5928e5deacd93f1e9565238aef8acfe32` under canonical AUTHORING
revision `2026-08-08-r5` (A-167). They are serial on purpose: downstream
contracts are JIT-frozen after the predecessor merges, so a future packet does
not pretend uncertain signatures or fixtures are already true.

`tier: implement-2` is the only suitable live implementation band today; the
body's contract class records the planned five-band fit. Model names below are
mandatory for this semi-manual wave, not permanent product routing:

| # | Claim boundary | class | JIT carve | implement | independent review |
|---|---|---|---|---|---|
| P20 | repository/artifact integrity | 2c | Sol xhigh required | Sonnet xhigh | fresh Opus xhigh |
| P21 | verdict v4 evidence contract | 2b | Sol xhigh required | Opus xhigh | fresh Opus xhigh |
| P22 | committed-object snapshot substrate | 2b | Sol xhigh required | Opus xhigh | fresh Opus xhigh |
| P23 | exact reexecution integration | 2c | drift-triggered | Sonnet xhigh | fresh Opus xhigh |
| P24 | versioned wheel contract | 2d | drift-triggered | Sonnet xhigh | fresh Opus xhigh |
| P25 | external Python/Topos qualification | 2d | drift-triggered | Sonnet xhigh | fresh Opus xhigh |
| P26 | attested-evidence CLI hardening | 2c | drift-triggered | Sonnet xhigh | fresh Opus xhigh |
| P27 | Go gate and adapter resolution | 2c | drift-triggered | Sonnet xhigh | fresh Opus xhigh |
| P28 | real srdm R1 qualification | 2d | drift-triggered | Sonnet xhigh | fresh Opus xhigh |
| P29 | Go mutation-helper/site protocol | 2b | Sol xhigh required | Opus xhigh | fresh Opus xhigh |
| P30 | real Go/srdm R2 integration | 2c | drift-triggered | Sonnet xhigh | fresh Opus xhigh |
| P31 | real Go/srdm R3 canary | 2d | drift-triggered | Sonnet xhigh | fresh Opus xhigh |
| P32 | real Vitest format conformance | 2c | drift-triggered | Sonnet xhigh | fresh Opus xhigh |

Every package currently says `PROVISIONAL` or `JIT-FREEZE REQUIRED`. That is
intentional, not missing ceremony. Before ACTIVE, its named proof assets are
committed and AUTHORING's exact pre-dispatch adversarial specification review
must return READY at the actual post-predecessor HEAD. P20 is next; no later
package should be dispatched merely because `nyxloom lint` accepts its header.

The dependency chain is exactly:

```text
P19 -> P20 -> P21 -> P22 -> P23 -> P24 -> P25 -> P26
    -> P27 -> P28 -> P29 -> P30 -> P31 -> P32
```

The apparent expansion from ten to thirteen packages is three splits, not a
microtask explosion: snapshot substrate/integration, Go adapter/real-srdm R1,
and Go helper/real R2. These are the points where one side has an independent
failure oracle and materially different model requirement. The remaining
packages stay intentionally large and solution-bearing to amortize orientation,
gate, review, and merge overhead.

Luna may run the frozen-orientation/fork workflow mechanically from
`nyxloom-trove/FROZEN-WAVE-CONTROLLER-PROMPT.md`. It does not adjudicate briefs,
change contracts, choose product semantics, or replace Sol/Opus.

## Historical P00–P14 record

Reissued 2026-08-07 from repaired input revision `9bd7d206`. P00 and P01 are
merged. P01c repaired their contract before the then-outstanding implementation was
dispatched: verdict schema v2 separates computed rigor from external evidence,
adds honest command failure, and freezes PATH/attestation/cgroup semantics.

The carving rule remains:

> Put the thing whose **claim** needs attacking in its own package. Do not split
> mechanical consequences that exist only to make that one claim observable.

That rule yielded thirteen packages, not twenty. Runner + emission
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
