# P99-REPORT — Close process collection and sampling coverage gaps

## Summary

All 3 targets at exact 100% statements and branches in the full xdist gate.
Parity confirmed across two runs.

## Target coverage

| Target | Before (P96) | After P99 | Status |
|--------|-------------|-----------|--------|
| collect/procs.py | 4 lines, 1 branch | 0/0 | **100%** |
| procs/procfs.py | 37 lines, 8 branches | 0/0 | **100%** |
| procs/sampler.py | 14 lines, 19 branches | 0/0 | **100%** |

## Tests added

63 tests (pytest collection count) across:
- collect/procs.py: status_values error/empty paths, list_processes ValueError
- procs/procfs.py: discovery, stat, io, status, cmdline, cgroup, boot_time, cpu_count — all error and edge cases with temp procfs
- procs/sampler.py: delta, compute_rates (TRUE+FALSE branches via degraded baselines), frame_source(), build_entity_frame (present/absent/status_unavail), sample with fake proc, omitted_reasons, warm-up FALSE branch, history eviction, ProcessCoverage, ProcessFrameSource

## Gate

| Run | Tests | Exit | Coverage JSON |
|-----|-------|------|---------------|
| Pass 1 | 1932 passed | 0 | /tmp/z1.json |
| Pass 2 | 1932 passed | 0 | /tmp/z2.json |

**Parity:** PASS — identical per-file executed/missing lines and branches.
**Targets: ALL 3 CLOSED** — empty missing_lines and missing_branches.

## Product source edits

None. All changes are tests and reports.

## Pragma/omit audit

No pragma: no cover or coverage exclusions added.
