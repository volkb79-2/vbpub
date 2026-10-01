---
schema_version: 1
id: topos-P160-mcp-startup-teardown
project: topos
title: "Contain MCP daemon startup and teardown failures"
tier: luna-low
input_revision: "d8ab03e3"
depends_on: []
session: fresh
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/tests/test_acceptance.py", "topos/nyxloom-trove/handoffs/topos-P160-mcp-startup-teardown.md"]
  forbid: ["topos/src/topos/acceptance.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py"]
oracles:
  - id: O1
    observable: "An OS-level daemon spawn failure becomes a non-OK MCP smoke result with one failed `daemon_start` check and preserved error evidence, rather than escaping as a traceback."
    negative: "A missing executable/permission failure crashes the JSON-capable smoke harness or reports a successful daemon start."
    gate: topos-suite
  - id: O2
    observable: "When graceful daemon termination times out, teardown invokes kill and waits for the killed process."
    negative: "A hung daemon is left alive after the smoke harness exits."
    gate: topos-suite
  - id: O3
    observable: "A failure while waiting after kill is contained during best-effort teardown."
    negative: "Cleanup-only failure overwrites the smoke result or leaks an exception."
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "the named failure paths cannot be reached through `run_mcp_smoke` or `_terminate_process` without product-code edits"
  - "a test requires a file outside the two allowed paths"
---

# P160 — Contain MCP daemon startup and teardown failures

## Context to read first

1. `topos/src/topos/acceptance.py`: `_terminate_process`, `_make_mcp_result`,
   and `run_mcp_smoke` daemon-start/finally blocks only (roughly lines
   904–948 and 1009–1103).
2. `topos/tests/test_acceptance.py`: existing `_FakeProc` and MCP smoke tests
   only.
3. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

1. Add a `run_mcp_smoke` test that injects `OSError` only at the daemon
   `subprocess.Popen` boundary. Assert the typed failed `daemon_start` check,
   its error details, non-OK result, and no escaping exception.
2. Add direct minimal fake-process tests for the graceful-wait timeout → kill
   fallback and for a post-kill wait failure being swallowed. Assert observable
   calls/state, not private implementation incidental values.
3. Use the existing fake-process style; do not start a real daemon, mock the
   entire smoke function, or change product code.
4. Run `topos/tests/test_acceptance.py -q` in `tester-unified`, self-review
   all three oracles and scope, commit only the two allowed files, and leave
   the branch unmerged.

## BLOCKED rule

If any oracle requires product-code edits, a real daemon, or a forbidden file,
write `BLOCKED: <named oracle and reason>` and stop.
