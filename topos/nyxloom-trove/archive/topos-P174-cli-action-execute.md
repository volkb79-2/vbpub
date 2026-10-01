---
schema_version: 1
id: topos-P174-cli-action-execute
project: topos
title: "Cover action execute CLI routing and public outcome contract"
tier: luna-low
input_revision: "9a204b33"
depends_on: []
session: "resume:cli"
source: {kind: product-goal, ref: "nyxloom-trove/3-roadmap.md"}
scope:
  touch:
    - "tests/test_cli_action_execute.py"
    - "nyxloom-trove/handoffs/topos-P174-cli-action-execute.md"
  forbid:
    - "src/topos/cli.py"
    - "src/topos/actions"
    - "nyxloom-trove/nyxloom.toml"
oracles:
  - id: O1
    observable: "For each execute action family, the CLI resolves the target and forwards the exact public options to the correct executor in the tester-unified gate."
    negative: "A regression that routes a kind to the wrong executor, drops an option, or bypasses the production owner safety callback fails."
    gate: topos-suite
  - id: O2
    observable: "JSON and text execute output use their public formatter contracts, and success/refusal/failure outcomes return 0/2/1 respectively in the tester-unified gate."
    negative: "A regression that changes the observable output or makes a refusal look like success fails."
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "a named contract cannot be met as specified"
  - "scope requires a forbidden file"
---

# P174 — action execute CLI dispatch

## Context to read first

1. `src/topos/cli.py`, `_main_action` lines 803–1012: execute routing, output, and exit contract.
2. `tests/test_cli_action_preview.py`: direct CLI-boundary test style and exact output assertions.
3. `tests/test_p87_owner_safety.py`, `TestCliProductionWiring`: the production owner-safety wiring already protected; extend its behavioural coverage rather than duplicating a weak call-count test.
4. `src/topos/actions/execute.py`: public `ExecuteResult`, executor signatures, `result_to_jsonable`, and `render_result_text` contracts.
5. `nyxloom-trove/nyxloom.toml`, `[gates.topos-suite]`: the only ship gate.

## Work

1. Add only `tests/test_cli_action_execute.py`. Test `_main_action` directly with injected boundary functions; never invoke Docker, systemd, or a real owner lookup.
2. Exercise all four execute routing families: `systemd-set-property` with both property/value, kill, docker update, and generic `execute_plan`. Assert the resolved target and exact public executor arguments, including the production `owner_safety.default_owner_inspect` callback in the three guarded families.
3. Prove the observable result contract: JSON uses `result_to_jsonable`, text uses `render_result_text`, and `ExecuteResult.outcome` maps success/refusal/another failure to 0/2/1. Use distinct sentinel output and results so a wrong route or formatter is visible.
4. Self-review the actual worktree diff for omitted branches and run the focused new test file in `tester-unified`; commit the test and this handoff if it changed.

## Oracles

- O1: Swap each executor at its imported module boundary for a recording fake returning a distinct `ExecuteResult`. Call the CLI with concrete arguments and assert the exact function family, target, safety options, and owner callback. A dropped `force`, `below_current`, or owner callback must make the test red.
- O2: Patch each public formatter/converter to return a distinct sentinel, then assert the exact stdout JSON/text and 0/2/1 outcomes. A formatter bypass, outcome remapping, or wrong executor result must make the test red.
- Gate: run the project’s declared `topos-suite` gate in `tester-unified`, never the cockpit. Focused command: `docker run --rm --cgroup-parent=nyxloom-gates.slice -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local bash -c 'cd /workspaces/vbpub && export PYTHONPATH=topos/src:topos && /opt/tester-venv/bin/python -m pytest topos/tests/test_cli_action_execute.py -q'`.

## Test constraints

- No wall-clock oracle, `sleep`, real network, real Docker/systemd, or real clock.
- Do not leak process-global state; use `monkeypatch` on the namespace that owns the boundary and fresh local data per test.
- Assert behavioural contracts (routing inputs, exact public output, exit codes), not private calls or vacuous non-raises.
- Do not add coverage exclusions or `no cover` text.

## Scope / forbid

Only the named new test and this handoff may change. Do not alter product logic, executor implementation, gate configuration, or existing tests. Work in the assigned worktree and branch.

## BLOCKED rule

If a named contract cannot be proven with the stated public test seams, or completing it requires any forbidden file, STOP; write `BLOCKED: <reason>` to the LOG, commit that record, and exit. Do not improvise a workaround.
