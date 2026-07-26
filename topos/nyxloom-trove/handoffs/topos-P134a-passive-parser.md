---
schema_version: 1
id: topos-P134a-passive-parser
project: topos
title: "Cover passive DAMON parser and attribution boundaries"
tier: sonnet-medium
input_revision: "82b0fab8"
depends_on: [topos-P133-origin-coverage]
session: fresh
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/tests/test_damon_passive.py", "topos/nyxloom-trove/handoffs/topos-P134a-passive-parser.md"]
  forbid: ["topos/src/topos/damon/passive.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py", "topos/pyproject.toml"]
oracles:
  - id: O1
    observable: "A real temporary DAMON filesystem yields correct public session class/aggregate metadata at classifier and scheme-selection boundaries."
    negative: "Zero intervals, invalid regions, or an empty scheme fabricate positive bytes or select the wrong scheme."
    gate: topos-suite
  - id: O2
    observable: "A vaddr session is published only when its real target PIDs map to exactly one entity; paddr retains host-only metadata."
    negative: "Zero/multiple/unmapped targets become an entity session, or a paddr-only session becomes entity data."
    gate: topos-suite
  - id: O3
    observable: "The full tester-unified coverage JSON contains no missing parser/attribution branch among passive.py lines 56–70, 199–307, 310–375."
    negative: "Happy fixture coverage leaves DAMON parser refusal, selection, or cgroup attribution paths untested."
    gate: topos-suite
gates: [topos-suite]
escalate_if: ["a named contract cannot be met as specified", "scope requires a forbidden file"]
---

# P134a — Cover passive DAMON parser and attribution boundaries

## Context to read first

1. `topos/src/topos/damon/passive.py`: lines 47–375, especially public
   `annotate_frame_damon` and its real DAMON filesystem input shape.
2. `topos/tests/test_damon_passive.py` in full; extend its `Collector` fixtures
   rather than testing helpers directly.
3. `topos/tests/test_damon_control.py`: only `_damon_root` and
   `_add_tried_regions`, for fixture-shape conventions.
4. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

Add a compact (roughly 5–8 tests) public `Collector.collect_once()` suite using
only `tmp_path` files. Do not call or import private symbols from
`topos.damon.passive`; do not change production code.

1. Make real region files prove classifier behavior: an invalid/zero interval
   denominator has zero access rate; a sufficiently old nonzero-access region
   is cold rather than idle; non-positive-size regions do not create bytes.
   Assert published session region classes and public entity metrics.
2. Build two candidate schemes with real files. Assert `stat` wins over a
   non-stat candidate; an all-invalid/empty scheme is skipped; a valid scheme
   with no usable region uses its integer `total_bytes` fallback. Assert public
   session metadata, not helper results.
3. Through real target/cgroup/proc files, demonstrate that invalid PID text,
   no resolved target, and targets resolving to multiple entities yield no
   vaddr entity session. Demonstrate one resolved target with unavailable
   `cgroup.procs` keeps the session but reports no entity pid count.
4. Demonstrate a paddr context is host-only, including the no-usable-scheme
   paddr result; demonstrate an unsupported/missing operations file is safely
   omitted. Assert host/entity public output.

Prefer one small fixture-builder for the kernel tree and one for proc/cgroup
input; do not duplicate whole trees per test. No coverage pragmas, mocks,
subprocesses, static source-audit tests, or broad test duplication.

## Oracle

Run the full declared tester-unified gate:

```bash
docker run --rm --mount type=bind,src=/home/vb/volkb79-2/vbpub,dst=/workspaces/vbpub tester-unified:local bash -c "set -euo pipefail; cd /workspaces/vbpub/.worktrees/feat/topos-P134a-passive-parser; export PYTHONPATH=topos/src:topos; /opt/tester-venv/bin/python -m pytest topos/tests -q -n auto --cov=topos/src/topos --cov-branch --cov-report=json:/tmp/topos-coverage.json && /opt/tester-venv/bin/python topos/tools/coverage_gate.py --repo . --base main --coverage-json /tmp/topos-coverage.json --source topos/src/topos"
```

The gate must pass. Record residual `passive.py` coverage after the gate; the
listed parser/attribution branches must be exercised or explicitly identified
for the following P134b package.

## Scope / forbid

Touch only the named test and handoff. Assert public collector/frame/session
observables; direct private-helper calls/imports are forbidden.

## BLOCKED rule

If a named public contract cannot be reached without a forbidden file, STOP.
Write `BLOCKED: <specific reason>` to the handoff LOG, commit that log-only
change, and exit. Do not use private helpers or suppress coverage.
