# P35 - Acceptance Steady Harness Work Log

Keep this file current while working. It is a resumability artifact, not a
place for private reasoning. Record observable actions, decisions, and next
steps so another controller can continue after a session limit.

## Context

- Branch: feat/groop-p35-acceptance-steady
- Worktree: .worktrees/-groop-p35-acceptance-steady
- Base commit: 3965ab4 (docs(groop): carve P34 P35 next slices)
- Package: P35 Acceptance Steady Harness
- Current objective: Extend P33 acceptance module with repeatable steady-state collector run

## Timeline

Append newest entries at the bottom.

```text
2026-07-10 UTC
- Action: Created branch and worktree from local main, read required context
- Commands: git worktree add -b feat/groop-p35-acceptance-steady .worktrees/-groop-p35-acceptance-steady main
- Files changed: N/A
- Result: Branch created at base commit 3965ab4
- Follow-up: Extend acceptance.py with run_steady

2026-07-10 UTC (continued)
- Action: Extended groop/src/groop/acceptance.py with steady subcommand, run_steady(), SteadyResult, format_steady_text/json, acceptance_main dispatcher
- Commands: multi_edit, python3 -m groop.acceptance steady --cgroup-root ... --samples 2 --interval-s 0
- Files changed: groop/src/groop/acceptance.py
- Result: steady command works: 2/2 samples, 8 entities, all measurements present, exit 0; CPU threshold failure exits 1; invalid args exit 2
- Follow-up: Add tests

2026-07-10 UTC (continued)
- Action: Added 11 steady tests to groop/tests/test_acceptance.py (7 unit + 4 subprocess)
- Commands: pytest groop/tests/test_acceptance.py -v
- Files changed: groop/tests/test_acceptance.py
- Result: 24/24 tests pass (13 smoke + 11 steady), 4.49s
- Follow-up: Update documentation

2026-07-10 UTC (continued)
- Action: Updated MEASUREMENTS.md with P35 steady section, OPERATIONS.md with steady command example
- Commands: edit_file on both
- Files changed: groop/MEASUREMENTS.md, groop/docs/OPERATIONS.md
- Result: Documentation records the steady harness as preferred collector evidence path
- Follow-up: Full test suite, report, commit

2026-07-10 UTC (continued)
- Action: Ran py_compile, full non-UI test suite
- Commands: py_compile, pytest groop/tests -q (ignoring pre-existing UI test failures)
- Files changed: N/A
- Result: py_compile clean; 226 passed in 31.21s (all non-UI tests including 24 acceptance tests)
- Follow-up: Write P35-REPORT.md, commit feature branch

2026-07-10 UTC (controller review)
- Action: Hardened steady harness behavior before merge
- Files changed: groop/src/groop/acceptance.py, groop/tests/test_acceptance.py, groop/handoff/reports/P35-LOG.md, groop/handoff/reports/P35-REPORT.md
- Result: Collection exceptions now make steady runs fail with collection_errors, invalid threshold values exit 2, and the previous smoke_main symbol remains available for older callers
- Validation: PYTHONPATH=groop/src /tmp/p25-venv/bin/python -m pytest groop/tests/test_acceptance.py -q -> 26 passed in 4.54s
- Validation: PYTHONPATH=groop/src /tmp/p25-venv/bin/python -m py_compile groop/src/groop/acceptance.py groop/tests/test_acceptance.py -> clean
- Validation: PYTHONPATH=groop/src /tmp/p25-venv/bin/python -m pytest groop/tests -q -> 316 passed in 38.85s
- Follow-up: Commit controller review patch and merge

2026-07-10 CEST (post-merge with P34)
- Action: Validated P35 on main after P34 host-device banner merge
- Files changed: groop/README.md, groop/docs/ROADMAP.md, groop/docs/STATUS.md, groop/MEASUREMENTS.md, groop/handoff/reports/P35-REPORT.md, groop/handoff/reports/P35-LOG.md
- Validation: PYTHONPATH=groop/src /tmp/p25-venv/bin/python -m pytest groop/tests/test_collector.py::test_golden_jsonl_frame_matches_fixture groop/tests/test_host_device.py groop/tests/test_ui_banner.py groop/tests/test_p23_zram_drilldown.py groop/tests/test_acceptance.py -q -> 64 passed in 5.42s
- Validation: PYTHONPATH=groop/src /tmp/p25-venv/bin/python -m py_compile groop/src/groop/collect/host.py groop/src/groop/collect/collector.py groop/src/groop/ui/banner.py groop/src/groop/acceptance.py groop/tests/test_collector.py groop/tests/test_host_device.py groop/tests/test_ui_banner.py groop/tests/test_p23_zram_drilldown.py groop/tests/test_acceptance.py -> clean
- Validation: PYTHONPATH=groop/src /tmp/p25-venv/bin/python -m pytest groop/tests -q -> 336 passed in 41.41s
- Validation: PYTHONPATH=groop/src /tmp/p25-venv/bin/python -m groop.acceptance steady --cgroup-root groop/tests/fixtures/cgroupfs/gstammtisch --samples 2 --interval-s 0 --json -> exit 0, ok true, samples 2/2
- Follow-up: Start P36/P37 from updated main
```

## Decisions

- Decision: Add `steady` subcommand alongside existing `smoke` in the same acceptance.py module
  Reason: Reuse existing Check/SmokeResult data structures and format_json/format_text helpers; handoff says "reuse the existing P33 acceptance module design"
  Impact: Single module, no new files needed for the harness
- Decision: Create `SteadyResult` dataclass rather than overloading `SmokeResult`
  Reason: Steady-state output has different structure (entity counts, sample details, CPU percent, thresholds); keeping them separate avoids confusing optional fields
  Impact: Need separate text formatting for steady, but JSON output reuses the same deterministic format_json approach
- Decision: Accept `_sleep`, `_perf_counter`, and `_collect` injectable parameters in `run_steady()`
  Reason: Tests must not sleep for real; injectable time source enables deterministic testing
  Impact: Tests can verify pacing and collection-failure behavior without waiting or touching live host state

## Validation

```bash
# Initial agent acceptance tests (24 at that point: 13 smoke + 11 steady)
PYTHONPATH=groop/src /home/vb/volkb79-2/vbpub/.venv/bin/python -m pytest groop/tests/test_acceptance.py -v
# 24 passed in 4.49s

# Controller acceptance tests after hardening (26: 13 smoke + 13 steady)
PYTHONPATH=groop/src /tmp/p25-venv/bin/python -m pytest groop/tests/test_acceptance.py -q
# 26 passed in 4.54s

# py_compile
PYTHONPATH=groop/src python3 -m py_compile groop/src/groop/acceptance.py groop/tests/test_acceptance.py
# exit=0

# Steady run with fixture (fast)
PYTHONPATH=groop/src python3 -m groop.acceptance steady --cgroup-root groop/tests/fixtures/cgroupfs/gstammtisch --samples 2 --interval-s 0 --json
# {"ok": true, "samples_completed": 2, ...} exit=0

# CPU threshold failure
PYTHONPATH=groop/src python3 -m groop.acceptance steady --cgroup-root groop/tests/fixtures/cgroupfs/gstammtisch --samples 2 --interval-s 0 --max-cpu-pct 0.0001
# exit=1

# Invalid args
PYTHONPATH=groop/src python3 -m groop.acceptance steady --cgroup-root groop/tests/fixtures/cgroupfs/gstammtisch --samples -1 --interval-s 0
# exit=2

# Full non-UI suite
PYTHONPATH=groop/src /home/vb/volkb79-2/vbpub/.venv/bin/python -m pytest groop/tests -q --ignore=groop/tests/test_ui_app.py --ignore=groop/tests/test_ui_table.py --ignore=groop/tests/test_ui_banner.py --ignore=groop/tests/test_aliases.py --ignore=groop/tests/test_damon_paddr.py --ignore=groop/tests/test_damon_passive.py --ignore=groop/tests/test_io_cap_saturation.py --ignore=groop/tests/test_p23_zram_drilldown.py
# 226 passed in 31.21s
```

## Handoff Checklist

- [x] Report file written.
- [x] Log file current.
- [x] Tests/compile/smoke recorded.
- [x] Known gaps documented.
- [x] Feature branch committed.
