---
schema_version: 1
id: topos-P157-acceptance-smoke-failures
project: topos
title: "Contain acceptance smoke stage failures"
tier: luna-low
input_revision: "7d3969f7"
depends_on: []
session: fresh
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/tests/test_acceptance.py", "topos/nyxloom-trove/handoffs/topos-P157-acceptance-smoke-failures.md"]
  forbid: ["topos/src/topos/acceptance.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py"]
oracles:
  - id: O1
    observable: "A collector exception produces a non-OK smoke result with a failed collect check, skipped serialization/source checks, and no frame summary rather than escaping the harness."
    negative: "One collection failure crashes the release-smoke command or reports downstream checks as successful."
    gate: topos-suite
  - id: O2
    observable: "A serialization round-trip exception is isolated to the serialize check while collection/source evidence remains visible."
    negative: "A frame serialization defect is hidden, or it erases successful collection evidence."
    gate: topos-suite
  - id: O3
    observable: "A replay-load exception for an existing replay path is reported as a failed replay check while the rest of smoke evidence remains intact."
    negative: "A corrupt/unreadable replay escapes the harness or is misreported as a nonexistent path."
    gate: topos-suite
gates: [topos-suite]
escalate_if: ["injecting one stage failure needs product-code edits or bypasses run_smoke", "existing fixture replay cannot exercise the existing-path replay branch"]
---

# P157 — Contain acceptance smoke stage failures

## Context to read first

1. `topos/src/topos/acceptance.py`: `run_smoke` collection, serialization,
   source-label, and replay blocks only (roughly lines 370–510).
2. `topos/tests/test_acceptance.py`: existing `run_smoke` fixture tests and
   established `monkeypatch` style only.
3. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

1. Add focused `run_smoke` tests that inject one failure at a time through the
   existing boundary helpers/imported round-trip dependency: collector,
   serialization, and replay loader.
2. Assert each result’s public `ok`, named check status/message/details, and
   preservation/skipping of the relevant surrounding evidence. Use existing
   fixture cgroup/replay inputs for successful sibling stages.
3. Do not mock the whole harness, alter product handling, or combine multiple
   injected failures in one test.
4. Run `topos/tests/test_acceptance.py -q` in `tester-unified`, self-review
   scope and all three oracles, and leave work unmerged.

## BLOCKED rule

If any stage cannot be fault-injected at its real boundary while retaining the
rest of `run_smoke`, write `BLOCKED: <named stage and reason>` and stop.
