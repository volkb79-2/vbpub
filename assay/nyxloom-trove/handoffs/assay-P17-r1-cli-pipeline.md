---
schema_version: 1
id: assay-P17-r1-cli-pipeline
project: assay
title: "assay run executes a declared Python R1 lane end to end"
tier: implement-2
input_revision: "48771e48c7b2ed7ed937cbe07e193718c6f242bb"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P16-independent-verdict-conformance-v3]
session: resume:assay-v11-cli
scope:
  touch: ["src/assay/cli.py", "src/assay/runner.py", "src/assay/config.py", "src/assay/registry.py", "src/assay/coverage.py", "src/assay/verdict.py", "src/assay/schemas/**", "tests/**", "README.md"]
  forbid: ["src/assay/mutation.py", "src/assay/canary.py", "src/assay/attestation.py", "src/assay/adapters/go.py"]
oracles:
  - id: O1
    observable: "An installed assay wheel runs a real two-commit Python fixture and emits exactly R0 and R1 claims matching a hand-written complete schema-v3 artifact"
    negative: "Leaving _cmd_run R0-only ends BAD_LANE_CONFIG or omits the R1 claim"
    gate: tester-unified
  - id: O2
    observable: "The lane command executes exactly once, the comparison base resolves once, and verdict timing encloses command plus R1 judgment"
    negative: "A second baseline invocation or using the R0 ended timestamp fails the invocation ledger or timestamp ordering"
    gate: tester-unified
  - id: O3
    observable: "A pre-existing declared coverage artifact is removed before execution; PASS requires a new regular non-symlink artifact produced by this invocation"
    negative: "A command returning zero without writing coverage cannot reuse the seeded stale PASS report"
    gate: tester-unified
  - id: O4
    observable: "Dirty worktree, missing tool, malformed coverage, unreadable coverage, uncovered lines, and clean PASS each emit the correct complete artifact whenever HEAD is known"
    negative: "Propagating an R1 AssayError without a verdict leaves at least one hand-written terminal artifact unreachable"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "one command invocation cannot be shared by R0 and R1"
  - "a terminal path with a known HEAD cannot be represented by schema v3"
mutexes: []
---

# P17 — R1 CLI pipeline

The claim to attack: **`assay run` executes and judges one declared Python R1 lane as one commit-bound operation.**

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P17-r1-cli-pipeline`
on branch `feat/assay-P17-r1-cli-pipeline`.

## Context to read first

1. `docs/DESIGN-GUIDE.md` §§1, 6, 10, 11, and 12; decisions A-017–A-019, A-023–A-025, A-031, A-040–A-041, A-049, A-067, A-090–A-102, A-123–A-130.
2. P15 and P16's merged handoffs, implementations, decisions, and successor briefs. Their input-integrity and schema-v3 contracts are dependencies, not contracts to restate or weaken.
3. `src/assay/cli.py::_cmd_run`, all of `src/assay/runner.py`, and `src/assay/config.py`'s rigor-dependent fields. Read the R0 installed-wheel fixture in `tests/test_standalone.py` and R1 real-Git pipeline tests in `tests/test_runner_evaluate_r1.py`.
4. `src/assay/registry.py`, `src/assay/adapters/python.py::PythonAdapter`, and `src/assay/adapters/go.py::GoAdapter`. The CLI needs an explicit immutable built-in registry; unknown language never falls back.
5. `src/assay/coverage.py` artifact loading and parser registry. A generated artifact is an output of this invocation, not durable input that can be reused from an earlier run.
6. `nyxloom-trove/reports/assay-v1-post-series-review-sol.md` findings 1, 6, 10, and 11 and its P17 contract.
7. `/workspaces/vbpub/nyxloom/reference/DOCTRINE.md` independent-evidence, gate/cockpit, defaults, and installed-artifact rules.

## Work

1. Add a required `judge.base` string for every lane declaring R1 or R2. Preserve Git's existing merge-commit/merge-base resolution, and record the resolved full commit in P16's `judgment.r1`; never default to `main` or another guessed ref.
2. Build a fresh explicit registry whose entries pair each adapter with the rigor levels reachable through the installed product. Register Python through R1 in this package; do not advertise Python R2/R3 or Go R1+ before their packages land. Resolve `judge.language` exactly, reject declared rigor above that entry's capability, and preflight every declared adapter external tool before the lane command. Missing prerequisites render a complete `NO_MEASUREMENT` claim, never fallback behavior.
3. Before running anything, resolve and record full `HEAD`, require the whole Git worktree/index to be clean, and reject a coverage artifact path that is tracked, outside project root, or a symlink. Initial HEAD failure may remain a pre-artifact error because no honest commit identity exists; every later terminal path must emit a complete artifact.
4. Remove an existing untracked regular coverage artifact before command execution. After a successful R0 command, require a newly-created regular non-symlink artifact. A command that writes nothing must render missing measurement and cannot consume prior output.
5. Execute the lane command exactly once. Build R0 from that result, run R1 only when its prerequisite permits, and propagate prerequisite adversity explicitly rather than launching or parsing work that cannot render a valid judgment.
6. Catch R1 Git/format/read failures after HEAD is known and represent them as complete R1 claims, including the previously unreachable `GIT_FAILED`, `FORMAT_MISMATCH`, and `UNREADABLE_ARTIFACT` producer pairs. Do not catch programmer errors or invent a generic PASS/ERROR fallback.
7. Assemble exactly the declared rigor identities and P16's effective judgment policy. Set final verdict `ended` after all R1 work, while preserving the lane command's own R0 start/end evidence separately where the v3 contract requires it.
8. Prove the full path through the installed console script in a disposable two-commit Python project using a hand-written lane file, real pytest coverage output, and a complete independent expected artifact. Source-tree `PYTHONPATH` execution is not this oracle.
9. Break R1 dispatch, built-in registration, base resolution, whole-tree cleanliness, stale-artifact removal, single invocation, exception-to-claim conversion, and final timing separately; run the real gate and record exact A-067 failure counts.

## Carried in from P16, merged (read before writing work items 1, 6 and 7)

**A-136 — a judged status carries the payload it judged, and `Claim` now
refuses to construct one that does not.** An R1 claim whose status is
`PASS` or `FAIL` MUST carry `coverage`; `NO_MEASUREMENT` must not, exactly
as before. `ERROR` stays payload-free, so work item 6's three
newly-reachable producer pairs (`GIT_FAILED`, `FORMAT_MISMATCH`,
`UNREADABLE_ARTIFACT`) are unaffected — but they are `ERROR`, and rendering
one of them as `FAIL` will now be refused at construction rather than
emitted.

**The `judgment.r1`-iff-`coverage` correspondence is a trap in the obvious
direction.** `Verdict` refuses BOTH halves: `judgment.r1` present without
an R1 coverage claim, and an R1 coverage claim without `judgment.r1`. So a
lane that resolves its R1 policy perfectly and then renders
`NO_MEASUREMENT` (dirty tree, base-is-head, empty coverage) must NOT record
that policy — "I resolved it, so I'll record it" builds a `Verdict` that
will not construct. `runner.assemble_verdict` raises a typed
`AssayError` (`ERROR`/`BAD_LANE_CONFIG`) on the missing half before
construction, so that direction at least fails as an assay error rather
than a bare `ValueError`; the present-without-a-claim direction does not,
and reaches `Verdict.__post_init__`.

Build `JudgmentR1.base` from `check_base_is_head`'s resolved `base_rev`
(work item 1's "record the resolved full commit"), and `source_roots` from
`judge.source_roots` — the DECLARED strings, never
`JudgeConfig.source_root_paths`, which are resolved absolute paths and
would bind the artifact to one machine's filesystem.

**A-138 — a foreign `schema_version` is a consumer migration.**
`assay verify` reports it as a version problem and reads nothing else. Do
not add a compatibility path, a default, or an upgrade.

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

**A. No speed-dependent verdicts.** Command completion and injected clocks establish ordering; elapsed duration never determines correctness.

**B. No order/worker dependence.** Every repository, artifact path, and wheel installation is unique per test; no ambient registry is mutated.

**C. No hollow tests.** Run the installed console script and compare complete hand-written artifacts; a call-count fake alone cannot satisfy O1.

**D. No coverage evasion.** Maintain 100% statement and branch and record the real failure count for each controlled pipeline break.

**E. Control inputs.** Work offline in disposable Git projects with explicit environment, clock, process, and filesystem boundaries.

## Scope / forbid

This package lands Python R1 CLI orchestration only. It must not interpret mutation, canary, or attestation tables, edit Go adapter semantics, or claim R2/R3 reachability. P16's v3 schema may be populated but not redesigned.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file, STOP — write `BLOCKED: <reason>` to the log, commit, and exit. Do not improvise a workaround.
