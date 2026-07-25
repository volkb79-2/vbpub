# P107 self-review — exact network providers

- All ten tests assert complete provider results, samples, candidates, status
  dictionaries, or exact helper outputs.
- Time, stat, file-read, and internal dependency patches are context-managed;
  no target function is mocked.
- Virtual paths are never touched unless their filesystem method is patched;
  real temporary files use worker-unique `tmp_path`.
- Both source removals have an invariant proof and preserve all reachable
  aggregation behavior; no pragma or omission hides a residual.
- No substring, membership-only, selected-field, non-None, range, length-only,
  assertion-free, `pass`, sleep, host-state, expensive boundary, or mutation
  claim remains.
- Two complete xdist runs have identical normalized records and empty
  whole-file missing sets for both targets.
- Scope contains only the two target modules, P107 tests, handoff, and reports.
