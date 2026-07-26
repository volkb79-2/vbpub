---
schema_version: 1
id: topos-P146-app-replay-control-coverage
project: topos
title: "Cover app replay control state transitions"
tier: luna-low
input_revision: "27e9abd4"
depends_on: [topos-P145-app-tree-navigation-coverage]
session: fresh
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/tests/test_ui_app.py", "topos/nyxloom-trove/handoffs/topos-P146-app-replay-control-coverage.md"]
  forbid: ["topos/src/topos/ui/app.py", "topos/src/topos/record/replay.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py"]
oracles:
  - id: O1
    observable: "Live-mode replay keys show their public unavailable status rather than mutating frames or crashing."
    negative: "Replay-only controls silently act in live mode."
    gate: topos-suite
  - id: O2
    observable: "Mounted replay keys pause/play, step backward/forward at bounds, and adjust speed with visible replay status."
    negative: "A replay control loses its paused/frame/speed state or bypasses bounds."
    gate: topos-suite
gates: [topos-suite]
escalate_if: ["testing requires directly calling private replay methods", "timer behavior cannot be observed deterministically through public mounted keys"]
---

# P146 — Cover app replay control state transitions

## Context to read first

1. `topos/src/topos/ui/app.py`: replay actions and `_set_replay_speed` only.
2. `topos/tests/test_ui_app.py`: `_replay_app` plus existing replay key tests.
3. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

Add mounted public key tests for replay-only controls in live mode and for
pause/play, bounded stepping, and speed cycle in replay-step mode. Assert
public status text and driver position; do not invoke private action methods
or make timing-dependent assertions about a running worker.

## BLOCKED rule

If a replay state cannot be observed through public keys/status without a
private callback or timing workaround, write `BLOCKED: <specific reason>` and
stop.
