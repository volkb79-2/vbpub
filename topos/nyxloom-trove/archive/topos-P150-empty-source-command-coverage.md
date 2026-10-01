---
schema_version: 1
id: topos-P150-empty-source-command-coverage
project: topos
title: "Cover empty-source command guards through public keys"
tier: luna-low
input_revision: "ac842624"
depends_on: [topos-P149-empty-row-highlight-guard]
session: resume topos-ui-app-coverage
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/tests/test_ui_app.py", "topos/nyxloom-trove/handoffs/topos-P150-empty-source-command-coverage.md"]
  forbid: ["topos/src/topos/ui/app.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py"]
oracles:
  - id: O1
    observable: "A mounted live app with an empty frame source shows waiting and accepts Enter, snapshot, host-memory, and Escape without changing away from its base screen or crashing."
    negative: "A no-frame command opens an invalid overlay, starts snapshot work, or raises."
    gate: topos-suite
  - id: O2
    observable: "The public test reaches the no-current-frame paths for snapshot, host-memory, and overlay close through `x`, `m`, and Escape; Enter is separately proved a harmless widget no-op when no row is selectable."
    negative: "A hollow test changes internal state directly and misses the real key-binding behaviour."
    gate: topos-suite
gates: [topos-suite]
escalate_if: ["any specified public key has no ToposApp binding", "one of the command paths requires a source change"]
---

# P150 — Cover empty-source command guards through public keys

## Context to read first

1. `topos/src/topos/ui/app.py`: bindings and no-current-frame guards for drill,
   snapshot, host-memory, and overlay close only.
2. `topos/tests/test_ui_app.py`: P149 empty-source test and existing mounted
   snapshot/host-memory interaction style.
3. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

1. Add one mounted `ToposApp(())` public-pilot regression test. Assert the
   waiting status before and after each command and retain the original base
   screen identity.
2. Exercise only the actual key bindings: Enter, `x`, `m`, and Escape. The
   empty DataTable consumes Enter before app drill dispatch; assert that safe
   no-op separately from the actual `x`/`m`/Escape command guards. Do not
   invoke actions/private methods or build fake widget events.
3. Do not duplicate P149 Up/Down coverage; this package covers the remaining
   no-frame commands.
4. Run `topos/tests/test_ui_app.py -q` in `tester-unified`, then self-review
   the diff against O1/O2 and scope. Do not merge to main.

## BLOCKED rule

If any required key cannot reach the stated public command path without a
forbidden source edit, write `BLOCKED: <specific key and reason>` and stop.
