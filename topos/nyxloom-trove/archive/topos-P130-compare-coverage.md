---
schema_version: 1
id: topos-P130-compare-coverage
project: topos
title: "Complete compare input and serialization coverage"
tier: haiku-high
input_revision: "7e9117b6"
depends_on: [topos-P129-paddr-lifecycle-coverage]
session: "resume:topos-compare-coverage"
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/src/topos/compare.py", "topos/tests/test_compare.py", "topos/nyxloom-trove/handoffs/topos-P130-compare-coverage.md"]
  forbid: ["topos/src/topos/compare.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py", "topos/pyproject.toml"]
oracles:
  - id: O1
    observable: "Malformed P88-shaped compare inputs fail as CompareError and typed refusal outcomes serialize as null-valued JSON rather than coercing data."
    negative: "Invalid input is accepted, boolean numbers are treated as numeric, or a refusal loses its typed null fields."
    gate: topos-suite
  - id: O2
    observable: "Unsupported semantics, non-finite rules, pretty output, optional assertion serialization, and pass/breach reasons all produce the documented public result."
    negative: "The compare CLI/library silently accepts an unsupported/non-finite condition or JSON omits assertion evidence."
    gate: topos-suite
  - id: O3
    observable: "The full tester-unified xdist coverage JSON has no missing line or branch for compare.py."
    negative: "A focused test is green but one of compare.py's input or serializer branches remains unexecuted."
    gate: topos-suite
gates: [topos-suite]
escalate_if: ["a named contract cannot be met as specified", "scope requires a forbidden file"]
---

# P130 — Complete compare input and serialization coverage

## Context to read first

1. `topos/src/topos/compare.py`: lines 96–137, 238–247, 300–321, 350–369,
   and 427–439.
2. `topos/tests/test_compare.py`: builders at the top and the existing typed
   outcome / rule-evaluation tests.
3. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

Append focused public-API tests only, reusing `_summary_result`, `_row`, and
`_gauge_cell`. Do not import private helpers.

1. Serialize a refused/redacted comparison with `compare_to_jsonable` and
   assert all scalar delta fields are `None` plus its reason survives. This
   proves typed missing values are JSON-safe, not merely a helper call.
2. `compare_summaries` must raise `CompareError` for: a non-dict top-level
   current input; a `meta` which is not a mapping; rows which are not a list;
   and a list row missing `key` or `metrics`. Assert an identifying message.
3. Two `state_duration` cells must yield the public unsupported-semantic
   outcome; a boolean p95 in baseline must yield missing-baseline rather than
   numeric zero/one coercion.
4. `compare_to_jsonable` with rule results must serialize both a passing rule
   (no `reason` key) and a breached rule (a `reason` beginning `breached:`),
   preserving `assertions`. `format_compare(..., pretty=True)` must parse as
   the same JSON and contain indentation/newlines.
5. `parse_compare_rule("a.scope:ram:delta<=1e309")` must raise CompareError
   mentioning finite: the parser accepts exponent syntax but rejects infinity.

Each assertion must use public `compare_summaries`, `compare_to_jsonable`,
`format_compare`, `parse_compare_rule`, and `evaluate_compare_rules`; no mocks
or private-helper direct tests.

## Oracle

Run the full declared gate in `tester-unified`:

```bash
docker run --rm --mount type=bind,src=/home/vb/volkb79-2/vbpub,dst=/workspaces/vbpub tester-unified:local bash -c "set -euo pipefail; cd /workspaces/vbpub/.worktrees/feat/topos-P130-compare-coverage; export PYTHONPATH=topos/src:topos; /opt/tester-venv/bin/python -m pytest topos/tests -q -n auto --cov=topos/src/topos --cov-branch --cov-report=json:/tmp/topos-coverage.json && /opt/tester-venv/bin/python topos/tools/coverage_gate.py --repo . --base main --coverage-json /tmp/topos-coverage.json --source topos/src/topos"
```

The full gate must pass and `compare.py` must have empty `missing_lines` and
`missing_branches` in its coverage JSON.

## Scope / forbid

Touch only the named source, test, and handoff. The sole permitted production
change is removing `_round`'s unreachable `None` branch: all its call sites
already guard absent values, so its input/output type is `float -> float`.
Do not alter comparison semantics, coverage tooling/configuration, or dependencies.

## BLOCKED rule

If any named public behavior cannot be reached without a forbidden file, STOP.
Write `BLOCKED: <specific reason>` to the handoff LOG, commit that log-only
change, and exit. Do not add a coverage pragma or test a private helper.
