# P19 Report

## What changed

- Added host swap-backend classification from `/proc/swaps` plus zswap state:
  `none`, `zswap_only`, `zram_only`, `disk_only`, `zswap_zram`,
  `zswap_disk`, `mixed`, and `unknown`.
- Added host ZRAM metrics by parsing `/sys/block/zram*/mm_stat`, `io_stat`,
  and optional `bd_stat`, aggregated across visible zram devices.
- Added registry entries for backend classification, active swap device counts,
  and host ZRAM byte/count/ratio/error/writeback metrics.
- Updated the host banner from a zswap/disk-only line to a backend-aware swap
  line showing zswap, zram, disk-device usage, and active device counts.
- Updated compressed-swap and operations docs with implemented metric names and
  the per-cgroup attribution caveat.
- Added synthetic `/proc`/`/sys` tests for zram-only, mixed, malformed stats, and
  banner rendering.

## Semantics for mixed-backend hosts

- `host_swap_backend=mixed` when active zram and non-zram swap devices are both
  present, regardless of zswap state.
- Per-cgroup `swap_disk` remains the compatibility metric name and is still the
  non-zswap swap-device estimate from cgroup files. It does not identify whether
  a cgroup's non-zswap pages reside on zram or disk when the host is mixed.
- `host_disk_swap` now means estimated non-zram disk-device swap usage, so
  zram-only hosts no longer show zram usage as disk swap.

## Deviations from handoff

- Per-device ZRAM drill-down was not added. P19 aggregates host totals in the
  banner/JSON; per-device detail can be a later drill-down polish slice.
- No metric rename from `swap_disk`/`rf_d` was attempted. User-facing wording and
  registry glossaries were corrected while preserving frame compatibility.

## Proposed contract changes

- None.

## Tests run

```bash
# /tmp/vbpub-groop-p13-venv/bin/python -m pytest groop/tests -q
# 93 passed in 14.59s

# find groop/src -name '*.py' -print0 | xargs -0 /tmp/vbpub-groop-p13-venv/bin/python -m py_compile
# (no output)

# PYTHONPATH=groop/src /tmp/vbpub-groop-p13-venv/bin/python -m groop.cli --once --json --cgroup-root groop/tests/fixtures/cgroupfs/gstammtisch
# schema_version=1 entities=8 host_metrics=36 backend=[5, 'host']

# PYTHONPATH=groop/src /tmp/vbpub-groop-p13-venv/bin/python -m groop.cli --replay groop/tests/fixtures/frames/gstammtisch-once.jsonl --step --ui-smoke
# ui smoke ok frames=1 view=tree profile=auto
```

## Known gaps / open items

- Per-device ZRAM detail is not rendered yet.
- Cgroup rows still use legacy `swap_disk`/`rf_d` metric names. A future
  compatibility-aware alias could make the names match the backend-aware
  semantics more directly.
