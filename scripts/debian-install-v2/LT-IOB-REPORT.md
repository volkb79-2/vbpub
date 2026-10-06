# LT-IOB report: io.cost benchmark step (branch `lt-iobench`)

Scope: `Installer._run_io_benchmark()` and its helpers, as specified in
`IO-BENCHMARK-DESIGN.md`. Nothing here was run against a real disk, loop
device, swap, fio or the VM harness; all evidence is unit tests against a
simulated disk (`debian_install_v2/tests/test_io_benchmark.py`, class `SimDisk`
behind the real `HostActions` allowlist). Live validation is the controller's
netcup run.

## Design as built

- `_stage2()` calls `_run_io_benchmark(swap_written=...)` right after
  `_verify_and_apply_root_shrink()` and before the swap shape (the later
  `_apply_known_swap_shape()` call). No-op unless `run_io_benchmark`.
- Refactor (behaviour-preserving, existing 577 tests stayed green): the
  forward write/partx/settle/readback block and the rollback block of
  `_apply_known_swap_shape()` became `_write_and_verify_partition_plan()` and
  `_restore_partition_table()`. Both swap placement and the benchmark use them,
  so there is one raw sfdisk path. `_validate_plan_geometry()` is NOT modified.
  The helper takes `record_in_dry_run` so the benchmark's actions appear in a
  dry-run plan; the swap shape still records none in dry-run, as before.
- Partition: tail of the disk, number `root_number + swap_file_count + 1`,
  GPT name `vbpub-iobench`, Linux filesystem type. Sizing
  `min(io_benchmark_max_size_gb, tail free - swap requirement - 1 GiB)`,
  2048-sector aligned; under 2 GiB -> step `skipped` with a reason.
  `_validate_benchmark_plan()` requires: exactly one added partition, all
  existing geometry unchanged, no overlap, start at least swap end + 1 GiB.
- Case B (hook already wrote swap): the existing partitions' end is the
  "requirement", so it benchmarks only if tail space remains (usually skips).
- Tool: `tools/iocost_coef_gen.py` next to the package; header
  `source-sha256`/`patch-series-sha256` are compared with the vendored source
  and `0*.patch` files (same formula as the builder). It is not regenerated.
  `fio` and `pv` are installed through `_packages()` (step `io_benchmark_packages`)
  only when the benchmark runs, before any partition is touched.
- Results parsed from the tool's last line, written to
  `state_dir/io-benchmark.json` (0600; timestamp, device, partition, devno,
  size GiB/sectors, duration, tool sha256 and header hashes, results), a compact
  `_mark_step` summary, and one `_notify`. No `io.cost.model`/`io.cost.qos` write.
- Failure policy: any failure up to and including the tool run is advisory
  (step `warned`, install continues). Cleanup runs in a `finally` whenever the
  partition write was attempted. Cleanup failure (umount fails, restored layout
  differs from the pre-benchmark layout, device node still present) raises
  `InstallerError("io benchmark cleanup failed ...")`, marks the step `failed`
  (not terminal, so resume re-checks) and stops before swap placement.
- Resume: terminal status (`success`/`skipped`/`warned`) skips. Otherwise a
  leftover `vbpub-iobench` partition is unmounted and deleted (live table minus
  that line) first; a partition at that number that is not ours is untouched
  and the step is skipped.
- Allowlist additions (`actions.py`): `mkfs.ext4` (-F -q -O -L), `mount`
  (-t -o), `umount` (only -v), `iocost_coef_gen.py` (--testfile,
  --testfile-size-gb, --duration, --quiet).

## Exact action sequence (real mode, benchmark runs)

1. `findmnt -rn -o SOURCE,TARGET,FSTYPE` (preflight), `apt-get update`,
   `apt-get install fio pv`
2. write backup `state_dir/backups/ptable-iobench-<ts>.sfdisk`
3. `sfdisk --force --no-reread /dev/D` (plan on stdin), `partx -a --nr N:N`,
   `udevadm settle`, `sfdisk --dump` (readback verify), wait for node
4. `mkfs.ext4 -F -q -O ^has_journal -L vbpub-iobench /dev/DN`
5. `mount -t ext4 -o noatime /dev/DN <mkdtemp>`
6. `tools/iocost_coef_gen.py --testfile <mnt>/iocost-coef-fio.testfile
   --testfile-size-gb X --duration S --quiet`
7. write `io-benchmark.json`
8. `findmnt` (mount check), `sync`, `umount /dev/DN`
9. `sfdisk --force /dev/D` (pre-benchmark table on stdin), `partx -d --nr N:N`,
   `partx -u`, `udevadm settle`, `sfdisk --dump` (verify equals pre-layout),
   node-gone check
10. then the unchanged swap shape (`sfdisk --force --no-reread` ...).

## Tests (`test_io_benchmark.py`, 37 collected cases incl. parametrized)

Lifecycle order via `SimDisk` (create -> verify -> mkfs -> mount -> tool ->
umount -> delete -> verify -> swap write, and exactly two table writes) and the
same order in a real `dry_run=True` plan; final layout has no benchmark
partition; skip-when-too-small (swap still applied, no benchmark actions);
size cap by swap requirement and by config max; boundary at exactly 2 GiB;
Case B swap-already-written; leftover removal before swap (including unmount);
foreign partition untouched; benchmark failure (tool error, unparseable output,
mkfs error, readback mismatch, bad tool artifact) -> cleanup -> install
continues; cleanup failures (umount, layout not restored, node still present)
stop before swap; `failed` marker not terminal; terminal marker skips; disabled
does nothing; result parsing (valid + 5 malformed); generator header
verification (real artifact, missing, stale hash, no header, changed patch);
`io-benchmark.json` mode 0600 and contents; allowlist refusals. `SimDisk`
refuses `write_file` outside the test tmp dir.

Evidence run: full suite in the worktree, serial, `nice -n 19 ionice -c 3`
under `flock`: 614 passed, 11 skipped (before: 577 passed, 11 skipped).

## Plant mutations (each applied by hand, full suite run, then reverted)

| Mutation | Result |
|---|---|
| skip cleanup (`if False and created:`) | killed, 14 failures |
| wrong order: `_run_io_benchmark` moved after `_apply_known_swap_shape` | killed, 5 failures |
| benchmark failure fatal (`except Exception: raise`) | killed, 5 failures |
| size not capped by swap requirement (dropped `- swap_required_end`) | killed, 4 failures |
| extra: cleanup layout verification disabled | killed, 1 failure |

(On the third row my first attempt was contaminated by a mis-restored
`_stage2()` call, giving 12 failures; I fixed the call and re-ran, giving the 5
reported. After the last revert the full suite was green again, 614 passed.)

## Gate

`flock .../gate.lock ./run-gate.py --worktree /workspaces/vbpub/.worktrees/lt-iobench r0-r1`
verdict (read in a separate step from the saved output): lane `r0-r1` PASS,
exit 0, 614 passed, 11 skipped, total coverage 95%. r2 (mutation) and the
VM lane were not run.

## Deviations / open items

- Cleanup restores the pre-benchmark table with `sfdisk --force` (the existing
  rollback primitive) instead of `sfdisk --delete`, which is not allowlisted.
- Header-hash check instead of regeneration (see above).
- `HostActions.run` has no timeout: a hung fio hangs stage 2. Not changed.
- The tool sets the whole disk's scheduler to `none` for the run (inherent to
  the generator); the mount directory is `tempfile.mkdtemp` under `/tmp`.
- Not exercised by any test: real udev/partx timing, real fio/pv, ext4 mkfs on
  a just-created node, the tool's own sysfs access on a partition-backed file
  target (patch 0002 is trusted, not re-tested here).
- Config, wizard, `_validate_plan_geometry`, controller-key and docker code
  were not touched.
