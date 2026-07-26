---
schema_version: 1
id: topos-P147-snapshot-frame-invariant
project: topos
title: "Remove unreachable snapshot worker frame guard"
tier: controller
input_revision: "b1c42672"
depends_on: [topos-P146-app-replay-control-coverage]
session: fresh
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/src/topos/ui/app.py", "topos/nyxloom-trove/handoffs/topos-P147-snapshot-frame-invariant.md"]
  forbid: ["topos/tests/test_ui_app.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py"]
oracles:
  - id: O1
    observable: "All public snapshot success/failure paths remain covered by the existing mounted snapshot suite."
    negative: "Snapshot scheduling can start its worker without the captured frame it requires."
    gate: topos-suite
  - id: O2
    observable: "The snapshot worker has no impossible no-frame branch; its only caller assigns `_snapshot_frame` immediately before `run_worker`."
    negative: "Coverage retains a defensive path no public execution can reach."
    gate: topos-suite
gates: [topos-suite]
escalate_if: ["another caller or reset of _snapshot_frame exists", "the full tester-unified gate changes snapshot behavior"]
---

# P147 — Remove unreachable snapshot worker frame guard

## Context to read first

1. `topos/src/topos/ui/app.py`: `action_create_snapshot` and
   `_run_snapshot_worker` only.
2. `topos/tests/test_ui_app.py`: its existing snapshot tests.

## Work

Remove only `_run_snapshot_worker`'s impossible `frame is None` guard. The
sole caller assigns `_snapshot_frame = current_frame` before starting the
worker, and the action returns before scheduling when no current frame exists.

## BLOCKED rule

If a second caller or a post-assignment reset is found, write `BLOCKED` to this
handoff and stop; do not remove the guard.
