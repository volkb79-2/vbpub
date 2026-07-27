---
schema_version: 1
id: topos-P170-cli-compare-dispatch
project: topos
title: "Specify compare CLI loading, rules, rendering, and status"
tier: luna-low
input_revision: "a31445f6"
depends_on: [topos-P169-cli-query-dispatch]
session: resume cli
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/tests/test_cli_compare_dispatch.py", "topos/nyxloom-trove/handoffs/topos-P170-cli-compare-dispatch.md"]
  forbid: ["topos/src/topos/cli.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py"]
oracles:
  - id: O1
    observable: "`topos compare` parses only its declared JSON contract, loads current and baseline summaries with labels, forwards optional metrics and parsed assertions, and renders the formatted comparison exactly once."
    negative: "A required JSON flag or malformed option reaches comparison, a file's role is swapped, metrics/assertions are lost, invalid rules are evaluated, or the result renders twice/not at all."
    gate: topos-suite
  - id: O2
    observable: "Missing, unreadable, malformed-JSON, and comparison/rule errors become exit 2 with actionable diagnostics; assertion results use compare_exit_code while a no-rule informational comparison exits 0."
    negative: "A file or comparison error escapes/succeeds, a malformed document loses its role/path, failed assertions return 0, or informational output incorrectly invokes the assertion exit policy."
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "an oracle needs a production-code edit or a file outside the two allowed paths"
---

# P170 — Specify compare CLI loading, rules, rendering, and status

## Context to read first

1. `topos/src/topos/cli.py`, only `parse_compare_args` and `_main_compare`
   (roughly lines 1315–1390).
2. `topos/tests/test_cli_query_dispatch.py` for direct CLI boundary style.
3. `topos/tests/test_compare.py`, only public result/rule contracts needed to
   avoid mocking comparison behavior incorrectly.
4. `topos/nyxloom-trove/nyxloom.toml`, `[gates.topos-suite]`.

## Work

1. Add exactly one focused direct test module; do not modify production code.
2. Use temporary summary files or narrow `Path.read_text` seams to prove both
   loaded JSON values and optional `--metric` values reach `compare_summaries`
   in the correct current/baseline order. Exercise `--assert` parsing, rule
   evaluation, `--pretty`, exact formatted output, and `compare_exit_code`.
3. Prove a no-rule informational comparison returns 0 and does not invoke the
   assertion-exit boundary; prove nonempty assertion results return exactly the
   patched exit sentinel.
4. Cover declared errors: parser misuse, missing file, unreadable file,
   malformed current/baseline JSON, `compare_summaries` failure, and invalid
   assertion rule. Assert exit 2, meaningful stderr, and no formatter/evaluator
   after the failed boundary. Do not edit or call the unit under test through a
   subprocess.
5. Run the focused module in `tester-unified` under
   `--cgroup-parent=nyxloom-gates.slice`, self-review for hollow assertions and
   scope, commit only the allowed files, and leave the branch unmerged.

## BLOCKED rule

If an oracle needs a production-code edit or a forbidden file, write
`BLOCKED: <named oracle and reason>` and stop.
