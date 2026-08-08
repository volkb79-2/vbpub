---
schema_version: 1
id: assay-P30-real-go-mutation-r2-integration
project: assay
title: "Real Go tests judge bounded site mutations through the installed R2 pipeline"
tier: implement-2
input_revision: "2f2167f5928e5deacd93f1e9565238aef8acfe32"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P29-go-mutation-helper-protocol]
session: resume:assay-go-mutation
scope:
  touch: ["gate/go/**", "src/assay/cli.py", "src/assay/registry.py", "src/assay/mutation.py", "tests/fixtures/go/**", "tests/**", "README.md", "docs/DESIGN-GUIDE.md"]
  forbid: ["src/assay/adapters/base.py", "src/assay/adapters/python.py", "src/assay/adapters/go.py", "src/assay/config.py", "src/assay/canary.py", "src/assay/verdict.py", "src/assay/verify.py", "src/assay/schemas", "nyxloom-trove/nyxloom.toml"]
oracles:
  - id: O1
    observable: "The installed CLI preflights the resolved Go helper, advances only Go to R2, discovers selected sites at max+1, and submits at most max_mutants candidates in deterministic identity order"
    negative: "Missing helper is mistaken for NO_MUTANTS, Python capability changes, or candidates are truncated after submissions start"
    gate: tester-unified
  - id: O2
    observable: "Each submitted site becomes one exact byte replacement in its own P22 snapshot and runs P23's unchanged command plan; no full mutated-file list or consumer write exists"
    negative: "Two sites share a scratch tree, appended argv/env disappears, unrelated bytes change, or source/cache/profile state leaks between candidates"
    gate: tester-unified
  - id: O3
    observable: "Real go test exit 0 is survived and any normally-started nonzero, including compile rejection, is killed; helper/protocol failure, no sites, limit, and lane budget retain distinct complete v4 terminals"
    negative: "Human-readable Go output relabels results, invalid source becomes NO_MUTANTS, or partial completion earns PASS"
    gate: tester-unified
  - id: O4
    observable: "Tiny fixtures provide independently expected killed/survived cases and at least one pinned real srdm site is executed in a disposable snapshot; all exact v4 identities/hashes and source hashes match"
    negative: "Universal killed/survived, helper-authored expectations, or toy-only proof passes"
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "P29's site contract cannot be applied through P22 without materializing all candidate files"
  - "no stable real srdm site can be pinned with an independently reproduced outcome"
mutexes: []
---

# P30 — real Go mutation R2 integration

The claim to attack: **Assay submits bounded single-site Go mutations through
the same installed command pipeline and truthfully reports what real `go test`
did, without touching a consumer checkout.**

## Dispatch contract

- Contract class: **2c — bounded integration**.
- Required roles: **Sonnet xhigh implementer → Opus xhigh independent reviewer**;
  route to Sol if the P29/P23/P22 landed seams do not compose as specified.
- Readiness: **PROVISIONAL until P29 merges.** Before dispatch, freeze exact
  helper path/hash, site manifests, tiny killed/survived fixtures, one srdm case,
  handwritten v4 artifacts, and a deterministic budget ledger.
- Implementer freedom: private job/stream plumbing only. Capability transition,
  preflight, max+1, snapshot application, process classification, order, and
  terminal mapping are fixed.

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P30-real-go-mutation-r2-integration`
on branch `feat/assay-P30-real-go-mutation-r2-integration`.

## Context to read first

1. P29's final `MutationSite`/helper/GoAdapter API and parity tests.
2. P21's v4 identity/candidate sentinel; P22's replacement snapshot; P23's
   immutable plan, deadline, target collection, and no-partial-credit rules.
3. P27's adapter construction/effective-PATH preflight and P28's pinned real
   srdm harness. Reuse them; do not create a second image or project copier.
4. `mutation.py` execution/classification and `cli._built_in_registry` with P18
   decisions A-145–A-148 and A-158–A-160.
5. Prepared exact tiny/srdm cases and expected artifacts from the JIT carve.

## Implementation packet (normative)

### Integration flow

1. Resolve the P23 plan and adapter once. Resolve both `go` and
   `assay-go-helper` from the plan's effective PATH; the adapter receives the
   absolute helper path. Missing/unexecutable helper uses P21's
   `NO_MEASUREMENT/MISSING_EXTERNAL_TOOL` before baseline.
2. Advance only the existing Go registry entry from `R1` to `R1,R2`. Python and
   other languages are unchanged. R0 remains command capability, not an adapter
   level.
3. For target files in sorted repo-relative path order, call
   `generate_mutation_sites` with exactly the declared operator tuple and the
   remaining lane-wide sentinel capacity. Stop at `max_mutants + 1`; if reached,
   emit P21's exact limit artifact and submit zero processes.
4. For each accepted site, compute the v4 identity from target bytes and
   descriptor. On submission only, form
   `replacement_file = original[:start] + replacement + original[end:]` and
   call P22's exact replacement snapshot. Retain at most `jobs` full replacement
   files concurrently; queued jobs carry only path/site identity.
5. Run P23's same plan at snapshot project root with remaining lane budget.
   Started exit 0 → survived; started nonzero → killed; process-boundary failure
   → crashed; deadline interruption → budget-exceeded. Never parse stdout/stderr
   to refine these buckets.
6. Sort every output bucket by v4 identity independent of completion order and
   assemble the complete payload. No-sites is `INCONCLUSIVE/NO_MUTANTS` only
   after a valid helper success; discovery/helper errors keep their own terminal.

### Reachable terminal table

| state | expected |
|---|---|
| selected real site, test still passes | survived identity |
| selected real site, test/compile exits nonzero | killed identity |
| valid helper success with zero eligible sites | `INCONCLUSIVE/NO_MUTANTS` |
| helper invalid/error/malformed/nonzero | typed helper/discovery non-PASS, not no-mutants |
| max+1 descriptors | v4 mutant-limit sentinel, zero submissions |
| shared budget ends | lane-budget terminal, no next unit, never partial PASS |
| process cannot start after passing baseline | schema-supported crashed identity; record as structurally unreachable in the real exact-plan fixture unless an honest reproduction exists |

The final row is intentional. Because baseline and mutants use the same absolute
executable/argv/env, a command-boundary crash generally cannot be created only by
a source mutation. Do not fake it with output matching; unit-level construction
coverage may validate the schema bucket while the real fixture records the
reachability argument.

### Prepared proof and traceability

Tiny two-commit modules pin one killed and one survived site per implemented Go
operator plus no-sites and compile-rejection cases. The srdm case pins at least
one source site and its independently reproduced real outcome; it need not cover
every operator. Other locked inputs combine nested project, appended argv,
passthrough env, max boundaries, reordered completion, missing/malformed helper,
and injected deadline. Every expected artifact is handwritten from manifests.

| work | owner | oracle | controlled break |
|---|---|---|---|
| preflight/capability | CLI/registry | O1 | omit helper or widen another language |
| bounded collection/application | mutation | O1/O2 | collect full files or submit before max check |
| classification/artifacts | mutation | O3 | scrape output or award partial PASS |
| real tiny/srdm proof | gate/tests | O4 | use helper output as expectation or edit source |

## Work

1. Wire resolved helper preflight and advance only Go's registry entry through R2.
2. Integrate lane-wide selected/bounded site collection with zero submissions at
   max+1.
3. Apply each site lazily through P22 and execute P23's immutable plan/deadline.
4. Emit exact v4 identity buckets and distinct discovery/no-sites/limit/budget
   terminals using boundary-only process classification.
5. Add tiny real Go and pinned disposable srdm cases, exact artifacts, ledgers,
   source hashes, and reviewer-created combined attacks.
6. Run the real gate; record helper/image/wheel hashes and controlled-break counts.

## Test constraints copied from AUTHORING.md §3b

- Inject clocks/executors for budget/order; no sleep or elapsed-time oracle.
- Fresh snapshot/cache/profile per candidate and restored process environment.
- Assert exact sites, process ledgers, artifacts, memory structure and source
  hashes—not call counts or merely matching exit codes.
- No weakened assertions or coverage-evasion pragmas.
- Offline pinned toolchain/helper/commits only; no ambient PATH or live consumer
  write.

## Scope / forbid

This package integrates already-fixed contracts. It cannot redesign the helper,
common adapter protocol, Python mutation, v4, snapshot/plan, config, canary, gate
registration, or srdm. P31 owns Go R3.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden
file, STOP — write `BLOCKED: <reason>` to the LOG, commit, and exit. Do not
improvise a workaround.
