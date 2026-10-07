# io.cost benchmark — design note (implemented 2026-10-06, LT-IOB; live validation pending)

Status: IMPLEMENTED as `Installer._run_io_benchmark()` in
`debian_install_v2/installer.py`, called from `_stage2()` between
`_verify_and_apply_root_shrink()` and the swap shape. It has run only against
a simulated disk (`tests/test_io_benchmark.py`); the first real run is a
controller-driven netcup test. The design below was worked out 2026-09-09;
`LT-IOB-REPORT.md` has the as-built action sequence.

## As built, and deviations from the design below

- **One write path.** The forward sfdisk/partx/settle/readback-verify block and
  the rollback block of `_apply_known_swap_shape()` were extracted unchanged
  into `_write_and_verify_partition_plan()` and `_restore_partition_table()`;
  swap placement and the benchmark both call them. `_validate_plan_geometry()`
  is untouched. The benchmark has its own small
  `_validate_benchmark_plan()` (adds exactly one partition, changes nothing,
  stays clear of the swap region).
- **Placement:** the throwaway partition sits at the TAIL of the disk, number
  `root_number + swap_file_count + 1` (above every swap number), named
  `vbpub-iobench`, so it cannot overlap swap and a leftover is identifiable.
- **Sizing:** `min(io_benchmark_max_size_gb, tail free - swap requirement -
  1 GiB margin)`, 2048-sector aligned; under 2 GiB the step is `skipped`
  (not a failure).
- **Cleanup is the same primitive as the rollback:** restore the pre-benchmark
  table with `sfdisk --force`, `partx -d --nr N:N`, `partx -u`,
  `udevadm settle`, then read the table back and require it to equal the
  pre-benchmark layout and the device node to be gone. (`sfdisk --delete` is
  not on the action allowlist and was not added.) Cleanup runs on success and
  on every failure after the partition write was attempted. Cleanup failure
  raises and stops stage 2 before swap placement; a failed `io_benchmark`
  marker is not terminal, so a resumed stage 2 re-checks.
- **Resume:** a terminal step status (`success`/`skipped`/`warned`) skips the
  step; otherwise a leftover `vbpub-iobench` partition is unmounted and
  deleted (via the live table minus that one line) before anything else. A
  partition at that number that is NOT ours is left alone and the step is
  skipped.
- **Case B (decision 2026-10-06, kept for both cases):** the benchmark always
  uses the throwaway TAIL partition, never a raw swap partition. In Case B it
  runs after the hook has written the swap partitions into the table but
  BEFORE `mkswap` and activation, and only when at least 2 GiB remain after
  the last existing partition (and the 1 GiB margin); otherwise the step is
  `skipped`. It uses the same sizing with the existing partitions' end as the
  "requirement". How often that skips depends on the disk (free tail =
  disk - root target - swap, which can be several GiB), so "usually skips" is
  NOT claimed. The swap partitions are untouched and unformatted at that
  point, so the tail partition is safe.
- **Resume safety:** `_stage2()` derives "swap partitions already written" from
  the LIVE table (all planned swap partitions present with the exact start, size
  and type), not only from `_verify_and_apply_root_shrink()`'s first-pass return
  value; a re-run after the deliberate cleanup-failure stop therefore skips the
  swap write instead of tripping `_validate_plan_geometry()`. Partial or
  mismatching presence still refuses.
- **Timeouts and the scheduler (fix round 1):** `HostActions.run(timeout=)`
  runs the command in its own session; on expiry the process GROUP gets
  SIGTERM, then SIGKILL after a grace period; a child that cannot be reaped
  (`ActionUnreapable`) is a cleanup failure and stops the install without
  touching the device. Tool timeout `6 x duration x 3 + testfile_gb x 30 + 120`
  s (the test-file fill is not covered by `--duration`); `sync`/`umount` 120 s,
  `mkfs` 300 s, `dd` 120 s. The disk's scheduler and `nomerges` are snapshotted
  before the tool and restored afterwards (success or failure) with `tee`,
  because the tool's atexit restore does not run on SIGTERM/SIGKILL.
- **Cleanup robustness:** `udevadm settle` before the restore; `sfdisk --force
  --no-reread` for the restore (a deliberate change to the shared rollback
  path: the `partx -d`/`-u` that follow sync the kernel); `partx -d`/`-u`
  retried up to 5 times with a 1 s backoff; the first MiB of the throwaway
  partition is zeroed (`dd`) before deletion so no phantom ext4 signature
  survives; the post-restore readback also compares uuid and name of the
  preserved partitions and the disk label-id.
- **Tool integrity:** besides the header hashes, the sha256 of the whole
  generated body must equal the committed `tools/iocost_coef_gen.py.sha256`
  (written by `build-iocost-generator.py`, verified by its `--check`). A
  mismatch marks the step `warned` ("tool integrity check failed"), skips the
  benchmark and the install continues.
- **Tool check:** the artifact's header hashes are compared with the vendored
  source and patches (same formula as `build-iocost-generator.py`); the file is
  not regenerated (that needs `patch` and the repo layout). The tool is run by
  absolute path from the bootstrap checkout; `fio` and `pv` are installed only
  when the benchmark runs. The test file is 75% of the partition, at most 16 GiB.
- **Advisory:** any failure before cleanup is recorded as step `warned`, the
  partition is removed and the install continues. No `io.cost.model` /
  `io.cost.qos` is written on a failed benchmark. (Update, LT-MEM 2026-10-06:
  on a VALID result a boot-time unit now writes both; see README "io.cost".)
- **Residual risks:** `HostActions.run` has no timeout (a hung `fio` would hang
  stage 2); the tool switches the whole disk's scheduler to `none` for the run.

## Original design (2026-09-09), kept for rationale

## What changed from the original idea

Original ask was "port v1's io.cost/rlat/wlat benchmark." Two corrections
landed before any code was written:

1. **Not swap-specific.** The operator explicitly ruled out replicating
   v1's swap-partition-targeted fio matrix. This benchmarks the disk
   generally, the same input io.cost's own model wants — not swap I/O
   patterns specifically.
2. **Use the kernel's own tool, not a bespoke fio job.** `tools/cgroup/
   iocost_coef_gen.py` (vendored, see `vendor/README.md`) is what real
   io.cost deployments (`resctl-demo` etc.) actually use to calibrate
   `io.cost.model`'s `rbps`/`rseqiops`/`rrandiops`/`wbps`/`wseqiops`/
   `wrandiops`. rlat/wlat live in the separate `io.cost.qos` file, and are
   an *input* the operator chooses (the target latency at a chosen
   percentile), not something this tool measures — the QoS controller
   throttles the model's max rates down when measured latency exceeds
   them. This benchmark step only produces the model half; choosing and
   writing `io.cost.qos` (and enabling io.cost at all) is explicitly a
   separate, later decision — see `debian_install_v2/README.md`'s "Not yet
   in v2" section.

## The `--testdev`-vs-partition problem — RESOLVED in the shared patch series

`iocost_coef_gen.py --testdev DEV` runs destructive tests directly against
whatever device it's given — explicitly documented as destroying
everything on it, which is exactly why a *partition* target (not the whole
disk) matters: carving a throwaway partition from the same free space swap
will use, benchmarking that, then discarding it before writing the real
swap layout, keeps this from ever touching live data.

Reading the vendored source found a real bug in that path, though: the
script resolves the elevator/`nomerges` sysfs paths as `/sys/block/<basename
of --testdev>/queue/...` (`iocost_coef_gen.py` lines ~126-131, 139-140) —
correct for `/dev/vda` (`/sys/block/vda/queue/...` exists), but **wrong for
a partition** (`/sys/block/vda7` does not exist; only `/sys/block/vda/vda7`
does). Pointed at a partition, it fails outright trying to open that path.

**Resolved, not worked around**: the shared generator patch
`debian_install_v2/vendor/0001-testdev-resolve-partition-to-parent-for-sysfs.patch`
reuses the script's own partition→whole-device resolution
(`glob.glob('/sys/block/*/' + devname)`) for `--testdev`'s `devname` too —
but only for the two sysfs lookups; `devno`/`testfile` (what fio actually
reads/writes) stay exactly the partition given on the command line.
Elevator/nomerges genuinely are whole-queue properties shared across every
partition of a disk, so widening only those two lookups is correct. The
preferred throwaway-partition flow is still to format and mount that partition
then run in file-target mode, avoiding raw-device writes. Raw `--testdev`
partition support remains available for an operator who deliberately accepts
its destructive target semantics. Both carried patches are applied when the
single shared runtime artifact is generated; see `vendor/README.md` and
`tools/build-iocost-generator.py`.

The mounted file-target mode also writes data: setup fills a new target with
random data and fio overwrites it during measurement. Use a dedicated
disposable scratch path. An existing target is reused only if it is a
single-link regular file with the requested size; mismatched paths are
refused without unlinking or replacing them.

## Sizing and duration (operator-specified, 2026-09-09)

- Duration: `io_benchmark_duration_s` (default 30) maps to the vendored
  script's own `--duration` (its upstream default is 120s per sub-test ×
  6 sub-tests ≈ 12 minutes total; 30s ×6 ≈ 3 minutes). 5s is enough for
  fast iteration inside the VM harness.
- Size: `io_benchmark_max_size_gb` (default 32) is an upper bound, not a
  fixed size — the actual throwaway partition should be
  `min(io_benchmark_max_size_gb, available_free_space - the real swap
  shape's own requirement - a safety margin)`, so the benchmark itself
  never eats space the swap layout needs, and never fails outright on a
  disk where the free region is smaller than 32GiB to begin with.

## Where this fits in the install flow, and why it must NOT touch
## `_apply_known_swap_shape()`/`_validate_plan_geometry()`

The natural insertion point is `_stage2()`, after free space is known to
exist (post root-shrink for Case B, already-present for Case A) but
*before* `_apply_known_swap_shape()` writes the real swap partition table
— i.e. between `_verify_and_apply_root_shrink()` and the
`if not swap_partitions_already_written: self._apply_known_swap_shape()`
call.

Critical constraint found reading `_verify_and_apply_root_shrink()`'s own
docstring: `_validate_plan_geometry()` deliberately **refuses** a plan that
would drop an existing partition it doesn't recognize as its own ("adversarial
review finding, a stage2 crash immediately after root_shrink reports
success" — see that docstring). A throwaway benchmark partition left on
disk when `_apply_known_swap_shape()` runs would trigger exactly that
refusal. **Do not extend `_validate_plan_geometry()` to special-case a
benchmark partition** — that function is the single most safety-critical
piece of code in this project (14 live bugs were found hardening this
exact area; see project memory) and should not grow a special case for a
feature that can instead just clean up after itself.

Correct approach: two-phase, using the *existing* rollback primitives
already proven in `_apply_known_swap_shape()`'s own mismatch-handling path
(`partx -d --nr ...`, `partx -u`, `udevadm settle`) —

1. Create ONE throwaway partition sized per "Sizing" above, using the same
   plan/apply/readback-verify path `_apply_known_swap_shape()` already
   uses (reuse, don't reinvent a second raw-sfdisk code path).
2. Run the benchmark (approach (a) above: mkfs + mount + `iocost_coef_gen.py`
   in testfile mode), capture `rbps`/`rseqiops`/`rrandiops`/`wbps`/
   `wseqiops`/`wrandiops`, persist to `state_dir/io-benchmark.json` and a
   compact `_mark_step("io_benchmark", ...)` summary.
3. Unmount, then **delete the throwaway partition** via the same
   `partx -d`/`sfdisk` rollback primitives — restoring a clean free-space
   region — *before* `_apply_known_swap_shape()` ever runs. It should see
   exactly the same disk state it already knows how to handle; no changes
   to it or to `_validate_plan_geometry()` should be needed.

This is now implemented (see the top of this note) but NOT yet reviewed or
live-validated: it needs the standard fresh adversarial review before it is
considered shipped — this is live disk partition surgery, the single
highest-bug-density area of this whole project.
