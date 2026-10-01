---
schema_version: 1
id: topos-P131-redaction-malformed-coverage
project: topos
title: "Complete redaction malformed-shape coverage"
tier: haiku-high
input_revision: "00fd72a2"
depends_on: [topos-P130-compare-coverage]
session: "resume:topos-redaction-coverage"
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/tests/test_p81_redaction_enforcement.py", "topos/nyxloom-trove/handoffs/topos-P131-redaction-malformed-coverage.md"]
  forbid: ["topos/src/topos/daemon/redaction.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py", "topos/pyproject.toml"]
oracles:
  - id: O1
    observable: "All five public redaction payload shapes fail closed or safely preserve malformed containers without throwing, while real sensitive values remain marked below the configured ceiling."
    negative: "A malformed nested shape crashes, leaks an above-ceiling value, or causes the visitor to ignore a valid sibling value."
    gate: topos-suite
  - id: O2
    observable: "The full tester-unified xdist coverage JSON has no missing line or branch for daemon/redaction.py."
    negative: "Only the happy-path frontend test is green while typed-visitor malformed-input guards are untested."
    gate: topos-suite
gates: [topos-suite]
escalate_if: ["a named contract cannot be met as specified", "scope requires a forbidden file"]
---

# P131 — Complete redaction malformed-shape coverage

Append direct public `redaction.redact_payload` tests only. These visitors are
the common enforcement boundary for both frontends; direct payload cases are
the correct way to test malformed transport shapes that a validated frontend
cannot naturally produce. Do not import/call private visitors or helpers.

## Context to read first

1. `topos/src/topos/daemon/redaction.py`, lines 109–245.
2. `topos/tests/test_p81_redaction_enforcement.py`, especially the existing
   direct `redact_payload` tests and imports.
3. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

Use `Sensitivity.PUBLIC` and a metadata map in which `secret` is sensitive and
`public` is public. Assert both preservation/redaction outcomes, not merely no
exception. Cover these exact public contracts:

1. An ENTITY_FRAME whose `metrics` is non-dict, `findings` is non-list, and a
   second ENTITY_FRAME whose findings list contains a non-dict must not crash;
   a finding with string `source_metrics` must keep its message/remedy, while a
   finding with an above-ceiling list must redact remedy even when message is
   absent.
2. A FRAME with non-dict `host`, non-dict `entities`, and a dict entities map
   containing a non-dict sibling plus a valid entity sibling must retain
   malformed values and redact the valid sibling's `secret` metric. This proves
   the valid sibling is not skipped by malformed data.
3. MCP_OVERVIEW: missing/non-list rows is a safe no-op; mixed rows (non-dict,
   dict without `value`, public valid row, sensitive valid row) preserve public
   and redact sensitive values.
4. MCP_ENTITY: non-dict metrics/findings is safe; mixed metric entries and
   finding entries preserve a public metric, redact a sensitive metric, skip
   malformed entries, and redact the prose of a valid sensitive finding.
5. MCP_HISTORY: a public metric leaves a valid series intact; sensitive metric
   with non-list series is safe; a mixed sensitive series preserves malformed
   points and replaces only index 1 of list points of length at least two with
   the typed marker.

No mocks, source edits, coverage pragmas, or frontend changes.

## Oracle

Run the full declared gate in `tester-unified`:

```bash
docker run --rm --mount type=bind,src=/home/vb/volkb79-2/vbpub,dst=/workspaces/vbpub tester-unified:local bash -c "set -euo pipefail; cd /workspaces/vbpub/.worktrees/feat/topos-P131-redaction-malformed-coverage; export PYTHONPATH=topos/src:topos; /opt/tester-venv/bin/python -m pytest topos/tests -q -n auto --cov=topos/src/topos --cov-branch --cov-report=json:/tmp/topos-coverage.json && /opt/tester-venv/bin/python topos/tools/coverage_gate.py --repo . --base main --coverage-json /tmp/topos-coverage.json --source topos/src/topos"
```

The gate must pass and coverage JSON must have empty `missing_lines` and
`missing_branches` for `topos/src/topos/daemon/redaction.py`.

## Scope / forbid

Touch only the named test and handoff. Do not weaken fail-closed behavior or
change production redaction semantics.

## BLOCKED rule

If any named public behavior cannot be reached without a forbidden file, STOP.
Write `BLOCKED: <specific reason>` to the handoff LOG, commit that log-only
change, and exit. Do not test a private visitor or add a coverage suppression.
