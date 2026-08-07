---
schema_version: 1
id: assay-P13-standalone-wheel-proof
project: assay
title: "The built wheel runs offline without source-tree or dependency leakage"
tier: implement-2
input_revision: "9bd7d206"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P09-cause-sensitive-canary, assay-P10-attested-evidence-staleness, assay-P12-bounded-mutation-execution]
session: fresh
scope:
  touch: ["pyproject.toml", "README.md", "tools/standalone-proof.sh", "tests/test_standalone.py", "tests/fixtures/standalone/**"]
  forbid: ["src/assay", "assay.toml", "nyxloom-trove/nyxloom.toml"]
oracles:
  - id: O1
    observable: "Inside tester-unified, a wheel is built offline with --no-build-isolation --no-deps, installed with --no-index into a clean scratch venv from a copied tree lacking .git, and its console command emits the expected R0 artifact"
    negative: "Removing package data or the console entry point makes the scratch invocation/build fail (A-124: dropping the fallback-version claim -- currently unfalsifiable in this gate image, see below)"
    gate: tester-unified
  - id: O2
    observable: "The installed distribution metadata has zero Requires-Dist runtime dependencies and the proof process has neither project PYTHONPATH nor the source checkout on sys.path"
    negative: "Adding requests as a runtime dependency or leaking the source tree makes the metadata/path assertion fail even if host site-packages contains it"
    gate: tester-unified
  - id: O3
    observable: "The installed wheel can load and independently validate its packaged schema v2, run one Python fixture through a real assay run, and prove the Go adapter is present and callable against a real committed Go fixture (A-126: adapter-level, never a genuine Go R0 run -- no toolchain exists)"
    negative: "Omitting schema/fixtures needed at runtime or importing a host-only module fails in the clean venv"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "the gate image lacks the already-recorded offline build prerequisites"
  - "proof requires changing runtime implementation instead of packaging metadata"
mutexes: []
---

# P13 — standalone wheel proof

The claim to attack: **the shipped wheel, not the source checkout, is a zero-runtime-dependency executable.**

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P13-standalone-wheel-proof`
on branch `feat/assay-P13-standalone-wheel-proof`.

## Context to read first

1. `docs/DESIGN-GUIDE.md` standalone requirements and decisions A-005, A-040, A-056, A-057, A-069, A-070, A-123–A-127.
2. **The two-environment recipe is ALREADY BUILT and ALREADY EXERCISED (A-123) — do not reimplement it.** `tests/conftest.py`'s session-scoped `standalone` fixture (`_build_backend_home`/`_clean_env`/`Standalone`, ~lines 624-741) already does exactly what A-070 specifies, as two subprocesses inside the SAME `docker run tester-unified:local ... pytest tests -q` gate command every prior package already used — never two separate containers. `tests/test_dependency_purity.py` and `tests/test_verdict_schema_is_packaged.py` already consume it and are already green (21 passed, independently reconfirmed). `tests/test_standalone.py` consumes the SAME fixture the same way — it does not build a new mechanism. `pyproject.toml`'s console entry point (`[project.scripts]`), package data (`[tool.setuptools.package-data]`), and zero-deps (`dependencies = []`) are ALL already declared — nothing needs adding there, only proving under the wheel.
3. `nyxloom/reference/DOCTRINE.md` gate/cockpit rule — the ordinary, project-wide "a bare devcontainer/manual run is a documented developer convenience, never a ship signal" rule (A-040). It does not imply a second container or gate; there is exactly one declared gate, `[gates.tester-unified]`.
4. **A-124, read before touching O1's fallback-version claim**: independently confirmed in the real gate image that `setuptools_scm` is absent from every interpreter, so removing `fallback_version` from `pyproject.toml` produces the BYTE-IDENTICAL wheel (`assay-0.0.0-py3-none-any.whl`) as leaving it in — the negative as originally worded is unfalsifiable here. Assert instead: the wheel builds successfully with no `.git` present; `pyproject.toml` declares `fallback_version` (already tested); the real installed version reads `"0.0.0"` (A-069's documented, accepted gap). If your own R0-artifact comparison uses `tests/fixtures/verdicts/r0_pass.json` as a template, exclude/normalize `assay_version` (it hardcodes `"0.1.0"`, which the real installed wheel will never produce here) the same way `commit`/`started`/`ended` already must be.
5. **A-125, a real scope trap**: `tests/conftest.py`'s `collect_ignore_glob` is NOT in your `scope.touch` and cannot be extended. Never commit a `test_*.py`-shaped file under `tests/fixtures/standalone/**` — pytest's default collection will try to import it and fail, the exact reason `fixtures/canary/python/**` and `fixtures/mutation_exec/python/**` are already ignored. Reuse one of those two already-ignored fixtures via a runtime copy, or materialize fixture content as literal strings directly inside `test_standalone.py`.
6. **A-126**: your Go fixture proof is adapter-level (`GoAdapter.has_executable_code`/`normalize_coverage_key` against real committed Go text — `tests/fixtures/canary/go/greet/greet.go` or `tests/fixtures/go/hello/hello.go`, neither collection-risky since they're not `.py`), never a genuine `assay run` R0 pass for Go — no Go toolchain exists anywhere in this devcontainer (A-042/A-087). Python's own fixture DOES go through the full real `assay run` pipeline via the installed console script against a real, `tmp_path`-materialized (never committed) git repo.
7. **A-127**: `tools/standalone-proof.sh` and `README.md` are both OPTIONAL — no oracle names either. Build `tools/standalone-proof.sh` only as a human/CI convenience wrapper (mirroring `tools/cgroup-parent.sh`'s shape — never invoked by pytest itself) if you find it useful; its absence is not a defect, and inventing content to fill the scope slot is not required.

## Work

1. Implement the exact two-environment offline build/install recipe from A-070 -- **already done, reuse `tests/conftest.py`'s `standalone` fixture (A-123), do not rebuild it.**
2. Remove `.git` before build to keep the install environment clean of build-only PYTHONPATH; the fallback-version claim itself is corrected per A-124, above.
3. Assert installed metadata and sys.path, then run real wheel behavior and schema loading -- the Python fixture goes through a full `assay run`; the Go fixture is adapter-level only (A-126).
4. Break dependency purity and packaged data (both genuinely testable in this gate image); record failure counts (A-067). Do not attempt to break fallback-version support -- see A-124.

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

**A. No speed-dependent verdicts.** Process completion decides; generous timeout only prevents hangs.

**B. No order/worker dependence.** Each proof owns scratch directories and virtual environment.

**C. No hollow tests.** Successful pip install alone proves nothing; assert metadata, paths, package data, and real emitted behavior.

**D. No coverage evasion.** No exclusions.

**E. Control inputs.** Offline flags are mandatory; no index/network or ambient site-packages.

## Scope / forbid

This package may repair packaging only. If runtime code is needed, the upstream owning package is incomplete and this one must block.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file, STOP — write `BLOCKED: <reason>` to the log, commit, and exit. Do not improvise a workaround.
