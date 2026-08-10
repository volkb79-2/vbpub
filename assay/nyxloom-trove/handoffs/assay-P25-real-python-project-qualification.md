---
schema_version: 1
id: assay-P25-real-python-project-qualification
project: assay
title: "An existing Python project obtains the same R1 answer from installed Assay"
tier: implement-2
input_revision: "2f2167f5928e5deacd93f1e9565238aef8acfe32"
source: {kind: product-goal, ref: "nyxloom-trove/reports/assay-v2-post-series-review-sol-P15-P19.md"}
stack: none
depends_on: [assay-P24-versioned-wheel-contract]
session: fresh
scope:
  touch: ["gate/python/**", "tools/tester-unified-gate.sh", "nyxloom-trove/nyxloom.toml", "tests/**", "README.md", "docs/DESIGN-GUIDE.md"]
  forbid: ["src/assay", "pyproject.toml", "nyxloom-trove/carve-assets/P24"]
oracles:
  - id: O1
    observable: "The versioned installed Assay wheel runs real pytest with coverage.py in an R0+R1 lane over a disposable two-commit Topos tree and emits an exact independently constructed v4 artifact"
    negative: "A hello-world-only fixture, source-tree import, or committed coverage file cannot satisfy the real Topos artifact comparison"
    gate: tester-unified
  - id: O2
    observable: "Assay and the disposable copy's independent Topos coverage evaluator classify the same changed executable/comment/missing lines and agree on PASS, FAIL, and NO_MEASUREMENT scenarios"
    negative: "A source-root, merge-base, exclusion, or 0/0 assumption makes the two tools disagree on the same commits/profile"
    gate: tester-unified
  - id: O3
    observable: "Topos and the shared vbpub checkout are byte-identical before/after; all work occurs in committed-object snapshots with the exact declared command plan and verified background cgroup"
    negative: "The qualification writes coverage/build residue into ../topos or launches a container without the verified configured cgroup parent"
    gate: tester-unified
  - id: O4
    observable: "Universal-PASS, wrong source root, stale/missing profile, dirty tree, base-is-HEAD, and post-command repository mutation each fail a complete artifact or the independent comparison"
    negative: "Any named corruption remains green because both checks consume the same producer-generated expectation"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "the pinned tester-unified image cannot run Topos's declared test dependency closure offline"
  - "Topos's independent coverage behavior and Assay's declared contract disagree on a product-policy question rather than an implementation defect"
mutexes: [merge-lane]
---

# P25 — real Python project qualification

The claim to attack: **a real existing Python project can replace its changed-line evaluator with the installed Assay product without changing the answer.**

## Dispatch contract

- Contract class: **2d — constrained implementation**.
- Required roles: **Sonnet xhigh implementer → Opus xhigh independent reviewer**.
- Readiness: **PROVISIONAL until P24 merges.** Before dispatch, the controller
  resolves and records the exact Topos commit and Sol/Opus freezes the disposable
  patch, hand manifest, expected v4 artifact, independent Topos command, and a
  clean tagged qualification wheel/manifest built from the P25 input with
  P24's exact tracked-source and hash-bound recipe.
- Implementer freedom: fixture plumbing and equivalent test decomposition only;
  the external project, commits, comparison semantics, and no-adoption boundary
  are fixed.

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P25-real-python-project-qualification`
on branch `feat/assay-P25-real-python-project-qualification`.

## Context to read first

1. `nyxloom-trove/reports/assay-v2-post-series-review-sol-P15-P19.md` §§Original-goal comparison and Future plan; decisions A-037–A-041, A-130–A-131, A-153–A-162.
2. P22/P23's committed-snapshot/effective-plan contract and P24's versioned-
   wheel contract. This package writes no Assay production code; a failure
   requiring it is BLOCKED and routes back upstream.
3. `/workspaces/vbpub/topos/pyproject.toml`, `tools/coverage_gate.py`, `tests/conftest.py`, and `nyxloom-trove/nyxloom.toml`'s `gates.topos-suite` semantics. Read behavior, not its literal Docker argv: that config currently hardcodes host path/cgroup values and is not reusable launch code under the repo-wide doctrine.
4. `/workspaces/vbpub/topos/src/topos` and representative tests only far enough to select stable executable, comment-only, excluded, and unmeasured cases. Never edit that tree.
5. `tests/test_self_hosting.py` and P24's wheel/hash witness for the two-
   environment installed-product pattern and independent complete-artifact
   comparison.
6. `/workspaces/vbpub/nyxloom/reference/DOCTRINE.md` gate, bounded evidence, independent oracle and consumer-ownership rules.

## Environment setup

Use Assay's registered `tester-unified` gate only. Extend
`tools/tester-unified-gate.sh`'s inner mode inside the same verified,
uid-complete container after the installed-wheel/self-hosting step; preserve
all existing phase/final receipt markers. Do not invoke Topos's outer Docker
command and do not start an unplaced container. All Python wheels/dependencies
come from the already-pinned offline image/closure.

The JIT carver, not the implementer, prepares the exact P25 qualification wheel
and release manifest from only tracked Assay source at the post-P24 anchor,
using P24's fixed tag/identity/epoch/toolchain recipe. The harness runs P24's
standalone `verify`, captures its sole PEP 508/hash line, and installs only via
offline pip `--require-hashes`. It may not copy/modify P24 assets, rebuild a
different wheel at runtime, install the first glob match, or use ordinary
`pip install <path>` after a separate hash check.

## Implementation packet (normative)

### Qualification topology and fixed lane

`gate/python/qualify_topos.py` owns the validation harness; production Assay
code is forbidden. It records the exact vbpub input commit and extracts only
that commit's tracked `topos/` subtree into a new disposable Git repository,
still rooted as `repo/topos/...`. It then creates two deterministic commits:

```text
repo/
  topos/src/topos/...               # real tracked package
  topos/tests/...                   # real tracked suite
  topos/tools/coverage_gate.py      # independent comparator
  topos/src/topos/_assay_probe.py   # controlled delta only in HEAD
  topos/tests/test_assay_probe.py   # imports real installed/project package
  .assay/.gitignore                # tracked parent; ignores generated profile
```

The profile itself is absent from every commit; `.assay/.gitignore` contains
`*` plus `!.gitignore`, so P20 can preflight a real tracked parent without the
runtime output making the snapshot dirty. The probe source is a literal checked into `gate/python/`; its numbered manifest
names one executed statement, one deliberately missing statement, one comment
line, and one `pragma: no cover` line. The harness first asserts those literal
line numbers against the seeded bytes, so an edited fixture cannot silently
move the expectation.

The lane is generated with no optional/defaulted fields:

```toml
schema_version = 1
[lanes.topos-qualification]
scope = "S1"
rigor = ["R0", "R1"]
enforcement = "gate"
argv = ["/opt/tester-venv/bin/python", "-m", "pytest", "topos/tests", "-q",
        "-n", "auto", "--cov=topos/src/topos", "--cov-branch",
        "--cov-report=json:.assay/topos-coverage.json"]
env = {PYTHONPATH = "topos/src:topos"}
env_passthrough = []
budget = "15m"
allow_argv_append = false
[lanes.topos-qualification.judge]
language = "python"
source_roots = ["topos/src/topos"]
base = "<full seeded base OID>"
fail_under = 100.0
allow_excluded = true
[lanes.topos-qualification.judge.coverage]
format = "coverage.py-json"
artifact = ".assay/topos-coverage.json"
```

The absolute interpreter is an image fact checked by the gate recipe, not an
Assay fallback. `PYTHONPATH` exposes Topos only; a separate assertion proves
the imported `assay` package and CLI live beneath P24's installed wheel venv.

### Three independent witnesses

1. Installed Assay emits the whole v4 artifact from the seeded commits and real
   coverage file.
2. The harness constructs a closed expected line manifest from its literal
   probe bytes, Git OIDs, and declared lane. It never parses Assay output to
   choose expected lines/status.
3. The unmodified copied `topos/tools/coverage_gate.py` runs with
   `--repo <repo> --base <base-oid> --coverage-json <profile> --source
   topos/src/topos --fail-under 100` and its pure `evaluate` result is captured
   separately.

For executed/missing/comment and 0/0 cases, all three agree on covered,
changed-executable, missing identities, and terminal class. Topos cannot report
exclusion provenance: compare parity with Assay's `allow_excluded=true`, then
run an **Assay-versus-hand-manifest** negative with `allow_excluded=false` and
require `EXCLUDED_LINES`; do not falsely require Topos's older evaluator to
express that fact. Dirty/base/profile/post-command integrity terminals are also
Assay-versus-manifest checks, not invented Topos features.

### Scenario matrix and traceability

| Scenario | Assay | Topos comparator | Independent source |
|---|---|---|---|
| executed + comment delta | PASS | PASS | literal line manifest |
| one missing statement | `UNCOVERED_LINES` | fail | literal line manifest |
| excluded, allowed | PASS | PASS/line absent | coverage JSON + manifest |
| excluded, forbidden | `EXCLUDED_LINES` | not compared | manifest only |
| base=HEAD / missing or stale profile / dirty or post-run mutation | named non-PASS | not compared | seeded state + command ledger |
| universal-PASS producer mutation | whole expected artifact differs | comparator/manifest remain honest | both independent witnesses |

Work 1–3 -> harness/lane/expected artifact -> O1/O3; work 4–5 -> differential
matrix -> O2/O4; work 6–7 -> provenance and controlled breaks -> all oracles.
The REPORT records pinned input OID, wheel filename/version/hash, exact command,
tree hashes, comparison table, tests, and break counts. Scratch naming and
probe function names may vary; the topology, lane, three-way independence,
policy comparison, and no-real-Topos-write rule may not.

## Work

1. Materialize `/workspaces/vbpub/topos` tracked content into a disposable repository inside the gate and create a controlled two-commit delta containing independently enumerated executable, comment-only, covered, uncovered, excluded, and profile-missing cases. Preserve Topos's real package/test topology and dependency closure; do not reduce it to a copied hello-world module.
2. Generate real coverage.py JSON by running Topos's real pytest suite (or the smallest pre-existing declared suite that still imports the actual package and exercises the controlled delta) through the exact installed-wheel Assay lane. No `PYTHONPATH` may expose Assay source; Topos source resolution is declared as part of the consumer command.
3. Build the complete expected v4 artifact independently from the seeded commits, declared lane, and hand-calculated changed-line manifest. Compare the whole document except deliberately injected version/timestamps/OIDs, whose exact real values are asserted separately.
4. Run the disposable copy of `topos/tools/coverage_gate.py` against the same base, HEAD, profile, source root, and floor. Compare the common coverage semantics exactly as fixed in the packet; separately prove Assay's richer exclusion policy against the hand manifest. Neither tool's output may be used to construct the other's expectation.
5. Exercise PASS, `UNCOVERED_LINES`, allowed and forbidden exclusions under the packet's comparison rules, `EMPTY_COVERAGE`, dirty tree, base-is-HEAD, stale/missing output, and command-created repository mutation. Preserve Topos/shared-tree hashes across every terminal.
6. Keep this a qualification, not a migration: add no `assay.toml` or dependency to real Topos, change no Topos gate, and make no claim that Topos consumes Assay. Record the exact wheel version/hash and the pinned Topos input revision used so a later Topos-owned adoption handoff can reproduce the comparison.
7. Break the installed-wheel boundary, independent manifest, Topos comparator, source-root prefix, commit binding, profile freshness, and universal-PASS negative separately; run the real gate and record exact A-067 failure counts.
8. After adding the qualification dispatch, sweep route reachability and
   coverage: prove no hello-world shortcut, source import, stale profile,
   alternate wheel-selection path, or producer-authored expected artifact is
   reachable. Record every retained non-production fixture explicitly.

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

**A. No speed-dependent verdicts.** Real process completion and exact artifacts decide; duration is never compared.

**B. No order/worker dependence.** Each scenario has its own disposable Topos repository, profile, cache, and fixed input revision.

**C. No hollow tests.** Assay, the independent manifest, and Topos's evaluator are three distinct witnesses; no output constructs another's expectation.

**D. No coverage evasion.** Keep the full Assay gate and record every controlled-break count; no reduced suite may silently replace the real project proof.

**E. Control inputs.** Offline pinned wheel/image, disposable commits, explicit env/argv, no network, Docker, host services, or writes to real Topos.

## Scope / forbid

This package adds validation fixtures and gate wiring only. It must not repair Assay production code, modify/migrate Topos, copy Topos source into Assay's repository, or invoke Topos's hardcoded outer Docker command.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file, STOP — write `BLOCKED: <reason>` to the log, commit, and exit. Do not improvise a workaround.
