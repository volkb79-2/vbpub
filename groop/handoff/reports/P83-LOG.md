# P83 Work Log — CIU stack grouping in the TUI

## Context

- Branch: `feat/groop-p83-ciu-stack-grouping-tui`
- Worktree: `.worktrees/groop-p83-ciu-stack-grouping-tui`
- Base commit: `07891aa carve(groop): P81/P82/P83 -- blended sources; merge evidence for P67/P75/P76`
- Package: P83
- Current objective: Implement CIU stack grouping in TUI

## Timeline

```text
2026-07-13 15:55 UTC
- Action: Initial codebase exploration and requirements analysis.
- Files read: TUI-SPEC.md §4.3, CONTRACTS.md, model.py, table.py, tree.py, app.py,
  test_ciu_metadata.py, conftest.py.
- Result: Understood CiuMeta model, table/tree rendering pipeline, view-mode switching.
- Follow-up: Implement grouping.py pure function.

2026-07-13 16:00 UTC
- Action: Created groop/src/groop/grouping.py with group_entities() pure function,
  CiuGroup and GroupedEntities dataclasses.
- Decision: Phase sort order: valid numeric phases (ascending) → unparseable
  (phase_raw set, phase=None) → absent (both None). This ensures an unknown phase
  never silently sorts as 0.
- Files changed: groop/src/groop/grouping.py (new)
- Verification: py_compile clean.

2026-07-13 16:10 UTC
- Action: Added grouped view rendering to table.py and ciu-grouped view mode to app.py.
  View mode cycles: tree → container → ciu-grouped.
- Decision: Group headers show stack, phase, and source tier marker — "(label)" or
  "(inferred)". Synthetic row keys prefixed with "__group__" or "__ungrouped__".
- Files changed: groop/src/groop/ui/table.py, groop/src/groop/ui/app.py
- Verification: py_compile clean on both files.

2026-07-13 16:25 UTC
- Action: Wrote comprehensive tests for all 5 acceptance oracles.
  - test_grouping.py: pure-function tests (Oracle 1-5, plus edge cases)
  - test_grouping_ui.py: rendering helper tests (Oracle 3 row-set, Oracle 4 tier visibility)
- Files changed: groop/tests/test_grouping.py (new), groop/tests/test_grouping_ui.py (new)
- Verification: 33 focused tests pass.

2026-07-13 19:00 UTC
- Action: Ran full suite gate.
- Command: timeout 900 env PYTHONPATH=groop/src python3 -m pytest groop/tests -q -W error -p no:schemathesis
- Result: 1360 passed, 2 skipped. No regressions.
- Also: py_compile on all changed files, git diff --check — all clean.
```

## Decisions

- Decision: Phase sorting uses (group, value) tuple: valid(0, phase) < unparseable(1, 0) < absent(2, 0).
  Reason: Numeric phase ordering (phase_2 < phase_10) requires int comparison, not lexicographic.
  Unparseable and absent must be distinct states with no silent collapse to 0.
  Impact: Clear, testable sort order that matches TUI-SPEC §4.3.

- Decision: Group headers are synthetic rows with keys `__group__<stack>__<phase_raw>`.
  Reason: Simple prefix-based distinction from real entity keys; drill-down on synthetic
  keys is safely rejected by existing `self.selected_key not in frame.entities` guard.
  Impact: Operators cannot drill into a group header; they select an individual entity.

- Decision: "other containers (no CIU)" header for ungrouped entities (non-empty).
  Reason: Avoids confusion — the operator sees a clear label for non-CIU entities
  rather than raw entities interspersed without context.
  Impact: When filter text is active, ungrouped entities are shown without header
  (consistent with existing container view behavior).

## Validation

```bash
# Focused grouping tests
PYTHONPATH=groop/src python3 -m pytest groop/tests/test_grouping.py groop/tests/test_grouping_ui.py -q -W error -p no:schemathesis
# → 33 passed

# Full suite
timeout 900 env PYTHONPATH=groop/src python3 -m pytest groop/tests -q -W error -p no:schemathesis
# → 1360 passed, 2 skipped

# py_compile
python3 -m py_compile groop/src/groop/grouping.py groop/src/groop/ui/table.py groop/src/groop/ui/app.py groop/tests/test_grouping.py groop/tests/test_grouping_ui.py
# → clean

# Whitespace
git diff --check
# → clean
```

## Handoff Checklist

- [x] Report file written.
- [x] Log file current.
- [x] Tests/compile/smoke recorded.
- [x] Known gaps documented.
- [x] Feature branch committed.
