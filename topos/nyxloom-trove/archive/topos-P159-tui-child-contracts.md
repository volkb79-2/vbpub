---
schema_version: 1
id: topos-P159-tui-child-contracts
project: topos
title: "Specify TUI child invocation and incomplete-output semantics"
tier: luna-low
input_revision: "08d1a9ce"
depends_on: []
session: fresh
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/tests/test_acceptance.py", "topos/nyxloom-trove/handoffs/topos-P159-tui-child-contracts.md"]
  forbid: ["topos/src/topos/acceptance.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py"]
oracles:
  - id: O1
    observable: "A configured TUI smoke child receives the replay, step, UI-smoke, config, and profile argv in their declared form, and a valid child smoke line becomes a successful structured result."
    negative: "A caller-supplied config/profile is silently dropped or a child result is fabricated without the actual smoke fields."
    gate: topos-suite
  - id: O2
    observable: "A zero-exit child that emits no `ui smoke ok` line remains a failed result, retaining bounded stdout/stderr evidence rather than being mistaken for a healthy UI."
    negative: "Exit status alone launders incomplete child output into a green TUI smoke result."
    gate: topos-suite
  - id: O3
    observable: "Oversized child stdout/stderr are capped at the harness's documented 500-character evidence boundary."
    negative: "One noisy child grows JSON/text acceptance output without bound."
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "the child-result contract cannot be demonstrated through the subprocess boundary without changing product code"
  - "an oracle requires a file outside the two allowed paths"
---

# P159 — Specify TUI child invocation and incomplete-output semantics

## Context to read first

1. `topos/src/topos/acceptance.py`: `run_tui_smoke` only (roughly lines
   666–783).
2. `topos/tests/test_acceptance.py`: existing `run_tui_smoke` unit tests and
   local `subprocess`/`monkeypatch` conventions only.
3. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

1. Add focused, test-only `run_tui_smoke` tests that replace only the child
   `subprocess.run` boundary with a result-shaped response.  Preserve the
   real harness's argv construction and result parsing; do not mock the whole
   function or Textual.
2. Prove the config+profile argv forwarding and success parsing contract.
   Assert the specific child argv elements, a valid smoke line, and the parsed
   fields.
3. Prove the zero-exit/no-smoke-line failure contract and the 500-character
   clipping of both child output streams.  Assert `ok` is false despite exit
   code zero and that no smoke fields are invented.
4. Run the focused `topos/tests/test_acceptance.py -q` command in
   `tester-unified`, self-review all three oracles/scope, commit only the two
   allowed files, and leave the branch unmerged.

## BLOCKED rule

If preserving the real subprocess boundary requires product-code edits, or an
oracle needs a forbidden file, write `BLOCKED: <named oracle and reason>` and
stop.
