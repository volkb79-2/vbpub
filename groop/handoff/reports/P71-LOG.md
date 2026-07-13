# P71 Work Log

Keep this file current while working. It is a resumability artifact, not a
place for private reasoning. Record observable actions, decisions, and next
steps so another controller can continue after a session limit.

## Context

- Branch: feat/groop-p71-zfs-arc-provider
- Worktree: .worktrees/groop-p71-zfs-arc-provider
- Base commit: main (local)
- Package: P71
- Current objective: Implement ZFS ARC host provider

## Timeline

Append newest entries at the bottom.

```text
2026-07-13 12:00 UTC
- Action: Read handoff P71-zfs-arc-provider.md, explored codebase (host.py, registry.py, banner.py, collector.py, model.py, test patterns).
- Files read: handoff, collect/host.py, registry.py, banner.py, collector.py, model.py, cgroup.py, test_host_swap.py, test_p23_zram_drilldown.py, test_ui_banner.py, CONTRACTS.md, ARCHITECTURE.md, COMPRESSED-SWAP.md, ROADMAP.md, STATUS.md, README.md
- Result: Full understanding of patterns to follow (P19/P23 host_zswap_* pattern, collector rate machinery, banner conditional lines).
- Follow-up: Implement.

2026-07-13 12:05 UTC
- Action: Added 5 host_zfs_arc_* metrics to registry.py following host_zswap_* pattern.
- Commands: PYTHONPATH=groop/src python3 -c "from groop.registry import REGISTRY; print([k for k in REGISTRY if 'zfs' in k])"
- Files changed: src/groop/registry.py
- Result: 5 metrics registered: host_zfs_arc_size, host_zfs_arc_target, host_zfs_arc_max, host_zfs_arc_min, host_zfs_arc_hit_ratio. Registry assertion passes.
- Follow-up: Implement collection function.

2026-07-13 12:10 UTC
- Action: Added _zfs_arc_metrics(), _parse_arcstats(), _zfs_arc_compute_hit_ratio(), _zfs_arc_detail(), reset_zfs_arc_rate_state() to collect/host.py. Integrated into collect_host() and collect_host_meta().
- Files changed: src/groop/collect/host.py
- Result: Present/missing ZFS, malformed kstat all handled correctly. Hit ratio computed as rate over interval with module-level state. Raw kstat fields exposed in host_meta["zfs_arc"].
- Follow-up: Write tests.

2026-07-13 12:20 UTC
- Action: Created fixture file tests/fixtures/procfs/zfs/arcstats with realistic values (12GB ARC size, 32GB max, ~97% hit ratio).
- Files changed: tests/fixtures/procfs/zfs/arcstats
- Result: Fixture ready for oracles 1, 4, 5.
- Follow-up: Write test file.

2026-07-13 12:25 UTC
- Action: Wrote test_zfs_arc.py with all 6 acceptance oracles. Ran tests, fixed two issues: (1) missing-size test expectation corrected (only size degrades, not all fields), (2) banner annotation added to banner.py.
- Files changed: tests/test_zfs_arc.py, src/groop/ui/banner.py
- Result: 11 tests pass covering all oracles. `--once --json` confirms non-ZFS host works correctly.
- Follow-up: Update docs, write LOG/REPORT, commit.

2026-07-13 12:40 UTC
- Action: Updated ARCHITECTURE.md, ROADMAP.md, STATUS.md, README.md, COMPRESSED-SWAP.md.
- Files changed: docs/ARCHITECTURE.md, groop/README.md, docs/ROADMAP.md, docs/STATUS.md, docs/COMPRESSED-SWAP.md
- Result: All docs reflect ZFS ARC as implemented.
- Follow-up: Write LOG, REPORT, commit.

2026-07-13 12:45 UTC
- Action: Wrote P71-LOG.md and P71-REPORT.md.
- Files changed: groop/handoff/reports/P71-LOG.md, groop/handoff/reports/P71-REPORT.md
- Result: Reports ready. Commit pending.
- Follow-up: Run full test suite, py_compile, git diff --check, commit.
```

## Decisions

- Decision: Use module-level state for ARC hit-ratio rate computation rather than per-entity-key delta mechanism in collector.
  Reason: ZFS ARC is a host-level metric, not per-cgroup. The collector's _delta() uses EntityKey tuples which don't naturally map to host-level state. Module-level state with a reset function is simpler and testable.
  Impact: Tests must reset state via autouse fixture. Works correctly across multiple collector sweeps.

- Decision: Expose raw kstat fields in host_meta["zfs_arc"].
  Reason: Contract 7 allows it, provides drill-down capability without new Frame fields.
  Impact: host_meta grows when ZFS is present; consumers tolerate absence.

## Self-Review Findings (pass #1, 2026-07-13)

### Finding 1: Hollow test `test_zfs_arc_banner_absent`
The test only checked that "ARC" was absent from the banner. If the ARC metric
collection were deleted/stubbed, the banner would also lack "ARC" and the test
would still pass — it verified nothing about the collection code actually
running.

**Fix:** Added explicit assertions that `host_zfs_arc_size` exists in the host
dict with `v=None, src="unavail_kernel"` before checking the banner. Now if the
collection code were deleted, the test would fail with KeyError.

### Finding 2: Duplicate assertion in `test_zfs_arc_banner_absent`
Two identical `assert "ARC" not in lines` lines were present (artifact of the
edit). Removed the duplicate.

### Gate commands — fresh output (not reconstructed)
```
$ PYTHONPATH=groop/src python3 -W ignore -m pytest groop/tests/test_zfs_arc.py -q -W ignore -v
groop/tests/test_zfs_arc.py ...........                                  [100%]
11 passed

$ python3 -m py_compile groop/src/groop/collect/host.py groop/src/groop/registry.py groop/src/groop/ui/banner.py
exit: 0

$ git diff --check
exit: 0
```

### Scope check
All 12 changed files are under `groop/`. No files outside `groop/**` touched.

### Hollow-test check
`test_zfs_arc_banner_absent` was the only hollow test. Fixed. All other tests
call the ARC collection functions directly (`_zfs_arc_metrics` or
`_zfs_arc_compute_hit_ratio`) or access `host["host_zfs_arc_*"]` which would
raise KeyError if the collection were deleted.

### Absent-ZFS degrade path
`test_zfs_arc_absent_fixture_all_unavail` tests the actual code path by calling
`_zfs_arc_metrics()` on a path with no ZFS file. It asserts `v is None`
explicitly (not falsy, which would pass for `0`) and `src == "unavail_kernel"`.
The `test_zfs_arc_non_zfs_fixtures_unaffected` test validates the same through
the full `collect_host()` integration path.

## Blockers

None.

## Validation

```bash
PYTHONPATH=groop/src python3 -W ignore -m pytest groop/tests/test_zfs_arc.py -q -W ignore
# 11 passed
PYTHONPATH=groop/src python3 -W ignore -m pytest groop/tests/test_host_swap.py groop/tests/test_p23_zram_drilldown.py groop/tests/test_ui_banner.py groop/tests/test_zfs_arc.py groop/tests/test_collector.py -q -W ignore
# 47 passed
PYTHONPATH=groop/src python3 -m groop.cli --once --json
# Works on non-ZFS host; ZFS metrics show unavail_kernel
python3 -m py_compile groop/src/groop/collect/host.py groop/src/groop/registry.py groop/src/groop/ui/banner.py
# Clean
```

## Handoff Checklist

- [x] Report file written.
- [x] Log file current.
- [x] Tests/compile/smoke recorded.
- [x] Known gaps documented.
- [x] Feature branch committed.