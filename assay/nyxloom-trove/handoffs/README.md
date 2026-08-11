# assay — package series

## Current pre-adoption queue: P20–P32

P00–P26 **and P33** are merged (P33 as `e41ea99f`). P27–P32 and P34 are the
remaining implementation queue,
recarved at `2f2167f5928e5deacd93f1e9565238aef8acfe32` under canonical AUTHORING
revision `2026-08-08-r5` (A-167). They are serial on purpose: downstream
contracts are JIT-frozen after the predecessor merges, so a future packet does
not pretend uncertain signatures or fixtures are already true.

`tier: implement-2` is the only suitable live implementation band today; the
body's contract class records the planned five-band fit. Model names below are
mandatory for this semi-manual wave, not permanent product routing:

> ## ⚠ THE TABLE BELOW IS NOT IN EXECUTION ORDER
>
> **Execution order: P20–P26 (done) → P33 → P34 → \[ship\] → P27 (resumed) →
> P28 → P29 → P30 → P31 → P32.**
>
> **P33 and P34 run BEFORE the resumed P27–P32 series, despite their higher
> numbers.** Numbers are identity, not sequence
> (A-153/A-167/A-219). Do not infer order from this table; read the dependency
> chain below it. P27 is additionally **not dispatchable** until it is re-carved
> around A-217.

| # | Claim boundary | class | JIT carve | implement | independent review |
|---|---|---|---|---|---|
| P20 | repository/artifact integrity | 2c | **merged as `618b6f15`** | Sonnet xhigh | fresh Opus xhigh |
| P21 | verdict v4 + bounded Python site contract | 2b | **merged as `678104ad` (A-180–A-183)** | Opus xhigh | fresh Opus xhigh |
| P22 | committed-object snapshot substrate | 2b | **merged as `9d30b25b` (A-184–A-187)** | Opus xhigh | fresh Opus xhigh |
| P23 | exact reexecution integration over landed sites | 2c | **merged as `a7f49bb4`; fixture epoch `7c52ecc2` (A-188–A-197)** | Sonnet xhigh | fresh Opus xhigh |
| P24 | versioned wheel contract | 2d | **merged as `9f522a72` (A-198–A-201)** | Sonnet xhigh | fresh Opus xhigh |
| P25 | external Python/Topos qualification | 2d | **merged as `233926ce` (A-202–A-208)** | Sonnet xhigh | fresh Opus xhigh |
| P26 | attested-evidence CLI hardening | 2c | **merged as `8f121be3` (A-209–A-214)** | Sonnet xhigh | fresh Opus xhigh |
| P27 | Go gate and adapter resolution | 2c | drift-triggered | Sonnet xhigh | fresh Opus xhigh |
| P28 | real srdm R1 qualification | 2d | drift-triggered | Sonnet xhigh | fresh Opus xhigh |
| P29 | Go mutation-helper/site protocol | 2b | Sol xhigh required | Opus xhigh | fresh Opus xhigh |
| P30 | real Go/srdm R2 integration | 2c | drift-triggered | Sonnet xhigh | fresh Opus xhigh |
| P31 | real Go/srdm R3 canary | 2d | drift-triggered | Sonnet xhigh | fresh Opus xhigh |
| P32 | real Vitest format conformance | 2c | drift-triggered | Sonnet xhigh | fresh Opus xhigh |
| P33 | **schema v5 contract** (language-qualified operators, hoisted lane facts, equivalence bucket, kill attribution, helper provenance) | 2b | **merged as `e41ea99f` (A-220–A-233); post-review carve repair `62305df3`** | Opus xhigh | fresh Opus xhigh |
| P34 | **SQL/DDL source-mutation adapter + real PostgreSQL integration** | 2d | NEXT: JIT freeze required; two rulings owed first (A-238) | Sonnet xhigh | fresh Opus xhigh |

P20 through P26 are **MERGED**. P25's implementation-shaped contract, pinned
966-entry Topos manifest, explicit three-symlink prospective adoption patch,
clean-tagged 1.2.5 wheel/manifest, full v4 templates, literal line fixtures,
compiling harness skeleton, quick acceptance, and real network-disabled
2,923-test differential probe are under
`nyxloom-trove/carve-assets/P25/` and
`nyxloom-trove/reports/assay-P25-JIT-CARVE.md`.

P26 merged as `8f121be3` after its exact config/
safe-I/O/Git/deadline APIs, four complete v4 templates, premise probe,
skeleton, and controlled-red 41-test acceptance packet under
`nyxloom-trove/carve-assets/P26/`; A-209–A-214 resolve the R0/external-evidence
grammar, atomic aggregate bound, literal Git semantics, CLI-started deadline,
and refused-artifact lifecycle. Its controller-owned retry gate and post-merge
locked acceptance passed. P27–P32 remain `PROVISIONAL`/`JIT-FREEZE
REQUIRED`. Before any later package becomes ACTIVE, its named proof assets are
committed and the same review must return READY at the actual post-predecessor
HEAD. No package should be dispatched merely because `nyxloom lint` accepts
its header.

The dependency chain is exactly:

```text
P19 -> P20 -> P21 -> P22 -> P23 -> P24 -> P25 -> P26
    -> P33 (schema v5 contract)
    -> P34 (SQL/DDL adapter + integration)
    -> [SHIP MILESTONE: cmru release adoption, full coverage, deep adversarial pass]
    -> P27 (re-carve around A-217 option 2) -> P28
    -> P29 -> P30 -> P31 -> P32
```

**RESEQUENCED 2026-08-11 by operator decision (A-219).** The former order was
`… P26 -> P27 -> P28 -> [B001 checkpoint] -> P29 …`. SQL now goes first, and the
Go work resumes after Assay ships. Two things forced it: A-217 ruled A-O19 as
option 2, so P27 must be re-carved around a real Go statement-position helper
rather than dispatched; and the B001 assessment found that SQL R2 is blocked on a
schema migration, because v4's `mutation_operator` is a closed four-value
Python-only enum. Designing v5 once, for SQL and Go together, is cheaper than
designing it twice.

**Numbers are identity, not order.** P27–P32 keep their ids even though they now
run later: A-153/A-167 already warn that ids from before the two renumbers are
not interpretable at face value, and those ids are cited across merged packets,
briefs, decisions and reports. Renumbering a third time would corrupt that
trail. Read the chain above for order and the table for identity.

**Before P27's re-carve is dispatched (A-234/A-235):** the committed Go coverage
fixtures contradict A-172's own disproven premise and must be regenerated *and*
their consumer expectations re-derived from the option-2 oracle — regenerating
alone would encode the over-approximation as truth. And `statement_spans` has no
seam through which that oracle could be invoked, because `go_cover.py` leaves no
unattributed line; closing it needs a real interface ruling with its own probe.
Evidence: `carve-assets/P27/GO-FIXTURES-STALE.md`.

**Before P34 is dispatched (A-238):** its carve must rule on whether the flat
seven-method `LanguageAdapter` stays honest for an R2-only language (B001 item 3,
still open), and on the A-183/V5-5 tension that makes a failed SQL parser's
provenance unrecordable.

B001 is **absorbed**, not deleted: A-215's checkpoint questions are answered by
P33's design (`SCHEMA-V5-DESIGN.md`) and proved by P34's real-PostgreSQL
qualification, so there is no longer a separate un-numbered checkpoint pending.
A-215's ordering rationale — do not freeze a second-language mutation contract
before the first language is qualified — is knowingly traded away here and the
risk is named in A-219.

The apparent expansion from ten to thirteen packages is three splits, not a
microtask explosion: snapshot substrate/integration, Go adapter/real-srdm R1,
and Go helper/real R2. These are the points where one side has an independent
failure oracle and materially different model requirement. The remaining
packages stay intentionally large and solution-bearing to amortize orientation,
gate, review, and merge overhead.

P21's JIT review moved the already-designed common/Python `MutationSite` seam
forward from P23 into P21. This is not a new package: a cap cannot truthfully be
claimed while the adapter still materializes an unbounded tuple of full source
copies. P23 now consumes that seam and owns only exact snapshot reexecution,
plan reuse, and the total lane budget (A-180). Its first dispatch then exposed
the residual forbidden Go import and omitted capability terminal; the corrected
P21 seam retains adapter-wide `UNSUPPORTED` as payload-free
`INCONCLUSIVE/MUTATION_UNSUPPORTED`, while real Go discovery remains P29
(A-183).

P22's JIT tracer exposed a performance/security ambiguity in the old stateless
API: either re-pack the full history for every mutant, or optimize with an
unsafe source alternate/hardlink. The frozen API prepares one bounded private
seed and makes concurrent independent snapshots from it, with explicit caller
scratch and remaining-lane-time inputs. It also adds the missing entry/path-
total bounds, refuses alternate/shallow/partial object topologies, fixes raw
tree and child-commit grammar, and supplies the locked snapshot-limit artifact
(A-184–A-187). P23 consumes that seed exactly once per lane and closes the
artifact's ordinary conformance ownership (A-190).

P23's JIT pass resolves live vbpub's deliberate absolute-symlink conflict by
making exact R0-only execution an explicit direct path and requiring every
higher-rigor lane to preserve P22's full refusal set—never a failure fallback.
It freezes one immutable effective plan, one injected monotonic deadline,
canonical R0-led rigor order, per-unit repository/output checks, exact
mutation/canary seams, and bounded child lifecycle. Its skeleton witnesses
`13 failed, 6 passed` before implementation; the real P22 composition tracer
passes in tester-unified. P23 also owns the ordinary snapshot-limit conformance
closure and promotes shared-blob/per-path identity into P29/P30 (A-188–A-196).

P24's JIT pass proves the old gate's `0.0.0` identity came from a genuinely
absent setuptools-scm backend and replaces its incomplete ambient build with a
five-wheel hash-bound offline closure. It freezes committed-source selection,
four honest version shapes, a standalone closed-manifest verifier, pip
install-time hash rechecking, and separate build/runtime venvs. Its first real
probe also caught a subtler false PASS: two byte-identical wheels can be
reproducibly contaminated when a fixture commits ignored pycache/egg-info, so
release inputs are now Git-tracked paths/private exact-OID clones (A-198–A-200).
P23 F8's residual Git-process deadline is assigned to P26 (A-201).

P25's JIT tracer found that unmodified Topos is not yet an Assay higher-rigor
consumer: its security fixtures commit three absolute `/etc/passwd` symlinks
that P22 correctly refuses. P25 therefore qualifies one exact prospective
consumer patch deleting those links while retaining five contained symlinks,
and requires the future Topos-owned adoption to resolve the same precondition.
It also caught three targeted-green/full-red proof defects before dispatch:
ordinary add dropped four tracked ignored fixtures (13 full-suite failures), a
`PYTHONPATH`-only lane omitted required identity/PATH facts, and two pytest-cov
JSON destinations left Assay's own reserved profile empty. A forced exact
965-entry index, closed witnessed environment, and bounded byte-copy wrapper
make the 2,923-test/full-v4/Topos-evaluator proof truthful (A-202–A-206).

P25 phase-1 review then found two authoring misses despite that freeze. First,
the widened 3,600-second registered gate and the existing `assay.toml` lane
budget are mechanically coupled by an anti-drift test, but the handoff omitted
`assay.toml` from `scope.touch`; A-207 ratifies the exact `30m` → `60m`
consequence instead of discarding a green reviewed branch. Second, the packet
recorded the copied Topos evaluator's exact 5/5 and 4/5 answers but did not
force production to compare those numbers: `passed=true` also describes a
vacuous 0/0 run. Opus's repair binds the complete shared tuple to the literal
hand manifest, and A-208 carries that requirement into P28's Go qualification.
This is a genuine carver/oracle miss, not implementer discretion hidden as a
review enhancement.

P26's JIT probe reproduces the two historical attestation false PASSes and a
third process-boundary defect: directory descendant changes evade exact-name
membership, `../` keys read a seeded record outside the declared directory,
and generic Git leaves a forked pipe-holder outside its ownership. The freeze
also resolves several provisional-contract defects before dispatch. Tier-3
evidence is independent of rigor, so R0 may carry only the exact attestation
judge pair; and A-160's lane budget starts before CLI HEAD/evidence work rather
than only after higher-rigor scratch creation. The locked packet fixes the
safe missing-parent distinction, `2 * paths <= 4096` query bound, four narrow
Git calls, atomic timeout artifacts, and canary/mutation deadline forwarding
without altering their computed semantics (A-209–A-214).

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
