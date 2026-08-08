---
schema_version: 1
id: assay-P20-repository-artifact-boundary-integrity
project: assay
title: "Repository identity and measured artifacts survive adversarial process state"
tier: implement-2
input_revision: "1d31eae137156e31abf0c88e6c8381941696d66c"
source: {kind: product-goal, ref: "nyxloom-trove/reports/assay-v2-post-series-review-sol-P15-P19.md"}
stack: none
depends_on: [assay-P19-isolated-r3-cli-pipeline]
session: fresh
scope:
  touch: ["src/assay/git.py", "src/assay/coverage.py", "src/assay/evaluate.py", "src/assay/runner.py", "src/assay/cli.py", "tests/**", "README.md", "docs/DESIGN-GUIDE.md"]
  forbid: ["src/assay/schemas", "src/assay/verdict.py", "src/assay/mutation.py", "src/assay/canary.py", "src/assay/adapters"]
oracles:
  - id: O1
    observable: "Every Git query is anchored to the supplied repository under a sanitized Git environment; hostile GIT_DIR/GIT_WORK_TREE/config, hooks, external diff, textconv, or another repository cannot change the resolved HEAD, diff, or dirty set"
    negative: "Pointing GIT_DIR at a second seeded repository changes Assay's recorded commit or path set"
    gate: tester-unified
  - id: O2
    observable: "A coverage artifact is accepted only when this command invocation created a fresh bounded regular non-symlink file, opened and validated without a blocking special-file read"
    negative: "A copied stale profile, FIFO, device, symlink swap, or oversized file is parsed or can hang beyond the lane process timeout"
    gate: tester-unified
  - id: O3
    observable: "After HEAD is known, every expected Git, decode, coverage, source-read, and evaluation refusal emits a complete artifact; a lane command that changes any repository path cannot retain PASS claims bound to the pre-run commit"
    negative: "A tracked test/support-file mutation outside source_roots exits zero with PASS and the original commit, or a normalized-key collision exits with a traceback and no artifact"
    gate: tester-unified
  - id: O4
    observable: "All repository/artifact checks use fixed byte/path/work bounds rather than ambient or elapsed-time guesses"
    negative: "An unbounded input reaches read_text before a size/type guard"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "a truthful terminal needs a reason code absent from schema v3"
  - "the Git boundary cannot disable an ambient/local executable behavior without changing repository contents"
mutexes: []
---

# P20 — repository and artifact boundary integrity

The claim to attack: **the repository and evidence Assay records are the ones it actually measured, even under hostile ambient process and filesystem state.**

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P20-repository-artifact-boundary-integrity`
on branch `feat/assay-P20-repository-artifact-boundary-integrity`.

## Context to read first

1. `nyxloom-trove/reports/assay-v2-post-series-review-sol-P15-P19.md`, findings F01, F04, F05, F06, F07 and F08; reproduce the hostile-`GIT_DIR`, post-command dirty-tree, stale-profile, and normalized-key-collision probes before implementation.
2. `docs/DESIGN-GUIDE.md` §§0, 5, 6, 9 and 12; decisions A-027, A-041, A-134, A-139–A-140 and A-153–A-157.
3. `src/assay/git.py` in full. Enumerate every Git subprocess and every inherited environment/config source; do not harden only the commands used by one fixture.
4. `src/assay/coverage.py::read_coverage_artifact`, `src/assay/evaluate.py`, and `src/assay/runner.py::{evaluate_r1,run_lane,write_verdict}` with their direct tests. Identify every call after `execute_command` that can raise an expected `AssayError`, `OSError`, or decode error outside artifact assembly.
5. P15's byte/path decisions A-134–A-135 and P17's total-terminal decisions A-139–A-143. Preserve their exact path transport and one-complete-artifact contract.
6. `/workspaces/vbpub/nyxloom/reference/DOCTRINE.md` §§4.2a, 5, and 6 for bounded evidence, fail-closed behavior, and real-gate discipline.

## Work

1. Make `git.py` the only Git process boundary. Resolve the executable explicitly, start from a minimal controlled environment, remove all ambient repository/config selectors, disable system/global config and executable diff/textconv/fsmonitor behavior, pass end-of-options-safe operands, and anchor every command to the exact supplied repository. Missing/unusable Git remains a typed terminal, never another repository or a local configuration fallback.
2. Add real two-repository attacks for `GIT_DIR`, `GIT_WORK_TREE`, `GIT_CONFIG_*`, local external diff/textconv, fsmonitor, aliases, and hooks where relevant. A test proves exact HEAD/path bytes, not merely that Git returned zero.
3. Replace path-based `read_text` coverage ingestion with a bounded safe-open sequence: reject symlinks and non-regular files, bind the opened descriptor to the checked inode, enforce a documented maximum byte count before decoding/parsing, and reject replacement races. Never open a FIFO/device in a way that can block judgment.
4. Bind coverage freshness to each execution. Remove or fingerprint the prior ordinary artifact only after all pre-run refusal checks; after execution require a newly produced file for this invocation. R2/R3 controls and transforms must not inherit a baseline artifact through a repository copy. Preserve A-140: a refused run does not delete anything.
5. Put every expected post-HEAD failure inside the complete-artifact path, including `evaluate_coverage` normalization collisions, bounded source reads, Unicode/filesystem errors, and Git decode failures. Do not blanket-catch programmer defects. Initial HEAD resolution may still be pre-artifact because there is no honest commit identity.
6. After the declared command, compare the whole repository against the resolved pre-run commit before awarding any claim. A mutation anywhere — including tests, support files, ignored-policy files, index state, or paths outside `source_roots` — makes the run non-PASS and prevents claims bound to the old commit. Preserve the command result in the artifact metadata; do not clean or restore consumer state.
7. Run the installed-wheel complete-artifact suite under hostile Git variables and filesystem objects. Break each guard individually and record the exact A-067 failure count.

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

**A. No speed-dependent verdicts.** FIFO/device attacks are proven by nonblocking safe-open behavior or a child-process failsafe, never an elapsed-time threshold.

**B. No order/worker dependence.** Every test owns fresh repositories, environment snapshots, and artifact paths; hostile process state is restored.

**C. No hollow tests.** Compare exact repository OIDs, paths, complete artifacts, and pre/post hashes under real hostile inputs.

**D. No coverage evasion.** Preserve 100% statement/branch coverage and mutation-check every new guard.

**E. Control inputs.** Git histories, config, hooks, filesystem objects, clocks, and process results are disposable local fixtures; no network or ambient repository is evidence.

## Scope / forbid

This package hardens facts already represented by schema v3. It must not redesign isolation, mutation/canary payloads, or the schema. Verdict-output write failures needing a new reason, full mutant evidence, canary-target binding, and exclusion capability belong to P21.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file, STOP — write `BLOCKED: <reason>` to the log, commit, and exit. Do not improvise a workaround.
