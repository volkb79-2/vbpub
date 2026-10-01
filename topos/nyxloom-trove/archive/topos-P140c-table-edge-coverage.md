---
schema_version: 1
id: topos-P140c-table-edge-coverage
project: topos
title: "Cover table public edge rendering"
tier: luna-low
input_revision: "9ad342e2"
depends_on: [topos-P140b-table-value-coverage]
session: fresh
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/tests/test_ui_table.py", "topos/nyxloom-trove/handoffs/topos-P140c-table-edge-coverage.md"]
  forbid: ["topos/src/topos/ui/table.py", "topos/src/topos/ui/sparkline.py", "topos/src/topos/record/ring.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py", "topos/pyproject.toml"]
oracles:
  - id: O1
    observable: "A configured profile whose requested list has no supported columns remains observable as an empty-column public snapshot, rather than selecting an unintended column."
    negative: "Unsupported configured columns are silently substituted or cause the public table API to fail."
    gate: topos-suite
  - id: O2
    observable: "Public table formatting renders an unknown present value literally, codes a missing/unknown governance or DAMON metric as `-`/its literal fallback, and exposes ignored profile names in the rendered table title."
    negative: "An unknown value is fabricated, a missing code crashes, or an ignored profile field is invisible to the operator."
    gate: topos-suite
  - id: O3
    observable: "A CPU history ring with an existing all-missing series visibly degrades to the dim `-` CPU trend through public `format_metric_value`."
    negative: "An existing but unusable history series renders a misleading trend or crashes."
    gate: topos-suite
gates: [topos-suite]
escalate_if: ["the edge behavior cannot be observed through named public APIs", "the contract requires a forbidden source file"]
---

# P140c — Cover table public edge rendering

## Context to read first

1. `topos/src/topos/ui/table.py`: `_row_cells`, `_format_metric`,
   `_profile_title_suffix`, `_metric_code`, and `format_metric_value` only.
2. `topos/src/topos/record/ring.py`: `HistoryRing.append_frame`, `last`, and
   `has_series`.
3. `topos/tests/test_ui_table.py`: P140a/P140b fixtures and public API style.
4. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

1. Extend `test_ui_table.py` with public `snapshot_container_table` behavior
   for a custom profile listing only unsupported columns and a real container
   frame: the profile has no columns, yet the snapshot exposes its entity key
   and empty cell tuple without an invented supported column.
2. Through public `format_metric_value` and `render_container_table`, assert
   the observable behavior of a present unknown metric/spec, missing and
   unrecognized governance/DAMON codes, and a title suffix naming ignored
   configured columns.
3. Build a normal `HistoryRing` by appending a frame with an all-missing CPU
   metric, then call public `format_metric_value("cpu_trend", ..., ring=ring)`
   and assert its visible `-` fallback. Do not call underscore helpers, mock
   internals, or modify source.

## Oracle

Run the tester-unified `topos-suite` gate. The named public edge cases must
cover their table branches without coverage suppression.

## Scope / forbid

Touch only the named test and handoff. Do not edit table/ring/sparkline source,
the gate, dependencies, or configuration.

## BLOCKED rule

If a named result cannot be observed through the public API without changing a
forbidden file, STOP. Write `BLOCKED: <specific reason>` to this handoff's LOG,
commit that log-only change, and exit.
