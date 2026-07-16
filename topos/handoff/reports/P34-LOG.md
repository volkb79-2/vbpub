# P34 Work Log

## Context

- Branch: feat/topos-p34-host-device-banner
- Worktree: .worktrees/-topos-p34-host-device-banner
- Base commit: 3965ab4 (docs(topos): carve P34 P35 next slices)
- Package: P34 - Host device banner
- Current objective: Add host-level per-device network and block-device rate summaries to the system banner via host_meta.

## Timeline

```text
2026-07-10 CEST
- Action: Created branch feat/topos-p34-host-device-banner and worktree .worktrees/-topos-p34-host-device-banner from local main.
- Commands: git worktree add -b feat/topos-p34-host-device-banner .worktrees/-topos-p34-host-device-banner main
- Files changed: (none yet)
- Result: Worktree ready at 3965ab4.
- Follow-up: Implement host device counter parsing, collector rate computation, banner rendering, and tests.

2026-07-10 CEST
- Action: Added _net_dev_counters() and _block_dev_counters() to host.py; updated collect_host_meta() to include them.
- Files changed: topos/src/topos/collect/host.py
- Result: Raw net/block device counter collection implemented. Excludes veth*, br-*, docker*, lo and loop*, ram*, zram*.
- Follow-up: Collector rate computation, banner rendering.

2026-07-10 CEST
- Action: Added _prev_device_counters field and _apply_host_device_rates() to Collector; updated collect_once().
- Files changed: topos/src/topos/collect/collector.py
- Result: Device rate computation from raw counter deltas works. First frame gets None rates (collecting).
- Follow-up: Banner NET/DISK rendering lines.

2026-07-10 CEST
- Action: Added _host_device_lines(), _net_device_line(), _block_device_line() to banner.py for NET/DISK rendering.
- Files changed: topos/src/topos/ui/banner.py
- Result: Banner shows busiest 2-3 devices per category with byte/packet rates, or "collecting..." on first frame, or "n/a" when absent.
- Follow-up: Tests, validation, commit.

2026-07-10 CEST
- Action: Wrote 14 host device tests, 8 banner/round-trip tests, updated 2 P23 tests, updated golden fixture.
- Files changed: topos/tests/test_host_device.py, topos/tests/test_ui_banner.py, topos/tests/test_p23_zram_drilldown.py, topos/tests/fixtures/frames/gstammtisch-once.jsonl
- Result: All 322 tests pass; py_compile clean.
- Follow-up: Commit feature branch.

2026-07-10 CEST
- Action: Updated docs/STATUS.md to note per-device network/disk banner lines implemented (P34).
- Files changed: topos/docs/STATUS.md
- Result: Banner status now reflects per-device lines.
- Follow-up: Commit, REPORT.md.
```
