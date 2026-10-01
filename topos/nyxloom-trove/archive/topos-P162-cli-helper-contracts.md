---
schema_version: 1
id: topos-P162-cli-helper-contracts
project: topos
title: "Specify BPF parser and filter conversion contracts"
tier: luna-low
input_revision: "c4096b29"
depends_on: []
session: fresh
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/tests/test_cli_helpers.py", "topos/nyxloom-trove/handoffs/topos-P162-cli-helper-contracts.md"]
  forbid: ["topos/src/topos/cli.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py"]
oracles:
  - id: O1
    observable: "The BPF command requires its explicit safe `gate` subcommand and preserves explicit proc/pin roots and JSON mode."
    negative: "A BPF invocation silently selects an operation or drops an operator-supplied probe root."
    gate: topos-suite
  - id: O2
    observable: "Parsed entity, slice, and container filters become the Collector's tuple-or-None contract while the selected metrics mode is preserved."
    negative: "Repeatable CLI filters reach collection with the wrong shape, silently disappear, or confuse absence with an empty filter."
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "an oracle needs a daemon connection, Collector run, or product-code change"
  - "the work requires a file outside the two allowed paths"
---

# P162 — Specify CLI validation, filter conversion, and daemon guidance

## Context to read first

1. `topos/src/topos/cli.py`: `parse_bpf_args` and `_filter_kwargs` only
   (roughly lines 260–333).
2. `topos/tests/test_p60_fieldlist.py` and `topos/tests/test_attach_cli.py`:
   existing coverage that this package must not duplicate.
3. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

1. Add one focused test module. Call only `parse_bpf_args` and `_filter_kwargs`;
   do not run a Collector, BPF probe, or daemon.
2. Test that BPF needs its subcommand and that explicit roots/JSON are retained.
3. Test all-present and all-absent filter conversion and the output types.
4. Run the focused test module in `tester-unified`, self-review all oracles and
   scope, commit only the allowed files, and leave the branch unmerged.

## BLOCKED rule

If any oracle needs a BPF probe, live daemon, Collector run, product-code edit, or a
forbidden file, write `BLOCKED: <named oracle and reason>` and stop.
