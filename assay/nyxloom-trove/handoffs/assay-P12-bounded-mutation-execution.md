---
schema_version: 1
id: assay-P12-bounded-mutation-execution
project: assay
title: "Mutation execution is baseline-gated, isolated, and mechanically bounded"
tier: implement-2
input_revision: "9bd7d206"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P11-valid-mutant-construction, assay-P04-runner-cli-verdict-emission]
session: resume:assay-runner
scope:
  touch: ["src/assay/mutation.py", "src/assay/runner.py", "src/assay/verdict.py", "src/assay/schemas/**", "tests/**"]
  forbid: ["src/assay/errors.py", "src/assay/config.py"]
oracles:
  - id: O1
    observable: "A passing unmodified baseline is mandatory; baseline failure, crash, or timeout stops before any mutant and renders the corresponding non-PASS reason in a complete expected artifact"
    negative: "Deleting the baseline guard submits mutant work and can award credit when the original suite is already red"
    gate: tester-unified
  - id: O2
    observable: "An injected executor factory receives max_workers equal to configured jobs and every mutant is submitted through that executor; jobs=1 and jobs=3 produce identical ordered result records"
    negative: "Constructing the executor with mutant count or bypassing it for one task fails the recorded bound/submission assertions without any wall-clock measurement"
    gate: tester-unified
  - id: O3
    observable: "Each mutant runs against isolated source state, restoration is byte-exact after killed, survived, crashed, and budget-stopped results, and deterministic input order yields deterministic output order"
    negative: "In-place shared mutation contaminates a later fake run or leaves the source hash changed; completion-order output changes the expected list"
    gate: tester-unified
  - id: O4
    observable: "Killed, survived, error, and budget-exhausted mutation sets each emit independently written schema-valid R2 artifacts with all attempted and unattempted identities accounted for"
    negative: "Dropping unattempted identities, treating crashes as killed, or universal PASS differs from the complete expected artifact"
    gate: tester-unified
  - id: O5
    observable: "The baseline and every mutant invocation receive the lane's declared argv byte-for-byte; changing source paths changes no command argument unless the caller explicitly appended one under the existing lane rule"
    negative: "Deriving tests/test_<module> from a mutated source path changes the fake runner's recorded argv and fails the paired two-source fixture"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "the jobs bound cannot be observed through the actual executor boundary without elapsed-time assertions"
  - "source restoration requires editing adapter contracts"
mutexes: []
---

# P12 — bounded mutation execution

The claim to attack: **tests kill valid changed-line mutants under a declared, deterministic execution bound.**

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P12-bounded-mutation-execution`
on branch `feat/assay-P12-bounded-mutation-execution`.

## Context to read first

1. `docs/DESIGN-GUIDE.md` — **corrected (A-118): there is no titled "mutation execution/budget section"** — the actual content spans §6 (`BUDGET_EXCEEDED`), §11 (the adapter surface plus the `judge.mutation` TOML fragment), §12 (R2 requiring `judge.mutation`). Decisions A-003, A-004, A-020–A-024, A-041, A-082, A-113, A-116–A-122.
2. P11 mutation manifest, runner/process boundary, verdict model and independent artifact tests.
3. **Corrected (A-113):** the real mutation-execution prior art is `/workspaces/vbpub/nyxloom/src/nyxloom/mutation_gate.py`'s `evaluate()`/`MutationResult` orchestration (`ThreadPoolExecutor` fan-out, deterministic result ordering independent of completion order) — NOT `shared-ramdisk-depot-manager`'s Go reference, which has zero mutation-related content (same defect class as A-105/A-112, verified). Take behavior (the executor-bound and ordering discipline), not structure. **Two specific traps in that reference, do NOT port (A-122):** its executor cap is `max(1, (os.cpu_count() or 2) - 2)` — machine-derived, not caller-declared, the opposite of what O2 and A-082 require; and its `_run_is_killed*` functions never set a subprocess timeout and treat ANY non-zero exit as "killed," collapsing kill/crash/hang into one bucket, the opposite of O3/O4's explicit four-way split.
4. `nyxloom-trove/reports/assay-P10-BRIEF.md` — P10 (merged before this package, though not in `depends_on`) already extended `runner.assemble_verdict` with two new KEYWORD-ONLY parameters, `evidence: tuple[Evidence, ...] = ()` and `declared_evidence: tuple[EvidenceDeclaration, ...] = ()`, both defaulting to empty so this package's own `claims=`-only call sites are unaffected -- and an identity-coverage guard (`ERROR`/`BAD_LANE_CONFIG` before constructing an incomplete `Verdict`) that this package's own R2/mutation guard should sit alongside, not duplicate or replace. Read `assemble_verdict`'s current signature directly before editing `runner.py`; do not assume the pre-P10 five-parameter shape.
5. `nyxloom-trove/reports/assay-P11-BRIEF.md` — P11 (merged, in `depends_on`) is construction-only and shipped the exact interface this package consumes: `adapter.generate_mutants(text, lines) -> tuple[Mutant, ...] | Literal["UNSUPPORTED"]` (the 7th/final `LanguageAdapter` method), and `Mutant` (`src/assay/mutation.py`, already present — this package ADDS execution logic to the same file, it does not recreate it): frozen `kw_only`, fields `lineno`/`operator`/`description`/`mutated_text` plus a derived `identity` property. `mutated_text` is the FULL replacement file content, not a diff/patch — write it wholesale over the scratch path. Every mutant from one `generate_mutants` call is an INDEPENDENT single-site experiment against the same original text — never cumulative, zero ordering dependency between them, exactly what a `jobs`-bounded executor needs. `result == ()` (nothing to mutate on the declared lines) and `result == "UNSUPPORTED"` (this adapter cannot mutate this text at all, renders `INCONCLUSIVE_NO_MUTANTS`, never green) are both real, distinguishable, legal outcomes this package must handle separately — collapsing them is a real defect, not a simplification.
6. `config.py` is now FORBIDDEN (A-121, moved out of `scope.touch`) — `jobs` is a direct parameter to whatever new function you add, never sourced from `assay.toml`/`JudgeConfig.mutation`. Do not add an operator-filter parameter either — no oracle exercises it (A-121).

## Work

1. Require and record a clean baseline before generating/submitting mutants.
2. Run the lane's declared argv unchanged for baseline and mutants; never derive a test command from a source path. Use an injectable executor constructed with exactly `jobs`; never test concurrency by elapsed time.
3. Restore bytes on every terminal path and serialize results deterministically.
4. Add the closed R2 payload/schema branch and complete hand-written artifacts for all terminal result classes.
5. Break baseline gating, declared-argv fidelity, executor bound, restoration, ordering, and result accounting; record failure counts (A-067).

**R2 payload, pinned (A-116) — read before writing any code.** Two new
frozen `kw_only` dataclasses in `verdict.py`: `MutantOutcome` (`path: str`,
`lineno: int`, `operator: str`, `description: str` — no `mutated_text`,
that would bloat the artifact) and `Mutation` (`total: int`, `killed: int`,
`survived: tuple[MutantOutcome, ...]`, `crashed: tuple[MutantOutcome,
...]`, `budget_exceeded: tuple[MutantOutcome, ...]` — FOUR buckets, not
three, per O3/O4's own explicit four-way enumeration; `__post_init__`
enforces `total == killed + len(survived) + len(crashed) +
len(budget_exceeded)`). `Claim.mutation: Mutation | None = None`, gated to
`rigor == "R2"`. Add the matching third schema `allOf` branch
(`$defs/mutation`, mirroring R1/R3's own branches) — `Claim`'s own
`__post_init__` validation alone does not suffice (A-071's "two
independently-verified layers" discipline). `Claim.mutation`'s PRESENCE is
baseline-conditional: `None` means mutation testing never started
(baseline never resolved to `PASS`); present means it did, even if every
attempted mutant crashed. Two claims can both render `(ERROR,
EXEC_FAILED)` this way (baseline crashed vs. some mutants crashed) — that
is correct, not a bug; the reason code names the outcome class, the
payload (when present) carries the mechanism.

**Outcome mapping, pinned (A-117), no new `ReasonCode` needed:** baseline
non-`PASS` → reuse its `(outcome, reason_code)` verbatim, `mutation`
omitted. Else: `total == 0` → `INCONCLUSIVE`/`NO_MUTANTS`; else `crashed`
non-empty → `ERROR`/`EXEC_FAILED`; else `budget_exceeded` non-empty →
`BUDGET_EXCEEDED`/`LANE_TIMEOUT`; else `survived` non-empty →
`FAIL`/`MUTANTS_SURVIVED`; else `PASS`. This order (crashed >
budget_exceeded > survived) matches the existing cross-claim
`ROLLUP_PRECEDENCE` exactly, applied one level down.

**Execution, pinned (A-119/A-120):** both the baseline and every per-mutant
run call `runner.execute_command` UNMODIFIED — it already takes an
injectable `cwd`, `process_runner`, and `clock`; never write a second
subprocess-invocation path. Isolation is copy-per-mutant: `shutil.copytree`
the project tree into a fresh scratch directory per mutant (the same "fresh
`tmp_path` per test" house pattern, one level down), write
`mutant.mutated_text` over the target file INSIDE the copy, run
`execute_command(..., cwd=<copy>)`, discard the copy — never in-place with
a lock, never a `git worktree` per mutant. This makes "byte-exact
restoration" (O3) true by construction: prove the SHARED source is
provably unchanged after every terminal case, not a write-then-restore
round-trip. New orchestration logic (executor-factory injection, the
copy/run/collect loop) lives in `mutation.py` beside `Mutant`, mirroring
`canary.py`'s own single-module precedent; `runner.py` gains only the R2
wiring into `assemble_verdict` (a fourth optional parameter alongside
`evidence`/`declared_evidence`).

**Do not port from `mutation_gate.py` (A-122):** its `os.cpu_count()`-based
executor cap, and its no-timeout/any-non-zero-exit-is-"killed" per-mutant
run. Both are confirmed present in the reference file and both are wrong
for this package's own oracles.

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

**A. Nothing may make the verdict depend on machine speed.** Do not assert elapsed time, sleep, or use a deadline as an oracle. Prove the bound at the executor constructor/submission boundary; timeouts only prevent hangs.

**B. Nothing may depend on order or workers.** Isolate every source tree, restore global state, and sort by stable identity rather than completion order.

**C. No hollow tests.** A serial/parallel equality check alone does not prove `jobs`; assert the actual executor bound and complete accounting.

**D. No coverage evasion.** No changed-line exclusions.

**E. Control network, clock, and filesystem.** Fake the child runner and executor; use tmp_path and deterministic events, no network.

## Scope / forbid

Mutant construction and adapter capability are frozen P11 inputs. This package owns execution and the R2 producer only.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file, STOP — write `BLOCKED: <reason>` to the log, commit, and exit. Do not improvise a workaround.
