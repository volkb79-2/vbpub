---
schema_version: 1
id: topos-P141-table-byte-invariant
project: topos
title: "Remove unreachable table byte-loop exit"
tier: controller
input_revision: "fe23f764"
depends_on: [topos-P140c-table-edge-coverage]
session: fresh
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/src/topos/ui/table.py", "topos/tests/test_ui_table.py", "topos/nyxloom-trove/handoffs/topos-P141-table-byte-invariant.md"]
  forbid: ["topos/src/topos/ui/drill.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py", "topos/pyproject.toml"]
oracles:
  - id: O1
    observable: "A public table byte metric at 1024^5 renders as 1024.0TiB, retaining the renderer's terminal-unit cap."
    negative: "The cap loses its TiB unit, overflows the unit list, or changes formatting."
    gate: topos-suite
  - id: O2
    observable: "The byte-unit loop reaches the terminal TiB unit by normal completion, with no unreachable final-unit break edge."
    negative: "The coverage branch remains structurally impossible."
    gate: topos-suite
gates: [topos-suite]
escalate_if: ["the TiB behavior differs from the named public regression"]
---

# P141 — Remove unreachable table byte-loop exit

## Context to read first

1. `topos/src/topos/ui/table.py`: `_fmt_bytes` only.
2. `topos/tests/test_ui_table.py`: public `format_metric_value` test style.

## Work

Replace the final-unit `break` condition in `_fmt_bytes` with normal loop
completion, preserving byte-format output. Add a public `format_metric_value`
regression that proves a `1024 ** 5` byte metric renders as `1024.0TiB`.

## BLOCKED rule

If the visible TiB formatting changes, STOP. Write `BLOCKED: <specific reason>`
to this handoff's LOG, commit that log-only change, and exit.
