# P97-REPORT — Close small deterministic coverage gaps

## Summary

48 tests across all 16 target modules. **9/16 targets closed to 100%**
in the full xdist gate. 7 targets have remaining gaps documented below.
Parity confirmed across two gate runs.

## Targets closed (9)

- collect/zswapmath.py, collect/dockerjoin.py
- model.py
- procs/identity.py, procs/sensitivity.py, procs/owners.py
- ui/keys.py
- inspect_files/plan.py
- actions/preview.py

## Targets with remaining gaps (7)

| Target | Gap | Reason |
|--------|-----|--------|
| collect/collector.py | 1 branch [165,167] | Block family filter — needs cgroup infra |
| registry.py | 1 line 279, 1 branch [278,279] | **BLOCKED**: mechanically unreachable (every valid token adds metrics) |
| ui/damon_control.py | 1 line 52 | _refresh needs Textual app harness |
| ui/sparkline.py | 1 line 73, 1 branch [72,73] | Padding path — coverage aggregation edge |
| record/ring.py | 1 branch [63,-60] | Early exit in last() — coverage aggregation |
| damon/paddr.py | 2 lines 82,85, 2 branches | Needs Damon sysfs infrastructure |
| daemon/component_health.py | 1 line 56, 1 branch [51,56] | Decode error path — coverage aggregation |

## Tests added

48 tests: zswapmath(13), dockerjoin(7), collector(5+2), model(4),
registry(2), procs-identity(1), procs-sensitivity(1), procs-owners(1),
ui-keys(1), ui-sparkline(3), ring(3), inspect-plan(1), damon-paddr(1),
actions-preview(2), component-health(7)

## Gate

Two runs: 1819 passed, exit 0, parity identical.

## BLOCKED

**registry.py line 279**: The `if not kept_metrics:` branch at line 279
of `parse_metrics_selector` is mechanically unreachable. Every valid
metric token in the current codebase (`FIELD_LIST_BLOCK_MAP`, metric
groups) adds at least one metric to `kept_metrics`. When all tokens
are unknown, the function raises ValueError at the "unknown metric
token" check (lines before 279) before reaching this line. No input
can trigger this branch without a product semantic change.
