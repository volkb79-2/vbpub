---
schema_version: 1
id: topos-P173-cli-action-preview
project: topos
title: "Specify action preview CLI safety, audit, and rendering"
tier: luna-low
input_revision: "099e5b12"
depends_on: [topos-P172-cli-optional-frontends]
session: resume:cli
source: {kind: product-goal, ref: "nyxloom-trove/3-roadmap.md"}
scope:
  touch: ["tests/test_cli_action_preview.py", "nyxloom-trove/handoffs/topos-P173-cli-action-preview.md"]
  forbid: ["src/topos/cli.py", "nyxloom-trove/nyxloom.toml", "tools/coverage_gate.py"]
oracles:
  - id: O1
    observable: "`topos action preview` resolves its target, forwards every safety option to preview, and rejects resolution/value/disabled/unexpected outcomes before audit or rendering."
    negative: "A failed preview reaches audit/rendering or loses admin/action safety arguments."
    gate: topos-suite
  - id: O2
    observable: "Typed set-property, kill, update, and generic action plans render exactly their requested text/JSON contract and audit only when configured."
    negative: "A typed plan uses the wrong renderer/audit fields, JSON and text mix, or preview executes an action."
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "an oracle needs a real action execution, real audit file, product-code edit, or a file outside the two allowed paths"
---

# P173 — Specify action preview CLI safety, audit, and rendering

## Context to read first

1. `topos/src/topos/cli.py`, only `_main_action` preview path (lines 803–964).
2. `topos/tests/test_cli_inspect_files_dispatch.py` for direct result dispatch style.
3. `topos/nyxloom-trove/nyxloom.toml`, `[gates.topos-suite]`.

## Work

1. In a fresh branch worktree based on `main`, add one direct test module only; production code is out of scope.
2. Use inert typed plan values and narrow boundaries to prove target/safety forwarding, disabled/value/resolution failures, and no later audit/rendering after each failure.
3. Cover set-property, kill, update, and generic plans: JSON/text output and configured audit fields must be exact; no real action or audit filesystem write may occur.
4. Run the focused tester-unified test under `--cgroup-parent=nyxloom-gates.slice`, self-review, commit only scope, do not merge.

## BLOCKED rule

If an oracle needs a real action execution, real audit file, production-code edit, or forbidden file, write `BLOCKED: <named oracle and reason>` and stop.
