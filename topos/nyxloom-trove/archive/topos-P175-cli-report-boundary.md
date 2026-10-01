---
schema_version: 1
id: topos-P175-cli-report-boundary
project: topos
title: "Cover report CLI validation, rendering, and assertion outcomes"
tier: luna-low
input_revision: "7072c8de"
depends_on: []
session: "resume:cli"
source: {kind: product-goal, ref: "nyxloom-trove/3-roadmap.md"}
scope:
  touch:
    - "tests/test_cli_report_boundary.py"
    - "nyxloom-trove/handoffs/topos-P175-cli-report-boundary.md"
  forbid:
    - "src/topos/cli.py"
    - "src/topos/report.py"
    - "src/topos/render.py"
    - "nyxloom-trove/nyxloom.toml"
oracles:
  - id: O1
    observable: "The report CLI turns invalid numeric input and every typed reader failure into its documented bounded stderr message and exit 2."
    negative: "A regression leaking a traceback or treating a damaged recording as success fails."
    gate: topos-suite
  - id: O2
    observable: "The report CLI selects its public JSON/text renderer and maps assertion pass/breach to exit 0/1."
    negative: "A regression that bypasses a renderer or lets a breached assertion pass fails."
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "a named contract cannot be met as specified"
  - "scope requires a forbidden file"
---

# P175 — report CLI boundary

## Context to read first

1. `src/topos/cli.py`, `_main_report` lines 1066–1170 only.
2. `tests/test_report.py`: report domain fixtures and public terminology; do not broaden into domain-calculation tests.
3. `tests/test_cli_action_execute.py`: direct CLI boundary test style using injected module seams and exact output/exit assertions.
4. `src/topos/report.py`: signatures and result shapes of the imported public report functions.
5. `nyxloom-trove/nyxloom.toml`, `[gates.topos-suite]`.

## Work

1. Add only `tests/test_cli_report_boundary.py`. Directly test `_main_report` with injected report/render module seams and a harmless synthetic input path; do not read real recordings or use real clocks/network.
2. Prove each public boundary family: invalid stability/min-frame values; file-not-found, zstandard RuntimeError, ordinary RuntimeError, ValueError, OSError, and generic reader failures; JSON versus text rendering; malformed assertion spec; and assertion pass versus breach exit semantics.
3. Use distinct sentinels and exact stdout/stderr assertions so a wrong formatter, exception mapping, or exit code is observable. Do not merely assert a mock was called.
4. Self-review only the actual worktree diff, run focused tester-unified tests, and commit only allowed files.

## Oracles

- O1: Each injected damaged-input exception must return 2 with the documented bounded message and no traceback. Broken exception handling or success exit makes the test red.
- O2: Distinct JSON/text renderer sentinel output must be exactly emitted; a fake failed `AssertionResult` must return 1 while all-pass/none returns 0. A renderer bypass or breach-as-success makes the test red.
- Gate: run the declared `topos-suite` gate in tester-unified, never the cockpit. For focus, mount the repository and run `/opt/tester-venv/bin/python -m pytest topos/tests/test_cli_report_boundary.py -q` from the assigned worktree with `PYTHONPATH=topos/src:topos`.

## Test constraints

- No wall-clock oracle, sleep, real filesystem recording, network, or global-state leak.
- Use `monkeypatch` only at the module namespace owning the public boundary; restore automatically.
- Assert public output and exit behaviour, not private call counts or non-raises.
- Do not add coverage exclusions or `no cover` text.

## Scope / forbid

Only the named new test and this handoff may change. Do not alter product logic, report/render implementation, existing tests, or gate configuration. Work in the assigned worktree and branch.

## BLOCKED rule

If a named contract cannot be proven through the stated seams, or completion requires a forbidden file, STOP; write `BLOCKED: <reason>` to the LOG, commit that record, and exit. Do not improvise a workaround.
