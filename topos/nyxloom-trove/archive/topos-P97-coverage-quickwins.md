---
schema_version: 1
id: topos-P97-coverage-quickwins
project: topos
title: "Close the small deterministic statement and branch gaps"
tier: sonnet5-high
input_revision: "025fb843"
source: {kind: product-goal, ref: "nyxloom-trove/reports/P96-COVERAGE-GAPS.md"}
stack: none
depends_on: [topos-P96-max-test-gate]
session: "resume:topos-coverage"
scope:
  touch:
    - "src/topos/collect/zswapmath.py"
    - "src/topos/collect/dockerjoin.py"
    - "src/topos/collect/collector.py"
    - "src/topos/model.py"
    - "src/topos/registry.py"
    - "src/topos/procs/identity.py"
    - "src/topos/procs/sensitivity.py"
    - "src/topos/procs/owners.py"
    - "src/topos/ui/keys.py"
    - "src/topos/ui/damon_control.py"
    - "src/topos/ui/sparkline.py"
    - "src/topos/record/ring.py"
    - "src/topos/inspect_files/plan.py"
    - "src/topos/damon/paddr.py"
    - "src/topos/actions/preview.py"
    - "src/topos/daemon/component_health.py"
    - "tests/**"
    - "tools/__init__.py"
    - "nyxloom-trove/nyxloom.toml"
    - "nyxloom-trove/handoffs/topos-P97-coverage-quickwins.md"
    - "nyxloom-trove/reports/P97-*.md"
  forbid:
    - "pyproject.toml"
    - "tools/coverage_gate.py"
oracles:
  - id: O1
    observable: "every targeted source module is reported at exactly 100% statements and 100% branches by the branch-aware JSON produced by the full parallel topos gate"
    negative: "a target is accepted from aggregate coverage, rounded output, line-only coverage, or a focused test invocation that omits the complete suite"
    gate: topos-suite
  - id: O2
    observable: "new tests drive real public or narrow internal behavior through both sides of every feasible target branch, with exact outputs, exceptions, and side effects asserted"
    negative: "coverage is obtained by calling lines without behavioral assertions, mocking the function under test, swallowing exceptions, deleting code, broad omission, or adding an unjustified pragma"
    gate: topos-suite
  - id: O3
    observable: "each repaired behavior has fail-before evidence against the P96 baseline or an equivalent deliberate mutation, followed by pass-after evidence from the same focused test"
    negative: "tests are written only after observing green and no mechanical evidence shows that the assertion can detect the missing or wrong behavior"
    gate: topos-suite
  - id: O4
    observable: "the exact declared topos-suite passes twice from a clean worktree and its coverage JSON is identical for every targeted file across both xdist runs"
    negative: "the package relies on a serial-only result, accepts xdist drift, or captures fork-child coverage to conceal a test that never observes its result"
    gate: topos-suite
  - id: O5
    observable: "tools is an explicit package and the authoritative gate declares tests-pass, changed-line-coverage, and canary-verified after P96's recorded TRUSTWORTHY verdict"
    negative: "the review helper becomes an implementation gate, the gate command changes without a new canary, or canary-verified is declared without the P96 control-path evidence"
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "a targeted branch is mechanically unreachable under Python 3.14 and closing it would require a pragma or product semantic change not justified by an existing invariant"
  - "a real defect is found whose repair requires a source file outside the named touch set"
  - "either of two clean exact-gate runs fails or produces different executed or missing sets for a targeted file"
advances: []
---

# P97 — Close small deterministic coverage gaps

## Goal

Heal the P96 ledger's small, deterministic gaps as one cache-efficient package.
The package is complete only when every named target reaches exact 100%
statement and branch coverage in the full xdist gate. Global 100% is a later
milestone; do not enable `--cov-fail-under=100` yet.

This is test reconstruction, not percentage painting. Preserve useful
defensive behavior and test it. A production edit is allowed only in one of the
named source files and only when a failing regression test exposes a real bug
or a narrow testability defect. Do not change behavior merely to make a branch
easy to execute.

## Context to read first

1. `/workspaces/vbpub/AGENTS.md`.
2. `/workspaces/vbpub/nyxloom/reference/AUTHORING.md`, especially behavioral
   oracles, fail-before/pass-after, gates, and BLOCKED.
3. `/workspaces/vbpub/nyxloom/reference/STANDARD.md`, the gate contract and the
   seven validation principles.
4. `/workspaces/vbpub/nyxloom/nyxloom-trove/LESSONS.md`, PL2 and PL3.
5. `topos/nyxloom-trove/reports/P96-COVERAGE-GAPS.md`, using only the entries
   for this package's named modules.
6. Existing tests nearest each target. Extend their established fixtures and
   assertion style; do not rebuild helpers already present.

## Target set

Close all P96 missing statements and branch pairs for:

- `collect/zswapmath.py`, `collect/dockerjoin.py`, `collect/collector.py`
- `model.py`, `registry.py`
- `procs/identity.py`, `procs/sensitivity.py`, `procs/owners.py`
- `ui/keys.py`, `ui/damon_control.py`, `ui/sparkline.py`
- `record/ring.py`, `inspect_files/plan.py`, `damon/paddr.py`
- `actions/preview.py`, `daemon/component_health.py`

Also add the missing empty `tools/__init__.py` recommended by P96's independent
review. Do not use it to alter evaluator behavior.

## Work

1. Record a machine-readable pre-change slice from the P96 ledger for every
   target: missing statements and missing branch pairs.
2. For each target, map every gap to an observable behavior. Add focused tests
   that fail against the unhealed baseline or demonstrate an equivalent
   deliberate mutation failure before retaining them.
3. Prefer real values, temporary files, injected narrow boundaries, and
   deterministic fakes. Mock only external effects; never mock the unit whose
   branch is being proved.
4. Run the relevant focused tests while iterating, then run the exact
   `topos-suite` argv. Parse `/tmp/topos-coverage.json` and mechanically assert
   `missing_lines == []` and `missing_branches == []` for every target.
5. Add `tools/__init__.py`. In `nyxloom.toml`, leave `topos-suite` unchanged
   and add `asserts = ["tests-pass", "changed-line-coverage",
   "canary-verified"]`; P96's post-merge control-path result was
   `TRUSTWORTHY`, with `cli.py` canary exit 1.
6. Run the exact gate a second time from a clean worktree. Compare targeted
   per-file executed/missing line and branch data between runs; they must be
   identical.
7. Write `P97-REPORT.md`, `P97-LOG.md`, and `P97-SELFREVIEW.md`. Include tests
   added, fail-before evidence, exact commands/exits, target-by-target before
   and after counts, any product edits and their justification, pragma/omit
   audit, and the two-run parity result.
8. Self-review the final diff adversarially. Check hollow assertions,
   over-mocking, exception swallowing, branch misinterpretation, nondeterminism,
   scope, and `git diff --check`. Repair findings and commit the final result.

## Gate

Run the exact `[gates.topos-suite]` command from
`topos/nyxloom-trove/nyxloom.toml`. A focused test command is iteration
evidence only. The package is not complete until two clean exact-gate runs pass
and the target-level JSON checks prove exact statement-and-branch closure.

## Scope / forbid

Only the frontmatter touch paths may change. Do not edit the coverage evaluator,
dependency metadata, unrelated source, existing P96 evidence, or global
coverage exclusions. Do not add `# pragma: no cover` unless the mechanical
unreachability escalation fires; if it fires, stop and report BLOCKED instead
of adding the pragma.

## BLOCKED

If any `escalate_if` condition fires, stop implementation, write
`BLOCKED: <trigger and exact evidence>` to `P97-LOG.md`, commit only coherent
evidence, and return the trigger. Do not widen scope or soften coverage.
