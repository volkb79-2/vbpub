# Install-time io.cost benchmark

The installer benchmark measures the planned tail swap capacity before it is
formatted as swap. There is no permanent `iobench-raw` partition or `@swap`
swapfile. The installer uses the existing guarded partition-table transaction
for both temporary table writes.

## Plan and target

- `partitioning.iobench_raw_gb` is removed. Absence and `false` are accepted
  for older profiles; any other value is refused as `iobench_raw_removed` and
  the error says to delete the key.
- On ext4 schema 1 and 2, `run_io_benchmark=true` requires
  `swap_policy="on"` and `swap_partition_count >= 1`. Otherwise planning
  refuses as `io_benchmark_requires_tail_swap_span`.
- The temporary partition starts at the first tail swap's start sector and
  ends at the last tail swap's end sector. It uses the first tail swap's
  partition number, PARTLABEL `vbpub-swap-span`, and the Linux filesystem type
  GUID (`0fc63daf-8483-4772-8e79-3d69d8477de4`). It is deliberately not a swap
  type, so the kernel cannot auto-activate it. Retained pre-root swaps stay
  outside the span, and no other partition may intersect it.
- Before every conversion, affected partitions must be unmounted, absent from
  `/proc/swaps`, and blank or carry only a swap signature. Span identity is
  checked before the runner: exactly one matching PARTLABEL, expected number,
  type, start, size, and a nonblank PARTUUID. The runner receives the exact
  target, byte size, and PARTUUID and keeps `init_device(own=True, expect_size,
  expect_partuuid)`; its iobench `Config` opts into `raw_write=True`.

Every conversion uses the existing guarded transaction: checksum-backed
pre-change table, geometry validation, refusal to move signed data partitions,
sfdisk write, scoped partx changes, udev settle, and table readback. There is
one table writer; this package does not change the shared `libraries/iobench`
source.

## Stage-two sequence and resume

The fresh-install sequence is:

1. Write the `vbpub-swap-span` table through the guarded transaction.
2. Run the configured iobench rung on the span.
3. Once the benchmark is final (success or advisory warning), run `wipefs -a`
   and zero the first MiB of the span to clear signatures and the iobench
   marker.
4. Write the planned regular swap partitions through the same transaction.
5. Continue with io.cost, root growth, and swap activation.

`ActionUnreapable` is a hard stop: the runner may still use the device, so the
span is not wiped, repartitioned, or formatted. Other runner/package failures
are advisory; the step becomes `warned`, then cleanup and regular swap
restoration continue. If all swap slots are retained before root, there is no
tail span; record `warned` with `no_tail_swap_span` and continue.

Resume is derived only from the live table and `io_benchmark` status:

| Live tail state | Benchmark status | Action |
| --- | --- | --- |
| Planned regular swaps | not final | Convert to span, run, wipe, restore swaps |
| Exact span | not final | Run, wipe, restore swaps |
| Exact span | `success`, `warned`, or `skipped` | Wipe, restore swaps |
| Planned regular swaps | `success`, `warned`, or `skipped` | Continue without a rewrite |
| Any other mix or geometry | any | Named refusal; write nothing |

This covers a crash after the span write (resume runs the benchmark) and after
the final swap write (resume sees the final benchmark plus regular swaps and
does not rewrite). Before cleanup, the saved step detail must match the live
PARTUUID, start, and size. Dry-run records both guarded table writes and does
not change the disk.

Every installer-written swap fstab row includes
`nofail,x-systemd.device-timeout=5s`, so an interrupted later repartition
does not make boot wait indefinitely for a missing swap device.

## Rung and output

The profile is `veryquick`, `quick` (default), or `standard`; outer timeouts
remain 270, 780, and 3660 seconds, respectively. The timeout is iobench's
profile budget plus 60 seconds. Each rung has its own state directory,
`state_dir/iobench/<rung>`, and the runner refuses a directory whose stored
record names another rung (`state_dir_profile_mismatch`).

The runner collects the positive io.cost coefficients produced by iobench and
writes `state_dir/io-benchmark.json` atomically with mode `0600`, recording the
profile, target, span byte size, tool version, calibration ID, and results. A
rung may omit coefficients; the step warns with their names and io.cost stays
unconfigured. No values are invented or reused from an earlier file. `fio
--direct=1` may still measure a hypervisor write-back cache; the results
describe the guest-visible stack, not guaranteed backing-media speed.

## Out of scope

Re-benchmarking an installed host is not implemented; `tools/swap-bench-window.sh`
is unchanged. Btrfs swap remains refused as `btrfs_swap_needs_p3`. Install-time
btrfs benchmark planning keeps `btrfs_iobench_raw_unavailable`, and the btrfs
stage records `iobench_raw_unavailable_on_btrfs`. These are later packages.
