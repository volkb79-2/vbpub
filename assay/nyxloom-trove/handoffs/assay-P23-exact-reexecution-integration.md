---
schema_version: 1
id: assay-P23-exact-reexecution-integration
project: assay
title: "Every rigor level reuses one command plan, snapshot contract, and lane budget"
tier: implement-2
input_revision: "2f2167f5928e5deacd93f1e9565238aef8acfe32"
source: {kind: product-goal, ref: "nyxloom-trove/reports/assay-v2-post-series-review-sol-P15-P19.md"}
stack: none
depends_on: [assay-P22-committed-object-snapshot-substrate]
session: fresh
scope:
  touch: ["src/assay/config.py", "src/assay/runner.py", "src/assay/mutation.py", "src/assay/canary.py", "tests/**", "README.md", "docs/DESIGN-GUIDE.md"]
  forbid: ["src/assay/git.py", "src/assay/schemas", "src/assay/verdict.py", "src/assay/verify.py", "src/assay/attestation.py", "src/assay/adapters"]
oracles:
  - id: O1
    observable: "One immutable effective command plan, including appended argv and captured passthrough values, is recorded and used byte-for-byte for snapshot R0, every R2 mutant, and both R3 halves; only snapshot root and remaining budget vary"
    negative: "An appended selector or passthrough token appears in R0 metadata but is absent from a mutant/control subprocess"
    gate: tester-unified
  - id: O2
    observable: "Baseline, every mutant, canary control, and canary transform start from independent P22 committed snapshots at the original project prefix with no inherited coverage artifact"
    negative: "A command reading ../shared fails only in mutants, or a no-output transform consumes baseline coverage and passes"
    gate: tester-unified
  - id: O3
    observable: "R0 is required for every lane, uncovered-line R3 also requires R1, and invalid rigor is refused at load before repository or command side effects"
    negative: "rigor=[R2] reaches verdict construction or uncovered-line R0+R3 is accepted despite its expected cause being impossible"
    gate: tester-unified
  - id: O4
    observable: "One injected lane deadline covers snapshot creation, baseline, discovery, every repeated process and evaluation; no new unit starts without remaining budget and max_mutants+1 is refused before submission"
    negative: "N mutants each receive the full lane timeout or excess candidates are silently sampled"
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "an effective plan field must be recomputed from ambient state after the first resolution"
  - "P22 cannot supply an independent snapshot for every repeated unit"
mutexes: []
---

# P23 — exact reexecution integration

The claim to attack: **R0, R2, and R3 compare controlled variants of one exact
declared command and one exact commit, under one honest lane-wide budget.**

## Dispatch contract

- Contract class: **2c — bounded integration**.
- Required roles: **Sonnet xhigh implementer → Opus xhigh independent reviewer**;
  route to Sol if P22's landed interface differs from the shapes below.
- Readiness: **PROVISIONAL until P22 merges.** Before dispatch, update signatures
  from the actual P22 substrate and commit the process-ledger/combined-axis
  acceptance assets. Do not ask the implementer to discover the adapter seam.
- Implementer freedom: private orchestration decomposition only. Plan fields,
  resolution-once rule, rigor prerequisites, snapshot-per-unit state machine,
  budget accounting, terminal mapping, and no-partial-credit rules are fixed.

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P23-exact-reexecution-integration`
on branch `feat/assay-P23-exact-reexecution-integration`.

## Context to read first

1. P22's final public interface and tests; never reimplement or bypass it.
2. Post-series review F01–F04/F13–F14 and decisions A-154–A-161. Reproduce
   lost appended argv/passthrough, nested sibling false-PASS, stale canary
   coverage, R2-only crash, and multiplicative budget probes.
3. `runner.py::{CommandPlan,CommandResult,execute_command,run_lane}`,
   `mutation.py::{resolve_mutation_targets,run_mutation,_run_one_mutant}`, and
   `canary.py::{run_isolated_canary,_run_pipeline}` with direct tests.
4. P20's artifact reservation and P21's landed v4 terminal, max-mutant, and
   bounded `MutationSite` contract. Consume them without changing adapters,
   schemas, models, verifier, safe I/O, or Git.
5. `config.py` rigor and mutation/canary validation; `DESIGN-GUIDE.md` §§5, 6,
   9, 11, 12 and A-036/A-061/A-119–A-122/A-137/A-145/A-149–A-161.

## Implementation packet (normative)

### Exact effective plan

Extend the existing frozen `CommandPlan`; do not create a competing plan type.
The JIT carve freezes exact landed names for these values:

```text
declared_argv       exact lane argv
appended_argv       exact caller suffix, order preserved
effective_argv      declared_argv + appended_argv
declared_env        closed lane mapping
passthrough_names   closed allowlist from lane declaration
effective_env       declared_env plus values captured once from caller env
project_prefix      repo-top-relative working directory identity
artifact_path       project-relative declared coverage output
budget_seconds      positive declared lane budget
```

`resolve_command_plan(lane, *, argv_append, ambient_env, project_prefix)` runs
exactly once before snapshot/process side effects. Duplicate env names between
declared and passthrough were already refused by P15. A missing passthrough name
is omitted exactly as today; it is never replaced by empty text or re-read later.
Every process ledger records `effective_argv`, the effective environment subset,
and the same project prefix. Relocation computes only
`snapshot.root / project_prefix`; it does not rebuild any other field.

### Landed bounded mutation-site contract

P21 already replaces full-text candidates with the exact selected-operator,
remaining-capacity `MutationSite` protocol, validates UTF-8 byte spans and
replacement hashes, stops at `max_mutants+1`, and makes Python match its locked
candidate manifest. P23 may change only where/how a landed site is materialized:
one exact byte replacement in a fresh P22 snapshot. It must not edit the
descriptor, ordering, adapter method, collection bound, v4 identity, error
translation, or manifest. P29 later makes Go implement P21's same protocol.

### Fixed state machine

1. At config load, require `rigor[0] == "R0"`. Preserve independent R1/R2/R3
   selection otherwise. If R3 mechanism is `uncovered-line`, also require R1.
   Refuse before Git, output reservation, snapshot, or command activity.
2. Resolve full repository/commit/project prefix through P20, reserve the verdict
   destination, resolve one plan, and create one injected monotonic deadline.
3. Create a uniquely owned P22 snapshot for the resolved commit before making
   the P20 output reservation; ensure the declared coverage artifact is absent;
   execute baseline from `snapshot.project_root` with only the remaining budget;
   read only that invocation's fresh bounded artifact. No shared live-tree
   writer may race reservation/arming: private snapshot ownership, not ctime
   granularity, removes that residual identity ambiguity.
4. If baseline/prerequisite fails, emit its complete v4 artifact and start no
   mutation/canary work.
5. For R2, resolve targets from the already-resolved diff and call P21's bounded
   common mutation-site interface unchanged. Retain at most
   `max_mutants + 1`; if the sentinel exists, emit P21's limit terminal with zero
   submissions. Otherwise each submitted site receives a fresh base snapshot,
   uses P22's exact-byte replacement, and runs the same plan with remaining
   budget.
6. For R3, materialize independent base control and exact transform snapshots.
   Neither inherits baseline/control coverage. Run the same plan; judge only the
   fixed expected cause.
7. Before snapshotting or launching any next unit, compute remaining budget from
   the injected clock. When non-positive, launch nothing else and emit the fixed
   lane-budget terminal. Completed outcomes remain evidence but never convert a
   partial R2/R3 run into PASS.

### Decision matrix

| combined state | terminal/observable |
|---|---|
| appended argv + passthrough + nested project | every process ledger is byte-equal; tracked sibling visible |
| baseline creates no coverage | complete no-measurement artifact; no R2/R3 |
| max_mutants+1 discovered | mutant-limit terminal, `candidate_count=max+1`, zero attempts |
| budget ends between units | lane-budget terminal; no next snapshot/process |
| ignored stale coverage + no-output transform | no-measurement, never canary PASS |
| shared live tree contains/races an ignored artifact | never used for execution; uniquely owned snapshot starts artifact-absent |
| R2-only or uncovered-line without R1 | load-time bad configuration, no side effect |
| consumer command writes tracked/untracked snapshot files | disposable only; source repository hash/status unchanged |

### Prepared proof and traceability

P21's locked Python candidate-parity manifest remains authoritative. The P23
JIT carve commits an execution-ledger spy, injected clock, and two-commit
nested repository with `apps/p` reading `shared/input`; nonempty appended argv;
present/absent passthrough values; stale ignored coverage; command-created
tracked/support files; max-1/max/max+1 candidates; and a canary whose control
writes coverage while transform does not. Expected v4 artifacts are handwritten.

| work | owner | oracle | controlled break |
|---|---|---|---|
| plan capture/reuse | `runner.py` | O1 | re-resolve lane inside R2/R3 |
| snapshot orchestration | runner/mutation/canary | O2 | reuse baseline tree/profile |
| rigor prerequisites | `config.py` | O3 | accept R2-only or missing R1 |
| landed sites/deadline/cap | runner/mutation | O4 | bypass P21 collection, per-process timeout, or truncate |

## Work

1. Extend and resolve the effective command plan exactly once.
2. Add load-time rigor prerequisites with complete diagnostics/tests.
3. Run baseline inside P22's committed snapshot and carry its plan/result forward.
4. Convert R2 and R3 to fresh-snapshot-per-unit execution without a second
   command-resolution path or copied coverage artifact.
5. Apply one lane deadline and P21's max-mutant sentinel before submissions.
6. Add the prepared fixtures plus reviewer-created combined-axis attacks; compare
   exact ledgers, complete v4 artifacts, and consumer repository hashes.
7. Run `tester-unified`; record controlled-break counts in the LOG/report.

## Test constraints copied from AUTHORING.md §3b

- No wall-clock deadline or sleep decides a test; use injected clocks and
  synchronization. Real timeouts are hang failsafes only.
- Restore process-global environment/config and use fresh repositories/snapshots;
  tests cannot depend on order or worker.
- Assert behavioral artifacts, ledgers, absence, and source hashes—not only call
  counts, private fields, or “did not raise”.
- Do not weaken assertions or use coverage-evasion pragmas.
- Network, clock, filesystem, environment, Git and processes are controlled
  inputs; the real gate remains offline.

## Scope / forbid

This package integrates already-fixed P20/P21/P22 contracts. It cannot change
snapshot mechanics, safe I/O/Git, verdict/schema/verifier, adapters, attestations,
or consumer repositories.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden
file, STOP — write `BLOCKED: <reason>` to the LOG, commit, and exit. Do not
improvise a workaround.
