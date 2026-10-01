---
schema_version: 1
id: topos-P178-cli-attach-replay
project: topos
title: "Cover CLI attach and replay mode contracts"
tier: luna-low
input_revision: "23a28cc1"
depends_on: []
session: "resume:cli"
source: {kind: product-goal, ref: "nyxloom-trove/3-roadmap.md"}
scope:
  touch: ["tests/test_cli_attach_replay_boundary.py", "nyxloom-trove/handoffs/topos-P178-cli-attach-replay.md"]
  forbid: ["src/topos/cli.py", "src/topos/daemon", "src/topos/record", "nyxloom-trove/nyxloom.toml"]
oracles:
  - id: O1
    observable: "attach/replay invalid option combinations fail before their live boundaries with the documented exit 2 messages."
    negative: "An incompatible option reaching daemon/replay/UI code or returning success fails."
    gate: topos-suite
  - id: O2
    observable: "valid attach/replay calls forward their exact public options, render their documented output, and map UI/daemon outcomes to public exit codes."
    negative: "A dropped option, wrong source label, fallback omission, or daemon error mapping regression fails."
    gate: topos-suite
gates: [topos-suite]
escalate_if: ["a named contract cannot be met as specified", "scope requires a forbidden file"]
---

# P178 — CLI attach/replay boundary

## Context to read first

1. `src/topos/cli.py`, `main` lines 504–694 only.
2. `tests/test_attach_cli.py`, `tests/test_record.py`, and `tests/test_cli_local_source_ui.py`: existing public contracts and direct seams.
3. `tests/test_cli_action_execute.py`: direct-boundary style.
4. `nyxloom-trove/nyxloom.toml`, `[gates.topos-suite]`.

## Work

1. Add only `tests/test_cli_attach_replay_boundary.py`. Test `main` directly using injected module boundaries; never connect a real daemon, launch a UI, read a real recording, sleep, or use a wall-clock oracle.
2. Cover each attach incompatibility and attach once/json, UI success/fallback, daemon-client failure, and keyboard interrupt outcome. Cover replay option rejection, exact `ReplayDriver.from_path`/`_run_ui` arguments, UI success, and textual frame fallback.
3. Use distinct fakes and exact stdout/stderr/exit assertions. Assert the observable forwarded source label, replay step/speed, cgroup root, and UI options; a mere mock call count is insufficient.
4. Self-review, focus-test in tester-unified, and commit only allowed files.

## Oracles

- O1: Invalid attach/replay combinations must return 2 before `current_frame`, `ReplayDriver`, or `_run_ui`; any boundary call makes a test red.
- O2: Fake attach/replay boundaries expose exact arguments and distinct frame/UI/fallback output. Wrong argument, wrong source label, error mapping, or exit code makes a test red.
- Gate: use tester-unified and then the declared `topos-suite`, never cockpit Python.

## Test constraints

- No sleep, clock-based verdict, live socket, filesystem recording, network, or leaking process-global state.
- Patch module-owned boundaries with `monkeypatch`; assert behavioural output/exit contracts.
- No coverage exclusions or `no cover` text.

## Scope / forbid

Only the named test and handoff may change; no product, gate, or existing-test changes. Work in assigned worktree/branch.

## BLOCKED rule

If a named contract cannot be proven through stated seams, or a forbidden file is needed, STOP; write `BLOCKED: <reason>` to the LOG, commit it, and exit. Do not improvise.
