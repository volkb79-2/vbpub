---
schema_version: 1
id: assay-P22-exact-reexecution-isolation
project: assay
title: "Every rigor level reexecutes one exact command against one exact commit"
tier: implement-2
input_revision: "1d31eae137156e31abf0c88e6c8381941696d66c"
source: {kind: product-goal, ref: "nyxloom-trove/reports/assay-v2-post-series-review-sol-P15-P19.md"}
stack: none
depends_on: [assay-P21-verdict-v4-evidence-contract]
session: fresh
scope:
  touch: ["src/assay/config.py", "src/assay/git.py", "src/assay/runner.py", "src/assay/mutation.py", "src/assay/canary.py", "src/assay/isolation.py", "tests/**", "README.md", "docs/DESIGN-GUIDE.md"]
  forbid: ["src/assay/schemas", "src/assay/verdict.py", "src/assay/verify.py", "src/assay/attestation.py", "src/assay/adapters"]
oracles:
  - id: O1
    observable: "One immutable effective command plan, including appended argv and resolved passthrough values, is recorded and used byte-for-byte for R0, every R2 mutant, and both R3 halves; only the snapshot root changes"
    negative: "An appended selector or passthrough token appears in R0 metadata but is absent from a mutant/control subprocess"
    gate: tester-unified
  - id: O2
    observable: "A project nested below repository top is reconstructed at the same relative path with tracked sibling inputs, so baseline and each isolated run see identical non-mutated repository bytes"
    negative: "A passing command that reads ../shared fails only in mutant copies and awards a killed mutant/PASS R2"
    gate: tester-unified
  - id: O3
    observable: "Baseline, every mutant, canary control, and canary transform start from fresh committed-object snapshots with no inherited coverage output; contained symlinks are preserved and absolute/escaping/special entries are refused before execution"
    negative: "A control/transform that writes no profile reads the baseline profile copied into scratch, or an external symlink is dereferenced into the snapshot"
    gate: tester-unified
  - id: O4
    observable: "R0 is required in every lane, uncovered-line R3 also requires R1, max_mutants bounds submissions, and one lane budget covers snapshot/evaluation plus all repeated subprocesses"
    negative: "rigor=[R2] reaches a ValueError, max_mutants+1 is silently sampled, or N mutants each receive the full lane timeout"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "a committed-object snapshot cannot preserve needed repository topology without executing consumer hooks or filters"
  - "an effective plan field cannot be relocated without recomputing an ambient value"
mutexes: []
---

# P22 — exact reexecution and isolation

The claim to attack: **all computed rigor compares the same declared command on controlled variants of the same committed repository, within one declared budget.**

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P22-exact-reexecution-isolation`
on branch `feat/assay-P22-exact-reexecution-isolation`.

## Context to read first

1. `nyxloom-trove/reports/assay-v2-post-series-review-sol-P15-P19.md`, findings F01–F04 and F13–F14; reproduce the lost-argv/env, monorepo-sibling false PASS, and stale-canary-profile probes before implementation.
2. `docs/DESIGN-GUIDE.md` §§0, 5, 6, 9, 11 and 12; decisions A-036, A-061, A-119–A-120, A-137, A-145, A-149–A-151, and A-154–A-161.
3. `src/assay/runner.py::{CommandResult,execute_command,run_lane}`, `src/assay/mutation.py::_run_one_mutant`, and `src/assay/canary.py::{_run_pipeline,run_isolated_canary}` with every direct test. Write down which values are currently re-resolved from `Lane` and which came from the baseline call.
4. P20's sanitized `git.py` process boundary and P21's v4 max-mutant/reason contract. Reuse them; do not add another subprocess or schema interpretation path.
5. `src/assay/config.py`'s rigor validation. Apply A-154 at load time so an illegal lane never reaches execution or verdict construction.
6. `/workspaces/vbpub/shared-ramdisk-depot-manager` only to exercise the repository/project nesting and real Go file topology in tests; this package does not run Go or edit srdm.

## Work

1. Introduce a frozen effective-command plan resolved exactly once from the lane plus caller inputs. It contains declared/appended/effective argv, declared/effective env (including captured allowlisted passthrough values), budget/deadline, and project working-directory identity. Artifact assembly and every process invocation consume that same object; no R2/R3 path may reconstruct a smaller plan from `Lane`.
2. Require R0 in every lane at config load. Preserve independent R1/R2/R3 selection otherwise. Additionally require R1 when R3's mechanism is `uncovered-line`, because its expected cause is otherwise unproducible. Update examples and complete config diagnostics.
3. Replace working-tree `copytree` isolation with one shared snapshot mechanism that materializes the resolved commit's tracked repository objects under a fresh root, preserving the project's repo-relative prefix and all tracked siblings. It must not execute consumer hooks, clean/smudge filters, external diff, or checkout helpers. Do not copy ignored/untracked files or infer an include list.
4. Preserve contained relative symlinks as symlinks. Reject absolute or repository-escaping symlinks, devices, FIFOs, sockets, unsupported Git entry modes, path collisions, and archive traversal before any command. Validate paths/entry counts/bytes under fixed documented safety limits and render P21's truthful isolation-limit terminal; never truncate.
5. Run the initial R0 command inside a fresh committed snapshot, then use its exact result and plan for R1/R2 assembly. Every mutant starts from another fresh snapshot and changes one target by atomic replacement, never a shared hardlink/inode. The consumer checkout is read-only throughout.
6. Start canary control and transform from two independent fresh snapshots. Ensure the declared coverage artifact is absent before each command and newly produced afterward. Build the transform commit from exact snapshot bytes with sanitized Git plumbing, neutral fixed identity, and no hooks/filters; retain the real base history needed for diff evaluation.
7. Make `lane.budget` one end-to-end deadline covering snapshot construction, baseline, mutation collection/execution, canary control/transform, and evaluation. Pass only remaining budget to a child command. Stop before starting the next unit when exhausted; never award partial mutation/canary credit. Use P21's required `max_mutants` before submission.
8. Add installed-wheel fixtures for nonempty appended argv, passthrough collisions, nested projects reading tracked siblings, ignored stale profiles, contained/escaping symlinks, special entries, command-created files, max-mutant excess, and total-budget exhaustion. Compare exact process ledgers, complete v4 artifacts, and consumer-tree hashes. Break each property independently and record exact A-067 counts.

## Test constraints copied from AUTHORING.md §3b

**A. Nothing may make the verdict depend on how fast the machine is.** (L20)
- ✗ `deadline = time.monotonic() + N` followed by an assertion. A time budget is
  a proxy for "eventually" and is hardware-dependent by construction.
- ✗ `time.sleep(N)` to "let the thread get there", then assert.
- ✗ Asserting on elapsed time, or on how many iterations something completed.
- ✓ Wait on a **real synchronization point**: `join()` a process/thread, block on
  an `Event` the code under test sets, drain a queue.
- ✓ **Best: remove the wait.** Extract the pure per-iteration step and call it
  directly from the main thread. Deterministic *and* trivially coverable.
- ✓ A timeout is legal ONLY as a failsafe against hanging the suite forever
  (make it generous — 60s, not 3s). It must never be the thing that decides
  pass/fail. If shrinking the timeout could flip the result, it is an oracle.
- **Rule: a test that fails when the machine is slow is a TRUE red — a real race
  the slow host revealed. Fix the test. Never widen a timeout, and never raise a
  cgroup weight / add CPU to make a suite pass.**

**B. Nothing may depend on test order, worker assignment, or a sibling test.**
- ✗ Mutating **process-global** state (logging config, `os.environ`, module
  attributes, singletons) without restoring it. Under `pytest-xdist` the damage
  lands in whichever test shares that worker. (PL7 §5)
- ✗ `monkeypatch.setattr` on an object that synthesizes attributes via
  `__getattr__` (lazy proxies, `SimpleNamespace` façades, ORM rows). Teardown
  *materializes* the patched attribute as a permanent instance attribute and
  pins it forever. Patch the **namespace that owns it** instead. (L19)
- ✗ Teardown that destroys shared state rather than restoring the prior value.
- ✓ Fresh `tmp_path` per test; assert cleanup actually restored what it found.
- When a test fails only in the full parallel suite, ask **"what did an earlier
  test leave behind?"** before "what raced?" — pollution is more common than a
  race and reproduces deterministically once you know the pair.

**C. No hollow tests.** (§3 above, and DOCTRINE's review checklist)
- ✗ A test body that is `pass`, or asserts only that nothing raised.
- ✗ Asserting implementation trivia (a call count, a private attribute, a log
  string) instead of the behavioral contract.
- ✗ Weakening or deleting an assertion to get past a failure.
- ✓ Assert the **contract**: given this input/state, this observable outcome.
- ✓ Where a check guards a real crash, add a test proving the crash is real —
  it ties the check to reality instead of to a style rule.

**D. No coverage evasion.** (L11, GA2b)
- ✗ A no-cover exclusion pragma on changed lines. nyxloom's gate **rejects**
  them, and note it matches the literal token anywhere on a line — including in
  a comment that merely *describes* the rule.
- ✗ Excluding an `except` body and assuming the `except` clause is covered too —
  it is not; that off-by-one killed a diff-coverage floor once already. (L11)
- ✓ If a line is genuinely unreachable, restructure so it does not exist.

**E. Network, clock, and filesystem are inputs — control them.**
- ✗ Real network calls, real registries, real model endpoints in a unit test.
- ✗ `datetime.now()` / `time.time()` where the assertion depends on the value.
- ✓ Inject or mock the boundary; make offline the default path.

**Author's check:** for every test you specify, ask *"could this flip its verdict
on a slower machine, in a different worker, or in a different order?"* If yes,
it is not an oracle yet.

## Package-specific test emphasis

**A. No speed-dependent verdicts.** Total-budget tests use injected clocks and scripted completed processes; real-command timeouts are failsafes only.

**B. No order/worker dependence.** Every execution owns a fresh snapshot; env changes are restored and result ordering follows identity.

**C. No hollow tests.** Seed real sibling dependencies, stale profiles, and hostile Git entries; assert exact process ledgers, artifacts, and source hashes.

**D. No coverage evasion.** Maintain 100% statement/branch coverage and break plan reuse, topology, freshness, and budget accounting separately.

**E. Control inputs.** Repositories, Git objects, clocks, subprocess results, argv, env, and scratch roots are explicit disposable inputs; no ambient cache is evidence.

## Scope / forbid

This package changes orchestration and isolation only, consuming P20's safe boundaries and P21's v4 contract. It must not change schema/model/verifier, adapters, attestations, or consumer projects.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file, STOP — write `BLOCKED: <reason>` to the log, commit, and exit. Do not improvise a workaround.
