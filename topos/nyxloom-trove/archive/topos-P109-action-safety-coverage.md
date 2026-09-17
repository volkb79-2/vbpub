---
schema_version: 1
id: topos-P109-action-safety-coverage
project: topos
title: "Complete kill and owner-safety coverage"
tier: sonnet5-high
input_revision: "ef0b1d79"
source: {kind: product-goal, ref: "nyxloom-trove/reports/P96-COVERAGE-GAPS.md"}
stack: none
depends_on: [topos-P108-host-network-provider-coverage]
session: "fresh"
scope:
  touch:
    - "src/topos/actions/kill_ops.py"
    - "src/topos/actions/owner_safety.py"
    - "tests/**"
    - "nyxloom-trove/handoffs/topos-P109-action-safety-coverage.md"
    - "nyxloom-trove/reports/P109-*.md"
  forbid:
    - "src/topos/actions/execute.py"
    - "src/topos/cli.py"
    - "nyxloom-trove/nyxloom.toml"
    - "tools/coverage_gate.py"
    - "pyproject.toml"
oracles:
  - id: O1
    observable: "kill_ops.py and owner_safety.py both have empty missing_lines and missing_branches in branch-aware JSON from the complete xdist gate, closing the literal 15-line/15-pair residual"
    negative: "completion is claimed from aggregate, serial, focused, rounded, or partial coverage without both whole-file records empty"
    gate: topos-suite
  - id: O2
    observable: "tests assert exact validation errors, argv/refusal/message/identity values, sanitized metadata, and inspect delegation while never invoking a host mutation"
    negative: "tests mock a target function, assert only substrings/selected fields/non-None/ranges/calls, contain pass or assertion-free bodies, or consult host Docker state"
    gate: topos-suite
  - id: O3
    observable: "two complete xdist gates close the literal and whole-file sets with identical normalized target records and exact arithmetic from 2070"
    negative: "serial-only evidence, xdist drift, reporter failure, count-only receipts, or contradicted prose is accepted"
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "a residual branch is mechanically unreachable and requires a semantic product decision"
  - "a real defect requires source outside kill_ops.py or owner_safety.py"
  - "either full gate fails or either target record differs"
advances: []
---

# P109 — Complete kill and owner-safety coverage

Assigned branch/worktree:
`feat/topos-P109-action-safety-coverage` at
`/workspaces/vbpub/.worktrees/feat/topos-P109-action-safety-coverage`.

## Literal residual

```text
kill_ops.py lines:
48 58 98 103 135 235

kill_ops.py pairs:
47->48 54->58 97->98 101->103 133->135 234->235

owner_safety.py lines:
117 151 153 156 158 241 254 353 355

owner_safety.py pairs:
116->117 150->151 152->153 155->156 157->158 161->160
238->241 249->254 270->272
```

Completion also requires both complete file records empty.

## Context to read first

1. `/workspaces/vbpub/AGENTS.md`.
2. `/workspaces/vbpub/nyxloom/reference/STANDARD.md`, gate contract and
   validation principles.
3. `/workspaces/vbpub/nyxloom/nyxloom-trove/LESSONS.md`, PL3/PL4 through P108.
4. P108 final report/review only for the 2,070 baseline and receipt quality.
5. `test_p72_kill_update.py`, `test_p87_owner_safety.py`, `kill_ops.py`, and
   `owner_safety.py`.

## Work

1. Confirm the exact worktree/revision and literal residual.
2. Cover non-string/empty signals and targets, unknown SIG-prefixed input,
   invalid action/string kinds, and the KILL warning render.
3. Cover non-string display metadata; absent/malformed/mixed inspect
   `Config.Labels`; Compose without safe detail; the defensive unknown-owner
   message; nameless protected-identity comparison; and default inspect
   delegation.
4. Assert complete values and exact error/refusal/message text. Patch the
   imported Docker-inspect seam; never invoke Docker, a signal, or another host
   mutation.
5. No weak/partial/hollow/duplicate tests, pragmas, omissions, gate changes,
   host venvs, copied worktrees, image rebuilds, or guessed runners.
6. Run two full declared gates with in-container whole-file record hashes;
   record literal intersections, complete sets, case arithmetic, commands/exits,
   `git diff --check`, LOG/REPORT/SELFREVIEW, and commit.

## Scope / forbid

Only the frontmatter touch paths may change. Execution/CLI code, gate/tooling,
and dependencies are out of scope and forbidden.

## BLOCKED

On a mechanical trigger, write `BLOCKED: <trigger and exact evidence>` to the
P109 log and stop. Never report partial completion.
