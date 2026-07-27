---
schema_version: 1
id: topos-P171-cli-inspect-files-dispatch
project: topos
title: "Specify inspect-files CLI result and diagnostic dispatch"
tier: luna-low
input_revision: "f376c53b"
depends_on: [topos-P170-cli-compare-dispatch]
session: resume:cli
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/tests/test_cli_inspect_files_dispatch.py", "topos/nyxloom-trove/handoffs/topos-P171-cli-inspect-files-dispatch.md"]
  forbid: ["topos/src/topos/cli.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py"]
oracles:
  - id: O1
    observable: "`topos inspect-files plan` resolves its target, forwards every gate flag to the plan boundary, renders valid plans as exact text or JSON, and rejects target/plan/disabled/unexpected outcomes with the declared nonzero status."
    negative: "A plan runs with an unresolved target or lost safety flag, a disabled/unexpected result succeeds, JSON/text rendering is swapped, or a declared failure reaches a later renderer."
    gate: topos-suite
  - id: O2
    observable: "`topos inspect-files read` forwards bounded-read options, renders valid content as exact text or JSON, and preserves distinct denied/read-error/unexpected-result statuses without exposing a later success renderer."
    negative: "Read bounds or safety flags are lost, a read error is reported as success, a denied read invokes a content renderer, or an unknown command silently succeeds."
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "an oracle needs a real container resolver, a real filesystem/journald read, a production-code edit, or a file outside the two allowed paths"
---

# P171 — Specify inspect-files CLI result and diagnostic dispatch

## Context to read first

1. `topos/src/topos/cli.py`, only `parse_inspect_files_args` and
   `_main_inspect_files` (roughly lines 270–289 and 1440–1522).
2. `topos/src/topos/inspect_files/plan.py`, only `InspectFilesPlan` and
   `DisabledInspector`; `topos/src/topos/inspect_files/reader.py`, only
   `InspectFilesReadResult`, `InspectFilesReadError`, and `ReadDenied`.
3. `topos/tests/test_cli_compare_dispatch.py` for the direct CLI-boundary
   style and error short-circuit assertions.
4. `topos/nyxloom-trove/nyxloom.toml`, `[gates.topos-suite]`.

## Work

1. Add exactly one focused direct test module; do not modify production code
   or the existing broad `test_inspect_files.py` module.
2. Build real result dataclass values with inert string fields (or use narrowly
   patched result functions) and monkeypatch only the resolver, parser, and
   plan/read boundary necessary to prove `plan` and `read` dispatch. Do not
   call Docker, `journalctl`, or any real read boundary.
3. For a successful plan and read, assert resolved target, all safety/bounds
   arguments, exact status, and the chosen text/JSON rendering once. Include a
   JSON path for each command.
4. Cover target-resolution failure, plan `ValueError`, disabled plan, denied
   read, typed read error (status 1), unexpected plan/read result (status 2),
   and unknown command. On each failure, assert actionable stderr and prove no
   later success renderer/boundary was called.
5. Run the focused module in `tester-unified` under
   `--cgroup-parent=nyxloom-gates.slice`, self-review for hollow assertions and
   scope, commit only the allowed files, and leave the branch unmerged.

## BLOCKED rule

If an oracle needs a real container resolver, a real filesystem/journald read,
a production-code edit, or a forbidden file, write `BLOCKED: <named oracle and
reason>` and stop.
