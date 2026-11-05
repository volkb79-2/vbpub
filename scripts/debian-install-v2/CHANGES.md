# Changelog

All notable changes to debian-install-v2 are recorded here.

## [Unreleased]

### Changed (BREAKING, div2-data-area-v2): one explicit `partitioning` plan, partition numbers follow disk position
- Live finding 2026-11-05 (v1001): data partition numbered 8 although it sat before swaps 4-7, and a 166.8 GiB hole between root
  and data (rule-sized `dev-host`: root 5/16, data 1/3, rest "reserve").
- NEW `partitioning` config object: `root` (`"*"`/`"keep"`/GiB/`"N%"`), `reserve_gb` (0 default, number or `"*"`), optional `data`
  (`kind: partitions|lvm`, volumes with `size`/`fs`/`mount`/`label`, LVM `vg`/`pv_size`/`vg_free_gib`). Exactly one `"*"` takes the
  remainder; the plan must account for the whole disk or it is refused with the fix computed. Design, algorithm and exact messages:
  `docs/DESIGN-data-area-v2.md`; README "Disk layout".
- Numbering: after root, data partitions first (config order), swaps last; ascending numbers == ascending start sectors. The data
  partition is no longer numbered after the swaps.
- Root behaviour derives from `partitioning.root` (`"keep"` = untouched); `root_resize_policy` is refused together with a plan.
  A plan accepts `swap_policy` `on`/`off` (not `untouched`).
- LVM: LV sizes GiB / percent of the VG (`-l` extents) / `"*"` (`-l 100%FREE`, created last); `vg_free_gib` keeps VG space.
  Raw partitions (`fs: none`, optional `type: zfs`) are planned but never formatted. A mount holding `/var/lib/docker` requires its
  mount for `docker.service`. Allowlist: `lvcreate -l`.
- REMOVED, no migration: `disk_layout`, `root_max_size_gb`, `data_area`, `data_partition`, `data_lvm` (a config with one is
  refused with a pointer to `partitioning`), and the rule sizing (`recommended_root_gib`, 1/3 data, `min_root_gib` floor).
  Migration by hand: old `dev-host` + `data_area: partition` (root 160, data 168, 166 GiB hole) is
  `{"partitioning": {"root": 160, "data": {"kind": "partitions", "volumes": [{"fs": "btrfs", "mount": "/srv/cow", "label": "MDT_COW"}]}}}`
  (data takes the rest); old `root_max_size_gb: N` alone is `{"partitioning": {"root": N, "reserve_gb": "*"}}`.
- Deferred: `fs: zfs` as a managed filesystem, btrfs `subvol` (both refused with a clear message).
- Tests: `test_layout_policy.py` (schema, pool/LV planners, property tests), `test_data_area.py` (numbering, sfdisk plan, shrink
  path, resume, stage-two commands), `test_data_area_vm.py` extended (existing lane `r1-vm-real-commit`).

### Tests / hardening (div2-layout-tests)
- Every fake or mocked run path in the installer tests now validates its argv through the production
  `HostActions._validate` (`tests/argv_guard.py`), so allowlist drift fails unit tests. This is the
  class of bug behind the 2026-11-05 v1001 stage-two failure (`sfdisk -N` / `--no-tell-kernel`).
- `tests/test_layout_argv_coverage.py`: an AST scan validates every literal installer argv (executed
  or not), and a stateful walk of the 512 GiB dev-host plan (gap-branch root growth, four tail swaps,
  MDT_COW btrfs) runs through the validator.
- `tests/test_data_area_vm.py::test_real_dev_host_layout_stage_two_with_root_growth_swap_and_cow` (existing lane `r1-vm-real-commit`): real stage-two steps on a loop disk with the
  production allowlist: sfdisk -N + partx + resize2fs gap growth, mkswap/swapon at the disk end,
  mkfs.btrfs MDT_COW + fstab + mount.
- Allowlist: `mkswap` (-L), `resize2fs` (-P), `blkid` (-s -o -t -U -L -p) now have flag allowlists;
  `dd` operands are limited to if/of/bs/count/conv.

### Added (NC-13 data area)
- `data_area = none | partition | lvm` (+ `data_partition`, `data_lvm`): the installer
  creates the middle data partition itself, directly in front of the end-of-disk
  swap, in the same sfdisk transaction: one partition with btrfs/ext4/xfs, or one
  LVM PV with a VG and LVs (`{name, size_gb, fs, mount, label}`, free VG space
  kept, `fs: none` leaves an LV unformatted for ZFS or other use). The 512 GiB
  numbers (root 160, cow 168, swap 16, reserve) are the default
  `data_area=partition`. Needs swap_policy=on, root_resize_policy=resize and a
  capped root; mount collisions, oversized LVs and unfitting areas are refused.
  `lvm2`/`btrfs-progs`/`xfsprogs` are installed when needed. New allowlist entries:
  `mkfs.btrfs`, `mkfs.xfs`, `pvcreate`, `vgcreate`, `vgchange`, `lvcreate`, `lvs`,
  `vgs`, `pvs`. Netcup: `cow-carve.sh`/`provision-runner.sh` detect an
  installer-created cow (partition or LV labelled `MDT_COW`) and skip carving.

### Fixed (data area review F1-F4)
- A rerun can no longer delete, renumber or resize a data-area partition that holds a
  signature (e.g. after changing the swap partition count): the installer refuses with
  a clear error before any table write.
- `blkid` failures other than exit 2 are no longer treated as "blank device" (fail
  closed for mkfs/pvcreate decisions).
- Partition mode writes `UUID=` into fstab and refuses when another device carries the
  cow label (ambiguous label with `nofail` would silently leave the cow on root).
- `data_partition.label` must be `MDT_COW`; `/srv/tree-cache` and `/srv/tree-upper` are
  refused as data-area mounts; `-f` removed from the `mkfs.btrfs`/`mkfs.xfs` allowlist.
  There is deliberately no wipe override: a stale foreign signature stops the install
  (manual remedy in the reinstall runbook).

### Breaking changes (NC-12 disk layout policy)
- **Old capped configs are refused.** A `root_max_size_gb` below `min_root_gib`
  (20% of the disk, clamp 8..100 GiB: 100 GiB on 512 GiB, 40 GiB on 200 GiB)
  is now a plan-time error. The 2026-11-04 bundles (`root_max_size_gb: 40` on a
  512 GiB disk) no longer install. Migration: delete `root_max_size_gb` and set
  `"disk_layout": "dev-host"` (root 160 GiB on 512 GiB), or raise the cap to at
  least the minimum. An installer ref from before this change keeps the old
  behaviour (pin the bundle's branch/commit to it if you must re-create the old layout).
- **Swap moves to the end of the disk also for `root_resize_policy=untouched` /
  `grow-only`** (with `swap_policy=on`): a gap appears between root and swap
  where swap used to be adjacent to root. Nothing is formatted in the gap.
- **Full-root minimum:** with `swap_policy=on` and `root_resize_policy=resize`
  a root that the installer shrinks below `min_root_gib` for swap is refused
  (a 20 GiB disk with 16 GiB swap used to produce a 4 GiB root). An untouched
  or not-shrunk root is never judged.
- `_grow_root_to_plan` is crash-safe: it compares the kernel's partition size
  with the table, re-runs `partx -u` when they differ, and records
  `root_resize: success` only when the ext4 filesystem fills the partition.

### Fixed
- Disk layout policy (NC-12): swap is always placed at the END of the disk,
  also when `root_max_size_gb` caps root (the cap no longer forces swap directly
  after root, which put swap in the middle on r1002/v1001 and boxed in root and
  the cow partition). The planner refuses plans where a non-swap partition
  follows swap, where more than 2 MiB follows the last swap, or where a capped
  root is below `min_root_gib` (20% of the disk, 8..100 GiB). Stage-two growth
  resizes root to exactly the plan when unallocated space follows it.

### Added
- `disk_layout` config key (`full-root` default, `dev-host`): `dev-host` sizes root
  by rule (5/16 of the disk, 160 GiB on 512 GiB), leaves the cow area (1/3 of the
  disk) and reserve unallocated between root and swap; wizard field and
  `show_plan` entry. New module `layout_policy.py`, tests
  `tests/test_layout_policy.py`, README "Disk layout policy".
- `scripts/netcup/runner/provision-runner.sh` carves the cow partition from the
  largest free row (`COW_SIZE_GIB`, default 1/3 of the disk), at the end of the gap;
  the row must hold the whole cow (>= `COW_MIN_GIB`, default 20), the partition is
  labelled `MDT_COW` (PARTLABEL), re-runs find it by label (never a second cow,
  independent of the partition number), a stale signature in the carved range is
  refused unless `COW_WIPE_STALE=1`. Decisions live in `runner/cow-carve.sh`
  (tests: `scripts/netcup/tests/test_cow_carve.py`).

### Changed
- A configured `root_max_size_gb` now installs cloud-init and initramfs root
  growth guards after stage one validates the capped tail-swap plan and before
  it installs the initramfs shrink hook and rebuilds images, then sets up stage
  two and reboots; stage two repeats them.
- Root caps are measured as partition lengths from the current root start, so
  absolute 1 MiB alignment no longer lowers the configured limit.
- Rename the optional wizard dependency manifest to `requirements-wizard.txt`
  and update its installer hint, CLI surface, and adoption documentation.

### Added
- Installer step `run_cpu_benchmark` (HB-2, default on, `vanilla` profile off): at the end of stage two, after io.cost and the swap
  health gate and before the apt timers are released, runs the shared `tools/cpu_benchmark.py` once while the host is idle,
  passing BOTH pins (`--geekbench-version`, `--sha256`) from `tools/cpu_benchmark.pins.json` (x86_64: Geekbench 7.1.0) or the config pair
  `cpu_benchmark_geekbench_version` / `cpu_benchmark_sha256`. The tool is checked against its `.sha256` first. Failure is advisory
  (`warned`; exit 4 points to `mdt host-setup cpu-benchmark --resolve`); one summary line (scores, result URL, never the claim link) is
  appended to the `Install complete` milestone. The v1 key `run_geekbench` stays refused, now with a pointer to `run_cpu_benchmark`.
  `bootstrap-remote.py` fetches the tool, its pin and the pins file; README, DESIGN-GUIDE and CONSUMERS aligned; tests in
  `debian_install_v2/tests/test_cpu_benchmark_step.py`. Live check: backlog MDT-066 (mdt-cockpit).
- `tools/cpu_benchmark.py` (HB-1, sha256-pinned in `tools/cpu_benchmark.py.sha256`): shared stdlib tool, logic lifted
  from the v1 `geekbench_runner.py` and generalised over the Geekbench major (6 or 7, default newest = 7 via CDN probing;
  records `tool_major`, per-major normalisation baselines, refuses to mix majors), that runs
  Geekbench once per host and writes `/var/lib/mdt/host-facts/cpu-benchmark.json` (0644, scores, CPU, conditions,
  result URL) and `cpu-benchmark.claim.json` (0600, claim link). Scores from the CLI output, else the result page
  (ported from the v1 runner); `--resolve` re-reads the page without running; refuses to re-measure without `--force`
  and under CPU pressure; hermetic tests in mdt-cockpit `host-setup/tests/test_cpu_benchmark.py`. The installer step
  and `mdt host-setup cpu-benchmark` follow (HB-2, HB-3).
- `root_max_size_gb` caps the ext root partition and leaves space after root
  and managed swap unallocated (wizard field, plan line, and user docs). A cap
  requires managed tail-swap partitions directly after root so boot-time and
  stage-two growth cannot consume it.

### Fixed
- Remote bootstrap imports the config validator from freshly fetched source
  even when no profile is selected.
- Root caps that `untouched` or `grow-only` cannot satisfy are refused before
  the swap-fit check, with the cap named in the error.
