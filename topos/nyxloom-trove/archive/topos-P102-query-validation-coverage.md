---
schema_version: 1
id: topos-P102-query-validation-coverage
project: topos
title: "Close query construction and validation coverage tranche"
tier: sonnet5-high
input_revision: "a4563a6f"
source: {kind: product-goal, ref: "nyxloom-trove/reports/P96-COVERAGE-GAPS.md"}
stack: none
depends_on: [topos-P101-query-coverage]
session: "resume:topos-coverage"
scope:
  touch:
    - "src/topos/query/engine.py"
    - "tests/**"
    - "nyxloom-trove/handoffs/topos-P102-query-validation-coverage.md"
    - "nyxloom-trove/reports/P102-*.md"
  forbid:
    - "src/topos/query/semantics.py"
    - "nyxloom-trove/nyxloom.toml"
    - "tools/coverage_gate.py"
    - "pyproject.toml"
oracles:
  - id: O1
    observable: "the declared 22 engine.py validation lines and 20 branch pairs are all absent from missing_lines/missing_branches in branch-aware JSON from the complete xdist gate"
    negative: "completion is claimed from aggregate, serial, focused, rounded, or from closing only part of either literal declared set"
    gate: topos-suite
  - id: O2
    observable: "tests construct real query dictionaries and Query values and assert exact parsed fields, typed validation errors, error messages, resolved semantics, sort behavior, and cap constraints"
    negative: "tests mock the parser/validator, contain pass or assertion-free bodies, assert only non-None/ranges/calls, swallow errors, or weaken validation"
    gate: topos-suite
  - id: O3
    observable: "the receipt checks the literal declared line and branch sets, binds each to exact-commit nl -ba source and a concrete input, and distinguishes test functions from collected cases"
    negative: "the package claims engine.py is globally exact, guesses line identities/counts, or claims mutations/fail-before checks without receipts"
    gate: topos-suite
  - id: O4
    observable: "two clean exact topos-suite runs pass with identical executed/missing statement and branch sets for engine.py and every declared P102 residual is closed"
    negative: "serial-only evidence, xdist drift, or a green suite with any declared residual still missing is accepted"
    gate: topos-suite
  - id: O5
    observable: "every retained test has exact behavioral assertions; no pragma, omit, evaluator, gate, dependency, sleep, global leak, or nondeterministic host-state reliance is introduced"
    negative: "coverage is raised with hollow/duplicate tests, private-state leakage, or scope weakening"
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "a declared branch is mechanically unreachable and closing it requires a semantic product decision rather than removal of demonstrably dead or redundant code"
  - "a real defect requires source outside query/engine.py"
  - "either clean full-gate run fails or engine.py executed/missing sets differ"
advances: []
---

# P102 — Close query construction and validation coverage tranche

## Goal

Close the complete pre-execution validation tranche in `query/engine.py`
without claiming that the whole 904-line module is exact. P103 owns the
remaining projection/execution/cap tranche and the final whole-file 100%
oracle.

Assigned worktree:
`/workspaces/vbpub/.worktrees/feat/topos-P102-query-validation-coverage`.

## Literal acceptance set

The full-gate JSON must no longer report any of these 22 lines:

```text
138 143 151 153 156 159 173 174 175 176 177 178 179 185 188
212 219 220 256 260 269 308
```

It must no longer report any of these 20 branch pairs:

```text
137->138 142->143 148->156 150->151 152->153
158->159 171->173 173->174 173->185 175->176
175->177 177->178 177->179 187->188 211->212
218->219 255->256 259->260 268->269 307->308
```

The checker must compare literal integer sets/pairs. A percentage or changed
line floor is not this package oracle.

## Context to read first

1. `/workspaces/vbpub/AGENTS.md`.
2. `/workspaces/vbpub/nyxloom/reference/AUTHORING.md`.
3. `/workspaces/vbpub/nyxloom/reference/STANDARD.md`, gate contract and seven
   validation principles.
4. `/workspaces/vbpub/nyxloom/nyxloom-trove/LESSONS.md`, especially PL3 and
   living PL4 through P101.
5. `topos/nyxloom-trove/reports/P96-COVERAGE-GAPS.md`, only the
   `query/engine.py` entry, and only the literal acceptance subset above.
6. `topos/tests/test_query.py` and `topos/src/topos/query/engine.py` through
   line 330. Later engine behavior is context only, not a P102 target.

## Work

1. Relocation preflight: print `pwd`, repository root, status, and HEAD.
   Treat every earlier P96–P101 worktree as stale and forbidden.
2. Run the full branch-aware gate and mechanically confirm the current literal
   P102 residual subset.
3. Bind every declared line/pair to exact-commit `nl -ba`, a real query input,
   and an exact parsed value or typed error.
4. Extend established query tests. Exercise mapping/type checks, metric,
   selector, sort and caps construction, integer/bool rejection, semantic and
   sort resolution, duplicate/unknown/incompatible values, and boundary caps.
5. Zero `pass` bodies, assertion-free calls, weak ranges, non-None-only
   assertions, function-under-test mocks, or tests named more strongly than
   their assertions.
6. Use bind-mounted `tester-unified:local`; never rebuild it.
7. Run the full xdist gate twice. On both JSON files, assert that intersection
   of `missing_lines` with the 22-line set is empty and intersection of
   `missing_branches` with the 20-pair set is empty; also compare complete
   engine.py executed/missing sets for parity.
8. Separately collect test function count and pytest case delta against the
   verified P101 total of 1984. Run `git diff --check`.
9. Write `P102-LOG.md`, `P102-REPORT.md`, and `P102-SELFREVIEW.md` with literal
   before/after sets, exact commands/exits, truthful negative evidence,
   collection arithmetic, assertion audit, and parity. Explicitly state that
   remaining post-validation engine gaps belong to P103.
10. Commit, adversarially self-review, repair findings, rerun verification,
    and commit the final state.

## Gate

Use the exact `[gates.topos-suite]` argv. Focused/serial runs are diagnostic.
Completion requires two complete parallel runs plus the literal P102 residual
checker exiting zero on both.

## Scope / forbid

Only frontmatter touch paths may change. Do not alter gate/tooling/dependencies
or `query/semantics.py`. No coverage pragmas or omissions. Target source edits
require a regression-proven defect or independently demonstrable dead path.

## BLOCKED

On a mechanical `escalate_if`, stop and write
`BLOCKED: <trigger and exact evidence>` to `P102-LOG.md`. Never return a
partial `done`.
