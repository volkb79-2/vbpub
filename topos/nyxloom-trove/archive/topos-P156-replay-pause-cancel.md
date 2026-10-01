---
schema_version: 1
id: topos-P156-replay-pause-cancel
project: topos
title: "Cover replay pause cancellation through the public key"
tier: luna-low
input_revision: "419b3923"
depends_on: [topos-P152-replay-minimum-delay]
session: fresh
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/tests/test_ui_app.py", "topos/nyxloom-trove/handoffs/topos-P156-replay-pause-cancel.md"]
  forbid: ["topos/src/topos/ui/app.py", "topos/src/topos/record/replay.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py"]
oracles:
  - id: O1
    observable: "In a mounted paused replay with a positive-gap next frame, Space starts playback and a second Space returns it to the same paused frame before the timer advances."
    negative: "Pause fails to cancel the scheduled replay timer or unexpectedly advances the frame."
    gate: topos-suite
  - id: O2
    observable: "The public replay status reports playing then paused, without direct timer/private-state calls."
    negative: "The test bypasses the public key path and cannot detect a broken pause transition."
    gate: topos-suite
gates: [topos-suite]
escalate_if: ["the fixture replay auto-advances before the second public Space despite its positive timestamp interval", "proving cancellation requires private timer inspection"]
---

# P156 — Cover replay pause cancellation through the public key

## Context to read first

1. `topos/src/topos/ui/app.py`: `action_toggle_replay_pause` and
   `_cancel_replay_timer` only.
2. `topos/tests/test_ui_app.py`: existing `_replay_app` and replay-space tests
   only.
3. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

1. Extend or add one mounted replay test that presses Space to start a normal
   paused replay, observes public playing status, then immediately presses
   Space again and observes paused frame 1/2 status.
2. Do not call timer/private state APIs or wait for the next one-second frame;
   status plus unchanged public frame number is the cancellation oracle.
3. Run `topos/tests/test_ui_app.py -q` in `tester-unified`, self-review scope
   and both oracles, and leave work unmerged.

## BLOCKED rule

If the existing positive-gap fixture cannot reliably accept the second Space
before timer advancement through normal pilot control, write
`BLOCKED: <specific timing behaviour>` and stop.
