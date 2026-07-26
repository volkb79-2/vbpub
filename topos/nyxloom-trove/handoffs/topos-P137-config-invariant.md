---
schema_version: 1
id: topos-P137-config-invariant
project: topos
title: "Remove unreachable configuration score-weight guard"
tier: controller
input_revision: "f53bf723"
depends_on: [topos-P136-config-coverage]
session: resume:topos-config-coverage
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/src/topos/config.py", "topos/nyxloom-trove/handoffs/topos-P137-config-invariant.md"]
  forbid: ["topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py", "topos/pyproject.toml"]
oracles:
  - id: O1
    observable: "Public load(path) preserves defaults for malformed score-weight TOML and applies valid custom score weights."
    negative: "Tightening the private helper's invariant changes public TOML behavior."
    gate: topos-suite
  - id: O2
    observable: "config.py has no missing statements or branches in the full tester-unified coverage JSON."
    negative: "A caller-impossible defensive guard remains a coverage hole."
    gate: topos-suite
gates: [topos-suite]
escalate_if: ["a caller other than load() reaches _load_score_weights", "public load behavior changes", "scope requires a forbidden file"]
---

# P137 — Remove unreachable configuration score-weight guard

## Context to read first

1. `topos/src/topos/config.py`: `_load_score_weights` and `load` only.
2. `topos/tests/test_config.py`: public TOML score-weight assertions.
3. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

`_load_score_weights` has one caller, `load`, which always constructs
`thresholds` with `dict(data.get("thresholds", {}) or {})`. Tighten its
parameter type to that real dict invariant and remove only its unreachable
non-dict guard. Do not change public TOML loading behavior or add a
private-helper test/coverage suppression.

## Oracle

Run the declared tester-unified gate. `config.py` must report no missing lines
or branches; the existing public `load(path)` malformed/default/custom
score-weight tests must pass.

## Scope / forbid

Touch only the named source and handoff. Any other caller or behavior change is
a BLOCKED condition.

## BLOCKED rule

If another caller reaches `_load_score_weights`, or retaining the guard is
needed for a public contract, STOP. Write `BLOCKED: <specific reason>` to the
handoff LOG, commit that log-only change, and exit. Do not use a private-helper
test or suppression.
