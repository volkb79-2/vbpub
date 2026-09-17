---
schema_version: 1
id: topos-P108-host-network-provider-coverage
project: topos
title: "Complete host network provider coverage"
tier: sonnet5-high
input_revision: "5f8b2d3e"
source: {kind: product-goal, ref: "nyxloom-trove/reports/P96-COVERAGE-GAPS.md"}
stack: none
depends_on: [topos-P107-network-provider-coverage]
session: "fresh"
scope:
  touch:
    - "src/topos/providers/net_host.py"
    - "tests/**"
    - "nyxloom-trove/handoffs/topos-P108-host-network-provider-coverage.md"
    - "nyxloom-trove/reports/P108-*.md"
  forbid:
    - "src/topos/providers/net_netns.py"
    - "src/topos/providers/net_bpf.py"
    - "nyxloom-trove/nyxloom.toml"
    - "tools/coverage_gate.py"
    - "pyproject.toml"
oracles:
  - id: O1
    observable: "src/topos/providers/net_host.py has empty missing_lines and missing_branches in branch-aware JSON from the complete xdist gate, closing the literal 26-line/10-pair residual"
    negative: "completion is claimed from aggregate, serial, focused, rounded, or partial coverage without the whole-file record empty"
    gate: topos-suite
  - id: O2
    observable: "tests assert exact parser dictionaries, complete provider samples/status, auxiliary-file degradation, and tc runner failure"
    negative: "tests mock the function under test, contain pass or assertion-free bodies, assert only substrings/selected fields/non-None/ranges/calls, or rely on host proc/tc"
    gate: topos-suite
  - id: O3
    observable: "two complete xdist gates close the literal and whole-file sets with identical normalized target records and exact arithmetic from 2062"
    negative: "serial-only evidence, xdist drift, reporter failure, count-only receipts, or contradicted prose is accepted"
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "a residual branch is mechanically unreachable and requires a semantic product decision"
  - "a real defect requires source outside net_host.py"
  - "either full gate fails or the target records differ"
advances: []
---

# P108 — Complete host network provider coverage

Assigned branch/worktree:
`feat/topos-P108-host-network-provider-coverage` at
`/workspaces/vbpub/.worktrees/feat/topos-P108-host-network-provider-coverage`.

## Literal residual

```text
lines:
31 34 35 56 60 61 77 85 86 97 102 103 104 109 144 147 148
200 201 207 208 214 215 223 224 225

pairs:
30->31 55->56 76->77 96->97 108->109 143->144 146->147
199->200 206->207 213->214
```

Completion also requires the complete file record empty.

## Context to read first

1. `/workspaces/vbpub/AGENTS.md`.
2. `/workspaces/vbpub/nyxloom/reference/STANDARD.md`, gate contract and
   validation principles.
3. `/workspaces/vbpub/nyxloom/nyxloom-trove/LESSONS.md`, PL3/PL4 through P107.
4. P107 final report/review only for the 2,062 baseline and provider-test
   quality.
5. `test_network_providers.py`, fixtures, and `net_host.py`.

## Work

1. Confirm the exact worktree/revision and literal residual.
2. Cover malformed/short/non-numeric net-dev rows, short/non-hex softnet rows,
   mismatched/non-numeric SNMP pairs, blank/malformed/pre-header qdisc lines,
   a collection without root, missing net/dev, missing auxiliary proc files,
   and a deterministic tc runner exception.
3. Assert complete parser outputs, `NetSample`, and status dictionaries. Patch
   time and command seams; use `tmp_path`, never host `/proc` or `tc`.
4. No weak/partial/hollow/duplicate tests, pragmas, omissions, gate changes,
   host venvs, copied worktrees, image rebuilds, or guessed runners.
5. Run two full declared gates with in-container whole-file record hash; record
   literal intersections, full sets, case arithmetic, commands/exits,
   `git diff --check`, LOG/REPORT/SELFREVIEW, and commit.

## Scope / forbid

Only the frontmatter touch paths may change. The other network providers,
gate/tooling, and dependencies are out of scope and forbidden.

## BLOCKED

On a mechanical trigger, write `BLOCKED: <trigger and exact evidence>` to the
P108 log and stop. Never report partial completion.
