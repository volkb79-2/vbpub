---
schema_version: 1
id: topos-P180-execution-audit-failures
project: topos
title: "Cover action execution audit failure and close contracts"
tier: luna-low
input_revision: "c41b1733"
depends_on: []
session: "fresh"
source: {kind: product-goal, ref: "nyxloom-trove/3-roadmap.md"}
scope:
  touch: ["tests/test_execution_audit_failure_boundary.py", "nyxloom-trove/handoffs/topos-P180-execution-audit-failures.md"]
  forbid: ["src/topos/actions/execute.py", "nyxloom-trove/nyxloom.toml"]
oracles:
  - id: O1
    observable: "Pre-audit unexpected write failure is normalised to the bounded typed audit error and closes the opened handle."
    negative: "A raw failure escapes, a typed audit error is replaced, or the handle remains open."
    gate: topos-suite
  - id: O2
    observable: "Post-audit failure is typed while close errors remain non-leaking cleanup, and successful post audit closes once."
    negative: "A close failure masks the audit contract or a successful path leaks a handle."
    gate: topos-suite
gates: [topos-suite]
escalate_if: ["a named contract cannot be met as specified", "scope requires a forbidden file"]
---

# P180 — execution audit failure boundary

## Context to read first

1. `src/topos/actions/execute.py`, `_write_execution_audit_pre` lines 298–329 and `_write_execution_audit_post` lines 331–362 only.
2. `tests/test_p113_execute_primitives_coverage.py`: existing execution-test fixtures and typed result terminology.
3. `tests/test_cli_action_execute.py`: contract-focused, controlled-boundary test style.
4. `nyxloom-trove/nyxloom.toml`, `[gates.topos-suite]`.

## Work

1. Add only `tests/test_execution_audit_failure_boundary.py`. Directly test the two named private boundary functions with fake file handles and monkeypatched `_open_safe_audit`/`_write_json_record`/`_audit_record`; never open a real audit path or invoke a runner.
2. Prove pre-audit: raw writer exception becomes `_AuditError("pre-audit write failed")` and closes exactly once; an already typed `_AuditError` preserves its meaning and still closes.
3. Prove post-audit: write failure becomes `_AuditError("post-audit write failed")`; close errors do not replace that result; success closes once. Use a real small `ExecuteResult` and fixed clock/identity inputs only as deterministic data.
4. Assert the observable typed exception message and closure state, not implementation call counts alone. Self-review, focus-test in tester-unified, commit only allowed files.

## Oracles

- O1: Fake raw failure must yield the documented typed pre-audit failure while `closed` is true. A raw exception or open handle is red.
- O2: Fake post write/close combinations must yield typed post failure or successful return with a closed handle. A masked result or leaked handle is red.
- Gate: tester-unified focus then declared `topos-suite`, never cockpit Python.

## Test constraints

- No real audit filesystem path, subprocess, sleep, clock oracle, or global-state leak.
- Patch owning module boundaries and restore via `monkeypatch`.
- No coverage exclusions or `no cover` text.

## Scope / forbid

Only the named test and handoff may change; no product/gate/existing-test edits. Work in assigned worktree/branch.

## BLOCKED rule

If a named contract cannot be proven through stated seams, or a forbidden file is needed, STOP; write `BLOCKED: <reason>` to the LOG, commit it, and exit. Do not improvise.
