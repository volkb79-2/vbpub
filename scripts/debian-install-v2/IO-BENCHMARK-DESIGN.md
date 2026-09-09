# io.cost benchmark — design note (not yet implemented)

Status: `Config.run_io_benchmark`/`io_benchmark_duration_s`/
`io_benchmark_max_size_gb` exist (`debian_install_v2/config.py`) and the
kernel's own `iocost_coef_gen.py` is vendored (`debian_install_v2/vendor/`).
Nothing in `installer.py` invokes it yet. This note captures the design
worked out 2026-09-09 so the actual partition-surgery integration can be
built as its own careful, reviewed pass — the same way
`CASE-B-ROOT-SHRINK-DESIGN.md` preceded that feature's implementation,
rather than folding brand-new live-disk-mutation code into an already large
batch of unrelated changes.

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

## The `--testdev`-vs-partition problem — RESOLVED via a vendored patch

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

**Resolved, not worked around**: `debian_install_v2/vendor/0001-testdev-
resolve-partition-to-parent-for-sysfs.patch` is a 9-line patch that reuses
the script's own `dir_to_dev()` partition→whole-device resolution
(`glob.glob('/sys/block/*/' + devname)`) for `--testdev`'s `devname` too —
but only for the two sysfs lookups; `devno`/`testfile` (what fio actually
reads/writes) stay exactly the partition given on the command line.
Elevator/nomerges genuinely are whole-queue properties shared across every
partition of a disk, so widening only those two lookups is correct. This
keeps the benchmark on the tool's primary, highest-fidelity raw-device
mode — no format+mount detour, no filesystem-layer overhead on the
numbers, and the vendored copy itself stays byte-identical to upstream
(the patch applies on top of it at deploy/invoke time — see
`vendor/README.md` for the exact `patch -p1`/drift-check invocation, and
never hand-edit the vendored file itself).

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

This has not been implemented or reviewed yet. Do not build it without a
dedicated pass (design review of the exact reused-primitive plan above,
plus the standard fresh adversarial review before it's considered shipped)
— this is live disk partition surgery, the single highest-bug-density area
of this whole project.
