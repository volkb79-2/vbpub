# P83 Self-review findings

Self-review of `feat/topos-p83-ciu-stack-grouping-tui` against the P83 handoff.

## Check 1 — Gate commands actually run and quoted in REPORT

✅ All gates were executed:
- Focused tests: `PYTHONPATH=topos/src python3 -m pytest topos/tests/test_grouping.py topos/tests/test_grouping_ui.py -q -W error -p no:schemathesis` → 33 passed
- Full suite: `timeout 900 env PYTHONPATH=topos/src python3 -m pytest topos/tests -q -W error -p no:schemathesis` → 1360 passed, 2 skipped
- py_compile: clean
- git diff --check: clean

REPORT quotes real output (exact counts and timing). Environment stated.

## Check 2 — Scope compliance

✅ All files in the diff are under `topos/`.
- `topos/src/topos/grouping.py` — new pure function module
- `topos/src/topos/ui/table.py` — grouped renderer (Textual allowed under ui/)
- `topos/src/topos/ui/app.py` — view mode cycling
- `topos/tests/test_grouping.py` — pure function tests
- `topos/tests/test_grouping_ui.py` — render artifact tests
- `topos/handoff/reports/P83-LOG.md` — log
- `topos/handoff/reports/P83-REPORT.md` — report

No new collector work, no subprocess, no `ciu` invocation. ✅

## Check 3 — Every numbered adversarial test exists and asserts observable outcome

| Oracle | Test class | Observable outcome asserted | Hollow risk |
|---|---|---|---|
| 1 | `TestOracle1NumericPhaseOrdering` | `group_entities().groups` phase order [1,2,10] — driven by grouping code, not test sorting | Low: sorting inline would produce wrong order; test asserts via `group_entities` only |
| 2 | `TestOracle2UnparseablePhaseNotZero` | Phase order: valid < unparseable < absent; None never sorts as 0 | Low: each sub-test would catch silent collapse |
| 3 | `TestOracle3UngroupedUntouched` | Zero-CIU frame returns all entities in ungrouped; entity keys preserved | Low: existing 1360-suite tests also cover `render_data_table_container` unchanged |
| 4 | `TestGroupHeaderRow` | Rendered `Text.plain` contains `(label)` vs `(inferred)` | Low: asserts on rendered text, not internal source flag |
| 5 | `TestOracle5MixedFrame` | Exact group counts and membership; no duplicate/lost entity | Low: counts and key-level assertions |

✅ No hollow tests.

## Check 4 — Dates, counts, paths in LOG/REPORT are real

✅ LOG records today's date (2026-07-13). REPORT quotes exact test counts (33, 1360, 2) and environment (Python 3.14.6). File paths are accurate.

## Check 5 — LOG, REPORT present; ASCII; no dead code/scaffolding

✅ LOG and REPORT are present and ASCII.

**Finding:** `import sys` was present but unused in `topos/src/topos/grouping.py` at commit time. Fixed in a separate commit.

No other dead code or scaffolding found.

## Summary

One finding (unused import) — fixed. All other checks pass.
