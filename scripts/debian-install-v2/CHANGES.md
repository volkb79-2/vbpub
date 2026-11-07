# Changelog

All notable changes to debian-install-v2 are recorded here.

## [Unreleased]

### Changed (BREAKING, div2-data-area-v2): one explicit `partitioning` plan, partition numbers follow disk position
- Live finding 2026-11-05 (v1001): data partition numbered 8 although it sat before swaps 4-7, and a 166.8 GiB hole between root
  and data (rule-sized `dev-host`: root 5/16, data 1/3, rest "reserve").
- NEW `partitioning` config object: `root` (`"*"`/`"keep"`/GiB/`"N%"`), `reserve_gb` (0 default, number or `"*"`), optional `data`
  (`kind: partitions|lvm`, volumes with `size`/`fs`/`mount`/`label`, LVM `vg`/`pv_size`/`vg_free_gib`). `iobench_raw_gb` defaults to
  an always-planned 8 GiB raw partition and accepts 2..10240 or `false`. Exactly one `"*"` takes the remainder; the plan must account for
  the whole disk or it is refused with the resolved table/fix. Design, algorithm and exact messages:
  `docs/DESIGN-data-area-v2.md`; README "Disk layout".
- Numbering follows physical order: after root, data partitions first (config order), then `iobench-raw`, then swaps; ascending
  partition numbers match ascending start sectors. The raw partition is never formatted or mounted and is the only install benchmark target.
- Root behaviour derives from `partitioning.root`; `root_resize_policy` is removed with no plan-less path. A plan accepts `swap_policy`
  `on`/`off` (not `untouched`).
- LVM: LV sizes GiB / percent of the VG (`-l` extents) / `"*"` (`-l 100%FREE`, created last); `vg_free_gib` keeps VG space.
  Raw partitions (`fs: none`, optional `type: zfs`) are planned but never formatted. A mount holding `/var/lib/docker` requires its
  mount for `docker.service`. Allowlist: `lvcreate -l`.
- REMOVED, no migration: `disk_layout`, `root_max_size_gb`, `data_area`, `data_partition`, `data_lvm` (a config with one is
  refused with a pointer to `partitioning`), and the rule sizing (`recommended_root_gib`, 1/3 data, `min_root_gib` floor).
  Migration by hand: the old `dev-host` + runner-created CoW layout is
  `{"partitioning": {"root": 160, "data": {"kind": "partitions", "volumes": [{"fs": "btrfs", "mount": "/srv/cow", "label": "MDT_COW"}]}}}`
  (data takes the remainder before the raw partition and swap); old capped-root-only settings must be rewritten as an explicit plan.
- Deferred: `fs: zfs` as a managed filesystem, btrfs `subvol` (both refused with a clear message).
- Fixed root/data sizes cannot mix with percentage sizes; a numeric fixed reserve can accompany a percentage pool. The offline resolver
  accepts `--root-start-sector` and `--root-number` (netcup: `2500608`, `3`). Config load and both planners enforce GPT's 128-entry limit.
- Mount refusals name the protected OS-state path. Exact `/`, `/var`, `/var/lib`, `/srv`, and `/home` mounts are refused, as are mounts
  covering `/usr` (except `/usr/local` and its descendants), `/etc`, `/boot`, `/root`, `/tmp`, `/proc`, `/sys`, `/dev`, `/run`, `/bin`,
  `/sbin`, `/lib`, `/var/lib/dpkg`,
  `/var/lib/apt`, `/var/cache/apt`, `/var/log`, `/srv/tree-cache`, or `/srv/tree-upper`; dedicated data children such as
  `/var/lib/docker`, `/var/lib/containerd`, `/srv/cow`, `/home/runner`, and `/usr/local` remain allowed.
- Tests cover boundary behavior with swap on/off, raw partition geometry and wipe ordering, PARTLABEL-only recognition, static invalid
  plans, protected and allowed mounts, offline root geometry, and the GPT partition-number limit. `test_data_area_vm.py` remains in `r1-vm-real-commit`.

### Fixed (div2-data-area-v2 review): one GPT-safe pool boundary and raw benchmark allocation
- The swap planner and data planner now share the same exclusive pool end. Swap-off plans reserve backup-GPT metadata and remain
  contiguous, aligned, and within the last usable sector.
- `run_io_benchmark` no longer creates a temporary tail partition, formats/mounts it, or writes a temporary table. The installer wipes
  and runs iobench against the planned `iobench-raw` partition, which stays in the final table.
- `show_plan`, the install summary, wizard summary, and refusal output describe the raw reservation; the resolved plan table is shown
  in GiB when disk facts suffice.
- Stage two runs `iobench` on the planned raw partition before configuring io.cost, so its benchmark result is available to the model.
- All root growth now updates only the root partition with `sfdisk -N <root-number>`, refreshes it with `partx -u`, and verifies the
  exact planned size. This applies to every current plan; the plan-less `root_resize_policy` path has been removed.

### Tests / hardening (div2-layout-tests)
- Every fake or mocked run path in the installer tests now validates its argv through the production
  `HostActions._validate` (`tests/argv_guard.py`), so allowlist drift fails unit tests. This is the
  class of bug behind the 2026-11-05 v1001 stage-two failure (`sfdisk -N` / `--no-tell-kernel`).
- `tests/test_layout_argv_coverage.py`: an AST scan validates every literal installer argv (executed
  or not), and a stateful walk of the 512 GiB plan (root growth, four tail swaps, MDT_COW, raw
  iobench partition) runs through the validator.
- `tests/test_data_area_vm.py::test_real_dev_host_layout_stage_two_with_root_growth_swap_and_cow` (existing lane `r1-vm-real-commit`): real stage-two steps on a loop disk with the
  production allowlist: sfdisk -N + partx + resize2fs exact growth, mkswap/swapon at the disk end,
  mkfs.btrfs MDT_COW + fstab + mount.
- Allowlist: `mkswap` (-L), `resize2fs` (-P), `blkid` (-s -o -t -U -L -p) now have flag allowlists;
  `dd` operands are limited to if/of/bs/count/conv.

### Superseded predecessor waves

The earlier NC-12/NC-13 interim disk layouts and runner-side partition creation
were superseded by the single `partitioning` plan described above. Their
removed config fields, rule-based sizing and partition-carve instructions are
not part of the current installer contract. The optional wizard manifest is
`requirements-wizard.txt`.

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
### Fixed
- Remote bootstrap imports the config validator from freshly fetched source
  even when no profile is selected.
