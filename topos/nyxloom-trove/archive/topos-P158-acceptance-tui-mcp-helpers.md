---
schema_version: 1
id: topos-P158-acceptance-tui-mcp-helpers
project: topos
title: "Exercise TUI and MCP smoke helper failure semantics"
tier: luna-low
input_revision: "0d4ffc58"
depends_on: []
session: resume acceptance
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/tests/test_acceptance.py", "topos/nyxloom-trove/handoffs/topos-P158-acceptance-tui-mcp-helpers.md"]
  forbid: ["topos/src/topos/acceptance.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py"]
oracles:
  - id: O1
    observable: "Malformed UI smoke fields remain safely parseable, and text output honestly identifies a missing smoke line while retaining a bounded stderr excerpt."
    negative: "Bad child output raises, invents a frame count, hides stderr, or claims that the UI smoke line was found."
    gate: topos-suite
  - id: O2
    observable: "MCP helper results distinguish a non-JSON text payload, a non-dict payload, and a malformed typed-error field from a successful typed result."
    negative: "A malformed MCP payload is silently treated as success or raises outside the smoke harness."
    gate: topos-suite
  - id: O3
    observable: "Response-byte accounting sums text blocks as UTF-8 bytes and records zero for absent/untextual content."
    negative: "The smoke evidence undercounts multibyte tool output or crashes on an ordinary content shape."
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "proving an oracle needs a change outside the two allowed files"
  - "a helper's externally observable contract cannot be tested without mocking the entire smoke run"
---

# P158 — Exercise TUI and MCP smoke helper failure semantics

## Context to read first

1. `topos/src/topos/acceptance.py`: only `_parse_ui_smoke_line`,
   `format_tui_smoke_text`, `_parse_tool_content`, `_tool_call_failure`, and
   `_update_byte_size` (roughly lines 644–901).
2. `topos/tests/test_acceptance.py`: existing direct helper and result-format
   tests, including its fixture/import style.
3. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

1. Add direct, small behavioural tests for the three named oracle groups.
   Use minimal result-shaped local objects where the helper's actual public
   boundary is a third-party MCP response; do not import or start an MCP
   server and do not mock a whole smoke harness.
2. For UI parsing, use a real malformed `frames=` value together with valid
   view/profile fields; for formatting, use a real `TuiSmokeResult` whose
   smoke line is absent and whose stderr has more than five lines.
3. For MCP helpers, demonstrate the raw-text fallback for invalid JSON, the
   non-dict and malformed-error failure reasons, and successful typed payload
   behaviour.  Demonstrate UTF-8 byte accounting (including multibyte text)
   and an empty/absent content shape.
4. Run the focused `topos/tests/test_acceptance.py -q` command in
   `tester-unified`, self-review every oracle and scope, commit only the two
   allowed files, and leave the branch unmerged.

## BLOCKED rule

If a named helper contract requires product-code changes, a real MCP daemon,
or a forbidden file, write `BLOCKED: <named oracle and reason>` and stop.
