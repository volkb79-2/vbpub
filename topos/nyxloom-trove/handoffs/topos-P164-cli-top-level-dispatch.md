---
schema_version: 1
id: topos-P164-cli-top-level-dispatch
project: topos
title: "Specify top-level CLI command dispatch"
tier: luna-low
input_revision: "5262eeb2"
depends_on: []
session: resume cli
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/tests/test_cli_top_level_dispatch.py", "topos/nyxloom-trove/handoffs/topos-P164-cli-top-level-dispatch.md"]
  forbid: ["topos/src/topos/cli.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py"]
oracles:
  - id: O1
    observable: "Every recognized top-level operational verb routes its remaining argv to exactly its dedicated handler and returns that handler's exit code."
    negative: "A command is parsed as ordinary live collection, receives its verb twice, routes to the wrong handler, or loses its exit status."
    gate: topos-suite
  - id: O2
    observable: "The live-collection default path remains separate from operational-verb dispatch."
    negative: "Adding a command verb accidentally bypasses the normal parser or makes an unknown/default live invocation execute an operational handler."
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "an oracle needs an actual action, daemon, BPF probe, Collector, UI, product-code edit, or a file outside the two allowed paths"
---

# P164 — Specify top-level CLI command dispatch

## Context to read first

1. `topos/src/topos/cli.py`: `main` dispatch block only (roughly lines
   504–530).
2. `topos/tests/test_cli_command_parsers.py`: the direct-parser safety style;
   do not duplicate its parser contracts.
3. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

1. Add one focused direct-dispatch test module. Parameterize the recognized
   operational verbs declared in the `main` dispatch block.
2. Monkeypatch each selected handler to record only its received tail argv and
   return a distinct sentinel. Assert `main` removes exactly the verb, routes
   to the correct handler, and returns that sentinel. No real handler may run.
3. Separately patch `parse_args`/the default pre-collection boundary to prove a
   normal non-command argv reaches the live path rather than a command handler;
   stop it before collection.
4. Run this focused module in `tester-unified`, self-review all oracles and
   scope, commit only the allowed files, and leave the branch unmerged.

## BLOCKED rule

If an oracle needs a real action, daemon, BPF probe, Collector, UI,
product-code edit, or a forbidden file, write `BLOCKED: <named oracle and
reason>` and stop.
