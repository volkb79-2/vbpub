# Compressed Swap Backends

This document is the canonical policy for zswap, zram, disk swap, and mixed
swap setups. It complements `TUI-SPEC.md`; implementation work should update
both when semantics change.

## Product Default

`topos` must show the active swap backend state up front. A host can have zswap,
one or more zram devices, and real disk swap active at the same time, so the UI
must not imply that every non-zswap byte is on a physical disk.

Preferred banner shape:

```text
SWAP backends zswap:on zram:1.2G/3.8G disk:8.0G active:mixed
```

Where space is tight, collapse to `swap:mixed` plus a drill-down line.

## Backend Classification

The collector should classify host state from `/proc/swaps` and sysfs:

| State | Meaning | UI wording |
|---|---|---|
| `none` | No active swap devices and no zswap pool. | `swap:none` |
| `zswap_only` | zswap is active and no swap device has non-zswap usage. | `zswap` |
| `zram_only` | Active swap devices are zram devices only. | `zram` |
| `disk_only` | Active swap devices are non-zram devices only. | `disk swap` |
| `zswap_zram` | zswap plus active zram swap devices. | `zswap+zram` |
| `zswap_disk` | zswap plus active non-zram swap devices. | `zswap+disk` |
| `mixed` | zram and non-zram swap devices are active together, with or without zswap. | `mixed` |
| `unknown` | Sources are unreadable or inconsistent. | `swap:?` with degraded source |

## Host ZRAM Metrics

ZRAM is exposed as RAM-backed block devices named `/dev/zram<N>` with
per-device stats under `/sys/block/zram<N>/`. For each initialized device, v1.5
should collect:

| Metric | Source | Meaning |
|---|---|---|
| `host_zram_orig_bytes` | `mm_stat:orig_data_size` | Uncompressed logical data stored in zram. |
| `host_zram_compr_bytes` | `mm_stat:compr_data_size` | Compressed payload bytes. |
| `host_zram_mem_used_bytes` | `mm_stat:mem_used_total` | Actual memory consumed, including metadata/fragmentation. |
| `host_zram_ratio` | `orig_data_size / compr_data_size` | Logical compression ratio, unavailable when compressed size is zero. |
| `host_zram_efficiency` | `compr_data_size / mem_used_total` | Allocator efficiency, unavailable when memory used is zero. |
| `host_zram_mem_limit_bytes` | `mm_stat:mem_limit` | Device memory cap. |
| `host_zram_mem_used_max_bytes` | `mm_stat:mem_used_max` | Peak consumed memory. |
| `host_zram_same_pages` | `mm_stat:same_pages` | Same-filled pages needing no allocation. |
| `host_zram_huge_pages` | `mm_stat:huge_pages` | Incompressible pages. |
| `host_zram_failed_reads` / `host_zram_failed_writes` | `io_stat` | Device-level errors. |
| `host_zram_writeback_bytes` | `bd_stat:bd_count * 4096` | Backing-device writeback, if configured. |

The first implementation may aggregate devices into host totals and expose
per-device detail in drill-down later.

Implemented v1.5 host metrics use these frame names:

- `host_swap_backend` with codes: `0 none`, `1 zswap_only`, `2 zram_only`,
  `3 disk_only`, `4 zswap_zram`, `5 zswap_disk`, `6 mixed`, `7 unknown`.
- `host_zram_swap_devices` and `host_disk_swap_devices` for active swap devices
  from `/proc/swaps`.
- `host_zram_device_count` for visible `/sys/block/zram*` devices.
- The `host_zram_*` byte/count/ratio metrics listed above, aggregated across
  visible zram block devices.
- `host_disk_swap` remains the compatibility metric name but is now non-zram
  disk-device usage, not zram-backed swap usage.

## Per-Cgroup Semantics

Cgroup v2 exposes total cgroup swap usage as `memory.swap.current`. It also
exposes zswap-specific cgroup data through `memory.zswap.current` and
`memory.stat:zswapped`.

There is no kernel file that attributes zram compressed bytes, zram physical
memory cost, or zram compression ratio to an individual cgroup. Therefore:

- Per-cgroup `z_pool`, `z_eq`, and `ratio` remain zswap-only.
- The canonical metric key `swap_disk` (alias `swap_dev`) means "non-zswap
  swap-device usage estimate".
- On `zram_only` hosts, that estimate is logical zram-backed swap, not disk IO.
- On `disk_only` hosts, it is a disk-swap estimate.
- On `mixed` hosts, the backend for a given cgroup is unknown; show the value
  with a mixed/estimated source label and explain it in drill-down.
- Never fabricate per-cgroup zram compression ratios from host totals.

## Alias Reference

The following user-facing aliases are accepted in configured column profiles
and resolve silently to the canonical metric key:

| Alias | Canonical key | Display label |
|---|---|---|
| `swap_dev` | `swap_disk` | `SWAP_DEV` |
| `rf_dev_per_s`, `rf_dev`, `rf_d` | `rf_d_per_s` | `RF_DEV/S` |

Legacy canonical keys (`swap_disk`, `rf_d_per_s`, `rf_d`) continue to work.
The alias layer is cosmetic only — recorded frame JSON, threshold config keys,
and diagnostic config keys are unaffected.

## Refault Wording

`rf_z/s` remains zswap refaults. `rf_d/s` currently means anonymous refaults
that did not come from zswap. On zram-only and mixed hosts, user-facing text
should avoid claiming those refaults are definitely physical disk IO.

Preferred wording:

- `rf_z/s`: zswap refault rate.
- `rf_dev/s` or legacy `rf_d/s`: non-zswap anonymous refault rate.
- Drill-down text: "backend is disk, zram, or mixed according to host swap
  classification; cgroup backend attribution is unavailable."

## Scope Boundaries

v1.5 should implement read-only detection, metrics, banner/drill-down wording,
fixtures, and tests. It should not tune zram, trigger recompression, configure
writeback, reset devices, or write to zram sysfs.

Per-device drill-down is implemented as structured host metadata
(`Frame.host_meta["zram_devices"]`) and rendered in the host-memory screen.
See `handoff/reports/P23-REPORT.md`.

Primary sources:

- Linux kernel ZRAM admin guide:
  <https://docs.kernel.org/admin-guide/blockdev/zram.html>
- Linux kernel cgroup v2 memory controller docs:
  <https://docs.kernel.org/admin-guide/cgroup-v2.html>

## ZFS ARC and Memory Accounting

On a ZFS host, the Adaptive Replacement Cache (ARC) can hold many GB of RAM
that `MemAvailable` counts as *unavailable* — the ARC is a kernel-managed
memory pool that automatically evicts under pressure, so it behaves more like
reclaimable cache than like anonymous memory. topos's memory banner therefore
annotates ARC usage when `/proc/spl/kstat/zfs/arcstats` is present, showing
`ARC <size>/<max> (hit <pct>%)` so the operator can see how much of the
reported "used" memory is reclaimable ARC.

ZFS ARC is a read-only host provider. The kernel does not attribute ARC memory
to individual cgroups, and topos never attempts to fabricate per-cgroup ARC
attribution. The five `host_zfs_arc_*` metrics (size, target, max, min, hit
ratio) are host-scope only. The hit ratio is computed as a rate over the sample
interval from the cumulative `hits`/`misses` counters, not as a lifetime
cumulative ratio. See `handoff/reports/P71-REPORT.md`.
