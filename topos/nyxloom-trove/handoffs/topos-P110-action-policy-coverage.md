---
schema_version: 1
id: topos-P110-action-policy-coverage
project: topos
title: "Complete action catalog and governance coverage"
tier: sonnet5-high
input_revision: "6aa902ac"
source: {kind: product-goal, ref: "nyxloom-trove/reports/P96-COVERAGE-GAPS.md"}
stack: none
depends_on: [topos-P109-action-safety-coverage]
session: "fresh"
scope:
  touch:
    - "src/topos/actions/catalog.py"
    - "src/topos/actions/governance.py"
    - "tests/**"
    - "nyxloom-trove/handoffs/topos-P110-action-policy-coverage.md"
    - "nyxloom-trove/reports/P110-*.md"
  forbid:
    - "src/topos/actions/execute.py"
    - "src/topos/actions/update_ops.py"
    - "src/topos/actions/squeeze.py"
    - "nyxloom-trove/nyxloom.toml"
    - "tools/coverage_gate.py"
    - "pyproject.toml"
oracles:
  - id: O1
    observable: "catalog.py and governance.py both have empty missing_lines and missing_branches in branch-aware JSON from the complete xdist gate, closing the literal 24-line/15-pair residual or explicitly removing proven-dead entries"
    negative: "completion is claimed from aggregate, serial, focused, rounded, warning-bearing, or partial coverage without both whole-file records empty"
    gate: topos-suite
  - id: O2
    observable: "tests assert exact validation errors, argv, persistence, current-value results, subprocess arguments, and exception boundaries without invoking systemctl or another host mutation"
    negative: "tests mock a target function, assert only substrings/selected fields/non-None/ranges/calls, contain pass or assertion-free bodies, or consult host systemd state"
    gate: topos-suite
  - id: O3
    observable: "the duplicate set-property empty guard is removed under an invariant proof, and preview reader failures catch Exception but allow KeyboardInterrupt/SystemExit to propagate"
    negative: "dead code is retained through pathological stateful inputs, BaseException swallowing is codified as desired behavior, or a deletion is accepted from changed-line 0/0 alone"
    gate: topos-suite
  - id: O4
    observable: "two complete xdist gates close the literal/whole-file sets with identical normalized target records and exact arithmetic from 2088"
    negative: "serial-only evidence, xdist drift, reporter warning/failure, count-only receipts, or contradicted prose is accepted"
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "another residual is mechanically unreachable or represents questionable exception policy"
  - "a real defect requires source outside catalog.py or governance.py"
  - "either full gate fails or either target record differs"
advances: []
---

# P110 — Complete action catalog and governance coverage

Assigned branch/worktree:
`feat/topos-P110-action-policy-coverage` at
`/workspaces/vbpub/.worktrees/feat/topos-P110-action-policy-coverage`.

## Literal residual

```text
catalog.py lines:
76 77 79 83 183 189 191 193 198 200 205 207

catalog.py pairs:
75->76 78->79 175->183 188->189 190->191 192->193
197->198 199->200 204->205 206->207

governance.py lines:
102 103 189 190 195 196 197 199 236 321 332 333

governance.py pairs:
192->195 196->197 196->199 235->236 318->321
```

Completion also requires both complete file records empty. Catalog lines
188–189 are a duplicate empty-target guard: line 156 rejects every empty target
before the kind-specific block. Remove that executable guard, preserve source
line stability if practical, and supply the deletion oracle. Governance
lines 332–333 currently catch `BaseException`; narrow the catch to `Exception`,
cover an ordinary reader failure, and prove `KeyboardInterrupt` propagates.

## Context to read first

1. `/workspaces/vbpub/AGENTS.md`.
2. `/workspaces/vbpub/nyxloom/reference/STANDARD.md`, gate contract and
   validation principles.
3. `/workspaces/vbpub/nyxloom/nyxloom-trove/LESSONS.md`, PL3/PL4 through P109.
4. P109 final report/review only for the 2,088 baseline and receipt quality.
5. `test_actions.py`, `catalog.py`, and `governance.py`.

## Work

1. Confirm the exact worktree/revision and current literal residual.
2. Cover empty/composite catalog set-property targets; the defensive
   execution-allowlist fallthrough; systemd set-property/kill and Docker
   kill/update whitespace/identifier failures.
3. Cover integer-conversion failure after numeric recognition, systemctl show
   `OSError`/timeout/nonzero/null/success results, empty argv unit, explicit
   preview persistence, and ordinary reader exceptions.
4. Remove the duplicate kind-specific empty guard with its invariant proof.
   Narrow preview reader handling from `BaseException` to `Exception`; assert a
   complete fallback plan for `RuntimeError` and propagation for
   `KeyboardInterrupt`.
5. Assert complete values and exact exception text. Patch only subprocess and
   current-reader seams; never invoke systemctl or another host mutation.
6. No weak/partial/hollow/duplicate tests, pragmas, omissions, gate changes,
   host venvs, copied worktrees, image rebuilds, or guessed runners.
7. Run two full declared gates with in-container whole-file record hashes;
   record deletion proof, literal intersections, complete sets, case
   arithmetic, commands/exits, `git diff --check`, LOG/REPORT/SELFREVIEW, and
   commit.

## Scope / forbid

Only the frontmatter touch paths may change. Execution/update/squeeze code,
gate/tooling, and dependencies are out of scope and forbidden.

## BLOCKED

On a mechanical trigger, write `BLOCKED: <trigger and exact evidence>` to the
P110 log and stop. Never report partial completion.
