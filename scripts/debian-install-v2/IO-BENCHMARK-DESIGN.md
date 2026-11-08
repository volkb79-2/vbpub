# Install-time io.cost benchmark

The installer benchmark measures a reserved raw partition with the shared
`iobench` tool. The public config and disk-plan contract are documented in
[README](README.md#disk-layout). This file records why the benchmark is a
first-class partition and how stage two runs it safely.

## Partition contract

- Every plan reserves a Linux GPT partition named `iobench-raw`, 8 GiB by
  default. `partitioning.iobench_raw_gb` accepts 2–10240 GiB or `false` to
  disable the partition. `run_io_benchmark=true` requires it to be enabled.
- The raw extent follows configured data and the explicit unallocated reserve,
  immediately before tail swap. Its number follows its position. The extent is
  included whether or not the benchmark runs, so enabling the benchmark does
  not add an interim table entry or move other partitions.
- The installer never formats, mounts, or activates it as swap. An enabled
  benchmark verifies the exact plan, checks mount and swap state, wipes
  signatures with `wipefs -a`, and runs iobench directly against the device.
  The partition remains reserved and raw afterward.
- The runner is copied into the remote bootstrap with only the `iobench`
  source modules it imports. Before calibration, iobench's guard checks target
  device size and PARTUUID. Its `veryquick` profile runs within a 270-second
  outer timeout.

The fixed extent avoids temporary partition writes, scratch-filesystem
creation, and deletion. It also keeps the installed benchmark on a planned raw
partition rather than the whole disk or a swap partition. Changing the size
does not alter the other allocation rules: swap is sized first, and the
remaining root/data/reserve pool is resolved against the same GPT-safe end.

## Stage-two sequence

Stage two first applies and verifies the final partition table containing root,
data, `iobench-raw`, and managed swap. It then runs the optional benchmark
before root growth, swap activation, and data-volume formatting:

1. Resolve the raw partition by the plan's partition number and verify its
   PARTLABEL, Linux type, start, and sector count against the current table.
2. Confirm its block device is not mounted and is not an active swap source.
3. Install `fio` if needed, wipe signatures, and read the partition PARTUUID.
4. Invoke `tools/iobench-install-runner` with target, exact byte size,
   PARTUUID, and an installer-owned state directory.
5. The runner initializes iobench's destructive-device guard, runs profile
   `veryquick`, validates the six positive io.cost coefficients, and writes
   `state_dir/io-benchmark.json` atomically with mode `0600`.
6. Mark the step `success` and notify with the coefficients. The raw partition
   is left in the same table and remains unformatted.

The step is idempotent: a terminal `success`, `skipped`, or `warned` result is
not repeated on resume. A normal benchmark/tool failure is advisory and marks
the step `warned`; an unreapable child is a hard failure because the raw device
could still be in use. Stage two never starts a second partition-table write
for benchmarking.

## Output and limits

The JSON result records tool version, profile, target, partition byte size,
calibration ID, and `rbps`, `rseqiops`, `rrandiops`, `wbps`, `wseqiops`, and
`wrandiops`. The result is advisory: this installer step does not write
`io.cost.model` or `io.cost.qos` from it.

`fio --direct=1` does not bypass every hypervisor write-back cache. A virtual
disk can therefore report host-cache throughput rather than durable device
performance. Interpret its coefficients as measurements of the stack seen by
the guest, not a promise about backing media speed. The existing calibration
plausibility refusal remains in `tools/iocost-calibrate.sh`; the install
result itself is recorded without applying that policy.
