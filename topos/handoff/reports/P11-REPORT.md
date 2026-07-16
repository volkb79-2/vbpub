# P11 Report

## What was built

- Added host paddr DAMON defaults under `[damon]`:
  - `paddr_sample_us = 400000`
  - `paddr_aggr_us = 8000000`
  - `paddr_update_us = 1000000`
- Added host paddr registry metrics:
  - `host_damon_{hot,warm,cold,idle}_bytes`
  - `host_damon_{hot,warm,cold,idle}_pct`
  - `host_damon_sample_age_s`
  - `host_damon_mode`
- Extended passive DAMON parsing so paddr sessions classify host physical DRAM
  heat into `Frame.host` while staying out of per-entity metrics.
- Added owner detection for passive DAMON sessions using topos ownership
  markers; unmarked sessions render as `foreign`.
- Added `topos.damon.paddr` for controlled topos-owned paddr session start,
  reusing P9 root checks, free-slot selection, ownership markers, sysfs writes,
  and audit logging.
- Added `topos damon paddr start --confirm START`; existing
  `topos damon stop --all-mine` tears down the owned paddr session because it
  uses the shared marker path.
- Added banner `DRAM HEAT` rendering when host paddr metrics are present.
- Added a host-memory status screen on `m`, with paddr session parameters,
  owner, region histogram bars, overhead note, and start-control planning text.
- Added tests for paddr passive classification, topos vs. foreign ownership,
  paddr control start/refusal/duplicate behavior, CLI start, banner heat output,
  host-memory status rendering, and TUI navigation.

## Safety evidence

- Automated tests use only temp DAMON roots and fixture cgroup/proc trees.
- Paddr start defaults to root-required and requires typed `START` confirmation.
- Start refuses busy kdamond slots and refuses duplicate topos-owned paddr
  sessions for the same DAMON root.
- Stop still operates only on topos marker files and leaves unmarked foreign
  sessions untouched.
- Passive paddr detection reads sysfs only; it emits host metrics and root
  metadata, never per-entity paddr attribution.

## Deviations / gaps

- Real-host root acceptance was not executed in this session; no live
  `/sys/kernel/mm/damon` mutation was performed.
- The Textual start control currently presents the planned root-only start and
  confirmation text from the host-memory screen. Applying the typed
  confirmation from a full modal remains a follow-up UI refinement; the
  underlying control API and CLI start path are implemented and tested.
- Auto-start / `paddr_enabled` config remains out of scope for v1.5 as required
  by the handoff.

## Validation

```bash
PYTHONPATH=/tmp/topos-pytest:/tmp/vbpub-topos-p11-damon-paddr/topos/src python3 -m pytest topos/tests -q
# 79 passed in 11.17s
```

```bash
PYTHONPATH=/tmp/vbpub-topos-p11-damon-paddr/topos/src python3 -m py_compile $(find topos/src/topos -name '*.py' | sort)
# clean
```

```bash
PYTHONPATH=/tmp/vbpub-topos-p11-damon-paddr/topos/src python3 -m topos.cli --once --json --cgroup-root topos/tests/fixtures/cgroupfs/gstammtisch
# once 1 8
```

```bash
PYTHONPATH=/tmp/topos-pytest:/tmp/vbpub-topos-p11-damon-paddr/topos/src python3 -m topos.cli --replay topos/tests/fixtures/frames/gstammtisch-once.jsonl --step --ui-smoke
# ui smoke ok frames=1 view=tree profile=auto
```

## Known open items

- Execute live root acceptance on the reference host: start paddr, wait roughly
  two aggregation windows, verify the banner heat bar, stop, and prove foreign
  sessions remain untouched.
- Replace the host-memory screen's start-control notice with a full Textual
  typed-confirmation modal that calls `start_planned_paddr_session` when run as
  root against an intentional DAMON root.
