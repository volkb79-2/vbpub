---
schema_version: 1
id: topos-P168-cli-damon-dispatch
project: topos
title: "Specify DAMON CLI stop and paddr dispatch"
tier: luna-low
input_revision: "a6c0bef5"
depends_on: [topos-P167-cli-local-source-and-ui-boundaries]
session: resume cli
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/tests/test_cli_damon_dispatch.py", "topos/nyxloom-trove/handoffs/topos-P168-cli-damon-dispatch.md"]
  forbid: ["topos/src/topos/cli.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py"]
oracles:
  - id: O1
    observable: "`topos damon stop` cannot act without --all-mine; with it, it forwards the declared filesystem and fixture-root settings, reports the stopped count, and maps root refusal to exit 2 versus DAMON control failure to exit 1."
    negative: "A bare stop can affect sessions, the ownership/root settings are lost, a failure reports success, or operational error classes collapse to one status."
    gate: topos-suite
  - id: O2
    observable: "`topos damon paddr start` loads its selected configuration, renders a confirmation plan without starting when confirmation is absent, starts exactly the planned session after confirmation, and preserves distinct root/control failure statuses."
    negative: "Planning mutates state, configuration is not forwarded, confirmation is bypassed, or error handling starts a session or reports success."
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "an oracle needs real DAMON sysfs/root privileges, product-code edit, or a file outside the two allowed paths"
---

# P168 — Specify DAMON CLI stop and paddr dispatch

## Context to read first

1. `topos/src/topos/cli.py`, only `_main_damon` (roughly lines 697–744).
2. `topos/tests/test_cli_squeeze_dispatch.py` for direct command-boundary test
   style and distinct output/exit assertions.
3. `topos/tests/test_damon_paddr.py`, only the fixture command test, to retain
   the real filesystem contract while adding no new real-sysfs test.
4. `topos/nyxloom-trove/nyxloom.toml`, `[gates.topos-suite]`.

## Work

1. Add exactly one focused direct test module. Do not modify production code.
2. Patch the narrow imported boundaries in `topos.cli` to prove `stop` refuses
   a missing `--all-mine` before any stop call; on success assert all four
   `stop_owned_sessions` keyword arguments and the exact success output. Make
   `RootRequired` and `DamonControlError` each observable with their distinct
   exit/status channel.
3. For `paddr start`, use a fake config returned by patched `load`, a plan
   object, and patched planning/start functions. Assert absent `--confirm`
   produces only the plan confirmation text and no start; assert a confirmation
   starts exactly that plan with the entered text and fixture-root choice and
   reports the session index. Prove its two documented error classes map to
   their distinct statuses without starting state.
4. Do not instantiate real DAMON roots, invoke a CLI subprocess, sleep, or
   mock the function under test. Every test must call `_main_damon` directly.
5. Run the focused module in `tester-unified` with
   `--cgroup-parent=nyxloom-gates.slice`, self-review for hollow assertions and
   scope, commit only the two allowed files, and leave the branch unmerged.

## BLOCKED rule

If an oracle needs real DAMON sysfs/root privileges, product-code edit, or a
forbidden file, write `BLOCKED: <named oracle and reason>` and stop.
