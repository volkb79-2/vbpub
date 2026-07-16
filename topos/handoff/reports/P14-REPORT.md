# P14 Report

## What changed

- Added a reusable Textual `DamonConfirmScreen` that displays planned sysfs
  writes, requires exact `START`, and reports apply errors without dismissing.
- Replaced the entity drill-down DAMON notice with a vaddr start flow using
  `plan_start_session` and `start_planned_session`.
- Replaced the host-memory paddr notice with a paddr start flow using
  `plan_start_paddr_session` and `start_planned_paddr_session`.
- Added TUI cleanup actions that call `stop_owned_sessions(all_mine=True)`.
  Foreign DAMON sessions are never stopped because cleanup is marker-owned.
- Added fixture-only pilot coverage for vaddr confirmation, paddr start,
  duplicate paddr reporting, and stop behavior that leaves a foreign kdamond
  slot untouched.
- Updated operations docs and `MEASUREMENTS.md` with fixture evidence and a
  live-root acceptance checklist.

## Deviations from handoff

- Live-root acceptance was not run. This environment should not mutate host
  DAMON sysfs. `MEASUREMENTS.md` records the blocked status and exact evidence
  fields for a deliberate test host.
- `damon_stat` disable/restore was not implemented. The current behavior is to
  surface plan/start failures from existing APIs rather than changing foreign or
  kernel-owned sessions.

## Proposed contract changes

- None.

## Tests run

```bash
# /tmp/vbpub-topos-p13-venv/bin/python -m pytest topos/tests -q
# 87 passed in 14.15s

# find topos/src -name '*.py' -print0 | xargs -0 /tmp/vbpub-topos-p13-venv/bin/python -m py_compile
# (no output)

# /tmp/vbpub-topos-p13-venv/bin/python -m topos.cli --once --json --cgroup-root topos/tests/fixtures/cgroupfs/gstammtisch
# schema_version=1 entities=8 host_metrics=20

# /tmp/vbpub-topos-p13-venv/bin/python -m topos.cli --replay topos/tests/fixtures/frames/gstammtisch-once.jsonl --step --ui-smoke
# ui smoke ok frames=1 view=tree profile=auto
```

## Known gaps / open items

- Live-root vaddr/paddr acceptance still needs to be run on a deliberate test
  host and recorded in `MEASUREMENTS.md`.
- The TUI starts and stops topos-owned sessions but does not live-refresh the
  current frame after a successful start/stop; the next collector sample updates
  passive DAMON visibility.
- `damon_stat` conflict handling remains conservative and read-only.
