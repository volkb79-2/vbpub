---
schema_version: 1
id: topos-P104-snapshot-coverage
project: topos
title: "Complete snapshot bundle and enrichment coverage"
tier: sonnet5-high
input_revision: "93b998a0"
source: {kind: product-goal, ref: "nyxloom-trove/reports/P96-COVERAGE-GAPS.md"}
stack: none
depends_on: [topos-P103-query-engine-coverage]
session: "resume:topos-coverage"
scope:
  touch:
    - "src/topos/snapshot/enrich.py"
    - "src/topos/snapshot/bundle.py"
    - "tests/**"
    - "nyxloom-trove/handoffs/topos-P104-snapshot-coverage.md"
    - "nyxloom-trove/reports/P104-*.md"
  forbid:
    - "nyxloom-trove/nyxloom.toml"
    - "tools/coverage_gate.py"
    - "pyproject.toml"
oracles:
  - id: O1
    observable: "src/topos/snapshot/enrich.py and src/topos/snapshot/bundle.py each have empty missing_lines and missing_branches in branch-aware JSON from the complete xdist gate, closing the literal 16-line/11-pair residual"
    negative: "completion is claimed from aggregate, serial, focused, rounded, or partial coverage without both whole-file records empty"
    gate: topos-suite
  - id: O2
    observable: "tests assert exact systemd and Docker enrichment outcomes, environment-derived default paths, archive format and extraction behavior, traversal rejection, malformed manifest handling, notable-file selection, cgroup copy failure handling, and unique-path exhaustion"
    negative: "tests mock the function under test, contain pass or assertion-free bodies, assert only non-None/ranges/calls, swallow errors, or weaken archive safety and enrichment behavior"
    gate: topos-suite
  - id: O3
    observable: "the receipt binds the literal 16 lines and 11 pairs to exact-commit nl -ba source and concrete inputs, prints empty literal intersections and whole-file missing sets for two runs, and distinguishes test functions from collected cases"
    negative: "line/arc identities or collection baselines are guessed, counts replace literal sets, or mutation/fail-before claims lack receipts"
    gate: topos-suite
  - id: O4
    observable: "two clean exact topos-suite runs pass with identical complete executed/missing sets for both target files"
    negative: "serial-only evidence, xdist drift, or a green suite with any target statement or branch missing is accepted"
    gate: topos-suite
  - id: O5
    observable: "every retained test has exact behavioral assertions; no pragma, omit, evaluator, gate, dependency, sleep, global leak, or nondeterministic host-state reliance is introduced"
    negative: "coverage is raised with hollow or duplicate tests, private-state leakage, or scope weakening"
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "a residual branch is mechanically unreachable and closing it requires a semantic product decision rather than removal of demonstrably dead or redundant code"
  - "a real defect requires source outside the two snapshot modules"
  - "either clean full-gate run fails or target-file executed/missing sets differ"
advances: []
---

# P104 — Complete snapshot bundle and enrichment coverage

## Goal

Bring all of `snapshot/enrich.py` and `snapshot/bundle.py` to exact 100%
statement and branch coverage with exact behavioral tests for archive safety,
bundle integrity, enrichment failure modes, and deterministic path selection.

Assigned worktree:
`/workspaces/vbpub/.worktrees/feat/topos-P104-snapshot-coverage`.

## Literal residual

The full-gate JSON must close these 16 remaining lines:

```text
snapshot/enrich.py: 26 29 31 46 47
snapshot/bundle.py: 26 27 116 117 148 149 155 178 186 205 249
```

and these 11 remaining branch pairs:

```text
snapshot/enrich.py: 28->29 30->31 61->60
snapshot/bundle.py: 139->148 154->155 177->178 185->186
                    204->205 207->203 245->249 247->245
```

Completion additionally requires `missing_lines=[]` and
`missing_branches=[]` for both complete file records.

## Context to read first

1. `/workspaces/vbpub/AGENTS.md`.
2. `/workspaces/vbpub/nyxloom/reference/AUTHORING.md`.
3. `/workspaces/vbpub/nyxloom/reference/STANDARD.md`, gate contract and seven
   validation principles.
4. `/workspaces/vbpub/nyxloom/nyxloom-trove/LESSONS.md`, especially PL3 and
   living PL4 through P103.
5. `topos/nyxloom-trove/reports/P103-REPORT.md` and `P103-REVIEW.md`, only the
   verified suite baseline and evidence discipline.
6. `topos/tests/test_snapshot_bundle.py`, snapshot-related tests, and the two
   target source files at the assigned revision.

## Work

1. Relocation preflight: print `pwd`, repository root, status, and HEAD.
   Treat every earlier P96–P103 worktree as stale and forbidden.
2. Run the full branch-aware gate and mechanically confirm the literal
   16-line/11-pair residual plus both complete target-file missing sets.
3. Bind every line/pair to exact-revision `nl -ba`, a concrete input, and an
   exact result or exception. Do not infer branch identities from stale line
   numbers.
4. Exercise exact error/status payloads for failed systemctl and Docker
   enrichment, stderr/nonzero systemctl results, reverse segment search, both
   default snapshot directory branches, cgroup ancestor read failure, tar
   fallback, absent zstandard support, unsafe members, malformed manifest
   items, notable-file skipping/loop fallthrough, and exhausted unique names.
   Preserve optional-zstandard behavior and archive traversal protection.
5. Zero `pass` bodies, assertion-free calls, weak ranges, non-None-only
   assertions, function-under-test mocks, duplicate cases, or invented
   mutation/fail-before claims. When inducing filesystem failures, assert the
   resulting externally observable bundle content or exact exception.
6. Use the bind-mounted `tester-unified:local`; never rebuild it.
7. Run the full xdist gate twice. On both JSON files require the literal P104
   intersections empty and complete `missing_lines`/`missing_branches` empty
   for both files; compare their complete executed/missing sets for parity.
8. Separately report added test-function count and pytest collected-case delta
   against the verified P103 total of 2018. Run `git diff --check`.
9. Write `P104-LOG.md`, `P104-REPORT.md`, and `P104-SELFREVIEW.md` with literal
   before/after sets, whole-file final sets, exact commands/exits, truthful
   negative evidence, collection arithmetic, assertion audit, and parity.
10. Commit, adversarially self-review, repair every finding, rerun verification,
    and commit the final state.

## Gate

Use the exact `[gates.topos-suite]` argv. Focused or serial runs are diagnostic.
Completion requires two complete parallel runs, literal residual checks, and
both whole-file target checks all exiting zero.

## Scope / forbid

Only frontmatter touch paths may change. Do not alter gate, tooling, or
dependencies. No coverage pragmas or omissions. Target source edits require a
regression-proven defect or independently demonstrable dead path.

## BLOCKED

On a mechanical `escalate_if`, stop and write
`BLOCKED: <trigger and exact evidence>` to `P104-LOG.md`. Never return partial
`done`.
