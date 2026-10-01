---
schema_version: 1
id: topos-P152-replay-minimum-delay
project: topos
title: "Make equal-timestamp replay frames advance safely"
tier: luna-low
input_revision: "f2757bb5"
depends_on: [topos-P146-app-replay-control-coverage]
session: resume topos-ui-replay-coverage
source: {kind: bug, ref: "P151 public zero-delay replay blocker"}
scope:
  touch: ["topos/src/topos/ui/app.py", "topos/tests/test_ui_app.py", "topos/nyxloom-trove/handoffs/topos-P152-replay-minimum-delay.md"]
  forbid: ["topos/src/topos/record/replay.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py"]
oracles:
  - id: O1
    observable: "A mounted non-step replay with two equal-timestamp frames reaches frame 2/2 and reports paused, without a Textual timer exception."
    negative: "A zero timer delay stalls the replay or raises ZeroDivisionError during timer teardown."
    gate: topos-suite
  - id: O2
    observable: "Positive timestamp gaps retain their timestamp-derived timing; only a non-positive computed delay is raised to the smallest safe scheduler delay."
    negative: "The repair changes normal replay cadence or relies on a test-only workaround."
    gate: topos-suite
gates: [topos-suite]
escalate_if: ["Textual's supported minimum timer interval cannot be established from installed API/docs and existing code", "the repair requires a file outside scope"]
---

# P152 — Make equal-timestamp replay frames advance safely

## Context to read first

1. `topos/src/topos/ui/app.py`: `_schedule_replay_tick`, `_advance_replay`, and
   the existing replay timer fields only.
2. `topos/tests/test_ui_app.py`: replay fixture construction and public pilot
   replay tests only.
3. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

1. Repair the scheduler’s non-positive delay case with the smallest safe,
   named-in-code interval that prevents Textual zero-delay timer teardown
   failure. Preserve the existing positive-gap calculation exactly.
2. Add one mounted public replay regression test with two same-timestamp frames
   and `replay_step=False`. Pump only with a finite `pilot.pause()` deadline;
   assert the public paused frame-2/2 status and no exception.
3. Do not mock timers, call timer internals, alter ReplayDriver, or sleep for a
   wall-clock frame interval.
4. Run `topos/tests/test_ui_app.py -q` in `tester-unified`, self-review the
   diff against O1/O2, and leave the work unmerged.

## BLOCKED rule

If no safe scheduler lower bound can be established without speculation, or the
mounted replay still cannot complete through public pilot pumping, write
`BLOCKED: <specific reason>` and stop.
