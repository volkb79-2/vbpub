---
schema_version: 1
id: assay-P23-versioned-wheel-contract
project: assay
title: "Every consumable assay wheel has a stable non-placeholder identity"
tier: implement-2
input_revision: "1d31eae137156e31abf0c88e6c8381941696d66c"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P22-exact-reexecution-isolation]
session: resume:assay-v11-distribution
scope:
  touch: ["pyproject.toml", "src/assay/__init__.py", "assay.toml", "nyxloom-trove/nyxloom.toml", "tests/test_standalone.py", "tests/test_self_hosting.py", "README.md"]
  forbid: ["src/assay/cli.py", "src/assay/runner.py", "src/assay/verdict.py", "src/assay/schemas"]
oracles:
  - id: O1
    observable: "The real offline self-hosting build includes the declared pinned build backend and emits a wheel whose metadata and verdict assay_version are the same non-0.0.0 semver"
    negative: "Removing setuptools_scm from the build closure or forcing 0.0.0 fails installed metadata comparison"
    gate: tester-unified
  - id: O2
    observable: "Two wheels built from the same release source and SOURCE_DATE_EPOCH are byte-identical, while changed source cannot masquerade as the same clean released wheel"
    negative: "A placeholder or manually shadowed version lets changed source pass identity comparison"
    gate: tester-unified
  - id: O3
    observable: "Consumer documentation pins an exact wheel version and sha256 and contains no monorepo-relative assay import"
    negative: "A path-import example or unpinned install fails the distribution contract assertion"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "a release identity cannot be derived offline from source or explicit release metadata"
  - "the real gate would need network access to build the wheel"
mutexes: [merge-lane]
---

# P23 — versioned wheel contract

The claim to attack: **an assay artifact names the exact distributable assay build that produced it.**

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P23-versioned-wheel-contract`
on branch `feat/assay-P23-versioned-wheel-contract`.

## Context to read first

1. `docs/DESIGN-GUIDE.md` §§5, 6, 9, and 10; decisions A-005, A-029, A-040–A-041, A-057, A-067, A-069–A-070, A-123–A-127, A-130–A-131.
2. `pyproject.toml` build-system and `tool.setuptools_scm` sections, `src/assay/__init__.py`, and `tests/test_dependency_purity.py`.
3. `tests/test_standalone.py` and `tests/test_self_hosting.py`; identify every assertion/comment that deliberately accepted `0.0.0` under P13/P14 and replace it with a real backend exercise, not a different literal shortcut.
4. `nyxloom-trove/nyxloom.toml`'s full `tester-unified` command and comments. Preserve its installed-wheel isolation, independent second witness, verified cgroup helper, uid identity, and offline behavior exactly.
5. `/workspaces/vbpub/ciu/pyproject.toml`, `/workspaces/vbpub/cmru/pyproject.toml`, `/workspaces/vbpub/topos/pyproject.toml`, and their release-wheel conventions for estate-local prior art.
6. `nyxloom-trove/reports/assay-v1-post-series-review-sol.md` finding 10 and distribution recommendation.

## Work

1. Make the real self-hosting build environment contain the exact build requirements declared in `pyproject.toml`, including `setuptools_scm==10.0.5`, without network access. Do not shadow the backend with a manual `__version__` or environment-only constant.
2. Build from a source shape that exercises both tagged/SCM identity and the documented no-VCS fallback deliberately. A clean released wheel must never report `0.0.0`; wheel metadata, `importlib.metadata.version("assay")`, `assay.__version__`, and emitted `assay_version` must agree.
3. Prove reproducibility with two independent offline builds from identical release input and fixed `SOURCE_DATE_EPOCH`, comparing wheel sha256. Prove a controlled source mutation cannot pass as that same clean release identity/artifact.
4. Retain zero runtime dependencies. Build dependencies belong only to the build closure; the scratch installed environment must still contain assay plus stdlib and no leaked source tree.
5. Document the consumer contract: publish/tagging is a controller release action; consuming gates install an exact assay wheel and verify its sha256; no `PYTHONPATH` or sibling-worktree import is supported distribution.
6. Update self-hosting/standalone expected **v4** artifacts to assert a non-placeholder version while preserving their independent verifier and universal-PASS producer mutation. Do not retain a v3 compatibility writer or upgrade a historical artifact in place.
7. Break backend availability, metadata/runtime/artifact agreement, reproducibility, mutation identity, dependency purity, hash pinning, and path-import prohibition separately; run the real gate and record exact A-067 counts.

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

**A. No speed-dependent verdicts.** Build completion and byte hashes decide; no build-duration threshold.

**B. No order/worker dependence.** Each build/install uses independent scratch directories and a fixed explicit source epoch.

**C. No hollow tests.** Inspect the real wheel metadata, installed import, emitted artifact, dependency closure, and sha256.

**D. No coverage evasion.** Preserve full statement/branch coverage and record real failure counts for each distribution mutation.

**E. Control inputs.** Offline local wheelhouse/build closure only; no registry, network, ambient package, or source-tree import.

## Scope / forbid

This package establishes distribution identity only. It must not change CLI semantics, verdict/schema, runtime dependencies, or publish/tag/merge externally. `nyxloom.toml` is in scope solely to preserve and repair the real self-hosting build command, hence `merge-lane`.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file, STOP — write `BLOCKED: <reason>` to the log, commit, and exit. Do not improvise a workaround.
