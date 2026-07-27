---
schema_version: 1
id: topos-P166-cli-snapshot-dispatch
project: topos
title: "Specify snapshot inspect CLI result handling"
tier: luna-low
input_revision: "672269d7"
depends_on: []
session: resume cli
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/tests/test_cli_snapshot_dispatch.py", "topos/nyxloom-trove/handoffs/topos-P166-cli-snapshot-dispatch.md"]
  forbid: ["topos/src/topos/cli.py", "topos/src/topos/snapshot.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py"]
oracles:
  - id: O1
    observable: "`snapshot inspect FILE` forwards the parsed Path to inspection, emits its summary on stdout, and returns zero."
    negative: "The inspection target is string-mangled, successful inspection is misreported, or its summary is lost."
    gate: topos-suite
  - id: O2
    observable: "Expected inspection failures emit their original actionable message on stderr and return one rather than escaping or succeeding."
    negative: "A corrupt/unreadable bundle looks successful or crashes the CLI without an operator-visible diagnostic."
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "an oracle needs a real archive read, product-code edit, or a file outside the two allowed paths"
---

# P166 — Specify snapshot inspect CLI result handling

## Context to read first

1. `topos/src/topos/cli.py`: `_main_snapshot` only (roughly lines 747–757).
2. `topos/tests/test_snapshot_bundle.py`: inspect-bundle contract examples;
   do not read or create a real archive in this package.
3. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

1. Add one focused test module which calls `_main_snapshot` and monkeypatches
   `topos.cli.inspect_bundle`; no archive filesystem operation may run.
2. Assert the success Path/summary/zero contract and parameterize the caught
   `OSError`, `RuntimeError`, and `ValueError` failure contract (stderr/one).
3. Test only the named CLI boundary; do not duplicate bundle-content tests.
4. Run this focused module in `tester-unified`, self-review all oracles and
   scope, commit only the allowed files, and leave the branch unmerged.

## BLOCKED rule

If an oracle needs a real archive read, product-code edit, or a forbidden
file, write `BLOCKED: <named oracle and reason>` and stop.
