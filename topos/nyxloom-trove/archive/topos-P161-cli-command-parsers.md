---
schema_version: 1
id: topos-P161-cli-command-parsers
project: topos
title: "Specify command-parser safety contracts"
tier: luna-low
input_revision: "c4096b29"
depends_on: []
session: fresh
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/tests/test_cli_command_parsers.py", "topos/nyxloom-trove/handoffs/topos-P161-cli-command-parsers.md"]
  forbid: ["topos/src/topos/cli.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py"]
oracles:
  - id: O1
    observable: "The DAMON command parser requires an explicit stop/paddr subcommand, keeps destructive stop opt-in, and accepts the paddr start confirmation/config inputs without performing a system action."
    negative: "An omitted DAMON action is accepted, a destructive all-owned-sessions stop becomes implicit, or paddr confirmation/config is silently discarded."
    gate: topos-suite
  - id: O2
    observable: "The snapshot parser requires an inspect action and preserves the requested bundle path as a Path."
    negative: "An ambiguous/no-op snapshot command parses successfully or an incident-bundle path loses its type/value."
    gate: topos-suite
  - id: O3
    observable: "The MCP parser requires serve, retains the default daemon socket, accepts valid redaction ceilings, and rejects an invalid ceiling at argument validation."
    negative: "MCP starts without an explicit service action or accepts an unrecognized sensitivity ceiling."
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "an oracle cannot be exercised through parser functions without calling a product action"
  - "the work requires a file outside the two allowed paths"
---

# P161 — Specify command-parser safety contracts

## Context to read first

1. `topos/src/topos/cli.py`: `parse_damon_args`, `parse_snapshot_args`, and
   `parse_mcp_args` only (roughly lines 92–224).
2. `topos/tests/test_daemon_http_gateway.py`: parser-test style only.
3. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

1. Add one focused parser test module. Use parser functions directly; never
   call a DAMON/MCP product action or touch host state.
2. Test DAMON stop defaults and explicit opt-in, plus paddr start with a
   confirmation, config, and fixture-only flag. Assert parsed public fields.
3. Test snapshot inspect’s required action/path and MCP serve’s default socket,
   valid redaction ceiling, and invalid ceiling rejection (`SystemExit`).
4. Run the focused test module in `tester-unified`, self-review each oracle and
   scope, commit only the two allowed files, and leave the branch unmerged.

## BLOCKED rule

If an oracle needs a real service, a product action, product-code edits, or a
forbidden file, write `BLOCKED: <named oracle and reason>` and stop.
