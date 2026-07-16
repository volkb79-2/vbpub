# P19 — ZRAM and swap-backend awareness

**Cut:** v1.5 stabilization. **Depends:** P12 preferred. Branch:
`feat/topos-p19-zram-swap-backends`. Follow `topos/README.md` workflow
protocol.

## Goal

Make compressed swap reporting correct on zswap, zram, disk-only, and mixed
hosts. Users should see the active backend state in the banner before
interpreting per-cgroup swap/refault columns.

## Scope — in

1. Host backend detection:
   - parse `/proc/swaps`;
   - detect active `/dev/zram<N>` swap devices;
   - classify `none`, `zswap_only`, `zram_only`, `disk_only`, `zswap_zram`,
     `zswap_disk`, `mixed`, or `unknown`.
2. ZRAM host metrics:
   - parse `/sys/block/zram<N>/mm_stat`, `io_stat`, and optional `bd_stat`;
   - aggregate host totals for logical bytes, compressed bytes, memory used,
     ratio, allocator efficiency, limits/peaks, same pages, huge pages,
     failures, and writeback bytes.
3. Registry/model:
   - add metrics with clear source labels and units;
   - preserve existing frame compatibility where possible;
   - do not invent per-cgroup zram compression metrics.
4. UI wording:
   - update banner to show active backend state;
   - make `swap_disk`/`rf_d` drill-down text backend-aware;
   - on zram-only hosts, avoid physical-disk wording.
5. Fixtures/tests:
   - add procfs/sysfs fixtures for zram-only, disk-only, zswap+zram, and mixed;
   - cover malformed/missing zram stat files;
   - cover banner text and JSON output.
6. Documentation:
   - update `docs/COMPRESSED-SWAP.md`, `docs/STATUS.md`, and handoff report if
     implementation reveals better metric names.

## Scope — out

- Writing to zram sysfs.
- ZRAM tuning, recompression, reset, writeback control, or swapon/swapoff.
- Per-cgroup ZRAM compression attribution.
- BPF/network work.
- Daemon work.

## Acceptance

- `python3 -m pytest topos/tests -q` passes.
- `python3 -m py_compile` passes for `topos/src`.
- `topos --once --json` includes backend classification and host ZRAM metrics
  when fixture/live sources provide them.
- Replay/UI smoke still passes.
- Handoff report includes the exact semantics used for mixed-backend hosts.

## Notes

- Kernel cgroup v2 exposes total cgroup swap (`memory.swap.current`) and
  zswap-specific cgroup counters (`memory.zswap.current`,
  `memory.stat:zswapped`), but not per-cgroup zram compression ratios.
- Keep implementation small and additive. If renaming `swap_disk` to a clearer
  metric risks broad churn, add an alias or improve labels first and propose the
  larger rename in the report.
