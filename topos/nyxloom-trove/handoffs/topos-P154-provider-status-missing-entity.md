---
schema_version: 1
id: topos-P154-provider-status-missing-entity
project: topos
title: "Cover absent-entity snapshot provider status"
tier: luna-low
input_revision: "71c9e23c"
depends_on: [topos-P153-ui-smoke-success]
session: resume topos-ui-replay-coverage
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/tests/test_ui_app.py", "topos/nyxloom-trove/handoffs/topos-P154-provider-status-missing-entity.md"]
  forbid: ["topos/src/topos/ui/app.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py"]
oracles:
  - id: O1
    observable: "Snapshot provider-status assembly returns an empty mapping when the requested entity is absent from the supplied frame."
    negative: "A stale/deleted selection dereferences a missing entity frame or fabricates provider fields."
    gate: topos-suite
  - id: O2
    observable: "The existing fixture-frame provider status for a real entity remains a mapping of the entity network/DAMON values."
    negative: "The missing-entity guard accidentally erases ordinary snapshot metadata."
    gate: topos-suite
gates: [topos-suite]
escalate_if: ["the helper contract is not observable from the existing pure helper API", "a real fixture entity cannot supply both provider fields"]
---

# P154 — Cover absent-entity snapshot provider status

## Context to read first

1. `topos/src/topos/ui/app.py`: `_providers_status` only.
2. `topos/tests/test_ui_app.py`: existing fixture-frame helpers and snapshot
   tests only.
3. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

1. Add a compact pure-helper regression that calls `_providers_status` with a
   clearly absent entity key and asserts `{}`.
2. In the same test, assert a known fixture entity retains its concrete
   `network` and `damon` values (using values from that fixture, not mocks).
3. This package intentionally tests a pure helper directly; do not construct a
   fake frame, change UI source, or duplicate asynchronous snapshot workflow
   tests.
4. Run `topos/tests/test_ui_app.py -q` in `tester-unified`, self-review scope
   and both oracles, and leave work unmerged.

## BLOCKED rule

If the fixture has no stable entity/provider values to assert, write
`BLOCKED: <specific missing fixture fact>` and stop.
