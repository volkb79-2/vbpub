# run-gate-WAVE-RG55-P7 — assay B091 — LOG

One entry per commit on the `assay-liveness` branch, self-hash rule: each
entry names the hash of the commit it documents (recorded once that commit
exists, in a small follow-up entry-only commit).

## Entries

### `de32bb91` — A1: `budget_per_candidate = "auto"` default (D-23)

- Files: `src/assay/{config,mutation,verdict,runner,cli}.py`,
  `src/assay/schemas/verdict.schema.json`, `docs/CONSUMERS.md`,
  `CHANGES.md`, `nyxloom-trove/4-backlog.md`, 6 test files.
- Tests-first: yes (`test_mutation_progress_budget_plan.py`'s new cases
  written against `run_mutation`'s new `budget_per_candidate_auto` kwarg
  before the kwarg existed; the two config-loader tests rewritten to name
  the new expected behavior before the loader changed).
  Full oracle → test mapping: REPORT.md, A1 section.
- Gate: not yet run (targeted diff-coverage self-check only — see
  REPORT.md). Full gate deferred to A6 per the handoff's own "mutation
  lane last, one at a time" ordering.
- Self-review found and fixed one real bug before this commit (WARN/
  "none"-detection conflation crashing `parse_duration("none")` when
  `diagnostics=None`) — see REPORT.md for detail and the regression test.

### `f649a249` — test fix: stray leftover assertion in the A1 regression test

- `tests/test_runner_run_lane_r2.py` only. The new `diagnostics=None`
  regression test (added in `de32bb91`) inherited a trailing
  `assert "B090" in warning` line from the test above it (an imprecise
  Edit match) — `NameError`, not a real failure; caught by actually running
  the file, not by trusting the diff. Production code (already committed)
  needed no change.
