---
schema_version: 1
id: topos-P167-cli-local-source-and-ui-boundaries
project: topos
title: "Specify local CLI source and UI boundary contracts"
tier: luna-low
input_revision: "a74864e2"
depends_on: []
session: resume cli
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/tests/test_cli_local_source_ui.py", "topos/nyxloom-trove/handoffs/topos-P167-cli-local-source-and-ui-boundaries.md"]
  forbid: ["topos/src/topos/cli.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py"]
oracles:
  - id: O1
    observable: "The attach source polls no faster than 100ms and yields exactly the canonical frames returned by its daemon boundary; replay yields each replay-frame payload in driver order."
    negative: "A zero or negative polling interval spins, daemon frames are skipped or transformed, or replay returns wrapper objects rather than their frames."
    gate: topos-suite
  - id: O2
    observable: "The UI adapter passes every supplied contract argument through to the UI boundary, prints a string result, and gives deterministic distinct outcomes for an unavailable Textual dependency in smoke and non-smoke modes."
    negative: "Arguments silently disappear, a useful UI message is lost, a missing optional UI dependency raises in ordinary mode, or smoke falsely succeeds."
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "an oracle needs a real daemon, real Textual UI, sleep, product-code edit, or a file outside the two allowed paths"
---

# P167 — Specify local CLI source and UI boundary contracts

## Context to read first

1. `topos/src/topos/cli.py`, only `_attach_frame_source`, `_replay_frame_source`,
   and `_run_ui` (roughly lines 358–404).
2. `topos/tests/test_cli_live_guards.py` for direct-boundary test style.
3. `topos/tests/test_cli_squeeze_dispatch.py` for focused, behavioral monkeypatching.
4. `topos/nyxloom-trove/nyxloom.toml`, `[gates.topos-suite]`.

## Work

1. Add exactly one focused direct test module; do not modify production code.
2. For attach, monkeypatch `current_frame` and `time.sleep`; consume two generator
   values and assert frame order and the 100ms floor for a nonpositive requested
   interval. Do not sleep in real time and do not contact a daemon.
3. For replay, use a small fake driver whose `play` records `speed` and `step`,
   returns wrapper values with `frame`, and assert the yielded payload sequence.
4. For `_run_ui`, inject a fake `topos.ui.app` module through `sys.modules` (or
   equivalent narrow import seam) and assert all arguments reach `run_ui`; prove
   both string-result printing and a non-string result. Simulate only a
   `ModuleNotFoundError` whose `name` begins `textual` to assert non-smoke returns
   `-1` and smoke writes its diagnostic to stderr and returns `2`. A different
   missing module must re-raise.
5. Run the focused module in `tester-unified`, inspect the diff for hollow
   assertions and over-mocking, commit only the allowed paths, and leave the
   branch unmerged.

## BLOCKED rule

If an oracle needs a real daemon, real Textual UI, sleep, product-code edit, or
a forbidden file, write `BLOCKED: <named oracle and reason>` and stop.
