---
schema_version: 1
id: topos-P134b-passive-degradation
project: topos
title: "Complete passive DAMON degradation and ownership coverage"
tier: luna-low
input_revision: "4b3da63e"
depends_on: [topos-P134a-passive-parser]
session: resume:topos-passive-damon-coverage
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/src/topos/damon/passive.py", "topos/tests/test_damon_passive.py", "topos/nyxloom-trove/handoffs/topos-P134b-passive-degradation.md"]
  forbid: ["topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py", "topos/pyproject.toml", "topos/src/topos/damon/control.py"]
oracles:
  - id: O1
    observable: "Public collection safely omits an unattributable vaddr session and reports a no-scheme paddr session, invalid regions, malformed cgroup data, and missing sample files without fabricated entity data or ages."
    negative: "Partial DAMON/proc/cgroup files crash collection, create a false entity session, or claim precise metadata that cannot be read."
    gate: topos-suite
  - id: O2
    observable: "Public session ownership is `foreign` for absent/malformed/non-topos/different-root markers and `topos` only for a matching marker."
    negative: "A non-owned or malformed state marker is presented as a topos-owned DAMON session."
    gate: topos-suite
  - id: O3
    observable: "The full tester-unified coverage JSON has no missing line or branch for damon/passive.py."
    negative: "Defensive dead paths or untested degradation branches leave a global-coverage hole."
    gate: topos-suite
gates: [topos-suite]
escalate_if: ["a named contract cannot be met as specified", "scope requires a forbidden file"]
---

# P134b — Complete passive DAMON degradation and ownership coverage

## Context to read first

1. `topos/src/topos/damon/passive.py` in full, with attention to the residual
   coverage branches at lines 108–120, 141–177, 205–221, 304–320, 349–388,
   and 429–440.
2. `topos/tests/test_damon_passive.py` in full, especially P134a's `_damon_fixture`,
   `_collector_with_inputs`, and its five public Collector tests.
3. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

Extend the existing public `Collector.collect_once()` tests using only
`tmp_path` files. Do not call/private-import passive helpers and do not mock
the code under test.

1. Use a real paddr tree with no usable scheme and assert host-only public
   session metadata; use a real vaddr tree whose resolved entity key is absent
   from the collected frame and assert no fabricated entity data.
2. Through real files, cover missing/non-numeric region fields, no candidate
   scheme, malformed/irrelevant proc cgroup lines, and no readable sample
   paths. Assert safe public omission/degradation rather than helper return
   values.
3. Pass a temporary DAMON state directory through `Collector` and prove public
   `owner` is foreign for a missing/malformed/non-topos/different-root marker
   and topos only for a matching topos marker.
4. Achieve 100% statements and branches for `passive.py`. If a residual branch
   is provably unreachable from every public caller because the module itself
   constructs a typed/structured session, remove or tighten only that dead
   branch, with a concise invariant comment. Do not use coverage pragmas or
   weaken parser/ownership behavior.

Keep the added suite compact. Prefer parameterized cases where public behavior
is identical; no duplicated fixture trees, subprocesses, source-audit tests,
or broad cleanup.

## Oracle

Run the full declared tester-unified gate:

```bash
docker run --rm --mount type=bind,src=/home/vb/volkb79-2/vbpub,dst=/workspaces/vbpub tester-unified:local bash -c "set -euo pipefail; cd /workspaces/vbpub/.worktrees/feat/topos-P134b-passive-degradation; export PYTHONPATH=topos/src:topos; /opt/tester-venv/bin/python -m pytest topos/tests -q -n auto --cov=topos/src/topos --cov-branch --cov-report=json:/tmp/topos-coverage.json && /opt/tester-venv/bin/python topos/tools/coverage_gate.py --repo . --base main --coverage-json /tmp/topos-coverage.json --source topos/src/topos"
```

The gate must pass; `topos/src/topos/damon/passive.py` must have empty
`missing_lines` and `missing_branches` in the coverage JSON.

## Scope / forbid

Touch only the named source, test, and handoff. Tests must observe public
Collector/frame output; private helper calls/imports and coverage suppressions
are forbidden.

## BLOCKED rule

If the named public contracts cannot be reached without a forbidden file, STOP.
Write `BLOCKED: <specific reason>` to the handoff LOG, commit that log-only
change, and exit. Do not fabricate coverage with private-helper tests or a
suppression.
