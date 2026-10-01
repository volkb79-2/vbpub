---
schema_version: 1
id: topos-P135-tree-coverage
project: topos
title: "Complete public tree-renderer coverage"
tier: luna-low
input_revision: "fa7bc325"
depends_on: [topos-P134b-passive-degradation]
session: fresh
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/tests/test_ui_tree.py", "topos/nyxloom-trove/handoffs/topos-P135-tree-coverage.md"]
  forbid: ["topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py", "topos/pyproject.toml", "topos/src/topos/ui/app.py", "topos/src/topos/ui/table.py"]
oracles:
  - id: O1
    observable: "The public tree renderers expose a stable hierarchical order, ancestor-preserving filter result, collapsed-child suppression, and matching indentation/glyph semantics."
    negative: "A renderer that drops matching descendants or ancestors, shows a collapsed child, or treats a leaf as expandable passes only a source-shaped test."
    gate: topos-suite
  - id: O2
    observable: "Public Rich-table and DataTable tree outputs retain selection/no-selection semantics, display profile columns and labels, and render the empty-tree sentinel."
    negative: "A renderer that leaks a table selection marker into DataTable, loses profile metadata, or emits no usable empty row is accepted."
    gate: topos-suite
  - id: O3
    observable: "The full tester-unified coverage JSON has no missing line or branch for ui/tree.py."
    negative: "Untested ordering/filtering/cell branches remain a global-coverage hole."
    gate: topos-suite
gates: [topos-suite]
escalate_if: ["a named contract cannot be met as specified", "scope requires a forbidden file"]
---

# P135 — Complete public tree-renderer coverage

## Context to read first

1. `topos/src/topos/ui/tree.py` in full.
2. `topos/src/topos/ui/table.py`: `resolve_profile`, `format_metric_value`,
   `metric_sort_value`, and `header_label`.
3. `topos/src/topos/model.py`: `Entity`, `EntityFrame`, `Frame`, and
   `MetricValue` dataclasses.
4. `topos/tests/test_ui_table.py` in full, as the small real-model fixture
   style to follow.
5. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

Create `topos/tests/test_ui_tree.py`. Test only the two public renderers
(`render_tree_table` and `render_data_table_tree`) with real `Frame`/
`EntityFrame` values and their public Rich/DataTable-shaped output. Do not
import or call private helpers, mock the renderer, inspect source, or add
coverage suppressions.

1. Build one compact parent/child/sibling frame with distinguishable names and
   metrics. Prove public tree output orders name ascending by default and
   numeric metrics descending by default, honors an explicit reverse override,
   preserves an ancestor for a matching descendant filter, and renders a
   collapsed parent without its child when no filter is active. Assert row keys
   and visible first-cell text/glyphs rather than private traversal tuples.
2. Prove Rich-table rendering marks exactly the selected row, includes the
   profile/ignored-column title information for a configured custom profile,
   and emits `no rows` for an empty frame.
3. Prove DataTable rendering returns public column keys/labels, has no Rich
   selection marker, includes the hierarchical prefix, honors the explicit
   `sort_reverse` value, and uses the `__empty__`/`no rows` sentinel for an
   empty frame.
4. Reach 100% statements and branches for `ui/tree.py` through those public
   renderer contracts. Keep the suite concise and fixture-driven. Do not edit
   production code merely to make coverage easier; if a genuinely unreachable
   defensive branch is found, STOP under the BLOCKED rule rather than removing
   it speculatively.

## Oracle

Run the full declared tester-unified gate:

```bash
docker run --rm --mount type=bind,src=/home/vb/volkb79-2/vbpub,dst=/workspaces/vbpub tester-unified:local bash -c "set -euo pipefail; cd /workspaces/vbpub/.worktrees/feat/topos-P135-tree-coverage; export PYTHONPATH=topos/src:topos; /opt/tester-venv/bin/python -m pytest topos/tests -q -n auto --cov=topos/src/topos --cov-branch --cov-report=json:/tmp/topos-coverage.json && /opt/tester-venv/bin/python topos/tools/coverage_gate.py --repo . --base main --coverage-json /tmp/topos-coverage.json --source topos/src/topos"
```

The gate must pass; `topos/src/topos/ui/tree.py` must have empty
`missing_lines` and `missing_branches` in the coverage JSON.

## Scope / forbid

Touch only the named test and handoff. Tests must exercise public renderer
behavior; private-helper imports/calls, coverage suppressions, source audits,
and production cleanup are forbidden.

## BLOCKED rule

If the named public contracts cannot be reached without a forbidden file,
STOP. Write `BLOCKED: <specific reason>` to the handoff LOG, commit that
log-only change, and exit. Do not fabricate coverage with private-helper tests
or a suppression.
