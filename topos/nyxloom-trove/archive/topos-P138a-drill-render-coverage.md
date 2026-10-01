---
schema_version: 1
id: topos-P138a-drill-render-coverage
project: topos
title: "Complete public drill-text rendering coverage"
tier: luna-low
input_revision: "15ef7498"
depends_on: [topos-P137-config-invariant]
session: resume:topos-ui-coverage
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/tests/test_ui_drill.py", "topos/nyxloom-trove/handoffs/topos-P138a-drill-render-coverage.md"]
  forbid: ["topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py", "topos/pyproject.toml", "topos/src/topos/ui/drill.py", "topos/src/topos/ui/app.py"]
oracles:
  - id: O1
    observable: "Public render_drill_text produces truthful available/unavailable DAMON, governance, network, pressure, history, findings, and process sections for real Frame/Ring/tmp-path inputs."
    negative: "Malformed or absent optional metadata crashes the drill view, fabricates a value, or suppresses an available diagnostic section."
    gate: topos-suite
  - id: O2
    observable: "Public drill text formats missing, numeric, constant/variable history, bounded percentages, byte units, session coverage, and optional remedy values consistently."
    negative: "A missing/malformed value displays as a misleading numeric value, or an out-of-range percentage produces an invalid visual bar."
    gate: topos-suite
  - id: O3
    observable: "The full tester-unified coverage JSON has no remaining render/helper line or branch hole in ui/drill.py outside DrillDownScreen control actions."
    negative: "Untested public text-degradation/formatting behavior leaves a coverage hole concealed behind private helpers."
    gate: topos-suite
gates: [topos-suite]
escalate_if: ["a named renderer contract requires a forbidden file", "remaining holes are only DrillDownScreen actions", "scope requires a forbidden file"]
---

# P138a — Complete public drill-text rendering coverage

## Context to read first

1. `topos/src/topos/ui/drill.py`: `render_drill_text` and all rendering helper
   functions from `_metric_groups` through `_bar`; do not work on
   `DrillDownScreen` actions in this package.
2. `topos/tests/test_damon_passive.py`: `test_render_drill_text_includes_damon_panel`.
3. `topos/tests/test_procs_cli.py`: temporary proc/cgroup fixture style.
4. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

Create `topos/tests/test_ui_drill.py`. Exercise only public
`render_drill_text` through real `Frame`, `EntityFrame`, `HistoryRing`, and
temporary cgroup/proc files. Do not import or call underscored helpers, mock
the renderer, inspect source, or add coverage suppressions.

1. Build compact real frames covering a normal hierarchical entity plus absent
   and malformed DAMON metadata/session entries. Assert user-visible
   availability, session coverage, region/class summaries, units, bounded bars,
   and safe placeholders rather than helper return values.
2. Assert public governance/network/pressure/history/findings/process sections
   represent optional reasons/protocols/limits, missing values, constant and
   variable history, remedy presence/absence, empty process lists, and real
   tmp-path process rows.
3. Cover all reachable rendering/helper lines and branches in `ui/drill.py`.
   Do not test `DrillDownScreen` actions here; report exactly those residuals
   for P138b. Keep fixtures parameterized and compact.

## Oracle

Run the declared tester-unified gate:

```bash
docker run --rm --mount type=bind,src=/home/vb/volkb79-2/vbpub,dst=/workspaces/vbpub tester-unified:local bash -c "set -euo pipefail; cd /workspaces/vbpub/.worktrees/feat/topos-P138a-drill-render-coverage; export PYTHONPATH=topos/src:topos; /opt/tester-venv/bin/python -m pytest topos/tests -q -n auto --cov=topos/src/topos --cov-branch --cov-report=json:/tmp/topos-coverage.json && /opt/tester-venv/bin/python topos/tools/coverage_gate.py --repo . --base main --coverage-json /tmp/topos-coverage.json --source topos/src/topos"
```

## Scope / forbid

Touch only the named test and handoff. Public rendered behavior is the oracle;
private-helper calls/imports, suppressions, source audits, and production edits
are forbidden.

## BLOCKED rule

If a named public renderer contract cannot be reached without a forbidden file,
or only screen-control action residuals remain, STOP. Write `BLOCKED: <specific
reason>` to the handoff LOG, commit that log-only change, and exit. Do not
invent a private-helper test or a suppression.
