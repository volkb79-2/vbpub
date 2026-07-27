---
schema_version: 1
id: topos-P177-acceptance-cli-dispatch
project: topos
title: "Cover acceptance harness CLI dispatch and result contract"
tier: luna-low
input_revision: "3abb76fa"
depends_on: []
session: "fresh"
source: {kind: product-goal, ref: "nyxloom-trove/3-roadmap.md"}
scope:
  touch: ["tests/test_acceptance_cli_dispatch.py", "nyxloom-trove/handoffs/topos-P177-acceptance-cli-dispatch.md"]
  forbid: ["src/topos/acceptance.py", "nyxloom-trove/nyxloom.toml"]
oracles:
  - id: O1
    observable: "Each acceptance subcommand forwards public parsed options to its matching runner and selects its documented JSON/text formatter."
    negative: "A route/option/formatter regression fails with a distinct sentinel observable."
    gate: topos-suite
  - id: O2
    observable: "Invalid steady/TUI/MCP numeric options return documented usage exit 2; a non-ok result returns 1 and an ok result returns 0."
    negative: "A bad option reaching a runner or a failed result returning success fails."
    gate: topos-suite
gates: [topos-suite]
escalate_if: ["a named contract cannot be met as specified", "scope requires a forbidden file"]
---

# P177 — acceptance CLI dispatch

## Context to read first

1. `src/topos/acceptance.py`, `acceptance_main` lines 1570–1644 only.
2. `tests/test_acceptance.py`: existing fixture nomenclature; this package tests the direct CLI boundary instead of starting live collector/daemon processes.
3. `tests/test_cli_action_execute.py`: injected direct-boundary style and exact output/exit assertions.
4. `nyxloom-trove/nyxloom.toml`, `[gates.topos-suite]`.

## Work

1. Add only `tests/test_acceptance_cli_dispatch.py`. Test `acceptance_main` directly and replace the runner/formatter boundaries with distinct local fakes; do not start subprocesses, daemon, MCP, collector, or use timing assertions.
2. Cover smoke, steady, tui-smoke, and mcp-smoke routes with exact forwarded public args; JSON/pretty JSON and text formatter selection; all documented validation rejections; and final ok/non-ok exit mapping.
3. Assert exact public stdout/stderr plus forwarded option dictionaries. Never assert merely a mock call count.
4. Self-review, run focused tester-unified tests, and commit only allowed files.

## Oracles

- O1: Distinct fake runner/formatter sentinels prove route, public options, and output selection. A wrong route, lost option, or formatter bypass is red.
- O2: Every invalid numeric option returns 2 before its runner; fake `ok=False` results return 1 and `ok=True` return 0. A validation bypass or failed-success mapping is red.
- Gate: run focus in tester-unified with `PYTHONPATH=topos/src:topos`; final proof is declared `topos-suite`, never cockpit Python.

## Test constraints

- No sleep, wall-clock verdict, real network/filesystem service, or global-state leak.
- Patch the module namespace owning the boundary and assert behavioural output/exit contracts.
- No coverage exclusions or `no cover` text.

## Scope / forbid

Only the named new test and this handoff may change. No product/gate/existing-test edits. Work in assigned worktree and branch.

## BLOCKED rule

If a named contract cannot be proven with these seams, or a forbidden file is required, STOP; write `BLOCKED: <reason>` to the LOG, commit it, and exit. Do not improvise.
