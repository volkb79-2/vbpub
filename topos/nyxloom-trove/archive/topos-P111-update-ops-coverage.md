---
schema_version: 1
id: topos-P111-update-ops-coverage
project: topos
title: "Complete Docker update operation coverage"
tier: sonnet5-high
input_revision: "0a26749b"
source: {kind: product-goal, ref: "nyxloom-trove/reports/P96-COVERAGE-GAPS.md"}
stack: none
depends_on: [topos-P110-action-policy-coverage]
session: "fresh"
scope:
  touch:
    - "src/topos/actions/update_ops.py"
    - "tests/**"
    - "nyxloom-trove/handoffs/topos-P111-update-ops-coverage.md"
    - "nyxloom-trove/reports/P111-*.md"
  forbid:
    - "src/topos/actions/execute.py"
    - "src/topos/actions/catalog.py"
    - "src/topos/actions/squeeze.py"
    - "nyxloom-trove/nyxloom.toml"
    - "tools/coverage_gate.py"
    - "pyproject.toml"
oracles:
  - id: O1
    observable: "update_ops.py has empty missing_lines and missing_branches in branch-aware JSON from the complete xdist gate, closing the literal 28-line/14-pair residual"
    negative: "completion is claimed from aggregate, serial, focused, rounded, warning-bearing, dirty-tree, or partial coverage without the whole-file record empty"
    gate: topos-suite
  - id: O2
    observable: "tests assert exact validation errors, resolution/read calls, cgroup path, argv/plan/render output, and fail-closed usage verdicts without accessing host cgroupfs or Docker"
    negative: "tests mock a target function, assert only substrings/selected fields/non-None/ranges/calls, contain pass or assertion-free bodies, or consult host state"
    gate: topos-suite
  - id: O3
    observable: "both resolution and preview reader failures catch Exception but allow KeyboardInterrupt/SystemExit to propagate"
    negative: "BaseException swallowing is retained or codified as desired behavior"
    gate: topos-suite
  - id: O4
    observable: "two complete xdist gates run from the exact clean implementation commit, close the literal/whole-file sets with identical normalized records, and reconcile from 2110"
    negative: "uncommitted evidence, serial-only evidence, xdist drift, reporter warning/failure, count-only receipts, or contradicted prose is accepted"
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "another residual is mechanically unreachable or represents questionable exception policy"
  - "a real defect requires source outside update_ops.py"
  - "either full gate fails or the target record differs"
advances: []
---

# P111 — Complete Docker update operation coverage

Assigned branch/worktree:
`feat/topos-P111-update-ops-coverage` at
`/workspaces/vbpub/.worktrees/feat/topos-P111-update-ops-coverage`.

## Literal residual

```text
lines:
58 63 94 103 130 131 132 133 134 135 137 138 139 140 141 142
143 144 145 146 174 178 180 182 293 294 298 339

pairs:
57->58 62->63 93->94 102->103 131->132 131->142 173->174
177->178 179->180 181->182 297->298 334->338 336->338 338->339
```

Completion also requires the complete file record empty. Lines 140 and 293
currently catch `BaseException`; narrow both to `Exception`, cover ordinary
fail-closed behavior, and prove `KeyboardInterrupt` propagates.

## Context to read first

1. `/workspaces/vbpub/AGENTS.md`.
2. `/workspaces/vbpub/nyxloom/reference/STANDARD.md`, gate contract and
   validation principles.
3. `/workspaces/vbpub/nyxloom/nyxloom-trove/LESSONS.md`, PL3/PL4 through P110.
4. P110 final report/review only for the 2,110 baseline and immutable-receipt
   discipline.
5. `test_p72_kill_update.py` and `update_ops.py`.

## Work

1. Confirm the exact worktree/revision and current literal residual.
2. Cover CPU finite/upper-bound, memory empty/upper-bound, argv empty/no-option/
   invalid typed option, unreadable usage, and CPU/missing-current render paths
   with exact behavior.
3. Cover default current-memory resolution by container name, direct cgroup
   key reads, read/parse failures, and resolution failures. Patch collector,
   resolver, config, and `Path.read_text` seams; assert complete call/path data.
4. Narrow both `BaseException` catches to `Exception`. Prove ordinary
   resolution/reader errors fail closed and `KeyboardInterrupt` propagates.
5. No weak/partial/hollow/duplicate tests, pragmas, omissions, gate changes,
   host venvs, copied worktrees, image rebuilds, or guessed runners.
6. Commit the tested implementation before authoritative gate execution. Run
   two full declared gates from its exact clean hash with in-container
   whole-file record hashes; record literal intersections, complete sets, case
   arithmetic, commands/exits, `git diff --check`, LOG/REPORT/SELFREVIEW, and
   commit the receipts afterward.

## Scope / forbid

Only the frontmatter touch paths may change. Execution/catalog/squeeze code,
gate/tooling, and dependencies are out of scope and forbidden.

## BLOCKED

On a mechanical trigger, write `BLOCKED: <trigger and exact evidence>` to the
P111 log and stop. Never report partial completion.
