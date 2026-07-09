# P37 Network Loss Diagnostics — Work Log

## Context

- Branch: feat/groop-p37-network-loss-diagnostics
- Worktree: .worktrees/-groop-p37-network-loss-diagnostics
- Base commit: 55daa25
- Package: P37 — Network loss diagnostics
- Current objective: Add host/interface-level network drop/error visibility via host_meta, banner line, and host-scoped diagnostics.

## Timeline

```text
2026-07-23 UTC
- Action: Created branch and worktree from main.
- Commands: git worktree add -b feat/groop-p37-network-loss-diagnostics .worktrees/-groop-p37-network-loss-diagnostics main
- Files changed: (none)
- Result: Worktree ready at 55daa25.
- Follow-up: Implement changes.

2026-07-23 UTC
- Action: Extended _net_dev_counters() to parse rx_errors, rx_drop, tx_errors, tx_drop.
- Files changed: groop/src/groop/collect/host.py
- Result: Raw counters now include drop/error fields.
- Follow-up: Extended rate computation.

2026-07-23 UTC
- Action: Extended _apply_host_device_rates() for drop/error rate computation.
- Files changed: groop/src/groop/collect/collector.py
- Result: rx_errors_s, rx_drops_s, tx_errors_s, tx_drops_s computed from deltas.

2026-07-23 UTC
- Action: Updated _net_device_line() banner with LOSS annotation for non-zero drops/errors.
- Files changed: groop/src/groop/ui/banner.py
- Result: NET line shows loss info when non-zero.

2026-07-23 UTC
- Action: Added _annotate_host_network_loss() in diag/__init__.py.
- Files changed: groop/src/groop/diag/__init__.py
- Result: Host-scoped finding on root entity when drops/errors detected.

2026-07-23 UTC
- Action: Added 9 new tests + updated existing tests for new fields.
- Files changed: groop/tests/test_host_device.py, groop/tests/test_ui_banner.py, groop/tests/test_diag.py
- Result: All tests pass.

2026-07-23 UTC
- Action: Updated golden fixture for new net_devices shape.
- Files changed: groop/tests/fixtures/frames/gstammtisch-once.jsonl
- Result: Golden frame matches collector output.

2026-07-23 UTC
- Action: Updated STATUS.md diagnostics notes.
- Files changed: groop/docs/STATUS.md
- Result: Diagnostics input gap noted as partially closed.

2026-07-23 UTC
- Action: Full test suite 345 passed (up from 344), py_compile clean.
- Commands: PYTHONPATH=groop/src python3 -m pytest groop/tests -q
- Result: 345 passed in 39.28s.

2026-07-23 UTC
- Action: Report and log finalized. Focus commit made.
- Files changed: groop/handoff/reports/P37-REPORT.md, groop/handoff/reports/P37-LOG.md
- Result: Package ready for review and merge.
```

## Decisions

- Decision: Extend _net_dev_counters() with rx_drop, rx_errs, tx_drop, tx_errs.
  Reason: These fields are already in /proc/net/dev; P34 only parsed byte/packet counters.
  Impact: Net device dicts grow by 4 fields, rates get 4 new computed fields.
- Decision: Show drop/error rates in the NET banner line only when non-zero.
  Reason: Handoff requires "concise host-scope banner/status line only when loss/error rates are non-zero".
  Impact: Clean banner when healthy; informative when degraded.
- Decision: Add host-network-loss diagnostic Finding on root entity.
  Reason: No host-level findings slot exists on Frame; root entity is the canonical host-level cgroup entity.
  Impact: Diagnostics appear in the root entity findings list.

## Blockers

None.

## Validation

```text
345 passed in 39.28s
```

## Handoff Checklist

- [x] Report file written.
- [x] Log file current.
- [x] Tests/compile/smoke recorded.
- [x] Known gaps documented.
- [x] Feature branch committed.
